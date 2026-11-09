"""AI 辅助办案：接单后异步生成六段式案件分析（PRD 5.3）。

六段式：案件摘要 / 法律分析 / 相关法条 / 类案参考 / 初步建议 / 待补充信息。
借鉴 AIAcquisition：一次运行写一行 `AiRun`，每步决策写一行 `AiDecision`，
输出落库前经 `citation_service` 强校验（引用缺失视为生成失败，触发 Job 重试）。

## N1（2026-11-09）：模型输出必须真正进入六段式

旧实现先调 `_build_sections()` 拼出全套模板，再去调模型，**模型返回的文本
只用于记 token、从未参与落库**——落库内容是模板，`AiRun` 却标 COMPLETED，
审计上表现为「模型成功」，实际律师看到的全是硬编码话术。

现在的契约：

- `_build_sections(..., model_text=...)` 优先解析模型 JSON，四段文本
  （摘要/法律分析/建议/待补充）**逐字来自模型**；相关法条与类案仍由检索结果
  补齐——它们必须带 `citation_id`，由模型自由生成会引入无法溯源的编造引用。
- 只有模型调用异常或输出无法解析时才回落到模板，且回落**必须可识别**：
  `AiRun.status=FAILED` + `error_message` 以 `TEMPLATE_FALLBACK:` 开头、
  `stage_counts.sections_source="template"`，并留一条 `decision=FALLBACK` 的
  `AiDecision`。绝不静默标成功。`ai_generated` 不参与兜底标记——它在律师端
  表示「律师完全手写」，置 0 会把模板话术误归为人工确认。
"""
import json
import re
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
        # 先调模型，再据其输出建 sections。模型异常或输出不可解析时由
        # `_build_sections` 回落模板，并把回落原因带回，供下方显式标记。
        model_text: Optional[str] = None
        model_error: Optional[str] = None
        result = None
        try:
            result = await router.complete(TaskType.COPILOT, _prompt(case, laws, precedents, note))
            model_text = result.text
        except Exception as exc:  # 模型调用失败：记录原因，走模板兜底
            model_error = f"模型调用失败：{exc}"

        if result is not None:
            run.model_tier = result.tier
            run.model_name = result.model
            run.is_mock = int(result.is_mock)
            run.prompt_tokens = result.prompt_tokens
            run.completion_tokens = result.completion_tokens

        sections, sections_source, fallback_reason = self._build_sections(
            case, laws, precedents, model_text=model_text, note=note
        )
        if model_error:
            fallback_reason = model_error
        if sections_source == "model":
            await self._decide(
                run, "generate", "PROCEED",
                f"模型 {run.model_name}（mock={run.is_mock}）输出已解析为六段式",
                {"is_mock": run.is_mock, "sections_source": "model"},
                int((time.perf_counter() - t0) * 1000),
            )
        else:
            # 兜底不是成功：run 标 FAILED、分析标非模型生成，审计里可检索到原因
            await self._decide(
                run, "generate", "FALLBACK",
                f"模型输出不可用，已回落模板：{fallback_reason}",
                {"is_mock": run.is_mock, "sections_source": "template",
                 "fallback_reason": fallback_reason},
                int((time.perf_counter() - t0) * 1000),
            )

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
        # 注意：**不改** `ai_generated`。该字段在律师端页面表示「是否律师完全手写」
        # （false ⇒ 归入已确认侧）；模板兜底仍不是律师写的，置 0 会误导责任归属。
        # 兜底的可识别性由 AiRun 承载（status=FAILED + TEMPLATE_FALLBACK + stage_counts）。
        if note:
            analysis.iteration_notes = list(analysis.iteration_notes or []) + [note]

        self.db.add(
            CaseAnalysisVersion(
                tenant_id=case.tenant_id,
                analysis_id=analysis.id,
                version=analysis.version,
                snapshot=sections,
                change_note=note or ("AI 初稿" if sections_source == "model" else f"模板兜底：{fallback_reason}"),
            )
        )
        run.stage_counts = {
            "laws": len(laws),
            "cases": len(precedents),
            "sections_source": sections_source,
        }
        run.duration_ms = int((time.perf_counter() - t0) * 1000)
        if sections_source == "model":
            run.finish(JobStatus.COMPLETED)
        else:
            run.finish(JobStatus.FAILED, error=f"TEMPLATE_FALLBACK: {fallback_reason}")
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
        self,
        case: Case,
        laws: list[LawArticle],
        precedents: list[CasePrecedent],
        *,
        model_text: Optional[str] = None,
        note: Optional[str] = None,
    ) -> tuple[dict[str, Any], str, Optional[str]]:
        """构建六段式。

        返回 `(sections, source, fallback_reason)`：

        - `source="model"`：摘要/法律分析/建议/待补充四项逐字来自模型 JSON；
        - `source="template"`：模型不可用或输出不可解析，落回本地模板，
          `fallback_reason` 说明原因，调用方必须据此把运行标记为失败。

        相关法条与类案始终取自检索结果（带库内 `citation_id`），不采信模型
        自行编造的法条/案号——那会让引用溯源面板展示无法验证的引用。
        """
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

        template = {
            "summary": summary,
            "legal_analysis": legal_analysis,
            "related_laws": related_laws,
            "similar_cases": similar_cases,
            "suggestions": suggestions,
            "missing_info": missing_info,
        }

        if not model_text or not (model_text or "").strip():
            reason = "模型返回空文本（可能为 mock provider 或网关未响应）"
            return template, "template", reason

        parsed, reason = _parse_model_sections(model_text)
        if parsed is None:
            return template, "template", reason

        # 模型只负责四段文本；法条/类案用检索结果保证 citation_id 可溯源。
        return (
            {
                "summary": parsed["summary"],
                "legal_analysis": parsed["legal_analysis"],
                "related_laws": related_laws,
                "similar_cases": similar_cases,
                "suggestions": parsed["suggestions"],
                "missing_info": parsed["missing_info"],
            },
            "model",
            None,
        )

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
        "你是中国执业律师，请基于给定材料为承办案件生成六段式分析。\n"
        "只输出一个 JSON 对象，不要 Markdown 围栏或任何额外文字，字段如下：\n"
        "{\n"
        '  "summary": "案件摘要：当事人、相对方、纠纷类型、争议焦点、标的额、关键时间线",\n'
        '  "legal_analysis": "法律分析：法律关系、请求权基础、责任构成",\n'
        '  "suggestions": [{"path": "方案名", "pros": "优势", "cons": "劣势"}],\n'
        '  "missing_info": [{"item": "待补材料", "reason": "为什么需要", "priority": 1}]\n'
        "}\n"
        "要求：内容必须与本案事实和下列检索材料一致，不得编造法条条号或案号；"
        "priority 用数字 1（高）/2（中）。相关法条与类案由系统按检索结果补充，无需你输出。\n"
        f"案件：{case.title}\n纠纷类型：{case.dispute_type}\n"
        f"摘要：{case.summary}\n相关法条：\n{law_txt}\n类案：\n{case_txt}\n"
        f"律师指令：{note or '无'}"
    )


