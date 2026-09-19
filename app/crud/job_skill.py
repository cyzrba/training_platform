"""岗位与技能成长仓储：岗位、技能树/节点/依赖、岗位技能、成长规则、学生选岗与进度。"""

from collections.abc import Sequence
from typing import Any

from sqlmodel import delete, func, or_, select

from app.core.time import now
from app.crud.base import BaseRepository
from app.models.job_skill import (
    GrowthRule,
    Job,
    JobSkill,
    ProjectSkill,
    SkillNode,
    SkillNodeDependency,
    SkillTree,
    StudentJob,
    StudentSkill,
)
from app.schemas.base import Page, PageParams


class JobRepository(BaseRepository[Job]):
    """岗位仓储，软删表。"""

    model = Job
    soft_delete = True

    async def list_jobs(
        self,
        params: PageParams,
        *,
        keyword: str | None = None,
        status: str | None = None,
        recommended_level: str | None = None,
        direction_tag: str | None = None,
    ) -> Page[Any]:
        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(or_(Job.job_name.like(pattern), Job.scene.like(pattern)))
        if status:
            filters.append(Job.status == status)
        if recommended_level:
            filters.append(Job.recommended_level == recommended_level)
        if direction_tag:
            filters.append(Job.direction_tag == direction_tag)
        return await self.list_page(params, *filters)

    async def by_name(self, job_name: str) -> Job | None:
        return await self.get_by(job_name=job_name)


