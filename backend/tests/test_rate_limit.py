"""限流回归测试（P0-6）。

## 为什么这个文件必须存在

P0-6 的原文是「**限流默认关闭**且为进程内内存态」。修复落地在三处：

1. `settings.RATE_LIMIT_ENABLED` 默认 `True`（安全默认值，`config.py:70-71`）；
2. `main.py:125-132` 按该开关装配 `RateLimitMiddleware`；
3. 后端可插拔：配 `REDIS_URL` 时用 Redis 共享计数（多副本正确）。

**但此前没有任何判据管它** —— `tests/` 下 `429` / `RATE_LIMIT` **零命中**。
唯一沾边的是 `test_observability.py:135` 的「限流**指标**路径归一化」，
那是可观测性，**不是限流行为**：把 `enabled=False` 写回配置、或把阈值放大 100 倍，
那条用例照样全绿。

⇒ 与「已修复的注册越权零判据」（见 `test_auth_register.py`）**同一族**：
**「写了」≠「有判据」**。

## 覆盖清单

- R1 安全默认值：`RATE_LIMIT_ENABLED` 默认为 `True`
- R2 四档阈值解析（纯函数）：认证 20 / 公开写 5 / 上传 10 / 生成 15，无关路径 `None`
- R3 **精确路径优先于后缀**（`_resolve_limit` 的顺序是有讲究的，写反会让公开写入端点落到更宽松的桶）
- R4 行为：超过阈值真的返回 **429** + `Retry-After` + `code=RATE_LIMITED`
- R5 只限 POST（GET 不限，否则会打断 SSE 流式输出与前端轮询）
- R6 `Retry-After` 是正整数（客户端要拿来等待）
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.rate_limit_backend import MemoryRateLimitBackend


def _middleware(**kw):
    """构造一个只带限流的中间件实例（用于测纯函数 `_resolve_limit`）。"""
    from app.middleware import RateLimitMiddleware

    async def _noop(request: Request) -> JSONResponse:  # pragma: no cover - 不会被调用
        return JSONResponse({"ok": True})

    return RateLimitMiddleware(_noop, backend=MemoryRateLimitBackend(), **kw)


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _app_with_limit(**kw) -> TestClient:
    """挂上限流中间件的最小应用。

    用**独立**的 `MemoryRateLimitBackend`，避免与进程内其他用例共享计数
    （共享会让「第 N+1 次应被拒」变成不确定断言）。
    """
    app = Starlette(
        routes=[
            Route("/api/v1/auth/register", _ok, methods=["GET", "POST"]),
            Route("/api/v1/cases", _ok, methods=["GET", "POST"]),
        ]
    )
    from app.middleware import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware, backend=MemoryRateLimitBackend(), **kw)
    return TestClient(app)


# ═══════════════════════ R1 安全默认值 ═══════════════════════


def test_rate_limit_enabled_by_default():
    """R1：限流必须**默认开启**——这是 P0-6 的核心（原文即「默认关闭」）。

    只断言配置读取，不依赖 `main.py` 的装配结果：配置层是「安全默认值」
    的落点，装配层只是把它接上。两层任一被改回 `False` 都应被判据抓到。
    """
    from app.config import settings

    assert settings.RATE_LIMIT_ENABLED is True, (
        "限流被改回默认关闭——这是 P0-6 的原始缺陷"
    )


# ═══════════════════════ R2 / R3 阈值解析 ═══════════════════════


@pytest.mark.parametrize(
    "path,expected_attr",
    [
        ("/api/v1/auth/login", "max_requests"),
        ("/api/v1/auth/register", "max_requests"),
        ("/api/v1/auth/refresh", "max_requests"),
        ("/api/v1/complaints", "public_write_max"),
        ("/api/v1/evidence/cases/42", "upload_max"),
        ("/api/v1/evidence/cases/7", "upload_max"),
        ("/api/v1/documents/7/generate", "generate_max"),
        ("/api/v1/compliance/scan", "generate_max"),
    ],
)
def test_resolve_limit_maps_path_to_tier(path, expected_attr):
    """R2：每个受保护路径必须落到**设计给定的那一档**。

    断言的是「等于该档阈值」而不是「> 0」——后者在阈值被放大或缩小时
    都不报警，等于没有判据。
    """
    mw = _middleware(
        max_requests=20, window_seconds=60, upload_max=10, generate_max=15, public_write_max=5
    )
    expected = getattr(mw, expected_attr)
    assert mw._resolve_limit(path) == expected, f"{path} 未落到 {expected_attr}"


def test_exact_path_wins_over_suffix():
    """R3：精确路径必须**先于**后缀匹配，否则公开写入端点会落进更宽松的桶。

    `middleware.py:_resolve_limit` 的注释明确写了这个顺序有讲究。
    `/api/v1/complaints` 是**公开提交**（无需登录），阈值必须是最严的 5；
    若被后缀规则先命中，攻击者就能以更高速率灌库。
    """
    mw = _middleware(
        max_requests=20, window_seconds=60, upload_max=10, generate_max=15, public_write_max=5
    )
    assert mw._resolve_limit("/api/v1/complaints") == 5


def test_upload_tier_matches_id_bearing_path():
    """R7（回归）：上传档必须匹配**带案件 ID 的运行时路径**。

    历史缺陷：`UPLOAD_PATH_SUFFIXES = ("/evidence/cases",)` 用 `endswith` 匹配，
    而路由是 `POST /api/v1/evidence/cases/{case_id}` —— **运行时以案件 ID 结尾**
    ⇒ `endswith` 恒为假 ⇒ **上传档限流从未生效**。
    实测：全量 **40** 个 POST 路由里，上传档命中 **0** 条（认证 3 / 公开 1 / 生成 3 都正常）。

    生成档没这个问题，是因为它的后缀（`/parse`、`/generate`、`/dispatch`）
    恰好是路径**最后一段**；上传档要匹配的是**父路径**。两者不是同一类，
    ⇒ 修法是把上传档改成**前缀**匹配，而不是照抄生成档的写法。
    """
    mw = _middleware(
        max_requests=20, window_seconds=60, upload_max=10, generate_max=15, public_write_max=5
    )
    assert mw._resolve_limit("/api/v1/evidence/cases/42") == 10
    assert mw._resolve_limit("/api/v1/evidence/cases/7") == 10


def test_upload_prefix_does_not_overmatch():
    """R7 配套：改成前缀匹配后不能误伤别的路径。

    少了这条，把前缀写短一点（如 `/api/v1/evidence`）就会静默扩大限流范围，
    把**所有证据读接口**也限上，而它们本不该受限。
    """
    mw = _middleware(
        max_requests=20, window_seconds=60, upload_max=10, generate_max=15, public_write_max=5
    )
    assert mw._resolve_limit("/api/v1/evidence/cases") is None
    assert mw._resolve_limit("/api/v1/other/evidence/cases/1") is None
    # 读取类证据端点不应被上传档命中
    assert mw._resolve_limit("/api/v1/evidence/9") is None


def test_unlisted_post_path_is_not_limited():
    """R3 配套：不在任何档里的 POST 路径必须返回 `None`（不误伤业务写操作）。"""
    mw = _middleware(
        max_requests=20, window_seconds=60, upload_max=10, generate_max=15, public_write_max=5
    )
    assert mw._resolve_limit("/api/v1/cases") is None


# ═══════════════════════ R4–R6 行为 ═══════════════════════


def test_blocks_with_429_after_threshold():
    """R4：超过阈值必须真的返回 429——P0-6 修复的**效果**就这一条。

    前 `limit` 次放行、第 `limit+1` 次拒绝。这条若绿，说明限流真的在拦。
    """
    limit = 3
    client = _app_with_limit(
        enabled=True, max_requests=limit, window_seconds=60,
        upload_max=10, generate_max=15, public_write_max=5,
    )
    url = "/api/v1/auth/register"

    for i in range(limit):
        resp = client.post(url, json={})
        assert resp.status_code == 200, f"第 {i + 1} 次请求被误拦（阈值 {limit}）"

    blocked = client.post(url, json={})
    assert blocked.status_code == 429, "超过阈值后没有返回 429——限流未生效"

    body = blocked.json()
    assert body.get("success") is False
    assert body["error"]["code"] == "RATE_LIMITED", body


@pytest.mark.parametrize("retry_index", [0, 1])
def test_retry_after_header_is_positive_int(retry_index):
    """R6：`Retry-After` 必须是正整数——客户端要直接拿它当等待秒数。

    字符串或 0 会让不同客户端行为不一致（有的当立即重试，有的当永久等待）。
    """
    client = _app_with_limit(
        enabled=True, max_requests=1, window_seconds=60,
        upload_max=10, generate_max=15, public_write_max=5,
    )
    url = "/api/v1/auth/register"
    client.post(url, json={})
    if retry_index:  # 第二次被拒：retry_after 应随窗口推进而变化，但仍须为正
        client.post(url, json={})

    blocked = client.post(url, json={})
    assert blocked.status_code == 429
    raw = blocked.headers.get("Retry-After")
    assert raw is not None, "429 响应缺少 Retry-After 头"
    assert raw.isdigit() and int(raw) >= 1, f"Retry-After 不是正整数：{raw!r}"


def test_get_requests_are_not_limited():
    """R5：只限 POST。

    这是**有意**的设计（中间件注释：避免影响 SSE 流式输出与前端轮询），
    不是缺陷。⇒ 这条用例的作用是**锁住这个取舍**，防止有人「顺手」把 GET 也限上。
    """
    client = _app_with_limit(
        enabled=True, max_requests=1, window_seconds=60,
        upload_max=10, generate_max=15, public_write_max=5,
    )
    for _ in range(5):
        assert client.get("/api/v1/auth/register").status_code == 200


def test_disabled_middleware_passes_through():
    """R5 配套：`enabled=False` 时必须完全放行（开关语义本身要被守住）。

    少了这条，「开关坏了但恒为开」与「开关正常」无法区分。
    """
    client = _app_with_limit(
        enabled=False, max_requests=1, window_seconds=60,
        upload_max=10, generate_max=15, public_write_max=5,
    )
    for _ in range(5):
        assert client.post("/api/v1/auth/register", json={}).status_code == 200


# ---------------------------------------------------------------------------
# R7–R12：`X-Forwarded-For` 的信任判断（2026-09-20，Q-K）
#
# 🚨 这组用例管的是一个**此前完全没有判据**的洞：旧实现无条件返回 XFF 首段，
# 而 XFF 是请求头、客户端可任意伪造 ⇒ "换一个头 = 换一个桶"，20/60s 的注册限流
# 可以被一行 curl 绕开。修复后：只有**直接对端是可信代理**时才采信 XFF，
# 且采信时**从右往左**取第一个不可信的跳（nginx 是追加，伪造值留在左边）。
# ---------------------------------------------------------------------------
def _req(client_host: str | None, xff: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (client_host, 50000) if client_host else None,
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_untrusted_peer_ignores_xff(monkeypatch):
    """R7：对端不在可信集合里 ⇒ **完全无视** XFF（哪怕它长得再像真的）。

    这是整个修复的**主干**：旧实现没有这一层判断。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1")
    from app.middleware import _client_ip

    assert _client_ip(_req("203.0.113.9", "1.1.1.1")) == "203.0.113.9", (
        "不可信对端伪造的 XFF 被采信了 ⇒ 限流桶可被任意更换"
    )