def _parse_model_sections(text: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """解析模型六段式 JSON；失败返回 `(None, 原因)`，由调用方回落模板。

    容错去 ```json 围栏、截取首个 `{` 到最后一个 `}`。摘要与法律分析必须
    非空；建议/待补充允许为空列表（模型可明确表示「暂无」），但结构必须是
    数组且每项关键字段齐全——否则视为不可解析，宁可回落模板也不落半成品。
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "模型输出中找不到 JSON 对象"
    try:
        obj = json.loads(raw[start : end + 1])
    except Exception as exc:
        return None, f"模型输出 JSON 解析失败：{exc}"
    if not isinstance(obj, dict):
        return None, "模型输出 JSON 顶层不是对象"

    summary = str(obj.get("summary") or "").strip()
    legal_analysis = str(obj.get("legal_analysis") or "").strip()
    if not summary or not legal_analysis:
        return None, "模型输出缺少非空的 summary / legal_analysis"

    def _normalize_items(value: Any, keys: tuple[str, ...]) -> Optional[list[dict]]:
        if value is None:
            return []
        if not isinstance(value, list):
            return None
        out: list[dict] = []
        for item in value:
            if not isinstance(item, dict):
                return None
            row: dict[str, Any] = {}
            for k in keys:
                v = item.get(k)
                if k == "priority":
                    try:
                        row[k] = int(v) if v is not None else 1
                    except (TypeError, ValueError):
                        row[k] = 1
                else:
                    row[k] = str(v or "").strip()
            if not row[keys[0]]:
                return None
            out.append(row)
        return out

    suggestions = _normalize_items(obj.get("suggestions"), ("path", "pros", "cons"))
    missing_info = _normalize_items(obj.get("missing_info"), ("item", "reason", "priority"))
    if suggestions is None:
        return None, "模型输出的 suggestions 结构不合法"
    if missing_info is None:
        return None, "模型输出的 missing_info 结构不合法"

    return (
        {
            "summary": summary,
            "legal_analysis": legal_analysis,
            "suggestions": suggestions,
            "missing_info": missing_info,
        },
        None,
    )


def _utcnow():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc)
