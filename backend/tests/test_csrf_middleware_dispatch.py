"""CSRF **中间件整机**的判据（§3.21，2026-09-20）。

## 为什么这个文件必须存在

`tests/test_csrf.py` 有 10 条用例，看上去 CSRF 覆盖得很充分。但逐条看下来，
它们测的全是**令牌原语**：`generate_csrf_token` / `validate_csrf_token` /
过期 / 伪造签名 / Cookie kwargs。

实测（本轮）：

| 事实 | 实测 |
|------|------|
| `tests/` 下对 `/api/v1/auth/refresh` 的请求 | **0** |
| `tests/` 下对 `/api/v1/auth/logout` 的请求 | **0** |
| `tests/` 下出现 `X-CSRF-Token` | 只在 `test_auth_cookie_transport.py` 的**注释**里 |
| `CsrfMiddleware` / `_needs_check` 被测试引用 | **0** |

⇒ **零件（令牌算法）测了 10 条，整机（中间件拦不拦）一条没有。**
`CSRF_PROTECTED_PREFIXES` 保护的正好就是这两个端点，而它们从未被请求过——
也就是说「中间件到底有没有挂上、有没有真的拦」此前**无人验证**。

这与 §3.14（权限门控从未被执行）是同一类问题：**测了算法，没测装配。**

## 覆盖清单

| 编号 | 性质 |
|------|------|
| C1 | **整机**：无令牌 POST `/api/v1/auth/refresh` ⇒ 403 `CSRF_TOKEN_INVALID` |
| C2 | **反向量**：带合法双提交令牌 ⇒ **不是** CSRF 拒绝（防中间件焊死） |
| C3 | cookie 与 header 值不一致 ⇒ 403（双提交的意义） |
| C4 | `_needs_check`：Bearer 请求**豁免**（浏览器不会自动带 Authorization 头） |
| C5 | `_needs_check`：`strict_all_writes=True` ⇒ 非保护前缀的写也要查 |
| C6 | `_needs_check`：`enabled=False` ⇒ 一律不查（**开关一关就真的全不查**） |
| C7 | **登记型**：默认 `CSRF_PROTECTED_PREFIXES` 必须覆盖 refresh / logout |

## 关于 C7

`CSRF_STRICT_ALL_WRITES` 默认 `False`，所以中间件的适用范围**完全**等于
`CSRF_PROTECTED_PREFIXES`。这个配置一旦被置空，CSRF 防护**整体消失且不报错**——
既没有启动告警，也没有测试变红。C7 把它钉住。
"""
from __future__ import annotations

import pytest


def _request(*, method="POST", path="/api/v1/auth/refresh", headers=None):
    """构造一个最小可用的 Starlette `Request`（只用到 method / path / headers）。"""
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if not any(k == b"host" for k, _ in raw):
        raw.append((b"host", b"testserver"))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": raw,
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def _middleware(**kw):
    from app.middleware import CsrfMiddleware

    params = {
        "app": None,
        "enabled": True,
        "protected_prefixes": ("/api/v1/auth/refresh", "/api/v1/auth/logout"),
        "strict_all_writes": False,
        "header_name": "X-CSRF-Token",
    }
    params.update(kw)
    return CsrfMiddleware(**params)


def _valid_token(session_id: str = "") -> str:
    from app.core.csrf import generate_csrf_token

    return generate_csrf_token(session_id)


# ═══════════════════════ C1–C3 整机（真实 create_app） ═══════════════════════


@pytest.fixture
def app(monkeypatch):
    """真实 `create_app()`，但**关掉限流**。

    ⚠️ 关限流是为了隔离被测对象：本文件的 3 个 HTTP 用例都打
    `/api/v1/auth/refresh`，而该路径正在认证档限流名单里
    （`test_rate_limit.py:96`）⇒ 不关会因为「撞桶」而假红，
    把限流的行为误算到 CSRF 头上。

    CsrfMiddleware 的挂载由 `AUTH_COOKIE_ENABLED` 决定，**不受此开关影响**。
    """
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False, raising=False)
    application = create_app()

    mounted = [m.cls.__name__ for m in application.user_middleware]
    assert "CsrfMiddleware" in mounted, (
        f"CSRF 中间件没挂上（AUTH_COOKIE_ENABLED={settings.AUTH_COOKIE_ENABLED}）：{mounted}"
    )
    # 不用 with：不触发 lifespan（避免 alembic 迁移）
    return TestClient(application)


def test_refresh_without_token_is_rejected(app):
    """C1：**整机**——无令牌 POST 受保护端点 ⇒ 403 且错误码是 CSRF 的。

    只断言 403 不够（401/422 也可能是 40x），必须钉住 `error.code`，
    否则「CSRF 没拦、是被别的东西拒了」也会被算成通过。
    """
    resp = app.post("/api/v1/auth/refresh")

    assert resp.status_code == 403, f"CSRF 没拦住：{resp.status_code} {resp.text[:200]}"
    assert resp.json()["error"]["code"] == "CSRF_TOKEN_INVALID", resp.text[:200]


