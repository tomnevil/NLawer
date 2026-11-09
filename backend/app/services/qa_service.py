"""智能问答：四段式结构化输出（PRD 5.8）+ SSE 流式 + 引用溯源。

四段式：结论 / 法律依据 / 行动建议 / 风险提示。
- 结论性段落必须带有效引用，落库前经 `citation_service` 强校验
- 时效标记：法条生效日期与「是否被新法替代」提示，避免引用失效条款
- SSE 协议沿用 AIAcquisition：`data: {json}\\n\\n` 逐包推送
"""
import json
import re
from typing import Any, AsyncIterator, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.models.citation import CasePrecedent, LawArticle
from app.models.knowledge import KnowledgeDoc
from app.services.citation_service import validate_citations

DISCLAIMER = "本内容由 AI 生成，仅供参考，不构成正式法律意见。"

# 生成失败时给客户的兜底话术（B3）：不再用 400 把技术错误甩给客户。
# 与流式 `failed` 分支共用同一段文本，保证 REST / SSE 两条链路对客户语义一致。
FALLBACK_ANSWER = (
    "抱歉，这次没能给出可靠的分析结果。为避免误导您，我先不展开结论。"
    "您可以稍后再试一次，或者补充一些关键事实（发生时间、金额、对方主体、手上有哪些证据），"
    "我马上重新帮您梳理；也可以直接联系人工律师为您解答。"
)

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
    async def _grounding(self, question: str, tenant_id: str):
        """检索 + 组装引用与上下文（REST / 流式 / 后台草稿共用）。"""
        laws, precedents = await self._retrieve(question, tenant_id)
        knowledge = await self._tenant_knowledge(question, tenant_id)
        citations = _build_citations(laws, precedents, knowledge)
        context = _format_context(laws, precedents, knowledge)
        return laws, precedents, knowledge, citations, context

    async def build(self, question: str, tenant_id: str, user_id: Optional[int] = None) -> dict[str, Any]:
        """生成咨询回复：客户侧为自然对话；四段式作为草稿落库（不直接展示）。

        模型单次调用返回 JSON：{answer: 自然回复, draft: 四段式要点}。
        `answer` 直接返回给客户；`draft` 存入 `ConsultReport`(草稿态)，供律师复核。

        **B3 兜底语义**：草稿引用校验失败（`CITATION_MISSING`）不再抛 400。
        改为返回正常响应 + `FALLBACK_ANSWER`，并带上 `degraded` / `degrade_reason`；
        `billable=False` 供请求边界跳过计费。此时不落草稿、不建复核、不留空单。
        """
        from app.ai.router import ModelRouter, TaskType

        laws, precedents, knowledge, citations, context = await self._grounding(question, tenant_id)

        # 寒暄 / 纯咨询意图：自然回复，不调模型、不编造法条、不生成草稿
        if _is_small_talk(question):
            answer = _smalltalk_reply(question)
            return {
                "answer": answer,
                # 没有草稿 ⇒ 四段式必须为空，避免「sections 有内容但 draft_saved=False」
                "sections": _normalize_draft(None),
                "citations": [],
                "disclaimer": DISCLAIMER,
                "structured": False,
                "draft_saved": False,
                "degraded": False,
                "degrade_reason": "",
                "billable": True,
            }

        answer: Optional[str] = None
        draft: Optional[dict] = None
        degraded = False
        degrade_reason = ""
        try:
            result = await ModelRouter().complete(
                TaskType.QA,
                # 有检索来源时要求四段式必须写满；无来源时保持旧口径（草稿可空）。
                # 无来源时强制出草稿会撞引用强校验，把可用的客户回复直接降级。
                _build_consult_prompt(question, context, has_sources=bool(citations)),
                max_tokens=1500,
            )
            answer, draft = _parse_consult(result.text)
        except Exception as exc:  # noqa: BLE001 - 模型失败 → 模板兜底
            degrade_reason = "MODEL_FALLBACK"
            logger.warning("咨询模型调用失败，回落模板：{}", exc)

        if answer is None or draft is None:
            tmpl = _template_sections(laws, precedents, knowledge)
            answer = answer or (tmpl["conclusion"] + "\n\n" + tmpl["advice"])
            draft = draft or tmpl
            degraded = True
            degrade_reason = degrade_reason or "MODEL_FALLBACK"

        # N2：/qa 返回的 sections 必须与落库的 draft_sections 完全同源。
        draft = _normalize_draft(draft)
        draft_saved = False
        billable = True
        if _has_draft_content(draft):
            # 引用强校验作用于「四段式草稿」（正式分析部分），而非客户自然回复。
            try:
                validate_citations(
                    {
                        "summary": draft.get("conclusion", ""),
                        "legal_analysis": draft.get("legal_basis", ""),
                    },
                    [c["id"] for c in citations],
                )
            except BadRequestError as exc:
                # B3：引用缺失 = 草稿生成失败。对客户不暴露错误码：
                # 返回 200 + 友好话术；不落空草稿、不建复核、不计费。
                await self._note_degraded(question, tenant_id, user_id, "CITATION_MISSING", exc)
                draft = _normalize_draft(None)
                answer = FALLBACK_ANSWER
                draft_saved = False
                billable = False
                degraded = True
                degrade_reason = "CITATION_MISSING"
            else:
                draft_saved = await self._save_consult_report(
                    question, answer, draft, citations, tenant_id, user_id
                )
        return {
            "answer": answer,
            "sections": draft,
            "citations": citations,
            "disclaimer": DISCLAIMER,
            "structured": False,
            "draft_saved": draft_saved,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "billable": billable,
        }

    async def _note_degraded(
        self,
        question: str,
        tenant_id: str,
        user_id: Optional[int],
        reason: str,
        exc: Exception,
    ) -> None:
        """降级必须可观测：日志 + 独立事务审计（主事务回滚也不丢）。"""
        message = getattr(exc, "message", None) or str(exc)
        logger.warning(
            "咨询生成降级（{}）：{} | tenant={} user={} question_len={}",
            reason,
            message,
            tenant_id,
            user_id,
            len(question or ""),
        )
        try:
            from app.core.audit import AuditAction, log_detached_ctx

            await log_detached_ctx(
                AuditAction.QA_DEGRADED,
                "qa",
                actor_id=user_id,
                tenant_id=tenant_id,
                success=False,
                # 只留原因与长度，不落提问原文（客户咨询内容属敏感信息）
                detail={"reason": reason, "code": reason, "question_len": len(question or "")},
            )
        except Exception as audit_exc:  # noqa: BLE001 - 留痕失败不阻断回复
            logger.warning("咨询降级审计写入失败：{}", audit_exc)

    # ---------------- 流式：回答先行，草稿后台生成 ----------------
    async def answer_only(
        self, question: str, tenant_id: str, user_id: Optional[int] = None
    ) -> dict[str, Any]:
        """只生成客户自然回复（流式用），不产草稿。

        草稿由调用方在响应结束后用**独立会话**交给 `generate_draft` 生成，
        这样客户不必等草稿（实测草稿占 completion 的一半以上）。
        本方法永不抛异常：失败返回 `ok=False`，由流式走安抚分支。
        """
        from app.ai.router import ModelRouter, TaskType

        try:
            laws, precedents, knowledge, citations, context = await self._grounding(question, tenant_id)
        except Exception as exc:  # noqa: BLE001 - 检索失败也走安抚分支
            logger.warning("流式问答检索失败：{}", exc)
            return {"ok": False, "answer": "", "citations": [], "disclaimer": DISCLAIMER,
                    "degraded": True, "degrade_reason": "RETRIEVAL_FAILED"}

        if _is_small_talk(question):
            return {
                "ok": True,
                "answer": _smalltalk_reply(question),
                "citations": [],
                "disclaimer": DISCLAIMER,
                "degraded": False,
                "degrade_reason": "",
            }

        try:
            result = await ModelRouter().complete(
                TaskType.QA,
                _build_answer_prompt(question, context, has_sources=bool(citations)),
                max_tokens=800,
            )
            answer = _parse_answer(result.text)
        except Exception as exc:  # noqa: BLE001 - 流式必须收尾成 done
            logger.warning("流式问答生成失败：{}", exc)
            return {"ok": False, "answer": "", "citations": citations, "disclaimer": DISCLAIMER,
                    "degraded": True, "degrade_reason": "MODEL_UNAVAILABLE"}

        if not answer:
            logger.warning("流式问答模型输出不可解析：{}", (result.text or "")[:80])
            return {"ok": False, "answer": "", "citations": citations, "disclaimer": DISCLAIMER,
                    "degraded": True, "degrade_reason": "UNPARSABLE"}

        return {
            "ok": True,
            "answer": answer,
            "citations": citations,
            "disclaimer": DISCLAIMER,
            "degraded": False,
            "degrade_reason": "",
        }

    async def generate_draft(
        self, question: str, answer: str, tenant_id: str, user_id: Optional[int] = None
    ) -> bool:
        """后台生成四段式草稿（`/qa/stream` 专用），返回是否已落库。

        调用方必须给它**独立 session 与独立事务**（不复用请求会话），
        失败只留痕、不影响已返回给客户的回答，也不触发任何计费。
        """
        from app.ai.router import ModelRouter, TaskType

        if _is_small_talk(question):
            return False
        try:
            laws, precedents, knowledge, citations, context = await self._grounding(question, tenant_id)
            result = await ModelRouter().complete(
                TaskType.QA,
                _build_draft_prompt(question, context, has_sources=bool(citations)),
                max_tokens=900,
            )
            draft = _normalize_draft(_parse_draft(result.text))
            if not _has_draft_content(draft):
                logger.warning("后台草稿为空，跳过落库（tenant={} user={}）", tenant_id, user_id)
                return False
            validate_citations(
                {"summary": draft.get("conclusion", ""), "legal_analysis": draft.get("legal_basis", "")},
                [c["id"] for c in citations],
            )
        except BadRequestError as exc:
            await self._note_degraded(question, tenant_id, user_id, "CITATION_MISSING", exc)
            return False
        except Exception as exc:  # noqa: BLE001 - 后台任务失败不影响客户回答
            await self._note_degraded(question, tenant_id, user_id, "MODEL_UNAVAILABLE", exc)
            return False

        return await self._save_consult_report(question, answer, draft, citations, tenant_id, user_id)

    async def _save_consult_report(
        self, question: str, answer: str, draft: dict, citations: list, tenant_id: str, user_id: Optional[int]
    ) -> bool:
        """落库四段式草稿（CONSULT_REPORT 草稿态，客户不可见），并自动入复核。

        **事务边界（B3）**：本方法只 `flush`，**不 commit**。
        由 `/qa`、`/qa/stream` 的请求边界统一提交，保证「草稿 + 复核任务 +
        计费流水」在同一事务里要么全成、要么全不成。此前服务层自行 commit，
        调用方无法把后续步骤（如计费）纳入同一事务，失败时留下半截数据。

        失败不阻断客户回复，但**记录日志**，不再无声无息。
        空草稿不落库、不建复核：`draft_saved=False` 必须与「没有复核工单」一致，
        否则律师复核台会收到无法复核的空单（N2）。
        """
        if not _has_draft_content(draft):
            return False
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
            return True
        except Exception as exc:  # noqa: BLE001 - 草稿落库失败不影响客户回复
            logger.warning("咨询草稿落库失败（不阻断回复）：{}", exc)
            return False

    # ---------------- SSE 流式 ----------------
    async def stream(self, question: str, tenant_id: str, user_id: Optional[int] = None) -> AsyncIterator[str]:
        """按 `data: {json}\n\n` 逐包推送（对齐 AIAcquisition SSE 协议）。

        **回答先行、草稿后置**：本流只做一次「回答专用」模型调用（不产草稿），
        草稿由端点拿到 `draft_deferred=True` 后用独立会话在后台生成。
        客户不必再等草稿那半份 completion，首字与完成都更早。

        为什么先发 `status` + 心跳：模型调用实测偶发数十秒。若这段时间
        一个字节都不发，浏览器 / 反向代理会把「长时间无数据」判为断流——
        用户看到的正是「生成中断 / network error」。因此：
        ① 首包立即发「正在分析」（首字延迟为 0）；
        ② 等待期间每 8s 发一个 SSE 注释包（`: keep-alive`）保活，超过 60s
           再补一句更体面的进度提示；
        ③ 任何意外都收尾为 `done(failed=True)` + 安抚话术，绝不甩技术异常。
        """
        import asyncio
        import time as _time

        yield _sse({"type": "status", "message": "正在为您分析…"})

        task = asyncio.create_task(self.answer_only(question, tenant_id, user_id=user_id))
        start = _time.monotonic()
        slow_notified = False
        while not task.done():
            # 必须「等答案或等 8s，先到先返回」：直接 sleep(8) 会让**每一次**
            # 问答都至少多等 8 秒（模型 2s 就答完也照样空等），把首字延迟
            # 从「模型耗时」劣化成「8s + 模型耗时」，与流式的意义相反。
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except asyncio.TimeoutError:
                if not slow_notified and _time.monotonic() - start > 60:
                    slow_notified = True
                    yield _sse({"type": "status", "message": "这个问题稍复杂，我正在仔细梳理，请再稍等片刻…"})
                else:
                    yield ": keep-alive\n\n"
            except Exception:  # noqa: BLE001 - 任务异常留到下方统一收尾为 done(failed)
                break

        try:
            payload = task.result()
        except Exception:
            payload = None

        failed = payload is None or not payload.get("ok", False)
        if failed:
            answer = FALLBACK_ANSWER
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
                # 供请求边界决定是否计费：失败降级的安抚回复不算成功生成
                "failed": failed,
                # 降级标记沿用 `degraded` 口径，供前端/运维识别
                "degraded": bool(failed or (payload or {}).get("degraded")),
                "degrade_reason": "" if failed else (payload or {}).get("degrade_reason", ""),
                # 由端点据此在响应结束后调度后台草稿任务（失败/寒暄不生成）
                "draft_deferred": (not failed) and not _is_small_talk(question),
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


