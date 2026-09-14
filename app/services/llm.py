"""主模型调用层：统一走 OpenAI 兼容端点（DeepSeek / Kimi / GLM 都支持）。

AI 评审（结构化 JSON、一次性返回）与 AI 问答（自由文本、流式）都从这里出去，
这样超时、usage 解析、依赖缺失提示只有一份实现。

**usage 的坑**：流式响应要显式带 ``stream_options={"include_usage": true}`` 才会在
最后一帧给出 token 用量（langchain-openai 侧是 ``stream_usage=True``），而且不是所有
兼容端点都支持。拿不到时用 :func:`estimate_usage` 按字符数粗估回填 —— 留空会让用量
统计永远是 0，宁可不精确也不能失效。
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import BusinessRuleError
from app.services.settings_store import LLMConfig

#: 缺 langchain 依赖时的统一提示
LLM_EXTRA_HINT = "缺少大模型调用依赖（langchain-openai），先执行：uv sync --extra llm"

#: 字符数 → token 的粗略换算（中文约 1.5 字符/token、英文约 4 字符/token，取折中值）
CHARS_PER_TOKEN = 2


def llm_available() -> bool:
    """langchain-openai 是否可用（没装不影响其它功能，只是不能调模型）。"""
    try:
        import langchain_openai  # noqa: F401
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class Usage:
    """一次调用的 token 用量。``estimated=True`` 表示按字符估算，不是模型返回的。"""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated: bool = False


def estimate_usage(prompt_text: str, answer_text: str) -> Usage:
    """端点不给 usage 时的兜底估算：按字符数折算，绝不返回空值。"""
    return Usage(
        prompt_tokens=max(1, len(prompt_text) // CHARS_PER_TOKEN),
        completion_tokens=max(1, len(answer_text) // CHARS_PER_TOKEN),
        estimated=True,
    )


def _build_llm(
    config: LLMConfig,
    *,
    temperature: float | None,
    max_tokens: int | None,
    json_mode: bool,
    streaming: bool,
) -> Any:
    """构造 ChatOpenAI。``stream_usage`` 在旧版本上没有，构造失败就退回不带它。"""
    if not llm_available():
        raise BusinessRuleError(LLM_EXTRA_HINT)
    from langchain_openai import ChatOpenAI

    options: dict[str, Any] = {
        "model": config.model,
        "api_key": config.api_key,
        "base_url": config.base_url,
        "temperature": config.temperature if temperature is None else temperature,
        "timeout": config.timeout,
    }
    if max_tokens:
        # 走 extra_body 而不是顶层 max_tokens：langchain-openai 1.x 会把 max_tokens
        # 改名成 max_completion_tokens，而 DeepSeek 只认 max_tokens —— 改名后上限直接失效
        # （实测给 60 的限额，模型返回了 3965 个输出 token）。extra_body 是原样透传的。
        #
        # 不传 ``max_tokens`` 就一个上限都不发：模型的输出长度由服务端默认值决定，
        # 业务侧不替它做决定（问答链路就是这么用的）。
        options["extra_body"] = {"max_tokens": max_tokens}
    if json_mode:
        options["model_kwargs"] = {"response_format": {"type": "json_object"}}
    if streaming:
        try:
            return ChatOpenAI(**options, stream_usage=True)
        except TypeError:  # 旧版本 langchain-openai 没有这个参数
            pass
    return ChatOpenAI(**options)


def _usage_of(response: Any) -> Usage | None:
    """从 LangChain 的返回对象里抠 usage：新版看 usage_metadata，旧版看 response_metadata。"""
    metadata = getattr(response, "usage_metadata", None)
    if isinstance(metadata, dict) and metadata:
        return Usage(
            prompt_tokens=metadata.get("input_tokens"),
            completion_tokens=metadata.get("output_tokens"),
        )
    raw = getattr(response, "response_metadata", None) or {}
    token_usage = raw.get("token_usage") if isinstance(raw, dict) else None
    if isinstance(token_usage, dict) and token_usage:
        return Usage(
            prompt_tokens=token_usage.get("prompt_tokens"),
            completion_tokens=token_usage.get("completion_tokens"),
        )
    return None


def _content_of(message: Any) -> str:
    """少数模型返回内容块列表，这里统一拼成字符串。"""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return "".join(str(part) for part in content)


def _messages(system_prompt: str, history: Sequence[tuple[str, str]], user_prompt: str) -> list[Any]:
    """拼消息序列：system → 历史（user/assistant 交替）→ 本轮问题。

    历史以消息而不是文本块的形式给出，多轮问答才不会被模型当成"用户一次性贴了一段对话"。
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages: list[Any] = [SystemMessage(content=system_prompt)]
    for role, content in history:
        messages.append(AIMessage(content=content) if role == "ASSISTANT" else HumanMessage(content=content))
    messages.append(HumanMessage(content=user_prompt))
    return messages


async def complete(
    config: LLMConfig,
    *,
    system_prompt: str,
    user_prompt: str,
    history: Sequence[tuple[str, str]] = (),
    json_mode: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> tuple[str, str | None, Usage | None]:
    """一次性调用；返回 ``(正文, 请求ID, 用量)``。"""
    llm = _build_llm(
        config,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=json_mode,
        streaming=False,
    )
    response = await llm.ainvoke(_messages(system_prompt, history, user_prompt))
    request_id = getattr(response, "id", None)
    prompt_text = system_prompt + user_prompt + "".join(item[1] for item in history)
    usage = _usage_of(response) or estimate_usage(prompt_text, _content_of(response))
    return _content_of(response), (str(request_id) if request_id else None), usage


async def stream_chat(
    config: LLMConfig,
    *,
    system_prompt: str,
    user_prompt: str,
    history: Sequence[tuple[str, str]] = (),
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[tuple[str, Usage | None]]:
    """流式调用：逐个产出 ``(增量文本, 用量)``。

    普通帧的用量是 ``None``；最后一帧可能是空文本 + 真实用量（带 ``stream_usage`` 时），
    也可能什么都没有 —— 调用方拿不到就自行用 :func:`estimate_usage` 兜底。
    """
    llm = _build_llm(config, temperature=temperature, max_tokens=max_tokens, json_mode=False, streaming=True)
    async for chunk in llm.astream(_messages(system_prompt, history, user_prompt)):
        text = _content_of(chunk)
        usage = _usage_of(chunk)
        if text or usage is not None:
            yield text, usage


__all__ = [
    "CHARS_PER_TOKEN",
    "LLM_EXTRA_HINT",
    "Usage",
    "complete",
    "estimate_usage",
    "llm_available",
    "stream_chat",
]
