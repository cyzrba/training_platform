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
    "FileAssetCreate",
    "FileAssetRead",
    "FileAssetUpdate",
    "ProjectSubmissionCreate",
    "ProjectSubmissionDetail",
    "ProjectSubmissionRead",
    "ProjectSubmissionUpdate",
    "StudentProjectCreate",
    "StudentProjectRead",
    "StudentProjectUpdate",
    "TrainingAttemptCreate",
    "TrainingAttemptRead",
    "TrainingAttemptUpdate",
]
