"""验证迁移链的两件事：**DDL 默认值用枚举 `name`**、**存量越界值会被归一**。

## 它防的是什么

### 坑 1：`server_default` 用了枚举「值」而不是「名」

`a7d3e91c4b52` 初版写的是 `sa.Enum("llm","rule","mock")` + `server_default="rule"`，
而 **SQLAlchemy 存取的是枚举 `name`**（大写）。实测：全新库迁到该版本后，
**裸 INSERT**（省略这三列 ⇒ 逼 DB 默认值生效）落库为 `('rule','degraded','prescreen_only')`，
随后 ORM 读回抛：

    LookupError: 'rule' is not among the defined enum values.
    Enum name: contractreviewsource. Possible values: LLM, RULE, MOCK

⇒ **任何依赖 DB 默认值的写入都读不回来**（写入时无声、读取时爆发）。

> 为什么必须「裸 INSERT」：走 ORM 写入时 Python 侧的 `default=` 会先兜底，
> **永远看不到 DB 默认值**。只有绕过 ORM 才测得到 DDL 里那个 `DEFAULT 'rule'`。
> 这也是「源码里写了 ≠ 运行时生效」的一个变体：**DDL 里写了 ≠ ORM 能读**。

### 坑 2：`b1f7c2a94e30` 必须把越界值归一

写一条 `channel='WEB'`（不在 `{WEB_SIM,WECOM,FEISHU}` 里）与三条 `''`，
迁到 head 之后必须被归一到各列模型 `default`，且 ORM 能读回。

## 用法

自带全流程（自己建库、自己跑 alembic），不需要手工准备：

    python evidence/verify_migration_enum_defaults.py

退出码（与项目其它证据工具同口径）：`0` 两件事都成立 / `1` 断言失败（迁移有缺陷） /
`2` 环境问题（alembic 不可用、库删不掉等）
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_BACKEND = _HERE.parents[0] / "backend"
DB = _BACKEND / "_mig_verify.db"

# ⚠️ 必须在 import 任何 `app.*` 之前设置：`app/database.py` 在 **import 期**就绑定 engine。
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB.as_posix()}"
os.environ["VECTOR_BACKEND"] = "none"

sys.path.insert(0, str(_BACKEND))


def upgrade(rev: str) -> bool:
    """把库迁到指定 revision（`alembic/env.py` 从 settings 取 URL，所以走环境变量）。"""
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(_BACKEND / "alembic.ini"))
        # ⚠️ `alembic.ini` 里 `script_location = alembic` 是**相对 cwd** 解析的，
        # 从仓库根跑就会找不到。显式改成绝对路径，脚本才能从任何目录运行。
        cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
        command.upgrade(cfg, rev)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [ENV ] alembic upgrade {rev} 失败：{type(e).__name__}: {e}")
        return False


def dump(label: str) -> None:
    con = sqlite3.connect(DB)
    print(f"  {label} contract_reviews:", con.execute(
        "select id, source, status, analysis_status from contract_reviews").fetchall())
    print(f"  {label} conversations   :", con.execute(
        "select id, channel from conversations").fetchall())
    con.close()


def orm_read() -> tuple[bool, list[str]]:
    """原始 sqlite 层看不出「ORM 能不能读」，必须真的用 SQLAlchemy 物化一次。"""
    import app.models  # noqa: F401  全量注册，避免 relationship 解析失败
    from app.models.conversation import Conversation
    from app.models.document import ContractReview
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    notes: list[str] = []
    ok = True
    with Session(create_engine(f"sqlite:///{DB}")) as s:
        for model, label in ((ContractReview, "ContractReview"), (Conversation, "Conversation")):
            try:
                rows = s.execute(select(model)).scalars().all()
                notes.append(f"✅ select({label}) → {len(rows)} 行")
            except Exception as e:  # noqa: BLE001
                ok = False
                notes.append(f"❌ select({label}) → {type(e).__name__}: {e}")
    return ok, notes


def main() -> int:
    if DB.exists():
        try:
            DB.unlink()
        except OSError as e:
            print(f"[ENV ] 删不掉旧库 {DB}：{e}（换个名字或手动删除后重跑）")
            return 2

    print("── 验证迁移链：DDL 默认值用 name + 越界值归一 ──\n")

    # ① 迁到含 server_default 的那一版
    print("① alembic upgrade a7d3e91c4b52")
    if not upgrade("a7d3e91c4b52"):
        return 2

    # ② 裸 INSERT（逼 DB 默认值生效）+ 写越界值
    con = sqlite3.connect(DB)
    con.execute(
        "insert into contract_reviews (id, title, overall_risk, tenant_id) "
        "values (1, 'bare-insert', 'LOW', 'ent_bare')"  # 省略三列 ⇒ 走 DDL 默认值
    )
    con.execute(
        "insert into contract_reviews (id, title, overall_risk, tenant_id, source, status, analysis_status) "
        "values (2, 'dirty', 'LOW', 'ent_acme', '', '', '')"
    )
    con.execute(
        "insert into conversations (id, tenant_id, external_user_id, channel, status, context) "
        "values (7, 'firm_hlw', 'demo-ext-1', 'WEB', 'BOT', '{}')"
    )
    con.commit()
    con.close()
    dump("[归一前]")

    # ③ 断言「坑 1 已修」：裸 INSERT 落的是 name，不是 value
    con = sqlite3.connect(DB)
    bare = con.execute(
        "select source, status, analysis_status from contract_reviews where id=1").fetchone()
    con.close()
    want = ("RULE", "DEGRADED", "PRESCREEN_ONLY")
    if bare != want:
        print(f"\n❌ 坑 1 未修：裸 INSERT 落库 {bare}，期望 {want}")
        print("   ⇒ `server_default` 用了枚举「值」。SQLAlchemy 存取的是 **name**（大写）。")
        return 1
    print(f"  ✅ 裸 INSERT 落库 {bare}（= 枚举 name，非 value）")

    # ④ 迁到 head ⇒ 触发 b1f7c2a94e30 的归一
    print("\n② alembic upgrade head（触发 b1f7c2a94e30 归一）")
    if not upgrade("head"):
        return 2
    dump("[归一后]")

    # ⑤ 断言「坑 2 已修」：越界值被归一 + ORM 能读回
    con = sqlite3.connect(DB)
    rows = con.execute("select source, status, analysis_status from contract_reviews order by id").fetchall()
    conv = con.execute("select channel from conversations where id=7").fetchone()
    con.close()
    bad = [r for r in rows if r != want] or ([] if conv == ("WEB_SIM",) else [conv])
    if bad:
        print(f"\n❌ 坑 2 未修：归一后仍存在越界值 {bad}")
        return 1
    print(f"  ✅ contract_reviews 全部归一到 {want}；conversations.channel → 'WEB_SIM'")

    ok, notes = orm_read()
    for n in notes:
        print(f"  {n}")

    print("\n" + "=" * 72)
    if ok:
        print("=> 退出码 0：DDL 默认值用 name、越界值被归一、ORM 可读回 —— 三项都成立。")
        return 0
    print("=> 退出码 1：ORM 读回失败，迁移链仍有缺陷。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
