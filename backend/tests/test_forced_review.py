"""强制复核命中器测试（PRD 5.5 五类场景）。

覆盖：客户要求正式意见 / 法律意见书 / 大额或敏感纠纷 / 高风险文书 / 对外合规报告，
以及最高级别聚合。
"""
from dataclasses import dataclass

from app.models.enums import ReviewLevel
from app.workflows.forced_review import (
    ForcedHit,
    detect_forced_review,
    highest_level,
)


@dataclass
class FakeCase:
    dispute_type: str = ""
    claim_amount: int = 0
    require_formal_opinion: bool = False


@dataclass
class FakeDoc:
    name: str = ""
    title: str = ""
    is_high_risk: bool = False


def test_no_hit_for_simple_case():
    hits = detect_forced_review(case=FakeCase(dispute_type="合同纠纷", claim_amount=10_000))
    assert hits == []


def test_client_formal_opinion_hits_l3():
    hits = detect_forced_review(case=FakeCase(require_formal_opinion=True))
    assert any(h.rule == "CLIENT_FORMAL_OPINION" and h.level == ReviewLevel.L3 for h in hits)


def test_legal_opinion_output_hits_l3():
    hits = detect_forced_review(output_type="LEGAL_OPINION")
    assert any(h.rule == "LEGAL_OPINION" and h.level == ReviewLevel.L3 for h in hits)


def test_large_amount_hits_l3():
    hits = detect_forced_review(case=FakeCase(dispute_type="合同纠纷", claim_amount=500_000))
    assert any(h.rule == "LARGE_AMOUNT" for h in hits)


def test_amount_below_threshold_no_hit():
    hits = detect_forced_review(case=FakeCase(claim_amount=499_999))
    assert not any(h.rule == "LARGE_AMOUNT" for h in hits)


def test_sensitive_dispute_hits_l3():
    for d in ("人身损害", "婚姻家庭", "刑事", "劳动争议"):
        hits = detect_forced_review(case=FakeCase(dispute_type=d))
        assert any(h.rule == "SENSITIVE_DISPUTE" for h in hits), d


def test_high_risk_document_hits_l3():
    hits = detect_forced_review(
        case=FakeCase(),
        document=FakeDoc(name="解除劳动合同通知书"),
    )
    assert any(h.rule == "HIGH_RISK_DOCUMENT" for h in hits)

    hits2 = detect_forced_review(case=FakeCase(), document=FakeDoc(is_high_risk=True))
    assert any(h.rule == "HIGH_RISK_DOCUMENT" for h in hits2)


def test_external_compliance_report_hits_l2():
    hits = detect_forced_review(compliance_report=True)
    assert any(h.rule == "EXTERNAL_COMPLIANCE_REPORT" and h.level == ReviewLevel.L2 for h in hits)


def test_multiple_hits_aggregate_highest():
    hits = detect_forced_review(
        case=FakeCase(dispute_type="刑事", claim_amount=1_000_000),
        compliance_report=True,
    )
    assert len(hits) >= 2
    assert highest_level(hits) == ReviewLevel.L3


def test_highest_level_default_l1():
    assert highest_level([]) == ReviewLevel.L1
    assert highest_level([ForcedHit(rule="x", reason="", level=ReviewLevel.L1)]) == ReviewLevel.L1
