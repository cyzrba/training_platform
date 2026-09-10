"""评审 Schema。"""

from datetime import datetime
from decimal import Decimal

from sqlmodel import Field, SQLModel

from app.models.review import ReviewAiJobBase, ReviewRecordBase
from app.schemas.base import CreatedAtRead


class DimensionScore(SQLModel):
    """评审维度明细（dimension_json 的元素结构）。"""

    name: str = Field(max_length=100, description="维度名称")
    score: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2, description="维度得分"
    )
    weight: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2, description="维度权重"
    )
    reason: str | None = Field(default=None, description="维度评语/理由")


class ReviewRecordCreate(ReviewRecordBase):
    pass


class ReviewRecordUpdate(SQLModel):
    status: str | None = Field(default=None, max_length=20)
    reviewer_id: int | None = None
    total_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    conclusion: str | None = Field(default=None, max_length=10)
    comment: str | None = None
    dimension_json: list[DimensionScore] | None = None
    ai_model: str | None = Field(default=None, max_length=100)
    raw_json: dict | None = None
    finished_at: datetime | None = None


class ReviewRecordRead(CreatedAtRead, ReviewRecordBase):
    id: int


class ReviewAiJobCreate(ReviewAiJobBase):
    pass


class ReviewAiJobUpdate(SQLModel):
    job_status: str | None = Field(default=None, max_length=20)
    model_name: str | None = Field(default=None, max_length=100)
    request_id: str | None = Field(default=None, max_length=100)
    error_msg: str | None = None
    attempt_count: int | None = Field(default=None, ge=0, le=999)
    finished_at: datetime | None = None


class ReviewAiJobRead(CreatedAtRead, ReviewAiJobBase):
    id: int


__all__ = [
    "DimensionScore",
    "ReviewAiJobCreate",
    "ReviewAiJobRead",
    "ReviewAiJobUpdate",
    "ReviewRecordCreate",
    "ReviewRecordRead",
    "ReviewRecordUpdate",
]
