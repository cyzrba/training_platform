"""账户与权限：用户、角色、权限点、系统配置。"""

from datetime import datetime

from sqlmodel import JSON, Field, Index, SQLModel, UniqueConstraint, text

from app.core.time import now
from app.models.base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin

# --------------------------------------------------------------------- 平台用户


class SysUserBase(SQLModel):
    user_no: str = Field(max_length=50, description="学号/工号")
    real_name: str = Field(max_length=50, description="姓名")
    user_type: str = Field(max_length=20, description="STUDENT 学生 / TEACHER 教师 / ADMIN 管理员")
    ext_uid: str | None = Field(default=None, max_length=64, description="学校 IDP 唯一标识")
    major_name: str | None = Field(default=None, max_length=100, description="专业")
    avatar_url: str | None = Field(default=None, max_length=255, description="头像地址")
    phone: str | None = Field(default=None, max_length=32, description="手机号")
    email: str | None = Field(default=None, max_length=128, description="邮箱")
    status: str = Field(default="ACTIVE", max_length=20, description="ACTIVE 正常 / DISABLED 停用")
    last_login_at: datetime | None = Field(default=None, description="最近登录时间")
    remark: str | None = Field(default=None, max_length=255, description="备注")


class SysUser(Base, TimestampMixin, SoftDeleteMixin, SysUserBase, table=True):
    """平台用户：单学院内学生/教师/管理员。

    密码只存哈希（见 app/services/password.py），默认密码由 settings.default_password 决定；
    password_hash 不放进 SysUserBase，避免泄漏到 Create / Read 等接口 Schema。
    """

    __tablename__ = "sys_user"
    __table_args__ = (
        UniqueConstraint("user_no", name="uk_sys_user_no"),
        Index("uk_sys_user_ext", "ext_uid", unique=True, sqlite_where=text("ext_uid IS NOT NULL")),
        Index("idx_sys_user_type_status", "user_type", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    password_hash: str | None = Field(
        default=None,
        max_length=128,
        description="密码哈希（pbkdf2_sha256$迭代次数$盐$摘要）；为空表示从未设置密码",
    )


# ------------------------------------------------------------------------- 角色


class SysRoleBase(SQLModel):
    role_code: str = Field(max_length=50, description="角色编码")
    role_name: str = Field(max_length=50, description="角色名称")


class SysRole(Base, TimestampMixin, SysRoleBase, table=True):
    """角色定义（教师/学生/管理员）。"""

    __tablename__ = "sys_role"
    __table_args__ = (UniqueConstraint("role_code", name="uk_sys_role_code"),)

    id: int | None = Field(default=None, primary_key=True)


# ----------------------------------------------------------------------- 权限点


class SysPermissionBase(SQLModel):
    perm_code: str = Field(max_length=100, description="权限编码")
    perm_name: str = Field(max_length=100, description="权限名称")


class SysPermission(Base, TimestampMixin, SysPermissionBase, table=True):
    """权限点（扁平结构，无父子层级、无排序）。"""

    __tablename__ = "sys_permission"
    __table_args__ = (UniqueConstraint("perm_code", name="uk_sys_perm_code"),)

    id: int | None = Field(default=None, primary_key=True)


# ----------------------------------------------------------------- 用户-角色关系


class SysUserRoleBase(SQLModel):
    user_id: int = Field(foreign_key="sys_user.id", description="用户 ID")
    role_id: int = Field(foreign_key="sys_role.id", description="角色 ID")
    assigned_by: int | None = Field(default=None, foreign_key="sys_user.id", description="分配人 ID")


class SysUserRole(Base, CreatedAtMixin, SysUserRoleBase, table=True):
    """用户-角色关系（含分配人）。"""

    __tablename__ = "sys_user_role"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uk_sys_user_role"),)

    id: int | None = Field(default=None, primary_key=True)


# ----------------------------------------------------------------- 角色-权限关系


class SysRolePermissionBase(SQLModel):
    role_id: int = Field(foreign_key="sys_role.id", description="角色 ID")
    permission_id: int = Field(foreign_key="sys_permission.id", description="权限 ID")


class SysRolePermission(Base, CreatedAtMixin, SysRolePermissionBase, table=True):
    """角色-权限关系。"""

    __tablename__ = "sys_role_permission"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uk_sys_role_perm"),)

    id: int | None = Field(default=None, primary_key=True)


# -------------------------------------------------------------------- 系统配置


class SystemConfigBase(SQLModel):
    config_key: str = Field(max_length=100, description="配置键")
    config_value: dict = Field(default_factory=dict, sa_type=JSON, description="配置值（JSON）")
    description: str | None = Field(default=None, max_length=255, description="配置说明")
    updated_by: int | None = Field(default=None, foreign_key="sys_user.id", description="最后修改人 ID")


class SystemConfig(Base, SystemConfigBase, table=True):
    """平台级键值配置（等级阈值、AI 模型参数、证书编号规则等）。"""

    __tablename__ = "system_config"
    __table_args__ = (UniqueConstraint("config_key", name="uk_system_config_key"),)

    id: int | None = Field(default=None, primary_key=True)
    updated_at: datetime = Field(default_factory=now, description="更新时间")


__all__ = [
    "SysPermission",
    "SysPermissionBase",
    "SysRole",
    "SysRoleBase",
    "SysRolePermission",
    "SysRolePermissionBase",
    "SysUser",
    "SysUserBase",
    "SysUserRole",
    "SysUserRoleBase",
    "SystemConfig",
    "SystemConfigBase",
]
