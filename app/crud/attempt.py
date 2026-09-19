"""闯关过程仓储：学生实训记录、闯关轮次、模块作答、整单提交。"""

from collections.abc import Sequence
from typing import Any

from sqlmodel import func, or_, select

from app.core.time import now
from app.crud.base import BaseRepository
from app.models.attempt import (
    AttemptStage,
    AttemptStageFile,
    FileAsset,
    ProjectSubmission,
    StudentProject,
    StudentProjectPick,
    TrainingAttempt,
)
from app.models.project import TrainingProject
from app.schemas.base import Page, PageParams


class StudentProjectRepository(BaseRepository[StudentProject]):
    """学生实训记录：一个学生 × 一个项目一条。"""

    model = StudentProject

    async def by_student_project(self, student_id: int, project_id: int) -> StudentProject | None:
        return await self.get_by(student_id=student_id, project_id=project_id)

    async def pairs_of(
        self, student_ids: Sequence[int], project_ids: Sequence[int]
    ) -> dict[tuple[int, int], StudentProject]:
        """批量取"学生 × 项目"的实训记录，键是 ``(student_id, project_id)``。

        教师工作台统计任务完成度时，一次把这批记录捞回来，避免按学生对逐条查。
        """
        students = list(dict.fromkeys(int(sid) for sid in student_ids))
        projects = list(dict.fromkeys(int(pid) for pid in project_ids))
        if not students or not projects:
            return {}
        stmt = select(StudentProject).where(
            StudentProject.student_id.in_(students),  # type: ignore[attr-defined]
            StudentProject.project_id.in_(projects),  # type: ignore[attr-defined]
        )
        return {
            (int(record.student_id), int(record.project_id)): record
            for record in (await self.session.exec(stmt)).all()
        }

    async def list_records(
        self,
        params: PageParams,
        *,
        student_id: int | None = None,
        project_id: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
    ) -> Page[Any]:
        conditions: list[Any] = []
        if student_id is not None:
            conditions.append(StudentProject.student_id == student_id)
        if project_id is not None:
            conditions.append(StudentProject.project_id == project_id)
        if status:
            conditions.append(StudentProject.status == status)
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            conditions.append(TrainingProject.project_name.like(pattern))

        stmt = (
            select(StudentProject, TrainingProject)
            .join(TrainingProject, TrainingProject.id == StudentProject.project_id)  # type: ignore[arg-type]
            .where(*conditions)
            .order_by(StudentProject.id.desc())  # type: ignore[attr-defined]
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
        count_stmt = (
            select(func.count())
            .select_from(StudentProject)
            .join(TrainingProject, TrainingProject.id == StudentProject.project_id)  # type: ignore[arg-type]
            .where(*conditions)
        )
        rows = (await self.session.exec(stmt)).all()
        total = int((await self.session.exec(count_stmt)).one())
        items = [
            {
                **record.model_dump(),
                "project_name": project.project_name,
                "project_level": project.project_level,
            }
            for record, project in rows
        ]
        return Page.build(items=items, total=total, params=params)


class StudentProjectPickRepository(BaseRepository[StudentProjectPick]):
    """「我的实训」清单仓储：学生自己挑的项目（关系表，移除即删行）。"""

    model = StudentProjectPick

    async def list_of_student(self, student_id: int) -> list[StudentProjectPick]:
        stmt = (
            select(StudentProjectPick)
            .where(StudentProjectPick.student_id == student_id)
            .order_by(StudentProjectPick.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def by_student_project(self, student_id: int, project_id: int) -> StudentProjectPick | None:
        return await self.get_by(student_id=student_id, project_id=project_id)

    async def add_projects(self, student_id: int, project_ids: Sequence[int]) -> int:
        """批量加入（已经加过的跳过），返回真正新增的条数。"""
        existing = {int(row.project_id) for row in await self.list_of_student(student_id)}
        created = 0
        for project_id in dict.fromkeys(int(pid) for pid in project_ids):
            if project_id in existing:
                continue
            await self.create({"student_id": student_id, "project_id": project_id})
            created += 1
        return created

    async def remove_project(self, student_id: int, project_id: int) -> bool:
        """把项目移出「我的实训」；本来就不在清单里返回 False（幂等）。"""
        row = await self.by_student_project(student_id, project_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def reorder(self, student_id: int, ordered_ids: Sequence[int]) -> list[int]:
        """按传入顺序写 sort_no（1..N，覆盖式）；不在清单里的 ID 忽略。

        返回清单里找不到的 ID，方便接口回 422 而不是悄悄吞掉前端传错的值。
        """
        rows = {int(row.project_id): row for row in await self.list_of_student(student_id)}
        missing: list[int] = []
        for index, project_id in enumerate(dict.fromkeys(int(pid) for pid in ordered_ids), start=1):
            row = rows.get(project_id)
            if row is None:
                missing.append(project_id)
                continue
            row.sort_no = index
            row.updated_at = now()
        await self.session.flush()
        return missing


class TrainingAttemptRepository(BaseRepository[TrainingAttempt]):
    """闯关轮次仓储。"""

    model = TrainingAttempt

    async def list_of_record(self, student_project_id: int) -> list[TrainingAttempt]:
        stmt = (
            select(TrainingAttempt)
            .where(TrainingAttempt.student_project_id == student_project_id)
            .order_by(TrainingAttempt.attempt_no.desc())  # type: ignore[attr-defined]
        )
        return list((await self.session.exec(stmt)).all())

    async def latest_of_record(self, student_project_id: int) -> TrainingAttempt | None:
        stmt = (
            select(TrainingAttempt)
            .where(TrainingAttempt.student_project_id == student_project_id)
            .order_by(TrainingAttempt.attempt_no.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return (await self.session.exec(stmt)).first()

    async def next_attempt_no(self, student_project_id: int) -> int:
        stmt = select(func.max(TrainingAttempt.attempt_no)).where(
            TrainingAttempt.student_project_id == student_project_id
        )
        return int((await self.session.exec(stmt)).one() or 0) + 1


class AttemptStageRepository(BaseRepository[AttemptStage]):
    """模块作答仓储。"""

    model = AttemptStage

    async def list_of_attempt(self, attempt_id: int) -> list[AttemptStage]:
        stmt = select(AttemptStage).where(AttemptStage.attempt_id == attempt_id).order_by(AttemptStage.id)
        return list((await self.session.exec(stmt)).all())

    async def by_module(self, attempt_id: int, project_module_id: int) -> AttemptStage | None:
        return await self.get_by(attempt_id=attempt_id, project_module_id=project_module_id)

    async def count_filled(self, attempt_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(AttemptStage)
            .where(AttemptStage.attempt_id == attempt_id, AttemptStage.is_filled.is_(True))  # type: ignore[attr-defined]
        )
        return int((await self.session.exec(stmt)).one())

    async def unsaved_module_ids(self, attempt_id: int, required_module_ids: Sequence[int]) -> list[int]:
        """必填但还没填写完成的模块 ID，用于整单提交前的校验。"""
        filled = {
            stage.project_module_id for stage in await self.list_of_attempt(attempt_id) if stage.is_filled
        }
        return [module_id for module_id in required_module_ids if module_id not in filled]


class AttemptStageFileRepository(BaseRepository[AttemptStageFile]):
    """模块作答附件仓储（一个作答可挂多个文件）。"""

    model = AttemptStageFile

    async def list_of_stage(self, attempt_stage_id: int) -> list[AttemptStageFile]:
        stmt = select(AttemptStageFile).where(AttemptStageFile.attempt_stage_id == attempt_stage_id)
        return list((await self.session.exec(stmt)).all())

    async def link_exists(self, attempt_stage_id: int, file_asset_id: int) -> bool:
        return await self.get_by(attempt_stage_id=attempt_stage_id, file_asset_id=file_asset_id) is not None


class FileAssetRepository(BaseRepository[FileAsset]):
    """文件元数据仓储（真正上传由对象存储负责，这里只登记）。"""

    model = FileAsset

    async def list_assets(
        self,
        params: PageParams,
        *,
        biz_type: str | None = None,
        uploader_id: int | None = None,
        keyword: str | None = None,
    ) -> Page[Any]:
        filters: list[Any] = []
        if biz_type:
            filters.append(FileAsset.biz_type == biz_type)
        if uploader_id is not None:
            filters.append(FileAsset.uploader_id == uploader_id)
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(or_(FileAsset.original_name.like(pattern), FileAsset.object_key.like(pattern)))
        return await self.list_page(params, *filters)


class ProjectSubmissionRepository(BaseRepository[ProjectSubmission]):
    """整单提交仓储。"""

    model = ProjectSubmission

    async def list_of_attempt(self, attempt_id: int) -> list[ProjectSubmission]:
        stmt = (
            select(ProjectSubmission)
            .where(ProjectSubmission.attempt_id == attempt_id)
            .order_by(ProjectSubmission.submit_no)  # type: ignore[attr-defined]
        )
        return list((await self.session.exec(stmt)).all())

    async def latest_of_attempt(self, attempt_id: int) -> ProjectSubmission | None:
        stmt = (
            select(ProjectSubmission)
            .where(ProjectSubmission.attempt_id == attempt_id)
            .order_by(ProjectSubmission.submit_no.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return (await self.session.exec(stmt)).first()

    async def next_submit_no(self, attempt_id: int) -> int:
        stmt = select(func.max(ProjectSubmission.submit_no)).where(ProjectSubmission.attempt_id == attempt_id)
        return int((await self.session.exec(stmt)).one() or 0) + 1

    async def list_submissions(
        self,
        params: PageParams,
        *,
        status: str | None = None,
        is_starred: bool | None = None,
        student_id: int | None = None,
        project_id: int | None = None,
    ) -> Page[Any]:
        conditions: list[Any] = []
        if status:
            conditions.append(ProjectSubmission.status == status)
        if is_starred is not None:
            conditions.append(ProjectSubmission.is_starred.is_(is_starred))  # type: ignore[attr-defined]
        if student_id is not None:
            conditions.append(StudentProject.student_id == student_id)
        if project_id is not None:
            conditions.append(StudentProject.project_id == project_id)

        stmt = (
            select(ProjectSubmission, TrainingAttempt, StudentProject, TrainingProject)
            .join(TrainingAttempt, TrainingAttempt.id == ProjectSubmission.attempt_id)  # type: ignore[arg-type]
            .join(StudentProject, StudentProject.id == TrainingAttempt.student_project_id)  # type: ignore[arg-type]
            .join(TrainingProject, TrainingProject.id == StudentProject.project_id)  # type: ignore[arg-type]
            .where(*conditions)
            .order_by(ProjectSubmission.id.desc())  # type: ignore[attr-defined]
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
        count_stmt = (
            select(func.count())
            .select_from(ProjectSubmission)
            .join(TrainingAttempt, TrainingAttempt.id == ProjectSubmission.attempt_id)  # type: ignore[arg-type]
            .join(StudentProject, StudentProject.id == TrainingAttempt.student_project_id)  # type: ignore[arg-type]
            .join(TrainingProject, TrainingProject.id == StudentProject.project_id)  # type: ignore[arg-type]
            .where(*conditions)
        )
        rows = (await self.session.exec(stmt)).all()
        total = int((await self.session.exec(count_stmt)).one())
        items = [
            {
                **submission.model_dump(),
                "student_id": record.student_id,
                "project_id": record.project_id,
                "project_name": project.project_name,
                "attempt_no": attempt.attempt_no,
            }
            for submission, attempt, record, project in rows
        ]
        return Page.build(items=items, total=total, params=params)


__all__ = [
    "AttemptStageFileRepository",
    "AttemptStageRepository",
    "FileAssetRepository",
    "ProjectSubmissionRepository",
    "StudentProjectPickRepository",
    "StudentProjectRepository",
    "TrainingAttemptRepository",
]
