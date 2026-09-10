"""D 域 Schema · 实训项目。"""

from decimal import Decimal

from pydantic import Field

from app.models.enums import LearningLevel, ProjectStatus
from app.schemas.base import ORMModel, ScoreField, SoftDeleteRead, TimestampRead

# -------------------------------------------------------- training_project


class TrainingProjectBase(ORMModel):
    project_name: str = Field(min_length=1, max_length=150, description="项目名称")
    project_level: LearningLevel = Field(description="项目层级")
    difficulty: int = Field(3, ge=1, le=5, description="难度 1~5")
    job_id: int | None = Field(None, description="绑定岗位（可空）")
    description: str | None = Field(None, description="项目说明")
    status: ProjectStatus = Field(ProjectStatus.DRAFT, description="项目状态")
    creator_id: int | None = Field(None, description="创建人")


class TrainingProjectCreate(TrainingProjectBase):
    pass


class TrainingProjectUpdate(ORMModel):
    project_name: str | None = Field(None, min_length=1, max_length=150)
    project_level: LearningLevel | None = None
    difficulty: int | None = Field(None, ge=1, le=5)
    job_id: int | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    creator_id: int | None = None


class TrainingProjectRead(TimestampRead, SoftDeleteRead, TrainingProjectBase):
    id: int


# --------------------------------------------------- project_stage_template


class ProjectStageTemplateBase(ORMModel):
    stage_key: str = Field(min_length=1, max_length=50, description="模块 code")
    stage_name: str = Field(min_length=1, max_length=100, description="模块名称")
    description: str | None = Field(None, description="说明")
    default_required: bool = Field(True, description="默认是否必填")
    default_weight: ScoreField = Field(Decimal(0), description="默认分值占比")
    default_requirement: str | None = Field(None, description="默认作答要求")
    default_accept_standard: str | None = Field(None, description="默认验收标准")
    sort_no: int = Field(0, ge=0, description="展示排序，值越小越靠前")


class ProjectStageTemplateCreate(ProjectStageTemplateBase):
    pass


class ProjectStageTemplateUpdate(ORMModel):
    stage_key: str | None = Field(None, min_length=1, max_length=50)
    stage_name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    default_required: bool | None = None
    default_weight: ScoreField | None = None
    default_requirement: str | None = None
    default_accept_standard: str | None = None
    sort_no: int | None = Field(None, ge=0)


class ProjectStageTemplateRead(TimestampRead, ProjectStageTemplateBase):
    id: int


# --------------------------------------------------------- project_module


class ProjectModuleBase(ORMModel):
    project_id: int = Field(description="项目 ID")
    template_id: int | None = Field(None, description="来自模块库；自定义模块为空")
    stage_key: str | None = Field(None, max_length=50, description="标准模块 code；自定义为空")
    module_name: str = Field(min_length=1, max_length=100, description="模块名称")
    stage_no: int = Field(ge=1, le=99, description="项目内填写顺序")
    requirement: str | None = Field(None, description="作答要求（覆盖模板）")
    accept_standard: str | None = Field(None, description="验收标准（覆盖模板）")
    required: bool = Field(True, description="是否必填")
    weight: ScoreField = Field(Decimal(0), description="分值占比")
    enabled: bool = Field(True, description="是否启用")


class ProjectModuleCreate(ProjectModuleBase):
    pass


class ProjectModuleUpdate(ORMModel):
    template_id: int | None = None
    stage_key: str | None = Field(None, max_length=50)
    module_name: str | None = Field(None, min_length=1, max_length=100)
    stage_no: int | None = Field(None, ge=1, le=99)
    requirement: str | None = None
    accept_standard: str | None = None
    required: bool | None = None
    weight: ScoreField | None = None
    enabled: bool | None = None


class ProjectModuleRead(TimestampRead, ProjectModuleBase):
    id: int


class TrainingProjectDetail(TrainingProjectRead):
    """项目详情：附带模块组成。"""

    modules: list[ProjectModuleRead] = Field(default_factory=list)


__all__ = [
    "ProjectModuleCreate",
    "ProjectModuleRead",
    "ProjectModuleUpdate",
    "ProjectStageTemplateCreate",
    "ProjectStageTemplateRead",
    "ProjectStageTemplateUpdate",
    "TrainingProjectCreate",
    "TrainingProjectDetail",
    "TrainingProjectRead",
    "TrainingProjectUpdate",
]
