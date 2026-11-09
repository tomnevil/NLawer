"""回填 `dispatches` / `case_events` 丢失的 `tenant_id`（A1 存量数据修复）。

背景
----
历史缺陷：`Dispatch(...)` 与 `CaseEvent(...)` 构造时未传 `tenant_id`，
落库时吃到 `TenantMixin` 的默认值 `"platform"`，导致律所租户的派单/事件
被记到平台租户下，律所管理员看不到、指派律师接单 404。

本脚本只做**存量数据回填**，不改表结构：
  dispatches.tenant_id  == cases.tenant_id
  case_events.tenant_id == cases.tenant_id
仅当子行 `tenant_id='platform'` 且所属案件 `tenant_id<>'platform'` 时才回填，
不触碰其它租户值，避免误改平台共享数据。

安全约束
--------
- **默认 dry-run**：只统计待回填行数、打印样例，不写库。
- 必须显式 `--apply` 才执行 UPDATE，且全程单事务。
- 目标含 `prod`/`production`：仅允许 dry-run，`--apply` 直接拒绝。
- 目标含 `nlaw`（含 `nlawer` 库名）：dry-run 允许；`--apply` 必须再加
  `--confirm-nlaw` 二次确认，避免误连开发库。
- `--backup PATH`：把待回填行导出成 JSON，默认放在仓库外临时目录。

用法
----
    export DATABASE_URL="postgresql+asyncpg://review:review@127.0.0.1:5437/review"
    python backfill_case_tenant.py            # dry-run，打印待修复行数与样例
    python backfill_case_tenant.py --apply    # 真正执行（临时库）

    # 开发库（显式二次确认 + 先备份）：
    python backfill_case_tenant.py --dsn "..." --backup "$env:TEMP\\nlaw_backfill.json"
    python backfill_case_tenant.py --dsn "..." --apply --confirm-nlaw --backup "$env:TEMP\\nlaw_backfill.json"

    # SQLite（同样默认 dry-run；--apply 前必须先导备份，未给 --backup 会自动落到临时目录）：
    python backfill_case_tenant.py --dsn "sqlite:///./storage/nlawer.db" --backup "$env:TEMP\\nlaw_backfill_sqlite.json"
    python backfill_case_tenant.py --dsn "sqlite:///./storage/nlawer.db" --apply --confirm-nlaw
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

import asyncpg


def _normalize_dsn(url: str) -> str:
    """SQLAlchemy 风格 DSN -> asyncpg 可用的 DSN。"""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _sqlite_path(dsn: str) -> str | None:
    """识别 SQLite 目标并返回文件路径；非 SQLite 返回 None。

    支持三种写法：
      sqlite+aiosqlite:///./storage/nlawer.db
      sqlite:///./storage/nlawer.db
      ./storage/nlawer.db            （纯路径，需以 .db/.sqlite/.sqlite3 结尾）
    """
    lowered = dsn.lower()
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if lowered.startswith(prefix):
            return dsn[len(prefix):]
    if lowered.startswith("sqlite://"):
        return dsn[len("sqlite://"):]
    if lowered.endswith((".db", ".sqlite", ".sqlite3")):
        return dsn
    return None


def _assert_target(dsn: str, *, apply: bool, confirm_nlaw: bool) -> None:
    lowered = dsn.lower()
    if "prod" in lowered or "production" in lowered:
        if apply:
            raise SystemExit("拒绝执行：目标疑似生产库，只允许 dry-run 报数字，禁止 --apply。")
    if "nlaw" in lowered and apply and not confirm_nlaw:
        raise SystemExit(
            "目标疑似 nlaw 开发库：dry-run 可直接执行；"
            "确需在开发库 apply 时请显式追加 --confirm-nlaw。"
        )


_DISP_SQL = """
    SELECT d.id, d.case_id, d.tenant_id AS old_tenant, c.tenant_id AS case_tenant,
           d.mode, d.status, d.lawyer_id
    FROM dispatches d JOIN cases c ON c.id = d.case_id
    WHERE d.tenant_id = 'platform' AND c.tenant_id <> 'platform'
    ORDER BY d.id
"""

_EVENT_SQL = """
    SELECT e.id, e.case_id, e.tenant_id AS old_tenant, c.tenant_id AS case_tenant,
           e.event_type, e.title
    FROM case_events e JOIN cases c ON c.id = e.case_id
    WHERE e.tenant_id = 'platform' AND c.tenant_id <> 'platform'
    ORDER BY e.id
