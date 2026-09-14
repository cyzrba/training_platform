"""AI 问答仓储：会话、消息、引用。

历史保留期是**按会话的活动时间**算的：一周内有活动的会话整体保留，`updated_at` 超期
的会话连同消息、引用一起清掉。所以这里所有"取会话"的方法都带 ``since`` 参数 ——
查询层先保证学生看不到过期数据，清理脚本（``scripts/prune_qa_history.py``）再负责回收空间。
"""

from collections.abc import Sequence
from datetime import datetime

from sqlmodel import delete, func, select

from app.crud.base import BaseRepository
from app.models.enums import QaRole
from app.models.knowledge import AiQaCitation, AiQaMessage, AiQaSession
from app.schemas.base import Page, PageParams


class AiQaSessionRepository(BaseRepository[AiQaSession]):
    """问答会话仓储。"""

    model = AiQaSession

    async def list_of_student(
        self,
        student_id: int,
        params: PageParams,
        *,
        since: datetime,
        status: str | None = None,
    ) -> Page[AiQaSession]:
        """学生的会话分页：只返回保留期内活动过的会话（走 idx_qa_session_student）。"""
        filters = [AiQaSession.student_id == student_id, AiQaSession.updated_at >= since]
        if status:
            filters.append(AiQaSession.status == status)
        return await self.list_page(params, *filters, order_by=AiQaSession.updated_at.desc())

    async def by_id_of_student(
        self, session_id: int, student_id: int, *, since: datetime | None = None
    ) -> AiQaSession | None:
        """按归属取会话；``since`` 给出时同时约束保留期（越权/过期对外一律 404）。"""
        stmt = self._statement().where(AiQaSession.id == session_id, AiQaSession.student_id == student_id)
        if since is not None:
            stmt = stmt.where(AiQaSession.updated_at >= since)
        return (await self.session.exec(stmt)).first()

    async def ids_of_student(self, student_id: int) -> list[int]:
        """该学生的全部会话 id（用量汇总用：先拿 id 再按会话做范围统计，避免跨表全表扫）。"""
        stmt = select(AiQaSession.id).where(AiQaSession.student_id == student_id)
        return [int(item) for item in (await self.session.exec(stmt)).all() if item is not None]

    async def touch(self, row: AiQaSession) -> AiQaSession:
        """刷新 updated_at，让会话冒泡到列表顶部（只有 update() 会刷这个字段）。"""
        return await self.update(row, {})

    async def stale_ids(self, cutoff: datetime) -> list[int]:
        """保留期外、且已无活动的会话 id（清理脚本用）。"""
        stmt = select(AiQaSession.id).where(AiQaSession.updated_at < cutoff)
        return [int(item) for item in (await self.session.exec(stmt)).all() if item is not None]

    async def delete_of_ids(self, session_ids: Sequence[int]) -> int:
        """批量物理删除会话（消息与引用必须先删，见清理脚本里的顺序说明）。"""
        ids = [int(item) for item in session_ids]
        if not ids:
            return 0
        result = await self.session.exec(
            delete(AiQaSession).where(AiQaSession.id.in_(ids))  # type: ignore[attr-defined]
        )
        await self.session.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


