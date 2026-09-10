"""A 域 Schema · 账户与权限。"""

from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import AccountStatus, UserType
from app.schemas.base import (
    CreatedAtRead,
    ORMModel,
    SoftDeleteRead,
    TimestampRead,
    UpdatedAtRead,
)

# ---------------------------------------------------------------- sys_user


class SysUserBase(ORMModel):
    user_no: str = Field(min_length=1, max_length=50, description="学号/工号")
    real_name: str = Field(min_length=1, max_length=50, description="姓名")
    user_type: UserType = Field(description="用户类型：学生/教师/管理员")
    ext_uid: str | None = Field(None, max_length=64, description="学校 IDP 唯一标识")
    major_name: str | None = Field(None, max_length=100, description="专业")
    avatar_url: str | None = Field(None, max_length=255, description="头像地址")
    phone: str | None = Field(None, max_length=32, description="手机号")
    email: str | None = Field(None, max_length=128, description="邮箱")
    status: AccountStatus = Field(AccountStatus.ACTIVE, description="账号状态")
    last_login_at: datetime | None = Field(None, description="最近登录时间")
    remark: str | None = Field(None, max_length=255, description="备注")


class SysUserCreate(SysUserBase):
    """新建用户：id 与时间戳由数据库生成。"""


class SysUserUpdate(ORMModel):
    user_no: str | None = Field(None, min_length=1, max_length=50)
    real_name: str | None = Field(None, min_length=1, max_length=50)
    user_type: UserType | None = None
    ext_uid: str | None = Field(None, max_length=64)
    major_name: str | None = Field(None, max_length=100)
    avatar_url: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=32)
    email: str | None = Field(None, max_length=128)
    status: AccountStatus | None = None
    last_login_at: datetime | None = None
    remark: str | None = Field(None, max_length=255)


class SysUserRead(TimestampRead, SoftDeleteRead, SysUserBase):
    id: int


class SysUserBrief(ORMModel):
    """列表 / 下拉用的精简结构。"""

    id: int
    user_no: str
    real_name: str
    user_type: UserType
    major_name: str | None = None
    avatar_url: str | None = None
    status: AccountStatus


# ---------------------------------------------------------------- sys_role


class SysRoleBase(ORMModel):
    role_code: str = Field(min_length=1, max_length=50, description="角色编码")
    role_name: str = Field(min_length=1, max_length=50, description="角色名称")
    password: str | None = Field(None, max_length=100, description="预留：mock 登录，生产走 SSO")


class SysRoleCreate(SysRoleBase):
    pass


class SysRoleUpdate(ORMModel):
    role_code: str | None = Field(None, min_length=1, max_length=50)
    role_name: str | None = Field(None, min_length=1, max_length=50)
    password: str | None = Field(None, max_length=100)


class SysRoleRead(TimestampRead, SysRoleBase):
    id: int


class SysRoleBrief(ORMModel):
    id: int
    role_code: str
    role_name: str


class SysUserWithRoles(SysUserRead):
    roles: list[SysRoleBrief] = Field(default_factory=list, description="用户已分配角色")


# ---------------------------------------------------------- sys_permission


class SysPermissionBase(ORMModel):
    perm_code: str = Field(min_length=1, max_length=100, description="权限编码")
    perm_name: str = Field(min_length=1, max_length=100, description="权限名称")


class SysPermissionCreate(SysPermissionBase):
    pass


class SysPermissionUpdate(ORMModel):
    perm_code: str | None = Field(None, min_length=1, max_length=100)
    perm_name: str | None = Field(None, min_length=1, max_length=100)


class SysPermissionRead(TimestampRead, SysPermissionBase):
    id: int


# ----------------------------------------------------------- sys_user_role


class SysUserRoleBase(ORMModel):
    user_id: int
    role_id: int
    assigned_by: int | None = Field(None, description="分配人")


class SysUserRoleCreate(SysUserRoleBase):
    pass


class SysUserRoleRead(CreatedAtRead, SysUserRoleBase):
    id: int


# ------------------------------------------------------- sys_role_permission


class SysRolePermissionBase(ORMModel):
    role_id: int
    permission_id: int


class SysRolePermissionCreate(SysRolePermissionBase):
    pass


class SysRolePermissionRead(CreatedAtRead, SysRolePermissionBase):
    id: int


# ------------------------------------------------------------ system_config


class SystemConfigBase(ORMModel):
    config_key: str = Field(min_length=1, max_length=100, description="配置键")
    config_value: dict[str, Any] = Field(default_factory=dict, description="配置值（JSON）")
    description: str | None = Field(None, max_length=255, description="配置说明")
    updated_by: int | None = Field(None, description="最后修改人")


class SystemConfigCreate(SystemConfigBase):
    pass


class SystemConfigUpdate(ORMModel):
    config_key: str | None = Field(None, min_length=1, max_length=100)
    config_value: dict[str, Any] | None = None
    description: str | None = Field(None, max_length=255)
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