def test_valid_double_submit_passes_csrf(app):
    """C2（**反向量**）：带合法双提交令牌 ⇒ **不是** CSRF 拒绝。

    没有这条，把 `dispatch` 改成一律 403 上面一条依然全绿，
    而正常的 refresh 其实已经彻底不可用。
    ⚠️ 刻意不断言「必须是 200」：refresh Cookie 不存在时端点会按自己的逻辑
    返回 401/422，那**属于通过 CSRF 之后**的事，不该由本文件判定。
    """
    token = _valid_token()
    resp = app.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": token},
        cookies={"nlaw_csrf": token},
    )

    code = (resp.json().get("error") or {}).get("code")
    assert code != "CSRF_TOKEN_INVALID", (
        f"合法双提交令牌被 CSRF 拒绝 ⇒ 中间件焊死：{resp.status_code} {resp.text[:200]}"
    )


def test_mismatched_cookie_and_header_rejected(app):
    """C3：cookie 与 header 值不一致 ⇒ 403。

    双提交的意义就在「攻击者写不进受害者的 Cookie，也读不出来」。
    若中间件只检查「两个都存在」而不比内容，这一条会红。
    """
    resp = app.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": _valid_token()},
        cookies={"nlaw_csrf": _valid_token()},  # 另一枚合法但不同的令牌
    )
    assert resp.status_code == 403, f"不匹配的双提交被放行：{resp.status_code}"
    assert resp.json()["error"]["code"] == "CSRF_TOKEN_INVALID"


# ═══════════════════════ C4–C6 `_needs_check` 分支 ═══════════════════════


def test_bearer_requests_are_exempt():
    """C4：带 `Authorization` 的请求豁免 CSRF。

    依据是「浏览器不会自动带上 Authorization 头」——CSRF 的攻击前提不成立。
    ⚠️ 成对判据：同路径**不带**该头必须查（下一条断言），
    否则把 `_needs_check` 改成恒 False 也能让这条通过。
    """
    mw = _middleware()

    assert mw._needs_check(_request(headers={"Authorization": "Bearer x"})) is False
    # 反向量：同一个受保护路径，没有 Bearer 就必须查
    assert mw._needs_check(_request()) is True


def test_strict_all_writes_covers_unprotected_paths():
    """C5：`strict_all_writes=True` ⇒ 保护前缀之外的写方法也要查。

    默认 `False`（前端尚未全面携带令牌），但生产上打算开启时必须确认这条路径
    是通的——否则开了开关以为全面防护，实际一个都没查。
    """
    loose = _middleware(strict_all_writes=False)
    strict = _middleware(strict_all_writes=True)

    unprotected = _request(method="POST", path="/api/v1/cases")
    assert loose._needs_check(unprotected) is False, "默认口径下不应查未登记路径"
    assert strict._needs_check(unprotected) is True, "strict_all_writes 开启后应查所有写方法"

    # GET 永远不查（CSRF 只对改变状态的请求有意义）
    assert strict._needs_check(_request(method="GET", path="/api/v1/cases")) is False


def test_disabled_middleware_checks_nothing():
    """C6：`enabled=False` ⇒ 一律不查。

    这不是「期望的部署状态」，而是**登记现状**：关掉开关就是完全不查，
    没有第二道防线。将来若有人加了兜底（例如对 Cookie 请求强制校验），
    这里会红，强制回头确认是否要同步调整开关语义。
    """
    mw = _middleware(enabled=False)

    assert mw._needs_check(_request()) is False
    assert mw._needs_check(_request(headers={"Authorization": "Bearer x"})) is False
    assert mw._needs_check(_request(method="POST", path="/api/v1/cases")) is False


# ═══════════════════════ C7 配置登记 ═══════════════════════


def test_default_protected_prefixes_cover_refresh_and_logout():
    """C7：**登记型**——默认保护前缀必须覆盖 refresh 与 logout。

    `CSRF_STRICT_ALL_WRITES` 默认 `False` ⇒ 中间件的作用域**完全**等于这个配置。
    它一旦被置空，CSRF 防护整体消失，且**不报错、无告警、测试不变红**——
    与本轮反复遇到的「静默失效」同一形态。
    """
    from app.config import settings

    prefixes = [p.strip() for p in settings.CSRF_PROTECTED_PREFIXES.split(",") if p.strip()]

    assert prefixes, "保护前缀为空 ⇒ CSRF 中间件形同虚设，且不报错"
    assert "/api/v1/auth/refresh" in prefixes, f"refresh 未受保护：{prefixes}"
    assert "/api/v1/auth/logout" in prefixes, f"logout 未受保护：{prefixes}"
