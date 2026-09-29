"""证据接口：上传 / 列表 / 解析 / 缺失清单 / 分轮追问 / 时间线。"""
from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context, require_permissions
from app.core.errors import ErrorCode, NotFoundError
from app.core.pagination import ok
from app.core.rbac import Role
from app.models.case import Case
from app.models.evidence import Evidence
from app.schemas.analysis import EvidenceOut
from app.services.evidence_service import EvidenceService
from app.services.job_handlers import trigger_evidence_parse
from app.services.storage_service import save_upload

#: Q-R（2026-09-21 裁定）：证据端点统一按权限码门控。
# 基础层 `evidence:read` 覆盖所有端点（读自己案件的材料至少需要 read）；
# 写端点（上传 / 重解析）再叠加 `evidence:create` / `evidence:update`。
# 归属隔离仍由 `_case_or_404` / `_evidence_or_404` 兜底（客户只能碰自己案件的材料）。
router = APIRouter(
    prefix="/evidence",
    tags=["证据材料"],
    dependencies=[Depends(require_permissions("evidence:read"))],
)


async def _case_or_404(db: AsyncSession, case_id: int, ctx) -> Case:
    """加载案件并执行**租户 + 客户归属**双重校验。

    与 `app/api/v1/cases.py::_case_or_404` 保持同一口径。

    历史缺陷（本轮修复）：本函数此前只接收 `tenant_id`，仅校验租户，
    因此**同租户内任意已登录用户遍历 `case_id` 即可读取他人案件的材料清单、
    证据文件名与证据时间线**——证据文件名本身即高度敏感信息（"离婚协议书"、
    "劳动仲裁申请书"、"欠条" 等足以暴露纠纷性质与当事人处境），
    对法律产品属致命数据泄露（违反《律师法》保密义务与 PIPL）。

    该缺陷之所以长期未被发现，是因为 `cases.py` 在修复同类问题时只改了
    自己文件里的守卫——**同一份守卫被复制到多个文件后，修一处不等于修全部**。
    凡新增按 `case_id` 读取的端点，必须经本函数（或 `cases.py` 的同名函数）。
    """
    case = await db.get(Case, case_id)
    if case is None or case.tenant_id != ctx.tenant_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    # 客户只能访问自己的案件；律师/管理员按租户隔离即可
    if ctx.role == Role.CLIENT and case.client_user_id != ctx.user_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    return case


async def _evidence_or_404(db: AsyncSession, ev: Evidence, ctx) -> Evidence:
    """在租户之上再补一层**客户归属**校验，与 `_case_or_404` 同一口径。

    历史缺陷（本轮修复）：`POST /evidence/{id}/parse` 此前只判
    `ev.tenant_id == ctx.tenant_id`，于是同租户内客户乙遍历 `evidence_id`
    即可重解析客户甲的材料，并读到 `EvidenceOut` 全字段——含 `ocr_text`
    与 `legality_risks`；还能**覆盖**对方的解析结果。

    读端点早已被 `_case_or_404` 堵住（见 `test_evidence_authz.py`），
    但因为本端点按 `evidence_id` 而非 `case_id` 取数，当时没被覆盖到——
    **守卫按参数名分类，缺口也会按参数名分布**。

    `case_id` 为空时退到 `uploaded_by`：当前只有上传端点会建证据（必带
    `case_id`），但保留这条兜底，避免将来 IM 侧接入无案件材料时被误拒。
    """
    if getattr(ctx, "role", None) != Role.CLIENT:
        return ev
    if ev.case_id is not None:
        case = await db.get(Case, ev.case_id)
        if case is None or case.client_user_id != ctx.user_id:
            raise NotFoundError("证据不存在", code=ErrorCode.EVIDENCE_NOT_FOUND)
        return ev
    if ev.uploaded_by != ctx.user_id:
        raise NotFoundError("证据不存在", code=ErrorCode.EVIDENCE_NOT_FOUND)
    return ev


@router.post("/cases/{case_id}", response_model=dict, summary="上传证据（自动触发解析）",
             dependencies=[Depends(require_permissions("evidence:create"))])
async def upload_evidence(
    case_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _case_or_404(db, case_id, ctx)
    rel_path, size, ctype = await save_upload(file, ctx.tenant_id, subdir="evidence")

    ev = Evidence(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        uploaded_by=ctx.user_id,
        name=file.filename or "未命名材料",
        file_path=rel_path,
        file_type=ctype,
        file_size=size,
    )
    db.add(ev)
    await db.flush()
    job_id = await trigger_evidence_parse(db, ev.id, ctx.tenant_id, background)
    # 证据材料属敏感卷宗：记录谁在何时上传了什么（文件名 + 大小 + 类型 + 摘要）
    await record(
        db,
        AuditAction.EVIDENCE_UPLOAD,
        "evidence",
        ev.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "case_id": case_id,
            "name": ev.name,
            "file_type": ctype,
            "file_size": size,
            "job_id": job_id,
        },
    )
    return ok({**EvidenceOut.model_validate(ev).model_dump(), "job_id": job_id})


@router.get("/cases/{case_id}", response_model=dict, summary="案件证据列表")
async def list_evidence(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    await _case_or_404(db, case_id, ctx)
    rows = list(
        (
            await db.execute(
                select(Evidence).where(Evidence.case_id == case_id).order_by(Evidence.id.desc())
            )
        ).scalars().all()
    )
    return ok([EvidenceOut.model_validate(r).model_dump() for r in rows])


@router.get("/cases/{case_id}/missing", response_model=dict, summary="材料缺失清单")
async def missing_list(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    await _case_or_404(db, case_id, ctx)
    return ok(await EvidenceService(db).missing_list(case_id))


@router.get("/cases/{case_id}/rounds", response_model=dict, summary="分轮智能追问（先关键后补充）")
async def ask_rounds(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    await _case_or_404(db, case_id, ctx)
    return ok(await EvidenceService(db).ask_rounds(case_id))


@router.get("/cases/{case_id}/timeline", response_model=dict, summary="事件时间线")
async def timeline(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    await _case_or_404(db, case_id, ctx)
    return ok(await EvidenceService(db).timeline(case_id))


@router.post("/{evidence_id}/parse", response_model=dict, summary="重新解析单份材料",
             dependencies=[Depends(require_permissions("evidence:update"))])
async def reparse(
    evidence_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    ev = await db.get(Evidence, evidence_id)
    if ev is None or ev.tenant_id != ctx.tenant_id:
        raise NotFoundError("证据不存在", code=ErrorCode.EVIDENCE_NOT_FOUND)
    await _evidence_or_404(db, ev, ctx)
    # 传 tenant_id 是服务层自带的纵深防御：端点里的手工判等万一写错/被删，
    # 服务层还能兜住一层（见 EvidenceService.parse 的 docstring）
    ev = await EvidenceService(db).parse(evidence_id, tenant_id=ctx.tenant_id)
    # 重新解析会覆盖既有解析结果，属可追溯性要求范围
    await record(
        db,
        AuditAction.EVIDENCE_PARSE,
        "evidence",
        evidence_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"case_id": ev.case_id, "retry": True},
    )
    await db.commit()
    return ok(EvidenceOut.model_validate(ev).model_dump())
