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


# ---------------------------------------------------------------- 技能前置依赖


class SkillNodeDependencyCreate(SkillNodeDependencyBase):
    pass


class SkillNodeDependencyRead(CreatedAtRead, SkillNodeDependencyBase):
    id: int


# ---------------------------------------------------------------- 学生技能进度


class StudentSkillCreate(StudentSkillBase):
    pass


class StudentSkillUpdate(SQLModel):
    student_id: int | None = None
    skill_node_id: int | None = None
    state: str | None = Field(default=None, max_length=20)
    level: int | None = Field(default=None, ge=0, le=99)
    progress: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    activated_at: datetime | None = None
    mastered_at: datetime | None = None
    source: str | None = Field(default=None, max_length=30)


class StudentSkillRead(TimestampRead, StudentSkillBase):
    id: int


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
