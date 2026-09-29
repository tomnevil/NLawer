"""通知读路径单元测试（P0-15）。

## 为什么用例集中在「隔离」与「幂等」两件事上

通知系统的失效模式与投诉系统（见 test_complaint.py）不同：投诉最怕
「假闭环」，通知最怕两件事——

1. **看不到**：写入侧存在但读取侧缺失，通知进库即成黑洞。
   这是本轮修复的**根因**，用例覆盖列表/未读数/详情三个读入口。
2. **看多了**：通知是**用户级**资源，只按 `tenant_id` 过滤会让同律所内
   律师甲读到律师乙的通知（含案件标题、客户信息、复核结论）。
   本文件里隔离相关用例占了一半以上，且跨用户、跨租户分别验证。

另外「已读」是状态迁移操作，天然会被重复触发（前端重试、用户连点、
离线补传）。因此幂等性不是锦上添花，而是**必须可断言的正确性属性**：
重复标记不得报错、不得重复计数、不得篡改首次阅读时间戳。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest

# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def _engine():
    """模块级引擎：`create_all` 建表开销约 25s，绝不能每个用例重做一次。

    库文件落在项目内 `_tmp_tests/`（`tmp_path` 在部分沙箱环境下不可写），
    且**不主动删除**——删除会触发沙箱的批量删除守卫。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"notif_{uuid.uuid4().hex[:8]}.db"

    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def db_ctx(_engine):
    """函数级会话工厂；每个用例前清空 `notifications`，保证用例间互不污染。

    清表而非重建库：单条 `DELETE` 是毫秒级，重建库是秒级。
    """
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


TENANT_A = "firm_a"
TENANT_B = "firm_b"
ALICE = 101  # firm_a
BOB = 102  # firm_a —— 同租户不同用户，最关键的隔离面
CAROL = 201  # firm_b


async def _seed(s, rows):
    """写入若干通知。rows: [(tenant_id, user_id, type, content)]"""
    from app.models.enums import NotificationType
    from app.services.notification_service import notify

    out = []
    for tenant_id, user_id, type_, content in rows:
        n = await notify(
            s,
            tenant_id=tenant_id,
            user_id=user_id,
            type=NotificationType(type_),
            content=content,
            ref_type="case",
            ref_id=7,
        )
        out.append(n)
    await s.commit()
    return out


# ═══════════════ A. 读入口存在性（根因回归） ═══════════════


def test_notification_is_readable_after_write(db_ctx):
    """**根因回归**：写入的通知必须能被读出来。

    修复前 `is_read` / `read_at` 全库无赋值点、且不存在任何读接口，
    通知写进库即消失。此用例锁死「写了能读到」这一最低要求。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(s, [(TENANT_A, ALICE, "DISPATCH_CREATED", "您有新案件待接：《张三诉李四》")])
            rows = await svc.list_for_user(s, tenant_id=TENANT_A, user_id=ALICE)
            return rows

    rows = _run(_go())
    assert len(rows) == 1
    assert "张三诉李四" in rows[0].content
    assert rows[0].title == "新派单待接"  # 类型 → 默认标题映射生效


def test_new_notification_defaults_to_unread(db_ctx):
    """新通知必须默认未读，否则角标永远为 0，通知形同不存在。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(s, [(TENANT_A, ALICE, "REVIEW_REQUIRED", "有新的强制复核任务待处理")])
            return await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE)

    assert _run(_go())["total"] == 1


# ═══════════════ B. 用户级隔离（最严重越权面） ═══════════════


def test_same_tenant_other_user_cannot_list(db_ctx):
    """**核心安全用例**：同租户内，甲不得读到乙的通知。

    只按 tenant_id 过滤就会漏掉这一层——而通知内容含案件标题与客户信息，
    泄露后果等同案件资料泄露。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [
                    (TENANT_A, ALICE, "DISPATCH_CREATED", "甲的通知"),
                    (TENANT_A, BOB, "REVIEW_REQUIRED", "乙的通知"),
                ],
            )
            alice_rows = await svc.list_for_user(s, tenant_id=TENANT_A, user_id=ALICE)
            bob_rows = await svc.list_for_user(s, tenant_id=TENANT_A, user_id=BOB)
            return alice_rows, bob_rows

    alice_rows, bob_rows = _run(_go())
    assert [r.content for r in alice_rows] == ["甲的通知"]
    assert [r.content for r in bob_rows] == ["乙的通知"]


def test_cross_tenant_cannot_list(db_ctx):
    """跨租户隔离：乙律所的用户看不到甲律所任何通知。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(s, [(TENANT_A, ALICE, "CASE_ARCHIVED", "甲所案件已归档")])
            return await svc.list_for_user(s, tenant_id=TENANT_B, user_id=ALICE)

    assert _run(_go()) == []


