"""第九轮验证：CI 门禁 + 可观测性（P0-10）。

覆盖三个层面：
  A. 指标内核（normalize_path / Counter / Histogram / Gauge / 渲染格式）
  B. Prometheus 文本格式合规性（能否被标准解析器解析、分层计数是否自洽）
  C. 端到端 HTTP（/metrics 真实数据、livez/readyz 语义、探针不进指标）

设计原则：**不用关键词扫描证明"代码里有"，而是让代码跑起来看真实输出**。
第 8 轮已两次证明关键词扫描会误判（匹配到无关的同名变量）。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_tmp_verify/observability.db")
os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///./_tmp_verify/observability.db")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "verify-only-secret-key-0123456789abcdef")
os.environ.setdefault("METRICS_ENABLED", "true")
os.environ.setdefault("RATE_LIMIT_ENABLED", "true")
os.environ.setdefault("MODERATION_ENABLED", "true")
os.environ.setdefault("MODERATION_BACKEND", "builtin")
os.environ.setdefault("LOG_FORMAT", "json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILURES: list[str] = []
PASSED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILURES.append(label)
        print(f"  [FAIL] {label} {detail}")


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ===========================================================================
# A. 指标内核
# ===========================================================================
section("A. 指标内核（core/metrics.py）")

from app.core.metrics import (  # noqa: E402
    Counter,
    Histogram,
    MetricsRegistry,
    normalize_path,
)

# A.1 — 路径归一化必须折叠 UUID / 数字 / 长 token
# 这是防"基数爆炸"的第一道防线：不折叠则每个案件详情页都产生一条新时间序列。
uuid_path = "/api/v1/cases/3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071/evidence/99"
norm = normalize_path(uuid_path)
check("A.1 UUID 与数字段被折叠为 {id}", norm == "/api/v1/cases/{id}/evidence/{id}", f"got={norm}")

# A.2 — 普通路径不应被误改（回归保护：过度归一化会让指标失去区分度）
plain = normalize_path("/api/v1/auth/login")
check("A.2 普通路径保持原样", plain == "/api/v1/auth/login", f"got={plain}")

# A.3 — 高基数路径归一化后必须收敛为有限集合
# 模拟 200 个不同案件 ID 的请求，归一化后应只产生 1 个标签值
paths = {normalize_path(f"/api/v1/cases/{i}/detail") for i in range(200)}
check("A.3 200 个唯一路径 → 1 个模板（基数受控）", len(paths) == 1, f"got={len(paths)}")

# A.4 — Counter 标签校验：数量不符必须报错（防误用静默丢数据）
c = Counter("t_total", "help", ("a", "b"))
c.inc(("x", "y"))
c.inc(("x", "y"))
try:
    c.inc(("only-one",))
    check("A.4a 标签数量不符应抛 ValueError", False, "未抛异常")
except ValueError:
    check("A.4a 标签数量不符应抛 ValueError", True)

# A.4b — 同类标签应累加，不同标签应分开
c.inc(("p", "q"))
rendered = "\n".join(c.render())
check(
    "A.4b 相同标签累加、不同标签分离",
    't_total{a="x",b="y"} 2' in rendered and 't_total{a="p",b="q"} 1' in rendered,
    rendered,
)

# A.5 — Histogram 桶必须累计（Prometheus 语义：le 单调不减）
h = Histogram("t_ms", "help", ("m",), buckets=[10, 100, 1000])
for v in (5, 50, 500, 5000):  # 分别落入第1/第2/第3/溢出桶
    h.observe(("GET",), v)
hist = "\n".join(h.render())
bucket_vals = [
    float(line.rsplit(" ", 1)[1])
    for line in hist.splitlines()
    if "_bucket{" in line
]
check(
    "A.5 直方图桶累计且单调不减，+Inf = 总数",
    bucket_vals == [1.0, 2.0, 3.0, 4.0],
    f"buckets={bucket_vals}",
)

# A.5b — _count 与 _sum 必须正确
check(
    "A.5b _count/_sum 正确",
    "t_ms_count{m=\"GET\"} 4" in hist and "t_ms_sum{m=\"GET\"} 5555" in hist,
    hist,
)

# A.6 — 空指标也必须输出（否则 Prometheus 端 rate() 会因序列消失而断点）
empty = Counter("e_total", "help", ())
check("A.6 无数据计数器仍输出 0", "e_total 0" in "\n".join(empty.render()))

# A.7 — 标签值必须转义（引号/反斜杠/换行）
esc = Counter("esc_total", "help", ("v",))
esc.inc(('a"b\\c\nd',))
escaped = "\n".join(esc.render())
check(
    "A.7 标签值转义（引号/反斜杠/换行）",
    '\\"' in escaped and "\\\\" in escaped and "\\n" in escaped,
    escaped,
)


# ===========================================================================
# B. Prometheus 文本格式合规性
# ===========================================================================
section("B. Prometheus 文本格式合规性")

from app.core.metrics import metrics  # noqa: E402

metrics.reset()
metrics.record_request("GET", "/api/v1/cases/123", 200, 42.5)
metrics.record_request("GET", "/api/v1/cases/456", 200, 55.0)
metrics.record_request("POST", "/api/v1/auth/login", 401, 8.2)
text = metrics.render()

# B.1 — 每个指标须有 HELP 与 TYPE（缺 TYPE 时 Prometheus 按 untyped 处理，
#      直方图的 histogram_quantile() 会直接失效）
lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
declared = {
    ln.split()[2] for ln in text.splitlines() if ln.startswith("# TYPE ")
}
used = {ln.split("{")[0].split(" ")[0] for ln in lines}
# 去掉 _bucket/_sum/_count 后缀再比对
bases = set()
for name in used:
    for suffix in ("_bucket", "_sum", "_count"):
        if name.endswith(suffix):
            bases.add(name[: -len(suffix)])
            break
    else:
        bases.add(name)
missing_type = bases - declared
check("B.1 所有输出指标均声明 TYPE", not missing_type, f"missing={missing_type}")

# B.2 — 行格式必须是 `name{labels} value` 或 `name value`，值必须可转 float
bad_lines = []
for ln in lines:
    parts = ln.rsplit(" ", 1)
    if len(parts) != 2:
        bad_lines.append(ln)
        continue
    try:
        float(parts[1])
    except ValueError:
        bad_lines.append(ln)
check("B.2 所有样本行值为合法浮点数", not bad_lines, f"bad={bad_lines[:3]}")

# B.3 — 路径已归一化：多个案件 ID 不应产生多组标签
# 断言要点：**收敛**（不同 ID 折叠为同一个标签值），而非"集合里含有某个字符串"。
# 初版断言写成了后者，逻辑上恒真/恒假，属测试自身缺陷。
case_series = [ln for ln in lines if 'path="/api/v1/cases/{id}"' in ln]
raw_case_paths = [
    ln for ln in lines
    if "path=" in ln
    and 'path="/api/v1/cases/{id}"' not in ln
    and "/api/v1/cases/" in ln
]
check(
    "B.3 端到端：多个案件 ID 收敛为同一模板，无原始 ID 残留",
    len(case_series) > 0 and not raw_case_paths,
    f"matched={len(case_series)} leaked={raw_case_paths}",
)

# B.4 — 计数器语义：同标签多次请求应累加
get_case_total = [
    ln for ln in lines
    if ln.startswith("nlaw_http_requests_total{") and 'path="/api/v1/cases/{id}"' in ln
]
check(
    "B.4 同标签请求累加为 2",
    any(ln.endswith(" 2") for ln in get_case_total),
    f"lines={get_case_total}",
)

# B.5 — 不同方法/状态码分离为不同序列（否则无法算错误率）
check(
    "B.5 方法/状态码维度分离",
    any('method="POST"' in ln and 'status="401"' in ln for ln in lines),
    text,
)

# B.6 — 不能出现残留的 {id} 之外的原始 UUID
check("B.6 输出中无裸 UUID 泄漏", "3f2a1b4c-5d6e" not in text)


# ===========================================================================
# C. 端到端 HTTP
# ===========================================================================
section("C. 端到端 HTTP（探针 + /metrics）")

os.makedirs("_tmp_verify", exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.metrics import metrics as live_metrics  # noqa: E402
from app.main import create_app  # noqa: E402

live_metrics.reset()
app = create_app()

with TestClient(app) as client:
    # C.1 — /metrics 可访问且返回 Prometheus 内容类型
    r = client.get("/metrics")
    check("C.1 /metrics 返回 200", r.status_code == 200, f"status={r.status_code}")
    check(
        "C.1b Content-Type 为 Prometheus 文本格式",
        "text/plain" in r.headers.get("content-type", ""),
        r.headers.get("content-type"),
    )

    # C.2 — 真实请求后 /metrics 必须含有对应的真实数据
    client.get("/api/health")
    client.get("/")
    body = client.get("/metrics").text
    check(
        "C.2 真实请求被记录（/ 出现在指标中）",
        'path="/"' in body,
        body[:200],
    )
    check(
        "C.2b 请求计数 > 0",
        any(
            ln.startswith("nlaw_http_requests_total{") and not ln.endswith(" 0")
            for ln in body.splitlines()
        ),
    )

    # C.3 — 探针与 /metrics 自身**不得**污染业务指标（关键回归点）
    # 若自计自身，K8s 每 5s 抓一次会让请求量指标完全失真
    probe_lines = [
        ln for ln in body.splitlines()
        if 'path="/api/health"' in ln
        or 'path="/metrics"' in ln
        or 'path="/api/health/livez"' in ln
        or 'path="/api/health/readyz"' in ln
    ]
    check("C.3 探针与 /metrics 自身不计入指标", not probe_lines, f"leaked={probe_lines}")

    # C.4 — livez：不依赖 DB，必须 200
    r = client.get("/api/health/livez")
    check("C.4 livez 返回 200", r.status_code == 200, f"status={r.status_code}")
    check(
        "C.4b livez 不含数据库检查（纯进程存活语义）",
        r.json().get("status") == "alive" and "checks" not in r.json(),
        r.json(),
    )

    # C.5 — readyz：DB 正常时 200 且含真实往返结果
    r = client.get("/api/health/readyz")
    check("C.5 readyz 数据库正常时 200", r.status_code == 200, f"status={r.status_code}")
    check(
        "C.5b readyz 报 database=ok",
        (r.json().get("checks") or {}).get("database") == "ok",
        r.json(),
    )

    # C.6 — readyz 必须在 DB 不可用时返回 503（否则编排器不会摘流量）
    # 做法：把 session 工厂替换为必然抛异常的假工厂，验证真实降级路径
    import app.main as main_mod

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("simulated db outage")

        async def __aexit__(self, *exc):
            return False

    original_factory = main_mod.async_session_factory
    main_mod.async_session_factory = lambda: _BrokenSession()  # type: ignore[assignment]
    try:
        r = client.get("/api/health/readyz")
        check(
            "C.6 DB 故障时 readyz 返回 503（触发摘流量）",
            r.status_code == 503,
            f"status={r.status_code} body={r.text[:120]}",
        )
        check(
            "C.6b 503 响应含具体故障原因",
            "error" in str((r.json().get("checks") or {}).get("database", "")),
            r.json(),
        )
        # C.7 — DB 故障时 livez 必须仍为 200（否则会引发滚动重启风暴）
        r2 = client.get("/api/health/livez")
        check(
            "C.7 DB 故障时 livez 仍为 200（不误杀进程）",
            r2.status_code == 200,
            f"status={r2.status_code}",
        )
    finally:
        main_mod.async_session_factory = original_factory

    # C.8 — X-Request-ID 透传（已存在的契约，防本轮改动破坏）
    r = client.get("/", headers={"X-Request-ID": "verify-trace-123"})
    check(
        "C.8 X-Request-ID 透传未被破坏",
        r.headers.get("X-Request-ID") == "verify-trace-123",
        r.headers.get("X-Request-ID"),
    )
    check("C.8b X-Response-Time 仍存在", "X-Response-Time" in r.headers)

    # C.9 — 未捕获异常应计入 unhandled_errors_total（并经 ErrorHandler 归一化）
    # 直接调 ErrorHandlerMiddleware 的路径较绕，这里用注册临时路由验证
    from fastapi import APIRouter

    boom = APIRouter()

    @boom.get("/__verify_boom")
    async def _boom():
        raise RuntimeError("verify-triggered")

    app.include_router(boom)
    before = int(live_metrics.unhandled_errors_total._values.get(("RuntimeError",), 0.0))
    resp = client.get("/__verify_boom")
    after = int(live_metrics.unhandled_errors_total._values.get(("RuntimeError",), 0.0))
    check("C.9 未捕获异常被归一化为 500", resp.status_code == 500, f"status={resp.status_code}")
    check("C.9b 未捕获异常计入 unhandled_errors_total", after == before + 1, f"{before}->{after}")
    check(
        "C.9c 500 响应体为标准错误结构",
        (resp.json().get("error") or {}).get("code") == "INTERNAL_SERVER_ERROR",
        resp.json(),
    )


# ===========================================================================
# D. 结构化日志
# ===========================================================================
section("D. 结构化日志（core/log_config.py）")

import json as _json  # noqa: E402
import logging  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

from app.core.log_config import (  # noqa: E402
    _json_sink,
    get_request_id,
    reset_logging_for_tests,
    set_request_id,
)

# D.1 — request_id 上下文穿透（service 层深处能拿到）
set_request_id("ctx-abc-123")
check("D.1 request_id 上下文可读写", get_request_id() == "ctx-abc-123")

# D.2 — JSON sink 必须产出**单行合法 JSON**，且含 request_id
captured: list[str] = []


def _capture(line: str) -> None:
    captured.append(line)


with patch("app.core.log_config.sys.stderr") as fake_stderr:
    fake_stderr.write = _capture  # type: ignore[method-assign]
    record = {
        "time": MagicMock(isoformat=lambda: "2026-09-12T12:00:00+00:00"),
        "level": MagicMock(name="INFO"),
        "name": "app.test",
        "module": "test",
        "function": "f",
        "line": 1,
        "message": '包含"双引号"与\n换行的消息',
        "extra": {"request_id": "ctx-abc-123", "tenant_id": "-"},
        "exception": None,
    }
    record["level"].name = "INFO"
    _json_sink(MagicMock(record=record))

check("D.2 JSON sink 产出内容", len(captured) == 1, f"captured={captured}")
if captured:
    raw = captured[0].rstrip("\n")
    check("D.2b 含换行的消息仍为单行输出", "\n" not in raw, repr(raw))
    parsed = _json.loads(raw)  # 解析失败会直接抛异常 → 验证转义正确性
    check("D.2c JSON 可被标准解析器解析", True)
    check("D.2d request_id 已注入", parsed.get("request_id") == "ctx-abc-123", parsed)
    check("D.2e 消息内容完整保留", "双引号" in parsed.get("message", ""), parsed.get("message"))

# D.3 — 异常栈必须是纯文本（JSON 中不能含未转义换行）
try:
    raise ValueError("verify-exception")
except ValueError:
    exc_info = sys.exc_info()

record2 = {
    "time": MagicMock(isoformat=lambda: "2026-09-12T12:00:00+00:00"),
    "level": MagicMock(name="ERROR"),
    "name": "app.test",
    "module": "test",
    "function": "f",
    "line": 1,
    "message": "boom",
    "extra": {"request_id": "-", "tenant_id": "-"},
    "exception": MagicMock(
        type=exc_info[0], value=exc_info[1], traceback=exc_info[2]
    ),
}
record2["level"].name = "ERROR"
captured.clear()
with patch("app.core.log_config.sys.stderr") as fake_stderr:
    fake_stderr.write = _capture  # type: ignore[method-assign]
    _json_sink(MagicMock(record=record2))

if captured:
    raw = captured[0].rstrip("\n")
    check("D.3 带异常栈的日志仍为单行", "\n" not in raw, repr(raw[:200]))
    parsed = _json.loads(raw)
    tb = (parsed.get("exception") or {}).get("traceback", "")
    check("D.3b 异常栈被重建为含 V 说明的纯文本", "ValueError" in tb and "verify-exception" in tb)
    check(
        "D.3c 异常类型被记录为类名",
        (parsed.get("exception") or {}).get("type") == "ValueError",
        parsed.get("exception"),
    )

# D.4 — 敏感字段必须脱敏（绝不能把密码/令牌写进日志）
secret_payload = {"password": "p@ssw0rd", "api_key": "sk-xxx", "normal": "keep"}
from app.core.log_config import _redact  # noqa: E402

redacted = _redact(secret_payload)
check(
    "D.4 敏感字段脱敏，普通字段保留",
    redacted["password"] == "***"
    and redacted["api_key"] == "***"
    and redacted["normal"] == "keep",
    redacted,
)

# D.5 — configure_logging 幂等（重复调用不应叠加 sink 导致日志翻倍）
reset_logging_for_tests()
from app.core.log_config import configure_logging  # noqa: E402

configure_logging("text", "INFO")
n_after_first = len(logger_sinks := __import__("loguru").logger._core.handlers)
configure_logging("text", "INFO")
n_after_second = len(__import__("loguru").logger._core.handlers)
check("D.5 configure_logging 幂等（不重复挂 sink）", n_after_first == n_after_second, f"{n_after_first}->{n_after_second}")

# D.6 — JSON 模式装配后仍可正常记日志（不抛异常）
reset_logging_for_tests()
configure_logging("json", "INFO")
try:
    __import__("loguru").logger.info("json mode smoke test")
    check("D.6 JSON 模式可正常记录日志", True)
except Exception as exc:  # noqa: BLE001
    check("D.6 JSON 模式可正常记录日志", False, str(exc))
reset_logging_for_tests()

# ===========================================================================
# E. CI 工作流配置
# ===========================================================================
section("E. CI 工作流配置（.github/workflows/ci.yml）")

import yaml  # noqa: E402

wf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".github", "workflows", "ci.yml")
wf_path = os.path.abspath(wf_path)
check("E.1 工作流文件存在", os.path.exists(wf_path), wf_path)

if os.path.exists(wf_path):
    with open(wf_path, encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    jobs = wf.get("jobs", {})
    check("E.2 三个 job 齐备（backend/frontend/docker-build）",
          set(jobs) == {"backend", "frontend", "docker-build"}, f"jobs={sorted(jobs)}")

    # E.3 — 后端 job 必须真正跑门禁三件套（否则"CI 通过"没有意义）
    backend_steps = jobs.get("backend", {}).get("steps", [])
    step_text = "\n".join(
        str(s.get("run", "")) + " " + str(s.get("uses", "")) for s in backend_steps
    )
    check("E.3a 后端 job 包含 ruff 检查", "ruff check" in step_text)
    check("E.3b 后端 job 包含 compileall", "compileall" in step_text)
    check("E.3c 后端 job 包含 pytest", "pytest" in step_text)
    check(
        "E.3d 后端 job 配置了 CI 专用环境变量（DATABASE_URL 等）",
        "DATABASE_URL" in str(jobs["backend"].get("env", {}))
        and "SECRET_KEY" in str(jobs["backend"].get("env", {})),
        jobs["backend"].get("env"),
    )
    # E.4 — 前端 job 必须跑 typecheck（第 6 轮教训：tsc 才能发现 grep 看不见的错误）
    fe_steps = jobs.get("frontend", {}).get("steps", [])
    fe_text = "\n".join(str(s.get("run", "")) for s in fe_steps)
    check("E.4 前端 job 包含 typecheck", "typecheck" in fe_text, fe_text[:200])

    # E.5 — 权限最小化（CI 不应有写权限）
    check(
        "E.5 权限最小化（contents: read）",
        (wf.get("permissions") or {}).get("contents") == "read",
        wf.get("permissions"),
    )

    # E.6 — 失败时上传诊断产物（否则 CI 红了无从排查）
    check(
        "E.6 失败时上传诊断产物",
        any("upload-artifact" in str(s.get("uses", "")) for s in backend_steps),
    )


print(f"\n{'=' * 72}")
print(f"结果：{PASSED} 通过 / {len(FAILURES)} 失败")
if FAILURES:
    print("失败项：")
    for item in FAILURES:
        print(f"  - {item}")
    sys.exit(1)
print("全部通过 ✅")
print("=" * 72)
