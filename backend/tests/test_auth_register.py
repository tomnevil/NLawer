"""公开自助注册 `POST /api/v1/auth/register` 的越权回归测试。

## 为什么这个文件必须存在

`app/schemas/auth.py:33` 的 `RegisterRequest` 文档字符串记录了一个**已修复的
越权漏洞**：早期版本曾暴露 `role` / `tenant_id` 字段并直接透传给
`AuthService`，导致任何人可通过公开注册接口自助注册为 `PLATFORM_ADMIN`，
再借 `X-Tenant-Id` 头跨租户读取全平台数据。

修复落地在**两层**（纵深防御）：
1. **入口层**：schema 不再声明 `role` / `tenant_id`，pydantic 默认 `extra=ignore`
   ⇒ 客户端硬塞的字段被丢弃；
2. **服务层**：`AuthService.register()` 对 `role != Role.CLIENT` 抛
   `PermissionDeniedError`，即使将来有人从内部误传也拦得住。

**但这两层此前一条判据都没有** —— `tests/` 下 `grep -ln "register"` 命中的
`test_notification_push.py` / `test_observability.py` 全是 WebSocket 连接管理器的
`ConnectionManager.register`，与注册接口无关（"关键词命中 ≠ 有判据"）。

⇒ 一个已修复的越权漏洞，只要有人把 `role` 加回 schema、或把服务层的校验删掉，
**全量回归照样全绿**。本文件把两层各钉一条判据，并补一条**最贴近攻击者路径**
的 HTTP 层断言（body 里带 `role` 也不生效）。

## 覆盖清单

- T1 入口层：`RegisterRequest` 不得声明 `role` / `tenant_id`
- T2 入口层：body 里硬塞的特权字段被丢弃、不被对象携带
- T3 服务层：`role=PLATFORM_ADMIN` 直接调用服务 ⇒ `PermissionDeniedError`，且未落库
- T4 服务层：默认落到 `CLIENT` + `settings.DEFAULT_TENANT_ID`
- T5 服务层：重名 ⇒ `ConflictError`，用户总数不增加
- T6 服务层：口令必须散列存储（不落明文）
- T7 HTTP 层：body 带 `role="PLATFORM_ADMIN"` 注册 ⇒ 落库仍是 `CLIENT`
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import func, select

from app.core.rbac import Role
from app.core.security import verify_password

# 越权字段清单：只要其中任意一个重新出现在 schema 里，T1 必须转红。
PRIVILEGED_FIELDS = ("role", "tenant_id")


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    """模块级引擎：`create_all` 建表开销大，不能每个用例重做一次。

    库文件落在项目内 `_tmp_tests/`（`tmp_path` 在部分沙箱环境下不可写），
    且**不主动删除**——删除会触发沙箱的批量删除守卫。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  触发全部模型注册

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"reg_{uuid.uuid4().hex[:8]}.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)
    return eng


@pytest.fixture(scope="module")
async def _tables(engine):
    import app.models  # noqa: F401
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


