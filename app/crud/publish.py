"""任务下发仓储：发布任务、目标班级/分组、岗位范围、项目快照。

学生可见性的判定口径见 ``app/services/publish.py`` 与 ``docs/方案设计.md`` §4.2，
底层查询都收在这里。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlmodel import and_, delete, func, or_, select

from app.crud.base import BaseRepository
from app.models.organization import ClassInfo, ClassStudent, ClassStudentGroup
from app.models.publish import (
    PublishTask,
    PublishTaskJob,
    PublishTaskProject,
    PublishTaskTarget,
)
from app.schemas.base import Page, PageParams


def _target_matches_student() -> Any:
    """目标命中学生的条件：全班目标直接命中，"GROUP"目标要比对学生的当前分组。"""
    return or_(
        PublishTaskTarget.target_type == "CLASS",
        and_(
            PublishTaskTarget.target_type == "GROUP",
            ClassStudentGroup.group_id == PublishTaskTarget.group_id,
        ),
    )


class PublishTaskRepository(BaseRepository[PublishTask]):
    """发布任务仓储，软删表。"""

    model = PublishTask
    soft_delete = True

    async def list_tasks(
        self,
        params: PageParams,
        *,
        keyword: str | None = None,
        status: str | None = None,
        project_level: str | None = None,
        creator_id: int | None = None,
    ) -> Page[Any]:
        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(or_(PublishTask.title.like(pattern), PublishTask.description.like(pattern)))
        if status:
            filters.append(PublishTask.status == status)
        if project_level:
            filters.append(PublishTask.project_level == project_level)
        if creator_id is not None:
            filters.append(PublishTask.creator_id == creator_id)
        return await self.list_page(params, *filters)

    async def due_tasks(self, at: datetime, *, task_id: int | None = None) -> list[PublishTask]:
        """到点待发布的定时任务（调度脚本用）。"""
        stmt = select(PublishTask).where(
            PublishTask.status == "PENDING",
            PublishTask.scheduled_at.is_not(None),  # type: ignore[attr-defined]
            PublishTask.scheduled_at <= at,
            PublishTask.deleted_at.is_(None),  # type: ignore[attr-defined]
        )
        if task_id is not None:
            stmt = stmt.where(PublishTask.id == task_id)
        return list((await self.session.exec(stmt.order_by(PublishTask.id))).all())

    async def list_of_class(self, class_id: int) -> list[PublishTask]:
        """目标命中该班级的任务：全班目标，或该班某个分组目标的都算（按生效时间倒序）。"""
        stmt = (
            select(PublishTask)
            .join(PublishTaskTarget, PublishTaskTarget.task_id == PublishTask.id)  # type: ignore[arg-type]
            .where(
                PublishTaskTarget.class_id == class_id,
                PublishTask.deleted_at.is_(None),  # type: ignore[attr-defined]
            )
            .distinct()
            .order_by(PublishTask.id.desc())
        )
        return list((await self.session.exec(stmt)).all())

    # --------------------------------------------------------------- 统计
    async def project_counts(self, task_ids: Sequence[int]) -> dict[int, int]:
        if not task_ids:
            return {}
        stmt = (
            select(PublishTaskProject.task_id, func.count())
            .where(PublishTaskProject.task_id.in_(list(task_ids)))  # type: ignore[attr-defined]
            .group_by(PublishTaskProject.task_id)
        )
        return {int(task_id): int(count) for task_id, count in (await self.session.exec(stmt)).all()}

    async def target_counts(self, task_ids: Sequence[int]) -> dict[int, int]:
        if not task_ids:
            return {}
        stmt = (
            select(PublishTaskTarget.task_id, func.count())
            .where(PublishTaskTarget.task_id.in_(list(task_ids)))  # type: ignore[attr-defined]
            .group_by(PublishTaskTarget.task_id)
        )
        return {int(task_id): int(count) for task_id, count in (await self.session.exec(stmt)).all()}

    async def student_ids_of_task(self, task_id: int) -> list[int]:
        """任务覆盖的学生（按目标班级 / 分组展开，只看在班学生）。"""
        grouped = await self.student_ids_of_tasks([task_id])
        return sorted(grouped.get(int(task_id), set()))

    async def student_ids_of_tasks(self, task_ids: Sequence[int]) -> dict[int, set[int]]:
        """批量展开任务覆盖的学生：``{task_id: {student_id}}``。"""
        if not task_ids:
            return {}
        stmt = (
            select(PublishTaskTarget.task_id, ClassStudent.student_id)
            .select_from(PublishTaskTarget)
            .join(ClassInfo, ClassInfo.id == PublishTaskTarget.class_id)  # type: ignore[arg-type]
            .join(
                ClassStudent,
                and_(
                    ClassStudent.class_id == PublishTaskTarget.class_id,
                    ClassStudent.status == "ENROLLED",
                ),
            )
            .outerjoin(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)
            .where(
                PublishTaskTarget.task_id.in_(list(task_ids)),  # type: ignore[attr-defined]
                ClassInfo.deleted_at.is_(None),  # type: ignore[attr-defined]
                _target_matches_student(),
            )
            .distinct()
        )
        grouped: dict[int, set[int]] = {}
        for task_id, student_id in (await self.session.exec(stmt)).all():
            grouped.setdefault(int(task_id), set()).add(int(student_id))
        return grouped

    # ----------------------------------------------------- 学生可见性查询
    async def visible_project_ids_of_student(self, student_id: int) -> set[int]:
        """任务"点名要求该学生完成"的项目 ID（= 必修项目）。

        注意：这个集合**不再决定学生能不能看到项目**（学生端能看到全部已发布项目），
        它代表的是"老师发任务点名要求做"的那批项目（口径见 docs/方案设计.md §4.2）。
        """
        stmt = (
            select(PublishTaskProject.project_id)
            .select_from(PublishTaskProject)
            .join(PublishTask, PublishTask.id == PublishTaskProject.task_id)  # type: ignore[arg-type]
            .join(PublishTaskTarget, PublishTaskTarget.task_id == PublishTask.id)  # type: ignore[arg-type]
            .join(ClassInfo, ClassInfo.id == PublishTaskTarget.class_id)  # type: ignore[arg-type]
            .join(
                ClassStudent,
                and_(
                    ClassStudent.class_id == PublishTaskTarget.class_id,
                    ClassStudent.student_id == student_id,
                    ClassStudent.status == "ENROLLED",
                ),
            )
            .outerjoin(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)
            .where(
                PublishTask.status == "PUBLISHED",
                PublishTask.deleted_at.is_(None),  # type: ignore[attr-defined]
                ClassInfo.deleted_at.is_(None),  # type: ignore[attr-defined]
                _target_matches_student(),
            )
            .distinct()
        )
        return {int(project_id) for project_id in (await self.session.exec(stmt)).all()}

    async def required_projects_of_student(self, student_id: int) -> dict[int, list[dict[str, Any]]]:
        """任务点名给该学生的项目 → 覆盖它的已生效任务。

        返回 ``{project_id: [{task_id, task_title, deadline_at}]}``。
        学生端项目列表 / 详情用它标"必修"（一个项目可能被多条任务同时点名）。
        """
        stmt = (
            select(
                PublishTaskProject.project_id,
                PublishTask.id,
                PublishTask.title,
                PublishTask.deadline_at,
            )
            .select_from(PublishTask)
            .join(PublishTaskProject, PublishTaskProject.task_id == PublishTask.id)  # type: ignore[arg-type]
            .join(PublishTaskTarget, PublishTaskTarget.task_id == PublishTask.id)  # type: ignore[arg-type]
            .join(ClassInfo, ClassInfo.id == PublishTaskTarget.class_id)  # type: ignore[arg-type]
            .join(
                ClassStudent,
                and_(
                    ClassStudent.class_id == PublishTaskTarget.class_id,
                    ClassStudent.student_id == student_id,
                    ClassStudent.status == "ENROLLED",
                ),
            )
            .outerjoin(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)
            .where(
                PublishTask.status == "PUBLISHED",
                PublishTask.deleted_at.is_(None),  # type: ignore[attr-defined]
                ClassInfo.deleted_at.is_(None),  # type: ignore[attr-defined]
                _target_matches_student(),
            )
            .distinct()
            .order_by(PublishTaskProject.project_id, PublishTask.id)
        )
        grouped: dict[int, list[dict[str, Any]]] = {}
        for project_id, task_id, title, deadline_at in (await self.session.exec(stmt)).all():
            grouped.setdefault(int(project_id), []).append(
                {"task_id": int(task_id), "task_title": title, "deadline_at": deadline_at}
            )
        return grouped

    async def visible_tasks_of_student(self, student_id: int) -> list[PublishTask]:
        """学生可见的已发布任务（按生效时间倒序）。"""
        stmt = (
            select(PublishTask)
            .select_from(PublishTask)
            .join(PublishTaskTarget, PublishTaskTarget.task_id == PublishTask.id)  # type: ignore[arg-type]
            .join(ClassInfo, ClassInfo.id == PublishTaskTarget.class_id)  # type: ignore[arg-type]
            .join(
                ClassStudent,
                and_(
                    ClassStudent.class_id == PublishTaskTarget.class_id,
                    ClassStudent.student_id == student_id,
                    ClassStudent.status == "ENROLLED",
                ),
            )
            .outerjoin(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)
            .where(
                PublishTask.status == "PUBLISHED",
                PublishTask.deleted_at.is_(None),  # type: ignore[attr-defined]
                ClassInfo.deleted_at.is_(None),  # type: ignore[attr-defined]
                _target_matches_student(),
            )
            .distinct()
            .order_by(PublishTask.published_at.desc(), PublishTask.id.desc())  # type: ignore[union-attr]
        )
        return list((await self.session.exec(stmt)).all())


class PublishTaskTargetRepository(BaseRepository[PublishTaskTarget]):
    """任务目标仓储。"""

    model = PublishTaskTarget

    async def list_of_task(self, task_id: int) -> list[PublishTaskTarget]:
        stmt = (
            select(PublishTaskTarget)
            .where(PublishTaskTarget.task_id == task_id)
            .order_by(PublishTaskTarget.class_id, PublishTaskTarget.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def list_of_task_class(self, task_id: int, class_id: int) -> list[PublishTaskTarget]:
        """某条任务在该班级上的目标记录（一条"全班"或若干条"分组"）。"""
        stmt = select(PublishTaskTarget).where(
            PublishTaskTarget.task_id == task_id, PublishTaskTarget.class_id == class_id
        )
        return list((await self.session.exec(stmt)).all())

    async def delete_of_task(self, task_id: int) -> None:
        await self.session.exec(delete(PublishTaskTarget).where(PublishTaskTarget.task_id == task_id))
        await self.session.flush()


class PublishTaskJobRepository(BaseRepository[PublishTaskJob]):
    """任务岗位范围仓储（只用于筛项目）。"""

    model = PublishTaskJob

    async def list_of_task(self, task_id: int) -> list[PublishTaskJob]:
        stmt = select(PublishTaskJob).where(PublishTaskJob.task_id == task_id).order_by(PublishTaskJob.id)
        return list((await self.session.exec(stmt)).all())

    async def job_ids_of_task(self, task_id: int) -> list[int]:
        stmt = select(PublishTaskJob.job_id).where(PublishTaskJob.task_id == task_id)
        return [int(job_id) for job_id in (await self.session.exec(stmt)).all()]

    async def delete_of_task(self, task_id: int) -> None:
        await self.session.exec(delete(PublishTaskJob).where(PublishTaskJob.task_id == task_id))
        await self.session.flush()


class PublishTaskProjectRepository(BaseRepository[PublishTaskProject]):
    """任务项目快照仓储。"""

    model = PublishTaskProject

    async def list_of_task(self, task_id: int) -> list[PublishTaskProject]:
        stmt = (
            select(PublishTaskProject)
            .where(PublishTaskProject.task_id == task_id)
            .order_by(PublishTaskProject.sort_no, PublishTaskProject.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def project_ids_of_task(self, task_id: int) -> list[int]:
        stmt = (
            select(PublishTaskProject.project_id)
            .where(PublishTaskProject.task_id == task_id)
            .order_by(PublishTaskProject.sort_no, PublishTaskProject.id)
        )
        return [int(project_id) for project_id in (await self.session.exec(stmt)).all()]

    async def delete_of_task(self, task_id: int) -> None:
        await self.session.exec(delete(PublishTaskProject).where(PublishTaskProject.task_id == task_id))
        await self.session.flush()


__all__ = [
    "PublishTaskJobRepository",
    "PublishTaskProjectRepository",
    "PublishTaskRepository",
    "PublishTaskTargetRepository",
]