def _build_consult_prompt(question: str, context: str, has_sources: Optional[bool] = None) -> str:
    """构造咨询提示词。

    `has_sources` 控制草稿口径：
    - True（有检索来源）：四段式必须写满，避免模型把「信息不足」当成整段留空的借口；
    - False（无来源）：允许草稿留空，避免强制出草稿后撞引用强校验。
    """
    if has_sources is None:
        has_sources = bool(context.strip()) and not context.lstrip().startswith("（无可用法条")
    draft_rule = (
        "同时作为内部工作草稿，整理一份**完整**的四段式要点（结论/法律依据/行动建议/风险提示），"
        "它不会直接展示给用户。四段都必须有内容：事实不全时给出条件式判断"
        "（例如「若…则可主张…」），并注明还需补充的关键事实；"
        "legal_basis 只能引用参考材料中出现过的法条，不得编造条号。\n"
        if has_sources
        else "同时作为内部工作草稿，严谨整理一份四段式要点（结论/法律依据/行动建议/风险提示），"
        "它不会直接展示给用户。\n"
    )
    empty_rule = (
        ""
        if has_sources
        else "若信息明显不足，draft 各字段可留空字符串，但在 answer 中温和地请对方补充关键事实。"
    )
    return (
        f"用户法律问题：{question}\n\n"
        f"参考材料（如有）：\n{context}\n\n"
        "你是一位耐心、亲切的中国执业律师助手，正和当事人面对面沟通。\n"
        "请先给用户一段自然、口语化的对话式回复（像律师朋友在聊天）：先共情、正面回应诉求，"
        "再给可操作的初步思路；不要使用「结论：」「法律依据：」这类标题。\n"
        f"{draft_rule}"
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
        f"{empty_rule}"
    )


