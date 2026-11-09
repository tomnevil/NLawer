"""A9：律师「在手案件数」计数口径。

判据核心：一次「派单 → 接单」完整链路只能让 `active_case_count` **+1**；
重复接单不重复计；案件终结（归档）回减；改派时旧律师 -1 / 新律师 +1。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT = "a9-tenant"


@pytest.fixture
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    return create_async_engine(
        f"sqlite+aiosqlite:///{base / f'a9_{uuid.uuid4().hex[:8]}.db'}", echo=False
    )


@pytest.fixture
def seeded(engine):
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import UserStatus
    from app.models.identity import LawyerProfile, Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            s.add(Tenant(tenant_id=TENANT, name="A9 租户"))

            def _lawyer(tag):
                return User(
                    username=f"a9-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.LAWYER,
                    tenant_id=TENANT,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            l1, l2 = _lawyer("L1"), _lawyer("L2")
            s.add_all([l1, l2])
            await s.flush()
            for u in (l1, l2):
                s.add(
                    LawyerProfile(
                        tenant_id=TENANT,
                        user_id=u.id,
                        specialties="劳动争议",
                        available=True,
                        active_case_count=0,
                    )
                )
            await s.commit()
            return {"l1": l1.id, "l2": l2.id}

    return asyncio.run(_seed())


def _factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


def _count(engine, user_id) -> int:
    from app.models.identity import LawyerProfile

    async def _q():
        async with _factory(engine)() as s:
            p = (
                await s.execute(
                    select(LawyerProfile).where(LawyerProfile.user_id == user_id)
                )
            ).scalars().one()
            return p.active_case_count

    return asyncio.run(_q())


def _new_case(engine, status_name="INTAKE"):
    from app.models.case import Case
    from app.models.enums import CaseGrade, CaseStatus

    async def _q():
        async with _factory(engine)() as s:
            c = Case(
                tenant_id=TENANT,
                case_no=f"A9-{uuid.uuid4().hex[:8]}",
                title="A9 计数用案件",
                status=getattr(CaseStatus, status_name),
                grade=CaseGrade.B,
                dispute_type="劳动争议",
            )
            s.add(c)
            await s.commit()
            return c.id

    return asyncio.run(_q())


def _dispatch(engine, case_id, *, mode, lawyer_id=None):
    from app.models.case import Case
    from app.models.enums import DispatchMode
    from app.services.dispatch_service import DispatchService

    async def _q():
        async with _factory(engine)() as s:
            case = await s.get(Case, case_id)
            svc = DispatchService(s)
            disp = await svc.dispatch(
                case,
                mode=getattr(DispatchMode, mode),
                bind_lawyer_id=lawyer_id,
            )
            await s.commit()
            return disp.id

    return asyncio.run(_q())


def _accept(engine, dispatch_id, lawyer_id):
    from app.services.dispatch_service import DispatchService

    async def _q():
        async with _factory(engine)() as s:
            await DispatchService(s).accept(dispatch_id, lawyer_id)
            await s.commit()

    return asyncio.run(_q())


def test_designated_dispatch_then_accept_counts_once(seeded, engine):
    """A9-1：指定律师派单（+1）→ 接单（±0），整条链路只 +1。"""
    case_id = _new_case(engine)
    disp_id = _dispatch(engine, case_id, mode="DESIGNATED", lawyer_id=seeded["l1"])
    assert _count(engine, seeded["l1"]) == 1, "派单后应为 1"

    _accept(engine, disp_id, seeded["l1"])
    assert _count(engine, seeded["l1"]) == 1, "接单不得二次递增（A9 核心判据）"


def test_pool_dispatch_then_grab_counts_once(seeded, engine):
    """A9-2：入池（0）→ 抢单（+1），同样只 +1。"""
    case_id = _new_case(engine)
    disp_id = _dispatch(engine, case_id, mode="POOL")
    assert _count(engine, seeded["l1"]) == 0, "入池阶段不应计数"

    _accept(engine, disp_id, seeded["l1"])
    assert _count(engine, seeded["l1"]) == 1


def test_repeated_accept_does_not_increment(seeded, engine):
    """A9-3：重复接单被 409 拦下，计数不变。"""
    from app.core.errors import ConflictError

    case_id = _new_case(engine)
    disp_id = _dispatch(engine, case_id, mode="POOL")
    _accept(engine, disp_id, seeded["l1"])
    assert _count(engine, seeded["l1"]) == 1

    with pytest.raises(ConflictError):
        _accept(engine, disp_id, seeded["l1"])
    assert _count(engine, seeded["l1"]) == 1, "重复接单不得再 +1"


def test_archive_decrements_load(seeded, engine):
    """A9-4：归档（案件终结）回减，且重复调用不再扣。"""
    from app.models.case import Case
    from app.models.enums import CaseStatus
    from app.services.archive_service import ArchiveService

    case_id = _new_case(engine)
    disp_id = _dispatch(engine, case_id, mode="DESIGNATED", lawyer_id=seeded["l1"])
    _accept(engine, disp_id, seeded["l1"])
    assert _count(engine, seeded["l1"]) == 1

    async def _confirm():
        async with _factory(engine)() as s:
            c = await s.get(Case, case_id)
            c.status = CaseStatus.CONFIRMED
            await s.commit()

    asyncio.run(_confirm())

    async def _archive():
        async with _factory(engine)() as s:
            await ArchiveService(s).archive_case(
                case_id, actor_id=seeded["l1"], tenant_id=TENANT
            )
            await s.commit()

    asyncio.run(_archive())
    assert _count(engine, seeded["l1"]) == 0, "归档后应回减到 0"

    # 重复归档在「状态检查」处被拦下（未确认定稿/已归档），计数不得再扣
    from app.core.errors import BadRequestError

    with pytest.raises(BadRequestError):
        asyncio.run(_archive())
    assert _count(engine, seeded["l1"]) == 0


def test_reassign_moves_count_between_lawyers(seeded, engine):
    """A9-5：改派（旧 → 新）旧律师 -1、新律师 +1（直接验计数入口）。"""
    from app.services.dispatch_service import adjust_active_case_count

    async def _reassign():
        async with _factory(engine)() as s:
            await adjust_active_case_count(s, seeded["l1"], -1)
            await adjust_active_case_count(s, seeded["l2"], 1)
            await s.commit()

    asyncio.run(_reassign())
    assert _count(engine, seeded["l1"]) == 0
    assert _count(engine, seeded["l2"]) == 1
