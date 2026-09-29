"""合同审查：条款级切分 + 模型逐条通读 + 依据分层 + 计费诚实性（P0-16）。

## 这个模块为什么被重写

改造前它是 88 行关键词匹配器，却按 99 元/次计费，有四个阻断级缺陷：

1. **零 LLM 调用**：产出上限 = 7 条关键词规则 + 5 条必备条款检查（共 12 条固定文案），
   与命中位置无关；
2. **假法条**：`if "合同" in (a.law_name + a.content)` 取前 3 条塞进 `citation_ids`，
   与具体风险**无因果关系**——律师一核验即崩；
3. **零发现即放行**：`_overall([]) → RiskLevel.NONE`，把**系统性假阴性**包装成
   「审查通过」的虚假保证；
4. **无来源门控**：端点先 `review()` 再无条件 `consume_atomic()`，而 `review()` 是
   纯本地操作、**永不失败** ⇒ 99 元 100% 收在规则产出上。

## 本模块的三条硬约束（改代码前请先读）

- **依据必须可验证**：模型给出的法条只有能在 `law_articles` 按
  `(law_name, article_no)` **精确命中**才保留为 `basis_type=statute`；命中不到就
  **剥离 citation 并降级为 `experience`**，文案明示「本条为经验判断，暂无明确法条依据」。
  ⚠️ **禁止复用 `citation_service.validate_citations`**：它的语义是「有结论性内容
  就必须有引用，否则生成失败」（`citation_service.py:23-39`），与本模块**默认值相反**
  ——本库仅 12 条法条且 8 条属劳动法域，对商事合同几乎无可引用，照搬会让
  **每一次审查都「生成失败」**；且它根本不检查 `(law_name, article_no)`。
  合同审查要的是「引用**可验证性**」校验，不是「引用**必需性**」校验（PRD §7.2）。
- **定位由本地构造保证**：模型**只返回 `clause_index`**，`char_start` / `char_end` /
  `clause_no` / `original` 全部由本地切分结果填入。因此
  `original == source_text[char_start:char_end]` 是**构造保证**，而不是靠模型自觉
  （模型返回字符偏移量必然会在某次长合同上错位，且错位后用户按高亮看到的
  是**另一条条款**——比不定位更糟）。
- **零发现不得放行**：区分 `complete_no_risk`（真实模型逐条通读且无风险，`NONE` 合法）
  与 `prescreen_only`（仅规则预筛，**禁止** `NONE`、**禁止**宣称无风险）。
"""
from __future__ import annotations

import json
import re
import time
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import LLMResult
from app.ai.router import ModelRouter, TaskType
from app.config import settings
from app.core.errors import ConfigurationError
from app.core.metrics import metrics
from app.models.ai_run import AiDecision, AiRun
from app.models.citation import LawArticle
from app.models.document import ContractReview
from app.models.enums import (
    BasisType,
    ContractAnalysisStatus,
    ContractReviewSource,
    ContractReviewStatus,
    JobStatus,
    RiskLevel,
)
from app.services.document_service import _RISK_RULES

#: 免责声明（PRD Q6 待法务定稿；此处为建议文案，前端固定展示）
DISCLAIMER = "本结果为 AI 辅助意见，不构成法律意见。"

#: 模型成本单价（分 / token）。取 STRONG 档公开定价高位（¥2/百万 in、¥8/百万 out）
#: 折算：200 分 / 1e6 = 0.0002 分/token。刻意取高位——成本宁可高估，
#: 因为「高估成本」只会让毛利看起来更低（保守），「低估」会掩盖定价问题。
_COST_CENTS_PER_PROMPT_TOKEN = 0.0002
_COST_CENTS_PER_COMPLETION_TOKEN = 0.0008

#: 单次审查的输出上限（一份 30 页合同的 redline 建议通常 1–3k token）
_MAX_TOKENS = 4000

