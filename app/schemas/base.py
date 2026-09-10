"""Schema 公共基类与分页结构。

表字段定义在各域模型的 ``XxxBase`` 里（SQLModel 单一定义原则），
这里只放跨表复用的读取片段与分页 / 批量结构。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import ConfigDict, Field
from sqlmodel import SQLModel


class ORMModel(SQLModel):
    """Schema 基类：允许从 ORM 对象构造，统一空白裁剪。"""

    model_config = ConfigDict(  # type: ignore[assignment]
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class CreatedAtRead(SQLModel):
    created_at: datetime = Field(description="创建时间")


class UpdatedAtRead(SQLModel):
    updated_at: datetime = Field(description="更新时间")


class TimestampRead(CreatedAtRead, UpdatedAtRead):
    """含 created_at / updated_at 的读取片段。"""


class SoftDeleteRead(SQLModel):
    deleted_at: datetime | None = Field(default=None, description="软删除时间，空表示未删除")


class IdListIn(SQLModel):
    ids: list[int] = Field(min_length=1, description="批量操作的主键列表")


# 0~100 的分数 / 百分比 / 分值占比
ScoreField = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class PageParams(SQLModel):
    page: int = Field(1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(20, ge=1, le=200, description="每页条数")


class Page[T](SQLModel):
    """统一分页响应。"""

    # 仓储层直接返回 ORM 实例时不做类型再校验
    model_config = ConfigDict(arbitrary_types_allowed=True)  # type: ignore[assignment]

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[Any], total: int, params: PageParams) -> "Page[Any]":
        pages = (total + params.page_size - 1) // params.page_size if params.page_size else 0
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=pages,
        )


class MessageOut(SQLModel):
    """通用操作结果。"""

    success: bool = True
    message: str = "操作成功"
