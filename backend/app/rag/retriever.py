"""混合检索编排：BM25 + 可选向量 + 重排序。

借鉴 AIECO RAG 范式；MVP 以 BM25 为主，向量与 rerank 为特性开关。
检索语料由 service 层在请求时从 DB 装载（种子法规规模千级，构建 <10ms）。

**关键约束（P0-2 修复）**：涉及向量的分支必须用**真实查询向量**，绝不允许传
`None` —— `None` 会被各个 vector store 当作「无向量」静默返回空列表，使向量
召回整体失效，而调用方毫无察觉，最终 RAG 退化为纯关键词检索。
"""
import inspect
from typing import Any, Dict, List, Optional, Union

from loguru import logger

from app.ai.embeddings import EmbeddingClient, build_embedding_client
from app.rag.bm25 import BM25Index
from app.rag.rerank import rerank
from app.rag.vector_store import PgVectorStore, VectorStore, build_vector_store


class Retriever:
    def __init__(self, *, embedding_client: Optional[EmbeddingClient] = None) -> None:
        self.bm25 = BM25Index()
        self.vector_store: Optional[Union[VectorStore, PgVectorStore]] = build_vector_store()
        # 允许注入（便于测试 / 复用连接）；未注入时按配置构建
        self.embedding_client = (
            embedding_client if embedding_client is not None else build_embedding_client()
        )

    def index_documents(self, docs: List[Dict[str, Any]]) -> None:
        """索引文档到 BM25（同步路径）。

        向量索引需要异步 embed，走 `index_documents_async`；此处保持同步以兼容
        既有调用方与千级语料的 <10ms 构建特性。
        """
        self.bm25.add_documents(docs)

    async def index_documents_async(
        self, docs: List[Dict[str, Any]], *, tenant_id: Optional[str] = None
    ) -> int:
        """索引文档到向量库，返回成功写入的条数。

        未启用向量（无 store 或无 embedding 客户端）时返回 0，不报错——
        这是合法的纯 BM25 模式，调用方无需分支。
        """
        if self.vector_store is None or self.embedding_client is None:
            return 0

        texts = [d.get("text", "") for d in docs]
        if not texts:
            return 0

        vectors = await self.embedding_client.embed(texts)
        written = 0
        for doc, vec in zip(docs, vectors):
            meta = doc.get("meta") or {}
            res = self.vector_store.add(
                str(doc["id"]),
                vec,
                meta,
                tenant_id=tenant_id or meta.get("tenant_id"),
                content=doc.get("text", ""),
            )
            if inspect.isawaitable(res):
                await res
            written += 1
        return written

    async def search(
        self, query: str, top_k: int = 8, *, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        hits = self.bm25.search(query, top_k=top_k * 2)

        if self.vector_store is not None:
            # 必须先把查询文本 embed 成真实向量；拿不到向量就跳过向量召回，
            # 而不是传 None 进 store（那等于静默关闭语义检索）。
            query_vector = await self._embed_query(query)
            if query_vector is not None:
                # pgvector 为异步实现，内存实现为同步，统一 awaitable 处理
                res = self.vector_store.search(query_vector, top_k=top_k, tenant_id=tenant_id)
                if inspect.isawaitable(res):
                    res = await res
                hits += res or []

        return rerank(query, hits, top_k=top_k)

    async def _embed_query(self, query: str) -> Optional[List[float]]:
        """取查询向量；未启用 embedding 时返回 None（合法的纯 BM25 模式）。"""
        if self.embedding_client is None:
            return None
        try:
            return await self.embedding_client.embed_one(query)
        except Exception as exc:  # noqa: BLE001
            # Embedding 服务抖动不应让整个检索失败：降级为 BM25 并告警
            logger.warning("查询向量获取失败，降级为纯 BM25 检索: {}", exc)
            return None

