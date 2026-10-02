"""模型包：统一导入以保证 Base.metadata 完整注册（Alembic 依赖）。

导入顺序有讲究：被外键引用的表必须先注册（users / cases / conversations）。
"""
from app.models.ai_run import AiDecision, AiRun  # noqa: F401
from app.models.analysis import CaseAnalysis, CaseAnalysisVersion  # noqa: F401
from app.models.archive import Archive, ArchiveVersion, HearingPack  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.auth_session import RefreshSession  # noqa: F401
from app.models.billing import (  # noqa: F401
    Subscription,
    UsageQuota,
    UsageRecord,
    WorkOrder,
)
from app.models.case import Case, CaseEvent, Dispatch, DispatchRule  # noqa: F401
from app.models.case_access import CaseAccessGrant  # noqa: F401
from app.models.citation import (  # noqa: F401
    CasePrecedent,
    Citation,
    LawArticle,
)
from app.models.complaint import (  # noqa: F401
    Complaint,
    ComplaintStatus,
    ComplaintType,
)
from app.models.consult_report import ConsultReport, ConsultReportStatus  # noqa: F401
from app.models.conversation import Conversation, Message  # noqa: F401
from app.models.document import (  # noqa: F401
    ContractReview,
    Document,
    DocumentTemplate,
)
from app.models.embedding import KnowledgeEmbedding  # noqa: F401
from app.models.evidence import Evidence, EvidenceChecklist  # noqa: F401
from app.models.identity import LawyerProfile, Tenant, User  # noqa: F401
from app.models.job import Job  # noqa: F401
from app.models.knowledge import (  # noqa: F401
    ComplianceFinding,
    ComplianceScan,
    KnowledgeDoc,
)
from app.models.moderation import ModerationRecord, ReportStatus  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.review import Review, ReviewRecord  # noqa: F401

__all__ = [
    "AuditLog",
    "LawyerProfile",
    "RefreshSession",
    "CaseAccessGrant",
    "Tenant",
    "User",
    "Conversation",
    "Message",
    "ConsultReport",
    "ConsultReportStatus",
    "Case",
    "CaseEvent",
    "Dispatch",
    "DispatchRule",
    "Job",
    "AiDecision",
    "AiRun",
    "CaseAnalysis",
    "CaseAnalysisVersion",
    "Evidence",
    "EvidenceChecklist",
    "Review",
    "ReviewRecord",
    "CasePrecedent",
    "Citation",
    "LawArticle",
    "ContractReview",
    "Document",
    "DocumentTemplate",
    "Archive",
    "ArchiveVersion",
    "HearingPack",
    "ComplianceFinding",
    "ComplianceScan",
    "KnowledgeDoc",
    "Subscription",
    "UsageQuota",
    "UsageRecord",
    "WorkOrder",
    "KnowledgeEmbedding",
    "Notification",
    "ModerationRecord",
    "Complaint",
    "ComplaintStatus",
    "ComplaintType",
    "ReportStatus",
]
