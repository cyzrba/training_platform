"""通知与审计：站内通知、操作日志。"""

from datetime import datetime

from sqlmodel import JSON, BigInteger, Field, Index, SQLModel, Text, text

from app.core.types import TZDateTime
from app.models.base import Base, CreatedAtMixin

# -------------------------------------------------------------------- 站内通知


class NotificationBase(SQLModel):
    recipient_user_id: int = Field(foreign_key="sys_user.id", description="接收人 ID")
    title: str = Field(max_length=255, description="通知标题")
    content: str | None = Field(default=None, sa_type=Text, description="通知内容")
    biz_type: str | None = Field(
        default=None,
        max_length=50,
        description="REVIEW_RESULT / STAGE_RESULT / TASK_PUBLISH / CERT / POINT",
    )
    biz_id: int | None = Field(default=None, sa_type=BigInteger, description="关联业务 ID")
    read_at: datetime | None = Field(default=None, sa_type=TZDateTime, description="已读时间")


class Notification(Base, CreatedAtMixin, NotificationBase, table=True):
    """站内通知（复审结果、技能点亮、任务发布、证书等）。"""

    __tablename__ = "notification"
    __table_args__ = (Index("idx_notification_recipient", "recipient_user_id", "read_at"),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 操作日志


class OperationLogBase(SQLModel):
    operator_id: int | None = Field(default=None, foreign_key="sys_user.id", description="操作人 ID")
    module: str = Field(max_length=50, description="业务模块，如 PROJECT / PUBLISH / REVIEW / CERT")
    action: str = Field(max_length=50, description="操作动作，如 CREATE / PUBLISH / WITHDRAW")
    target_type: str | None = Field(default=None, max_length=50, description="操作对象类型")
    target_id: int | None = Field(default=None, sa_type=BigInteger, description="操作对象 ID")
    detail_json: dict = Field(
        default_factory=dict,
        sa_type=JSON,
        description="变更前后快照（JSON）",
        sa_column_kwargs={"server_default": text("'{}'")},
    )
    ip: str | None = Field(default=None, max_length=45, description="客户端 IP")
    user_agent: str | None = Field(default=None, max_length=255, description="客户端 User-Agent")


class OperationLog(Base, CreatedAtMixin, OperationLogBase, table=True):
    """关键操作审计日志（发布、撤回、审核、发证等），append-only。"""

    __tablename__ = "operation_log"
    __table_args__ = (
        Index("idx_operation_log_module", "module", "created_at"),
        Index("idx_operation_log_operator", "operator_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "Notification",
    "NotificationBase",
    "OperationLog",
    "OperationLogBase",
]
