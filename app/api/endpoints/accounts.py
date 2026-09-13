"""账户与权限接口：用户、角色、权限点、系统配置（/api/users 等）。

密码只以哈希落库：新建用户写默认密码哈希，重置密码单独走两个接口。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, PageDep
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.crud.account import (
    PermissionRepository,
    RolePermissionRepository,
    RoleRepository,
    SystemConfigRepository,
    UserRepository,
    UserRoleRepository,
)
from app.models.account import SysPermission, SysRole, SystemConfig, SysUser
from app.schemas.account import (
    PasswordResetBatchIn,
    PasswordResetIn,
    PasswordResetResult,
    RolePermissionSetIn,
    SysPermissionCreate,
    SysPermissionRead,
    SysPermissionUpdate,
    SysRoleBrief,
    SysRoleCreate,
    SysRoleRead,
    SysRoleUpdate,
    SystemConfigCreate,
    SystemConfigRead,
    SystemConfigUpdate,
    SysUserCreate,
    SysUserRead,
    SysUserUpdate,
    UserRoleSetIn,
)
from app.schemas.base import ApiResponse, MessageOut, Page
from app.services.password import default_password_hash, hash_password

router = APIRouter(route_class=EnvelopeRoute, tags=["账户权限"])


# --------------------------------------------------------------------- 依赖


def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


def role_repo(db: DbSession) -> RoleRepository:
    return RoleRepository(db)


def permission_repo(db: DbSession) -> PermissionRepository:
    return PermissionRepository(db)


def user_role_repo(db: DbSession) -> UserRoleRepository:
    return UserRoleRepository(db)


def role_permission_repo(db: DbSession) -> RolePermissionRepository:
    return RolePermissionRepository(db)


def system_config_repo(db: DbSession) -> SystemConfigRepository:
    return SystemConfigRepository(db)


UserRepo = Annotated[UserRepository, Depends(user_repo)]
RoleRepo = Annotated[RoleRepository, Depends(role_repo)]
PermissionRepo = Annotated[PermissionRepository, Depends(permission_repo)]
UserRoleRepo = Annotated[UserRoleRepository, Depends(user_role_repo)]
RolePermissionRepo = Annotated[RolePermissionRepository, Depends(role_permission_repo)]
SystemConfigRepo = Annotated[SystemConfigRepository, Depends(system_config_repo)]


# --------------------------------------------------------------------- 工具


async def _user_or_404(users: UserRepository, user_id: int) -> SysUser:
    user = await users.get(user_id)
    if user is None:
        raise NotFoundError(f"用户 {user_id} 不存在")
    return user


async def _role_or_404(roles: RoleRepository, role_id: int) -> SysRole:
    role = await roles.get(role_id)
    if role is None:
        raise NotFoundError(f"角色 {role_id} 不存在")
    return role


async def _permission_or_404(permissions: PermissionRepository, permission_id: int) -> SysPermission:
    permission = await permissions.get(permission_id)
    if permission is None:
        raise NotFoundError(f"权限点 {permission_id} 不存在")
    return permission


async def _config_or_404(configs: SystemConfigRepository, config_id: int) -> SystemConfig:
    config = await configs.get(config_id)
    if config is None:
        raise NotFoundError(f"系统配置 {config_id} 不存在")
    return config


def _hash_of(new_password: str | None) -> str:
    """留空 = 重置为系统默认密码（settings.default_password）。"""
    return hash_password(new_password) if new_password else default_password_hash()


async def _assert_roles_exist(roles: RoleRepository, role_ids: list[int]) -> None:
    for role_id in dict.fromkeys(role_ids):
        if await roles.get(role_id) is None:
            raise BusinessRuleError(f"角色 {role_id} 不存在")


async def _assert_permissions_exist(permissions: PermissionRepository, permission_ids: list[int]) -> None:
    for permission_id in dict.fromkeys(permission_ids):
        if await permissions.get(permission_id) is None:
            raise BusinessRuleError(f"权限点 {permission_id} 不存在")


# ------------------------------------------------------------------- 用户


@router.get("/users", response_model=ApiResponse[Page[SysUserRead]], summary="用户分页列表")
async def list_users(
    users: UserRepo,
    page: PageDep,
    user_type: Annotated[str | None, Query(description="STUDENT / TEACHER / ADMIN")] = None,
    status_: Annotated[str | None, Query(alias="status", description="ACTIVE / DISABLED")] = None,
    keyword: Annotated[str | None, Query(description="学号或姓名模糊搜索")] = None,
) -> Page[object]:
    return await users.list_users(page, user_type=user_type, status=status_, keyword=keyword)


@router.post(
    "/users",
    response_model=ApiResponse[SysUserRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建用户（写入默认密码）",
)
async def create_user(payload: SysUserCreate, users: UserRepo) -> SysUser:
    data = payload.model_dump()
    if await users.user_no_exists(data["user_no"]):
        raise ConflictError(f"学号/工号 {data['user_no']} 已存在")
    data["password_hash"] = default_password_hash()
    try:
        return await users.create(data)
    except IntegrityError as exc:
        raise ConflictError(f"学号/工号 {data['user_no']} 已存在") from exc


@router.get("/users/{user_id}", response_model=ApiResponse[SysUserRead], summary="用户详情")
async def get_user(user_id: int, users: UserRepo) -> SysUser:
    return await _user_or_404(users, user_id)


@router.patch("/users/{user_id}", response_model=ApiResponse[SysUserRead], summary="更新用户（部分字段）")
async def update_user(user_id: int, payload: SysUserUpdate, users: UserRepo) -> SysUser:
    user = await _user_or_404(users, user_id)
    data = payload.model_dump(exclude_unset=True)
    new_user_no = data.get("user_no")
    if new_user_no and new_user_no != user.user_no and await users.user_no_exists(new_user_no):
        raise ConflictError(f"学号/工号 {new_user_no} 已存在")
    try:
        return await users.update(user, data)
    except IntegrityError as exc:
        raise ConflictError("更新失败：唯一约束冲突") from exc


@router.delete("/users/{user_id}", response_model=ApiResponse[MessageOut], summary="删除用户（软删）")
async def delete_user(user_id: int, users: UserRepo) -> MessageOut:
    user = await _user_or_404(users, user_id)
    await users.remove(user)
    return MessageOut(message="用户已删除")


@router.get("/users/{user_id}/roles", response_model=ApiResponse[list[SysRoleBrief]], summary="查询用户角色")
async def list_user_roles(user_id: int, users: UserRepo, user_roles: UserRoleRepo) -> list[SysRole]:
    await _user_or_404(users, user_id)
    return await user_roles.list_roles_of_user(user_id)


@router.post(
    "/users/{user_id}/roles", response_model=ApiResponse[list[SysRoleBrief]], summary="覆盖式设置用户角色"
)
async def set_user_roles(
    user_id: int, payload: UserRoleSetIn, users: UserRepo, roles: RoleRepo, user_roles: UserRoleRepo
) -> list[SysRole]:
    await _user_or_404(users, user_id)
    await _assert_roles_exist(roles, payload.role_ids)
    await user_roles.replace_roles(user_id, payload.role_ids)
    return await user_roles.list_roles_of_user(user_id)


@router.delete(
    "/users/{user_id}/roles/{role_id}", response_model=ApiResponse[MessageOut], summary="解除用户角色"
)
async def remove_user_role(
    user_id: int, role_id: int, users: UserRepo, roles: RoleRepo, user_roles: UserRoleRepo
) -> MessageOut:
    await _user_or_404(users, user_id)
    await _role_or_404(roles, role_id)
    await user_roles.remove_role(user_id, role_id)
    return MessageOut(message="角色已解除")


# ----------------------------------------------------------------- 密码操作


@router.post(
    "/users/password/reset-batch",
    response_model=ApiResponse[PasswordResetResult],
    summary="批量重置密码（默认密码或不指定）",
)
async def reset_password_batch(payload: PasswordResetBatchIn, users: UserRepo) -> PasswordResetResult:
    password_hash = _hash_of(payload.new_password)
    updated: list[int] = []
    for user_id in dict.fromkeys(payload.user_ids):
        user = await users.get(user_id)
        if user is None:
            continue
        await users.update(user, {"password_hash": password_hash})
        updated.append(user_id)
    if not updated:
        raise NotFoundError("没有匹配到可重置的用户")
    return PasswordResetResult(updated=len(updated), user_ids=updated)


@router.post(
    "/users/{user_id}/password/reset",
    response_model=ApiResponse[PasswordResetResult],
    summary="重置单个用户密码",
)
async def reset_user_password(user_id: int, payload: PasswordResetIn, users: UserRepo) -> PasswordResetResult:
    user = await _user_or_404(users, user_id)
    await users.update(user, {"password_hash": _hash_of(payload.new_password)})
    return PasswordResetResult(updated=1, user_ids=[user_id])


# ------------------------------------------------------------------- 角色


@router.get("/roles", response_model=ApiResponse[Page[SysRoleRead]], summary="角色分页列表")
async def list_roles(roles: RoleRepo, page: PageDep) -> Page[object]:
    return await roles.list_page(page)


@router.post(
    "/roles", response_model=ApiResponse[SysRoleRead], status_code=status.HTTP_201_CREATED, summary="新建角色"
)
async def create_role(payload: SysRoleCreate, roles: RoleRepo) -> SysRole:
    if await roles.by_code(payload.role_code) is not None:
        raise ConflictError(f"角色编码 {payload.role_code} 已存在")
    return await roles.create(payload.model_dump())


@router.get("/roles/{role_id}", response_model=ApiResponse[SysRoleRead], summary="角色详情")
async def get_role(role_id: int, roles: RoleRepo) -> SysRole:
    return await _role_or_404(roles, role_id)


@router.patch("/roles/{role_id}", response_model=ApiResponse[SysRoleRead], summary="更新角色")
async def update_role(role_id: int, payload: SysRoleUpdate, roles: RoleRepo) -> SysRole:
    role = await _role_or_404(roles, role_id)
    data = payload.model_dump(exclude_unset=True)
    new_code = data.get("role_code")
    if new_code and new_code != role.role_code and await roles.by_code(new_code) is not None:
        raise ConflictError(f"角色编码 {new_code} 已存在")
    return await roles.update(role, data)


@router.delete("/roles/{role_id}", response_model=ApiResponse[MessageOut], summary="删除角色")
async def delete_role(
    role_id: int, roles: RoleRepo, user_roles: UserRoleRepo, role_permissions: RolePermissionRepo
) -> MessageOut:
    role = await _role_or_404(roles, role_id)
    in_use = await user_roles.list_user_ids_by_role(role_id)
    if in_use:
        raise ConflictError(f"该角色已分配给 {len(in_use)} 个用户，请先解除后再删除")
    await role_permissions.replace_permissions(role_id, [])
    await roles.remove(role)
    return MessageOut(message="角色已删除")


@router.get(
    "/roles/{role_id}/permissions",
    response_model=ApiResponse[list[SysPermissionRead]],
    summary="查询角色权限",
)
async def list_role_permissions(
    role_id: int, roles: RoleRepo, role_permissions: RolePermissionRepo
) -> list[SysPermission]:
    await _role_or_404(roles, role_id)
    return await role_permissions.list_permissions_of_role(role_id)


@router.put(
    "/roles/{role_id}/permissions",
    response_model=ApiResponse[list[SysPermissionRead]],
    summary="覆盖式设置角色权限",
)
async def set_role_permissions(
    role_id: int,
    payload: RolePermissionSetIn,
    roles: RoleRepo,
    permissions: PermissionRepo,
    role_permissions: RolePermissionRepo,
) -> list[SysPermission]:
    await _role_or_404(roles, role_id)
    await _assert_permissions_exist(permissions, payload.permission_ids)
    await role_permissions.replace_permissions(role_id, payload.permission_ids)
    return await role_permissions.list_permissions_of_role(role_id)


# ----------------------------------------------------------------- 权限点


@router.get("/permissions", response_model=ApiResponse[Page[SysPermissionRead]], summary="权限点分页列表")
async def list_permissions(permissions: PermissionRepo, page: PageDep) -> Page[object]:
    return await permissions.list_page(page)


@router.post(
    "/permissions",
    response_model=ApiResponse[SysPermissionRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建权限点",
)
async def create_permission(payload: SysPermissionCreate, permissions: PermissionRepo) -> SysPermission:
    if await permissions.get_by(perm_code=payload.perm_code) is not None:
        raise ConflictError(f"权限编码 {payload.perm_code} 已存在")
    return await permissions.create(payload.model_dump())


@router.get(
    "/permissions/{permission_id}", response_model=ApiResponse[SysPermissionRead], summary="权限点详情"
)
async def get_permission(permission_id: int, permissions: PermissionRepo) -> SysPermission:
    return await _permission_or_404(permissions, permission_id)


@router.patch(
    "/permissions/{permission_id}", response_model=ApiResponse[SysPermissionRead], summary="更新权限点"
)
async def update_permission(
    permission_id: int, payload: SysPermissionUpdate, permissions: PermissionRepo
) -> SysPermission:
    permission = await _permission_or_404(permissions, permission_id)
    data = payload.model_dump(exclude_unset=True)
    new_code = data.get("perm_code")
    if (
        new_code
        and new_code != permission.perm_code
        and await permissions.get_by(perm_code=new_code) is not None
    ):
        raise ConflictError(f"权限编码 {new_code} 已存在")
    return await permissions.update(permission, data)


@router.delete("/permissions/{permission_id}", response_model=ApiResponse[MessageOut], summary="删除权限点")
async def delete_permission(
    permission_id: int, permissions: PermissionRepo, role_permissions: RolePermissionRepo
) -> MessageOut:
    permission = await _permission_or_404(permissions, permission_id)
    in_use = await role_permissions.list_role_ids_by_permission(permission_id)
    if in_use:
        raise ConflictError(f"该权限点已分配给 {len(in_use)} 个角色，请先解除后再删除")
    await permissions.remove(permission)
    return MessageOut(message="权限点已删除")


# --------------------------------------------------------------- 系统配置


@router.get("/system-configs", response_model=ApiResponse[Page[SystemConfigRead]], summary="系统配置分页列表")
async def list_system_configs(
    configs: SystemConfigRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="配置键或说明模糊搜索")] = None,
) -> Page[object]:
    return await configs.list_configs(page, keyword=keyword)


@router.post(
    "/system-configs",
    response_model=ApiResponse[SystemConfigRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建系统配置",
)
async def create_system_config(payload: SystemConfigCreate, configs: SystemConfigRepo) -> SystemConfig:
    if await configs.by_key(payload.config_key) is not None:
        raise ConflictError(f"配置键 {payload.config_key} 已存在")
    return await configs.create(payload.model_dump())


@router.get(
    "/system-configs/{config_id}", response_model=ApiResponse[SystemConfigRead], summary="系统配置详情"
)
async def get_system_config(config_id: int, configs: SystemConfigRepo) -> SystemConfig:
    return await _config_or_404(configs, config_id)


@router.patch(
    "/system-configs/{config_id}", response_model=ApiResponse[SystemConfigRead], summary="更新系统配置"
)
async def update_system_config(
    config_id: int, payload: SystemConfigUpdate, configs: SystemConfigRepo
) -> SystemConfig:
    config = await _config_or_404(configs, config_id)
    data = payload.model_dump(exclude_unset=True)
    new_key = data.get("config_key")
    if new_key and new_key != config.config_key and await configs.by_key(new_key) is not None:
        raise ConflictError(f"配置键 {new_key} 已存在")
    return await configs.update(config, data)


@router.delete("/system-configs/{config_id}", response_model=ApiResponse[MessageOut], summary="删除系统配置")
async def delete_system_config(config_id: int, configs: SystemConfigRepo) -> MessageOut:
    config = await _config_or_404(configs, config_id)
    await configs.remove(config)
    return MessageOut(message="系统配置已删除")
