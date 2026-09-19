"""任务下发业务规则：创建 / 发布 / 撤回、目标展开、必修名单与学生任务视图。

口径（docs/方案设计.md）：
- **学生可见 = ``training_project.status = 'PUBLISHED'``**：项目一旦发布，所有学生都能在
  「实训项目」里看到并闯关，不等任务；
- **任务 = 必修**：一条已生效（``status='PUBLISHED'``）的任务覆盖到该学生（班级 / 分组命中）后，
  这些项目对该学生就是"老师点名要求完成"的必修项目，学生端标必修、教师端按任务统计完成情况；
  必修与"平时自己刷项目"互不冲突，都记在同一份闯关记录上；
- 草稿（DRAFT）/ 已下架（OFF_SHELF）的项目既不开放闯关，**也不能**发布任务；
- 岗位范围只用于筛项目，不筛学生。
"""

from collections.abc import Sequence
from datetime import datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.time import now
from app.crud.job_skill import JobRepository
from app.crud.organization import ClassGroupRepository, ClassRepository
from app.crud.project import TrainingProjectRepository
from app.crud.publish import (
    PublishTaskJobRepository,
    PublishTaskProjectRepository,
    PublishTaskRepository,
    PublishTaskTargetRepository,
)
from app.models.enums import PublishMode, PublishTargetType, PublishTaskStatus
from app.models.organization import ClassStudent, ClassStudentGroup
from app.models.project import TrainingProject
from app.models.publish import PublishTask
from app.schemas.publish import PublishTargetIn, PublishTaskCreate, PublishTaskUpdate
from app.services import audit, notify

#: 被任务引用的项目必须处于这个状态
REQUIRED_PROJECT_STATUS = "PUBLISHED"

#: 通知的业务类型（notification.biz_type）
TASK_PUBLISH_BIZ = "TASK_PUBLISH"


def _status_label(status: str) -> str:
    return {"DRAFT": "还在草稿中", "OFF_SHELF": "已下架"}.get(status, f"状态为 {status}")


def _check_publish_mode(mode: str | None) -> str:
    value = (mode or PublishMode.IMMEDIATE.value).strip().upper()
    if value not in {item.value for item in PublishMode}:
        raise BusinessRuleError(f"发布方式只能是 IMMEDIATE（即时）或 SCHEDULED（定时），收到 {mode}")
    return value


def _check_project_level(level: str | None) -> str | None:
    if level is None or not str(level).strip():
        return None
    value = str(level).strip().upper()
    if value not in {"BASIC", "ADVANCED", "EXPANDED"}:
        raise BusinessRuleError(f"项目层级只能是 BASIC / ADVANCED / EXPANDED，收到 {level}")
    return value


# ------------------------------------------------------------------ 目标校验


def _normalize_target(item: PublishTargetIn) -> tuple[str, int, int | None]:
    target_type = (item.target_type or PublishTargetType.CLASS.value).strip().upper()
    if target_type not in {member.value for member in PublishTargetType}:
        raise BusinessRuleError(f"目标类型只能是 CLASS（全班）/ GROUP（分组），收到 {item.target_type}")
    if target_type == PublishTargetType.CLASS.value:
        if item.group_id is not None:
            raise BusinessRuleError("全班目标不用指定分组")
        return target_type, int(item.class_id), None
    if item.group_id is None:
        raise BusinessRuleError("指定分组目标必须给出 group_id")
    return target_type, int(item.class_id), int(item.group_id)


async def normalize_targets(
    session: AsyncSession, targets: Sequence[PublishTargetIn]
) -> list[tuple[str, int, int | None]]:
    """校验目标班级 / 分组是否存在、分组是否属于该班级，并去重。"""
    classes = ClassRepository(session)
    groups = ClassGroupRepository(session)
    normalized: list[tuple[str, int, int | None]] = []
    for item in targets:
        target_type, class_id, group_id = _normalize_target(item)
        if await classes.get(class_id) is None:
            raise NotFoundError(f"班级 {class_id} 不存在")
        if group_id is not None:
            group = await groups.get(group_id)
            if group is None:
                raise NotFoundError(f"分组 {group_id} 不存在")
            if int(group.class_id) != class_id:
                raise BusinessRuleError(f"分组「{group.group_name}」不属于班级 {class_id}")
        record = (target_type, class_id, group_id)
        if record not in normalized:
            normalized.append(record)
    if not normalized:
        raise BusinessRuleError("请至少选择一个目标班级")
    return normalized


