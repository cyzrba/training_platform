"""知识入库编排：解析 → 切片 → 落库（真源），向量化单独一步。

按方案 4.4 的事务纪律：解析与向量化都是 CPU/GPU 重活，**不放在数据库写事务里**。
这里的顺序是"先解析切片（不写库）→ 再一次短写入落 doc + chunks"，SQLite 单写，
写锁只覆盖落库那一小段。

切片状态流转：``PENDING``（已切片）→ ``READY``（向量已写入 Milvus）。失败时文档
``status=FAILED`` 并记 ``parse_error``——**入库失败不影响上传成功**：文件照常落对象存储、
附件照常挂项目，老师能在知识文档里看到失败原因并重试。
"""

import hashlib
from dataclasses import dataclass, field

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.time import now
from app.models.enums import KnowledgeChunkStatus, KnowledgeDocStatus, KnowledgeDocType
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.services import embedding, parsing, settings_store, splitting, storage, vector_store

#: Milvus VarChar 上限 65535 字节，单块正文超出就截断并计数
MAX_TEXT_BYTES = 60000


@dataclass
class IngestResult:
    """解析入库结果。"""

    doc: KnowledgeDoc
    chunk_count: int = 0
    ok: bool = True
    error: str | None = None
    #: 非致命问题（向量没清掉 / 没重新向量化等），调用方应回给前端
    warnings: list[str] = field(default_factory=list)


@dataclass
class EmbedResult:
    """向量化结果。"""

    doc_id: int
    chunk_count: int
    collection: str
    model: str
    truncated: int = 0


@dataclass
class IngestPlan:
    """解析 + 切分的结果（不含任何数据库操作）。

    先算出计划再落库，是为了让"CPU 密集的解析"发生在写事务之外——
    SQLite 单写，长事务会阻塞所有写入（方案 4.4）。
    """

    chunks: list[splitting.Chunk]
    strategy: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def build_plan(
    content: bytes, filename: str, *, doc_type: str = KnowledgeDocType.EVAL_CRITERIA
) -> IngestPlan:
    """解析 + 切分，纯计算不碰数据库；失败原因装进 ``error`` 而不是抛出去。

    **切分策略按文档类型分流**：

    - 评分标准（``EVAL_CRITERIA``）是"整份对照着用"的文档，正文不超过
      ``knowledge_whole_doc_max_chars`` 就整份作为一块——它本来就要全文交给模型，
      拆成几十片反而会让后面的维度拿不到条款；
    - 知识库（``KNOWLEDGE``）是给学生检索的资料，可能有几万字，必须结构化细切，
      否则向量只反映整篇的平均语义，检索精度会很差。

    超过整份上限的评分标准自动回落到结构化切分，不会撑爆向量模型的上下文。
    """
    try:
        parsed = parsing.parse(content, filename=filename)
        if parsed.is_empty:
            raise BusinessRuleError("文件里没有可解析的文本（扫描件需要先做 OCR）")
        text_chars = sum(len(block.text) for block in parsed.blocks)
        whole = (
            doc_type == KnowledgeDocType.EVAL_CRITERIA
            and 0 < text_chars <= settings.knowledge_whole_doc_max_chars
        )
        strategy = (
            splitting.WHOLE_STRATEGY
            if whole
            else splitting.strategy_name(settings.knowledge_chunk_size_chars)
        )
        chunks = splitting.split_document(parsed, whole=whole)
        if not chunks:
            raise BusinessRuleError("切分后没有任何内容，请检查文件是否为空")
    except Exception as exc:  # 解析失败也要留下可追溯的记录
        strategy = splitting.strategy_name(settings.knowledge_chunk_size_chars)
        return IngestPlan(chunks=[], strategy=strategy, error=str(exc))
    return IngestPlan(chunks=chunks, strategy=strategy)