def _build_answer_prompt(question: str, context: str, has_sources: bool) -> str:
    """流式专用：只要求自然回复，不产草稿（草稿由后台任务另生成）。"""
    basis_rule = (
        "如需引用法条，只能引用参考材料中出现过的条文，不得编造条号。\n"
        if has_sources
        else "参考材料中没有可用法条，请基于一般法律原则回答，不要编造具体条号。\n"
    )
    return (
        f"用户法律问题：{question}\n\n"
        f"参考材料（如有）：\n{context}\n\n"
        "你是一位耐心、亲切的中国执业律师助手，正和当事人面对面沟通。\n"
        "请给出一段自然、口语化的对话式回复：先共情、正面回应诉求，再给可操作的初步思路；"
        "不要使用「结论：」「法律依据：」这类标题。\n"
        f"{basis_rule}"
        "仅输出如下 JSON，不要任何额外文字：\n"
        '{"answer": "给当事人的自然对话回复"}'
    )


def _build_draft_prompt(question: str, context: str, has_sources: bool) -> str:
    """后台草稿专用：只要四段式，不要自然回复（回答已经在流里发过了）。"""
    fill_rule = (
        "四段都必须有内容：事实不全时给出条件式判断（例如「若…则可主张…」），"
        "并注明还需补充的关键事实；legal_basis 只能引用参考材料中出现过的法条，不得编造条号。\n"
        if has_sources
        else "信息不足的段落可留空字符串。\n"
    )
    return (
        f"用户法律问题：{question}\n\n"
        f"参考材料（如有）：\n{context}\n\n"
        "你是执业律师，请为这个问题整理一份内部四段式工作草稿（不会直接展示给客户）。\n"
        f"{fill_rule}"
        "仅输出如下 JSON，不要任何额外文字：\n"
        "{\n"
        '  "draft": {\n'
        '    "conclusion": "一句结论性判断",\n'
        '    "legal_basis": "对应法条要点",\n'
        '    "advice": "可执行的行动建议",\n'
        '    "risk": "时效/证据等风险提示"\n'
        "  }\n"
        "}"
    )


