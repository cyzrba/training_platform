"""AI 问答编排：校验 → 落 USER 消息 → 组装 prompt → 流式生成 → 收尾落库。

一次提问的时序（每一步都有明确理由，改动前先看注释）：

1. 会话归属 / 状态 / 保留期 / 并发 / 频控 / 问题长度校验；
2. **先取历史，再写 USER 消息** —— 顺序反了会把本轮问题也当成历史喂回去；
3. 写 USER 消息（断线时学生至少能看到"我问过什么"）；
4. 取上下文（一期恒为空，二期由 ``qa_context`` 填检索片段）；
5. 流式调用主模型，逐块产出 ``delta``；
6. **finally 里收尾落库** —— 正常、报错、客户端断开三条路径都要把 ASSISTANT 消息写下来，
   否则学生刷新页面会看到一条没有回答的提问。

上下文窗口是**最近 3 轮**（1 轮 = 1 问 + 1 答），按"轮"裁剪而不是按"条"：按条硬截会
切在回答中间，历史以 ASSISTANT 开头会让模型把上一轮回答当成用户说的话。
"""

import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.time import now
from app.crud.qa import AiQaCitationRepository, AiQaMessageRepository, AiQaSessionRepository
from app.models.enums import QaMessageStatus, QaRole, QaSessionStatus
from app.models.knowledge import AiQaMessage, AiQaSession
from app.services import llm, qa_context, settings_store
from app.services.qa_context import ContextBundle
from app.services.settings_store import LLMConfig, QaConfig

#: 内置提示词。配置 ``ai.qa.system_prompt`` 留空时用它，填了就覆盖（改提示词不用发版）。
DEFAULT_SYSTEM_PROMPT = """你是「岗位闯关式实训平台」的 AI 助教，面向人工智能相关专业的实训学生，
主要覆盖工业视觉、数据处理、模型训练与优化等方向。

回答要求：
1. 用中文作答，先给结论再展开要点；步骤、参数、代码用列表或代码块（代码块标注语言）；
2. 按"懂技术的初学者"能看懂的水准讲，不复述常识、不堆砌空话；
3. 涉及平台内部数据（项目名称、关卡内容、成绩、教师点评）时以平台数据为准；
   没有拿到就说"我这边看不到这项数据，请以页面显示为准"，**不要编造**；
4. 不确定的内容直接说不确定，宁可少答也不要猜；
5. 当前没有接入知识库检索，回答来自模型的通用知识：**不要写"根据知识库/根据参考资料/
   根据文档"这类措辞**，也不要在正文里编造引用编号；
6. 问题与课程、实训、技术无关时，一句话礼貌回应并把话题引回学习内容。"""

#: 同一会话"在飞提问"的判定上限（略大于模型超时，异常退出也不会把会话永久卡住）
INFLIGHT_GRACE_SECONDS = 30

#: 进程内的在飞标记与会话频控窗口（一期单进程足够；多实例要换 Redis）
_inflight: dict[int, float] = {}
_recent_asks: dict[int, deque[float]] = {}

#: 会话空闲到这个状态之外就不再允许提问（保留期按 updated_at 算）
RETENTION_ERROR = "该会话已超出保留期（仅保留最近 {days} 天），请新建会话继续提问"


@dataclass
class QaTurn:
    """一次提问的准备结果：已经落库的 USER 消息 + 拼好的 prompt。"""

    session_row: AiQaSession
    user_message: AiQaMessage
    system_prompt: str
    user_prompt: str
    history: list[tuple[str, str]] = field(default_factory=list)
    bundle: ContextBundle = field(default_factory=ContextBundle)
    warnings: list[str] = field(default_factory=list)
    qa_config: QaConfig | None = None
    llm_config: LLMConfig | None = None

    @property
    def session_id(self) -> int:
        return self.session_row.id or 0


@dataclass
class QaAnswer:
    """非流式提问的结果。"""

    message: AiQaMessage
    usage: llm.Usage
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


# ------------------------------------------------------------------ 纯函数


def retention_cutoff(config: QaConfig) -> datetime:
    """保留期起点：早于这个时间没活动过的会话，学生看不到、也提问不了。"""
    return now() - timedelta(days=config.history_retention_days)


def group_rounds(messages: list[AiQaMessage]) -> list[list[AiQaMessage]]:
    """把消息切成"轮"：1 轮 = 1 问 + 1 答（答缺失时该轮只有提问）。"""
    rounds: list[list[AiQaMessage]] = []
    for message in messages:
        if message.role == QaRole.USER or not rounds:
            rounds.append([message])
        else:
            rounds[-1].append(message)
    return rounds


