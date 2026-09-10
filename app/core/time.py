"""时间工具：全库统一按 UTC 处理。"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """当前 UTC 时间（不带时区标记），用作 created_at / updated_at 的默认值。

    SQLite 不保存时区，写入与读出一致才能避免时间语义漂移。
    """
    return datetime.now(UTC).replace(tzinfo=None)
