"""合同审查真实化测试（P0-16）。

## 这个文件在守护什么

合同审查是**按 99 元/次计费**的功能，改造前它零 LLM 调用、法条是假的、
零发现即判「无风险」，而计费是「只要返回 200 就扣费」。因此本文件的断言
几乎都指向**钱与信任**，而不是「代码能跑」：

1. **定位逐字一致**（`original == source_text[char_start:char_end]`）——
   用户按高亮跳过去必须看到清单里那一条，否则定位比不定位更伤信任。
   这条由「模型只返回 `clause_index`，区间本地填」保证，测试用多条款 + 首尾边界验证。
2. **依据可验证**——`basis_type=statute` 必须在库内精确命中；
   模型编的法条必须被降级为 `experience` 且 `citation is None`。
3. **零发现不放行**——LLM 失败 + 规则零命中 ⇒ `prescreen_only` 且
   `overall_risk != NONE`（回归原 `_overall([]) → NONE` 缺陷）。
4. **计费诚实**——`source=rule/mock` 或 `status=degraded/failed` 时
   `consume_atomic` **一次都不许被调用**，且返回体**没有** `usage` 键。

## 离线可验证 vs 无法验证（如实标注）

本文件全部用 `FakeRouter` 注入固定输出，因此验证的是**与模型无关的正确性属性**
（切分/定位/引用校验/降级链/计费门控/留痕）。**没有任何一条断言能证明
「真实模型给出的法律判断是对的」**——那需要真实 LLM Key 与人工复核，本轮无法离线验证。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import uuid

import httpx
import pytest

CONTRACT = (
    "采购合同\n"
    "\n"
    "第一条 甲方为某某科技有限公司。\n"
    "第二条 乙方应于收到货物后三十日内支付全部货款。\n"
    "第三条 任何一方违约的，应向对方支付违约金，标准为合同总价的 200%。\n"
    "第四条 因本合同发生争议，提交甲方所在地人民法院管辖。\n"
)


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    """模块级引擎（NullPool）。

    用 `NullPool` 而不是默认池：每个用例各跑在自己的事件循环里，
    池化连接跨循环复用会随机抛「attached to a different loop」——
    这类失败与代码无关，却会让整个文件变成不可信的噪声源。
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"cr_{uuid.uuid4().hex[:8]}.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False, poolclass=NullPool)

    async def _setup() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models.citation import LawArticle

        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as s:
            s.add_all(
                [
                    LawArticle(
                        tenant_id="platform",
                        law_name="中华人民共和国民法典",
                        article_no="第577条",
                        content="当事人一方不履行合同义务或者履行合同义务不符合约定的，"
                        "应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
                        tags="合同纠纷,违约责任",
                    ),
                    LawArticle(
                        tenant_id="platform",
                        law_name="中华人民共和国民法典",
                        article_no="第584条",
                        content="损失赔偿额应当相当于因违约所造成的损失。",
                        tags="合同纠纷,损失赔偿",
                    ),
                ]
            )
            await s.commit()

    asyncio.run(_setup())
    yield eng
    asyncio.run(eng.dispose())


