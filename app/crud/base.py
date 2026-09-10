"""通用仓储基类：分页、软删过滤、部分更新。"""

from datetime import UTC
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base
from app.schemas.base import Page, PageParams


class BaseRepository[ModelT: Base]:
    """所有业务仓储的基类。

    子类只需声明 ``model``，即可获得基础的增删改查能力；
    P1 各域仓储在此基础上叠加业务过滤条件。
    """

    model: type[ModelT]
    #: 该模型是否带 deleted_at 软删字段
    soft_delete: bool = False

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------- 查询
    def _base_query(self) -> Select[tuple[ModelT]]:
        stmt = select(self.model)
        if self.soft_delete:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    async def get(self, pk: int) -> ModelT | None:
        stmt = self._base_query().where(self.model.id == pk)  # type: ignore[attr-defined]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by(self, **filters: Any) -> ModelT | None:
        stmt = self._base_query()
        for field, value in filters.items():
            stmt = stmt.where(getattr(self.model, field) == value)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_page(
        self,
        params: PageParams,
        *filters: Any,
        order_by: Any | None = None,
    ) -> Page[ModelT]:
        stmt = self._base_query()
        count_stmt = select(func.count()).select_from(self.model)
        if self.soft_delete:
            count_stmt = count_stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        for condition in filters:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        stmt = stmt.order_by(order_by if order_by is not None else self.model.id.desc())  # type: ignore[attr-defined]
        stmt = stmt.offset((params.page - 1) * params.page_size).limit(params.page_size)

        items = list((await self.session.execute(stmt)).scalars().all())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        return Page.build(items=items, total=total, params=params)

    # ------------------------------------------------------------- 写入
    async def create(self, data: dict[str, Any]) -> ModelT:
        obj = self.model(**data)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, data: dict[str, Any]) -> ModelT:
        for field, value in data.items():
            setattr(obj, field, value)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def remove(self, obj: ModelT) -> None:
        """软删模型打标记，其余模型物理删除。"""
        if self.soft_delete:
            from datetime import datetime

            obj.deleted_at = datetime.now(UTC)  # type: ignore[attr-defined]
            await self.session.flush()
        else:
            await self.session.delete(obj)
            await self.session.flush()
