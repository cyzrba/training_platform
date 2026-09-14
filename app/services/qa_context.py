"""问答上下文提供者：把"给模型的依据"做成可插拔的一层。

一期 ``none``：不检索，返回空上下文 + 一条提醒（回答来自模型通用知识）；
二期 ``knowledge``：调 ``retrieval.recall`` 召回知识切片，填进 ``ContextBundle.text``，
并把 ``hits`` 落到 ``ai_qa_citation``。

**接口、SSE 事件、表结构都不随这个切换而变** —— 这是"先不接 RAG、后接不返工"的全部秘密：
拼 prompt 的那段代码只管往 ``<knowledge>`` 槽位里填 ``bundle.text``，填的是空串还是检索
片段，它不关心。
"""

from dataclasses import dataclass
from typing import Any, Protocol

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import BusinessRuleError

#: 无检索时固定带上的提醒，直接进 SSE 的 warnings 与接口返回
NO_KNOWLEDGE_WARNING = "当前未接入知识库检索，回答来自模型通用知识，可能不准确"

#: prompt 里承载依据的槽位名，二期填检索片段时保持同一个名字
KNOWLEDGE_SLOT = "knowledge"


@dataclass(frozen=True)
class ContextHit:
    """一条可追溯的依据（二期由检索结果填充，一期恒为空）。"""

    doc_id: int | None = None
    chunk_id: int | None = None
    source_title: str | None = None
    snippet: str = ""
    relevance: float | None = None


@dataclass(frozen=True)
class ContextBundle:
    """一次提问需要的上下文：给模型的文本 + 给引用用的命中列表 + 降级提示。"""

    hits: tuple[ContextHit, ...] = ()
    text: str = ""
    warnings: tuple[str, ...] = ()


class ContextProvider(Protocol):
    """上下文提供者契约。实现类只需要关心"怎么拿到依据"。"""

    name: str

    async def provide(
        self,
        session: AsyncSession,
        *,
        session_row: Any,
        question: str,
        history: list[Any],
    ) -> ContextBundle: ...


class EmptyContextProvider:
    """一期实现：不检索。上下文为空，但必须明确告诉调用方"这次没有依据"。"""

    name = "none"

    async def provide(
        self,
        session: AsyncSession,
        *,
        session_row: Any,
        question: str,
        history: list[Any],
    ) -> ContextBundle:
        return ContextBundle(warnings=(NO_KNOWLEDGE_WARNING,))


#: 名称 → 提供者实例。二期在这里注册 "knowledge" 即可，调用方不用改。
PROVIDERS: dict[str, ContextProvider] = {EmptyContextProvider.name: EmptyContextProvider()}


def get_provider(name: str) -> ContextProvider:
    provider = PROVIDERS.get(name)
    if provider is None:
        known = "、".join(sorted(PROVIDERS))
        raise BusinessRuleError(f"未知的上下文提供者 {name!r}（可用：{known}）")
    return provider


def render_context(bundle: ContextBundle) -> str:
    """把上下文渲染成 prompt 片段；空上下文返回空串（槽位仍在，只是内容为空）。"""
    if not bundle.text.strip():
        return ""
    return f"<{KNOWLEDGE_SLOT}>\n{bundle.text.strip()}\n</{KNOWLEDGE_SLOT}>"


__all__ = [
    "KNOWLEDGE_SLOT",
    "NO_KNOWLEDGE_WARNING",
    "PROVIDERS",
    "ContextBundle",
    "ContextHit",
    "ContextProvider",
    "EmptyContextProvider",
    "get_provider",
    "render_context",
]
