"""AI 问答流式链路：SSE 事件序列、断线落库、游标翻页。

流式响应在测试里直接驱动生成器（``astream`` / ``aclose``），比走 ASGI 更精确地
模拟"客户端中途断开"，也顺带验证了收尾落库确实在 ``finally`` 里。
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlmodel import select

from app.models.enums import QaMessageStatus
from app.models.knowledge import AiQaMessage
from app.services import llm, qa
from tests.test_qa_flow import (  # 复用同一套假模型与建数据工具
    _ask,
    _session,
    _stub_llm_config,
    _stub_stream,
    _student,
)


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析成 ``[(event, data)]``，顺便验证帧格式没错。"""
    import json

    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        if not lines:
            continue
        event = next(line.split(": ", 1)[1] for line in lines if line.startswith("event: "))
        payload = json.loads(next(line.split(": ", 1)[1] for line in lines if line.startswith("data: ")))
        events.append((event, payload))
    return events


@pytest.mark.asyncio
async def test_sse_event_sequence(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    student = await _student(client, user_no="2026200")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("三端", "稳压管"))

    async with client.raw.stream(
        "POST",
        f"/api/qa/sessions/{session['id']}/ask",
        json={"student_id": student["id"], "question": "流式怎么返回？", "stream": True},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        raw = "".join([chunk async for chunk in response.aiter_text()])

    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["meta", "delta", "delta", "done"]
    assert events[0][1]["message_id"], "首帧必须给出落库的消息 id"
    assert "".join(item["text"] for name, item in events if name == "delta") == "三端稳压管"
    done = events[-1][1]
    assert done["citations"] == []
    assert done["usage"]["prompt_tokens"] == 120
    assert done["usage"]["estimated"] is False
    assert any("未接入知识库" in item for item in done["warnings"])


@pytest.mark.asyncio
async def test_sse_error_frame_carries_reason(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """流中途的异常不能靠统一响应体兜底，必须自己发 error 事件。"""
    student = await _student(client, user_no="2026201")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, error=RuntimeError("upstream timeout"))

    async with client.raw.stream(
        "POST",
        f"/api/qa/sessions/{session['id']}/ask",
        json={"student_id": student["id"], "question": "会断吗？", "stream": True},
    ) as response:
        raw = "".join([chunk async for chunk in response.aiter_text()])

    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["meta", "error"]
    assert "调用大模型失败" in events[-1][1]["msg"]


@pytest.mark.asyncio
async def test_client_disconnect_still_persists_partial_answer(
    client: httpx.AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """客户端中途断开（aclose）时，已经生成的那半句也要落库，刷新页面能看见。"""
    student = await _student(client, user_no="2026202")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("第一段", "第二段", "第三段"))

    turn = await qa.begin_turn(
        db_session, student_id=student["id"], session_id=session["id"], question="断开试试"
    )
    stream = qa.stream_answer(db_session, turn)
    assert (await anext(stream))[0] == "meta"
    assert (await anext(stream))[0] == "delta"
    await stream.aclose()  # 等价于客户端取消请求

    rows = list((await db_session.exec(select(AiQaMessage).order_by(AiQaMessage.id))).all())
    assert [item.role for item in rows] == ["USER", "ASSISTANT"]
    answer = rows[-1]
    assert answer.content == "第一段"
    assert answer.status == QaMessageStatus.FAILED
    # 在飞标记要清掉，否则这个会话会被永久卡在"还有回答在生成"
    follow_up = await qa.begin_turn(
        db_session, student_id=student["id"], session_id=session["id"], question="还能继续问"
    )
    assert follow_up.user_message.content == "还能继续问"
    await qa.stream_answer(db_session, follow_up).aclose()  # 别把在飞标记留给下一个用例


@pytest.mark.asyncio
async def test_message_cursor_pagination(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """历史用 id 游标往前翻，不用 offset（流式追加写入时 offset 会漂移）。"""
    student = await _student(client, user_no="2026203")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("答",))
    for index in range(1, 4):
        await _ask(client, session["id"], student["id"], f"第{index}问")

    first = (
        await client.get(
            f"/api/qa/sessions/{session['id']}/messages",
            params={"student_id": student["id"], "limit": 2},
        )
    ).json()
    assert [item["content"] for item in first["items"]] == ["第3问", "答"]
    assert first["total"] == 6
    assert first["next_before_id"] == first["items"][0]["id"]

    older = (
        await client.get(
            f"/api/qa/sessions/{session['id']}/messages",
            params={
                "student_id": student["id"],
                "limit": 2,
                "before_id": first["next_before_id"],
            },
        )
    ).json()
    assert [item["content"] for item in older["items"]] == ["第2问", "答"]

    # 游标模式不会漏也不会重：两页拼起来没有任何交集
    assert {item["id"] for item in first["items"]} & {item["id"] for item in older["items"]} == set()


@pytest.mark.asyncio
async def test_usage_estimates_when_endpoint_hides_usage(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端点不返回 usage 时按字符估算回填 —— 留空会让统计永远是 0。"""
    student = await _student(client, user_no="2026204")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch, pieces=("回答正文",))

    async def _no_usage_stream(config: object, **kwargs: object) -> AsyncIterator[tuple[str, None]]:
        yield "回答正文", None  # 只在末尾给 usage 的帧都不给

    monkeypatch.setattr(llm, "stream_chat", _no_usage_stream)

    response = await _ask(client, session["id"], student["id"], "有用量吗？")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "usage" in payload, response.text
    assert payload["usage"]["estimated"] is True
    assert (payload["usage"]["completion_tokens"] or 0) > 0

    # 估算出来的用量同样计入统计
    usage = (await client.get("/api/qa/usage", params={"student_id": student["id"]})).json()
    assert usage["today"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_empty_answer_is_reported_as_failure(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """输出预算被推理过程吃光时模型会返回"零正文"，这要按失败处理，不能给一条空回答。"""
    student = await _student(client, user_no="2026206")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)

    async def _empty_stream(config: object, **kwargs: object) -> AsyncIterator[tuple[str, None]]:
        yield "", None

    monkeypatch.setattr(llm, "stream_chat", _empty_stream)

    response = await _ask(client, session["id"], student["id"], "长问题")
    assert response.json()["code"] == 422, response.text
    assert "没有返回内容" in response.json()["msg"]
    detail = (
        await client.get(f"/api/qa/sessions/{session['id']}", params={"student_id": student["id"]})
    ).json()
    assert detail["messages"][-1]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_closed_session_rejects_ask(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    student = await _student(client, user_no="2026205")
    session = await _session(client, student["id"])
    _stub_llm_config(monkeypatch)
    _stub_stream(monkeypatch)
    await client.patch(
        f"/api/qa/sessions/{session['id']}",
        params={"student_id": student["id"]},
        json={"status": "CLOSED"},
    )
    response = await _ask(client, session["id"], student["id"], "关了还能问吗？")
    assert response.json()["code"] == 422
    assert "已结束" in response.json()["msg"]