#: 条款起始标记。契约正文里「第X条」是最可靠的条款边界；无标记时退化为段落切分。
_CLAUSE_START_RE = re.compile(
    r"^[ \t\u3000]*(?:第[一二三四五六七八九十百千零〇0-9]+条"
    r"|[一二三四五六七八九十]+[、．.]"
    r"|[0-9]+[、．.](?![0-9]))",
    re.MULTILINE,
)
#: 从条款正文前缀提取条款编号（取不到则 None，**不猜**）
_CLAUSE_NO_RE = re.compile(r"^[ \t\u3000]*(第[一二三四五六七八九十百千零〇0-9]+条)")

#: 超过该长度的段落再按换行细分，避免「整份合同只有一个 clause」使定位退化
_LONG_SEGMENT_CHARS = 1500

#: 必备条款检查：**只作为提示送进 prompt**，不直接产出 finding。
#: 原因：它是「全文未检索到某关键词」的**否定式**结论，天然没有字符区间，
#: 无法满足 `original == source_text[char_start:char_end]` 的定位契约；
#: 且「没出现『保密』两个字」远不足以断定缺少保密条款。
_REQUIRED_CLAUSES: list[tuple[str, str]] = [
    ("争议解决", "未出现争议解决/管辖相关表述"),
    ("保密", "未出现保密相关表述"),
    ("违约", "未出现违约责任相关表述"),
    ("解除", "未出现合同解除/终止相关表述"),
    ("通知", "未出现通知送达相关表述"),
]

#: 失败态**面向用户**的原因文案。
#:
#: 为什么不直接把异常原文给用户：`ConfigurationError` 的 message 里带
#: 环境变量名与档位（"未配置 local 档模型（LLM_LOCAL_API_KEY 为空）"），
#: 那是运维视角。前端会把该字段原样展示，因此这里必须是「人话 + 可行动」，
#: 且不泄露基础设施细节。原始异常仍完整落在 `AiRun.error_message`。
_FAILURE_USER_TEXT = {
    "configuration_error": "服务端未配置可用的审查模型，请联系管理员或稍后重试。",
    "empty_text": "提交的合同正文为空，请粘贴合同内容后重试。",
}

#: 风险维度（PRD CR-02：至少覆盖 ≥3 类；模型必须从此集合取值）
_DIMENSIONS = (
    "权利义务失衡",
    "违约责任",
    "争议解决",
    "保密",
    "解除终止",
    "效力瑕疵",
    "付款与结算",
    "交付与验收",
    "知识产权",
)

_SYSTEM_PROMPT = (
    "你是一名资深合同审查律师，为当事人审查其即将签署的合同。\n"
    "你的产出会被律师直接核验，任何编造都会导致信任崩塌，因此：\n"
    "1. 只输出**一个 JSON 对象**，不要输出解释性文字，不要使用 Markdown 代码围栏。\n"
    "2. 每条风险必须给出 `clause_index`（条款清单中的序号），不得编造序号。\n"
    "3. 引用法条时必须填写你**确知原文**的法律名称与条号；不确定就填 null。"
    "**严禁编造法条**——宁可标注为经验判断，也不要把经验伪装成法条。\n"
    "4. `suggestion_text` 必须是**可直接替换原文的改写后条款全文**，"
    "而不是「明确违约金计算方式」这类方向性短语。\n"
    "5. 若通读后未发现明显风险，`findings` 返回空数组——**不要为凑数虚构风险**。"
)


@dataclass(frozen=True)
class Clause:
    """本地切分出的一个条款（定位的唯一事实来源）。"""

    index: int
    char_start: int
    char_end: int
    clause_no: Optional[str]
    text: str


# ---------------------------------------------------------------------------
# 条款切分
# ---------------------------------------------------------------------------


