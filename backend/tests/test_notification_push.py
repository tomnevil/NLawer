"""通知实时推送测试（P0-15 收口项）。

## 本文件要守住的两件事

### 1. 「推送不早于落库」——幽灵通知防线

`notify()` 在业务事务**内部**执行，而业务事务随时可能回滚。若在 `flush()`
之后直接推送，就会出现最难排查的一类错误：

> 客户端弹出「您有新派单待接」，用户点进去 → **案件不存在**。

因为那条通知随事务回滚了，**只有推送到达了**。它不报错、不留日志、
只在特定失败路径下出现，而且**直接摧毁用户对通知的信任**。
本文件用 `test_rollback_does_not_push` 把这条防线钉死。

### 2. 「推送只到本人」——用户级隔离

通知是**用户级**资源（P0-15 第一轮确立）。推送侧若按 `tenant_id` 建索引，
同租户内律师甲会收到律师乙的通知——正是第一轮修掉的那类越权。
本文件用 `test_send_does_not_cross_users` 钉死。

## 为什么连接管理器的用例不启真实 WebSocket

`starlette.WebSocket` 很难构造「发送到一半连接断掉」「单用户第 6 个连接」
这类分支。用几十行的 `_FakeWS` 覆盖这些分支，比起一个真实 ASGI 服务
更可靠也更快。真实 WS 协议层由端到端脚本 `verify_p0_15_push.py` 覆盖，
两者是不同层次的验证，不互相替代。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest

TENANT_A = "firm_a"
ALICE = 101
BOB = 102  # 同租户不同用户：最关键的隔离面


# ═══════════════════════ 假连接 ═══════════════════════


class _FakeWS:
    """满足 `SupportsSendJson` 的最小假连接。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.closed: int | None = None
        self.fail = fail

    async def send_json(self, data) -> None:
        if self.fail:
            raise RuntimeError("模拟发送失败（连接已死）")
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════ A. ConnectionManager ═══════════════════════


def test_register_and_count():
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        a1, a2 = _FakeWS(), _FakeWS()
        assert await mgr.register(ALICE, a1) is True
        assert await mgr.register(ALICE, a2) is True
        assert mgr.count(ALICE) == 2
        assert mgr.online_users() == 1  # 用户数，不是连接数
        assert mgr.total_connections() == 2

    _run(_go())


def test_register_rejects_non_positive_user_id():
    """`user_id <= 0` 不是真实用户（哨兵值），不得登记。"""
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        assert await mgr.register(0, _FakeWS()) is False
        assert await mgr.register(-1, _FakeWS()) is False
        assert mgr.online_users() == 0

    _run(_go())


def test_max_per_user_is_enforced():
    """**有界性**：第 6 个连接（上限 5）必须被拒绝，而不是静默堆积。"""
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager(max_per_user=5)
        for _ in range(5):
            assert await mgr.register(ALICE, _FakeWS()) is True
        assert await mgr.register(ALICE, _FakeWS()) is False
        assert mgr.count(ALICE) == 5
        # 另一个用户不受影响：上限是「每用户」而非全局
        assert await mgr.register(BOB, _FakeWS()) is True

    _run(_go())


def test_unregister_is_idempotent_and_frees_empty_set():
    """注销幂等；用户连接清空后**不得留下空集合**。

    留空集合会让 `online_users()` 随「历史出现过的用户数」单调增长，
    指标失真且内存永不释放。
    """
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        ws = _FakeWS()
        await mgr.register(ALICE, ws)
        await mgr.unregister(ALICE, ws)
        await mgr.unregister(ALICE, ws)  # 幂等：不报错
        assert mgr.count(ALICE) == 0
        assert mgr.online_users() == 0
        assert mgr.total_connections() == 0

    _run(_go())


def test_send_delivers_to_all_connections_of_user():
    """多端（手机 + 电脑）必须都收到。"""
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        a1, a2 = _FakeWS(), _FakeWS()
        await mgr.register(ALICE, a1)
        await mgr.register(ALICE, a2)

        outcome = await mgr.send_to_user(
            ALICE, {"type": "notification", "data": {"id": 1}}
        )
        assert outcome.delivered == 2
        assert outcome.attempted == 2
        assert outcome.failed == 0
        assert a1.sent == a2.sent == [{"type": "notification", "data": {"id": 1}}]

    _run(_go())


