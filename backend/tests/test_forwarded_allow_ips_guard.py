"""`FORWARDED_ALLOW_IPS=*` 生产守卫（Q-K 修复的下游）。

## 为什么这个文件必须存在

`middleware.py::_client_ip` 已经做了「仅当直接对端可信才采信 XFF」的判断（R7–R12），
但那个判断发生在**应用层**。在它之前，uvicorn 的 `ProxyHeadersMiddleware`
（`uvicorn/middleware/proxy_headers.py:32-40,68`）会先跑：

- `always_trust`（`forwarded_allow_ips` 含 `*`）⇒ 取
  **`x_forwarded_for_hosts[0]`** —— **左起第一个，客户端可随便写**；
- 然后 **改写 `scope["client"]`**。

于是应用层拿到的"直接对端"已经是攻击者伪造的值，`TRUSTED_PROXIES` 判断根本不成立，
**每换一个头 = 换一个限流桶** ⇒ 应用层的修复被完全架空。而且这个开关在
**网关 / CDN / ALB 后面**被设成 `*` 是极常见的操作，失效是**静默**的。

⇒ 与 P0-11 的 CORS 守卫同款原则：**显式失败优于静默错误**。

## 覆盖清单

- F1 生产环境 `FORWARDED_ALLOW_IPS=*` ⇒ 拒绝启动（**核心**）
- F2 非生产环境允许（**干净对照**：它若也红，说明 `create_app()` 整体装配失败，
  F1 的红就不能归因于守卫生效）
- F3 精确白名单（含 `* ` 之外的多值）不误伤
- F4 未设置该环境变量 ⇒ 不干预（大多数人根本不设它）
- F5 **反向对照**：生产环境的红**不是**"生产一律拒绝启动"——
  换成精确的 `FORWARDED_ALLOW_IPS` 后必须能正常装配
"""
from __future__ import annotations

import pytest

# ⚠️ 必须**模块级**导入：`app/main.py` 末尾有模块级 `app = create_app()`，
# 守卫在 import 阶段就会跑。若等到用例里再 import，第一条用例（F1）会
# 在 `with pytest.raises` **之外**抛异常 ⇒ 用例变成依赖执行顺序。
# （`test_cors_credentials_guard.py::C2` 之所以绿，是因为同文件的 C1 先跑过、
#   把模块导入缓存了——同款顺序依赖，只是恰好成立。）
import app.main  # noqa: F401
from app.config import settings
from app.core.errors import ConfigurationError

EXPLICIT = "10.0.0.1,10.0.0.2"


def _set(monkeypatch, *, env: str, forwarded: str | None) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", env, raising=False)
    if forwarded is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", forwarded)


def test_production_rejects_wildcard(monkeypatch):
    """F1（核心）：生产环境下 `FORWARDED_ALLOW_IPS=*` ⇒ 拒绝启动。

    ⚠️ **必须先 import 再改环境**：`app/main.py` 末尾有**模块级** `app = create_app()`，
    守卫在 **import 阶段**就会跑一次。若先设了 `*` 再 import，异常会抛在
    `with pytest.raises` **之外**，用例结构就依赖 import 顺序了（别人先 import 过才绿）。
    """
    from app.main import create_app  # noqa: F401  先安全导入

    _set(monkeypatch, env="production", forwarded="*")

    with pytest.raises(ConfigurationError) as e:
        create_app()

    assert "FORWARDED_ALLOW_IPS" in f"{getattr(e.value, 'message', '')} {e.value}"


def test_non_production_allows_wildcard(monkeypatch):
    """F2：非生产环境只是告警，不阻断（否则本地演示会起不来）。"""
    _set(monkeypatch, env="development", forwarded="*")

    from app.main import create_app

    assert create_app() is not None


def test_explicit_allowlist_is_not_blocked(monkeypatch):
    """F3：精确白名单（含 `*` 之外的多值与空格）不误伤。"""
    _set(monkeypatch, env="production", forwarded=f" {EXPLICIT} , 127.0.0.1 ")

    from app.main import create_app

    assert create_app() is not None


def test_unset_env_is_ignored(monkeypatch):
    """F4：未设置该变量 ⇒ 不干预（uvicorn 默认 127.0.0.1，本就安全）。"""
    _set(monkeypatch, env="production", forwarded=None)

    from app.main import create_app

    assert create_app() is not None


def test_production_still_boots_with_explicit_allowlist(monkeypatch):
    """F5（**反向对照**）：生产环境能启动，只是不接受 `*`。

    ⚠️ 缺了这条，「生产环境一律拒绝启动」这种**过度收紧**也能让 F1 全绿——
    那时守卫就变成了"生产不可用"，比没有守卫更糟。
    """
    _set(monkeypatch, env="production", forwarded=EXPLICIT)

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code in (200, 503)