def test_same_user_id_in_two_tenants_is_isolated(db_ctx):
    """**容易漏掉的边界**：不同租户下 `user_id` 数值可能相同。

    若查询写成 `WHERE user_id = ?` 而漏掉 tenant_id，则 B 租户中 id 恰好
    也是 101 的用户会读到 A 租户的通知——这是「看起来有隔离、实际没有」
    的典型形态，必须显式覆盖。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [
                    (TENANT_A, ALICE, "DISPATCH_CREATED", "A 租户 101 号的通知"),
                    (TENANT_B, ALICE, "DISPATCH_CREATED", "B 租户 101 号的通知"),
                ],
            )
            a = await svc.list_for_user(s, tenant_id=TENANT_A, user_id=ALICE)
            b = await svc.list_for_user(s, tenant_id=TENANT_B, user_id=ALICE)
            return a, b

    a, b = _run(_go())
    assert len(a) == 1 and len(b) == 1
    assert a[0].tenant_id == TENANT_A and b[0].tenant_id == TENANT_B
    assert a[0].content != b[0].content


def test_get_owned_rejects_other_user(db_ctx):
    """按 id 直取时也必须校验归属，否则等于提供「猜 id 读他人通知」的接口。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            rows = await _seed(s, [(TENANT_A, BOB, "REVIEW_REQUIRED", "乙的通知")])
            nid = rows[0].id
            own = await svc.get_owned(s, tenant_id=TENANT_A, user_id=BOB, notification_id=nid)
            alien = await svc.get_owned(s, tenant_id=TENANT_A, user_id=ALICE, notification_id=nid)
            cross = await svc.get_owned(s, tenant_id=TENANT_B, user_id=BOB, notification_id=nid)
            return own, alien, cross

    own, alien, cross = _run(_go())
    assert own is not None
    assert alien is None, "同租户他人通知被读到了"
    assert cross is None, "跨租户通知被读到了"


def test_unread_count_is_per_user(db_ctx):
    """未读数必须按用户独立——共享计数会让角标显示「别人的待办」。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [
                    (TENANT_A, ALICE, "DISPATCH_CREATED", "a1"),
                    (TENANT_A, ALICE, "REVIEW_REQUIRED", "a2"),
                    (TENANT_A, BOB, "REVIEW_REQUIRED", "b1"),
                ],
            )
            return (
                await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE),
                await svc.unread_count(s, tenant_id=TENANT_A, user_id=BOB),
            )

    alice, bob = _run(_go())
    assert alice["total"] == 2
    assert bob["total"] == 1


def test_mark_read_cannot_touch_other_user(db_ctx):
    """标记已读同样是写操作，越权写必须失败（返回 None），且不得改动数据。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            rows = await _seed(s, [(TENANT_A, BOB, "REVIEW_REQUIRED", "乙的通知")])
            nid = rows[0].id
            touched = await svc.mark_read(
                s, tenant_id=TENANT_A, user_id=ALICE, notification_id=nid
            )
            await s.commit()
            still = await svc.unread_count(s, tenant_id=TENANT_A, user_id=BOB)
            return touched, still

    touched, still = _run(_go())
    assert touched is None
    assert still["total"] == 1, "他人通知被越权标记为已读"


# ═══════════════ C. 幂等性 ═══════════════