@pytest.fixture()
async def db(engine, _tables):
    """用例级会话；每个用例结束后回滚，避免用例间互相污染。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


async def _ensure_default_tenant(db):
    """注册会把用户挂到 `settings.DEFAULT_TENANT_ID`，该租户必须存在。"""
    from app.config import settings
    from app.models.identity import Tenant

    existing = (
        await db.execute(
            select(Tenant).where(Tenant.tenant_id == settings.DEFAULT_TENANT_ID)
        )
    ).scalars().first()
    if existing:
        return existing
    tenant = Tenant(tenant_id=settings.DEFAULT_TENANT_ID, name="默认租户")
    db.add(tenant)
    await db.flush()
    return tenant


def _uname() -> str:
    return f"u_{uuid.uuid4().hex[:10]}"


async def _count_users(db) -> int:
    from app.models.identity import User

    return (
        await db.execute(select(func.count()).select_from(User))
    ).scalar_one()


# ═══════════════════════ T1 / T2 入口层 ═══════════════════════


def test_schema_declares_no_privileged_fields():
    """T1：`RegisterRequest` 不得声明 `role` / `tenant_id`。

    这是纵深防御的**第一层**。它单独不成立（还有服务层兜底），
    但缺了它，服务层的校验就是**唯一**一道防线——删掉就没了。
    """
    from app.schemas.auth import RegisterRequest

    declared = set(RegisterRequest.model_fields)
    leaked = [f for f in PRIVILEGED_FIELDS if f in declared]
    assert not leaked, (
        f"`RegisterRequest` 重新暴露了特权字段 {leaked}——"
        "这曾是「自助注册为 PLATFORM_ADMIN」越权漏洞的直接成因"
    )


def test_privileged_fields_in_body_are_dropped():
    """T2：客户端硬塞的 `role` / `tenant_id` 必须被丢弃，不得被对象携带。

    注意 pydantic v2 的默认是 `extra="ignore"`（不是 `forbid`），
    所以「不报错」是对的；要断言的是**值没有被对象接管**，
    而不是「构造时抛异常」——把判据写成后者会得到一条永不生效的假绿。
    """
    from app.schemas.auth import RegisterRequest

    payload = RegisterRequest(
        username=_uname(),
        password="Str0ngPass!",
        role="PLATFORM_ADMIN",
        tenant_id="tenant-victim",
    )
    for f in PRIVILEGED_FIELDS:
        assert not hasattr(payload, f), (
            f"body 里的 `{f}` 被 schema 接管了——接口层已可被用于指定角色/租户"
        )


# ═══════════════════════ T3–T6 服务层 ═══════════════════════


@pytest.mark.parametrize(
    "privileged",
    [Role.PLATFORM_ADMIN, Role.FIRM_ADMIN, Role.LAWYER, Role.ENTERPRISE_ADMIN],
)
async def test_service_rejects_privileged_role(db, privileged):
    """T3：`AuthService.register()` 对任何非 CLIENT 角色都必须拒绝。

    这是**第二层**防线：即使有人绕过 schema 从内部调用，也造不出特权账号。
    同时断言**没有落库**——只抛异常但事务里残留一条用户，等于防线漏了一半。
    """
    from app.core.errors import PermissionDeniedError
    from app.services.auth_service import AuthService

    await _ensure_default_tenant(db)
    before = await _count_users(db)

    with pytest.raises(PermissionDeniedError):
        await AuthService(db).register(
            username=_uname(), password="Str0ngPass!", role=privileged
        )

    assert await _count_users(db) == before, "拒绝之后仍落库了一条用户"


async def test_service_defaults_to_client_and_default_tenant(db):
    """T4：正常注册固定落到 `CLIENT` + 默认租户。"""
    from app.config import settings
    from app.services.auth_service import AuthService

    await _ensure_default_tenant(db)
    user = await AuthService(db).register(
        username=_uname(), password="Str0ngPass!", full_name="张三"
    )

    assert user.role == Role.CLIENT
    assert user.tenant_id == settings.DEFAULT_TENANT_ID
    assert user.full_name == "张三"


async def test_duplicate_username_conflicts(db):
    """T5：重名必须冲突，且用户总数不增加（不能造出第二个同名账号）。"""
    from app.core.errors import ConflictError
    from app.services.auth_service import AuthService

    await _ensure_default_tenant(db)
    name = _uname()
    svc = AuthService(db)
    await svc.register(username=name, password="Str0ngPass!")
    after_first = await _count_users(db)

    with pytest.raises(ConflictError):
        await svc.register(username=name, password="An0therPass!")

    assert await _count_users(db) == after_first


async def test_password_stored_hashed(db):
    """T6：口令必须散列存储，明文不得出现在任何一列的可读形式上。"""
    from app.services.auth_service import AuthService

    await _ensure_default_tenant(db)
    raw = "Str0ngPass!"
    user = await AuthService(db).register(username=_uname(), password=raw)

    assert user.hashed_password != raw, "口令以明文落库"
    assert verify_password(raw, user.hashed_password), "散列后无法再校验（口令不可用）"


# ═══════════════════════ T7 HTTP 层（攻击者路径） ═══════════════════════


async def test_endpoint_ignores_client_supplied_role(engine, _tables):
    """T7：body 里带 `role="PLATFORM_ADMIN"` 走完整 HTTP 链路，落库仍须是 CLIENT。

    这是**最贴近攻击者路径**的一条——前六条都测的是「组件」，
    这一条测的是「接口」。接口层的字段过滤与服务层的校验之间
    还隔着依赖注入、中间件、序列化，任何一环把 `role` 透传进去，
    前六条都会全绿而漏洞复活。
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_db
    from app.main import create_app
    from app.models.identity import Tenant, User

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    username = _uname()
    with TestClient(app) as client:
        # 先确保默认租户存在（走 HTTP 之外的通路，与被测逻辑无关）
        async with factory() as s:
            from app.config import settings

            if not (
                await s.execute(
                    select(Tenant).where(Tenant.tenant_id == settings.DEFAULT_TENANT_ID)
                )
            ).scalars().first():
                s.add(Tenant(tenant_id=settings.DEFAULT_TENANT_ID, name="默认租户"))
                await s.commit()

        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": "Str0ngPass!",
                "role": "PLATFORM_ADMIN",
                "tenant_id": "tenant-victim",
            },
        )

    assert resp.status_code in (200, 201), f"注册失败：{resp.status_code} {resp.text}"

    async with factory() as s:
        user = (
            await s.execute(select(User).where(User.username == username))
        ).scalars().one_or_none()

    assert user is not None, "接口返回成功但用户没落库"
    assert user.role == Role.CLIENT, (
        f"越权复活：body 里的 role 生效了，落库为 {user.role}"
    )


