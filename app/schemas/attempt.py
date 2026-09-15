"""闯关过程 Schema。"""

from datetime import datetime
from decimal import Decimal

from sqlmodel import Field, SQLModel

from app.models.attempt import (
    AttemptStageBase,
    AttemptStageFileBase,
    FileAssetBase,
    ProjectSubmissionBase,
    StudentProjectBase,
    TrainingAttemptBase,
)
from app.schemas.base import CreatedAtRead, TimestampRead
from app.schemas.project import StageItem
from app.schemas.review import ReviewRecordRead

# --------------------------------------------------------------------- 文件


class FileAssetCreate(FileAssetBase):
    pass


class FileAssetUpdate(SQLModel):
    original_name: str | None = Field(default=None, max_length=255)
    content_type: str | None = Field(default=None, max_length=100)
    sha256: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=20)


class FileAssetRead(CreatedAtRead, FileAssetBase):
    id: int


# --------------------------------------------------------------- 学生实训记录


class StudentProjectCreate(StudentProjectBase):
    pass


class StudentProjectUpdate(SQLModel):
    status: str | None = Field(default=None, max_length=20)
    progress: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    total_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    best_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    attempt_count: int | None = Field(default=None, ge=0, le=999)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    completed_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)


class StudentProjectRead(TimestampRead, StudentProjectBase):
    id: int


class StudentProjectDetail(StudentProjectRead):
    """学生实训记录详情：带项目名称与闯关轮次。"""

    project_name: str | None = Field(default=None, description="项目名称")
    project_level: str | None = Field(default=None, description="项目层级")
    attempts: list["TrainingAttemptRead"] = Field(default_factory=list, description="闯关轮次")


# ------------------------------------------------- 学生的实训项目成长视图


class ProjectSkillNodeItem(SQLModel):
    """项目关联的一个技能点（带所属技能树体系）。"""

    skill_node_id: int = Field(description="技能节点 ID")
    node_code: str = Field(description="技能节点编码")
    node_name: str = Field(description="技能节点名称")
    tree_id: int = Field(description="所属技能树 ID")
    tree_code: str | None = Field(default=None, description="所属技能树编码")
    tree_name: str | None = Field(default=None, description="所属技能树名称")


class StudentTrainingProject(SQLModel):
    """学生的实训项目：项目信息 + 这个学生的最高分、关卡进度与状态。"""

    project_id: int = Field(description="项目 ID")
    project_name: str = Field(description="实训项目名称")
    project_level: str = Field(description="项目层级 BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展")
    job_id: int | None = Field(default=None, description="所属岗位 ID")
    job_name: str | None = Field(default=None, description="所属岗位名称")
    status: str = Field(
        default="NOT_STARTED",
        description=(
            "当前项目状态 NOT_STARTED 未开始 / IN_PROGRESS 进行中 / SUBMITTED 已提交 / COMPLETED 已完成"
        ),
    )
    best_score: float | None = Field(
        default=None, description="该学生在这个项目的最高分（历史最高，只升不降）"
    )
    total_score: float | None = Field(default=None, description="该学生在这个项目的最新成绩")
    progress: float = Field(default=0, description="关卡进度百分比 0~100 = 已填写关卡 ÷ 关卡总数")
    level_total: int = Field(default=0, description="关卡总数")
    level_done: int = Field(default=0, description="已完成关卡数（最新一轮闯关里已填写的关卡数）")
    skill_nodes: list[ProjectSkillNodeItem] = Field(default_factory=list, description="关联的技能点")


# ------------------------------------------------------------------- 闯关轮次


class TrainingAttemptCreate(TrainingAttemptBase):
    pass


class TrainingAttemptUpdate(SQLModel):
    status: str | None = Field(default=None, max_length=20)
    filled_stage_count: int | None = Field(default=None, ge=0)
    submitted_at: datetime | None = None
    finished_at: datetime | None = None
    total_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)


class TrainingAttemptRead(TimestampRead, TrainingAttemptBase):
    id: int


# ------------------------------------------------------------------- 模块作答


class AttemptStageCreate(AttemptStageBase):
    pass


