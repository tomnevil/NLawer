"""请求体**自由文本**字段的长度上限：还有多少个「无上限」的旧账，以及**不许再涨**。

## 为什么要有这个脚本

2026-09-22 建完端点层授权门禁后挑的下一个零判据维度。缺陷类是：

写端点的自由文本入参若**没有 `max_length`**，会被直接喂给模型或整段入库 ⇒
① 提示词成本被单次请求放大（项目未配 LLM 时走降级路径，同样会落库）；
② 存储放大；③ 与「输入审核」叠加时，超长文本会拖慢审核链路。

而全库 `grep -rn "max_length" tests/` **零命中** ⇒ 这个维度**至今零判据**。
项目自己已经在意它：`complaints.py` 的模块 docstring 把「描述长度上限」列为
滥用控制三道之一 —— 但那只是**一处**手工上限，没有尺子保证别处也有。

## 口径（**收窄过一版**，第一版噪声太大）

首版直接扫 `app/schemas/*.py` 的全部 str 字段 ⇒ **170 个**，其中绝大多数是
① **响应模型**（`*Out` / `*Response`，不需要入参上限）② **枚举/代码型**字段。
⇒ **总数不是信号**，分类才是。现口径：

| 步骤 | 做法 |
|---|---|
| ① 只取**请求体**模型 | 扫描 `app/api/**` **端点**（带 HTTP 方法装饰器的函数）签名里出现的大写注解名。<br>🚨 **不数内部辅助函数** —— 否则响应体模型会被误当请求体（失真 ④，见 `_is_endpoint`） |
| ② 排除响应模型 | 类名以 `Out` / `Response` 收尾 |
| ③ 排除代码型字段 | 字段名以 `_type`/`_code`/`_id`/`status`/`period`/`_level`/`tier`/`token`/`username` 等收尾 |
| ④ 剩下的 `str` 字段没有 `max_length` | 🎯 记为 `OPEN`（**自由文本且无上限**） |

## 判据

- **A 守卫**：必须解析到 ≥ 8 个请求体模型、≥ 10 个 str 字段 —— 防止「解析器静默退化成空集」被当成「0 欠账」。
- **B 棘轮（只降不涨）**：`OPEN` 集合必须 ⊆ `KNOWN_OPEN_TEXT_FIELDS`（旧账登记清单）。
  出现**清单外**的新字段 ⇒ 红并**点名**。
  ⇒ 修法二选一：给字段加 `max_length`，或登记进清单并写明「为什么这条不需要上限」。
  ⇒ 与 Q-F（`BASELINE_UNAUDITED_MAX=21`）同范式：**旧账先登记，但一滴都不许再加**。
- **只报**：已登记但字段其实已经有上限了 ⇒ 提示「可以把这条从清单里删掉」（不判红，避免噪音）。

⚠️ **刻意不做**：不判「上限值合不合理」——那是产品口径（多少字算合理），静态扫不出来。

退出码：`0` 通过 · `1` 突破棘轮 · `2` 环境问题。
"""
from __future__ import annotations

import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parents[0] / "backend"
SCHEMAS = BACKEND / "app" / "schemas"
API = BACKEND / "app" / "api"

#: 守卫下限（首扫实测：请求体模型 13 个 / str 字段 33 个）。
MIN_MODELS = 8
MIN_STR_FIELDS = 10

#: 代码型字段名**后缀** —— 这些不是自由文本，无需长度上限。
CODE_SUFFIXES = (
    "_type", "_code", "_id", "status", "period", "level", "tier",
    "token", "username", "_no", "_date", "_path", "_url",
    # `_dir` 与 `_path` / `_url` 同族（路径型，不是自由文本）：
    # 实测 `ArchiveRequest.out_dir`（`audit_retention.py:57`）否则会被记成欠账。
    "_dir",
)
#: 🚨 **整名匹配**：`type` / `kind` / `mode` / `decision` 这类**不带前缀**的字段名
#: 匹配不到上面的后缀（自检 Q3/Q5 当场抓到 —— `ComplaintCreate.type` 被误判成自由文本）。
#: 后缀匹配对「独立代码名」无效，必须单独列出。
CODE_EXACT = frozenset({
    "type", "kind", "mode", "decision", "status", "period", "level",
    "tier", "channel", "role", "username", "token", "dimensions",
})

