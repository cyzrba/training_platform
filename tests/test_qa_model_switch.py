"""AI 助教的模型选项：选了哪家就真的连哪家（不是前端假切换）。

配置口径（``ai.llm``）：

- 平铺字段 = **默认模型**（``settings_store.DEFAULT_LLM_MODEL``，当前 DeepSeek）；
- ``ai.llm.models.<key>`` = 别家（``kimi`` / ``mimo``…），只需写要覆盖的字段；
  ``base_url`` / ``model`` / ``api_key`` **不继承**默认那套，避免"没填 key 却悄悄用默认 key"。

用例里只把 ``llm.stream_chat`` 换成假实现，配置解析走真实代码（schema → 服务 → settings_store），
所以"选 kimi 就带着 kimi 的 base_url/model 去调模型"这件事是被真实覆盖的。
"""

from typing import Any

import httpx
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crud.account import SystemConfigRepository
from app.models.knowledge import AiQaMessage
from app.services import settings_store
from tests.test_qa_flow import _ask, _session, _stub_stream, _student


async def _write_llm_config(db: AsyncSession, models: dict[str, Any], *, api_key: str = "sk-default") -> None:
    """写一条 ai.llm：平铺字段给默认模型用，``models`` 挂别家。"""
    repo = SystemConfigRepository(db)
    row = await repo.by_key(settings_store.LLM_KEY)
    current = row.config_value if row and isinstance(row.config_value, dict) else {}
    value = {**current, "api_key": api_key, "models": models}
    if row is None:
        await repo.create({"config_key": settings_store.LLM_KEY, "config_value": value})
    else:
        await repo.update(row, {"config_value": value})
    await db.commit()
    settings_store.invalidate()


async def _last_answer(db: AsyncSession, session_id: int) -> AiQaMessage:
    stmt = (
        select(AiQaMessage)
        .where(AiQaMessage.session_id == session_id, AiQaMessage.role == "ASSISTANT")
        .order_by(AiQaMessage.id.desc())  # type: ignore[attr-defined]
    )
    message = (await db.exec(stmt)).first()
    assert message is not None
    return message


@pytest.mark.asyncio
async def test_selected_model_is_the_one_called(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选 kimi：请求带的是 kimi 的 base_url / model，落库也记 kimi 的模型名。"""
    await _write_llm_config(
        db_session,
        {
            "kimi": {
                "label": "Kimi",
                "base_url": "https://kimi.example/v1",
                "model": "kimi-latest",
                "api_key": "sk-kimi",
            }
        },
    )
    calls = _stub_stream(monkeypatch, pieces=("我是 Kimi",))
    student = await _student(client, user_no="2026300")
    session = await _session(client, student["id"])

    answered = await _ask(client, session["id"], student["id"], "你是谁？", model="kimi")
    assert answered.status_code == 200, answered.text

    assert calls[0]["base_url"] == "https://kimi.example/v1"
    assert calls[0]["model"] == "kimi-latest"
    assert calls[0]["model_key"] == "kimi"
    assert (await _last_answer(db_session, session["id"])).model_name == "kimi-latest"


@pytest.mark.asyncio
async def test_default_model_when_model_omitted(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不传 model（或前端还是老版本）走默认模型那套平铺字段。"""
    await _write_llm_config(
        db_session,
        {
            "kimi": {
                "base_url": "https://kimi.example/v1",
                "model": "kimi-latest",
                "api_key": "sk-kimi",
            }
        },
    )
    calls = _stub_stream(monkeypatch)
    student = await _student(client, user_no="2026301")
    session = await _session(client, student["id"])

    answered = await _ask(client, session["id"], student["id"], "默认用哪个模型？")
    assert answered.status_code == 200, answered.text
    assert calls[0]["model"] == "deepseek-v4-flash"  # DEFAULTS 里的默认模型
    assert calls[0]["base_url"] == "https://api.deepseek.com/v1"
    assert calls[0]["model_key"] == "deepseek"


@pytest.mark.asyncio
async def test_model_without_api_key_names_the_right_place(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kimi 只配了地址没配 key：报错要指名道姓，别只说"没配 key"。"""
    await _write_llm_config(
        db_session,
        {"kimi": {"label": "Kimi", "base_url": "https://kimi.example/v1", "model": "kimi-latest"}},
    )
    _stub_stream(monkeypatch)
    student = await _student(client, user_no="2026302")
    session = await _session(client, student["id"])

    body = (await _ask(client, session["id"], student["id"], "你是谁？", model="kimi")).json()
    assert body["code"] == 422
    msg = body["msg"]
    assert "Kimi" in msg and "ai.llm.models.kimi" in msg, msg


@pytest.mark.asyncio
async def test_model_without_endpoint_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新增一家模型先配地址：MiMo 默认骨架里 base_url / model 是空的，选到它要说清怎么修。"""
    await _write_llm_config(db_session, {})
    _stub_stream(monkeypatch)
    student = await _student(client, user_no="2026303")
    session = await _session(client, student["id"])

    body = (await _ask(client, session["id"], student["id"], "你是谁？", model="mimo")).json()
    assert body["code"] == 422
    assert "MiMo" in body["msg"] and "ai.llm.models.mimo" in body["msg"]


@pytest.mark.asyncio
async def test_unknown_model_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前端传了个后端没有的模型 id：报 422 并列出可选值，而不是悄悄回落默认模型。"""
    await _write_llm_config(
        db_session, {"kimi": {"base_url": "https://kimi.example/v1", "model": "kimi-latest"}}
    )
    _stub_stream(monkeypatch)
    student = await _student(client, user_no="2026304")
    session = await _session(client, student["id"])

    body = (await _ask(client, session["id"], student["id"], "你是谁？", model="gpt-9")).json()
    assert body["code"] == 422
    assert "gpt-9" in body["msg"]
    assert "deepseek" in body["msg"] and "kimi" in body["msg"]
