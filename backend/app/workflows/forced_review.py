"""强制复核命中器（PRD 5.5 五类场景）。

范式映射 AIAcquisition 的 `_risk_gate` + `_escalate`：规则函数列表 + 短路求值，
命中即返回要求级别，由调用方写入 `required_level` 并阻塞流转。

五类场景：
1. 出具法律意见书
2. 标的额 >= 50 万，或涉人身损害 / 婚姻家庭 / 刑事
3. 高风险文书（解除通知、放弃权利声明等）
4. 对外提交的合规报告
5. 客户明确要求出具正式法律意见
"""
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models.enums import ReviewLevel

# 触发 L3 的敏感纠纷类型
_SENSITIVE_DISPUTES = {"人身损害", "婚姻家庭", "刑事", "劳动争议"}
# 高风险文书类型关键词
_HIGH_RISK_DOC_KEYWORDS = ("解除", "放弃", "免除", "免责", "承诺函")


@dataclass
class ForcedHit:
    """一条强制复核命中记录。"""

    rule: str
    reason: str
    level: ReviewLevel
    details: dict = field(default_factory=dict)


def detect_forced_review(
    *,
    case: Any = None,
    document: Any = None,
    compliance_report: bool = False,
    output_type: Optional[str] = None,
) -> list[ForcedHit]:
    """按五类规则短路求值，返回全部命中项（空列表表示无强制要求）。"""
    hits: list[ForcedHit] = []

    # 场景 5：客户明确要求正式法律意见
    if case is not None and getattr(case, "require_formal_opinion", False):
        hits.append(
            ForcedHit(
                rule="CLIENT_FORMAL_OPINION",
                reason="客户明确要求出具正式法律意见",
                level=ReviewLevel.L3,
            )
        )

    # 场景 1：输出类型为法律意见书
    if output_type == "LEGAL_OPINION":
        hits.append(
            ForcedHit(rule="LEGAL_OPINION", reason="输出为法律意见书，须合伙人终审", level=ReviewLevel.L3)
        )

    # 场景 2：标的额 / 敏感纠纷类型
    if case is not None:
        amount = getattr(case, "claim_amount", None) or 0
        if amount >= 500_000:
            hits.append(
                ForcedHit(
                    rule="LARGE_AMOUNT",
                    reason=f"标的额 {amount:,.0f} 元 >= 50 万",
                    level=ReviewLevel.L3,
                    details={"claim_amount": amount},
                )
            )
        dispute = getattr(case, "dispute_type", None)
        if dispute and any(k in dispute for k in _SENSITIVE_DISPUTES):
            hits.append(
                ForcedHit(
                    rule="SENSITIVE_DISPUTE",
                    reason=f"涉{dispute}，风险较高",
                    level=ReviewLevel.L3,
                    details={"dispute_type": dispute},
                )
            )

    # 场景 3：高风险文书
    if document is not None:
        name = f"{getattr(document, 'name', '') or ''}{getattr(document, 'title', '') or ''}"
        if any(k in name for k in _HIGH_RISK_DOC_KEYWORDS) or getattr(document, "is_high_risk", False):
            hits.append(
                ForcedHit(rule="HIGH_RISK_DOCUMENT", reason="高风险文书，须高级律师终审", level=ReviewLevel.L3)
            )

    # 场景 4：对外合规报告
    if compliance_report:
        hits.append(
            ForcedHit(rule="EXTERNAL_COMPLIANCE_REPORT", reason="对外合规报告须律师复核", level=ReviewLevel.L2)
        )

    return hits


def highest_level(hits: list[ForcedHit]) -> ReviewLevel:
    """取命中的最高复核级别（无命中则 L1）。"""
    if not hits:
        return ReviewLevel.L1
    order = {ReviewLevel.L1: 1, ReviewLevel.L2: 2, ReviewLevel.L3: 3}
    return max((h.level for h in hits), key=lambda lv: order[lv])
