"""C 域 Schema · 岗位与技能成长。"""

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.models.enums import (
    EnabledStatus,
    LearningLevel,
    SkillProgressSource,
    SkillState,
)
from app.schemas.base import (
    CreatedAtRead,
    ORMModel,
    ScoreField,
    SoftDeleteRead,
    TimestampRead,
)

# ---------------------------------------------------------------------- job


class JobBase(ORMModel):
    job_name: str = Field(min_length=1, max_length=100, description="岗位名称")
    direction_tag: str | None = Field(None, max_length=50, description="方向标签")
    recommended_level: LearningLevel | None = Field(None, description="推荐学习等级")
    scene: str | None = Field(None, max_length=100, description="适配实训场景")
    description: str | None = Field(None, description="岗位说明")
    heat: int = Field(0, ge=0, description="热度")
    status: EnabledStatus = Field(EnabledStatus.ENABLED, description="岗位状态")
    created_by: int | None = Field(None, description="创建人")


class JobCreate(JobBase):
    pass


class JobUpdate(ORMModel):
    job_name: str | None = Field(None, min_length=1, max_length=100)
    direction_tag: str | None = Field(None, max_length=50)
    recommended_level: LearningLevel | None = None
    scene: str | None = Field(None, max_length=100)
    description: str | None = None
    heat: int | None = Field(None, ge=0)
    status: EnabledStatus | None = None
    created_by: int | None = None


class JobRead(TimestampRead, SoftDeleteRead, JobBase):
    id: int


# --------------------------------------------------------------- student_job


class StudentJobBase(ORMModel):
    student_id: int = Field(description="学生 ID")
    job_id: int = Field(description="岗位 ID")
    is_primary: bool = Field(False, description="是否当前主岗位（每人至多一个）")
    selected_at: datetime | None = Field(None, description="选择时间，默认当前时间")
    switched_at: datetime | None = Field(None, description="切换时间")


class StudentJobCreate(StudentJobBase):
    pass


class StudentJobUpdate(ORMModel):
    student_id: int | None = None
    job_id: int | None = None
    is_primary: bool | None = None
    switched_at: datetime | None = None


class StudentJobRead(TimestampRead, StudentJobBase):
    id: int


# --------------------------------------------------------------- skill_tree


class SkillTreeBase(ORMModel):
    tree_code: str = Field(min_length=1, max_length=50, description="技能树编码")
    tree_name: str = Field(min_length=1, max_length=100, description="技能树名称")
    description: str | None = Field(None, max_length=255, description="说明")
    status: EnabledStatus = Field(EnabledStatus.ENABLED, description="启用状态")


class SkillTreeCreate(SkillTreeBase):
    pass


class SkillTreeUpdate(ORMModel):
    tree_code: str | None = Field(None, min_length=1, max_length=50)
    tree_name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=255)
    status: EnabledStatus | None = None


class SkillTreeRead(TimestampRead, SoftDeleteRead, SkillTreeBase):
    id: int


# --------------------------------------------------------------- skill_node


class SkillNodeBase(ORMModel):
    tree_id: int = Field(description="技能树 ID")
    node_code: str = Field(min_length=1, max_length=50, description="技能节点编码")
    node_name: str = Field(min_length=1, max_length=100, description="技能节点名称")
    description: str | None = Field(None, description="说明")
    unlock_note: str | None = Field(None, max_length=255, description="解锁说明文案")
    unlock_rule_json: dict = Field(default_factory=dict, description="未解锁提示规则")
    status: EnabledStatus = Field(EnabledStatus.ENABLED, description="启用状态")


class SkillNodeCreate(SkillNodeBase):
    pass


class SkillNodeUpdate(ORMModel):
    tree_id: int | None = None
    node_code: str | None = Field(None, min_length=1, max_length=50)
    node_name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    unlock_note: str | None = Field(None, max_length=255)
    unlock_rule_json: dict | None = None
    status: EnabledStatus | None = None


class SkillNodeRead(TimestampRead, SoftDeleteRead, SkillNodeBase):
    id: int


# ----------------------------------------------------- skill_node_dependency