def test_trusted_peer_takes_rightmost_untrusted_hop(monkeypatch):
    """R8：对端可信 ⇒ 取**右起第一个不可信**的跳，而不是首段。

    nginx 用 `$proxy_add_x_forwarded_for` **追加**，客户端伪造的值留在**左边**；
    取首段恰好取到攻击者写的那个值——这正是旧实现的漏洞所在。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1")
    from app.middleware import _client_ip

    assert _client_ip(_req("127.0.0.1", "9.9.9.9, 203.0.113.9")) == "203.0.113.9"
    # 没有伪造、只有代理追加的一跳时也要正确
    assert _client_ip(_req("127.0.0.1", "203.0.113.9")) == "203.0.113.9"


def test_all_hops_trusted_falls_back_to_leftmost(monkeypatch):
    """R9：链上每一跳都是己方代理 ⇒ 没有"不可信跳"可挑，退回最左一跳。

    极端情况（多级内部代理）下不能返回空串或 'unknown'，否则所有人共用一个桶。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,10.0.0.0/8")
    from app.middleware import _client_ip

    assert _client_ip(_req("127.0.0.1", "10.1.2.3, 10.4.5.6")) == "10.1.2.3"


def test_cidr_and_invalid_entries(monkeypatch):
    """R10：CIDR 生效；且**配置里的非法条目不会让请求 500**。

    后者是可用性问题：运维手抖写错一格，不该把整站打挂（对端可能是主机名、
    Unix socket 或 `TestClient` 的 `"testclient"`，本来就不是合法 IP）。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8,not-an-ip,,::1")
    from app.middleware import _client_ip

    # CIDR 命中 ⇒ 采信 XFF
    assert _client_ip(_req("10.0.0.7", "1.1.1.1, 203.0.113.9")) == "203.0.113.9"
    # 非 IP 对端 ⇒ 不信任，且不抛异常
    assert _client_ip(_req("testclient", "1.1.1.1")) == "testclient"


def test_forged_xff_cannot_escape_the_bucket(monkeypatch):
    """R11（**端到端**）：同一真实 IP 换 3 个伪造 XFF，必须仍被同一个桶拦住。

    这是最贴近攻击现场的一条：脚本每次换 `X-Forwarded-For` 重放注册请求。
    旧实现下这 3 组请求会各自开一个新桶 ⇒ 永远打不到 429。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1")
    from starlette.applications import Starlette
    from starlette.routing import Route

    from app.middleware import RateLimitMiddleware

    app = Starlette(routes=[Route("/api/v1/auth/register", _ok, methods=["POST"])])
    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        max_requests=2,
        window_seconds=60,
        backend=MemoryRateLimitBackend(),
    )
    # 对端是**不可信**的普通客户端 IP ⇒ 它伪造的 XFF 一律无效
    client = TestClient(app, client=("203.0.113.9", 50000))

    for i in range(2):
        resp = client.post(
            "/api/v1/auth/register", json={}, headers={"X-Forwarded-For": f"10.0.0.{i}"}
        )
        assert resp.status_code == 200, f"第 {i + 1} 次被误拦"

    blocked = client.post(
        "/api/v1/auth/register", json={}, headers={"X-Forwarded-For": "10.0.0.99"}
    )
    assert blocked.status_code == 429, (
        "换了伪造 XFF 就没被拦 ⇒ 限流可被绕过（旧实现正是这个行为）"
    )


