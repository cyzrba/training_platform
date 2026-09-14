"""把 SQLite 里的知识切片重新写入 Milvus（Milvus 是可随时重建的派生索引）。

用法：
    uv run python scripts/reindex_knowledge.py                 # 重建全部已入库文档的向量
    uv run python scripts/reindex_knowledge.py --doc-id 3      # 只重建某个文档
    uv run python scripts/reindex_knowledge.py --doc-type EVAL_CRITERIA
    uv run python scripts/reindex_knowledge.py --pending       # 只补做"还没向量化"的文档

什么时候用：换了 embedding 模型、怀疑索引与真源不一致、Milvus 数据被清空之后。
真源始终是 SQLite，重建不需要动 knowledge_doc / knowledge_chunk。

前置：Milvus 已启动（deploy/docker-compose.rag.yml）、rag 依赖已装（uv sync --extra rag）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models.enums import KnowledgeChunkStatus, KnowledgeDocStatus  # noqa: E402
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc  # noqa: E402
from app.services import knowledge_ingest, settings_store, vector_store  # noqa: E402


async def _docs_to_reindex(
    *, doc_id: int | None, doc_type: str | None, pending_only: bool
) -> list[KnowledgeDoc]:
    async with SessionLocal() as session:
        stmt = select(KnowledgeDoc).where(KnowledgeDoc.deleted_at.is_(None))
        if doc_id is not None:
            stmt = stmt.where(KnowledgeDoc.id == doc_id)
        if doc_type:
            stmt = stmt.where(KnowledgeDoc.doc_type == doc_type)
        if pending_only:
            pending_doc_ids = select(KnowledgeChunk.doc_id).where(
                KnowledgeChunk.status != KnowledgeChunkStatus.READY
            )
            stmt = stmt.where(KnowledgeDoc.id.in_(pending_doc_ids))  # type: ignore[attr-defined]
        else:
            stmt = stmt.where(KnowledgeDoc.status == KnowledgeDocStatus.READY)
        return list((await session.exec(stmt)).all())


async def reindex(*, doc_id: int | None, doc_type: str | None, pending_only: bool) -> int:
    docs = await _docs_to_reindex(doc_id=doc_id, doc_type=doc_type, pending_only=pending_only)
    if not docs:
        print("没有需要重建的知识文档")
        return 0

    async with SessionLocal() as session:
        milvus_config = await settings_store.get_milvus_config(session)
    client = vector_store.get_client(milvus_config.uri)

    total_chunks = 0
    for doc in docs:
        async with SessionLocal() as session:
            reloaded = await session.get(KnowledgeDoc, doc.id)
            if reloaded is None:
                continue
            result = await knowledge_ingest.embed_document(session, reloaded)
            await session.commit()
        total_chunks += result.chunk_count
        print(f"  已重建：doc={result.doc_id} 切片={result.chunk_count} 集合={result.collection}")

    stats = vector_store.describe(client, milvus_config)
    print(
        f"完成：{len(docs)} 个文档、{total_chunks} 个切片；"
        f"集合 {stats.get('collection')} 行数 {stats.get('row_count')}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--doc-id", type=int, default=None, help="只重建某个知识文档")
    parser.add_argument("--doc-type", default=None, help="KNOWLEDGE / EVAL_CRITERIA")
    parser.add_argument("--pending", action="store_true", help="只补做还没向量化（切片状态非 READY）的文档")
    args = parser.parse_args()
    return asyncio.run(reindex(doc_id=args.doc_id, doc_type=args.doc_type, pending_only=args.pending))


if __name__ == "__main__":
    raise SystemExit(main())
