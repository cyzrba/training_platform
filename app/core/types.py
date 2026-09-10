"""SQLite 场景下的自定义列类型与时间工具。

字段清单源自 PostgreSQL DDL，落到 SQLite 时有两处需要显式处理：
1. ``timestamptz``：SQLite 无时区概念，用 TZDateTime 统一按 UTC 存取，读出来始终是 aware；
2. ``jsonb``：SQLModel 的 JSON 类型在 SQLite 中以 TEXT 存储。
"""

from datetime import UTC, datetime
from typing import Any

from sqlmodel import DateTime, TypeDecorator


def utc_now() -> datetime:
    """当前 UTC 时间，作为 created_at / updated_at 的 Python 侧默认值。"""
    return datetime.now(UTC)


class TZDateTime(TypeDecorator[datetime]):
    """以 UTC 存储、读回带时区的 datetime。"""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(self, value: datetime | None, _dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, _dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
