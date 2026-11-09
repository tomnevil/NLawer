"""四维合规扫描：劳动用工 / 商业合同 / 数据隐私 / 广告营销（PRD 5.10）。

输出：分维度得分与风险等级 + 整改建议清单 + 整体报告。
对外版本（`is_external`）自动命中强制 L2 复核（见 forced_review）。

## 判定引擎：模型为主 + 关键词兜底（N4）

- **主判**：一次模型调用覆盖本次请求的全部维度（JSON 输出），逐维度给出分数、
  风险等级与发现项；发现项必须带 `evidence`，且该片段能在材料原文中**逐字校验**，
  否则不落库（防编造）。
- **兜底**：关键词规则始终执行并完整保留其发现项（确定性、可审计）。
- **降级**：模型不可用 / 超时 / 输出不可解析 / 材料为空时，退回纯关键词结果，
  并在 `dimension_scores` 的每个维度写 `engine=keyword`、`degraded/degrade_reason`，
  在 `report_summary` 首行显式提示，同时打 `logger.warning`。绝不静默降级。

### 冲突取舍规则（关键词 vs 模型）

1. **关键词命中即风险下限（floor）**：关键词判定为 HIGH/MEDIUM 时，模型**不得下调**
   到更低一档；下调一律拒绝，记 `MODEL_DOWNGRADE_REJECTED_KEYWORD_FLOOR`。
   若关键词该维度**没有任何命中**，则其 LOW 只代表「未命中」，不构成合规证据，
   不设下限，模型可自由定级（这正是「未命中 ≠ 合规」要修的问题）。
2. **模型上调必须有可核验证据**：模型风险等级高于关键词时需要至少一个
   evidence 通过原文校验的发现项；否则拒绝上调，记
   `MODEL_ESCALATION_REJECTED_NO_VERIFIED_EVIDENCE`。关键词仍按原等级保留。
3. **模型新增发现项同样要证据**：evidence 为空或不在原文中即丢弃，记
   `MODEL_FINDINGS_REJECTED_UNVERIFIABLE:<n>`，只保留关键词发现项。
4. **分数跟随最终等级**：最终分数夹在最终风险档位内（HIGH 20-59 / MEDIUM 60-84 /
   LOW 85-100），保证 `score` 与 `risk` 永不矛盾。
5. **去重**：模型标题与关键词标题相同视为同一问题，保留关键词版本。
"""
import asyncio
import json
import re
import time
from typing import Any, Optional

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.router import ModelRouter, TaskType
from app.core.errors import ErrorCode, NotFoundError
from app.models.enums import ComplianceDimension, RiskLevel, ScanStatus
from app.models.knowledge import ComplianceFinding, ComplianceScan
from app.workflows.forced_review import detect_forced_review, highest_level

