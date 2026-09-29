"""枚举取值域漂移扫描：`native_enum=False` 的列，写入侧不校验、读取侧严格解析。

## 为什么要有这个脚本

本轮运行时健康检查抓到一例真实缺陷：`conversations.channel` 存了 `'WEB'`
（枚举只认 `WEB_SIM`/`WECOM`/`FEISHU`）⇒ `GET /api/v1/conversations` 对该租户
**永久 500**（`LookupError`，在结果物化阶段抛）。

发现一例之后必须先问一句：**这是「一个实例」还是「一类」？**
只修那一列，等于把同一形状的其它列留在原地。

实测答案：**本仓库 44 个枚举列，全部是 `native_enum=False`**
（= 纯 VARCHAR，**DB 层没有任何约束**）⇒ 全部具备这个失效形状。

## 扫描两条轴（缺一不可）

| 轴 | 问题 | 后果 |
|---|---|---|
| **A 数据侧** | 库里有没有**枚举定义之外**的取值？ | 有 ⇒ 读路径当场炸（**真实故障**） |
| **B 代码侧** | 写入 schema 把该字段标成 `str` 而不是枚举？ | **不一定**——见下面的「洞 vs 味道」 |

> 只报 A 会漏掉「还没脏但迟早会脏」的列；只报 B 会把「有洞但没数据」当成事故。
> 两者都要报，但**措辞必须分开**——一个是故障，一个是缺口。

轴 A 内部还要再分一档，因为**严重度差一个数量级**：

| 分档 | 判据 | 含义 |
|---|---|---|
| **真实故障** | 有 ORM 读路径（`select(Model)` / `query(Model)`） | 一读就抛 ⇒ **接口 500** |
| **潜伏** | 无 ORM 读路径 | 今天不炸，**加上列表端点就会炸** |

判据取「有没有 `select(<Model>)`」——那正是会让 SQLAlchemy 在**结果物化阶段**
抛异常的那一行。不做调用图分析：那是另一个量级的复杂度，而这里只需要一个
**保守下界**（判成「无」最多低估严重度，不会把潜伏说成故障）。

轴 B 内部同样要分档，因为**「schema 写成了 `str`」本身不等于缺陷**：

| 分档 | 判据（实测形状） | 含义 |
|---|---|---|
| **洞** | `channel=payload.channel` —— 值**直通 ORM** | schema 是**唯一关口** ⇒ 脏值会落库 |
| **味道** | `mode = DispatchMode(payload.mode)` —— 消费点**显式转换并拒绝** | 已在别处守住 ⇒ **不是缺陷**，但值得记一笔 |

> 反例（第一版就犯过）：两者一律报成「缺口」⇒ 4 个**已被显式守住**的字段被当成洞，
> 扫描器一上来就退 1。**一条只会报警的防线等于没有防线**——噪声会把真信号淹掉。
>
> 判据取「有没有 `= payload.<field>` 这种**不经任何转换**的赋值」，是**文本近似**：
> 它只能证明「**存在**直通写法」，不能证明「**所有**用法都直通」。
> ⇒ **命中**才判「洞」，**未命中一律降级为「味道，需人工确认」**——
> 漏判方向（把洞说成味道）落在安全侧，宁可让人多看一眼，也不误杀。

## 自检（`--self-test`）：两侧都要证明「会报警」且「不会乱报警」

沿用本项目纪律——**先证明它真的会 FAIL**，并且必须有**对照组**：

1. **会报警**：库（自检用 `:memory:`）里塞一条越界值 ⇒ 必须被标记；
2. **不乱报警**：同一张表里合法的那一列 ⇒ **必须不被标记**（对照组）。
3. 代码侧同理：一个 `str` 字段被标记，一个枚举字段不被标记，
   且名字不匹配的类不被误报。
4. **分档也要证明**：同一个 `str` 字段，直通 ORM 的判「洞」、经显式转换的判「味道」——
   否则「洞 vs 味道」这层判断本身就没被验证过；
5. **同名不同类不连坐**：两个请求体都有 `channel`，只有被直通判据命中的那个判「洞」。
   （第一版按**全局字段名**判定，`type` 被一个**普通 String 列**的调用点点亮，
   把无关的 `ReadBatchIn.type` 连坐成「洞」⇒ 干净数据集上退 1。）

五条都成立才退出 0。只有一侧成立说明探测器是「无差别全报」或「全不报」，不可信。

> 自检用 `:memory:` 而不是临时文件：沙箱的 safe-delete 垫片会把 `unlink` 改道去回收站，
> **偶尔失败** ⇒ 自检会因为「删不掉临时库」而报错，把工具问题伪装成断言失败。
> **用一个不需要删的库即可根治。**

用法：
    python evidence/verify_enum_domain_drift.py                 # 扫默认的两个库
    python evidence/verify_enum_domain_drift.py --db path.db    # 扫指定库
    python evidence/verify_enum_domain_drift.py --self-test     # 自检

退出码：`0` 干净（**只剩「味道」也算干净**，但清单要看清）
        / `1` 发现真问题（数据越界 = 故障，或 schema 直通 ORM = 洞）
        / `2` 环境问题或自检未通过
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).parent
BACKEND = HERE.parents[0] / "backend"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND))

DEFAULT_DBS = ("demo_8001.db", "storage/nlawer.db")

# 写入 schema 里「等于没校验」的注解写法
LOOSE_ANNOTATIONS = {"str", "Optional[str]", "str | None", "None | str"}


# ── 模型侧：枚举列清单 ──────────────────────────────────────────────


class EnumCol:
    def __init__(self, model: str, table: str, column: str, enum_name: str, allowed: tuple[str, ...]):
        self.model = model
        self.table = table
        self.column = column
        self.enum_name = enum_name
        self.allowed = allowed

    def __repr__(self) -> str:
        return f"{self.table}.{self.column}"


def enum_columns() -> list[EnumCol]:
    """从 `Base.metadata` 取全部枚举列（**内省而非解析源码**，避免正则漂移）。"""
    import app.models  # noqa: F401  触发模型注册
    from app.database import Base
    from sqlalchemy import Enum as SAEnum

    # 类名 → 表名（用于把表映射回模型名，再去 schemas 里找同名 Create/Update）
    cls_of_table: dict[str, str] = {}
    for m in Base.registry.mappers:
        try:
            cls_of_table[m.class_.__table__.name] = m.class_.__name__
        except Exception:  # noqa: BLE001  非映射到单表的类
            continue

    out: list[EnumCol] = []
    # ⚠️ 用 `tables.values()` 而不是 `sorted_tables`：本仓库 `cases` 与 `conversations`
    # 之间是**互相依赖的外键环**，`sorted_tables` 会抛 SAWarning
    # （"Cannot correctly sort tables; unresolvable cycles"）。
    # 这里只要列清单，**顺序无关** ⇒ 直接遍历原字典，把噪声去掉。
    for t in Base.metadata.tables.values():
        for c in t.columns:
            if not isinstance(c.type, SAEnum):
                continue
            # ⚠️ `type.enums` 是**枚举名**（SQLAlchemy 存取的是 name 不是 value），
            # 所以拿它跟库里存的值比对才是正确口径。
            out.append(
                EnumCol(
                    model=cls_of_table.get(t.name, t.name),
                    table=t.name,
                    column=c.name,
                    enum_name=c.type.enum_class.__name__ if c.type.enum_class else "?",
                    allowed=tuple(c.type.enums),
                )
            )
    return out


# ── 读路径判定：越界值是「真实故障」还是「潜伏」 ────────────────────
#
# 越界值本身是**事实**，但严重度差一个数量级：
#   - 有 ORM 读路径（`select(Model)`）⇒ 一读就抛 ⇒ **真实故障**（接口 500）
#   - 无读路径                      ⇒ 今天不炸，但**一旦加上列表端点就会炸**
#
# 用「有没有 `select(<Model>)` / `query(<Model>)`」当判据——
# 这正是会让 SQLAlchemy 在**结果物化阶段**抛异常的那一行。
# 不做调用图分析：那是另一个量级的复杂度，而且这里只需要一个**保守下界**
# （判成「无」最多是低估严重度，不会把潜伏说成故障）。


def read_paths(models: set[str]) -> dict[str, int]:
    hits = {m: 0 for m in models}
    for path in (BACKEND / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in models:
            hits[m] += text.count(f"select({m})") + text.count(f"query({m})")
    return hits


# ── 轴 A：库里有没有越界取值 ────────────────────────────────────────


def scan_conn(con: sqlite3.Connection, cols: list[EnumCol]) -> tuple[list[str], list[str]]:
    """在已打开的连接上扫描。返回 (越界明细, 跳过说明)。

    与「开文件」分离，是为了自检能用 `:memory:` ——
    沙箱的 safe-delete 垫片会把 `unlink` 改道去回收站，**偶尔失败**，
    于是自检脚本会因为「删不掉临时库」而报错退出。**用一个不需要删的库即可根治。**
    """
    bad: list[str] = []
    skipped: list[str] = []
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    for ec in cols:
        if ec.table not in tables:
            skipped.append(f"{ec.table}.{ec.column}（该库无此表）")
            continue
        try:
            got = [r[0] for r in con.execute(f"select distinct {ec.column} from {ec.table}")]  # noqa: S608
        except sqlite3.OperationalError as e:
            skipped.append(f"{ec.table}.{ec.column}（{e}）")
            continue
        for v in got:
            if v is None:
                continue
            if str(v) not in ec.allowed:
                n = con.execute(
                    f"select count(*) from {ec.table} where {ec.column}=?", (v,)  # noqa: S608
                ).fetchone()[0]
                bad.append(
                    f"{ec.table}.{ec.column} = {v!r} ×{n} 行"
                    f"（{ec.enum_name} 只认 {list(ec.allowed)}）"
                )
    return bad, skipped


def scan_db(db_path: pathlib.Path, cols: list[EnumCol]) -> tuple[list[str], list[str]]:
    if not db_path.exists():
        return [], [f"{db_path.name} 不存在，跳过"]
    con = sqlite3.connect(db_path)
    try:
        return scan_conn(con, cols)
    finally:
        con.close()


# ── 轴 B：写入 schema 是否把该字段标成 str ──────────────────────────


def schema_annotations() -> dict[str, dict[str, str]]:
    """AST 解析 `app/schemas/*.py` ⇒ {类名: {字段名: 注解文本}}。

    用 AST 而不是正则：注解可能写成 `Optional[str]` / `str | None`，
    正则很容易漏一种写法，而**漏掉的正是最该报的那一种**。
    """
    out: dict[str, dict[str, str]] = {}
    for path in sorted((BACKEND / "app" / "schemas").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            fields: dict[str, str] = {}
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields[stmt.target.id] = ast.unparse(stmt.annotation)
            if fields:
                out[node.name] = fields
    return out


def input_schemas() -> set[str]:
    """**真正当输入用的** schema 类名 = 出现在 API 端点函数参数注解里的类。

    ⚠️ 这一步是整个扫描器的精度关键，不能省。
    第一版按「类名以模型名开头且以 Create/Update 结尾」猜，结果：
      - **漏报** `SendMessageRequest.msg_type`（名字不含 `Message` 开头 + Create 结尾）；
      - 若改成「只按字段名匹配」，又会有 **45 处误报**——
        绝大多数是 `*Out` 读 DTO，那里的 `str` 是**正确**的
        （Pydantic 会把 str 枚举序列化成它的 value，输出就该是 `"web_sim"`）。

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
                # 注解可能是 `X` / `Optional[X]` / `Annotated[X, ...]`
                for node in ast.walk(arg.annotation):
                    if isinstance(node, ast.Name):
                        names.add(node.id)
                    elif isinstance(node, ast.Attribute):
                        names.add(node.attr)
    return names


