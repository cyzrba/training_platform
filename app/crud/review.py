"""评审仓储：整单评审记录与 AI 评审任务。"""

from typing import Any

from sqlmodel import func, select

from app.crud.base import BaseRepository
from app.models.review import ReviewAiJob, ReviewRecord
from app.schemas.base import Page, PageParams


class ReviewRecordRepository(BaseRepository[ReviewRecord]):
    """评审记录仓储（AI / 教师，可多版本）。"""

    model = ReviewRecord

    async def list_of_submission(self, submission_id: int) -> list[ReviewRecord]:
        stmt = (
            select(ReviewRecord)
            .where(ReviewRecord.submission_id == submission_id)
            .order_by(ReviewRecord.review_kind, ReviewRecord.version_no)
        )
        return list((await self.session.exec(stmt)).all())

    async def latest_final(self, submission_id: int, review_kind: str | None = None) -> ReviewRecord | None:
        stmt = select(ReviewRecord).where(
            ReviewRecord.submission_id == submission_id, ReviewRecord.status == "FINAL"
        )
        if review_kind:
            stmt = stmt.where(ReviewRecord.review_kind == review_kind)
        stmt = stmt.order_by(ReviewRecord.version_no.desc()).limit(1)
        return (await self.session.exec(stmt)).first()

    async def next_version_no(self, submission_id: int, review_kind: str) -> int:
        stmt = select(func.max(ReviewRecord.version_no)).where(
            ReviewRecord.submission_id == submission_id, ReviewRecord.review_kind == review_kind
        )
        return int((await self.session.exec(stmt)).one() or 0) + 1

    async def list_records(
        self,
        params: PageParams,
        *,
        submission_id: int | None = None,
        review_kind: str | None = None,
        status: str | None = None,
        reviewer_id: int | None = None,
    ) -> Page[Any]:
        filters: list[Any] = []
        if submission_id is not None:
            filters.append(ReviewRecord.submission_id == submission_id)
        if review_kind:
            filters.append(ReviewRecord.review_kind == review_kind)
        if status:
            filters.append(ReviewRecord.status == status)
        if reviewer_id is not None:
            filters.append(ReviewRecord.reviewer_id == reviewer_id)
        return await self.list_page(params, *filters)


class ReviewAiJobRepository(BaseRepository[ReviewAiJob]):
    """AI 评审任务仓储（真实调用是异步任务，这里只做记录与状态流转）。"""

    model = ReviewAiJob

    async def list_of_submission(self, submission_id: int) -> list[ReviewAiJob]:
        stmt = select(ReviewAiJob).where(ReviewAiJob.submission_id == submission_id).order_by(ReviewAiJob.id)
        return list((await self.session.exec(stmt)).all())

    async def latest_queued(self, submission_id: int) -> ReviewAiJob | None:
        stmt = (
            select(ReviewAiJob)
            .where(
                ReviewAiJob.submission_id == submission_id,
                ReviewAiJob.job_status.in_(["QUEUED", "PROCESSING"]),  # type: ignore[attr-defined]
            )
            .order_by(ReviewAiJob.id.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return (await self.session.exec(stmt)).first()


__all__ = ["ReviewAiJobRepository", "ReviewRecordRepository"]
