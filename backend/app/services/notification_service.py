"""通知服务：派单 / 接单 / 材料缺失 / 复核 / 定稿等节点推送。

异常 try/except + 日志兜底，不阻断主流程（audit 同策略）。

## 读路径（P0-15）

写入侧自项目初期就存在，但读取侧长期缺失：`is_read` / `read_at` 全库无赋值点，
通知写进库即成为**黑洞**——用户永远看不到。本模块补齐读侧。

### 为什么所有读侧函数都必须同时收 `tenant_id` 与 `user_id`？

`tenant_id` 只解决「律所 A 看不到律所 B」；但通知是**用户级**资源，
同一律所内律师甲与律师乙的通知也必须互不可见。只按 tenant 过滤会让
同租户任意律师读到同事的通知（含案件标题、客户信息、复核结论），
这是本模块最严重的越权面。因此签名上强制两者同时出现，**不提供
只按 tenant 过滤的读接口**，从 API 形状上杜绝误用。
"""
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import NotificationType
from app.models.notification import Notification
from app.schemas.notification import NotificationOut
from app.services.notification_push import queue_push

# 类型 -> 默认标题
_TITLE: dict[NotificationType, str] = {
    NotificationType.DISPATCH_CREATED: "新派单待接",
    NotificationType.CASE_ACCEPTED: "案件已接单",
    NotificationType.EVIDENCE_MISSING: "证据材料缺失提醒",
    NotificationType.REVIEW_REQUIRED: "待复核任务",
    NotificationType.REVIEW_DECIDED: "复核已出结论",
    NotificationType.DOCUMENT_CONFIRMED: "文书已确认定稿",
    NotificationType.CASE_ARCHIVED: "案件已归档",
    NotificationType.QUOTA_WARNING: "用量额度预警",
    NotificationType.WORK_ORDER_CREATED: "计费工单已生成",
}


def _push_payload(n: Notification) -> dict[str, Any]:
    """把通知序列化为**推送帧**载荷。

    刻意复用读接口的 `NotificationOut`，而不是手写一份 dict：

    1. **前端只有一套解析逻辑**。若推送帧与 `GET /notifications` 的字段形状
       不一致，前端就得为「实时到的通知」和「拉取到的通知」各写一份解析，
       两份迟早会漂移（典型后果：实时到的那条没有 `ref_id`，点击无法跳转）。
    2. **`is_read` 的类型一致性**。库里是 `Integer(0/1)`，`NotificationOut`
       统一收敛为 `bool`。手写 dict 极易漏掉这一步，导致前端实时收到 `0`
       而拉取时收到 `false`，`!item.is_read` 这类判断在两种来源下行为不同。

    `mode="json"` 让 `datetime` 落成 ISO 字符串，可直接 JSON 序列化。
    """
    return NotificationOut.model_validate(n).model_dump(mode="json")


async def notify(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: Optional[int],
    type: NotificationType,
    content: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
    payload: Optional[dict] = None,
) -> Optional[Notification]:
    """写入一条通知（失败仅告警，不影响主流程）。

    ## 实时推送

    落库后**登记**一次推送（`queue_push`），真正发送发生在**事务提交之后**。
    推送是旁路：它失败只记日志，绝不影响通知落库与调用方业务流程。

    ## 为什么拒绝「无接收人」的通知（P0-15 补）

    通知是**用户级**资源，读路径一律按 `user_id == 当前登录用户` 过滤。
    因此一条 `user_id` 为空或 0 的通知，**对任何人都不可见**——它不是
    「稍后可见」，而是永久黑洞：既占存储，又让「未读积压」这类统计失真。

    历史成因：计费侧调用点写的是 `user_id=user_id or 0`，当调用方没有
    具体用户上下文（如租户级批量扣费）时，`None` 被兜底成 `0`，而 `0`
    不是任何真实用户的 id。此处从**写入侧**堵住：宁可不写，也不写一条
    没人能读到的记录。真正需要「租户级公告」时应新增独立的广播模型，
    而不是复用用户级通知并塞一个哨兵 id。
    """
    if not user_id:
        logger.warning(
            "通知缺少有效接收人（user_id={!r}），已跳过写入：type={} ref={}/{}",
            user_id,
            getattr(type, "value", type),
            ref_type,
            ref_id,
        )
        return None

    try:
        n = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            type=type,
            title=_TITLE.get(type, "系统通知"),
            content=content,
            ref_type=ref_type,
            ref_id=ref_id,
            payload=payload,
        )
        db.add(n)
        await db.flush()
        # 落库成功后才登记实时推送。**这里不发送**——真正发送挂在事务
        # 提交之后（`notification_push._push_after_commit`）。若在 flush 后
        # 直接推送，事务一旦回滚，客户端会收到一条**点开不存在**的幽灵通知。
        queue_push(db, user_id=user_id, payload=_push_payload(n))
        return n
    except Exception as exc:  # noqa: BLE001  通知失败不应阻断业务
        logger.warning("通知写入失败（已忽略）: {}", exc)
        return None


