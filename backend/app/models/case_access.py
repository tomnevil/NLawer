"""团队案件可见授权（P2-10）：申请-审批制的限时授权。

产品语义（2026-09-29 决策）：律师默认只看自己承办的案件；经「团队内
申请 -> 律所管理员审批」获得授权后，在**有效期内**可见本租户全量案件
（含按 lawyer_id 检索他人承办案件）；到期自动失效，回退为仅本人承办。

为什么落库而不塞进 RBAC：授权有**时效**与**个案审批流**（申请 -> 审批），
RBAC 角色是静态身份能力，两者正交——用一张授权表表达「谁、被谁、
在什么时间窗内、授权了什么范围」，到期即失效，无需定时任务。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin

#: 授权范围（预留扩展；当前唯一合法值）
GRANT_SCOPE_TEAM_READ = "team_read"

#: 申请状态机：PENDING -> APPROVED / REJECTED；到期由查询时按时间判断，
#: 不落 EXPIRED 状态（避免定时任务，列表/详情执行时以 expires_at 为准）。
GRANT_STATUS_PENDING = "PENDING"
GRANT_STATUS_APPROVED = "APPROVED"
GRANT_STATUS_REJECTED = "REJECTED"


class CaseAccessGrant(Base, TenantMixin, TimestampMixin):
    """一次团队案件可见授权（申请-审批记录 + 有效期）。"""

    __tablename__ = "case_access_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 申请人（被授权查看他人承办案件的律师）
    grantee_user_id: Mapped[int] = mapped_column(Integer, index=True)
    # 审批人（律所管理员）；PENDING 时为空
    granted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PENDING / APPROVED / REJECTED
    status: Mapped[str] = mapped_column(String(16), default=GRANT_STATUS_PENDING, index=True)
    # 预留扩展；当前恒为 team_read
    scope: Mapped[str] = mapped_column(String(32), default=GRANT_SCOPE_TEAM_READ)
    # 授权有效期截止（naive UTC）；审批通过时写入
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    # 申请理由（审批页展示）
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
