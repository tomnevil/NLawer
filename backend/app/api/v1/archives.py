"""归档与开庭材料包接口（PRD 5.6）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context
from app.core.errors import ErrorCode, NotFoundError
from app.core.pagination import ok
from app.models.archive import Archive, HearingPack
from app.schemas.review import ArchiveOut, HearingPackOut
from app.services.archive_service import ArchiveService

router = APIRouter(prefix="/archives", tags=["归档与开庭"])


@router.post("/cases/{case_id}", response_model=dict, summary="归档案件（须已定稿）")
async def archive_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    a = await ArchiveService(db).archive_case(case_id, actor_id=ctx.user_id, tenant_id=ctx.tenant_id)
    # 归档是不可逆的交付动作（PRD：归档后可审计、责任可追溯），必须留痕
    await record(
        db,
        AuditAction.ARCHIVE_CREATE,
        "archive",
        a.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"case_id": case_id, "version": getattr(a, "version", None)},
    )
    await db.commit()
    return ok(ArchiveOut.model_validate(a).model_dump())


@router.get("/cases/{case_id}", response_model=dict, summary="查询卷宗")
async def get_archive(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    a = (await db.execute(select(Archive).where(Archive.case_id == case_id))).scalars().first()
    if a is None or a.tenant_id != ctx.tenant_id:
        raise NotFoundError("卷宗不存在", code=ErrorCode.ARCHIVE_NOT_FOUND)
    return ok(ArchiveOut.model_validate(a).model_dump())


@router.get("/{archive_id}/versions", response_model=dict, summary="卷宗版本历史（回溯对比）")
async def archive_versions(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    a = await db.get(Archive, archive_id)
    if a is None or a.tenant_id != ctx.tenant_id:
        raise NotFoundError("卷宗不存在", code=ErrorCode.ARCHIVE_NOT_FOUND)
    rows = await ArchiveService(db).versions(archive_id)
    return ok(
        [
            {
                "version": v.version,
                "change_note": v.change_note,
                "created_by_user_id": v.created_by_user_id,
                "snapshot": v.snapshot,
            }
            for v in rows
        ]
    )


@router.post("/cases/{case_id}/hearing-pack", response_model=dict, summary="导出开庭材料包")
async def export_hearing_pack(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    pack = await ArchiveService(db).export_hearing_pack(case_id, actor_id=ctx.user_id, tenant_id=ctx.tenant_id)
    # 材料包导出 = 卷宗外流，属高敏感操作，必须留痕（谁在何时导出了哪个案件）
    await record(
        db,
        AuditAction.HEARING_PACK_EXPORT,
        "hearing_pack",
        pack.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"case_id": case_id},
    )
    await db.commit()
    return ok(HearingPackOut.model_validate(pack).model_dump())


@router.get("/hearing-packs/{pack_id}", response_model=dict, summary="查询开庭材料包")
async def get_hearing_pack(
    pack_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    p = await db.get(HearingPack, pack_id)
    if p is None or p.tenant_id != ctx.tenant_id:
        raise NotFoundError("材料包不存在", code=ErrorCode.ARCHIVE_NOT_FOUND)
    return ok(HearingPackOut.model_validate(p).model_dump())
