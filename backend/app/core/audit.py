"""审计日志服务。

设计要点：
- 审计写入失败**绝不阻断主流程**（try/except + 日志），控制爆炸半径
- 提供 `log_detached` 用独立会话写入，避免认证失败等场景随主事务回滚丢失
- `log_detached` 内置**退避重试**：独立会话是另一个连接，与调用方的未提交
  写事务天然竞争锁；不重试会让审计静默丢失（本项目已三次踩坑）
- 敏感卷宗原文不落审计（detail 只存字段级 diff 与上下文摘要）
"""
import asyncio
from typing import Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.audit_context import get_audit_context
from app.core.metrics import metrics
from app.database import async_session_factory
from app.models.audit_log import AuditLog


class AuditAction:
    """审计动作常量。"""

    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    # 复核相关（PRD 5.5 全程留痕）
    REVIEW_CREATE = "REVIEW_CREATE"
    REVIEW_EDIT = "REVIEW_EDIT"
    REVIEW_SUBMIT = "REVIEW_SUBMIT"
    REVIEW_APPROVE = "REVIEW_APPROVE"
    REVIEW_REJECT = "REVIEW_REJECT"
    REVIEW_ARCHIVE = "REVIEW_ARCHIVE"
    REVIEW_VOID = "REVIEW_VOID"
    # 派单
    DISPATCH_CREATE = "DISPATCH_CREATE"
    DISPATCH_ACCEPT = "DISPATCH_ACCEPT"
    DISPATCH_REJECT = "DISPATCH_REJECT"
    # AI 运行
    AI_RUN_START = "AI_RUN_START"
    AI_RUN_FINISH = "AI_RUN_FINISH"
    # 案件分析（AI 初稿 → 律师编辑 → 迭代，责任可追溯）
    ANALYSIS_GENERATE = "ANALYSIS_GENERATE"
    ANALYSIS_EDIT = "ANALYSIS_EDIT"
    ANALYSIS_ITERATE = "ANALYSIS_ITERATE"
    # 证据（多模态材料，涉敏感卷宗）
    EVIDENCE_UPLOAD = "EVIDENCE_UPLOAD"
    EVIDENCE_PARSE = "EVIDENCE_PARSE"
    # 归档
    ARCHIVE_CREATE = "ARCHIVE_CREATE"
    HEARING_PACK_EXPORT = "HEARING_PACK_EXPORT"
    # 文件下载（卷宗原文获取，等保要求必须留痕）
    FILE_DOWNLOAD = "FILE_DOWNLOAD"
    # 知识库（企业私有资产）
    KNOWLEDGE_CREATE = "KNOWLEDGE_CREATE"
    KNOWLEDGE_DELETE = "KNOWLEDGE_DELETE"
    KNOWLEDGE_READ = "KNOWLEDGE_READ"
    # 文书
    DOCUMENT_RENDER = "DOCUMENT_RENDER"
    CONTRACT_REVIEW = "CONTRACT_REVIEW"
    COMPLIANCE_SCAN = "COMPLIANCE_SCAN"
    # 计费
    USAGE_CONSUME = "USAGE_CONSUME"
    QUOTA_ADJUST = "QUOTA_ADJUST"      # 管理员手动调整额度（Q-M：线下充值期间改额度必须留痕）
    WORK_ORDER_CREATE = "WORK_ORDER_CREATE"
    # 内容安全（《生成式人工智能服务管理暂行办法》第十四条留痕要求）
    CONTENT_BLOCKED = "CONTENT_BLOCKED"          # 内容被拦截（停止生成 / 停止传输）
    CONTENT_UNDER_REVIEW = "CONTENT_UNDER_REVIEW"  # 疑似内容放行待人工抽检
    CONTENT_REPORTED = "CONTENT_REPORTED"        # 已向主管部门报告
    CONTENT_REPORT_PENDING = "CONTENT_REPORT_PENDING"  # 应报未报：监管通道未配置/失败，欠一笔法定上报
    # 投诉举报（《暂行办法》第十五条：建立健全投诉举报机制）
    COMPLAINT_SUBMIT = "COMPLAINT_SUBMIT"        # 公众/用户提交投诉举报
    COMPLAINT_HANDLE = "COMPLAINT_HANDLE"        # 管理员处理投诉（含办结与不予受理）
    # 审计自身治理（审计的删除也必须被审计，否则无法回答"谁清掉了哪段日志"）
    AUDIT_RETENTION_PURGE = "AUDIT_RETENTION_PURGE"  # 按保留期清理审计日志（含归档校验信息）


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
    tenant_id: Optional[str] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
    success: bool = True,
) -> Optional[AuditLog]:
    """在当前会话内写审计；失败仅记录日志，不向调用方抛异常。"""
    if not settings.AUDIT_ENABLED:
        return None
    entry = AuditLog(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        detail=detail,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
        success=success,
    )
    db.add(entry)
    return entry


