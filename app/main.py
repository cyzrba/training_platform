"""应用入口：uvicorn app.main:app"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import include_api_routers
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.schemas.base import ErrorResponse

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
        description=(
            "岗位闯关式实训平台后端。\n\n"
            "**统一响应体**：所有接口返回 `{code, data, msg}` —— 成功 `code=200`、`msg=ok`、"
            "业务数据在 `data`；失败 `code=422`、错误提示在 `msg`、明细在 `data`。"
        ),
        openapi_tags=OPENAPI_TAGS,
        responses={
            422: {
                "model": ErrorResponse,
                "description": "业务失败（HTTP 200，失败与否看 body 的 code=422）",
            },
            500: {"model": ErrorResponse, "description": "服务异常（body 的 code=500）"},
        },
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
    include_api_routers(app, prefix=settings.api_prefix)

    return app


app = create_app()
