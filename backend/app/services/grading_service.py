"""案件分级（PRD 5.2 S/A/B/C）。

依据标的额、复杂度、紧急度三维评分，映射到 S/A/B/C 等级，直接决定
复核深度（forced_review）与响应时效。纯函数实现，便于单元测试与规则热插拔。
"""
from app.models.enums import CaseGrade


def grade_case(
    *,
    claim_amount: float = 0.0,
    complexity: int = 0,  # 0-3
    urgency: int = 0,  # 0-3
    dispute_type: str | None = None,
) -> CaseGrade:
    """综合评分 -> 等级。

    评分权重：标的额（0-3）+ 复杂度（0-3）+ 紧急度（0-3），满分 9。
    - >=7 -> S（重大复杂）
    - 5-6 -> A（较复杂）
    - 3-4 -> B（一般）
    - <3  -> C（简单）
    """
    amount_score = _amount_score(claim_amount)
    score = amount_score + complexity + urgency
    if score >= 7:
        return CaseGrade.S
    if score >= 5:
        return CaseGrade.A
    if score >= 3:
        return CaseGrade.B
    return CaseGrade.C


def _amount_score(claim_amount: float) -> int:
    """标的额映射到 0-3 分。"""
    if claim_amount >= 500_000:
        return 3
    if claim_amount >= 100_000:
        return 2
    if claim_amount >= 10_000:
        return 1
    return 0


def grade_label(grade: CaseGrade) -> str:
    return {"S": "重大复杂", "A": "较复杂", "B": "一般", "C": "简单"}.get(grade.value, "未知")
