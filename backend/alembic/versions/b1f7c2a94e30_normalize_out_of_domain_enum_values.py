"""normalize out-of-domain enum values (P0 follow-up)

Revision ID: b1f7c2a94e30
Revises: a7d3e91c4b52
Create Date: 2026-09-19 09:30:00.000000

## 为什么需要

`conversations.channel` 上出现了枚举定义之外的取值 `'WEB'`（枚举只认
`WEB_SIM` / `WECOM` / `FEISHU`）。因为该列是 `native_enum=False`（**纯 VARCHAR，
DB 层没有 CHECK 约束**），非法值能直接落库；而读取时 SQLAlchemy 会在
**结果物化阶段**抛：

    LookupError: 'WEB' is not among the defined enum values.

⇒ `GET /api/v1/conversations` 对**该租户永久 500**，且**写入时毫无报错**。
（写入侧的洞已在 `app/schemas/conversation.py` 收紧：`channel` / `msg_type`
的类型由 `str` 改为枚举。）

## 本迁移做什么

把**已经在库里的越界值**规范回模型默认值。判据是「值不在该枚举的允许集合里」，
而不是硬编码某几个已知脏值——**任何**越界值都归一，因此可重复执行（幂等）。

回填值刻意取各列在 `app/models/` 里声明的 `default`：

| 列 | 回填为 | 依据 |
|---|---|---|
| `conversations.channel` | `WEB_SIM` | 模型 default；且这是产品的主渠道 |
| `contract_reviews.source` | `RULE` | 模型 default |
| `contract_reviews.status` | `DEGRADED` | 模型 default，**刻意不是 `SUCCESS`** |
| `contract_reviews.analysis_status` | `PRESCREEN_ONLY` | 模型 default |

⚠️ `contract_reviews.status` **必须**回填 `DEGRADED` 而不是 `SUCCESS`：
`source=llm && status=success` 是**计费门控**的判据（见
`a7d3e91c4b52_contract_review_traceability.py` 的注释）。把「取值域未知的历史行」
默认成 `success`，等于把未经验证的产出伪装成「模型审查成功」并让它可计费。

## downgrade

**不可逆**，且是刻意如此：原值是非法值，恢复它只会把 500 装回去。
降级实现为 no-op 并打印说明，而不是抛错——迁移链回滚时不该因为「这条没得回滚」
而整体卡住。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b1f7c2a94e30"
down_revision: Union[str, None] = "a7d3e91c4b52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (表, 列, 允许集合, 回填值, 理由)
TARGETS: tuple[tuple[str, str, tuple[str, ...], str, str], ...] = (
    (
        "conversations",
        "channel",
        ("WEB_SIM", "WECOM", "FEISHU"),
        "WEB_SIM",
        "模型 default；产品主渠道",
    ),
    (
        "contract_reviews",
        "source",
        ("LLM", "RULE", "MOCK"),
        "RULE",
        "模型 default",
    ),
    (
        "contract_reviews",
        "status",
        ("SUCCESS", "DEGRADED", "FAILED"),
        "DEGRADED",
        "模型 default；**不能**用 SUCCESS（计费门控判据）",
    ),
    (
        "contract_reviews",
        "analysis_status",
        ("COMPLETE_NO_RISK", "PRESCREEN_ONLY", "RISK_FOUND"),
        "PRESCREEN_ONLY",
        "模型 default",
    ),
)


def _normalize(table: str, column: str, allowed: tuple[str, ...], fill: str, why: str) -> None:
    allowed_sql = ", ".join(f"'{v}'" for v in allowed)
    # NULL 一并处理：列是 NOT NULL，但历史上若有写入路径绕过约束，
    # 空值同样会让 ORM 物化失败（`None` 不是合法枚举成员）。
    result = op.get_bind().exec_driver_sql(
        f"UPDATE {table} SET {column} = '{fill}' "  # noqa: S608  表/列名来自本文件常量
        f"WHERE {column} IS NULL OR {column} NOT IN ({allowed_sql})"
    )
    n = getattr(result, "rowcount", -1)
    print(f"  {table}.{column}: 归一 {n} 行 → {fill!r}（{why}）")


def upgrade() -> None:
    print("[b1f7c2a94e30] 归一枚举取值域之外的存量值")
    for table, column, allowed, fill, why in TARGETS:
        _normalize(table, column, allowed, fill, why)


def downgrade() -> None:
    print(
        "[b1f7c2a94e30] downgrade 为 no-op（刻意）：\n"
        "  本迁移把**非法值**规范成合法值，原值正是会触发 500 的脏数据，\n"
        "  恢复它没有任何意义。若确需回退，请先确认写入侧已重新放宽，\n"
        "  否则回退后会立刻重新产生 500。"
    )