def split_clauses(text: str) -> list[Clause]:
    """把合同全文切成**首尾相接、无空洞**的条款序列。

    覆盖性是硬契约：`"".join(c.text for c in split_clauses(t)) == t`。
    `coverage.reviewed_ratio` 完全依赖它——若切分丢字，用户按高亮定位时
    会看到与清单不符的内容，而这正是最伤信任的失败模式。
    """
    if not text:
        return []

    starts = [0]
    for m in _CLAUSE_START_RE.finditer(text):
        if m.start() > 0:
            starts.append(m.start())
    spans = _cut(text, starts)

    # 长段落（如整份合同无「第X条」标记）再按换行细分，避免定位粒度退化为全文
    refined: list[tuple[int, int]] = []
    for start, end in spans:
        if end - start > _LONG_SEGMENT_CHARS and "\n" in text[start:end]:
            breaks = [start] + [i + 1 for i in range(start, end) if text[i] == "\n"]
            refined.extend(_cut(text, breaks))
        else:
            refined.append((start, end))

    return [
        Clause(
            index=i,
            char_start=s,
            char_end=e,
            clause_no=_clause_no(text[s:e]),
            text=text[s:e],
        )
        for i, (s, e) in enumerate(refined)
    ]


def _cut(text: str, starts: list[int]) -> list[tuple[int, int]]:
    """按 `starts` 把全文切成连续区间（末段补到 `len(text)`）。"""
    ordered = sorted({max(0, s) for s in starts} | {0})
    spans: list[tuple[int, int]] = []
    for i, s in enumerate(ordered):
        e = ordered[i + 1] if i + 1 < len(ordered) else len(text)
        if e > s:
            spans.append((s, e))
    return spans


def _clause_no(segment: str) -> Optional[str]:
    m = _CLAUSE_NO_RE.match(segment)
    return m.group(1) if m else None


def clause_index_for_offset(clauses: list[Clause], offset: int) -> Optional[int]:
    """把全文偏移量映射到条款序号（供规则预筛命中项定位）。"""
    if not clauses:
        return None
    starts = [c.char_start for c in clauses]
    i = bisect_right(starts, offset) - 1
    if i < 0:
        i = 0
    c = clauses[i]
    return c.index if c.char_start <= offset < c.char_end else None


# ---------------------------------------------------------------------------
# 引用可验证性（**不是** citation_service.validate_citations）
# ---------------------------------------------------------------------------


