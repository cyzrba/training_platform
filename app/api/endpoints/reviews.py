"""评审接口：整单评审记录（AI / 教师）的增删改查与定稿。

**定稿是技能进度的触发点**：当一条评审被置为 FINAL 且结论为 PASS 时，
提交记录标记为通过、学生实训记录置为 COMPLETED，并重算该项目关联的技能点进度。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, PageDep
from app.core.exceptions import ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.core.time import now
from app.crud.attempt import ProjectSubmissionRepository
from app.crud.review import ReviewRecordRepository
from app.models.review import ReviewRecord
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.review import (
    ReviewRecordRead,
    ReviewRecordUpdate,
    SubmissionReviewIn,
)
from app.services.attempt import finalize_review, resolve_conclusion

router = APIRouter(route_class=EnvelopeRoute, tags=["闯关评审"])


# --------------------------------------------------------------------- 依赖


def review_record_repo(db: DbSession) -> ReviewRecordRepository:
    return ReviewRecordRepository(db)


def submission_repo(db: DbSession) -> ProjectSubmissionRepository:
    return ProjectSubmissionRepository(db)


ReviewRepo = Annotated[ReviewRecordRepository, Depends(review_record_repo)]
SubmissionRepo = Annotated[ProjectSubmissionRepository, Depends(submission_repo)]


async def _review_or_404(reviews: ReviewRecordRepository, review_id: int) -> ReviewRecord:
    review = await reviews.get(review_id)
    if review is None:
        raise NotFoundError(f"评审记录 {review_id} 不存在")
    return review


async def _submission_or_404(submissions: ProjectSubmissionRepository, submission_id: int) -> object:
    submission = await submissions.get(submission_id)
    if submission is None:
        raise NotFoundError(f"提交记录 {submission_id} 不存在")
    return submission


def _review_data(payload: SubmissionReviewIn | ReviewRecordUpdate, *, exclude_unset: bool) -> dict:
    """评审入参转成可落库的 dict：维度明细按 JSON 模式序列化（得分是 Decimal，不能直接进 JSON 列）。"""
    data = payload.model_dump(exclude_unset=exclude_unset)
    dimensions = getattr(payload, "dimension_json", None)
    if dimensions is not None:
        data["dimension_json"] = [item.model_dump(mode="json") for item in dimensions]
    return data


# ----------------------------------------------------------------- 评审记录


@router.get(
    "/reviews",
    response_model=ApiResponse[Page[ReviewRecordRead]],
    summary="评审记录分页列表",
)
async def list_reviews(
    reviews: ReviewRepo,
    page: PageDep,
    submission_id: Annotated[int | None, Query(description="按提交过滤")] = None,
    review_kind: Annotated[str | None, Query(description="AI / TEACHER")] = None,
    status_: Annotated[str | None, Query(alias="status", description="DRAFT / FINAL")] = None,
    reviewer_id: Annotated[int | None, Query(description="评审教师 ID")] = None,
) -> Page[object]:
    return await reviews.list_records(
        page,
        submission_id=submission_id,
        review_kind=review_kind,
        status=status_,
        reviewer_id=reviewer_id,
    )


@router.get(
    "/submissions/{submission_id}/reviews",
    response_model=ApiResponse[list[ReviewRecordRead]],
    summary="某次提交的全部评审记录",
)
async def list_submission_reviews(
    submission_id: int, submissions: SubmissionRepo, reviews: ReviewRepo
) -> list[ReviewRecord]:
    await _submission_or_404(submissions, submission_id)
    return await reviews.list_of_submission(submission_id)


@router.post(
    "/submissions/{submission_id}/reviews",
    response_model=ApiResponse[ReviewRecordRead],
    status_code=status.HTTP_201_CREATED,
    summary="新增评审（AI 或教师）；status=FINAL 即定稿并结算",
)
async def create_review(
    submission_id: int,
    payload: SubmissionReviewIn,
    db: DbSession,
    submissions: SubmissionRepo,
    reviews: ReviewRepo,
) -> ReviewRecord:
    submission = await _submission_or_404(submissions, submission_id)
    if submission.status == "WITHDRAWN":  # type: ignore[attr-defined]
        raise ConflictError("该提交已被学生撤回，不能再评审")
    if payload.status == "FINAL" and payload.total_score is None:
        raise ConflictError("定稿必须给出分数，系统按项目层级的及格线判定是否通过")

    data = _review_data(payload, exclude_unset=False)
    data["submission_id"] = submission_id
    data["version_no"] = await reviews.next_version_no(submission_id, payload.review_kind)
    data["raw_json"] = data.get("raw_json") or {}
    # 结论不由评审人给：按项目层级的及格线算
    conclusion, _pass_score = await resolve_conclusion(
        db, submission_id=submission_id, total_score=payload.total_score
    )
    data["conclusion"] = conclusion
    if payload.status == "FINAL":
        data["finished_at"] = now()
    review = await reviews.create(data)
    if review.status == "FINAL":
        await finalize_review(db, review=review)
    return review


@router.get("/reviews/{review_id}", response_model=ApiResponse[ReviewRecordRead], summary="评审记录详情")
async def get_review(review_id: int, reviews: ReviewRepo) -> ReviewRecord:
    return await _review_or_404(reviews, review_id)


@router.patch(
    "/reviews/{review_id}",
    response_model=ApiResponse[ReviewRecordRead],
    summary="更新评审；改成 FINAL 即定稿并结算（含项目完成与技能进度）",
)
async def update_review(
    review_id: int, payload: ReviewRecordUpdate, db: DbSession, reviews: ReviewRepo
) -> ReviewRecord:
    review = await _review_or_404(reviews, review_id)
    if review.status == "FINAL" and payload.status != "FINAL" and payload.status is not None:
        raise ConflictError("已定稿的评审不能再改回草稿")

    data = _review_data(payload, exclude_unset=True)
    score = data.get("total_score", review.total_score)
    if data.get("status") == "FINAL" and score is None:
        raise ConflictError("定稿必须给出分数，系统按项目层级的及格线判定是否通过")
    conclusion, _pass_score = await resolve_conclusion(
        db, submission_id=review.submission_id, total_score=score
    )
    data["conclusion"] = conclusion

    updated = await reviews.update(review, data)
    if updated.status == "FINAL":
        if updated.finished_at is None:
            updated = await reviews.update(updated, {"finished_at": now()})
        await finalize_review(db, review=updated)
    return updated


@router.delete(
    "/reviews/{review_id}",
    response_model=ApiResponse[MessageOut],
    summary="删除评审记录（已定稿的不允许删除）",
)
async def delete_review(review_id: int, reviews: ReviewRepo) -> MessageOut:
    review = await _review_or_404(reviews, review_id)
    if review.status == "FINAL":
        raise ConflictError("已定稿的评审不能删除")
    await reviews.remove(review)
    return MessageOut(message="评审记录已删除")


__all__ = ["router"]
