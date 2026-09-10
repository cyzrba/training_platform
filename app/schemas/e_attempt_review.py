"""E 域 Schema · 闯关过程与评审。"""

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.models.enums import (
    AiJobStatus,
    AttemptStatus,
    Conclusion,
    FileBizType,
    FileStatus,
    ReviewKind,
    ReviewStatus,
    StudentProjectStatus,
    SubmissionStatus,
)
from app.schemas.base import (
    CreatedAtRead,
    ORMModel,
    ScoreField,
    TimestampRead,
)

# --------------------------------------------------------------- file_asset


class FileAssetBase(ORMModel):
    uploader_id: int | None = Field(None, description="上传人")
    bucket: str = Field(min_length=1, max_length=100, description="对象存储桶")
    object_key: str = Field(min_length=1, max_length=255, description="对象键")
    original_name: str = Field(min_length=1, max_length=255, description="原始文件名")
    content_type: str | None = Field(None, max_length=100, description="MIME 类型")
    size_bytes: int = Field(0, ge=0, description="文件大小（字节）")
    sha256: str | None = Field(None, max_length=64, description="文件摘要")
    biz_type: FileBizType = Field(description="文件业务类型")
    status: FileStatus = Field(FileStatus.ACTIVE, description="文件状态")


class FileAssetCreate(FileAssetBase):
    pass


class FileAssetUpdate(ORMModel):
    original_name: str | None = Field(None, min_length=1, max_length=255)
    content_type: str | None = Field(None, max_length=100)
    sha256: str | None = Field(None, max_length=64)
    status: FileStatus | None = None


class FileAssetRead(CreatedAtRead, FileAssetBase):
    id: int


# ---------------------------------------------------------- student_project


class StudentProjectBase(ORMModel):
    student_id: int = Field(description="学生 ID")
    project_id: int = Field(description="项目 ID")
    status: StudentProjectStatus = Field(StudentProjectStatus.NOT_STARTED, description="实训记录状态")
    progress: ScoreField = Field(Decimal(0), description="进度 0~100")
    total_score: ScoreField | None = Field(None, description="当前（最新）成绩")
    best_score: ScoreField | None = Field(None, description="历史最高成绩")
    attempt_count: int = Field(0, ge=0, le=999, description="闯关轮次数量")
    started_at: datetime | None = Field(None, description="开始时间")
    completed_at: datetime | None = Field(None, description="完成时间")
    completed_score: ScoreField | None = Field(None, description="完成时分数快照")


class StudentProjectCreate(StudentProjectBase):
    pass


class StudentProjectUpdate(ORMModel):
    status: StudentProjectStatus | None = None
    progress: ScoreField | None = None
    total_score: ScoreField | None = None
    best_score: ScoreField | None = None
    attempt_count: int | None = Field(None, ge=0, le=999)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    completed_score: ScoreField | None = None


class StudentProjectRead(TimestampRead, StudentProjectBase):
    id: int


# -------------------------------------------------------- training_attempt


class TrainingAttemptBase(ORMModel):
    student_project_id: int = Field(description="学生实训记录 ID")
    attempt_no: int = Field(ge=1, le=999, description="轮次序号")
    status: AttemptStatus = Field(AttemptStatus.IN_PROGRESS, description="轮次状态")
    filled_stage_count: int = Field(0, ge=0, description="已填写模块数")
    submitted_at: datetime | None = Field(None, description="整单提交时间")
    finished_at: datetime | None = Field(None, description="评审结束时间")
    total_score: ScoreField | None = Field(None, description="本轮得分")


class TrainingAttemptCreate(TrainingAttemptBase):
    pass


class TrainingAttemptUpdate(ORMModel):
    status: AttemptStatus | None = None
    filled_stage_count: int | None = Field(None, ge=0)
    submitted_at: datetime | None = None
    finished_at: datetime | None = None
    total_score: ScoreField | None = None


class TrainingAttemptRead(TimestampRead, TrainingAttemptBase):
    id: int


# ------------------------------------------------------------ attempt_stage


class AttemptStageBase(ORMModel):
    attempt_id: int = Field(description="闯关轮次 ID")
    project_module_id: int = Field(description="项目模块 ID")
    is_filled: bool = Field(False, description="该模块是否已填写完成")
    answer_text: str | None = Field(None, description="作答文本内容")
    filled_at: datetime | None = Field(None, description="填写完成时间")


class AttemptStageCreate(AttemptStageBase):
    pass


class AttemptStageUpdate(ORMModel):
    is_filled: bool | None = None
    answer_text: str | None = None
    filled_at: datetime | None = None


class AttemptStageRead(TimestampRead, AttemptStageBase):
    id: int


class AttemptStageFileBase(ORMModel):
    attempt_stage_id: int = Field(description="模块作答 ID")
    file_asset_id: int = Field(description="文件 ID")


class AttemptStageFileCreate(AttemptStageFileBase):
    pass


class AttemptStageFileRead(CreatedAtRead, AttemptStageFileBase):
    id: int


# ------------------------------------------------------ project_submission