async def preview_targets(session: AsyncSession, targets: Sequence[PublishTargetIn]) -> dict:
    """选完班级 / 分组后预览覆盖情况：先去重校验，再按在班状态展开学生。"""
    normalized = await normalize_targets(session, targets)
    grouped: dict[int, set[int]] = {}
    for index, (target_type, class_id, group_id) in enumerate(normalized, start=1):
        # 复用学生展开查询：临时挂到 task_id=index 上不可行，这里直接查在班学生
        student_ids = await _students_of_target(session, target_type, class_id, group_id)
        grouped[index] = set(student_ids)
    all_students: set[int] = set()
    for ids in grouped.values():
        all_students |= ids
    return {
        "student_count": len(all_students),
        "class_count": len({class_id for _, class_id, _ in normalized}),
        "group_count": len([1 for _, _, group_id in normalized if group_id is not None]),
    }


async def _students_of_target(
    session: AsyncSession, target_type: str, class_id: int, group_id: int | None
) -> list[int]:
    conditions = [ClassStudent.class_id == class_id, ClassStudent.status == "ENROLLED"]
    if target_type == PublishTargetType.GROUP.value and group_id is not None:
        stmt = (
            select(ClassStudent.student_id)
            .select_from(ClassStudent)
            .join(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)  # type: ignore[arg-type]
            .where(*conditions, ClassStudentGroup.group_id == group_id)
        )
    else:
        stmt = select(ClassStudent.student_id).select_from(ClassStudent).where(*conditions)
    return [int(student_id) for student_id in (await session.exec(stmt)).all()]


# ------------------------------------------------------------------ 项目筛选


async def validate_jobs(session: AsyncSession, job_ids: Sequence[int]) -> list[int]:
    """校验岗位存在；空列表 = 全部岗位。"""
    jobs = JobRepository(session)
    normalized: list[int] = []
    for job_id in job_ids:
        value = int(job_id)
        if value in normalized:
            continue
        if await jobs.get(value) is None:
            raise NotFoundError(f"岗位 {value} 不存在")
        normalized.append(value)
    return normalized


async def select_projects(
    session: AsyncSession,
    *,
    job_ids: Sequence[int] = (),
    project_level: str | None = None,
    project_ids: Sequence[int] = (),
) -> list[TrainingProject]:
    """挑出任务要发布的项目：手工指定优先，否则按「岗位 × 层级」筛选。

    无论哪条路径，项目都必须是 PUBLISHED，且（指定了岗位范围时）岗位要在范围内。
    """
    projects = TrainingProjectRepository(session)
    picked: list[TrainingProject] = []
    if project_ids:
        for project_id in project_ids:
            project = await projects.get(int(project_id))
            if project is None:
                raise NotFoundError(f"项目 {project_id} 不存在")
            picked.append(project)
    else:
        conditions = [
            TrainingProject.status == REQUIRED_PROJECT_STATUS,
            TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
        ]
        if job_ids:
            conditions.append(TrainingProject.job_id.in_(list(job_ids)))  # type: ignore[attr-defined]
        if project_level:
            conditions.append(TrainingProject.project_level == project_level)
        stmt = select(TrainingProject).where(*conditions).order_by(TrainingProject.id)
        picked = list((await session.exec(stmt)).all())

    unique: dict[int, TrainingProject] = {}
    for project in picked:
        unique[int(project.id)] = project
    for project in unique.values():
        if project.status != REQUIRED_PROJECT_STATUS:
            raise BusinessRuleError(
                f"《{project.project_name}》{_status_label(project.status)}，不能发布任务；"
                "请先在实训项目管理里发布该项目"
            )
        if job_ids and (project.job_id is None or int(project.job_id) not in list(job_ids)):
            raise BusinessRuleError(f"《{project.project_name}》不在本任务的岗位范围内")
    if not unique:
        raise BusinessRuleError("该岗位 + 该层级下没有已发布的项目，请先发布项目或调整筛选条件")
    return list(unique.values())


