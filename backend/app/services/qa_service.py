"""智能问答：四段式结构化输出（PRD 5.8）+ SSE 流式 + 引用溯源。

四段式：结论 / 法律依据 / 行动建议 / 风险提示。
- 结论性段落必须带有效引用，落库前经 `citation_service` 强校验
- 时效标记：法条生效日期与「是否被新法替代」提示，避免引用失效条款
- SSE 协议沿用 AIAcquisition：`data: {json}\\n\\n` 逐包推送
"""
import json
import re
from typing import Any, AsyncIterator, Optional

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

# 轻量会话识别：命中则不走四段式、不调模型，直接给一句自然、礼貌的引导
_SMALLTALK = {
    "你好", "您好", "在吗", "在么", "在嘛", "在不在", "有人吗",
    "hi", "hello", "嗨", "哈喽", "好", "谢谢", "感谢", "拜拜", "再见",
}

# 寒暄的「应答式」输入：对方只是在附和/确认，没有带来新信息。
# 这类输入若送进模型，既慢又会被模型硬凑成长篇分析（体验割裂），
# 故一并短路为自然回应，避免「好呀 / 好的」触发一次数十秒的模型调用。
_ACK_WORDS = {
    "好呀", "好的", "好嘞", "好哒", "好啊", "行", "行呀", "可以", "可以呀",
    "嗯", "嗯嗯", "嗯呢", "收到", "ok", "okay", "ok啦", "没问题", "明白", "明白了",
}

# 实质法律关键词：出现任一即视为「有内容的问题」，照常交给模型分析
_LEGAL_HINTS = (
    "劳动", "合同", "工资", "薪", "加班", "社保", "工伤", "辞退", "解雇", "解除", "离职",
    "赔偿", "补偿", "违约", "欠款", "借款", "借贷", "债务", "债权", "离婚", "婚姻", "抚养",
    "继承", "遗嘱", "房产", "房屋", "租赁", "租房", "买卖", "侵权", "事故", "伤害",
    "仲裁", "诉讼", "起诉", "法院", "判决", "时效", "股权", "股东", "破产", "清算",
    "专利", "商标", "著作权", "版权", "刑事", "拘留", "取保", "判刑", "诈骗",
    "行政", "拆迁", "征收", "土地", "保险", "医疗", "竞业", "保密", "定金", "押金",
    "退款", "发票", "户口", "户籍", "遗产", "抚养费",
)

# 仅表达「想咨询」的意图词 / 问候词（在缺少事实时按引导处理）
_CONSULT_WORDS = (
    "咨询", "请教", "问一下", "问问", "问个", "求教", "求助", "帮忙", "帮我",
    "了解一下", "想了解", "有个问题", "有问题",
)
_GREETING_WORDS = ("你好", "您好", "hi", "hello", "嗨", "哈喽", "在吗", "在么")
_THANKS_WORDS = {"谢谢", "感谢", "多谢", "thanks", "thx", "谢了"}


def _is_small_talk(question: str) -> bool:
    """纯问候/致谢，或「只想咨询但未提供任何事实」的输入。

    这类输入不该套四段式——模型会硬凑出「您目前仅表明…」的空洞话术，
    读起来生硬又没温度。直接回一句自然、礼貌的引导即可。
    """
    q = (question or "").strip().lower()
    if not q:
        return True
    core = re.sub(r"[\s，。！？!?、,.~～]", "", q)
    if core in _SMALLTALK:
        return True
    # 纯应答（"好呀" "嗯嗯" "收到" 等）：无新信息，短路
    if core in _ACK_WORDS:
        return True
    # 去掉标点后极短且无实质内容
    if len(core) <= 1:
        return True
    # 已含实质法律要素：是真正的问题，交给模型
    if any(h in core for h in _LEGAL_HINTS):
        return False
    # 仅表达咨询/问候意图、未给事实：按引导处理（限短句，避免误伤长问题）
    intent = any(w in core for w in _CONSULT_WORDS) or any(g in core for g in _GREETING_WORDS)
    return intent and len(core) <= 30


def _smalltalk_reply(question: str) -> str:
    """针对寒暄/纯咨询意图的自然回应（单段纯文本，不走四段式）。"""
    core = re.sub(r"[\s，。！？!?、,.~～]", "", (question or "").strip().lower())
    if core in _THANKS_WORDS:
        return "不客气～ 如果还有其他法律问题，随时告诉我。"
    if core in {"再见", "拜拜", "88", "拜拜了"}:
        return "好的，祝您一切顺利。有需要随时找我。"
    if core in _ACK_WORDS:
        return "好的～您慢慢说，我在听。把具体情况告诉我，我帮您理一理。"
    return (
        "您好，我是您的智能法务助手，很高兴为您服务。"
        "方便的话，请把您遇到的情况告诉我——比如未签劳动合同、被违法辞退、"
        "合同违约、工伤赔偿等，我会帮您分析并给出可行的处理思路。"
    )


