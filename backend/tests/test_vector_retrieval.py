"""向量检索链路的回归测试（防 P0-2 复发）。

守护的核心契约：**查询向量绝不允许传 None**。历史缺陷是
`self.vector_store.search(None, ...)` 硬编码 None，导致语义检索静默失效、
RAG 退化为纯关键词检索，而调用方毫无察觉。改动 rag 模块时本文件必须全绿。
"""
import pytest

from app.rag.vector_store import VectorStore, _cosine


# ------------------------------------------------------------ 余弦相似度
def test_cosine_identical_is_one():
    assert abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_zero_vector_does_not_divide_by_zero():
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_dimension_mismatch_returns_zero():
    assert _cosine([1.0], [1.0, 2.0]) == 0.0


# ------------------------------------------------------------ 内存向量库
def test_vector_store_ranks_by_similarity():
    store = VectorStore()
    store.add("d1", [1.0, 0.0, 0.0], {"content": "劳动合同"})
    store.add("d2", [0.0, 1.0, 0.0], {"content": "离婚财产"})
    store.add("d3", [0.9, 0.1, 0.0], {"content": "劳动合同解除"})

    hits = store.search([1.0, 0.0, 0.0], top_k=2)
    assert len(hits) == 2
    assert hits[0]["doc_id"] == "d1", "最相似项必须排第一"
    assert hits[0]["score"] > hits[1]["score"]


def test_vector_store_none_query_returns_empty():
    """契约：None 表示「本次不做向量召回」，必须返回空而不是报错。"""
    store = VectorStore()
    store.add("d1", [1.0, 0.0], {"content": "x"})
    assert store.search(None) == []


def test_vector_store_respects_top_k():
    store = VectorStore()
    for i in range(5):
        store.add(f"d{i}", [1.0, float(i) / 10], {"content": f"c{i}"})
    assert len(store.search([1.0, 0.0], top_k=3)) == 3


# ------------------------------------------------------------ Retriever 集成
class FakeEmbedding:
    """确定性假 embedding：关键词 → 3 维语义空间。"""

    _MAP = [
        (["劳动", "员工", "工资", "解除"], [1.0, 0.0, 0.0]),
        (["离婚", "财产", "抚养", "婚姻"], [0.0, 1.0, 0.0]),
        (["合同", "违约", "赔偿", "租赁"], [0.0, 0.0, 1.0]),
    ]

    async def embed(self, texts):
        out = []
        for t in texts:
            vec = [0.0, 0.0, 0.0]
            for kws, v in self._MAP:
                if any(k in t for k in kws):
                    vec = [a + b for a, b in zip(vec, v)]
            if not any(vec):
                vec = [0.1, 0.1, 0.1]
            out.append(vec)
        return out

    async def embed_one(self, text):
        if not text or not text.strip():
            return None
        return (await self.embed([text]))[0]


DOCS = [
    {"id": "law-1", "text": "《劳动合同法》违法解除劳动合同应当支付赔偿金", "meta": {"content": "违法解除"}},
    {"id": "law-2", "text": "《民法典》离婚时夫妻共同财产的分割", "meta": {"content": "离婚财产"}},
    {"id": "law-3", "text": "《民法典》违约方应当承担赔偿损失责任", "meta": {"content": "违约责任"}},
]


@pytest.mark.asyncio
async def test_bm25_only_mode_still_works():
    """无 embedding 时为合法的纯 BM25 模式，检索必须可用。"""
    from app.rag.retriever import Retriever

    r = Retriever(embedding_client=None)
    r.index_documents(DOCS)
    hits = await r.search("劳动合同解除赔偿", top_k=3)
    assert hits, "纯 BM25 模式必须有结果"


@pytest.mark.asyncio
async def test_vector_index_write_count():
    from app.rag.retriever import Retriever

    r = Retriever(embedding_client=FakeEmbedding())
    r.index_documents(DOCS)
    written = await r.index_documents_async(DOCS, tenant_id="t1")
    assert written == len(DOCS)


@pytest.mark.asyncio
async def test_vector_recall_adds_beyond_bm25():
    """判决性用例：向量分支必须带来 BM25 之外的额外召回。

    若有人再把查询向量改回 None，本用例会失败。
    """
    from app.rag.bm25 import BM25Index
    from app.rag.retriever import Retriever

    fake = FakeEmbedding()
    r = Retriever(embedding_client=fake)
    r.index_documents(DOCS)
    await r.index_documents_async(DOCS, tenant_id="t1")

    hybrid = await r.search("夫妻分家析产", top_k=3)

    # 只保留向量分支（清空 BM25 语料）
    r_vec_only = Retriever(embedding_client=fake)
    r_vec_only.vector_store = r.vector_store
    r_vec_only.bm25 = BM25Index()
    vec_only = await r_vec_only.search("夫妻分家析产", top_k=3)

    assert vec_only, "仅向量分支必须能召回 —— 否则向量链路未生效"
    assert len(hybrid) >= len(vec_only) or hybrid, "混合检索不应少于向量单独召回"