async def list_publishable_projects(
    session: AsyncSession,
    *,
    job_ids: Sequence[int] = (),
    project_level: str | None = None,
    keyword: str | None = None,
) -> list[TrainingProject]:
    """教师端勾选用的"可发布项目"：只返回已发布且未下架的项目。"""
    conditions = [
        TrainingProject.status == REQUIRED_PROJECT_STATUS,
        TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
    ]
    if job_ids:
        conditions.append(TrainingProject.job_id.in_(list(job_ids)))  # type: ignore[attr-defined]
    if project_level:
        conditions.append(TrainingProject.project_level == project_level)
    if keyword and keyword.strip():
        conditions.append(TrainingProject.project_name.like(f"%{keyword.strip()}%"))
    stmt = (
        select(TrainingProject).where(*conditions).order_by(TrainingProject.project_level, TrainingProject.id)
    )
    return list((await session.exec(stmt)).all())


# ------------------------------------------------------------------ 任务写入


async def _insert_targets(
    session: AsyncSession, task_id: int, normalized: Sequence[tuple[str, int, int | None]]
) -> None:
    targets = PublishTaskTargetRepository(session)
    for target_type, class_id, group_id in normalized:
        await targets.create(
            {
                "task_id": task_id,
                "target_type": target_type,
                "class_id": class_id,
                "group_id": group_id,
            }
        )


async def _insert_jobs(session: AsyncSession, task_id: int, job_ids: Sequence[int]) -> None:
    jobs = PublishTaskJobRepository(session)
    for job_id in job_ids:
        await jobs.create({"task_id": task_id, "job_id": int(job_id)})


async def _insert_projects(session: AsyncSession, task_id: int, projects: Sequence[TrainingProject]) -> None:
    links = PublishTaskProjectRepository(session)
    for index, project in enumerate(projects, start=1):
        await links.create({"task_id": task_id, "project_id": int(project.id), "sort_no": index})


async def create_task(session: AsyncSession, payload: PublishTaskCreate) -> PublishTask:
    """创建任务：即时发布就当场生效，定时发布留在 PENDING 等调度脚本。"""
    title = (payload.title or "").strip()
    if not title:
        raise BusinessRuleError("任务名称不能为空")
    mode = _check_publish_mode(payload.publish_mode)
    scheduled_at = payload.scheduled_at
    if mode == PublishMode.SCHEDULED.value:
        if scheduled_at is None:
            raise BusinessRuleError("定时发布必须指定发布时间")
        if scheduled_at <= now():
            raise BusinessRuleError("定时发布时间必须晚于当前时间")
    else:
        scheduled_at = None
    if payload.deadline_at is not None and payload.deadline_at <= now():
        raise BusinessRuleError("截止时间必须晚于当前时间")

    project_level = _check_project_level(payload.project_level)
    job_ids = await validate_jobs(session, payload.job_ids)
    normalized_targets = await normalize_targets(session, payload.targets)
    projects = await select_projects(
        session,
        job_ids=job_ids,
        project_level=project_level,
        project_ids=payload.project_ids,
    )

    task = await PublishTaskRepository(session).create(
        {
            "title": title,
            "description": payload.description,
            "project_level": project_level,
            "publish_mode": mode,
            "scheduled_at": scheduled_at,
            "deadline_at": payload.deadline_at,
            "status": PublishTaskStatus.PENDING.value,
            "creator_id": payload.creator_id,
            "remark": payload.remark,
        }
    )
    await _insert_targets(session, int(task.id), normalized_targets)
    await _insert_jobs(session, int(task.id), job_ids)
    await _insert_projects(session, int(task.id), projects)

    if mode == PublishMode.IMMEDIATE.value:
        await _publish(session, task, at=now(), operator_id=payload.creator_id, projects=projects)
    else:
        await audit.record(
            session,
            module="PUBLISH",
            action="SCHEDULE",
            target_type="publish_task",
            target_id=int(task.id),
            detail={
                "title": task.title,
                "project_count": len(projects),
                "student_count": len(await PublishTaskRepository(session).student_ids_of_task(int(task.id))),
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
            },
            operator_id=payload.creator_id,
        )
    return task


