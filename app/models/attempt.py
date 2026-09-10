"""闯关过程：文件、学生实训记录、闯关轮次、模块作答、整单提交。"""

from datetime import datetime
from decimal import Decimal

from sqlmodel import (
    BigInteger,
    Field,
    Index,
    Numeric,
    SmallInteger,
    SQLModel,
    Text,
    UniqueConstraint,
    text,
)

from app.core.types import TZDateTime, utc_now
from app.models.base import Base, CreatedAtMixin, TimestampMixin

# --------------------------------------------------------------------- 文件


class FileAssetBase(SQLModel):
    uploader_id: int | None = Field(default=None, foreign_key="sys_user.id", description="上传人 ID")
    bucket: str = Field(max_length=100, description="对象存储桶")
    object_key: str = Field(max_length=255, description="对象键")
    original_name: str = Field(max_length=255, description="原始文件名")
    content_type: str | None = Field(default=None, max_length=100, description="MIME 类型")
    size_bytes: int = Field(
        default=0,
        sa_type=BigInteger,
        description="文件大小（字节）",
        sa_column_kwargs={"server_default": text("0")},
    )
    sha256: str | None = Field(default=None, max_length=64, description="文件摘要")
    biz_type: str = Field(max_length=30, description="SUBMISSION / AVATAR / CERT_PDF / IMPORT / REPORT")
    status: str = Field(
        default="ACTIVE",
        max_length=20,
        description="ACTIVE 可用 / INVALID 已失效",
        sa_column_kwargs={"server_default": text("'ACTIVE'")},
    )


class FileAsset(Base, CreatedAtMixin, FileAssetBase, table=True):
    """文件元数据（作答附件、头像、证书 PDF、导入名单）。"""

    __tablename__ = "file_asset"
    __table_args__ = (UniqueConstraint("bucket", "object_key", name="uk_file_asset_object"),)

    id: int | None = Field(default=None, primary_key=True)


# --------------------------------------------------------------- 学生实训记录


class StudentProjectBase(SQLModel):
    student_id: int = Field(foreign_key="sys_user.id", description="学生 ID")
    project_id: int = Field(foreign_key="training_project.id", description="项目 ID")
    status: str = Field(
        default="NOT_STARTED",
        max_length=20,
        description="NOT_STARTED / IN_PROGRESS / SUBMITTED / COMPLETED",
        sa_column_kwargs={"server_default": text("'NOT_STARTED'")},
    )
    progress: Decimal = Field(
        default=Decimal("0"),
        sa_type=Numeric(5, 2),
        description="进度 0~100 = 已填写模块 ÷ 启用模块",
        sa_column_kwargs={"server_default": text("0")},
    )
    total_score: Decimal | None = Field(default=None, sa_type=Numeric(5, 2), description="当前（最新）成绩")
    best_score: Decimal | None = Field(default=None, sa_type=Numeric(5, 2), description="历史最高成绩")
    attempt_count: int = Field(
        default=0,
        sa_type=SmallInteger,
        description="闯关轮次数量",
        sa_column_kwargs={"server_default": text("0")},
    )
    started_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="开始时间")
    completed_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="完成时间")
    completed_score: Decimal | None = Field(
        default=None, sa_type=Numeric(5, 2), description="完成时的分数快照"
    )


class StudentProject(Base, TimestampMixin, StudentProjectBase, table=True):
    """学生实训记录：点击"开始闯关"时生成，承载进度、成绩与完成状态。"""

    __tablename__ = "student_project"
    __table_args__ = (
        UniqueConstraint("student_id", "project_id", name="uk_student_project"),
        Index("idx_student_project_student", "student_id", "status"),
        Index("idx_student_project_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------- 闯关轮次


class TrainingAttemptBase(SQLModel):
    student_project_id: int = Field(foreign_key="student_project.id", description="学生实训记录 ID")
    attempt_no: int = Field(sa_type=SmallInteger, description="轮次序号")
    status: str = Field(
        default="IN_PROGRESS",
        max_length=20,
        description="IN_PROGRESS / SUBMITTED / COMPLETED / ABANDONED",
        sa_column_kwargs={"server_default": text("'IN_PROGRESS'")},
    )
    filled_stage_count: int = Field(
        default=0,
        sa_type=SmallInteger,
        description="已填写模块数",
        sa_column_kwargs={"server_default": text("0")},
    )
    submitted_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="整单提交时间")
    finished_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="评审结束时间")
    total_score: Decimal | None = Field(default=None, sa_type=Numeric(5, 2), description="本轮得分")


