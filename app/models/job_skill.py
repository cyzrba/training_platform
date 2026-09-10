"""岗位与技能成长：岗位、技能树、技能节点、学生技能进度、成长规则。"""

from datetime import datetime
from decimal import Decimal

from sqlmodel import (
    JSON,
    BigInteger,
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

from app.core.types import TZDateTime, utc_now
from app.models.base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin

# ------------------------------------------------------------------------- 岗位


class JobBase(SQLModel):
    job_name: str = Field(max_length=100, description="岗位名称")
    direction_tag: str | None = Field(default=None, max_length=50, description="岗位方向标签")
    recommended_level: str | None = Field(
        default=None, max_length=20, description="BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展"
    )
    scene: str | None = Field(default=None, max_length=100, description="适配实训场景")
    description: str | None = Field(default=None, sa_type=Text, description="说明/描述")
    heat: int = Field(
        default=0,
        sa_type=BigInteger,
        description="热度（事件更新）",
        sa_column_kwargs={"server_default": text("0")},
    )
    status: str = Field(
        default="ENABLED",
        max_length=20,
        description="ENABLED 启用 / DISABLED 停用",
        sa_column_kwargs={"server_default": text("'ENABLED'")},
    )
    created_by: int | None = Field(default=None, foreign_key="sys_user.id", description="创建人 ID")


class Job(Base, TimestampMixin, SoftDeleteMixin, JobBase, table=True):
    """岗位主数据（方向、推荐等级、适配场景、热度、启用状态）。"""

    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("job_name", name="uk_job_name"),
        Index("idx_job_status", "status", "recommended_level"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ----------------------------------------------------------------- 学生-岗位选择


class StudentJobBase(SQLModel):
    student_id: int = Field(foreign_key="sys_user.id", description="学生 ID")
    job_id: int = Field(foreign_key="job.id", description="岗位 ID")
    is_primary: bool = Field(
        default=False,
        description="是否当前主岗位（每名学生至多一个）",
        sa_column_kwargs={"server_default": text("0")},
    )
    selected_at: datetime = Field(
        default_factory=utc_now,
        sa_type=TZDateTime,
        description="选择时间",
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    )
    switched_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="切换时间")


class StudentJob(Base, TimestampMixin, StudentJobBase, table=True):
    """学生选择的岗位，支持多岗位与主岗位标记，切换岗位保留技能进度。"""

    __tablename__ = "student_job"
    __table_args__ = (
        UniqueConstraint("student_id", "job_id", name="uk_student_job"),
        Index("uk_student_job_primary", "student_id", unique=True, sqlite_where=text("is_primary")),
        Index("idx_student_job_student", "student_id", "is_primary"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ---------------------------------------------------------------------- 技能树


class SkillTreeBase(SQLModel):
    tree_code: str = Field(max_length=50, description="技能树编码")
    tree_name: str = Field(max_length=100, description="技能树名称")
    description: str | None = Field(default=None, max_length=255, description="说明/描述")
    status: str = Field(
        default="ENABLED",
        max_length=20,
        description="ENABLED / DISABLED",
        sa_column_kwargs={"server_default": text("'ENABLED'")},
    )


class SkillTree(Base, TimestampMixin, SoftDeleteMixin, SkillTreeBase, table=True):
    """技能树体系（光学成像系 / 传统算法系 / 深度学习系 / 系统部署系）。"""

    __tablename__ = "skill_tree"
    __table_args__ = (UniqueConstraint("tree_code", name="uk_skill_tree_code"),)

    id: int | None = Field(default=None, primary_key=True)


# -------------------------------------------------------------------- 技能节点


class SkillNodeBase(SQLModel):
    tree_id: int = Field(foreign_key="skill_tree.id", description="技能树 ID")
    node_code: str = Field(max_length=50, description="技能节点编码")
    node_name: str = Field(max_length=100, description="技能节点名称")
    description: str | None = Field(default=None, sa_type=Text, description="说明/描述")
    unlock_note: str | None = Field(default=None, max_length=255, description="给学生看的解锁说明")
    unlock_rule_json: dict = Field(
        default_factory=dict,
        sa_type=JSON,
        description="未解锁提示用的规则；权威关系在 project_skill",
        sa_column_kwargs={"server_default": text("'{}'")},
    )
    status: str = Field(
        default="ENABLED",
        max_length=20,
        description="ENABLED / DISABLED",
        sa_column_kwargs={"server_default": text("'ENABLED'")},
    )


class SkillNode(Base, TimestampMixin, SoftDeleteMixin, SkillNodeBase, table=True):
    """技能节点（技能点），技能树上的最小能力单元。"""

    __tablename__ = "skill_node"
    __table_args__ = (
        UniqueConstraint("node_code", name="uk_skill_node_code"),
        Index("idx_skill_node_tree", "tree_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ---------------------------------------------------------------- 技能前置依赖


class SkillNodeDependencyBase(SQLModel):
    node_id: int = Field(foreign_key="skill_node.id", description="技能节点 ID")
    prerequisite_node_id: int = Field(foreign_key="skill_node.id", description="前置技能节点 ID")


class SkillNodeDependency(Base, CreatedAtMixin, SkillNodeDependencyBase, table=True):
    """技能点之间的前置依赖（DAG），决定"先学什么才能学什么"。"""

    __tablename__ = "skill_node_dependency"
    __table_args__ = (
        UniqueConstraint("node_id", "prerequisite_node_id", name="uk_skill_node_dep"),
        CheckConstraint("node_id <> prerequisite_node_id", name="chk_skill_node_not_self"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ---------------------------------------------------------------- 学生技能进度


class StudentSkillBase(SQLModel):
    student_id: int = Field(foreign_key="sys_user.id", description="学生 ID")
    skill_node_id: int = Field(foreign_key="skill_node.id", description="技能节点 ID")
    state: str = Field(
        default="LOCKED",
        max_length=20,
        description="LOCKED 未解锁 / ACTIVATED 已激活 / MASTERED 已精通",
        sa_column_kwargs={"server_default": text("'LOCKED'")},
    )
    level: int = Field(
        default=0,
        sa_type=SmallInteger,
        description="熟练等级（预留：三态模式下可用 0/1/2）",
        sa_column_kwargs={"server_default": text("0")},
    )
    progress: Decimal = Field(
        default=Decimal("0"),
        sa_type=Numeric(5, 2),
        description="技能进度 0~100 = 完成数 ÷ 关联项目总数",
        sa_column_kwargs={"server_default": text("0")},
    )
    activated_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="点亮时间")
    mastered_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="精通时间")
    source: str | None = Field(default=None, max_length=30, description="进度来源 PROJECT / REVIEW / MANUAL")


class StudentSkill(Base, TimestampMixin, StudentSkillBase, table=True):
    """学生每个技能点的进度与状态。"""

    __tablename__ = "student_skill"
    __table_args__ = (
        UniqueConstraint("student_id", "skill_node_id", name="uk_student_skill"),
        Index("idx_student_skill_node", "skill_node_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 岗位-技能


class JobSkillBase(SQLModel):
    job_id: int = Field(foreign_key="job.id", description="岗位 ID")
    skill_node_id: int = Field(foreign_key="skill_node.id", description="技能节点 ID")


class JobSkill(Base, CreatedAtMixin, JobSkillBase, table=True):
    """岗位-技能关联；不设要求程度/权重，岗位推荐按学生技能进度计算。"""

    __tablename__ = "job_skill"
    __table_args__ = (
        UniqueConstraint("job_id", "skill_node_id", name="uk_job_skill"),
        Index("idx_job_skill_skill", "skill_node_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


# -------------------------------------------------------------------- 成长规则


class GrowthRuleBase(SQLModel):
    level_type: str = Field(max_length=20, description="实训层级 BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展")
    unlock_condition_json: dict = Field(
        default_factory=dict,
        sa_type=JSON,
        description="该层级的解锁条件",
        sa_column_kwargs={"server_default": text("'{}'")},
    )
    skill_max_level: int = Field(
        default=1,
        sa_type=SmallInteger,
        description="技能书最大等级",
        sa_column_kwargs={"server_default": text("1")},
    )
    pass_score: Decimal = Field(
        default=Decimal("60"),
        sa_type=Numeric(5, 2),
        description="该层级项目的完成及格线",
        sa_column_kwargs={"server_default": text("60")},
    )
    level_description: str | None = Field(default=None, sa_type=Text, description="等级说明")
    enabled: bool = Field(
        default=True, description="该层级成长规则是否启用", sa_column_kwargs={"server_default": text("1")}
    )
    updated_by: int | None = Field(default=None, foreign_key="sys_user.id", description="最后修改人 ID")


class GrowthRule(Base, TimestampMixin, GrowthRuleBase, table=True):
    """按实训层级统一配置解锁条件、技能书最大等级、及格线与等级说明。"""

    __tablename__ = "growth_rule"
    __table_args__ = (UniqueConstraint("level_type", name="uk_growth_rule_level"),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 项目-技能


class ProjectSkillBase(SQLModel):
    project_id: int = Field(foreign_key="training_project.id", description="项目 ID")
    skill_node_id: int = Field(foreign_key="skill_node.id", description="技能节点 ID")


class ProjectSkill(Base, CreatedAtMixin, ProjectSkillBase, table=True):
    """项目-技能多对多：完成关联项目即推进该技能进度（完成数/关联项目总数）。"""

    __tablename__ = "project_skill"
    __table_args__ = (
        UniqueConstraint("project_id", "skill_node_id", name="uk_project_skill"),
        Index("idx_project_skill_node", "skill_node_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "GrowthRule",
    "GrowthRuleBase",
    "Job",
    "JobBase",
    "JobSkill",
    "JobSkillBase",
    "ProjectSkill",
    "ProjectSkillBase",
    "SkillNode",
    "SkillNodeBase",
    "SkillNodeDependency",
    "SkillNodeDependencyBase",
    "SkillTree",
    "SkillTreeBase",
    "StudentJob",
    "StudentJobBase",
    "StudentSkill",
    "StudentSkillBase",
]
