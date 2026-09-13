"""健康检查。"""

from typing import Any

from fastapi import APIRouter

from app.core.config import settings
from app.core.db import check_database
from app.core.response import EnvelopeRoute
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