def test_send_does_not_cross_users():
    """**隔离核心用例**：推给 ALICE 的消息绝不能到 BOB 的连接上。"""
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        a, b = _FakeWS(), _FakeWS()
        await mgr.register(ALICE, a)
        await mgr.register(BOB, b)

        await mgr.send_to_user(ALICE, {"type": "notification", "data": {"id": 7}})
        assert len(a.sent) == 1
        assert b.sent == [], "同租户另一用户收到了不属于他的推送"

    _run(_go())


def test_send_to_offline_user_returns_zero():
    """离线用户没有连接：送达 0、尝试 0，且**不抛异常**。

    `attempted == 0` 是「用户没在线」的判据——推送层据此记为 `no_connection`
    常态值，而不是 `failed` 事故值。
    """
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        outcome = await mgr.send_to_user(ALICE, {"type": "notification", "data": {}})
        assert outcome.delivered == 0
        assert outcome.attempted == 0
        assert outcome.had_connections is False

    _run(_go())


def test_send_prunes_dead_connections():
    """**失败只回收不抛出**：坏连接被摘除，好连接照常收到。"""
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        dead, alive = _FakeWS(fail=True), _FakeWS()
        await mgr.register(ALICE, dead)
        await mgr.register(ALICE, alive)

        outcome = await mgr.send_to_user(
            ALICE, {"type": "notification", "data": {"id": 3}}
        )
        assert outcome.delivered == 1, "坏连接不应计入送达数"
        assert outcome.attempted == 2, "尝试数应包含失败的那条"
        assert outcome.failed == 1, "失败数必须可观测（区分「离线」与「推失败」）"
        assert len(alive.sent) == 1
        assert mgr.count(ALICE) == 1, "坏连接未被摘除"

        # 再推一次：不应再次尝试坏连接
        await mgr.send_to_user(ALICE, {"type": "notification", "data": {"id": 4}})
        assert len(alive.sent) == 2

    _run(_go())


def test_all_connections_failing_is_distinguishable_from_offline():
    """**关键区分**：在线但全失败 ≠ 离线。

    两者送达数都是 0，但前者是事故、后者是常态。若无法区分，指标里
    「在线用户推送全失败」会被海量「用户不在线」淹没，告警永不触发。
    """
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        offline = await mgr.send_to_user(ALICE, {"type": "notification", "data": {}})
        assert offline.had_connections is False

        await mgr.register(BOB, _FakeWS(fail=True))
        broken = await mgr.send_to_user(BOB, {"type": "notification", "data": {}})
        assert broken.delivered == offline.delivered == 0
        assert broken.had_connections is True, "有连接却全部失败，不能等同于离线"
        assert broken.failed == 1

    _run(_go())


def test_send_after_dead_prune_frees_user():
    """唯一连接失败后，用户应被完全摘除（不留空集合）。"""
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        await mgr.register(ALICE, _FakeWS(fail=True))
        await mgr.send_to_user(ALICE, {"type": "notification", "data": {}})
        assert mgr.online_users() == 0

    _run(_go())


def test_concurrent_register_does_not_exceed_max():
    """**并发安全**：`register` 是「读-判断-写」，不加锁时会突破上限。

    20 个协程同时抢 5 个名额，成功数必须恰好为 5。
    """
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager(max_per_user=5)
        results = await asyncio.gather(
            *[mgr.register(ALICE, _FakeWS()) for _ in range(20)]
        )
        assert sum(1 for r in results if r) == 5
        assert mgr.count(ALICE) == 5

    _run(_go())


