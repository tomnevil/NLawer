"""P0-2 集成验证：知识文档写入时确实落向量索引（需真实 DB）。

覆盖：写文档 → knowledge_embeddings 有行 → 租户隔离正确 → 未配 embedding 时
不报错且降级为关键词检索。

运行：
  DATABASE_URL="sqlite+aiosqlite:///./storage/k.db" \
  DATABASE_URL_SYNC="sqlite:///./storage/k.db" ENVIRONMENT=development \
  python verify_p0_2_ingest.py
"""
import asyncio
import sys

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeEmbedding:
    async def embed(self, texts):
        return [[float(len(t) % 7) + 1.0, 0.5, 0.25] for t in texts]

    async def embed_one(self, text):
        return (await self.embed([text]))[0] if text.strip() else None


async def main() -> int:
    print("=" * 68)
    print("P0-2 集成验证：知识文档向量索引落库")
    print("=" * 68)

    from app import models  # noqa: F401
    from app.ai import embeddings as emb
    from app.config import settings
    from app.database import Base, async_session_factory, engine
    from app.models.embedding import KnowledgeEmbedding
    from app.services.knowledge_service import KnowledgeService
    from sqlalchemy import func, select

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    tenants = ["platform"]
    old_backend, old_key = settings.VECTOR_BACKEND, settings.EMBEDDING_API_KEY

    # ---------------- 场景 1：未配 embedding（纯 BM25 模式）----------------
    print("\n=== 场景 1：未配置 embedding，写入不得报错 ===")
    settings.VECTOR_BACKEND = "auto"
    settings.EMBEDDING_API_KEY = None
    emb.build_embedding_client.__wrapped__ if False else None

    async with async_session_factory() as db:
        # 打桩：让 build_embedding_client 返回 None（模拟未配 Key）
        import app.services.knowledge_service as ks

        orig = emb.build_embedding_client
        emb.build_embedding_client = lambda: None
        try:
            svc = KnowledgeService(db)
            doc = await svc.create(
                tenant_id="platform",
                title="员工手册",
                doc_type="POLICY",
                content="劳动合同解除需提前三十日通知。" * 20,
            )
            await db.commit()
            check("未配 embedding 时写入成功", doc.id is not None)

            n = (
                await db.execute(
                    select(func.count()).select_from(KnowledgeEmbedding)
                )
            ).scalar_one()
            check("未配 embedding 时不产生向量行", n == 0, f"rows={n}")
        finally:
            emb.build_embedding_client = orig

    # ---------------- 场景 2：配了 embedding，应落向量 ----------------
    print("\n=== 场景 2：配置 embedding，写入应落向量索引 ===")
    settings.EMBEDDING_API_KEY = "sk-test"
    settings.VECTOR_BACKEND = "auto"

    import app.services.knowledge_service as ks2

    orig = emb.build_embedding_client
    emb.build_embedding_client = lambda: FakeEmbedding()
    try:
        async with async_session_factory() as db:
            svc = KnowledgeService(db)
            doc = await svc.create(
                tenant_id="platform",
                title="劳动合同管理规范",
                doc_type="POLICY",
                content="劳动合同解除需提前三十日书面通知员工并支付经济补偿。" * 15,
                uploaded_by=None,
            )
            await db.commit()
            doc_id = doc.id
            chunks = doc.chunk_count

            rows = (
                await db.execute(
                    select(KnowledgeEmbedding).where(
                        KnowledgeEmbedding.doc_id.like(f"knowledge:{doc_id}:%")
                    )
                )
            ).scalars().all()

            check("文档写入成功", doc_id is not None)
            check("chunk_count > 0", chunks > 0, f"chunks={chunks}")
            check("向量行数 == 切片数", len(rows) == chunks, f"{len(rows)} vs {chunks}")
            check("向量行 tenant_id 正确", all(r.tenant_id == "platform" for r in rows))
            check("向量行带 content", all(r.content for r in rows))
            check("source_type 为 KNOWLEDGE", all(r.source_type == "KNOWLEDGE" for r in rows))
    finally:
        emb.build_embedding_client = orig

    # ---------------- 场景 3：embedding 服务故障，写入仍成功 ----------------
    print("\n=== 场景 3：embedding 服务故障，知识写入不得失败 ===")

    class Broken:
        async def embed(self, texts):
            raise RuntimeError("embedding 503")

        async def embed_one(self, text):
            raise RuntimeError("embedding 503")

    emb.build_embedding_client = lambda: Broken()
    try:
        async with async_session_factory() as db:
            before = (
                await db.execute(select(func.count()).select_from(KnowledgeEmbedding))
            ).scalar_one()
            svc = KnowledgeService(db)
            doc = await svc.create(
                tenant_id="platform",
                title="故障降级测试",
                doc_type="POLICY",
                content="测试内容。" * 30,
            )
            await db.commit()
            check("embedding 故障时文档仍写入成功", doc.id is not None)

            after = (
                await db.execute(select(func.count()).select_from(KnowledgeEmbedding))
            ).scalar_one()
            check("故障时不产生半截向量行", after == before, f"{before} -> {after}")
    finally:
        emb.build_embedding_client = orig
        settings.VECTOR_BACKEND, settings.EMBEDDING_API_KEY = old_backend, old_key

    # ---------------- 场景 4：租户隔离 ----------------
    print("\n=== 场景 4：向量行按租户隔离 ===")
    async with async_session_factory() as db:
        rows = (await db.execute(select(KnowledgeEmbedding))).scalars().all()
        check("所有向量行都有 tenant_id", all(r.tenant_id for r in rows))
        check("无跨租户泄漏（仅 platform）", {r.tenant_id for r in rows} <= {"platform"})

    print("\n" + "=" * 68)
    total = len(PASS) + len(FAIL)
    print(f"结果：{len(PASS)}/{total} 通过")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 68)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
