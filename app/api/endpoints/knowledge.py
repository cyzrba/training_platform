"""知识库接口：知识文档、切片、向量化与召回测试。

老师上传评分标准后想知道"这份准则切片切得对不对、学生这么问能不能检索到"，
所以除了入库，还提供切片列表（验收切分质量）和召回测试（输入问法看命中哪几片）。

``doc_type=EVAL_CRITERIA`` 的文档没有对外暴露的问答入口：评分标准只走批改链路
在服务端内部检索，学生端没有任何路径能读到。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import select

from app.api.deps import DbSession, PageDep
from app.core.exceptions import NotFoundError
from app.core.response import EnvelopeRoute
from app.crud.attempt import FileAssetRepository
from app.crud.knowledge import KnowledgeChunkRepository, KnowledgeDocRepository
from app.models.enums import KnowledgeDocStatus
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
from app.services import knowledge_ingest, retrieval

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


def _ingest_out(doc: KnowledgeDoc, warnings: list[str] | None = None) -> KnowledgeIngestOut:
    return KnowledgeIngestOut(
        knowledge_doc_id=doc.id or 0,
        doc_type=doc.doc_type,
        status=doc.status,
        chunk_count=doc.total_chunks,
        chunk_strategy=doc.chunk_strategy,
        parse_error=doc.parse_error,
        warnings=warnings or [],
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
    return _ingest_out(result.doc, result.warnings)


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
    summary="删除知识文档（连带删除切片与 Milvus 向量）",
)
async def delete_knowledge_doc(
    doc_id: int,
    session: DbSession,
    docs: KnowledgeDocRepo,
    chunks: KnowledgeChunkRepo,
) -> MessageOut:
    doc = await _doc_or_404(docs, doc_id)
    # 删除要把三处一起收干净：Milvus 向量 → 切片 → 文档。
    # 顺序是先删"外面的"再删"里面的"：向量删不掉时（Milvus 挂了）不能把切片和文档
    # 先删了，否则会留下搜得到、又没法回溯的脏命中。
    warnings: list[str] = []
    purge_warning = await knowledge_ingest.purge_vectors(session, doc.id or 0)
    if purge_warning:
        warnings.append(purge_warning)

    # 切片是派生数据（能从源文件重新解析），直接物理删除，不留孤儿行
    removed = await chunks.delete_of_doc(doc.id or 0)

    await docs.update(doc, {"status": KnowledgeDocStatus.DISABLED, "total_chunks": 0})
    await docs.remove(doc)

    message = f"知识文档 {doc_id} 已删除，连带删除 {removed} 条切片"
    message += f"；{'；'.join(warnings)}" if warnings else "，Milvus 向量已清理"
    return MessageOut(message=message)


__all__ = ["router"]
