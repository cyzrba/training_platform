"""A 域 · 账户与权限（6 张表）。"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import JSONB, TZDateTime
from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class SysUser(Base, TimestampMixin, SoftDeleteMixin):
    """平台用户：单学院内学生/教师/管理员；不存密码，身份来自校方 SSO。"""

    __tablename__ = "sys_user"
    __table_args__ = (
        UniqueConstraint("user_no", name="uk_sys_user_no"),
        Index("uk_sys_user_ext", "ext_uid", unique=True, sqlite_where=text("ext_uid IS NOT NULL")),
        Index("idx_sys_user_type_status", "user_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_no: Mapped[str] = mapped_column(String(50), nullable=False, comment="学号/工号")
    real_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="姓名")
    user_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="STUDENT/TEACHER/ADMIN")
    ext_uid: Mapped[str | None] = mapped_column(String(64), comment="学校 IDP 唯一标识")
    major_name: Mapped[str | None] = mapped_column(String(100), comment="专业")
    avatar_url: Mapped[str | None] = mapped_column(String(255), comment="头像地址")
    phone: Mapped[str | None] = mapped_column(String(32), comment="手机号")
    email: Mapped[str | None] = mapped_column(String(128), comment="邮箱")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), comment="ACTIVE/DISABLED"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="最近登录时间")
    remark: Mapped[str | None] = mapped_column(String(255), comment="备注")


class SysRole(Base, TimestampMixin):
    """角色定义（教师/学生/管理员）。"""

    __tablename__ = "sys_role"
    __table_args__ = (UniqueConstraint("role_code", name="uk_sys_role_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_code: Mapped[str] = mapped_column(String(50), nullable=False, comment="角色编码")
    role_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="角色名称")
    password: Mapped[str | None] = mapped_column(String(100), comment="预留：mock 登录/联调；生产走 SSO")


class SysPermission(Base, TimestampMixin):
    """权限点（扁平结构，无父子层级、无排序）。"""

    __tablename__ = "sys_permission"
    __table_args__ = (UniqueConstraint("perm_code", name="uk_sys_perm_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    perm_code: Mapped[str] = mapped_column(String(100), nullable=False, comment="权限编码")
    perm_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="权限名称")


class SysUserRole(Base):
    """用户-角色关系（含分配人）。"""

    __tablename__ = "sys_user_role"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uk_sys_user_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("sys_role.id"), nullable=False)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


class SysRolePermission(Base):
    """角色-权限关系。"""

    __tablename__ = "sys_role_permission"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uk_sys_role_perm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("sys_role.id"), nullable=False)
    permission_id: Mapped[int] = mapped_column(ForeignKey("sys_permission.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


class SystemConfig(Base):
    """平台级键值配置（等级阈值、AI 模型参数、证书编号规则等）。"""

    __tablename__ = "system_config"
    __table_args__ = (UniqueConstraint("config_key", name="uk_system_config_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(100), nullable=False, comment="配置键")
    config_value: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'"), comment="配置值（JSON）"
    )
    description: Mapped[str | None] = mapped_column(String(255), comment="配置说明")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="最后修改人")
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )


__all__ = [
    "SysPermission",
    "SysRole",
    "SysRolePermission",
    "SysUser",
    "SysUserRole",
    "SystemConfig",
]
