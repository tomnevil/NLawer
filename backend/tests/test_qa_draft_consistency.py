"""N2 判据：`/qa` 的 `sections` 必须与落库草稿同源；空草稿不落库、不建复核。

历史缺陷（2026-11-09）：

- `QAService.build()` 固定返回 `sections={"conclusion": answer, 其余空}`，
  而 `consult_reports.draft_sections` 是模型另行给出的四段式 ⇒ 接口返回与
  落库内容**两套数据**，调用方无法据 `sections` 复核/回放。
- 模型明确表示信息不足、四段全空时，草稿照样落库并建一条 `CONSULT_REPORT`
  复核 ⇒ 律师复核台出现无法复核的空单（生产已有 4 条）。
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT = "tenant-n2"
LAWYER_ID = 7


@pytest.fixture()
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    db = base / f"n2_{uuid.uuid4().hex[:8]}.db"
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


def _seed_lawyer():
    from app.models.identity import LawyerProfile

    return LawyerProfile(
        user_id=LAWYER_ID,
        tenant_id=TENANT,
        available=True,
        can_l3_review=False,
        active_case_count=0,
    )


def _seed_law():
    """种子法条：让 `_retrieve` 能命中并产出非空 citations。

    B3 之后引用校验是**强校验**（不再只在 citations 非空时才校验），
    无命中的问题会被判生成失败。成功路径的用例必须提供命中来源。
    """
    from app.models.citation import LawArticle

    return LawArticle(
        tenant_id="platform",
        law_name="中华人民共和国劳动合同法",
        article_no="第82条",
        content="用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资。",
        tags="劳动争议",
        effective_date="2008-01-01",
    )


def _patch_model(monkeypatch, *, payload=None, exc=None):
    from app.ai.providers import LLMResult

    async def _complete(self, task, prompt, **kwargs):
        if exc is not None:
            raise exc
        return LLMResult(
            text=payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
            model="qwen3-30b",
            tier="cheap",
            is_mock=False,
            prompt_tokens=10,
            completion_tokens=20,
        )

    monkeypatch.setattr("app.ai.router.ModelRouter.complete", _complete)


DRAFT = {
    "conclusion": "公司未签书面劳动合同，应支付二倍工资差额。",
    "legal_basis": "《中华人民共和国劳动合同法》第82条。",
    "advice": "1. 固定工资流水；2. 申请劳动仲裁。",
    "risk": "仲裁时效为一年，注意不要逾期。",
}


async def _build(factory, monkeypatch, **patch_kw):
    from app.services.qa_service import QAService

    _patch_model(monkeypatch, **patch_kw)
    async with factory() as s:
        result = await QAService(s).build("公司拖欠工资且没签劳动合同怎么办？", TENANT, user_id=3)
        # B3：服务层不再自行 commit，由请求边界统一提交（这里模拟边界）。
        await s.commit()
        return result


async def _counts(factory):
    from app.models.consult_report import ConsultReport
    from app.models.review import Review

    async with factory() as s:
        reports = (await s.execute(select(ConsultReport))).scalars().all()
        reviews = (await s.execute(select(Review))).scalars().all()
        return reports, reviews


# ═══════════════════════ 纯函数判据 ═══════════════════════


def test_parse_consult_normalizes_four_keys():
    from app.services.qa_service import _has_draft_content, _parse_consult

    text = json.dumps({"answer": "自然回复", "draft": DRAFT}, ensure_ascii=False)
    answer, draft = _parse_consult(text)
    assert answer == "自然回复"
    assert set(draft) == {"conclusion", "legal_basis", "advice", "risk"}
    assert _has_draft_content(draft) is True
    assert _has_draft_content({"conclusion": "", "legal_basis": " ", "advice": "", "risk": ""}) is False


# ═══════════════════════ 端到端判据 ═══════════════════════


def test_qa_sections_match_saved_draft_and_review_created(monkeypatch, factory):
    import asyncio

    async def _run():
        async with factory() as s:
            s.add(_seed_lawyer())
            s.add(_seed_law())
            await s.commit()
        return await _build(factory, monkeypatch, payload={"answer": "自然回复", "draft": DRAFT})

    result = asyncio.run(_run())
    reports, reviews = asyncio.run(_counts(factory))

    assert result["draft_saved"] is True
    assert result["sections"] == DRAFT
    assert len(reports) == 1
    # 红线：接口返回的四段式与落库草稿必须一字不差
    assert reports[0].draft_sections == result["sections"]
    assert reports[0].review_id is not None
    assert len(reviews) == 1
    assert reviews[0].target_type.value == "CONSULT_REPORT"


def test_empty_draft_is_not_saved_and_creates_no_review(monkeypatch, factory):
    import asyncio

    empty = {"conclusion": "", "legal_basis": "", "advice": "", "risk": ""}

    async def _run():
        async with factory() as s:
            s.add(_seed_lawyer())
            await s.commit()
        return await _build(factory, monkeypatch, payload={"answer": "请补充合同与工资流水。", "draft": empty})

    result = asyncio.run(_run())
    reports, reviews = asyncio.run(_counts(factory))

    assert result["draft_saved"] is False
    assert result["sections"] == empty
    assert result["answer"] == "请补充合同与工资流水。"
    assert reports == [], "空草稿不得落库"
    assert reviews == [], "空草稿不得创建 CONSULT_REPORT 复核"


def test_small_talk_returns_empty_sections(monkeypatch, factory):
    import asyncio

    from app.services.qa_service import QAService

    async def _run():
        async with factory() as s:
            return await QAService(s).build("你好", TENANT)

    result = asyncio.run(_run())
    assert result["draft_saved"] is False
    assert result["sections"] == {"conclusion": "", "legal_basis": "", "advice": "", "risk": ""}


def test_model_failure_falls_back_to_template_and_saves(monkeypatch, factory):
    import asyncio

    async def _run():
        async with factory() as s:
            s.add(_seed_lawyer())
            s.add(_seed_law())
            await s.commit()
        return await _build(factory, monkeypatch, exc=RuntimeError("gateway down"))

    result = asyncio.run(_run())
    reports, reviews = asyncio.run(_counts(factory))

    assert result["draft_saved"] is True
    assert result["sections"]["conclusion"], "模板兜底仍应给出非空结论"
    assert reports[0].draft_sections == result["sections"]
    assert len(reviews) == 1


# ═══════════════════════ 提示词口径（空草稿覆盖率）═══════════════════════


def test_prompt_demands_full_draft_only_when_sources_exist():
    """有检索来源时四段必须写满；无来源时保留旧口径（草稿可空）。"""
    from app.services.qa_service import _build_consult_prompt

    q = "公司未签劳动合同怎么办？"
    with_sources = _build_consult_prompt(
        q, "- 法条《中华人民共和国劳动合同法》第82条：二倍工资。", has_sources=True
    )
    assert "四段都必须有内容" in with_sources
    assert "可留空字符串" not in with_sources

    without_sources = _build_consult_prompt(
        q, "（无可用法条/类案，请基于一般法律原则回答）", has_sources=False
    )
    assert "可留空字符串" in without_sources
    assert "四段都必须有内容" not in without_sources


def test_build_passes_source_flag_to_prompt(monkeypatch, factory):
    """`build` 必须把「是否有引用来源」传进提示词，而不是一律用同一条提示词。"""
    import asyncio

    from app.services.qa_service import QAService

    seen: list[str] = []

    def _capture_patch():
        from app.ai.providers import LLMResult

        async def _complete(self, task, prompt, **kwargs):
            seen.append(prompt)
            return LLMResult(
                text=json.dumps({"answer": "自然回复", "draft": DRAFT}, ensure_ascii=False),
                model="qwen3-30b",
                tier="cheap",
                is_mock=False,
            )

        monkeypatch.setattr("app.ai.router.ModelRouter.complete", _complete)

    _capture_patch()

    async def _seed_profiles():
        async with factory() as s:
            s.add(_seed_lawyer())
            s.add(_seed_law())
            await s.commit()

    asyncio.run(_seed_profiles())

    async def _with_sources():
        async with factory() as s:
            await QAService(s).build("公司拖欠工资且没签劳动合同怎么办？", TENANT, user_id=3)

    asyncio.run(_with_sources())
    assert "四段都必须有内容" in seen[0], "有来源时提示词未要求写满四段"

    async def _drop_sources():
        from sqlalchemy import delete

        from app.models.citation import LawArticle

        async with factory() as s:
            await s.execute(delete(LawArticle))
            await s.commit()

    asyncio.run(_drop_sources())

    async def _without_sources():
        async with factory() as s:
            await QAService(s).build("公司拖欠工资且没签劳动合同怎么办？", TENANT, user_id=3)

    asyncio.run(_without_sources())
    assert "可留空字符串" in seen[1], "无来源时不应强制四段非空（否则必然撞引用校验）"


# ═══════════════════════ B3 引用强校验 / 事务边界 ═══════════════════════


def test_missing_citations_downgrades_to_friendly_reply(monkeypatch, factory):
    """B3（拍板后）：无检索命中 + 非空结论 ⇒ 200 + 友好话术，不抛 400。

    旧实现抛 `CITATION_MISSING`，客户看到错误码；现在改由后端兜底：
    - 响应 `degraded=True` / `degrade_reason=CITATION_MISSING` / `billable=False`；
    - 客户话术换成 `FALLBACK_ANSWER`，四段式置空；
    - 不落草稿、不建复核（不产生律师无法复核的空单）。
    """
    import asyncio

    from app.services.qa_service import FALLBACK_ANSWER, QAService

    _patch_model(monkeypatch, payload={"answer": "自然回复", "draft": DRAFT})

    async def _run():
        async with factory() as s:
            s.add(_seed_lawyer())
            await s.commit()  # 刻意不种子法条 ⇒ citations 为空
        async with factory() as s:
            return await QAService(s).build("公司拖欠工资且没签劳动合同怎么办？", TENANT, user_id=3)

    result = asyncio.run(_run())
    reports, reviews = asyncio.run(_counts(factory))

    assert result["degraded"] is True
    assert result["degrade_reason"] == "CITATION_MISSING"
    assert result["billable"] is False, "引用缺失属生成失败，不得计费"
    assert result["answer"] == FALLBACK_ANSWER, "对客户不得出现错误码/原始技术异常"
    assert result["sections"] == {"conclusion": "", "legal_basis": "", "advice": "", "risk": ""}
    assert result["draft_saved"] is False
    assert reports == [], "引用缺失时草稿不得落库"
    assert reviews == [], "引用缺失时不得创建复核"


def test_success_path_is_billable_and_not_degraded(monkeypatch, factory):
    """对照组：成功落草稿时为 `billable=True`、`degraded=False`（防止一律降级改绿）。"""
    import asyncio

    async def _run():
        async with factory() as s:
            s.add(_seed_lawyer())
            s.add(_seed_law())
            await s.commit()
        return await _build(factory, monkeypatch, payload={"answer": "自然回复", "draft": DRAFT})

    result = asyncio.run(_run())
    assert result["billable"] is True
    assert result["degraded"] is False
    assert result["draft_saved"] is True


def test_build_does_not_commit_on_its_own(monkeypatch, factory):
    """B3：服务层不自行 commit —— 不提交会话时，草稿不会凭空落库。

    旧实现 `_save_consult_report` 内部 commit，调用方即使不提交也已经持久化，
    「请求失败可整体回滚」这条保证不存在。
    """
    import asyncio

    from app.services.qa_service import QAService

    _patch_model(monkeypatch, payload={"answer": "自然回复", "draft": DRAFT})

    async def _run():
        async with factory() as s:
            s.add(_seed_lawyer())
            s.add(_seed_law())
            await s.commit()
        async with factory() as s:
            result = await QAService(s).build(
                "公司拖欠工资且没签劳动合同怎么办？", TENANT, user_id=3
            )
            assert result["draft_saved"] is True
            # 故意不 commit：模拟请求在提交前失败
            await s.rollback()
            return result

    asyncio.run(_run())
    reports, reviews = asyncio.run(_counts(factory))
    assert reports == [], "服务层自行 commit 了（请求边界无法整体回滚）"
    assert reviews == []


# ═══════════════════════ 流式：回答先行、草稿后置 ═══════════════════════


def test_answer_only_returns_natural_answer(monkeypatch, factory):
    """回答专用调用不得产草稿，且永不抛异常。"""
    import asyncio

    from app.services.qa_service import QAService

    _patch_model(monkeypatch, payload={"answer": "先给客户的回答"})

    async def _run():
        async with factory() as s:
            return await QAService(s).answer_only("公司拖欠工资怎么办？", TENANT)

    result = asyncio.run(_run())
    assert result["ok"] is True
    assert result["answer"] == "先给客户的回答"
    assert result["degraded"] is False


def test_answer_only_returns_not_ok_on_model_error(monkeypatch, factory):
    import asyncio

    from app.services.qa_service import QAService

    _patch_model(monkeypatch, exc=RuntimeError("gateway down"))

    async def _run():
        async with factory() as s:
            return await QAService(s).answer_only("公司拖欠工资怎么办？", TENANT)

    result = asyncio.run(_run())
    assert result["ok"] is False and result["degraded"] is True
    assert result["degrade_reason"] == "MODEL_UNAVAILABLE"


def test_generate_draft_persists_with_given_answer(monkeypatch, factory):
    """后台草稿：用自己的会话落库，`ConsultReport.answer` 复用已发送的回答。"""
    import asyncio

    from app.services.qa_service import QAService

    _patch_model(monkeypatch, payload={"draft": DRAFT})

    async def _run():
        async with factory() as s:
            s.add(_seed_lawyer())
            s.add(_seed_law())
            await s.commit()
        async with factory() as s:
            saved = await QAService(s).generate_draft(
                "公司拖欠工资且没签劳动合同怎么办？", "已发送的回答", TENANT, user_id=3
            )
            await s.commit()
            return saved

    assert asyncio.run(_run()) is True
    reports, reviews = asyncio.run(_counts(factory))
    assert len(reports) == 1
    assert reports[0].draft_sections == DRAFT
    assert reports[0].answer == "已发送的回答"
    assert len(reviews) == 1, "后台草稿同样要入复核"


def test_generate_draft_drops_when_citations_missing(monkeypatch, factory):
    import asyncio

    from app.services.qa_service import QAService

    _patch_model(monkeypatch, payload={"draft": DRAFT})

    async def _run():
        async with factory() as s:
            return await QAService(s).generate_draft("公司拖欠工资怎么办？", "回答", TENANT)

    assert asyncio.run(_run()) is False
    reports, reviews = asyncio.run(_counts(factory))
    assert reports == [] and reviews == []


def test_stream_marks_draft_deferred(monkeypatch, factory):
    """流式 done 必须声明草稿是否后置，且失败时不后置（供端点决策）。"""
    import asyncio
    import json as _json

    from app.services.qa_service import QAService

    async def _ok(self, question, tenant_id, user_id=None):
        return {
            "ok": True,
            "answer": "自然回答",
            "citations": [],
            "disclaimer": "x",
            "degraded": False,
            "degrade_reason": "",
        }

    monkeypatch.setattr(QAService, "answer_only", _ok)

    async def _collect(q):
        async with factory() as s:
            chunks = [c async for c in QAService(s).stream(q, TENANT)]
        done = [
            _json.loads(c[len("data: ") :])
            for c in chunks
            if c.startswith("data: ") and '"done"' in c
        ][-1]
        return chunks, done

    _, done = asyncio.run(_collect("公司拖欠工资怎么办？"))
    assert done["failed"] is False and done["draft_deferred"] is True, done
    assert "草稿" not in "".join(done.get("sections", {}).values()), "草稿不得出现在流里"

    _, done_small = asyncio.run(_collect("你好"))
    assert done_small["draft_deferred"] is False, "寒暄不生成草稿"


def test_stream_falls_back_without_leaking_error(monkeypatch, factory):
    import asyncio
    import json as _json

    from app.services.qa_service import FALLBACK_ANSWER, QAService

    async def _bad(self, question, tenant_id, user_id=None):
        raise RuntimeError("gateway exploded")

    monkeypatch.setattr(QAService, "answer_only", _bad)

    async def _collect():
        async with factory() as s:
            return [c async for c in QAService(s).stream("公司拖欠工资怎么办？", TENANT)]

    chunks = asyncio.run(_collect())
    done = [
        _json.loads(c[len("data: ") :]) for c in chunks if c.startswith("data: ") and '"done"' in c
    ][-1]
    assert done["failed"] is True and done["degraded"] is True
    assert done["draft_deferred"] is False
    assert done["sections"]["conclusion"] == FALLBACK_ANSWER
    assert "gateway exploded" not in "".join(chunks), "技术异常不得出现在流里"