#: 🎯 旧账登记清单（2026-09-22 首扫 **18** 条；修掉仪器失真 ④ 后 **16** 条，已去重）。
#: ⚠️ `UserBrief.*` 已从清单**删除**：它是响应体模型（`TokenResponse.user`），
#:    首扫被内部辅助函数 `_issue_and_store(brief: UserBrief)` 误当请求体带进来 ⇒ **假红**。
#: **只许减、不许增** —— 新增字段要么加 `max_length`，要么在这里写明理由。
KNOWN_OPEN_TEXT_FIELDS: frozenset[str] = frozenset({
    # 2026-09-22 I1 收口后**只剩**这两条（已裁定**永久豁免**，产品口径 A4）：
    "KnowledgeDocCreate.source_ref",
    "KnowledgeDocCreate.tags",
})

#: 已收口的 18 条（**留作审计轨迹**，不参与判定）—— 从 20 条降到 2 条。
#: ⚠️ 若哪天有人把某个 `max_length` 摘掉，棘轮会立刻把它点名出来。
CLOSED_TEXT_FIELDS: frozenset[str] = frozenset({
    "AnalysisUpdate.summary",
    "AnalysisUpdate.legal_analysis",
    "AnalysisUpdate.change_note",
    "IterateRequest.note",
    "ComplianceScanCreate.title",
    "ComplianceScanCreate.scope",
    "ComplianceScanCreate.input_summary",
    "ContractReviewRequest.title",
    "ContractReviewRequest.source_text",
    "KnowledgeDocCreate.title",
    "KnowledgeDocCreate.content",
    "SendMessageRequest.text",
    "ReviewAction.comment",
    "QuotaAdjust.reason",
    # —— 失真 ⑤ 修好后才暴露出来的 4 条（模型定义在 `app/api/`，首版扫不到）——
    "QARequest.question",
    "ComplaintCreate.description",
    "ComplaintCreate.contact",
    "ComplaintHandle.handle_note",
})


def _upper_tokens(ann: str) -> list[str]:
    """从注解里取类名候选：`Optional[ComplaintCreate]` / `X | None` 都能取出。"""
    cleaned = ann.replace("Optional[", "").replace("]", "").replace("List[", "")
    return [t.strip() for t in cleaned.split("|") if t.strip() and t.strip()[0].isupper()]


_HTTP_METHODS = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route",
})


def _is_endpoint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """端点 = 带 **HTTP 方法装饰器** 的函数（`@router.post(...)` / `@app.get(...)`）。

    🚨 2026-09-22 修（仪器失真 ④）：首版扫 `app/api/**` 里**所有函数**的入参，
    把内部辅助函数也算成端点 —— 实测 `auth.py:52`
    `async def _issue_and_store(response, user, brief: UserBrief, *, svc)` ⇒
    `UserBrief` 这个**纯响应体模型**（`TokenResponse.user`）被当成请求体，
    产生 2 条假红：`UserBrief.full_name` / `UserBrief.tenant_name`。
    ⇒ 按它们的「建议」去给响应体字段加 `max_length` 是**错的**（会截断前端显示的名字）。

    实测全库 `add_api_route` **零命中** ⇒ 端点**一律**走装饰器注册，
    故用装饰器判定是可靠的；若将来改用 `add_api_route`，判据 A 的
    `MIN_MODELS` 守卫会因模型数突降而报警。
    """
    for d in node.decorator_list:
        head = ast.unparse(d).split("(")[0].strip()
        if "." in head and head.rsplit(".", 1)[-1].lower() in _HTTP_METHODS:
            return True
    return False


def request_models() -> set[str]:
    """① 端点签名里真正当入参用的模型名（**只数端点**，不数内部辅助函数）。"""
    used: set[str] = set()
    for p in API.rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_endpoint(node):
                continue
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                if a.annotation is None:
                    continue
                used.update(_upper_tokens(ast.unparse(a.annotation)))
    return used