async def persist_plan(
    session: AsyncSession,
    plan: IngestPlan,
    *,
    title: str,
    doc_type: str = KnowledgeDocType.EVAL_CRITERIA,
    file_asset_id: int | None = None,
    uploaded_by: int | None = None,
    description: str | None = None,
    doc: KnowledgeDoc | None = None,
) -> IngestResult:
    """把解析计划落库：写 doc + 切片，失败时把原因记在 doc 上。"""
    if doc is None:
        doc = KnowledgeDoc(
            title=title,
            doc_type=doc_type,
            file_asset_id=file_asset_id,
            source="UPLOAD",
            description=description,
            uploaded_by=uploaded_by,
        )
        session.add(doc)
    else:
        doc.title = title

    doc.chunk_strategy = plan.strategy
    doc.parse_error = plan.error
    if not plan.ok:
        doc.status = KnowledgeDocStatus.FAILED
        doc.total_chunks = 0
        await session.flush()
        return IngestResult(doc=doc, ok=False, error=plan.error)

    if doc.id is not None:  # 重切：先清掉旧切片
        for old in await _existing_chunks(session, doc.id):
            await session.delete(old)
        await session.flush()

    doc.status = KnowledgeDocStatus.READY
    doc.total_chunks = len(plan.chunks)
    await session.flush()  # 先拿到 doc.id 才能挂切片

    session.add_all(
        [
            KnowledgeChunk(
                doc_id=doc.id or 0,
                chunk_index=index,
                content=chunk.content,
                content_hash=chunk.content_hash,
                char_count=chunk.char_count,
                heading_path=chunk.heading_path or None,
                page_no=chunk.page_no,
                status=KnowledgeChunkStatus.PENDING,
            )
            for index, chunk in enumerate(plan.chunks)
        ]
    )
    await session.flush()
    return IngestResult(doc=doc, chunk_count=len(plan.chunks), ok=True)


async def create_doc_from_bytes(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    title: str,
    doc_type: str = KnowledgeDocType.EVAL_CRITERIA,
    file_asset_id: int | None = None,
    uploaded_by: int | None = None,
    description: str | None = None,
    doc: KnowledgeDoc | None = None,
) -> IngestResult:
    """解析 + 切片 + 落库（先算计划再落库）；``doc`` 传已存在的文档就做重切。"""
    plan = build_plan(content, filename, doc_type=doc_type)
    return await persist_plan(
        session,
        plan,
        title=title,
        doc_type=doc_type,
        file_asset_id=file_asset_id,
        uploaded_by=uploaded_by,
        description=description,
        doc=doc,
    )


async def reparse_document(session: AsyncSession, doc: KnowledgeDoc) -> IngestResult:
    """重新读源文件并重切，然后**自动重新向量化**。

    为什么要连向量一起重做：重切会删掉旧切片、按新策略生成新切片（新主键），
    Milvus 里那条 `doc_id` 的旧向量指向的 chunk_id 已经不存在了。如果只重切不重嵌，
    这份文档会**静默地搜不到**（旧向量命中后被丢弃、新切片又没有向量），而接口却返回成功。
    所以这里把"重切 → 清旧向量 → 重新向量化"做成一个动作。

    向量化不可用时（没装 rag 依赖 / Milvus 连不上）不清空真源，而是**清掉旧向量**并回一个
    warning，避免留下指向已删除切片的脏命中；文档状态停在``READY``但切片是 ``PENDING``，
    调一次 ``/embed`` 就能补齐。
    """
    content, filename = await load_source(session, doc)
    result = await create_doc_from_bytes(
        session,
        content=content,
        filename=filename,
        title=doc.title,
        doc_type=doc.doc_type,
        file_asset_id=doc.file_asset_id,
        uploaded_by=doc.uploaded_by,
        description=doc.description,
        doc=doc,
    )
    # 切片失败时旧切片已被删除，旧向量更要清掉，否则会命中已不存在的 chunk
    if not result.ok:
        warning = await purge_vectors(session, doc.id or 0)
        if warning:
            result.warnings.append(warning)
        return result
    return await reembed_after_reparse(session, doc, result)


async def reembed_after_reparse(
    session: AsyncSession, doc: KnowledgeDoc, result: IngestResult
) -> IngestResult:
    """重切之后把向量补上；补不上就清掉旧向量并留下提示。"""
    if not (embedding.rag_available() and vector_store.milvus_available()):
        warning = await purge_vectors(session, doc.id or 0)
        result.warnings.append(
            warning or "未安装 RAG 依赖或 Milvus 不可用，已清理旧向量；请稍后调用 /embed 重新向量化"
        )
        return result
    try:
        await embed_document(session, doc)
    except Exception as exc:  # 重新向量化失败不该让重切整体失败
        warning = await purge_vectors(session, doc.id or 0)
        detail = warning or "已清理旧向量"
        result.warnings.append(f"重新向量化失败，{detail}；请稍后重试 /embed（{exc}）")
        return result
    return result