class QAService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 检索 ----------------
    async def _retrieve(
        self, question: str, tenant_id: str, top_k: int = 3
    ) -> tuple[list[LawArticle], list[CasePrecedent]]:
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
        # 仅返回“真正命中关键词”的法条/类案：相关度为 0 的一律不注入，
        # 避免无关问题（如「你好」）被硬塞不相干法条导致模型编造依据。
        laws = [a for a in laws if _score_law(a, question) > 0]
        precedents = [c for c in precedents if _score_case(c, question) > 0]
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

    # ---------------- 自然对话 + 后台四段式草稿 ----------------
    async def build(self, question: str, tenant_id: str, user_id: Optional[int] = None) -> dict[str, Any]:
        """生成咨询回复：客户侧为自然对话；四段式作为草稿落库（不直接展示）。

        模型单次调用返回 JSON：{answer: 自然回复, draft: 四段式要点}。
        `answer` 流式推送给客户；`draft` 存入 `ConsultReport`(草稿态)，供律师复核。
        模型不可用时降级为模板（仍可答，且仍生成草稿）。
        """
        from app.ai.router import ModelRouter, TaskType

        laws, precedents = await self._retrieve(question, tenant_id)
        knowledge = await self._tenant_knowledge(question, tenant_id)
        citations = _build_citations(laws, precedents, knowledge)
        context = _format_context(laws, precedents, knowledge)

        # 寒暄 / 纯咨询意图：自然回复，不调模型、不编造法条、不生成草稿
        if _is_small_talk(question):
            answer = _smalltalk_reply(question)
            return {
                "answer": answer,
                "sections": {"conclusion": answer, "legal_basis": "", "advice": "", "risk": ""},
                "citations": [],
                "disclaimer": DISCLAIMER,
                "structured": False,
                "draft_saved": False,
            }

        answer: Optional[str] = None
        draft: Optional[dict] = None
        try:
            result = await ModelRouter().complete(
                TaskType.QA, _build_consult_prompt(question, context), max_tokens=1500
            )
            answer, draft = _parse_consult(result.text)
        except Exception:
            pass  # 模型失败 → 降级模板

        if answer is None or draft is None:
            tmpl = _template_sections(laws, precedents, knowledge)
            answer = answer or (tmpl["conclusion"] + "\n\n" + tmpl["advice"])
            draft = draft or tmpl

        # 引用强校验作用于「四段式草稿」（正式分析部分），而非客户自然回复；
        # 校验失败仅影响草稿质量，绝不能阻断客户的自然回复。
        if citations and draft:
            try:
                validate_citations(
                    {"summary": draft.get("conclusion", ""), "legal_analysis": draft.get("legal_basis", "")},
                    [c["id"] for c in citations],
                )
            except Exception:
                pass

        draft_saved = await self._save_consult_report(
            question, answer, draft, citations, tenant_id, user_id
        )
        return {
            "answer": answer,
            "sections": {"conclusion": answer, "legal_basis": "", "advice": "", "risk": ""},
            "citations": citations,
            "disclaimer": DISCLAIMER,
            "structured": False,
            "draft_saved": draft_saved,
        }

    async def _save_consult_report(
        self, question: str, answer: str, draft: dict, citations: list, tenant_id: str, user_id: Optional[int]
    ) -> bool:
        """best-effort 落库四段式草稿（CONSULT_REPORT 草稿态，客户不可见），并自动入复核。"""
        try:
            from app.models.consult_report import ConsultReport, ConsultReportStatus
            from app.services.consult_review import dispatch_consult_report_review

            report = ConsultReport(
                tenant_id=tenant_id,
                user_id=user_id,
                question=question,
                answer=answer,
                draft_sections=draft,
                citations=citations,
                status=ConsultReportStatus.DRAFT,
            )
            self.db.add(report)
            await self.db.flush()  # 先拿到 report.id 再建复核任务
            await dispatch_consult_report_review(self.db, report, tenant_id)
            await self.db.commit()
            return True
        except Exception:
            return False

    # ---------------- SSE 流式 ----------------
    async def stream(self, question: str, tenant_id: str, user_id: Optional[int] = None) -> AsyncIterator[str]:
        """按 `data: {json}\n\n` 逐包推送（对齐 AIAcquisition SSE 协议）。

        客户侧始终为自然对话（`structured: false`，不渲染四段式骨架）；
        四段式草稿已落库，不在本流中回传。

        为什么先发 `status` + 心跳：`build()` 会同步等待模型（实测偶发数十秒）。
        若这段时间一个字节都不发，浏览器 / 反向代理会把「长时间无数据」的连接
        判定为断流——用户看到的正是「生成中断 / network error」。因此这里：
        ① 首包立即发「正在分析」（首字延迟也为 0）；
        ② 等待期间每 8s 发一个 SSE 注释包（`: keep-alive`，前端解析器会忽略，
           仅用于保活）；超过 60s 再补一句更体面的进度提示。
        ③ `build` 抛出的意外（检索/落库等）一律降级为一段自然的安抚回复，
           保证流始终以 `done` 正常收尾，绝不把技术异常甩给客户。
        """
        import asyncio
        import time as _time

        yield _sse({"type": "status", "message": "正在为您分析…"})

        task = asyncio.create_task(self.build(question, tenant_id, user_id=user_id))
        start = _time.monotonic()
        slow_notified = False
        while not task.done():
            await asyncio.sleep(8)
            if task.done():
                break
            if not slow_notified and _time.monotonic() - start > 60:
                slow_notified = True
                yield _sse({"type": "status", "message": "这个问题稍复杂，我正在仔细梳理，请再稍等片刻…"})
            else:
                yield ": keep-alive\n\n"

        try:
            payload = task.result()
        except Exception:
            payload = None

        if payload is None:
            answer = (
                "抱歉，刚才这一步没能顺利完成。您可以稍后再试一次，"
                "或者把问题描述得更具体一些（比如涉及的时间、金额、对方情况），我马上帮您分析。"
            )
            citations: list = []
            disclaimer = DISCLAIMER
        else:
            answer = payload["answer"]
            citations = payload["citations"]
            disclaimer = payload["disclaimer"]

        for piece in _chunks(answer, size=24):
            yield _sse({"type": "delta", "content": piece})
        yield _sse(
            {
                "type": "done",
                "structured": False,
                "sections": {"conclusion": answer},
                "citations": citations,
                "disclaimer": disclaimer,
            }
        )


