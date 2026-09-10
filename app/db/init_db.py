"""建库入口：uv run python -m app.db.init_db

先执行 Alembic 迁移到最新版本，再写入种子数据（幂等）。
"""

import asyncio

from alembic.config import Config

from alembic import command
from app.core.config import BASE_DIR, settings


def run_migrations() -> None:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.resolved_database_url)
    command.upgrade(cfg, "head")


def main() -> None:
    from app.db.seed import run_seed

    settings.sqlite_file.parent.mkdir(parents=True, exist_ok=True)
    run_migrations()
    result = asyncio.run(run_seed())
    print(f"数据库就绪：{settings.sqlite_file}")
    print(f"种子数据：{result}")


if __name__ == "__main__":
    main()
