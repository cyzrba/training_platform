"""账户与权限仓储：用户、角色、权限点、两类关联关系、系统配置。"""

from collections.abc import Sequence
from typing import Any

from sqlmodel import delete, func, or_, select

from app.crud.base import BaseRepository
from app.models.account import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SystemConfig,
    SysUser,
    SysUserRole,
)
from app.schemas.base import Page, PageParams


class UserRepository(BaseRepository[SysUser]):
    """平台用户仓储，软删表。"""

    model = SysUser
    soft_delete = True

    async def list_users(
        self,
        params: PageParams,
        *,
        user_type: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
    ) -> Page[Any]:
        """按类型 / 状态 / 关键字（学号或姓名模糊）分页。"""
        filters: list[Any] = []
        if user_type:
            filters.append(SysUser.user_type == user_type)
        if status:
            filters.append(SysUser.status == status)
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(or_(SysUser.user_no.like(pattern), SysUser.real_name.like(pattern)))
        return await self.list_page(params, *filters)

    async def user_no_exists(self, user_no: str) -> bool:
        """学号是否已被占用（含已软删的账号，因为唯一约束不分软删）。"""
        stmt = select(func.count()).select_from(SysUser).where(SysUser.user_no == user_no)
        return int((await self.session.exec(stmt)).one()) > 0

    async def existing_user_nos(self, user_nos: Sequence[str]) -> set[str]:
        """批量查已存在的学号，供导入预校验一次查完。"""
        if not user_nos:
            return set()
        stmt = select(SysUser.user_no).where(SysUser.user_no.in_(list(user_nos)))  # type: ignore[attr-defined]
        return set((await self.session.exec(stmt)).all())


class RoleRepository(BaseRepository[SysRole]):
    """角色仓储。"""

    model = SysRole

    async def by_code(self, role_code: str) -> SysRole | None:
        return await self.get_by(role_code=role_code)


class PermissionRepository(BaseRepository[SysPermission]):
    """权限点仓储。"""

    model = SysPermission


class UserRoleRepository(BaseRepository[SysUserRole]):
    """用户-角色关系仓储，覆盖式设置。"""

    model = SysUserRole

    async def list_roles_of_user(self, user_id: int) -> list[SysRole]:
        stmt = (
            select(SysRole)
            .join(SysUserRole, SysUserRole.role_id == SysRole.id)  # type: ignore[arg-type]
            .where(SysUserRole.user_id == user_id)
            .order_by(SysRole.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def replace_roles(self, user_id: int, role_ids: Sequence[int]) -> int:
        """清空后重建，返回写入条数。"""
        await self.session.exec(delete(SysUserRole).where(SysUserRole.user_id == user_id))
        for role_id in role_ids:
            self.session.add(SysUserRole(user_id=user_id, role_id=role_id))
        await self.session.flush()
        return len(role_ids)

    async def remove_role(self, user_id: int, role_id: int) -> None:
        await self.session.exec(
            delete(SysUserRole).where(SysUserRole.user_id == user_id, SysUserRole.role_id == role_id)
        )
        await self.session.flush()

    async def list_user_ids_by_role(self, role_id: int) -> list[int]:
        stmt = select(SysUserRole.user_id).where(SysUserRole.role_id == role_id)
        return list((await self.session.exec(stmt)).all())


class RolePermissionRepository(BaseRepository[SysRolePermission]):
    """角色-权限关系仓储，覆盖式设置。"""

    model = SysRolePermission

    async def list_permissions_of_role(self, role_id: int) -> list[SysPermission]:
        stmt = (
            select(SysPermission)
            .join(SysRolePermission, SysRolePermission.permission_id == SysPermission.id)  # type: ignore[arg-type]
            .where(SysRolePermission.role_id == role_id)
            .order_by(SysPermission.id)
        )
        return list((await self.session.exec(stmt)).all())

    async def replace_permissions(self, role_id: int, permission_ids: Sequence[int]) -> int:
        await self.session.exec(delete(SysRolePermission).where(SysRolePermission.role_id == role_id))
        for permission_id in permission_ids:
            self.session.add(SysRolePermission(role_id=role_id, permission_id=permission_id))
        await self.session.flush()
        return len(permission_ids)

    async def list_role_ids_by_permission(self, permission_id: int) -> list[int]:
        stmt = select(SysRolePermission.role_id).where(SysRolePermission.permission_id == permission_id)
        return list((await self.session.exec(stmt)).all())


class SystemConfigRepository(BaseRepository[SystemConfig]):
    """系统配置仓储。"""

    model = SystemConfig

    async def by_key(self, config_key: str) -> SystemConfig | None:
        return await self.get_by(config_key=config_key)

    async def list_configs(self, params: PageParams, *, keyword: str | None = None) -> Page[Any]:
        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(or_(SystemConfig.config_key.like(pattern), SystemConfig.description.like(pattern)))
        return await self.list_page(params, *filters)


__all__ = [
    "PermissionRepository",
    "RolePermissionRepository",
    "RoleRepository",
    "SystemConfigRepository",
    "UserRepository",
    "UserRoleRepository",
]