# ---------------- 模块级辅助 ----------------
def _build_citations(laws, precedents, knowledge) -> list[dict]:
    """把检索到的法条/类案/企业知识整理成引用溯源清单。"""
    return [
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


def _format_context(laws, precedents, knowledge) -> str:
    """拼接检索上下文，供模型 grounding 使用。"""
    parts: list[str] = []
    for a in laws:
        parts.append(f"- 法条《{a.law_name}{a.article_no}》（{a.effective_date or '生效日期未标注'}）：{(a.content or '')[:300]}")
    for c in precedents:
        parts.append(f"- 类案《{c.case_no} {c.title}》：{(c.holding or '')[:200]}")
    for k in knowledge:
        parts.append(f"- 企业知识《{k['title']}》：{k['excerpt']}")
    return "\n".join(parts) if parts else "（无可用法条/类案，请基于一般法律原则回答）"


def _build_consult_prompt(question: str, context: str) -> str:
    return (
        f"用户法律问题：{question}\n\n"
        f"参考材料（如有）：\n{context}\n\n"
        "你是一位耐心、亲切的中国执业律师助手，正和当事人面对面沟通。\n"
        "请先给用户一段自然、口语化的对话式回复（像律师朋友在聊天）：先共情、正面回应诉求，"
        "再给可操作的初步思路；不要使用「结论：」「法律依据：」这类标题。\n"
        "同时作为内部工作草稿，严谨整理一份四段式要点（结论/法律依据/行动建议/风险提示），"
        "它不会直接展示给用户。\n"
        "仅输出如下 JSON，不要任何额外文字：\n"
        "{\n"
        '  "answer": "给当事人的自然对话回复",\n'
        '  "draft": {\n'
        '    "conclusion": "一句结论性判断",\n'
        '    "legal_basis": "对应法条要点，可引用《法名》第X条，不要编造条号",\n'
        '    "advice": "可执行的行动建议",\n'
        '    "risk": "时效/证据等风险提示"\n'
        "  }\n"
        "}\n"
        "若信息明显不足，draft 各字段可留空字符串，但在 answer 中温和地请对方补充关键事实。"
    )


def _parse_consult(text: str) -> tuple[Optional[str], Optional[dict]]:
    """从模型输出解析 {answer, draft}；容错去 ```json 围栏、截取首个 {...}。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, None
    try:
        obj = json.loads(t[start : end + 1])
    except Exception:
        return None, None
    answer = str(obj.get("answer") or "").strip()
    draft_raw = obj.get("draft") or {}
    draft = {
        "conclusion": str(draft_raw.get("conclusion", "") or ""),
        "legal_basis": str(draft_raw.get("legal_basis", "") or ""),
        "advice": str(draft_raw.get("advice", "") or ""),
        "risk": str(draft_raw.get("risk", "") or ""),
    }
    if not answer:
        return None, None
    return answer, draft


def _template_sections(laws, precedents, knowledge) -> dict[str, str]:
    """模型不可用时的模板兜底（保持可答）。"""
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
    return {"conclusion": conclusion, "legal_basis": legal_basis, "advice": advice, "risk": risk}


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
    """英文/数字按空白分词；中文无天然空格，按字符 bigram 切分以做关键词重叠匹配。

    旧实现对中文整句只产出 1 个 token（isalnum 对汉字恒真且无空格），
    导致任意中文问句与法条的重合度永远为 0、检索完全失效。
    """
    text = text or ""
    parts = "".join(ch if ch.isalnum() else " " for ch in text).split()
    tokens = [t for t in parts if len(t) >= 2]
    cjk = "".join(ch for ch in text if "一" <= ch <= "鿿")
    for i in range(len(cjk) - 1):
        tokens.append(cjk[i : i + 2])
    return tokens


def _overlap_count(blob: str, question: str) -> int:
    b = set(_tokens(blob))
    q = set(_tokens(question))
    return len(b & q)


def _overlap(blob: str, question: str) -> bool:
    return _overlap_count(blob, question) > 0
