"""枚举输入校验回归测试 —— 「写入不校验 / 读取严格」这一类缺陷的固化。

## 背景（真实缺陷，2026-09-19 实测）

`conversations.channel` 里落了一条 `'WEB'`，而 `IMChannel` 只认
`WEB_SIM` / `WECOM` / `FEISHU`。后果链是：

    写入侧 schema 是 `str`              ⇒ 非法值顺利落库
      （列是 `native_enum=False` = 纯 VARCHAR，**DB 层没有 CHECK 约束**）
    读取侧 `select(Conversation)` 在**结果物化阶段**抛
      `LookupError: 'WEB' is not among the defined enum values`
    ⇒ `GET /api/v1/conversations` 对**该租户永久 500**，且写入时毫无报错

实测证据：`evidence/verify_runtime_health.py --app im` 报 2 条
`500 Fetch .../api/v1/conversations?page=1&page_size=50`；
`evidence/verify_enum_domain_drift.py` 把根因定位到 `firm_hlw` 的那一行。

## 本文件的四组断言

| 组 | 断言什么 | 防的是 |
|---|---|---|
| **A** | 非法值被拒、合法值放行并规范成枚举成员 | 已修的这一个洞回归 |
| **B** | **请求体**里凡映射到枚举列的字段，注解都不能是 `str` | 修好 `channel` 之后下一个字段重演 |
| **C** | 越界值一旦落库，读路径**必然抛异常** | 有人觉得「A 组太严，放宽吧」 |
| **D** | WS 路径不得把**裸 dict** 的值直通 ORM（源码级守卫） | 没有 schema 的那条入口被改回去 |

> **D 组的存在理由**：B 组的判据是「**请求体 schema** 的类型」，
> 而 `app/api/v1/ws.py` 那条路径**根本没有 schema**（`payload = json.loads(raw)`）
> ⇒ **B 组扫不到它**。实测过：收紧 REST 侧之后，WS 侧仍能写入越界 `msg_type`，
> 让会话详情永久 500（行为级复现见 `evidence/verify_ws_msg_type_bypass.py`）。
> **工具报「干净」时，先问它的判据覆盖了哪些路径。**

C 组是**故障注入**：它绕过 schema 直接用原生 SQL 写越界值（= 修复前的真实写入路径），
把「后果有多严重」变成可执行的事实，而不是注释里的一句话。

## B 组为什么要带「非空前提锁」

若扫描器因为目录改名 / 注解口径不匹配而**什么也没扫到**，
「没发现 `str` 字段」就是**假通过**。所以先断言扫到的 schema、枚举列、
请求体数量都 > 0；再喂一个人造 `str` 字段证明扫描器**真的会报**。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# 写入 schema 里「等于没校验」的注解写法（与 evidence/verify_enum_domain_drift.py 同口径）
LOOSE_ANNOTATIONS = {"str", "Optional[str]", "str | None", "None | str"}

# ── B 组的豁免清单：已实测确认是「味道」而非「洞」的请求字段 ────────────────
#
# 这四个字段的 schema 也是 `str`，但**消费点显式转换并拒绝非法值** ⇒ 脏值进不了 ORM。
# 与 `channel` / `msg_type` 的失效形状**不同**：那两个是「schema 是唯一关口」。
#
# ⚠️ 每条的 `文件:行` 都是**实测**位置（2026-09-19），改动代码时须同步核对。
# ⚠️ 后端已冻结 ⇒ 不为了「统一风格」去动这四处；它们的正确性不依赖 schema。
#
# 本清单的作用是：**新增**一个未列出的 `str` 请求字段时，测试立刻失败。
KNOWN_GUARDED_STR_FIELDS: dict[str, str] = {
    "DispatchRequest.mode": "cases.py:159 `DispatchMode(payload.mode)` → BadRequestError(400)",
    "ReviewAction.decision": "reviews.py:188 `ReviewDecision(...)` → BadRequestError(400)",
    # ⚠️ 2026-09-21 §3.32 修复后，该分支由 NotFoundError(404) 改为
    #    BadRequestError(400) —— 注释里的状态码已同步，别再照旧的 404 排查。
    "UsageConsume.usage_type": "billing.py `UsageType(payload.usage_type)` → BadRequestError(400)",
    "ReadBatchIn.type": "notifications.py:222 `_parse_type()` → NotificationType(raw) → BadRequestError(400)",
}


# ═══════════════════ A 组：写入关口必须拦得住 ═══════════════════


def test_invalid_channel_rejected():
    """枚举定义之外的值必须 422（Pydantic `ValidationError` ⇒ FastAPI 422）。

    修复前 `channel: str` 时这四种写法**全部被接受**并原样落库。
    """
    from pydantic import ValidationError

    from app.schemas.conversation import ConversationCreate

    for bad in ("WEB", "WEB_SIM", "", "bogus"):
        with pytest.raises(ValidationError):
            ConversationCreate(external_user_id="ext-1", channel=bad)


def test_valid_channel_accepted_and_normalized():
    """合法值必须放行，且**规范成枚举成员**。

    口径取枚举 **value**（`"web_sim"`）而不是 name —— 与接口输出一致
    （Pydantic 把 str 枚举序列化成 value）。前端不传 `channel`，所以收紧
    不会影响任何调用方；这里锁住的是「将来有人按输出口径回传」时的行为。
    """
    from app.models.enums import IMChannel
    from app.schemas.conversation import ConversationCreate

    assert ConversationCreate(external_user_id="ext-1", channel="web_sim").channel is IMChannel.WEB_SIM
    # 不传时的默认值也必须是枚举成员（不是裸字符串）
    assert ConversationCreate(external_user_id="ext-1").channel is IMChannel.WEB_SIM


def test_invalid_msg_type_rejected():
    """`SendMessageRequest.msg_type` 与 `channel` 同形状：直接进 ORM，schema 是唯一关口。"""
    from pydantic import ValidationError

    from app.schemas.conversation import SendMessageRequest

    for bad in ("TEXT", "bogus", ""):
        with pytest.raises(ValidationError):
            SendMessageRequest(text="hi", msg_type=bad)


def test_valid_msg_type_accepted():
    """前端 `apps/im/app/(app)/page.tsx` 发的是 `"text"`（value）⇒ 必须接受。"""
    from app.models.enums import MessageType
    from app.schemas.conversation import SendMessageRequest

    assert SendMessageRequest(text="hi", msg_type="text").msg_type is MessageType.TEXT
    assert SendMessageRequest(text="hi").msg_type is MessageType.TEXT


# ═══════════════════ B 组：关口本身必须还在（类级） ═══════════════════


def _enum_columns() -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(表名, 列名, 枚举名, 允许集合)。**内省 `Base.metadata`**，不解析源码。

    用内省而不是正则解析模型文件：正则会在 `Enum(...)` 换行、别名导入等写法上漂移，
    而漂移的方向恰好是**漏报**（= 漏掉最该报的那一列）。
    """
    from sqlalchemy import Enum as SAEnum

    import app.models  # noqa: F401  触发模型注册
    from app.database import Base

    out: list[tuple[str, str, str, tuple[str, ...]]] = []
    # 用 `metadata.tables` 而不是 `sorted_tables`：后者要做依赖排序，
    # 而本仓库 `cases` / `conversations` 互相引用（外键成环）会触发 SAWarning，
    # 而列清单**与顺序无关**。
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, SAEnum):
                out.append(
                    (
                        table.name,
                        col.name,
                        col.type.enum_class.__name__ if col.type.enum_class else "?",
                        # ⚠️ `type.enums` 是**枚举名**：SQLAlchemy 存取的是 name 不是 value
                        tuple(col.type.enums),
                    )
                )
    return out


