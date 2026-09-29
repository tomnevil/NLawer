"""企业专属知识库接口（租户隔离，PRD 5.7）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context, require_permissions
from app.core.pagination import Page, PaginationParams, ok
from app.core.rbac import Role, require_roles
from app.schemas.knowledge import KnowledgeDocCreate, KnowledgeDocOut
from app.services.knowledge_service import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["企业知识库"])


@router.post("/docs", response_model=dict, summary="新增知识文档", dependencies=[Depends(require_roles(Role.ENTERPRISE_ADMIN, Role.ENTERPRISE_USER, Role.FIRM_ADMIN))])
async def create_doc(
    payload: KnowledgeDocCreate,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    doc = await KnowledgeService(db).create(
        tenant_id=ctx.tenant_id,
        title=payload.title,
        doc_type=payload.doc_type,
        content=payload.content,
        source_ref=payload.source_ref,
        tags=payload.tags,
        uploaded_by=ctx.user_id,
    )
    # 企业知识库是租户私有资产，写入行为必须留痕（合规 + 资产溯源）
    await record(
        db,
        AuditAction.KNOWLEDGE_CREATE,
        "knowledge_doc",
        doc.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "title": payload.title,
            "doc_type": payload.doc_type,
            "chunk_count": getattr(doc, "chunk_count", None),
        },
    )
    await db.commit()
    return ok(KnowledgeDocOut.model_validate(doc).model_dump())


@router.get("/docs", response_model=dict, summary="知识文档列表（租户过滤）")
async def list_docs(
    params: PaginationParams = Depends(),
    doc_type: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    rows, total, is_lower_bound = await KnowledgeService(db).list_docs(
        ctx.tenant_id, params, doc_type=doc_type, keyword=keyword
    )
    return ok(
        Page.build(
            [KnowledgeDocOut.model_validate(r).model_dump() for r in rows],
            total,
            params,
            total_is_lower_bound=is_lower_bound,
        ).model_dump()
    )


@router.get("/docs/{doc_id}", response_model=dict, summary="知识文档详情（归属二次校验）")
async def get_doc(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    doc = await KnowledgeService(db).get(doc_id, ctx.tenant_id)
    # 读取私有知识资产同样留痕：知识库内容可能构成商业秘密，
    # 需能回答「谁在什么时候读过哪份资料」。
    await record(
        db,
        AuditAction.KNOWLEDGE_READ,
        "knowledge_doc",
        doc.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"title": doc.title},
    )
    await db.commit()
    return ok(KnowledgeDocOut.model_validate(doc).model_dump())


@router.delete(
    "/docs/{doc_id}",
    response_model=dict,
    summary="删除知识文档",
    # 2026-09-22 收口（端点层授权门禁 TENANT 档）：此前只按 `ctx.tenant_id` 取数，
    # **任何已登录用户（含 CLIENT）都能删掉所里的知识库文档**。
    # 知识库内容可能构成商业秘密，删除必须限 `knowledge:write`
    # （= FIRM_ADMIN / ENTERPRISE_ADMIN / PLATFORM_ADMIN）。
    dependencies=[Depends(require_permissions("knowledge:write"))],
)
async def delete_doc(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    # 先取快照再删除——删除后对象已不在库里，detail 里就没法记录标题了
    doc = await KnowledgeService(db).get(doc_id, ctx.tenant_id)
    snapshot = {"title": doc.title, "doc_type": doc.doc_type}
    await KnowledgeService(db).delete(doc_id, ctx.tenant_id)
    await record(
        db,
        AuditAction.KNOWLEDGE_DELETE,
        "knowledge_doc",
        doc_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail=snapshot,
    )
    await db.commit()
    return ok({"deleted": doc_id})
