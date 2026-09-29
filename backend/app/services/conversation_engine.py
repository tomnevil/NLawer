"""会话引擎：IM 接待状态机 + 上下文维护 + 接待/派单决策（PRD 5.2）。

状态机：BOT ->（命中派单）-> WAITING_HUMAN ->（律师接入）-> HUMAN -> CLOSED。
AI 在 BOT 态处理咨询直答、判断是否进派单；进入 WAITING_HUMAN 后转人工。
"""
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.case import Case, CaseEvent
from app.models.conversation import Conversation, Message
from app.models.enums import (
    CaseStatus,
    ConversationStatus,
    DispatchMode,
    IntentType,
    MessageSender,
    MessageType,
)
from app.models.identity import User
from app.services.dispatch_service import DispatchService
from app.services.grading_service import grade_case
from app.services.intent_service import (
    intent_needs_dispatch,
    recognize_intent,
    summarize_dispute_type,
)


class ConversationEngine:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 入口 ----------------
    async def process(self, conversation_id: int, text: str, *, card: Optional[dict] = None) -> dict:
        """处理一条客户入站消息，返回 {reply, card, dispatch, status}。"""
        conv = await self._get(conversation_id)
        intent = recognize_intent(text)
        dispute_type = summarize_dispute_type(text)
        claim_amount = _parse_amount(text)
        complexity = 3 if intent == IntentType.ENTRUST else (2 if intent in (IntentType.REVIEW, IntentType.CALCULATION) else 1)

        # 维护会话上下文
        ctx = dict(conv.context or {})
        ctx["last_intent"] = intent.value
        if dispute_type:
            ctx["dispute_type"] = dispute_type
        conv.context = ctx

        need_dispatch = intent_needs_dispatch(intent, complexity_hint=complexity)
        if need_dispatch and conv.status == ConversationStatus.BOT:
            dispatch_info = await self._to_dispatch(conv, text, intent, dispute_type, claim_amount, complexity)
            conv.status = ConversationStatus.WAITING_HUMAN
            reply = f"您的问题需要律师介入，我已为您生成案件并派单（{dispatch_info['mode']}）。请稍候，律师接单后将与您联系。"
            await self._add_message(conv.id, MessageSender.AI, reply, card=dispatch_info.get("card"))
            conv.last_message_at = _now()
            return {
                "reply": reply,
                "card": dispatch_info.get("card"),
                "dispatch": dispatch_info,
                "status": conv.status.value,
            }

        # 咨询直答（四段式结构化卡片）
        answer = await self._consult_answer(text, dispute_type)
        await self._add_message(conv.id, MessageSender.AI, answer["reply"], card=answer.get("card"))
        conv.last_message_at = _now()
        return {"reply": answer["reply"], "card": answer.get("card"), "status": conv.status.value}

    # ---------------- 派单分支 ----------------
    async def _to_dispatch(
        self, conv: Conversation, text: str, intent: IntentType,
        dispute_type: Optional[str], claim_amount: float, complexity: int,
    ) -> dict:
        tenant_id = conv.tenant_id
        grade = grade_case(claim_amount=claim_amount, complexity=complexity, urgency=1, dispute_type=dispute_type)
        case = Case(
            tenant_id=tenant_id,
            case_no=_gen_case_no(tenant_id),
            title=(text or "")[:60] or "客户咨询案件",
            client_user_id=conv.client_user_id,
            conversation_id=conv.id,
            status=CaseStatus.INTAKE,
            intent=intent,
            grade=grade,
            dispute_type=dispute_type,
            claim_amount=claim_amount or None,
            complexity=complexity,
            summary=(text or "")[:500],
        )
        self.db.add(case)
        await self.db.flush()
        self.db.add(
            CaseEvent(case_id=case.id, event_type="INTAKE", title="咨询转案件", description=(text or "")[:300])
        )

        mode = DispatchMode.DESIGNATED if conv.bind_lawyer_id else DispatchMode.AUTO
        svc = DispatchService(self.db)
        disp = await svc.dispatch(case, mode=mode, bind_lawyer_id=conv.bind_lawyer_id)

        lawyer_name = None
        if disp.lawyer_id is not None:
            u = await self.db.get(User, disp.lawyer_id)
            lawyer_name = u.full_name if u else None

        card = {
            "kind": "dispatch",
            "case_no": case.case_no,
            "grade": grade.value,
            "mode": mode.value,
            "lawyer_name": lawyer_name,
            "dispute_type": dispute_type,
        }
        return {"card": card, "mode": mode.value, "case_id": case.id, "dispatch_id": disp.id}

    # ---------------- 咨询直答 ----------------
    async def _consult_answer(self, text: str, dispute_type: Optional[str]) -> dict:
        """四段式结构化回答：结论 / 法律依据 / 行动建议 / 风险提示。"""
        articles = await self._retrieve_laws(text, dispute_type)
        if not articles:
            reply = (
                "您好，我是律小智 AI 法律顾问。根据您描述的情况，建议先梳理关键事实与证据，"
                "如需进一步分析，可点击「转人工律师」获得专业支持。\n\n"
                "（本内容由 AI 生成，仅供参考，不构成正式法律意见）"
            )
            return {"reply": reply, "card": {"kind": "consult", "sections": {}}}

        top = articles[0]
        conclusion = f"根据《{top['law_name']}{top['article_no']}》，您所述情形可依法主张相应权利，建议尽快固定证据。"
        legal = "\n".join(f"- 《{a['law_name']}{a['article_no']}》：{a['content']}" for a in articles[:3])
        advice = "1. 收集并保全相关证据材料（合同、聊天记录、支付凭证等）；\n2. 明确诉求与对方主体信息；\n3. 必要时委托律师发函或提起诉讼。"
        risk = "注意时效：劳动争议仲裁时效一般为一年；人身损害赔偿诉讼时效三年。逾期可能丧失胜诉权。"
        sections = {
            "conclusion": conclusion,
            "legal_basis": legal,
            "advice": advice,
            "risk": risk,
        }
        card = {
            "kind": "consult",
            "sections": sections,
            "citations": [
                {"law_name": a["law_name"], "article_no": a["article_no"], "id": a["id"]} for a in articles[:3]
            ],
        }
        reply = f"{conclusion}\n\n【法律依据】\n{legal}\n\n【行动建议】\n{advice}\n\n【风险提示】\n{risk}\n\n（本内容由 AI 生成，仅供参考，不构成正式法律意见）"
        return {"reply": reply, "card": card}

    async def _retrieve_laws(self, text: str, dispute_type: Optional[str]) -> list[dict]:
        """从法规库检索相关条款（BM25 风格关键词匹配，演示规模足够快）。"""
        from app.models.citation import LawArticle

        query = select(LawArticle).where(LawArticle.tenant_id == "platform")
        rows = list((await self.db.execute(query)).scalars().all())
        scored = []
        for a in rows:
            score = 0
            blob = f"{a.law_name} {a.article_no} {a.content} {a.tags or ''}"
            for kw in (dispute_type, "二倍工资", "经济补偿", "违法解除", "工伤", "违约金", "解除"):
                if kw and kw in blob:
                    score += 1
            for w in (text or "").split():
                if len(w) >= 2 and w in blob:
                    score += 1
            if score > 0:
                scored.append((score, a))
        scored.sort(key=lambda x: -x[0])
        return [
            {
                "id": a.id,
                "law_name": a.law_name,
                "article_no": a.article_no,
                "content": a.content,
                "tags": a.tags,
            }
            for _, a in scored[:3]
        ]

    # ---------------- 收尾 ----------------
    async def _add_message(self, conversation_id: int, sender: MessageSender, content: str, *, card: Optional[dict] = None) -> None:
        self.db.add(
            Message(
                conversation_id=conversation_id,
                sender=sender,
                msg_type=MessageType.TEXT,
                content=content,
                card_payload=card,
            )
        )

    async def _get(self, conversation_id: int) -> Conversation:
        conv = await self.db.get(Conversation, conversation_id)
        if conv is None:
            raise NotFoundError("会话不存在", code="CONVERSATION_NOT_FOUND")
        return conv


def _parse_amount(text: str) -> float:
    """从文本粗略解析标的额（支持「X万」「X元」）。"""
    if not text:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    if m:
        return float(m.group(1)) * 10_000
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", text)
    if m:
        return float(m.group(1))
    return 0.0


def _gen_case_no(tenant_id: str) -> str:
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{tenant_id}-{ts}"


def _now() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")