def _schema_annotations() -> dict[str, dict[str, str]]:
    """AST 解析 `app/schemas/*.py` ⇒ {类名: {字段名: 注解文本}}。

    用 AST 而不是正则：注解可能是 `Optional[str]` / `str | None`，
    正则很容易漏一种写法 —— 而**漏掉的正是最该报的那一种**。
    """
    out: dict[str, dict[str, str]] = {}
    for path in sorted((BACKEND / "app" / "schemas").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            fields = {
                s.target.id: ast.unparse(s.annotation)
                for s in node.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            }
            if fields:
                out[node.name] = fields
    return out


def _input_schemas() -> set[str]:
    """**真正当输入用的** schema 类名 = 出现在 API 端点参数注解里的类。

    这一步是精度关键：按类名猜（`*Create` / `*Update`）会**漏报**
    `SendMessageRequest`；只按字段名匹配又会把 `*Out` 读 DTO 大量误报
    （那里 `str` 是**正确**的 —— Pydantic 把 str 枚举序列化成 value）。
    ⇒ 判据只能是「它是不是请求体」。
    """
    names: set[str] = set()
    for path in (BACKEND / "app" / "api").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for arg in [*fn.args.args, *fn.args.kwonlyargs, fn.args.vararg, fn.args.kwarg]:
                if arg is None or arg.annotation is None:
                    continue
                for node in ast.walk(arg.annotation):
                    if isinstance(node, ast.Name):
                        names.add(node.id)
                    elif isinstance(node, ast.Attribute):
                        names.add(node.attr)
    return names


def _loose(cols, schemas, inputs) -> list[str]:
    """找出「本该是枚举、却写成了 `str`」的**请求体**字段。"""
    by_name: dict[str, list[tuple[str, str]]] = {}
    for table, column, enum_name, _allowed in cols:
        by_name.setdefault(column, []).append((table, enum_name))

    hits: list[str] = []
    for cls in sorted(inputs):
        for field, ann in (schemas.get(cls) or {}).items():
            if field not in by_name or ann not in LOOSE_ANNOTATIONS:
                continue
            for table, enum_name in by_name[field]:
                hits.append(f"{cls}.{field}: {ann} → 应为 {enum_name}（列 {table}.{field}）")
    return hits


def test_scanner_has_non_empty_precondition():
    """**非空前提锁**：先证明扫描器真的扫到了东西。

    没有这一条，「没发现 `str` 字段」可能只是因为它什么都没扫到 —— **假通过**。

    还要证明**覆盖到了本次修复的那两个字段**：如果 `_input_schemas()` 漏了
    `ConversationCreate`，即使有人把它改回 `str`，类级防线也不会响 ——
    那是「防线还在但守不住这一段城墙」。
    """
    assert len(_schema_annotations()) > 0, "没解析到任何 schema 类（目录改名了？）"
    assert len(_enum_columns()) > 0, "没解析到任何枚举列（模型注册失败？）"

    inputs = _input_schemas()
    assert len(inputs) > 0, "没解析到任何请求体 schema（端点签名变了？）"
    for must_have in ("ConversationCreate", "SendMessageRequest"):
        assert must_have in inputs, f"{must_have} 未被识别为请求体 ⇒ 类级防线覆盖不到它"


def test_no_request_schema_uses_str_for_enum_column():
    """**类级防线**：请求体里凡映射到枚举列的字段，要么是枚举类型，要么在豁免清单里。

    豁免清单里的四条都**实测确认过**消费点会拒绝非法值（见常量注释），
    所以它们不会把脏值写进 ORM —— 报出来只会制造噪声，掩盖真正的新洞。
    """
    loose = _loose(_enum_columns(), _schema_annotations(), _input_schemas())
    unexpected = [h for h in loose if h.split(":")[0] not in KNOWN_GUARDED_STR_FIELDS]
    assert not unexpected, (
        "请求体把枚举列标成了 str，且不在豁免清单里"
        "（写入侧不校验 ⇒ 脏值会继续落库，读路径迟早 500）：\n  " + "\n  ".join(unexpected)
    )


def test_known_guarded_allowlist_is_not_stale():
    """**豁免清单不能腐烂**：每条豁免都必须在扫描结果里**仍然出现**。

    否则代码改动（例如某端点改成枚举类型）之后，那条豁免会一直躺着，
    变成永久的盲区 —— 将来同一个字段重新变回 `str` 也不会被发现。
    """
    loose = _loose(_enum_columns(), _schema_annotations(), _input_schemas())
    seen = {h.split(":")[0] for h in loose}
    stale = sorted(set(KNOWN_GUARDED_STR_FIELDS) - seen)
    assert not stale, (
        "豁免清单里的字段已不在扫描结果中（代码改了？请核对后删除对应豁免）：\n  "
        + "\n  ".join(f"{k}（原判据：{KNOWN_GUARDED_STR_FIELDS[k]}）" for k in stale)
    )


def test_scanner_would_catch_a_str_field():
    """**故障注入对照**：证明上一条不是「永远通过」。

    若扫描器写坏了（`inputs` 恒空、注解口径不匹配……），上一条仍会绿。
    这里喂一个人造 `str` 字段，断言**必须**被报出来；同时断言读 DTO
    **不被误报**（它用 `str` 是对的）。
    """
    cols = [("conversations", "channel", "IMChannel", ("WEB_SIM",))]
    schemas = {"ConvCreate": {"channel": "str"}, "ConvOut": {"channel": "str"}}
    hits = _loose(cols, schemas, {"ConvCreate"})  # 只有 ConvCreate 是请求体

    assert any("ConvCreate.channel" in h for h in hits), f"扫描器漏报了人造 str 字段：{hits}"
    assert not any("ConvOut" in h for h in hits), f"扫描器误报了读 DTO：{hits}"


def test_ws_path_does_not_pass_raw_msg_type_to_orm():
    """**WS 路径的源码级守卫**：不得把裸 dict 的值直接交给 ORM。

    为什么这里只能做源码级（而不是行为级）：`app/api/v1/ws.py` 那条路径
    **没有 Pydantic schema**（`payload = json.loads(raw)`），所以上面的类级防线
    **扫不到它**。行为级复现放在 `evidence/verify_ws_msg_type_bypass.py`
    （真实 WS 帧 + 现场清理），但它需要起服务；这里放一条**零成本、可进 CI** 的守卫，
    防止有人把它改回去。

    ⚠️ 明确它的强度边界：这是**文本匹配**，比行为断言弱 ——
    它只能防「原样改回去」，防不了「换一种写法绕过」。所以两者都要有。
    """
    src = (BACKEND / "app" / "api" / "v1" / "ws.py").read_text(encoding="utf-8")

    # 非空前提锁：确实在构造 Message 且确实做了枚举转换（否则下面的断言是空转）
    assert "Message(" in src, "ws.py 里找不到 Message( 构造点，守卫前提不成立"
    assert "MessageType(" in src, "ws.py 里找不到 MessageType( 转换，守卫前提不成立"

    assert "msg_type=payload.get(" not in src, (
        "ws.py 又把裸 dict 的 msg_type 直接交给 ORM 了 —— "
        "这条路径没有 schema，会让越界值落库并让会话详情永久 500"
    )


# ═══════════════════ C 组：为什么必须卡写入侧（故障注入） ═══════════════════


@pytest.fixture(scope="module")
def _engine():
    """只建 `conversations` 一张表。

    刻意**不用** `Base.metadata.create_all`：全量建表实测约 25 秒，
    而这里只验证一张表的读行为。SQLite 默认不启用外键强制，
    因此单独建这张表（FK 指向的 users/cases 不存在）是安全的。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401  全量注册，避免 relationship 解析失败
    from app.models.conversation import Conversation

    base = BACKEND / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"enum_input_{uuid.uuid4().hex[:8]}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Conversation.__table__.create(c, checkfirst=True)
            )

    asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def db(_engine):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _clean():
        async with _engine.begin() as conn:
            await conn.execute(text("DELETE FROM conversations"))

    asyncio.run(_clean())
    return factory


async def test_out_of_domain_value_breaks_read_path(db):
    """**把缺陷本身固化成可执行的测试**：越界值一旦落库，读路径必然抛异常。

    这条测试的存在意义是「解释 A 组的关口为什么不能松」。它绕过 schema、
    用原生 SQL 写越界值（= 修复前的真实写入路径），断言后果是
    **整个列表查询失败**，而不是「这一行显示异常」。

    若哪天 SQLAlchemy 改成宽容处理（不再抛），这条会失败 ——
    那正是需要重新评估 A 组强度的信号，而不是把这条测试删掉。
    """
    from sqlalchemy import select, text
    from sqlalchemy.exc import StatementError

    from app.models.conversation import Conversation

    async with db() as s:
        await s.execute(
            text(
                "insert into conversations "
                "(tenant_id, external_user_id, channel, status, context) "
                "values ('t_x', 'ext-1', 'WEB', 'BOT', '{}')"
            )
        )
        await s.commit()

        # LookupError 由结果物化阶段抛出，SQLAlchemy 会把它包成 StatementError
        with pytest.raises((StatementError, LookupError)) as ei:
            (await s.execute(select(Conversation))).scalars().all()
        assert "WEB" in str(ei.value), f"抛出的异常与越界值无关，判据可能失效：{ei.value!r}"
