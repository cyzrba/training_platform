"""实训项目：项目主数据、标准化模块库、项目模块组成。"""

from decimal import Decimal

from sqlmodel import (
    CheckConstraint,
    Field,
    Index,
    Numeric,
    SmallInteger,
    SQLModel,
    Text,
    UniqueConstraint,
    text,
)

from app.models.base import Base, SoftDeleteMixin, TimestampMixin

# -------------------------------------------------------------------- 实训项目


class TrainingProjectBase(SQLModel):
    project_name: str = Field(max_length=150, description="项目名称")
    project_level: str = Field(
        max_length=20, description="项目层级 BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展"
    )
    difficulty: int = Field(
        default=3,
        sa_type=SmallInteger,
        description="难度 1~5（1 最简单）",
        sa_column_kwargs={"server_default": text("3")},
    )
    job_id: int | None = Field(default=None, foreign_key="job.id", description="绑定岗位（可空）")
    description: str | None = Field(default=None, sa_type=Text, description="说明/描述")
    status: str = Field(
        default="DRAFT",
        max_length=20,
        description="DRAFT 草稿 / PUBLISHED 已发布 / OFF_SHELF 已下架",
        sa_column_kwargs={"server_default": text("'DRAFT'")},
    )
    creator_id: int | None = Field(default=None, foreign_key="sys_user.id", description="创建人 ID")


class TrainingProject(Base, TimestampMixin, SoftDeleteMixin, TrainingProjectBase, table=True):
    """实训项目主数据；完成及格线不在此配置，统一读 growth_rule.pass_score。"""

    __tablename__ = "training_project"
    __table_args__ = (
        UniqueConstraint("project_name", name="uk_training_project_name"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="chk_training_project_difficulty"),
        Index("idx_training_project_status", "project_level", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)


# --------------------------------------------------------------- 标准化模块库


class ProjectStageTemplateBase(SQLModel):
    stage_key: str = Field(max_length=50, description="七大模块 code")
    stage_name: str = Field(max_length=100, description="模块名称")
    description: str | None = Field(default=None, sa_type=Text, description="说明/描述")
    default_required: bool = Field(
        default=True, description="默认是否必填", sa_column_kwargs={"server_default": text("1")}
    )
    default_weight: Decimal = Field(
        default=Decimal("0"),
        sa_type=Numeric(5, 2),
        description="默认分值占比",
        sa_column_kwargs={"server_default": text("0")},
    )
    default_requirement: str | None = Field(default=None, sa_type=Text, description="默认作答要求")
    default_accept_standard: str | None = Field(default=None, sa_type=Text, description="默认验收标准")
    sort_no: int = Field(
        default=0, description="展示排序，值越小越靠前", sa_column_kwargs={"server_default": text("0")}
    )


class ProjectStageTemplate(Base, TimestampMixin, ProjectStageTemplateBase, table=True):
    """标准化模块库（七大模块默认配置），教师建项目时从中挑选。"""

    __tablename__ = "project_stage_template"
    __table_args__ = (UniqueConstraint("stage_key", name="uk_stage_template_key"),)

    id: int | None = Field(default=None, primary_key=True)


# ---------------------------------------------------------------- 项目模块组成


class ProjectModuleBase(SQLModel):
    project_id: int = Field(foreign_key="training_project.id", description="项目 ID")
    template_id: int | None = Field(
        default=None,
        foreign_key="project_stage_template.id",
        description="来自模块库；自定义模块为空",
    )
    stage_key: str | None = Field(default=None, max_length=50, description="标准模块 code；自定义模块为空")
    module_name: str = Field(max_length=100, description="模块名称（来自模板或自定义）")
    stage_no: int = Field(sa_type=SmallInteger, description="项目内填写顺序，数量不限")
    requirement: str | None = Field(default=None, sa_type=Text, description="该模块的作答要求（覆盖模板）")
    accept_standard: str | None = Field(
        default=None, sa_type=Text, description="该模块的验收标准（覆盖模板）"
    )
    required: bool = Field(
        default=True, description="是否必填", sa_column_kwargs={"server_default": text("1")}
    )
    weight: Decimal = Field(
        default=Decimal("0"),
        sa_type=Numeric(5, 2),
        description="分值占比（启用模块合计 100）",
        sa_column_kwargs={"server_default": text("0")},
    )
    enabled: bool = Field(
        default=True, description="是否启用", sa_column_kwargs={"server_default": text("1")}
    )


class ProjectModule(Base, TimestampMixin, ProjectModuleBase, table=True):
    """项目模块组成：可从模块库挑选或自定义，数量不限；一条 = 一个模块。"""

    __tablename__ = "project_module"
    __table_args__ = (
        UniqueConstraint("project_id", "stage_key", name="uk_project_module_key"),
        UniqueConstraint("project_id", "stage_no", name="uk_project_module_no"),
        Index("idx_project_module_project", "project_id", "stage_no"),
    )

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "ProjectModule",
    "ProjectModuleBase",
    "ProjectStageTemplate",
    "ProjectStageTemplateBase",
    "TrainingProject",
    "TrainingProjectBase",
]
