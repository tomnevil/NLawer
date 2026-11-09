"""复核回写幂等：目标已终态时不应重复署名 / 重复推送客户会话。

正常路径由 `ReviewFSM` 阻断重复 `decide`（CONFIRMED / ARCHIVED 不可再流转），
本组用例覆盖「手动或未来路径重复调用 `_on_confirmed` / `_finalize_consult_report`」
时的纵深防御。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT = "tenant-idem"


@pytest.fixture()
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    db = base / f"idem_{uuid.uuid4().hex[:8]}.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return eng


@pytest.fixture()
def factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


def test_finalize_consult_report_already_approved_is_noop(factory):
    from app.models.consult_report import ConsultReport, ConsultReportStatus
    from app.models.conversation import Conversation, Message
    from app.models.enums import ReviewLevel, ReviewStatus, ReviewTargetType
    from app.models.review import Review
    from app.services.review_service import ReviewService

    async def _go():
        async with factory() as s:
            conv = Conversation(tenant_id=TENANT, external_user_id="ext-idem")
            s.add(conv)
            await s.flush()
            cr = ConsultReport(
                tenant_id=TENANT,
                conversation_id=conv.id,
                question="q",
                answer="a",
                draft_sections={"summary": "draft"},
                status=ConsultReportStatus.APPROVED,
                lawyer_id=99,
                signed_by=99,
                signed_at="2026-01-01T00:00:00",
                final_report={"summary": "final"},
                review_id=1,
            )
            s.add(cr)
            await s.flush()
            r = Review(
                tenant_id=TENANT,
                target_type=ReviewTargetType.CONSULT_REPORT,
                target_id=cr.id,
                status=ReviewStatus.CONFIRMED,
                required_level=ReviewLevel.L2,
                satisfied_level=ReviewLevel.L2,
                decided_by=3,
                decided_at="2026-09-01T10:00:00",
            )
            s.add(r)
            await s.commit()

            await ReviewService(s)._finalize_consult_report(r)

            msgs = (await s.execute(select(Message))).scalars().all()
            assert msgs == []
            refreshed = await s.get(ConsultReport, cr.id)
            assert refreshed.status == ConsultReportStatus.APPROVED
            assert refreshed.signed_by == 99
            assert refreshed.signed_at == "2026-01-01T00:00:00"
            assert refreshed.final_report == {"summary": "final"}

    asyncio.run(_go())


def test_on_confirmed_does_not_rewrite_signature_when_already_confirmed(factory):
    from app.models.analysis import CaseAnalysis
    from app.models.case import Case
    from app.models.enums import CaseStatus, ReviewLevel, ReviewStatus, ReviewTargetType
    from app.models.review import Review
    from app.services.review_service import ReviewService

    async def _go():
        async with factory() as s:
            case = Case(
                tenant_id=TENANT,
                case_no=f"IDEM-{uuid.uuid4().hex[:8]}",
                title="幂等用例",
                status=CaseStatus.ACCEPTED,
            )
            s.add(case)
            await s.flush()
            a = CaseAnalysis(
                tenant_id=TENANT,
                case_id=case.id,
                status=ReviewStatus.CONFIRMED,
                confirmed_by=7,
                confirmed_at="2026-01-01T00:00:00",
            )
            s.add(a)
            await s.flush()
            r = Review(
                tenant_id=TENANT,
                target_type=ReviewTargetType.CASE_ANALYSIS,
                target_id=a.id,
                case_id=case.id,
                status=ReviewStatus.CONFIRMED,
                required_level=ReviewLevel.L2,
                satisfied_level=ReviewLevel.L2,
                decided_by=3,
                decided_at="2026-09-01T10:00:00",
            )
            s.add(r)
            await s.commit()

            await ReviewService(s)._on_confirmed(r)

            refreshed = await s.get(CaseAnalysis, a.id)
            assert refreshed.confirmed_by == 7
            assert refreshed.confirmed_at == "2026-01-01T00:00:00"
            case_refreshed = await s.get(Case, case.id)
            assert case_refreshed.status == CaseStatus.CONFIRMED

    asyncio.run(_go())
