"""contract review traceability + billing honesty (P0-16)

Revision ID: a7d3e91c4b52
Revises: c4a91f7e2b83
Create Date: 2026-09-16 12:10:00.000000

为 `contract_reviews` 补 13 列，使「这次审查用了哪个模型 / 哪些 token /
为何降级 / 是否可计费」成为**可查询的事实**。

在此之前该表只有 title/source_text/findings/overall_risk/summary，
接口却是「先 review 再无条件 consume_atomic」——即只要返回 200 就扣 99 元，
而 review() 是纯本地关键词匹配、**永不失败**，所以 99 元 100% 收在规则产出上。
新列 `source` / `status` 就是计费门控的判据。

三列枚举列带 `server_default`：表内已有历史行（生产库 1 行 '采购合同'），
NOT NULL 列必须能回填。默认值刻意取**最保守**的组合 `rule` / `degraded` /
`prescreen_only`——历史行本就由规则引擎产出、且从未经过模型验证，回填成
`rule`+`degraded` 才不会把老数据伪装成「模型审查成功」（`success` 正是
计费门控的判据之一）。`cost_cents` 刻意**不给** server_default：NULL 表示
「当时未计量」，与「成本为 0」是两件事，前者才是历史行的真实状态。

---

## ⚠️ 2026-09-19 更正：三个 `server_default` 原本用的是**枚举值**，必须用**枚举名**

原文件把 `sa.Enum` 的成员串与 `server_default` 都写成了小写**值**
（`"llm"/"rule"/"mock"`、`server_default="rule"`），而模型侧是
`Enum(ContractReviewSource)`——SQLAlchemy 对 `str` 枚举**存取的是 `.name`**。
于是 DDL 里落下 `DEFAULT 'rule'`，而 ORM 只会写/读 `RULE`。

**后果（已实测）**：任何「不提供这三列」的 INSERT（裸 SQL / `COPY` /
将来的批量导入）会写进 `'rule'`，之后 `select(ContractReview)` 在**结果物化阶段**抛
`LookupError: 'rule' is not among the defined enum values` ⇒ **该表任何读取都 500**。

复现步骤：全新库 `alembic upgrade head` → 裸 `INSERT` 三列缺省 → ORM 读回即抛。
已把成员串与 `server_default` 一并对齐到**枚举名**。
（`native_enum=False` 默认不建 CHECK，所以成员串本身是惰性的；致命的是 `server_default`。）
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a7d3e91c4b52"
down_revision: Union[str, None] = "c4a91f7e2b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 来源与审查深度（计费门控判据）----
    op.add_column(
        "contract_reviews",
        sa.Column(
            "source",
            # ⚠️ 枚举串与 server_default 都必须用**枚举名**（大写），不是枚举值。
            #
            # 原写法是 `sa.Enum("llm","rule","mock")` + `server_default="rule"`（小写值），
            # 而模型侧是 `Enum(ContractReviewSource)`——SQLAlchemy 对 str 枚举
            # **存取的是 `.name`**（`RULE`）。于是 DDL 里落下 `DEFAULT 'rule'`：
            # 任何「不提供这三列」的 INSERT（裸 SQL / COPY / 将来的批量导入）
            # 都会写进 `'rule'`，而 ORM 读回时抛
            # `LookupError: 'rule' is not among the defined enum values`
            # ⇒ **该表任何读取都 500**。
            # 实测复现：全新库跑完本迁移 → 裸 INSERT → `select(ContractReview)` 直接抛。
            # （`native_enum=False` 默认不建 CHECK，所以列表本身是惰性的；
            #  真正致命的是 server_default。两者一并对齐到「名」，避免以后再踩。）
            sa.Enum("LLM", "RULE", "MOCK", name="contractreviewsource", native_enum=False, length=16),
            nullable=False,
            server_default="RULE",
        ),
    )
    op.add_column(
        "contract_reviews",
        sa.Column(
            "status",
            sa.Enum(
                "SUCCESS", "DEGRADED", "FAILED",
                name="contractreviewstatus", native_enum=False, length=16,
            ),
            nullable=False,
            # 历史行由规则引擎产出 ⇒ 回填 degraded 而不是 success。
            # success 是计费门控的判据之一，把「未经验证的旧产出」默认成
            # success 会让老数据看起来像「模型审查成功」。
            server_default="DEGRADED",
        ),
    )
    op.add_column(
        "contract_reviews",
        sa.Column(
            "analysis_status",
            sa.Enum(
                "COMPLETE_NO_RISK", "PRESCREEN_ONLY", "RISK_FOUND",
                name="contractanalysisstatus", native_enum=False, length=32,
            ),
            nullable=False,
            server_default="PRESCREEN_ONLY",
        ),
    )
    op.add_column("contract_reviews", sa.Column("coverage", sa.JSON(), nullable=True))
    op.add_column("contract_reviews", sa.Column("disclaimer", sa.String(length=500), nullable=True))
    # 面向用户的失败原因（前端失败页原样展示）。刻意与 `ai_runs.error_message`
    # 分开：后者是运维视角的原始异常，含环境变量名与 base_url，不该出现在用户界面。
    op.add_column("contract_reviews", sa.Column("error_message", sa.Text(), nullable=True))

    # ---- 留痕（对齐 ai_runs 字段）----
    op.add_column("contract_reviews", sa.Column("run_id", sa.Integer(), nullable=True))
    op.add_column("contract_reviews", sa.Column("model_name", sa.String(length=100), nullable=True))
    op.add_column("contract_reviews", sa.Column("model_tier", sa.String(length=32), nullable=True))
    op.add_column(
        "contract_reviews",
        sa.Column("is_mock", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "contract_reviews",
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "contract_reviews",
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("contract_reviews", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("contract_reviews", sa.Column("cost_cents", sa.Float(), nullable=True))

    # 运维排查入口：「这次审查对应哪条 AiRun」。无索引时按 run_id 反查要全表扫。
    op.create_index(
        "ix_contract_reviews_run_id", "contract_reviews", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_contract_reviews_run_id", table_name="contract_reviews")
    op.drop_column("contract_reviews", "cost_cents")
    op.drop_column("contract_reviews", "duration_ms")
    op.drop_column("contract_reviews", "completion_tokens")
    op.drop_column("contract_reviews", "prompt_tokens")
    op.drop_column("contract_reviews", "is_mock")
    op.drop_column("contract_reviews", "model_tier")
    op.drop_column("contract_reviews", "model_name")
    op.drop_column("contract_reviews", "run_id")
    op.drop_column("contract_reviews", "disclaimer")
    op.drop_column("contract_reviews", "error_message")
    op.drop_column("contract_reviews", "coverage")
    op.drop_column("contract_reviews", "analysis_status")
    op.drop_column("contract_reviews", "status")
    op.drop_column("contract_reviews", "source")
