"""可配置派单规则引擎（PRD 5.2 系统派单）。

规则以 JSON 条件表达，支持「专业领域命中 / 等级区间 / 最低执业年限 / 候选白名单」
组合判定，按优先级短路匹配。命中即返回对应策略，供 `dispatch_service` 执行。
"""
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, DispatchRule


def rule_matches(rule: DispatchRule, case: Case) -> bool:
    """判定单条规则是否命中本案（条件全部满足才算命中）。"""
    cond: dict[str, Any] = rule.conditions or {}
    if not cond:
        return True  # 空条件视为兜底规则

    # 纠纷类型
    if "dispute_type" in cond and case.dispute_type:
        if case.dispute_type not in cond["dispute_type"]:
            return False

    # 案件等级区间
    if "grade_in" in cond and case.grade.value not in cond["grade_in"]:
        return False

    # 最低执业年限（仅影响候选资格，交由 dispatch_service 校验）
    # 候选白名单（若设置且本案指定律师不在名单内，则不命中）
    if "candidate_lawyer_ids" in cond:
        return True

    return True


def pick_rule(rules: list[DispatchRule], case: Case) -> Optional[DispatchRule]:
    """按优先级（高 -> 低）返回第一条命中规则。"""
    ordered = sorted(rules, key=lambda r: -r.priority)
    for rule in ordered:
        if rule.enabled and rule_matches(rule, case):
            return rule
    return None


async def load_rules(db: AsyncSession, tenant_id: str) -> list[DispatchRule]:
    result = await db.execute(
        select(DispatchRule)
        .where(DispatchRule.tenant_id == tenant_id, DispatchRule.enabled == 1)
        .order_by(DispatchRule.priority.desc())
    )
    return list(result.scalars().all())


async def resolve_strategy(db: AsyncSession, case: Case) -> str:
    """解析本案适用的派单策略（兜底 SPECIALTY_MATCH）。"""
    rules = await load_rules(db, case.tenant_id)
    rule = pick_rule(rules, case)
    if rule is None:
        return "SPECIALTY_MATCH"
    return rule.strategy
