"""向量检索（特性开关）。

- 未配置 `EMBEDDING_API_KEY` 时返回 None，检索编排层跳过向量召回，仅用 BM25。
- Postgres + pgvector 时使用 `PgVectorStore` 做真实余弦检索（租户隔离）。
- 其余场景使用内存 `VectorStore`（余弦相似度暴力检索，适合开发/千级语料）。

**接口契约（P0-2 修复）**：`search(vector, ...)` 的 `vector` 为 `None` 时一律返回
空列表，表示「本次不做向量召回」。调用方**必须**先通过 embedding 客户端取得真实
查询向量；直接传 `None` 会让语义检索静默失效。
"""
import math
from typing import Any, Dict, List, Optional

from loguru import logger

from app.config import settings


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度；任一为零向量返回 0.0（避免除零）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class VectorStore:
    """内存向量库（开发 / 降级用）：余弦相似度暴力检索。

    千级语料的暴力检索为亚毫秒级，无需建索引；语料上万后应切换到 pgvector。
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}

    def add(self, doc_id: str, vector: List[float], meta: Any = None, **_: Any) -> None:
        self._store[doc_id] = {"vector": vector, "meta": meta}

    def search(
        self, vector: Optional[List[float]], top_k: int = 8, **_: Any
    ) -> List[Dict[str, Any]]:
        if vector is None:
            return []

        scored: List[tuple[float, str, Dict[str, Any]]] = []
        for doc_id, item in self._store.items():
            score = _cosine(vector, item["vector"])
            if score > 0:
                scored.append((score, doc_id, item.get("meta") or {}))
        scored.sort(key=lambda t: t[0], reverse=True)

        return [
            {
                "doc_id": doc_id,
                "content": meta.get("content", ""),
                "source_type": meta.get("source_type", "KNOWLEDGE"),
                "score": round(score, 4),
            }
            for score, doc_id, meta in scored[:top_k]
        ]



class PgVectorStore:
    """pgvector 向量检索：需要 Postgres + `vector` 扩展。

    使用 `cosine_distance` 排序，越小越相似；返回 score = 1 - distance 便于与 BM25 融合。
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def add(
        self,
        doc_id: str,
        vector: List[float],
        meta: Any = None,
        *,
        tenant_id: Optional[str] = None,
        content: str = "",
    ) -> None:
        from app.models.embedding import KnowledgeEmbedding

        meta = meta or {}
        async with self._session_factory() as db:
            db.add(
                KnowledgeEmbedding(
                    tenant_id=tenant_id or settings.DEFAULT_TENANT_ID,
                    doc_id=doc_id,
                    source_type=meta.get("source_type", "KNOWLEDGE"),
                    content=content or meta.get("content", ""),
                    embedding=vector,
                )
            )
            await db.commit()

    async def search(
        self,
        vector: Optional[List[float]],
        top_k: int = 8,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if vector is None:
            return []
        from sqlalchemy import select

        from app.models.embedding import KnowledgeEmbedding

        distance = KnowledgeEmbedding.embedding.cosine_distance(vector)
        stmt = select(KnowledgeEmbedding, distance.label("dist")).order_by(distance).limit(top_k)
        if tenant_id:
            stmt = stmt.where(KnowledgeEmbedding.tenant_id == tenant_id)

        async with self._session_factory() as db:
            rows = (await db.execute(stmt)).all()

        return [
            {
                "doc_id": row[0].doc_id,
                "content": row[0].content,
                "source_type": row[0].source_type,
                "score": round(1 - float(row[1] or 0.0), 4),
            }
            for row in rows
        ]


def build_vector_store():
    """特性开关：按配置选择向量后端，未配置 embedding 则关闭向量召回。

    **不再用 `EMBEDDING_API_KEY` 是否为空来决定后端存亡**：向量存储与查询向量是
    两件事，前者是「库」、后者是「检索钥匙」。用 Key 决定库的有无会导致
    「配了 Key 但后端选错」这类问题被掩盖。是否启用召回由 Retriever 层依据
    embedding 客户端是否存在统一判定。
    """
    backend = settings.vector_backend

    if backend == "none":
        return None

    if backend == "pgvector":
        try:
            from app.database import async_session_factory
            from app.models.embedding import HAS_PGVECTOR
        except Exception:  # pragma: no cover - 依赖缺失时降级
            return None
        if HAS_PGVECTOR and settings.is_postgres:
            return PgVectorStore(async_session_factory)
        # 声明了 pgvector 却不可用：降级内存实现并告警，避免向量召回整体消失
        logger.warning(
            "VECTOR_BACKEND=pgvector 但环境不满足（HAS_PGVECTOR={}, is_postgres={}），"
            "已降级为内存向量库",
            HAS_PGVECTOR,
            settings.is_postgres,
        )
        return VectorStore()

    return VectorStore()