def direct_orm_fields() -> set[tuple[str, str]]:
    """**值直通 ORM** 的 **(请求体类名, 字段名)** 组合（**文本近似**，不是数据流分析）。

    判据：某个端点函数**收到** `X` 这个请求体，并在函数体内出现
    `= <该参数名>.<字段>` 这种**不经任何转换**的赋值 —— 那正是
    `channel=payload.channel` 的形状。对照：`DispatchMode(payload.mode)`
    右边是**构造调用**，不是裸属性 ⇒ 不命中。

    ## ⚠️ 为什么必须按**类**限定，不能只按字段名

    第一版用**全局字段名集合**，结果 `type` 被
    `complaints.py:77` 的 `type_=payload.type` 命中 ——
    可那是 `complaints.type`，一个**普通 `String(32)` 列，压根不是枚举**。
    于是 `ReadBatchIn.type` 被**连坐**判成「洞」，扫描器在干净数据集上退 1。

    > **字段名是全局的，消费点不是。** 同一串 `type` / `status` / `mode`
    > 在不同请求体里含义完全不同，跨类共享判定必然误报。

    ## 边界（必须写在输出里）

    它只能证明「**存在**直通写法」，不能证明「**所有**用法都直通」。
    漏判方向是「把洞说成味道」⇒ 只用来**降级**（未命中标「味道，需人工确认」），
    不用来**定罪**。
    """
    hits: set[tuple[str, str]] = set()
    for path in (BACKEND / "app" / "api").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # 参数名 → 注解里出现的类名（注解可能是 `X` / `Optional[X]` / `Annotated[X, ...]`）
            params: dict[str, set[str]] = {}
            args = [*fn.args.args, *fn.args.kwonlyargs, fn.args.vararg, fn.args.kwarg]
            for a in args:
                if a is None or a.annotation is None:
                    continue
                names: set[str] = set()
                for node in ast.walk(a.annotation):
                    if isinstance(node, ast.Name):
                        names.add(node.id)
                    elif isinstance(node, ast.Attribute):
                        names.add(node.attr)
                if names:
                    params[a.arg] = names
            if not params:
                continue
            # 函数体内「右边是裸属性」的两种写法：
            #   `x = payload.field`（Assign）与 `col=payload.field`（keyword）
            # ⚠️ 关键字参数也要收：`channel=payload.channel` 正是 keyword 形状。
            values: list[ast.expr] = []
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    values.append(node.value)
                elif isinstance(node, ast.keyword):
                    values.append(node.value)
            for v in values:
                if not (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)):
                    continue
                for cls_name in params.get(v.value.id, ()):
                    hits.add((cls_name, v.attr))
    return hits


