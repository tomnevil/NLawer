"""审计日志保留期 / 归档 / 清理管理接口（P1，等保 2.0 三级）。

**为什么清理审计日志需要专门的管理接口？**
等保 2.0（三级）要求审计记录留存**不少于 6 个月**；同时《个人信息保护法》
的最小必要原则又禁止无限期留存。两个约束夹出一个三段式流程：
**保底留存 → 到期归档 → 归档校验通过后清理**。
中间任何一步都可能出错（磁盘满、导出截断、误删），因此：

- 所有写操作（归档/清理）**仅平台管理员**可调用，并受
  `AUDIT_RETENTION_ADMIN_ENABLED` 开关约束（生产可临时关闭）。
- `purge` **默认 dry-run**，必须显式传 `confirm=true` 才真的删除。
- `archive-then-purge` 是推荐入口：归档行数与待清理行数不一致时**拒绝清理**。
- 每次清理都会写入 `AUDIT_RETENTION_PURGE` 墓碑审计——
  **审计的删除也必须被审计**。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_db, require_roles
from app.core.errors import ConfigurationError
from app.core.pagination import ok
from app.core.rbac import Role
from app.services.audit_retention import (
    SECURITY_BASELINE_DAYS,
    AuditRetentionService,
    retention_days,
)

router = APIRouter(prefix="/audit/retention", tags=["审计保留期"])


class PurgeRequest(BaseModel):
    # 🚨 等保下限必须同时拦在**请求**这一层。
    # `retention_days()` 只对**配置值**做了下限保护（配 30 天会抬到 180 天），
    # 而服务层的 `cutoff_at(days)` 是 `days if days is not None else retention_days()`
    # ⇒ 请求里传 1 天就按 1 天删。历史缺陷（L4/L5 坐实）：
    # `POST /audit/retention/purge {"days":1,"confirm":true}` 会清掉
    # 等保要求留存 6 个月的审计日志。
    days: Optional[int] = Field(
        None,
        ge=SECURITY_BASELINE_DAYS,
        description=f"保留天数，缺省用配置值；不得低于等保下限 {SECURITY_BASELINE_DAYS} 天",
    )
    confirm: bool = Field(
        False,
        description="必须显式传 true 才真的删除；缺省为 dry-run（只统计不删除）",
    )


class ArchiveRequest(BaseModel):
    days: Optional[int] = Field(
        None,
        ge=SECURITY_BASELINE_DAYS,
        description=f"保留天数，缺省用配置值；不得低于等保下限 {SECURITY_BASELINE_DAYS} 天",
    )
    out_dir: Optional[str] = Field(None, description="归档输出目录，缺省用配置值")


def _ensure_enabled() -> None:
    """开关关闭时直接拒绝——清理是不可逆操作，需要一个"急停"开关。"""
    if not getattr(settings, "AUDIT_RETENTION_ADMIN_ENABLED", True):
        raise ConfigurationError(
            "审计保留期管理已在配置中关闭（AUDIT_RETENTION_ADMIN_ENABLED=false）"
        )


@router.get("/stats", response_model=dict, summary="审计日志保留状态（管理员）")
async def retention_stats(
    db: AsyncSession = Depends(get_db),
    ctx=Depends(require_roles(Role.PLATFORM_ADMIN)),
):
    """回答两个运维问题：① 我合规吗？② 我快爆了吗？"""
    _ensure_enabled()
    return ok(await AuditRetentionService(db).stats())


@router.post("/archive", response_model=dict, summary="归档过期审计日志（管理员）")
async def retention_archive(
    payload: ArchiveRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(require_roles(Role.PLATFORM_ADMIN)),
):
    """导出过期日志为 gzip JSONL，返回行数与 sha256 供人工核对。

    **只读不删**——归档本身不改变数据库内容，因此不需要 confirm。
    """
    _ensure_enabled()
    return ok(await AuditRetentionService(db).archive(days=payload.days, out_dir=payload.out_dir))


@router.post("/purge", response_model=dict, summary="清理过期审计日志（管理员，默认 dry-run）")
async def retention_purge(
    payload: PurgeRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(require_roles(Role.PLATFORM_ADMIN)),
):
    """删除超出保留期的记录。

    **默认 dry-run**：不传 `confirm` 或传 `false` 时只返回 `would_delete`。
    确认无误后再传 `confirm=true`。
    """
    _ensure_enabled()
    # purge() 内部已在写墓碑前自行 commit（顺序对锁竞争敏感），此处不再重复提交
    return ok(await AuditRetentionService(db).purge(days=payload.days, confirm=payload.confirm))


@router.post(
    "/archive-then-purge",
    response_model=dict,
    summary="归档并清理（推荐入口，管理员，默认 dry-run）",
)
async def retention_archive_then_purge(
    payload: PurgeRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(require_roles(Role.PLATFORM_ADMIN)),
):
    """推荐的运维入口：先归档并校验行数，一致才清理。

    行数不一致时**拒绝清理**（宁可保留，不可丢失）。
    """
    _ensure_enabled()
    # archive_then_purge 内部的 purge 已自行 commit，此处不再重复提交
    return ok(
        await AuditRetentionService(db).archive_then_purge(
            days=payload.days, confirm=payload.confirm
        )
    )


@router.get("/policy", response_model=dict, summary="保留策略说明（公开）")
async def retention_policy(
    days: int = Query(0, description="传入自定义值可预览生效结果", include_in_schema=False),
):
    """公开展示保留策略，便于合规审查时直接取用。"""
    return ok(
        {
            "baseline_days": SECURITY_BASELINE_DAYS,
            "effective_days": retention_days(),
            "basis": "等保 2.0（三级）：审计记录留存不少于 6 个月",
            "policy": [
                "保留期内不得删除；到期前必须先归档",
                "归档产物为 gzip JSONL，附 sha256 校验值",
                "归档行数与待清理行数不一致时拒绝清理",
                "清理操作默认 dry-run，需显式确认",
                "每次清理写入 AUDIT_RETENTION_PURGE 审计记录",
            ],
        }
    )
