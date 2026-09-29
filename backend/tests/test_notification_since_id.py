"""`since_id` 增量补拉单元测试（PRD NP-17，M1 出口标准之一）。

## 为什么 `since_id` 值得单独一个文件

它不是「多一个可选参数」，而是**另一种查询意图**：

| | 常规列表 | `since_id` 补拉 |
|---|---|---|
| 意图 | 翻页浏览 | 断线后**追赶** |
| 排序 | `id DESC`（最新在前） | `id ASC`（按时间顺序补齐） |
| 分页 | `offset` + `limit` | **keyset**（锚在 id 上，`offset` 被忽略） |

两种意图混用会出**静默的错**：若补拉仍用倒序 + `OFFSET`，追赶过程中一旦有新
通知写入，整个结果集向前位移，第 2 页会重复第 1 页的行、并跳过原本属于
第 2 页的行（offset 漂移）。它不报错、不丢响应，只表现为「用户少看了几条」。

因此本文件用**「造 N 条 + 逐页 keyset 追赶」**的方式断言**不重不漏**，
而不是只断言单页的排序——单页正确但跨页漂移，是这类 bug 的典型形态。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest

TENANT_A = "firm_since"
TENANT_B = "firm_since_other"
ALICE = 701
BOB = 702  # 同租户不同用户：最关键的隔离面


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def _engine():
    """模块级引擎：`create_all` 建表开销约 25s，不能每个用例重做一次。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"since_{uuid.uuid4().hex[:8]}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def db_ctx(_engine):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _clean():
        async with _engine.begin() as conn:
            await conn.execute(text("DELETE FROM notifications"))

    asyncio.run(_clean())
    return factory


def _run(coro):
    return asyncio.run(coro)


async def _seed(s, *, user_id: int, count: int, tenant_id: str = TENANT_A) -> list[int]:
    """写入 `count` 条通知并返回其 id（升序）。"""
    from app.models.enums import NotificationType
    from app.services.notification_service import notify

    ids: list[int] = []
    for i in range(count):
        n = await notify(
            s,
            tenant_id=tenant_id,
            user_id=user_id,
            type=NotificationType.DISPATCH_CREATED,
            content=f"第 {i} 条",
            ref_type="case",
            ref_id=i,
        )
        ids.append(n.id)
    await s.commit()
    # 提交会触发推送调度；无连接时记为 no_connection。等它跑完，
    # 避免事件循环关闭时留下 pending task 告警。
    from app.services import notification_push

    await notification_push.drain_inflight()
    return ids


# ═══════════════════════ A. 排序与分页语义 ═══════════════════════


def test_without_since_id_is_descending(db_ctx):
    """**向后兼容**：不传 `since_id` 时行为与第一轮完全一致（倒序）。"""
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            await _seed(s, user_id=ALICE, count=5)
            rows = await list_for_user(s, tenant_id=TENANT_A, user_id=ALICE, limit=10)
            return [r.id for r in rows]

    ids = _run(_go())
    assert ids == sorted(ids, reverse=True), f"未按 id 倒序：{ids}"


def test_with_since_id_is_ascending(db_ctx):
    """补拉必须**升序**：前端要按时间顺序合并进本地列表。"""
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            await _seed(s, user_id=ALICE, count=5)
            rows = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=0, limit=10
            )
            return [r.id for r in rows]

    ids = _run(_go())
    assert ids == sorted(ids), f"补拉未按 id 升序：{ids}"


def test_since_id_filters_strictly_greater(db_ctx):
    """边界：`id > since_id`（**严格大于**，不是 >=）。

    若写成 `>=`，客户端把 `since_id` 设为「已收到的最大 id」后会**重复收到
    那一条**，表现为每次重连都多出一条重复通知。
    """
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            ids = await _seed(s, user_id=ALICE, count=4)
            rows = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=ids[1], limit=10
            )
            return ids, [r.id for r in rows]

    ids, got = _run(_go())
    assert ids[1] not in got, "since_id 被当作 >= 处理，会重复推送边界那条"
    assert got == ids[2:], f"got={got} expected={ids[2:]}"