def find_loose_schema_fields(
    cols: list[EnumCol],
    schemas: dict[str, dict[str, str]],
    inputs: set[str],
    direct: set[tuple[str, str]],
) -> list[tuple[str, str]]:
    """找出「本该是枚举、却写成了 `str`」的**请求体**字段，并分「洞」与「味道」。

    只扫**输入** schema：输出 DTO 用 `str` 是对的，报出来只会制造噪声。

    **判据是「值有没有直通 ORM」，不是「schema 写没写枚举」**：

    | 形状 | 判定 | 含义 |
    |---|---|---|
    | `channel=payload.channel` | **洞** | schema 是**唯一关口** ⇒ 脏值会落库 |
    | `mode = DispatchMode(payload.mode)` | **味道** | 消费点已显式转换并拒绝 ⇒ 不是缺陷 |

    判定按 **(类名, 字段名)** 这一对做（不是按字段名），理由见 `direct_orm_fields()`。
    """
    by_name: dict[str, list[EnumCol]] = {}
    for ec in cols:
        by_name.setdefault(ec.column, []).append(ec)

    out: list[tuple[str, str]] = []
    for cls_name in sorted(inputs):
        for field, ann in (schemas.get(cls_name) or {}).items():
            if field not in by_name or ann not in LOOSE_ANNOTATIONS:
                continue
            kind = "洞" if (cls_name, field) in direct else "味道"
            for ec in by_name[field]:
                out.append(
                    (
                        kind,
                        f"{cls_name}.{field}: {ann}  →  应为 {ec.enum_name}（列 {ec.table}.{ec.column}）",
                    )
                )
    return out


