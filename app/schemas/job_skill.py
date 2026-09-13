"""岗位与技能成长 Schema。"""

from datetime import datetime
from decimal import Decimal

from sqlmodel import Field, SQLModel

from app.models.job_skill import (
    GrowthRuleBase,
    JobBase,
    JobSkillBase,
    ProjectSkillBase,
    SkillNodeBase,
    SkillNodeDependencyBase,
    SkillTreeBase,
    StudentJobBase,
    StudentSkillBase,
)
from app.schemas.base import CreatedAtRead, SoftDeleteRead, TimestampRead

# ------------------------------------------------------------------------- 岗位


class JobCreate(JobBase):
    pass


class JobUpdate(SQLModel):
    job_name: str | None = Field(default=None, max_length=100)
    direction_tag: str | None = Field(default=None, max_length=50)
    recommended_level: str | None = Field(default=None, max_length=20)
    scene: str | None = Field(default=None, max_length=100)
    description: str | None = None
    heat: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=20)
    created_by: int | None = None


class JobRead(TimestampRead, SoftDeleteRead, JobBase):
    id: int


class JobDetail(JobRead):
    """岗位详情：带已关联技能数。"""

    skill_count: int = Field(default=0, description="已关联技能节点数")


class JobSkillSetIn(SQLModel):
    """覆盖式设置岗位所需技能。"""

    skill_node_ids: list[int] = Field(default_factory=list, description="技能节点 ID 列表")


# ----------------------------------------------------------------- 学生-岗位选择


class StudentJobCreate(StudentJobBase):
    pass


class StudentJobUpdate(SQLModel):
    student_id: int | None = None
    job_id: int | None = None
    is_primary: bool | None = None
    switched_at: datetime | None = None


class StudentJobRead(TimestampRead, StudentJobBase):
    id: int


class StudentJobSetIn(SQLModel):
    """给学生选岗位；is_primary=True 时会自动清掉该学生原来的主岗位。"""

    job_id: int = Field(description="岗位 ID")
    is_primary: bool = Field(default=True, description="是否设为主岗位")


class StudentJobDetail(StudentJobRead):
    """学生选岗记录：带岗位名称。"""

    job_name: str | None = Field(default=None, description="岗位名称")


class StudentJobPrimaryIn(SQLModel):
    """切换主岗位标记。"""

    is_primary: bool = Field(description="true=设为主岗位（会自动取消该学生其它主岗位）")


# ---------------------------------------------------------------------- 技能树


class SkillTreeCreate(SkillTreeBase):
    pass


class SkillTreeUpdate(SQLModel):
    tree_code: str | None = Field(default=None, max_length=50)
    tree_name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=20)


class SkillTreeRead(TimestampRead, SoftDeleteRead, SkillTreeBase):
    id: int


# -------------------------------------------------------------------- 技能节点


class SkillNodeCreate(SkillNodeBase):
    pass


class SkillNodeUpdate(SQLModel):
    tree_id: int | None = None
    node_code: str | None = Field(default=None, max_length=50)
    node_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    unlock_note: str | None = Field(default=None, max_length=255)
    unlock_rule_json: dict | None = None
    status: str | None = Field(default=None, max_length=20)


class SkillNodeRead(TimestampRead, SoftDeleteRead, SkillNodeBase):
    id: int


class SkillNodeCreateIn(SQLModel):
    """在技能树下新建节点（tree_id 取自路径，不需要放在 body 里）。"""

    node_code: str = Field(max_length=50, description="技能节点编码")
    node_name: str = Field(max_length=100, description="技能节点名称")
    description: str | None = Field(default=None, description="说明/描述")
    unlock_note: str | None = Field(default=None, max_length=255, description="给学生看的解锁说明")
    unlock_rule_json: dict | None = Field(default=None, description="未解锁提示用的规则")
    status: str = Field(default="ENABLED", max_length=20, description="ENABLED / DISABLED")