async def notify_many(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_ids: list[int],
    type: NotificationType,
    content: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
) -> None:
    for uid in user_ids:
        await notify(
            db,
            tenant_id=tenant_id,
            user_id=uid,
            type=type,
            content=content,
            ref_type=ref_type,
            ref_id=ref_id,
        )


# ═══════════════════════════════════════════════════════════════════════════
# 读路径（P0-15）
# ═══════════════════════════════════════════════════════════════════════════


def _owned(user_id: int, tenant_id: str):
    """读侧查询的**唯一**归属条件构造器。

    集中在一处，避免各调用点各写一份 WHERE 而漏掉 `user_id` 或 `tenant_id`。
    历史缺陷模式：`WHERE id = ? AND tenant_id = ?` 漏掉 user_id —— 同租户
    跨用户越权正是这样产生的。
    """
    return (Notification.tenant_id == tenant_id) & (Notification.user_id == user_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_query(
    tenant_id: str,
    user_id: int,
    *,
    is_read: Optional[bool] = None,
    type: Optional[str] = None,
    since_id: Optional[int] = None,
):
    """构造**已带归属约束**的查询语句（列表与计数共用）。

    对外暴露是为了让路由层能在不接触 `Notification` 模型细节的前提下
    复用同一份 WHERE 条件去做有界计数——若路由自己再拼一遍条件，
    就出现了第二处需要维护归属约束的地方，漏改即越权。

    `since_id` 是**增量补拉**的锚点（`id > since_id`）。它服务于断线重连：
    客户端记着「本地已见过的最大 id」，重连后只取比它新的部分。
    """
    stmt = select(Notification).where(_owned(user_id, tenant_id))
    if is_read is not None:
        stmt = stmt.where(Notification.is_read == (1 if is_read else 0))
    if type:
        stmt = stmt.where(Notification.type == type)
    if since_id is not None:
        stmt = stmt.where(Notification.id > since_id)
    return stmt


async def get_owned(
    db: AsyncSession, *, tenant_id: str, user_id: int, notification_id: int
) -> Optional[Notification]:
    """按 id 取**本人**通知；不存在或不属于本人/本租户时返回 `None`。"""
    return (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id, _owned(user_id, tenant_id)
            )
        )
    ).scalars().first()


async def list_for_user(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: int,
    is_read: Optional[bool] = None,
    type: Optional[str] = None,
    since_id: Optional[int] = None,
    offset: int = 0,
    limit: int = 20,
) -> Sequence[Notification]:
    """列出**本人**通知。

    默认按 `id` **倒序**（最新在前）。

    `id` 倒序而非 `created_at` 倒序：同一批业务动作（如归档同时通知客户与
    承办律师）会在同一秒写入，`created_at` 精度不足以稳定排序，会导致翻页
    时出现重复/漏项。`id` 单调递增且与写入顺序一致，天然稳定。

    ## 传入 `since_id` 时改为**正序**，且 `offset` 被忽略

    这是刻意的：`since_id` 表达的是**另一种查询意图**——不是「翻页」，
    而是「增量追赶」。此时必须正序，理由是 **offset 分页在追赶场景下不稳定**：

    倒序 + `OFFSET` 分页时，若追赶过程中又有新通知写入，整个结果集
    向前位移，第 2 页会重复第 1 页的行、并跳过原本应在第 2 页的行
    （经典的 offset 漂移）。正序 + **keyset**（客户端把 `since_id`
    更新为已收到的最大 id）则每页都重新锚定在 id 上，**不受新写入影响**，
    不重不漏。

    因此 `since_id` 模式下客户端应：取一页 → 把 `since_id` 设为返回项的最大 id
    → 再取一页 → 直到返回条数 < `limit` 或结果为空。
    """
    stmt = build_query(tenant_id, user_id, is_read=is_read, type=type, since_id=since_id)
    if since_id is not None:
        # 增量追赶：正序 + 不用 offset（见 docstring）
        stmt = stmt.order_by(Notification.id.asc()).limit(limit)
    else:
        stmt = stmt.order_by(Notification.id.desc()).offset(offset).limit(limit)
    rows = await db.execute(stmt)
    return rows.scalars().all()


