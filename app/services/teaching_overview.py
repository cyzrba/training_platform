"""教师工作台：任教班级总览（班级 → 分组 → 任务完成进度），只读派生、不落库。

口径（与接口文档 `docs/教师端接口文档.md` §5 一致）：

- **任教班级** = ``class_info.head_teacher_id`` 指向该教师、且未软删的班级（归档班也返回，带 status）；
- **分组情况** = ``class_group`` + ``class_student_group``；**没分组的学生单独列在
  ``ungrouped_students``**，否则教师会以为人少了；
- **任务命中该班** = 任务的目标里有"全班目标"或"该班某个分组目标"（``publish_task_target``），
  待发布 / 已撤回的任务也列出来，但只有 PUBLISHED 的任务进平均完成率；
- **任务完成进度**：
  ``应完成对数 = 任务在本班覆盖的学生数 × 任务项目数``；
  ``已完成`` = 这些 (学生, 项目) 组合里 ``student_project.completed_at`` 有值（与技能进度的
  "完成"同源）；``进行中`` = 有实训记录但还没完成；``未开始`` = 连记录都没有；
  ``完成率 = 已完成对数 ÷ 应完成对数 × 100``；
- **平均完成率**：班级 = 该班已发布任务完成率的**算术平均**；顶层 = 各班级平均完成率的算术平均。
"""

from collections.abc import Sequence

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.time import now
from app.crud.account import UserRepository
from app.crud.attempt import StudentProjectRepository
from app.crud.organization import (
    ClassGroupRepository,
    ClassRepository,
    ClassStudentRepository,
)
from app.crud.publish import (
    PublishTaskProjectRepository,
    PublishTaskRepository,
    PublishTaskTargetRepository,
)
from app.models.organization import ClassInfo
from app.models.project import TrainingProject
from app.models.publish import PublishTask

#: 能"任教"的用户类型
TEACHER_USER_TYPES = ("TEACHER", "ADMIN")

#: 进平均完成率统计的任务状态
COUNTED_TASK_STATUS = "PUBLISHED"


def _rate(completed: int, total: int) -> float:
    """完成率（0~100，保留两位小数）。"""
    if total <= 0:
        return 0.0
    return round(completed * 100 / total, 2)


