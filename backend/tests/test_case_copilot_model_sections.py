"""N1 判据：六段式分析必须真正使用模型输出，模板兜底必须可识别。

历史缺陷（2026-11-09）：`generate()` 先调 `_build_sections()` 拼模板，再去调模型，
模型返回文本只用于记录 token，**从未进入落库内容**；`AiRun` 仍标 COMPLETED，
审计上把「模板话术」记成「模型成功」。

本文件钉住两件事：

1. 模型输出可解析时，summary / legal_analysis / suggestions / missing_info
   逐字等于模型 JSON；法条与类案来自检索结果（citation_id 可溯源）。
2. 模型调用失败或输出不可解析时回落模板，且 `AiRun.status=FAILED`、
   `error_message` 以 `TEMPLATE_FALLBACK:` 开头、`stage_counts.sections_source="template"`，
   并留一条 `decision=FALLBACK` 的审计。`ai_generated` 保持 1（该字段表示
   「律师完全手写」，模板话术不能被误归为人工确认）。
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT = "tenant-n1"

MODEL_JSON = {
    "summary": "模型生成的案件摘要：原告主张未签劳动合同二倍工资。",
    "legal_analysis": "模型生成的法律分析：用人单位自用工满一个月未签书面合同，应支付二倍工资。",
    "suggestions": [
        {"path": "先协商", "pros": "成本低", "cons": "对方可能拖延"},
        {"path": "申请仲裁", "pros": "有强制力", "cons": "周期较长"},
    ],
    "missing_info": [
        {"item": "工资流水", "reason": "核算二倍工资差额", "priority": 1},
        {"item": "考勤记录", "reason": "证明用工事实", "priority": 2},
    ],
}


@pytest.fixture()
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    db = base / f"n1_{uuid.uuid4().hex[:8]}.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)

    import asyncio

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return eng


@pytest.fixture()
def factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


def _case():
    from app.models.case import Case
    from app.models.enums import CaseGrade, CaseStatus

    return Case(
        tenant_id=TENANT,
        case_no=f"CASE-N1-{uuid.uuid4().hex[:6]}",
        title="劳动争议案件",
        status=CaseStatus.ACCEPTED,
        grade=CaseGrade.C,
        dispute_type="劳动争议",
        summary="公司未签劳动合同并拖欠工资",
        party_a="张三",
        party_b="某公司",
        claim_amount=12000,
    )


def _law():
    from app.models.citation import LawArticle

    return LawArticle(
        tenant_id="platform",
        law_name="中华人民共和国劳动合同法",
        article_no="第82条",
        content="用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资。",
        tags="劳动争议",
    )


async def _seed(factory):
    case, law = _case(), _law()
    async with factory() as s:
        s.add_all([case, law])
        await s.commit()
        return case.id, law.id


# ═══════════════════════ 纯函数判据 ═══════════════════════


def test_parse_model_sections_accepts_fenced_json():
    from app.services.case_copilot import _parse_model_sections

    text = "```json\n" + json.dumps(MODEL_JSON, ensure_ascii=False) + "\n```"
    parsed, reason = _parse_model_sections(text)
    assert reason is None
    assert parsed["summary"] == MODEL_JSON["summary"]
    assert parsed["legal_analysis"] == MODEL_JSON["legal_analysis"]
    assert [s["path"] for s in parsed["suggestions"]] == ["先协商", "申请仲裁"]
    assert parsed["missing_info"][0]["priority"] == 1


@pytest.mark.parametrize(
    "text,reason_kw",
    [
        ("", "找不到 JSON"),
        ("这不是 JSON", "找不到 JSON"),
        ('{"summary": "", "legal_analysis": "分析"}', "summary"),
        ('{"summary": "摘要", "legal_analysis": "分析", "suggestions": "x"}', "suggestions"),
    ],
)
def test_parse_model_sections_rejects_invalid(text, reason_kw):
    from app.services.case_copilot import _parse_model_sections

    parsed, reason = _parse_model_sections(text)
    assert parsed is None
    assert reason_kw in (reason or "")


def test_build_sections_uses_model_text_and_grounded_citations():
    from app.services.case_copilot import CaseCopilot

    case, law = _case(), _law()
    law.id = 42
    sections, source, reason = CaseCopilot(db=None)._build_sections(
        case, [law], [], model_text=json.dumps(MODEL_JSON, ensure_ascii=False), note=None
    )
    assert source == "model"
    assert reason is None
    assert sections["summary"] == MODEL_JSON["summary"]
    assert sections["legal_analysis"] == MODEL_JSON["legal_analysis"]
    # 法条来自检索结果，citation_id 可溯源；模型不得编造
    assert sections["related_laws"][0]["citation_id"] == 42
    assert sections["related_laws"][0]["law_name"] == "中华人民共和国劳动合同法"


def test_build_sections_falls_back_to_template():
    from app.services.case_copilot import CaseCopilot

    case, law = _case(), _law()
    sections, source, reason = CaseCopilot(db=None)._build_sections(
        case, [law], [], model_text="", note=None
    )
    assert source == "template"
    assert reason == "模型返回空文本（可能为 mock provider 或网关未响应）"
    assert "【当事人】" in sections["summary"]


# ═══════════════════════ 端到端判据 ═══════════════════════


def _patch_router(monkeypatch, *, text=None, exc=None, tokens=(123, 45)):
    import app.services.case_copilot as mod
    from app.ai.providers import LLMResult

    async def _complete(task, prompt, **kwargs):
        if exc is not None:
            raise exc
        return LLMResult(
            text=text,
            model="qwen3-30b",
            tier="cheap",
            is_mock=False,
            prompt_tokens=tokens[0],
            completion_tokens=tokens[1],
        )

    monkeypatch.setattr(mod.router, "complete", _complete)


def test_generate_persists_model_sections_and_tokens(monkeypatch, factory):
    import asyncio

    from app.models.ai_run import AiRun
    from app.models.analysis import CaseAnalysis, CaseAnalysisVersion
    from app.services.case_copilot import CaseCopilot

    case_id, _ = asyncio.run(_seed(factory))
    _patch_router(monkeypatch, text=json.dumps(MODEL_JSON, ensure_ascii=False))

    async def _run():
        async with factory() as s:
            analysis = await CaseCopilot(s).generate(case_id)
            await s.commit()
            return analysis

    analysis = asyncio.run(_run())

    async def _verify():
        async with factory() as s:
            a = (await s.execute(
                select(CaseAnalysis).where(CaseAnalysis.id == analysis.id))).scalars().one()
            run = (await s.execute(select(AiRun).where(AiRun.id == a.run_id))).scalars().one()
            snap = (await s.execute(
                select(CaseAnalysisVersion).where(CaseAnalysisVersion.analysis_id == a.id)
            )).scalars().one()
            return a, run, snap

    a, run, snap = asyncio.run(_verify())
    assert a.summary == MODEL_JSON["summary"]
    assert a.legal_analysis == MODEL_JSON["legal_analysis"]
    assert [s["path"] for s in a.suggestions] == ["先协商", "申请仲裁"]
    assert a.missing_info[0]["item"] == "工资流水"
    assert a.ai_generated == 1
    assert run.status.value == "completed"
    assert run.stage_counts["sections_source"] == "model"
    assert run.prompt_tokens == 123 and run.completion_tokens == 45
    assert snap.snapshot["summary"] == MODEL_JSON["summary"]


def test_generate_marks_template_fallback_on_model_error(monkeypatch, factory):
    import asyncio

    from app.models.ai_run import AiDecision, AiRun
    from app.models.analysis import CaseAnalysis
    from app.services.case_copilot import CaseCopilot

    case_id, _ = asyncio.run(_seed(factory))
    _patch_router(monkeypatch, exc=RuntimeError("gateway down"))

    async def _run():
        async with factory() as s:
            analysis = await CaseCopilot(s).generate(case_id)
            await s.commit()
            return analysis

    analysis = asyncio.run(_run())

    async def _verify():
        async with factory() as s:
            a = (await s.execute(
                select(CaseAnalysis).where(CaseAnalysis.id == analysis.id))).scalars().one()
            run = (await s.execute(select(AiRun).where(AiRun.id == a.run_id))).scalars().one()
            decisions = (await s.execute(
                select(AiDecision).where(AiDecision.run_id == a.run_id)
            )).scalars().all()
            return a, run, decisions

    a, run, decisions = asyncio.run(_verify())
    assert "【当事人】" in a.summary, "模板兜底内容应回落，而不是落空"
    # ai_generated 在律师端表示「律师完全手写」；模板兜底仍是生成内容，不能标 0
    assert a.ai_generated == 1
    assert run.status.value == "failed"
    assert run.error_message.startswith("TEMPLATE_FALLBACK:")
    assert run.stage_counts["sections_source"] == "template"
    assert any(d.stage == "generate" and d.decision == "FALLBACK" for d in decisions)


def test_generate_marks_template_fallback_on_unparseable_output(monkeypatch, factory):
    import asyncio

    from app.models.ai_run import AiRun
    from app.models.analysis import CaseAnalysis
    from app.services.case_copilot import CaseCopilot

    case_id, _ = asyncio.run(_seed(factory))
    _patch_router(monkeypatch, text="模型这次只回了自然语言，没有 JSON。")

    async def _run():
        async with factory() as s:
            analysis = await CaseCopilot(s).generate(case_id)
            await s.commit()
            return analysis

    analysis = asyncio.run(_run())

    async def _verify():
        async with factory() as s:
            a = (await s.execute(
                select(CaseAnalysis).where(CaseAnalysis.id == analysis.id))).scalars().one()
            run = (await s.execute(select(AiRun).where(AiRun.id == a.run_id))).scalars().one()
            return a, run

    a, run = asyncio.run(_verify())
    assert a.ai_generated == 1
    assert run.status.value == "failed"
    assert "JSON" in (run.error_message or "") or "TEMPLATE_FALLBACK" in (run.error_message or "")
