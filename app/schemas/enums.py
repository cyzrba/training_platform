"""枚举字典接口的响应模型。"""

from pydantic import BaseModel, Field

from app.models.enums import ENUM_DICTS, ENUM_INDEX, EnumDict


class EnumItemOut(BaseModel):
    code: str = Field(description="落库 code")
    label: str = Field(description="中文文案")


class EnumDictOut(BaseModel):
    key: str = Field(description="枚举标识，如 user_type")
    title: str = Field(description="枚举名称")
    items: list[EnumItemOut]

    @classmethod
    def from_dataclass(cls, data: EnumDict) -> "EnumDictOut":
        return cls(
            key=data.key,
            title=data.title,
            items=[EnumItemOut(code=i.code, label=i.label) for i in data.items],
        )


__all__ = ["ENUM_DICTS", "ENUM_INDEX", "EnumDictOut", "EnumItemOut"]
