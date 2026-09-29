"""投诉举报机制单元测试（P0-13 / 《暂行办法》第十五条）。

测试重点放在**假闭环**上——投诉机制最常见的失效不是"收不到投诉"，
而是"收到了但没人处理、处理了但投诉人不知道、显示已办结却没有结论"。
因此用例集中验证：
  - 匿名投诉必须被受理（入口有无登录门槛，等于机制是否存在）
  - 办结/不予受理必须有处理结论（防"空办结"）
  - 承诺时限必须从受理起算（防"受理即逾期"）
  - 工单号必须不可枚举（防靠猜工单号读取他人投诉，涉隐私）
"""
from __future__ import annotations

import asyncio
import datetime

import pytest

from app.core.errors import BadRequestError, NotFoundError
from app.models.complaint import (
    COMPLAINT_DUE_DAYS,
    ComplaintStatus,
    ComplaintType,
    new_ticket_no,
)


# ═══════════════ A. 工单号 ═══════════════
def test_ticket_no_unpredictable():
    """工单号必须含随机段——否则可被枚举，投诉人隐私（含联系方式）会泄露。"""
    nos = {new_ticket_no() for _ in range(500)}
    assert len(nos) == 500, "工单号出现碰撞，随机段不足"
    assert all(n.startswith("AI") and len(n) > 12 for n in nos)


# ═══════════════ B. 时限口径 ═══════════════
def test_due_days_is_committed_value():
    """反馈时限必须是一个明确承诺值（第十五条要求「公布…反馈时限」）。"""
    assert isinstance(COMPLAINT_DUE_DAYS, int)
    assert COMPLAINT_DUE_DAYS > 0


# ═══════════════ C. 服务层（真库）═══════════════
@pytest.fixture
def db_ctx():
    """建一个临时 SQLite 库并返回 sessionmaker。

    不依赖全局 engine，避免与其他测试互相污染。
    库文件落在项目内 `_tmp_tests/`（`tmp_path` 在部分沙箱环境下不可写），
    且**不主动删除**——删除会触发沙箱的批量删除守卫。
    """
    import pathlib
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"cmp_{uuid.uuid4().hex[:8]}.db"

    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield factory
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


def test_anonymous_submit_is_accepted(db_ctx):
    """匿名投诉必须能受理——公众举报入口不能有登录门槛。"""
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            row = await ComplaintService(s).submit(
                type_=ComplaintType.CONTENT_MISJUDGED.value,
                description="我认为系统误判了我的正常咨询内容，请复核。",
                reporter=None,
            )
            await s.commit()
            return row

    row = _run(_go())
    assert row.id is not None
    assert row.reporter_id is None
    assert row.status == ComplaintStatus.PENDING.value
    assert row.ticket_no.startswith("AI")


def test_due_at_set_from_acceptance(db_ctx):
    """时限从**受理时刻**起算，不是从办结起算。"""
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            before = datetime.datetime.now(datetime.timezone.utc)
            row = await ComplaintService(s).submit(
                type_=ComplaintType.OTHER.value,
                description="随便反馈一个问题，用于验证时限计算。",
            )
            await s.commit()
            return row, before

    row, before = _run(_go())
    assert row.due_at is not None
    due = row.due_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=datetime.timezone.utc)
    delta_days = (due - before).total_seconds() / 86400
    assert COMPLAINT_DUE_DAYS - 1 < delta_days <= COMPLAINT_DUE_DAYS + 0.1


def test_description_too_short_rejected(db_ctx):
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            await ComplaintService(s).submit(
                type_=ComplaintType.OTHER.value, description="坏"
            )

    with pytest.raises(BadRequestError):
        _run(_go())


def test_unknown_type_rejected(db_ctx):
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            await ComplaintService(s).submit(
                type_="NOT_A_REAL_TYPE", description="这是一段足够长的投诉描述内容"
            )

    with pytest.raises(BadRequestError):
        _run(_go())


def test_resolve_requires_conclusion(db_ctx):
    """**核心用例**：办结却不给结论 = 假闭环，投诉人拿不到答复。"""
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            svc = ComplaintService(s)
            row = await svc.submit(
                type_=ComplaintType.CONTENT_MISJUDGED.value,
                description="这是一段足够长的投诉描述内容用于验证办结约束。",
            )
            await s.commit()
            try:
                await svc.handle(row.id, status="RESOLVED", handle_note="嗯")
            finally:
                await s.rollback()

    with pytest.raises(BadRequestError):
        _run(_go())


