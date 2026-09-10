"""实训项目 Schema。"""

from decimal import Decimal

from sqlmodel import Field, SQLModel

from app.models.project import (
    ProjectModuleBase,
    ProjectStageTemplateBase,
    TrainingProjectBase,
)
from app.schemas.base import SoftDeleteRead, TimestampRead

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
    pass


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


# ---------------------------------------------------------------- 项目模块组成


class ProjectModuleCreate(ProjectModuleBase):
    pass


class ProjectModuleUpdate(SQLModel):
    template_id: int | None = None
    stage_key: str | None = Field(default=None, max_length=50)
    module_name: str | None = Field(default=None, max_length=100)
    stage_no: int | None = Field(default=None, ge=1, le=99)
    requirement: str | None = None
    accept_standard: str | None = None
    required: bool | None = None
    weight: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
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