# 每维度的检查项：命中关键词即扣分
_DIMENSION_RULES: dict[ComplianceDimension, list[dict[str, Any]]] = {
    ComplianceDimension.LABOR: [
        {"kw": "未签劳动合同", "risk": "HIGH", "title": "存在未签订书面劳动合同情形",
         "suggestion": "自用工之日起一个月内补签书面劳动合同"},
        {"kw": "试用期", "risk": "MEDIUM", "title": "试用期约定需核对法定上限",
         "suggestion": "核对试用期时长与工资是否不低于法定标准"},
        {"kw": "加班", "risk": "MEDIUM", "title": "加班与工时管理待规范",
         "suggestion": "完善考勤与加班审批记录，依法支付加班费"},
        {"kw": "社保", "risk": "HIGH", "title": "社会保险缴纳存在风险",
         "suggestion": "依法为全体在职员工足额缴纳社会保险"},
    ],
    ComplianceDimension.COMMERCIAL: [
        {"kw": "口头", "risk": "MEDIUM", "title": "存在口头约定，缺乏书面凭证",
         "suggestion": "补签书面合同，明确标的、价款、履约期限与违约责任"},
        {"kw": "违约金", "risk": "MEDIUM", "title": "违约金标准需复核",
         "suggestion": "违约金以实际损失为基础，避免约定过高被酌减"},
        {"kw": "格式条款", "risk": "HIGH", "title": "格式条款提示义务风险",
         "suggestion": "对免责、限责条款履行显著提示与说明义务"},
    ],
    ComplianceDimension.DATA_PRIVACY: [
        {"kw": "人脸", "risk": "HIGH", "title": "涉人脸识别等敏感个人信息处理",
         "suggestion": "取得单独同意并进行个人信息保护影响评估"},
        {"kw": "个人信息", "risk": "MEDIUM", "title": "个人信息收集与使用需合规",
         "suggestion": "完善隐私政策，落实告知同意与最小必要原则"},
        {"kw": "跨境", "risk": "HIGH", "title": "涉数据跨境传输",
         "suggestion": "评估是否需申报安全评估或订立标准合同"},
    ],
    ComplianceDimension.ADVERTISING: [
        {"kw": "最", "risk": "MEDIUM", "title": "宣传用语含绝对化用语",
         "suggestion": "避免使用「最佳」「第一」等绝对化用语"},
        {"kw": "疗效", "risk": "HIGH", "title": "宣传内容涉疗效或功效断言",
         "suggestion": "删除疗效承诺，确保宣传内容有依据"},
        {"kw": "原价", "risk": "MEDIUM", "title": "价格标示需真实可查",
         "suggestion": "确保划线价有真实交易记录支撑"},
    ],
}

_DIMENSION_LABEL = {
    ComplianceDimension.LABOR: "劳动用工",
    ComplianceDimension.COMMERCIAL: "商业合同",
    ComplianceDimension.DATA_PRIVACY: "数据隐私",
    ComplianceDimension.ADVERTISING: "广告营销",
}

_RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
# 与既有阈值一致：<60 HIGH，<85 MEDIUM，其余 LOW
_RISK_BAND = {"HIGH": (20, 59), "MEDIUM": (60, 84), "LOW": (85, 100)}

# 单次覆盖全部维度的实测用量（四维全开约 1800 completion tokens），
# 留足余量避免 JSON 被 max_tokens 截断（截断=不可解析=降级）。
_MODEL_MAX_TOKENS = 2200

_JSON_SHAPE = (
    '{"dimensions": {"LABOR": {"score": 0-100, "risk": "LOW|MEDIUM|HIGH", '
    '"findings": [{"title": "问题标题", "description": "问题说明", "risk": "LOW|MEDIUM|HIGH", '
    '"suggestion": "整改建议", "evidence": "材料原文中的连续片段"}]}, ...}}'
)


def _build_scan_prompt(text: str, dims: list[ComplianceDimension]) -> str:
    labels = "\n".join(f"- {d.value}（{_DIMENSION_LABEL[d]}）" for d in dims)
    return (
        "你是企业合规律师。请对以下材料做合规扫描，只扫描给定维度。\n"
        f"维度：\n{labels}\n\n"
        f"材料：\n{text[:2000]}\n\n"
        "要求：\n"
        "1. 每个维度给出 0-100 分（100 为无风险）、风险等级 LOW/MEDIUM/HIGH 与发现清单；\n"
        "2. 每个发现项必须给出 evidence：从材料中原样摘录的连续片段，不得改写、不得编造；\n"
        "3. 只输出 JSON，不要额外文字。格式：\n"
        f"{_JSON_SHAPE}"
    )