class JobSkillRepository(BaseRepository[JobSkill]):
    """岗位-技能关联仓储，覆盖式设置。"""

    model = JobSkill

    async def list_nodes_of_job(self, job_id: int) -> list[SkillNode]:
        stmt = (
            select(SkillNode)
            .join(JobSkill, JobSkill.skill_node_id == SkillNode.id)  # type: ignore[arg-type]
            .where(JobSkill.job_id == job_id, SkillNode.deleted_at.is_(None))  # type: ignore[attr-defined]
            .order_by(SkillNode.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def count_of_job(self, job_id: int) -> int:
        stmt = select(func.count()).select_from(JobSkill).where(JobSkill.job_id == job_id)
        return int((await self.session.exec(stmt)).one())

    async def replace_skills(self, job_id: int, skill_node_ids: Sequence[int]) -> int:
        await self.session.exec(delete(JobSkill).where(JobSkill.job_id == job_id))
        for skill_node_id in skill_node_ids:
            self.session.add(JobSkill(job_id=job_id, skill_node_id=skill_node_id))
        await self.session.flush()
        return len(skill_node_ids)

    async def add_skill(self, job_id: int, skill_node_id: int) -> JobSkill:
        return await self.create({"job_id": job_id, "skill_node_id": skill_node_id})

    async def link_exists(self, job_id: int, skill_node_id: int) -> bool:
        """先查再插，避免用唯一约束异常当流程控制（那会把会话打成待回滚状态）。"""
        return await self.get_by(job_id=job_id, skill_node_id=skill_node_id) is not None

    async def remove_skill(self, job_id: int, skill_node_id: int) -> None:
        await self.session.exec(
            delete(JobSkill).where(JobSkill.job_id == job_id, JobSkill.skill_node_id == skill_node_id)
        )
        await self.session.flush()


class ProjectSkillRepository(BaseRepository[ProjectSkill]):
    """项目-技能关联仓储：项目要训练哪些技能，覆盖式设置。"""

    model = ProjectSkill

    async def list_nodes_of_project(self, project_id: int) -> list[SkillNode]:
        stmt = (
            select(SkillNode)
            .join(ProjectSkill, ProjectSkill.skill_node_id == SkillNode.id)  # type: ignore[arg-type]
            .where(ProjectSkill.project_id == project_id, SkillNode.deleted_at.is_(None))  # type: ignore[attr-defined]
            .order_by(SkillNode.tree_id, SkillNode.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def count_of_project(self, project_id: int) -> int:
        stmt = select(func.count()).select_from(ProjectSkill).where(ProjectSkill.project_id == project_id)
        return int((await self.session.exec(stmt)).one())

    async def replace_skills(self, project_id: int, skill_node_ids: Sequence[int]) -> int:
        await self.session.exec(delete(ProjectSkill).where(ProjectSkill.project_id == project_id))
        for skill_node_id in skill_node_ids:
            self.session.add(ProjectSkill(project_id=project_id, skill_node_id=skill_node_id))
        await self.session.flush()
        return len(skill_node_ids)

    async def add_skill(self, project_id: int, skill_node_id: int) -> ProjectSkill:
        return await self.create({"project_id": project_id, "skill_node_id": skill_node_id})

    async def link_exists(self, project_id: int, skill_node_id: int) -> bool:
        return await self.get_by(project_id=project_id, skill_node_id=skill_node_id) is not None

    async def remove_skill(self, project_id: int, skill_node_id: int) -> None:
        await self.session.exec(
            delete(ProjectSkill).where(
                ProjectSkill.project_id == project_id, ProjectSkill.skill_node_id == skill_node_id
            )
        )
        await self.session.flush()


class SkillTreeRepository(BaseRepository[SkillTree]):
    """技能树仓储，软删表。"""

    model = SkillTree
    soft_delete = True

    async def list_trees(
        self, params: PageParams, *, keyword: str | None = None, status: str | None = None
    ) -> Page[Any]:
        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(SkillTree.tree_name.like(pattern))
        if status:
            filters.append(SkillTree.status == status)
        return await self.list_page(params, *filters)

    async def by_name(self, tree_name: str) -> SkillTree | None:
        return await self.get_by(tree_name=tree_name)


class SkillNodeRepository(BaseRepository[SkillNode]):
    """技能节点仓储，软删表。"""

    model = SkillNode
    soft_delete = True

    async def list_by_tree(self, tree_id: int) -> list[SkillNode]:
        stmt = (
            select(SkillNode)
            .where(SkillNode.tree_id == tree_id, SkillNode.deleted_at.is_(None))  # type: ignore[attr-defined]
            .order_by(SkillNode.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def by_name(self, node_name: str) -> SkillNode | None:
        return await self.get_by(node_name=node_name)


class SkillNodeDependencyRepository(BaseRepository[SkillNodeDependency]):
    """技能前置依赖仓储（DAG），覆盖式设置。"""

    model = SkillNodeDependency

    async def prerequisite_ids(self, node_id: int) -> list[int]:
        stmt = (
            select(SkillNodeDependency.prerequisite_node_id)
            .join(SkillNode, SkillNode.id == SkillNodeDependency.prerequisite_node_id)  # type: ignore[arg-type]
            .where(
                SkillNodeDependency.node_id == node_id,
                SkillNode.deleted_at.is_(None),  # type: ignore[attr-defined]
            )
        )
        return list((await self.session.exec(stmt)).all())

    async def prerequisite_ids_of_many(self, node_ids: Sequence[int]) -> dict[int, list[int]]:
        if not node_ids:
            return {}
        stmt = (
            select(SkillNodeDependency.node_id, SkillNodeDependency.prerequisite_node_id)
            .join(SkillNode, SkillNode.id == SkillNodeDependency.prerequisite_node_id)  # type: ignore[arg-type]
            .where(
                SkillNodeDependency.node_id.in_(list(node_ids)),  # type: ignore[attr-defined]
                SkillNode.deleted_at.is_(None),  # type: ignore[attr-defined]
            )
        )
        grouped: dict[int, list[int]] = {}
        for node_id, prerequisite_id in (await self.session.exec(stmt)).all():
            grouped.setdefault(int(node_id), []).append(int(prerequisite_id))
        return grouped

    async def replace_prerequisites(self, node_id: int, prerequisite_node_ids: Sequence[int]) -> int:
        await self.session.exec(delete(SkillNodeDependency).where(SkillNodeDependency.node_id == node_id))
        for prerequisite_id in prerequisite_node_ids:
            self.session.add(SkillNodeDependency(node_id=node_id, prerequisite_node_id=prerequisite_id))
        await self.session.flush()
        return len(prerequisite_node_ids)

    async def all_edges(self) -> list[tuple[int, int]]:
        stmt = select(SkillNodeDependency.node_id, SkillNodeDependency.prerequisite_node_id)
        return [(int(node_id), int(prereq)) for node_id, prereq in (await self.session.exec(stmt)).all()]


class GrowthRuleRepository(BaseRepository[GrowthRule]):
    """成长规则仓储。"""

    model = GrowthRule

    async def by_level_type(self, level_type: str) -> GrowthRule | None:
        return await self.get_by(level_type=level_type)


class StudentJobRepository(BaseRepository[StudentJob]):
    """学生选岗仓储，负责维持"每名学生至多一个主岗位"。"""

    model = StudentJob

    async def list_of_student(self, student_id: int) -> list[tuple[StudentJob, Job]]:
        stmt = (
            select(StudentJob, Job)
            .join(Job, Job.id == StudentJob.job_id)  # type: ignore[arg-type]
            .where(StudentJob.student_id == student_id)
            .order_by(StudentJob.id)
        )
        return [(selection, job) for selection, job in (await self.session.exec(stmt)).all()]

    async def by_student_job(self, student_id: int, job_id: int) -> StudentJob | None:
        return await self.get_by(student_id=student_id, job_id=job_id)

    async def by_id(self, student_job_id: int) -> StudentJob | None:
        return await self.get(student_job_id)

    async def clear_primary(self, student_id: int, *, exclude_id: int | None = None) -> None:
        """把该学生其它主岗位降级，并记录切换时间（保留历史，不删行）。"""
        stmt = select(StudentJob).where(
            StudentJob.student_id == student_id,
            StudentJob.is_primary.is_(True),  # type: ignore[attr-defined]
        )
        for selection in (await self.session.exec(stmt)).all():
            if exclude_id is not None and selection.id == exclude_id:
                continue
            await self.update(selection, {"is_primary": False, "switched_at": now()})


class StudentSkillRepository(BaseRepository[StudentSkill]):
    """学生技能进度仓储。"""

    model = StudentSkill

    async def by_student_node(self, student_id: int, skill_node_id: int) -> StudentSkill | None:
        return await self.get_by(student_id=student_id, skill_node_id=skill_node_id)

    async def list_of_student(self, student_id: int) -> list[tuple[StudentSkill, SkillNode, SkillTree]]:
        stmt = (
            select(StudentSkill, SkillNode, SkillTree)
            .join(SkillNode, SkillNode.id == StudentSkill.skill_node_id)  # type: ignore[arg-type]
            .join(SkillTree, SkillTree.id == SkillNode.tree_id)  # type: ignore[arg-type]
            .where(StudentSkill.student_id == student_id)
            .order_by(SkillNode.tree_id, SkillNode.id)
        )
        return [(skill, node, tree) for skill, node, tree in (await self.session.exec(stmt)).all()]


__all__ = [
    "GrowthRuleRepository",
    "JobRepository",
    "JobSkillRepository",
    "ProjectSkillRepository",
    "SkillNodeDependencyRepository",
    "SkillNodeRepository",
    "SkillTreeRepository",
    "StudentJobRepository",
    "StudentSkillRepository",
]
