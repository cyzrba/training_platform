"""系统配置里的密钥不能从接口读出来。

``/api/system-configs`` 是通用配置接口，``config_value`` 里放着主模型 api key。
掩码统一做在 ``SystemConfigRead`` 的出参序列化上，所以列表 / 详情 / 新建 / 更新
四条路径都要覆盖到；同时要保证服务端内部读到的仍是明文（否则调不动大模型）。
"""

import httpx
import pytest

from app.services import settings_store

#: 假 key，只用来验证掩码逻辑。**不要在这里填真实密钥** —— 真实 key 只存在
#: ``system_config`` 表里（接口读取一律掩码，见 app/core/secrets.py）。
API_KEY = "sk-fake-0123456789abcdefghij-WXYZ"


def _assert_masked(payload: dict) -> None:
    """key 不能出现明文，只能看到尾 4 位。"""
    dumped = str(payload)
    assert API_KEY not in dumped
    assert "0123456789abcdefghij" not in dumped
    assert payload["config_value"]["api_key"] == "****WXYZ"


@pytest.mark.asyncio
async def test_config_endpoints_never_leak_api_key(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/system-configs",
        json={
            "config_key": "ai.llm",
            "config_value": {
                "provider": "openai-compatible",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
                "api_key": API_KEY,
            },
            "description": "主模型参数",
        },
    )
    assert created.status_code == 201, created.text
    config_id = created.json()["id"]
    _assert_masked(created.json())

    listed = (await client.get("/api/system-configs", params={"keyword": "ai.llm"})).json()
    assert listed["total"] == 1
    _assert_masked(listed["items"][0])

    detail = (await client.get(f"/api/system-configs/{config_id}")).json()
    _assert_masked(detail)

    updated = (
        await client.patch(
            f"/api/system-configs/{config_id}",
            json={"config_value": {"api_key": "sk-new-key-123456"}},
        )
    ).json()
    assert updated["config_value"]["api_key"] == "****3456"


@pytest.mark.asyncio
async def test_masking_is_recursive_and_ignores_non_secrets(client: httpx.AsyncClient) -> None:
    """嵌套结构里的密钥同样要挡；普通字段（模型名、地址）保持原样可读。"""
    created = (
        await client.post(
            "/api/system-configs",
            json={
                "config_key": "vendor.credentials",
                "config_value": {
                    "model": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com/v1",
                    "nested": {"api_key": API_KEY, "region": "cn"},
                },
            },
        )
    ).json()
    assert created["config_value"]["model"] == "deepseek-v4-flash"
    assert created["config_value"]["base_url"] == "https://api.deepseek.com/v1"
    assert created["config_value"]["nested"]["region"] == "cn"
    assert created["config_value"]["nested"]["api_key"] == "****WXYZ"


@pytest.mark.asyncio
async def test_empty_api_key_stays_empty(client: httpx.AsyncClient) -> None:
    """空 key 保持空：它表示"还没配"，不该被掩码成看起来已配置的样子。"""
    created = (
        await client.post(
            "/api/system-configs",
            json={"config_key": "ai.unset", "config_value": {"api_key": ""}},
        )
    ).json()
    assert created["config_value"]["api_key"] == ""


@pytest.mark.asyncio
async def test_settings_store_reads_plaintext_internally(client: httpx.AsyncClient, db_session) -> None:
    """掩码只挡接口出口；服务端内部取值必须是明文，否则调不动大模型。"""
    await client.post(
        "/api/system-configs",
        json={"config_key": "ai.llm", "config_value": {"api_key": API_KEY, "model": "deepseek-v4-flash"}},
    )
    settings_store.invalidate(settings_store.LLM_KEY)
    config = await settings_store.get_llm_config(db_session)
    assert config.api_key == API_KEY
    assert config.configured is True
    settings_store.invalidate(settings_store.LLM_KEY)