def _parse_json_object(text: str) -> Optional[dict]:
    """从模型输出中抠出首个完整 JSON 对象（容忍 ```json 围栏）。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
    except Exception:  # noqa: BLE001 - 模型输出不可解析属预期降级路径
        return None
    return obj if isinstance(obj, dict) else None


def _to_score(raw: Any) -> Optional[int]:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return min(max(value, 0), 100)


def _normalize_risk(raw: Any, score: int) -> str:
    risk = str(raw or "").strip().upper()
    if risk in _RISK_RANK:
        return risk
    # 等级缺失/非法时按分数归档，保证 score 与 risk 一致
    return "HIGH" if score < 60 else ("MEDIUM" if score < 85 else "LOW")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _evidence_verified(evidence: Any, material: str) -> bool:
    ev = str(evidence or "").strip()
    return bool(ev) and _squash(ev) in _squash(material)


def _normalize_findings(raw: Any, material: str) -> tuple[list[dict], int]:
    """模型发现项：标题必填、证据必须能在原文中逐字校验，否则丢弃。"""
    if not isinstance(raw, list):
        return [], 0
    out: list[dict] = []
    rejected = 0
    for item in raw:
        if not isinstance(item, dict):
            rejected += 1
            continue
        title = str(item.get("title") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if not title or not _evidence_verified(evidence, material):
            rejected += 1
            continue
        description = str(item.get("description") or "").strip()
        suggestion = str(item.get("suggestion") or "").strip() or "请人工复核并整改"
        score = _to_score(item.get("score"))  # 发现项通常不带分数
        risk = _normalize_risk(item.get("risk"), score if score is not None else 100)
        out.append({
            "title": title,
            "description": f"{description}（原文：{evidence}）" if description else f"（原文：{evidence}）",
            "risk": risk,
            "suggestion": suggestion,
            "source": "model",
        })
    return out, rejected


def _keyword_dimension(dim: ComplianceDimension, text: str) -> dict[str, Any]:
    rules = _DIMENSION_RULES[dim]
    hits = [r for r in rules if r["kw"] in text]
    # 起始 100 分，命中一项扣 25（HIGH）/15，命中高风险额外扣 10 已在原实现中
    # 由 25/15 差值体现；这里保持完全一致，降级时结果与旧版逐分相同。
    score = 100
    for r in hits:
        score -= 25 if r["risk"] == "HIGH" else 15
    score = max(score, 20)
    risk = "HIGH" if score < 60 else ("MEDIUM" if score < 85 else "LOW")
    findings = [
        {
            "title": r["title"],
            "description": f"命中关键词：{r['kw']}",
            "risk": r["risk"],
            "suggestion": r["suggestion"],
            "source": "keyword",
        }
        for r in hits
    ]
    return {"score": score, "risk": risk, "findings": findings}


def _merge_dimension(keyword: dict, model: Optional[dict]) -> dict[str, Any]:
    """按模块文档头的冲突规则合并「关键词 + 模型」判定。"""
    kw_risk = keyword["risk"]
    has_keyword_hit = bool(keyword["findings"])
    conflicts: list[str] = []
    final_risk = kw_risk
    score = keyword["score"]
    findings = list(keyword["findings"])

    if model is not None:
        model_risk = model["risk"]
        if not has_keyword_hit:
            # 关键词零命中 ⇒ 它的 LOW 只是「没命中」，不是合规证据，不设下限；
            # 此时以模型定级为主（否则「未命中 = 满分」的老问题依旧）。
            final_risk = model_risk
        # 1) 关键词有命中时构成风险下限，模型不得下调
        elif _RISK_RANK[model_risk] < _RISK_RANK[kw_risk]:
            conflicts.append("MODEL_DOWNGRADE_REJECTED_KEYWORD_FLOOR")
        # 2) 模型上调必须有可核验证据（至少一个通过校验的发现项）
        elif _RISK_RANK[model_risk] > _RISK_RANK[kw_risk]:
            if model["findings"]:
                final_risk = model_risk
            else:
                conflicts.append("MODEL_ESCALATION_REJECTED_NO_VERIFIED_EVIDENCE")
        else:
            final_risk = model_risk
        # 3) 模型发现项去重后追加（标题相同视为同一问题，保留关键词版）
        existing = {f["title"] for f in findings}
        for f in model["findings"]:
            if f["title"] in existing:
                continue
            existing.add(f["title"])
            findings.append(f)
        if model["rejected_findings"]:
            conflicts.append(f"MODEL_FINDINGS_REJECTED_UNVERIFIABLE:{model['rejected_findings']}")
        # 4) 分数夹进最终风险档位，保证 score/risk 一致
        lo, hi = _RISK_BAND[final_risk]
        score = min(max(int(model["score"]), lo), hi)

    return {
        "score": score,
        "risk": final_risk,
        "findings": findings,
        "conflicts": conflicts,
    }


def _degrade_reason(exc: Exception) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return "timeout"
    return "unavailable"


class ComplianceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_scan(
        self,
        *,
        title: str,
        tenant_id: str,
        dimensions: Optional[list[str | ComplianceDimension]] = None,
        scope: Optional[str] = None,
        input_summary: Optional[str] = None,
        is_external: bool = False,
    ) -> ComplianceScan:
        # 请求层已用枚举校验（A10）；这里统一落库为字符串，SQLite/PG 的 JSON 列都能存。
        dims = [getattr(d, "value", str(d)) for d in (dimensions or [])] or [
            d.value for d in ComplianceDimension
        ]
        scan = ComplianceScan(
            tenant_id=tenant_id,
            title=title,
            status=ScanStatus.PENDING,
            dimensions=dims,
            scope=scope,
            input_summary=(input_summary or "")[:2000],
            is_external=int(is_external),
        )
        self.db.add(scan)
        await self.db.flush()
        return scan

    async def run(self, scan_id: int) -> ComplianceScan:
        scan = await self.db.get(ComplianceScan, scan_id)
        if scan is None:
            raise NotFoundError("扫描任务不存在", code=ErrorCode.SCAN_NOT_FOUND)

        scan.status = ScanStatus.RUNNING
        text = scan.input_summary or scan.scope or ""
        dims = [ComplianceDimension(d) for d in (scan.dimensions or [])] or list(ComplianceDimension)

        keyword = {d.value: _keyword_dimension(d, text) for d in dims}
        model, engine_meta = await self._model_scan(text, dims)
        merged = {d.value: _merge_dimension(keyword[d.value], (model or {}).get(d.value)) for d in dims}

        scores: dict[str, Any] = {}
        for dim in dims:
            item = merged[dim.value]
            scores[dim.value] = {
                "score": item["score"],
                "risk": item["risk"],
                "findings": len(item["findings"]),
                "engine": "model+keyword" if (model or {}).get(dim.value) else "keyword",
                "degraded": engine_meta["degraded"],
                "degrade_reason": engine_meta["reason"],
                "conflicts": item["conflicts"],
            }
            for f in item["findings"]:
                self.db.add(
                    ComplianceFinding(
                        tenant_id=scan.tenant_id,
                        scan_id=scan.id,
                        dimension=dim,
                        title=f["title"],
                        description=f["description"],
                        risk_level=RiskLevel(f["risk"]),
                        suggestion=f["suggestion"],
                    )
                )

        scan.dimension_scores = scores
        avg = sum(v["score"] for v in scores.values()) // max(len(scores), 1)
        scan.overall_risk = RiskLevel("HIGH" if avg < 60 else ("MEDIUM" if avg < 85 else "LOW"))
        scan.report_summary = self._summary(scores, avg, engine_meta)
        scan.status = ScanStatus.COMPLETED
        scan.finished_at = _now()

        # 对外报告 -> 强制 L2 复核
        hits = detect_forced_review(compliance_report=bool(scan.is_external))
        scan.review_status = highest_level(hits).value
        return scan

    async def _model_scan(
        self, text: str, dims: list[ComplianceDimension]
    ) -> tuple[Optional[dict], dict[str, Any]]:
        """一次模型调用覆盖全部请求维度；任何异常/不可解析都退回关键词（可观测）。"""
        meta: dict[str, Any] = {"degraded": False, "reason": "", "model": "", "tokens": ""}
        if not text.strip():
            meta.update(degraded=True, reason="empty_input")
            logger.warning("合规扫描模型判定跳过：材料为空，仅执行关键词规则")
            return None, meta

        t0 = time.perf_counter()
        try:
            result = await ModelRouter().complete(
                TaskType.COMPLIANCE, _build_scan_prompt(text, dims), max_tokens=_MODEL_MAX_TOKENS
            )
        except Exception as exc:  # noqa: BLE001 - 模型异常必须降级而非让扫描失败
            meta.update(degraded=True, reason=_degrade_reason(exc), model="", tokens="")
            logger.warning(
                "合规扫描模型判定不可用（{}），已降级为关键词规则：{}", meta["reason"], exc
            )
            return None, meta

        parsed = _parse_model_output(result, text, dims)
        meta.update(
            model=result.model,
            tokens=f"p={result.prompt_tokens},c={result.completion_tokens},ms={result.duration_ms}",
        )
        if parsed is None:
            meta.update(degraded=True, reason="unparsable")
            logger.warning(
                "合规扫描模型输出不可解析，已降级为关键词规则（model={}，片段={}）",
                result.model,
                (result.text or "")[:80],
            )
            return None, meta
        logger.info(
            "合规扫描模型判定完成（model={}，维度={}，tokens={}，wall={}ms）",
            result.model,
            len(parsed),
            meta["tokens"],
            int((time.perf_counter() - t0) * 1000),
        )
        return parsed, meta

    async def findings(self, scan_id: int) -> list[ComplianceFinding]:
        rows = (
            await self.db.execute(
                select(ComplianceFinding).where(ComplianceFinding.scan_id == scan_id).order_by(ComplianceFinding.id.asc())
            )
        ).scalars().all()
        return list(rows)

    def _summary(self, scores: dict[str, Any], avg: int, engine_meta: dict[str, Any]) -> str:
        parts = []
        for dim, v in scores.items():
            label = _DIMENSION_LABEL.get(ComplianceDimension(dim), dim)
            parts.append(f"{label} {v['score']} 分（{v['risk']}，{v['findings']} 项发现）")
        body = f"综合得分 {avg} 分。\n" + "\n".join(parts)
        if engine_meta.get("degraded"):
            reason = _REASON_LABEL.get(engine_meta.get("reason", ""), engine_meta.get("reason", ""))
            return (
                f"⚠️ 模型判定不可用（{reason}），本次已降级为关键词规则扫描，结论仅供初筛。\n"
                f"{body}"
            )
        return body


def _parse_model_output(result, text: str, dims: list[ComplianceDimension]) -> Optional[dict]:
    """校验并归一模型输出；任一维度都不可用时返回 None（整体降级）。"""
    obj = _parse_json_object(result.text)
    if obj is None:
        # MockProvider 返回空文本，与真实模型的空/乱输出同样处理
        return None
    block = obj.get("dimensions") if isinstance(obj.get("dimensions"), dict) else obj
    parsed: dict[str, Any] = {}
    for dim in dims:
        raw = block.get(dim.value)
        if not isinstance(raw, dict):
            continue
        score = _to_score(raw.get("score"))
        if score is None:
            continue
        findings, rejected = _normalize_findings(raw.get("findings"), text)
        parsed[dim.value] = {
            "score": score,
            "risk": _normalize_risk(raw.get("risk"), score),
            "findings": findings,
            "rejected_findings": rejected,
        }
    return parsed or None


_REASON_LABEL = {
    "empty_input": "未提供可扫描材料",
    "timeout": "调用超时",
    "unavailable": "模型不可用",
    "unparsable": "输出不可解析",
}


def _now() -> str:
    import datetime

    return datetime.datetime.now().isoformat(timespec="seconds")
