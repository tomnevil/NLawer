"""通知推送桥：把「事务提交」与「实时推送」绑定在一起。

## 要解决的核心问题：推送不能早于落库

`notify()` 在业务事务**内部**执行，而业务事务随时可能回滚（校验失败、
并发冲突、下游异常……）。若在 `notify()` 里 `await flush()` 之后就直接推送，
会出现一类最难排查的错误：

> 客户端弹出一条「您有新派单待接」，用户点进去 → **案件不存在**。
> 因为那条通知的写入随事务一起回滚了，**只有推送到达了**。

这类「幽灵通知」不报错、不留日志、只在特定失败路径下出现，而且**直接
摧毁用户对通知的信任**——一条点不开的通知比十条不推送更糟。

因此推送必须**挂在事务提交之后**：只有数据真的落库了，才告诉客户端。

## 实现方式：`session.info` + `after_commit` 事件

1. `notify()` 落库后，把「待推送」追加到 `session.info`（不发送）。
2. SQLAlchemy 的 `after_commit` 事件触发时**取出并清空**该队列，逐个调度发送。
3. `after_rollback` 触发时**丢弃**队列（这些行从未落库，推出去就是幽灵）。

选 `session.info` 而不是「维护一个全局待推送列表」的原因：`session.info`
**天然按会话隔离**。全局列表在并发请求下会把 A 请求的待推送混进 B 请求的
提交里，导致跨请求的错误推送——而 `session.info` 的生命周期与会话一致，
提交/回滚/关闭时自然终结，不需要额外的清理逻辑。

## 为什么用 `create_task` 而不是同步发送

`after_commit` 是**同步**回调（SQLAlchemy 事件不允许 await）。所以这里只
调度协程，不等结果。这带来两个必须处理的细节：

- **任务必须被强引用持有**：`asyncio` 只持有 `Task` 的**弱引用**，若不留引用，
  任务可能在执行到一半时被垃圾回收，表现为「推送时有时无」。因此用模块级
  `_inflight` 集合持有，完成后移除。
- **发送异常必须内部消化**：任务脱离调用栈后无人 `await`，异常会变成
  「Task exception was never retrieved」告警。`_safe_send` 全量兜底。

## 无事件循环时的行为

若 `after_commit` 在**没有运行中事件循环**的上下文触发（同步脚本、
Alembic 迁移、部分测试），则**跳过推送**并记 debug 日志。
这是正确的降级：落库已完成，推送只是旁路；为推送强行创建/获取事件循环
会引入更严重的问题（循环泄漏、跨线程使用 loop）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.metrics import metrics
from app.services.connection_manager import ConnectionManager, PushOutcome, manager

#: `session.info` 中的队列键。带下划线前缀以表明「内部约定」。
_PENDING_KEY = "_pending_notification_pushes"

#: 持有 fire-and-forget 任务的强引用（见模块 docstring）。
_inflight: set[asyncio.Task] = set()

#: 推送消息的帧类型，与 WS 端点的其他帧区分。
FRAME_TYPE = "notification"

#: 推送结果标签（与 PRD NP-12 的四态一致）。
RESULT_DELIVERED = "delivered"
RESULT_NO_CONNECTION = "no_connection"
RESULT_FAILED = "failed"
RESULT_ROLLED_BACK = "rolled_back"


def queue_push(
    db: AsyncSession, *, user_id: int, payload: dict[str, Any]
) -> None:
    """登记一次待推送（**不发送**，等事务提交）。

    `user_id <= 0` 直接忽略：与 `notify()` 的「无有效接收人则拒写」同一原则，
    这种通知对任何人都不可见，推出去只会浪费一次连接遍历。

    同时记录 `time.monotonic()` 作为**时延起点**。用单调时钟而非墙上时钟：
    墙上时钟会被 NTP 校正回拨，算出负时延污染直方图（`_sum` 一旦被负值
    污染就再也修不回来）。
    """
    if not user_id or user_id <= 0:
        return
    db.info.setdefault(_PENDING_KEY, []).append(
        (int(user_id), payload, time.monotonic())
    )


def _drain(session: Session) -> list[tuple[int, dict[str, Any], float]]:
    """取出并清空待推送队列（幂等：无队列时返回空列表）。"""
    return session.info.pop(_PENDING_KEY, [])


def _spawn(coro) -> None:
    """调度一个协程并**持有强引用**，完成后自动释放。"""
    task = asyncio.ensure_future(coro)
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)


def _record(result: str, queued_at: float) -> None:
    """记录一次推送结果与时延。**任何路径都必须经过这里**，否则面板会失真。"""
    metrics.notification_push_total.inc((result,))
    metrics.notification_push_latency_milliseconds.observe(
        None, (time.monotonic() - queued_at) * 1000.0
    )


async def _safe_send(
    user_id: int, payload: dict[str, Any], mgr: ConnectionManager, queued_at: float
) -> None:
    """推送单条通知，**异常全量消化**。"""
    result = RESULT_FAILED
    try:
        outcome: PushOutcome = await mgr.send_to_user(
            user_id, {"type": FRAME_TYPE, "data": payload}
        )
        if outcome.delivered > 0:
            result = RESULT_DELIVERED
        elif not outcome.had_connections:
            # 用户没在线：常态。通知已在库中，轮询/重连会补上。
            result = RESULT_NO_CONNECTION
        else:
            # 有连接但一个都没发出去 = 连接已损坏。这是事故，必须与
            # 「用户没在线」区分开，否则告警永远被常态淹没。
            result = RESULT_FAILED
            logger.warning(
                "推送失败：user_id={} 在线连接 {} 个全部发送失败（连接将被回收）",
                user_id, outcome.attempted,
            )
    except Exception as exc:  # noqa: BLE001  旁路失败不得上抛
        logger.warning("通知实时推送失败（已忽略）：user_id={} err={}", user_id, exc)
    finally:
        # 用 `finally` 而不是在每个分支里各记一次：漏记某条分支的代价是
        # 「delivered + no_connection + failed + rolled_back ≠ 落库总数」，
        # 而指标不守恒会直接毁掉对账能力，比少一个标签严重得多。
        _record(result, queued_at)


@event.listens_for(Session, "after_commit")
def _push_after_commit(session: Session) -> None:
    """事务提交后发送待推送。**这是推送的唯一触发点。**"""
    pending = _drain(session)
    if not pending:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 无事件循环 ⇒ 没有可用的连接，推送被丢弃。计为 failed 而非
        # no_connection：这是一个「本该推送却没推」的事实，若混入常态值
        # 就再也无法从指标上发现（实际只会在迁移脚本等同步上下文出现）。
        for _user_id, _payload, queued_at in pending:
            _record(RESULT_FAILED, queued_at)
        logger.debug(
            "无运行中的事件循环，跳过 {} 条实时推送（通知已落库，客户端可通过轮询/重连补拉）",
            len(pending),
        )
        return
    for user_id, payload, queued_at in pending:
        _spawn(_safe_send(user_id, payload, manager, queued_at))


@event.listens_for(Session, "after_rollback")
def _discard_after_rollback(session: Session) -> None:
    """事务回滚后丢弃待推送，**防止幽灵通知**。

    这里记的 `rolled_back` 是**幽灵通知防线的活体证据**：它每增加 1，
    就代表「一条本会被推出去、但点开不存在的通知」被拦下了。
    没有这个计数，这条防线是否真的在生效就只能靠读代码相信。
    """
    dropped = _drain(session)
    if dropped:
        for _user_id, _payload, queued_at in dropped:
            _record(RESULT_ROLLED_BACK, queued_at)
        logger.debug("事务回滚，丢弃 {} 条实时推送（对应通知未落库）", len(dropped))


def pending_count(db: AsyncSession) -> int:
    """当前会话中待推送的条数（供测试断言「是否已登记」）。"""
    return len(db.info.get(_PENDING_KEY, ()))


def inflight_tasks() -> int:
    """尚未完成的推送任务数（供测试等待/断言）。"""
    return len(_inflight)


async def drain_inflight(timeout: float = 5.0) -> None:
    """等待所有在途推送任务结束（**仅供测试**，避免断言早于推送完成）。"""
    if not _inflight:
        return
    pending = list(_inflight)
    await asyncio.wait(pending, timeout=timeout)


def install() -> None:
    """显式安装钩子。

    `event.listens_for` 在**导入本模块**时即已生效，因此该函数本身不做事。
    存在的意义是：让 `main.py` 的启动流程显式表达「推送桥已启用」，
    并保证本模块被导入（否则事件监听器根本不会注册）。
    """
    return None


def reset_for_tests() -> None:
    """**仅供测试**：清空在途任务引用，避免用例间相互污染。"""
    _inflight.clear()