def _average(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _member(row: dict) -> dict:
    """花名册一行 → 分组视图里的成员结构（与 /classes/{id}/groups 的成员结构保持一致）。"""
    return {
        "class_student_id": row["class_student_id"],
        "student_id": row["student_id"],
        "user_no": row["user_no"],
        "real_name": row["real_name"],
        "major_name": row["major_name"],
    }


async def _project_names(session: AsyncSession, project_ids: Sequence[int]) -> list[str]:
    if not project_ids:
        return []
    stmt = select(TrainingProject.id, TrainingProject.project_name).where(
        TrainingProject.id.in_(list(project_ids))  # type: ignore[attr-defined]
    )
    names = {int(project_id): project_name for project_id, project_name in (await session.exec(stmt)).all()}
    return [names[project_id] for project_id in project_ids if project_id in names]


async def _target_students(
    session: AsyncSession,
    task_id: int,
    class_id: int,
    roster: list[dict],
) -> dict[int, dict]:
    """任务在本班覆盖的学生：全班目标命中所有人，分组目标命中该组成员。"""
    targets = await PublishTaskTargetRepository(session).list_of_task_class(task_id, class_id)
    picked: dict[int, dict] = {}
    for target in targets:
        if target.target_type == "CLASS":
            for row in roster:
                picked[row["student_id"]] = row
            continue
        if target.group_id is None:
            continue
        for row in roster:
            if row["group_id"] == int(target.group_id):
                picked[row["student_id"]] = row
    return picked


async def _task_progress(session: AsyncSession, task: PublishTask, class_id: int, roster: list[dict]) -> dict:
    """一条任务在本班的完成进度（含逐个学生的明细）。"""
    task_id = int(task.id)
    students = await _target_students(session, task_id, class_id, roster)
    project_ids = await PublishTaskProjectRepository(session).project_ids_of_task(task_id)
    project_names = await _project_names(session, project_ids)
    records = await StudentProjectRepository(session).pairs_of(list(students), project_ids)

    rows: list[dict] = []
    completed_total = in_progress_total = not_started_total = 0
    for row in sorted(students.values(), key=lambda item: (item["user_no"], item["student_id"])):
        done = started = 0
        for project_id in project_ids:
            record = records.get((row["student_id"], project_id))
            if record is None:
                not_started_total += 1
            elif record.completed_at is not None:
                completed_total += 1
                done += 1
                started += 1
            else:
                in_progress_total += 1
                started += 1
        total = len(project_ids)
        if total and done == total:
            status = "COMPLETED"
        elif started:
            status = "IN_PROGRESS"
        else:
            status = "NOT_STARTED"
        rows.append(
            {
                "student_id": row["student_id"],
                "user_no": row["user_no"],
                "real_name": row["real_name"],
                "class_student_id": row["class_student_id"],
                "group_id": row["group_id"],
                "group_name": row["group_name"],
                "completed_count": done,
                "total_count": total,
                "completion_rate": _rate(done, total),
                "status": status,
            }
        )

    total_count = len(students) * len(project_ids)
    return {
        "task_id": task_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "effective": task.status == COUNTED_TASK_STATUS,
        "publish_mode": task.publish_mode,
        "published_at": task.published_at,
        "scheduled_at": task.scheduled_at,
        "deadline_at": task.deadline_at,
        "project_count": len(project_ids),
        "project_names": project_names,
        "student_count": len(students),
        "total_count": total_count,
        "completed_count": completed_total,
        "in_progress_count": in_progress_total,
        "not_started_count": not_started_total,
        "completion_rate": _rate(completed_total, total_count),
        "students": rows,
    }


async def _class_payload(session: AsyncSession, classroom: ClassInfo, *, with_task_students: bool) -> dict:
    class_id = int(classroom.id)
    groups = await ClassGroupRepository(session).list_by_class(class_id)
    roster = await ClassStudentRepository(session).roster(class_id)

    roster_by_group: dict[int, list[dict]] = {}
    for row in roster:
        if row["group_id"] is not None:
            roster_by_group.setdefault(int(row["group_id"]), []).append(row)

    tasks = await PublishTaskRepository(session).list_of_class(class_id)
    task_payloads = [await _task_progress(session, task, class_id, roster) for task in tasks]
    if not with_task_students:
        for payload in task_payloads:
            payload["students"] = []

    published_rates = [
        payload["completion_rate"] for payload in task_payloads if payload["status"] == COUNTED_TASK_STATUS
    ]
    ungrouped = [row for row in roster if row["group_id"] is None]
    return {
        "class_id": class_id,
        "class_name": classroom.class_name,
        "grade_year": classroom.grade_year,
        "status": classroom.status,
        "remark": classroom.remark,
        "student_count": len(roster),
        "group_count": len(groups),
        "ungrouped_count": len(ungrouped),
        "groups": [
            {
                "group_id": int(group.id),
                "group_no": group.group_no,
                "group_name": group.group_name,
                "member_count": len(roster_by_group.get(int(group.id), [])),
                "members": [_member(row) for row in roster_by_group.get(int(group.id), [])],
            }
            for group in groups
        ],
        "ungrouped_students": [_member(row) for row in ungrouped],
        "task_count": len(task_payloads),
        "average_completion_rate": _average(published_rates),
        "tasks": task_payloads,
    }


async def teacher_class_overview(
    session: AsyncSession, teacher_id: int, *, with_task_students: bool = True
) -> dict:
    """教师工作台总览：我任教的班级 + 分组明细 + 任务完成进度。

    ``with_task_students=False`` 时每个任务不带逐学生明细，只给汇总数字（班级多、人多的场景省流量）。
    """
    teacher = await UserRepository(session).get(teacher_id)
    if teacher is None:
        raise NotFoundError(f"用户 {teacher_id} 不存在")
    if teacher.user_type not in TEACHER_USER_TYPES:
        raise BusinessRuleError(f"{teacher.real_name} 是{teacher.user_type}，没有任教的班级")

    classrooms = await ClassRepository(session).list_of_teacher(teacher_id)
    class_payloads = [
        await _class_payload(session, classroom, with_task_students=with_task_students)
        for classroom in classrooms
    ]
    task_ids: set[int] = set()
    for payload in class_payloads:
        task_ids |= {task["task_id"] for task in payload["tasks"]}
    return {
        "teacher_id": int(teacher.id),
        "teacher_name": teacher.real_name,
        "class_count": len(class_payloads),
        "student_count": sum(payload["student_count"] for payload in class_payloads),
        "task_count": len(task_ids),
        "average_completion_rate": _average(
            [payload["average_completion_rate"] for payload in class_payloads]
        ),
        "generated_at": now(),
        "classes": class_payloads,
    }


__all__ = ["teacher_class_overview"]
