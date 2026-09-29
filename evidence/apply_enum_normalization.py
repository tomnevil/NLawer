"""把 `b1f7c2a94e30` 的归一逻辑，应用到**没有 alembic 版本表**的开发库上。

## 为什么需要这个脚本（不是重复造轮子）

`alembic` 只在 `ENVIRONMENT=production` 时运行（见 `app/database.py:97`）；
开发 / 测试环境的库是 `Base.metadata.create_all` 建的，**没有 `alembic_version` 表**。

实测（2026-09-19）：

    demo_8001.db        → no such table: alembic_version
    storage/nlawer.db   → no such table: alembic_version

后果：`alembic upgrade head` 对开发库**定位不到版本**（会尝试从零建表而失败）。
也就是说 —— **数据回填类迁移在开发环境永远不会执行**。
存量脏值必须**带外**修，否则「迁移写好了」只是纸面完成。

## 单一事实来源

本脚本**直接复用迁移文件里的 `TARGETS`**（`importlib` 按路径加载），
而不是手抄一份 SQL —— 手抄的副本迟早会和迁移漂移，
而漂移的表现恰好是「迁移改了、脚本没改，脏值没修干净」。

用法：
    python evidence/apply_enum_normalization.py --db backend/storage/nlawer.db
    python evidence/apply_enum_normalization.py --db a.db --db b.db --dry-run

退出码：`0` 干净或已修好 / `1` `--dry-run` 下发现越界值 / `2` 环境问题
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).parent
MIGRATION = (
    HERE.parents[0]
    / "backend"
    / "alembic"
    / "versions"
    / "b1f7c2a94e30_normalize_out_of_domain_enum_values.py"
)


def load_targets() -> tuple[tuple[str, str, tuple[str, ...], str, str], ...]:
    """从迁移文件加载 `TARGETS` —— **唯一事实来源**，不复制。"""
    if not MIGRATION.exists():
        raise FileNotFoundError(f"找不到迁移文件：{MIGRATION}")
    spec = importlib.util.spec_from_file_location("_enum_norm_migration", MIGRATION)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载迁移模块：{MIGRATION}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TARGETS


def bad_rows(con: sqlite3.Connection, table: str, column: str, allowed: tuple[str, ...]) -> list:
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    if table not in tables:
        return []
    allowed_sql = ", ".join(f"'{v}'" for v in allowed)
    return con.execute(
        f"select {column}, count(*) from {table} "  # noqa: S608  表/列名来自迁移常量
        f"where {column} IS NULL OR {column} NOT IN ({allowed_sql}) "
        f"group by {column}"
    ).fetchall()


def process(db: pathlib.Path, targets, dry_run: bool) -> tuple[int, bool]:
    """返回 (越界行数, 是否出错)。"""
    con = sqlite3.connect(db)
    total = 0
    try:
        for table, column, allowed, fill, why in targets:
            bad = bad_rows(con, table, column, allowed)
            if not bad:
                continue
            n = sum(c for _, c in bad)
            total += n
            print(f"  {table}.{column}: 越界 {bad} ⇒ 归一为 {fill!r}（{why}）")
            if not dry_run:
                allowed_sql = ", ".join(f"'{v}'" for v in allowed)
                cur = con.execute(
                    f"update {table} set {column} = ? "  # noqa: S608
                    f"where {column} IS NULL OR {column} NOT IN ({allowed_sql})",
                    (fill,),
                )
                print(f"      已更新 {cur.rowcount} 行")
        if not dry_run:
            con.commit()
            # 复验：改完必须真的没有越界值了（否则说明 UPDATE 的 WHERE 写错了）
            left = [
                f"{t}.{c}={bad_rows(con, t, c, a)}"
                for t, c, a, _f, _w in targets
                if bad_rows(con, t, c, a)
            ]
            if left:
                print(f"  [FAIL] 归一后仍有越界值：{left}")
                return total, True
    finally:
        con.close()
    return total, False


def main() -> int:
    ap = argparse.ArgumentParser(description="把 b1f7c2a94e30 的归一应用到开发库")
    ap.add_argument("--db", action="append", default=[], required=True, help="要处理的库（可重复）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不修改")
    args = ap.parse_args()

    try:
        targets = load_targets()
    except Exception as e:  # noqa: BLE001
        print(f"[ENV ] 加载迁移 TARGETS 失败：{type(e).__name__}: {e}")
        return 2

    print(f"── 归一枚举越界值（{len(targets)} 个目标列；来源：{MIGRATION.name}）──")
    if args.dry_run:
        print("   [dry-run] 不会修改任何数据\n")

    err = False
    grand_total = 0
    for raw in args.db:
        db = pathlib.Path(raw)
        if not db.exists():
            print(f"[ENV ] {db} 不存在")
            err = True
            continue
        print(f"\n▶ {db}")
        try:
            n, bad = process(db, targets, args.dry_run)
        except sqlite3.DatabaseError as e:
            print(f"  [ENV ] 打不开：{e}")
            err = True
            continue
        grand_total += n
        err |= bad
        if n == 0:
            print("  [ ok ] 无越界值")

    print("\n" + "=" * 72)
    if err:
        print("=> 退出码 2：存在环境问题或归一未生效")
        return 2
    if args.dry_run and grand_total:
        print(f"=> 退出码 1：dry-run 发现 {grand_total} 行越界值（未修改）")
        return 1
    print(f"=> 退出码 0：{'无越界值' if grand_total == 0 else f'已归一 {grand_total} 行'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
