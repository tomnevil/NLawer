"""身份与租户模型：Tenant / User / LawyerProfile。"""
from typing import Optional

from sqlalchemy import Boolean, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.rbac import Role
from app.models.base import Base, TenantMixin, TimestampMixin
from app.models.enums import TenantType, UserStatus


class Tenant(Base, TimestampMixin):
    """租户：律所（产品线 A）或企业（产品线 B）。数据按 tenant_id 逻辑隔离。"""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[TenantType] = mapped_column(
        Enum(TenantType, native_enum=False, length=32), default=TenantType.LAW_FIRM
    )
    # 律所专属：对外展示名称（IM 机器人顶部栏显示）
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 企业专属：行业属性，用于合规扫描与知识库个性化
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 订阅席位上限（律所按律师席位订阅）
    seat_limit: Mapped[int] = mapped_column(Integer, default=5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 机器人配置：欢迎语、免责声明文案（可按租户定制）
    bot_welcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bot_disclaimer: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default="本内容由 AI 生成，仅供参考，不构成法律意见"
    )


class User(Base, TenantMixin, TimestampMixin):
    """平台用户。客户（CLIENT）也可不绑定租户，由会话的绑定关系解析。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), unique=True, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32), default=Role.CLIENT
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False, length=32), default=UserStatus.ACTIVE
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # IM 侧外部标识（web_sim / wecom / feishu 的 openid），客户由 IM 进入时填充
    external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    im_channel: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class LawyerProfile(Base, TenantMixin, TimestampMixin):
    """律师档案：派单匹配的依据（专业领域、执业年限、忙闲状态）。"""

    __tablename__ = "lawyer_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    # 专业领域标签，逗号分隔：劳动争议,合同纠纷,婚姻家庭
    specialties: Mapped[str] = mapped_column(String(500), default="")
    # 执业年限，用于派单权重
    practice_years: Mapped[int] = mapped_column(Integer, default=0)
    license_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 当前在手案件数（由派单服务维护，用于负载均衡）
    active_case_count: Mapped[int] = mapped_column(Integer, default=0)
    # 忙闲状态：True 表示可接单
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    # 是否具备 L3 高级复核资格（合伙人 / 高级律师）
    can_l3_review: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