@pytest.fixture
async def db(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _clean_metrics():
    """指标是进程内单例，用例之间必须隔离，否则计数断言会互相污染。"""
    from app.core.metrics import metrics

    metrics.reset()
    yield
    metrics.reset()


class FakeRouter:
    """可注入的假路由：把「模型输出」变成测试的输入变量。

    这是本轮验证方案的核心——无真实 LLM Key 时，只有注入才能把
    「定位/依据/降级/计费」这些与模型无关的属性变成可断言的事实。
    """

    def __init__(
        self,
        *,
        text: str = "",
        exc: Exception | None = None,
        is_mock: bool = False,
        model: str = "fake-strong",
        tier: str = "strong",
        prompt_tokens: int = 1200,
        completion_tokens: int = 300,
    ) -> None:
        self.text = text
        self.exc = exc
        self.is_mock = is_mock
        self.model = model
        self.tier = tier
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.calls: list[dict] = []

    async def complete(self, task, prompt, *, system=None, sensitive=False, temperature=0.3, max_tokens=2000):
        self.calls.append(
            {"task": task, "prompt": prompt, "system": system, "sensitive": sensitive}
        )
        if self.exc is not None:
            raise self.exc
        from app.ai.providers import LLMResult

        return LLMResult(
            text=self.text,
            model=self.model,
            tier=self.tier,
            is_mock=self.is_mock,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            duration_ms=7,
        )


def _payload(*findings: dict, summary: str = "总体结论") -> str:
    return json.dumps({"summary": summary, "findings": list(findings)}, ensure_ascii=False)


def _index_of(clauses, needle: str) -> int:
    for c in clauses:
        if needle in c.text:
            return c.index
    raise AssertionError(f"切分结果里找不到包含「{needle}」的条款")


async def _review(db, router, *, text: str = CONTRACT, tenant: str = "firm_cr", title: str = "采购合同"):
    from app.services.contract_review import ContractReviewService

    return await ContractReviewService(db, router=router).review(
        title=title, source_text=text, tenant_id=tenant, user_id=1
    )


async def _decisions(db, run_id: int):
    from sqlalchemy import select

    from app.models.ai_run import AiDecision

    return list(
        (
            await db.execute(select(AiDecision).where(AiDecision.run_id == run_id).order_by(AiDecision.id))
        ).scalars()
    )


async def _run_row(db, cr):
    from app.models.ai_run import AiRun

    return await db.get(AiRun, cr.run_id)


# ═══════════════════════ 1. 条款切分 ═══════════════════════


def test_split_clauses_covers_full_text_exactly():
    """覆盖性是硬契约：拼接必须等于原文，且区间首尾相接。"""
    from app.services.contract_review import split_clauses

    clauses = split_clauses(CONTRACT)
    assert "".join(c.text for c in clauses) == CONTRACT
    assert clauses[0].char_start == 0
    assert clauses[-1].char_end == len(CONTRACT)
    for i, c in enumerate(clauses):
        assert c.index == i
        assert CONTRACT[c.char_start : c.char_end] == c.text
        if i:
            assert clauses[i - 1].char_end == c.char_start  # 无空洞、无重叠


def test_split_clauses_extracts_clause_no():
    from app.services.contract_review import split_clauses

    clauses = split_clauses(CONTRACT)
    nos = {c.clause_no for c in clauses}
    assert {"第一条", "第二条", "第三条", "第四条"} <= nos
    # 前言段无条款编号 ⇒ None（取不到就不猜）
    assert clauses[0].clause_no is None


def test_split_clauses_refines_long_paragraph_without_losing_text():
    """无「第X条」标记的超长文本也必须细分，且不丢字。"""
    from app.services.contract_review import split_clauses

    text = "\n".join(f"第 {i} 行内容" + "补" * 60 for i in range(40))
    clauses = split_clauses(text)
    assert len(clauses) > 1
    assert "".join(c.text for c in clauses) == text


def test_split_clauses_handles_empty_text():
    from app.services.contract_review import split_clauses

    assert split_clauses("") == []


# ═══════════════════════ 2. 定位逐字一致 ═══════════════════════


async def test_finding_original_is_exact_substring(db):
    """多条款 + 首尾边界：`original` 必须逐字等于原文区间。"""
    from app.services.contract_review import split_clauses

    clauses = split_clauses(CONTRACT)
    i_pay = _index_of(clauses, "三十日内支付")
    i_penalty = _index_of(clauses, "违约金")
    i_venue = _index_of(clauses, "人民法院管辖")

    router = FakeRouter(
        text=_payload(
            {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
             "consequence": "标题段信息不完整", "suggestion_text": "补全合同名称与签署主体。"},
            {"clause_index": i_pay, "dimension": "付款与结算", "risk_level": "MEDIUM",
             "consequence": "付款期限可能被拖延", "suggestion_text": "乙方应于收货后 15 日内付款。"},
            {"clause_index": i_penalty, "dimension": "违约责任", "risk_level": "HIGH",
             "consequence": "违约金可能被法院调减",
             "suggestion_text": "违约金为合同总价的 20%。",
             "cited_law_name": "中华人民共和国民法典", "cited_article_no": "第577条"},
            {"clause_index": i_venue, "dimension": "争议解决", "risk_level": "MEDIUM",
             "consequence": "异地诉讼成本高", "suggestion_text": "提交乙方所在地人民法院管辖。"},
        )
    )
    cr = await _review(db, router)
    assert len(cr.findings) == 4

    for f in cr.findings:
        assert f["original"] == CONTRACT[f["char_start"] : f["char_end"]]
        assert f["original"] == CONTRACT[CONTRACT.index(f["original"]) : CONTRACT.index(f["original"]) + len(f["original"])]
        # 兼容旧键
        assert f["clause"] == f["original"]
        assert f["issue"] == f["consequence"]
        assert f["suggestion"] == f["suggestion_text"]

    # 边界：首段与末段都被正确定位
    assert cr.findings[0]["char_start"] == 0
    assert cr.findings[-1]["char_end"] == len(CONTRACT)
    assert cr.coverage == {"total_clauses": len(clauses), "reviewed_clauses": len(clauses), "reviewed_ratio": 1.0}


async def test_clause_no_filled_from_local_split(db):
    from app.services.contract_review import split_clauses

    clauses = split_clauses(CONTRACT)
    i_penalty = _index_of(clauses, "违约金")
    router = FakeRouter(
        text=_payload(
            {"clause_index": i_penalty, "dimension": "违约责任", "risk_level": "HIGH",
             "consequence": "过高违约金", "suggestion_text": "调整为 20%。"}
        )
    )
    cr = await _review(db, router)
    assert cr.findings[0]["clause_no"] == "第三条"


# ═══════════════════════ 3. 依据分层（禁止硬凑）═══════════════════════


async def test_statute_citation_must_hit_library(db):
    """库内命中 ⇒ statute + 完整 citation；库内没有 ⇒ experience + citation=None。"""
    from app.services.contract_review import split_clauses

    clauses = split_clauses(CONTRACT)
    i_penalty = _index_of(clauses, "违约金")
    router = FakeRouter(
        text=_payload(
            {"clause_index": i_penalty, "dimension": "违约责任", "risk_level": "HIGH",
             "consequence": "违约金过高可能被调减", "suggestion_text": "调整为合同总价的 20%。",
             "cited_law_name": "中华人民共和国民法典", "cited_article_no": "第577条"},
            {"clause_index": i_penalty, "dimension": "解除终止", "risk_level": "MEDIUM",
             "consequence": "解除条件不明", "suggestion_text": "明确约定解除条件与通知程序。",
             "cited_law_name": "中华人民共和国民法典", "cited_article_no": "第9999条"},
        )
    )
    cr = await _review(db, router)
    by_dim = {f["dimension"]: f for f in cr.findings}

    hit = by_dim["违约责任"]
    assert hit["basis_type"] == "statute"
    assert hit["citation"] is not None
    assert hit["citation"]["law_name"] == "中华人民共和国民法典"
    assert hit["citation"]["article_no"] == "第577条"
    assert hit["citation"]["content"]  # 必须带上可展示的原文

    miss = by_dim["解除终止"]
    assert miss["basis_type"] == "experience"
    assert miss["citation"] is None


async def test_model_without_citation_is_experience(db):
    router = FakeRouter(
        text=_payload(
            {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
             "consequence": "信息不完整", "suggestion_text": "补全主体信息。"}
        )
    )
    cr = await _review(db, router)
    assert cr.findings[0]["basis_type"] == "experience"
    assert cr.findings[0]["citation"] is None


async def test_validate_citations_is_not_reused(db):
    """显式禁令回归：结论性内容无引用时**不得**抛 CITATION_MISSING。

    `citation_service.validate_citations` 的语义是「引用必需」，与合同审查
    默认值相反——本库 12 条法条对商事合同几乎无可引用，一旦有人「顺手统一」
    把它接过来，**每一次审查都会生成失败**，且失败方向是「更严」，看起来像好事。
    """
    import app.services.contract_review as cr_module

    assert not hasattr(cr_module, "validate_citations")

    router = FakeRouter(
        text=_payload(
            {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "HIGH",
             "consequence": "无任何法条依据的风险", "suggestion_text": "补全。"}
        )
    )
    cr = await _review(db, router)  # 不抛异常即通过
    assert cr.status.value == "success"
    assert cr.findings[0]["basis_type"] == "experience"


# ═══════════════════════ 4. 零发现不放行 ═══════════════════════


async def test_llm_success_without_findings_is_complete_no_risk(db):
    """真实模型通读且无风险 ⇒ `complete_no_risk`，此时 NONE 才合法。"""
    router = FakeRouter(text=_payload())
    cr = await _review(db, router)
    assert cr.source.value == "llm"
    assert cr.status.value == "success"
    assert cr.analysis_status.value == "complete_no_risk"
    assert cr.overall_risk.value == "NONE"
    assert cr.findings == []


async def test_llm_failure_without_rule_hits_is_prescreen_only_and_not_none(db):
    """**阻断级缺陷回归**：不含任何关键词 + LLM 失败 ⇒ 不得是 NONE。"""
    plain = "合作协议\n\n第一条 双方就合作事宜达成一致。\n第二条 本协议自签署之日起生效。\n"
    router = FakeRouter(exc=RuntimeError("provider down"))
    cr = await _review(db, router, text=plain)

    assert cr.source.value == "rule"
    assert cr.status.value == "degraded"
    assert cr.analysis_status.value == "prescreen_only"
    assert cr.overall_risk.value != "NONE"
    assert cr.findings == []
    assert "未命中规则库不等于合同安全" in cr.summary
    # 0 条建议时禁止输出「建议按修改建议逐条修订」
    assert "逐条修订" not in cr.summary
    assert cr.coverage["reviewed_clauses"] == 0
    assert cr.coverage["reviewed_ratio"] == 0.0


async def test_llm_failure_with_rule_hits_returns_located_findings(db):
    """降级兜底：规则命中项必须**可定位**，且依据只能是 experience。"""
    router = FakeRouter(exc=RuntimeError("provider down"))
    cr = await _review(db, router)

    assert cr.status.value == "degraded"
    assert cr.analysis_status.value == "prescreen_only"
    assert cr.findings, "规则命中时必须给出兜底条目，否则降级产出毫无价值"
    for f in cr.findings:
        assert f["original"] == CONTRACT[f["char_start"] : f["char_end"]]
        assert f["basis_type"] == "experience"
        assert f["citation"] is None
    assert cr.overall_risk.value != "NONE"


async def test_empty_text_fails_without_conclusion(db):
    router = FakeRouter(text=_payload())
    cr = await _review(db, router, text="   \n  ")
    assert cr.status.value == "failed"
    assert cr.findings == []
    assert cr.overall_risk.value != "NONE"
    assert router.calls == [], "空正文不得调用模型（否则空合同也会被计费）"


# ═══════════════════════ 5. 降级链与解析容错 ═══════════════════════


async def test_provider_exception_degrades_without_raising(db):
    router = FakeRouter(exc=RuntimeError("boom"))
    cr = await _review(db, router)  # 不抛 500
    assert (cr.source.value, cr.status.value) == ("rule", "degraded")


async def test_timeout_reason_recorded(db):
    router = FakeRouter(exc=httpx.TimeoutException("slow"))
    cr = await _review(db, router)
    assert cr.status.value == "degraded"
    decisions = await _decisions(db, cr.run_id)
    degrade = [d for d in decisions if d.stage == "degrade"]
    assert degrade and degrade[0].payload["reason"] == "timeout"


async def test_mock_provider_is_degraded_and_labelled_mock(db):
    router = FakeRouter(is_mock=True, model="mock-strong")
    cr = await _review(db, router)
    assert (cr.source.value, cr.status.value) == ("mock", "degraded")
    assert cr.is_mock is True
    decisions = await _decisions(db, cr.run_id)
    degrade = [d for d in decisions if d.stage == "degrade"]
    assert degrade and degrade[0].payload["reason"] == "mock"


async def test_json_fence_is_stripped(db):
    fenced = "```json\n" + _payload(
        {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
         "consequence": "信息不完整", "suggestion_text": "补全。"}
    ) + "\n```"
    router = FakeRouter(text=fenced)
    cr = await _review(db, router)
    assert cr.status.value == "success"
    assert len(cr.findings) == 1


async def test_unparseable_output_degrades(db):
    router = FakeRouter(text="抱歉，我无法完成该请求。")
    cr = await _review(db, router)
    assert (cr.source.value, cr.status.value) == ("rule", "degraded")
    decisions = await _decisions(db, cr.run_id)
    degrade = [d for d in decisions if d.stage == "degrade"]
    assert degrade and degrade[0].payload["reason"] == "rule_fallback"


async def test_sensitive_flag_is_passed_through(db):
    """`sensitive` 必须是配置值（默认 True），**不得为跑通而偷偷改成 False**。"""
    from app.config import settings

    router = FakeRouter(text=_payload())
    await _review(db, router)
    assert router.calls[0]["sensitive"] is True
    assert settings.CONTRACT_REVIEW_SENSITIVE is True


async def test_task_type_is_contract_review_strong_tier():
    from app.ai.router import TIER_FOR_TASK, ModelTier, TaskType

    assert TIER_FOR_TASK[TaskType.CONTRACT_REVIEW] == ModelTier.STRONG


# ═══════════════════════ 6. 越界与丢弃 ═══════════════════════


async def test_out_of_range_clause_index_dropped_and_recorded(db):
    router = FakeRouter(
        text=_payload(
            {"clause_index": 999, "dimension": "违约责任", "risk_level": "HIGH",
             "consequence": "越界条目", "suggestion_text": "不该被采纳。"},
            {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
             "consequence": "信息不完整", "suggestion_text": "补全。"},
        )
    )
    cr = await _review(db, router)
    assert len(cr.findings) == 1
    assert cr.findings[0]["dimension"] == "效力瑕疵"

    decisions = await _decisions(db, cr.run_id)
    grade = [d for d in decisions if d.stage == "grade"]
    assert grade and grade[0].payload["dropped"] == 1


async def test_incomplete_finding_is_dropped(db):
    """缺 `suggestion_text` 的条目不可交付（CR-04 要求非空），必须丢弃。"""
    router = FakeRouter(
        text=_payload(
            {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
             "consequence": "只有后果没有改写文本", "suggestion_text": ""},
        )
    )
    cr = await _review(db, router)
    assert cr.findings == []
    decisions = await _decisions(db, cr.run_id)
    grade = [d for d in decisions if d.stage == "grade"]
    assert grade and grade[0].payload["dropped"] == 1


# ═══════════════════════ 7. 留痕 ═══════════════════════


async def test_ai_run_and_decisions_written(db):
    router = FakeRouter(
        text=_payload(
            {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
             "consequence": "信息不完整", "suggestion_text": "补全。"}
        )
    )
    cr = await _review(db, router)
    run = await _run_row(db, cr)
    assert run is not None
    assert run.pipeline == "CONTRACT_REVIEW"
    assert run.ref_type == "contract_review"
    assert run.ref_id == cr.id
    assert run.model_name == "fake-strong"
    assert run.model_tier == "strong"
    assert run.prompt_tokens == 1200 and run.completion_tokens == 300
    assert run.duration_ms is not None
    assert run.cost_cents is not None and run.cost_cents > 0

    decisions = await _decisions(db, cr.run_id)
    assert len(decisions) >= 2
    stages = {d.stage for d in decisions}
    assert {"prescreen", "read", "cite_validate", "grade"} <= stages

    # ContractReview 自身也要能还原（不必 join ai_runs）
    assert cr.run_id == run.id
    assert cr.model_name == "fake-strong"
    assert cr.cost_cents is not None
    assert cr.duration_ms is not None


async def test_ai_run_written_even_when_failed(db):
    router = FakeRouter(exc=RuntimeError("boom"))
    cr = await _review(db, router)
    run = await _run_row(db, cr)
    assert run is not None
    assert run.error_message
    assert cr.cost_cents == 0.0


# ═══════════════════════ 8. 计费门控（本轮核心）═══════════════════════


class _FakeUser:
    def __init__(self, uid: int = 1, tenant: str = "firm_cr"):
        self.id = uid
        self.role = "LAWYER"
        self.tenant_id = tenant


@pytest.fixture
def billing_probe(monkeypatch):
    """探针：记录 `consume_atomic` 的调用次数。

    断言「未被调用」而不是「调用后回滚」——回滚依赖后续代码不出错，
    而计费诚实性需要的是**不依赖任何后续步骤**的硬保证。
    """
    from app.services.billing_service import BillingService

    calls: list[dict] = []

    async def _probe(self, **kwargs):
        calls.append(kwargs)
        return {"used": 1, "limit": 100, "remaining": 99, "exceeded": False, "work_order_id": None}

    monkeypatch.setattr(BillingService, "consume_atomic", _probe)
    return calls


@pytest.fixture
def no_moderation(monkeypatch):
    """隔离内容审核：本组用例只验证计费门控。"""
    from app.config import settings

    monkeypatch.setattr(settings, "MODERATION_CHECK_INPUT", False)


@pytest.fixture
def inject_router(monkeypatch):
    def _install(router):
        monkeypatch.setattr("app.services.contract_review.ModelRouter", lambda *a, **k: router)
        return router

    return _install


async def _call_endpoint(db, *, text: str = CONTRACT, tenant: str = "firm_cr"):
    from app.api.v1.documents import review_contract
    from app.core.deps import TenantContext
    from app.schemas.document import ContractReviewRequest

    ctx = TenantContext(tenant_id=tenant, user_id=1, role="LAWYER")
    return await review_contract(
        payload=ContractReviewRequest(title="采购合同", source_text=text),
        db=db,
        ctx=ctx,
        user=_FakeUser(tenant=tenant),
    )


async def test_degraded_review_is_not_charged(db, billing_probe, no_moderation, inject_router):
    inject_router(FakeRouter(exc=RuntimeError("provider down")))
    res = await _call_endpoint(db)

    assert res["success"] is True
    data = res["data"]
    assert data["source"] == "rule"
    assert data["status"] == "degraded"
    assert "usage" not in data, "降级产出不得返回 usage 键"
    assert billing_probe == [], "降级路径 consume_atomic 必须一次都不被调用"

    from app.core.metrics import metrics

    rendered = metrics.render()
    assert 'nlaw_contract_review_charged_total{source="rule"}' not in rendered
    assert 'nlaw_contract_review_total{source="rule",status="degraded"} 1' in rendered


async def test_mock_review_is_not_charged(db, billing_probe, no_moderation, inject_router):
    inject_router(FakeRouter(is_mock=True))
    res = await _call_endpoint(db)
    assert res["data"]["source"] == "mock"
    assert "usage" not in res["data"]
    assert billing_probe == []

    from app.core.metrics import metrics

    assert 'nlaw_contract_review_charged_total{source="mock"}' not in metrics.render()


async def test_failed_review_is_not_charged(db, billing_probe, no_moderation, inject_router):
    inject_router(FakeRouter(text=_payload()))
    res = await _call_endpoint(db, text="  ")
    assert res["data"]["status"] == "failed"
    assert res["data"]["findings"] == []
    assert "usage" not in res["data"]
    assert billing_probe == []


async def test_configuration_error_fails_without_charging_or_leaking(
    db, billing_probe, no_moderation, inject_router
):
    """生产无 Key（含 sensitive 走 LOCAL 未配）⇒ failed + 不计费 + 原因不泄露基础设施。

    面向用户的 `error_message` 与运维视角的 `AiRun.error_message` 必须分开：
    前者前端会原样展示，不能出现环境变量名 / 内部地址。
    """
    from app.core.errors import ConfigurationError

    inject_router(
        FakeRouter(
            exc=ConfigurationError(
                "生产环境未配置 local 档模型（LLM_LOCAL_API_KEY 为空），已拒绝以 Mock 结果代替真实模型输出",
                details={"tier": "local", "env_key": "LLM_LOCAL_API_KEY"},
            )
        )
    )
    res = await _call_endpoint(db)
    data = res["data"]

    assert data["status"] == "failed"
    assert data["source"] == "rule"
    assert data["findings"] == []
    assert data["overall_risk"] != "NONE"
    assert "usage" not in data
    assert billing_probe == []

    assert data["error_message"] == "服务端未配置可用的审查模型，请联系管理员或稍后重试。"
    assert "LLM_LOCAL_API_KEY" not in data["error_message"]
    assert "local" not in data["error_message"]


async def test_ai_run_keeps_raw_configuration_error(db, billing_probe, no_moderation, inject_router):
    """上一条的补充：原始异常必须落在 AiRun.error_message（用户文案与运维原因不互斥）。"""
    from app.core.errors import ConfigurationError

    inject_router(
        FakeRouter(
            exc=ConfigurationError(
                "生产环境未配置 local 档模型（LLM_LOCAL_API_KEY 为空）",
                details={"env_key": "LLM_LOCAL_API_KEY"},
            )
        )
    )
    res = await _call_endpoint(db)

    from sqlalchemy import select

    from app.models.ai_run import AiRun

    run = (
        await db.execute(
            select(AiRun).where(AiRun.ref_id == res["data"]["id"], AiRun.ref_type == "contract_review")
        )
    ).scalars().first()
    assert run is not None
    assert "LLM_LOCAL_API_KEY" in (run.error_message or "")


async def test_error_message_absent_when_not_failed(db, billing_probe, no_moderation, inject_router):
    """`error_message` 与 `usage` 同一约定：键不存在表示「不适用」。"""
    inject_router(FakeRouter(text=_payload()))
    ok_res = await _call_endpoint(db)
    assert ok_res["data"]["status"] == "success"
    assert "error_message" not in ok_res["data"]

    inject_router(FakeRouter(exc=RuntimeError("boom")))
    degraded = await _call_endpoint(db)
    assert degraded["data"]["status"] == "degraded"
    assert "error_message" not in degraded["data"]
    # 降级原因已写在 summary 里，用户不会「看不出为什么」
    assert "模型服务不可用" in degraded["data"]["summary"]


async def test_exceeded_quota_still_reports_full_charge(db, no_moderation, inject_router, monkeypatch):
    """额度耗尽转工单时**钱仍然要付**，`charged` 不得因此变成 0。

    前端直接读 `charged/100` 渲染金额，若这里返回 0，用户会以为本次免费。
    """
    from app.services.billing_service import BillingService

    async def _exceeded(self, **kwargs):
        return {"used": 100, "limit": 100, "remaining": 0, "exceeded": True, "work_order_id": 7}

    monkeypatch.setattr(BillingService, "consume_atomic", _exceeded)
    inject_router(FakeRouter(text=_payload()))

    res = await _call_endpoint(db)
    assert res["data"]["usage"]["charged"] == 9900
    assert res["data"]["usage"]["exceeded"] is True
    assert res["data"]["usage"]["work_order_id"] == 7


async def test_every_finding_has_adoptable_suggestion_text(db, billing_probe, no_moderation, inject_router):
    """前端用「`suggestion_text` 非空」决定是否渲染一键采纳 ⇒ 缺它的条目必须被丢弃。

    否则会出现「有风险项但采纳按钮点不了」——而那恰恰是 CR-04 不可降级的理由。
    """
    inject_router(
        FakeRouter(
            text=_payload(
                {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
                 "consequence": "有后果无改写文本", "suggestion_text": "   "},
                {"clause_index": 3, "dimension": "违约责任", "risk_level": "HIGH",
                 "consequence": "违约金过高", "suggestion_text": "违约金为合同总价的 20%。"},
            )
        )
    )
    res = await _call_endpoint(db)
    findings = res["data"]["findings"]
    assert len(findings) == 1
    for f in findings:
        assert f["suggestion_text"].strip()
        assert f["original"] == CONTRACT[f["char_start"] : f["char_end"]]


async def test_llm_success_review_is_charged(db, billing_probe, no_moderation, inject_router):
    inject_router(
        FakeRouter(
            text=_payload(
                {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
                 "consequence": "信息不完整", "suggestion_text": "补全。"}
            )
        )
    )
    res = await _call_endpoint(db)
    data = res["data"]
    assert data["source"] == "llm" and data["status"] == "success"
    assert len(billing_probe) == 1
    assert billing_probe[0]["usage_type"].value == "CONTRACT_REVIEW"
    assert data["usage"]["charged"] == 9900

    from app.core.metrics import metrics

    assert 'nlaw_contract_review_charged_total{source="llm"} 1' in metrics.render()


async def test_response_contract_fields_present(db, billing_probe, no_moderation, inject_router):
    inject_router(
        FakeRouter(
            text=_payload(
                {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
                 "consequence": "信息不完整", "suggestion_text": "补全。"}
            )
        )
    )
    res = await _call_endpoint(db)
    data = res["data"]
    for key in (
        "id", "title", "source", "status", "analysis_status",
        "overall_risk", "coverage", "disclaimer", "summary", "findings",
    ):
        assert key in data, key
    f = data["findings"][0]
    for key in (
        "clause_index", "clause_no", "char_start", "char_end", "original",
        "dimension", "risk_level", "consequence", "suggestion_text",
        "basis_type", "citation", "clause", "issue", "suggestion",
    ):
        assert key in f, key
    assert data["disclaimer"] == "本结果为 AI 辅助意见，不构成法律意见。"


# ═══════════════════════ 9. 指标 ═══════════════════════


def test_contract_review_metrics_are_exported():
    """新增指标必须出现在 /metrics（render() 遍历实例属性自动收集）。"""
    from app.core.metrics import metrics

    metrics.contract_review_total.inc(("llm", "success"))
    metrics.contract_review_charged_total.inc(("llm",))
    metrics.contract_review_degraded_total.inc(("timeout",))
    metrics.contract_review_duration_milliseconds.observe(("llm",), 1234.0)

    rendered = metrics.render()
    for name in (
        "nlaw_contract_review_total",
        "nlaw_contract_review_charged_total",
        "nlaw_contract_review_degraded_total",
        "nlaw_contract_review_duration_milliseconds",
    ):
        assert f"# TYPE {name}" in rendered, name
    assert 'nlaw_contract_review_degraded_total{reason="timeout"} 1' in rendered
    assert 'nlaw_contract_review_duration_milliseconds_count{source="llm"} 1' in rendered


# ═══════════════════════ 10. 审计留痕 ═══════════════════════
#
# 改造前 `audit_logs` 里 `CONTRACT_REVIEW` 是 **0 条** —— 既有审查根本无法审计。
# 本轮新增了审计写入，属新行为，必须有测试守住。


async def _audit_rows(db, review_id: int):
    """取**本次审查**对应的审计行。

    `db` 夹具在整个文件内是同一个会话，前面用例写下的审计行不会消失，
    因此必须按 `resource_id` 过滤，不能靠「表里只有一条」来断言。
    """
    from sqlalchemy import select

    from app.core.audit import AuditAction
    from app.models.audit_log import AuditLog

    return (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.CONTRACT_REVIEW,
                    AuditLog.resource_id == review_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def test_audit_log_written_on_success(db, no_moderation, inject_router):
    """成功路径必须留痕，且**不得落合同原文**（合同通常含商业秘密）。

    审计要能回答「这 99 元收在什么产出上」——`source` / `status` / `charged`
    三者缺一，事后就完全无法区分「模型审查」与「规则预筛」。
    """
    inject_router(
        FakeRouter(
            text=_payload(
                {"clause_index": 0, "dimension": "效力瑕疵", "risk_level": "LOW",
                 "consequence": "信息不完整", "suggestion_text": "补全。"}
            )
        )
    )
    res = await _call_endpoint(db)
    data = res["data"]
    assert data["source"] == "llm" and data["status"] == "success"

    rows = await _audit_rows(db, data["id"])
    assert len(rows) == 1, "本次成功审查必须且只写一条审计"

    row = rows[0]
    assert row.resource_type == "contract_review"
    assert row.tenant_id == "firm_cr"

    detail = row.detail or {}
    assert detail["source"] == "llm"
    assert detail["status"] == "success"
    assert detail["charged"] is True
    assert detail["finding_count"] == len(data["findings"])
    assert detail["overall_risk"] == data["overall_risk"]

    # 负面断言（最重要）：审计只许记元信息，合同原文不得进入 detail。
    # 用原文里独有的哨兵串断言其**不出现**——合同常含商业秘密。
    assert "某某科技有限公司" in CONTRACT, "哨兵串必须真实存在于测试合同里，否则断言是空的"
    assert "某某科技有限公司" not in json.dumps(detail, ensure_ascii=False), (
        "审计 detail 里出现了合同原文——商业秘密泄露"
    )


async def test_audit_log_written_on_degraded(db, no_moderation, inject_router):
    """降级路径同样留痕，且 `charged=False` —— 审计要能证明「这笔钱没收」。"""
    inject_router(FakeRouter(exc=RuntimeError("provider down")))
    res = await _call_endpoint(db)
    data = res["data"]
    assert data["source"] == "rule" and data["status"] == "degraded"

    rows = await _audit_rows(db, data["id"])
    assert len(rows) == 1, "降级路径同样必须留痕"

    detail = rows[0].detail or {}
    assert detail["charged"] is False
    assert detail["source"] == "rule"
    assert detail["status"] == "degraded"