class SkillNodeDetail(SkillNodeRead):
    """技能节点详情：带技能树名称与前置节点 ID。"""

    tree_name: str | None = Field(default=None, description="所属技能树名称")
    prerequisite_ids: list[int] = Field(default_factory=list, description="前置技能节点 ID 列表")


# ---------------------------------------------------------------- 技能前置依赖


class SkillNodeDependencyCreate(SkillNodeDependencyBase):
    pass


class SkillNodeDependencyRead(CreatedAtRead, SkillNodeDependencyBase):
    id: int


class SkillNodeDependencySetIn(SQLModel):
    """覆盖式设置前置技能（DAG）。"""

    prerequisite_node_ids: list[int] = Field(default_factory=list, description="前置技能节点 ID 列表")


# ---------------------------------------------------------------- 学生技能进度


class StudentSkillCreate(StudentSkillBase):
    pass


class StudentSkillUpdate(SQLModel):
    student_id: int | None = None
    skill_node_id: int | None = None
    level: int | None = Field(default=None, ge=0, le=99)
    progress: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    activated_at: datetime | None = None
    mastered_at: datetime | None = None
    source: str | None = Field(default=None, max_length=30)


class StudentSkillRead(TimestampRead, StudentSkillBase):
    id: int


class StudentSkillDetail(StudentSkillRead):
    """学生技能进度：带技能节点与技能树信息。"""

    node_code: str | None = Field(default=None, description="技能节点编码")
    node_name: str | None = Field(default=None, description="技能节点名称")
    tree_id: int | None = Field(default=None, description="所属技能树 ID")
    tree_name: str | None = Field(default=None, description="所属技能树名称")


class StudentSkillPatchIn(SQLModel):
    """手工调整学生技能（进度来源默认记为 MANUAL）。"""

    level: int | None = Field(default=None, ge=0, le=99, description="熟练等级（预留）")
    progress: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    activated_at: datetime | None = None
    mastered_at: datetime | None = None
    source: str = Field(default="MANUAL", max_length=30, description="进度来源，默认 MANUAL")


# ------------------------------------------------------------------ 岗位-技能


class JobSkillCreate(JobSkillBase):
    pass


class JobSkillRead(CreatedAtRead, JobSkillBase):
    id: int


# -------------------------------------------------------------------- 成长规则


class GrowthRuleCreate(GrowthRuleBase):
    pass


class GrowthRuleUpdate(SQLModel):
    level_type: str | None = Field(default=None, max_length=20)
    unlock_condition_json: dict | None = None
    skill_max_level: int | None = Field(default=None, ge=1, le=99)
    pass_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    level_description: str | None = None
    enabled: bool | None = None
    updated_by: int | None = None


class GrowthRuleRead(TimestampRead, GrowthRuleBase):
    id: int


# ------------------------------------------------------------------ 项目-技能


class ProjectSkillCreate(ProjectSkillBase):
    pass


class ProjectSkillRead(CreatedAtRead, ProjectSkillBase):
    id: int


__all__ = [
    "GrowthRuleCreate",
    "GrowthRuleRead",
    "GrowthRuleUpdate",
    "JobCreate",
    "JobDetail",
    "JobRead",
    "JobSkillCreate",
    "JobSkillRead",
    "JobSkillSetIn",
    "JobUpdate",
    "ProjectSkillCreate",
    "ProjectSkillRead",
    "SkillNodeCreate",
    "SkillNodeCreateIn",
    "SkillNodeDependencyCreate",
    "SkillNodeDependencyRead",
    "SkillNodeDependencySetIn",
    "SkillNodeDetail",
    "SkillNodeRead",
    "SkillNodeUpdate",
    "SkillTreeCreate",
    "SkillTreeRead",
    "SkillTreeUpdate",
    "StudentJobCreate",
    "StudentJobDetail",
    "StudentJobPrimaryIn",
    "StudentJobRead",
    "StudentJobSetIn",
    "StudentJobUpdate",
    "StudentSkillCreate",
    "StudentSkillDetail",
    "StudentSkillPatchIn",
    "StudentSkillRead",
    "StudentSkillUpdate",
]
