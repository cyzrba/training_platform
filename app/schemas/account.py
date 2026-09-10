"""账户与权限 Schema。"""

from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.account import (
    SysPermissionBase,
    SysRoleBase,
    SysRolePermissionBase,
    SystemConfigBase,
    SysUserBase,
    SysUserRoleBase,
)
from app.schemas.base import CreatedAtRead, SoftDeleteRead, TimestampRead, UpdatedAtRead

# --------------------------------------------------------------------- 平台用户


class SysUserCreate(SysUserBase):
    """新建用户：id 与时间戳由数据库生成。"""


class SysUserUpdate(SQLModel):
    user_no: str | None = Field(default=None, max_length=50)
    real_name: str | None = Field(default=None, max_length=50)
    user_type: str | None = Field(default=None, max_length=20)
    ext_uid: str | None = Field(default=None, max_length=64)
    major_name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=20)
    last_login_at: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)


class SysUserRead(TimestampRead, SoftDeleteRead, SysUserBase):
    id: int


class SysUserBrief(SQLModel):
    """列表 / 下拉用的精简结构。"""

    id: int
    user_no: str
    real_name: str
    user_type: str
    major_name: str | None = None
    avatar_url: str | None = None
    status: str


class SysRoleBrief(SQLModel):
    id: int
    role_code: str
    role_name: str


class SysUserWithRoles(SysUserRead):
    roles: list[SysRoleBrief] = Field(default_factory=list, description="用户已分配角色")


# ------------------------------------------------------------------------- 角色


class SysRoleCreate(SysRoleBase):
    pass


class SysRoleUpdate(SQLModel):
    role_code: str | None = Field(default=None, max_length=50)
    role_name: str | None = Field(default=None, max_length=50)
    password: str | None = Field(default=None, max_length=100)


class SysRoleRead(TimestampRead, SysRoleBase):
    id: int


# ----------------------------------------------------------------------- 权限点


class SysPermissionCreate(SysPermissionBase):
    pass


class SysPermissionUpdate(SQLModel):
    perm_code: str | None = Field(default=None, max_length=100)
    perm_name: str | None = Field(default=None, max_length=100)


class SysPermissionRead(TimestampRead, SysPermissionBase):
    id: int


# ----------------------------------------------------------------- 用户-角色关系


class SysUserRoleCreate(SysUserRoleBase):
    pass


class SysUserRoleRead(CreatedAtRead, SysUserRoleBase):
    id: int


# ----------------------------------------------------------------- 角色-权限关系


class SysRolePermissionCreate(SysRolePermissionBase):
    pass


class SysRolePermissionRead(CreatedAtRead, SysRolePermissionBase):
    id: int


# -------------------------------------------------------------------- 系统配置


class SystemConfigCreate(SystemConfigBase):
    pass


class SystemConfigUpdate(SQLModel):
    config_key: str | None = Field(default=None, max_length=100)
    config_value: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=255)
    updated_by: int | None = None


class SystemConfigRead(UpdatedAtRead, SystemConfigBase):
    id: int


__all__ = [
    "SysPermissionCreate",
    "SysPermissionRead",
    "SysPermissionUpdate",
    "SysRoleBrief",
    "SysRoleCreate",
    "SysRolePermissionCreate",
    "SysRolePermissionRead",
    "SysRoleRead",
    "SysRoleUpdate",
    "SysUserBrief",
    "SysUserCreate",
    "SysUserRead",
    "SysUserRoleCreate",
    "SysUserRoleRead",
    "SysUserUpdate",
    "SysUserWithRoles",
    "SystemConfigCreate",
    "SystemConfigRead",
    "SystemConfigUpdate",
]
