"""接口层冒烟测试（不依赖真实数据库文件）。"""

import pytest


@pytest.mark.asyncio
async def test_health(client) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_enum_dict_endpoints(client) -> None:
    response = await client.get("/api/enums")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 26  # 去掉 skill_state（技能不再分三态）
    assert {item["key"] for item in payload} >= {"user_type", "submission_status", "skill_progress_source"}
    assert "skill_state" not in {item["key"] for item in payload}

    response = await client.get("/api/enums/user_type")
    assert response.status_code == 200
    assert response.json()["items"] == [
        {"code": "STUDENT", "label": "学生"},
        {"code": "TEACHER", "label": "教师"},
        {"code": "ADMIN", "label": "管理员"},
    ]

    response = await client.get("/api/enums/not_exists")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 422 and payload["data"] is None
    assert "不存在" in payload["msg"]
