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
    node_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    unlock_note: str | None = Field(default=None, max_length=255)
    unlock_rule_json: dict | None = None
    status: str | None = Field(default=None, max_length=20)


class SkillNodeRead(TimestampRead, SoftDeleteRead, SkillNodeBase):
    id: int


class SkillNodeCreateIn(SQLModel):
    """在技能树下新建节点（tree_id 取自路径，不需要放在 body 里）。"""

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


# ---------------------------------------------------------------- 岗位推荐视图


class JobSkillProgressItem(SQLModel):
    """岗位关联的一个技能点：进度来自 student_skill，项目数由 project_skill 推导。"""

    skill_node_id: int = Field(description="技能节点 ID")
    node_name: str = Field(description="技能节点名称")
    progress: float = Field(default=0, description="该技能点的进度 0~100")


class JobSkillGroup(SQLModel):
    """按技能树（四大体系）分组的岗位技能点。"""

    tree_id: int = Field(description="技能树 ID")
    tree_name: str | None = Field(default=None, description="技能树名称")
    skill_total_count: int = Field(default=0, description="该体系下岗位关联的技能点总数")
    skill_done_count: int = Field(default=0, description="其中进度已达 100% 的个数")
    skills: list[JobSkillProgressItem] = Field(default_factory=list, description="技能点进度明细")


class JobRecommendation(SQLModel):
    """岗位推荐条目：岗位画像 + 该学生的技能匹配情况。"""

    job_id: int
    job_name: str = Field(description="岗位名称")
    direction_tag: str | None = Field(default=None, description="岗位方向标签")
    recommended_level: str | None = Field(
        default=None, description="推荐等级 BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展"
    )
    scene: str | None = Field(default=None, description="适配实训场景")
    description: str | None = Field(default=None, description="岗位描述")
    heat: int = Field(default=0, description="岗位热度")
    match_score: float = Field(default=0, description="匹配度 0~100 = 岗位关联技能点进度均值，排序依据")
    skill_total_count: int = Field(default=0, description="岗位关联技能点总数")
    skill_done_count: int = Field(default=0, description="其中进度已达 100% 的个数")
    project_total_count: int = Field(default=0, description="岗位关联的已发布项目总数")
    project_done_count: int = Field(default=0, description="其中该学生已完成的个数")
    skill_groups: list[JobSkillGroup] = Field(
        default_factory=list, description="岗位技能点，按技能树体系分组"
    )


# ------------------------------------------------------------ 技能树进度视图


class SkillNodeProgressItem(SQLModel):
    """技能节点 + 该学生的进度。"""

    skill_node_id: int = Field(description="技能节点 ID")
    node_name: str = Field(description="技能节点名称")
    description: str | None = Field(default=None, description="技能点说明")
    status: str = Field(default="ENABLED", description="ENABLED / DISABLED")
    progress: float = Field(default=0, description="技能点进度 0~100")
    project_total: int = Field(default=0, description="培养该技能点的已发布项目总数")
    project_done: int = Field(default=0, description="其中该学生已完成的个数")


class SkillTreeProgress(SQLModel):
    """技能树 + 该树的总进度与该学生的节点进度。"""

    tree_id: int = Field(description="技能树 ID")
    tree_name: str = Field(description="技能树名称")
    description: str | None = Field(default=None, description="技能树说明")
    status: str = Field(default="ENABLED", description="ENABLED / DISABLED")
    total: int = Field(default=0, description="该技能树的技能点总数")
    done: int = Field(default=0, description="其中进度已达 100% 的个数")
    percent: float = Field(default=0, description="该技能树的总进度 0~100 = 技能点进度均值")
    nodes: list[SkillNodeProgressItem] = Field(default_factory=list, description="技能节点列表")


class SkillTreeProgressOverview(SQLModel):
    """技能树总览：全部技能树与技能节点 + 整体统计（四个体系的整体进度等）。"""

    student_id: int = Field(description="学生 ID")
    tree_count: int = Field(default=0, description="技能树数量")
    total_nodes: int = Field(default=0, description="所有技能点总数")
    done_nodes: int = Field(default=0, description="进度已达 100% 的技能点个数")
    overall_percent: float = Field(default=0, description="四个技能树的整体进度 0~100 = 全部技能点进度均值")
    trees: list[SkillTreeProgress] = Field(default_factory=list, description="技能树列表（含节点进度）")


# ------------------------------------------------------ 岗位项目分档进度视图


class JobLevelProjectProgress(SQLModel):
    """某一层级（基础 / 进阶 / 拓展）上岗位关联项目的总数与已完成数。"""

    level_type: str = Field(description="层级 BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展")
    level_name: str = Field(description="层级名称：基础 / 进阶 / 拓展")
    total: int = Field(default=0, description="该层级下岗位关联的已发布项目总数")
    completed: int = Field(default=0, description="其中该学生已完成的个数")
    percent: float = Field(default=0, description="该层级的完成占比 0~100")


class JobProjectProgress(SQLModel):
    """学生所选岗位的实训项目进度：基础 / 进阶 / 拓展三档的总数与已完成数。"""

    student_id: int = Field(description="学生 ID")
    job_id: int | None = Field(default=None, description="统计的岗位 ID；没选岗位时为 null")
    job_name: str | None = Field(default=None, description="岗位名称")
    is_primary: bool = Field(default=False, description="该岗位是否为学生当前主岗位")
    total: int = Field(default=0, description="三档项目总数合计")
    completed: int = Field(default=0, description="三档已完成数合计")
    levels: list[JobLevelProjectProgress] = Field(
        default_factory=list, description="按基础 / 进阶 / 拓展顺序的分档进度"
    )


__all__ = [
    "GrowthRuleCreate",
    "GrowthRuleRead",
    "GrowthRuleUpdate",
    "JobCreate",
    "JobDetail",
    "JobLevelProjectProgress",
    "JobProjectProgress",
    "JobRead",
    "JobRecommendation",
    "JobSkillCreate",
    "JobSkillGroup",
    "JobSkillProgressItem",
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
    "SkillNodeProgressItem",
    "SkillNodeRead",
    "SkillNodeUpdate",
    "SkillTreeCreate",
    "SkillTreeProgress",
    "SkillTreeProgressOverview",
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