async def test_real_stack_forged_xff_cannot_split_the_bucket(engine, _tables, monkeypatch):
    """T8：**完整装配**下复验 XFF 修复——伪造头换不来新桶。

    ## 为什么还要这一条（R7–R12 已经有了）

    R7–R12 跑的是**最小 Starlette 应用**，那里只装了 `RateLimitMiddleware` **一个零件**。
    真实的 `create_app()` 还叠着 ErrorHandler / RequestContext / CSRF / CORS，
    且 `main.py` 是**按 settings 现取现装**的。任何一层改写 scope、提前 return，
    或装配时读的是**另一个**限流实现，都会让"零件对、装起来不对"。
    ⇒ 这条是从「零件有判据」到「整机有判据」的桥。

    ## 攻击现场

    公开自助注册（Q-A）下，脚本每次换一个 `X-Forwarded-For` 重放注册请求。
    修复前：每个伪造头一个新桶 ⇒ 永远打不到 429。
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import settings
    from app.core.deps import get_db
    from app.main import create_app
    from app.models.identity import Tenant

    # 阈值调小：真实装配下每条请求都要走完整栈，21 条太慢。
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_MAX", 2)
    # 对端是**不可信**的普通客户端 ⇒ 它带来的 XFF 一律无效
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1")

    app = create_app()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db

    async with factory() as s:
        if not (
            await s.execute(
                select(Tenant).where(Tenant.tenant_id == settings.DEFAULT_TENANT_ID)
            )
        ).scalars().first():
            s.add(Tenant(tenant_id=settings.DEFAULT_TENANT_ID, name="默认租户"))
            await s.commit()

    with TestClient(app, client=("203.0.113.9", 50000)) as client:
        for i in range(2):
            resp = client.post(
                "/api/v1/auth/register",
                json={"username": _uname(), "password": "Str0ngPass!"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            )
            assert resp.status_code in (200, 201), f"第 {i + 1} 次被误拦：{resp.text}"

        # 换一个全新的伪造头：修复前这里会开一个新桶 ⇒ 200
        blocked = client.post(
            "/api/v1/auth/register",
            json={"username": _uname(), "password": "Str0ngPass!"},
            headers={"X-Forwarded-For": "10.9.9.9"},
        )
        assert blocked.status_code == 429, (
            "真实装配下换了伪造 XFF 仍未被拦 ⇒ 限流仍可绕过"
        )
