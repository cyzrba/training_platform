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


# ------------------------------------------------------ 学生视角的项目全量详情


class ProjectReviewHistoryItem(SQLModel):
    """一次提交上的一条评审记录（AI 自动评审 / 教师复审）。"""

    review_id: int = Field(description="评审记录 ID")
    review_kind: str = Field(description="AI 自动评审 / TEACHER 教师复审")
    reviewer_id: int | None = Field(default=None, description="教师 ID；AI 评审为空")
    reviewer_name: str | None = Field(default=None, description="教师姓名；AI 评审为空")
    status: str = Field(default="FINAL", description="DRAFT 草稿 / FINAL 最终")
    version_no: int = Field(default=1, description="同一提交的评审版本号")
    total_score: float | None = Field(default=None, description="本次评审总分")
    conclusion: str | None = Field(default=None, description="PASS / FAIL")
    comment: str | None = Field(default=None, description="评语 / 批注")
    dimensions: list[dict] = Field(default_factory=list, description="各关卡的维度得分与理由")
    created_at: datetime = Field(description="评审创建时间")
    finished_at: datetime | None = Field(default=None, description="评审结束时间")


class ProjectSubmissionHistoryItem(SQLModel):
    """一次整单提交：提交次数、日期与该次提交收到的评语。"""

    submission_id: int = Field(description="提交记录 ID")
    attempt_no: int = Field(default=1, description="所属闯关轮次")
    submit_no: int = Field(default=1, description="该轮次的第几次提交")
    status: str = Field(description="提交状态（PENDING_AI / REVIEWED 等）")
    final_conclusion: str | None = Field(default=None, description="最终结论 PASS / FAIL")
    total_score: float | None = Field(default=None, description="本次提交总分")
    submitted_at: datetime = Field(description="提交时间")
    reviewed_at: datetime | None = Field(default=None, description="评审完成时间")
    objection_reason: str | None = Field(default=None, description="学生对 AI 结果的异议说明")
    is_starred: bool = Field(default=False, description="教师是否标星")
    reviews: list[ProjectReviewHistoryItem] = Field(default_factory=list, description="AI / 教师评语")


class TrainingProjectLevelDetail(SQLModel):
    """一个关卡：模块库信息 + 子标题（含每个子标题的简介）+ 学生当前作答状态。"""

    project_module_id: int = Field(description="项目模块 ID（该项目里的这个关卡）")
    attempt_stage_id: int | None = Field(
        default=None, description="本轮该关卡的作答行 ID；用它调保存作答接口，未开始闯关时为空"
    )
    stage_no: int = Field(description="关卡顺序，从 1 开始")
    stage_key: str | None = Field(default=None, description="关卡编码")
    stage_name: str = Field(description="关卡名称")
    description: str | None = Field(default=None, description="关卡简介（做什么）")
    requirement: str | None = Field(default=None, description="作答要求")
    accept_standard: str | None = Field(default=None, description="验收标准")
    weight: float = Field(default=0, description="分值占比")
    required: bool = Field(default=True, description="是否必填")
    sub_titles: list[StageItem] = Field(default_factory=list, description="子标题与每个子标题的填写简介")
    is_filled: bool = Field(default=False, description="该关卡是否已填写完成（最新一轮）")
    filled_at: datetime | None = Field(default=None, description="填写完成时间")
    answer_text: str | None = Field(default=None, description="本轮已保存的作答内容（草稿与已填完都在这）")
    answer_saved_at: datetime | None = Field(default=None, description="本轮该关卡作答的最后保存时间")
    file_count: int = Field(default=0, description="该关卡已上传的附件数")


class StudentTrainingProjectDetail(SQLModel):
    """学生在某个实训项目上的全量详情：任务简介、关卡与子标题、提交历史与评语。"""

    student_id: int = Field(description="学生 ID")
    project_id: int = Field(description="项目 ID")
    project_name: str = Field(description="实训项目名称")
    project_level: str = Field(description="项目层级 BASIC / ADVANCED / EXPANDED")
    level_name: str = Field(description="层级名称：基础 / 进阶 / 拓展")
    difficulty: int = Field(default=3, description="难度 1~5")
    intro: str | None = Field(default=None, description="任务简介")
    job_id: int | None = Field(default=None, description="所属岗位 ID")
    job_name: str | None = Field(default=None, description="所属岗位名称")
    status: str = Field(default="NOT_STARTED", description="该学生在这个项目上的状态")
    best_score: float | None = Field(default=None, description="最高分")
    total_score: float | None = Field(default=None, description="最新成绩")
    progress: float = Field(default=0, description="关卡进度百分比 0~100")
    level_total: int = Field(default=0, description="关卡总数")
    level_done: int = Field(default=0, description="已完成关卡数（最新一轮已填写的关卡数）")
    attempt_count: int = Field(default=0, description="闯关轮次数量")
    current_attempt_id: int | None = Field(
        default=None, description="当前（最新）轮次 ID；保存本轮作答时用它，还没开始闯关时为空"
    )
    current_attempt_no: int | None = Field(default=None, description="当前（最新）轮次序号；还没开始时为空")
    submission_count: int = Field(default=0, description="历史提交次数")
    skill_nodes: list[ProjectSkillNodeItem] = Field(default_factory=list, description="关联的技能点")
    levels: list[TrainingProjectLevelDetail] = Field(default_factory=list, description="关卡列表（含子标题）")
    submissions: list[ProjectSubmissionHistoryItem] = Field(
        default_factory=list, description="历史提交（按提交时间倒序，含评语）"
    )


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


class AttemptAnswerItem(SQLModel):
    """一次保存里的一个关卡作答。"""

    attempt_stage_id: int = Field(description="关卡作答行 ID（详情接口里每关的 attempt_stage_id）")
    answer_text: str | None = Field(default=None, description="作答内容")
    is_filled: bool | None = Field(
        default=None, description="是否标记该关卡为已完成；不传=只存草稿，不动完成状态"
    )


class AttemptAnswersSaveIn(SQLModel):
    """保存作答：一次保存本轮多个关卡（只传改过的关卡即可，没传的不动）。"""

    answers: list[AttemptAnswerItem] = Field(
        min_length=1, max_length=50, description="要保存的关卡作答，一次最多 50 条"
    )


class AttemptAnswersSaveResult(SQLModel):
    """保存作答的结果：存了几关、本轮最新进度。"""

    attempt_id: int = Field(description="闯关轮次 ID")
    attempt_no: int = Field(default=1, description="轮次序号")
    saved_count: int = Field(default=0, description="本次保存的关卡数")
    saved_stage_ids: list[int] = Field(default_factory=list, description="本次保存的关卡作答行 ID")
    filled_stage_count: int = Field(default=0, description="本轮已填写完成的关卡数")
    stage_total: int = Field(default=0, description="本轮关卡总数")
    progress: float = Field(default=0, description="本轮进度百分比 0~100")
    saved_at: datetime = Field(description="本次保存时间")


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
    "AttemptAnswerItem",
    "AttemptAnswersSaveIn",
    "AttemptAnswersSaveResult",
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
    "ProjectReviewHistoryItem",
    "ProjectSubmissionHistoryItem",
    "AttemptDetail",
    "StudentProjectCreate",
    "StudentProjectDetail",
    "StudentProjectRead",
    "StudentTrainingProject",
    "StudentTrainingProjectDetail",
    "StudentProjectUpdate",
    "TrainingProjectLevelDetail",
    "TrainingAttemptCreate",
    "TrainingAttemptRead",
    "TrainingAttemptUpdate",
]
