"""D 域 · 实训项目（3 张表）。"""

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class TrainingProject(Base, TimestampMixin, SoftDeleteMixin):
    """实训项目主数据；及格线不在此配置，统一读 growth_rule.pass_score。"""

    __tablename__ = "training_project"
    __table_args__ = (
        UniqueConstraint("project_name", name="uk_training_project_name"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="chk_training_project_difficulty"),
        Index("idx_training_project_status", "project_level", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_name: Mapped[str] = mapped_column(String(150), nullable=False, comment="项目名称")
    project_level: Mapped[str] = mapped_column(String(20), nullable=False, comment="BASIC/ADVANCED/EXPANDED")
    difficulty: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3"), comment="难度 1~5"
    )
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"), comment="绑定岗位（可空）")
    description: Mapped[str | None] = mapped_column(Text, comment="说明/描述")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'DRAFT'"), comment="DRAFT/PUBLISHED/OFF_SHELF"
    )
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="创建人")

    modules: Mapped[list["ProjectModule"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectModule.stage_no",
    )


class ProjectStageTemplate(Base, TimestampMixin):
    """标准化模块库（七大模块默认配置），教师建项目时从中挑选。"""

    __tablename__ = "project_stage_template"
    __table_args__ = (UniqueConstraint("stage_key", name="uk_stage_template_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stage_key: Mapped[str] = mapped_column(String(50), nullable=False, comment="七大模块 code")
    stage_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="模块名称")
    description: Mapped[str | None] = mapped_column(Text, comment="说明/描述")
    default_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1"), comment="默认是否必填"
    )
    default_weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0"), comment="默认分值占比"
    )
    default_requirement: Mapped[str | None] = mapped_column(Text, comment="默认作答要求")
    default_accept_standard: Mapped[str | None] = mapped_column(Text, comment="默认验收标准")
    sort_no: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="展示排序"
    )


class ProjectModule(Base, TimestampMixin):
    """项目模块组成：一条 = 项目中的一个模块（可来自模块库，也可自定义）。"""

    __tablename__ = "project_module"
    __table_args__ = (
        UniqueConstraint("project_id", "stage_key", name="uk_project_module_key"),
        UniqueConstraint("project_id", "stage_no", name="uk_project_module_no"),
        Index("idx_project_module_project", "project_id", "stage_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("training_project.id"), nullable=False)
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_stage_template.id"), comment="来自模块库；自定义模块为空"
    )
    stage_key: Mapped[str | None] = mapped_column(String(50), comment="标准模块 code；自定义为空")
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="模块名称")
    stage_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="项目内填写顺序")
    requirement: Mapped[str | None] = mapped_column(Text, comment="作答要求（覆盖模板）")
    accept_standard: Mapped[str | None] = mapped_column(Text, comment="验收标准（覆盖模板）")
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1"), comment="是否必填"
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0"), comment="分值占比"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1"), comment="是否启用"
    )

    project: Mapped[TrainingProject] = relationship(back_populates="modules")


__all__ = ["ProjectModule", "ProjectStageTemplate", "TrainingProject"]
