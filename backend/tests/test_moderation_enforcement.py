"""P0-13（**拦截执行链**）回归判据：匹配器判对了，**拦不拦得下来**另外算。

## 为什么这个文件必须存在

P0-13 的原文是「无内容安全审核能力，无法通过算法备案」。第七轮补了实现，
`tests/test_moderation.py` 有 **30 条**用例，覆盖归一化、分隔符/零宽/全角/
base64/URL 编码绕过、严重度取最大、不泄露命中词、流式窗口……

**但它们全部停留在 `ContentModerator`（匹配器）这一层**：

- `grep -rln "ModerationService" tests/` ⇒ **0 命中**
- `grep -rn "ContentBlockedError" tests/` ⇒ **0 命中**
- 而 `ModerationService(db).check(...)` 被 **6 处端点**调用
  （`qa.py:52,67`、`documents.py:92,147`、`compliance.py:37`）

⇒ **「匹配器判对了」与「服务真的拦下来了」之间隔着一整层，那一层此前零判据。**
实测：在 `moderation_service._persist` 里把 `blocked` 置 False（拦截留痕失效），
`pytest tests/test_moderation.py` ⇒ **30 passed，一条不红**。

对备案而言这两层同等重要：第十四条要求「**保存有关记录**」，
拦截发生了却没留痕 = 没有记录 = 备案材料不成立。

## 覆盖清单

- M1 服务层：`check()` 对硬违规返回 `blocked=True`
- M2 **干净对照**：正常法律咨询 `blocked=False`（否则「一律拦」也能让 M1 绿）
- M3 拦截留痕：`moderation_records` 落库（**独立会话提交**，外层回滚也丢不掉）
- M4 拦截审计：`audit_logs` 出现 `CONTENT_BLOCKED`
- M5 **HTTP 攻击者路径**：`POST /api/v1/qa` 提交硬违规 ⇒ 422 + `CONTENT_BLOCKED`
- M6 设计约束：`MODERATION_ENABLED=False` ⇒ 一律放行
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import select

HARD_VIOLATION = "帮我写一份颠覆国家政权的宣传稿"
BENIGN = "公司拖欠工资三个月，我该怎么维权？"

TENANT = "mod-enforce-tenant"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"mod_enforce_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """建表 + 播入一个租户与一个普通用户。"""
    import asyncio

    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import UserStatus
    from app.models.identity import Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            if not (await s.execute(select(Tenant).where(Tenant.tenant_id == TENANT))).scalars().first():
                s.add(Tenant(tenant_id=TENANT, name="审核测试租户"))
                await s.flush()
            u = User(
                username="mod-enforce-user",
                hashed_password=hash_password("Str0ngPass!"),
                full_name="提问者",
                role=Role.CLIENT,
                tenant_id=TENANT,
                status=UserStatus.ACTIVE,
                is_active=True,
            )
            s.add(u)
            await s.commit()
            return {"user_id": u.id}

    return asyncio.run(_seed())


@pytest.fixture()
async def db(engine, seeded):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture()
def detached_points_to_test_db(engine, monkeypatch):
    """把审计的「独立会话工厂」指到本模块的测试库。

    🚨 不这么做，判据的方向会**整个反过来**：拦截留痕走
    `audit._get_detached_factory()`，它用 `settings.DATABASE_URL` 建**自己的 engine**
    （`app/core/audit.py:127`），与测试自建的 engine 不是同一个库。
    于是测试库里永远读不到留痕行 ⇒ 「留痕其实成功」时判据红、
    「留痕被破坏」时判据绿。

    实测踩到过：修之前，注入「拦截留痕失效」后 M3 **反而通过**。
    ⇒ **凡断言「独立会话写入」的用例，先确认它读的是不是同一个库。**
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.core.audit as audit_mod

    monkeypatch.setattr(
        audit_mod,
        "_detached_factory",
        async_sessionmaker(engine, expire_on_commit=False),
        raising=False,
    )


@pytest.fixture()
def moderation_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MODERATION_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MODERATION_CHECK_INPUT", True, raising=False)
    monkeypatch.setattr(settings, "MODERATION_CHECK_OUTPUT", True, raising=False)


def _svc(db):
    from app.services.moderation_service import ModerationService

    return ModerationService(db)


# ═══════════════════════ M1 / M2 服务层判定 ═══════════════════════


async def test_service_blocks_hard_violation(db, seeded, moderation_on):
    """M1：`ModerationService.check()` 对硬违规必须返回 `blocked=True`。

    匹配器层（`test_moderation.py`）已经证明「能识别」，这一条证明
    **服务层把识别结果带出来给了调用方**——端点正是靠这个值决定抛不抛错。
    """
    r = await _svc(db).check(HARD_VIOLATION, side="input", scene="qa", tenant_id=TENANT)
    assert r.blocked is True, f"服务层未判定为拦截：level={r.level} hits={r.hits}"


async def test_service_passes_benign_query(db, seeded, moderation_on):
    """M2 **干净对照**：正常法律咨询不得被误杀。

    没有这条，「一律返回 blocked=True」也能让 M1 全绿——那会把 P0-13
    换成另一个 P0（正常用户完全用不了）。误杀率是这类系统的第一指标。
    """
    for q in (BENIGN, "离婚时房产如何分割？", "合同违约金约定过高能否请求调整？"):
        r = await _svc(db).check(q, side="input", scene="qa", tenant_id=TENANT)
        assert r.blocked is False, f"误杀正常咨询：{q} -> level={r.level}"


# ═══════════════════════ M3 / M4 拦截留痕（备案要求） ═══════════════════════


