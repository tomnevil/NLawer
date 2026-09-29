"""进程内指标注册表（Prometheus 文本格式导出，零外部依赖）。

**为什么不用 prometheus_client？**
本项目的核心工程约束之一是「零必需外部服务即可完整启动」——CI 不需要
Postgres/Redis 边车容器也是同一原则的延伸。引入 `prometheus_client` 会新增
一个运行期依赖，而它在本项目的实际用法只用到「计数器 + 直方图 + 文本导出」
三件事，自行实现约 150 行即可，且完全可控。

**设计要点（踩过的坑）**
1. **基数爆炸**：标签绝不能带原始 URL。`/api/v1/cases/{uuid}` 这类路径每条
   请求都不同，会把指标表撑爆（内存泄漏 + 导出超时）。因此本模块引入
   `normalize_path()`，把 UUID / 长数字段折叠为 `{id}`。
2. **分位数不能在导出时算**：直方图只存桶计数与总和，分位数由 Prometheus
   服务端用 `histogram_quantile()` 计算。进程内算分位数需要保留全部样本，
   是内存与精度的双重错误。
3. **多副本语义**：进程内计数在 N 副本部署下每个实例各存一份，Prometheus
   按 `instance` 标签天然聚合。这与 `REDIS_URL` 限流后端的分工一致：
   限流要「跨副本正确」故需共享存储，指标要「可聚合」故用拉取模型。

**并发安全性**：FastAPI 单事件循环内 `dict` 操作为原子操作，无 await 打断，
因此无需加锁。`_lock` 仅为将来可能引入的多线程（如同步 job worker）预留。
"""
from __future__ import annotations

import re
import threading
from typing import Dict, Iterable, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# 路径归一化：指标标签的第一道防线
# ---------------------------------------------------------------------------

#: UUID（含无连字符形式）
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\b[0-9a-fA-F]{32}\b"
)
#: 纯数字路径段（如 /orders/12345）
_NUMERIC_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")
#: 明显是标识符的长十六进制/混合串（>=16 位）
_LONG_TOKEN_RE = re.compile(r"/[0-9a-zA-Z_-]{16,}(?=/|$)")


def normalize_path(path: str) -> str:
    """把具体路径折叠为低基数模板，避免指标表被唯一 URL 撑爆。

    `normalize_path("/api/v1/cases/3f2a...-.../evidence/12")`
      -> `/api/v1/cases/{id}/evidence/{id}`
    """
    if not path:
        return "/"
    normalized = _UUID_RE.sub("{id}", path)
    normalized = _NUMERIC_SEGMENT_RE.sub("/{id}", normalized)
    normalized = _LONG_TOKEN_RE.sub("/{id}", normalized)
    return normalized


# ---------------------------------------------------------------------------
# 计数器
# ---------------------------------------------------------------------------

#: 直方图默认桶（毫秒）。覆盖「本地 SQLite 快路径」到「外部 LLM 慢调用」：
#: 法律业务单请求耗时可从数十毫秒（查列表）到数十秒（整卷分析）。
DEFAULT_LATENCY_BUCKETS_MS: Tuple[float, ...] = (
    10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000,
)