class SkillNodeDependencyBase(ORMModel):
    node_id: int = Field(description="技能节点 ID")
    prerequisite_node_id: int = Field(description="前置技能节点 ID")


class SkillNodeDependencyCreate(SkillNodeDependencyBase):
    pass


class SkillNodeDependencyRead(CreatedAtRead, SkillNodeDependencyBase):
    id: int


# ------------------------------------------------------------- student_skill


class StudentSkillBase(ORMModel):
    student_id: int = Field(description="学生 ID")
    skill_node_id: int = Field(description="技能节点 ID")
    state: SkillState = Field(SkillState.LOCKED, description="技能状态")
    level: int = Field(0, ge=0, le=99, description="熟练等级（预留）")
    progress: ScoreField = Field(Decimal(0), description="技能进度 0~100")
    activated_at: datetime | None = Field(None, description="点亮时间")
    mastered_at: datetime | None = Field(None, description="精通时间")
    source: SkillProgressSource | None = Field(None, description="进度来源")


class StudentSkillCreate(StudentSkillBase):
    pass


class StudentSkillUpdate(ORMModel):
    student_id: int | None = None
    skill_node_id: int | None = None
    state: SkillState | None = None
    level: int | None = Field(None, ge=0, le=99)
    progress: ScoreField | None = None
    activated_at: datetime | None = None
    mastered_at: datetime | None = None
    source: SkillProgressSource | None = None


class StudentSkillRead(TimestampRead, StudentSkillBase):
    id: int


# ----------------------------------------------------------------- job_skill


class JobSkillBase(ORMModel):
    job_id: int = Field(description="岗位 ID")
    skill_node_id: int = Field(description="技能节点 ID")


class JobSkillCreate(JobSkillBase):
    pass


class JobSkillRead(CreatedAtRead, JobSkillBase):
    id: int


# -------------------------------------------------------------- growth_rule


class GrowthRuleBase(ORMModel):
    level_type: LearningLevel = Field(description="实训层级")
    unlock_condition_json: dict = Field(default_factory=dict, description="该层级解锁条件")
    skill_max_level: int = Field(1, ge=1, le=99, description="技能书最大等级")
    pass_score: ScoreField = Field(Decimal(60), description="该层级项目及格线")
    level_description: str | None = Field(None, description="等级说明")
    enabled: bool = Field(True, description="是否启用")
    updated_by: int | None = Field(None, description="最后修改人")


class GrowthRuleCreate(GrowthRuleBase):
    pass


class GrowthRuleUpdate(ORMModel):
    level_type: LearningLevel | None = None
    unlock_condition_json: dict | None = None
    skill_max_level: int | None = Field(None, ge=1, le=99)
    pass_score: ScoreField | None = None
    level_description: str | None = None
    enabled: bool | None = None
    updated_by: int | None = None


class GrowthRuleRead(TimestampRead, GrowthRuleBase):
    id: int


# ------------------------------------------------------------- project_skill


class ProjectSkillBase(ORMModel):
    project_id: int = Field(description="项目 ID")
    skill_node_id: int = Field(description="技能节点 ID")


class ProjectSkillCreate(ProjectSkillBase):
    pass


class ProjectSkillRead(CreatedAtRead, ProjectSkillBase):
    id: int


__all__ = [
    "GrowthRuleCreate",
    "GrowthRuleRead",
    "GrowthRuleUpdate",
    "JobCreate",
    "JobRead",
    "JobSkillCreate",
    "JobSkillRead",
    "JobUpdate",
    "ProjectSkillCreate",
    "ProjectSkillRead",
    "SkillNodeCreate",
    "SkillNodeDependencyCreate",
    "SkillNodeDependencyRead",
    "SkillNodeRead",
    "SkillNodeUpdate",
    "SkillTreeCreate",
    "SkillTreeRead",
    "SkillTreeUpdate",
    "StudentJobCreate",
    "StudentJobRead",
    "StudentJobUpdate",
    "StudentSkillCreate",
    "StudentSkillRead",
    "StudentSkillUpdate",
]