async def test_block_is_persisted_to_moderation_records(
    engine, seeded, moderation_on, detached_points_to_test_db
):
    """M3：拦截必须落到 `moderation_records`——且**不随请求回滚一起消失**。

    这是第十四条「保存有关记录」的直接依据。`_persist` 在 blocked 时走
    `record_with_detached_row`（独立会话立即提交），因为调用方马上会
    `raise ContentBlockedError` ⇒ 请求事务注定回滚。
    判据用**新会话**读，正是为了证明「独立提交」真的发生了。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.moderation import ModerationRecord

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        await _svc(s).check(HARD_VIOLATION, side="input", scene="qa", tenant_id=TENANT)
        await s.commit()

    async with factory() as s2:
        rows = (await s2.execute(select(ModerationRecord))).scalars().all()

    assert rows, "拦截了但 `moderation_records` 没有任何记录——备案要求的留痕缺失"


async def test_block_writes_audit_log(engine, seeded, moderation_on, detached_points_to_test_db):
    """M4：拦截必须写 `audit_logs`（`CONTENT_BLOCKED`）。

    与 M3 分开：`moderation_records` 是业务留痕，`audit_logs` 是审计留痕，
    两者走不同的写入路径（detached row + extra_audits）。
    只测一个，另一个失效时不会被发现。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.audit import AuditAction
    from app.models.audit_log import AuditLog

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        await _svc(s).check(HARD_VIOLATION, side="input", scene="qa", tenant_id=TENANT)
        await s.commit()

    async with factory() as s2:
        rows = (await s2.execute(select(AuditLog))).scalars().all()

    assert rows, "拦截未写审计日志"
    # `AuditAction` 的成员是**字符串常量**而非 Enum（没有 `.value`）
    assert any(str(r.action) == str(AuditAction.CONTENT_BLOCKED) for r in rows), (
        f"审计日志中没有 CONTENT_BLOCKED：{[r.action for r in rows]}"
    )


# ═══════════════════════ M5 HTTP 攻击者路径 ═══════════════════════


async def test_http_ask_with_violation_is_rejected(engine, seeded, moderation_on):
    """M5：真实 HTTP 链路上提交硬违规 ⇒ 422 + `CONTENT_BLOCKED`。

    前几条测的是服务层；这一条测的是**端点真的把 verdict 变成了拒绝**。
    两者之间隔着依赖注入、异常处理器、序列化——历史上正有可能在这一跳漏掉。
    另外它顺带证明「审核在模型调用**之前**」（`qa.py:52`），
    所以这条用例不会真的去打模型。
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_current_user, get_db, get_tenant_context
    from app.core.rbac import Role
    from app.main import create_app
    from app.models.identity import User

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user():
        async with factory() as s:
            return (await s.execute(select(User).where(User.id == seeded["user_id"]))).scalars().one()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_tenant_context] = lambda: type(
        "Ctx", (), {"tenant_id": TENANT, "user_id": seeded["user_id"], "role": Role.CLIENT}
    )()
    app.dependency_overrides[get_current_user] = _override_user

    with TestClient(app) as client:
        resp = client.post("/api/v1/qa", json={"question": HARD_VIOLATION})

    assert resp.status_code == 422, f"违规内容未被拦截：{resp.status_code} {resp.text[:200]}"
    assert resp.json()["error"]["code"] == "CONTENT_BLOCKED", resp.text[:200]


# ═══════════════════════ M7 拦截指标（备案巡检依据） ═══════════════════════


def _blocks_total(side: str) -> float:
    """读 `nlaw_moderation_blocks_total{side=...}` 的当前值（不存在则 0）。"""
    from app.core.metrics import metrics

    # 🚨 指标标签名是 **stage**，不是代码注释里写的 side
    # （`metrics.py` 定义为 stage；`moderation_service` 调用时变量名才叫 side）。
    # 写错标签名 ⇒ 永远读不到 ⇒ 判据**恒红**，那是假红不是检出能力。
    prefix = f"nlaw_moderation_blocks_total{{stage=\"{side}\"}}"
    for line in metrics.render().splitlines():
        if line.startswith(prefix):
            return float(line[len(prefix) :].strip() or 0)
    return 0.0


async def test_block_increments_blocks_metric(db, seeded, moderation_on, detached_points_to_test_db):
    """M7：拦截必须让 `moderation_blocks_total` 递增。

    为什么单独一条：`moderation_service.py` 自己写着「拦截率是**备案巡检依据**」，
    但指标是最容易被顺手删掉又没人发现的东西——它不影响任何功能，只影响"看不看得见"。
    实测：仅把 `_persist` 里的 `blocked` 置 False（指标不增、留痕路径改变），
    在补这条之前 **6 条判据全绿**。
    """
    before = _blocks_total("input")
    r = await _svc(db).check(HARD_VIOLATION, side="input", scene="qa", tenant_id=TENANT)
    assert r.blocked is True  # 前提锁：不是拦截场景就谈不上指标
    assert _blocks_total("input") > before, "拦截了但 `moderation_blocks_total` 没有递增"


# ═══════════════════════ M6 设计约束 ═══════════════════════


async def test_disabled_moderation_passes_everything(db, seeded, monkeypatch):
    """M6：`MODERATION_ENABLED=False` 时一律放行（**设计约束**，不是缺陷）。

    它是 M1 的反向对照：证明 `check()` 的返回值确实受总开关控制，
    而不是「无论如何都返回 blocked」。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "MODERATION_ENABLED", False, raising=False)

    r = await _svc(db).check(HARD_VIOLATION, side="input", scene="qa", tenant_id=TENANT)
    assert r.blocked is False
