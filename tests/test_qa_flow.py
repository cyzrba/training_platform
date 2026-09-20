"""AI 问答：会话管理、3 轮上下文窗口、7 天保留、token 统计。

大模型调用一律用假实现替换（``monkeypatch`` 掉 ``llm.stream_chat``），所以这组用例
不消耗真实 token、离线也能跑；真实连通性由手工端到端验证覆盖。
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlmodel import select

from app.core.exceptions import ConflictError
from app.core.time import now
from app.models.knowledge import AiQaMessage, AiQaSession
from app.services import llm, qa, settings_store
from app.services.settings_store import LLMConfig

#: 测试用大模型配置：key 是假的，真实调用一律被 monkeypatch 掉
TEST_LLM_CONFIG = LLMConfig(
    provider="openai-compatible",
    base_url="https://example.invalid/v1",
    model="deepseek-v4-flash",
    api_key="sk-test-key",
    temperature=0.2,
    timeout=10,
    max_tokens=1024,
)


def _stub_llm_config(monkeypatch: pytest.MonkeyPatch, *, api_key: str = "sk-test-key") -> None:
    config = replace(TEST_LLM_CONFIG, api_key=api_key)

    async def _fake(_session, model: str | None = None) -> LLMConfig:  # noqa: ARG001
        return config

    monkeypatch.setattr(settings_store, "get_llm_config", _fake)


def _stub_stream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pieces: tuple[str, ...] = ("三端稳压管", "是把电压稳定住的器件"),
    error: Exception | None = None,
) -> list[dict]:
    """假流式调用：记录每次请求的 prompt（用来验证上下文窗口），按片段产出文本。"""
    calls: list[dict] = []

    async def _fake_stream(
        config,
        *,
        system_prompt: str,
        user_prompt: str,
        history=(),
        temperature=None,
        max_tokens=None,
    ) -> AsyncIterator[tuple[str, llm.Usage | None]]:
        calls.append(
            {
                "system": system_prompt,
                "user": user_prompt,
                "history": list(history),
                "model": config.model,
                "base_url": config.base_url,
                "model_key": getattr(config, "key", None),
            }
        )
        if error is not None:
            raise error
        for piece in pieces:
            yield piece, None
        yield "", llm.Usage(prompt_tokens=120, completion_tokens=45)

    monkeypatch.setattr(llm, "stream_chat", _fake_stream)
    return calls


async def _student(client: httpx.AsyncClient, user_no: str = "2026100") -> dict:
    response = await client.post(
        "/api/users", json={"user_no": user_no, "real_name": "张三", "user_type": "STUDENT"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _session(client: httpx.AsyncClient, student_id: int, **extra: Any) -> dict:
    response = await client.post("/api/qa/sessions", json={"student_id": student_id, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def _ask(
    client: httpx.AsyncClient,
    session_id: int,
    student_id: int,
    question: str,
    *,
    model: str | None = None,
) -> Any:
    return await client.post(
        f"/api/qa/sessions/{session_id}/ask",
        json={"student_id": student_id, "question": question, "stream": False, "model": model},
    )


# ------------------------------------------------------------------ 纯函数


def _msg(role: str, content: str, *, status: str = "COMPLETED") -> AiQaMessage:
    return AiQaMessage(session_id=1, role=role, content=content, status=status)


def test_group_rounds_tolerates_dangling_question() -> None:
    """学生问了但没答上（失败消息被过滤）时，那一轮只留提问，不能把它并到上一轮。"""
    rounds = qa.group_rounds(
        [
            _msg("USER", "q1"),
            _msg("ASSISTANT", "a1"),
            _msg("USER", "q2"),
        ]
    )
    assert [[item.content for item in one] for one in rounds] == [["q1", "a1"], ["q2"]]


def test_trim_history_drops_whole_rounds() -> None:
    """字符超预算时丢的是整轮，不是单条 —— 丢单条会留下没有回答的孤儿提问。"""
    messages = [
        _msg("USER", "q1"),
        _msg("ASSISTANT", "a" * 100),
        _msg("USER", "q2"),
        _msg("ASSISTANT", "b" * 100),
    ]
    # 总长 204 > 110：从最旧的一轮开始丢，丢掉第一轮（102）后就够了
    kept = qa.trim_history(messages, max_chars=110)
    assert [item.content for item in kept] == ["q2", "b" * 100]


def test_output_limit_goes_out_as_max_tokens() -> None:
    """要限就原样发 ``max_tokens``，不限就一个上限都不发。

    langchain-openai 1.x 会把顶层的 ``max_tokens`` 改名成 ``max_completion_tokens``，
    而 DeepSeek 只认 ``max_tokens`` —— 改名之后上限直接失效（实测给 60 的限额返回了
    3965 个输出 token）。所以上限走 ``extra_body`` 透传；问答链路不传上限，输出长度
    交给模型服务端的默认值。
    """
    from langchain_core.messages import HumanMessage

    model = llm._build_llm(TEST_LLM_CONFIG, temperature=None, max_tokens=64, json_mode=False, streaming=False)
    assert model.extra_body == {"max_tokens": 64}
    payload = model._get_request_payload([HumanMessage(content="hi")])
    assert "max_completion_tokens" not in payload

    unlimited = llm._build_llm(
        TEST_LLM_CONFIG, temperature=None, max_tokens=None, json_mode=False, streaming=False
    )
    assert not unlimited.extra_body
    assert "max_completion_tokens" not in unlimited._get_request_payload([HumanMessage(content="hi")])


# ------------------------------------------------------------------ 提问主链路


@pytest.mark.asyncio
async def test_ask_writes_messages_and_usage(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    student = await _student(client)
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    calls = _stub_stream(monkeypatch)

    response = await _ask(client, session["id"], student["id"], "什么是三端稳压管？")
    assert response.status_code == 200, response.text
    payload = response.json()

    message = payload["message"]
    assert message["role"] == "ASSISTANT"
    assert message["content"] == "三端稳压管是把电压稳定住的器件"
    assert message["status"] == "COMPLETED"
    assert message["model_name"] == "deepseek-v4-flash"
    assert message["prompt_tokens"] == 120
    assert message["completion_tokens"] == 45
    # 一期不检索：引用恒为空，但必须带一条"没有依据"的提醒
    assert payload["citations"] == []
    assert any("未接入知识库" in item for item in payload["warnings"])

    # 会话标题按首个问题自动生成，列表里才看得懂
    detail = (
        await client.get(f"/api/qa/sessions/{session['id']}", params={"student_id": student["id"]})
    ).json()
    assert detail["title"] == "什么是三端稳压管？"
    assert [item["role"] for item in detail["messages"]] == ["USER", "ASSISTANT"]

    # token 统计：当日与保留期内都能查
    usage = (await client.get("/api/qa/usage", params={"student_id": student["id"]})).json()
    assert usage["today"]["question_count"] == 1
    assert usage["today"]["total_tokens"] == 165
    assert usage["recent"]["total_tokens"] == 165
    assert usage["retention_days"] == 7

    # 一轮对话里历史为空（本轮问题不能被当成历史再喂一次）
    assert calls[0]["history"] == []


@pytest.mark.asyncio
async def test_history_window_keeps_last_three_rounds(
    client: httpx.AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    student = await _student(client, user_no="2026101")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("答",))

    for index in range(1, 5):
        response = await _ask(client, session["id"], student["id"], f"第{index}问")
        assert response.status_code == 200, response.text

    # 走服务层看一眼"下一轮会带上什么历史"：窗口是 3 轮 = 6 条，且首条必须是 USER
    turn = await qa.begin_turn(
        db_session,
        student_id=student["id"],
        session_id=session["id"],
        question="第5问",
    )
    assert [role for role, _ in turn.history] == ["USER", "ASSISTANT"] * 3
    assert turn.history[0][1] == "第2问"
    assert turn.history[-1][1] == "答"


@pytest.mark.asyncio
async def test_missing_api_key_records_failed_answer(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 key 时给可读提示，并且落一条 FAILED 的应答，不留"只有提问没有回答"的缺口。"""
    student = await _student(client, user_no="2026102")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch, api_key="")

    response = await _ask(client, session["id"], student["id"], "没配 key 会怎样？")
    assert response.status_code == 200
    assert response.json()["code"] == 422
    assert "api key" in response.json()["msg"]

    detail = (
        await client.get(f"/api/qa/sessions/{session['id']}", params={"student_id": student["id"]})
    ).json()
    assert [item["role"] for item in detail["messages"]] == ["USER", "ASSISTANT"]
    assert detail["messages"][1]["status"] == "FAILED"
    assert "api key" in detail["messages"][1]["content"]