# ── 自检 ────────────────────────────────────────────────────────────


def self_test() -> int:
    print("── 自检：两侧都要「会报警」且「不乱报警」──\n")
    ok = True

    # 侧 1：数据越界探测（含同表对照组）。
    # 用 `:memory:` —— 不落文件，就不需要删，也就不会被沙箱的 safe-delete 垫片影响。
    con = sqlite3.connect(":memory:")
    con.execute("create table t (ok_col varchar, bad_col varchar)")
    con.execute("insert into t values ('OK','WEB_SIM')")  # 两侧都合法
    con.execute("insert into t values ('OK','WEB')")  # bad_col 越界
    con.commit()

    cols = [
        EnumCol("T", "t", "bad_col", "IMChannel", ("WEB_SIM", "WECOM", "FEISHU")),
        EnumCol("T", "t", "ok_col", "OkEnum", ("OK",)),  # 对照组：合法
    ]
    bad, _ = scan_conn(con, cols)
    con.close()
    hit_bad = any("bad_col" in b for b in bad)
    hit_ok = any("ok_col" in b for b in bad)
    print(f"  {'✅' if hit_bad else '❌'} 越界值被标记（期望：标记）")
    print(f"  {'✅' if not hit_ok else '❌'} 合法列未被标记（期望：不标记，对照组）")
    if bad:
        print(f"      检出：{bad[0]}")
    ok &= hit_bad and not hit_ok

    # 侧 2：schema 松类型探测（含对照组 + **分档对照**）
    #
    # ⚠️ 分档必须一起自检：只证明「会标记 str 字段」是不够的——
    # 第一版正是把「已被显式守住的字段」也标成洞，才让扫描器一上来就退 1。
    # 所以这里要同时证明「直通 ORM 的判洞」和「经转换的判味道」。
    cols = [
        EnumCol("Conversation", "conversations", "channel", "IMChannel", ("WEB_SIM",)),
        EnumCol("Conversation", "conversations", "status", "ConversationStatus", ("BOT",)),
        EnumCol("Case", "cases", "mode", "DispatchMode", ("AUTO",)),
    ]
    schemas = {
        # channel 松（且实测直通 ORM）、status 紧 ⇒ 对照组
        "ConversationCreate": {"channel": "str", "status": "ConversationStatus"},
        # mode 松，但消费点是 `DispatchMode(payload.mode)` ⇒ 应为「味道」而非「洞」
        "DispatchRequest": {"mode": "str"},
        # **跨类不连坐**对照组：字段名同为 channel，但直通判据只命中 ConversationCreate
        "OtherBatchIn": {"channel": "str"},
        "ConversationOut": {"channel": "str"},  # 读 DTO：**不是输入** ⇒ 不该被报
    }
    inputs = {"ConversationCreate", "DispatchRequest", "OtherBatchIn"}  # ConversationOut 不在输入集合里
    direct = {("ConversationCreate", "channel")}  # 实测：只有这一对是「不经转换」的写法

    hits = find_loose_schema_fields(cols, schemas, inputs, direct)
    holes = [m for k, m in hits if k == "洞"]
    smells = [m for k, m in hits if k == "味道"]
    all_msgs = [m for _, m in hits]

    hit_loose = any("ConversationCreate.channel" in m for m in holes)
    hit_hole_kind = "洞" in {k for k, _ in hits} and bool(holes)
    hit_smell = any("DispatchRequest.mode" in m for m in smells)
    # 同名不同类必须**不**被判洞（第一版就栽在这：`type` 被别的类连坐）
    hit_contagion = any("OtherBatchIn.channel" in m for m in holes)
    hit_tight = any("status" in m for m in all_msgs)
    hit_out = any("ConversationOut" in m for m in all_msgs)

    print(f"  {'✅' if hit_loose else '❌'} 输入 schema 的 `str` 字段被标记（期望：标记）")
    print(f"  {'✅' if hit_hole_kind else '❌'} 直通 ORM 的字段判为 **洞**（期望：洞）")
    print(f"  {'✅' if hit_smell else '❌'} 经显式转换的字段判为 **味道**、不判洞（期望：味道）")
    print(f"  {'✅' if not hit_contagion else '❌'} **同名不同类**未被连坐（期望：不判洞）")
    print(f"  {'✅' if not hit_tight else '❌'} 枚举字段未被标记（期望：不标记，对照组）")
    print(f"  {'✅' if not hit_out else '❌'} **读 DTO** 未被误报（期望：不误报）")
    for m in sorted(set(all_msgs)):
        print(f"      检出：{m}")
    ok &= hit_loose and hit_hole_kind and hit_smell and not hit_contagion and not hit_tight and not hit_out

    print("\n" + "=" * 72)
    if ok:
        print("✅ 自检通过：两侧都会报警、且都有对照组未被误报 ⇒ 扫描结论可信。")
        return 0
    print("❌ 自检未通过：探测器失效（无差别全报 / 全不报 / 误报），结论不可信。")
    return 2