def _model_files() -> list[pathlib.Path]:
    """请求体模型的定义文件：**`app/schemas/` 与 `app/api/` 都要扫**。

    🚨 2026-09-22 修（仪器失真 ⑤）：首版只扫 `app/schemas/*.py`，而项目把**一部分
    请求体模型直接定义在 API 文件里** —— 实测 7 个：`PurgeRequest` / `ArchiveRequest`
    （`audit_retention.py`）、`ComplaintCreate` / `ComplaintHandle`（`complaints.py`）、
    `StartRequest` / `CollectRequest`（`documents.py`）、`QARequest`（`qa.py`）。
    ⇒ 四条真自由文本**全在尺子外面**：`QARequest.question`（**问答主入口**，直接喂模型）、
    `ComplaintCreate.description`（上限只写在服务层 `complaint_service.py:59`）、
    `ComplaintCreate.contact`、`ComplaintHandle.handle_note`。
    ⇒ 「0 欠账」的置信度取决于**扫描范围**，不只是判据写得好不好（同族：#137、#138）。

    ⚠️ `app/schemas/` 排在前 ⇒ 同名类以 schemas 里的定义为准（去重取先出现者）。
    """
    # ⚠️ **必须按路径去重**：两个根目录可能重叠（自检里就把 SCHEMAS 和 API 指向了同一个
    # 临时目录）⇒ 同一文件被扫两次 ⇒ 模型数翻倍（自检 Q1 实测抓到：期望 3、实测 6）。
    files = sorted(SCHEMAS.glob("*.py")) + sorted(API.rglob("*.py"))
    return list(dict.fromkeys(files))


def scan(models: set[str]) -> tuple[list[str], list[str], int, int]:
    """返回 (OPEN 字段, 全部请求体 str 字段, 模型数)。

    ⚠️ **必须去重**：同一个类名会在多个 schema 文件里各定义一次
    （实测 `ComplianceScanCreate` 在 `compliance.py` 与 `document.py` 都有）
    ⇒ 不去重会**重复计数**，报出来的「欠账 N」比实际大（实测 21 vs 去重后 18）。
    """
    open_fields: list[str] = []
    all_fields: list[str] = []
    n_models = 0
    for p in _model_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name not in models:
                continue
            if node.name.endswith(("Out", "Response")):
                continue
            n_models += 1
            for st in node.body:
                if not isinstance(st, ast.AnnAssign) or not isinstance(st.target, ast.Name):
                    continue
                ann = ast.unparse(st.annotation)
                if "str" not in ann:
                    continue
                fid = f"{node.name}.{st.target.id}"
                all_fields.append(fid)
                val = ast.unparse(st.value) if st.value is not None else ""
                if "max_length" in val:
                    continue
                if (any(st.target.id.endswith(s) for s in CODE_SUFFIXES)
                        or st.target.id in CODE_EXACT):
                    continue
                open_fields.append(fid)
    return _dedup(open_fields), _dedup(all_fields), n_models


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def main() -> int:
    models = request_models()
    open_fields, all_fields, n_models = scan(models)

    print(f"# 请求体模型 {n_models} 个 · str 字段 {len(all_fields)} 个 · "
          f"**无上限的自由文本 {len(open_fields)} 个**（旧账清单 {len(KNOWN_OPEN_TEXT_FIELDS)} 条）\n")

    rc = 0

    # ---- 判据 A：守卫（防解析器静默退化）----
    if n_models < MIN_MODELS or len(all_fields) < MIN_STR_FIELDS:
        print(f"❌ 守卫失败：模型 {n_models} < {MIN_MODELS} 或字段 {len(all_fields)} < {MIN_STR_FIELDS}")
        print("   多半是 AST 解析/路径变了 ⇒ 尺子已经量不到东西，别把「0 欠账」当结论。")
        return 1

    fresh = sorted(set(open_fields) - KNOWN_OPEN_TEXT_FIELDS)
    registered = sorted(set(open_fields) & KNOWN_OPEN_TEXT_FIELDS)
    stale = sorted(KNOWN_OPEN_TEXT_FIELDS - set(open_fields))

    print(f"## 已登记旧账（{len(registered)}）\n")
    for f in registered:
        print(f"  · {f}")
    print()

    if fresh:
        print(f"## ❌ 清单外的**新**无上限自由文本（{len(fresh)}）\n")
        for f in fresh:
            print(f"  · {f}")
        print("\n   给字段加 `max_length`，或登记进 `KNOWN_OPEN_TEXT_FIELDS` 并写明理由。")
        rc = 1
    else:
        print(f"✅ 棘轮未突破（无上限字段 {len(open_fields)} 条**全部**在旧账清单内）")

    if stale:
        print("\nℹ️ 清单里这些字段**已经有上限了** ⇒ 可以把它们从清单里删掉（不判红）：")
        for f in stale:
            print(f"  · {f}")

    return rc


