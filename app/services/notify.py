"""站内通知写入（薄封装）。

约定见 docs/通知与审计实施方案.md §3.1：
- 同事务：只 flush 不 commit，事务边界统一由 get_db 收口 —— 业务成功通知一定在，业务回滚通知一起没；
- 幂等：默认按 (recipient_user_id, biz_type, biz_id) 去重，重复触发不刷屏；
- 文案在调用点给：title 是给人看的短句，content 放细节，biz_type + biz_id 让前端能跳详情。
"""

from collections.abc import Sequence

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.notification import Notification


async def notify(
    session: AsyncSession,
    *,
    recipient_user_id: int,
    title: str,
    content: str | None = None,
    biz_type: str,
    biz_id: int | None = None,
    dedupe: bool = True,
) -> Notification | None:
    """写一条站内通知；dedupe=True 时同一 (接收人, biz_type, biz_id) 已存在则跳过。"""
    if dedupe and biz_id is not None:
        exists = await session.exec(
            select(Notification.id).where(
                Notification.recipient_user_id == recipient_user_id,
                Notification.biz_type == biz_type,
                Notification.biz_id == biz_id,
            )
        )
        if exists.first() is not None:
            return None

    notification = Notification(
        recipient_user_id=recipient_user_id,
        title=title,
        content=content,
        biz_type=biz_type,
        biz_id=biz_id,
    )
    session.add(notification)
    await session.flush()
    return notification


async def notify_many(
    session: AsyncSession,
    *,
    recipient_user_ids: Sequence[int],
    title: str,
    content: str | None = None,
    biz_type: str,
    biz_id: int | None = None,
    dedupe: bool = True,
) -> int:
    """批量写（任务下发面向一个班，几十人一次插入）；返回真正落库的条数。"""
    written = 0
    for user_id in dict.fromkeys(recipient_user_ids):  # 去重且保持顺序
        created = await notify(
            session,
            recipient_user_id=int(user_id),
            title=title,
            content=content,
            biz_type=biz_type,
            biz_id=biz_id,
            dedupe=dedupe,
        )
        if created is not None:
            written += 1
    return written


__all__ = ["notify", "notify_many"]
