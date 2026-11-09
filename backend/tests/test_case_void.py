"""A9 案件作废入口回归测试（`POST /api/v1/cases/{case_id}/void`）。

作废是「责任链的终止动作」，一旦写错会同时影响四处：
1. **权限**：客户不能作废自己的案件；其他律师即使有团队只读授权也不能处置；
   跨租户必须 404（不能泄露案件是否存在）。
2. **幂等**：前端重试 / 用户双击不能把在手案件数回减两次，也不能重复记事件。
3. **状态机**：ARCHIVED / CLOSED 是归档终态，不允许二次终止。
4. **连带清理**：PENDING 派单要退出抢单池，未完成复核要退出复核台，
   且都要留痕——否则「作废」会留下一堆永远没人处理的悬挂任务。

本文件对以上四点分别做**正向 + 反向**断言：只断言「被拒绝」会让
「一律 403/404」的错误实现蒙混过关，因此正向路径（承办律师 / 本所管理员
可以作废）与副作用（回减、事件、派单、复核）同时被钉住。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest

TENANT_A = "void-a"
TENANT_B = "void-b"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"case_void_{uuid.uuid4().hex[:8]}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture(scope="module")
def seeded(_engine):
    """用户：本所管理员 / 承办律师甲 / 其他律师乙 / 客户；另在租户 B 放一个律师。

    返回 id 映射；**具体案件由各用例自建**，避免用例之间互相污染状态。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.rbac import Role
    from app.models.identity import LawyerProfile, User

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _seed() -> dict[str, int]:
        async with factory() as db:
            admin = User(
                tenant_id=TENANT_A,
                username=f"void_admin_{uuid.uuid4().hex[:6]}",
                hashed_password="x",
                full_name="本所管理员",
                role=Role.FIRM_ADMIN,
            )
            lawyer_a = User(
                tenant_id=TENANT_A,
                username=f"void_law_a_{uuid.uuid4().hex[:6]}",
                hashed_password="x",
                full_name="承办律师甲",
                role=Role.LAWYER,
            )
            lawyer_b = User(
                tenant_id=TENANT_A,
                username=f"void_law_b_{uuid.uuid4().hex[:6]}",
                hashed_password="x",
                full_name="其他律师乙",
                role=Role.LAWYER,
            )
            client = User(
                tenant_id=TENANT_A,
                username=f"void_client_{uuid.uuid4().hex[:6]}",
                hashed_password="x",
                full_name="客户",
                role=Role.CLIENT,
            )
            foreign_lawyer = User(
                tenant_id=TENANT_B,
                username=f"void_foreign_{uuid.uuid4().hex[:6]}",
                hashed_password="x",
                full_name="外所律师",
                role=Role.LAWYER,
            )
            db.add_all([admin, lawyer_a, lawyer_b, client, foreign_lawyer])
            await db.flush()
            for uid in (lawyer_a.id, lawyer_b.id, foreign_lawyer.id):
                db.add(
                    LawyerProfile(
                        user_id=uid,
                        tenant_id=TENANT_A if uid != foreign_lawyer.id else TENANT_B,
                        available=True,
                        can_l3_review=False,
                        active_case_count=0,
                    )
                )
            await db.commit()
            return {
                "admin": admin.id,
                "lawyer_a": lawyer_a.id,
                "lawyer_b": lawyer_b.id,
                "client": client.id,
                "foreign_lawyer": foreign_lawyer.id,
            }

    return asyncio.run(_seed())


# ═══════════════════════ 构造与查询辅助 ═══════════════════════


def _run(coro):
    return asyncio.run(coro)


