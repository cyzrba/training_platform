"""关键操作审计留痕（append-only）。

约定见 docs/通知与审计实施方案.md §4：审计只追加、不改不删；写失败不应该把业务也带崩，
所以调用方按事件分级决定是否吞掉异常。这里只负责"写一条"。
"""

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.notification import OperationLog


async def record(
    session: AsyncSession,
    *,
    module: str,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: dict[str, Any] | None = None,
    operator_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> OperationLog:
    """写一条操作日志（与业务同事务，只 flush 不 commit）。"""
    log = OperationLog(
        operator_id=operator_id,
        module=module,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail_json=detail or {},
        ip=ip,
        user_agent=user_agent,
    )
    session.add(log)
    await session.flush()
    return log


__all__ = ["record"]