# 独立引擎/会话工厂：用于主事务回滚后仍需留痕的场景（如认证失败）
_detached_factory: Optional[async_sessionmaker] = None


def _get_detached_factory() -> async_sessionmaker:
    global _detached_factory
    if _detached_factory is None:
        kwargs: dict[str, Any] = {"echo": False}
        if not settings.is_sqlite:
            kwargs.update(pool_pre_ping=True)
        engine = create_async_engine(settings.DATABASE_URL, **kwargs)
        _detached_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _detached_factory


async def log_detached(
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
    tenant_id: Optional[str] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
    success: bool = True,
    retries: int = 5,
) -> None:
    """用独立会话写审计，脱离主事务生命周期。

    适用场景：主事务会被回滚（如认证失败 401），用当前会话写审计会一并丢失；
    或响应直接返回 `FileResponse` 不经过请求事务 commit。

    **为什么要重试**：独立会话意味着**另一个数据库连接**。若调用方此刻仍
    持有写事务未提交（例如刚执行完一批 DELETE），SQLite 会在库级加写锁，
    独立连接写入直接 `database is locked`；Postgres 也可能因行锁等待超时。
    早先实现"失败即忽略"，导致**审计静默丢失**——本项目已三次踩到同一坑
    （第 5 轮审计、第 7 轮内容拦截、第 8 轮清理墓碑），故在此内置退避重试。
    仍失败时打 **error** 级日志（而非 warning），避免"看起来正常"。
    """
    if not settings.AUDIT_ENABLED:
        return
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            # 工厂获取也放进 try —— 它同样可能失败（配置错误 / 引擎创建异常），
            # 而"审计失败绝不阻断主流程"这个契约是**无条件**的。
            factory = _get_detached_factory()
            async with factory() as session:
                session.add(
                    AuditLog(
                        action=action,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        actor_id=actor_id,
                        actor_role=actor_role,
                        tenant_id=tenant_id,
                        detail=detail,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        request_id=request_id,
                        success=success,
                    )
                )
                await session.commit()
            return
        except Exception as exc:  # noqa: BLE001 —— 审计失败绝不阻断主流程
            last_exc = exc
            await asyncio.sleep(0.05 * (attempt + 1))
    # 指标：审计丢失是**合规事故**（等保 2.0 三级要求审计记录完整留存），
    # 必须可被告警捕获，不能只留在日志里靠人翻。
    metrics.audit_write_failures_total.inc()
    logger.error(
        "审计写入失败（已重试 {} 次，该条审计已丢失，请检查数据库锁竞争）: {}",
        retries,
        last_exc,
    )


async def log_detached_ctx(
    action: str,
    resource_type: str,
    resource_id: Optional[int] = None,
    **kwargs: Any,
) -> None:
    """`log_detached` 的上下文自动补全版。

    自动从 `audit_context` 带出 IP / UA / request_id —— 否则「登录失败」
    这类安全事件会留下一条**没有来源 IP** 的记录，等于白留痕
    （等保与算法备案都要求能还原「谁、何时、从何处」）。
    """
    ctx = get_audit_context()
    kwargs.setdefault("ip_address", ctx.get("ip_address"))
    kwargs.setdefault("user_agent", ctx.get("user_agent"))
    kwargs.setdefault("request_id", ctx.get("request_id"))

    actor = kwargs.pop("actor", None)
    if actor is not None:
        kwargs.setdefault("actor_id", getattr(actor, "id", None))
        if "actor_role" not in kwargs:
            role = getattr(actor, "role", None)
            kwargs["actor_role"] = getattr(role, "value", role) if role is not None else None
        kwargs.setdefault("tenant_id", getattr(actor, "tenant_id", None))

    await log_detached(action=action, resource_type=resource_type, resource_id=resource_id, **kwargs)


