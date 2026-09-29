"""P0-7 令牌承载：refresh 的**传输层**判据（HTTP 响应头，不是 kwargs 字典）。

## 为什么还要再补一个文件

`tests/test_csrf.py::test_cookie_config_is_sane` 已经断言了
`auth_cookies._base_kwargs()["httponly"] is True`。但它测的是**一个字典**，
不是**一条真实响应**：

- 若有人绕过 `set_refresh_cookie()`、直接 `response.set_cookie(...)` 下发 refresh，
  该用例**照样绿**；
- 若 `_issue_and_store()` 漏调 `set_refresh_cookie()`（Cookie 根本没下发），
  该用例**照样绿**；
- 「响应体里回不回传 refresh」也不在它的射程内。

这是与 P0-8「`test_audit_log.py` 只测 `record()` helper、测不到端点是否调用它」
**同一个病**：测了组件，没测接线。故本文件全部走**真实 HTTP 响应**。

## 覆盖清单

| 编号 | 层 | 断言 |
|------|----|------|
| K1 | HTTP 响应头 | refresh Cookie 真实下发且带 `HttpOnly` |
| K2 | HTTP 响应体 | Cookie 模式下**不回传** refresh（不得让 JS 读到长效凭据） |
| K3 | HTTP 响应头 | csrf Cookie **不得** HttpOnly（反向对照：否则双提交失效） |
| K4 | 启动守卫 | 生产 + `AUTH_COOKIE_SECURE=False` ⇒ 拒绝构造配置 |
| K5 | 签发层 | access 有效期 = `ACCESS_TOKEN_EXPIRE_MINUTES`（默认 30 分钟，不是 24 小时） |
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.identity import Tenant


def _uname() -> str:
    return f"ck-{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def engine():
    """模块级引擎：`create_all` 建表开销大，不能每个用例重做一次。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  触发全部模型注册

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"ck_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
async def _tables(engine):
    import app.models  # noqa: F401
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def _seed_tenant(factory) -> None:
    from sqlalchemy import select

    from app.config import settings

    async with factory() as s:
        if not (
            await s.execute(
                select(Tenant).where(Tenant.tenant_id == settings.DEFAULT_TENANT_ID)
            )
        ).scalars().first():
            s.add(Tenant(tenant_id=settings.DEFAULT_TENANT_ID, name="默认租户"))
            await s.commit()


def _login(client, username: str, password: str = "Str0ngPass!"):
    """注册 + 登录，返回登录响应。"""
    client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def _find_cookie(resp, name: str) -> str | None:
    """从 Set-Cookie 里取出指定 Cookie 的完整属性串（大小写不敏感）。"""
    for raw in resp.headers.get_list("set-cookie"):
        if raw.split("=", 1)[0].strip().lower() == name.lower():
            return raw
    return None


@pytest.fixture()
async def http_client(engine, _tables):
    from fastapi.testclient import TestClient

    from app.core.deps import get_db
    from app.main import create_app

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    await _seed_tenant(factory)
    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:
        yield client


@pytest.mark.asyncio
async def test_k1_refresh_cookie_is_httponly_on_the_wire(http_client):
    """K1：真实响应里的 refresh Cookie 必须带 `HttpOnly`。

    只断言 `_base_kwargs()["httponly"]` 是不够的——那条在「绕过 helper 直接
    set_cookie」或「压根没下发 Cookie」时**都保持绿**。
    """
    from app.config import settings

    resp = _login(http_client, _uname())
    assert resp.status_code == 200, f"登录失败：{resp.status_code} {resp.text}"

    raw = _find_cookie(resp, settings.REFRESH_COOKIE_NAME)
    assert raw, (
        f"响应里没有下发 `{settings.REFRESH_COOKIE_NAME}` Cookie。"
        f"实际 Set-Cookie：{resp.headers.get_list('set-cookie')}"
    )
    assert "httponly" in raw.lower(), f"refresh Cookie 缺 HttpOnly：{raw}"


@pytest.mark.asyncio
async def test_k2_refresh_token_not_returned_in_body(http_client):
    """K2：Cookie 模式下响应体不得回传 refresh（否则又变回 JS 可读）。

    这是「HttpOnly 防 XSS」的另一半：即使 Cookie 设对了，只要 body 里还带
    refresh，XSS 照样能拿到长效凭据 —— 而这一半**没有任何既有判据**。
    """
    from app.config import settings

    resp = _login(http_client, _uname())
    assert resp.status_code == 200, f"登录失败：{resp.status_code} {resp.text}"

    data = (resp.json() or {}).get("data") or {}
    assert settings.AUTH_COOKIE_ENABLED, "本用例的前提是 Cookie 模式已启用"
    assert not data.get("refresh_token"), (
        "Cookie 模式下响应体不得回传 refresh_token —— 它会被 XSS 读走，"
        "让 HttpOnly 的防护整体失效"
    )


