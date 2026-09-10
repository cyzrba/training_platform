"""Alembic 环境配置：使用 app.models 的 metadata，SQLite 走 batch 模式。"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.resolved_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: SQLite 无法反射表达式索引（DESC 排序 / 部分索引），这 4 条在首个迁移里手工维护，
#: 不参与 autogenerate 比对，否则 alembic check 会一直报假差异。
EXPRESSION_INDEXES = frozenset(
    {
        "idx_project_submission_status",
        "idx_project_submission_starred",
        "idx_review_record_submission",
        "idx_qa_session_student",
    }
)


def include_object(_obj, name, type_, _reflected, _compare_to) -> bool:  # noqa: ANN001
    return not (type_ == "index" and name in EXPRESSION_INDEXES)


def _configure(connection: Connection | None = None) -> None:
    context.configure(
        connection=connection,
        url=None if connection is not None else config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        # SQLite 改表能力弱，必须走 batch 模式
        render_as_batch=True,
        literal_binds=connection is None,
    )


def run_migrations_offline() -> None:
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