def test_mark_read_is_idempotent_and_keeps_first_timestamp(db_ctx):
    """**核心用例**：重复标记已读不得报错，且 `read_at` 保持首次值。

    `read_at` 在通知场景下有证据价值（「律师是否在开庭前读过派单提醒」）。
    若每次调用都刷新时间戳，重复调用会篡改「用户何时真正读过」这一事实。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            rows = await _seed(s, [(TENANT_A, ALICE, "DISPATCH_CREATED", "待接案件")])
            nid = rows[0].id

            first = await svc.mark_read(
                s, tenant_id=TENANT_A, user_id=ALICE, notification_id=nid
            )
            await s.commit()
            first_at = first.read_at

            second = await svc.mark_read(
                s, tenant_id=TENANT_A, user_id=ALICE, notification_id=nid
            )
            await s.commit()

            unread = await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE)
            return first, second, first_at, unread

    first, second, first_at, unread = _run(_go())
    assert first is not None and second is not None, "重复标记报错了（不幂等）"
    assert bool(second.is_read) is True
    assert second.read_at == first_at, "重复标记刷新了首次阅读时间戳"
    assert unread["total"] == 0


def test_mark_read_batch_returns_real_transitions(db_ctx):
    """批量标记返回**实际发生迁移**的行数，重复调用必须为 0。

    若返回「请求里包含的 id 数」，前端无法区分「真的清掉了」与
    「这些早就读过了」，幂等性也就无从断言。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            rows = await _seed(
                s,
                [(TENANT_A, ALICE, "DISPATCH_CREATED", f"n{i}") for i in range(3)],
            )
            ids = [r.id for r in rows]
            first = await svc.mark_read_batch(
                s, tenant_id=TENANT_A, user_id=ALICE, ids=ids
            )
            await s.commit()
            second = await svc.mark_read_batch(
                s, tenant_id=TENANT_A, user_id=ALICE, ids=ids
            )
            await s.commit()
            return first, second

    first, second = _run(_go())
    assert first == 3
    assert second == 0, "重复批量标记仍报有迁移，幂等性被破坏"


def test_mark_read_batch_skips_foreign_ids_silently(db_ctx):
    """批量中混入他人 id：合法部分照常处理，非法部分静默跳过。

    若因一个越权 id 让整批失败，会把「前端缓存了过期 id」这类正常情况
    变成用户可见的报错；但归属约束必须仍然生效，不得误伤他人数据。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            mine = await _seed(s, [(TENANT_A, ALICE, "DISPATCH_CREATED", "我的")])
            theirs = await _seed(s, [(TENANT_A, BOB, "REVIEW_REQUIRED", "他的")])
            updated = await svc.mark_read_batch(
                s,
                tenant_id=TENANT_A,
                user_id=ALICE,
                ids=[mine[0].id, theirs[0].id],
            )
            await s.commit()
            bob_unread = await svc.unread_count(s, tenant_id=TENANT_A, user_id=BOB)
            return updated, bob_unread

    updated, bob_unread = _run(_go())
    assert updated == 1, "越权 id 被计入迁移数"
    assert bob_unread["total"] == 1, "他人通知被批量越权标记"


def test_mark_all_read_only_affects_self(db_ctx):
    """「全部已读」必须只清自己的，不能把同事的待办一起清掉。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [
                    (TENANT_A, ALICE, "DISPATCH_CREATED", "a1"),
                    (TENANT_A, ALICE, "REVIEW_REQUIRED", "a2"),
                    (TENANT_A, BOB, "REVIEW_REQUIRED", "b1"),
                    (TENANT_B, ALICE, "DISPATCH_CREATED", "other-tenant"),
                ],
            )
            updated = await svc.mark_read_batch(s, tenant_id=TENANT_A, user_id=ALICE)
            await s.commit()
            return (
                updated,
                await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE),
                await svc.unread_count(s, tenant_id=TENANT_A, user_id=BOB),
                await svc.unread_count(s, tenant_id=TENANT_B, user_id=ALICE),
            )

    updated, alice, bob, other = _run(_go())
    assert updated == 2
    assert alice["total"] == 0
    assert bob["total"] == 1, "「全部已读」波及了同租户他人"
    assert other["total"] == 1, "「全部已读」波及了其他租户"


