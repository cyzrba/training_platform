"""健康检查。"""

from typing import Any

from fastapi import APIRouter
from sqlmodel import func, select

from app.api.deps import DbSession
from app.core.config import settings
from app.core.db import check_database
from app.core.response import EnvelopeRoute
from app.models.knowledge import KnowledgeChunk
from app.schemas.base import ApiResponse

router = APIRouter(route_class=EnvelopeRoute, tags=["系统"])


@router.get("/health", response_model=ApiResponse[dict[str, Any]], summary="服务健康检查")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "app": settings.app_name,
        "version": settings.app_version,
        "env": "debug" if settings.debug else "prod",
    }


@router.get("/health/db", response_model=ApiResponse[dict[str, Any]], summary="数据库健康检查")
async def health_db() -> dict[str, object]:
    return await check_database()


@router.get(
    "/health/milvus",
    response_model=ApiResponse[dict[str, Any]],
    summary="Milvus 连通性与集合统计（知识库是否就绪）",
)
async def health_milvus(session: DbSession) -> dict[str, object]:
    from app.services import settings_store, vector_store

    config = await settings_store.get_milvus_config(session)
    counts = (
        await session.exec(select(KnowledgeChunk.status, func.count()).group_by(KnowledgeChunk.status))
    ).all()
    chunks = {str(status): int(total) for status, total in counts}

    if not vector_store.milvus_available():
        return {
            "ok": False,
            "uri": config.uri,
            "error": "未安装 pymilvus，执行 uv sync --extra rag",
            "chunks": chunks,
        }
    try:
        client = vector_store.get_client(config.uri)
        info = vector_store.describe(client, config)
    except Exception as exc:
        return {"ok": False, "uri": config.uri, "error": str(exc), "chunks": chunks}
    return {"ok": True, "chunks": chunks, **info}
