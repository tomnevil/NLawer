"""通知读路径接口（P0-15）。

## 背景：为什么这个模块此前不存在

通知写入侧（`notification_service.notify`）自项目初期就在 5 个业务节点
（派单 / 归档 / 计费 ×2 / 复核）持续写库，但**读取侧从未实现**：
- 没有列表接口、没有未读数接口、没有标记已读接口
- `is_read` / `read_at` 全库无赋值点 → 已读状态永远为 0
- 前端 `AppShell` 的铃铛是**没有 onClick 的死按钮**，红点硬编码常亮

结果是通知写进库即成为黑洞：用户在界面上看不到任何一条。本模块补齐读侧。

## 安全设计（本模块最关键的约束）

通知是**用户级**资源，不是租户级资源。仅按 `tenant_id` 过滤会让同一律所内
律师甲读到律师乙的通知（含案件标题、客户信息、复核结论）。因此：

1. 所有查询强制同时带 `tenant_id` 与 `user_id`（见 `notification_service._owned`）
2. 越权访问（他人通知 / 他租户通知）一律返回 **404 而非 403** —— 
   403 会告诉攻击者「这个 id 存在，只是你没权限」，可用来枚举系统内
   通知总量与他人活跃度；404 不泄露存在性。这是有意的取舍：牺牲一点
   排错便利性，换取不泄露资源存在性。
3. `user_id` 一律取自 `get_tenant_context`（由 JWT 派生），**不接受任何
   请求参数传入** —— 否则等于开放了「按 user_id 读任意人通知」的后门。

## 为什么不做审计留痕

读取通知是高频只读操作（每次点铃铛、每次轮询），逐条落审计表会让
`audit_logs` 被噪音淹没，反而降低真正需要审计的写操作的可见性。
通知行本身（含 `read_at`）即为「谁在何时读过什么」的记录，无需重复留痕。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_tenant_context
from app.core.errors import BadRequestError, ErrorCode, NotFoundError
from app.core.pagination import Page, PaginationParams, count_bounded, ok
from app.models.enums import NotificationType
from app.schemas.notification import (
    NotificationOut,
    ReadBatchIn,
    ReadResultOut,
    UnreadCountOut,
)
from app.services import notification_service as svc

router = APIRouter(prefix="/notifications", tags=["通知"])


def _parse_type(raw: str | None) -> str | None:
    """校验 type 参数。

    不做「未知类型静默返回空列表」：那会让前端把拼写错误（如
    `REVIEW_REQUIRED` 写成 `REVIEW_REQUIRE`）当成「没有这类通知」，
    排查成本极高。显式 400 让错误在第一跳就暴露。
    """
    if not raw:
        return None
    try:
        return NotificationType(raw).value
    except ValueError:
        raise BadRequestError(
            f"未知通知类型：{raw}",
            code=ErrorCode.VALIDATION_ERROR,
            details={"allowed": [t.value for t in NotificationType]},
        )


@router.get("", response_model=dict, summary="我的通知列表")
async def list_notifications(
    params: PaginationParams = Depends(),
    is_read: bool | None = Query(None, description="按已读/未读筛选，不传为全部"),
    type: str | None = Query(None, description="按通知类型筛选"),
    since_id: int | None = Query(
        None,
        ge=0,
        description=(
            "增量补拉锚点：只返回 id 大于该值的通知。"
            "传入时结果按 id **正序**返回，且 `page` 被忽略"
            "（客户端应把 since_id 更新为已收到的最大 id 再取下一页，即 keyset 分页）"
        ),
    ),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """列出当前登录用户的通知。

    响应内附带 `unread_total`：铃铛角标与列表是同一屏的两个视图，
    分两次请求会在弱网下出现「角标已清零但列表仍有未读」的撕裂。
    一次往返同时给出，前端渲染天然一致。

    ## `since_id`（断线补拉）

    前端断线重连后需要「把错过的补回来」，而不是重新拉一遍。`since_id`
    提供这个能力：客户端记着本地已见过的最大 id，重连后只取比它新的部分。

    该模式下**返回正序且忽略 `page`**（详见 `svc.list_for_user` 的说明：
    倒序 + offset 在追赶场景会因新写入导致位移、产生重复与漏项）。
    客户端按 keyset 方式推进——取一页、把 `since_id` 设为返回项的最大 id、
    再取下一页，直到返回条数少于 `page_size`。

    该模式下不做有界计数（`total` 恒为本次返回条数）：补拉关心的是
    「还有没有更多」，而不是「历史上共有多少条」；为一个追赶查询跑
    一次 `COUNT` 是纯浪费，且 `total` 在客户端合并去重后本就无意义。
    """
    type_value = _parse_type(type)

    if since_id is not None:
        rows = await svc.list_for_user(
            db,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            is_read=is_read,
            type=type_value,
            since_id=since_id,
            limit=params.limit,
        )
        unread = await svc.unread_count(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        page = Page.build(
            [NotificationOut.model_validate(r).model_dump() for r in rows],
            len(rows),
            params,
        ).model_dump()
        page["unread_total"] = unread["total"]
        # 高水位：客户端据此判断「是否已追平」，不必自己从结果里推
        page["latest_id"] = unread["latest_id"]
        page["since_id"] = since_id
        return ok(page)

    base = svc.build_query(ctx.tenant_id, ctx.user_id, is_read=is_read, type=type_value)
    total, is_lower_bound = await count_bounded(db, base)
    rows = await svc.list_for_user(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        is_read=is_read,
        type=type_value,
        offset=params.offset,
        limit=params.limit,
    )
    unread = await svc.unread_count(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)

    page = Page.build(
        [NotificationOut.model_validate(r).model_dump() for r in rows],
        total,
        params,
        total_is_lower_bound=is_lower_bound,
    ).model_dump()
    page["unread_total"] = unread["total"]
    return ok(page)


@router.get("/unread-count", response_model=dict, summary="我的未读数")
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """未读数（总数 + 按类型分组）。

    供轮询调用。刻意做成极轻量的聚合查询：只有 `GROUP BY type` 一次扫描，
    走 `(tenant_id, user_id, is_read)` 覆盖索引，不触达数据行。
    """
    data = await svc.unread_count(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return ok(UnreadCountOut(**data).model_dump())


@router.get("/{notification_id}", response_model=dict, summary="通知详情")
async def get_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """单条通知详情。越权/不存在统一 404（不泄露存在性）。"""
    n = await svc.get_owned(
        db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, notification_id=notification_id
    )
    if n is None:
        raise NotFoundError("通知不存在", code=ErrorCode.NOTIFICATION_NOT_FOUND)
    return ok(NotificationOut.model_validate(n).model_dump())


@router.post("/{notification_id}/read", response_model=dict, summary="标记单条已读")
async def read_one(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """标记单条已读。**幂等**：重复调用返回 200，`read_at` 不被刷新。

    用 POST 而非 PATCH：这是「执行一次标记动作」，不是「部分更新资源」，
    且语义上允许重复执行（幂等），符合 POST 的宽松语义。
    """
    n = await svc.mark_read(
        db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, notification_id=notification_id
    )
    if n is None:
        raise NotFoundError("通知不存在", code=ErrorCode.NOTIFICATION_NOT_FOUND)
    unread = await svc.unread_count(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return ok(
        {
            "notification": NotificationOut.model_validate(n).model_dump(),
            "unread_total": unread["total"],
        }
    )


@router.post("/read", response_model=dict, summary="批量标记已读")
async def read_batch(
    payload: ReadBatchIn,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """批量标记已读。

    `ids` 为空 → 全部未读标记已读（等价于「全部已读」，但保留了
    `type` 限定能力，律师端可只清「待复核」类）。

    越权的 id 静默跳过而非报错：批量操作里只要有一个越权 id 就让整批
    失败，会把「前端缓存了过期 id」这类正常情况变成用户可见的报错。
    归属约束仍在 WHERE 里强制生效，跳过的是**非法**部分，安全不受影响。
    """
    type_value = _parse_type(payload.type)
    updated = await svc.mark_read_batch(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        ids=payload.ids,
        type=type_value,
    )
    unread = await svc.unread_count(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return ok(ReadResultOut(updated=updated, unread_total=unread["total"]).model_dump())


@router.post("/read-all", response_model=dict, summary="全部标记已读")
async def read_all(
    type: str | None = Query(None, description="仅标记该类型的全部通知"),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """全部标记已读。

    与 `POST /read`（空 body）功能等价，单独保留是为了给前端一个
    **语义明确、无需构造请求体**的入口 —— 「一键已读」按钮不应因为
    body 序列化差异而失败。
    """
    updated = await svc.mark_read_batch(
        db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, type=_parse_type(type)
    )
    unread = await svc.unread_count(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return ok(ReadResultOut(updated=updated, unread_total=unread["total"]).model_dump())