async def resolve_verifiable_citation(
    db: AsyncSession, *, law_name: Any, article_no: Any
) -> Optional[LawArticle]:
    """按 `(law_name, article_no)` 在 `law_articles` 中**精确**查库。

    命中 ⇒ 该引用可溯源，允许作为 `basis_type=statute`；
    未命中 ⇒ 调用方**必须**剥离 citation 并降级为 `experience`。

    刻意不做模糊匹配（同义法律名、阿拉伯数字与汉字条号互转）：模糊匹配会
    让「模型编的法条恰好撞上库里某条」变得可能，而这正是要消灭的假依据。
    """
    if not law_name or not article_no:
        return None
    stmt = select(LawArticle).where(
        LawArticle.tenant_id == "platform",
        LawArticle.law_name == str(law_name).strip(),
        LawArticle.article_no == str(article_no).strip(),
    )
    return (await db.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------
# LLM 输出解析
# ---------------------------------------------------------------------------


def parse_llm_json(raw: str) -> Optional[dict]:
    """从模型输出中提取 JSON 对象；解析失败返回 None（**绝不抛异常**）。

    必须容忍 Markdown 围栏：即便 system prompt 明确禁止，模型仍会习惯性
    输出 ```` ```json ... ``` ````——把「模型不听话」升级成 500 是设计错误。
    """
    if not raw:
        return None
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    else:
        left, right = text.find("{"), text.rfind("}")
        if left != -1 and right > left:
            text = text[left : right + 1]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------


class ContractReviewService:
    """合同审查主流程。

    `router` **可注入**：无真实 LLM Key 时测试注入 FakeRouter，
    这样「定位/依据/降级/计费门控」这些**与模型无关**的正确性属性才能被离线断言。
    """

    def __init__(self, db: AsyncSession, router: Optional[Any] = None) -> None:
        self.db = db
        self.router = router if router is not None else ModelRouter()

    async def review(
        self,
        *,
        title: str,
        source_text: str,
        tenant_id: str,
        user_id: int,
        evidence_id: Optional[int] = None,
    ) -> ContractReview:
        t0 = time.perf_counter()
        text = source_text or ""
        clauses = split_clauses(text)
        prescreen = self._prescreen(text, clauses)

        cr = ContractReview(
            tenant_id=tenant_id,
            evidence_id=evidence_id,
            created_by=user_id,
            title=title,
            # 只留前 5000 字：合同含商业秘密，落库全文不是本轮的目标
            # （findings 里的 `original` 才是交付物需要的片段）
            source_text=text[:5000],
            findings=[],
            overall_risk=RiskLevel.LOW,
            source=ContractReviewSource.RULE,
            status=ContractReviewStatus.FAILED,
            analysis_status=ContractAnalysisStatus.PRESCREEN_ONLY,
            disclaimer=DISCLAIMER,
        )
        self.db.add(cr)
        await self.db.flush()

        run = AiRun(
            job_id=None,
            pipeline="CONTRACT_REVIEW",
            ref_type="contract_review",
            ref_id=cr.id,
            trigger="USER_MANUAL",
            tenant_id=tenant_id,
            started_at=_utcnow(),
        )
        self.db.add(run)
        await self.db.flush()
        cr.run_id = run.id

        await self._decide(
            run,
            "prescreen",
            "PROCEED",
            f"规则预筛命中 {len(prescreen)} 项（仅作召回提示，不代表已确认风险）",
            {"hits": [h["keyword"] for h in prescreen], "total_clauses": len(clauses)},
            0,
        )

        # ---- 空正文：**不调用模型、不产出结论、不计费** ----
        # 否则空文本会走完模型（返回空 findings）拿到 complete_no_risk 并照扣 99 元。
        if not text.strip():
            return await self._finalize_failed(
                cr, run, t0, "合同正文为空，无法审查", clauses,
                user_message=_FAILURE_USER_TEXT["empty_text"],
            )

        result, degraded_reason, error_message = await self._call_model(run, clauses, prescreen, text)

        # 生产环境未配 Provider（含 sensitive=True 走 LOCAL 档）：**不写业务结论**、
        # 不计费，但保留留痕（PRD Q7）。刻意返回 200 + status=failed 而不是抛错：
        # 前端需要据此渲染「可重试的失败页」，而不是一个空白的 500。
        if degraded_reason == "configuration_error":
            return await self._finalize_failed(
                cr, run, t0, error_message or "模型不可用", clauses,
                user_message=_FAILURE_USER_TEXT["configuration_error"],
            )

        findings: list[dict] = []
        analysis_status = ContractAnalysisStatus.PRESCREEN_ONLY
        source = ContractReviewSource.RULE
        status = ContractReviewStatus.DEGRADED

        if result is not None and degraded_reason is None:
            payload = parse_llm_json(result.text)
            if payload is None:
                degraded_reason = "rule_fallback"
                error_message = "模型输出无法解析为 JSON"
            else:
                findings = await self._build_findings(payload, clauses, text, run)
                source = ContractReviewSource.LLM
                status = ContractReviewStatus.SUCCESS
                analysis_status = (
                    ContractAnalysisStatus.RISK_FOUND
                    if findings
                    else ContractAnalysisStatus.COMPLETE_NO_RISK
                )

        if status != ContractReviewStatus.SUCCESS:
            findings = self._prescreen_findings(text, clauses, prescreen)
            analysis_status = ContractAnalysisStatus.PRESCREEN_ONLY
            if degraded_reason == "mock":
                # 非生产无 Key：产出确实来自 Mock 路径，必须如实标注为 mock，
                # 不能混进 rule——前端横幅文案与「模型是否参与」的语义不同
                source = ContractReviewSource.MOCK
            await self._decide(
                run,
                "degrade",
                "RETRY",
                f"模型不可用（{degraded_reason}），降级为规则预筛：{error_message or ''}".strip(),
                {
                    "reason": degraded_reason,
                    "rule_hits": len(findings),
                    # 降级产出**不计费**，这条留痕是事后核对「为何没扣费」的唯一依据
                    "charged": False,
                },
                int((time.perf_counter() - t0) * 1000),
            )

        # ---- 收敛结论 ----
        # prescreen_only 时 `allow_none=False`：这是「零发现即放行」的修复点。
        allow_none = analysis_status == ContractAnalysisStatus.COMPLETE_NO_RISK
        overall = _overall(findings, allow_none=allow_none)

        cr.source = source
        cr.status = status
        cr.analysis_status = analysis_status
        cr.findings = findings
        cr.overall_risk = overall
        cr.coverage = _coverage(len(clauses), len(clauses) if status == ContractReviewStatus.SUCCESS else 0)
        cr.summary = (
            _llm_summary(len(clauses), findings)
            if status == ContractReviewStatus.SUCCESS
            else _degraded_summary(len(findings), degraded_reason)
        )

        _apply_run_metrics(run, result)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        run.duration_ms = duration_ms
        run.cost_cents = _cost_cents(result)
        run.stage_counts = {
            "total_clauses": len(clauses),
            "findings": len(findings),
            "prescreen_hits": len(prescreen),
        }
        run.finish(
            JobStatus.COMPLETED if status == ContractReviewStatus.SUCCESS else JobStatus.FAILED,
            error=error_message if status != ContractReviewStatus.SUCCESS else None,
        )

        cr.model_name = run.model_name
        cr.model_tier = run.model_tier
        cr.is_mock = bool(run.is_mock)
        cr.prompt_tokens = run.prompt_tokens or 0
        cr.completion_tokens = run.completion_tokens or 0
        cr.duration_ms = duration_ms
        cr.cost_cents = run.cost_cents

        _record_metrics(source, status, duration_ms, degraded_reason)
        await self.db.flush()
        return cr

    # ---------------- 模型调用 ----------------

    async def _call_model(
        self, run: AiRun, clauses: list[Clause], prescreen: list[dict], text: str
    ) -> tuple[Optional[LLMResult], Optional[str], Optional[str]]:
        """调用模型；返回 `(结果, 降级原因, 错误信息)`。

        降级原因 ∈ {provider_error, timeout, mock, rule_fallback, configuration_error}。
        `configuration_error` 由调用方转成 `status=failed`（不写业务结论、不计费）。
        """
        try:
            result = await self.router.complete(
                TaskType.CONTRACT_REVIEW,
                _build_prompt(clauses, prescreen, text),
                system=_SYSTEM_PROMPT,
                # 合同通常含商业秘密 ⇒ 默认走 LOCAL 档。**不要**为了让代码
                # 「跑通」而偷偷改成 False：那会让敏感合同静默走非 LOCAL 档。
                sensitive=settings.CONTRACT_REVIEW_SENSITIVE,
                temperature=0.3,
                max_tokens=_MAX_TOKENS,
            )
        except ConfigurationError as exc:
            # 生产无 Key（含 LOCAL 档未配）：拒绝以 Mock 冒充真实模型输出
            run.model_tier = "local" if settings.CONTRACT_REVIEW_SENSITIVE else None
            return None, "configuration_error", f"模型不可用：{exc}"
        except (httpx.TimeoutException, TimeoutError) as exc:
            return None, "timeout", f"模型调用超时：{exc}"
        except Exception as exc:  # noqa: BLE001  任何 Provider 异常都必须降级而非 500
            return None, "provider_error", f"模型调用失败：{exc}"

        _apply_run_metrics(run, result)
        await self._decide(
            run,
            "read",
            "PROCEED",
            f"模型 {result.model}（mock={result.is_mock}）逐条通读 {len(clauses)} 个条款",
            {"is_mock": result.is_mock, "tier": result.tier},
            result.duration_ms,
        )
        if result.is_mock:
            # 非生产环境无 Key：MockProvider 返回空文本，**不能**当成模型产出
            return result, "mock", "非生产环境未配置真实模型，返回 Mock 结果"
        return result, None, None

    # ---------------- 规则预筛 ----------------

    def _prescreen(self, text: str, clauses: list[Clause]) -> list[dict]:
        """规则预筛：**只做召回提示与降级兜底，不产出 finding**。

        零命中不产生任何条目——「没命中 7 个关键词」不构成任何安全性结论。
        """
        hits: list[dict] = []
        for rule in _RISK_RULES:
            m = re.search(re.escape(rule["keyword"]), text or "")
            if m is None:
                continue
            hits.append(
                {
                    "keyword": rule["keyword"],
                    "risk_level": rule["level"],
                    "dimension": rule.get("dimension", "其他"),
                    "consequence": rule["issue"],
                    "suggestion_text": rule["suggestion"],
                    "offset": m.start(),
                    "clause_index": clause_index_for_offset(clauses, m.start()),
                }
            )
        return hits

    def _prescreen_findings(
        self, text: str, clauses: list[Clause], hits: list[dict]
    ) -> list[dict]:
        """把规则命中项转成**可定位**的 finding（降级路径专用）。

        这些条目 `basis_type` 恒为 `experience`：关键词规则本就无法给出
        可溯源的法定依据，硬塞法条就是改造前那个假引用。
        """
        findings: list[dict] = []
        for h in hits:
            idx = h["clause_index"]
            if idx is None or not (0 <= idx < len(clauses)):
                continue
            c = clauses[idx]
            original = text[c.char_start : c.char_end]
            findings.append(
                {
                    "clause_index": c.index,
                    "clause_no": c.clause_no,
                    "char_start": c.char_start,
                    "char_end": c.char_end,
                    "original": original,
                    "dimension": h["dimension"],
                    "risk_level": h["risk_level"],
                    "consequence": h["consequence"],
                    "suggestion_text": h["suggestion_text"],
                    "basis_type": BasisType.EXPERIENCE.value,
                    "citation": None,
                    "clause": original,
                    "issue": h["consequence"],
                    "suggestion": h["suggestion_text"],
                }
            )
        return findings

    # ---------------- findings 组装 ----------------

    async def _build_findings(
        self, payload: dict, clauses: list[Clause], text: str, run: AiRun
    ) -> list[dict]:
        """把模型输出组装为契约要求的 finding（定位与依据校验都在这里）。"""
        raw = payload.get("findings")
        if not isinstance(raw, list):
            await self._decide(
                run, "cite_validate", "PROCEED", "模型未返回 findings 数组", {"raw_type": type(raw).__name__}, 0
            )
            return []

        findings: list[dict] = []
        seen: set[tuple[int, str]] = set()
        dropped = 0
        statute_hits = 0
        statute_miss = 0
        normalized_level = 0

        for item in raw:
            if not isinstance(item, dict):
                dropped += 1
                continue

            idx = item.get("clause_index")
            if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < len(clauses)):
                # **绝不静默填错区间**：越界即丢弃并留痕
                dropped += 1
                continue

            dimension = str(item.get("dimension") or "").strip() or "其他"
            level = str(item.get("risk_level") or "").strip().upper()
            if level not in ("HIGH", "MEDIUM", "LOW"):
                # 等级非法时按 MEDIUM 保留：丢弃会漏报，而漏报的代价高于多报
                level = "MEDIUM"
                normalized_level += 1
            consequence = str(item.get("consequence") or "").strip()
            suggestion_text = str(item.get("suggestion_text") or "").strip()
            if not consequence or not suggestion_text:
                # 缺后果或改写文本的条目**不可交付**（CR-04 要求 suggestion_text 非空）
                dropped += 1
                continue

            c = clauses[idx]
            key = (c.index, dimension)
            if key in seen:
                dropped += 1
                continue
            seen.add(key)

            citation = await resolve_verifiable_citation(
                self.db,
                law_name=item.get("cited_law_name"),
                article_no=item.get("cited_article_no"),
            )
            if citation is not None:
                basis_type = BasisType.STATUTE
                statute_hits += 1
                citation_payload = {
                    "law_name": citation.law_name,
                    "article_no": citation.article_no,
                    "content": citation.content,
                }
            else:
                basis_type = BasisType.EXPERIENCE
                if item.get("cited_law_name") or item.get("cited_article_no"):
                    statute_miss += 1
                citation_payload = None

            original = text[c.char_start : c.char_end]
            findings.append(
                {
                    "clause_index": c.index,
                    "clause_no": c.clause_no,
                    "char_start": c.char_start,
                    "char_end": c.char_end,
                    "original": original,
                    "dimension": dimension,
                    "risk_level": level,
                    "consequence": consequence,
                    "suggestion_text": suggestion_text,
                    "basis_type": basis_type.value,
                    "citation": citation_payload,
                    # —— 旧键（向后兼容既有调用方）——
                    "clause": original,
                    "issue": consequence,
                    "suggestion": suggestion_text,
                }
            )

        await self._decide(
            run,
            "cite_validate",
            "PROCEED",
            f"引用校验：库内精确命中 {statute_hits} 条，未命中降级为经验判断 {statute_miss} 条",
            {"statute": statute_hits, "experience": statute_miss},
            0,
        )
        await self._decide(
            run,
            "grade",
            "PROCEED" if findings else "PROCEED",
            f"产出 {len(findings)} 条风险（丢弃 {dropped} 条无法定位/不完整/重复的条目）",
            {"findings": len(findings), "dropped": dropped, "level_normalized": normalized_level},
            0,
        )
        return findings

    # ---------------- 收尾 ----------------

    async def _finalize_failed(
        self,
        cr: ContractReview,
        run: AiRun,
        t0: float,
        reason: str,
        clauses: list[Clause],
        *,
        user_message: str,
    ) -> ContractReview:
        """失败态：**不写任何业务结论**，但保留留痕与指标（PRD Q7）。

        `reason` 是**运维视角**的原始原因（含环境变量名等），落 `AiRun.error_message`
        与 `AiDecision`；`user_message` 是**用户视角**文案，落 `cr.error_message`
        并由接口返回给前端失败页原样展示。两者刻意分开，不能互相替代。
        """
        duration_ms = int((time.perf_counter() - t0) * 1000)
        await self._decide(
            run,
            "degrade",
            "ABORT",
            reason,
            {"reason": "failed", "charged": False, "findings": 0},
            duration_ms,
        )
        run.finish(JobStatus.FAILED, error=reason)
        run.duration_ms = duration_ms
        run.cost_cents = 0.0
        run.stage_counts = {"total_clauses": len(clauses), "findings": 0}

        cr.source = ContractReviewSource.RULE
        cr.status = ContractReviewStatus.FAILED
        cr.analysis_status = ContractAnalysisStatus.PRESCREEN_ONLY
        cr.findings = []
        # 失败态同样**禁止** NONE：NONE 会被读成「没有风险」
        cr.overall_risk = RiskLevel.LOW
        cr.coverage = _coverage(len(clauses), 0)
        cr.summary = f"本次审查失败，未生成任何风险结论。{user_message}本次不计费。"
        # 前端失败页会原样展示这一条（不加工、不翻译），因此只能是上面那句人话
        cr.error_message = user_message
        cr.duration_ms = duration_ms
        cr.cost_cents = 0.0

        _record_metrics(ContractReviewSource.RULE, ContractReviewStatus.FAILED, duration_ms, None)
        await self.db.flush()
        return cr

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


