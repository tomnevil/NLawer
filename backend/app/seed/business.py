"""业务演示数据：案件 / 派单 / 复核 / 工单 / 合规扫描 / 用量额度。

目的：让 web → lawyer → admin 三端有真实数字可点（直接解决驾驶舱长期为 0 的问题）。
- 幂等：某租户已存在 `*-DEMO-*` 前缀案件则跳过该租户，可反复运行。
- `--reset` 由 `seed_demo.reset_business_tables` 清空业务表后重灌。
- 同时写入 `firm_hlw`（律所运营，最完整）与 `platform`（平台管理员登录也能看到数字）两个租户。
- 租户隔离：所有行带真实 tenant_id，与既有多租户过滤逻辑一致；不改动任何 LLM / MockProvider 代码。
"""
from typing import Optional

from sqlalchemy import select

from app.models import User
from app.models.analysis import CaseAnalysis
from app.models.billing import UsageQuota, WorkOrder
from app.models.case import Case, Dispatch
from app.models.enums import (
    CaseGrade,
    CaseStatus,
    ComplianceDimension,
    DispatchMode,
    DispatchStatus,
    IntentType,
    ReviewLevel,
    ReviewStatus,
    ReviewTargetType,
    RiskLevel,
    ScanStatus,
    UsageType,
    WorkOrderStatus,
)
from app.models.knowledge import ComplianceFinding, ComplianceScan
from app.models.review import Review, ReviewRecord


def _period() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m")


def _wo_no(tenant_id: str) -> str:
    import datetime
    import uuid

    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    return f"WO-{tenant_id}-{ts}-{uuid.uuid4().hex[:4]}"


async def _get_user(session, username: str) -> Optional[User]:
    return (await session.execute(select(User).where(User.username == username))).scalars().first()


async def _already_seeded(session, tenant_id: str, prefix: str) -> bool:
    row = (
        await session.execute(
            select(Case).where(Case.tenant_id == tenant_id, Case.case_no.like(f"{prefix}-%"))
        )
    ).scalars().first()
    return row is not None


# ── 演示分析引用的「库内可验证」映射 ────────────────────────────────────
#
# 🚨 原则（与第十四轮合同审查一致）：**引用必须库内可验证，否则不显示引用**。
# 所以这里只引用 `app/seed/laws.py` / `app/seed/cases.py` 里**真实存在**的条目，
# 运行时按 (law_name, article_no) / case_no 查回 id 填入 `citation_id`。
#
# 库内**没有**对应法域的（婚姻家庭、知识产权），`related_laws` **留空**——
# 这不是缺陷，而是把「法条库域不匹配」这一能力边界**诚实地体现在数据里**。
# （实测：库内 12 条法条中 8 条属劳动法域，买卖/租赁/承揽/委托/保证/保密/知产/管辖覆盖为零。）
#
# 键 = 案件编号；值 = (法条 [(law_name, article_no)], 判例 [case_no])
_CITATIONS: dict[str, tuple[list[tuple[str, str]], list[str]]] = {
    "HLW-DEMO-0001": ([], ["(2023)桂03民初234号"]),  # 婚姻家庭：库内无法条
    "HLW-DEMO-0002": (
        [("中华人民共和国劳动合同法", "第38条"), ("中华人民共和国劳动合同法", "第40条")],
        ["指导案例18号"],
    ),
    "HLW-DEMO-0005": (
        [("中华人民共和国民法典", "第577条"), ("中华人民共和国民法典", "第584条")],
        ["(2021)桂05民初567号"],
    ),
    "HLW-DEMO-0007": ([("中华人民共和国民法典", "第577条")], ["(2021)桂05民初567号"]),
    "HLW-DEMO-0008": (
        [("中华人民共和国民法典", "第577条"), ("中华人民共和国民法典", "第563条")],
        ["(2021)桂05民初567号"],
    ),
    "PLT-DEMO-0002": (
        [("中华人民共和国劳动合同法", "第82条"), ("中华人民共和国劳动合同法", "第47条")],
        ["指导案例18号"],
    ),
    "PLT-DEMO-0003": ([], []),  # 知识产权：库内无对应法条与判例
}