async def update_task(session: AsyncSession, task: PublishTask, payload: PublishTaskUpdate) -> PublishTask:
    """改任务：未发布（PENDING）可全量改；已发布只允许改说明 / 截止时间 / 备注。"""
    data = payload.model_dump(exclude_unset=True)
    locked_fields = [key for key in data if key not in {"description", "deadline_at", "remark"}]
    if task.status != PublishTaskStatus.PENDING.value and locked_fields:
        raise BusinessRuleError(
            "任务已发布或已撤回，目标 / 项目 / 岗位 / 层次已锁定；要改请撤回后新建一条任务"
        )

    updates: dict = {}
    if "description" in data:
        updates["description"] = data["description"]
    if "deadline_at" in data:
        deadline = data["deadline_at"]
        if deadline is not None and deadline <= now():
            raise BusinessRuleError("截止时间必须晚于当前时间")
        updates["deadline_at"] = deadline
    if "remark" in data:
        updates["remark"] = data["remark"]

    if task.status == PublishTaskStatus.PENDING.value:
        if "title" in data:
            title = (data["title"] or "").strip()
            if not title:
                raise BusinessRuleError("任务名称不能为空")
            updates["title"] = title
        mode = (
            _check_publish_mode(data.get("publish_mode", task.publish_mode))
            if ("publish_mode" in data or "scheduled_at" in data)
            else task.publish_mode
        )
        scheduled_at = data.get("scheduled_at", task.scheduled_at)
        if mode == PublishMode.SCHEDULED.value:
            if scheduled_at is None:
                raise BusinessRuleError("定时发布必须指定发布时间")
            if scheduled_at <= now():
                raise BusinessRuleError("定时发布时间必须晚于当前时间")
        else:
            scheduled_at = None
        updates["publish_mode"] = mode
        updates["scheduled_at"] = scheduled_at

        if any(key in data for key in ("project_level", "job_ids", "project_ids", "targets")):
            project_level = _check_project_level(data.get("project_level", task.project_level))
            job_ids = await validate_jobs(session, data.get("job_ids") or [])
            projects = await select_projects(
                session,
                job_ids=job_ids,
                project_level=project_level,
                project_ids=data.get("project_ids") or [],
            )
            normalized_targets = await normalize_targets(session, data.get("targets") or [])
            task_id = int(task.id)
            await PublishTaskTargetRepository(session).delete_of_task(task_id)
            await PublishTaskJobRepository(session).delete_of_task(task_id)
            await PublishTaskProjectRepository(session).delete_of_task(task_id)
            await _insert_targets(session, task_id, normalized_targets)
            await _insert_jobs(session, task_id, job_ids)
            await _insert_projects(session, task_id, projects)
            updates["project_level"] = project_level

    updated = await PublishTaskRepository(session).update(task, updates)
    await audit.record(
        session,
        module="PUBLISH",
        action="UPDATE",
        target_type="publish_task",
        target_id=int(task.id),
        detail={"fields": sorted(updates)},
        operator_id=task.creator_id,
    )
    return updated


async def _load_project_links(session: AsyncSession, task_id: int) -> list[TrainingProject]:
    """任务快照里仍然处于 PUBLISHED 的项目（跳过被删掉的）。"""
    links = await PublishTaskProjectRepository(session).list_of_task(task_id)
    projects = TrainingProjectRepository(session)
    result: list[TrainingProject] = []
    for link in links:
        project = await projects.get(int(link.project_id))
        if project is None:
            continue
        result.append(project)
    return result


