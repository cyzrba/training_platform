"""Schema 公共基类与分页结构。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    """所有 schema 的基类：支持从 ORM 对象直接构造。"""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class CreatedAtRead(BaseModel):
    created_at: datetime = Field(description="创建时间")


class UpdatedAtRead(BaseModel):
    updated_at: datetime = Field(description="更新时间")


class TimestampRead(CreatedAtRead, UpdatedAtRead):
    """含 created_at / updated_at 的读取基类。"""


class SoftDeleteRead(BaseModel):
    deleted_at: datetime | None = Field(None, description="软删除时间，空表示未删除")


class IdListIn(BaseModel):
    ids: list[int] = Field(min_length=1, description="批量操作的主键列表")


# 0~100 的分数 / 百分比 / 分值占比
ScoreField = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class PageParams(BaseModel):
    page: int = Field(1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(20, ge=1, le=200, description="每页条数")


class Page[T](BaseModel):
    """统一分页响应。"""

    # Repository 直接返回 ORM 实例时不做类型再校验
    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> "Page[T]":
        pages = (total + params.page_size - 1) // params.page_size if params.page_size else 0
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=pages,
        )


class MessageOut(BaseModel):
    """通用操作结果。"""

    success: bool = True
    message: str = "操作成功"
