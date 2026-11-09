"""复核一致性对账：历史跨租户复核行导致的 analysis / report 状态不一致。

背景
----
A5 修复前 `ReviewService.ensure()` 不校验复核目标归属，任意租户可为他租户的
分析/报告建复核；`_on_confirmed()` 也不带 tenant 条件，因此跨租户复核一旦被
decide 完成就会「隔空」改写对方的分析/报告。

A5 修复后两处都加了 tenant 条件，历史跨租户复核即使已完成也不再改写目标，
于是留下两类不一致：

1. 非法跨租户（inconsistency A）
   `review.tenant_id != 目标对象/目标所属案件的 tenant_id`，且复核已完成
   （CONFIRMED / ARCHIVED）。
2. 同租户历史漏落地（inconsistency B）
   复核已完成且租户一致，但目标仍是 DRAFT（旧逻辑未回写或回写失败）。

处理原则（不一律追认）
----------------------
- 非法跨租户：**不生效**。复核置为 `VOIDED`，原因与作废时间写入 `comment`，
  并追加一条 `ReviewRecord(action="VOID")` 留痕；不物理删除，目标保持原状。
- 同租户漏落地：仅当复核链本身合法时才补一致化
    CASE_ANALYSIS   -> status=CONFIRMED, confirmed_by/confirmed_at
    CONSULT_REPORT  -> status=APPROVED, signed_by/signed_at/final_report/review_id
  不推送 IM 消息（历史补写不应触发第二次客户侧副作用）。
- 边界情况（目标缺失、结论非 APPROVED、级别不足、缺结论人/时间、
  案件归属矛盾、同目标重复已完成复核等）只列出不处理，交由人工判断。

安全约束
--------
- **默认 dry-run**：只统计条数、打印样例，不写库。
- 必须显式 `--apply` 才写库，且单事务。
- 目标含 `prod`/`production`：仅允许 dry-run，`--apply` 直接拒绝。
- 目标含 `nlaw`：`--apply` 需再加 `--confirm-nlaw` 二次确认（与
  `backfill_case_tenant.py` 保持一致）。
- `--backup PATH`：把将变更的 review/目标前后快照导出 JSON；`--apply` 未给
  路径时自动落到临时目录。
- 幂等：复核作废以 `status in (CONFIRMED, ARCHIVED)` 为条件，目标补写以
  `status == DRAFT` 为条件；重复执行不会二次变更。

用法
----
    # dry-run（默认），可选顺带导出快照
    python reconcile_review_consistency.py \
        --dsn "sqlite:///./storage/nlawer.db" \
        --backup "$env:TEMP\\nlaw_reconcile.json"

    # 真正执行（开发库需二次确认）
    python reconcile_review_consistency.py \
        --dsn "sqlite:///./storage/nlawer.db" \
        --apply --confirm-nlaw \
        --backup "$env:TEMP\\nlaw_reconcile_apply.json"

    # Postgres（DSN 可写 sqlite/postgresql，脚本自动切异步驱动）
    python reconcile_review_consistency.py \
        --dsn "postgresql://review:review@127.0.0.1:5437/review"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.analysis import CaseAnalysis
from app.models.case import Case
from app.models.consult_report import ConsultReport, ConsultReportStatus
from app.models.enums import ReviewDecision, ReviewLevel, ReviewStatus, ReviewTargetType
from app.models.review import Review, ReviewRecord

COMPLETED = {ReviewStatus.CONFIRMED, ReviewStatus.ARCHIVED}
SUPPORTED = {ReviewTargetType.CASE_ANALYSIS, ReviewTargetType.CONSULT_REPORT}
LEVEL_ORDER = {ReviewLevel.L1: 1, ReviewLevel.L2: 2, ReviewLevel.L3: 3}


# ---------------- 基础工具 ----------------
def _normalize_async_dsn(dsn: str) -> str:
    """把常见 DSN 统一成 SQLAlchemy 异步驱动 URL。"""
    d = dsn.strip()
    low = d.lower()
    if low.startswith("postgresql+asyncpg://"):
        return d
    if low.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + d[len("postgresql+psycopg2://"):]
    if low.startswith("postgresql://"):
        return "postgresql+asyncpg://" + d[len("postgresql://"):]
    if low.startswith("sqlite+aiosqlite:///"):
        return d
    if low.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + d[len("sqlite:///"):]
    if d.endswith((".db", ".sqlite", ".sqlite3")):
        return "sqlite+aiosqlite:///" + d.replace("\\", "/")
    raise SystemExit(f"无法识别的 DSN：{dsn}")


def _assert_target(dsn: str, *, apply: bool, confirm_nlaw: bool) -> None:
    lowered = dsn.lower()
    if ("prod" in lowered or "production" in lowered) and apply:
        raise SystemExit("拒绝执行：目标疑似生产库，只允许 dry-run 报数字，禁止 --apply。")
    if "nlaw" in lowered and apply and not confirm_nlaw:
        raise SystemExit(
            "目标疑似 nlaw 开发库：dry-run 可直接执行；"
            "确需在开发库 apply 时请显式追加 --confirm-nlaw。"
        )


def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_backup_path() -> str:
    return os.path.join(
        os.environ.get("TEMP", "."),
        f"nlaw_reconcile_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _write_backup(path: str, *, target: str, report: dict[str, Any]) -> str:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "database": target,
        "mode": "apply" if report.get("apply") else "dry-run",
        "illegal_cross_tenant": report["illegal_cross_tenant"],
        "same_tenant_stale": report["same_tenant_stale"],
        "boundary": report["boundary"],
    }
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_jsonable)
    return abs_path


# ---------------- 识别 ----------------
def _review_item(r: Review, target_tenant: str) -> dict[str, Any]:
    return {
        "review_id": r.id,
        "review_tenant": r.tenant_id,
        "target_type": r.target_type.value,
        "target_id": r.target_id,
        "target_tenant": target_tenant,
        "review_status": r.status.value,
        "decision": r.decision.value if r.decision else None,
        "required_level": r.required_level.value if r.required_level else None,
        "satisfied_level": r.satisfied_level.value if r.satisfied_level else None,
        "decided_by": r.decided_by,
        "decided_at": r.decided_at,
    }


def _analysis_snapshot(a: CaseAnalysis) -> dict[str, Any]:
    return {
        "id": a.id,
        "case_id": a.case_id,
        "tenant_id": a.tenant_id,
        "status": a.status.value if a.status else None,
        "confirmed_by": a.confirmed_by,
        "confirmed_at": a.confirmed_at,
    }


def _report_snapshot(cr: ConsultReport) -> dict[str, Any]:
    return {
        "id": cr.id,
        "case_id": cr.case_id,
        "tenant_id": cr.tenant_id,
        "status": cr.status.value if cr.status else None,
        "signed_by": cr.signed_by,
        "signed_at": cr.signed_at,
        "review_id": cr.review_id,
        "has_draft_sections": bool(cr.draft_sections),
    }


async def _load_target(db: AsyncSession, r: Review) -> tuple[Any, Optional[int]]:
    """返回 (目标对象, 目标 case_id)；不支持的 target_type 返回 (None, None)。"""
    if r.target_type == ReviewTargetType.CASE_ANALYSIS:
        obj = await db.get(CaseAnalysis, r.target_id)
        return obj, (obj.case_id if obj is not None else None)
    if r.target_type == ReviewTargetType.CONSULT_REPORT:
        obj = await db.get(ConsultReport, r.target_id)
        return obj, (obj.case_id if obj is not None else None)
    return None, None


async def _case_issue(
    db: AsyncSession,
    *,
    target_case_id: Optional[int],
    review_case_id: Optional[int],
    target_tenant: str,
) -> Optional[str]:
    """目标与所属案件的租户/归属校验；返回问题描述（None=通过）。"""
    if review_case_id is not None and target_case_id is not None and review_case_id != target_case_id:
        return f"复核 case_id={review_case_id} 与目标所属案件 #{target_case_id} 不一致"
    if target_case_id is not None:
        case = await db.get(Case, target_case_id)
        if case is None:
            return f"目标所属案件 #{target_case_id} 不存在"
        if case.tenant_id != target_tenant:
            return (
                f"目标对象租户 {target_tenant} 与所属案件租户 {case.tenant_id} 矛盾"
            )
    return None


async def _legality_reason(
    db: AsyncSession,
    r: Review,
    *,
    target_case_id: Optional[int],
    target_tenant: str,
    duplicate_count: int,
) -> Optional[str]:
    """复核链合法性校验；返回问题描述（None=合法，可补一致化）。"""
    if r.decision != ReviewDecision.APPROVED:
        dec = r.decision.value if r.decision else "空"
        return f"复核结论为 {dec}，不是 APPROVED"
    req, sat = r.required_level, r.satisfied_level
    if req is None or sat is None:
        return "复核级别信息缺失（required_level / satisfied_level）"
    if LEVEL_ORDER.get(sat, 0) < LEVEL_ORDER.get(req, 0):
        return f"复核级别不足（要求 {req.value}，已满足 {sat.value}）"
    if r.decided_by is None:
        return "缺少结论人 decided_by"
    if not r.decided_at:
        return "缺少结论时间 decided_at"
    if duplicate_count > 1:
        return f"同一目标存在 {duplicate_count} 条已完成同租户复核，无法判定以哪条为准"
    return await _case_issue(
        db,
        target_case_id=target_case_id,
        review_case_id=r.case_id,
        target_tenant=target_tenant,
    )


async def _classify(db: AsyncSession) -> dict[str, Any]:
    reviews = (
        (
            await db.execute(
                select(Review)
                .where(
                    Review.target_type.in_(list(SUPPORTED)),
                    Review.status.in_(list(COMPLETED)),
                )
                .order_by(Review.id.asc())
            )
        )
        .scalars()
        .all()
    )

    dup_counts = Counter((r.target_type, r.target_id, r.tenant_id) for r in reviews)
    report: dict[str, Any] = {
        "illegal_cross_tenant": [],
        "same_tenant_stale": {"analysis": [], "consult_report": []},
        "boundary": [],
        "consistent": 0,
        "scanned": len(reviews),
    }

    for r in reviews:
        target, target_case_id = await _load_target(db, r)
        if target is None:
            report["boundary"].append(
                {
                    "review_id": r.id,
                    "target_type": r.target_type.value,
                    "target_id": r.target_id,
                    "reason": "目标对象不存在，无法判定租户归属",
                }
            )
            continue

        target_tenant = target.tenant_id

        # ---- 类型 1：非法跨租户，且复核已完成 ----
        if r.tenant_id != target_tenant:
            item = _review_item(r, target_tenant)
            item["target_snapshot"] = (
                _analysis_snapshot(target)
                if isinstance(target, CaseAnalysis)
                else _report_snapshot(target)
            )
            report["illegal_cross_tenant"].append(item)
            continue

        # ---- 类型 2：同租户，复核已完成但目标仍 DRAFT ----
        if isinstance(target, CaseAnalysis):
            if target.status == ReviewStatus.DRAFT:
                reason = await _legality_reason(
                    db,
                    r,
                    target_case_id=target_case_id,
                    target_tenant=target_tenant,
                    duplicate_count=dup_counts[(r.target_type, r.target_id, r.tenant_id)],
                )
                if reason is not None:
                    report["boundary"].append(
                        {
                            "review_id": r.id,
                            "target_type": r.target_type.value,
                            "target_id": r.target_id,
                            "reason": reason,
                        }
                    )
                    continue
                item = _review_item(r, target_tenant)
                item["target_snapshot"] = _analysis_snapshot(target)
                report["same_tenant_stale"]["analysis"].append(item)
            elif target.status == ReviewStatus.CONFIRMED:
                if target.confirmed_by is None or not target.confirmed_at:
                    report["boundary"].append(
                        {
                            "review_id": r.id,
                            "target_type": r.target_type.value,
                            "target_id": r.target_id,
                            "reason": "目标已 CONFIRMED 但缺少 confirmed_by/confirmed_at",
                        }
                    )
                else:
                    report["consistent"] += 1
            else:
                report["boundary"].append(
                    {
                        "review_id": r.id,
                        "target_type": r.target_type.value,
                        "target_id": r.target_id,
                        "reason": f"目标分析状态为 {target.status.value}，既非 DRAFT 也非 CONFIRMED",
                    }
                )
        else:
            if target.status == ConsultReportStatus.DRAFT:
                reason = await _legality_reason(
                    db,
                    r,
                    target_case_id=target_case_id,
                    target_tenant=target_tenant,
                    duplicate_count=dup_counts[(r.target_type, r.target_id, r.tenant_id)],
                )
                if reason is not None:
                    report["boundary"].append(
                        {
                            "review_id": r.id,
                            "target_type": r.target_type.value,
                            "target_id": r.target_id,
                            "reason": reason,
                        }
                    )
                    continue
                item = _review_item(r, target_tenant)
                item["target_snapshot"] = _report_snapshot(target)
                report["same_tenant_stale"]["consult_report"].append(item)
            elif target.status == ConsultReportStatus.APPROVED:
                if target.signed_by is None or not target.signed_at:
                    report["boundary"].append(
                        {
                            "review_id": r.id,
                            "target_type": r.target_type.value,
                            "target_id": r.target_id,
                            "reason": "目标已 APPROVED 但缺少 signed_by/signed_at",
                        }
                    )
                else:
                    report["consistent"] += 1
            else:
                report["boundary"].append(
                    {
                        "review_id": r.id,
                        "target_type": r.target_type.value,
                        "target_id": r.target_id,
                        "reason": f"目标报告状态为 {target.status.value}，既非 DRAFT 也非 APPROVED",
                    }
                )

    return report


# ---------------- 执行 ----------------
async def _apply(db: AsyncSession, report: dict[str, Any]) -> dict[str, int]:
    voided = fixed_analysis = fixed_report = 0

    for item in report["illegal_cross_tenant"]:
        r = await db.get(Review, item["review_id"])
        if r is None or r.status not in COMPLETED:
            continue  # 幂等：已作废 / 状态已变则跳过
        prev = r.status
        voided_at = _now_str()
        reason = (
            f"[RECONCILE {voided_at}] 跨租户复核作废：review 租户 {r.tenant_id}"
            f"，目标 {r.target_type.value}#{r.target_id} 属于租户 "
            f"{item['target_tenant']}，本次复核不生效"
        )
        r.status = ReviewStatus.VOIDED
        r.comment = (r.comment + "\n" if r.comment else "") + reason
        db.add(
            ReviewRecord(
                tenant_id=r.tenant_id,
                review_id=r.id,
                action="VOID",
                actor_id=None,
                actor_role="SYSTEM",
                level=r.satisfied_level,
                from_status=prev.value,
                to_status=ReviewStatus.VOIDED.value,
                changes={
                    "source": "reconcile_review_consistency",
                    "reason": "cross_tenant_review",
                    "voided_at": voided_at,
                    "target_type": r.target_type.value,
                    "target_id": r.target_id,
                    "target_tenant": item["target_tenant"],
                },
                comment=reason,
            )
        )
        voided += 1

    for item in report["same_tenant_stale"]["analysis"]:
        a = await db.get(CaseAnalysis, item["target_id"])
        if a is None or a.status != ReviewStatus.DRAFT:
            continue  # 幂等：已落地则跳过
        a.status = ReviewStatus.CONFIRMED
        a.confirmed_by = item["decided_by"]
        a.confirmed_at = item["decided_at"] or _now_str()
        fixed_analysis += 1

    for item in report["same_tenant_stale"]["consult_report"]:
        cr = await db.get(ConsultReport, item["target_id"])
        if cr is None or cr.status != ConsultReportStatus.DRAFT:
            continue  # 幂等：已落地则跳过
        cr.status = ConsultReportStatus.APPROVED
        cr.signed_by = item["decided_by"]
        cr.signed_at = item["decided_at"] or _now_str()
        cr.final_report = cr.final_report or cr.draft_sections or {}
        cr.review_id = cr.review_id or item["review_id"]
        fixed_report += 1

    await db.flush()
    return {
        "voided": voided,
        "fixed_analysis": fixed_analysis,
        "fixed_report": fixed_report,
    }


# ---------------- 输出 ----------------
def _print_report(report: dict[str, Any]) -> None:
    illegal = report["illegal_cross_tenant"]
    stale_a = report["same_tenant_stale"]["analysis"]
    stale_r = report["same_tenant_stale"]["consult_report"]
    boundary = report["boundary"]
    stale_total = len(stale_a) + len(stale_r)

    print(
        f"[scan] 已完成复核={report['scanned']}  "
        f"非法跨租户={len(illegal)}  "
        f"同租户未落地={stale_total}（analysis={len(stale_a)} / consult_report={len(stale_r)}）  "
        f"已一致={report['consistent']}  "
        f"边界={len(boundary)}"
    )

    for item in illegal[:5]:
        print(
            f"[sample][illegal] review#{item['review_id']} tenant={item['review_tenant']} "
            f"-> {item['target_type']}#{item['target_id']} tenant={item['target_tenant']} "
            f"status={item['review_status']} decision={item['decision']}"
        )
    for item in stale_a[:5]:
        print(
            f"[sample][stale-analysis] review#{item['review_id']} tenant={item['review_tenant']} "
            f"-> {item['target_type']}#{item['target_id']} status={item['target_snapshot']['status']} "
            f"decision={item['decision']} satisfied={item['satisfied_level']}"
        )
    for item in stale_r[:5]:
        print(
            f"[sample][stale-report] review#{item['review_id']} tenant={item['review_tenant']} "
            f"-> {item['target_type']}#{item['target_id']} status={item['target_snapshot']['status']} "
            f"decision={item['decision']} satisfied={item['satisfied_level']}"
        )
    for item in boundary[:5]:
        print(
            f"[sample][boundary] review#{item['review_id']} "
            f"{item['target_type']}#{item['target_id']} reason={item['reason']}"
        )


# ---------------- 入口 ----------------
async def _run(dsn: str, *, apply: bool, backup: Optional[str]) -> None:
    async_dsn = _normalize_async_dsn(dsn)
    engine = create_async_engine(async_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            report = await _classify(db)
        report["apply"] = apply
        _print_report(report)

        if backup or apply:
            path = backup or _default_backup_path()
            saved = _write_backup(path, target=dsn, report=report)
            total = (
                len(report["illegal_cross_tenant"])
                + len(report["same_tenant_stale"]["analysis"])
                + len(report["same_tenant_stale"]["consult_report"])
            )
            print(f"[backup] {saved} 待变更={total}")

        if not apply:
            print("[dry-run] 未写库。确认目标后加 --apply 执行（开发库需 --confirm-nlaw）。")
            return

        async with factory() as db:
            async with db.begin():
                result = await _apply(db, report)
        print(
            f"[apply] 作废跨租户复核={result['voided']}  "
            f"补一致化 analysis={result['fixed_analysis']} "
            f"consult_report={result['fixed_report']}"
        )

        # apply 后复检：应只剩边界情况
        async with factory() as db:
            after = await _classify(db)
        stale_after = len(after["same_tenant_stale"]["analysis"]) + len(
            after["same_tenant_stale"]["consult_report"]
        )
        print(
            f"[verify] 非法跨租户={len(after['illegal_cross_tenant'])}  "
            f"同租户未落地={stale_after}  边界={len(after['boundary'])}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="复核一致性对账（analysis / consult_report）")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("DATABASE_URL", "sqlite:///./storage/nlawer.db"),
        help="数据库 DSN（sqlite:///... 或 postgresql://...）",
    )
    parser.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    parser.add_argument("--confirm-nlaw", action="store_true", help="nlaw 开发库 apply 二次确认")
    parser.add_argument("--backup", default=None, help="变更前快照 JSON 路径")
    args = parser.parse_args()

    _assert_target(args.dsn, apply=args.apply, confirm_nlaw=args.confirm_nlaw)
    asyncio.run(_run(args.dsn, apply=args.apply, backup=args.backup))


if __name__ == "__main__":
    main()
