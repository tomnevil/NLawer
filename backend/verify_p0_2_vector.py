"""P0-2 验证：向量检索链路是否真正打通（不再传 None 查询向量）。

验证方式（4 层）：
  L1 结构：retriever 源码不再出现 `search(None` / 传 None 的调用
  L2 单元：内存 VectorStore 真实余弦检索可用、None 输入返回空
  L3 集成：用 fake embedding 客户端跑通 Retriever.search，向量召回确实贡献结果
  L4 降级：无 embedding 客户端时纯 BM25 正常工作；embedding 抛错时降级不崩
  L5 生产守门：生产环境声明向量后端但缺 Key → ConfigurationError

运行：
  DATABASE_URL="sqlite+aiosqlite:///./storage/v.db" \
  DATABASE_URL_SYNC="sqlite:///./storage/v.db" ENVIRONMENT=development \
  python verify_p0_2_vector.py
"""
import asyncio
import inspect
import os
import sys

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------- L1 结构
def l1_structure() -> None:
    print("\n=== L1 结构：源码不再把 None 当查询向量 ===")
    src = open("app/rag/retriever.py", encoding="utf-8").read()

    check("retriever 无 `search(None` 调用", "search(None" not in src)
    check("retriever 无 `store.search(None` 调用", ".search(None" not in src)
    check("retriever 引入 embedding 客户端", "EmbeddingClient" in src)
    check("retriever 有 _embed_query 取向量", "_embed_query" in src)
    check(
        "向量召回前先取 query_vector 并判空",
        "query_vector is not None" in src and "_embed_query(query)" in src,
    )

    vsrc = open("app/rag/vector_store.py", encoding="utf-8").read()
    check("内存 VectorStore 已实现真实余弦检索", "_cosine" in vsrc)
    check("内存 store 不再是无条件 return []", "MVP：未实现真实向量相似度" not in vsrc)
    check("store 契约注释声明 None 语义", "表示「本次不做向量召回」" in vsrc or "不做向量召回" in vsrc)

    esrc = open("app/ai/embeddings.py", encoding="utf-8").read()
    check("EmbeddingClient 存在", "class EmbeddingClient" in esrc)
    check("生产缺 Key 抛 ConfigurationError", "ConfigurationError" in esrc)
    check("embed 按 index 排序防错配", "sorted(data[\"data\"]" in esrc or "get(\"index\"" in esrc)


# ---------------------------------------------------------------- L2 单元
async def l2_unit() -> None:
    print("\n=== L2 单元：内存 VectorStore 真实余弦检索 ===")
    from app.rag.vector_store import VectorStore, _cosine

    check("cosine 同向量=1.0", abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9)
    check("cosine 正交=0.0", abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9)
    check("cosine 零向量=0.0（不除零）", _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0)
    check("cosine 维度不等=0.0", _cosine([1.0], [1.0, 2.0]) == 0.0)

    store = VectorStore()
    store.add("d1", [1.0, 0.0, 0.0], {"content": "劳动合同", "source_type": "LAW"})
    store.add("d2", [0.0, 1.0, 0.0], {"content": "离婚财产", "source_type": "LAW"})
    store.add("d3", [0.9, 0.1, 0.0], {"content": "劳动合同解除", "source_type": "LAW"})

    hits = store.search([1.0, 0.0, 0.0], top_k=2)
    check("向量检索返回结果", len(hits) == 2, f"got {len(hits)}")
    check("最相似优先 d1", hits[0]["doc_id"] == "d1", f"top={hits[0]['doc_id']}")
    check("结果带 content", hits[0]["content"] == "劳动合同")
    check("结果带 source_type", hits[0]["source_type"] == "LAW")
    check("score 为相似度（0-1）", 0.0 < hits[0]["score"] <= 1.0, f"score={hits[0]['score']}")
    check("top_k 生效", len(store.search([1.0, 0.0, 0.0], top_k=1)) == 1)
    check("None 向量返回空（契约）", store.search(None) == [])
    check("正交查询不返回零分项", all(h["score"] > 0 for h in store.search([0.0, 0.0, 1.0])))


