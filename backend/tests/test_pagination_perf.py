"""有界计数（`count_bounded`）单元测试。

## 为什么必须单独测边界

`count_bounded` 的失效模式是**"看起来对但实际错"**：
若取 `LIMIT cap` 而非 `cap + 1`，两种完全不同的情况会塌缩成同一个结果——

    「恰好 cap 条」  → 应返回 (cap, False)  精确值
    「超过 cap 条」  → 应返回 (cap, True)   下界

只取 cap 时会**数到 cap 就停**，因而把"正好 200 条"错报成下界（前端显示
"200+"），也会把"201 条"错报成精确 200（静默丢 1 条）。两者都不报错，
只是数字错——与第八轮的丢失更新、第九轮的直方图分桶属同一类缺陷。

因此本轮把边界钉死在测试里，而不是靠人工 review。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest

CAP = 200


@pytest.fixture
def db():
    """独立临时库（`_tmp_tests/`，沙箱下 `tmp_path` 不可写；不 unlink）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"perf_{uuid.uuid4().hex[:8]}.db"

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{dbfile}", echo=False, connect_args={"timeout": 30}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield factory
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


async def _seed(factory, n: int, tenant: str = "firm_t1") -> None:
    from app.models.case import Case
    from app.models.enums import CaseStatus

    async with factory() as s:
        s.add_all(
            [
                Case(
                    tenant_id=tenant,
                    # `case_no` 是**全局唯一**（非按租户唯一），因此编号必须
                    # 带租户前缀，否则多租户造数会撞 UNIQUE 约束。
                    case_no=f"{tenant}-C{i:06d}",
                    title=f"货款纠纷{i}",
                    status=CaseStatus.INTAKE,
                )
                for i in range(n)
            ]
        )
        await s.commit()


# ═══════════════ 边界：cap 与 cap+1 必须区分 ═══════════════
def test_exactly_cap_is_exact(db):
    """恰好 `cap` 条 → 精确值，`is_lower_bound=False`。"""
    from sqlalchemy import select

    from app.core.pagination import count_bounded
    from app.models.case import Case

    _run(_seed(db, CAP))

    async def go():
        async with db() as s:
            return await count_bounded(s, select(Case).where(Case.tenant_id == "firm_t1"))

    total, lower = _run(go())
    assert total == CAP
    assert lower is False, "数完恰好 cap 条必须报『精确』，不能报下界"


def test_cap_plus_one_is_lower_bound(db):
    """`cap + 1` 条 → 报 `cap` 且标记下界（说明确实还有更多）。"""
    from sqlalchemy import select

    from app.core.pagination import count_bounded
    from app.models.case import Case

    _run(_seed(db, CAP + 1))

    async def go():
        async with db() as s:
            return await count_bounded(s, select(Case).where(Case.tenant_id == "firm_t1"))

    total, lower = _run(go())
    assert total == CAP
    assert lower is True, "多出 1 条即说明总数超过 cap，必须标记下界"


def test_below_cap_is_exact(db):
    from sqlalchemy import select

    from app.core.pagination import count_bounded
    from app.models.case import Case

    _run(_seed(db, 7))

    async def go():
        async with db() as s:
            return await count_bounded(s, select(Case).where(Case.tenant_id == "firm_t1"))

    total, lower = _run(go())
    assert (total, lower) == (7, False)


def test_zero_matches(db):
    from sqlalchemy import select

    from app.core.pagination import count_bounded
    from app.models.case import Case

    async def go():
        async with db() as s:
            return await count_bounded(s, select(Case).where(Case.tenant_id == "nobody"))

    assert _run(go()) == (0, False)


def test_far_above_cap_reports_cap_not_actual(db):
    """远超 cap 时返回 cap（而非真实值），证明**确实短路了**。"""
    from sqlalchemy import select

    from app.core.pagination import COUNT_CAP, count_bounded
    from app.models.case import Case

    _run(_seed(db, COUNT_CAP * 3))

    async def go():
        async with db() as s:
            return await count_bounded(s, select(Case).where(Case.tenant_id == "firm_t1"))

    total, lower = _run(go())
    assert total == COUNT_CAP
    assert lower is True


def test_filter_is_respected(db):
    """计数必须带原查询的 WHERE——否则会把其它租户的数据算进来。"""
    from sqlalchemy import select

    from app.core.pagination import count_bounded
    from app.models.case import Case

    _run(_seed(db, 3, tenant="firm_a"))
    _run(_seed(db, 5, tenant="firm_b"))

    async def go():
        async with db() as s:
            a = await count_bounded(s, select(Case).where(Case.tenant_id == "firm_a"))
            b = await count_bounded(s, select(Case).where(Case.tenant_id == "firm_b"))
            return a, b

    assert _run(go()) == ((3, False), (5, False))


def test_custom_cap_honoured(db):
    from sqlalchemy import select

    from app.core.pagination import count_bounded
    from app.models.case import Case

    _run(_seed(db, 10))

    async def go():
        async with db() as s:
            return await count_bounded(s, select(Case).where(Case.tenant_id == "firm_t1"), cap=4)

    assert _run(go()) == (4, True)


# ═══════════════ Page 契约 ═══════════════
def test_page_marks_lower_bound_and_pages():
    from app.core.pagination import Page, PaginationParams

    class P(PaginationParams):
        def __init__(self, page=1, page_size=20):
            self.page, self.page_size = page, page_size

    p = Page.build([], 200, P(), total_is_lower_bound=True)
    assert p.total == 200 and p.pages == 10 and p.total_is_lower_bound is True

    # 默认必须为 False——避免旧调用点被误标为下界
    q = Page.build([], 42, P())
    assert q.total_is_lower_bound is False


def test_page_default_flag_is_false_in_schema():
    """`total_is_lower_bound` 必须有默认值：否则所有旧序列化路径会 500。"""
    from app.core.pagination import Page

    assert Page.model_fields["total_is_lower_bound"].default is False


def test_pagination_params_offset_limit():
    from app.core.pagination import PaginationParams

    class P(PaginationParams):
        def __init__(self, page, page_size):
            self.page, self.page_size = page, page_size

    p = P(3, 20)
    assert p.offset == 40 and p.limit == 20
