"""Schema 公共基类与分页结构。

表字段定义在各域模型的 ``XxxBase`` 里（SQLModel 单一定义原则），
这里只放跨表复用的读取片段与分页 / 批量结构。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
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


class Page[T](BaseModel):
    """统一分页响应。

    注意：这里刻意继承 Pydantic 的 BaseModel 而不是 SQLModel —— SQLModel 对 PEP 695
    泛型不做参数化（``Page[SysUserRead]`` 里的 T 仍是未绑定的 TypeVar），会导致
    ``items`` 退化成 Any、把 ORM 上的敏感字段（如 password_hash）一起吐出去。
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

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


class ApiResponse[T](BaseModel):
    """统一响应体 ``{code, data, msg}``。

    端点照旧直接返回业务数据，FastAPI 校验响应时由下面的 before 校验器包成统一结构，
    所以 /docs 里展示的响应模型和真实返回完全一致。

    同 Page：必须用 Pydantic 泛型，SQLModel 的泛型参数化不生效。
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    code: int = Field(default=200, description="200 成功 / 422 业务失败 / 500 服务异常")
    data: T | None = Field(default=None, description="业务数据；失败时为明细或 null")
    msg: str = Field(default="ok", description="提示信息，成功固定为 ok")

    @model_validator(mode="before")
    @classmethod
    def _wrap(cls, value: Any) -> Any:
        # 已经是统一结构的（异常处理器、文件流之后的自定义响应）原样放行
        if isinstance(value, dict) and {"code", "data", "msg"} <= value.keys():
            return value
        return {"code": 200, "data": value, "msg": "ok"}


class ErrorResponse(SQLModel):
    """失败响应（供 OpenAPI 文档描述 422/500）。"""

    code: int = Field(default=422, description="422 业务失败 / 500 服务异常")
    data: Any = Field(default=None, description="明细（如名单校验的逐行原因），没有则 null")
    msg: str = Field(description="错误提示，直接展示给用户")
