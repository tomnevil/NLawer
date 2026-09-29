"""P0-11（CORS 半边）通配 origin 与凭据互斥守卫回归测试。

## 为什么这个文件必须存在

P0-11 的原文是「无 CSRF 防护，且 **CORS 允许凭据 + 通配头**」。
CSRF 那一半有 `test_csrf.py`；**CORS 这一半此前零判据**
（改动前 `grep -rli cors tests/` ⇒ **0 命中**）。

风险不在代码里，而在**配置里**：`app/main.py` 硬编码 `allow_credentials=True`
（Cookie 承载 refresh token 需要它），唯一的开关是 `CORS_ORIGINS`。
而 Starlette 在「通配 origin + 凭据」组合下走的是
`starlette/middleware/cors.py:167`：

```python
if self.allow_all_origins and self.allow_credentials:
    self.allow_explicit_origin(headers, origin)   # 原样回显请求方 origin
```

即**任意站点**都能发起带 Cookie 的跨域请求并读到响应。把 `CORS_ORIGINS=*`
写进生产是极常见的「先让它跑起来」操作——改动前它会**静默**生效，
没有任何用例会红。

修法沿用 P0-3 的同款原则（**显式失败优于静默错误**）：
`main.py::_assert_cors_origins_safe()` 在生产环境直接拒绝启动。

## 覆盖清单

- C1 **干净对照**：非生产 + 通配 ⇒ 允许启动（本地零配置演示不受影响）
- C2 生产 + 通配 ⇒ `ConfigurationError`（核心）
- C3 生产 + 精确白名单 ⇒ 正常启动，且只有白名单内 origin 被回显
- C4 **第三方行为钉子**：非生产 + 通配 ⇒ 确实会回显任意 origin。
  这条**不是**在背书该行为，而是把 Starlette 的行为钉住——
  升级 Starlette 若改变此语义，这条会先红，提醒重新评估 C1 的放行是否仍安全。
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.core.errors import ConfigurationError

WHITELISTED = "http://localhost:3000"
EVIL = "https://evil.example"


# ═══════════════════════ C1 干净对照 ═══════════════════════


def test_non_production_allows_wildcard(monkeypatch):
    """C1：开发环境允许通配（零配置可跑通双产品线演示的前提）。

    它是 C2 的干净对照：如果这条也红，说明 `create_app()` 整体装配失败，
    那 C2 的红就不能归因于「CORS 守卫生效」。
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(settings, "CORS_ORIGINS", "*", raising=False)

    from app.main import create_app

    app = create_app()
    assert app is not None


# ═══════════════════════ C2 生产守卫（核心） ═══════════════════════


def test_production_rejects_wildcard_origin(monkeypatch):
    """C2：生产环境 `CORS_ORIGINS=*` + 硬编码凭据 ⇒ 拒绝启动。"""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(settings, "CORS_ORIGINS", "*", raising=False)

    from app.main import create_app

    with pytest.raises(ConfigurationError) as e:
        create_app()

    assert "CORS_ORIGINS" in f"{getattr(e.value, 'message', '')} {e.value}"


# ═══════════════════════ C3 白名单行为 ═══════════════════════


def test_production_whitelist_echoes_only_allowed_origin(monkeypatch):
    """C3：精确白名单下，白名单内回显、白名单外**不**回显。

    没有这条，「生产一律拒绝跨域」这种过度收紧也能让 C2 全绿。
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(settings, "CORS_ORIGINS", WHITELISTED, raising=False)

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    try:
        ok = client.get("/api/health", headers={"Origin": WHITELISTED})
        bad = client.get("/api/health", headers={"Origin": EVIL})
    finally:
        client.close()

    assert ok.headers.get("access-control-allow-origin") == WHITELISTED, (
        f"白名单内 origin 未回显：{dict(ok.headers)}"
    )
    assert bad.headers.get("access-control-allow-origin") != EVIL, (
        f"白名单外 origin 被回显——跨域白名单失效：{dict(bad.headers)}"
    )


# ═══════════════════════ C4 第三方行为钉子 ═══════════════════════


def test_wildcard_with_credentials_echoes_any_origin(monkeypatch):
    """C4（登记，非背书）：非生产 + 通配 ⇒ Starlette 回显任意 origin。

    见 `starlette/middleware/cors.py:167`。这条存在的意义是：
    **第三方依赖的安全语义变化时能被发现**，而不是把它当成正确行为。
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(settings, "CORS_ORIGINS", "*", raising=False)

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    try:
        resp = client.get("/api/health", headers={"Origin": EVIL})
    finally:
        client.close()

    assert resp.headers.get("access-control-allow-credentials") == "true"
    assert resp.headers.get("access-control-allow-origin") == EVIL, (
        "Starlette 的「通配 + 凭据 ⇒ 回显任意 origin」行为发生变化，"
        "需重新评估 C1 对开发环境放行通配是否仍然安全"
    )