# ---------------------------------------------------------------------------
# 纯函数辅助
# ---------------------------------------------------------------------------


def _apply_run_metrics(run: AiRun, result: Optional[LLMResult]) -> None:
    if result is None:
        return
    run.model_tier = result.tier
    run.model_name = result.model
    run.is_mock = int(result.is_mock)
    run.prompt_tokens = result.prompt_tokens
    run.completion_tokens = result.completion_tokens


def _cost_cents(result: Optional[LLMResult]) -> float:
    """单次审查的模型成本（分）。**必须非 NULL**，否则成本永久不可回溯。"""
    if result is None or result.is_mock:
        return 0.0
    return round(
        result.prompt_tokens * _COST_CENTS_PER_PROMPT_TOKEN
        + result.completion_tokens * _COST_CENTS_PER_COMPLETION_TOKEN,
        6,
    )


def _coverage(total: int, reviewed: int) -> dict:
    return {
        "total_clauses": total,
        "reviewed_clauses": reviewed,
        "reviewed_ratio": round(reviewed / total, 4) if total else 0.0,
    }


def _overall(findings: list[dict], *, allow_none: bool) -> RiskLevel:
    """整体风险等级。

    `allow_none=False` 是「零发现即放行」的修复点：仅规则预筛时**禁止** `NONE`。
    """
    if any(f["risk_level"] == "HIGH" for f in findings):
        return RiskLevel.HIGH
    if any(f["risk_level"] == "MEDIUM" for f in findings):
        return RiskLevel.MEDIUM
    if findings:
        return RiskLevel.LOW
    return RiskLevel.NONE if allow_none else RiskLevel.LOW