async def count_for_user(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: int,
    is_read: Optional[bool] = None,
    type: Optional[str] = None,
) -> int:
    """计数（无界；路由层请改用 `pagination.count_bounded` 以免大集合下计数拖慢响应）。"""
    stmt = build_query(tenant_id, user_id, is_read=is_read, type=type)
    bounded = select(func.count()).select_from(stmt.subquery())
    return int((await db.execute(bounded)).scalar_one())


async def unread_count(
    db: AsyncSession, *, tenant_id: str, user_id: int
) -> dict[str, Any]:
    """未读数：总数 + 按类型分组 + 最新未读 id。

    用一条 `GROUP BY type` 拿到分类明细，避免前端为每个 Tab 各发一次请求
    （律师端底部 Tab 有 5 项，最坏情况会放大成 5 次往返，弱网下体验很差）。

    `latest_id` 供前端做「有没有新东西」的廉价判据：轮询拿到同一个
    `latest_id` 即可跳过列表重渲染，避免每 30 秒无谓地重建一次列表
    （移动端上重渲染会打断用户的滚动位置）。
    """
    stmt = (
        select(Notification.type, func.count())
        .where(_owned(user_id, tenant_id), Notification.is_read == 0)
        .group_by(Notification.type)
    )
    rows = (await db.execute(stmt)).all()
    by_type: dict[str, int] = {}
    total = 0
    for t, c in rows:
        key = t.value if isinstance(t, NotificationType) else str(t)
        by_type[key] = int(c)
        total += int(c)

    latest = (
        await db.execute(
            select(func.max(Notification.id)).where(
                _owned(user_id, tenant_id), Notification.is_read == 0
            )
        )
    ).scalar_one()

    return {"total": total, "by_type": by_type, "latest_id": int(latest or 0)}


async def mark_read(
    db: AsyncSession, *, tenant_id: str, user_id: int, notification_id: int
) -> Optional[Notification]:
    """标记单条已读。**幂等**：已读的再标一次不报错、不刷新 `read_at`。

    返回 `None` 表示「不存在 **或** 不属于该用户 / 该租户」——
    两者在 API 层都映射为 404，不区分，避免通过状态码探测他人通知的存在性。

    `read_at` 只在**首次**从未读转已读时写入：若每次调用都刷新时间戳，
    重复调用会篡改「用户何时真正读过」这一事实，而该字段在通知场景下
    具有证据价值（例如「律师是否在开庭前读过派单提醒」）。
    """
    n = await get_owned(
        db, tenant_id=tenant_id, user_id=user_id, notification_id=notification_id
    )
    if n is None:
        return None
    if not n.is_read:
        n.is_read = 1
        n.read_at = _now_iso()
        await db.flush()
    return n


async def mark_read_batch(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: int,
    ids: Optional[list[int]] = None,
    type: Optional[str] = None,
) -> int:
    """批量标记已读，返回**实际发生状态迁移**的行数。

    - `ids` 非空 → 只处理这些 id（仍受 user/tenant 归属约束，越权 id 静默跳过）
    - `ids` 为空 → 全部未读标记已读（可用 `type` 限定类型）

    单条 UPDATE 完成，不做 N 次 SELECT+UPDATE：批量场景下（律师出差回来
    一次清 200 条）逐条往返会让请求耗时线性膨胀。
    返回值恒为「本次真正从未读变已读的行数」，因此重复调用返回 0 —— 
    这正是幂等性的可断言证据。
    """
    conds = [_owned(user_id, tenant_id), Notification.is_read == 0]
    if ids:
        conds.append(Notification.id.in_(list(ids)))
    elif type:
        conds.append(Notification.type == type)

    result = await db.execute(
        update(Notification)
        .where(*conds)
        .values(is_read=1, read_at=_now_iso())
        # `synchronize_session="fetch"` 而非 `False`：
        # 批量 UPDATE 走 Core 层，ORM 身份映射不会自动感知行变化。若用 False，
        # 同一会话内「先批量标记、再读列表」会拿到**陈旧的 is_read=0**——
        # 一个不报错、不抛异常、只在特定调用顺序下出现的静默错误。
        # `fetch` 会取回受影响主键并同步会话内对象（实测 SQLAlchemy 2.0.35
        # 下对象属性即时更新为 1），代价是一次有界 SELECT（≤200 行）。
        .execution_options(synchronize_session="fetch")
    )
    await db.flush()
    return int(result.rowcount or 0)