async def record_with_detached_row(
    action: Any,
    resource_type: str,
    *,
    row: Any,
    resource_id: Optional[int] = None,
    actor: Any = None,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
    tenant_id: Optional[str] = None,
    detail: Optional[dict] = None,
    extra_audits: Optional[list[tuple[str, dict]]] = None,
    **kwargs: Any,
) -> bool:
    """在**同一个独立事务**里同时写入一条业务留痕行与一条（或多条）审计行。

    为什么需要它（第 7 轮踩到的坑）：
    内容审核命中时调用方会 `raise ContentBlockedError`，`get_db` 捕获异常后
    `rollback()`，于是用请求会话写的 `moderation_records` 与 `audit_logs`
    **一起被丢弃**——留痕彻底失效，而第十四条恰恰要求「保存有关记录」。

    因此命中（拦截）路径必须用独立会话、独立事务、立即 commit。
    把「业务行 + 审计行」放进**同一个事务**是有意为之：宁可两条都失败，
    也不要出现「有审核记录但查不到审计」或反之的**对不上账**。

    `action` 可传单个动作码，也可传动作码列表（`extra_audits` 用于附加
    「同一业务事实有多条审计含义」的场景，如既「拦截」又「欠上报」）。

    返回是否写入成功。失败只告警，绝不抛异常（拦截动作优先）。
    """
    if not settings.AUDIT_ENABLED:
        return False
    try:
        ctx = get_audit_context()

        if actor is not None:
            actor_id = actor_id if actor_id is not None else getattr(actor, "id", None)
            if actor_role is None:
                role = getattr(actor, "role", None)
                actor_role = getattr(role, "value", role) if role is not None else None
            if tenant_id is None:
                tenant_id = getattr(actor, "tenant_id", None)

        actions = [action] if isinstance(action, str) else list(action)
        for extra_action, extra_detail in extra_audits or []:
            actions.append(extra_action)

        factory = _get_detached_factory()
        async with factory() as session:
            session.add(row)
            detail_by_action = dict(extra_audits or [])
            for act in actions:
                session.add(
                    AuditLog(
                        action=act,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        actor_id=actor_id,
                        actor_role=actor_role,
                        tenant_id=tenant_id,
                        detail=detail_by_action.get(act, detail),
                        ip_address=ctx.get("ip_address"),
                        user_agent=ctx.get("user_agent"),
                        request_id=ctx.get("request_id"),
                        success=kwargs.pop("success", True),
                    )
                )
            await session.commit()
        return True
    except Exception as exc:
        logger.warning(f"审核留痕写入失败（已忽略，拦截动作仍生效）: {exc}")
        return False


async def record(
    db: AsyncSession,
    action: str,
    resource_type: str,
    resource_id: Optional[int] = None,
    *,
    actor: Any = None,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
    tenant_id: Optional[str] = None,
    detail: Optional[dict] = None,
    **kwargs: Any,
) -> None:
    """便捷封装：写审计并立即 flush（不 commit，由外层事务统一提交）。

    自动补全三件事，避免每个调用点重复：
    1. **请求上下文**（IP / UA / request_id）—— 从 `audit_context` 取
    2. **操作者** —— 传 `actor=user` 即可，自动拆出 id 与 role
    3. **租户** —— 未显式传时从 actor 推导

    审计失败一律吞掉并告警，**绝不阻断主流程**（合规要求留痕，但留痕失败
    不应导致业务不可用）。
    """
    try:
        ctx = get_audit_context()

        if actor is not None:
            actor_id = actor_id if actor_id is not None else getattr(actor, "id", None)
            if actor_role is None:
                role = getattr(actor, "role", None)
                actor_role = getattr(role, "value", role) if role is not None else None
            if tenant_id is None:
                tenant_id = getattr(actor, "tenant_id", None)

        await write_audit(
            db,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            detail=detail,
            ip_address=ctx.get("ip_address"),
            user_agent=ctx.get("user_agent"),
            request_id=ctx.get("request_id"),
            success=kwargs.pop("success", True),
        )
        await db.flush()
    except Exception as exc:
        logger.warning(f"审计 flush 失败（已忽略）: {exc}")


__all__ = [
    "AuditAction",
    "write_audit",
    "log_detached",
    "log_detached_ctx",
    "record",
    "record_with_detached_row",
    "async_session_factory",
]
