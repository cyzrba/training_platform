"""I 域 · 通知与审计（2 张表）。"""

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import JSONB, TZDateTime
from app.models.base import Base


class Notification(Base):
    """站内通知（复审结果、技能点亮、任务发布、证书等）。"""

    __tablename__ = "notification"
    __table_args__ = (Index("idx_notification_recipient", "recipient_user_id", "read_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="通知标题")
    content: Mapped[str | None] = mapped_column(Text, comment="通知内容")
    biz_type: Mapped[str | None] = mapped_column(
        String(50), comment="REVIEW_RESULT/STAGE_RESULT/TASK_PUBLISH/CERT/POINT"
    )
    biz_id: Mapped[int | None] = mapped_column(BigInteger, comment="关联业务 ID")
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="已读时间")
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


class OperationLog(Base):
    """关键操作审计日志（发布、撤回、审核、发证等），append-only。"""

    __tablename__ = "operation_log"
    __table_args__ = (
        Index("idx_operation_log_module", "module", "created_at"),
        Index("idx_operation_log_operator", "operator_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="操作人")
    module: Mapped[str] = mapped_column(String(50), nullable=False, comment="业务模块")
    action: Mapped[str] = mapped_column(String(50), nullable=False, comment="操作动作")
    target_type: Mapped[str | None] = mapped_column(String(50), comment="操作对象类型")
    target_id: Mapped[int | None] = mapped_column(BigInteger, comment="操作对象 ID")
    detail_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'"), comment="变更前后快照"
    )
    ip: Mapped[str | None] = mapped_column(String(45), comment="客户端 IP")
    user_agent: Mapped[str | None] = mapped_column(String(255), comment="客户端 User-Agent")
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


__all__ = ["Notification", "OperationLog"]
