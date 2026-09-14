"""召回链路：查询编码 → Milvus 混合检索 → 回 SQLite 取真源 → 可选重排。

设计要点（见方案 6.1）：

1. 查询走 BGE-M3 编码，dense + sparse 双路召回，RRF 融合；
2. 可见范围与 ``doc_type`` 在**检索阶段**用标量表达式过滤，不能检索后再筛（后者会掉召回）；
3. Milvus 只当索引：命中的 chunk_id 回 SQLite 取正文与标题路径，保证引用与实际内容一致；
4. 重排是可选增强，缺依赖或模型时降级为召回顺序，并把原因放进 ``warnings`` 回给调用方。

关于分数：``RecallHit.score`` 是 Milvus 的 **RRF 融合分**，值域很小且经常并列（几十条候选
时基本都是 1/(k+rank) 量级），**不能拿来当相关性阈值**；要卡阈值请用重排分数
``RecallHit.rerank_score``（cross-encoder 的 logits，越大越相关），
也就是 ``rag.retrieval.score_threshold`` 的实际作用对象。
"""

from dataclasses import dataclass, field

from sqlmodel.ext.asyncio.session import AsyncSession

from app.crud.knowledge import KnowledgeChunkRepository
from app.services import embedding, reranking, settings_store, vector_store


@dataclass(frozen=True)
class RecallHit:
    """一条召回结果（正文来自 SQLite，分数来自 Milvus / 重排）。"""

    chunk_id: int
    doc_id: int
    chunk_index: int
    heading_path: str | None
    content: str
    score: float
    rerank_score: float | None = None


@dataclass
class RecallResult:
    hits: list[RecallHit] = field(default_factory=list)
    #: 混合检索召回条数（重排前）
    candidate_count: int = 0
    #: 是否真的做了重排
    reranked: bool = False
    warnings: list[str] = field(default_factory=list)


async def recall(
    session: AsyncSession,
    *,
    query: str,
    doc_ids: list[int] | None = None,
    doc_type: str | None = None,
    top_k: int | None = None,
    use_rerank: bool = True,
) -> RecallResult:
    """对给定查询做一次召回，返回排序后的切片。"""
    query = query.strip()
    if not query:
        return RecallResult(warnings=["查询内容为空"])

    embedding_config = await settings_store.get_embedding_config(session)
    milvus_config = await settings_store.get_milvus_config(session)
    retrieval_config = await settings_store.get_retrieval_config(session)

    dense, sparse = embedding.encode_query(embedding_config, query)
    client = vector_store.get_client(milvus_config.uri)
    raw_hits = vector_store.hybrid_search(
        client,
        milvus_config,
        retrieval_config,
        dense=dense,
        sparse=sparse,
        doc_ids=doc_ids,
        doc_type=doc_type,
    )
    result = RecallResult(candidate_count=len(raw_hits))
    if not raw_hits:
        return result

    # Milvus 是派生索引，正文一律回 SQLite 取（真源）
    chunks = await KnowledgeChunkRepository(session).by_ids([hit["chunk_id"] for hit in raw_hits])
    by_id = {chunk.id: chunk for chunk in chunks}
    candidates: list[RecallHit] = []
    for hit in raw_hits:
        chunk = by_id.get(hit["chunk_id"])
        if chunk is None:  # 索引里有、真源里没有 → 脏数据，跳过（可用 reindex 修复）
            continue
        candidates.append(
            RecallHit(
                chunk_id=chunk.id or 0,
                doc_id=chunk.doc_id,
                chunk_index=chunk.chunk_index,
                heading_path=chunk.heading_path,
                content=chunk.content,
                score=hit["score"],
            )
        )
    if len(candidates) != len(raw_hits):
        result.warnings.append("部分命中在知识切片表里找不到，可能索引与真源不一致，建议重建索引")

    limit = top_k or retrieval_config.context_top_n
    if use_rerank:
        reranked, warning = await _try_rerank(
            session, query, candidates, retrieval_config.rerank_top_k, limit
        )
        if warning:
            result.warnings.append(warning)
        else:
            result.reranked = True
        result.hits = reranked
        return result

    result.hits = candidates[:limit]
    return result


async def _try_rerank(
    session: AsyncSession,
    query: str,
    candidates: list[RecallHit],
    rerank_top_k: int,
    limit: int,
) -> tuple[list[RecallHit], str | None]:
    """重排 top N 候选；不可用时返回 (原顺序, 降级原因)。"""
    if not reranking.reranker_available():
        return candidates[:limit], "未安装 FlagEmbedding，已跳过重排（uv sync --extra rag）"
    config = await settings_store.get_reranker_config(session)
    head = candidates[: max(1, rerank_top_k)]
    try:
        scores = reranking.score_pairs(config, query, [item.content for item in head])
    except Exception as exc:  # 重排是增强项，失败不该让召回整体失败
        return candidates[:limit], f"重排失败，已按召回顺序返回：{exc}"

    rescored = [
        RecallHit(
            chunk_id=item.chunk_id,
            doc_id=item.doc_id,
            chunk_index=item.chunk_index,
            heading_path=item.heading_path,
            content=item.content,
            score=item.score,
            rerank_score=score,
        )
        for item, score in zip(head, scores, strict=False)
    ]
    rescored.sort(
        key=lambda item: item.rerank_score if item.rerank_score is not None else float("-inf"), reverse=True
    )
    return rescored[:limit], None


__all__ = ["RecallHit", "RecallResult", "recall"]
