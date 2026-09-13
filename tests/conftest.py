"""测试夹具：内存 SQLite 数据库（复用生产同款 PRAGMA 设置）。"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import create_engine
from app.core.db import get_db as real_get_db
from app.main import app
from app.models import Base


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator["ApiClient", None]:
    """接口测试客户端：请求会话换成内存库，并自动解开统一响应体。

    成功响应（code=200）的 ``.json()`` 直接返回 ``data``，失败响应原样返回
    ``{code, data, msg}``。需要看原始响应时用 ``client.raw``。
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[real_get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield ApiClient(http_client)
    app.dependency_overrides.clear()


class ApiResponse:
    """httpx 响应薄包装：成功自动取 data。"""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self._response.headers

    @property
    def content(self) -> bytes:
        return self._response.content

    @property
    def text(self) -> str:
        return self._response.text

    def json(self) -> Any:
        payload = self._response.json()
        if isinstance(payload, dict) and payload.get("code") == 200 and "data" in payload:
            return payload["data"]
        return payload


class ApiClient:
    """httpx.AsyncClient 的薄包装，方法签名保持一致；``.raw`` 是原始客户端。"""

    _METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request"})

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.raw = client

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.raw, name)
        if name not in self._METHODS:
            return attribute

        async def _call(*args: Any, **kwargs: Any) -> ApiResponse:
            return ApiResponse(await attribute(*args, **kwargs))

        return _call
