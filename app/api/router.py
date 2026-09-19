"""接口路由汇总：各业务域的路由在这里集中登记。

新增业务域时，在 endpoints/ 下建同名文件，然后在 API_ROUTERS 里加一行即可。
"""

from fastapi import APIRouter, FastAPI

from app.api.endpoints import (
    accounts,
    attempts,
    enums,
    health,
    job_skill,
    knowledge,
    organization,
    projects,
    publish,
    qa,
    reviews,
    teacher,
)

#: 全部业务域路由
API_ROUTERS: tuple[APIRouter, ...] = (
    health.router,
    enums.router,
    accounts.router,
    organization.router,
    job_skill.router,
    projects.router,
    publish.router,
    teacher.router,
    knowledge.router,
    qa.router,
    attempts.router,
    reviews.router,
)


def include_api_routers(app: FastAPI, *, prefix: str) -> None:
    """按统一前缀挂载业务路由。

    刻意保持"一层 include"：FastAPI 0.141 的嵌套 include_router 会重建路由上下文，
    丢掉自定义 route_class，而统一响应体正是靠 EnvelopeRoute 生效的。
    """
    for router in API_ROUTERS:
        app.include_router(router, prefix=prefix)
