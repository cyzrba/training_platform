"""SQLite 场景下的自定义列类型。

字段清单源自 PostgreSQL DDL，落到 SQLite 时有两处需要显式处理：
1. ``timestamptz``：SQLite 无时区概念，用 TZDateTime 统一按 UTC 存取，读出来始终是 aware；
2. ``jsonb``：SQLite 用 JSON（TEXT 存储），保留 JSONB 别名以便与字段清单表述对应。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, TypeDecorator
from sqlalchemy.types import TypeEngine

# SQLite 只有 INTEGER PRIMARY KEY 才是 rowid 别名（可自增），
# 因此主键统一用 Integer；其余 bigint 字段保留 BigInteger 语义（SQLite 内部同为 8 字节整数）。
JSONB = JSON


class TZDateTime(TypeDecorator[datetime]):
    """以 UTC 存储、读回带时区的 datetime。"""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> TypeEngine[Any]:
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


def sqlite_pk() -> Integer:
    """可自增主键列类型（SQLite 的 INTEGER PRIMARY KEY）。"""
    return Integer()
