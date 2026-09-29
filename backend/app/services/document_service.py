"""文书自动化：模板引擎 + 多轮变量收集 + 实时风险提示（PRD 5.9）。

- 模板正文使用 `{{ key }}` 占位符，渲染前校验必填变量完整率
- 风险扫描：命中风险条款即给出修改建议，并据 `template.is_high_risk` 提升复核级别
"""
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ErrorCode, NotFoundError
from app.models.document import Document, DocumentTemplate
from app.models.enums import DocumentStatus, RiskLevel
from app.workflows.forced_review import detect_forced_review, highest_level

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# 风险条款规则库（演示规模，可扩展为独立配置）
#
# `dimension` 供合同审查（P0-16）在**降级路径**下给规则命中项标注风险维度；
# `scan_risks` 只取 keyword/level/issue/suggestion 四键，多出的键对它无影响。
_RISK_RULES: list[dict[str, str]] = [
    {"keyword": "违约金", "level": "MEDIUM", "issue": "违约金标准未明确或可能过高",
     "suggestion": "明确违约金计算方式，一般不超过实际损失的 30%",
     "dimension": "违约责任"},
    {"keyword": "不可撤销", "level": "HIGH", "issue": "不可撤销条款可能过度限制己方权利",
     "suggestion": "增加撤销情形与通知程序", "dimension": "权利义务失衡"},
    {"keyword": "放弃", "level": "HIGH", "issue": "含权利放弃表述，可能导致实体权利丧失",
     "suggestion": "限缩放弃范围，明确放弃的具体权利与期限", "dimension": "权利义务失衡"},
    {"keyword": "免责", "level": "MEDIUM", "issue": "免责条款可能因免除己方主要责任而无效",
     "suggestion": "调整免责范围，避免免除故意或重大过失责任", "dimension": "效力瑕疵"},
    {"keyword": "单方", "level": "MEDIUM", "issue": "单方权利条款可能造成权利义务失衡",
     "suggestion": "增加对等条款或设置前置条件", "dimension": "权利义务失衡"},
    {"keyword": "最终解释权", "level": "HIGH", "issue": "格式条款中「最终解释权」条款通常无效",
     "suggestion": "删除该表述，改为双方协商解释", "dimension": "效力瑕疵"},
    {"keyword": "管辖", "level": "LOW", "issue": "管辖法院约定可能影响维权成本",
     "suggestion": "优先选择己方所在地法院管辖", "dimension": "争议解决"},
]