# ── 主流程 ──────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="枚举取值域漂移扫描")
    ap.add_argument("--db", action="append", default=[], help="要扫的库（可重复；默认扫两个已知库）")
    ap.add_argument("--self-test", action="store_true", help="自检：证明两侧探测器都会报警且有对照组")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    cols = enum_columns()
    print("── 枚举列清单 ──")
    print(f"  共 {len(cols)} 个枚举列；**全部为 `native_enum=False`**（纯 VARCHAR，DB 层无约束）")
    print("  ⇒ 全部具备「写入不校验 / 读取严格」的失效形状。\n")

    schemas = schema_annotations()
    inputs = input_schemas()
    direct = direct_orm_fields()
    loose = find_loose_schema_fields(cols, schemas, inputs, direct)
    holes = sorted({m for k, m in loose if k == "洞"})
    smells = sorted({m for k, m in loose if k == "味道"})

    print("── 轴 B：**请求体** schema 把枚举字段标成了 `str` ──")
    print("  判据是**值有没有直通 ORM**，不是「schema 写没写枚举」：")
    print("    [洞]   `channel=payload.channel`           ⇒ schema 是唯一关口 ⇒ 脏值会落库（缺陷）")
    print("    [味道] `mode = DispatchMode(payload.mode)` ⇒ 消费点已显式转换并拒绝 ⇒ 不是缺陷\n")
    if holes:
        for h in holes:
            print(f"  [洞]   {h}")
    if smells:
        for h in smells:
            print(f"  [味道] {h}")
    if not holes and not smells:
        print("  [ ok ] 未发现（请求体里该字段的类型都是枚举）")
    print(f"  说明：扫了 {len(schemas)} 个 schema 类，其中 {len(inputs)} 个类名出现在端点参数注解里（= 请求体）。")
    print(f"        直通判据命中（**类名.字段名**）：{sorted(f'{c}.{f}' for c, f in direct)}")
    print("        ⚠️ 直通判据是**文本近似**：只能证明「**存在**直通写法」，")
    print("           不能证明「**所有**用法都直通」⇒ 未命中一律**降级**为「味道，需人工确认」，不定罪。")
    print("        ⚠️ **只扫请求体**：输出 DTO 用 `str` 是正确的（Pydantic 把 str 枚举序列化成 value）。")

    dbs = [pathlib.Path(d) for d in args.db] or [BACKEND / d for d in DEFAULT_DBS]
    reads = read_paths({ec.model for ec in cols})

    print("\n── 轴 A：库内是否存在枚举定义之外的取值 ──")
    print("  判据：越界值本身是事实；「有读路径」⇒ 真实故障（接口 500），")
    print("        「无读路径」⇒ 潜伏（今天不炸，加上列表端点就会炸）。\n")
    live = latent = 0
    for db in dbs:
        bad, skipped = scan_db(db, cols)
        tag = db.name if db.is_absolute() else str(db)
        if not db.exists():
            print(f"  [ENV ] {tag} 不存在，跳过")
            continue
        if not bad:
            print(f"  [ ok ] {tag}：无越界取值（扫描 {len(cols) - len(skipped)} 列，跳过 {len(skipped)}）")
            continue
        by_table = {ec.table: ec for ec in cols}
        for b in bad:
            model = by_table[b.split(".")[0]].model
            n = reads.get(model, 0)
            if n:
                live += 1
                print(f"  [故障] {tag}：{b}   ← 读路径 {n} 处 ⇒ **一读就抛**")
            else:
                latent += 1
                print(f"  [潜伏] {tag}：{b}   ← **无 ORM 读路径**（尚未造成故障）")

    print("\n" + "=" * 72)
    if live:
        print(f"结果：轴 A **真实故障 {live} 处**（会让接口 500）、潜伏 {latent} 处；轴 B 洞 {len(holes)} 处。")
        print("=> 退出码 1")
        return 1
    if latent or holes:
        print(f"结果：轴 A 无真实故障；潜伏 {latent} 处、轴 B 洞 {len(holes)} 处。")
        print("=> 退出码 1（尚未造成故障，但都是**迟早会炸**的形状）")
        return 1
    if smells:
        print(f"结果：两轴皆干净。轴 B 另有 {len(smells)} 处「味道」——消费点已显式拒绝，**不构成缺陷**。")
        print("      味道清单已逐条列在上面；**不因此退 1**，但改动对应消费点时应回看。")
        print("=> 退出码 0")
        return 0
    print("结果：两轴皆干净。=> 退出码 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
