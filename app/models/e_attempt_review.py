"""E 域 · 闯关过程与评审（8 张表）。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import JSONB, TZDateTime
from app.models.base import Base, TimestampMixin


class FileAsset(Base):
    """文件元数据（作答附件、头像、证书 PDF、导入名单）。"""

    __tablename__ = "file_asset"
    __table_args__ = (UniqueConstraint("bucket", "object_key", name="uk_file_asset_object"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="上传人")
    bucket: Mapped[str] = mapped_column(String(100), nullable=False, comment="对象存储桶")
    object_key: Mapped[str] = mapped_column(String(255), nullable=False, comment="对象键")
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    content_type: Mapped[str | None] = mapped_column(String(100), comment="MIME 类型")
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), comment="文件大小（字节）"
    )
    sha256: Mapped[str | None] = mapped_column(String(64), comment="文件摘要")
    biz_type: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="SUBMISSION/AVATAR/CERT_PDF/IMPORT/REPORT"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), comment="ACTIVE/INVALID"
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


class StudentProject(Base, TimestampMixin):
    """学生实训记录：点击"开始闯关"时生成，承载进度、成绩与完成状态。"""

    __tablename__ = "student_project"
    __table_args__ = (
        UniqueConstraint("student_id", "project_id", name="uk_student_project"),
        Index("idx_student_project_student", "student_id", "status"),
        Index("idx_student_project_project", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("training_project.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'NOT_STARTED'"),
        comment="NOT_STARTED/IN_PROGRESS/SUBMITTED/COMPLETED",
    )
    progress: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0"), comment="0~100：已填写模块 ÷ 启用模块"
    )
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), comment="当前（最新）成绩")
    best_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), comment="历史最高成绩")
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), comment="闯关轮次数量"
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="开始时间")
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="完成时间")
    completed_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), comment="完成时的分数快照")

    attempts: Mapped[list["TrainingAttempt"]] = relationship(
        back_populates="student_project",
        cascade="all, delete-orphan",
        order_by="TrainingAttempt.attempt_no",
    )


class TrainingAttempt(Base, TimestampMixin):
    """闯关轮次：重新挑战生成新的 attempt_no，历史保留。"""

    __tablename__ = "training_attempt"
    __table_args__ = (
        UniqueConstraint("student_project_id", "attempt_no", name="uk_training_attempt"),
        Index("idx_training_attempt_project", "student_project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_project_id: Mapped[int] = mapped_column(ForeignKey("student_project.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="轮次序号")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'IN_PROGRESS'"),
        comment="IN_PROGRESS/SUBMITTED/COMPLETED/ABANDONED",
    )
    filled_stage_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), comment="已填写模块数"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="整单提交时间")
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="评审结束时间")
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), comment="本轮得分")

    student_project: Mapped[StudentProject] = relationship(back_populates="attempts")
    stages: Mapped[list["AttemptStage"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["ProjectSubmission"]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="ProjectSubmission.submit_no",
    )


class AttemptStage(Base, TimestampMixin):
    """轮次内每个模块的作答与填写状态。"""

    __tablename__ = "attempt_stage"
    __table_args__ = (
        UniqueConstraint("attempt_id", "project_module_id", name="uk_attempt_stage"),
        Index("idx_attempt_stage_module", "project_module_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("training_attempt.id"), nullable=False)
    project_module_id: Mapped[int] = mapped_column(ForeignKey("project_module.id"), nullable=False)
    is_filled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"), comment="该模块是否已填写完成"
    )
    answer_text: Mapped[str | None] = mapped_column(Text, comment="作答文本内容")
    filled_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="填写完成时间")

    attempt: Mapped[TrainingAttempt] = relationship(back_populates="stages")
    files: Mapped[list["AttemptStageFile"]] = relationship(
        back_populates="stage", cascade="all, delete-orphan"
    )


class AttemptStageFile(Base):
    """模块作答与文件的关联（一个模块可带多个附件）。"""

    __tablename__ = "attempt_stage_file"
    __table_args__ = (UniqueConstraint("attempt_stage_id", "file_asset_id", name="uk_attempt_stage_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_stage_id: Mapped[int] = mapped_column(ForeignKey("attempt_stage.id"), nullable=False)
    file_asset_id: Mapped[int] = mapped_column(ForeignKey("file_asset.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )

    stage: Mapped[AttemptStage] = relationship(back_populates="files")


class ProjectSubmission(Base, TimestampMixin):
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("training_attempt.id"), nullable=False)
    submit_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="整单提交序号")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'PENDING_AI'"),
        comment="PENDING_AI/AI_PASSED/AI_FAILED/PENDING_REVIEW/REVIEWING/REVIEWED/WITHDRAWN",
    )
    final_conclusion: Mapped[str | None] = mapped_column(String(10), comment="PASS/FAIL")
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), comment="本次提交总分")
    objection_reason: Mapped[str | None] = mapped_column(String(500), comment="学生对 AI 结果的异议说明")
    is_starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"), comment="教师标星"
    )
    submitted_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="整单提交时间"
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="学生撤回时间")
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="评审完成时间")

    attempt: Mapped[TrainingAttempt] = relationship(back_populates="submissions")
    reviews: Mapped[list["ReviewRecord"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    ai_jobs: Mapped[list["ReviewAiJob"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class ReviewRecord(Base):
    """整单评审记录（AI/教师）；维度明细以 JSON 存储。"""

    __tablename__ = "review_record"
    __table_args__ = (
        UniqueConstraint("submission_id", "review_kind", "version_no", name="uk_review_record"),
        Index(
            "idx_review_record_submission",
            text("submission_id, review_kind, version_no DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("project_submission.id"), nullable=False)
    review_kind: Mapped[str] = mapped_column(String(20), nullable=False, comment="AI/TEACHER")
    version_no: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"), comment="版本号"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'DRAFT'"), comment="DRAFT/FINAL"
    )
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="教师；AI 为空")
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), comment="本次评审总分")
    conclusion: Mapped[str | None] = mapped_column(String(10), comment="PASS/FAIL")
    comment: Mapped[str | None] = mapped_column(Text, comment="评语/批注")
    dimension_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'"), comment="各维度得分与理由"
    )
    ai_model: Mapped[str | None] = mapped_column(String(100), comment="AI 模型名称")
    raw_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'"), comment="AI 原始返回快照"
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="结束时间")

    submission: Mapped[ProjectSubmission] = relationship(back_populates="reviews")


class ReviewAiJob(Base):
    """AI 评审异步任务记录，用于重试与对账。"""

    __tablename__ = "review_ai_job"
    __table_args__ = (Index("idx_review_ai_job_status", "job_status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("project_submission.id"), nullable=False)
    job_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'QUEUED'"),
        comment="QUEUED/PROCESSING/SUCCEED/FAILED",
    )
    model_name: Mapped[str | None] = mapped_column(String(100), comment="执行批阅的 AI 模型")
    request_id: Mapped[str | None] = mapped_column(String(100), comment="AI 服务请求 ID")
    error_msg: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), comment="已尝试次数"
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="结束时间")

    submission: Mapped[ProjectSubmission] = relationship(back_populates="ai_jobs")


__all__ = [
    "AttemptStage",
    "AttemptStageFile",
    "FileAsset",
    "ProjectSubmission",
    "ReviewAiJob",
    "ReviewRecord",
    "StudentProject",
    "TrainingAttempt",
]
