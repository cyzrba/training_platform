"""数据库引擎、会话与 PRAGMA 设置。"""

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _ensure_sqlite_dir() -> None:
    if settings.resolved_database_url.startswith("sqlite"):
        settings.sqlite_file.parent.mkdir(parents=True, exist_ok=True)


def create_engine(database_url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """创建异步引擎，并为 SQLite 打开必需的外键约束与并发参数。"""
    url = database_url or settings.resolved_database_url
    engine = create_async_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )

    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            # SQLite 默认不校验外键，必须每个连接打开
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


_ensure_sqlite_dir()

engine: AsyncEngine = create_engine(echo=settings.sql_echo)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每请求一个会话，异常自动回滚。"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database() -> dict[str, object]:
    """健康检查：连通性 + 已建表数量。"""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        result = await conn.execute(
            text(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            )
        )
        table_count = int(result.scalar_one())
    return {"ok": True, "dialect": engine.dialect.name, "table_count": table_count}
