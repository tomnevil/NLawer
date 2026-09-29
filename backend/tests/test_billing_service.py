"""计费服务测试（PRD 5.7 / 11.1）。

覆盖：收入预测分项系数法、工单号格式、价目表完整性（加急 > 标准、问答免费）、
超量转律师人工的类型集合。
"""
import re

import pytest

from app.models.enums import UsageType
from app.services.billing_service import (
    _ESCALATE_TYPES,
    _PRICE_CENTS,
    _REVENUE_WEIGHTS,
    BillingService,
    _order_no,
)


def _svc():
    # 纯函数测试，不需要 DB 会话
    return BillingService(None)


def test_project_revenue_weights_applied():
    r = _svc().project_revenue(
        subscription_cents=100_000,
        case_service_cents=50_000,
        work_order_cents=20_000,
        value_added_cents=10_000,
    )
    b = r["breakdown"]
    assert b["subscription"] == 100_000                    # 权重 1.0
    assert b["case_service"] == int(50_000 * 0.85)
    assert b["work_order"] == int(20_000 * 0.9)
    assert b["value_added"] == int(10_000 * 0.6)
    assert r["total_cents"] == sum(b.values())
    assert r["total_yuan"] == round(r["total_cents"] / 100, 2)
    assert r["weights"] == _REVENUE_WEIGHTS


def test_project_revenue_rejects_all_zero():
    """Q-AA（2026-09-21 裁定）：净合计 **<= 0** 必须被拒收 —— 含「全零」这种空输入。

    原用例名 `test_project_revenue_all_zero`，断言「默认入参 ⇒ total_cents == 0」；
    Q-AA 拍板「净零 / 净负都要拒」之后，这条期望**本身就是被裁掉的那部分**，
    故改为断言拒绝。

    ⚠️ 若产品认为「空预测=0」是合法业务语义，把服务层的判等从 `<= 0` 放宽为
    `< 0` 即可 —— 本用例与 `test_billing_endpoint_layer.py::M7` 会同时提示，
    不会静默漂移。
    """
    from app.core.errors import BadRequestError

    with pytest.raises(BadRequestError) as exc:
        _svc().project_revenue()
    assert exc.value.code == "VALIDATION_ERROR"


def test_weights_descending_by_certainty():
    # 确定性越高权重越高：订阅 > 工单 > 案件服务 > 增值
    w = _REVENUE_WEIGHTS
    assert w["subscription"] == 1.0
    assert w["subscription"] > w["work_order"] > w["case_service"] > w["value_added"]


def test_order_no_format():
    no = _order_no("firm_hlw")
    assert re.match(r"^WO-firm_hlw-\d{14}-[0-9a-f]{4}$", no), no


def test_order_no_unique():
    assert _order_no("t") != _order_no("t")


def test_price_table_covers_all_usage_types():
    assert set(_PRICE_CENTS) == set(UsageType)


def test_urgent_price_higher_than_standard_for_paid():
    for t, p in _PRICE_CENTS.items():
        assert p["urgent"] >= p["standard"], t
        if t != UsageType.QA:
            assert p["urgent"] > p["standard"] > 0, t


def test_qa_is_free():
    assert _PRICE_CENTS[UsageType.QA]["standard"] == 0
    assert _PRICE_CENTS[UsageType.QA]["urgent"] == 0


def test_escalate_types_are_high_value_services():
    # 合同审查与合规扫描超量时必须转律师人工处理
    assert _ESCALATE_TYPES == {UsageType.CONTRACT_REVIEW, UsageType.COMPLIANCE_SCAN}
    assert UsageType.QA not in _ESCALATE_TYPES
    assert UsageType.DOCUMENT not in _ESCALATE_TYPES
