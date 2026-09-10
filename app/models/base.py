"""ORM 基类与公共 Mixin。"""

from datetime import datetime

from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.naming import NAMING_CONVENTION
from app.core.types import TZDateTime


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """created_at / updated_at：库内默认 now()，更新时由 ORM 维护。"""

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class SoftDeleteMixin:
    """软删除标记，仅主数据表使用；流水 / 快照表不带该字段。"""

    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="软删除时间")
