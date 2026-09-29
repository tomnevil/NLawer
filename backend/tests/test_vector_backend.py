"""向量后端与 Postgres 切换测试（特性开关）。

覆盖：auto 解析规则、SQLite 下 pgvector 的降级行为、内存向量库的真实检索能力。

**注意（P0-2 修复后语义变更）**：
- `build_vector_store()` 的返回值**不再由 `EMBEDDING_API_KEY` 决定**。向量存储（库）
  与查询向量（钥匙）是两件事：库的存在性由 `VECTOR_BACKEND` 决定，是否做向量
  召回由 Retriever 层依据 embedding 客户端是否存在判定。旧测试把「没 Key 就没库」
  当作正确行为，实际掩盖了「后端配置错误被误报成无向量能力」的问题。
- 内存 `VectorStore` **已实现真实余弦检索**，不再无条件返回空。旧测试断言
  「返回空由 BM25 兜底」等于把缺陷固化为规范。
"""
from app.config import settings
from app.rag.vector_store import PgVectorStore, VectorStore, build_vector_store


def test_sqlite_default_backend_is_memory(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "auto")
    assert settings.is_postgres is False
    assert settings.vector_backend == "memory"


def test_postgres_auto_resolves_pgvector(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "auto")
    assert settings.is_postgres is True
    assert settings.vector_backend == "pgvector"


def test_auto_on_sqlite_returns_memory_store(monkeypatch):
    """auto + SQLite → 内存向量库（真实检索能力，不是 None）。"""
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", None)
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "auto")
    assert isinstance(build_vector_store(), VectorStore)


def test_backend_none_disables_vector(monkeypatch):
    """显式 none 才是「关闭向量」的唯一开关。"""
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "none")
    assert build_vector_store() is None


def test_pgvector_on_sqlite_degrades_to_memory(monkeypatch):
    """SQLite 下指定 pgvector：降级为内存实现并告警，而非返回 None。

    返回 None 会让「后端配置错误」被误读成「该环境没有向量能力」，
    降级 + 告警才能让问题可见、同时保持功能可用。
    """
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "pgvector")
    store = build_vector_store()
    assert isinstance(store, VectorStore), "应降级为内存向量库"


async def test_pgvector_search_without_query_vector_returns_empty():
    """契约：None 查询向量一律返回空（表示本次不做向量召回）。"""
    store = PgVectorStore(None)  # 不触碰 DB
    assert await store.search(None) == []


def test_memory_store_performs_real_similarity_search():
    """内存向量库必须做真实余弦检索（P0-2 修复的核心之一）。"""
    store = VectorStore()
    store.add("doc-1", [1.0, 0.0], {"content": "法条"})
    store.add("doc-2", [0.0, 1.0], {"content": "判例"})
    assert "doc-1" in store._store

    hits = store.search([1.0, 0.0], top_k=3, tenant_id="t")
    assert len(hits) == 1, "只有余弦>0 的项应被召回"
    assert hits[0]["doc_id"] == "doc-1"
    assert hits[0]["content"] == "法条"
    assert hits[0]["score"] > 0


def test_memory_store_none_vector_returns_empty():
    store = VectorStore()
    store.add("doc-1", [0.1, 0.2], {"content": "法条"})
    assert store.search(None) == []
