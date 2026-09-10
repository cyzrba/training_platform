"""接口层冒烟测试（不依赖真实数据库文件）。"""

import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_health() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["ok"] is True

        response = await client.get("/api/v1/health")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_enum_dict_endpoints() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/enums")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 27
        assert {item["key"] for item in payload} >= {"user_type", "submission_status", "skill_state"}

        response = await client.get("/api/v1/enums/user_type")
        assert response.status_code == 200
        assert response.json()["items"] == [
            {"code": "STUDENT", "label": "学生"},
            {"code": "TEACHER", "label": "教师"},
            {"code": "ADMIN", "label": "管理员"},
        ]

        response = await client.get("/api/v1/enums/not_exists")
        assert response.status_code == 404
        assert response.json()["code"] == "HTTP_404"
