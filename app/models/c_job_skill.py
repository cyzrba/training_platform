"""C 域 · 岗位与技能成长（9 张表）。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import JSONB, TZDateTime
from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Job(Base, TimestampMixin, SoftDeleteMixin):
    """岗位主数据（方向、推荐等级、适配场景、热度、启用状态）。"""

    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("job_name", name="uk_job_name"),
        Index("idx_job_status", "status", "recommended_level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="岗位名称")
    direction_tag: Mapped[str | None] = mapped_column(String(50), comment="岗位方向标签")
    recommended_level: Mapped[str | None] = mapped_column(String(20), comment="BASIC/ADVANCED/EXPANDED")
    scene: Mapped[str | None] = mapped_column(String(100), comment="适配实训场景")
    description: Mapped[str | None] = mapped_column(Text, comment="说明/描述")
    heat: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), comment="热度（事件更新）"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ENABLED'"), comment="ENABLED/DISABLED"
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="创建人")


class StudentJob(Base, TimestampMixin):
    """学生-岗位选择，支持多岗位与主岗位标记。"""

    __tablename__ = "student_job"
    __table_args__ = (
        UniqueConstraint("student_id", "job_id", name="uk_student_job"),
        Index(
            "uk_student_job_primary",
            "student_id",
            unique=True,
            sqlite_where=text("is_primary"),
        ),
        Index("idx_student_job_student", "student_id", "is_primary"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"), comment="是否当前主岗位（每人至多一个）"
    )
    selected_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="选择时间"
    )
    switched_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="切换时间")


class SkillTree(Base, TimestampMixin, SoftDeleteMixin):
    """技能树体系（光学成像系 / 传统算法系 / 深度学习系 / 系统部署系）。"""

    __tablename__ = "skill_tree"
    __table_args__ = (UniqueConstraint("tree_code", name="uk_skill_tree_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tree_code: Mapped[str] = mapped_column(String(50), nullable=False, comment="技能树编码")
    tree_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="技能树名称")
    description: Mapped[str | None] = mapped_column(String(255), comment="说明/描述")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ENABLED'"), comment="ENABLED/DISABLED"
    )


class SkillNode(Base, TimestampMixin, SoftDeleteMixin):
    """技能节点（技能点），技能树上的最小能力单元。"""

    __tablename__ = "skill_node"
    __table_args__ = (
        UniqueConstraint("node_code", name="uk_skill_node_code"),
        Index("idx_skill_node_tree", "tree_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tree_id: Mapped[int] = mapped_column(ForeignKey("skill_tree.id"), nullable=False, comment="技能树 ID")
    node_code: Mapped[str] = mapped_column(String(50), nullable=False, comment="技能节点编码")
    node_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="技能节点名称")
    description: Mapped[str | None] = mapped_column(Text, comment="说明/描述")
    unlock_note: Mapped[str | None] = mapped_column(String(255), comment="给学生看的解锁说明")
    unlock_rule_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'"),
        comment="未解锁提示规则（权威关系在 project_skill）",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ENABLED'"), comment="ENABLED/DISABLED"
    )


class SkillNodeDependency(Base):
    """技能点之间的前置依赖（DAG）。"""

    __tablename__ = "skill_node_dependency"
    __table_args__ = (
        UniqueConstraint("node_id", "prerequisite_node_id", name="uk_skill_node_dep"),
        CheckConstraint("node_id <> prerequisite_node_id", name="chk_skill_node_not_self"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("skill_node.id"), nullable=False)
    prerequisite_node_id: Mapped[int] = mapped_column(ForeignKey("skill_node.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


class StudentSkill(Base, TimestampMixin):
    """学生每个技能点的进度与状态。"""

    __tablename__ = "student_skill"
    __table_args__ = (
        UniqueConstraint("student_id", "skill_node_id", name="uk_student_skill"),
        Index("idx_student_skill_node", "skill_node_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    skill_node_id: Mapped[int] = mapped_column(ForeignKey("skill_node.id"), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'LOCKED'"), comment="LOCKED/ACTIVATED/MASTERED"
    )
    level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), comment="熟练等级（预留）"
    )
    progress: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0"), comment="0~100：完成数 ÷ 关联项目总数"
    )
    activated_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="点亮时间")
    mastered_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="精通时间")
    source: Mapped[str | None] = mapped_column(String(30), comment="PROJECT/REVIEW/MANUAL")


class JobSkill(Base):
    """岗位-技能关联；岗位推荐按学生技能进度计算。"""

    __tablename__ = "job_skill"
    __table_args__ = (
        UniqueConstraint("job_id", "skill_node_id", name="uk_job_skill"),
        Index("idx_job_skill_skill", "skill_node_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False)
    skill_node_id: Mapped[int] = mapped_column(ForeignKey("skill_node.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


class GrowthRule(Base, TimestampMixin):
    """按实训层级统一配置解锁条件、技能书最大等级、及格线与等级说明。"""

    __tablename__ = "growth_rule"
    __table_args__ = (UniqueConstraint("level_type", name="uk_growth_rule_level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="BASIC/ADVANCED/EXPANDED")
    unlock_condition_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'"), comment="该层级的解锁条件"
    )
    skill_max_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"), comment="技能书最大等级"
    )
    pass_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("60"), comment="该层级项目完成及格线"
    )
    level_description: Mapped[str | None] = mapped_column(Text, comment="等级说明")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1"), comment="是否启用"
    )
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="最后修改人")


class ProjectSkill(Base):
    """项目-技能多对多：完成关联项目即推进技能进度。"""

    __tablename__ = "project_skill"
    __table_args__ = (
        UniqueConstraint("project_id", "skill_node_id", name="uk_project_skill"),
        Index("idx_project_skill_node", "skill_node_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("training_project.id"), nullable=False)
    skill_node_id: Mapped[int] = mapped_column(ForeignKey("skill_node.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


__all__ = [
    "GrowthRule",
    "Job",
    "JobSkill",
    "ProjectSkill",
    "SkillNode",
    "SkillNodeDependency",
    "SkillTree",
    "StudentJob",
    "StudentSkill",
]
