"""文书与合同审查接口（PRD 5.9）。

内容安全（P0-13）：合同审查过输入审核（原文可能是违规载体），
文书生成过输出审核（AI 产出可能因用户变量带出违规内容）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context
from app.core.errors import ErrorCode, NotFoundError
from app.core.metrics import metrics
from app.core.moderation import ContentBlockedError
from app.core.pagination import ok
from app.models.document import ContractReview, Document
from app.models.enums import (
    ContractReviewSource,
    ContractReviewStatus,
    UsageType,
)
from app.schemas.document import (
    ContractReviewAsyncRequest,
    ContractReviewRequest,
    DocumentOut,
    TemplateOut,
)
from app.services.billing_service import BillingService, price_cents
from app.services.contract_review import DISCLAIMER, ContractReviewService
from app.services.document_service import DocumentService
from app.services.job_handlers import trigger_contract_review
from app.services.moderation_service import ModerationService

router = APIRouter(prefix="/documents", tags=["文书与合同"])


async def _document_or_404(db: AsyncSession, doc_id: int, tenant_id: str) -> Document:
    """加载文书并校验租户归属（`collect` / `render` / 详情必须经此守卫）。

    历史缺陷（2026-09-21 由 `tests/test_document_endpoint_layer.py::W4/W5/W5b` 坐实）：
    `collect_variables` 与 `render_document` 直接 `DocumentService(db).collect/render(doc_id)`，
    而 `DocumentService._get()` 只按主键取、**不带租户过滤**，服务层也不接收 `tenant_id`。

    两个方向的后果，都比单纯「读越权」严重：

    - `collect` 跨租户 ⇒ 把变量**写进**他租户的文书（`doc.variables` 被改）；
    - `render` 跨租户 ⇒ 返回他租户文书**渲染后的完整正文**，
      而且 `BillingService.consume` 用的是 `ctx.tenant_id`
      ⇒ **读别人的东西，记自己租户的账**（实测流水 2 → 3）。

    与 `dispatches._dispatch_or_404`（§3.28.1）是同一个形状：
    「服务层不收 `tenant_id`」这个缺口会按模块复现。
    """
    d = await db.get(Document, doc_id)
    if d is None or d.tenant_id != tenant_id:
        raise NotFoundError("文书不存在", code=ErrorCode.DOCUMENT_NOT_FOUND)
    return d


class StartRequest(BaseModel):
    template_id: int
    case_id: Optional[int] = None


class CollectRequest(BaseModel):
    variables: dict


@router.get("/templates", response_model=dict, summary="模板库")
async def list_templates(
    lifecycle: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    rows = await DocumentService(db).list_templates(ctx.tenant_id, lifecycle=lifecycle, keyword=keyword)
    return ok([TemplateOut.model_validate(r).model_dump() for r in rows])


@router.post("/start", response_model=dict, summary="选择模板开始收集变量")
async def start_document(
    payload: StartRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    doc = await DocumentService(db).start(
        payload.template_id, tenant_id=ctx.tenant_id, user_id=ctx.user_id, case_id=payload.case_id
    )
    await db.commit()
    return ok(DocumentOut.model_validate(doc).model_dump())


@router.post("/{doc_id}/collect", response_model=dict, summary="多轮变量收集")
async def collect_variables(
    doc_id: int,
    payload: CollectRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    await _document_or_404(db, doc_id, ctx.tenant_id)
    doc = await DocumentService(db).collect(doc_id, payload.variables)
    await db.commit()
    return ok(DocumentOut.model_validate(doc).model_dump())


@router.post("/{doc_id}/render", response_model=dict, summary="生成文书（含风险提示）")
async def render_document(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _document_or_404(db, doc_id, ctx.tenant_id)
    doc = await DocumentService(db).render(doc_id)

    # —— 输出审核（P0-13 第十四条「停止传输」）——
    # 文书正文是 AI 产出，可能因用户填入的变量而带出违法内容，
    # 因此生成后必须过审，命中则不把正文返回给用户。
    if settings.MODERATION_CHECK_OUTPUT:
        body_text = _document_text(doc)
        if body_text:
            verdict = await ModerationService(db).check(
                body_text,
                side="output",
                scene="document",
                actor=user,
                resource_type="document",
                resource_id=doc.id,
            )
            if verdict.blocked:
                raise ContentBlockedError(verdict)

    usage = await BillingService(db).consume(
        tenant_id=ctx.tenant_id, user_id=ctx.user_id, usage_type=UsageType.DOCUMENT,
        period=_period(), ref_type="document", ref_id=doc.id,
    )
    # 文书生成属 AI 产出，责任可追溯（PRD 5.9 全程留痕）
    await record(
        db,
        AuditAction.DOCUMENT_RENDER,
        "document",
        doc.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"template_id": getattr(doc, "template_id", None), "case_id": getattr(doc, "case_id", None)},
    )
    await db.commit()
    return ok({**DocumentOut.model_validate(doc).model_dump(), "usage": usage})


@router.get("/{doc_id}", response_model=dict, summary="文书详情")
async def get_document(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    # 收敛到与 `collect` / `render` **同一个**守卫：此前这里是内联的重复判据，
    # 与另两个端点各写一份、且那两份还是缺失的（`conversation_access.py`
    # 收敛前的同款问题）。判据分散必然漂移。
    d = await _document_or_404(db, doc_id, ctx.tenant_id)
    return ok(DocumentOut.model_validate(d).model_dump())


@router.post("/contract-review", response_model=dict, summary="合同审查（风险标注 + 修改建议）")
async def review_contract(
    payload: ContractReviewRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    # —— 输入审核（P0-13 第十四条「停止生成」）——
    # 与异步端点**共用** `_moderate_contract_input`：判据分散必然漂移，
    # 两处各写一份迟早不一致（同步挡住了、异步没挡 = 白挡）。
    await _moderate_contract_input(db, payload.source_text, user)

    cr = await ContractReviewService(db).review(
        title=payload.title or "合同审查",
        source_text=payload.source_text,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        evidence_id=payload.evidence_id,
    )

    # —— 计费门控（P0-16 核心）——
    # 只有「真实模型逐条通读且成功」才收费。降级（规则兜底 / Mock）与失败
    # **完全不调用** consume_atomic——不是「调用后回滚」，因为回滚依赖后续代码
    # 不出错，而这里要的是一个**不依赖任何后续步骤**的硬保证。
    #
    # 改造前这里是「先 review 再无条件 consume_atomic」，而 review 是纯本地
    # 关键词匹配、永不失败 ⇒ 99 元 100% 收在规则产出上。
    data: dict = {
        "id": cr.id,
        "title": cr.title,
        "source": cr.source.value,
        "status": cr.status.value,
        "analysis_status": cr.analysis_status.value,
        "overall_risk": cr.overall_risk.value,
        "coverage": cr.coverage or {"total_clauses": 0, "reviewed_clauses": 0, "reviewed_ratio": 0.0},
        "disclaimer": cr.disclaimer or DISCLAIMER,
        "findings": cr.findings or [],
        "summary": cr.summary,
    }
    # 失败原因：**仅在 status=failed 时出现**（与 `usage` 同一约定——键不存在
    # 表示「不适用」）。文案由服务层给出，是人话且不含环境变量/内部地址。
    if cr.status == ContractReviewStatus.FAILED:
        data["error_message"] = cr.error_message or "本次审查失败，请稍后重试。"

    billable = (
        cr.source == ContractReviewSource.LLM
        and cr.status == ContractReviewStatus.SUCCESS
    )
    if billable:
        usage = await BillingService(db).consume_atomic(
            tenant_id=ctx.tenant_id, user_id=ctx.user_id, usage_type=UsageType.CONTRACT_REVIEW,
            period=_period(), ref_type="contract_review", ref_id=cr.id,
        )
        # 计费诚实性护栏：source!=llm 时该指标**恒为 0**
        metrics.contract_review_charged_total.inc((cr.source.value,))
        data["usage"] = {**usage, "charged": price_cents(UsageType.CONTRACT_REVIEW)}

    # 只记风险等级与元信息，**不落合同原文**（合同通常含商业秘密）
    await record(
        db,
        AuditAction.CONTRACT_REVIEW,
        "contract_review",
        cr.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "title": cr.title,
            "overall_risk": cr.overall_risk.value,
            "finding_count": len(cr.findings or []),
            "evidence_id": payload.evidence_id,
            # 审计必须能回答「这 99 元收在什么产出上」——缺这两项时
            # 事后完全无法区分「模型审查」与「规则预筛」
            "source": cr.source.value,
            "status": cr.status.value,
            "charged": billable,
        },
    )
    await db.commit()
    return ok(data)


@router.get(
    "/contract-review/{review_id}",
    response_model=dict,
    summary="合同审查结果（异步任务完成后按 id 取回）",
)
async def get_contract_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """按 id 取回一次合同审查的结论。

    ## 为什么必须有这条接口

    异步通道（`POST /contract-review/async`）只回 `job_id`，审查实体是 worker
    在 `ContractReviewService.review()` **内部**创建的。没有这条读回接口，
    「转异步」就还是一条**死路**：任务明明跑完了，用户却拿不到结论——
    413 的 `guidance=upload_or_async` 依然指向不存在的能力。

    ⚠️ 归属校验用 **404 而不是 403**：403 会确认资源存在，可被用来枚举他人的
    审查记录（与 P0 判据「客户乙读客户甲 ⇒ 404 非 403，防枚举」同一纪律）。
    """
    cr = await db.get(ContractReview, review_id)
    if cr is None or cr.tenant_id != ctx.tenant_id:
        raise NotFoundError("审查记录不存在", code=ErrorCode.CONTRACT_REVIEW_NOT_FOUND)

    data: dict = {
        "id": cr.id,
        "title": cr.title,
        "source": cr.source.value,
        "status": cr.status.value,
        "analysis_status": cr.analysis_status.value,
        "overall_risk": cr.overall_risk.value,
        "coverage": cr.coverage
        or {"total_clauses": 0, "reviewed_clauses": 0, "reviewed_ratio": 0.0},
        "disclaimer": cr.disclaimer or DISCLAIMER,
        "findings": cr.findings or [],
        "summary": cr.summary,
    }
    # 失败原因**仅在 status=failed 时出现**（键不存在 = 不适用），与同步端点同口径。
    if cr.status == ContractReviewStatus.FAILED and cr.error_message:
        data["error_message"] = cr.error_message
    return ok(data)


async def _moderate_contract_input(db: AsyncSession, text: str, user) -> None:
    """合同原文输入审核 —— **同步与异步两条路共用**。

    合同原文可能被用作夹带违法内容的载体（如伪装成条款的煽动性文本），
    命中即拒绝调用模型，避免违规内容进入上下文与审查结果。

    ⚠️ 为什么不各写一份：判据分散必然漂移（本项目已多次踩到）。
    异步端点若漏了这道审核，等于给「绕过内容安全」开一条后门——
    只要把文本加长到 >20k 走异步即可绕过同步端的拦截。
    """
    if settings.MODERATION_CHECK_INPUT and text:
        verdict = await ModerationService(db).check(
            text,
            side="input",
            scene="contract_review",
            actor=user,
            resource_type="contract_review",
        )
        if verdict.blocked:
            raise ContentBlockedError(verdict)


@router.post(
    "/contract-review/async",
    response_model=dict,
    status_code=202,
    summary="异步合同审查（长合同专用，>20k 走这条）",
)
async def review_contract_async(
    payload: ContractReviewAsyncRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    """提交长合同审查，返回 `job_id`；前端轮询 `GET /jobs/{id}` 看进度与结果。

    ## 与同步端点的分工

    `source_text` 是**唯一无截断的成本放大口**（`_build_prompt` 全量入 prompt）：
    - ≤20k：走同步端点，直接返回结果；
    - >20k：同步端点返 **413**（`guidance=upload_or_async`）—— 本端点就是
      那条「异步」的路。上限 **500k**，超限同样 **413**、**绝不静默截断**
      （截断 = 审查结论基于残缺合同，是最不能接受的失败模式）。

    ⚠️ 在 2026-09-23 之前，413 指引的这条异步通道**并不存在**
    （`JobType` 里没有 `contract_review`），等于指引用户走一条死路。
    """
    await _moderate_contract_input(db, payload.source_text, user)

    job_id = await trigger_contract_review(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        title=payload.title,
        source_text=payload.source_text,
        evidence_id=payload.evidence_id,
    )
    await record(
        db,
        AuditAction.CONTRACT_REVIEW,
        "job",
        job_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "title": payload.title,
            "async": True,
            "length": len(payload.source_text),
            "evidence_id": payload.evidence_id,
        },
    )
    await db.commit()
    return ok({"job_id": job_id, "status": "pending"})


def _document_text(doc) -> str:
    """从文书实体中取出全部可审核文本（正文 + 风险提示）。

    只挑"用户可见"的字段：审核的是**将要展示的内容**，
    而不是内部的元数据/ID，避免误判。
    """
    parts: list[str] = []
    for attr in ("title", "content", "rendered_content", "body", "risk_notice", "summary"):
        val = getattr(doc, attr, None)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    return "\n".join(parts)


def _period() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m")