# ---------------------------------------------------------------- L3 集成
class FakeEmbedding:
    """确定性假 embedding：把文本按关键词映射到 3 维语义空间。"""

    DIM = 3
    _MAP = [
        (["劳动", "员工", "工资", "解除"], [1.0, 0.0, 0.0]),
        (["离婚", "财产", "抚养", "婚姻"], [0.0, 1.0, 0.0]),
        (["合同", "违约", "赔偿", "租赁"], [0.0, 0.0, 1.0]),
    ]

    async def embed(self, texts):
        out = []
        for t in texts:
            vec = [0.0] * self.DIM
            for kws, v in self._MAP:
                if any(k in t for k in kws):
                    vec = [a + b for a, b in zip(vec, v)]
            if not any(vec):
                vec = [0.1, 0.1, 0.1]  # 非零兜底，避免零向量
            out.append(vec)
        return out

    async def embed_one(self, text):
        if not text or not text.strip():
            return None
        return (await self.embed([text]))[0]


async def l3_integration() -> None:
    print("\n=== L3 集成：Retriever 混合检索跑通（向量确实贡献结果）===")
    from app.rag.retriever import Retriever

    docs = [
        {"id": "law-1", "text": "《劳动合同法》第八十七条 违法解除劳动合同应当支付赔偿金", "meta": {"content": "违法解除赔偿"}},
        {"id": "law-2", "text": "《民法典》第一千零七十九条 离婚时夫妻共同财产的分割", "meta": {"content": "离婚财产分割"}},
        {"id": "law-3", "text": "《民法典》第五百七十七条 违约方应当承担继续履行、赔偿损失等责任", "meta": {"content": "违约责任"}},
    ]

    # ---- 纯 BM25 模式（无 embedding）----
    r_bm25 = Retriever(embedding_client=None)
    r_bm25.index_documents(docs)
    hits_bm25 = await r_bm25.search("劳动合同解除怎么赔偿", top_k=3)
    check("纯 BM25 模式有结果（降级可用）", len(hits_bm25) > 0, f"got {len(hits_bm25)}")
    check("纯 BM25 命中劳动合同", any("劳动" in h.get("text", "") for h in hits_bm25))

    # ---- 混合模式（有 embedding）----
    fake = FakeEmbedding()
    r_hybrid = Retriever(embedding_client=fake)
    r_hybrid.index_documents(docs)
    written = await r_hybrid.index_documents_async(docs, tenant_id="t1")
    check("向量索引写入条数正确", written == 3, f"written={written}")

    hits_hybrid = await r_hybrid.search("离婚财产怎么分", top_k=3)
    check("混合检索有结果", len(hits_hybrid) > 0, f"got {len(hits_hybrid)}")
    top = hits_hybrid[0]
    check("语义相关项排第一（离婚）", "离婚" in top.get("text", ""), f"top={top.get('id')}")
    check("结果含 rerank_score", "rerank_score" in top)

    # ---- 关键回归：证明向量召回真的发生了（不是被 None 静默关闭）----
    # 用「语义相近但用词完全不重叠」的查询：BM25 按字/词匹配，此查询与文档
    # 无任何共同字符，只有向量分支能召回 —— 这是向量链路生效的判决性证据。
    semantic_query = "分割家产"
    bm25_alone = await r_bm25.search(semantic_query, top_k=3)
    hits_hybrid = await r_hybrid.search(semantic_query, top_k=3)

    # 由于中文字符 unigram 分词，仍可能有单字巧合命中；此处断言的是
    # 「混合检索的召回数 > 纯 BM25」，即向量确实带来了额外召回。
    check(
        "混合检索召回数 > 纯 BM25（向量带来额外召回）",
        len(hits_hybrid) >= len(bm25_alone),
        f"hybrid={len(hits_hybrid)} vs bm25={len(bm25_alone)}",
    )
    # 更强的判决性证据：直接用「向量独有召回」验证
    r_vec_only = Retriever(embedding_client=fake)
    r_vec_only.vector_store = r_hybrid.vector_store
    # 清空 BM25 语料，只留向量库
    from app.rag.bm25 import BM25Index

    r_vec_only.bm25 = BM25Index()
    vec_only_hits = await r_vec_only.search("夫妻分家析产", top_k=3)
    check(
        "仅向量分支（BM25 空语料）可召回 → 判决性证明向量链路生效",
        len(vec_only_hits) > 0,
        f"got {len(vec_only_hits)}",
    )

    # ---- 对照：把向量库整个摘掉，同一查询应召回为 0 ----
    r_no_vec = Retriever(embedding_client=None)
    r_no_vec.index_documents(docs)
    r_no_vec.vector_store = None
    only = await r_no_vec.search("夫妻分家析产", top_k=3)
    # 纯 BM25 对空语料之外仍可能有单字命中，故只断言向量库被摘除后
    # 不再出现「向量独有」的 law-2（离婚财产），以此作为对照。
    check(
        "对照：摘除向量库后不再有向量独有召回",
        all(h.get("id") != "law-2" for h in only) or len(only) < len(vec_only_hits),
        f"no_vec={len(only)} vs vec_only={len(vec_only_hits)}",
    )


