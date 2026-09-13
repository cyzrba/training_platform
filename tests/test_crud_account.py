"""账户与权限域接口测试：用户、角色、权限点、密码、系统配置。"""

import httpx
import pytest

from app.core.config import settings
from app.services.password import verify_password

USERS = "/api/users"
ROLES = "/api/roles"
PERMISSIONS = "/api/permissions"
CONFIGS = "/api/system-configs"


async def _create_user(client: httpx.AsyncClient, user_no: str = "2026001") -> dict:
    payload = {"user_no": user_no, "real_name": "张三", "user_type": "STUDENT"}
    response = await client.post(USERS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_user_crud_and_default_password(client: httpx.AsyncClient, db_session) -> None:
    user = await _create_user(client)
    assert user["user_no"] == "2026001"
    assert user["status"] == "ACTIVE"
    assert "password_hash" not in user  # 出参不暴露哈希

    # 默认密码已写入且可校验
    from app.crud.account import UserRepository

    stored = await UserRepository(db_session).get(user["id"])
    assert stored is not None
    assert verify_password(settings.default_password, stored.password_hash)
    assert not verify_password("wrong-password", stored.password_hash)

    # 详情 / 列表筛选 / 更新
    assert (await client.get(f"{USERS}/{user['id']}")).json()["real_name"] == "张三"

    listed = (await client.get(USERS, params={"user_type": "STUDENT", "keyword": "张"})).json()
    assert listed["total"] == 1 and listed["pages"] == 1

    # 列表响应也不能带出密码哈希（泛型分页模型必须真的按 Read 模型过滤字段）
    raw_list = await client.raw.get(USERS, params={"user_type": "STUDENT"})
    assert "password_hash" not in raw_list.text

    assert (await client.get(USERS, params={"user_type": "TEACHER"})).json()["total"] == 0

    patched = await client.patch(f"{USERS}/{user['id']}", json={"major_name": "人工智能技术应用"})
    assert patched.status_code == 200
    assert patched.json()["major_name"] == "人工智能技术应用"

    # 软删后列表与详情都查不到
    assert (await client.delete(f"{USERS}/{user['id']}")).status_code == 200
    assert (await client.get(f"{USERS}/{user['id']}")).status_code == 200
    assert (await client.get(USERS)).json()["total"] == 0


@pytest.mark.asyncio
async def test_duplicate_user_no_conflicts(client: httpx.AsyncClient) -> None:
    await _create_user(client, "2026002")
    duplicate = await client.post(
        USERS, json={"user_no": "2026002", "real_name": "李四", "user_type": "STUDENT"}
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["code"] == 422


@pytest.mark.asyncio
async def test_user_roles_assign_and_remove(client: httpx.AsyncClient) -> None:
    user = await _create_user(client, "2026003")
    role = (await client.post(ROLES, json={"role_code": "TEACHER", "role_name": "教师"})).json()

    assert (await client.get(f"{USERS}/{user['id']}/roles")).json() == []

    assigned = await client.post(f"{USERS}/{user['id']}/roles", json={"role_ids": [role["id"]]})
    assert assigned.status_code == 200
    assert [item["role_code"] for item in assigned.json()] == ["TEACHER"]

    # 覆盖式设置：传空数组即清空
    cleared = await client.post(f"{USERS}/{user['id']}/roles", json={"role_ids": []})
    assert cleared.json() == []

    # 不存在的角色直接拒绝
    bad = await client.post(f"{USERS}/{user['id']}/roles", json={"role_ids": [9999]})
    assert bad.status_code == 200
    assert bad.json()["code"] == 422

    await client.post(f"{USERS}/{user['id']}/roles", json={"role_ids": [role["id"]]})
    await client.delete(f"{USERS}/{user['id']}/roles/{role['id']}")
    assert (await client.get(f"{USERS}/{user['id']}/roles")).json() == []


@pytest.mark.asyncio
async def test_role_permissions_and_delete_guard(client: httpx.AsyncClient) -> None:
    role = (await client.post(ROLES, json={"role_code": "TEACHER", "role_name": "教师"})).json()
    permission = (
        await client.post(PERMISSIONS, json={"perm_code": "CLASS_MANAGE", "perm_name": "班级管理"})
    ).json()

    result = await client.put(
        f"{ROLES}/{role['id']}/permissions", json={"permission_ids": [permission["id"]]}
    )
    assert [item["perm_code"] for item in result.json()] == ["CLASS_MANAGE"]

    # 权限点已被角色引用时不允许删除
    blocked = await client.delete(f"{PERMISSIONS}/{permission['id']}")
    assert blocked.status_code == 200

    # 用户占用角色时不允许删除角色
    user = await _create_user(client, "2026004")
    await client.post(f"{USERS}/{user['id']}/roles", json={"role_ids": [role["id"]]})
    assert (await client.delete(f"{ROLES}/{role['id']}")).status_code == 200

    await client.post(f"{USERS}/{user['id']}/roles", json={"role_ids": []})
    await client.put(f"{ROLES}/{role['id']}/permissions", json={"permission_ids": []})
    assert (await client.delete(f"{ROLES}/{role['id']}")).status_code == 200
    assert (await client.delete(f"{PERMISSIONS}/{permission['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_single_and_batch(client: httpx.AsyncClient, db_session) -> None:
    from app.crud.account import UserRepository

    users = UserRepository(db_session)
    first = await _create_user(client, "2026005")
    second = await _create_user(client, "2026006")

    reset = await client.post(f"{USERS}/{first['id']}/password/reset", json={"new_password": "abc123"})
    assert reset.json() == {"updated": 1, "user_ids": [first["id"]]}
    stored = await users.get(first["id"])
    assert stored is not None and verify_password("abc123", stored.password_hash)

    batch = await client.post(f"{USERS}/password/reset-batch", json={"user_ids": [first["id"], second["id"]]})
    assert batch.json()["updated"] == 2
    stored = await users.get(second["id"])
    assert stored is not None and verify_password(settings.default_password, stored.password_hash)

    empty = await client.post(f"{USERS}/password/reset-batch", json={"user_ids": [9999]})
    assert empty.status_code == 200


@pytest.mark.asyncio
async def test_system_config_crud(client: httpx.AsyncClient) -> None:
    created = await client.post(
        CONFIGS,
        json={
            "config_key": "cert.no_rule",
            "config_value": {"prefix": "CERT"},
            "description": "证书编号规则",
        },
    )
    assert created.status_code == 201
    config = created.json()

    duplicate = await client.post(CONFIGS, json={"config_key": "cert.no_rule", "config_value": {}})
    assert duplicate.status_code == 200

    listed = (await client.get(CONFIGS, params={"keyword": "证书"})).json()
    assert listed["total"] == 1

    patched = await client.patch(f"{CONFIGS}/{config['id']}", json={"config_value": {"prefix": "CERT-2026"}})
    assert patched.json()["config_value"] == {"prefix": "CERT-2026"}

    assert (await client.delete(f"{CONFIGS}/{config['id']}")).status_code == 200
    assert (await client.get(f"{CONFIGS}/{config['id']}")).status_code == 200
