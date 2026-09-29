"""Embedding 客户端：为向量检索提供查询/文档向量。

设计原则与 `ai/router.py` 一致：
- **零强制外部依赖**：未配置 `EMBEDDING_API_KEY` 时返回 None，检索层自动回退纯 BM25。
- **生产禁止静默降级**：生产环境若声明要用向量后端（`VECTOR_BACKEND != none`）却没配
  Key，抛 `ConfigurationError` 而非静默返回 None —— 否则会出现「以为有语义检索、
  实际只有关键词匹配」的错觉（这正是本模块要修复的 P0-2 的根因）。

接口对齐 OpenAI `/embeddings` 协议（Qwen / GLM / DeepSeek / Ollama 均兼容）。
"""
import time
from typing import Optional, Sequence

import httpx
from loguru import logger

from app.config import settings
from app.core.errors import ConfigurationError


class EmbeddingClient:
    """OpenAI 兼容的 Embedding 客户端。"""

    def __init__(self, base_url: str, api_key: str, model: str, dim: int) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.dim = dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量取向量。空输入直接返回空列表，不发请求。"""
        items = [t for t in texts if t and t.strip()]
        if not items:
            return []

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                self.base_url.rstrip("/") + "/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": list(items)},
            )
            resp.raise_for_status()
            data = resp.json()

        # 按 index 排序，防止服务端乱序返回导致向量与文本错配
        rows = sorted(data["data"], key=lambda r: r.get("index", 0))
        vectors = [r["embedding"] for r in rows]

        dim = len(vectors[0]) if vectors else 0
        if dim and dim != self.dim:
            # 维度不一致会让 pgvector 写入/检索直接报错，提前暴露比事后排查便宜
            logger.warning(
                "Embedding 维度与配置不符：返回 {} 维，配置 EMBEDDING_DIM={}",
                dim,
                self.dim,
            )
        logger.debug(
            "Embedding {} 条，{} 维，耗时 {}ms",
            len(vectors),
            dim,
            int((time.perf_counter() - t0) * 1000),
        )
        return vectors

    async def embed_one(self, text: str) -> Optional[list[float]]:
        """单条取向量；空文本返回 None。"""
        if not text or not text.strip():
            return None
        vecs = await self.embed([text])
        return vecs[0] if vecs else None


def build_embedding_client() -> Optional[EmbeddingClient]:
    """按配置构建 Embedding 客户端。

    返回 None 的**唯一**合法情形：未声明使用向量后端（`VECTOR_BACKEND=none`）
    或开发环境未配 Key。生产环境声明要用向量却不配 Key 属配置错误，必须显式失败。
    """
    if settings.VECTOR_BACKEND == "none":
        return None

    if not settings.EMBEDDING_API_KEY:
        if settings.ENVIRONMENT == "production":
            raise ConfigurationError(
                "VECTOR_BACKEND={} 需要语义检索，但 EMBEDDING_API_KEY 未配置。"
                "请配置 Embedding Key，或将 VECTOR_BACKEND 显式设为 none 以启用纯关键词检索。".format(
                    settings.VECTOR_BACKEND
                ),
                details={
                    "env_key": "EMBEDDING_API_KEY",
                    "vector_backend": settings.VECTOR_BACKEND,
                },
            )
        return None

    return EmbeddingClient(
        base_url=settings.EMBEDDING_BASE_URL,
        api_key=settings.EMBEDDING_API_KEY,
        model=settings.EMBEDDING_MODEL,
        dim=settings.EMBEDDING_DIM,
    )
