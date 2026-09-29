"""请求体自由文本长度上限：**运行时判据**。

与静态探针 `evidence/verify_request_text_limits.py` 是**互补的两半**，不能互相替代：

- 静态探针证明「源码里**写了** `max_length`」—— 改注释/改字符串就能骗过；
- 本文件证明「**真的会拒**」—— 上限值被改大、或 `Field` 被整个摘掉时，这里会红。

🚨 为什么必须有运行时这一半：静态扫描读的是**源码文本**，
`max_length=2000` 与 `max_length=200000` 在静态眼里长得一样。
而缺陷的真实形态（成本放大 / 提示词爆炸）恰恰只对**值**敏感。

取值来源：`deliverables/product-strategy/roadmap-update-pending-rulings-2026-09-22.md` §4.1
（析客裁定表）+ 失真 ⑤ 修好后新增暴露的 4 条。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.complaints import ComplaintCreate, ComplaintHandle
from app.api.v1.qa import QARequest
from app.schemas.analysis import AnalysisUpdate, IterateRequest
from app.schemas.billing import QuotaAdjust, UsageType
from app.schemas.compliance import ComplianceScanCreate
from app.schemas.conversation import SendMessageRequest
from app.schemas.document import ContractReviewRequest
from app.schemas.knowledge import KnowledgeDocCreate
from app.schemas.review import ReviewAction

# (构造超长入参的 lambda, 字段标签)
OVERSIZED = [
    (lambda: AnalysisUpdate(summary="x" * 2001), "AnalysisUpdate.summary"),
    (lambda: AnalysisUpdate(legal_analysis="x" * 20001), "AnalysisUpdate.legal_analysis"),
    (lambda: AnalysisUpdate(change_note="x" * 501), "AnalysisUpdate.change_note"),
    (lambda: IterateRequest(note="x" * 2001), "IterateRequest.note"),
    (lambda: ComplianceScanCreate(title="x" * 101), "ComplianceScanCreate.title"),
    # ⚠️ `title` 是必填 —— 不带上它，下面的「超长被拒」会变成**空转的臂**：
    # 缺字段本身就抛 ValidationError ⇒ 就算把 `max_length` 摘掉也照样红（假绿）。
    # 反向向量 `test_just_at_limit_is_accepted` 当场抓到了这两条。
    (
        lambda: ComplianceScanCreate(title="t", scope="x" * 501),
        "ComplianceScanCreate.scope",
    ),
    (
        lambda: ComplianceScanCreate(title="t", input_summary="x" * 2001),
        "ComplianceScanCreate.input_summary",
    ),
    (
        lambda: ContractReviewRequest(title="t", source_text="x" * 20001),
        "ContractReviewRequest.source_text",
    ),
    (
        lambda: ContractReviewRequest(title="x" * 101, source_text="s"),
        "ContractReviewRequest.title",
    ),
    (
        lambda: KnowledgeDocCreate(title="x" * 101, doc_type="d", content="c"),
        "KnowledgeDocCreate.title",
    ),
    (
        lambda: KnowledgeDocCreate(title="t", doc_type="d", content="x" * 50001),
        "KnowledgeDocCreate.content",
    ),
    (lambda: SendMessageRequest(text="x" * 4001), "SendMessageRequest.text"),
    (lambda: QARequest(question="x" * 4001), "QARequest.question"),
    (
        lambda: ComplaintCreate(type="OTHER", description="x" * 5001),
        "ComplaintCreate.description",
    ),
    (
        lambda: ComplaintCreate(type="OTHER", description="d", contact="x" * 101),
        "ComplaintCreate.contact",
    ),
    (
        lambda: ComplaintHandle(status="RESOLVED", handle_note="x" * 2001),
        "ComplaintHandle.handle_note",
    ),
    (lambda: ReviewAction(decision="APPROVED", comment="x" * 1001), "ReviewAction.comment"),
    (
        lambda: QuotaAdjust(
            usage_type=list(UsageType)[0], limit_count=1, reason="x" * 1001
        ),
        "QuotaAdjust.reason",
    ),
]


@pytest.mark.parametrize("build,label", OVERSIZED, ids=[c[1] for c in OVERSIZED])
def test_oversized_text_is_rejected(build, label: str) -> None:
    """超长自由文本必须**被拒**，而不是被静默截断或放行。"""
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize("build,label", OVERSIZED, ids=[c[1] for c in OVERSIZED])
def test_just_at_limit_is_accepted(build, label: str) -> None:
    """反向向量：正好等于上限必须放行 —— 否则说明「上限写错了地方」或 off-by-one。

    ⚠️ 没有这条，把上限值写小一半（比如 2000 → 1000）也测不出来：
    超长那条照样红，但正常长度的合法输入会被误杀。
    """
    build_at_limit = _AT_LIMIT[label]
    build_at_limit()


def test_exempt_fields_keep_no_limit() -> None:
    """已裁定豁免的 2 条（产品口径 A4）：量级差 3–4 个数量级，占输入 <0.1%。

    反向向量：确认**没有**顺手给它们加上限（加了会让长 URL / 长标签被误杀）。
    """
    doc = KnowledgeDocCreate(
        title="t", doc_type="d", content="c", source_ref="x" * 5000, tags="x" * 5000
    )
    assert len(doc.source_ref) == 5000
    assert len(doc.tags) == 5000


def test_no_silent_truncation() -> None:
    """🚨 最不能接受的失败模式：静默截断 —— 审查结论基于残缺合同。

    pydantic 的默认行为就是**报错**（而不是截断），这条把它钉住：
    一旦有人改成「截断后放行」，这里立刻红。
    """
    with pytest.raises(ValidationError):
        ContractReviewRequest(title="t", source_text="合" * 20001)
    # 正常长度不被动过
    ok = ContractReviewRequest(title="t", source_text="合" * 20000)
    assert len(ok.source_text) == 20000


# ---------------------------------------------------------------------------
# 「正好等于上限」的构造（与 OVERSIZED 一一对应）
# ---------------------------------------------------------------------------
_AT_LIMIT = {
    "AnalysisUpdate.summary": lambda: AnalysisUpdate(summary="x" * 2000),
    "AnalysisUpdate.legal_analysis": lambda: AnalysisUpdate(legal_analysis="x" * 20000),
    "AnalysisUpdate.change_note": lambda: AnalysisUpdate(change_note="x" * 500),
    "IterateRequest.note": lambda: IterateRequest(note="x" * 2000),
    "ComplianceScanCreate.title": lambda: ComplianceScanCreate(title="x" * 100),
    "ComplianceScanCreate.scope": lambda: ComplianceScanCreate(title="t", scope="x" * 500),
    "ComplianceScanCreate.input_summary": lambda: ComplianceScanCreate(
        title="t", input_summary="x" * 2000
    ),
    "ContractReviewRequest.source_text": lambda: ContractReviewRequest(
        title="t", source_text="x" * 20000
    ),
    "ContractReviewRequest.title": lambda: ContractReviewRequest(
        title="x" * 100, source_text="s"
    ),
    "KnowledgeDocCreate.title": lambda: KnowledgeDocCreate(
        title="x" * 100, doc_type="d", content="c"
    ),
    "KnowledgeDocCreate.content": lambda: KnowledgeDocCreate(
        title="t", doc_type="d", content="x" * 50000
    ),
    "SendMessageRequest.text": lambda: SendMessageRequest(text="x" * 4000),
    "QARequest.question": lambda: QARequest(question="x" * 4000),
    "ComplaintCreate.description": lambda: ComplaintCreate(
        type="OTHER", description="x" * 5000
    ),
    "ComplaintCreate.contact": lambda: ComplaintCreate(
        type="OTHER", description="d", contact="x" * 100
    ),
    "ComplaintHandle.handle_note": lambda: ComplaintHandle(
        status="RESOLVED", handle_note="x" * 2000
    ),
    "ReviewAction.comment": lambda: ReviewAction(decision="APPROVED", comment="x" * 1000),
    "QuotaAdjust.reason": lambda: QuotaAdjust(
        usage_type=list(UsageType)[0], limit_count=1, reason="x" * 1000
    ),
}
