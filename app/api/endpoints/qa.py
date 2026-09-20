"""AI 问答接口：会话管理、提问（SSE 流式）、token 用量统计。

一期不接检索：回答来自大模型的通用知识，``citations`` 恒为空数组，并在 ``warnings``
里明确告知"未接入知识库检索"。保留期、上下文窗口、token 统计口径见
``docs/AI问答实施方案-不接RAG阶段.md``。

流式接口的两条约定：

1. **SSE 不走统一响应体信封**（``text/event-stream`` 会被 ``wrap_success`` 原样放过），
   因此流中途的错误只能用 ``error`` 事件表达，不能指望全局兜底；
2. 生成器一开写就把 ``meta`` 发出去，前端拿到落库的消息 id，后续 ``done`` 才能对上。
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import DbSession, PageDep
from app.core.exceptions import NotFoundError
from app.core.response import EnvelopeRoute
from app.crud.account import UserRepository
from app.crud.qa import AiQaCitationRepository, AiQaMessageRepository, AiQaSessionRepository
from app.models.knowledge import AiQaSession
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.knowledge import (
    AiQaMessageRead,
    AiQaSessionCreate,
    AiQaSessionDetail,
    AiQaSessionRead,
    AiQaSessionUpdate,
    QaAskIn,
    QaAskOut,
    QaMessagePage,
    QaUsageOut,
    QaUsageTotal,
)
from app.services import qa, settings_store

router = APIRouter(route_class=EnvelopeRoute, tags=["AI 问答"])

#: 打开会话时一次返回多少条消息；更早的用 /messages 游标翻
DEFAULT_MESSAGE_LIMIT = 30
MAX_MESSAGE_LIMIT = 200

#: SSE 响应头：关掉各级缓存与反向代理缓冲，否则流会被攒成一坨再吐
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# --------------------------------------------------------------------- 依赖


def session_repo(db: DbSession) -> AiQaSessionRepository:
    return AiQaSessionRepository(db)


def message_repo(db: DbSession) -> AiQaMessageRepository:
    return AiQaMessageRepository(db)


def citation_repo(db: DbSession) -> AiQaCitationRepository:
    return AiQaCitationRepository(db)


def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


QaSessionRepo = Annotated[AiQaSessionRepository, Depends(session_repo)]
QaMessageRepo = Annotated[AiQaMessageRepository, Depends(message_repo)]
QaCitationRepo = Annotated[AiQaCitationRepository, Depends(citation_repo)]
UserRepo = Annotated[UserRepository, Depends(user_repo)]


# --------------------------------------------------------------------- 工具


async def _students_own_session(
    sessions: AiQaSessionRepository,
    session: DbSession,
    *,
    session_id: int,
    student_id: int,
) -> AiQaSession:
    """会话必须属于该学生、且在保留期内；越权与过期一律 404（不泄露存在性）。"""
    config = await settings_store.get_qa_config(session)
    row = await sessions.by_id_of_student(session_id, student_id)
    if row is None:
        raise NotFoundError(f"问答会话 {session_id} 不存在")
    if row.updated_at < qa.retention_cutoff(config):
        raise NotFoundError(
            f"该会话已超出保留期（仅保留最近 {config.history_retention_days} 天），请新建会话"
        )
    return row


def _sse(event: str, data: dict[str, Any]) -> str:
    """拼一帧 SSE：``event:`` + ``data:`` 两行 + 空行结尾。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# --------------------------------------------------------------------- 会话


@router.post(
    "/qa/sessions",
    response_model=ApiResponse[AiQaSessionRead],
    status_code=http_status.HTTP_201_CREATED,
    summary="新建问答会话",
)
async def create_qa_session(
    payload: AiQaSessionCreate,
    sessions: QaSessionRepo,
    users: UserRepo,
) -> AiQaSession:
    if await users.get(payload.student_id) is None:
        raise NotFoundError(f"学生 {payload.student_id} 不存在")
    return await sessions.create(payload.model_dump())


@router.get(
    "/qa/sessions",
    response_model=ApiResponse[Page[AiQaSessionRead]],
    summary="我的会话列表（只含保留期内活动过的会话）",
)
async def list_qa_sessions(
    sessions: QaSessionRepo,
    session: DbSession,
    params: PageDep,
    student_id: Annotated[int, Query(description="学生 ID")],
    status: Annotated[str | None, Query(description="ACTIVE 进行中 / CLOSED 已结束")] = None,
) -> Page:
    config = await settings_store.get_qa_config(session)
    return await sessions.list_of_student(
        student_id, params, since=qa.retention_cutoff(config), status=status
    )


