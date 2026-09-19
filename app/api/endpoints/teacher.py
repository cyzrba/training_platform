"""教师工作台接口：我任教的班级 + 分组明细 + 发布任务的完成进度。

这一层只做取参与组装，统计口径全部收在 ``app/services/teaching_overview.py``，
接口文档见 ``docs/教师端接口文档.md`` §5。
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession
from app.core.response import EnvelopeRoute
from app.schemas.base import ApiResponse
from app.schemas.teaching import TeacherClassOverviewOut
from app.services.teaching_overview import teacher_class_overview

router = APIRouter(route_class=EnvelopeRoute, tags=["教师工作台"])


@router.get(
    "/teachers/{teacher_id}/classes",
    response_model=ApiResponse[TeacherClassOverviewOut],
    summary="教师任教的班级总览（班级 / 分组 / 任务完成进度 / 平均完成率）",
)
async def get_teacher_classes(
    teacher_id: int,
    db: DbSession,
    with_task_students: Annotated[
        bool, Query(description="true（默认）时每个任务带逐学生明细；false 只给汇总数字")
    ] = True,
) -> dict:
    return await teacher_class_overview(db, teacher_id, with_task_students=with_task_students)


__all__ = ["router"]
