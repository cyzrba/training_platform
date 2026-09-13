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
    comment: str | None = None
    dimension_json: list[DimensionScore] | None = None
    ai_model: str | None = Field(default=None, max_length=100)
    raw_json: dict | None = None
    finished_at: datetime | None = None


class ReviewRecordRead(CreatedAtRead, ReviewRecordBase):
    id: int


class SubmissionReviewIn(SQLModel):
    """给某次整单提交新增一条评审（AI 或教师）。

    评审人只给**分数 + 评语**：PASS/FAIL 由后端按项目层级的及格线（growth_rule.pass_score）判定。
    """

    review_kind: str = Field(default="TEACHER", max_length=20, description="AI / TEACHER")
    status: str = Field(default="FINAL", max_length=20, description="DRAFT 草稿 / FINAL 定稿")
    reviewer_id: int | None = Field(default=None, description="教师 ID；AI 评审留空")
    total_score: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        max_digits=5,
        decimal_places=2,
        description="本次评审总分，定稿必填（PASS/FAIL 按及格线自动判定）",
    )
    comment: str | None = Field(default=None, description="评语/批注")
    dimension_json: list[DimensionScore] = Field(default_factory=list, description="各维度得分与理由")
    ai_model: str | None = Field(default=None, max_length=100, description="AI 模型名称")
    raw_json: dict | None = Field(default=None, description="AI 原始返回快照")


class SkillRecalcResult(SQLModel):
    """技能进度重算结果。"""

    student_id: int = Field(description="学生 ID")
    updated: int = Field(description="重算的技能点数量")


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
    "SkillRecalcResult",
    "SubmissionReviewIn",
]
