"""项目域业务规则：模块组成拼装与发布校验。"""

from decimal import Decimal

from app.core.exceptions import BusinessRuleError
from app.crud.project import (
    ProjectModuleRepository,
    ProjectModuleSkillRepository,
    StageTemplateRepository,
)
from app.models.project import ProjectModule
from app.schemas.project import ProjectModuleDetail

#: 发布项目时，已选模块的权重合计必须等于这个值
REQUIRED_WEIGHT_TOTAL = Decimal("100")


async def build_module_details(
    modules: list[ProjectModule],
    templates: StageTemplateRepository,
    skills: ProjectModuleSkillRepository | None = None,
) -> list[ProjectModuleDetail]:
    """把项目模块拼成带模板信息的明细（关卡名称统一读模块库）。

    传入 ``skills`` 仓储时会一并带上该关卡关联的技能点 ID（技能点亮按关卡判定）。
    """
    template_map = {
        template.id: template
        for template in await templates.list_by_ids(
            [module.template_id for module in modules], include_deleted=True
        )
    }
    skill_map = (
        await skills.skill_node_ids_of_modules([module.id for module in modules if module.id])
        if skills is not None and modules
        else {}
    )
    details: list[ProjectModuleDetail] = []
    for module in modules:
        template = template_map.get(module.template_id)
        if template is None:  # 理论上外键保证不会发生
            raise BusinessRuleError(f"模块 {module.id} 引用的模板 {module.template_id} 不存在")
        details.append(
            ProjectModuleDetail(
                **module.model_dump(),
                stage_name=template.stage_name,
                default_weight=template.default_weight,
                skill_node_ids=skill_map.get(module.id, []),
            )
        )
    return details


async def module_detail(
    module: ProjectModule,
    templates: StageTemplateRepository,
    skills: ProjectModuleSkillRepository | None = None,
) -> ProjectModuleDetail:
    details = await build_module_details([module], templates, skills)
    return details[0]


async def weight_total(modules: list[ProjectModule]) -> Decimal:
    return sum((module.weight for module in modules), Decimal(0))


async def ensure_publishable(project_id: int, modules: ProjectModuleRepository) -> list[ProjectModule]:
    """发布前校验：至少一个关卡，且权重合计等于 100。"""
    items = await modules.list_of_project(project_id)
    if not items:
        raise BusinessRuleError("项目还没有选择任何关卡，先去模块库挑模板")
    total = await weight_total(items)
    if total != REQUIRED_WEIGHT_TOTAL:
        raise BusinessRuleError(f"关卡权重合计为 {total}，发布前必须调整为 {REQUIRED_WEIGHT_TOTAL}")
    return items


__all__ = [
    "REQUIRED_WEIGHT_TOTAL",
    "build_module_details",
    "ensure_publishable",
    "module_detail",
    "weight_total",
]