class Counter:
    """带标签的单调递增计数器。"""

    __slots__ = ("name", "help", "label_names", "_values")

    def __init__(
        self, name: str, help_: str, label_names: Sequence[str] = ()
    ) -> None:
        self.name = name
        self.help = help_
        self.label_names = tuple(label_names)
        self._values: Dict[Tuple[str, ...], float] = {}

    def inc(self, labels: Sequence[str] | None = None, value: float = 1.0) -> None:
        key = self._key(labels)
        self._values[key] = self._values.get(key, 0.0) + value

    def _key(self, labels: Sequence[str] | None) -> Tuple[str, ...]:
        if not self.label_names:
            return ()
        labels = labels or ()
        if len(labels) != len(self.label_names):
            raise ValueError(
                f"计数器 {self.name} 需要 {len(self.label_names)} 个标签"
                f"（{self.label_names}），收到 {len(labels)} 个"
            )
        return tuple(str(v) for v in labels)

    def render(self) -> List[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        if not self.label_names:
            lines.append(f"{self.name} {self._fmt(self._values.get((), 0.0))}")
            return lines
        for key, value in sorted(self._values.items()):
            pairs = ",".join(
                f'{n}="{_escape_label(v)}"' for n, v in zip(self.label_names, key)
            )
            lines.append(f"{self.name}{{{pairs}}} {self._fmt(value)}")
        return lines

    @staticmethod
    def _fmt(value: float) -> str:
        # 整数计数器不要渲染成 "12.0"，便于人眼扫读与 diff
        return str(int(value)) if float(value).is_integer() else repr(float(value))

    def reset(self) -> None:
        self._values.clear()


# ---------------------------------------------------------------------------
# 直方图
# ---------------------------------------------------------------------------


class Histogram:
    """累计桶直方图（cumulative buckets，Prometheus 语义）。

    只保留 `le` 累计计数与 `_sum` / `_count`，分位数交由 Prometheus 端计算。
    """

    __slots__ = ("name", "help", "label_names", "buckets", "_counts", "_sums", "_totals")

    def __init__(
        self,
        name: str,
        help_: str,
        label_names: Sequence[str],
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS_MS,
    ) -> None:
        self.name = name
        self.help = help_
        self.label_names = tuple(label_names)
        self.buckets = tuple(sorted(float(b) for b in buckets))
        #: key -> 每个桶的累计计数（索引与 self.buckets 对齐）+ 溢出桶
        self._counts: Dict[Tuple[str, ...], List[float]] = {}
        self._sums: Dict[Tuple[str, ...], float] = {}
        self._totals: Dict[Tuple[str, ...], float] = {}

    def observe(self, labels: Sequence[str] | None, value: float) -> None:
        """记录一次观测。

        ⚠️ **必须累加到所有 >= value 的桶**，而不是只加到命中的第一个桶。

        只加第一个桶是最容易犯的错误（本模块初版即如此），后果是
        `_bucket{le="5000"}` 会**小于** `_bucket{le="500"}`——违反
        Prometheus 「le 单调不减」的硬性约定，导致服务端
        `histogram_quantile()` 静默返回错误分位数（不报错，只是结果错），
        而分位数恰恰是 P99 告警的唯一依据。
        """
        key = self._key(labels)
        counts = self._counts.get(key)
        if counts is None:
            counts = [0.0] * (len(self.buckets) + 1)
            self._counts[key] = counts
        value = float(value)
        # 命中的第一个桶及其后所有桶（含溢出桶）都要 +1
        for idx, bound in enumerate(self.buckets):
            if value <= bound:
                for j in range(idx, len(counts)):
                    counts[j] += 1
                break
        else:
            counts[-1] += 1
        self._sums[key] = self._sums.get(key, 0.0) + value
        self._totals[key] = self._totals.get(key, 0.0) + 1.0

    def _key(self, labels: Sequence[str] | None) -> Tuple[str, ...]:
        if not self.label_names:
            return ()
        labels = labels or ()
        if len(labels) != len(self.label_names):
            raise ValueError(
                f"直方图 {self.name} 需要 {len(self.label_names)} 个标签，收到 {len(labels)} 个"
            )
        return tuple(str(v) for v in labels)

    def render(self) -> List[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        keys: Iterable[Tuple[str, ...]] = (
            sorted(self._counts.keys()) if self.label_names else [()]
        )
        for key in keys:
            prefix = ""
            if self.label_names:
                prefix = "{" + ",".join(
                    f'{n}="{_escape_label(v)}"' for n, v in zip(self.label_names, key)
                ) + "}"
            counts = self._counts.get(key, [0.0] * (len(self.buckets) + 1))
            for idx, bound in enumerate(self.buckets):
                le = _le_str(bound)
                if self.label_names:
                    inner = prefix[1:-1] + f',le="{le}"'
                    lines.append(f"{self.name}_bucket{{{inner}}} {_int(counts[idx])}")
                else:
                    lines.append(f'{self.name}_bucket{{le="{le}"}} {_int(counts[idx])}')
            # +Inf 桶 = 总数
            if self.label_names:
                inner = prefix[1:-1] + ',le="+Inf"'
                lines.append(f"{self.name}_bucket{{{inner}}} {_int(self._totals.get(key, 0.0))}")
            else:
                lines.append(
                    f'{self.name}_bucket{{le="+Inf"}} {_int(self._totals.get(key, 0.0))}'
                )
            suffix = prefix if self.label_names else ""
            lines.append(f"{self.name}_sum{suffix} {_num(self._sums.get(key, 0.0))}")
            lines.append(f"{self.name}_count{suffix} {_int(self._totals.get(key, 0.0))}")
        return lines

    def reset(self) -> None:
        self._counts.clear()
        self._sums.clear()
        self._totals.clear()


class Gauge:
    """可增可减的瞬时值（如队列积压、在途请求数）。"""

    __slots__ = ("name", "help", "label_names", "_values")

    def __init__(self, name: str, help_: str, label_names: Sequence[str] = ()) -> None:
        self.name = name
        self.help = help_
        self.label_names = tuple(label_names)
        self._values: Dict[Tuple[str, ...], float] = {}

    def set(self, value: float, labels: Sequence[str] | None = None) -> None:
        key = tuple(str(v) for v in (labels or ()))
        self._values[key] = float(value)

    def get(self, labels: Sequence[str] | None = None) -> float:
        """读取当前值。

        之所以提供这个方法，而不是让调用方直接碰 `_values`：
        在途请求计数需要「读-改-写」两个动作，调用方若直接操作私有字典，
        任何一次字段重命名都会静默地把计数逻辑改坏。
        """
        key = tuple(str(v) for v in (labels or ()))
        return self._values.get(key, 0.0)

    def inc(self, labels: Sequence[str] | None = None, value: float = 1.0) -> None:
        self.set(self.get(labels) + value, labels)

    def dec(self, labels: Sequence[str] | None = None, value: float = 1.0) -> None:
        # 下限夹到 0：异常路径下若多减一次，负数会污染面板且难以察觉
        self.set(max(0.0, self.get(labels) - value), labels)

    def render(self) -> List[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        if not self.label_names:
            lines.append(f"{self.name} {_num(self._values.get((), 0.0))}")
            return lines
        for key, value in sorted(self._values.items()):
            pairs = ",".join(
                f'{n}="{_escape_label(v)}"' for n, v in zip(self.label_names, key)
            )
            lines.append(f"{self.name}{{{pairs}}} {_num(value)}")
        return lines

    def reset(self) -> None:
        self._values.clear()


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """指标集合 + Prometheus 文本导出。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # ---- HTTP 层 ----
        self.http_requests_total = Counter(
            "nlaw_http_requests_total",
            "HTTP 请求总数（按方法/归一化路径/状态码）",
            ("method", "path", "status"),
        )
        self.http_request_duration_ms = Histogram(
            "nlaw_http_request_duration_milliseconds",
            "HTTP 请求耗时（毫秒）",
            ("method", "path"),
        )
        self.http_requests_in_progress = Gauge(
            "nlaw_http_requests_in_progress",
            "当前在处理中的 HTTP 请求数",
        )

        # ---- 安全 / 限流 ----
        self.rate_limit_hits_total = Counter(
            "nlaw_rate_limit_hits_total",
            "触发限流次数（按归一化路径）",
            ("path",),
        )
        self.csrf_rejections_total = Counter(
            "nlaw_csrf_rejections_total",
            "CSRF 校验拒绝次数",
            ("reason",),
        )
        self.unhandled_errors_total = Counter(
            "nlaw_unhandled_errors_total",
            "未捕获异常次数（按异常类型）",
            ("type",),
        )

        # ---- 内容安全 ----
        self.moderation_blocks_total = Counter(
            "nlaw_moderation_blocks_total",
            "内容安全拦截次数（按阶段：input/output）",
            ("stage",),
        )

        # ---- 业务 / 队列 ----
        self.quota_exhausted_total = Counter(
            "nlaw_quota_exhausted_total",
            "配额耗尽拒绝次数（按用量类型）",
            ("usage_type",),
        )
        self.job_queue_pending = Gauge(
            "nlaw_job_queue_pending",
            "任务队列积压（等待执行的任务数）",
        )
        self.job_queue_completed_total = Counter(
            "nlaw_job_queue_completed_total",
            "任务完成总数（按结果：succeeded/failed/retried）",
            ("result",),
        )

        # ---- 合同审查（P0-16）----
        #
        # 这组指标存在的唯一理由：让「99 元到底买到了什么」成为**可查询的事实**。
        # 改造前合同审查零 LLM 调用、零指标，只要返回 200 就扣费，事后无法
        # 回答「本月有多少次审查其实是规则引擎产出」。北极星口径 =
        # `llm success` 占比（目标 ≥99%），护栏 = `charged_total{source!="llm"}` 恒为 0。
        self.contract_review_total = Counter(
            "nlaw_contract_review_total",
            "合同审查次数（按产出来源 source=llm|rule|mock / 状态 status=success|degraded|failed）",
            ("source", "status"),
        )
        # 计费诚实性护栏：**降级/失败产出计费必须恒为 0**。
        # 单独建指标而不是从 usage_records 推算——后者无法区分「扣费那次
        # 用的是模型还是规则」，而这恰恰是唯一需要监控的东西。
        self.contract_review_charged_total = Counter(
            "nlaw_contract_review_charged_total",
            "合同审查实际计费次数（按产出来源；source!=llm 时恒为 0）",
            ("source",),
        )
        self.contract_review_degraded_total = Counter(
            "nlaw_contract_review_degraded_total",
            "合同审查降级次数（按原因 provider_error|timeout|mock|rule_fallback）",
            ("reason",),
        )
        self.contract_review_duration_milliseconds = Histogram(
            "nlaw_contract_review_duration_milliseconds",
            "合同审查端到端耗时（毫秒，按产出来源）",
            ("source",),
        )

        # ---- 审计 / 合规 ----
        self.audit_write_failures_total = Counter(
            "nlaw_audit_write_failures_total",
            "审计写入失败次数（脱附会话重试耗尽）",
        )
        self.moderation_report_pending = Gauge(
            "nlaw_moderation_report_pending",
            "待人工上报的违规事件数",
        )

        # ---- 实时推送 / WebSocket（P0-15 收口项）----
        #
        # 这一组指标存在的唯一理由：让「通知到底有没有实时送达」成为**可查询
        # 的事实**，而不是只能翻日志猜。推送是旁路——它失败时业务接口照样
        # 返回 200，因此**没有指标就等同于没有故障信号**。
        self.ws_connections_active = Gauge(
            "nlaw_ws_connections_active",
            "当前活跃的 WebSocket 连接数（按端点）",
            ("endpoint",),
        )
        self.ws_connections_total = Counter(
            "nlaw_ws_connections_total",
            "累计建立的 WebSocket 连接数（按端点）",
            ("endpoint",),
        )
        self.ws_connection_rejected_total = Counter(
            "nlaw_ws_connection_rejected_total",
            "被拒绝的 WebSocket 连接数（按原因：over_limit/invalid_user）",
            ("reason",),
        )
        self.ws_auth_failed_total = Counter(
            "nlaw_ws_auth_failed_total",
            "WebSocket 鉴权失败次数（按原因：missing_token/invalid_token/user_not_found）",
            ("reason",),
        )
        self.ws_heartbeat_timeout_total = Counter(
            "nlaw_ws_heartbeat_timeout_total",
            "因空闲超时被回收的 WebSocket 连接数（「假活」连接）",
        )
        # `result` 四态刻意区分「没人连」与「推失败」：
        # 二者都表现为「用户没收到」，但前者是常态、后者是事故。
        # 合并成一个值会让「在线用户的连接全部发送失败」被淹没在
        # 海量 no_connection 里，告警永远不触发。
        self.notification_push_total = Counter(
            "nlaw_notification_push_total",
            "通知实时推送次数（按结果：delivered/no_connection/failed/rolled_back）",
            ("result",),
        )
        # 时延口径 = 「通知落库」到「推送完成」。它直接回答
        # 「实时推送到底有多实时」，也是判断是否需要引入 Redis 广播的依据。
        self.notification_push_latency_milliseconds = Histogram(
            "nlaw_notification_push_latency_milliseconds",
            "通知从落库到推送完成的耗时（毫秒）",
            (),
        )

    # -- 便捷入口：给业务代码用的最小 API --
    def record_request(self, method: str, path: str, status: int, duration_ms: float) -> None:
        p = normalize_path(path)
        self.http_requests_total.inc((method, p, str(status)))
        self.http_request_duration_ms.observe((method, p), duration_ms)

    def render(self) -> str:
        """导出为 Prometheus 文本格式（`text/plain; version=0.0.4`）。

        **枚举方式与 `reset()` 保持一致：遍历实例属性，而不是维护一份清单。**

        初版这里是一份手写的收集器元组，于是「新增指标但忘记加进元组」会让
        该指标**永远不出现在 `/metrics` 里**——不报错、测试也发现不了，
        只表现为「面板上没数据」，而排查方向会先怀疑采集侧。
        与 `reset()` 的不一致本身就是信号：同一件事有两种写法时，
        其中一种迟早会漂移。
        """
        chunks: List[str] = []
        with self._lock:
            for collector in self._collectors():
                chunks.extend(collector.render())
                chunks.append("")
        return "\n".join(chunks) + "\n"

    def _collectors(self) -> List[object]:
        """所有具备 `render()` 的实例属性（即全部指标收集器）。"""
        return [
            value
            for value in vars(self).values()
            if callable(getattr(value, "render", None))
        ]

    def reset(self) -> None:
        with self._lock:
            for collector in vars(self).values():
                reset = getattr(collector, "reset", None)
                if callable(reset):
                    reset()


#: 全局单例（进程内）。测试可通过 `reset()` 隔离。
metrics = MetricsRegistry()


# ---------------------------------------------------------------------------
# 渲染辅助
# ---------------------------------------------------------------------------


def _escape_label(value: str) -> str:
    """Prometheus 标签值转义：反斜杠、双引号、换行。"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _le_str(bound: float) -> str:
    return str(int(bound)) if float(bound).is_integer() else repr(float(bound))


def _int(value: float) -> str:
    return str(int(value))


def _num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))
