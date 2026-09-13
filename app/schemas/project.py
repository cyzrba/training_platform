"""实训项目 Schema。"""

from decimal import Decimal

from sqlmodel import Field, SQLModel

from app.models.project import (
    ProjectModuleBase,
    ProjectStageTemplateBase,
    TrainingProjectBase,
)
from app.schemas.base import SoftDeleteRead, TimestampRead


class StageItem(SQLModel):
    """关卡里的一个填写引导子标题（由教师手填，只做引导，不参与校验与评分）。"""

    title: str = Field(max_length=100, description="子标题，如「检测对象描述」")
    prompt: str | None = Field(default=None, max_length=500, description="该子标题下的填写提示")


# -------------------------------------------------------------------- 实训项目


class TrainingProjectCreate(TrainingProjectBase):
    pass


class TrainingProjectUpdate(SQLModel):
    project_name: str | None = Field(default=None, max_length=150)
    project_level: str | None = Field(default=None, max_length=20)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    job_id: int | None = None
    description: str | None = None
    status: str | None = Field(default=None, max_length=20)
    creator_id: int | None = None


class TrainingProjectRead(TimestampRead, SoftDeleteRead, TrainingProjectBase):
    id: int


# --------------------------------------------------------------- 标准化模块库


class ProjectStageTemplateCreate(ProjectStageTemplateBase):
    """新建模块库条目：只填通用信息，模板不预设填写子标题。"""


class ProjectStageTemplateUpdate(SQLModel):
    stage_key: str | None = Field(default=None, max_length=50)
    stage_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    default_required: bool | None = None
    default_weight: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    default_requirement: str | None = None
    default_accept_standard: str | None = None
    sort_no: int | None = Field(default=None, ge=0)


class ProjectStageTemplateRead(TimestampRead, ProjectStageTemplateBase):
    id: int


class ProjectStageTemplateDetail(ProjectStageTemplateRead):
    """模块库条目详情：带被多少个项目选中。"""

    used_by_projects: int = Field(default=0, description="被多少个项目选中（选中后不可删除）")


# ---------------------------------------------------------------- 项目模块组成


class ProjectModuleCreate(ProjectModuleBase):
    """项目里添加一个模块：只能挑模块库里的模板。"""


class ProjectModuleAddIn(SQLModel):
    """往项目里加一个模块库模板；stage_no 留空则排到最后。"""

    template_id: int = Field(description="模块库模板 ID")
    stage_no: int | None = Field(default=None, ge=1, le=99, description="项目内顺序，留空自动排到最后")
    requirement: str | None = Field(default=None, description="作答要求（留空用模板默认）")
    accept_standard: str | None = Field(default=None, description="验收标准（留空用模板默认）")
    required: bool | None = Field(default=None, description="是否必填（留空用模板默认）")
    weight: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2, description="权重（留空用模板默认）"
    )
    items_json: list[StageItem] | None = Field(
        default=None, description="填写引导子标题，由教师手填；不传就是空数组"
    )


class ProjectModuleUpdate(SQLModel):
    stage_no: int | None = Field(default=None, ge=1, le=99)
    requirement: str | None = None
    accept_standard: str | None = None
    required: bool | None = None
    weight: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    items_json: list[StageItem] | None = Field(default=None, description="覆盖本项目的填写引导子标题")


class ProjectModuleRead(TimestampRead, ProjectModuleBase):
    id: int


class ProjectModuleDetail(ProjectModuleRead):
    """项目里的一个模块：带上模板里的名称与编码（关卡定义统一读模块库）。"""

    stage_key: str = Field(description="模块编码（取模板）")
    stage_name: str = Field(description="模块名称（取模板）")
    default_weight: Decimal = Field(default=Decimal(0), description="模板默认权重")
    items_json: list[StageItem] = Field(default_factory=list, description="本项目的填写引导子标题")


class ProjectModuleOrderIn(SQLModel):
    """按传入顺序重排项目内的关卡。"""

    module_ids: list[int] = Field(min_length=1, description="项目模块 ID，按目标顺序排列")


class TrainingProjectDetail(TrainingProjectRead):
    """项目详情：附带模块组成与权重合计。"""

    modules: list[ProjectModuleDetail] = Field(default_factory=list, description="已选关卡，按顺序")
    weight_total: Decimal = Field(default=Decimal(0), description="已选模块权重合计（发布时需等于 100）")


__all__ = [
    "ProjectModuleCreate",
    "ProjectModuleAddIn",
    "ProjectModuleDetail",
    "ProjectModuleOrderIn",
    "ProjectModuleRead",
    "ProjectModuleUpdate",
    "ProjectStageTemplateCreate",
    "ProjectStageTemplateDetail",
    "ProjectStageTemplateRead",
    "ProjectStageTemplateUpdate",
    "StageItem",
    "TrainingProjectCreate",
    "TrainingProjectDetail",
    "TrainingProjectRead",
    "TrainingProjectUpdate",
]
