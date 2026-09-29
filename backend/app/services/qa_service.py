"""智能问答：四段式结构化输出（PRD 5.8）+ SSE 流式 + 引用溯源。

四段式：结论 / 法律依据 / 行动建议 / 风险提示。
- 结论性段落必须带有效引用，落库前经 `citation_service` 强校验
- 时效标记：法条生效日期与「是否被新法替代」提示，避免引用失效条款
- SSE 协议沿用 AIAcquisition：`data: {json}\n\n` 逐包推送
"""
import json
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.citation import CasePrecedent, LawArticle
from app.models.knowledge import KnowledgeDoc
from app.services.citation_service import validate_citations

DISCLAIMER = "本内容由 AI 生成，仅供参考，不构成正式法律意见。"

# 已被新法替代的旧法提示（演示用，真实场景应维护法规版本表）
_SUPERSEDED_HINT = {
    "中华人民共和国合同法": "已被《民法典》废止，请引用民法典对应条款",
    "中华人民共和国侵权责任法": "已被《民法典》废止，请引用民法典对应条款",
    "中华人民共和国婚姻法": "已被《民法典》废止，请引用民法典对应条款",
}


class QAService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 检索 ----------------
    async def _retrieve(self, question: str, tenant_id: str, top_k: int = 3) -> tuple[list[LawArticle], list[CasePrecedent]]:
        laws = list(
            (await self.db.execute(select(LawArticle).where(LawArticle.tenant_id.in_([tenant_id, "platform"]))))
            .scalars()
            .all()
        )
        precedents = list(
            (await self.db.execute(select(CasePrecedent).where(CasePrecedent.tenant_id.in_([tenant_id, "platform"]))))
            .scalars()
            .all()
        )
        laws.sort(key=lambda a: -_score_law(a, question))
        precedents.sort(key=lambda c: -_score_case(c, question))
        return laws[:top_k], precedents[:2]

    async def _tenant_knowledge(self, question: str, tenant_id: str, top_k: int = 2) -> list[dict]:
        """企业私有知识（租户隔离：强制按 tenant_id 过滤）。"""
        docs = list(
            (await self.db.execute(select(KnowledgeDoc).where(KnowledgeDoc.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        hits = [d for d in docs if _overlap(d.content or "", question)]
        return [
            {"id": d.id, "title": d.title, "doc_type": d.doc_type, "excerpt": (d.content or "")[:160]}
            for d in hits[:top_k]
        ]

    # ---------------- 四段式生成 ----------------
    async def build(self, question: str, tenant_id: str) -> dict[str, Any]:
        laws, precedents = await self._retrieve(question, tenant_id)
        knowledge = await self._tenant_knowledge(question, tenant_id)

        if not laws and not knowledge:
            return {
                "sections": {
                    "conclusion": "未检索到明确对应的法规条款，建议补充事实细节或转人工律师。",
                    "legal_basis": "",
                    "advice": "请描述具体情形（时间、主体、金额、是否已签合同等），以便精准匹配。",
                    "risk": DISCLAIMER,
                },
                "citations": [],
                "disclaimer": DISCLAIMER,
            }

        top = laws[0] if laws else None
        if top is not None:
            conclusion = (
                f"依据《{top.law_name}{top.article_no}》，您所述情形可依法主张相应权利；"
                f"结合检索到的{len(precedents)}个类案，建议先固定证据再选择维权路径。"
            )
            legal_basis = "\n".join(
                f"- 《{a.law_name}{a.article_no}》（{a.effective_date or '生效日期未标注'}）：{(a.content or '')[:180]}"
                for a in laws
            )
            hint = _SUPERSEDED_HINT.get(top.law_name)
            if hint:
                legal_basis += f"\n- 时效提示：{hint}"
        else:
            conclusion = "依据贵司内部制度与历史咨询，初步判断如下。"
            legal_basis = ""

        advice = "\n".join(
            f"{i}. {s}" for i, s in enumerate(
                ["收集并保全相关证据材料", "明确相对方主体与诉求金额", "必要时委托律师发函或提起诉讼"], 1
            )
        )
        risk = "注意时效限制（劳动争议仲裁时效一年、普通诉讼时效三年），逾期可能丧失胜诉权。" + DISCLAIMER

        sections = {
            "conclusion": conclusion,
            "legal_basis": legal_basis,
            "advice": advice,
            "risk": risk,
        }
        citations = [
            {
                "id": a.id,
                "type": "LAW",
                "title": f"{a.law_name}{a.article_no}",
                "excerpt": (a.content or "")[:200],
                "effective_date": a.effective_date,
                "timeliness_warning": _SUPERSEDED_HINT.get(a.law_name),
            }
            for a in laws
        ] + [
            {
                "id": c.id,
                "type": "CASE",
                "title": f"{c.case_no} {c.title}",
                "excerpt": (c.holding or "")[:200],
                "court": c.court,
                "judgment_date": c.judgment_date,
            }
            for c in precedents
        ] + [
            {"id": k["id"], "type": "KNOWLEDGE", "title": k["title"], "excerpt": k["excerpt"]}
            for k in knowledge
        ]

        # 引用强校验：结论性内容必须有引用
        validate_citations({"summary": conclusion, "legal_analysis": legal_basis}, [c["id"] for c in citations])

        return {"sections": sections, "citations": citations, "disclaimer": DISCLAIMER}

    # ---------------- SSE 流式 ----------------
    async def stream(self, question: str, tenant_id: str) -> AsyncIterator[str]:
        """按 `data: {json}\\n\\n` 逐包推送（对齐 AIAcquisition SSE 协议）。"""
        payload = await self.build(question, tenant_id)
        text = payload["sections"]["conclusion"] + "\n\n" + payload["sections"]["legal_basis"]

        # 逐句推送，模拟打字机效果
        for piece in _chunks(text, size=24):
            yield _sse({"type": "delta", "content": piece})
        yield _sse({"type": "done", "sections": payload["sections"], "citations": payload["citations"],
                    "disclaimer": payload["disclaimer"]})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _chunks(text: str, size: int = 24):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _score_law(a: LawArticle, question: str) -> int:
    blob = f"{a.law_name} {a.content or ''} {a.tags or ''}"
    return _overlap_count(blob, question)


def _score_case(c: CasePrecedent, question: str) -> int:
    blob = f"{c.title or ''} {c.holding or ''} {c.dispute_type or ''}"
    return _overlap_count(blob, question)


def _tokens(text: str) -> list[str]:
    return [t for t in "".join(ch if ch.isalnum() else " " for ch in (text or "")).split() if len(t) >= 2]


def _overlap_count(blob: str, question: str) -> int:
    b = set(_tokens(blob))
    q = set(_tokens(question))
    return len(b & q)


def _overlap(blob: str, question: str) -> bool:
    return _overlap_count(blob, question) > 0
