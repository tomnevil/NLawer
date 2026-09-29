"""Job 处理器注册表：把各业务流水线挂到异步任务框架上。

Handlers 在应用启动时由 `register_all()` 注册，供 `run_job` 与
`/jobs/{id}/retry` 使用。worker 约定：从 `job.step_state` 恢复进度（断点续跑），
并把中间产物写回 step_state，避免重跑时重复消耗。
"""
from typing import Optional

from fastapi import BackgroundTasks
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, NotFoundError
from app.models.case import Case
from app.models.enums import JobType, NotificationType
from app.services.case_copilot import CaseCopilot
from app.services.compliance_service import ComplianceService
from app.services.contract_review import ContractReviewService
from app.services.evidence_service import EvidenceService
from app.services.job_service import (
    JobService,
    job_queue,
    register_job_handler,
)
from app.services.notification_service import notify


async def _notify_missing_key_evidence(db: AsyncSession, ev) -> None:
    """解析完成后，若**关键材料**（priority=1）仍缺，提醒承办律师。

    ## 触发点为什么必须放在「解析完成之后」

    材料分类（`ev.category`）是**异步**由解析任务写入的。若把检查放在
    上传接口里，那一刻 `category` 还是空值，`missing_list()` 会把刚上传的
    这份材料也算成「缺」的——发出的是**错误提醒**，而且是最伤信任的那种：
    用户刚做完一个动作，系统立刻说他没做。

    ## 为什么只提醒 priority=1

    完整清单可能有十几项（三轮追问）。把全部缺失项一次性推给律师，
    结果是一条通知里塞 12 个待办，等于没有重点。**第一轮关键材料**才是
    真正卡住立案/庭审的东西，其余的留在证据页里按轮次逐步引导。

    ## 去重

    `missing_list` 是实时计算、不落状态的，因此每上传一份材料都会重算一次。
    只有当**关键缺失项数量相比解析前发生变化**时才有提醒价值——
    但解析前无法可靠计算（同一原因），故退一步：内容里带上当前缺失项，
    由前端按 `payload.key_missing` 去重；同时天然不会重复推送相同内容，
    因为每次上传后缺失集合必然少一项（上传的材料被归入某类）。
    """
    case = await db.get(Case, ev.case_id)
    if case is None or not case.lawyer_id:
        return

    items = await EvidenceService(db).missing_list(ev.case_id)
    key_missing = [i["item"] for i in items if i["priority"] == 1 and i["missing"]]
    if not key_missing:
        return

    shown = "、".join(key_missing[:3])
    more = f" 等 {len(key_missing)} 项" if len(key_missing) > 3 else ""
    await notify(
        db,
        tenant_id=case.tenant_id,
        user_id=case.lawyer_id,
        type=NotificationType.EVIDENCE_MISSING,
        content=f"《{case.title}》仍缺关键材料：{shown}{more}",
        ref_type="case",
        ref_id=case.id,
        payload={"key_missing": key_missing, "trigger_evidence_id": ev.id},
    )


async def _case_analysis_worker(job, db: AsyncSession) -> None:
    """六段式案件分析：已生成过则跳过（断点续跑）。"""
    state = job.step_state or {}
    if state.get("analysis_id"):
        job.progress = 100
        return

    case_id = job.ref_id
    job.step_name = "generate"
    job.progress = 30
    analysis = await CaseCopilot(db).generate(case_id, trigger="SYSTEM_AUTO")
    job.step_state = {**state, "analysis_id": analysis.id}
    job.progress = 100
    logger.info("案件 {} 六段式分析生成完成（analysis_id={}）", case_id, analysis.id)


async def _evidence_parse_worker(job, db: AsyncSession) -> None:
    """证据解析：已解析过则跳过。"""
    state = job.step_state or {}
    if state.get("parsed"):
        job.progress = 100
        return

    evidence_id = job.ref_id
    job.step_name = "parse"
    job.progress = 40
    # 传入 job.tenant_id 做归属校验，防止跨租户解析
    ev = await EvidenceService(db).parse(evidence_id, tenant_id=job.tenant_id)
    job.step_state = {**state, "parsed": True, "category": ev.category.value}
    job.progress = 100
    # 分类已写入，此刻判定「关键材料缺失」才是准确的
    await _notify_missing_key_evidence(db, ev)


async def _compliance_scan_worker(job, db: AsyncSession) -> None:
    """四维合规扫描：已扫描过则跳过。"""
    state = job.step_state or {}
    if state.get("scanned"):
        job.progress = 100
        return

    scan_id = job.ref_id
    job.step_name = "scan"
    job.progress = 40
    scan = await ComplianceService(db).run(scan_id)
    job.step_state = {**state, "scanned": True, "overall_risk": scan.overall_risk.value}
    job.progress = 100
    logger.info("合规扫描 {} 完成（整体风险={}）", scan_id, scan.overall_risk.value)


