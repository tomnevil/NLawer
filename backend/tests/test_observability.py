"""P0-10 可观测性回归测试（CI 门禁的一部分）。

这些测试的价值不在于"覆盖率"，而在于把**已经踩过的坑**钉死：

1. `test_histogram_buckets_are_cumulative` —— 直方图桶只加到命中桶的不累计
   实现，会让 `histogram_quantile()` 静默返回错误分位数（不报错，只是结果错），
   而分位数是 P99 告警的唯一依据。**这是本轮真实引入过的缺陷**。
2. `test_normalize_path_bounds_cardinality` —— 路径不归一化会让指标基数
   随案件数线性增长，最终拖垮 Prometheus。
3. `test_probes_not_self_counted` —— 探针被 K8s 每几秒抓一次，若计入指标
   会把业务请求量与耗时完全稀释成噪声。
4. `test_livez_survives_db_outage` / `test_readyz_fails_when_db_down` ——
   两个探针语义混淆会导致「DB 挂了但进程没挂」时编排器滚动重启所有副本，
   把局部故障放大成全局故障。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.log_config import _redact, get_request_id, set_request_id
from app.core.metrics import (
    Counter,
    Histogram,
    MetricsRegistry,
    metrics,
    normalize_path,
)

# ---------------------------------------------------------------------------
# A. 指标内核
# ---------------------------------------------------------------------------


def test_normalize_path_folds_uuid_and_numeric_segments():
    path = "/api/v1/cases/3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071/evidence/99"
    assert normalize_path(path) == "/api/v1/cases/{id}/evidence/{id}"


def test_normalize_path_keeps_plain_paths_intact():
    # 回归保护：过度归一化会让指标失去区分度
    assert normalize_path("/api/v1/auth/login") == "/api/v1/auth/login"


def test_normalize_path_bounds_cardinality():
    """200 个唯一案件 ID 必须收敛为 1 个标签值，否则指标基数随业务量线性膨胀。"""
    templates = {normalize_path(f"/api/v1/cases/{i}/detail") for i in range(200)}
    assert len(templates) == 1


def test_normalize_path_handles_empty():
    assert normalize_path("") == "/"


def test_counter_label_arity_is_enforced():
    """标签数量不符必须报错——静默丢弃标签会让指标维度悄悄消失。"""
    counter = Counter("t_total", "help", ("a", "b"))
    counter.inc(("x", "y"))
    with pytest.raises(ValueError):
        counter.inc(("only-one",))


def test_counter_sums_same_labels_and_separates_different_ones():
    counter = Counter("t_total", "help", ("a",))
    counter.inc(("x",))
    counter.inc(("x",))
    counter.inc(("y",))
    rendered = "\n".join(counter.render())
    assert 't_total{a="x"} 2' in rendered
    assert 't_total{a="y"} 1' in rendered


def test_histogram_buckets_are_cumulative():
    """核心回归：桶必须累计（le 单调不减），否则 histogram_quantile() 静默出错。

    观测值 5 / 50 / 500 / 5000 落入 buckets=[10,100,1000]：
      - le=10    → 只有 5          → 1
      - le=100   → 5, 50           → 2
      - le=1000  → 5, 50, 500      → 3
      - le=+Inf  → 全部 4 个        → 4
    不累计的实现会得到 [1,1,1,4]，Prometheus 端会算出错误分位数。
    """
    hist = Histogram("t_ms", "help", ("m",), buckets=[10, 100, 1000])
    for value in (5, 50, 500, 5000):
        hist.observe(("GET",), value)

    rendered = hist.render()
    buckets = [
        float(line.rsplit(" ", 1)[1])
        for line in rendered
        if "_bucket{" in line
    ]
    assert buckets == [1.0, 2.0, 3.0, 4.0]
    # 单调不减（Prometheus 硬性约定）
    assert buckets == sorted(buckets)


def test_histogram_sum_and_count():
    hist = Histogram("t_ms", "help", ("m",), buckets=[10, 100, 1000])
    for value in (5, 50, 500, 5000):
        hist.observe(("GET",), value)
    rendered = hist.render()
    assert 't_ms_count{m="GET"} 4' in rendered
    assert 't_ms_sum{m="GET"} 5555' in rendered


def test_empty_counter_still_renders_zero():
    """无数据也要输出 0：序列消失会让 Prometheus 端 rate() 出现断点。"""
    counter = Counter("e_total", "help")
    assert "e_total 0" in "\n".join(counter.render())


def test_gauge_inc_dec_roundtrip_and_clamps_at_zero():
    """在途计数必须能正确增删，且多减时夹到 0（负值会污染面板）。"""
    from app.core.metrics import Gauge

    gauge = Gauge("g_test", "help")
    assert gauge.get() == 0.0

    gauge.inc()
    gauge.inc()
    assert gauge.get() == 2.0

    gauge.dec()
    assert gauge.get() == 1.0

    # 多减：必须夹到 0，不能变负
    gauge.dec()
    gauge.dec()
    assert gauge.get() == 0.0


def test_rate_limit_metric_path_is_normalized():
    """限流指标必须用归一化路径。

    `/evidence/cases/{id}` 这类端点带业务 ID；若直接用原始 path，
    每个案件都会产生一条时间序列——这正是 normalize_path 要防的基数爆炸。
    """
    from app.core.metrics import normalize_path

    assert normalize_path("/api/v1/evidence/cases/12345") == "/api/v1/evidence/cases/{id}"


def test_every_registered_collector_appears_in_render():
    """**回归保护**：新增指标不得从 `/metrics` 静默消失。

    `render()` 初版维护了一份**手写的收集器清单**，而 `reset()` 用的是
    「遍历实例属性」——同一件事两种写法。于是「加了指标但忘了加进清单」
    会让该指标永远不导出：不报错、无日志、测试也发现不了，只表现为
    「面板上没数据」，而排查会先怀疑采集侧。

    这条测试把「render 覆盖全部收集器」钉死，让上述漂移无法再发生。
    """
    reg = MetricsRegistry()
    text = reg.render()
    declared = {
        line.split()[2] for line in text.splitlines() if line.startswith("# TYPE ")
    }
    for collector in reg._collectors():
        assert collector.name in declared, f"{collector.name} 未出现在 /metrics 输出中"


def test_realtime_push_metrics_are_exposed():
    """推送链路指标必须存在于导出结果中（PRD NP-12）。

    显式列出名称而非依赖遍历：若有人重命名或删除这些指标，
    遍历式断言会「跟着一起变」而永远通过。
    """
    text = MetricsRegistry().render()
    for name in (
        "nlaw_ws_connections_active",
        "nlaw_ws_connections_total",
        "nlaw_ws_connection_rejected_total",
        "nlaw_ws_auth_failed_total",
        "nlaw_ws_heartbeat_timeout_total",
        "nlaw_notification_push_total",
        "nlaw_notification_push_latency_milliseconds",
    ):
        assert f"# TYPE {name} " in text, f"缺少推送链路指标 {name}"


def test_push_result_counter_uses_low_cardinality_labels():
    """`result` 标签必须是有限枚举，不能把 user_id / 通知 id 混进去。

    推送指标按「每条通知」自增，一旦标签带上通知 id，序列数就等于通知总量
    ——这正是 `normalize_path` 要防的基数爆炸，且这次是**主动**引入的。
    """
    from app.core.metrics import metrics

    assert metrics.notification_push_total.label_names == ("result",)
    # 直方图无标签：它按条观测，带上任何业务维度都会爆炸
    assert metrics.notification_push_latency_milliseconds.label_names == ()


def test_metrics_endpoint_no_high_cardinality_paths(client):
    """端到端：多次不同 ID 请求后，指标中不得出现未归一化的路径标签。"""
    import re

    for case_id in (111, 222, 333):
        client.get(f"/api/v1/cases/{case_id}")

    body = client.get("/metrics").text
    # 抓取所有 path="..." 标签值，逐个检查是否含裸数字 ID
    path_labels = re.findall(r'path="([^"]+)"', body)
    leaks = [p for p in path_labels if re.search(r"/\d+(/|$)", p)]
    assert not leaks, f"发现未归一化的高基数路径标签: {leaks}"


def test_label_values_are_escaped():
    counter = Counter("esc_total", "help", ("v",))
    counter.inc(('a"b\\c\nd',))
    rendered = "\n".join(counter.render())
    assert '\\"' in rendered
    assert "\\\\" in rendered
    assert "\\n" in rendered


def test_registry_renders_prometheus_text_format():
    reg = MetricsRegistry()
    reg.record_request("GET", "/api/v1/cases/123", 200, 42.5)
    reg.record_request("GET", "/api/v1/cases/456", 200, 55.0)
    reg.record_request("POST", "/api/v1/auth/login", 401, 8.2)
    text = reg.render()

    sample_lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    declared = {ln.split()[2] for ln in text.splitlines() if ln.startswith("# TYPE ")}

    # 每个样本的指标族都必须声明 TYPE，否则 histogram_quantile() 直接失效
    bases = set()
    for line in sample_lines:
        name = line.split("{")[0].split(" ")[0]
        for suffix in ("_bucket", "_sum", "_count"):
            if name.endswith(suffix):
                bases.add(name[: -len(suffix)])
                break
        else:
            bases.add(name)
    assert not (bases - declared)

    # 每行值必须可解析为浮点
    for line in sample_lines:
        parts = line.rsplit(" ", 1)
        assert len(parts) == 2, line
        float(parts[1])

    # 多个案件 ID 收敛为同一模板，且无原始 ID 泄漏
    assert 'path="/api/v1/cases/{id}"' in text
    assert "3f2a1b4c" not in text

    # 维度分离：方法 + 状态码
    assert 'method="POST"' in text and 'status="401"' in text


# ---------------------------------------------------------------------------
# B. 结构化日志
# ---------------------------------------------------------------------------


def test_request_id_context_roundtrip():
    set_request_id("ctx-test-123")
    assert get_request_id() == "ctx-test-123"


def test_redact_hides_secrets_but_keeps_normal_fields():
    redacted = _redact(
        {"password": "p@ssw0rd", "api_key": "sk-xxx", "normal": "keep"}
    )
    assert redacted["password"] == "***"
    assert redacted["api_key"] == "***"
    assert redacted["normal"] == "keep"


def test_json_sink_emits_single_line_valid_json(monkeypatch):
    """含换行与双引号的消息必须仍产出单行合法 JSON。

    这是真实踩过的坑：用 f-string 拼 JSON 时，消息里一个引号就能产出
    无法解析的日志行——而日志解析失败往往在事故复盘时才发现。
    """
    from app.core import log_config

    captured: list[str] = []
    monkeypatch.setattr(log_config.sys, "stderr", type("_S", (), {"write": staticmethod(captured.append)})())

    from loguru import logger

    sink_id = logger.add(log_config._json_sink, level="INFO", format="{message}")
    try:
        logger.info('包含"双引号"与换行的消息\n第二行')
    finally:
        logger.remove(sink_id)

    assert captured, "未捕获到日志输出"
    raw = captured[-1].rstrip("\n")
    assert "\n" not in raw, f"输出被换行破坏: {raw!r}"
    parsed = json.loads(raw)  # 解析失败直接抛异常
    assert "双引号" in parsed["message"]
    assert "request_id" in parsed


# ---------------------------------------------------------------------------
# C. 端到端探针语义
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_metrics_endpoint_returns_prometheus_text(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_metrics_endpoint_records_real_requests(client):
    client.get("/")
    body = client.get("/metrics").text
    assert 'path="/"' in body


def test_probes_not_self_counted(client):
    """探针与 /metrics 自身不得污染业务指标。

    K8s 每几秒抓一次探针；若计入，请求量指标会被探针流量淹没。
    """
    client.get("/api/health/livez")
    client.get("/api/health/readyz")
    client.get("/api/health")
    body = client.get("/metrics").text
    leaked = [
        line
        for line in body.splitlines()
        if "_requests_total" in line
        and any(
            f'path="{path}"' in line
            for path in ("/metrics", "/api/health", "/api/health/livez", "/api/health/readyz")
        )
    ]
    assert not leaked, f"探针污染了指标: {leaked}"


def test_livez_returns_alive_without_dependency_checks(client):
    response = client.get("/api/health/livez")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    # liveness 绝不能带依赖检查结果
    assert "checks" not in response.json()


def test_readyz_reports_database_ok_when_healthy(client):
    response = client.get("/api/health/readyz")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


def test_livez_survives_db_outage(client, monkeypatch):
    """DB 挂了但进程健康时，liveness 必须仍为 200。

    否则编排器会滚动重启所有副本——重启治不了 DB 故障，
    只会把"局部依赖故障"放大成"全局服务不可用"。

    ⚠️ 测试写法要点：必须在**已启动**的应用上打补丁，而不是先打补丁再
    `create_app()`。后者会让 `lifespan` 里的 `init_db()` 一起失败，
    测到的是"启动失败"而非"探针语义"（初版即因此误报）。
    """
    import app.main as main_module

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("simulated db outage")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    # 应用已在 fixture 中启动完毕，此刻再切断 DB 才符合真实故障时序
    monkeypatch.setattr(main_module, "async_session_factory", lambda: _BrokenSession())
    response = client.get("/api/health/livez")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readyz_fails_when_db_down(client, monkeypatch):
    """DB 不可用时 readyz 必须 503，否则编排器不会摘流量、请求持续打向坏实例。"""
    import app.main as main_module

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("simulated db outage")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main_module, "async_session_factory", lambda: _BrokenSession())
    response = client.get("/api/health/readyz")
    assert response.status_code == 503
    assert "error" in response.json()["checks"]["database"]


def test_request_id_header_still_propagates(client):
    """本轮改动不得破坏既有契约。"""
    response = client.get("/", headers={"X-Request-ID": "pytest-trace-1"})
    assert response.headers["X-Request-ID"] == "pytest-trace-1"
    assert "X-Response-Time" in response.headers


def test_unhandled_error_counter_increments(client):
    """未捕获异常必须计入指标，否则 500 突增无法告警。"""
    from fastapi import APIRouter

    app = client.app
    router = APIRouter()

    @router.get("/__pytest_boom")
    async def _boom():
        raise RuntimeError("pytest-triggered")

    app.include_router(router)

    key = ("RuntimeError",)
    before = int(metrics.unhandled_errors_total._values.get(key, 0.0))
    response = client.get("/__pytest_boom")
    after = int(metrics.unhandled_errors_total._values.get(key, 0.0))

    assert response.status_code == 500
    assert after == before + 1
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