def trim_history(messages: list[AiQaMessage], max_chars: int) -> list[AiQaMessage]:
    """历史字符超预算时，从最旧的一轮开始整轮丢弃（不丢单条，避免半个问答）。"""
    rounds = group_rounds(messages)
    total = sum(len(item.content or "") for item in messages)
    while rounds and total > max_chars:
        dropped = rounds.pop(0)
        total -= sum(len(item.content or "") for item in dropped)
    return [item for one_round in rounds for item in one_round]


def to_pairs(messages: list[AiQaMessage]) -> list[tuple[str, str]]:
    """转成 ``[(role, content)]`` 交给模型调用层拼消息序列。"""
    return [(item.role, item.content or "") for item in messages]


def build_user_prompt(bundle: ContextBundle, question: str) -> str:
    """本轮提问的正文：先放依据槽位（一期为空），再放问题。"""
    context_text = qa_context.render_context(bundle)
    if not context_text:
        return question
    return f"{context_text}\n\n# 学生提问\n{question}"


def readable_error(exc: Exception) -> str:
    """把异常翻成能直接展示给学生的一句话。"""
    if isinstance(exc, BusinessRuleError):
        return exc.message
    if isinstance(exc, (NotFoundError, ConflictError)):
        return exc.message
    text = str(exc).strip() or exc.__class__.__name__
    return f"调用大模型失败：{text[:200]}"


# ------------------------------------------------------------------ 准入控制


def _inflight_since(session_id: int) -> float | None:
    """取在飞开始时间；超过宽限期视为异常残留，直接清掉，避免会话被永久卡死。"""
    started = _inflight.get(session_id)
    if started is None:
        return None
    if time.monotonic() - started > INFLIGHT_GRACE_SECONDS:
        _inflight.pop(session_id, None)
        return None
    return started


def _check_rate_limit(student_id: int, per_minute: int) -> None:
    if per_minute <= 0:
        return
    window = _recent_asks.setdefault(student_id, deque())
    current = time.monotonic()
    while window and current - window[0] > 60:
        window.popleft()
    if len(window) >= per_minute:
        raise BusinessRuleError(f"提问太频繁了，每分钟最多 {per_minute} 次，稍后再试")
    window.append(current)


def reset_limits() -> None:
    """清空进程内的在飞标记与频控窗口（测试与维护用）。"""
    _inflight.clear()
    _recent_asks.clear()


# ------------------------------------------------------------------ 主流程


async def _session_or_404(
    sessions: AiQaSessionRepository, session_id: int, student_id: int, *, cutoff: datetime, days: int
) -> AiQaSession:
    # 分两步报错：先看"有没有、是不是你的"，再看"过没过保留期"。
    # 合成一句会让学生对着一个打错的 id 看到"已超出保留期"，排查方向全错。
    row = await sessions.by_id_of_student(session_id, student_id)
    if row is None:
        raise NotFoundError(f"问答会话 {session_id} 不存在")
    if row.updated_at < cutoff:
        raise NotFoundError(RETENTION_ERROR.format(days=days))
    return row