def test_offset_is_ignored_when_since_id_present(db_ctx):
    """`since_id` 模式下 `offset` 必须被忽略（否则就是 offset 漂移的来源）。"""
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            await _seed(s, user_id=ALICE, count=6)
            a = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=0, limit=10, offset=0
            )
            b = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=0, limit=10, offset=3
            )
            return [r.id for r in a], [r.id for r in b]

    a, b = _run(_go())
    assert a == b, f"offset 未被忽略：offset=0 -> {a} / offset=3 -> {b}"


def test_since_id_beyond_max_returns_empty(db_ctx):
    """边界：`since_id` 大于当前最大 id → 空列表（且不报错）。

    这是「断线很久后重连，本地 id 比服务端还新」的正常情形
    （例如用户切换了账号、或数据被清理）。
    """
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            ids = await _seed(s, user_id=ALICE, count=3)
            rows = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=ids[-1] + 999, limit=10
            )
            return list(rows)

    assert _run(_go()) == []


def test_since_id_zero_equals_first_page(db_ctx):
    """`since_id=0` 等价于「从头拉」（受 limit 保护）。"""
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            ids = await _seed(s, user_id=ALICE, count=3)
            rows = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=0, limit=10
            )
            return ids, [r.id for r in rows]

    ids, got = _run(_go())
    assert got == ids


# ═══════════════════════ B. keyset 追赶：不重不漏 ═══════════════════════


def test_keyset_catch_up_retrieves_everything_exactly_once(db_ctx):
    """**核心用例**：逐页 keyset 追赶 25 条，必须一条不多、一条不少。

    这条同时覆盖「升序」「严格大于」「limit 生效」「跨页不漂移」四件事——
    它们中的任何一个出错，都会在这里表现为重复或缺失。
    """
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            expected = await _seed(s, user_id=ALICE, count=25)
            collected: list[int] = []
            cursor = 0
            for _ in range(10):  # 上限 10 轮，防死循环
                page = await list_for_user(
                    s, tenant_id=TENANT_A, user_id=ALICE, since_id=cursor, limit=10
                )
                if not page:
                    break
                collected.extend(r.id for r in page)
                cursor = page[-1].id
                if len(page) < 10:
                    break
            return expected, collected

    expected, collected = _run(_go())
    assert collected == expected, (
        f"keyset 追赶结果与期望不符："
        f"重复={set(collected) & set(expected) and len(collected) != len(set(collected))} "
        f"len(expected)={len(expected)} len(collected)={len(collected)}"
    )
    assert len(collected) == len(set(collected)), "出现重复条目"


def test_keyset_catch_up_is_stable_under_concurrent_writes(db_ctx):
    """**offset 漂移的对照实验**：追赶途中写入新通知，不得导致重复或漏项。

    这正是 `since_id` 必须用 keyset 而不能用 offset 的原因：
    offset 分页下，中途新增的行会把结果集整体后移，第 2 页会重复第 1 页。
    """
    from app.models.enums import NotificationType
    from app.services.notification_service import list_for_user, notify

    async def _go():
        async with db_ctx() as s:
            first_batch = await _seed(s, user_id=ALICE, count=10)
            page1 = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=0, limit=10
            )
            got = [r.id for r in page1]

            # 追赶途中来了新通知
            n = await notify(
                s,
                tenant_id=TENANT_A,
                user_id=ALICE,
                type=NotificationType.CASE_ARCHIVED,
                content="追赶途中写入",
                ref_type="case",
                ref_id=1,
            )
            await s.commit()
            from app.services import notification_push

            await notification_push.drain_inflight()
            intruder = n.id

            page2 = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=got[-1], limit=10
            )
            got += [r.id for r in page2]
            return first_batch, intruder, got

    first_batch, intruder, got = _run(_go())
    assert intruder not in first_batch
    assert len(got) == len(set(got)), f"keyset 追赶出现重复：{got}"
    assert set(first_batch) <= set(got), "keyset 追赶漏掉了首批中的条目"
    assert got == sorted(got), "追赶结果未保持升序"


