"""企业专属知识库（PRD 5.7）：合同库 / 制度库 / 历史咨询 / 偏好设置。

隔离硬要求（PRD AC：企业私有知识隔离率 100%）：
1. 查询层强制按 `tenant_id` 过滤，禁止裸查询
2. 读取前后双重校验归属（写入时校验 + 读取后 assert_tenant）

**向量索引（P0-2 修复）**：写入知识文档时同步切片并写入 `knowledge_embeddings`，
使语义检索有语料可用。未配置 embedding 时静默跳过（纯 BM25 模式），不影响写入。
"""
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, NotFoundError, TenantDeniedError
from app.core.pagination import PaginationParams, count_bounded
from app.models.knowledge import KnowledgeDoc
from app.rag.chunking import chunk_text


class KnowledgeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        tenant_id: str,
        title: str,
        doc_type: str,
        content: str,
        source_ref: Optional[str] = None,
        tags: Optional[str] = None,
        uploaded_by: Optional[int] = None,
        index_embeddings: bool = True,
    ) -> KnowledgeDoc:
        chunks = chunk_text(content)
        doc = KnowledgeDoc(
            tenant_id=tenant_id,
            title=title,
            doc_type=doc_type,
            content=content,
            source_ref=source_ref,
            tags=tags,
            uploaded_by=uploaded_by,
            chunk_count=len(chunks),
        )
        self.db.add(doc)
        await self.db.flush()

        if index_embeddings and chunks:
            await self._index_chunks(doc, chunks)

        return doc

    async def _index_chunks(self, doc: KnowledgeDoc, chunks: list) -> int:
        """把文档切片写入向量库，返回成功条数。

        `chunk_text` 返回的是 `[{"text": str, "meta": {...}}]` 结构，这里只取 text。

        任何失败（未配 Key / embedding 服务不可用）都只告警不抛错：知识库写入
        的可用性优先于语义检索。此时该文档仍可被 BM25 检索到。
        """
        texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
        texts = [t for t in texts if t and t.strip()]
        if not texts:
            return 0

        try:
            from app.ai.embeddings import build_embedding_client
            from app.models.embedding import KnowledgeEmbedding
        except Exception as exc:  # noqa: BLE001
            logger.debug("向量索引组件不可用，跳过：{}", exc)
            return 0

        try:
            client = build_embedding_client()
        except Exception as exc:  # noqa: BLE001 生产缺 Key 会抛 ConfigurationError
            logger.warning("Embedding 客户端不可用，知识文档仅支持关键词检索：{}", exc)
            return 0

        if client is None:
            return 0  # 合法的纯 BM25 模式

        try:
            vectors = await client.embed(texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识文档向量化失败（已降级为关键词检索）：{}", exc)
            return 0

        for i, (text, vec) in enumerate(zip(texts, vectors)):
            self.db.add(
                KnowledgeEmbedding(
                    tenant_id=doc.tenant_id,
                    doc_id=f"knowledge:{doc.id}:{i}",
                    source_type="KNOWLEDGE",
                    content=text,
                    embedding=vec,
                )
            )
        await self.db.flush()
        logger.info("知识文档 {} 已写入向量索引 {} 条", doc.id, len(vectors))
        return len(vectors)

    async def list_docs(
        self,
        tenant_id: str,
        params: PaginationParams,
        *,
        doc_type: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> tuple[list[KnowledgeDoc], int, bool]:
        base = select(KnowledgeDoc).where(KnowledgeDoc.tenant_id == tenant_id)
        if doc_type:
            base = base.where(KnowledgeDoc.doc_type == doc_type)
        if keyword:
            base = base.where(KnowledgeDoc.title.like(f"%{keyword}%"))

        # 有界计数（见 core/pagination.py 的实测数据）：关键词 LIKE 前导通配
        # 无法走索引，无界 COUNT 需全表扫描且不能短路。
        total, is_lower_bound = await count_bounded(self.db, base)
        rows = list(
            (
                await self.db.execute(
                    base.order_by(KnowledgeDoc.id.desc()).offset(params.offset).limit(params.limit)
                )
            ).scalars().all()
        )
        return rows, total, is_lower_bound

    async def get(self, doc_id: int, tenant_id: str) -> KnowledgeDoc:
        """读取后二次校验归属（双重校验之一）。"""
        doc = await self.db.get(KnowledgeDoc, doc_id)
        if doc is None:
            raise NotFoundError("知识文档不存在", code=ErrorCode.KNOWLEDGE_NOT_FOUND)
        if doc.tenant_id != tenant_id:
            raise TenantDeniedError(
                "无权访问其他企业的知识库", code=ErrorCode.KNOWLEDGE_TENANT_DENIED
            )
        return doc

    async def delete(self, doc_id: int, tenant_id: str) -> None:
        doc = await self.get(doc_id, tenant_id)
        await self.db.delete(doc)