class _CiteResolver:
    """把 (law_name, article_no) / case_no 解析为**带库内 id** 的引用条目。

    - 查得到 → 产出完整条目（含 `citation_id`，前端可跳原文）；
    - 查不到 → **跳过该条**（宁可不显示，也不编造 id），并把键记进 `missing` 便于自检。
    """

    def __init__(self, laws: list, precedents: list) -> None:
        self._law = {(a.law_name, a.article_no): a for a in laws}
        self._prec = {c.case_no: c for c in precedents}
        self.missing: list[str] = []

    def related_laws(self, case_no: str) -> list[dict]:
        out: list[dict] = []
        for key in _CITATIONS.get(case_no, ([], []))[0]:
            art = self._law.get(key)
            if art is None:
                self.missing.append(f"{case_no}:{key[0]}{key[1]}")
                continue
            out.append(
                {
                    "law_name": art.law_name,
                    "article_no": art.article_no,
                    "excerpt": (art.content or "")[:200],
                    "citation_id": art.id,
                }
            )
        return out

    def similar_cases(self, case_no: str) -> list[dict]:
        out: list[dict] = []
        for no in _CITATIONS.get(case_no, ([], []))[1]:
            prec = self._prec.get(no)
            if prec is None:
                self.missing.append(f"{case_no}:{no}")
                continue
            out.append(
                {
                    "case_no": prec.case_no,
                    "title": prec.title,
                    "court": prec.court,
                    "holding": (prec.holding or "")[:200],
                    "citation_id": prec.id,
                }
            )
        return out


async def _citation_resolver(session) -> _CiteResolver:
    """从平台共享域加载法条 / 判例，构造解析器。

    ⚠️ `seed_laws` / `seed_cases` 必须先于 `seed_business` 执行（见 `seed_demo.py:181-185`）；
    若库为空（例如单独跑本模块），解析器会产出**空引用**而不是假引用——这是预期行为。
    """
    from app.models.citation import CasePrecedent, LawArticle

    laws = list(
        (
            await session.execute(select(LawArticle).where(LawArticle.tenant_id == "platform"))
        ).scalars().all()
    )
    precs = list(
        (
            await session.execute(select(CasePrecedent).where(CasePrecedent.tenant_id == "platform"))
        ).scalars().all()
    )
    return _CiteResolver(laws, precs)


