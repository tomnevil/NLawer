"""派单规则引擎测试（PRD 5.2 系统派单）。

覆盖：单条规则条件命中；优先级短路；空条件兜底；无命中走默认策略。
"""
from dataclasses import dataclass

from app.models.enums import CaseGrade
from app.workflows.dispatch_rules import pick_rule, rule_matches


@dataclass
class FakeCase:
    dispute_type: str = ""
    grade: CaseGrade = CaseGrade.C
    tenant_id: str = "t1"


@dataclass
class FakeRule:
    conditions: dict
    strategy: str
    priority: int = 0
    enabled: bool = True


def test_rule_matches_dispute_type():
    rule = FakeRule(conditions={"dispute_type": ["劳动争议"]}, strategy="AUTO")
    assert rule_matches(rule, FakeCase(dispute_type="劳动争议")) is True
    assert rule_matches(rule, FakeCase(dispute_type="合同纠纷")) is False


def test_rule_matches_grade_range():
    rule = FakeRule(conditions={"grade_in": ["S", "A"]}, strategy="AUTO")
    assert rule_matches(rule, FakeCase(grade=CaseGrade.S)) is True
    assert rule_matches(rule, FakeCase(grade=CaseGrade.C)) is False


def test_empty_conditions_is_fallback():
    rule = FakeRule(conditions={}, strategy="SPECIALTY_MATCH")
    assert rule_matches(rule, FakeCase()) is True


def test_pick_rule_by_priority():
    rules = [
        FakeRule(conditions={"dispute_type": ["合同纠纷"]}, strategy="AUTO", priority=10),
        FakeRule(conditions={}, strategy="SPECIALTY_MATCH", priority=1),
    ]
    chosen = pick_rule(rules, FakeCase(dispute_type="合同纠纷"))
    assert chosen.strategy == "AUTO"


def test_pick_rule_disabled_skipped():
    rules = [
        FakeRule(conditions={"dispute_type": ["合同纠纷"]}, strategy="AUTO", priority=10, enabled=False),
        FakeRule(conditions={}, strategy="SPECIALTY_MATCH", priority=1),
    ]
    chosen = pick_rule(rules, FakeCase(dispute_type="合同纠纷"))
    assert chosen.strategy == "SPECIALTY_MATCH"


def test_no_match_returns_none():
    rules = [FakeRule(conditions={"dispute_type": ["刑事"]}, strategy="AUTO", priority=1)]
    assert pick_rule(rules, FakeCase(dispute_type="合同纠纷")) is None