async def _publish(
    session: AsyncSession,
    task: PublishTask,
    *,
    at: datetime,
    operator_id: int | None,
    projects: Sequence[TrainingProject] | None = None,
) -> PublishTask:
    """把任务置为已发布：校验项目、给目标学生发通知、写审计。"""
    current = list(projects) if projects is not None else await _load_project_links(session, int(task.id))
    invalid = [project for project in current if project.status != REQUIRED_PROJECT_STATUS]
    if invalid:
        names = "、".join(f"《{project.project_name}》" for project in invalid[:3])
        raise BusinessRuleError(f"{names} 不在已发布状态，不能发布任务；请先发布或撤回这些项目")
    if not current:
        raise BusinessRuleError("任务里已经没有可发布的项目")

    task.status = PublishTaskStatus.PUBLISHED.value
    task.published_at = at
    await session.flush()

    student_ids = await PublishTaskRepository(session).student_ids_of_task(int(task.id))
    if student_ids:
        deadline = f"，{task.deadline_at:%Y-%m-%d %H:%M} 前完成" if task.deadline_at else ""
        await notify.notify_many(
            session,
            recipient_user_ids=student_ids,
            title=f"新任务：{task.title}",
            content=f"共 {len(current)} 个实训项目{deadline}",
            biz_type=TASK_PUBLISH_BIZ,
            biz_id=int(task.id),
        )
    await audit.record(
        session,
        module="PUBLISH",
        action="PUBLISH",
        target_type="publish_task",
        target_id=int(task.id),
        detail={
            "title": task.title,
            "publish_mode": task.publish_mode,
            "project_count": len(current),
            "student_count": len(student_ids),
        },
        operator_id=operator_id,
    )
    return task


async def publish_now(
    session: AsyncSession, task: PublishTask, *, operator_id: int | None = None
) -> PublishTask:
    """立即发布（定时任务也可以提前发）。"""
    if task.status == PublishTaskStatus.PUBLISHED.value:
        raise ConflictError("任务已经发布，不用重复发布")
    if task.status == PublishTaskStatus.CANCELLED.value:
        raise BusinessRuleError("任务已撤回，不能重新发布；请新建一条任务")
    return await _publish(session, task, at=now(), operator_id=operator_id or task.creator_id)


async def cancel_task(
    session: AsyncSession, task: PublishTask, *, operator_id: int | None = None
) -> PublishTask:
    """撤回任务：学生端立刻不可见；已经产生的闯关记录保留。"""
    if task.status == PublishTaskStatus.CANCELLED.value:
        return task
    was_published = task.published_at is not None
    task.status = PublishTaskStatus.CANCELLED.value
    task.cancelled_at = now()
    await session.flush()

    if was_published:
        student_ids = await PublishTaskRepository(session).student_ids_of_task(int(task.id))
        if student_ids:
            await notify.notify_many(
                session,
                recipient_user_ids=student_ids,
                title=f"任务已撤回：{task.title}",
                content="该任务不再需要完成；已完成的部分会保留",
                biz_type=TASK_PUBLISH_BIZ,
                biz_id=int(task.id),
                dedupe=False,  # 撤回是一次性状态流转，允许与"发布通知"并存
            )
    await audit.record(
        session,
        module="PUBLISH",
        action="CANCEL",
        target_type="publish_task",
        target_id=int(task.id),
        detail={"title": task.title, "was_published": was_published},
        operator_id=operator_id or task.creator_id,
    )
    return task


