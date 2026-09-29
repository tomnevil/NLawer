"""四维合规扫描：劳动用工 / 商业合同 / 数据隐私 / 广告营销（PRD 5.10）。

输出：分维度得分与风险等级 + 整改建议清单 + 整体报告。
对外版本（`is_external`）自动命中强制 L2 复核（见 forced_review）。
"""
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


class ComplianceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_scan(
        self,
        *,
        title: str,
        tenant_id: str,
        dimensions: Optional[list[str]] = None,
        scope: Optional[str] = None,
        input_summary: Optional[str] = None,
        is_external: bool = False,
    ) -> ComplianceScan:
        dims = dimensions or [d.value for d in ComplianceDimension]
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

        scores: dict[str, Any] = {}
        for dim in dims:
            rules = _DIMENSION_RULES[dim]
            hits = [r for r in rules if r["kw"] in text]
            # 起始 100 分，命中一项扣 25，命中高风险额外扣 10
            score = 100
            for r in hits:
                score -= 25 if r["risk"] == "HIGH" else 15
            score = max(score, 20)
            risk = "HIGH" if score < 60 else ("MEDIUM" if score < 85 else "LOW")
            scores[dim.value] = {"score": score, "risk": risk, "findings": len(hits)}

            for r in hits:
                self.db.add(
                    ComplianceFinding(
                        tenant_id=scan.tenant_id,
                        scan_id=scan.id,
                        dimension=dim,
                        title=r["title"],
                        description=f"命中关键词：{r['kw']}",
                        risk_level=RiskLevel(r["risk"]),
                        suggestion=r["suggestion"],
                    )
                )

        scan.dimension_scores = scores
        avg = sum(v["score"] for v in scores.values()) // max(len(scores), 1)
        scan.overall_risk = RiskLevel("HIGH" if avg < 60 else ("MEDIUM" if avg < 85 else "LOW"))
        scan.report_summary = self._summary(scores, avg)
        scan.status = ScanStatus.COMPLETED
        scan.finished_at = _now()

        # 对外报告 -> 强制 L2 复核
        hits = detect_forced_review(compliance_report=bool(scan.is_external))
        scan.review_status = highest_level(hits).value
        return scan

    async def findings(self, scan_id: int) -> list[ComplianceFinding]:
        rows = (
            await self.db.execute(
                select(ComplianceFinding).where(ComplianceFinding.scan_id == scan_id).order_by(ComplianceFinding.id.asc())
            )
        ).scalars().all()
        return list(rows)

    def _summary(self, scores: dict[str, Any], avg: int) -> str:
        parts = []
        for dim, v in scores.items():
            label = _DIMENSION_LABEL.get(ComplianceDimension(dim), dim)
            parts.append(f"{label} {v['score']} 分（{v['risk']}，{v['findings']} 项发现）")
        return f"综合得分 {avg} 分。\n" + "\n".join(parts)


def _now() -> str:
    import datetime

    return datetime.datetime.now().isoformat(timespec="seconds")