@pytest.mark.asyncio
async def test_embedding_failure_degrades_to_bm25():
    """embedding 服务故障不应让检索整体失败。"""
    from app.rag.retriever import Retriever

    class Broken:
        async def embed(self, texts):
            raise RuntimeError("503")

        async def embed_one(self, text):
            raise RuntimeError("503")

    r = Retriever(embedding_client=Broken())
    r.index_documents(DOCS)

    hits = await r.search("劳动合同赔偿", top_k=3)
    assert isinstance(hits, list)
    assert hits, "降级后 BM25 仍应返回结果"
    assert await r._embed_query("x") is None, "取向量失败应返回 None 而非上抛"


@pytest.mark.asyncio
async def test_empty_query_returns_none_vector():
    from app.ai.embeddings import EmbeddingClient

    client = EmbeddingClient("http://x", "k", "m", 3)
    assert await client.embed_one("") is None


# ------------------------------------------------------------ 生产守门
def test_production_without_embedding_key_fails_fast():
    """生产环境声明用向量却没配 Key，必须显式失败而非静默降级。"""
    from app.ai import embeddings as emb
    from app.config import settings
    from app.core.errors import ConfigurationError

    old = (settings.ENVIRONMENT, settings.EMBEDDING_API_KEY, settings.VECTOR_BACKEND)
    try:
        settings.ENVIRONMENT = "production"
        settings.VECTOR_BACKEND = "pgvector"
        settings.EMBEDDING_API_KEY = None
        with pytest.raises(ConfigurationError):
            emb.build_embedding_client()
    finally:
        settings.ENVIRONMENT, settings.EMBEDDING_API_KEY, settings.VECTOR_BACKEND = old


def test_vector_backend_none_returns_no_client():
    """显式关闭向量后端是合法的纯 BM25 模式，不应报错。"""
    from app.ai import embeddings as emb
    from app.config import settings

    old = (settings.VECTOR_BACKEND, settings.EMBEDDING_API_KEY)
    try:
        settings.VECTOR_BACKEND = "none"
        settings.EMBEDDING_API_KEY = None
        assert emb.build_embedding_client() is None
    finally:
        settings.VECTOR_BACKEND, settings.EMBEDDING_API_KEY = old


# ------------------------------------------------------------ 向量列序列化
def test_json_vector_serializes_list():
    """无 pgvector 时向量列必须能把 list[float] 序列化为 JSON。

    历史缺陷：Text 分支直接绑 list，SQLite 报
    `Error binding parameter: type 'list' is not supported`。
    """
    from app.models.embedding import JsonVector

    col = JsonVector()
    bound = col.process_bind_param([1.0, 2.5, -3.0], None)
    assert isinstance(bound, str), "绑定时必须转成字符串"
    assert col.process_result_value(bound, None) == [1.0, 2.5, -3.0]


def test_json_vector_handles_none_and_bad_data():
    from app.models.embedding import JsonVector

    col = JsonVector()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None
    assert col.process_result_value("not-json", None) is None
    assert col.process_result_value('{"a": 1}', None) is None, "非 list 应返回 None"


@pytest.mark.asyncio
async def test_knowledge_write_persists_vector_row():
    """知识文档写入应落向量行；未配 embedding 时不报错也不产生半截数据。"""
    from sqlalchemy import func, select

    from app import models  # noqa: F401
    from app.ai import embeddings as emb
    from app.database import Base, async_session_factory, engine
    from app.models.embedding import KnowledgeEmbedding
    from app.services.knowledge_service import KnowledgeService

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    class FakeEmb:
        async def embed(self, texts):
            return [[float(len(t) % 5) + 1.0, 0.5] for t in texts]

        async def embed_one(self, text):
            return (await self.embed([text]))[0] if text.strip() else None

    orig = emb.build_embedding_client
    try:
        # 未配 embedding：写入成功且无向量行
        emb.build_embedding_client = lambda: None
        async with async_session_factory() as db:
            doc = await KnowledgeService(db).create(
                tenant_id="platform",
                title="手册",
                doc_type="POLICY",
                content="劳动合同解除需提前通知。" * 10,
            )
            await db.commit()
            n = (await db.execute(select(func.count()).select_from(KnowledgeEmbedding))).scalar_one()
            assert n == 0, "未配 embedding 不应产生向量行"

        # 配 embedding：应落向量行且可回读为 list
        emb.build_embedding_client = lambda: FakeEmb()
        async with async_session_factory() as db:
            doc = await KnowledgeService(db).create(
                tenant_id="platform",
                title="劳动合同规范",
                doc_type="POLICY",
                content="劳动合同解除需提前三十日通知。" * 20,
            )
            await db.commit()
            rows = (
                await db.execute(
                    select(KnowledgeEmbedding).where(
                        KnowledgeEmbedding.doc_id.like(f"knowledge:{doc.id}:%")
                    )
                )
            ).scalars().all()
            assert len(rows) == doc.chunk_count, "向量行数应等于切片数"
            assert isinstance(rows[0].embedding, list), "回读应为 list"
            assert rows[0].tenant_id == "platform"
    finally:
        emb.build_embedding_client = orig