async def purge_vectors(session: AsyncSession, doc_id: int) -> str | None:
    """删掉某文档在 Milvus 里的全部向量。

    尽力而为：Milvus 挂掉或依赖没装时不让调用方失败（否则脏数据就删不掉了），
    而是把原因回给调用方——真源那边已经处理完，重建索引也不会把错误数据带回来。
    """
    if not doc_id:
        return None
    if not vector_store.milvus_available():
        return "未安装 pymilvus，Milvus 里的旧向量没清理（uv sync --extra rag）"
    try:
        config = await settings_store.get_milvus_config(session)
        client = vector_store.get_client(config.uri)
        vector_store.delete_by_doc(client, config, doc_id)
    except Exception as exc:  # 任何失败都只降级提示
        return f"Milvus 旧向量没清理（{exc}），建议稍后重试或用 reindex 重建"
    return None


async def load_source(session: AsyncSession, doc: KnowledgeDoc) -> tuple[bytes, str]:
    """从对象存储读回源文件，返回 ``(内容, 原始文件名)``。"""
    if not doc.file_asset_id:
        raise BusinessRuleError("该知识文档没有关联源文件，无法重新解析")
    from app.crud.attempt import FileAssetRepository

    asset = await FileAssetRepository(session).get(doc.file_asset_id)
    if asset is None:
        raise NotFoundError(f"源文件 {doc.file_asset_id} 不存在")
    return storage.read_bytes(asset.bucket, asset.object_key), asset.original_name


async def embed_document(session: AsyncSession, doc: KnowledgeDoc) -> EmbedResult:
    """把文档切片向量化并写入 Milvus，切片状态推进到 READY。"""
    from app.crud.knowledge import KnowledgeChunkRepository

    chunks = await KnowledgeChunkRepository(session).list_of_doc(doc.id or 0)
    if not chunks:
        raise BusinessRuleError("该文档还没有切片，先解析入库再向量化")

    embedding_config = await settings_store.get_embedding_config(session)
    milvus_config = await settings_store.get_milvus_config(session)

    texts: list[str] = []
    truncated = 0
    for chunk in chunks:
        text = chunk.content
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            text = text.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
            truncated += 1
        texts.append(text)

    dense_vectors, sparse_vectors = embedding.encode_texts(embedding_config, texts)
    token_counts = embedding.count_tokens(embedding_config, texts)

    client = vector_store.get_client(milvus_config.uri)
    vector_store.delete_by_doc(client, milvus_config, doc.id or 0)  # 重嵌入前清掉旧向量
    rows = [
        {
            "pk": chunk.id,
            "doc_id": chunk.doc_id,
            "doc_type": doc.doc_type,
            "chunk_index": chunk.chunk_index,
            "status": KnowledgeChunkStatus.READY.value,
            "text": text,
            "dense": dense,
            "sparse": sparse,
        }
        for chunk, text, dense, sparse in zip(chunks, texts, dense_vectors, sparse_vectors, strict=False)
    ]
    written = vector_store.upsert_chunks(client, milvus_config, rows, dim=embedding_config.dim)

    timestamp = now()
    for index, chunk in enumerate(chunks):
        chunk.status = KnowledgeChunkStatus.READY
        chunk.model_name = embedding_config.model
        chunk.vector_id = f"{milvus_config.collection_name}:{chunk.id}"
        chunk.updated_at = timestamp
        if token_counts is not None and index < len(token_counts):
            chunk.token_count = token_counts[index]
    await session.flush()

    doc.status = KnowledgeDocStatus.READY
    doc.parse_error = None
    return EmbedResult(
        doc_id=doc.id or 0,
        chunk_count=written,
        collection=milvus_config.collection_name,
        model=embedding_config.model,
        truncated=truncated,
    )


async def _existing_chunks(session: AsyncSession, doc_id: int) -> list[KnowledgeChunk]:
    from app.crud.knowledge import KnowledgeChunkRepository

    return await KnowledgeChunkRepository(session).list_of_doc(doc_id)


def content_digest(content: bytes) -> str:
    """源文件摘要，用于重复上传短路（现由 file_asset.sha256 承担）。"""
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "MAX_TEXT_BYTES",
    "EmbedResult",
    "IngestPlan",
    "IngestResult",
    "build_plan",
    "content_digest",
    "create_doc_from_bytes",
    "embed_document",
    "load_source",
    "persist_plan",
    "purge_vectors",
    "reembed_after_reparse",
    "reparse_document",
]
