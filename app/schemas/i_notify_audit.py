"""I 域 Schema · 通知与审计。"""

from datetime import datetime

from pydantic import Field

from app.models.enums import NotificationBizType
from app.schemas.base import CreatedAtRead, ORMModel

# ------------------------------------------------------------ notification


class NotificationBase(ORMModel):
    recipient_user_id: int = Field(description="接收人 ID")
    title: str = Field(min_length=1, max_length=255, description="通知标题")
    content: str | None = Field(None, description="通知内容")
    biz_type: NotificationBizType | None = Field(None, description="业务类型")
    biz_id: int | None = Field(None, description="关联业务 ID")
    read_at: datetime | None = Field(None, description="已读时间")


class NotificationCreate(NotificationBase):
    pass


class NotificationUpdate(ORMModel):
    read_at: datetime | None = None


class NotificationRead(CreatedAtRead, NotificationBase):
    id: int


# ----------------------------------------------------------- operation_log


class OperationLogBase(ORMModel):
    operator_id: int | None = Field(None, description="操作人")
    module: str = Field(min_length=1, max_length=50, description="业务模块")
    action: str = Field(min_length=1, max_length=50, description="操作动作")
    target_type: str | None = Field(None, max_length=50, description="操作对象类型")
    target_id: int | None = Field(None, description="操作对象 ID")
    detail_json: dict = Field(default_factory=dict, description="变更前后快照")
    ip: str | None = Field(None, max_length=45, description="客户端 IP")
    user_agent: str | None = Field(None, max_length=255, description="客户端 User-Agent")


class OperationLogCreate(OperationLogBase):
    pass


class OperationLogRead(CreatedAtRead, OperationLogBase):
    id: int


__all__ = [
    "NotificationCreate",
    "NotificationRead",
    "NotificationUpdate",
    "OperationLogCreate",
    "OperationLogRead",
]
