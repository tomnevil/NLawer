"""引用溯源模型（PRD 5.11）。

硬约束：每条结论性陈述必须至少对应一个可点击引用；
引用缺失视为生成失败，由 `citation_service.validate()` 在落库前拦截。
"""
from typing import Optional

from sqlalchemy import Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin
from app.models.enums import CitationSourceType


class Citation(Base, TenantMixin, TimestampMixin):
    """引用：法规条款 / 判例 / 模板 / 企业私有知识的溯源锚点。"""

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # platform = 公共法规库；企业租户 = 私有知识引用
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[CitationSourceType] = mapped_column(
        Enum(CitationSourceType, native_enum=False, length=32),
        default=CitationSourceType.LAW,
    )
    # 来源库中的主键（laws.id / cases.id / knowledge_docs.id）
    source_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ---- 展示用字段 ----
    title: Mapped[str] = mapped_column(String(500))
    # 法规名称 + 条款号，如「中华人民共和国劳动合同法 第87条」
    reference: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # 原文摘要（侧边栏抽屉展示）
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 发布机关 / 法院
    authority: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 生效日期，用于时效性标记
    effective_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # 原文链接（法规库官方页面）
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 时效性：CURRENT（现行有效）/ AMENDED（已修订）/ ABOLISHED（已废止）
    timeliness: Mapped[str] = mapped_column(String(32), default="CURRENT")


class LawArticle(Base, TenantMixin, TimestampMixin):
    """法规条文（种子知识库）。租户固定为 platform，全平台共享。"""

    __tablename__ = "law_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    law_name: Mapped[str] = mapped_column(String(300), index=True)
    article_no: Mapped[str] = mapped_column(String(64))
    chapter: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    # 适用纠纷类型标签，逗号分隔：劳动争议,合同纠纷
    tags: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    effective_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # 地区（全国 / 广西 / 广东 ...），支持地方法规筛选
    region: Mapped[str] = mapped_column(String(64), default="全国")
    timeliness: Mapped[str] = mapped_column(String(32), default="CURRENT")


class CasePrecedent(Base, TenantMixin, TimestampMixin):
    """指导案例与类案（脱敏后引用，PRD 5.3 类案参考）。"""

    __tablename__ = "case_precedents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_no: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(500))
    court: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 裁判要旨
    holding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 案情摘要（脱敏）
    facts: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispute_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 关联法条，逗号分隔：劳动合同法第87条
    related_laws: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    judgment_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    region: Mapped[str] = mapped_column(String(64), default="全国")