# ═══════════════════════ C. 归属约束（越权防护） ═══════════════════════


def test_since_id_only_returns_own_notifications(db_ctx):
    """**隔离**：补拉仍受 `(tenant_id, user_id)` 约束，不能借它读到他人通知。"""
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            alice_ids = await _seed(s, user_id=ALICE, count=3)
            await _seed(s, user_id=BOB, count=4)
            rows = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=0, limit=100
            )
            return alice_ids, [r.id for r in rows]

    alice_ids, got = _run(_go())
    assert got == alice_ids, f"补拉泄漏了他人通知：got={got} own={alice_ids}"


def test_since_id_cannot_be_used_to_probe_other_users_ids(db_ctx):
    """**`since_id` 不参与归属判据**。

    攻击设想：把自己的 `since_id` 设成「猜出来的他人通知 id」，
    若实现里把 `since_id` 当成了筛选条件之一而放松了归属约束，
    就能用 id 区间二分探测他人通知的存在性。
    正确行为：传任何 `since_id`，返回的都**只有本人**通知。
    """
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            await _seed(s, user_id=ALICE, count=2)
            bob_ids = await _seed(s, user_id=BOB, count=5)
            # 用 Bob 的最小 id 作为锚点去探测
            rows = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=bob_ids[0] - 1, limit=100
            )
            return bob_ids, [r.id for r in rows]

    bob_ids, got = _run(_go())
    assert not (set(got) & set(bob_ids)), f"用 since_id 探测到了他人通知：{got}"


def test_since_id_is_tenant_scoped(db_ctx):
    """跨租户：即便 user_id 相同，租户不同也不得返回。"""
    from app.services.notification_service import list_for_user

    async def _go():
        async with db_ctx() as s:
            await _seed(s, user_id=ALICE, count=3, tenant_id=TENANT_B)
            rows = await list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, since_id=0, limit=100
            )
            return list(rows)

    assert _run(_go()) == [], "跨租户补拉泄漏了通知"


def test_since_id_combines_with_is_read_filter(db_ctx):
    """`since_id` 与既有筛选（`is_read`）可叠加，互不覆盖。"""
    from app.models.enums import NotificationType
    from app.services.notification_service import list_for_user, mark_read, notify

    async def _go():
        async with db_ctx() as s:
            ids = await _seed(s, user_id=ALICE, count=4)
            await mark_read(s, tenant_id=TENANT_A, user_id=ALICE, notification_id=ids[0])
            await s.commit()
            from app.services import notification_push

            await notification_push.drain_inflight()

            unread = await list_for_user(
                s,
                tenant_id=TENANT_A,
                user_id=ALICE,
                is_read=False,
                since_id=0,
                limit=100,
            )
            # 再补一条，验证叠加时也能被 since_id 截断
            n = await notify(
                s,
                tenant_id=TENANT_A,
                user_id=ALICE,
                type=NotificationType.CASE_ARCHIVED,
                content="追加",
                ref_type="case",
                ref_id=9,
            )
            await s.commit()
            await notification_push.drain_inflight()
            after = await list_for_user(
                s,
                tenant_id=TENANT_A,
                user_id=ALICE,
                is_read=False,
                since_id=ids[-1],
                limit=100,
            )
            return ids, [r.id for r in unread], n.id, [r.id for r in after]

    ids, unread_ids, newest, after = _run(_go())
    assert unread_ids == ids[1:], f"is_read 与 since_id 叠加结果错误：{unread_ids}"
    assert after == [newest], f"since_id 未与 is_read 叠加生效：{after}"
