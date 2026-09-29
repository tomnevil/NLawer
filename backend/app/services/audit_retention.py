"""审计日志保留期、归档与轮转（P1）。

法律依据与工程理由：
- **等保 2.0（三级）** 要求审计记录**留存不少于 6 个月**（180 天）。
  留存不足即不合规；**无限期留存**同样是问题——审计表会无限膨胀，
  拖垮查询性能，且与《个人信息保护法》的最小必要原则相冲突
  （审计 detail 里可能含个人信息）。
- 因此正解是「**保底留存 + 到期归档 + 归档后清理**」三段式：
  1. 到期前**必须**能整批导出（否则等保检查时"昨天还在的日志今天没了"）
  2. 导出成功且校验通过后才允许删除
  3. 删除动作本身**必须留痕**（审计的删除也要被审计）

设计要点：
- **默认 dry-run**：`purge()` 默认不删只统计，必须显式 `confirm=True` 才落刀。
  清理是不可逆操作，默认值必须站在安全一侧。
- **分批删除**：一次性删除千万行会长时间持锁并可能撑爆事务日志，
  因此按 `batch_size` 循环删除。
- **归档先于删除**：`archive()` 生成 JSONL 并返回校验信息（行数 + sha256），
  供调用方核对后再执行 `purge`。
"""
from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import os
from typing import Any, Optional

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_log import AuditLog

#: 等保 2.0（三级）的审计留存下限（天）
SECURITY_BASELINE_DAYS = 180


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def retention_days() -> int:
    """生效的保留天数。低于等保下限时告警并**强制抬到下限**。

    为什么不直接用它配置的值：把保留期配成 30 天是**明确的不合规**，
    而这种配置错误往往在检查时才被发现。因此这里选择"不遵从错误配置"，
    并把冲突写进日志——宁可多留，不可少留。
    """
    configured = int(getattr(settings, "AUDIT_RETENTION_DAYS", SECURITY_BASELINE_DAYS))
    if configured < SECURITY_BASELINE_DAYS:
        logger.warning(
            "AUDIT_RETENTION_DAYS={} 低于等保下限 {} 天，已按 {} 天执行（请在配置中修正）",
            configured,
            SECURITY_BASELINE_DAYS,
            SECURITY_BASELINE_DAYS,
        )
        return SECURITY_BASELINE_DAYS
    return configured


def cutoff_at(days: Optional[int] = None) -> datetime.datetime:
    """早于此时刻的记录视为"已过保留期"。"""
    return _utcnow() - datetime.timedelta(days=days if days is not None else retention_days())


class AuditRetentionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 统计 ----------------
    async def stats(self) -> dict[str, Any]:
        """保留状态全景：总量 / 过期量 / 最老与最新时间。

        运维据此回答两个问题：① 我合规吗（有没有超过保留期还留着的）？
        ② 我快爆了吗（总量增长趋势）？
        """
        total = int((await self.db.execute(select(func.count()).select_from(AuditLog))).scalar() or 0)
        cutoff = cutoff_at()

        oldest = (await self.db.execute(select(func.min(AuditLog.created_at)))).scalar()
        newest = (await self.db.execute(select(func.max(AuditLog.created_at)))).scalar()

        expired = int(
            (
                await self.db.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.created_at < cutoff)
                )
            ).scalar()
            or 0
        )

        return {
            "total": total,
            "expired": expired,
            "retained_days": retention_days(),
            "baseline_days": SECURITY_BASELINE_DAYS,
            "compliant": True,  # retention_days() 已保证不低于下限
            "cutoff": cutoff.isoformat(),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
        }

    # ---------------- 归档 ----------------
    async def archive(
        self,
        *,
        days: Optional[int] = None,
        out_dir: Optional[str] = None,
        batch_size: int = 2000,
    ) -> dict[str, Any]:
        """把超出保留期的记录导出为 gzip JSONL。

        返回 `{path, rows, sha256, bytes, cutoff}`。
        **只有在拿到并核对 sha256 之后，才有资格执行 `purge()`。**
        """
        cutoff = cutoff_at(days)
        base = out_dir or getattr(settings, "AUDIT_ARCHIVE_DIR", "storage/audit_archive")
        os.makedirs(base, exist_ok=True)
        stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(base, f"audit_archive_{stamp}.jsonl.gz")

        hasher = hashlib.sha256()
        rows = 0
        last_id = 0

        with gzip.open(path, "wt", encoding="utf-8") as fh:
            while True:
                stmt = (
                    select(AuditLog)
                    .where(AuditLog.created_at < cutoff, AuditLog.id > last_id)
                    .order_by(AuditLog.id)
                    .limit(batch_size)
                )
                batch = list((await self.db.execute(stmt)).scalars().all())
                if not batch:
                    break
                for r in batch:
                    line = json.dumps(_to_dict(r), ensure_ascii=False, default=str)
                    hasher.update((line + "\n").encode("utf-8"))
                    fh.write(line + "\n")
                    rows += 1
                    last_id = r.id

        size = os.path.getsize(path)
        digest = hasher.hexdigest()
        logger.info("审计归档完成：{} 行 → {}（sha256={}）", rows, path, digest[:16])
        return {
            "path": path,
            "rows": rows,
            "sha256": digest,
            "bytes": size,
            "cutoff": cutoff.isoformat(),
        }

    # ---------------- 清理 ----------------
    async def purge(
        self,
        *,
        days: Optional[int] = None,
        confirm: bool = False,
        batch_size: int = 2000,
        max_batches: int = 200,
        write_tombstone: bool = True,
    ) -> dict[str, Any]:
        """删除超出保留期的记录。

        **默认 dry-run**（`confirm=False` 只统计不删除）——
        清理不可逆，默认值必须站在安全一侧。

        `write_tombstone=True` 时，删除行为自身写入一条审计
        （`AUDIT_RETENTION_PURGE`）：**审计的删除也必须被审计**，
        否则无法回答"是谁在什么时候清掉了哪段日志"。
        """
        cutoff = cutoff_at(days)
        total_expired = int(
            (
                await self.db.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.created_at < cutoff)
                )
            ).scalar()
            or 0
        )

        if not confirm:
            return {
                "dry_run": True,
                "would_delete": total_expired,
                "cutoff": cutoff.isoformat(),
                "retained_days": retention_days(),
                "note": "未执行删除。确认无误后传 confirm=True。",
            }

        deleted = 0
        effective_cap = batch_size * max_batches
        while deleted < effective_cap:
            subq = (
                select(AuditLog.id)
                .where(AuditLog.created_at < cutoff)
                .order_by(AuditLog.id)
                .limit(batch_size)
                .scalar_subquery()
            )
            res = await self.db.execute(
                delete(AuditLog).where(AuditLog.id.in_(subq))
            )
            n = int(res.rowcount or 0)
            if n == 0:
                break
            deleted += n

        capped = deleted >= effective_cap and total_expired > deleted

        # ⚠️ 先在**主会话内提交删除**，再写墓碑审计。
        # 顺序反了会踩锁：墓碑走独立连接，而此刻主连接仍持有未提交的写事务，
        # SQLite 库级写锁会让墓碑写入失败——"审计的删除"本身没被审计。
        # 本项目已在第 5/7/8 轮三次踩到"独立会话 vs 未提交主事务"的锁竞争。
        await self.db.commit()

        if write_tombstone:
            # 用独立事务写"墓碑"，避免这次的删除动作随外层回滚而消失
            from app.core.audit import AuditAction, log_detached_ctx

            await log_detached_ctx(
                AuditAction.AUDIT_RETENTION_PURGE,
                "audit_log",
                detail={
                    "deleted": deleted,
                    "cutoff": cutoff.isoformat(),
                    "retained_days": retention_days(),
                    "capped": capped,
                },
            )

        logger.warning("审计清理：删除 {} 行（cutoff={}，capped={}）", deleted, cutoff.isoformat(), capped)
        return {
            "dry_run": False,
            "deleted": deleted,
            "remaining_expired": max(total_expired - deleted, 0),
            "cutoff": cutoff.isoformat(),
            "retained_days": retention_days(),
            "capped": capped,
            "note": "已达单次上限，可再次调用继续清理。" if capped else "清理完成。",
        }

    # ---------------- 一键：归档 + 清理 ----------------
    async def archive_then_purge(
        self, *, days: Optional[int] = None, confirm: bool = False
    ) -> dict[str, Any]:
        """推荐的运维入口：**先归档并校验，再清理**。

        校验规则：归档行数必须等于待清理行数；不一致则**拒绝清理**
        （宁可保留，不可丢失）。
        """
        pending = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.created_at < cutoff_at(days))
                )
            ).scalar()
            or 0
        )

        if pending == 0:
            return {"archived": None, "purged": None, "note": "无超出保留期的记录。"}

        arc = await self.archive(days=days)
        if arc["rows"] != pending:
            logger.error(
                "归档行数（{}）与待清理行数（{}）不一致，已中止清理", arc["rows"], pending
            )
            return {
                "archived": arc,
                "purged": None,
                "note": f"归档行数 {arc['rows']} != 待清理 {pending}，已中止清理（请人工核对）",
            }

        prg = await self.purge(days=days, confirm=confirm)
        return {"archived": arc, "purged": prg, "note": "归档校验通过后执行清理。"}


def _to_dict(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "actor_id": row.actor_id,
        "actor_role": row.actor_role,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "detail": row.detail,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "request_id": row.request_id,
        "success": bool(row.success),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


__all__ = [
    "AuditRetentionService",
    "SECURITY_BASELINE_DAYS",
    "retention_days",
    "cutoff_at",
]
