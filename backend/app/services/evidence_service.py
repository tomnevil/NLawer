"""证据材料智能整理（PRD 5.4）。

多模态解析 -> 五类自动归类 -> 要素抽取（日期/金额/主体/签章）
-> 缺失清单与分轮智能追问 -> 事件时间线 -> 合法性风险提示。

解析为可插拔实现：MVP 用「文件名 + 文本关键词」规则模拟 OCR 结果；
接入真实 OCR/多模态模型后替换 `_mock_ocr` 即可，其余流程不变。
"""
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, NotFoundError
from app.models.case import Case, CaseEvent
from app.models.enums import EvidenceCategory, EvidenceStatus
from app.models.evidence import Evidence, EvidenceChecklist

# ---- 五类归类关键词 ----
_CATEGORY_KEYWORDS: dict[EvidenceCategory, tuple[str, ...]] = {
    EvidenceCategory.CONTRACT: ("合同", "协议", "条款", "订单"),
    EvidenceCategory.PAYMENT: ("支付", "转账", "流水", "工资", "发票", "收据", "回单", "账单"),
    EvidenceCategory.COMMUNICATION: ("聊天", "微信", "邮件", "短信", "通话", "记录", "截图"),
    EvidenceCategory.IDENTITY: ("身份", "营业执照", "身份证", "护照", "主体"),
    EvidenceCategory.OFFICIAL: ("判决", "裁定", "仲裁", "认定", "通知", "决定书", "公证书"),
}

_DATE_RE = re.compile(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})")
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万元|元|万)")
_PARTY_RE = re.compile(r"(甲方|乙方|原告|被告|申请人|被申请人)\s*[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})")
_SEAL_RE = re.compile(r"(盖章|签章|签字|印章|电子签)")


class EvidenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 解析 ----------------
    async def parse(self, evidence_id: int, tenant_id: Optional[str] = None) -> Evidence:
        """解析证据。

        `tenant_id` 传入时会执行归属校验——worker 从 job 恢复执行时同样应传入，
        避免仅凭 evidence_id 即可解析他人证据（IDOR 纵深防御）。
        """
        ev = await self.db.get(Evidence, evidence_id)
        if ev is None:
            raise NotFoundError("证据不存在", code=ErrorCode.EVIDENCE_NOT_FOUND)
        if tenant_id is not None and ev.tenant_id != tenant_id:
            raise NotFoundError("证据不存在", code=ErrorCode.EVIDENCE_NOT_FOUND)

        ev.status = EvidenceStatus.PARSING
        try:
            text = self._mock_ocr(ev)
            ev.ocr_text = text
            cat, conf = self.classify(ev.name, text)
            ev.category = cat
            ev.category_confidence = conf
            ev.extracted = self.extract_elements(text)
            ev.legality_risks = self.legality_risks(ev.name, text)
            ev.event_date = (ev.extracted.get("dates") or [None])[0]
            ev.status = EvidenceStatus.PARSED
            ev.parse_error = None
        except Exception as exc:  # noqa: BLE001  单份材料解析失败不影响其他材料
            ev.status = EvidenceStatus.FAILED
            ev.parse_error = str(exc)
        return ev

    def _mock_ocr(self, ev: Evidence) -> str:
        """占位 OCR：真实实现接多模态模型，此处用文件名 + 元信息拼装。"""
        return f"{ev.name}（类型 {ev.file_type or '未知'}，大小 {ev.file_size or 0} 字节）"

    def classify(self, name: str, text: str) -> tuple[EvidenceCategory, float]:
        blob = f"{name} {text}"
        best, hits = EvidenceCategory.OTHER, 0
        for cat, words in _CATEGORY_KEYWORDS.items():
            n = sum(1 for w in words if w in blob)
            if n > hits:
                best, hits = cat, n
        confidence = min(0.95, 0.5 + 0.15 * hits) if hits else 0.3
        return best, round(confidence, 2)

    def extract_elements(self, text: str) -> dict[str, Any]:
        dates = [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in _DATE_RE.findall(text)]
        amounts = []
        for num, unit in _AMOUNT_RE.findall(text):
            val = float(num) * (10_000 if "万" in unit else 1)
            amounts.append({"amount": val, "raw": f"{num}{unit}"})
        parties = [{"role": r, "name": n} for r, n in _PARTY_RE.findall(text)]
        return {
            "dates": dates,
            "amounts": amounts,
            "parties": parties,
            "has_seal": bool(_SEAL_RE.search(text)),
        }

    def legality_risks(self, name: str, text: str) -> list[dict[str, str]]:
        risks: list[dict[str, str]] = []
        if any(k in name for k in ("复印", "截图", "照片", "扫描")):
            risks.append({"level": "MEDIUM", "tip": "疑似非原件，证明力可能受限，建议核对原件"})
        if "聊天" in name or "微信" in name:
            risks.append({"level": "MEDIUM", "tip": "电子数据建议保留原始载体或办理公证"})
        if not _SEAL_RE.search(text):
            risks.append({"level": "LOW", "tip": "未识别到签章信息，建议确认文件是否盖章/签字"})
        return risks

    # ---------------- 缺失清单与分轮追问 ----------------
    async def missing_list(self, case_id: int) -> list[dict[str, Any]]:
        case = await self.db.get(Case, case_id)
        if case is None:
            raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)

        checklists = list(
            (
                await self.db.execute(
                    select(EvidenceChecklist).where(
                        EvidenceChecklist.tenant_id.in_([case.tenant_id, "platform"]),
                        EvidenceChecklist.dispute_type == (case.dispute_type or ""),
                    )
                )
            ).scalars().all()
        )
        uploaded = list(
            (await self.db.execute(select(Evidence).where(Evidence.case_id == case_id))).scalars().all()
        )
        have = {e.category.value for e in uploaded}
        return [
            {
                "item": c.item,
                "category": c.category.value,
                "priority": c.priority,
                "description": c.description,
                "missing": c.category.value not in have,
            }
            for c in sorted(checklists, key=lambda x: x.priority)
        ]

    async def ask_rounds(self, case_id: int) -> list[dict[str, Any]]:
        """分轮智能追问：先关键（priority=1）后补充（priority>=2）。"""
        items = [m for m in await self.missing_list(case_id) if m["missing"]]
        rounds: list[dict[str, Any]] = []
        for prio, label in ((1, "第一轮：关键材料"), (2, "第二轮：重要材料"), (3, "第三轮：补充材料")):
            group = [i for i in items if i["priority"] == prio]
            if group:
                rounds.append({"round": prio, "label": label, "items": group})
        return rounds

    # ---------------- 事件时间线 ----------------
    async def timeline(self, case_id: int) -> list[dict[str, Any]]:
        evs = list(
            (
                await self.db.execute(
                    select(Evidence).where(
                        Evidence.case_id == case_id, Evidence.event_date.isnot(None)
                    )
                )
            ).scalars().all()
        )
        nodes = [
            {
                "date": e.event_date,
                "type": "EVIDENCE",
                "title": e.name,
                "category": e.category.value,
            }
            for e in evs
            if e.event_date
        ]
        case_events = list(
            (
                await self.db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id))
            ).scalars().all()
        )
        nodes += [
            {"date": c.occurred_at or "", "type": "CASE_EVENT", "title": c.title, "description": c.description}
            for c in case_events
        ]
        return sorted(nodes, key=lambda n: n["date"] or "")
