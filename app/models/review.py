"""评审：整单评审记录与 AI 评审任务。"""

from datetime import datetime
from decimal import Decimal

from sqlmodel import (
    JSON,
    Field,
    Index,
    Numeric,
    SmallInteger,
    SQLModel,
    Text,
    UniqueConstraint,
    text,
)

from app.core.types import TZDateTime
from app.models.base import Base, CreatedAtMixin

# ------------------------------------------------------------------ 评审记录


class ReviewRecordBase(SQLModel):
    submission_id: int = Field(foreign_key="project_submission.id", description="整单提交 ID")
    review_kind: str = Field(max_length=20, description="评审类型 AI 自动评审 / TEACHER 教师复审")
    version_no: int = Field(
        default=1,
        sa_type=SmallInteger,
        description="版本号",
        sa_column_kwargs={"server_default": text("1")},
    )
    status: str = Field(
        default="DRAFT",
        max_length=20,
        description="DRAFT 草稿 / FINAL 最终",
        sa_column_kwargs={"server_default": text("'DRAFT'")},
    )
    reviewer_id: int | None = Field(default=None, foreign_key="sys_user.id", description="教师 ID；AI 为空")
    total_score: Decimal | None = Field(default=None, sa_type=Numeric(5, 2), description="本次评审总分 0~100")
    conclusion: str | None = Field(default=None, max_length=10, description="PASS / FAIL")
    comment: str | None = Field(default=None, sa_type=Text, description="评语/批注")
    dimension_json: list = Field(
        default_factory=list,
        sa_type=JSON,
        description="各维度得分与理由（JSON 数组）",
        sa_column_kwargs={"server_default": text("'[]'")},
    )
    ai_model: str | None = Field(default=None, max_length=100, description="AI 模型名称")
    raw_json: dict = Field(
        default_factory=dict,
        sa_type=JSON,
        description="AI 原始返回快照",
        sa_column_kwargs={"server_default": text("'{}'")},
    )
    finished_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="结束时间")


class ReviewRecord(Base, CreatedAtMixin, ReviewRecordBase, table=True):
    """整单评审记录（AI/教师）；维度明细以 JSON 存储，不单独建维度字典表。"""

    __tablename__ = "review_record"
    __table_args__ = (
        UniqueConstraint("submission_id", "review_kind", "version_no", name="uk_review_record"),
        Index("idx_review_record_submission", text("submission_id, review_kind, version_no DESC")),
    )

    id: int | None = Field(default=None, primary_key=True)


# --------------------------------------------------------------- AI 评审任务


class ReviewAiJobBase(SQLModel):
    submission_id: int = Field(foreign_key="project_submission.id", description="整单提交 ID")
    job_status: str = Field(
        default="QUEUED",
        max_length=20,
        description="QUEUED 排队 / PROCESSING 处理中 / SUCCEED 成功 / FAILED 失败",
        sa_column_kwargs={"server_default": text("'QUEUED'")},
    )
    model_name: str | None = Field(default=None, max_length=100, description="执行批阅的 AI 模型")
    request_id: str | None = Field(default=None, max_length=100, description="AI 服务请求 ID")
    error_msg: str | None = Field(default=None, sa_type=Text, description="错误信息")
    attempt_count: int = Field(
        default=0,
        sa_type=SmallInteger,
        description="已尝试次数（用于重试与对账）",
        sa_column_kwargs={"server_default": text("0")},
    )
    finished_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="结束时间")


class ReviewAiJob(Base, CreatedAtMixin, ReviewAiJobBase, table=True):
    """AI 评审的异步任务记录，用于重试与对账。"""

    __tablename__ = "review_ai_job"
    __table_args__ = (Index("idx_review_ai_job_status", "job_status", "created_at"),)

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "ReviewAiJob",
    "ReviewAiJobBase",
    "ReviewRecord",
    "ReviewRecordBase",
]