def test_trusted_proxy_still_attributes_per_client(monkeypatch):
    """R12（**反向对照**）：可信代理后面，不同真实客户端**仍要分开计数**。

    ⚠️ 没有这条对照组，上面那条"一律无视 XFF"会被误当成正确实现——
    直接 `return peer` 也能让 R7/R11 通过，但会让整站共用一个桶（限流形同虚设的另一极）。
    这条用例锁住"既不能太松，也不能太严"。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1")
    from starlette.applications import Starlette
    from starlette.routing import Route

    from app.middleware import RateLimitMiddleware

    app = Starlette(routes=[Route("/api/v1/auth/register", _ok, methods=["POST"])])
    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        max_requests=1,
        window_seconds=60,
        backend=MemoryRateLimitBackend(),
    )

    # 两个不同真实客户端，经同一个可信代理进来
    a = TestClient(app, client=("127.0.0.1", 50000))
    b = TestClient(app, client=("127.0.0.1", 50000))
    assert a.post(
        "/api/v1/auth/register", json={}, headers={"X-Forwarded-For": "203.0.113.1"}
    ).status_code == 200
    assert b.post(
        "/api/v1/auth/register", json={}, headers={"X-Forwarded-For": "203.0.113.2"}
    ).status_code == 200, "不同真实客户端被塞进了同一个桶 ⇒ 修复过头了"
