"""统一响应体测试：成功 code=200/msg=ok，失败 code=422/msg=提示，非 JSON 不包壳。

这里刻意用 client.raw（不做自动解包的原始客户端）来断言结构本身。
HTTP 状态：成功保留 200/201，业务失败统一 200（失败与否看 body 的 code）。
"""

import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_success_envelope(client) -> None:
    response = await client.raw.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"code", "data", "msg"}
    assert payload["code"] == 200 and payload["msg"] == "ok"
    assert payload["data"]["ok"] is True

    # 列表接口的 data 里是分页结构
    listed = (await client.raw.get("/api/users")).json()
    assert listed["code"] == 200
    assert set(listed["data"]) >= {"items", "total", "page", "page_size", "pages"}


@pytest.mark.asyncio
async def test_failure_envelope(client) -> None:
    missing = await client.raw.get("/api/users/99999")
    assert missing.status_code == 200  # 失败也是 HTTP 200，错误码看 body
    payload = missing.json()
    assert set(payload) == {"code", "data", "msg"}
    assert payload["code"] == 422 and payload["data"] is None
    assert "不存在" in payload["msg"]

    invalid = await client.raw.get("/api/users/abc")
    assert invalid.status_code == 200
    assert invalid.json()["code"] == 422
    assert invalid.json()["msg"] == "请求参数校验失败"
    assert isinstance(invalid.json()["data"], list)

    created = await client.raw.post(
        "/api/users", json={"user_no": "E2E001", "real_name": "张三", "user_type": "STUDENT"}
    )
    assert created.status_code == 201
    assert created.json()["code"] == 200

    duplicated = await client.raw.post(
        "/api/users", json={"user_no": "E2E001", "real_name": "李四", "user_type": "STUDENT"}
    )
    assert duplicated.status_code == 200
    assert duplicated.json()["code"] == 422
    assert "已存在" in duplicated.json()["msg"]


@pytest.mark.asyncio
async def test_unhandled_exception_returns_server_envelope(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """未预期异常也要有统一结构（code=500），且不把堆栈细节回给前端。"""
    from app.crud.account import UserRepository

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("模拟数据库故障")

    monkeypatch.setattr(UserRepository, "list_users", _boom)
    response = await client.raw.get("/api/users")
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == 500 and payload["data"] is None
    assert payload["msg"] == "服务器内部错误，请稍后重试"
    assert "模拟数据库故障" not in payload["msg"]


@pytest.mark.asyncio
async def test_non_json_response_is_not_wrapped(client) -> None:
    response = await client.raw.get("/api/classes/students/import-template")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.content[:2] == b"PK"  # 直接是 xlsx 字节流，没有被包进 JSON


@pytest.mark.asyncio
async def test_openapi_and_docs_not_wrapped() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        spec = await raw.get("/openapi.json")
        assert spec.status_code == 200
        assert "openapi" in spec.json()  # 仍是标准 OpenAPI 文档，业务前端不受影响

        docs = await raw.get("/docs")
        assert docs.status_code == 200
        assert docs.text.lstrip().startswith("<!DOCTYPE html>")