"""


def _write_backup(path: str, *, target: str, disp_rows: list[dict], event_rows: list[dict]) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": target,
        "dispatches": disp_rows,
        "case_events": event_rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return os.path.abspath(path)


def run_sqlite(path: str, apply: bool, backup: str | None) -> None:
    """SQLite 分支：dry-run 默认；apply 单事务（BEGIN IMMEDIATE）。

    SQLite 版本兼容性说明：不用 `UPDATE ... FROM`（3.33+ 才有），
    改用相关子查询，Python 3.12 自带的 sqlite3 均可执行。
    """
    real_path = os.path.abspath(path)
    if not os.path.exists(real_path):
        raise SystemExit(f"SQLite 目标不存在：{real_path}")
    print(f"[target] sqlite={real_path}")

    conn = sqlite3.connect(real_path)
    conn.isolation_level = None  # 手动控制事务
    conn.row_factory = sqlite3.Row
    try:
        disp_rows = [dict(r) for r in conn.execute(_DISP_SQL)]
        event_rows = [dict(r) for r in conn.execute(_EVENT_SQL)]
        print(f"[dry-run] dispatches 待回填={len(disp_rows)}  case_events 待回填={len(event_rows)}")
        for tag, rows in (("dispatch", disp_rows), ("event", event_rows)):
            for r in rows[:5]:
                print(f"[sample] {tag} {r}")

        # apply 前必须已导备份：未显式给 --backup 时自动落到临时目录
        if apply and not backup:
            backup = os.path.join(
                os.environ.get("TEMP", "."),
                f"nlaw_backfill_sqlite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        if backup:
            saved = _write_backup(
                backup, target=real_path, disp_rows=disp_rows, event_rows=event_rows
            )
            print(f"[backup] {saved} rows={len(disp_rows)}+{len(event_rows)}")

        if not apply:
            print("[dry-run] 未写库。确认目标后加 --apply 执行。")
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            cur1 = conn.execute(
                """
                UPDATE dispatches
                SET tenant_id = (
                    SELECT c.tenant_id FROM cases c WHERE c.id = dispatches.case_id
                )
                WHERE tenant_id = 'platform'
                  AND case_id IN (SELECT id FROM cases WHERE tenant_id <> 'platform')
                """
            )
            cur2 = conn.execute(
                """
                UPDATE case_events
                SET tenant_id = (
                    SELECT c.tenant_id FROM cases c WHERE c.id = case_events.case_id
                )
                WHERE tenant_id = 'platform'
                  AND case_id IN (SELECT id FROM cases WHERE tenant_id <> 'platform')
                """
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"[apply] dispatches={cur1.rowcount} case_events={cur2.rowcount}")
    finally:
        conn.close()


async def run(dsn: str, apply: bool, backup: str | None) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            db = await conn.fetchval("SELECT current_database()")
            addr = await conn.fetchval("SELECT inet_server_addr()")
            print(f"[target] db={db} addr={addr}")
            disp_rows = await conn.fetch(_DISP_SQL)
            event_rows = await conn.fetch(_EVENT_SQL)
            print(f"[dry-run] dispatches 待回填={len(disp_rows)}  case_events 待回填={len(event_rows)}")
            for tag, rows in (("dispatch", disp_rows), ("event", event_rows)):
                for r in rows[:5]:
                    print(f"[sample] {tag} {dict(r)}")

            if backup:
                payload = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "database": db,
                    "dispatches": [dict(r) for r in disp_rows],
                    "case_events": [dict(r) for r in event_rows],
                }
                os.makedirs(os.path.dirname(os.path.abspath(backup)), exist_ok=True)
                with open(backup, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
                print(f"[backup] {os.path.abspath(backup)} rows={len(disp_rows)}+{len(event_rows)}")

            if not apply:
                print("[dry-run] 未写库。确认目标后加 --apply 执行。")
                return

            r1 = await conn.execute(
                """
                UPDATE dispatches d
                SET tenant_id = c.tenant_id
                FROM cases c
                WHERE c.id = d.case_id
                  AND d.tenant_id = 'platform'
                  AND c.tenant_id <> 'platform'
                """
            )
            r2 = await conn.execute(
                """
                UPDATE case_events e
                SET tenant_id = c.tenant_id
                FROM cases c
                WHERE c.id = e.case_id
                  AND e.tenant_id = 'platform'
                  AND c.tenant_id <> 'platform'
                """
            )
            print(f"[apply] {r1} | {r2}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 dispatches/case_events 的 tenant_id")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""),
                        help="数据库 DSN；默认取 DATABASE_URL")
    parser.add_argument("--apply", action="store_true", help="真正执行回填（默认 dry-run）")
    parser.add_argument("--confirm-nlaw", action="store_true",
                        help="在疑似 nlaw 开发库上执行 --apply 的二次确认开关")
    parser.add_argument("--backup", default=None, help="把待回填行导出为 JSON 的路径")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("缺少 DSN：请设置 DATABASE_URL 或传 --dsn")

    _assert_target(args.dsn, apply=args.apply, confirm_nlaw=args.confirm_nlaw)
    sqlite_path = _sqlite_path(args.dsn)
    if sqlite_path is not None:
        run_sqlite(sqlite_path, args.apply, args.backup)
        return
    dsn = _normalize_dsn(args.dsn)
    asyncio.run(run(dsn, args.apply, args.backup))


if __name__ == "__main__":
    main()
