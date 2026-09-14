"""实训项目：项目主数据、标准化模块库、项目模块组成。"""

from decimal import Decimal

from sqlmodel import (
    JSON,
    CheckConstraint,
    Field,
    Index,
    SQLModel,
    UniqueConstraint,
)

from app.models.base import Base, SoftDeleteMixin, TimestampMixin

# -------------------------------------------------------------------- 实训项目


class TrainingProjectBase(SQLModel):
    project_name: str = Field(max_length=150, description="项目名称")
    project_level: str = Field(
        max_length=20, description="项目层级 BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展"
    )
    difficulty: int = Field(default=3, description="难度 1~5（1 最简单）")
    job_id: int | None = Field(default=None, foreign_key="job.id", description="绑定岗位（可空）")
    description: str | None = Field(default=None, description="说明/描述")
    status: str = Field(
        default="DRAFT",
        max_length=20,
        description="DRAFT 草稿 / PUBLISHED 已发布 / OFF_SHELF 已下架",
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
    description: str | None = Field(default=None, description="说明/描述")
    default_required: bool = Field(default=True, description="默认是否必填")
    default_weight: Decimal = Field(default=Decimal(0), description="默认分值占比")
    default_requirement: str | None = Field(default=None, description="默认作答要求")
    default_accept_standard: str | None = Field(default=None, description="默认验收标准")
    sort_no: int = Field(default=0, description="展示排序，值越小越靠前")


class ProjectStageTemplate(Base, TimestampMixin, SoftDeleteMixin, ProjectStageTemplateBase, table=True):
    """标准化模块库（关卡模板）：教师可自定义增删，项目只能从这里挑。

    软删：删掉的条目不再出现在列表/详情里，但被项目引用过的历史仍可追溯；
    重新用同一个 ``stage_key`` 新增时会把软删的条目恢复出来（见 create 接口）。
    """

    __tablename__ = "project_stage_template"
    __table_args__ = (UniqueConstraint("stage_key", name="uk_stage_template_key"),)

    id: int | None = Field(default=None, primary_key=True)


# ---------------------------------------------------------------- 项目模块组成


class ProjectModuleBase(SQLModel):
    project_id: int = Field(foreign_key="training_project.id", description="项目 ID")
    template_id: int = Field(
        foreign_key="project_stage_template.id",
        description="模块库模板 ID；关卡只能从模块库选，这里必填",
    )
    stage_no: int = Field(description="项目内填写顺序，数量不限")
    items_json: list = Field(
        default_factory=list,
        sa_type=JSON,
        description='填写引导子标题，由教师在本项目里手填；形如 [{"title": "检测对象描述"}]',
    )
    requirement: str | None = Field(default=None, description="该模块的作答要求（覆盖模板）")
    accept_standard: str | None = Field(default=None, description="该模块的验收标准（覆盖模板）")
    required: bool = Field(default=True, description="是否必填")
    weight: Decimal = Field(default=Decimal(0), description="分值占比（选中的模块合计 100）")


class ProjectModule(Base, TimestampMixin, ProjectModuleBase, table=True):
    """项目模块组成：一条 = 项目里选中的一个模块库模板。

    只记录"被选中的模板"，没选的不落库，因此不需要 enabled 字段；
    关卡内容（名称、code、默认要求/标准）统一读 project_stage_template，
    要新增关卡必须先加到模块库，不能在项目里临时造。
    """

    __tablename__ = "project_module"
    __table_args__ = (
        UniqueConstraint("project_id", "template_id", name="uk_project_module_template"),
        UniqueConstraint("project_id", "stage_no", name="uk_project_module_no"),
        Index("idx_project_module_project", "project_id", "stage_no"),
    )

    id: int | None = Field(default=None, primary_key=True)


class ProjectFileBase(SQLModel):
    project_id: int = Field(foreign_key="training_project.id", description="项目 ID")
    file_asset_id: int = Field(foreign_key="file_asset.id", description="文件 ID（file_asset.id）")
    file_kind: str = Field(
        max_length=30,
        description="用途：REPORT_TEMPLATE 报告模板 / DATASET 数据文件 / GUIDE 说明文档 / OTHER 其它",
    )
    title: str | None = Field(default=None, max_length=150, description="展示名称，留空用文件名")
    remark: str | None = Field(default=None, max_length=255, description="备注")
    sort_no: int = Field(default=0, description="展示排序，值越小越靠前")
    uploaded_by: int | None = Field(default=None, foreign_key="sys_user.id", description="上传人 ID")


class ProjectFile(Base, TimestampMixin, ProjectFileBase, table=True):
    """项目附件：报告模板、数据文件、说明文档等（文件本体在存储里，这里只存关联）。"""

    __tablename__ = "project_file"
    __table_args__ = (
        UniqueConstraint("project_id", "file_asset_id", name="uk_project_file"),
        Index("idx_project_file_project", "project_id", "file_kind"),
    )

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "ProjectFile",
    "ProjectFileBase",
    "ProjectModule",
    "ProjectModuleBase",
    "ProjectStageTemplate",
    "ProjectStageTemplateBase",
    "TrainingProject",
    "TrainingProjectBase",
]