async def _contract_review_worker(job, db: AsyncSession) -> None:
    """异步合同审查：已审查过则跳过（断点续跑）。

    ## 为什么原文放在 `input_payload`，而不是先建业务行

    合同审查的实体行是在 `ContractReviewService.review()` **内部**创建的
    （建行 + 规则预筛 + AI 通读在同一个事务里），拆开重构风险大。因此这里沿用
    本项目「任务先落库」的既有约定：用户粘贴的原文**先进 job 行**再执行 ——
    即便进程立刻崩溃原文也不丢，正好对应 B1「零丢失」的目标。

    ⚠️ 副作用：原文会以明文落在 `jobs.input_payload`（合同含商业秘密）。
    与同步端点落库 `source_text[:5000]` 的口径不同，这是**为了可重试/可续跑
    而付的代价**；若将来要求「job 行不留正文」，应改为先落证据文件、只传
    `evidence_id`，本 worker 再按 id 取全文。
    """
    state = job.step_state or {}
    if state.get("contract_review_id"):
        job.progress = 100
        return

    payload = job.input_payload or {}
    source_text = payload.get("source_text") or ""
    if not source_text:
        raise ValueError("异步合同审查缺少 source_text")

    job.step_name = "review"
    job.progress = 30
    cr = await ContractReviewService(db).review(
        title=payload.get("title") or "合同审查",
        source_text=source_text,
        tenant_id=job.tenant_id,
        user_id=payload["user_id"],
        evidence_id=payload.get("evidence_id"),
    )
    job.step_state = {**state, "contract_review_id": cr.id}
    job.progress = 100
    logger.info("异步合同审查完成（contract_review_id={}）", cr.id)


async def trigger_contract_review(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: int,
    title: Optional[str],
    source_text: str,
    evidence_id: Optional[int] = None,
) -> int:
    """提交长合同审查：先落库再交给常驻队列执行。

    ⚠️ `ref_id=None` 是有意的：审查实体由 worker 在 `review()` 内部创建，
    完成后回写 `step_state.contract_review_id`。
    """
    svc = JobService(db)
    job = await svc.enqueue(
        JobType.CONTRACT_REVIEW,
        ref_type="contract_review",
        ref_id=None,
        tenant_id=tenant_id,
        input_payload={
            "title": title,
            "source_text": source_text,
            "user_id": user_id,
            "evidence_id": evidence_id,
        },
    )
    await db.commit()
    await job_queue.enqueue(job.id, _contract_review_worker)
    return job.id


def register_all() -> None:
    register_job_handler(JobType.CASE_ANALYSIS.value, _case_analysis_worker)
    register_job_handler(JobType.EVIDENCE_PARSE.value, _evidence_parse_worker)
    register_job_handler(JobType.COMPLIANCE_SCAN.value, _compliance_scan_worker)
    register_job_handler(JobType.CONTRACT_REVIEW.value, _contract_review_worker)


async def trigger_case_analysis(
    db: AsyncSession, case_id: int, tenant_id: str, background: BackgroundTasks
) -> int:
    """接单后触发案件分析：先落库再交给常驻队列执行。

    安全：入队前必须校验案件归属——否则任意用户遍历 `case_id` 调用
    `POST /analyses/case/{id}/generate` 即可为他人案件生成分析并读回内容
    （与案件时间线越权同类，属 IDOR）。

    可靠性：任务**先落库**再入队；队列满时反压等待。即便进程崩溃，
    Job 行仍在库中，可由僵尸回收逻辑重新入队。
    """
    case = await db.get(Case, case_id)
    if case is None or case.tenant_id != tenant_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)

    svc = JobService(db)
    job = await svc.enqueue(
        JobType.CASE_ANALYSIS,
        ref_type="case",
        ref_id=case_id,
        tenant_id=tenant_id,
        input_payload={"case_id": case_id},
    )
    await db.commit()
    await job_queue.enqueue(job.id, _case_analysis_worker)
    return job.id


async def trigger_evidence_parse(
    db: AsyncSession, evidence_id: int, tenant_id: str, background: BackgroundTasks
) -> int:
    """上传证据后触发解析：先落库再交给常驻队列执行。"""
    svc = JobService(db)
    job = await svc.enqueue(
        JobType.EVIDENCE_PARSE,
        ref_type="evidence",
        ref_id=evidence_id,
        tenant_id=tenant_id,
        input_payload={"evidence_id": evidence_id},
    )
    await db.commit()
    await job_queue.enqueue(job.id, _evidence_parse_worker)
    return job.id


async def trigger_compliance_scan(
    db: AsyncSession, scan_id: int, tenant_id: str, background: BackgroundTasks
) -> int:
    """创建合规扫描后触发：先落库再交给常驻队列执行。"""
    svc = JobService(db)
    job = await svc.enqueue(
        JobType.COMPLIANCE_SCAN,
        ref_type="compliance_scan",
        ref_id=scan_id,
        tenant_id=tenant_id,
        input_payload={"scan_id": scan_id},
    )
    await db.commit()
    await job_queue.enqueue(job.id, _compliance_scan_worker)
    return job.id
