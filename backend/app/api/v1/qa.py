"""智能问答接口：四段式输出（REST）+ SSE 流式。

内容安全（P0-13）：输入侧与输出侧都过审——对应《生成式人工智能服务
管理暂行办法》第十四条的「停止生成」与「停止传输」两个动作。
"""
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_db, get_tenant_context
from app.core.moderation import ContentBlockedError, StreamModerator
from app.core.pagination import ok
from app.models.enums import UsageType
from app.models.identity import User
from app.services.billing_service import BillingService
from app.services.moderation_service import ModerationService
from app.services.qa_service import QAService

router = APIRouter(prefix="/qa", tags=["智能问答"])


class QARequest(BaseModel):
    # 2026-09-22 补长度上限（门禁）：问答主入口的自由文本，直接喂模型。
    # ⚠️ 这个模型定义在 `app/api/` 而不是 `app/schemas/` ⇒ 首版探针**扫不到它**
    # （失真 ⑤），是扩面后才暴露出来的 —— 比已登记的 16 条里大半都更该管。
    question: str = Field(..., max_length=4000)


def _period() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m")


async def _actor(db: AsyncSession, ctx) -> User | None:
    """取 ctx 背后的 User（`get_tenant_context` 已校验过，此处仅取实体用于留痕）。"""
    return (
        await db.execute(select(User).where(User.id == ctx.user_id))
    ).scalars().first()


