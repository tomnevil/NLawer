"""全局枚举定义。集中放置便于跨模块复用与前端对齐。"""
from enum import Enum


# ---------------- 租户与身份 ----------------
class TenantType(str, Enum):
    """租户类型：律所（产品线 A）/ 企业（产品线 B）/ 平台。"""

    PLATFORM = "PLATFORM"
    LAW_FIRM = "LAW_FIRM"
    ENTERPRISE = "ENTERPRISE"


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"


# ---------------- 会话与消息（IM）----------------
class ConversationStatus(str, Enum):
    """会话状态机：AI 接待 -> 等待人工 -> 人工接入 -> 结束。"""

    BOT = "BOT"                    # AI 接待中
    WAITING_HUMAN = "WAITING_HUMAN"  # 已转人工，等待律师接单
    HUMAN = "HUMAN"                # 律师已接入
    CLOSED = "CLOSED"              # 已结束


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    VOICE = "voice"
    VIDEO = "video"
    CARD = "card"      # 结构化卡片（材料清单 / 满意度评价）
    EVENT = "event"    # 系统事件（派单、接单、定稿）


class MessageSender(str, Enum):
    CLIENT = "CLIENT"
    AI = "AI"
    LAWYER = "LAWYER"
    SYSTEM = "SYSTEM"


class IMChannel(str, Enum):
    WEB_SIM = "web_sim"
    WECOM = "wecom"
    FEISHU = "feishu"


# ---------------- 意图与分级 ----------------
class IntentType(str, Enum):
    """PRD 5.2：五类意图。"""

    CONSULT = "CONSULT"        # 咨询类
    DOCUMENT = "DOCUMENT"      # 文书类
    CALCULATION = "CALCULATION"  # 计算类
    REVIEW = "REVIEW"          # 审查类
    ENTRUST = "ENTRUST"        # 委托类


class CaseGrade(str, Enum):
    """PRD 5.2：案件分级，决定复核深度与响应时效。"""

    S = "S"  # 重大复杂
    A = "A"  # 较复杂
    B = "B"  # 一般
    C = "C"  # 简单


# ---------------- 案件与派单 ----------------
class CaseStatus(str, Enum):
    INTAKE = "INTAKE"            # 接待中
    PENDING_DISPATCH = "PENDING_DISPATCH"  # 待派单
    DISPATCHED = "DISPATCHED"    # 已派单待接
    ACCEPTED = "ACCEPTED"        # 已接单办案中
    IN_REVIEW = "IN_REVIEW"      # 复核中
    CONFIRMED = "CONFIRMED"      # 已确认定稿
    ARCHIVED = "ARCHIVED"        # 已归档
    CLOSED = "CLOSED"            # 已结案
    VOIDED = "VOIDED"            # 已作废


class DispatchMode(str, Enum):
    """PRD 5.2：三种派单方式。"""

    DESIGNATED = "DESIGNATED"  # 客户指定律师
    AUTO = "AUTO"              # 系统自动派单
    POOL = "POOL"              # 律师抢单


class DispatchStatus(str, Enum):
    PENDING = "PENDING"      # 待接单
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# ---------------- 异步任务 ----------------
class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class JobType(str, Enum):
    CASE_ANALYSIS = "case_analysis"
    EVIDENCE_PARSE = "evidence_parse"
    COMPLIANCE_SCAN = "compliance_scan"
    DOCUMENT_GEN = "document_gen"
    # 2026-09-23 新增：异步合同审查。
    # 由来：`source_text` 是**唯一无截断的成本放大口**（同步端点已限 20k），
    # 超限后 413 会引导 `guidance=upload_or_async` —— 但此前**没有这条异步通道**，
    # 等于指引用户走一条不存在的路。此枚举项是 B1 决策门选项①的落地前提。
    # ⚠️ SQLAlchemy 2.0 的 `Enum(native_enum=False)` 默认 `create_constraint=False`
    #    ⇒ 新增成员**不需要**改 CHECK 约束/迁移（列就是 VARCHAR(32)）。
    CONTRACT_REVIEW = "contract_review"


# ---------------- 证据 ----------------
class EvidenceCategory(str, Enum):
    """PRD 5.4：五类证据自动归类。"""

    CONTRACT = "CONTRACT"              # 合同类
    PAYMENT = "PAYMENT"                # 支付凭证类
    COMMUNICATION = "COMMUNICATION"    # 沟通记录类
    IDENTITY = "IDENTITY"              # 身份证明类
    OFFICIAL = "OFFICIAL"              # 公文书类
    OTHER = "OTHER"


class EvidenceStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    FAILED = "FAILED"