class AttemptStageUpdate(SQLModel):
    is_filled: bool | None = None
    answer_text: str | None = None
    filled_at: datetime | None = None


class AttemptStageRead(TimestampRead, AttemptStageBase):
    id: int


class AttemptStageDetail(AttemptStageRead):
    """模块作答：带上关卡信息（名称/顺序/是否必填/填写引导子标题）。"""

    stage_no: int = Field(description="关卡顺序")
    stage_key: str | None = Field(default=None, description="关卡编码（取模块库）")
    stage_name: str | None = Field(default=None, description="关卡名称（取模块库）")
    required: bool = Field(default=True, description="是否必填")
    weight: Decimal = Field(default=Decimal(0), description="该关卡分值占比")
    items_json: list[StageItem] = Field(default_factory=list, description="教师为这个项目填的填写引导子标题")
    file_count: int = Field(default=0, description="附件数量")


class AttemptDetail(TrainingAttemptRead):
    """一轮闯关详情：带全部关卡作答。"""

    student_id: int = Field(description="学生 ID")
    project_id: int = Field(description="项目 ID")
    project_name: str | None = Field(default=None, description="项目名称")
    stages: list[AttemptStageDetail] = Field(default_factory=list, description="各关卡作答")


class AttemptStageSaveIn(SQLModel):
    """保存某个关卡的作答。"""

    answer_text: str | None = Field(default=None, description="作答内容")
    is_filled: bool | None = Field(default=None, description="是否填写完成；不传则按作答内容是否为空自动判断")


class AttemptStageFileCreate(AttemptStageFileBase):
    pass


class AttemptStageFileRead(CreatedAtRead, AttemptStageFileBase):
    id: int


# ------------------------------------------------------------------- 整单提交


class ProjectSubmissionCreate(ProjectSubmissionBase):
    pass


class ProjectSubmissionUpdate(SQLModel):
    status: str | None = Field(default=None, max_length=20)
    final_conclusion: str | None = Field(default=None, max_length=10)
    total_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    objection_reason: str | None = Field(default=None, max_length=500)
    is_starred: bool | None = None
    withdrawn_at: datetime | None = None
    reviewed_at: datetime | None = None


class ProjectSubmissionRead(TimestampRead, ProjectSubmissionBase):
    id: int


class ProjectSubmissionListRead(ProjectSubmissionRead):
    """教师看板用的提交行：带学生、项目与轮次信息。"""

    student_id: int | None = Field(default=None, description="学生 ID")
    project_id: int | None = Field(default=None, description="项目 ID")
    project_name: str | None = Field(default=None, description="项目名称")
    attempt_no: int | None = Field(default=None, description="第几轮闯关")


class SubmissionObjectionIn(SQLModel):
    """学生对 AI 评审结果提异议（会转给教师复核）。"""

    reason: str = Field(min_length=1, max_length=500, description="异议说明 / 留言，教师复核时能看到")


class ProjectSubmissionDetail(ProjectSubmissionRead):
    """提交详情：附带模块作答与评审记录。"""

    attempt: TrainingAttemptRead | None = None
    stages: list[AttemptStageRead] = Field(default_factory=list)
    reviews: list[ReviewRecordRead] = Field(default_factory=list)


__all__ = [
    "AttemptStageCreate",
    "AttemptStageDetail",
    "AttemptStageSaveIn",
    "AttemptStageFileCreate",
    "AttemptStageFileRead",
    "AttemptStageRead",
    "AttemptStageUpdate",
    "FileAssetCreate",
    "FileAssetRead",
    "FileAssetUpdate",
    "ProjectSubmissionCreate",
    "ProjectSubmissionDetail",
    "ProjectSubmissionListRead",
    "SubmissionObjectionIn",
    "ProjectSubmissionRead",
    "ProjectSubmissionUpdate",
    "ProjectSkillNodeItem",
    "AttemptDetail",
    "StudentProjectCreate",
    "StudentProjectDetail",
    "StudentProjectRead",
    "StudentTrainingProject",
    "StudentProjectUpdate",
    "TrainingAttemptCreate",
    "TrainingAttemptRead",
    "TrainingAttemptUpdate",
]