class AiQaMessageRepository(BaseRepository[AiQaMessage]):
    """问答消息仓储。"""

    model = AiQaMessage

    async def recent_turns(self, session_id: int, rounds: int) -> list[AiQaMessage]:
        """取最近 N 轮完整对话（1 轮 = 1 问 + 1 答），用于喂给大模型。

        两个刻意的约束：

        1. 只取 ``status=COMPLETED`` —— 失败回答不进上下文，否则模型会把
           "未配置大模型 api key" 当成对话内容；
        2. 对齐到 USER 开头 —— 按条数硬截会切在回答中间，历史以 ASSISTANT 开头会让
           模型把上一轮回答当成用户说的话（部分兼容端点还会直接报错）。
        """
        stmt = (
            select(AiQaMessage)
            .where(
                AiQaMessage.session_id == session_id,
                AiQaMessage.status == "COMPLETED",
            )
            .order_by(AiQaMessage.id.desc())  # type: ignore[attr-defined]
            .limit(rounds * 2)
        )
        rows = list((await self.session.exec(stmt)).all())
        rows.reverse()
        while rows and rows[0].role != QaRole.USER:
            rows.pop(0)
        return rows

    async def page_of_session(
        self, session_id: int, *, before_id: int | None, limit: int
    ) -> list[AiQaMessage]:
        """按 id 游标翻历史（倒序取一屏再翻回时间正序）。

        用游标而不是 offset：流式回答是按轮追加写入的，offset 翻页在写入过程中会漂移，
        出现重复或漏读。
        """
        stmt = select(AiQaMessage).where(AiQaMessage.session_id == session_id)
        if before_id is not None:
            stmt = stmt.where(AiQaMessage.id < before_id)  # type: ignore[attr-defined]
        stmt = stmt.order_by(AiQaMessage.id.desc()).limit(limit)  # type: ignore[attr-defined]
        rows = list((await self.session.exec(stmt)).all())
        rows.reverse()
        return rows

    async def count_of_session(self, session_id: int) -> int:
        stmt = select(func.count()).select_from(AiQaMessage).where(AiQaMessage.session_id == session_id)
        return int((await self.session.exec(stmt)).one())

    async def usage_of_sessions(self, session_ids: Sequence[int], *, since: datetime) -> tuple[int, int, int]:
        """汇总用量：返回 ``(提问次数, prompt_tokens, completion_tokens)``。

        只统计 ASSISTANT 消息 —— token 用量只有回答结束时才知道，USER 行不写这两个字段。
        先由调用方用 student_id 查出会话 id 再传进来，避免跨表 JOIN 触发全表扫。
        """
        ids = [int(item) for item in session_ids]
        if not ids:
            return 0, 0, 0
        questions = (
            select(func.count())
            .select_from(AiQaMessage)
            .where(
                AiQaMessage.session_id.in_(ids),  # type: ignore[attr-defined]
                AiQaMessage.role == QaRole.USER,
                AiQaMessage.created_at >= since,
            )
        )
        tokens = select(
            func.coalesce(func.sum(AiQaMessage.prompt_tokens), 0),
            func.coalesce(func.sum(AiQaMessage.completion_tokens), 0),
        ).where(
            AiQaMessage.session_id.in_(ids),  # type: ignore[attr-defined]
            AiQaMessage.role == QaRole.ASSISTANT,
            AiQaMessage.created_at >= since,
        )
        question_count = int((await self.session.exec(questions)).one())
        prompt_tokens, completion_tokens = (await self.session.exec(tokens)).one()
        return question_count, int(prompt_tokens or 0), int(completion_tokens or 0)

    async def delete_of_sessions(self, session_ids: Sequence[int]) -> int:
        """物理删除这些会话下的全部消息，返回删除条数。"""
        ids = [int(item) for item in session_ids]
        if not ids:
            return 0
        result = await self.session.exec(
            delete(AiQaMessage).where(AiQaMessage.session_id.in_(ids))  # type: ignore[attr-defined]
        )
        await self.session.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


class AiQaCitationRepository(BaseRepository[AiQaCitation]):
    """回答引用仓储。一期不写入（不检索就没有来源），二期接知识库后由检索结果落库。"""

    model = AiQaCitation

    async def list_of_messages(self, message_ids: Sequence[int]) -> list[AiQaCitation]:
        ids = [int(item) for item in message_ids]
        if not ids:
            return []
        stmt = select(AiQaCitation).where(
            AiQaCitation.message_id.in_(ids)  # type: ignore[attr-defined]
        )
        return list((await self.session.exec(stmt)).all())

    async def delete_of_sessions(self, session_ids: Sequence[int]) -> int:
        """先删引用再删消息：引用挂着 message_id 的外键，顺序反了会留下悬空引用。"""
        ids = [int(item) for item in session_ids]
        if not ids:
            return 0
        message_ids = select(AiQaMessage.id).where(
            AiQaMessage.session_id.in_(ids)  # type: ignore[attr-defined]
        )
        result = await self.session.exec(
            delete(AiQaCitation).where(
                AiQaCitation.message_id.in_(message_ids)  # type: ignore[attr-defined]
            )
        )
        await self.session.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


__all__ = [
    "AiQaCitationRepository",
    "AiQaMessageRepository",
    "AiQaSessionRepository",
]