def test_mark_read_batch_is_visible_in_same_session(db_ctx):
    """**核心用例**：批量标记后，同一会话内立刻读列表必须看到新状态。

    批量标记走 Core 层 UPDATE，ORM 身份映射不会自动感知。若不同步会话
    （`synchronize_session=False`），「先批量标记、再读列表」会返回**陈旧的
    `is_read=0`** —— 不报错、不抛异常，只在特定调用顺序下出现。这类静默
    错误正是本轮要消灭的对象，故必须有回归用例锁死。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            rows = await _seed(
                s,
                [(TENANT_A, ALICE, "DISPATCH_CREATED", f"n{i}") for i in range(3)],
            )
            # 先读一次，让 ORM 身份映射持有这些对象
            _ = await svc.list_for_user(s, tenant_id=TENANT_A, user_id=ALICE)
            await svc.mark_read_batch(
                s, tenant_id=TENANT_A, user_id=ALICE, ids=[r.id for r in rows]
            )
            await s.commit()
            after = await svc.list_for_user(s, tenant_id=TENANT_A, user_id=ALICE)
            return after

    after = _run(_go())
    assert all(bool(r.is_read) for r in after), "会话内对象未同步，读到陈旧 is_read"


def test_unread_count_reports_latest_id(db_ctx):
    """`latest_id` 必须指向**未读**中最大的 id，供轮询做「有无新通知」判据。

    若误取全表最大 id，用户把最新一条标记已读后 `latest_id` 不变，
    前端会误判「没有新通知」而跳过刷新。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            rows = await _seed(
                s,
                [(TENANT_A, ALICE, "DISPATCH_CREATED", f"n{i}") for i in range(3)],
            )
            ids = [r.id for r in rows]
            before = await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE)
            # 把最新一条标记已读 → latest_id 必须回退到次新
            await svc.mark_read(
                s, tenant_id=TENANT_A, user_id=ALICE, notification_id=ids[-1]
            )
            await s.commit()
            after = await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE)
            return ids, before, after

    ids, before, after = _run(_go())
    assert before["latest_id"] == ids[-1]
    assert after["latest_id"] == ids[-2], "latest_id 未随已读回退"


def test_unread_count_latest_id_zero_when_all_read(db_ctx):
    """全部已读时 `latest_id` 必须为 0，前端据此隐藏角标。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(s, [(TENANT_A, ALICE, "DISPATCH_CREATED", "n0")])
            await svc.mark_read_batch(s, tenant_id=TENANT_A, user_id=ALICE)
            await s.commit()
            return await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE)

    assert _run(_go())["latest_id"] == 0


# ═══════════════ D. 筛选 / 分组 / 分页 ═══════════════


def test_filter_by_is_read(db_ctx):
    """未读筛选是通知中心默认视图，必须准确。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            rows = await _seed(
                s,
                [(TENANT_A, ALICE, "DISPATCH_CREATED", f"n{i}") for i in range(4)],
            )
            await svc.mark_read_batch(
                s, tenant_id=TENANT_A, user_id=ALICE, ids=[rows[0].id, rows[1].id]
            )
            await s.commit()
            unread = await svc.list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, is_read=False
            )
            read = await svc.list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, is_read=True
            )
            return unread, read

    unread, read = _run(_go())
    assert len(unread) == 2 and len(read) == 2
    assert all(not r.is_read for r in unread)
    assert all(bool(r.is_read) for r in read)


def test_unread_count_groups_by_type(db_ctx):
    """分类未读数供 Tab 角标使用，分组必须正确。

    用一次 `GROUP BY` 取回全部分类，避免前端为每个 Tab 各发一次请求
    （律师端 5 个 Tab 会在弱网下放大成 5 次往返）。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [
                    (TENANT_A, ALICE, "REVIEW_REQUIRED", "r1"),
                    (TENANT_A, ALICE, "REVIEW_REQUIRED", "r2"),
                    (TENANT_A, ALICE, "DISPATCH_CREATED", "d1"),
                ],
            )
            return await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE)

    data = _run(_go())
    assert data["total"] == 3
    assert data["by_type"] == {"REVIEW_REQUIRED": 2, "DISPATCH_CREATED": 1}


def test_mark_all_read_can_be_limited_by_type(db_ctx):
    """按类型清未读：律师出差回来只想清「待复核」，不想清掉派单提醒。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [
                    (TENANT_A, ALICE, "REVIEW_REQUIRED", "r1"),
                    (TENANT_A, ALICE, "REVIEW_REQUIRED", "r2"),
                    (TENANT_A, ALICE, "DISPATCH_CREATED", "d1"),
                ],
            )
            updated = await svc.mark_read_batch(
                s, tenant_id=TENANT_A, user_id=ALICE, type="REVIEW_REQUIRED"
            )
            await s.commit()
            return updated, await svc.unread_count(s, tenant_id=TENANT_A, user_id=ALICE)

    updated, left = _run(_go())
    assert updated == 2
    assert left["total"] == 1
    assert left["by_type"] == {"DISPATCH_CREATED": 1}


