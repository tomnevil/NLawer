"""P0-2 向量检索：接线级与租户隔离判据（棘轮 + 行为级）。

背景（2026-09-20 复核实测）
--------------------------
1. `tests/test_vector_retrieval.py` 已有 17 条用例，其中
   `test_vector_recall_adds_beyond_bm25` 是**真实的**行为级判据：
   把查询向量改成 `None`、或改成零向量，它都会转红（两次故障注入已验证）。
2. 但**把「拿不到向量就跳过向量召回」那层保护删掉（改成 `if True:`），
   17 条全部保持绿** —— 因为 `VectorStore.search(None)` 本来就返回 `[]`，
   删掉保护在行为上不可观测 ⇒ 该保护**没有判据**，它只是防御性注释。
3. 更硬的问题：`Retriever`（BM25 + 向量 + rerank）在 `app/` 产品代码里
   **零实例化**；`PgVectorStore` 只有 `build_vector_store()` 造，后者只被
   `Retriever.__init__` 调 ⇒ **向量写进 `KnowledgeEmbedding` 表，但产品代码
   从不把它读出来**。P0-2 修的那行代码位于一条没有调用方的链路上。

本文件补的是「接线 + 租户」两层：
- R1 写侧不许静默消失（AST，防注释误判）
- R2 读侧接线棘轮（AST，基线 0；>0 时必须同时满足 R3/R5）
- R3 读侧 SQL 必须带租户过滤（AST）
- R4 内存后端**不具备**租户隔离（行为级，登记现状：变更即红）
- R5 `Retriever.search` 必须把 tenant_id 透传给 store（行为级）
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, List, Optional

import pytest

import app
from app.rag.vector_store import VectorStore

APP_ROOT = Path(app.__file__).resolve().parent
RAG_DIR = APP_ROOT / "rag"

#: 「向量读侧已接入产品代码」的标志符号
_READ_SIDE_TARGETS = {"Retriever", "PgVectorStore", "VectorStore", "build_vector_store"}

#: 当前实测基线：`app/`（排除 `app/rag/**`）对上述符号的真实引用数
BASELINE_PRODUCT_REFERENCES = 0


def _is_rag(path: Path) -> bool:
    try:
        path.resolve().relative_to(RAG_DIR)
    except ValueError:
        return False
    return True


def _iter_product_py() -> List[Path]:
    return [p for p in APP_ROOT.rglob("*.py") if not _is_rag(p)]


def _referenced_names(tree: ast.AST) -> set[str]:
    """收集真实语法节点里的符号引用（不含注释、不含字符串）。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _READ_SIDE_TARGETS:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _READ_SIDE_TARGETS:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found |= {a.name for a in node.names if a.name in _READ_SIDE_TARGETS}
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _READ_SIDE_TARGETS or a.name.split(".")[-1] in _READ_SIDE_TARGETS:
                    found.add(a.name.split(".")[-1])
    return found


# ------------------------------------------------------------------ R1 写侧
def test_r1_knowledge_service_still_writes_vectors():
    """知识库写入侧必须真的构造 `KnowledgeEmbedding`。

    用 AST 而非子串：`"KnowledgeEmbedding(" in src` 在该调用被注释掉时
    依然恒绿（本项目已踩过一次，见 test_review_tenant_guard.py 的 G5）。
    """
    src = APP_ROOT / "services" / "knowledge_service.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    constructed = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "KnowledgeEmbedding"
    ]
    assert constructed, (
        "`app/services/knowledge_service.py` 里找不到 `KnowledgeEmbedding(...)` 的真实构造调用。"
        "向量写侧被静默删除会让「语义检索」整体失去语料，且不会有任何报错。"
    )

    # 写侧必须带上 tenant_id 关键字，否则读侧再怎么过滤也无意义
    kwargs = {k.arg for c in constructed for k in c.keywords if k.arg}
    assert "tenant_id" in kwargs, "写侧 `KnowledgeEmbedding(...)` 必须显式带 `tenant_id`"


# ------------------------------------------------------- R2 读侧接线（棘轮）
def test_r2_retrieval_read_side_wiring_ratchet():
    """向量「读侧」在产品代码里的接入状态棘轮。

    当前实测：**0 处**。即向量只写不读。本断言的作用不是宣判对错，而是
    禁止这个状态被**悄悄**改变 —— 一旦有人接入（`>0`），必须同时满足
    R3（SQL 带租户过滤）和 R5（tenant_id 透传），并把本基线显式上调。
    """
    hits: dict[str, list[str]] = {}
    for path in _iter_product_py():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 语法错误会先被 CI 的编译步骤拦下
            continue
        names = _referenced_names(tree)
        if names:
            hits[str(path.relative_to(APP_ROOT))] = sorted(names)

    total = sum(len(v) for v in hits.values())
    assert total == BASELINE_PRODUCT_REFERENCES, (
        "向量读侧接入状态发生变化：\n"
        f"  实测 {total} 处，基线 {BASELINE_PRODUCT_REFERENCES} 处\n"
        f"  明细：{hits}\n"
        "若为有意接入：请同时确认 R3（读侧 SQL 带租户过滤）与 R5（tenant_id 透传）通过，"
        "再把 BASELINE_PRODUCT_REFERENCES 上调；否则请回退这次意外接入。"
    )


