"""N4 判据：合规扫描「模型为主 + 关键词兜底」及冲突取舍规则。

覆盖：
- 模型与关键词一致 / 模型上调（有可核验证据）被采纳；
- 模型下调被关键词下限拒绝；
- 模型发现项证据无法在原文校验 ⇒ 不落库（防编造）；
- 不可解析 / 超时 / 不可用 / 空材料 ⇒ 降级为关键词且**可观测**（不静默）。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import uuid

import pytest

TENANT = "tenant-n4"


@pytest.fixture()
def factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    db = base / f"n4_{uuid.uuid4().hex[:8]}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return async_sessionmaker(engine, expire_on_commit=False)


def _patch_model(monkeypatch, *, payload=None, exc=None, calls=None):
    from app.ai.providers import LLMResult

    async def _complete(self, task, prompt, **kwargs):
        if calls is not None:
            calls.append(prompt)
        if exc is not None:
            raise exc
        return LLMResult(
            text=payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
            model="qwen3-30b",
            tier="strong",
            is_mock=False,
            prompt_tokens=506,
            completion_tokens=1380,
            duration_ms=69178,
        )

    monkeypatch.setattr("app.ai.router.ModelRouter.complete", _complete)


def _run_scan(factory, text, dimensions=None):
    from app.services.compliance_service import ComplianceService

    async def _go():
        async with factory() as s:
            svc = ComplianceService(s)
            scan = await svc.create_scan(
                title="N4 扫描", tenant_id=TENANT, input_summary=text, dimensions=dimensions
            )
            await svc.run(scan.id)
            await s.commit()
            rows = await svc.findings(scan.id)
            return {
                "scores": scan.dimension_scores,
                "summary": scan.report_summary,
                "overall": scan.overall_risk.value,
                "findings": [
                    (f.dimension.value, f.title, f.description, f.risk_level.value) for f in rows
                ],
            }

    return asyncio.run(_go())


def _labor_model(score, risk, findings):
    return {
        "dimensions": {
            "LABOR": {"score": score, "risk": risk, "findings": findings}
        }
    }


# ═══════════════════════ 合并规则 ═══════════════════════


def test_model_evidence_backed_escalation_is_adopted(monkeypatch, factory):
    """关键词 MEDIUM（未签合同+试用期）→ 模型 HIGH 且有原文证据 ⇒ 采纳上调。

    模型发现项标题与关键词不同 ⇒ 追加；与关键词同名 ⇒ 去重保留关键词版。
    """
    text = "存在未签劳动合同情形，试用期约定 6 个月。"
    payload = _labor_model(
        35,
        "HIGH",
        [
            {
                "title": "未签劳动合同",
                "description": "3 名员工入职超过一个月未签合同",
                "risk": "HIGH",
                "suggestion": "一个月内补签",
                "evidence": "未签劳动合同情形",
            },
            {
                "title": "试用期约定需核对法定上限",  # 与关键词同名 ⇒ 去重
                "description": "重复条目",
                "risk": "MEDIUM",
                "suggestion": "核对",
                "evidence": "试用期约定",
            },
        ],
    )
    _patch_model(monkeypatch, payload=payload)

    out = _run_scan(factory, text)

    labor = out["scores"]["LABOR"]
    assert labor["risk"] == "HIGH", labor
    assert labor["score"] == 35, "分数应落在 HIGH 档（20-59）"
    assert labor["engine"] == "model+keyword"
    assert labor["degraded"] is False
    assert labor["conflicts"] == [], labor
    # 关键词 2 条 + 模型新增 1 条（同名那条被去重）
    assert labor["findings"] == 3, labor
    titles = [t for _, t, _, _ in out["findings"]]
    assert "未签劳动合同" in titles
    assert "试用期约定需核对法定上限" in titles


def test_model_downgrade_is_rejected_by_keyword_floor(monkeypatch, factory):
    """关键词 HIGH（未签合同 + 社保）→ 模型 LOW ⇒ 拒绝下调，关键词下限保留。"""
    text = "存在未签劳动合同情形，社保未足额缴纳。"
    _patch_model(monkeypatch, payload=_labor_model(95, "LOW", []))

    out = _run_scan(factory, text)

    labor = out["scores"]["LABOR"]
    assert labor["risk"] == "HIGH", labor
    assert "MODEL_DOWNGRADE_REJECTED_KEYWORD_FLOOR" in labor["conflicts"], labor
    # 模型给 95 分，但最终 HIGH 档只能落在 20-59
    assert 20 <= labor["score"] <= 59, labor


def test_model_findings_without_verifiable_evidence_are_dropped(monkeypatch, factory):
    """模型发现项的 evidence 不在原文中 ⇒ 丢弃且记录，不落库（防编造）。"""
    text = "本公司成立于 2010 年，主要从事软件研发。"
    payload = _labor_model(
        30,
        "MEDIUM",
        [
            {
                "title": "员工未签劳动合同",
                "description": "模型编造的问题",
                "risk": "HIGH",
                "suggestion": "补签",
                "evidence": "员工未签劳动合同",  # 原文中不存在
            }
        ],
    )
    _patch_model(monkeypatch, payload=payload)

    out = _run_scan(factory, text)

    labor = out["scores"]["LABOR"]
    assert labor["findings"] == 0, "无法校验的模型发现项不得落库"
    assert any(c.startswith("MODEL_FINDINGS_REJECTED_UNVERIFIABLE") for c in labor["conflicts"]), labor
    # 关键词零命中时不设下限：模型的风险判断（MEDIUM）仍被采纳
    assert labor["risk"] == "MEDIUM", labor
    assert 60 <= labor["score"] <= 84, labor
    assert out["findings"] == []


def test_model_score_is_clamped_into_same_band(monkeypatch, factory):
    """模型与关键词同为 LOW，但给出 60 分（MEDIUM 档）⇒ 夹回 LOW 档。"""
    text = "试用期约定为 6 个月。"
    _patch_model(monkeypatch, payload=_labor_model(60, "LOW", []))

    out = _run_scan(factory, text)

    labor = out["scores"]["LABOR"]
    assert labor["risk"] == "LOW", labor
    assert labor["score"] == 85, labor


# ═══════════════════════ 降级可观测 ═══════════════════════


def test_unparsable_output_degrades_to_keyword_visibly(monkeypatch, factory):
    text = "存在未签劳动合同情形，社保未足额缴纳。"
    _patch_model(monkeypatch, payload="抱歉，我无法处理这段材料。")

    out = _run_scan(factory, text)

    labor = out["scores"]["LABOR"]
    assert labor["engine"] == "keyword", labor
    assert labor["degraded"] is True, labor
    assert labor["degrade_reason"] == "unparsable", labor
    # 降级后与纯关键词结果逐分一致（HIGH：-25 -25 = 50）
    assert labor["score"] == 50 and labor["risk"] == "HIGH", labor
    assert "降级为关键词规则扫描" in out["summary"], out["summary"]


def test_timeout_degrades_with_timeout_reason(monkeypatch, factory):
    import httpx

    _patch_model(monkeypatch, exc=httpx.ReadTimeout("gateway read timeout"))

    out = _run_scan(factory, "存在未签劳动合同情形。")

    labor = out["scores"]["LABOR"]
    assert labor["degraded"] is True and labor["degrade_reason"] == "timeout", labor
    assert "调用超时" in out["summary"], out["summary"]


def test_unavailable_model_degrades_with_reason(monkeypatch, factory):
    _patch_model(monkeypatch, exc=RuntimeError("gateway down"))

    out = _run_scan(factory, "存在未签劳动合同情形。")

    labor = out["scores"]["LABOR"]
    assert labor["degraded"] is True and labor["degrade_reason"] == "unavailable", labor
    assert "模型不可用" in out["summary"], out["summary"]


def test_empty_material_skips_model_and_reports_it(monkeypatch, factory):
    calls: list[str] = []
    _patch_model(monkeypatch, payload=_labor_model(90, "LOW", []), calls=calls)

    out = _run_scan(factory, "")

    assert calls == [], "材料为空时不应调用模型"
    labor = out["scores"]["LABOR"]
    assert labor["degraded"] is True and labor["degrade_reason"] == "empty_input", labor
    assert labor["score"] == 100 and labor["risk"] == "LOW", labor
    assert "未提供可扫描材料" in out["summary"], out["summary"]


def test_single_dimension_prompt_only_covers_requested_dimension(monkeypatch, factory):
    calls: list[str] = []
    _patch_model(
        monkeypatch,
        payload={"dimensions": {"LABOR": {"score": 40, "risk": "HIGH", "findings": []}}},
        calls=calls,
    )

    out = _run_scan(factory, "存在未签劳动合同情形。", dimensions=["LABOR"])

    assert len(calls) == 1, "单维度扫描只应调用一次模型"
    assert "DATA_PRIVACY" not in calls[0], "单维度提示词不应包含未请求的维度"
    assert set(out["scores"]) == {"LABOR"}, out["scores"]
