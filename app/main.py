"""应用入口：uvicorn app.main:app"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.db import check_database
from app.core.exceptions import register_exception_handlers

OPENAPI_TAGS = [
    {"name": "系统", "description": "健康检查等基础接口"},
    {"name": "枚举字典", "description": "状态 / 类型 code 与中文文案"},
    {"name": "账户权限", "description": "用户、角色、权限、系统配置"},
    {"name": "教学组织", "description": "班级、分组、在班学生"},
    {"name": "岗位技能", "description": "岗位、技能树、技能节点、成长规则"},
    {"name": "实训项目", "description": "项目、模块库、项目模块"},
    {"name": "闯关评审", "description": "闯关轮次、作答、提交、评审"},
    {"name": "证书", "description": "证书台账"},
    {"name": "知识库问答", "description": "RAG 知识库与 AI 问答"},
    {"name": "通知审计", "description": "站内通知与操作日志"},
]


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="岗位闯关式实训平台后端（P0 · 单学院）",
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["系统"], summary="根路径健康检查")
    async def root_health() -> dict[str, object]:
        return {"ok": True, "app": settings.app_name, "version": settings.app_version}

    @app.get("/health/db", tags=["系统"], summary="根路径数据库健康检查", include_in_schema=False)
    async def root_health_db() -> dict[str, object]:
        return await check_database()

    return app


app = create_app()
