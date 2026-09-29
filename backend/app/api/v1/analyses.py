"""案件分析接口：六段式生成 / 查询 / 律师编辑 / AI 迭代 / 决策审计。"""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context
from app.core.errors import ErrorCode, NotFoundError
from app.core.pagination import ok
from app.core.rbac import Role
from app.models.ai_run import AiDecision
from app.models.analysis import CaseAnalysis, CaseAnalysisVersion
from app.models.case import Case
from app.schemas.analysis import (
    AnalysisUpdate,
    CaseAnalysisOut,
    IterateRequest,
)
from app.services.case_copilot import CaseCopilot
from app.services.job_handlers import trigger_case_analysis

router = APIRouter(prefix="/analyses", tags=["AI 办案"])


async def _load_or_404(db: AsyncSession, analysis_id: int, tenant_id: str) -> CaseAnalysis:
    a = await db.get(CaseAnalysis, analysis_id)
    if a is None or a.tenant_id != tenant_id:
        raise NotFoundError("分析不存在", code=ErrorCode.ANALYSIS_NOT_FOUND)
    return a


async def _case_or_404(db: AsyncSession, case_id: int, ctx) -> Case:
    """与 `cases.py:_case_or_404` **同口径**：租户 + 客户归属双重校验。

    2026-09-22 收口（端点层授权门禁 TENANT 档）：`POST /case/{id}/generate`
    此前只把 `ctx.tenant_id` 传下去，**同租户的任意客户都能对他人案件触发 AI 生成**
    （跨租户那道守卫在 `job_handlers`，一直都在）。
    """
    case = await db.get(Case, case_id)
    if case is None or case.tenant_id != ctx.tenant_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    # 客户只能对自己的案件触发；律师/管理员按租户隔离即可
    if ctx.role == Role.CLIENT and case.client_user_id != ctx.user_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    return case


@router.get("/case/{case_id}", response_model=dict, summary="查询案件最新分析")
async def get_case_analysis(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    a = (
        (
            await db.execute(
                select(CaseAnalysis)
                .where(CaseAnalysis.case_id == case_id, CaseAnalysis.tenant_id == ctx.tenant_id)
                .order_by(CaseAnalysis.id.desc())
            )
        ).scalars().first()
    )
    if a is None:
        raise NotFoundError("尚未生成分析", code=ErrorCode.ANALYSIS_NOT_FOUND)
    return ok(CaseAnalysisOut.model_validate(a).model_dump())


@router.post("/case/{case_id}/generate", response_model=dict, summary="触发生成（异步）")
async def generate_analysis(
    case_id: int,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _case_or_404(db, case_id, ctx)  # 租户 + 客户归属（跨租户那道在 job_handlers）
    job_id = await trigger_case_analysis(db, case_id, ctx.tenant_id, background)
    # AI 生成会消耗算力并产出法律分析初稿，需记录触发者与案件
    await record(
        db,
        AuditAction.ANALYSIS_GENERATE,
        "case_analysis",
        None,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"case_id": case_id, "job_id": job_id},
    )
    await db.commit()
    return ok({"job_id": job_id, "case_id": case_id})


@router.put("/{analysis_id}", response_model=dict, summary="律师编辑（生成版本快照）")
async def update_analysis(
    analysis_id: int,
    payload: AnalysisUpdate,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    a = await _load_or_404(db, analysis_id, ctx.tenant_id)
    # 记录哪些字段被律师改过（字段名，不落正文，避免卷宗内容进审计表）
    changed = [f for f in ("summary", "legal_analysis", "related_laws", "similar_cases", "suggestions", "missing_info")
               if getattr(payload, f) is not None]
    for field in changed:
        setattr(a, field, getattr(payload, field))
    a.version = (a.version or 1) + 1
    db.add(
        CaseAnalysisVersion(
            tenant_id=a.tenant_id,
            analysis_id=a.id,
            version=a.version,
            snapshot={
                "summary": a.summary,
                "legal_analysis": a.legal_analysis,
                "related_laws": a.related_laws,
                "similar_cases": a.similar_cases,
                "suggestions": a.suggestions,
                "missing_info": a.missing_info,
            },
            change_note=payload.change_note or "律师编辑",
            changed_by=ctx.user_id,
        )
    )
    # PRD：AI 输出 → 律师修改 → 确认的完整过程可追溯，编辑必须留痕
    await record(
        db,
        AuditAction.ANALYSIS_EDIT,
        "case_analysis",
        analysis_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "case_id": a.case_id,
            "version": a.version,
            "changed_fields": changed,
            "change_note": payload.change_note,
        },
    )
    await db.commit()
    return ok(CaseAnalysisOut.model_validate(a).model_dump())


@router.post("/{analysis_id}/iterate", response_model=dict, summary="律师批注驱动 AI 迭代")
async def iterate_analysis(
    analysis_id: int,
    payload: IterateRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    a = await _load_or_404(db, analysis_id, ctx.tenant_id)
    updated = await CaseCopilot(db).generate(a.case_id, trigger="LAWYER_ITERATE", note=payload.note)
    # 迭代会覆盖分析版本，属「谁驱动了这次重生成」的关键证据
    await record(
        db,
        AuditAction.ANALYSIS_ITERATE,
        "case_analysis",
        updated.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"case_id": a.case_id, "note": payload.note, "version": updated.version},
    )
    await db.commit()
    return ok(CaseAnalysisOut.model_validate(updated).model_dump())


@router.get("/{analysis_id}/decisions", response_model=dict, summary="AI 决策审计")
async def analysis_decisions(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    a = await _load_or_404(db, analysis_id, ctx.tenant_id)
    rows = list(
        (await db.execute(select(AiDecision).where(AiDecision.run_id == a.run_id))).scalars().all()
    )
    return ok(
        [
            {"stage": d.stage, "decision": d.decision, "reason": d.reason, "duration_ms": d.duration_ms}
            for d in rows
        ]
    )


@router.get("/{analysis_id}/versions", response_model=dict, summary="版本历史（支持对比）")
async def analysis_versions(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    await _load_or_404(db, analysis_id, ctx.tenant_id)
    rows = list(
        (
            await db.execute(
                select(CaseAnalysisVersion)
                .where(CaseAnalysisVersion.analysis_id == analysis_id)
                .order_by(CaseAnalysisVersion.version.asc())
            )
        ).scalars().all()
    )
    return ok(
        [
            {"version": v.version, "change_note": v.change_note, "changed_by": v.changed_by, "snapshot": v.snapshot}
            for v in rows
        ]
    )