def test_ordering_is_stable_newest_first(db_ctx):
    """按 id 倒序而非 created_at：同秒写入的多条通知不能排序抖动。

    归档动作会同时通知客户与承办律师，两条 `created_at` 可能完全相同；
    用 `created_at` 排序会让翻页出现重复/漏项。
    """
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [(TENANT_A, ALICE, "DISPATCH_CREATED", f"n{i}") for i in range(5)],
            )
            return await svc.list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, limit=3
            )

    rows = _run(_go())
    assert [r.content for r in rows] == ["n4", "n3", "n2"]


def test_pagination_does_not_leak_across_pages(db_ctx):
    """翻页不得重复或漏项（这是 `id` 稳定排序的直接收益）。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [(TENANT_A, ALICE, "DISPATCH_CREATED", f"n{i}") for i in range(7)],
            )
            p1 = await svc.list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, offset=0, limit=3
            )
            p2 = await svc.list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, offset=3, limit=3
            )
            p3 = await svc.list_for_user(
                s, tenant_id=TENANT_A, user_id=ALICE, offset=6, limit=3
            )
            return p1, p2, p3

    p1, p2, p3 = _run(_go())
    ids = [r.id for r in (*p1, *p2, *p3)]
    assert len(ids) == 7
    assert len(set(ids)) == 7, "翻页出现重复项"


def test_count_matches_list(db_ctx):
    """计数与列表必须同源——两处各拼一份 WHERE 是越权与错数的常见来源。"""
    from app.services import notification_service as svc

    async def _go():
        async with db_ctx() as s:
            await _seed(
                s,
                [
                    (TENANT_A, ALICE, "DISPATCH_CREATED", "a1"),
                    (TENANT_A, ALICE, "REVIEW_REQUIRED", "a2"),
                    (TENANT_A, BOB, "DISPATCH_CREATED", "b1"),
                ],
            )
            n = await svc.count_for_user(s, tenant_id=TENANT_A, user_id=ALICE)
            rows = await svc.list_for_user(s, tenant_id=TENANT_A, user_id=ALICE)
            return n, rows

    n, rows = _run(_go())
    assert n == len(rows) == 2


def test_write_path_failure_does_not_break_business(db_ctx):
    """通知写入失败必须被吞掉——通知是旁路，不能把主业务拖垮。

    这是既有设计（`notify()` 的 try/except），读路径落地后不能破坏它。
    用 mock 强制 flush 抛错，而不是靠构造非法数据：后者依赖具体方言的
    约束行为（SQLite 不强制 VARCHAR 长度），换库就会失效。
    """
    from unittest.mock import AsyncMock, patch

    from app.models.enums import NotificationType
    from app.services.notification_service import notify

    async def _go():
        async with db_ctx() as s:
            with patch.object(s, "flush", new=AsyncMock(side_effect=RuntimeError("模拟写库失败"))):
                return await notify(
                    s,
                    tenant_id=TENANT_A,
                    user_id=ALICE,
                    type=NotificationType.DISPATCH_CREATED,
                    content="这条会写失败",
                )

    assert _run(_go()) is None, "写入异常未被子类吞掉，会拖垮主业务"


def test_notify_skips_when_no_recipient(db_ctx):
    """**无接收人的通知必须拒绝写入**（P0-15 新增守卫）。

    通知读路径一律按 `user_id == 当前登录用户` 过滤，因此 `user_id` 为
    `None` 或 `0` 的通知**对任何人都不可见**——不是「稍后可见」，而是
    永久黑洞：既占存储，又让「未读积压」统计失真。

    历史成因：计费侧曾写 `user_id=user_id or 0`，把缺失的用户上下文兜底成
    哨兵值 0，而 0 不是任何真实用户的 id。此处从写入侧堵住。
    """
    from sqlalchemy import func, select

    from app.models.enums import NotificationType
    from app.models.notification import Notification
    from app.services.notification_service import notify

    async def _go():
        async with db_ctx() as s:
            r_none = await notify(
                s,
                tenant_id=TENANT_A,
                user_id=None,
                type=NotificationType.WORK_ORDER_CREATED,
                content="无接收人（None）",
            )
            r_zero = await notify(
                s,
                tenant_id=TENANT_A,
                user_id=0,
                type=NotificationType.WORK_ORDER_CREATED,
                content="无接收人（0）",
            )
            await s.commit()
            count = int(
                (await s.execute(select(func.count()).select_from(Notification))).scalar_one()
            )
            return r_none, r_zero, count

    r_none, r_zero, count = _run(_go())
    assert r_none is None and r_zero is None
    assert count == 0, "无接收人的通知被写入了库（将成为无人可读的孤儿记录）"