@router.get(
    "/qa/sessions/{session_id}",
    response_model=ApiResponse[AiQaSessionDetail],
    summary="会话详情（默认带最近 30 条消息，正序）",
)
async def get_qa_session(
    session_id: int,
    sessions: QaSessionRepo,
    messages: QaMessageRepo,
    session: DbSession,
    student_id: Annotated[int, Query(description="学生 ID")],
    message_limit: Annotated[int, Query(ge=1, le=MAX_MESSAGE_LIMIT, description="返回多少条消息")] = (
        DEFAULT_MESSAGE_LIMIT
    ),
) -> dict[str, Any]:
    row = await _students_own_session(sessions, session, session_id=session_id, student_id=student_id)
    rows = await messages.page_of_session(session_id, before_id=None, limit=message_limit)
    return {**row.model_dump(), "messages": rows}


@router.get(
    "/qa/sessions/{session_id}/messages",
    response_model=ApiResponse[QaMessagePage],
    summary="会话历史消息（before_id 游标翻更早的消息）",
)
async def list_qa_messages(
    session_id: int,
    sessions: QaSessionRepo,
    messages: QaMessageRepo,
    session: DbSession,
    student_id: Annotated[int, Query(description="学生 ID")],
    before_id: Annotated[int | None, Query(description="取 id 小于它的消息；不传取最新一屏")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_MESSAGE_LIMIT)] = DEFAULT_MESSAGE_LIMIT,
) -> QaMessagePage:
    await _students_own_session(sessions, session, session_id=session_id, student_id=student_id)
    rows = await messages.page_of_session(session_id, before_id=before_id, limit=limit)
    # 用游标而不是 offset：流式回答是逐轮追加写入的，offset 翻页会漂移、重复或漏读
    return QaMessagePage(
        items=[AiQaMessageRead.model_validate(item) for item in rows],
        total=await messages.count_of_session(session_id),
        next_before_id=rows[0].id if rows else None,
    )


@router.patch(
    "/qa/sessions/{session_id}",
    response_model=ApiResponse[AiQaSessionRead],
    summary="会话改名 / 关闭 / 重开",
)
async def update_qa_session(
    session_id: int,
    payload: AiQaSessionUpdate,
    sessions: QaSessionRepo,
    session: DbSession,
    student_id: Annotated[int, Query(description="学生 ID")],
) -> AiQaSession:
    row = await _students_own_session(sessions, session, session_id=session_id, student_id=student_id)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        return row
    return await sessions.update(row, data)


@router.delete(
    "/qa/sessions/{session_id}",
    response_model=ApiResponse[MessageOut],
    summary="删除会话（连带消息与引用）",
)
async def delete_qa_session(
    session_id: int,
    sessions: QaSessionRepo,
    messages: QaMessageRepo,
    citations: QaCitationRepo,
    session: DbSession,
    student_id: Annotated[int, Query(description="学生 ID")],
) -> MessageOut:
    row = await _students_own_session(sessions, session, session_id=session_id, student_id=student_id)
    # 顺序固定：先删外面的引用，再删消息，最后删会话 —— 反了会留下悬空引用
    removed_citations = await citations.delete_of_sessions([session_id])
    removed_messages = await messages.delete_of_sessions([session_id])
    await sessions.remove(row)
    return MessageOut(
        message=f"会话 {session_id} 已删除，连带删除 {removed_messages} 条消息、{removed_citations} 条引用"
    )


# --------------------------------------------------------------------- 提问


@router.post(
    "/qa/sessions/{session_id}/ask",
    response_class=StreamingResponse,
    summary="提问（默认 SSE 流式；stream=false 返回一次性 JSON）",
)
async def ask_question(session_id: int, payload: QaAskIn, session: DbSession) -> Any:
    if not payload.stream:
        result = await qa.answer_once(
            session,
            student_id=payload.student_id,
            session_id=session_id,
            question=payload.question,
            model=payload.model,
        )
        body = QaAskOut(
            message=AiQaMessageRead.model_validate(result.message),
            usage=QaUsageOut(
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                estimated=result.usage.estimated,
            ),
            citations=[],
            warnings=result.warnings,
            elapsed_ms=result.elapsed_ms,
        )
        # 走 JSONResponse 而不是直接返回模型：本路由声明了 StreamingResponse，
        # 直接返回对象会被按流处理；包成 JSON 后再由 EnvelopeRoute 套统一响应体。
        return JSONResponse(content=jsonable_encoder(body))

    turn = await qa.begin_turn(
        session,
        student_id=payload.student_id,
        session_id=session_id,
        question=payload.question,
        model=payload.model,
    )

    async def event_stream() -> AsyncIterator[str]:
        async for event, data in qa.stream_answer(session, turn):
            yield _sse(event, data)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


# --------------------------------------------------------------------- 用量


@router.get(
    "/qa/usage",
    response_model=ApiResponse[QaUsageTotal],
    summary="token 用量统计（当日 / 保留期内，只统计不限制）",
)
async def get_qa_usage(
    session: DbSession,
    student_id: Annotated[int, Query(description="学生 ID")],
) -> dict[str, Any]:
    return await qa.usage_summary(session, student_id=student_id)


__all__ = ["router"]