def test_close_all_closes_everything():
    """优雅停机：全部关闭，注册表清空，返回关闭数。"""
    from app.services.connection_manager import ConnectionManager

    async def _go():
        mgr = ConnectionManager()
        socks = [_FakeWS() for _ in range(4)]
        await mgr.register(ALICE, socks[0])
        await mgr.register(ALICE, socks[1])
        await mgr.register(BOB, socks[2])
        await mgr.register(BOB, socks[3])

        closed = await mgr.close_all()
        assert closed == 4
        assert all(s.closed == 1001 for s in socks), "应以 1001(going away) 关闭"
        assert mgr.online_users() == 0

    _run(_go())


# ═══════════════════════ B. 提交桥（幽灵通知防线） ═══════════════════════


@pytest.fixture(scope="module")
def _engine():
    """模块级引擎：`create_all` 约 25s，不能每个用例重做。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"push_{uuid.uuid4().hex[:8]}.db"
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


@pytest.fixture
def captured():
    """把全局 `manager` 换成独立的实例，并捕获推送。

    直接往全局 `manager` 上挂假连接会让用例相互污染（前一个用例的坏连接
    会被后一个用例的推送碰到）。因此每个用例用独立实例，通过
    `patch` 替换 `notification_push.manager`。
    """
    from unittest.mock import patch

    from app.services import notification_push
    from app.services.connection_manager import ConnectionManager

    mgr = ConnectionManager()
    sent: list[tuple[int, dict]] = []

    original_send = mgr.send_to_user

    async def _spy(user_id: int, message: dict):
        sent.append((user_id, message))
        return await original_send(user_id, message)

    mgr.send_to_user = _spy  # type: ignore[method-assign]

    with patch.object(notification_push, "manager", mgr):
        notification_push.reset_for_tests()
        yield mgr, sent
        notification_push.reset_for_tests()


async def _notify(s, *, user_id=ALICE, content="测试通知"):
    from app.models.enums import NotificationType
    from app.services.notification_service import notify

    return await notify(
        s,
        tenant_id=TENANT_A,
        user_id=user_id,
        type=NotificationType.DISPATCH_CREATED,
        content=content,
        ref_type="case",
        ref_id=42,
    )


def test_commit_triggers_push(db_ctx, captured):
    """**主路径**：提交后推送到达收件人，且载荷与读接口同形。"""
    from app.services import notification_push

    mgr, sent = captured

    async def _go():
        ws = _FakeWS()
        await mgr.register(ALICE, ws)
        async with db_ctx() as s:
            await _notify(s)
            assert notification_push.pending_count(s) == 1, "落库后应已登记待推送"
            await s.commit()
        await notification_push.drain_inflight()
        return ws

    ws = _run(_go())
    assert len(ws.sent) == 1
    frame = ws.sent[0]
    assert frame["type"] == "notification"
    data = frame["data"]
    # 与 NotificationOut 同形（前端只需一套解析）
    assert set(data) >= {"id", "type", "title", "content", "ref_type", "ref_id", "is_read"}
    assert data["type"] == "DISPATCH_CREATED"
    assert data["is_read"] is False, "is_read 必须收敛为 bool，不能是 0"
    assert data["ref_type"] == "case"
    assert data["ref_id"] == 42
    assert sent == [(ALICE, frame)]


def test_rollback_does_not_push(db_ctx, captured):
    """**幽灵通知防线**：事务回滚后**一条都不能推**。

    这是本文件最重要的一条断言。若推送写在 `flush()` 之后而非提交之后，
    客户端会收到一条点开不存在的通知——不报错、不留日志、只摧毁信任。
    """
    from app.services import notification_push

    mgr, sent = captured

    async def _go():
        ws = _FakeWS()
        await mgr.register(ALICE, ws)
        async with db_ctx() as s:
            await _notify(s)
            assert notification_push.pending_count(s) == 1
            await s.rollback()
            assert notification_push.pending_count(s) == 0, "回滚后待推送队列必须清空"
        await notification_push.drain_inflight()
        return ws

    ws = _run(_go())
    assert ws.sent == [], "事务回滚却推送了通知（幽灵通知）"
    assert sent == []


def test_rollback_leaves_no_row(db_ctx, captured):
    """与上一条互补：回滚后库里确实没有该通知（证明「不推」是对的）。"""
    from sqlalchemy import select

    from app.models.notification import Notification

    async def _go():
        async with db_ctx() as s:
            await _notify(s)
            await s.rollback()
        async with db_ctx() as s:
            rows = (await s.execute(select(Notification))).scalars().all()
            assert rows == []

    _run(_go())


def test_push_only_reaches_recipient(db_ctx, captured):
    """推送与落库一致地只到本人。"""
    from app.services import notification_push

    mgr, _sent = captured

    async def _go():
        a, b = _FakeWS(), _FakeWS()
        await mgr.register(ALICE, a)
        await mgr.register(BOB, b)
        async with db_ctx() as s:
            await _notify(s, user_id=ALICE)
            await s.commit()
        await notification_push.drain_inflight()
        return a, b

    a, b = _run(_go())
    assert len(a.sent) == 1
    assert b.sent == [], "推送泄漏到了同租户另一用户"


def test_notify_without_recipient_queues_nothing(db_ctx, captured):
    """无有效接收人的通知既不落库也不推送（写入侧守卫的一致性）。"""
    from app.services import notification_push

    mgr, sent = captured

    async def _go():
        ws = _FakeWS()
        await mgr.register(ALICE, ws)
        async with db_ctx() as s:
            n = await _notify(s, user_id=None)
            assert n is None
            assert notification_push.pending_count(s) == 0
            await s.commit()
        await notification_push.drain_inflight()
        return ws

    ws = _run(_go())
    assert ws.sent == []
    assert sent == []


def test_push_payload_matches_rest_contract(db_ctx, captured):
    """**契约一致性**：推送帧的 `data` 必须能反序列化回 `NotificationOut`。

    若推送帧字段与 REST 不一致，前端要为两种来源各写一套解析，迟早漂移
    （典型后果：实时到的那条没有 `ref_id`，点击无法跳转）。
    """
    from app.services import notification_push

    mgr, _sent = captured

    async def _go():
        ws = _FakeWS()
        await mgr.register(ALICE, ws)
        async with db_ctx() as s:
            await _notify(s)
            await s.commit()
        await notification_push.drain_inflight()
        return ws

    ws = _run(_go())
    from app.schemas.notification import NotificationOut

    parsed = NotificationOut.model_validate(ws.sent[0]["data"])
    assert parsed.id > 0
    assert parsed.type == "DISPATCH_CREATED"
    assert parsed.title == "新派单待接"  # 由 _TITLE 映射生成
    assert parsed.is_read is False
    assert parsed.created_at is not None


def test_push_is_side_channel_when_no_connections(db_ctx, captured):
    """**旁路语义**：无人在线时提交仍然成功，只是送达数为 0。

    这条守的是「推送绝不能影响主流程」——若推送抛异常，接单/复核这些
    主流程会被带崩。
    """
    from sqlalchemy import select

    from app.models.notification import Notification
    from app.services import notification_push

    async def _go():
        async with db_ctx() as s:
            await _notify(s)
            await s.commit()  # 无任何连接，不应抛
        await notification_push.drain_inflight()
        async with db_ctx() as s:
            rows = (await s.execute(select(Notification))).scalars().all()
            assert len(rows) == 1

    _run(_go())


# ═══════════════════════ C. 推送指标（NP-12） ═══════════════════════


def _push_count(result: str) -> int:
    """读取 `notification_push_total{result}` 的当前值。

    用**增量**而非绝对值断言：指标是进程级单例，用例间会累积。
    读私有 `_values` 与 `test_observability.py` 的既有做法一致
    （该模块没有提供只读取值 API，导出格式是唯一对外契约）。
    """
    from app.core.metrics import metrics

    return int(metrics.notification_push_total._values.get((result,), 0.0))


def _latency_observations() -> int:
    from app.core.metrics import metrics

    return int(
        metrics.notification_push_latency_milliseconds._totals.get((), 0.0)
    )


def test_push_metric_records_delivered(db_ctx, captured):
    """送达成功必须计入 `delivered`——这是「实时推送真的在工作」的唯一证据。"""
    from app.services import notification_push

    mgr, _sent = captured
    before = _push_count("delivered")

    async def _go():
        await mgr.register(ALICE, _FakeWS())
        async with db_ctx() as s:
            await _notify(s)
            await s.commit()
        await notification_push.drain_inflight()

    _run(_go())
    assert _push_count("delivered") == before + 1


def test_push_metric_records_no_connection(db_ctx, captured):
    """无人连接计入 `no_connection`（常态值，不应触发告警）。"""
    from app.services import notification_push

    _mgr, _sent = captured
    before = _push_count("no_connection")

    async def _go():
        async with db_ctx() as s:
            await _notify(s)
            await s.commit()
        await notification_push.drain_inflight()

    _run(_go())
    assert _push_count("no_connection") == before + 1


def test_push_metric_records_rolled_back(db_ctx, captured):
    """**幽灵通知防线的活体证据**：回滚必须计入 `rolled_back`。

    这个计数每 +1，就代表「一条本会被推出去、但点开不存在的通知」被拦下了。
    没有它，这条防线是否真的在生效就只能靠读代码相信。
    """
    from app.services import notification_push

    _mgr, sent = captured
    before = _push_count("rolled_back")

    async def _go():
        async with db_ctx() as s:
            await _notify(s)
            await s.rollback()
        await notification_push.drain_inflight()

    _run(_go())
    assert _push_count("rolled_back") == before + 1
    assert sent == [], "回滚却发生了推送"


def test_push_metric_records_failed_when_connections_are_broken(db_ctx, captured):
    """**在线但全失败必须计入 `failed`**，不能混进 `no_connection`。

    这是本轮刻意区分四态的原因：若两者合并，一次「所有在线用户的推送
    全部失败」的事故会被淹没在常态值里，告警永远不会触发。
    """
    from app.services import notification_push

    mgr, _sent = captured
    before_failed = _push_count("failed")
    before_nc = _push_count("no_connection")

    async def _go():
        await mgr.register(ALICE, _FakeWS(fail=True))
        async with db_ctx() as s:
            await _notify(s)
            await s.commit()
        await notification_push.drain_inflight()

    _run(_go())
    assert _push_count("failed") == before_failed + 1
    assert _push_count("no_connection") == before_nc, "全失败被误记为「无连接」"


def test_push_latency_is_observed_for_every_outcome(db_ctx, captured):
    """时延必须**每条都观测**，含失败与回滚路径。

    若只在成功路径记录，`histogram_quantile()` 算出的 P99 会系统性偏小
    ——正好把「慢到失败」的那批样本丢掉，而它们才是要告警的对象。
    """
    from app.services import notification_push

    _mgr, _sent = captured
    before = _latency_observations()

    async def _go():
        # 两种结局各一次：no_connection（无人连）与 rolled_back（事务回滚）
        async with db_ctx() as s:
            await _notify(s)
            await s.commit()
        async with db_ctx() as s:
            await _notify(s)
            await s.rollback()
        await notification_push.drain_inflight()

    _run(_go())
    assert _latency_observations() == before + 2, "时延观测数与推送条数不符"


def test_push_metrics_are_conserved(db_ctx, captured):
    """**指标守恒**：四态之和必须等于推送条数。

    不守恒意味着某条分支漏记（例如新增了一条 early-return），
    后果是「推送总数为 N 但四态加起来不到 N」——对账能力直接失效。
    """
    from app.services import notification_push

    _mgr, _sent = captured
    before = {
        r: _push_count(r)
        for r in ("delivered", "no_connection", "failed", "rolled_back")
    }

    async def _go():
        # 提交成功（无连接）→ no_connection
        async with db_ctx() as s:
            await _notify(s)
            await s.commit()
        # 回滚 → rolled_back
        async with db_ctx() as s:
            await _notify(s)
            await s.rollback()
        await notification_push.drain_inflight()

    _run(_go())

    delta = {
        r: _push_count(r) - before[r]
        for r in ("delivered", "no_connection", "failed", "rolled_back")
    }
    assert sum(delta.values()) == 2, f"四态之和与推送条数不符：{delta}"
    assert delta["no_connection"] == 1 and delta["rolled_back"] == 1, delta
