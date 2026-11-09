"""A7 案号并发：同租户同秒建案不再撞唯一约束。

历史格式 `{tenant}-{YYYYmmddHHMMSS}` 只有秒级精度；本用例把时钟冻结到同一
秒/微秒，验证随机后缀足以保证唯一，并验证并发落库全部成功。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import func, select

TENANT = "firm_concurrency"


@pytest.fixture()
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    db = base / f"case_no_{uuid.uuid4().hex[:8]}.db"
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{db}",
        echo=False,
        connect_args={"timeout": 30},
    )

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return eng


@pytest.fixture()
def factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


def test_same_frozen_timestamp_still_unique(monkeypatch):
    """秒 + 微秒完全相同的情况下，随机后缀保证案号唯一且保留可读时间前缀。"""
    import datetime as dt

    from app.services.conversation_engine import _gen_case_no

    class FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return cls(2026, 11, 9, 15, 30, 0, 123456)

    monkeypatch.setattr(dt, "datetime", FrozenDatetime)

    numbers = [_gen_case_no(TENANT) for _ in range(50)]
    assert len(set(numbers)) == 50
    prefix = f"{TENANT}-20261109153000123456-"
    assert all(n.startswith(prefix) for n in numbers)


def test_concurrent_case_inserts_all_succeed_and_unique(factory):
    """并发建案：每个会话独立提交，全部成功且 case_no 唯一。"""
    from app.models.case import Case
    from app.models.enums import CaseStatus
    from app.services.conversation_engine import _gen_case_no

    async def _insert(i: int) -> str:
        async with factory() as s:
            case = Case(
                tenant_id=TENANT,
                case_no=_gen_case_no(TENANT),
                title=f"并发建案-{i}",
                status=CaseStatus.ACCEPTED,
            )
            s.add(case)
            await s.commit()
            return case.case_no

    async def _go():
        rows = await asyncio.gather(*[_insert(i) for i in range(20)])
        async with factory() as s:
            total = (await s.execute(select(func.count()).select_from(Case))).scalar_one()
        return rows, total

    rows, total = asyncio.run(_go())
    assert total == 20
    assert len(set(rows)) == 20

    # 至少能证明这一批案号共享同一秒前缀（随机/微秒后缀承担唯一性）
    seconds = {no.split("-", 1)[1][:14] for no in rows}
    assert len(seconds) <= 2
