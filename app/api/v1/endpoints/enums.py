"""枚举字典接口：前端下拉框 / 标签文案统一从这里取。"""

from fastapi import APIRouter, HTTPException, status

from app.models.enums import ENUM_DICTS, ENUM_INDEX
from app.schemas.enums import EnumDictOut

router = APIRouter(prefix="/enums", tags=["枚举字典"])


@router.get("", response_model=list[EnumDictOut], summary="全部枚举字典")
async def list_enum_dicts() -> list[EnumDictOut]:
    return [EnumDictOut.from_dataclass(item) for item in ENUM_DICTS]


@router.get("/{enum_key}", response_model=EnumDictOut, summary="单个枚举字典")
async def get_enum_dict(enum_key: str) -> EnumDictOut:
    data = ENUM_INDEX.get(enum_key)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"枚举 {enum_key} 不存在，可选值：{', '.join(ENUM_INDEX)}",
        )
    return EnumDictOut.from_dataclass(data)