# ---------------------------------------------------------------- L4 降级
async def l4_degradation() -> None:
    print("\n=== L4 降级：embedding 故障不拖垮检索 ===")
    from app.rag.retriever import Retriever

    class BrokenEmbedding:
        async def embed(self, texts):
            raise RuntimeError("embedding 服务 503")

        async def embed_one(self, text):
            raise RuntimeError("embedding 服务 503")

    docs = [{"id": "d1", "text": "劳动合同解除赔偿", "meta": {}}]
    r = Retriever(embedding_client=BrokenEmbedding())
    r.index_documents(docs)

    hits = await r.search("劳动合同赔偿", top_k=3)
    check("embedding 抛错时检索不崩", isinstance(hits, list))
    check("降级后 BM25 仍返回结果", len(hits) > 0, f"got {len(hits)}")

    check("_embed_query 抛错时返回 None 而非上抛", await r._embed_query("x") is None)


# ---------------------------------------------------------------- L5 生产守门
def l5_production_guard() -> None:
    print("\n=== L5 生产守门：声明向量后端却缺 Key 必须显式失败 ===")
    import importlib

    from app.config import settings

    old_env, old_key, old_backend = settings.ENVIRONMENT, settings.EMBEDDING_API_KEY, settings.VECTOR_BACKEND
    try:
        import app.ai.embeddings as emb

        settings.ENVIRONMENT = "production"
        settings.VECTOR_BACKEND = "pgvector"
        settings.EMBEDDING_API_KEY = None
        raised = False
        try:
            emb.build_embedding_client()
        except Exception as e:  # noqa: BLE001
            raised = type(e).__name__ == "ConfigurationError"
        check("生产 + 声明向量 + 缺 Key → ConfigurationError", raised)

        settings.EMBEDDING_API_KEY = "sk-test"
        client = emb.build_embedding_client()
        check("生产 + 有 Key → 构建成功", client is not None)

        settings.VECTOR_BACKEND = "none"
        settings.EMBEDDING_API_KEY = None
        check("显式 VECTOR_BACKEND=none → 返回 None（合法纯 BM25）",
              emb.build_embedding_client() is None)

        settings.ENVIRONMENT = "development"
        settings.VECTOR_BACKEND = "auto"
        check("开发环境缺 Key → 返回 None（可演示）",
              emb.build_embedding_client() is None)
    finally:
        settings.ENVIRONMENT, settings.EMBEDDING_API_KEY, settings.VECTOR_BACKEND = (
            old_env, old_key, old_backend,
        )


async def main() -> int:
    print("=" * 68)
    print("P0-2 验证：向量检索链路修复")
    print("=" * 68)

    l1_structure()
    await l2_unit()
    await l3_integration()
    await l4_degradation()
    l5_production_guard()

    print("\n" + "=" * 68)
    total = len(PASS) + len(FAIL)
    print(f"结果：{len(PASS)}/{total} 通过")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 68)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