def _llm_summary(total: int, findings: list[dict]) -> str:
    if not findings:
        return (
            f"本次由 AI 模型逐条通读全部 {total} 个条款，未发现明显风险。"
            "未发现风险不等于合同无风险，重大交易仍建议由执业律师复核。"
        )
    high = sum(1 for f in findings if f["risk_level"] == "HIGH")
    med = sum(1 for f in findings if f["risk_level"] == "MEDIUM")
    return (
        f"本次由 AI 模型逐条通读全部 {total} 个条款，发现 {len(findings)} 处风险，"
        f"其中高风险 {high} 处、中风险 {med} 处。建议按修改建议逐条修订后再签署。"
    )


_DEGRADE_REASON_TEXT = {
    "timeout": "模型调用超时",
    "provider_error": "模型服务不可用",
    "mock": "当前环境未配置真实模型",
    "rule_fallback": "模型输出无法解析",
}


def _degraded_summary(count: int, reason: Optional[str]) -> str:
    """降级文案。

    **刻意不写**「建议按修改建议逐条修订」——0 条建议时这句话毫无意义，
    更会掩盖「本次其实没做完整审查」这一事实。
    """
    why = _DEGRADE_REASON_TEXT.get(reason or "", "模型不可用")
    return (
        f"本次仅完成规则预筛（{why}），未使用模型逐条通读，"
        f"共命中 {count} 项关键词提示。"
        "未命中规则库不等于合同安全，建议开启完整 AI 审查。本次不计费。"
    )


