"""实训项目仓储：模块库（模板）与项目模块组成。"""

from collections.abc import Sequence
from typing import Any

from sqlmodel import delete, func, or_, select

from app.core.time import now
from app.crud.base import BaseRepository
from app.models.project import ProjectModule, ProjectStageTemplate, TrainingProject
from app.schemas.base import Page, PageParams


class StageTemplateRepository(BaseRepository[ProjectStageTemplate]):
    """模块库（模板）仓储：教师可自定义增删，项目只能从这里挑。"""

    model = ProjectStageTemplate
    soft_delete = True

    async def list_templates(self, params: PageParams, *, keyword: str | None = None) -> Page[Any]:
        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    ProjectStageTemplate.stage_name.like(pattern),
                    ProjectStageTemplate.stage_key.like(pattern),
                )
            )
        return await self.list_page(params, *filters, order_by=ProjectStageTemplate.sort_no)

    async def by_key(self, stage_key: str) -> ProjectStageTemplate | None:
        return await self.get_by(stage_key=stage_key)

    async def by_key_including_deleted(self, stage_key: str) -> ProjectStageTemplate | None:
        """含已软删的条目：重新新增同一个编码时用来恢复。"""
        stmt = select(ProjectStageTemplate).where(ProjectStageTemplate.stage_key == stage_key)
        return (await self.session.exec(stmt)).first()

    async def restore(self, template: ProjectStageTemplate, data: dict[str, Any]) -> ProjectStageTemplate:
        """把软删的条目恢复出来，并用新提交的内容覆盖。"""
        return await self.update(template, {**data, "deleted_at": None})

    async def next_sort_no(self) -> int:
        stmt = select(func.max(ProjectStageTemplate.sort_no))
        return int((await self.session.exec(stmt)).one() or 0) + 1

    async def list_by_ids(
        self, template_ids: Sequence[int], *, include_deleted: bool = False
    ) -> list[ProjectStageTemplate]:
        """按 ID 批量取模板；include_deleted=True 时把软删的也带出来（历史项目仍要能显示关卡名）。"""
        if not template_ids:
            return []
        stmt = select(ProjectStageTemplate).where(
            ProjectStageTemplate.id.in_(list(template_ids))  # type: ignore[attr-defined]
        )
        if not include_deleted:
            stmt = stmt.where(ProjectStageTemplate.deleted_at.is_(None))  # type: ignore[attr-defined]
        return list((await self.session.exec(stmt)).all())


class TrainingProjectRepository(BaseRepository[TrainingProject]):
    """实训项目仓储，软删表。"""

    model = TrainingProject
    soft_delete = True

    async def list_projects(
        self,
        params: PageParams,
        *,
        keyword: str | None = None,
        project_level: str | None = None,
        status: str | None = None,
        job_id: int | None = None,
        creator_id: int | None = None,
    ) -> Page[Any]:
        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    TrainingProject.project_name.like(pattern),
                    TrainingProject.description.like(pattern),
                )
            )
        if project_level:
            filters.append(TrainingProject.project_level == project_level)
        if status:
            filters.append(TrainingProject.status == status)
        if job_id is not None:
            filters.append(TrainingProject.job_id == job_id)
        if creator_id is not None:
            filters.append(TrainingProject.creator_id == creator_id)
        return await self.list_page(params, *filters)

    async def by_name(self, project_name: str) -> TrainingProject | None:
        return await self.get_by(project_name=project_name)


class ProjectModuleRepository(BaseRepository[ProjectModule]):
    """项目模块组成仓储：只存"被选中的模板"。"""

    model = ProjectModule

    async def list_of_project(self, project_id: int) -> list[ProjectModule]:
        stmt = (
            select(ProjectModule)
            .where(ProjectModule.project_id == project_id)
            .order_by(ProjectModule.stage_no)
        )
        return list((await self.session.exec(stmt)).all())

    async def by_template(self, project_id: int, template_id: int) -> ProjectModule | None:
        return await self.get_by(project_id=project_id, template_id=template_id)

    async def by_stage_no(self, project_id: int, stage_no: int) -> ProjectModule | None:
        return await self.get_by(project_id=project_id, stage_no=stage_no)

    async def count_by_template(self, template_id: int) -> int:
        """该模板被多少个项目选中（用于阻止误删模块库条目）。"""
        stmt = select(func.count()).select_from(ProjectModule).where(ProjectModule.template_id == template_id)
        return int((await self.session.exec(stmt)).one())

    async def next_stage_no(self, project_id: int) -> int:
        stmt = select(func.max(ProjectModule.stage_no)).where(ProjectModule.project_id == project_id)
        return int((await self.session.exec(stmt)).one() or 0) + 1

    async def remove(self, obj: ProjectModule) -> None:
        """项目模块是纯关系记录，直接物理删除（表也没有软删字段）。"""
        await self.session.delete(obj)
        await self.session.flush()

    async def renumber(self, project_id: int) -> None:
        """按当前顺序把 stage_no 重排成 1..N，避免删掉中间模块后留下空洞。"""
        modules = await self.list_of_project(project_id)
        await self.reorder(project_id, [module.id for module in modules])

    async def reorder(self, project_id: int, ordered_ids: Sequence[int]) -> None:
        """按给定顺序重排 stage_no。

        唯一约束 uk_project_module_no 让"逐条改"必然中途撞车（1→2 时 2 还被占着），
        所以先把所有行挪到临时区间，再落回 1..N。
        """
        modules = await self.list_of_project(project_id)
        by_id = {module.id: module for module in modules}
        offset = len(modules) + 1000
        for module in modules:
            module.stage_no = module.stage_no + offset
            module.updated_at = now()
        await self.session.flush()

        for index, module_id in enumerate(ordered_ids, start=1):
            module = by_id[module_id]
            module.stage_no = index
            module.updated_at = now()
        await self.session.flush()

    async def delete_of_project(self, project_id: int) -> None:
        """删项目时一并清掉它的模块组成。"""
        await self.session.exec(delete(ProjectModule).where(ProjectModule.project_id == project_id))
        await self.session.flush()


__all__ = [
    "ProjectModuleRepository",
    "StageTemplateRepository",
    "TrainingProjectRepository",
]
