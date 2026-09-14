"""知识库接口：知识文档、切片、向量化与召回测试。

老师上传评分标准后想知道"这份准则切片切得对不对、学生这么问能不能检索到"，
所以除了入库，还提供切片列表（验收切分质量）和召回测试（输入问法看命中哪几片）。

``doc_type=EVAL_CRITERIA`` 的文档没有对外暴露的问答入口：评分标准只走批改链路
在服务端内部检索，学生端没有任何路径能读到。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import DbSession, PageDep
from app.core.exceptions import NotFoundError
from app.core.response import EnvelopeRoute
from app.crud.attempt import FileAssetRepository
from app.crud.knowledge import KnowledgeChunkRepository, KnowledgeDocRepository
from app.models.enums import KnowledgeChunkStatus, KnowledgeDocStatus
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.models.project import ProjectFile
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.knowledge import (
    KnowledgeChunkRead,
    KnowledgeDocListItem,
    KnowledgeEmbedOut,
    KnowledgeIngestOut,
    KnowledgeRecallHit,
    KnowledgeRecallIn,
    KnowledgeRecallOut,
)
from app.services import knowledge_ingest, retrieval, settings_store, vector_store

router = APIRouter(route_class=EnvelopeRoute, tags=["知识库"])


# --------------------------------------------------------------------- 依赖


def doc_repo(db: DbSession) -> KnowledgeDocRepository:
    return KnowledgeDocRepository(db)


def chunk_repo(db: DbSession) -> KnowledgeChunkRepository:
    return KnowledgeChunkRepository(db)


def asset_repo(db: DbSession) -> FileAssetRepository:
    return FileAssetRepository(db)


KnowledgeDocRepo = Annotated[KnowledgeDocRepository, Depends(doc_repo)]
KnowledgeChunkRepo = Annotated[KnowledgeChunkRepository, Depends(chunk_repo)]
FileAssetRepo = Annotated[FileAssetRepository, Depends(asset_repo)]


async def _doc_or_404(repo: KnowledgeDocRepository, doc_id: int) -> KnowledgeDoc:
    doc = await repo.get(doc_id)
    if doc is None:
        raise NotFoundError(f"知识文档 {doc_id} 不存在")
    return doc


def _ingest_out(doc: KnowledgeDoc) -> KnowledgeIngestOut:
    return KnowledgeIngestOut(
        knowledge_doc_id=doc.id or 0,
        doc_type=doc.doc_type,
        status=doc.status,
        chunk_count=doc.total_chunks,
        chunk_strategy=doc.chunk_strategy,
        parse_error=doc.parse_error,
    )


# ----------------------------------------------------------------- 文档列表


@router.get(
    "/knowledge/docs",
    response_model=ApiResponse[Page[KnowledgeDocListItem]],
    summary="知识文档列表（按用途 / 状态 / 项目过滤）",
)
async def list_knowledge_docs(
    session: DbSession,
    docs: KnowledgeDocRepo,
    assets: FileAssetRepo,
    params: PageDep,
    doc_type: Annotated[str | None, Query(description="KNOWLEDGE 知识问答 / EVAL_CRITERIA 评分标准")] = None,
    status_filter: Annotated[
        str | None, Query(alias="status", description="PARSING / READY / FAILED / DISABLED")
    ] = None,
    project_id: Annotated[int | None, Query(description="只看某个项目的附件（如评分标准）")] = None,
) -> Page[dict]:
    filters = []
    if doc_type:
        filters.append(KnowledgeDoc.doc_type == doc_type)
    if status_filter:
        filters.append(KnowledgeDoc.status == status_filter)
    if project_id is not None:
        asset_ids = [
            int(item)
            for item in (
                await session.exec(
                    select(ProjectFile.file_asset_id).where(ProjectFile.project_id == project_id)
                )
            ).all()
        ]
        if not asset_ids:
            return Page.build(items=[], total=0, params=params)
        filters.append(KnowledgeDoc.file_asset_id.in_(asset_ids))  # type: ignore[attr-defined]

    page = await docs.list_page(params, *filters)
    items = []
    for doc in page.items:
        asset = await assets.get(doc.file_asset_id) if doc.file_asset_id else None
        items.append({**doc.model_dump(), "original_name": asset.original_name if asset else None})
    return Page.build(items=items, total=page.total, params=params)


@router.get(
    "/knowledge/docs/{doc_id}",
    response_model=ApiResponse[KnowledgeDocListItem],
    summary="知识文档详情（状态、块数、失败原因）",
)
async def get_knowledge_doc(doc_id: int, docs: KnowledgeDocRepo, assets: FileAssetRepo) -> dict:
    doc = await _doc_or_404(docs, doc_id)
    asset = await assets.get(doc.file_asset_id) if doc.file_asset_id else None
    return {**doc.model_dump(), "original_name": asset.original_name if asset else None}


@router.get(
    "/knowledge/docs/{doc_id}/chunks",
    response_model=ApiResponse[Page[KnowledgeChunkRead]],
    summary="切片列表（验收切分质量）",
)
async def list_knowledge_chunks(
    doc_id: int,
    docs: KnowledgeDocRepo,
    chunks: KnowledgeChunkRepo,
    params: PageDep,
    status_filter: Annotated[
        str | None, Query(alias="status", description="PENDING / READY / FAILED")
    ] = None,
) -> Page[KnowledgeChunkRead]:
    await _doc_or_404(docs, doc_id)
    filters = [KnowledgeChunk.doc_id == doc_id]
    if status_filter:
        filters.append(KnowledgeChunk.status == status_filter)
    return await chunks.list_page(params, *filters, order_by=KnowledgeChunk.chunk_index)


# ----------------------------------------------------------------- 入库动作


@router.post(
    "/knowledge/docs/{doc_id}/reparse",
    response_model=ApiResponse[KnowledgeIngestOut],
    summary="重新切片（幂等重建，改了切分参数后用）",
)
async def reparse_knowledge_doc(
    doc_id: int,
    docs: KnowledgeDocRepo,
) -> KnowledgeIngestOut:
    doc = await _doc_or_404(docs, doc_id)
    result = await knowledge_ingest.reparse_document(docs.session, doc)
    return _ingest_out(result.doc)


@router.post(
    "/knowledge/docs/{doc_id}/embed",
    response_model=ApiResponse[KnowledgeEmbedOut],
    summary="向量化并写入 Milvus（切片状态推进到 READY）",
)
async def embed_knowledge_doc(doc_id: int, docs: KnowledgeDocRepo) -> KnowledgeEmbedOut:
    doc = await _doc_or_404(docs, doc_id)
    result = await knowledge_ingest.embed_document(docs.session, doc)
    return KnowledgeEmbedOut(
        knowledge_doc_id=result.doc_id,
        chunk_count=result.chunk_count,
        collection=result.collection,
        model=result.model,
        truncated=result.truncated,
    )


@router.post(
    "/knowledge/docs/{doc_id}/recall",
    response_model=ApiResponse[KnowledgeRecallOut],
    summary="召回测试：输入问法，看命中哪些切片",
)
async def recall_knowledge_doc(
    doc_id: int,
    payload: KnowledgeRecallIn,
    session: DbSession,
    docs: KnowledgeDocRepo,
) -> KnowledgeRecallOut:
    doc = await _doc_or_404(docs, doc_id)
    result = await retrieval.recall(
        session,
        query=payload.query,
        doc_ids=[doc.id or 0],
        top_k=payload.top_k,
        use_rerank=payload.use_rerank,
    )
    return KnowledgeRecallOut(
        query=payload.query,
        doc_ids=[doc.id or 0],
        candidate_count=result.candidate_count,
        reranked=result.reranked,
        warnings=result.warnings,
        hits=[
            KnowledgeRecallHit(
                chunk_id=hit.chunk_id,
                doc_id=hit.doc_id,
                chunk_index=hit.chunk_index,
                heading_path=hit.heading_path,
                content=hit.content,
                score=hit.score,
                rerank_score=hit.rerank_score,
            )
            for hit in result.hits
        ],
    )


@router.post(
    "/knowledge/recall",
    response_model=ApiResponse[KnowledgeRecallOut],
    summary="跨文档召回测试（按项目 / 用途检索，评分标准批改链路用的就是这个）",
)
async def recall_knowledge(
    payload: KnowledgeRecallIn,
    session: DbSession,
    docs: KnowledgeDocRepo,
    project_id: Annotated[int | None, Query(description="限定某个项目的附件")] = None,
    doc_type: Annotated[str | None, Query(description="KNOWLEDGE / EVAL_CRITERIA")] = None,
) -> KnowledgeRecallOut:
    doc_ids: list[int] | None = None
    if project_id is not None:
        asset_ids = [
            int(item)
            for item in (
                await session.exec(
                    select(ProjectFile.file_asset_id).where(ProjectFile.project_id == project_id)
                )
            ).all()
        ]
        if not asset_ids:
            return KnowledgeRecallOut(query=payload.query, warnings=["该项目下没有任何知识文档"])
        ready_docs = await docs.list_by_file_assets(asset_ids, status=KnowledgeDocStatus.READY)
        doc_ids = [item.id or 0 for item in ready_docs]
        if not doc_ids:
            return KnowledgeRecallOut(query=payload.query, warnings=["该项目下没有已入库的知识文档"])

    result = await retrieval.recall(
        session,
        query=payload.query,
        doc_ids=doc_ids,
        doc_type=doc_type,
        top_k=payload.top_k,
        use_rerank=payload.use_rerank,
    )
    return KnowledgeRecallOut(
        query=payload.query,
        doc_ids=doc_ids or [],
        candidate_count=result.candidate_count,
        reranked=result.reranked,
        warnings=result.warnings,
        hits=[
            KnowledgeRecallHit(
                chunk_id=hit.chunk_id,
                doc_id=hit.doc_id,
                chunk_index=hit.chunk_index,
                heading_path=hit.heading_path,
                content=hit.content,
                score=hit.score,
                rerank_score=hit.rerank_score,
            )
            for hit in result.hits
        ],
    )


@router.delete(
    "/knowledge/docs/{doc_id}",
    response_model=ApiResponse[MessageOut],
    summary="停用知识文档（软删 + 切片标 DISABLED + 清理 Milvus 向量）",
)
async def disable_knowledge_doc(
    doc_id: int,
    session: DbSession,
    docs: KnowledgeDocRepo,
    chunks: KnowledgeChunkRepo,
) -> MessageOut:
    doc = await _doc_or_404(docs, doc_id)
    # 停用要把三处一起收干净：文档状态、切片状态、Milvus 向量。
    # 只改文档状态的话，向量还在（Milvus 里的 status 冗余字段仍是 READY），
    # 不带 doc_id 的召回仍会命中已停用的内容。
    for chunk in await chunks.list_of_doc(doc.id or 0):
        await chunks.update(chunk, {"status": KnowledgeChunkStatus.DISABLED})
    await docs.update(doc, {"status": KnowledgeDocStatus.DISABLED})
    await docs.remove(doc)
    warning = await _purge_vectors(session, doc)
    if warning:
        return MessageOut(message=f"知识文档 {doc_id} 已停用，切片标记为 DISABLED；{warning}")
    return MessageOut(message=f"知识文档 {doc_id} 已停用，切片标记为 DISABLED，Milvus 向量已清理")


async def _purge_vectors(session: AsyncSession, doc: KnowledgeDoc) -> str | None:
    """删掉该文档在 Milvus 里的向量。

    尽力而为：Milvus 挂掉或依赖没装时不让"停用"这个动作失败（否则脏数据就停不掉了），
    而是把原因回给调用方——真源已经标停用，重建索引也不会把它带回来。
    """
    if not vector_store.milvus_available():
        return "未安装 pymilvus，Milvus 里的旧向量没清理（uv sync --extra rag）"
    try:
        config = await settings_store.get_milvus_config(session)
        client = vector_store.get_client(config.uri)
        vector_store.delete_by_doc(client, config, doc.id or 0)
    except Exception as exc:  # 任何失败都只降级提示，不让停用失败
        return f"Milvus 旧向量没清理（{exc}），建议稍后重试或用 reindex 重建"
    return None


__all__ = ["router"]