def _mentions_tenant_attr(node: ast.AST) -> bool:
    """`KnowledgeEmbedding.tenant_id` 是否作为真实属性链出现（非注释/非字符串）。"""
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Attribute)
            and n.attr == "tenant_id"
            and isinstance(n.value, ast.Name)
            and n.value.id == "KnowledgeEmbedding"
        ):
            return True
    return False


def _live_tenant_filters(fn: ast.AST) -> list[ast.Call]:
    """收集函数里**可达**的 `.where(KnowledgeEmbedding.tenant_id == ...)` 调用。

    「可达」的近似：不在 `if <假常量>:` 分支里。纯子串检查会把注释掉的过滤、
    `if False:` 里的过滤都算成「已过滤」（本项目已踩过两次），故这里走 AST。
    """
    found: list[ast.Call] = []

    def visit(node: ast.AST, shadowed: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                test = child.test
                dead = isinstance(test, ast.Constant) and not test.value
                visit(child, shadowed or dead)
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "where"
                and any(_mentions_tenant_attr(a) for a in child.args)
                and not shadowed
            ):
                found.append(child)
            visit(child, shadowed)

    visit(fn, False)
    return found


# ------------------------------------------------- R3 读侧 SQL 的租户过滤
def test_r3_vector_read_sql_has_tenant_filter():
    """凡是 `select(KnowledgeEmbedding...)` 的函数，必须有**可达**的租户过滤。

    覆盖 `PgVectorStore.search`（`vector_store.py:114-117`）。目前内存后端
    不走 SQL，故不在此列 —— 它的租户盲区由 R4 用行为级判据单独钉住。

    ⚠️ 已知边界：只拦「常量假分支」这一种不可达写法；拦不住运行时恒假的动态条件。
    """
    offenders: list[str] = []
    checked = 0

    for path in APP_ROOT.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            seg = ast.get_source_segment(text, fn) or ""
            if "KnowledgeEmbedding" not in seg or "select(" not in seg:
                continue
            checked += 1
            if not _live_tenant_filters(fn):
                offenders.append(f"{path.relative_to(APP_ROOT)}::{fn.name}")

    assert checked, "没找到任何 `select(KnowledgeEmbedding...)`，判据前提不成立（可能是误改判据）"
    assert not offenders, (
        "以下函数读了 `KnowledgeEmbedding` 却没有可达的 `tenant_id` 过滤，会造成跨租户语义泄漏：\n  "
        + "\n  ".join(offenders)
    )


# -------------------------------------- R4 内存后端不具备租户隔离（登记现状）
def test_r4_in_memory_backend_is_not_tenant_isolated():
    """行为级登记：`VectorStore`（内存后端）**不做**租户过滤。

    `add`/`search` 都靠 `**_` 吞掉 `tenant_id`。而 `vector_backend=auto` 在非
    Postgres 下解析为 `memory`（`app/config.py:192`），即 **SQLite 部署启用
    向量召回 = 无租户隔离**。本断言把「当前确实不过滤」钉死：一旦有人悄悄
    加上过滤，本用例转红，提醒同步更新这条登记与相关决策。
    """
    store = VectorStore()
    store.add("t1-doc", [1.0, 0.0], {"content": "租户A的私密合同"}, tenant_id="t1")
    store.add("t2-doc", [1.0, 0.0], {"content": "租户B的私密合同"}, tenant_id="t2")

    hits = store.search([1.0, 0.0], top_k=5, tenant_id="t1")
    ids = {h["doc_id"] for h in hits}
    assert ids == {"t1-doc", "t2-doc"}, (
        "预期内存后端**不**过滤租户（现状登记）。若此处变成只剩 t1-doc，"
        "说明隔离已实现，请同步更新本登记与「SQLite 部署不得启用向量召回」的结论。"
    )


# ---------------------------------------- R5 Retriever 必须透传 tenant_id
class _SpyVectorStore:
    """记录 `search()` 收到的关键字参数。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search(
        self, vector: Optional[List[float]], top_k: int = 8, **kw: Any
    ) -> List[dict[str, Any]]:
        self.calls.append({"top_k": top_k, **kw})
        return []


class _ConstEmbedding:
    async def embed(self, texts: List[str]) -> List[List[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def embed_one(self, text: str) -> Optional[List[float]]:
        return [1.0, 0.0]


@pytest.mark.asyncio
async def test_r5_retriever_forwards_tenant_to_store():
    """`Retriever.search(tenant_id=...)` 必须原样透传给 `vector_store.search`。

    这是租户隔离链条的第一环（编排层 → 存储层）。R4 证明第二环在内存后端
    是断的；R3 证明 Postgres 后端这一环是通的。三条例证合起来才完整。
    """
    from app.rag.retriever import Retriever

    r = Retriever(embedding_client=_ConstEmbedding())
    spy = _SpyVectorStore()
    r.vector_store = spy

    await r.search("劳动合同解除", top_k=3, tenant_id="T-9")

    assert spy.calls, "向量分支未执行 —— 查询向量没算出来，或 store 为 None"
    assert spy.calls[0].get("tenant_id") == "T-9", (
        f"`tenant_id` 未透传，实际收到：{spy.calls[0]}"
    )