class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 模板 ----------------
    #: 模板列表硬上限。模板是"供用户挑选"的目录型数据（PRD 目标 200+），
    #: 不是业务流水，因此不做分页（前端需一次性渲染下拉/卡片列表）；
    #: 但必须有上限，否则异常数据增长会让响应体无界膨胀。
    TEMPLATE_LIST_MAX: int = 500

    async def list_templates(
        self, tenant_id: str, lifecycle: Optional[str] = None, keyword: Optional[str] = None
    ) -> list[DocumentTemplate]:
        stmt = select(DocumentTemplate).where(DocumentTemplate.tenant_id.in_([tenant_id, "platform"]))
        if lifecycle:
            stmt = stmt.where(DocumentTemplate.lifecycle == lifecycle)
        if keyword:
            stmt = stmt.where(DocumentTemplate.name.like(f"%{keyword}%"))
        # 目录型数据保序 + 加上限（避免无界结果集）
        stmt = stmt.order_by(DocumentTemplate.id.asc()).limit(self.TEMPLATE_LIST_MAX)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_template(self, template_id: int) -> DocumentTemplate:
        t = await self.db.get(DocumentTemplate, template_id)
        if t is None:
            raise NotFoundError("模板不存在", code=ErrorCode.TEMPLATE_NOT_FOUND)
        return t

    # ---------------- 创建与变量收集 ----------------
    async def start(self, template_id: int, *, tenant_id: str, user_id: int, case_id: Optional[int] = None) -> Document:
        tpl = await self.get_template(template_id)
        doc = Document(
            tenant_id=tenant_id,
            template_id=tpl.id,
            case_id=case_id,
            created_by=user_id,
            title=tpl.name,
            status=DocumentStatus.COLLECTING,
            variables={},
            missing_variables=self._missing(tpl.variables or [], {}),
        )
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def collect(self, doc_id: int, variables: dict) -> Document:
        doc = await self._get(doc_id)
        merged = {**(doc.variables or {}), **variables}
        doc.variables = merged
        tpl = await self.get_template(doc.template_id) if doc.template_id else None
        spec = tpl.variables if tpl else []
        doc.missing_variables = self._missing(spec or [], merged)
        doc.status = DocumentStatus.COLLECTING if doc.missing_variables else DocumentStatus.DRAFT
        return doc

    async def render(self, doc_id: int) -> Document:
        """渲染正文（必填变量缺失则拒绝生成）。"""
        doc = await self._get(doc_id)
        if doc.missing_variables:
            raise BadRequestError(
                "必填变量尚未收集完整",
                code=ErrorCode.DOCUMENT_VARIABLES_INCOMPLETE,
                details={"missing": doc.missing_variables},
            )
        tpl = await self.get_template(doc.template_id) if doc.template_id else None
        body = tpl.body if tpl else ""
        doc.content = _render(body, doc.variables or {})
        doc.status = DocumentStatus.GENERATED
        if tpl is not None:
            tpl.usage_count = (tpl.usage_count or 0) + 1

        # 风险扫描
        findings = self.scan_risks(doc.content)
        doc.risk_findings = findings
        doc.overall_risk = _overall(findings)

        # 高风险文书 / 强制复核级别
        hits = detect_forced_review(document=_FakeDoc(tpl.name if tpl else "", tpl.is_high_risk if tpl else False))
        doc.required_level = highest_level(hits)
        return doc

    # ---------------- 风险扫描 ----------------
    def scan_risks(self, content: str) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for rule in _RISK_RULES:
            for m in re.finditer(re.escape(rule["keyword"]), content or ""):
                start = max(0, m.start() - 20)
                snippet = (content or "")[start : m.end() + 20]
                findings.append(
                    {
                        "clause": snippet,
                        "risk_level": rule["level"],
                        "issue": rule["issue"],
                        "suggestion": rule["suggestion"],
                    }
                )
                break  # 同类风险只报一次，避免刷屏
        return findings

    # ---------------- 辅助 ----------------
    def _missing(self, spec: list[dict], collected: dict) -> list[dict]:
        return [
            {"key": v.get("key"), "label": v.get("label")}
            for v in spec
            if v.get("required") and not (collected.get(v.get("key")) or "").strip()
        ]

    async def _get(self, doc_id: int) -> Document:
        d = await self.db.get(Document, doc_id)
        if d is None:
            raise NotFoundError("文书不存在", code=ErrorCode.DOCUMENT_NOT_FOUND)
        return d


class _FakeDoc:
    """轻量适配：让 forced_review 只依据名称与高风险标记判定。"""

    def __init__(self, name: str, is_high_risk: bool) -> None:
        self.name = name
        self.title = name
        self.is_high_risk = is_high_risk


def _render(body: str, variables: dict) -> str:
    def _sub(m: re.Match) -> str:
        key = m.group(1)
        return str(variables.get(key, f"【待补充：{key}】"))

    return _PLACEHOLDER_RE.sub(_sub, body or "")


def _overall(findings: list[dict]) -> RiskLevel:
    if any(f["risk_level"] == "HIGH" for f in findings):
        return RiskLevel.HIGH
    if any(f["risk_level"] == "MEDIUM" for f in findings):
        return RiskLevel.MEDIUM
    if findings:
        return RiskLevel.LOW
    return RiskLevel.NONE