def _make_case(
    engine,
    *,
    tenant_id: str = TENANT_A,
    lawyer_id: int | None = None,
    client_id: int | None = None,
    status=None,
    with_pending_dispatch: bool = False,
    with_open_review: bool = False,
) -> int:
    """建一个案件（可选带 PENDING 派单 / 未完成复核），返回 case_id。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.case import Case, Dispatch
    from app.models.enums import (
        CaseGrade,
        CaseStatus,
        DispatchMode,
        DispatchStatus,
        ReviewStatus,
        ReviewTargetType,
    )
    from app.models.review import Review

    factory = async_sessionmaker(engine, expire_on_commit=False)
    status = status or CaseStatus.ACCEPTED

    async def _do() -> int:
        async with factory() as db:
            case = Case(
                case_no=f"VOID-{uuid.uuid4().hex[:10]}",
                tenant_id=tenant_id,
                title="待作废案件",
                lawyer_id=lawyer_id,
                client_user_id=client_id,
                status=status,
                grade=CaseGrade.B,
                dispute_type="劳动争议",
            )
            db.add(case)
            await db.flush()
            if with_pending_dispatch:
                db.add(
                    Dispatch(
                        tenant_id=tenant_id,
                        case_id=case.id,
                        lawyer_id=lawyer_id,
                        mode=DispatchMode.AUTO,
                        status=DispatchStatus.PENDING,
                    )
                )
            if with_open_review:
                db.add(
                    Review(
                        tenant_id=tenant_id,
                        case_id=case.id,
                        target_type=ReviewTargetType.CASE_ANALYSIS,
                        target_id=case.id,
                        status=ReviewStatus.DRAFT,
                    )
                )
            await db.commit()
            return case.id

    return _run(_do())


def _set_lawyer_load(engine, user_id: int, count: int) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.identity import LawyerProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _do():
        async with factory() as db:
            prof = (
                await db.execute(
                    select(LawyerProfile).where(LawyerProfile.user_id == user_id)
                )
            ).scalars().one()
            prof.active_case_count = count
            await db.commit()

    _run(_do())


def _lawyer_load(engine, user_id: int) -> int:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.identity import LawyerProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _do() -> int:
        async with factory() as db:
            return (
                await db.execute(
                    select(LawyerProfile.active_case_count).where(
                        LawyerProfile.user_id == user_id
                    )
                )
            ).scalar_one()

    return _run(_do())


def _snapshot(engine, case_id: int) -> dict:
    """读取作废后的关键状态：案件 / 派单 / 复核 / 事件 / 审计。"""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.audit import AuditAction
    from app.models.audit_log import AuditLog
    from app.models.case import Case, CaseEvent, Dispatch
    from app.models.review import Review, ReviewRecord

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _do() -> dict:
        async with factory() as db:
            case = await db.get(Case, case_id)
            dispatches = list(
                (
                    await db.execute(select(Dispatch).where(Dispatch.case_id == case_id))
                ).scalars().all()
            )
            reviews = list(
                (
                    await db.execute(select(Review).where(Review.case_id == case_id))
                ).scalars().all()
            )
            review_records = list(
                (
                    await db.execute(
                        select(ReviewRecord).where(
                            ReviewRecord.review_id.in_([r.id for r in reviews])
                        )
                    )
                ).scalars().all()
                if reviews
                else []
            )
            events = list(
                (
                    await db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id))
                ).scalars().all()
            )
            audits = list(
                (
                    await db.execute(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.CASE_VOID,
                            AuditLog.resource_id == case_id,
                        )
                    )
                ).scalars().all()
            )
            return {
                "case": case,
                "dispatches": dispatches,
                "reviews": reviews,
                "review_records": review_records,
                "events": events,
                "audits": audits,
            }

    return _run(_do())


def _client_for(engine, *, tenant_id: str, user_id: int, role):
    """以指定身份构造 TestClient（不触发 lifespan，表已由 create_all 建好）。"""
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import TenantContext, get_current_user, get_db, get_tenant_context
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
        return User(
            id=user_id, tenant_id=tenant_id, role=role,
            username="override", full_name="override",
        )

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        tenant_id=tenant_id, user_id=user_id, role=role
    )
    return TestClient(app)


def _void(engine, *, tenant_id: str, user_id: int, role, case_id: int, reason: str = "客户撤回委托"):
    client = _client_for(engine, tenant_id=tenant_id, user_id=user_id, role=role)
    return client.post(f"/api/v1/cases/{case_id}/void", json={"reason": reason})


# ═══════════════════════ 权限：谁能作废 ═══════════════════════


def test_client_cannot_void_own_case(_engine, seeded):
    """客户即使拥有该案件也不能作废：CLIENT 无 case:close ⇒ 403，状态不变。"""
    from app.core.rbac import Role
    from app.models.enums import CaseStatus

    case_id = _make_case(
        _engine, lawyer_id=seeded["lawyer_a"], client_id=seeded["client"],
        status=CaseStatus.ACCEPTED,
    )
    res = _void(_engine, tenant_id=TENANT_A, user_id=seeded["client"], role=Role.CLIENT, case_id=case_id)

    assert res.status_code == 403, f"客户不应能作废案件：{res.status_code} {res.text[:200]}"
    snap = _snapshot(_engine, case_id)
    assert snap["case"].status == CaseStatus.ACCEPTED


def test_other_lawyer_gets_404(_engine, seeded):
    """同所其他律师（非承办、无团队授权）作废 → 404（不泄露案件存在）。"""
    from app.core.rbac import Role

    case_id = _make_case(_engine, lawyer_id=seeded["lawyer_a"])
    res = _void(_engine, tenant_id=TENANT_A, user_id=seeded["lawyer_b"], role=Role.LAWYER, case_id=case_id)

    assert res.status_code == 404, f"非承办律师应 404：{res.status_code} {res.text[:200]}"


def test_cross_tenant_gets_404(_engine, seeded):
    """外所律师凭自己的租户上下文作废本所案件 → 404。"""
    from app.core.rbac import Role

    case_id = _make_case(_engine, lawyer_id=seeded["lawyer_a"])
    res = _void(
        _engine, tenant_id=TENANT_B, user_id=seeded["foreign_lawyer"],
        role=Role.LAWYER, case_id=case_id,
    )

    assert res.status_code == 404, f"跨租户应 404：{res.status_code} {res.text[:200]}"


# ═══════════════════════ 正向：承办律师作废的完整副作用 ═══════════════════════


def test_owner_lawyer_void_updates_state_and_side_effects(_engine, seeded):
    """承办律师作废：状态、在手数、派单、复核、事件、审计全部到位。"""
    from app.core.rbac import Role
    from app.models.enums import CaseStatus, DispatchStatus, ReviewStatus

    _set_lawyer_load(_engine, seeded["lawyer_a"], 2)
    case_id = _make_case(
        _engine, lawyer_id=seeded["lawyer_a"],
        with_pending_dispatch=True, with_open_review=True,
    )

    res = _void(_engine, tenant_id=TENANT_A, user_id=seeded["lawyer_a"], role=Role.LAWYER, case_id=case_id)
    assert res.status_code == 200, f"承办律师应能作废：{res.status_code} {res.text[:300]}"
    body = res.json()["data"]
    assert body["status"] == CaseStatus.VOIDED.value
    assert body["already_voided"] is False
    assert body["expired_dispatches"] == 1
    assert body["voided_reviews"] == 1

    snap = _snapshot(_engine, case_id)
    assert snap["case"].status == CaseStatus.VOIDED, "案件状态必须落到 VOIDED"
    assert _lawyer_load(_engine, seeded["lawyer_a"]) == 1, "在手案件数应回减 1（2→1）"

    assert len(snap["dispatches"]) == 1
    assert snap["dispatches"][0].status == DispatchStatus.EXPIRED, "PENDING 派单必须退出抢单池"
    assert snap["dispatches"][0].decided_at, "派单失效需记录决定时间"

    assert len(snap["reviews"]) == 1
    assert snap["reviews"][0].status == ReviewStatus.VOIDED, "未完成复核必须退出复核台"
    assert len(snap["review_records"]) == 1
    assert snap["review_records"][0].action == "VOID"
    assert snap["review_records"][0].to_status == ReviewStatus.VOIDED.value

    void_events = [e for e in snap["events"] if e.event_type == "VOID"]
    assert len(void_events) == 1, "作废必须在时间线留一条事件"
    assert void_events[0].payload == {"previous_status": CaseStatus.ACCEPTED.value}

    assert len(snap["audits"]) == 1, "作废必须写一条 CASE_VOID 审计"
    assert snap["audits"][0].detail["previous_status"] == CaseStatus.ACCEPTED.value


def test_repeat_void_is_idempotent(_engine, seeded):
    """重复作废：返回 already_voided，不再回减、不再记事件/审计。"""
    from app.core.rbac import Role

    _set_lawyer_load(_engine, seeded["lawyer_a"], 1)
    case_id = _make_case(
        _engine, lawyer_id=seeded["lawyer_a"],
        with_pending_dispatch=True, with_open_review=True,
    )

    first = _void(_engine, tenant_id=TENANT_A, user_id=seeded["lawyer_a"], role=Role.LAWYER, case_id=case_id)
    assert first.status_code == 200, first.text[:200]

    after_first = _snapshot(_engine, case_id)
    load_after_first = _lawyer_load(_engine, seeded["lawyer_a"])
    assert load_after_first == 0

    second = _void(_engine, tenant_id=TENANT_A, user_id=seeded["lawyer_a"], role=Role.LAWYER, case_id=case_id)
    assert second.status_code == 200, second.text[:200]
    assert second.json()["data"]["already_voided"] is True

    after_second = _snapshot(_engine, case_id)
    assert _lawyer_load(_engine, seeded["lawyer_a"]) == load_after_first, "重复作废不得二次回减"
    assert len(after_second["events"]) == len(after_first["events"]), "重复作废不得重复记事件"
    assert len(after_second["audits"]) == len(after_first["audits"]), "重复作废不得重复写审计"


# ═══════════════════════ 状态机：归档终态不可作废 ═══════════════════════


@pytest.mark.parametrize("terminal", ["ARCHIVED", "CLOSED"])
def test_archived_or_closed_cannot_be_voided(_engine, seeded, terminal):
    """已归档 / 已结案是终态 → 409，且不改状态、不回减计数。"""
    from app.core.rbac import Role
    from app.models.enums import CaseStatus

    status = CaseStatus(terminal)
    _set_lawyer_load(_engine, seeded["lawyer_a"], 1)
    case_id = _make_case(_engine, lawyer_id=seeded["lawyer_a"], status=status)

    res = _void(_engine, tenant_id=TENANT_A, user_id=seeded["lawyer_a"], role=Role.LAWYER, case_id=case_id)

    assert res.status_code == 409, f"{terminal} 不应可作废：{res.status_code} {res.text[:200]}"
    snap = _snapshot(_engine, case_id)
    assert snap["case"].status == status
    assert _lawyer_load(_engine, seeded["lawyer_a"]) == 1, "被拒绝的作废不得回减在手数"


# ═══════════════════════ 本所管理员：可作废他人承办的案件 ═══════════════════════


def test_firm_admin_can_void_other_lawyers_case(_engine, seeded):
    """本所管理员可作废本所案件，回减的是**承办律师**的在手数。"""
    from app.core.rbac import Role

    _set_lawyer_load(_engine, seeded["lawyer_a"], 3)
    case_id = _make_case(_engine, lawyer_id=seeded["lawyer_a"])

    res = _void(_engine, tenant_id=TENANT_A, user_id=seeded["admin"], role=Role.FIRM_ADMIN, case_id=case_id)

    assert res.status_code == 200, f"管理员应能作废：{res.status_code} {res.text[:300]}"
    assert _lawyer_load(_engine, seeded["lawyer_a"]) == 2, "应回减承办律师的在手数"


def test_missing_case_returns_404(_engine, seeded):
    """不存在的案件（含已删除/纯编号猜测）→ 404。"""
    from app.core.rbac import Role

    res = _void(
        _engine, tenant_id=TENANT_A, user_id=seeded["admin"],
        role=Role.FIRM_ADMIN, case_id=999_999_999,
    )
    assert res.status_code == 404, f"不存在案件应 404：{res.status_code}"