async def _seed_firm(session, users: dict) -> dict:
    """律所（firm_hlw）运营演示数据集：最完整，覆盖律师端派单池/我的案件/复核 与 管理员驾驶舱。"""
    tenant_id = "firm_hlw"
    if await _already_seeded(session, tenant_id, "HLW-DEMO"):
        return {}
    client = users.get("client")
    wang = users.get("lawyer_wang")
    li = users.get("lawyer_li")
    zhao = users.get("lawyer_zhao")
    firm = users.get("firm_admin")

    # 案件：覆盖各状态，律师端“我的案件”按 lawyer_id 过滤可见
    case_specs = [
        ("HLW-DEMO-0001", "劳动争议仲裁申请书代写", wang, CaseStatus.ACCEPTED, CaseGrade.B, IntentType.DOCUMENT, "劳动争议", 80000.0, 2, 0),
        ("HLW-DEMO-0002", "劳动合同解除经济补偿核算", li, CaseStatus.IN_REVIEW, CaseGrade.A, IntentType.CALCULATION, "劳动争议", 120000.0, 3, 1),
        ("HLW-DEMO-0003", "买卖合同违约追责", li, CaseStatus.DISPATCHED, CaseGrade.B, IntentType.CONSULT, "合同纠纷", 250000.0, 1, 0),
        ("HLW-DEMO-0004", "房屋租赁合同纠纷咨询", wang, CaseStatus.PENDING_DISPATCH, CaseGrade.C, IntentType.CONSULT, "租赁合同", 30000.0, 1, 0),
        ("HLW-DEMO-0005", "离婚财产分割协议", zhao, CaseStatus.CONFIRMED, CaseGrade.A, IntentType.DOCUMENT, "婚姻家庭", 500000.0, 2, 1),
        ("HLW-DEMO-0006", "法定继承纠纷咨询", zhao, CaseStatus.ACCEPTED, CaseGrade.B, IntentType.CONSULT, "继承纠纷", 0.0, 1, 0),
        ("HLW-DEMO-0007", "交通事故人身损害赔偿", wang, CaseStatus.ARCHIVED, CaseGrade.C, IntentType.CALCULATION, "交通事故", 60000.0, 2, 0),
        ("HLW-DEMO-0008", "工伤待遇赔偿争议", wang, CaseStatus.CLOSED, CaseGrade.B, IntentType.CALCULATION, "劳动争议", 180000.0, 3, 0),
        ("HLW-DEMO-0009", "科技公司股权架构设计", firm, CaseStatus.DISPATCHED, CaseGrade.S, IntentType.ENTRUST, "公司合规", 0.0, 2, 1),
        ("HLW-DEMO-0010", "商业秘密保护合规咨询", zhao, CaseStatus.INTAKE, CaseGrade.C, IntentType.CONSULT, "知识产权", 0.0, 1, 0),
    ]
    cases: dict[str, Case] = {}
    for no, title, lawyer, status, grade, intent, disp, claim, urg, formal in case_specs:
        c = Case(
            case_no=no,
            tenant_id=tenant_id,
            title=title,
            client_user_id=client.id if client else None,
            lawyer_id=lawyer.id if lawyer else None,
            status=status,
            grade=grade,
            intent=intent,
            dispute_type=disp,
            claim_amount=claim or None,
            urgency=urg,
            require_formal_opinion=formal,
            summary=f"{title}（演示数据）",
        )
        session.add(c)
        await session.flush()
        cases[no] = c

    # 派单：抢单池（PENDING / lawyer_id=None）与已派单待接/已接
    dispatch_specs = [
        (cases["HLW-DEMO-0003"], li, DispatchMode.AUTO, DispatchStatus.ACCEPTED),
        (cases["HLW-DEMO-0004"], None, DispatchMode.POOL, DispatchStatus.PENDING),
        (cases["HLW-DEMO-0009"], firm, DispatchMode.DESIGNATED, DispatchStatus.ACCEPTED),
        (cases["HLW-DEMO-0001"], wang, DispatchMode.AUTO, DispatchStatus.ACCEPTED),
        (cases["HLW-DEMO-0006"], zhao, DispatchMode.AUTO, DispatchStatus.ACCEPTED),
        (cases["HLW-DEMO-0010"], None, DispatchMode.POOL, DispatchStatus.PENDING),
    ]
    for case, lawyer, mode, status in dispatch_specs:
        session.add(
            Dispatch(
                case_id=case.id,
                tenant_id=tenant_id,
                lawyer_id=lawyer.id if lawyer else None,
                mode=mode,
                status=status,
                score=0.92 if status == DispatchStatus.ACCEPTED else None,
                reason="演示数据：按专业领域匹配" if status == DispatchStatus.ACCEPTED else None,
            )
        )

    # 复核任务
    #
    # 修复 (2026-09-17)：之前 `target_id=case.id` 把「案件 id」当成了「分析 id」
    # ——`case_analyses` 表 0 行 ⇒ /analyses/case/{id} 全部 404，复核队列是「空壳」。
    # 现在每条 Review 都**先建对应 CaseAnalysis**，再以 analysis.id 作 target_id，
    # 关系对齐：`Review.target_id → CaseAnalysis.id`，`CaseAnalysis.case_id → Case.id`。
    review_specs = [
        (cases["HLW-DEMO-0002"], li, ReviewStatus.PENDING_CONFIRM, ReviewLevel.L2, True,
         "劳动合同纠纷：用人单位单方调岗降薪，劳动者主张继续履行与赔偿。"),
        (cases["HLW-DEMO-0005"], zhao, ReviewStatus.CONFIRMED, ReviewLevel.L3, True,
         "建设工程分包合同争议：发包人拖欠工程款及利息，连带责任主体识别。"),
        (cases["HLW-DEMO-0001"], wang, ReviewStatus.DRAFT, ReviewLevel.L2, False,
         "离婚财产分割：婚前按揭房产的产权归属与补偿计算。"),
        (cases["HLW-DEMO-0007"], wang, ReviewStatus.ARCHIVED, ReviewLevel.L2, False,
         "民间借贷：借条真实性与诉讼时效中断事由审查。"),
        (cases["HLW-DEMO-0008"], li, ReviewStatus.CONFIRMED, ReviewLevel.L2, False,
         "买卖合同质量争议：瑕疵通知期限与举证责任分配。"),
    ]
    cite = await _citation_resolver(session)
    for case, assignee, rstatus, rlevel, forced, summary_text in review_specs:
        # 1) 先建对应分析（六字段六段式，简写，演示用）
        analysis = CaseAnalysis(
            tenant_id=tenant_id,
            case_id=case.id,
            version=1,
            summary=summary_text,
            legal_analysis=f"围绕案件焦点，结合当事人主张与现有证据进行法律关系定性。"
            f"详见《{case.case_no}》卷宗。",
            # ⚠️ 键名必须与 `app/services/case_copilot.py::_build_sections` 完全一致，
            # 否则前端（lawyer 的 `RelatedLaw`、admin 的 `AnalysisSummary`）渲染出空值。
            # 权威契约：related_laws → {law_name, article_no, excerpt, citation_id}
            # 🚨 引用来自平台共享域，**带库内可验证的 citation_id**；
            # 库内无对应法域时为空列表（如婚姻家庭），见 `_CITATIONS` 注释。
            related_laws=cite.related_laws(case.case_no),
            similar_cases=cite.similar_cases(case.case_no),
            suggestions=[
                {"path": "协商解决", "pros": "成本低、周期短", "cons": "对方不配合则无强制力"},
                {"path": "申请调解/仲裁", "pros": "程序较快、费用较低", "cons": "需双方同意"},
                {"path": "提起诉讼", "pros": "有强制执行力", "cons": "周期长、成本较高"},
            ],
            # priority 是**数字** 1/2（与 `_build_sections` 一致），不是字符串
            missing_info=[
                {"item": "关键证据原件", "reason": "尚未提交完整证据链", "priority": 1},
                {"item": "对方主体信息", "reason": "确定被告/被申请人", "priority": 2},
            ],
            status=rstatus,
            required_level=rlevel,
            forced_hits=["MANUAL_ESCALATION"] if forced else None,
            ai_generated=1,
        )
        session.add(analysis)
        await session.flush()  # 取到 analysis.id

        # 2) 再建复核任务，target_id 指向分析 id（修复前的 bug）
        review = Review(
            tenant_id=tenant_id,
            target_type=ReviewTargetType.CASE_ANALYSIS,
            target_id=analysis.id,
            case_id=case.id,
            status=rstatus,
            required_level=rlevel,
            satisfied_level=rlevel if rstatus == ReviewStatus.CONFIRMED else None,
            is_forced=int(forced),
            assignee_id=assignee.id if assignee else None,
            decided_by=assignee.id if assignee and rstatus == ReviewStatus.CONFIRMED else None,
        )
        session.add(review)
        await session.flush()  # 取到 review.id 给留痕用

        # 3) 留痕：每条至少 1 条 CREATE，可选一条 SUBMIT/APPROVE
        session.add(
            ReviewRecord(
                tenant_id=tenant_id,
                review_id=review.id,
                action="CREATE",
                actor_id=assignee.id if assignee else None,
                actor_role="LAWYER",
                level=rlevel,
                from_status=None,
                to_status=ReviewStatus.DRAFT.value if rstatus != ReviewStatus.DRAFT else rstatus.value,
                comment="AI 初稿生成（演示数据）",
            )
        )
        if rstatus in (ReviewStatus.CONFIRMED, ReviewStatus.ARCHIVED):
            session.add(
                ReviewRecord(
                    tenant_id=tenant_id,
                    review_id=review.id,
                    action="APPROVE" if rstatus == ReviewStatus.CONFIRMED else "ARCHIVE",
                    actor_id=assignee.id if assignee else None,
                    actor_role="LAWYER",
                    level=rlevel,
                    from_status=ReviewStatus.PENDING_CONFIRM.value,
                    to_status=rstatus.value,
                    comment=("终审定稿（演示数据）" if rstatus == ReviewStatus.CONFIRMED
                             else "已归档（演示数据）"),
                )
            )
        elif rstatus == ReviewStatus.PENDING_CONFIRM:
            session.add(
                ReviewRecord(
                    tenant_id=tenant_id,
                    review_id=review.id,
                    action="SUBMIT",
                    actor_id=assignee.id if assignee else None,
                    actor_role="LAWYER",
                    level=rlevel,
                    from_status=ReviewStatus.DRAFT.value,
                    to_status=ReviewStatus.PENDING_CONFIRM.value,
                    comment="提交复核（演示数据）",
                )
            )

    # 工单：超量转工单场景（价格取自 billing_service._PRICE_CENTS）
    wo_specs = [
        ("COMPLIANCE_SCAN", "企业合规扫描超量转工单", WorkOrderStatus.COMPLETED, False, 19900),
        ("DOCUMENT", "文书代写工单", WorkOrderStatus.PENDING, False, 2900),
        ("CONTRACT_REVIEW", "合同审查工单", WorkOrderStatus.COMPLETED, False, 9900),
        ("COMPLIANCE_SCAN", "对外合规报告（加急）", WorkOrderStatus.COMPLETED, True, 29900),
        ("DOCUMENT", "离婚协议代写（加急）", WorkOrderStatus.PROCESSING, True, 4900),
        ("CONTRACT_REVIEW", "股权协议审查（加急）", WorkOrderStatus.PENDING, True, 16900),
    ]
    for ut, title, wstatus, urgent, price in wo_specs:
        session.add(
            WorkOrder(
                order_no=_wo_no(tenant_id),
                tenant_id=tenant_id,
                created_by=firm.id if firm else None,
                usage_type=UsageType(ut),
                title=title,
                status=wstatus,
                urgent=int(urgent),
                price_cents=price,
                escalate_to_lawyer=int(ut in ("CONTRACT_REVIEW", "COMPLIANCE_SCAN")),
            )
        )

    # 合规扫描（含维度得分与发现项）
    scan_specs = [
        (
            "2026Q3 劳动用工与广告宣传合规自查",
            ScanStatus.COMPLETED,
            RiskLevel.MEDIUM,
            [ComplianceDimension.LABOR, ComplianceDimension.COMMERCIAL, ComplianceDimension.DATA_PRIVACY, ComplianceDimension.ADVERTISING],
            {"LABOR": {"score": 72, "risk": "MEDIUM"}, "COMMERCIAL": {"score": 85, "risk": "LOW"}, "DATA_PRIVACY": {"score": 68, "risk": "MEDIUM"}, "ADVERTISING": {"score": 55, "risk": "HIGH"}},
            False,
        ),
        ("劳动合同模板合规审查", ScanStatus.COMPLETED, RiskLevel.LOW, [ComplianceDimension.LABOR], {"LABOR": {"score": 90, "risk": "LOW"}}, False),
        ("对外宣传材料合规扫描", ScanStatus.COMPLETED, RiskLevel.HIGH, [ComplianceDimension.ADVERTISING], {"ADVERTISING": {"score": 48, "risk": "HIGH"}}, True),
        ("数据隐私合规自查", ScanStatus.COMPLETED, RiskLevel.MEDIUM, [ComplianceDimension.DATA_PRIVACY], {"DATA_PRIVACY": {"score": 70, "risk": "MEDIUM"}}, False),
    ]
    for title, st, risk, dims, scores, is_ext in scan_specs:
        scan = ComplianceScan(
            tenant_id=tenant_id,
            title=title,
            status=st,
            overall_risk=risk,
            dimensions=[d.value for d in dims],
            dimension_scores=scores,
            scope="演示企业 2026 年度合规自查",
            is_external=int(is_ext),
            finished_at="2026-09-08 10:00:00",
        )
        session.add(scan)
        await session.flush()
        if title.startswith("2026Q3"):
            for dim, lvl, t in [
                (ComplianceDimension.LABOR, RiskLevel.MEDIUM, "加班费计算口径与考勤记录不一致"),
                (ComplianceDimension.ADVERTISING, RiskLevel.HIGH, "宣传用语含“最佳”“第一”等绝对化表述"),
            ]:
                session.add(
                    ComplianceFinding(
                        scan_id=scan.id,
                        tenant_id=tenant_id,
                        dimension=dim,
                        title=t,
                        risk_level=lvl,
                        suggestion="建议修订相关条款/用语，并保留书面确认记录",
                    )
                )

    # 用量额度（让计费看板有“已用/剩余”）
    for ut, used, limit in [("QA", 37, 100), ("DOCUMENT", 18, 50), ("COMPLIANCE_SCAN", 9, 20)]:
        session.add(
            UsageQuota(
                tenant_id=tenant_id,
                usage_type=UsageType(ut),
                period=_period(),
                limit_count=limit,
                used_count=used,
            )
        )

    await session.flush()
    return {
        "cases": len(cases),
        "dispatches": len(dispatch_specs),
        "reviews": len(review_specs),
        "work_orders": len(wo_specs),
        "scans": len(scan_specs),
    }


