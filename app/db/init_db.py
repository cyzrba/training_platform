"""建库入口：uv run python -m app.db.init_db

先执行 Alembic 迁移到最新版本，再写入种子数据（幂等）。
"""

import asyncio

from alembic.config import Config

from alembic import command
from app.core.config import BASE_DIR, settings
from app.core.db import engine


def run_migrations() -> None:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.resolved_database_url)
    command.upgrade(cfg, "head")


async def _seed_and_release() -> dict[str, int]:
    """写种子数据，结束后释放连接池，让 SQLite 把 -wal 落盘。

    WAL 模式下只有最后一个连接关闭时才会 checkpoint；CLI 进程若不释放引擎，
    data/app.db-wal 会一直留着，只读 app.db 的工具就会看不到刚写入的数据。
    """
    from app.db.seed import run_seed

    try:
        return await run_seed()
    finally:
        await engine.dispose()


def main() -> None:
    settings.sqlite_file.parent.mkdir(parents=True, exist_ok=True)
    run_migrations()
    result = asyncio.run(_seed_and_release())
    print(f"数据库就绪：{settings.sqlite_file}")
    print(f"种子数据：{result}")


if __name__ == "__main__":
    main()
