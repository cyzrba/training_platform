"""SQLModel 表模型基类与公共 Mixin。"""

from datetime import datetime

from sqlmodel import Field, MetaData, SQLModel

from app.core.naming import NAMING_CONVENTION
from app.core.time import now


class Base(SQLModel):
    """所有表模型的基类：统一约束命名规范，保证迁移脚本稳定。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CreatedAtMixin(SQLModel):
    """只有 created_at 的表（关系表、流水表）。"""

    created_at: datetime = Field(default_factory=now, description="创建时间")


class TimestampMixin(SQLModel):
    """created_at + updated_at（主数据表）；updated_at 由仓储层在更新时刷新。

    时间取值见 app/core/time.py：单时区部署，直接存本地时间。
    """

    created_at: datetime = Field(default_factory=now, description="创建时间")
    updated_at: datetime = Field(default_factory=now, description="更新时间")


class SoftDeleteMixin(SQLModel):
    """软删除标记，仅主数据表使用；流水 / 快照表不带该字段。"""

    deleted_at: datetime | None = Field(default=None, description="软删除时间（为空表示未删除）")