def test_resolve_sets_handled_and_feedback(db_ctx):
    """正常办结必须置 handled_at 与 feedback_sent，形成闭环。"""
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            svc = ComplaintService(s)
            row = await svc.submit(
                type_=ComplaintType.ILLEGAL_CONTENT.value,
                description="举报某次生成内容涉嫌违规，请核实处理。",
            )
            await s.commit()
            done = await svc.handle(
                row.id,
                status=ComplaintStatus.RESOLVED.value,
                handle_note="经复核，已对该内容作下架处理并向投诉人反馈。",
            )
            await s.commit()
            return done

    done = _run(_go())
    assert done.status == ComplaintStatus.RESOLVED.value
    assert done.handled_at is not None
    assert bool(done.feedback_sent) is True
    assert done.handle_note


def test_processing_does_not_close(db_ctx):
    """转入处理中不应置 handled_at（尚未办结）。"""
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            svc = ComplaintService(s)
            row = await svc.submit(
                type_=ComplaintType.SERVICE_ABUSE.value,
                description="举报有用户滥用本服务批量生成违规内容。",
            )
            await s.commit()
            mid = await svc.handle(
                row.id, status=ComplaintStatus.PROCESSING.value, handle_note="已分派核查"
            )
            await s.commit()
            return mid

    mid = _run(_go())
    assert mid.status == ComplaintStatus.PROCESSING.value
    assert mid.handled_at is None


def test_get_by_ticket(db_ctx):
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            svc = ComplaintService(s)
            row = await svc.submit(
                type_=ComplaintType.OTHER.value,
                description="这是一个用于验证工单号回查的描述内容。",
            )
            await s.commit()
            found = await svc.get_by_ticket(row.ticket_no.lower())
            missing = await svc.get_by_ticket("AI19700101FFFFFF")
            return found, missing

    found, missing = _run(_go())
    assert found is not None and found.ticket_no.startswith("AI")
    assert missing is None


def test_tenant_isolation_on_handle(db_ctx):
    """跨租户处理必须 404——不得泄露其他租户投诉的存在性。"""
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            svc = ComplaintService(s)
            row = await svc.submit(
                type_=ComplaintType.OTHER.value,
                description="用于验证跨租户隔离的投诉描述内容。",
                tenant_id="firm_a",
            )
            await s.commit()
            try:
                await svc.handle(
                    row.id,
                    status="PROCESSING",
                    handle_note="越权尝试",
                    tenant_id="firm_b",
                )
            finally:
                await s.rollback()

    with pytest.raises(NotFoundError):
        _run(_go())


def test_counts_reports_overdue(db_ctx):
    """看板必须给出逾期口径——逾期是违约信号，不能只数总数。"""
    from sqlalchemy import update

    from app.models.complaint import Complaint
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            svc = ComplaintService(s)
            row = await svc.submit(
                type_=ComplaintType.OTHER.value,
                description="用于验证逾期统计口径的投诉描述内容。",
            )
            await s.commit()
            # 把截止时间改到过去，模拟逾期
            past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
            await s.execute(update(Complaint).where(Complaint.id == row.id).values(due_at=past))
            await s.commit()
            return await svc.counts()

    c = _run(_go())
    assert c["overdue"] >= 1
    assert c["due_days"] == COMPLAINT_DUE_DAYS
    assert c["pending"] >= 1


def test_complaint_does_not_store_moderation_terms(db_ctx):
    """投诉描述本身允许留存（是投诉材料），但不得混入审核词库信息。"""
    from app.services.complaint_service import ComplaintService

    async def _go():
        async with db_ctx() as s:
            row = await ComplaintService(s).submit(
                type_=ComplaintType.CONTENT_MISJUDGED.value,
                description="我的提问被系统拦截了，请复核该判定是否合理。",
                related_moderation_id=12345,
            )
            await s.commit()
            return row

    row = _run(_go())
    assert row.related_moderation_id == 12345
    assert "词库" not in (row.description or "")