# ---------------- 复核工作流 ----------------
class ReviewStatus(str, Enum):
    """PRD 5.5：复核状态机（未确认不可定稿 / 不可归档）。"""

    DRAFT = "draft"                    # AI 初稿
    LAWYER_EDITING = "lawyer_editing"  # 律师修改中
    PENDING_CONFIRM = "pending_confirm"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"
    VOIDED = "voided"


class ReviewLevel(str, Enum):
    """PRD 5.5：三级复核。"""

    L1 = "L1"  # AI 自检
    L2 = "L2"  # 律师复核
    L3 = "L3"  # 合伙人 / 高级律师终审


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"


class ReviewTargetType(str, Enum):
    CASE_ANALYSIS = "CASE_ANALYSIS"
    DOCUMENT = "DOCUMENT"
    EVIDENCE_LIST = "EVIDENCE_LIST"
    COMPLIANCE_REPORT = "COMPLIANCE_REPORT"
    LEGAL_OPINION = "LEGAL_OPINION"


# ---------------- 引用溯源 ----------------
class CitationSourceType(str, Enum):
    LAW = "LAW"              # 法律法规
    CASE = "CASE"            # 判例 / 指导案例
    TEMPLATE = "TEMPLATE"    # 文书模板
    KNOWLEDGE = "KNOWLEDGE"  # 企业私有知识


# ---------------- 文书 ----------------
class DocumentStatus(str, Enum):
    DRAFT = "DRAFT"
    COLLECTING = "COLLECTING"  # 变量收集中
    GENERATED = "GENERATED"
    IN_REVIEW = "IN_REVIEW"
    CONFIRMED = "CONFIRMED"
    EXPORTED = "EXPORTED"
    VOIDED = "VOIDED"


class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


# ---------------- 合同审查（P0-16 真实化）----------------
class ContractReviewSource(str, Enum):
    """审查产出的来源。

    `rule` 不只是「规则引擎产出」，也是**任何未经真实模型逐条通读的产出**
    （含 LLM 失败后的规则兜底）——它存在的意义是让用户能一眼分辨
    「这份结论到底是不是模型给的」，而不是把规则产出伪装成 AI 分析。
    """

    LLM = "llm"
    RULE = "rule"
    MOCK = "mock"


class ContractReviewStatus(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"  # 模型不可用，已降级为规则预筛（不计费）
    FAILED = "failed"      # 未产出任何业务结论（不计费）


class ContractAnalysisStatus(str, Enum):
    """审查深度三态——**用来消除「零发现即放行」**。

    没有这个字段时，「模型通读后确认无风险」与「规则库没命中任何关键词」
    在返回体上完全无法区分，两者都会表现为「无风险」，而后者是**系统性
    假阴性**。专业场景下把假阴性包装成「审查通过」，比多报风险危险得多。
    """

    COMPLETE_NO_RISK = "complete_no_risk"  # 真实模型逐条通读且未发现风险，NONE 合法
    PRESCREEN_ONLY = "prescreen_only"      # 仅规则预筛，**禁止**输出 NONE / 禁止宣称无风险
    RISK_FOUND = "risk_found"


class BasisType(str, Enum):
    """风险结论的依据分层（禁止硬凑法条）。"""

    STATUTE = "statute"        # 已在 law_articles 精确命中的法条
    EXPERIENCE = "experience"  # 经验判断，暂无明确法条依据（本库 12 条下属常态）
    MANUAL = "manual"          # 需人工确认


# ---------------- 合规扫描 ----------------
class ComplianceDimension(str, Enum):
    """PRD 5.10：四维合规扫描。"""

    LABOR = "LABOR"              # 劳动用工
    COMMERCIAL = "COMMERCIAL"    # 商业合同
    DATA_PRIVACY = "DATA_PRIVACY"  # 数据隐私
    ADVERTISING = "ADVERTISING"  # 广告营销


class ScanStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------- 计费 ----------------
class WorkOrderStatus(str, Enum):
    PENDING = "PENDING"      # 待处理
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class UsageType(str, Enum):
    QA = "QA"
    DOCUMENT = "DOCUMENT"
    CONTRACT_REVIEW = "CONTRACT_REVIEW"
    COMPLIANCE_SCAN = "COMPLIANCE_SCAN"


# ---------------- 通知 ----------------
class NotificationType(str, Enum):
    DISPATCH_CREATED = "DISPATCH_CREATED"
    CASE_ACCEPTED = "CASE_ACCEPTED"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REVIEW_DECIDED = "REVIEW_DECIDED"
    DOCUMENT_CONFIRMED = "DOCUMENT_CONFIRMED"
    CASE_ARCHIVED = "CASE_ARCHIVED"
    QUOTA_WARNING = "QUOTA_WARNING"
    WORK_ORDER_CREATED = "WORK_ORDER_CREATED"