def _build_prompt(clauses: list[Clause], prescreen: list[dict], text: str) -> str:
    lines = ["【合同条款清单】（格式：序号 | 条款号 | 正文）"]
    for c in clauses:
        lines.append(f"{c.index} | {c.clause_no or '-'} | {c.text.strip()}")

    lines.append("")
    if prescreen:
        lines.append("【规则预筛命中（仅作召回提示，不代表已确认风险，请自行判断）】")
        for h in prescreen:
            lines.append(f"- 关键词「{h['keyword']}」出现在条款 {h['clause_index']}")
    else:
        lines.append("【规则预筛未命中任何关键词——这不代表合同安全，仍需逐条通读】")

    missing = [kw for kw, _ in _REQUIRED_CLAUSES if kw not in text]
    if missing:
        lines.append(
            "【全文未出现以下必备条款关键词，请重点核查是否确实缺失："
            + "、".join(missing)
            + "】"
        )

    lines += [
        "",
        "【输出格式】只输出一个 JSON 对象：",
        '{"summary": "一句话总体结论", "findings": ['
        '{"clause_index": 7, "dimension": "违约责任", "risk_level": "HIGH", '
        '"consequence": "不改会怎样（具体后果）", '
        '"suggestion_text": "改写后的条款全文，可直接替换原文", '
        '"cited_law_name": "中华人民共和国民法典", "cited_article_no": "第577条"}]}',
        f"dimension 只能取以下之一：{'、'.join(_DIMENSIONS)}",
        "risk_level 只能取 HIGH / MEDIUM / LOW",
        "cited_law_name / cited_article_no 不确定时填 null（宁可留空，不可编造）",
    ]
    return "\n".join(lines)


def _record_metrics(
    source: ContractReviewSource,
    status: ContractReviewStatus,
    duration_ms: int,
    degraded_reason: Optional[str],
) -> None:
    metrics.contract_review_total.inc((source.value, status.value))
    metrics.contract_review_duration_milliseconds.observe((source.value,), duration_ms)
    if degraded_reason:
        metrics.contract_review_degraded_total.inc((degraded_reason,))


def _utcnow():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc)
