"""内容安全审核服务层：审核 + 留痕 + 上报（P0-13）。

`core/moderation.py` 是**纯判定引擎**（无 IO，可单测、可离线跑）。
本模块负责把判定结果落到 `moderation_records` 并写审计日志——
对应《生成式人工智能服务管理暂行办法》第十四条的「保存有关记录」。

为什么判定与留痕分离：
- 判定引擎必须能在流式输出里**同步、高频**调用（每个 SSE 片段一次），
  不能每次都碰数据库。
- 留痕是低频的（只在命中时发生），且需要事务与审计链路配合。
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, record, record_with_detached_row
from app.core.audit_context import get_audit_context
from app.core.metrics import metrics
from app.core.moderation import (
    ContentModerator,
    ModerationLevel,
    ModerationResult,
    report_to_authority,
)
from app.models.moderation import ModerationRecord, ReportStatus


def _report_note(reported: bool, needs_report: bool, report_status: str) -> Optional[str]:
    """上报备注：与 `report_status` 一一对应，不得再把两种失败混成一句话。

    - 已上报 ⇒ 无备注
    - `PENDING` ⇒ 通道压根没配 ⇒ 去配通道
    - `FAILED`  ⇒ 配了但这次没发成功 ⇒ 重发 / 排查接口
    """
    if reported or not needs_report:
        return None
    if report_status == ReportStatus.FAILED.value:
        return "监管上报调用失败（通道已配置），需重发并排查通道"
    return "监管上报通道未配置，需人工补报"


def _sha32(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _full_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ModerationService:
    """把审核判定与持久化/审计串起来。"""

    def __init__(self, db: Optional[AsyncSession] = None) -> None:
        self.db = db
        self._moderator: Optional[ContentModerator] = None

    # ---------------- 引擎（懒加载，便于替换词库）----------------
    @property
    def moderator(self) -> ContentModerator:
        if self._moderator is None:
            if settings.MODERATION_TERMS_FILE:
                from app.core.moderation import load_terms_from_file

                self._moderator = ContentModerator(
                    terms=load_terms_from_file(settings.MODERATION_TERMS_FILE)
                )
            else:
                self._moderator = ContentModerator()
        return self._moderator

    # ---------------- 审核（带留痕）----------------
    async def check(
        self,
        text: str,
        *,
        side: str,
        scene: str,
        actor: Any = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> ModerationResult:
        """审核并（在需要时）留痕。返回判定结果供调用方决定处置。"""
        if not settings.MODERATION_ENABLED:
            return ModerationResult(side=side)

        result = await self.moderator.check(text, side=side)

        # PASS 不留痕（否则表会被正常流量淹没，且无合规价值）
        if result.level == ModerationLevel.PASS and not result.degraded:
            return result

        await self._persist(
            result,
            text=text,
            scene=scene,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            tenant_id=tenant_id,
        )
        return result

    async def _persist(
        self,
        result: ModerationResult,
        *,
        text: str,
        scene: str,
        actor: Any,
        resource_type: Optional[str],
        resource_id: Optional[int],
        tenant_id: Optional[str],
    ) -> None:
        """写记录 + 写审计 + 触发上报判定。

        **绝不因留痕失败阻断主流程**（与审计一致的容错原则）：
        拦截动作必须生效，记录能补则补。

        **事务生命周期分叉**（第 7 轮修复的关键缺陷）：
        - `result.blocked` → 调用方会 `raise ContentBlockedError`，
          请求会话随后被 `get_db` 回滚。此时必须走**独立会话立即 commit**，
          否则审核记录与审计行会跟着回滚一起消失（第十四条留痕直接失效）。
        - 非 blocked（REVIEW / degraded 放行）→ 随请求会话 flush，
          由外层事务统一提交即可。
        """
        ctx = get_audit_context()
        if actor is not None and tenant_id is None:
            tenant_id = getattr(actor, "tenant_id", None)

        # —— 上报状态判定（第十四条）——
        # ESCALATE 级必须上报；BLOCK 级同样属于「发现违法内容」，必须上报。
        needs_report = result.level in (
            ModerationLevel.BLOCK,
            ModerationLevel.ESCALATE,
        )
        report_status = ReportStatus.PENDING.value if needs_report else ReportStatus.NOT_REQUIRED.value

        reported = False
        if needs_report:
            subject = tenant_id or "unknown"
            reported = await report_to_authority(result, subject=subject, period="")
            if reported:
                report_status = ReportStatus.REPORTED.value
            else:
                # ⚠️ 上报失败**不等于**通道未配置 —— 两者的人工处置方式完全不同：
                #   PENDING = 压根没配通道 ⇒ 去配通道；
                #   FAILED  = 配了但这次没发成功 ⇒ 需要重发 / 排查接口。
                # 过去一律落 PENDING，运维按「通道未配置」处理 ⇒  statutory 上报被延误，
                # 且不报错（静默错分）。2026-09-20 由 test_moderation_report.py::R5 钉住。
                channel_configured = bool(
                    settings.MODERATION_REPORT_ENABLED and settings.MODERATION_REPORT_URL
                )
                report_status = (
                    ReportStatus.FAILED.value
                    if channel_configured
                    else ReportStatus.PENDING.value
                )

        record_row = ModerationRecord(
            tenant_id=tenant_id or "platform",
            actor_id=getattr(actor, "id", None),
            actor_role=_role_of(actor),
            side=result.side,
            scene=scene,
            resource_type=resource_type,
            resource_id=resource_id,
            content_hash=_sha32(text),
            content_len=len(text or ""),
            normalized_hash=result.normalized_digest or None,
            level=result.level.value,
            action=result.action.value,
            categories=sorted({h.category.value for h in result.hits}),
            detail={
                "hits": [h.as_dict() for h in result.hits],
                "degraded": result.degraded,
            },
            backend=_backend_name(result),
            degraded=result.degraded,
            user_notified=result.blocked,
            user_restricted=result.level == ModerationLevel.ESCALATE,
            report_status=report_status,
            report_note=(_report_note(reported, needs_report, report_status)),
            ip_address=ctx.get("ip_address"),
            user_agent=ctx.get("user_agent"),
            request_id=ctx.get("request_id"),
        )

        # —— 写审计 ——
        # 「拦截」与「上报」是**两件独立的事**（第十四条区分了动作与报告义务），
        # 因此可能产生两条审计行，而不是把二者塞进一个动作码里：
        #   1) 处置行：CONTENT_BLOCKED（已拦截）/ CONTENT_UNDER_REVIEW（放行待抽检）
        #   2) 上报行：CONTENT_REPORTED（已报）/ CONTENT_REPORT_PENDING（欠报，待人工补报）
        # 分开记的好处：运维能单独查出「欠监管几笔上报」，而不用从拦截记录里猜。
        base_detail = {
            "scene": scene,
            "side": result.side,
            "level": result.level.value,
            "moderation_action": result.action.value,
            "categories": sorted({h.category.value for h in result.hits}),
            "content_hash": _sha32(text),
            "content_len": len(text or ""),
            "report_status": report_status,
            "degraded": result.degraded,
        }
        disposition_action = (
            AuditAction.CONTENT_BLOCKED if result.blocked else AuditAction.CONTENT_UNDER_REVIEW
        )
        report_action = None
        if needs_report:
            report_action = (
                AuditAction.CONTENT_REPORTED if reported else AuditAction.CONTENT_REPORT_PENDING
            )

        blocked = result.blocked
        if blocked:
            # 指标：拦截率是「词库是否过严/过松」的核心观测项，也是备案巡检依据。
            # side 取 input/output，低基数可安全作为标签。
            metrics.moderation_blocks_total.inc((str(result.side),))
        if blocked or self.db is None:
            # 拦截路径：请求事务注定回滚，必须独立提交
            await record_with_detached_row(
                disposition_action,
                "moderation",
                row=record_row,
                resource_id=resource_id,
                actor=actor,
                tenant_id=tenant_id,
                detail=base_detail,
                extra_audits=(
                    [(report_action, {**base_detail, "audit_kind": "report"})]
                    if report_action
                    else None
                ),
            )
            return

        # 放行但需关注（REVIEW / degraded）：随请求事务走
        try:
            self.db.add(record_row)
            await self.db.flush()
        except Exception as exc:
            logger.warning(f"内容审核记录写入失败（已忽略，拦截动作仍生效）: {exc}")
        await record(
            self.db,
            disposition_action,
            "moderation",
            resource_id=resource_id,
            actor=actor,
            tenant_id=tenant_id,
            detail=base_detail,
        )
        if report_action:
            await record(
                self.db,
                report_action,
                "moderation",
                resource_id=resource_id,
                actor=actor,
                tenant_id=tenant_id,
                detail={**base_detail, "audit_kind": "report"},
            )

    # ---------------- 审计主体（审核通过/拒绝的用量计费对齐）----------------
    async def record_pass(
        self,
        text: str,
        *,
        scene: str,
        actor: Any = None,
        success: bool = True,
    ) -> None:
        """为正常通过的敏感场景留一条轻量审计。

        仅用于**必须留痕**的场景（如 AI 生成法律意见），
        避免为所有正常问答写审计导致表膨胀。
        """
        if not settings.MODERATION_ENABLED or self.db is None:
            return
        await record(
            self.db,
            AuditAction.AI_RUN_FINISH if success else AuditAction.AI_RUN_START,
            "moderation",
            actor=actor,
            detail={"scene": scene, "result": "pass", "content_hash": _sha32(text)},
        )


def _role_of(actor: Any) -> Optional[str]:
    role = getattr(actor, "role", None)
    if role is None:
        return None
    return getattr(role, "value", role)


def _backend_name(result: ModerationResult) -> str:
    if result.degraded:
        return "degraded"
    vias = {h.via for h in result.hits}
    if "external" in vias:
        return "external"
    return "builtin"


def content_fingerprint(text: str) -> str:
    """对外暴露的内容指纹（供调用方在日志里关联同一内容，不泄露原文）。"""
    return _full_sha(text or "")


__all__ = ["ModerationService", "content_fingerprint"]
