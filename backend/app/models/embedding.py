"""知识库向量（pgvector）。

仅当 Postgres + pgvector 且配置了 embedding 时启用真实向量列；SQLite 开发态
回落为 Text 列并以 JSON 序列化存储，保证：
1. 「零依赖启动」不被破坏（无需安装 pgvector）；
2. **写入语义一致** —— 两种后端都能存 `list[float]`，调用方无需分支。

（历史缺陷：Text 分支直接存 Python list 会在 SQLite 上报
 `Error binding parameter: type 'list' is not supported`，导致开发态写入失败。）
"""
import json

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.config import settings
from app.models.base import Base, TenantMixin, TimestampMixin

try:
    from pgvector.sqlalchemy import Vector

    HAS_PGVECTOR = True
except ImportError:  # pragma: no cover - 未安装 pgvector 的降级路径
    HAS_PGVECTOR = False
    Vector = None  # type: ignore


class JsonVector(TypeDecorator):
    """无 pgvector 时的向量列：Python `list[float]` <-> JSON 文本。

    只做序列化，不做相似度计算；相似度检索由 pgvector 或内存 VectorStore 负责。
    这样开发态（SQLite）写入不报错，也让测试可以覆盖真实的写入路径。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return value  # 已是序列化结果，幂等
        return json.dumps(list(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):  # pragma: no cover - 脏数据兜底
            return None
        return parsed if isinstance(parsed, list) else None


def _embedding_type():
    if HAS_PGVECTOR:
        return Vector(settings.EMBEDDING_DIM)
    return JsonVector()


class KnowledgeEmbedding(Base, TenantMixin, TimestampMixin):
    __tablename__ = "knowledge_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="KNOWLEDGE")
    content: Mapped[str] = mapped_column(Text, default="")
    embedding = mapped_column(_embedding_type(), nullable=True)
