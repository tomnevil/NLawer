"""合规扫描接口：四维扫描（劳动用工/商业合同/数据隐私/广告营销）。

内容安全（P0-13）：`input_summary` 与 `scope` 会作为提示词上下文喂给模型，
属用户可控的自由文本，因此必须过输入审核（第十四条「停止生成」）。
"""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context, require_permissions
from app.core.errors import ErrorCode, NotFoundError
from app.core.moderation import ContentBlockedError
from app.core.pagination import ok
from app.models.knowledge import ComplianceScan
from app.schemas.compliance import ComplianceFindingOut, ComplianceScanCreate, ComplianceScanOut
from app.services.compliance_service import ComplianceService
from app.services.job_handlers import trigger_compliance_scan
from app.services.moderation_service import ModerationService

router = APIRouter(prefix="/compliance", tags=["合规扫描"])


@router.post(
    "/scans",
    response_model=dict,
    summary="创建并触发四维合规扫描",
    # 2026-09-22 收口（端点层授权门禁 TENANT 档）：此前**任何已登录用户（含 CLIENT）
    # 都能发起合规扫描** —— 扫描消耗算力，且结论可能作为对外交付依据（本文件头已写明）。
    # 用既有权限码 `compliance:scan`（当前授予 ENTERPRISE_ADMIN）。
    # ⚠️ 待拍板：律所侧的 FIRM_ADMIN / LAWYER 是否也应能发起？矩阵里他们没有这个码，
    #    需要的话在 `rbac.py` 各加一行即可，判据不会红。
    dependencies=[Depends(require_permissions("compliance:scan"))],
)
async def create_scan(
    payload: ComplianceScanCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    # —— 输入审核（P0-13）——
    if settings.MODERATION_CHECK_INPUT:
        _text = "\n".join(x for x in (payload.title, payload.scope, payload.input_summary) if x)
        if _text:
            verdict = await ModerationService(db).check(
                _text,
                side="input",
                scene="compliance",
                actor=user,
                resource_type="compliance_scan",
            )
            if verdict.blocked:
                raise ContentBlockedError(verdict)

    scan = await ComplianceService(db).create_scan(
        title=payload.title,
        tenant_id=ctx.tenant_id,
        dimensions=payload.dimensions,
        scope=payload.scope,
        input_summary=payload.input_summary,
        is_external=payload.is_external,
    )
    job_id = await trigger_compliance_scan(db, scan.id, ctx.tenant_id, background)
    # 合规扫描结论可能作为对外交付依据，必须能追溯「谁发起的、扫什么范围」
    await record(
        db,
        AuditAction.COMPLIANCE_SCAN,
        "compliance_scan",
        scan.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "title": payload.title,
            "dimensions": payload.dimensions,
            "is_external": payload.is_external,
            "job_id": job_id,
        },
    )
    await db.commit()
    return ok({**ComplianceScanOut.model_validate(scan).model_dump(), "job_id": job_id})


@router.get("/scans", response_model=dict, summary="扫描任务列表")
async def list_scans(
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    rows = list(
        (await db.execute(select(ComplianceScan).where(ComplianceScan.tenant_id == ctx.tenant_id)
                          .order_by(ComplianceScan.id.desc()))).scalars().all()
    )
    return ok([ComplianceScanOut.model_validate(r).model_dump() for r in rows])


@router.get("/scans/{scan_id}", response_model=dict, summary="扫描详情 + 发现项")
async def get_scan(
    scan_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    scan = await db.get(ComplianceScan, scan_id)
    if scan is None or scan.tenant_id != ctx.tenant_id:
        raise NotFoundError("扫描任务不存在", code=ErrorCode.SCAN_NOT_FOUND)
    findings = await ComplianceService(db).findings(scan_id)
    return ok(
        {
            **ComplianceScanOut.model_validate(scan).model_dump(),
            "findings": [ComplianceFindingOut.model_validate(f).model_dump() for f in findings],
        }
    )
