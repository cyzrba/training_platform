"""任务下发接口：发布任务、目标班级/分组、岗位范围、学生端"我的任务"。

口径：实训项目管理里的"发布"只代表项目编辑完成（可被任务引用），
教师还要发任务学生才看得到；草稿项目不能发布任务。
详见 docs/方案设计.md。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import DbSession, PageDep
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.response import EnvelopeRoute
from app.core.time import now
from app.crud.account import UserRepository
from app.crud.job_skill import JobRepository
from app.crud.organization import ClassGroupRepository, ClassRepository
from app.crud.project import TrainingProjectRepository
from app.crud.publish import (
    PublishTaskJobRepository,
    PublishTaskProjectRepository,
    PublishTaskRepository,
    PublishTaskTargetRepository,
)
from app.models.enums import PublishTaskStatus
from app.models.publish import PublishTask
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.publish import (
    PublishableProjectRead,
    PublishTargetsIn,
    PublishTaskCreate,
    PublishTaskDetail,
    PublishTaskRead,
    PublishTaskUpdate,
    StudentTaskRead,
    TargetPreviewOut,
)
from app.services import publish as publish_service

router = APIRouter(route_class=EnvelopeRoute, tags=["任务下发"])


# --------------------------------------------------------------------- 依赖


def task_repo(db: DbSession) -> PublishTaskRepository:
    return PublishTaskRepository(db)


def target_repo(db: DbSession) -> PublishTaskTargetRepository:
    return PublishTaskTargetRepository(db)


def job_scope_repo(db: DbSession) -> PublishTaskJobRepository:
    return PublishTaskJobRepository(db)


def task_project_repo(db: DbSession) -> PublishTaskProjectRepository:
    return PublishTaskProjectRepository(db)


def class_repo(db: DbSession) -> ClassRepository:
    return ClassRepository(db)


def group_repo(db: DbSession) -> ClassGroupRepository:
    return ClassGroupRepository(db)


def job_repo(db: DbSession) -> JobRepository:
    return JobRepository(db)


def training_project_repo(db: DbSession) -> TrainingProjectRepository:
    return TrainingProjectRepository(db)


def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


TaskRepo = Annotated[PublishTaskRepository, Depends(task_repo)]
TargetRepo = Annotated[PublishTaskTargetRepository, Depends(target_repo)]
JobScopeRepo = Annotated[PublishTaskJobRepository, Depends(job_scope_repo)]
TaskProjectRepo = Annotated[PublishTaskProjectRepository, Depends(task_project_repo)]
ClassRepo = Annotated[ClassRepository, Depends(class_repo)]
GroupRepo = Annotated[ClassGroupRepository, Depends(group_repo)]
JobRepo = Annotated[JobRepository, Depends(job_repo)]
TrainingProjectRepo = Annotated[TrainingProjectRepository, Depends(training_project_repo)]
UserRepo = Annotated[UserRepository, Depends(user_repo)]


# --------------------------------------------------------------------- 工具


async def _task_or_404(tasks: PublishTaskRepository, task_id: int) -> PublishTask:
    task = await tasks.get(task_id)
    if task is None:
        raise NotFoundError(f"发布任务 {task_id} 不存在")
    return task


async def _task_payload(session: AsyncSession, task: PublishTask) -> dict:
    """列表项：任务 + 项目数 / 目标数 / 覆盖学生数 + 是否过期。"""
    stats = (await publish_service.task_stats(session, [int(task.id)]))[int(task.id)]
    return {
        **task.model_dump(),
        **stats,
        "expired": task.deadline_at is not None and task.deadline_at <= now(),
    }


async def _task_detail_payload(
    session: AsyncSession,
    task: PublishTask,
    *,
    targets: PublishTaskTargetRepository,
    job_scopes: PublishTaskJobRepository,
    task_projects: PublishTaskProjectRepository,
    classes: ClassRepository,
    groups: ClassGroupRepository,
    jobs: JobRepository,
    projects: TrainingProjectRepository,
) -> dict:
    """任务详情：目标班级 / 分组、岗位范围、项目快照（都带展示名）。"""
    payload = await _task_payload(session, task)

    target_rows = await targets.list_of_task(int(task.id))
    target_payloads: list[dict] = []
    for row in target_rows:
        classroom = await classes.get(int(row.class_id))
        group = await groups.get(int(row.group_id)) if row.group_id is not None else None
        target_payloads.append(
            {
                **row.model_dump(),
                "class_name": classroom.class_name if classroom else None,
                "group_name": group.group_name if group else None,
            }
        )

    job_payloads: list[dict] = []
    for row in await job_scopes.list_of_task(int(task.id)):
        job = await jobs.get(int(row.job_id))
        job_payloads.append({**row.model_dump(), "job_name": job.job_name if job else None})

    project_payloads: list[dict] = []
    for row in await task_projects.list_of_task(int(task.id)):
        project = await projects.get(int(row.project_id))
        project_payloads.append(
            {
                **row.model_dump(),
                "project_name": project.project_name if project else None,
                "project_level": project.project_level if project else None,
                "status": project.status if project else None,
            }
        )

    payload.update(
        {
            "targets": target_payloads,
            "jobs": job_payloads,
            "projects": project_payloads,
            "can_edit": task.status == PublishTaskStatus.PENDING.value,
        }
    )
    return payload


# --------------------------------------------------------------------- 接口


@router.get(
    "/publish-tasks",
    response_model=ApiResponse[Page[PublishTaskRead]],
    summary="发布任务分页列表（教师端）",
)
async def list_publish_tasks(
    db: DbSession,
    tasks: TaskRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="任务名或说明模糊搜索")] = None,
    status_: Annotated[
        str | None, Query(alias="status", description="PENDING / PUBLISHED / CANCELLED")
    ] = None,
    project_level: Annotated[str | None, Query(description="BASIC / ADVANCED / EXPANDED")] = None,
    creator_id: Annotated[int | None, Query(description="发布教师 ID")] = None,
) -> Page[object]:
    result = await tasks.list_tasks(
        page,
        keyword=keyword,
        status=status_,
        project_level=project_level,
        creator_id=creator_id,
    )
    task_ids = [int(item.id) for item in result.items]
    stats = await publish_service.task_stats(db, task_ids)
    items = [
        {
            **item.model_dump(),
            **stats.get(int(item.id), {}),
            "expired": item.deadline_at is not None and item.deadline_at <= now(),
        }
        for item in result.items
    ]
    return Page.build(items=items, total=result.total, params=page)


@router.post(
    "/publish-tasks",
    response_model=ApiResponse[PublishTaskDetail],
    status_code=status.HTTP_201_CREATED,
    summary="创建并下发任务（即时生效 / 定时到点生效）",
)
async def create_publish_task(
    payload: PublishTaskCreate,
    db: DbSession,
    targets: TargetRepo,
    job_scopes: JobScopeRepo,
    task_projects: TaskProjectRepo,
    classes: ClassRepo,
    groups: GroupRepo,
    jobs: JobRepo,
    projects: TrainingProjectRepo,
) -> dict:
    task = await publish_service.create_task(db, payload)
    return await _task_detail_payload(
        db,
        task,
        targets=targets,
        job_scopes=job_scopes,
        task_projects=task_projects,
        classes=classes,
        groups=groups,
        jobs=jobs,
        projects=projects,
    )


@router.post(
    "/publish-tasks/preview-students",
    response_model=ApiResponse[TargetPreviewOut],
    summary="预览目标覆盖学生数（不建任务）",
)
async def preview_publish_targets(payload: PublishTargetsIn, db: DbSession) -> dict:
    return await publish_service.preview_targets(db, payload.targets)


@router.get(
    "/publish-tasks/publishable-projects",
    response_model=ApiResponse[list[PublishableProjectRead]],
    summary="可发布项目列表（只含已发布且未下架的项目，按岗位 × 层级筛选）",
)
async def list_publishable_projects(
    db: DbSession,
    jobs: JobRepo,
    job_ids: Annotated[list[int] | None, Query(description="岗位范围，可多选；不传 = 全部岗位")] = None,
    project_level: Annotated[str | None, Query(description="BASIC / ADVANCED / EXPANDED")] = None,
    keyword: Annotated[str | None, Query(description="项目名模糊搜索")] = None,
) -> list[dict]:
    picked = await publish_service.list_publishable_projects(
        db, job_ids=job_ids or [], project_level=project_level, keyword=keyword
    )
    job_names: dict[int, str] = {}
    for project in picked:
        if project.job_id is not None and int(project.job_id) not in job_names:
            job = await jobs.get(int(project.job_id))
            if job is not None:
                job_names[int(job.id)] = job.job_name
    return [
        {
            "id": int(project.id),
            "project_name": project.project_name,
            "project_level": project.project_level,
            "difficulty": int(project.difficulty),
            "job_id": int(project.job_id) if project.job_id is not None else None,
            "job_name": job_names.get(int(project.job_id)) if project.job_id is not None else None,
            "status": project.status,
        }
        for project in picked
    ]


@router.get(
    "/publish-tasks/{task_id}",
    response_model=ApiResponse[PublishTaskDetail],
    summary="任务详情（目标班级/分组、岗位范围、项目快照、覆盖学生数）",
)
async def get_publish_task(
    task_id: int,
    db: DbSession,
    tasks: TaskRepo,
    targets: TargetRepo,
    job_scopes: JobScopeRepo,
    task_projects: TaskProjectRepo,
    classes: ClassRepo,
    groups: GroupRepo,
    jobs: JobRepo,
    projects: TrainingProjectRepo,
) -> dict:
    task = await _task_or_404(tasks, task_id)
    return await _task_detail_payload(
        db,
        task,
        targets=targets,
        job_scopes=job_scopes,
        task_projects=task_projects,
        classes=classes,
        groups=groups,
        jobs=jobs,
        projects=projects,
    )


@router.patch(
    "/publish-tasks/{task_id}",
    response_model=ApiResponse[PublishTaskDetail],
    summary="修改任务（未发布可全量改；已发布只能改说明 / 截止时间 / 备注）",
)
async def update_publish_task(
    task_id: int,
    payload: PublishTaskUpdate,
    db: DbSession,
    tasks: TaskRepo,
    targets: TargetRepo,
    job_scopes: JobScopeRepo,
    task_projects: TaskProjectRepo,
    classes: ClassRepo,
    groups: GroupRepo,
    jobs: JobRepo,
    projects: TrainingProjectRepo,
) -> dict:
    task = await _task_or_404(tasks, task_id)
    await publish_service.update_task(db, task, payload)
    return await _task_detail_payload(
        db,
        task,
        targets=targets,
        job_scopes=job_scopes,
        task_projects=task_projects,
        classes=classes,
        groups=groups,
        jobs=jobs,
        projects=projects,
    )


@router.post(
    "/publish-tasks/{task_id}/publish",
    response_model=ApiResponse[PublishTaskRead],
    summary="立即发布（定时任务也可以提前发）",
)
async def publish_task_now(
    task_id: int, db: DbSession, tasks: TaskRepo, creator_id: int | None = None
) -> dict:
    task = await _task_or_404(tasks, task_id)
    await publish_service.publish_now(db, task, operator_id=creator_id)
    return await _task_payload(db, task)


@router.post(
    "/publish-tasks/{task_id}/cancel",
    response_model=ApiResponse[PublishTaskRead],
    summary="撤回任务（学生端立刻不可见，已产生的闯关记录保留）",
)
async def cancel_publish_task(
    task_id: int, db: DbSession, tasks: TaskRepo, creator_id: int | None = None
) -> dict:
    task = await _task_or_404(tasks, task_id)
    await publish_service.cancel_task(db, task, operator_id=creator_id)
    return await _task_payload(db, task)


@router.delete(
    "/publish-tasks/{task_id}",
    response_model=ApiResponse[MessageOut],
    summary="删除任务（软删，只能删未发布过的任务）",
)
async def delete_publish_task(
    task_id: int,
    tasks: TaskRepo,
    targets: TargetRepo,
    job_scopes: JobScopeRepo,
    task_projects: TaskProjectRepo,
) -> MessageOut:
    task = await _task_or_404(tasks, task_id)
    if task.published_at is not None:
        raise BusinessRuleError("任务已经发布过，不能删除；请先撤回，记录会保留在平台上")
    await targets.delete_of_task(task_id)
    await job_scopes.delete_of_task(task_id)
    await task_projects.delete_of_task(task_id)
    await tasks.remove(task)
    return MessageOut(message="任务已删除（软删，目标与项目关联已清理）")


@router.get(
    "/students/{student_id}/tasks",
    response_model=ApiResponse[list[StudentTaskRead]],
    summary="学生端我的任务（只含发给自己的已发布任务）",
)
async def list_student_tasks(student_id: int, db: DbSession, students: UserRepo) -> list[dict]:
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    return await publish_service.visible_tasks_of_student(db, student_id)


__all__ = ["router"]