@pytest.mark.asyncio
async def test_k3_csrf_cookie_must_be_readable_by_js(http_client):
    """K3（反向对照）：csrf Cookie 必须**非** HttpOnly。

    双提交校验要求前端 JS 读出 csrf 再回填到 `X-CSRF-Token`。若有人「顺手」
    把它一起设成 HttpOnly，CSRF 中间件会永远读不到头 ⇒ 所有写请求 403。
    ⇒ 这条不是安全加固，是**防止过度加固把可用性打死**。
    """
    from app.config import settings

    resp = _login(http_client, _uname())
    assert resp.status_code == 200, f"登录失败：{resp.status_code} {resp.text}"

    raw = _find_cookie(resp, settings.CSRF_COOKIE_NAME)
    assert raw, f"响应里没有下发 `{settings.CSRF_COOKIE_NAME}` Cookie"
    assert "httponly" not in raw.lower(), (
        f"csrf Cookie 必须可被 JS 读取（否则无法回填 X-CSRF-Token）：{raw}"
    )


def test_k4_production_rejects_insecure_cookie():
    """K4：生产环境启用 Cookie 但 `SECURE=False` ⇒ 拒绝启动。

    `Secure=False` 会让 refresh 经明文 HTTP 传输，同网络攻击者可直接嗅探，
    **把 HttpOnly 防 XSS 的收益全部抵消**。守卫在 `config.py:208`。
    """
    import os

    from app.config import Settings

    strong = "x" * 40
    try:
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY=strong,
            AUTH_COOKIE_ENABLED=True,
            AUTH_COOKIE_SECURE=False,
            _env_file=None,
        )
    except Exception as exc:  # pydantic 会包一层 ValidationError
        assert "AUTH_COOKIE_SECURE" in str(exc), f"报错信息应点名 AUTH_COOKIE_SECURE：{exc}"
        return
    finally:
        os.environ.pop("AUTH_COOKIE_SECURE", None)
    pytest.fail("生产 + AUTH_COOKIE_SECURE=False 竟然构造成功（守卫缺失）")


def test_k5_issued_access_token_expires_within_30_minutes():
    """K5：签出来的 access 令牌，**实际** `exp - iat` ≤ 30 分钟。

    P0-7 原文是「24 小时长效」。XSS 的最坏后果之所以能从「长期接管」降级为
    「当前标签页 30 分钟内被借用」，靠的正是这个数。

    ⚠️ **为什么不能只断言 `settings.ACCESS_TOKEN_EXPIRE_MINUTES`**（实测教训）：
    该值在 `backend/.env:24` 被覆盖为 30，改类的默认值**根本不生效** ——
    第一版 K5 就是把类默认改成 1440 后跑出来 **绿**，看起来像"注入未命中"，
    其实是**注入没生效**。凡「配置类」判据，注入后必须确认运行时值确实变了；
    更稳的做法是**直接测签出来的令牌**，它绕开了配置来源的优先级问题。
    """
    from app.core.security import create_access_token, decode_token_or_none

    payload = {"sub": "1", "username": "u", "role": "CLIENT", "tenant_id": "platform"}
    claims = decode_token_or_none(create_access_token(payload))

    assert claims, "access 令牌无法解码（判据前提不成立）"
    assert claims.get("type") == "access", f"这不是 access 令牌：{claims.get('type')}"
    lifetime = int(claims["exp"]) - int(claims["iat"])
    assert lifetime <= 30 * 60, (
        f"access 令牌实际有效期 {lifetime}s（{lifetime / 60:.0f} 分钟）超过 30 分钟；"
        "P0-7 的威胁模型按 30 分钟建立，改大等于把 XSS 最坏后果退回「长期接管」"
    )


def test_k6_deployment_config_does_not_extend_access_lifetime():
    """K6：部署配置（`.env` / `.env.example`）同样不得把有效期放大。

    K5 测的是「签出来的令牌」，但**实际部署读的是 `.env`**，而 `.env` 的优先级
    高于类默认值 ⇒ 两者都要钉。缺这一条，只改 `.env` 就能绕过 K5。
    """
    import pathlib

    import app as app_pkg

    backend_root = pathlib.Path(app_pkg.__file__).resolve().parent.parent
    checked = 0
    for name in (".env", ".env.example"):
        path = backend_root / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("ACCESS_TOKEN_EXPIRE_MINUTES="):
                continue
            checked += 1
            value = int(line.split("=", 1)[1].strip())
            assert value <= 60, f"`{name}` 把 access 有效期设成了 {value} 分钟（>60）"
    assert checked, "两个 env 文件里都没有 `ACCESS_TOKEN_EXPIRE_MINUTES`，判据前提不成立"