class TrainingAttempt(Base, TimestampMixin, TrainingAttemptBase, table=True):
    """闯关轮次：重新挑战生成新的 attempt_no，历史保留。"""

    __tablename__ = "training_attempt"
    __table_args__ = (
        UniqueConstraint("student_project_id", "attempt_no", name="uk_training_attempt"),
        Index("idx_training_attempt_project", "student_project_id", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------- 模块作答


class AttemptStageBase(SQLModel):
    attempt_id: int = Field(foreign_key="training_attempt.id", description="闯关轮次 ID")
    project_module_id: int = Field(foreign_key="project_module.id", description="项目模块 ID")
    is_filled: bool = Field(
        default=False,
        description="该模块是否已填写完成",
        sa_column_kwargs={"server_default": text("0")},
    )
    answer_text: str | None = Field(default=None, sa_type=Text, description="作答文本内容")
    filled_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="填写完成时间")


class AttemptStage(Base, TimestampMixin, AttemptStageBase, table=True):
    """轮次内每个模块的作答与填写状态；全部必填模块填写完成后才允许整单提交。"""

    __tablename__ = "attempt_stage"
    __table_args__ = (
        UniqueConstraint("attempt_id", "project_module_id", name="uk_attempt_stage"),
        Index("idx_attempt_stage_module", "project_module_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


class AttemptStageFileBase(SQLModel):
    attempt_stage_id: int = Field(foreign_key="attempt_stage.id", description="模块作答 ID")
    file_asset_id: int = Field(foreign_key="file_asset.id", description="文件 ID")


class AttemptStageFile(Base, CreatedAtMixin, AttemptStageFileBase, table=True):
    """模块作答与文件的关联（一个模块可带多个附件）。"""

    __tablename__ = "attempt_stage_file"
    __table_args__ = (UniqueConstraint("attempt_stage_id", "file_asset_id", name="uk_attempt_stage_file"),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------- 整单提交


class ProjectSubmissionBase(SQLModel):
    attempt_id: int = Field(foreign_key="training_attempt.id", description="闯关轮次 ID")
    submit_no: int = Field(sa_type=SmallInteger, description="整单提交序号（从 1 递增）")
    status: str = Field(
        default="PENDING_AI",
        max_length=20,
        description=(
            "PENDING_AI 待AI评审 / AI_PASSED / AI_FAILED / PENDING_REVIEW 待复审 / "
            "REVIEWING 复审中 / REVIEWED 已复审 / WITHDRAWN 已撤回"
        ),
        sa_column_kwargs={"server_default": text("'PENDING_AI'")},
    )
    final_conclusion: str | None = Field(
        default=None, max_length=10, description="最终结论 PASS 通过 / FAIL 不通过"
    )
    total_score: Decimal | None = Field(default=None, sa_type=Numeric(5, 2), description="本次提交总分 0~100")
    objection_reason: str | None = Field(default=None, max_length=500, description="学生对 AI 结果的异议说明")
    is_starred: bool = Field(
        default=False,
        description="教师标星（标星后进入审核列表关注区）",
        sa_column_kwargs={"server_default": text("0")},
    )
    submitted_at: datetime = Field(
        default_factory=utc_now,
        sa_type=TZDateTime,
        description="整单提交时间",
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    )
    withdrawn_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="学生撤回时间")
    reviewed_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="评审完成时间")


class ProjectSubmission(Base, TimestampMixin, ProjectSubmissionBase, table=True):
    """整单提交记录：一次提交包含该轮次全部模块的作答，可多次重提、可撤回。"""

    __tablename__ = "project_submission"
    __table_args__ = (
        UniqueConstraint("attempt_id", "submit_no", name="uk_project_submission"),
        Index("idx_project_submission_status", text("status, submitted_at DESC")),
        Index(
            "idx_project_submission_starred",
            text("is_starred, submitted_at DESC"),
            sqlite_where=text("is_starred"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "AttemptStage",
    "AttemptStageBase",
    "AttemptStageFile",
    "AttemptStageFileBase",
    "FileAsset",
    "FileAssetBase",
    "ProjectSubmission",
    "ProjectSubmissionBase",
    "StudentProject",
    "StudentProjectBase",
    "TrainingAttempt",
    "TrainingAttemptBase",
]