class ProjectSubmissionBase(ORMModel):
    attempt_id: int = Field(description="闯关轮次 ID")
    submit_no: int = Field(ge=1, le=999, description="整单提交序号")
    status: SubmissionStatus = Field(SubmissionStatus.PENDING_AI, description="评审状态")
    final_conclusion: Conclusion | None = Field(None, description="最终结论")
    total_score: ScoreField | None = Field(None, description="本次提交总分")
    objection_reason: str | None = Field(None, max_length=500, description="异议说明")
    is_starred: bool = Field(False, description="教师标星")
    submitted_at: datetime | None = Field(None, description="整单提交时间，默认当前时间")
    withdrawn_at: datetime | None = Field(None, description="撤回时间")
    reviewed_at: datetime | None = Field(None, description="评审完成时间")


class ProjectSubmissionCreate(ProjectSubmissionBase):
    pass


class ProjectSubmissionUpdate(ORMModel):
    status: SubmissionStatus | None = None
    final_conclusion: Conclusion | None = None
    total_score: ScoreField | None = None
    objection_reason: str | None = Field(None, max_length=500)
    is_starred: bool | None = None
    withdrawn_at: datetime | None = None
    reviewed_at: datetime | None = None


class ProjectSubmissionRead(TimestampRead, ProjectSubmissionBase):
    id: int


# ----------------------------------------------------------- review_record


class DimensionScore(ORMModel):
    """评审维度明细（dimension_json 的元素结构）。"""

    name: str = Field(min_length=1, max_length=100, description="维度名称")
    score: ScoreField | None = Field(None, description="维度得分")
    weight: ScoreField | None = Field(None, description="维度权重")
    reason: str | None = Field(None, description="维度评语/理由")


class ReviewRecordBase(ORMModel):
    submission_id: int = Field(description="整单提交 ID")
    review_kind: ReviewKind = Field(description="评审类型")
    version_no: int = Field(1, ge=1, le=999, description="版本号")
    status: ReviewStatus = Field(ReviewStatus.DRAFT, description="评审状态")
    reviewer_id: int | None = Field(None, description="教师；AI 为空")
    total_score: ScoreField | None = Field(None, description="本次评审总分")
    conclusion: Conclusion | None = Field(None, description="评审结论")
    comment: str | None = Field(None, description="评语/批注")
    dimension_json: list[DimensionScore] = Field(default_factory=list, description="维度明细")
    ai_model: str | None = Field(None, max_length=100, description="AI 模型名称")
    raw_json: dict = Field(default_factory=dict, description="AI 原始返回快照")
    finished_at: datetime | None = Field(None, description="结束时间")


class ReviewRecordCreate(ReviewRecordBase):
    pass


class ReviewRecordUpdate(ORMModel):
    status: ReviewStatus | None = None
    reviewer_id: int | None = None
    total_score: ScoreField | None = None
    conclusion: Conclusion | None = None
    comment: str | None = None
    dimension_json: list[DimensionScore] | None = None
    ai_model: str | None = Field(None, max_length=100)
    raw_json: dict | None = None
    finished_at: datetime | None = None


class ReviewRecordRead(CreatedAtRead, ReviewRecordBase):
    id: int


# ------------------------------------------------------------ review_ai_job


class ReviewAiJobBase(ORMModel):
    submission_id: int = Field(description="整单提交 ID")
    job_status: AiJobStatus = Field(AiJobStatus.QUEUED, description="任务状态")
    model_name: str | None = Field(None, max_length=100, description="执行批阅的 AI 模型")
    request_id: str | None = Field(None, max_length=100, description="AI 服务请求 ID")
    error_msg: str | None = Field(None, description="错误信息")
    attempt_count: int = Field(0, ge=0, le=999, description="已尝试次数")
    finished_at: datetime | None = Field(None, description="结束时间")


class ReviewAiJobCreate(ReviewAiJobBase):
    pass


class ReviewAiJobUpdate(ORMModel):
    job_status: AiJobStatus | None = None
    model_name: str | None = Field(None, max_length=100)
    request_id: str | None = Field(None, max_length=100)
    error_msg: str | None = None
    attempt_count: int | None = Field(None, ge=0, le=999)
    finished_at: datetime | None = None


class ReviewAiJobRead(CreatedAtRead, ReviewAiJobBase):
    id: int


class ProjectSubmissionDetail(ProjectSubmissionRead):
    """提交详情：附带模块作答与评审记录。"""

    attempt: TrainingAttemptRead | None = None
    stages: list[AttemptStageRead] = Field(default_factory=list)
    reviews: list[ReviewRecordRead] = Field(default_factory=list)


__all__ = [
    "AttemptStageCreate",
    "AttemptStageFileCreate",
    "AttemptStageFileRead",
    "AttemptStageRead",
    "AttemptStageUpdate",
    "DimensionScore",
    "FileAssetCreate",
    "FileAssetRead",
    "FileAssetUpdate",
    "ProjectSubmissionCreate",
    "ProjectSubmissionDetail",
    "ProjectSubmissionRead",
    "ProjectSubmissionUpdate",
    "ReviewAiJobCreate",
    "ReviewAiJobRead",
    "ReviewAiJobUpdate",
    "ReviewRecordCreate",
    "ReviewRecordRead",
    "ReviewRecordUpdate",
    "StudentProjectCreate",
    "StudentProjectRead",
    "StudentProjectUpdate",
    "TrainingAttemptCreate",
    "TrainingAttemptRead",
    "TrainingAttemptUpdate",
]
