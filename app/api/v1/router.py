"""v1 路由汇总。"""

from fastapi import APIRouter

from app.api.v1.endpoints import enums, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(enums.router)