def _normalize_draft(draft: Any) -> dict[str, str]:
    """把草稿统一成四段式固定键，供返回与落库共用同一对象。"""
    raw = draft if isinstance(draft, dict) else {}
    return {
        key: str(raw.get(key, "") or "").strip()
        for key in ("conclusion", "legal_basis", "advice", "risk")
    }


def _has_draft_content(draft: Any) -> bool:
    """四段全空视为空草稿：不落库、不建复核（N2）。"""
    if not isinstance(draft, dict):
        return False
    return any(str(draft.get(key, "") or "").strip() for key in ("conclusion", "legal_basis", "advice", "risk"))


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
    draft = _normalize_draft(obj.get("draft") or {})
    if not answer:
        return None, None
    return answer, draft


def _parse_answer(text: str) -> Optional[str]:
    """解析回答专用输出；JSON 解析失败时把纯文本原样当作回答（容错）。"""
    answer, _ = _parse_consult(text)
    if answer:
        return answer
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    # 明显的半截 JSON 不当作回答，交给上层走降级
    if not t or t.startswith("{") or t.startswith("["):
        return None
    return t


def _parse_draft(text: str) -> Optional[dict]:
    """解析草稿专用输出：既接受 {"draft": {...}}，也接受直接给出的四段对象。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    nested = obj.get("draft")
    return nested if isinstance(nested, dict) else obj


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