@pytest.mark.asyncio
async def test_llm_failure_is_readable(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """模型报错时把原因翻成一句话，而不是把 SDK 的堆栈甩给学生。"""
    student = await _student(client, user_no="2026103")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, error=RuntimeError("connection reset by peer"))

    response = await _ask(client, session["id"], student["id"], "会失败吗？")
    assert response.json()["code"] == 422
    assert "调用大模型失败" in response.json()["msg"]
    assert "connection reset" in response.json()["msg"]


# ------------------------------------------------------------------ 归属与准入


@pytest.mark.asyncio
async def test_ask_rejects_other_students_session(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await _student(client, user_no="2026104")
    other = await _student(client, user_no="2026105")
    session = await _session(client, owner["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch)

    response = await _ask(client, session["id"], other["id"], "偷看别人的会话")
    assert response.json()["code"] == 422
    assert "不存在" in response.json()["msg"]


@pytest.mark.asyncio
async def test_same_session_rejects_parallel_ask(client: httpx.AsyncClient, db_session, monkeypatch) -> None:
    """同一会话同时只允许一条回答在生成，否则两轮回答的历史会互相污染。"""
    student = await _student(client, user_no="2026106")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch)

    await qa.begin_turn(db_session, student_id=student["id"], session_id=session["id"], question="第一问")
    with pytest.raises(ConflictError):
        await qa.begin_turn(db_session, student_id=student["id"], session_id=session["id"], question="第二问")


@pytest.mark.asyncio
async def test_rate_limit_blocks_burst(
    client: httpx.AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    student = await _student(client, user_no="2026107")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("答",))

    real_config = await settings_store.get_qa_config(db_session)
    limited = replace(real_config, rate_limit_per_minute=1)

    async def _fake_config(_session) -> settings_store.QaConfig:
        return limited

    monkeypatch.setattr(settings_store, "get_qa_config", _fake_config)

    first = await _ask(client, session["id"], student["id"], "第一问")
    assert first.status_code == 200, first.text
    assert "message" in first.json(), first.text
    second = await _ask(client, session["id"], student["id"], "第二问")
    assert second.json()["code"] == 422, second.text
    assert "太频繁" in second.json()["msg"]


# ------------------------------------------------------------------ 保留期与清理


@pytest.mark.asyncio
async def test_retention_hides_and_prunes_stale_sessions(
    client: httpx.AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    student = await _student(client, user_no="2026108")
    stale = await _session(client, student["id"])
    fresh = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("答",))
    await _ask(client, stale["id"], student["id"], "八天前问的")
    await _ask(client, fresh["id"], student["id"], "刚问的")

    # 把第一个会话的活动时间改成 8 天前
    row = (await db_session.exec(select(AiQaSession).where(AiQaSession.id == stale["id"]))).first()
    row.updated_at = now() - timedelta(days=8)
    db_session.add(row)
    await db_session.flush()

    listing = (await client.get("/api/qa/sessions", params={"student_id": student["id"]})).json()
    assert [item["id"] for item in listing["items"]] == [fresh["id"]]

    detail = await client.get(f"/api/qa/sessions/{stale['id']}", params={"student_id": student["id"]})
    assert detail.json()["code"] == 422
    assert "保留期" in detail.json()["msg"]

    blocked = await _ask(client, stale["id"], student["id"], "还能问吗？")
    assert blocked.json()["code"] == 422

    # 清理：按会话粒度整条删掉（消息一起走），保留期内的一条不动
    result = await qa.prune_history(db_session, days=7)
    assert result["sessions"] == 1
    assert result["messages"] == 2
    remaining = (await db_session.exec(select(AiQaSession))).all()
    assert [item.id for item in remaining] == [fresh["id"]]
    left_messages = (await db_session.exec(select(AiQaMessage))).all()
    assert {item.session_id for item in left_messages} == {fresh["id"]}


@pytest.mark.asyncio
async def test_prune_dry_run_keeps_data(client: httpx.AsyncClient, db_session) -> None:
    student = await _student(client, user_no="2026109")
    session = await _session(client, student["id"])
    row = (await db_session.exec(select(AiQaSession).where(AiQaSession.id == session["id"]))).first()
    row.updated_at = now() - timedelta(days=30)
    db_session.add(row)
    await db_session.flush()

    result = await qa.prune_history(db_session, days=7, dry_run=True)
    assert result["dry_run"] is True
    assert result["sessions"] == 1
    assert (await db_session.exec(select(AiQaSession))).first() is not None


@pytest.mark.asyncio
async def test_delete_session_cascades(client: httpx.AsyncClient, db_session, monkeypatch) -> None:
    student = await _student(client, user_no="2026110")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("答",))
    await _ask(client, session["id"], student["id"], "问一句就删")

    response = await client.delete(f"/api/qa/sessions/{session['id']}", params={"student_id": student["id"]})
    assert response.status_code == 200
    assert "已删除" in response.json()["message"]
    assert (await db_session.exec(select(AiQaSession))).all() == []
    assert (await db_session.exec(select(AiQaMessage))).all() == []
