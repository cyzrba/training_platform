"""通知与审计 Schema。"""

from datetime import datetime

from sqlmodel import SQLModel

from app.models.notification import NotificationBase, OperationLogBase
from app.schemas.base import CreatedAtRead

# -------------------------------------------------------------------- 站内通知


class NotificationCreate(NotificationBase):
    pass


class NotificationUpdate(SQLModel):
    read_at: datetime | None = None


class NotificationRead(CreatedAtRead, NotificationBase):
    id: int


# ------------------------------------------------------------------ 操作日志


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
