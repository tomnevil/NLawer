"""AI 辅助办案：接单后异步生成六段式案件分析（PRD 5.3）。

六段式：案件摘要 / 法律分析 / 相关法条 / 类案参考 / 初步建议 / 待补充信息。
借鉴 AIAcquisition：一次运行写一行 `AiRun`，每步决策写一行 `AiDecision`，
输出落库前经 `citation_service` 强校验（引用缺失视为生成失败，触发 Job 重试）。
"""
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.router import ModelRouter, TaskType
from app.core.errors import ErrorCode, NotFoundError
from app.models.ai_run import AiDecision, AiRun
from app.models.analysis import CaseAnalysis, CaseAnalysisVersion
from app.models.case import Case
from app.models.citation import CasePrecedent, LawArticle
from app.models.enums import JobStatus, ReviewStatus
from app.services.citation_service import validate_citations
from app.workflows.forced_review import detect_forced_review, highest_level

router = ModelRouter()


class CaseCopilot:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 主流程 ----------------
    async def generate(
        self,
        case_id: int,
        *,
        trigger: str = "SYSTEM_AUTO",
        note: Optional[str] = None,
        run: Optional[AiRun] = None,
    ) -> CaseAnalysis:
        case = await self.db.get(Case, case_id)
        if case is None:
            raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)

        # --- 先落库：分析行 + 运行行（崩溃不丢输入，对齐 Job 框架约定）---
        # 已存在则复用并升版本（律师迭代场景），否则新建
        existing = (
            (
                await self.db.execute(
                    select(CaseAnalysis)
                    .where(CaseAnalysis.case_id == case_id)
                    .order_by(CaseAnalysis.id.desc())
                )
            ).scalars().first()
        )
        if existing is not None:
            analysis = existing
            analysis.version = (analysis.version or 1) + 1
            analysis.status = ReviewStatus.DRAFT
        else:
            analysis = CaseAnalysis(case_id=case_id, tenant_id=case.tenant_id, status=ReviewStatus.DRAFT)
            self.db.add(analysis)
            await self.db.flush()

        if run is None:
            run = AiRun(
                job_id=None,
                pipeline="CASE_ANALYSIS",
                ref_type="case",
                ref_id=case_id,
                trigger=trigger,
                tenant_id=case.tenant_id,
            )
            self.db.add(run)
            await self.db.flush()
        analysis.run_id = run.id
        run.started_at = run.started_at or _utcnow()

        t0 = time.perf_counter()
        # --- 阶段 1：检索 ---
        laws, precedents = await self._retrieve(case)
        await self._decide(run, "retrieve", "PROCEED", f"命中法条{len(laws)}条、类案{len(precedents)}个",
                           {"laws": len(laws), "cases": len(precedents)}, 0)

        # --- 阶段 2：生成六段式 ---
        sections = self._build_sections(case, laws, precedents, note=note)
        result = await router.complete(TaskType.COPILOT, _prompt(case, laws, precedents, note))
        run.model_tier = result.tier
        run.model_name = result.model
        run.is_mock = int(result.is_mock)
        run.prompt_tokens = result.prompt_tokens
        run.completion_tokens = result.completion_tokens
        await self._decide(run, "generate", "PROCEED", f"模型 {result.model}（mock={result.is_mock}）",
                           {"is_mock": result.is_mock}, int((time.perf_counter() - t0) * 1000))

        # --- 阶段 3：强制复核命中检查 ---
        hits = detect_forced_review(case=case)
        required_level = highest_level(hits)
        analysis.forced_hits = [{"rule": h.rule, "reason": h.reason, "level": h.level.value} for h in hits]
        analysis.required_level = required_level
        await self._decide(run, "forced_review_check", "PROCEED" if not hits else "ESCALATE",
                           f"命中{len(hits)}条强制复核规则", {"hits": analysis.forced_hits}, 0)

        # --- 阶段 4：引用溯源强校验（PRD：缺失视为生成失败）---
        citation_ids = [law["citation_id"] for law in sections["related_laws"]] + [
            c["citation_id"] for c in sections["similar_cases"]
        ]
        validate_citations(sections, citation_ids)
        await self._decide(run, "citation_validate", "PROCEED", f"引用 {len(citation_ids)} 条校验通过",
                           {"citation_ids": citation_ids}, 0)

        # --- 落库 ---
        analysis.summary = sections["summary"]
        analysis.legal_analysis = sections["legal_analysis"]
        analysis.related_laws = sections["related_laws"]
        analysis.similar_cases = sections["similar_cases"]
        analysis.suggestions = sections["suggestions"]
        analysis.missing_info = sections["missing_info"]
        analysis.ai_generated = 1
        if note:
            analysis.iteration_notes = list(analysis.iteration_notes or []) + [note]

        self.db.add(
            CaseAnalysisVersion(
                tenant_id=case.tenant_id,
                analysis_id=analysis.id,
                version=analysis.version,
                snapshot=sections,
                change_note=note or "AI 初稿",
            )
        )
        run.stage_counts = {"laws": len(laws), "cases": len(precedents)}
        run.duration_ms = int((time.perf_counter() - t0) * 1000)
        run.finish(JobStatus.COMPLETED)
        return analysis

    # ---------------- 检索 ----------------
    async def _retrieve(self, case: Case) -> tuple[list[LawArticle], list[CasePrecedent]]:
        laws = list((await self.db.execute(select(LawArticle).where(LawArticle.tenant_id == "platform"))).scalars().all())
        precedents = list(
            (await self.db.execute(select(CasePrecedent).where(CasePrecedent.tenant_id == "platform")))
            .scalars()
            .all()
        )
        dt = case.dispute_type or ""

        laws = sorted(laws, key=lambda a: -_score(f"{a.law_name}{a.content}{a.tags or ''}", dt, case.summary))
        precedents = sorted(precedents, key=lambda c: -_score(f"{c.title}{c.holding}{c.dispute_type or ''}", dt, case.summary))
        return laws[:5], precedents[:3]

    # ---------------- 六段式生成 ----------------
    def _build_sections(
        self, case: Case, laws: list[LawArticle], precedents: list[CasePrecedent], *, note: Optional[str]
    ) -> dict[str, Any]:
        amount = f"{case.claim_amount:,.0f} 元" if case.claim_amount else "未填写"
        summary = (
            f"【当事人】{case.party_a or '待补充'}\n"
            f"【相对方】{case.party_b or '待补充'}\n"
            f"【纠纷类型】{case.dispute_type or '待明确'}\n"
            f"【争议焦点】{case.focus or (case.summary or '')[:120] or '待明确'}\n"
            f"【标的额】{amount}\n"
            f"【案件等级】{case.grade.value} 级"
        )
        legal_analysis = self._legal_analysis(case, laws)
        related_laws = [
            {
                "law_name": a.law_name,
                "article_no": a.article_no,
                "excerpt": (a.content or "")[:200],
                "citation_id": a.id,
            }
            for a in laws
        ]
        similar_cases = [
            {
                "case_no": c.case_no,
                "title": c.title,
                "court": c.court,
                "holding": (c.holding or "")[:200],
                "citation_id": c.id,
            }
            for c in precedents
        ]
        suggestions = [
            {"path": "协商解决", "pros": "成本低、周期短", "cons": "对方不配合则无强制力"},
            {"path": "申请调解/仲裁", "pros": "程序较快、费用较低", "cons": "需双方同意或存在仲裁条款"},
            {"path": "提起诉讼", "pros": "有强制执行力", "cons": "周期长、成本较高"},
        ]
        missing_info = [
            {"item": "书面合同/劳动关系证明", "reason": "确认法律关系成立的基础", "priority": 1},
            {"item": "支付凭证或工资流水", "reason": "核算具体金额", "priority": 1},
            {"item": "沟通记录（聊天/邮件）", "reason": "证明催告与对方违约事实", "priority": 2},
            {"item": "对方主体信息", "reason": "确定被告/被申请人", "priority": 2},
        ]
        if note:
            missing_info.insert(0, {"item": f"按律师指令补充：{note}", "reason": "迭代要求", "priority": 1})

        return {
            "summary": summary,
            "legal_analysis": legal_analysis,
            "related_laws": related_laws,
            "similar_cases": similar_cases,
            "suggestions": suggestions,
            "missing_info": missing_info,
        }

    def _legal_analysis(self, case: Case, laws: list[LawArticle]) -> str:
        base = case.dispute_type or "本案纠纷"
        lines = [f"本案系{base}，核心争议在于权利义务的成立与违约责任（或赔偿责任）的承担。"]
        for a in laws[:3]:
            lines.append(f"- 依据《{a.law_name}{a.article_no}》：{(a.content or '')[:120]}…")
        lines.append("综合上述规定，建议先固定证据、明确请求权基础，再选择协商或诉讼路径。")
        return "\n".join(lines)

    # ---------------- 决策审计 ----------------
    async def _decide(
        self, run: AiRun, stage: str, decision: str, reason: str, payload: dict, duration_ms: int
    ) -> None:
        self.db.add(
            AiDecision(
                tenant_id=run.tenant_id,
                run_id=run.id,
                stage=stage,
                decision=decision,
                reason=reason,
                payload=payload,
                duration_ms=duration_ms,
            )
        )


def _score(blob: str, dispute_type: str, summary: Optional[str]) -> int:
    score = 0
    if dispute_type and dispute_type in blob:
        score += 3
    for kw in ("二倍工资", "经济补偿", "违法解除", "工伤", "违约金", "解除", "赔偿"):
        if kw in blob and (not summary or kw in summary):
            score += 1
    return score


def _prompt(case: Case, laws: list[LawArticle], precedents: list[CasePrecedent], note: Optional[str]) -> str:
    law_txt = "\n".join(f"{a.law_name}{a.article_no}: {a.content}" for a in laws[:3])
    case_txt = "\n".join(f"{c.case_no} {c.title}" for c in precedents[:2])
    return (
        f"请按六段式输出案件分析。\n案件：{case.title}\n纠纷类型：{case.dispute_type}\n"
        f"摘要：{case.summary}\n相关法条：\n{law_txt}\n类案：\n{case_txt}\n"
        f"律师指令：{note or '无'}"
    )


def _utcnow():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc)