@router.post("", response_model=dict, summary="四段式问答（一次性返回）")
async def ask(
    payload: QARequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    user = await _actor(db, ctx)

    # —— 输入审核：命中即「停止生成」，不调用模型（省算力，也避免违规内容进上下文）
    if settings.MODERATION_CHECK_INPUT:
        verdict = await ModerationService(db).check(
            payload.question,
            side="input",
            scene="qa",
            actor=user,
            resource_type="qa",
        )
        if verdict.blocked:
            raise ContentBlockedError(verdict)

    result = await QAService(db).build(payload.question, ctx.tenant_id, user_id=ctx.user_id)

    # —— 输出审核：命中即「停止传输」，不把违规内容返回给用户
    if settings.MODERATION_CHECK_OUTPUT:
        # N2 之后 `sections` 是后台四段式草稿，客户实际看到的自然回复在 `answer`；
        # 两处都必须过审，否则「草稿合规、回复违规」会漏检。
        out_text = " ".join(
            [str(result.get("answer") or "")]
            + [str(v) for v in (result.get("sections") or {}).values()]
        )
        verdict = await ModerationService(db).check(
            out_text,
            side="output",
            scene="qa",
            actor=user,
            resource_type="qa",
        )
        if verdict.blocked:
            raise ContentBlockedError(verdict)

    # 用原子扣减（条件 UPDATE）替代"读-判-写"，避免并发下丢失更新导致少计费。
    # B3：草稿引用校验失败时后端已兜成友好话术（`billable=False`），
    # 这属于「生成失败」，不扣费，响应仍为 200。
    usage = None
    if result.get("billable", True):
        usage = await BillingService(db).consume_atomic(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            usage_type=UsageType.QA,
            period=_period(),
            ref_type="qa",
        )
    await db.commit()
    return ok({**result, "usage": usage})


@router.post("/stream", summary="四段式问答（SSE 流式）")
async def ask_stream(
    payload: QARequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """按 `data: {json}\\n\\n` 逐包推送，首字延迟低（PRD 问答首字 <= 3s）。

    流式场景的输出审核是难点：违禁词可能**跨片段**被切开，
    因此用 `StreamModerator` 的滑动窗口做跨片段校验，一旦命中立即
    发 `blocked` 事件并终止流（而不是静默截断，否则用户看到半句话
    会以为系统故障）。

    **草稿异步生成**：流里只发客户回答；`done.draft_deferred=True` 时把
    四段式草稿交给 BackgroundTasks，用独立 session/事务生成。请求会话
    在响应结束后即关闭，后台任务不复用它；失败只留痕，不影响已发送的回答。
    """
    svc = QAService(db)
    mod_svc = ModerationService(db)

    stream_mod = (
        StreamModerator(mod_svc.moderator) if settings.MODERATION_CHECK_OUTPUT else None
    )

    async def event_gen():
        import json as _json

        user = await _actor(db, ctx)

        # 输入审核在流内部首个事件之前完成——命中就发 blocked 事件并终止，
        # 不把违规提问送进上下文（也节省算力）。
        if settings.MODERATION_CHECK_INPUT:
            verdict = await mod_svc.check(
                payload.question,
                side="input",
                scene="qa_stream",
                actor=user,
                resource_type="qa",
            )
            if verdict.blocked:
                await db.commit()
                yield (
                    "data: "
                    + _json.dumps(
                        {
                            "type": "blocked",
                            "reason": verdict.reason_for_user(),
                            "action": verdict.action.value,
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                return

        try:
            async for chunk in svc.stream(payload.question, ctx.tenant_id, user_id=ctx.user_id):
                obj = _sse_object(chunk)
                # 从 SSE 包里取出实际内容做跨片段校验
                content = _content_of(obj)
                if stream_mod is not None and content:
                    allow, res = stream_mod.prepare(content)
                    if not allow:
                        # 「停止传输」：明确告知用户已拦截，而非静默截断。
                        # 被阻断的回复不构成一次成功问答 ⇒ 不计费（A8）。
                        await mod_svc.check(
                            stream_mod.head + content,
                            side="output",
                            scene="qa_stream",
                            actor=user,
                            resource_type="qa",
                        )
                        await db.commit()
                        yield (
                            "data: "
                            + _json.dumps(
                                {
                                    "type": "blocked",
                                    "reason": res.reason_for_user(),
                                    "action": res.action.value,
                                },
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
                        return
                    stream_mod.committed(content)
                if obj is not None and obj.get("type") == "done":
                    # A8：与 `/qa` 同一计费口径——回复真正生成完毕才扣减。
                    # 模型异常降级为安抚回复时 `failed=True`，不扣；流中断/阻断
                    # 时走不到这里，也不扣。
                    if obj.get("failed"):
                        yield chunk
                        continue
                    usage = await BillingService(db).consume_atomic(
                        tenant_id=ctx.tenant_id,
                        user_id=ctx.user_id,
                        usage_type=UsageType.QA,
                        period=_period(),
                        ref_type="qa_stream",
                    )
                    await db.commit()
                    obj["usage"] = usage
                    # 计费只按「客户回答成功生成」一次；草稿后台生成不再计费。
                    if obj.get("draft_deferred"):
                        background.add_task(
                            _run_draft_job,
                            db.bind,
                            payload.question,
                            str((obj.get("sections") or {}).get("conclusion") or ""),
                            ctx.tenant_id,
                            ctx.user_id,
                        )
                    yield "data: " + _json.dumps(obj, ensure_ascii=False) + "\n\n"
                    continue
                yield chunk
        except Exception as exc:  # 流中途异常也必须给前端一个终止事件
            _msg = str(exc)[:200]
            yield (
                "data: "
                + _json.dumps({"type": "error", "message": _msg}, ensure_ascii=False)
                + "\n\n"
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=background,
    )


async def _run_draft_job(bind, question: str, answer: str, tenant_id: str, user_id: int | None) -> None:
    """后台草稿任务：独立 session/事务，失败只留痕，不影响已返回的回答。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind, expire_on_commit=False)
    async with factory() as session:
        try:
            saved = await QAService(session).generate_draft(question, answer, tenant_id, user_id)
            await session.commit()
            logger.info("后台草稿生成结束 saved={} tenant={} user={}", saved, tenant_id, user_id)
        except Exception as exc:  # noqa: BLE001 - 后台任务绝不能把异常抛回请求
            logger.warning("后台草稿任务失败（不影响已返回的回答）：{}", exc)


def _sse_object(chunk: str) -> dict | None:
    """从 `data: {json}` 包里取出对象；非数据行返回 None。"""
    import json as _json

    for line in (chunk or "").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            obj = _json.loads(line[5:].strip())
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _content_of(obj: dict | None) -> str:
    """取出 delta/正文文本，供输出审核使用。"""
    if not obj:
        return ""
    if obj.get("type") == "delta":
        return str(obj.get("content") or "")
    if obj.get("type") == "done":
        secs = obj.get("sections") or {}
        return " ".join(str(v) for v in secs.values())
    return ""