async def _seed_platform(session, users: dict) -> dict:
    """平台（platform）演示数据集：让默认登录的“平台管理员”也能看到非空驾驶舱。"""
    tenant_id = "platform"
    if await _already_seeded(session, tenant_id, "PLT-DEMO"):
        return {}
    admin = users.get("admin")

    specs = [
        ("PLT-DEMO-0001", "平台咨询工单-A", CaseStatus.ACCEPTED, CaseGrade.B, "合同纠纷", 40000.0),
        ("PLT-DEMO-0002", "平台咨询工单-B", CaseStatus.IN_REVIEW, CaseGrade.A, "劳动争议", 90000.0),
        ("PLT-DEMO-0003", "平台咨询工单-C", CaseStatus.CONFIRMED, CaseGrade.C, "知识产权", 0.0),
        ("PLT-DEMO-0004", "平台咨询工单-D", CaseStatus.DISPATCHED, CaseGrade.B, "婚姻家庭", 30000.0),
    ]
    cases: dict[str, Case] = {}
    for no, title, status, grade, disp, claim in specs:
        c = Case(
            case_no=no,
            tenant_id=tenant_id,
            title=title,
            client_user_id=admin.id if admin else None,
            lawyer_id=admin.id if admin else None,
            status=status,
            grade=grade,
            intent=IntentType.CONSULT,
            dispute_type=disp,
            claim_amount=claim or None,
            urgency=1,
            summary="平台演示案件",
        )
        session.add(c)
        await session.flush()
        cases[no] = c

    cite = await _citation_resolver(session)
    for case in (cases["PLT-DEMO-0002"], cases["PLT-DEMO-0003"]):
        # 同样修：先建分析，再让 target_id 指向分析 id（不再是 case.id）
        session.add(
            CaseAnalysis(
                tenant_id=tenant_id,
                case_id=case.id,
                version=1,
                summary=f"平台演示案件 {case.case_no}：内部测试用的标准化场景。",
                # 同样只挂**库内可验证**的引用；知识产权在库内无法条 ⇒ 空列表。
                related_laws=cite.related_laws(case.case_no),
                similar_cases=cite.similar_cases(case.case_no),
                suggestions=[{"path": "协商解决", "pros": "成本低", "cons": "无强制力"}],
                missing_info=[{"item": "演示材料", "reason": "演示", "priority": 1}],
                status=ReviewStatus.PENDING_CONFIRM,
                required_level=ReviewLevel.L2,
                ai_generated=1,
            )
        )
        await session.flush()
        # 取刚刚插入的分析（demo 数量少，直接查最新一条）
        latest = (
            await session.execute(
                select(CaseAnalysis).where(CaseAnalysis.case_id == case.id).order_by(CaseAnalysis.id.desc())
            )
        ).scalars().first()
        rid = latest.id if latest else case.id  # 兜底：万一没拿到，至少不崩

        review = Review(
            tenant_id=tenant_id,
            target_type=ReviewTargetType.CASE_ANALYSIS,
            target_id=rid,
            case_id=case.id,
            status=ReviewStatus.PENDING_CONFIRM,
            required_level=ReviewLevel.L2,
            is_forced=0,
            assignee_id=admin.id if admin else None,
        )
        session.add(review)
        await session.flush()
        session.add(
            ReviewRecord(
                tenant_id=tenant_id,
                review_id=review.id,
                action="CREATE",
                actor_id=admin.id if admin else None,
                actor_role="LAWYER",
                level=ReviewLevel.L2,
                from_status=None,
                to_status=ReviewStatus.PENDING_CONFIRM.value,
                comment="平台演示数据：AI 初稿即提交复核",
            )
        )

    for ut, title, wstatus, price in [
        ("DOCUMENT", "平台文书工单", WorkOrderStatus.COMPLETED, 2900),
        ("COMPLIANCE_SCAN", "平台合规扫描工单", WorkOrderStatus.PENDING, 19900),
        ("CONTRACT_REVIEW", "平台合同审查工单", WorkOrderStatus.COMPLETED, 9900),
    ]:
        session.add(
            WorkOrder(
                order_no=_wo_no(tenant_id),
                tenant_id=tenant_id,
                created_by=admin.id if admin else None,
                usage_type=UsageType(ut),
                title=title,
                status=wstatus,
                price_cents=price,
                escalate_to_lawyer=int(ut in ("CONTRACT_REVIEW", "COMPLIANCE_SCAN")),
            )
        )

    for title, st, risk in [
        ("平台全量合规扫描", ScanStatus.COMPLETED, RiskLevel.LOW),
        ("平台宣传合规扫描", ScanStatus.COMPLETED, RiskLevel.MEDIUM),
    ]:
        session.add(
            ComplianceScan(
                tenant_id=tenant_id,
                title=title,
                status=st,
                overall_risk=risk,
                dimensions=[d.value for d in ComplianceDimension],
                dimension_scores={"LABOR": {"score": 88, "risk": "LOW"}},
                finished_at="2026-09-08 10:00:00",
            )
        )

    await session.flush()
    return {"cases": len(cases), "reviews": 2, "work_orders": 3, "scans": 2}


async def seed_business(session) -> dict:
    """写入业务演示数据，返回各类型数量（已存在则跳过，保证幂等）。"""
    users: dict[str, User] = {}
    for uname in ("client", "lawyer_wang", "lawyer_li", "lawyer_zhao", "firm_admin", "admin"):
        user = await _get_user(session, uname)
        if user:
            users[uname] = user

    stats_firm = await _seed_firm(session, users)
    stats_plt = await _seed_platform(session, users)

    return {
        k: stats_firm.get(k, 0) + stats_plt.get(k, 0)
        for k in ("cases", "dispatches", "reviews", "work_orders", "scans")
    }