async def dispatch_due_tasks(
    session: AsyncSession,
    *,
    at: datetime | None = None,
    task_id: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """把到点的定时任务翻成已发布（cron 脚本调用）。

    发布前逐个复核项目状态：已被下架 / 改回草稿的项目从任务里剔除；
    项目被剔空的任务整体留在 PENDING，并写一条 SKIP 审计，等教师处理。
    """
    moment = at or now()
    tasks = await PublishTaskRepository(session).due_tasks(moment, task_id=task_id)
    results: list[dict] = []
    for task in tasks:
        projects = await _load_project_links(session, int(task.id))
        valid = [project for project in projects if project.status == REQUIRED_PROJECT_STATUS]
        dropped = [project for project in projects if project.status != REQUIRED_PROJECT_STATUS]
        if not valid:
            await audit.record(
                session,
                module="PUBLISH",
                action="SKIP",
                target_type="publish_task",
                target_id=int(task.id),
                detail={
                    "reason": "任务里的项目都不在已发布状态",
                    "dropped": [project.project_name for project in dropped],
                },
                operator_id=task.creator_id,
            )
            results.append({"task": task, "published": False, "reason": "任务里已没有可发布的项目"})
            continue
        if dropped:
            links = PublishTaskProjectRepository(session)
            for project in dropped:
                for link in await links.list_of_task(int(task.id)):
                    if int(link.project_id) == int(project.id):
                        await links.remove(link)  # type: ignore[attr-defined]
            await audit.record(
                session,
                module="PUBLISH",
                action="DROP_PROJECTS",
                target_type="publish_task",
                target_id=int(task.id),
                detail={"dropped": [project.project_name for project in dropped]},
                operator_id=task.creator_id,
            )
        if dry_run:
            results.append({"task": task, "published": False, "reason": "dry-run，未改动"})
            continue
        await _publish(session, task, at=moment, operator_id=task.creator_id, projects=valid)
        results.append({"task": task, "published": True, "reason": ""})
    return results


# ------------------------------------------------------------ 项目开放与必修


async def ensure_project_published(session: AsyncSession, project_id: int) -> TrainingProject:
    """学生开始闯关前的校验：项目必须存在且处于已发布状态。

    学生端能看到全部已发布项目（不再要求"先发任务"），草稿 / 已下架的不开放闯关。
    """
    project = await TrainingProjectRepository(session).get(project_id)
    if project is None:
        raise NotFoundError(f"实训项目 {project_id} 不存在")
    if project.status != REQUIRED_PROJECT_STATUS:
        raise BusinessRuleError(f"《{project.project_name}》还没发布，暂时不能闯关，请联系老师")
    return project


async def visible_tasks_of_student(session: AsyncSession, student_id: int) -> list[dict]:
    """学生端"我的任务"：任务 + 它包含的（已发布）项目。"""
    tasks = await PublishTaskRepository(session).visible_tasks_of_student(student_id)
    links = PublishTaskProjectRepository(session)
    projects_repo = TrainingProjectRepository(session)
    payloads: list[dict] = []
    for task in tasks:
        items: list[dict] = []
        for link in await links.list_of_task(int(task.id)):
            project = await projects_repo.get(int(link.project_id))
            if project is None or project.status != REQUIRED_PROJECT_STATUS:
                continue
            items.append(
                {
                    "id": int(link.id),
                    "task_id": int(task.id),
                    "project_id": int(project.id),
                    "sort_no": int(link.sort_no),
                    "created_at": link.created_at,
                    "project_name": project.project_name,
                    "project_level": project.project_level,
                    "status": project.status,
                }
            )
        payloads.append(
            {
                "id": int(task.id),
                "title": task.title,
                "description": task.description,
                "project_level": task.project_level,
                "published_at": task.published_at,
                "deadline_at": task.deadline_at,
                "expired": task.deadline_at is not None and task.deadline_at <= now(),
                "projects": items,
            }
        )
    return payloads


async def task_stats(session: AsyncSession, task_ids: Sequence[int]) -> dict[int, dict]:
    """列表 / 详情用的统计：项目数、目标数、覆盖学生数。"""
    tasks = PublishTaskRepository(session)
    project_counts = await tasks.project_counts(task_ids)
    target_counts = await tasks.target_counts(task_ids)
    student_counts = await tasks.student_ids_of_tasks(task_ids)
    return {
        int(task_id): {
            "project_count": int(project_counts.get(int(task_id), 0)),
            "target_count": int(target_counts.get(int(task_id), 0)),
            "student_count": len(student_counts.get(int(task_id), set())),
        }
        for task_id in task_ids
    }


__all__ = [
    "REQUIRED_PROJECT_STATUS",
    "TASK_PUBLISH_BIZ",
    "cancel_task",
    "create_task",
    "dispatch_due_tasks",
    "ensure_project_published",
    "list_publishable_projects",
    "normalize_targets",
    "preview_targets",
    "publish_now",
    "select_projects",
    "task_stats",
    "update_task",
    "validate_jobs",
    "visible_tasks_of_student",
]