async def begin_turn(
    session: AsyncSession,
    *,
    student_id: int,
    session_id: int,
    question: str,
    model: str | None = None,
) -> QaTurn:
    """校验 + 落 USER 消息 + 拼 prompt，返回可交给 :func:`stream_answer` 的上下文。

    ``model`` 是学生在 AI 助教里选的模型选项 id（``deepseek`` / ``kimi`` / ``mimo``…），
    留空用默认模型；取值见 ``settings_store.get_llm_config``。
    """
    config = await settings_store.get_qa_config(session)
    llm_config = await settings_store.get_llm_config(session, model)
    text = (question or "").strip()
    if not text:
        raise BusinessRuleError("问题不能为空")
    if len(text) > config.max_question_chars:
        raise BusinessRuleError(f"问题太长了（上限 {config.max_question_chars} 字），请精简后重试")

    sessions = AiQaSessionRepository(session)
    row = await _session_or_404(
        sessions,
        session_id,
        student_id,
        cutoff=retention_cutoff(config),
        days=config.history_retention_days,
    )
    if row.status == QaSessionStatus.CLOSED:
        raise ConflictError("该会话已结束，请新建会话继续提问")
    if _inflight_since(session_id) is not None:
        raise ConflictError("这个会话还有一条回答正在生成，请等它结束再提问")
    _check_rate_limit(student_id, config.rate_limit_per_minute)

    messages = AiQaMessageRepository(session)
    # 顺序很关键：历史必须在写入本轮提问之前取，否则本轮问题会被当成历史再喂一次
    history = trim_history(
        await messages.recent_turns(session_id, config.history_rounds), config.history_max_chars
    )
    user_message = await messages.create(
        {
            "session_id": session_id,
            "role": QaRole.USER,
            "content": text,
            "status": QaMessageStatus.COMPLETED,
        }
    )
    if not row.title:
        row = await sessions.update(row, {"title": text[:30]})
    else:
        row = await sessions.touch(row)  # 会话列表按 updated_at 排序，必须刷新

    bundle = await qa_context.get_provider(config.context_provider).provide(
        session, session_row=row, question=text, history=history
    )
    system_prompt = (config.system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
    turn = QaTurn(
        session_row=row,
        user_message=user_message,
        system_prompt=system_prompt,
        user_prompt=build_user_prompt(bundle, text),
        history=to_pairs(history),
        bundle=bundle,
        warnings=list(bundle.warnings),
        qa_config=config,
        llm_config=llm_config,
    )
    _inflight[session_id] = time.monotonic()
    return turn


async def _persist_answer(
    session: AsyncSession,
    turn: QaTurn,
    *,
    content: str,
    status: str,
    usage: llm.Usage | None,
    error: str | None,
) -> AiQaMessage:
    """收尾落库：正常、报错、断开都走这里。"""
    llm_config = turn.llm_config
    if usage is None:
        usage = llm.estimate_usage(turn.system_prompt + turn.user_prompt, content)
    body = content.strip() or (error or "回答失败")
    return await AiQaMessageRepository(session).create(
        {
            "session_id": turn.session_id,
            "role": QaRole.ASSISTANT,
            "content": body,
            "model_name": llm_config.model if llm_config else None,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "status": status,
        }
    )


async def stream_answer(session: AsyncSession, turn: QaTurn) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """产出 SSE 事件：``meta`` → ``delta``* → ``done`` / ``error``。

    收尾落库放在 ``finally``：正常结束、模型报错、客户端断开（``aclose()``）三条路径都会把
    ASSISTANT 消息写下来 —— 断线时学生刷新页面还能看到已经生成的那半句。
    """
    started = time.monotonic()
    chunks: list[str] = []
    usage: llm.Usage | None = None
    error: str | None = None
    finished = False
    yield (
        "meta",
        {
            "session_id": turn.session_id,
            "message_id": turn.user_message.id,
            "model": turn.llm_config.model if turn.llm_config else None,
            "model_key": turn.llm_config.key if turn.llm_config else None,
            "model_label": turn.llm_config.display_name if turn.llm_config else None,
            "created_at": turn.user_message.created_at.isoformat(),
        },
    )
    try:
        if turn.llm_config is None:
            raise BusinessRuleError("还没配置大模型 api key：请在系统配置 ai.llm 里填 api_key")
        if not turn.llm_config.has_endpoint:
            # 新增一家模型时最常见的漏配：接口地址 / 模型名没填
            raise BusinessRuleError(
                f"{turn.llm_config.display_name} 的接口地址或模型名还没配："
                f"请在系统配置 ai.llm.models.{turn.llm_config.key} 里填 base_url 与 model"
            )
        if not turn.llm_config.configured:
            where = (
                "ai.llm 里填 api_key"
                if turn.llm_config.key == settings_store.DEFAULT_LLM_MODEL
                else f"ai.llm.models.{turn.llm_config.key} 里填 api_key"
            )
            raise BusinessRuleError(f"{turn.llm_config.display_name} 还没配置 api key：请在系统配置 {where}")
        async for delta, chunk_usage in llm.stream_chat(
            turn.llm_config,
            system_prompt=turn.system_prompt,
            user_prompt=turn.user_prompt,
            history=turn.history,
            temperature=turn.qa_config.temperature if turn.qa_config else None,
        ):
            if chunk_usage is not None:
                usage = chunk_usage
            if delta:
                chunks.append(delta)
                yield "delta", {"text": delta}
        if not "".join(chunks).strip():
            # 输出预算被推理过程吃光时会返回"零正文"（流式协议里是正常的完成帧，
            # 但不是一次可用的回答）。当成失败处理，别给前端一条空消息。
            raise BusinessRuleError("模型没有返回内容，请重试或换个问法")
        finished = True
    except Exception as exc:  # 模型/网络/鉴权错误都走这里，转成可读提示
        error = readable_error(exc)
    finally:
        content = "".join(chunks)
        status = QaMessageStatus.COMPLETED if finished else QaMessageStatus.FAILED
        message = await _persist_answer(
            session, turn, content=content, status=status, usage=usage, error=error
        )
        _inflight.pop(turn.session_id, None)

    if error is not None:
        yield "error", {"code": "LLM_FAILED", "msg": error, "message_id": message.id}
        return
    yield (
        "done",
        {
            "message_id": message.id,
            "usage": {
                "prompt_tokens": message.prompt_tokens,
                "completion_tokens": message.completion_tokens,
                "estimated": bool(usage.estimated) if usage else True,
            },
            "citations": [],
            "warnings": turn.warnings,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )


async def answer_once(
    session: AsyncSession,
    *,
    student_id: int,
    session_id: int,
    question: str,
    model: str | None = None,
) -> QaAnswer:
    """非流式提问：内部消费同一条流式链路，把增量拼成完整回答返回。

    走同一条链路是为了让"落库时机、失败处理、权限校验"只有一份实现 —— 脚本化验收
    和集成测试因此不必解析 SSE。
    """
    turn = await begin_turn(
        session, student_id=student_id, session_id=session_id, question=question, model=model
    )
    payload: dict[str, Any] | None = None
    async for event, data in stream_answer(session, turn):
        if event == "error":
            raise BusinessRuleError(str(data.get("msg") or "回答失败"))
        if event == "done":
            payload = data
    if payload is None:
        raise BusinessRuleError("回答没有正常结束，请稍后重试")
    message = await AiQaMessageRepository(session).get(int(payload["message_id"]))
    if message is None:  # 理论上不可能：上一步刚写进去
        raise NotFoundError("回答消息写入后读不到了")
    usage_payload = payload.get("usage") or {}
    return QaAnswer(
        message=message,
        usage=llm.Usage(
            prompt_tokens=usage_payload.get("prompt_tokens"),
            completion_tokens=usage_payload.get("completion_tokens"),
            estimated=bool(usage_payload.get("estimated")),
        ),
        citations=list(payload.get("citations") or []),
        warnings=list(payload.get("warnings") or []),
        elapsed_ms=int(payload.get("elapsed_ms") or 0),
    )


async def usage_summary(session: AsyncSession, *, student_id: int) -> dict[str, Any]:
    """token 用量统计（只统计不限制）：当日 / 保留期内。"""
    config = await settings_store.get_qa_config(session)
    session_ids = await AiQaSessionRepository(session).ids_of_student(student_id)
    messages = AiQaMessageRepository(session)
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)

    today_counts = await messages.usage_of_sessions(session_ids, since=day_start)
    recent_counts = await messages.usage_of_sessions(session_ids, since=retention_cutoff(config))

    def _summary(scope: str, counts: tuple[int, int, int]) -> dict[str, Any]:
        questions, prompt_tokens, completion_tokens = counts
        return {
            "scope": scope,
            "question_count": questions,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    return {
        "student_id": student_id,
        "retention_days": config.history_retention_days,
        "today": _summary("today", today_counts),
        "recent": _summary(f"recent_{config.history_retention_days}d", recent_counts),
    }


async def prune_history(
    session: AsyncSession, *, days: int | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """按会话粒度清理保留期外的历史（``scripts/prune_qa_history.py`` 挂 cron 调用）。

    先删引用、再删消息、最后删会话 —— 反了会撞上 ``ai_qa_citation.message_id`` 的外键。
    ``dry_run`` 只统计会删掉多少个会话，不动数据（上线前先跑一次看看量级）。
    """
    config = await settings_store.get_qa_config(session)
    keep_days = config.history_retention_days if days is None else max(1, days)
    cutoff = now() - timedelta(days=keep_days)
    sessions = AiQaSessionRepository(session)
    stale = await sessions.stale_ids(cutoff)
    result: dict[str, Any] = {
        "days": keep_days,
        "cutoff": cutoff.isoformat(),
        "sessions": len(stale),
        "messages": 0,
        "citations": 0,
        "dry_run": dry_run,
    }
    if dry_run or not stale:
        return result
    result["citations"] = await AiQaCitationRepository(session).delete_of_sessions(stale)
    result["messages"] = await AiQaMessageRepository(session).delete_of_sessions(stale)
    result["sessions"] = await sessions.delete_of_ids(stale)
    return result


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "INFLIGHT_GRACE_SECONDS",
    "QaAnswer",
    "QaTurn",
    "answer_once",
    "begin_turn",
    "build_user_prompt",
    "group_rounds",
    "prune_history",
    "readable_error",
    "reset_limits",
    "retention_cutoff",
    "stream_answer",
    "to_pairs",
    "trim_history",
    "usage_summary",
]