# ---------------------------------------------------------------------------
# 自检：**合成源码**，不读真实仓库 ⇒ 可进 CI 的 SELFTESTABLE。
# ---------------------------------------------------------------------------
SYN_SCHEMA = '''
from pydantic import BaseModel, Field

class ComplaintCreate(BaseModel):
    type: str = Field(..., description="投诉类型")
    description: str = Field(..., max_length=2000)
    contact: Optional[str] = Field(None, max_length=100)

class NoteIn(BaseModel):
    body: str
    kind: str

class DocOut(BaseModel):
    title: str
'''

#: 合成**端点**源码（自检 Q9/Q10 用）：一个真端点 + 一个内部辅助函数。
#: ⚠️ `_helper` 的入参是响应体模型 `UserBrief`，正对应 `auth.py:52` 的 `_issue_and_store`
#: ⇒ 只数端点时它**不该**被采集。
SYN_API = '''
from fastapi import APIRouter

router = APIRouter()


@router.post("/notes", summary="新建批注")
async def create_note(body: NoteIn):
    return {}


async def _helper(brief: UserBrief):
    return brief


# 🚨 模型**定义在 API 文件里**（不在 `app/schemas/`）—— 失真 ⑤ 的真实形态：
# 首版 `scan()` 只扫 schemas 目录 ⇒ 这种模型**整个看不见**。
class ApiOnlyIn(BaseModel):
    question: str
'''


def run_self_test() -> int:
    bad: list[str] = []

    def check(key: str, desc: str, got: object, want: object) -> None:
        ok = got == want
        print(f"  [{key}] {desc}\n        期望 {want}  实测 {got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[{key}] 期望 {want} 实测 {got}")

    # 用合成源码跑真函数：临时把 **SCHEMAS 与 API 都**指向临时目录。
    # ⚠️ 两个都要换：只换 SCHEMAS 的话，真实 `app/api/**` 里的类会混进 `scan()`，
    #    让 `n` 随仓库内容漂移 ⇒ 自检变成「看仓库今天长什么样」，不是「看探针逻辑」。
    import tempfile

    global SCHEMAS, API
    saved, saved_api = SCHEMAS, API
    try:
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "x.py").write_text(SYN_SCHEMA, encoding="utf-8")
            (d / "y.py").write_text(SYN_API, encoding="utf-8")
            SCHEMAS = d
            API = d
            open_f, all_f, n = scan({"ComplaintCreate", "NoteIn", "DocOut", "ApiOnlyIn"})
            got_models = request_models()
    finally:
        SCHEMAS, API = saved, saved_api

    check("Q1", "响应模型 `DocOut` 不收（类名以 Out 收尾）；其余 3 个都收", n, 3)
    check("Q2", "有 `max_length` 的 `description` **不算** OPEN",
          "ComplaintCreate.description" in open_f, False)
    check("Q3", "代码型字段 `type` **不算** OPEN（防假红）",
          "ComplaintCreate.type" in open_f, False)
    check("Q4", "自由文本 `NoteIn.body` 无上限 ⇒ **算** OPEN",
          "NoteIn.body" in open_f, True)
    check("Q5", "代码型字段 `NoteIn.kind` 不算 OPEN", "NoteIn.kind" in open_f, False)
    check("Q6", "全部请求体 str 字段都被收集（含响应模型外的）", len(all_f) >= 3, True)
    check("Q7", "`Optional[ComplaintCreate]` 能取出类名",
          "ComplaintCreate" in _upper_tokens("Optional[ComplaintCreate]"), True)
    check("Q8", "`X | None` 也能取出类名",
          "ComplaintCreate" in _upper_tokens("ComplaintCreate | None"), True)
    check("Q9", "🚨 内部辅助函数 `_helper(brief: UserBrief)` **不算**端点 ⇒ 其入参模型不采集",
          "UserBrief" in got_models, False)
    check("Q10", "真端点 `@router.post` 的入参模型**要**采集（防上一条改过头）",
          "NoteIn" in got_models, True)
    check("Q11", "🚨 定义在 `app/api/` 里的模型也要扫到（失真 ⑤：`scan()` 不能只扫 schemas）",
          "ApiOnlyIn.question" in open_f, True)

    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **探针坏了**（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    print("\n自检通过（11 臂）。")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(run_self_test())
    sys.exit(main())
