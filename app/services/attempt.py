"""闯关流程编排：开始闯关、保存作答、整单提交、撤回、评审定稿。

状态流转：
- student_project：NOT_STARTED → IN_PROGRESS → SUBMITTED → COMPLETED（可反复重来）
- training_attempt：IN_PROGRESS → SUBMITTED → COMPLETED / ABANDONED
- project_submission：PENDING_AI → AI_PASSED / AI_FAILED →（教师复审）REVIEWED / WITHDRAWN
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError
from app.core.time import now
from app.crud.attempt import (
    AttemptStageRepository,
    ProjectSubmissionRepository,
    StudentProjectRepository,
    TrainingAttemptRepository,
)
from app.crud.job_skill import GrowthRuleRepository
from app.crud.project import ProjectModuleRepository, TrainingProjectRepository
from app.crud.review import ReviewAiJobRepository, ReviewRecordRepository
from app.models.attempt import ProjectSubmission, StudentProject, TrainingAttempt
from app.models.review import ReviewRecord
from app.services.skill import sync_skills_after_project_completed

#: 及格线兜底值：项目层级没配成长规则时用这个
DEFAULT_PASS_SCORE = Decimal(60)

#: 判定口径：False = 达到及格线即通过（分数 >= 及格线）；True = 必须严格大于及格线
#: 这里的 PASS/FAIL 表示"这个项目是否通过"（不是"是否完成评审"），由程序按配置分数线判定
PASS_IS_STRICTLY_GREATER = False


def judge_conclusion(score: Decimal | None, pass_score: Decimal) -> str:
    """按配置的及格线判定项目是否通过（PASS / FAIL）。分数缺失时一律 FAIL（定稿前会先拦住）。"""
    if score is None:
        return "FAIL"
    if PASS_IS_STRICTLY_GREATER:
        return "PASS" if score > pass_score else "FAIL"
    return "PASS" if score >= pass_score else "FAIL"


async def pass_score_of_project(session: AsyncSession, project_id: int) -> Decimal:
    """取项目对应层级的及格线：project.project_level → growth_rule.level_type → pass_score。"""
    project = await TrainingProjectRepository(session).get(project_id)
    if project is None:
        return DEFAULT_PASS_SCORE
    rule = await GrowthRuleRepository(session).by_level_type(project.project_level)
    if rule is None or not rule.enabled:
        return DEFAULT_PASS_SCORE
    return rule.pass_score


async def resolve_conclusion(
    session: AsyncSession, *, submission_id: int, total_score: Decimal | None
) -> tuple[str, Decimal]:
    """给一次评审算结论：返回 (PASS/FAIL, 该项目层级的及格线)。"""
    submissions = ProjectSubmissionRepository(session)
    attempts = TrainingAttemptRepository(session)
    records = StudentProjectRepository(session)

    submission = await submissions.get(submission_id)
    if submission is None:
        raise BusinessRuleError(f"提交记录 {submission_id} 不存在")
    attempt = await attempts.get(submission.attempt_id)
    record = await records.get(attempt.student_project_id) if attempt else None
    if record is None:
        raise BusinessRuleError("提交对应的实训记录缺失，无法取及格线")
    pass_score = await pass_score_of_project(session, record.project_id)
    return judge_conclusion(total_score, pass_score), pass_score


def _percent(part: int, whole: int) -> Decimal:
    if whole <= 0:
        return Decimal(0)
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def start_attempt(
    session: AsyncSession, *, student_id: int, project_id: int
) -> tuple[StudentProject, TrainingAttempt]:
    """开始（或重新挑战）一个项目：建实训记录 → 新一轮闯关 → 预生成每个关卡的作答行。"""
    projects = TrainingProjectRepository(session)
    project = await projects.get(project_id)
    if project is None:
        raise BusinessRuleError(f"项目 {project_id} 不存在")
    if project.status != "PUBLISHED":
        raise BusinessRuleError(f"项目「{project.project_name}」还没发布，暂不能开始闯关")

    modules = ProjectModuleRepository(session)
    project_modules = await modules.list_of_project(project_id)
    if not project_modules:
        raise BusinessRuleError("项目还没有配置关卡，无法开始闯关")

    records = StudentProjectRepository(session)
    attempts = TrainingAttemptRepository(session)
    stages = AttemptStageRepository(session)

    record = await records.by_student_project(student_id, project_id)
    if record is None:
        record = await records.create(
            {
                "student_id": student_id,
                "project_id": project_id,
                "status": "IN_PROGRESS",
                "started_at": now(),
                "attempt_count": 0,
            }
        )

    attempt = await attempts.create(
        {
            "student_project_id": record.id,
            "attempt_no": await attempts.next_attempt_no(record.id),
            "status": "IN_PROGRESS",
        }
    )
    for module in project_modules:
        await stages.create({"attempt_id": attempt.id, "project_module_id": module.id})

    await records.update(
        record,
        {
            "status": "IN_PROGRESS",
            "attempt_count": record.attempt_count + 1,
            "started_at": record.started_at or now(),
        },
    )
    return record, attempt


async def save_stage_answer(
    session: AsyncSession,
    *,
    attempt_id: int,
    stage_id: int,
    answer_text: str | None,
    is_filled: bool | None,
) -> tuple[StudentProject, TrainingAttempt]:
    """保存一个模块的作答，并回写轮次/记录的进度。"""
    attempts = TrainingAttemptRepository(session)
    stages = AttemptStageRepository(session)
    records = StudentProjectRepository(session)

    attempt = await attempts.get(attempt_id)
    if attempt is None:
        raise BusinessRuleError(f"闯关轮次 {attempt_id} 不存在")
    if attempt.status != "IN_PROGRESS":
        raise ConflictError("本轮已提交或已完成，不能再修改作答")

    stage = await stages.get(stage_id)
    if stage is None or stage.attempt_id != attempt_id:
        raise BusinessRuleError(f"模块作答 {stage_id} 不属于本轮闯关")

    data: dict[str, object] = {}
    if answer_text is not None:
        data["answer_text"] = answer_text
    if is_filled is None:
        # 没显式传状态时，按"有没有写内容"判断
        data["is_filled"] = bool(answer_text and answer_text.strip())
    else:
        data["is_filled"] = is_filled
    data["filled_at"] = now() if data["is_filled"] else None
    await stages.update(stage, data)

    total = len(await stages.list_of_attempt(attempt_id))
    filled = await stages.count_filled(attempt_id)
    await attempts.update(attempt, {"filled_stage_count": filled})

    record = await records.get(attempt.student_project_id)
    if record is None:  # 外键保证不会发生
        raise BusinessRuleError("实训记录缺失")
    await records.update(record, {"progress": _percent(filled, total)})
    return record, attempt


async def save_attempt_answers(
    session: AsyncSession,
    *,
    attempt_id: int,
    answers: Sequence[dict[str, object]],
) -> tuple[StudentProject, TrainingAttempt, int, list[int]]:
    """一次保存本轮多个关卡的作答（"保存作答"按钮用的草稿保存）。

    与"关卡提交"的区别：默认**只写作答文本，不动 ``is_filled``**，
    所以保存过一半内容不会让关卡变成已完成、也不会推进关卡进度；
    显式传 ``is_filled`` 时才改填写状态（前端想让保存同时标记完成时用）。

    返回 ``(实训记录, 轮次, 本轮关卡总数, 本次保存的作答行 ID)``；本轮已提交/已完成时拒绝再写。
    """
    attempts = TrainingAttemptRepository(session)
    stages = AttemptStageRepository(session)
    records = StudentProjectRepository(session)

    attempt = await attempts.get(attempt_id)
    if attempt is None:
        raise BusinessRuleError(f"闯关轮次 {attempt_id} 不存在")
    if attempt.status != "IN_PROGRESS":
        raise ConflictError("本轮已提交或已完成，不能再修改作答")

    existing = {int(stage.id): stage for stage in await stages.list_of_attempt(attempt_id)}
    saved_ids: list[int] = []
    for item in answers:
        stage_id = int(item["attempt_stage_id"])  # type: ignore[arg-type]
        stage = existing.get(stage_id)
        if stage is None:
            raise BusinessRuleError(f"关卡作答 {stage_id} 不属于本轮闯关")

        data: dict[str, object] = {"answer_text": item.get("answer_text")}
        if item.get("is_filled") is not None:
            filled = bool(item["is_filled"])
            data["is_filled"] = filled
            data["filled_at"] = now() if filled else None
        await stages.update(stage, data)
        saved_ids.append(stage_id)

    # 作答行变了就重算一次进度（没动 is_filled 时结果不变，统一算更省心）
    filled_count = await stages.count_filled(attempt_id)
    total = len(existing)
    await attempts.update(attempt, {"filled_stage_count": filled_count})

    record = await records.get(attempt.student_project_id)
    if record is None:  # 外键保证不会发生
        raise BusinessRuleError("实训记录缺失")
    await records.update(record, {"progress": _percent(filled_count, total)})
    return record, attempt, total, saved_ids


async def submit_attempt(session: AsyncSession, *, attempt_id: int) -> ProjectSubmission:
    """整单提交：必填关卡全部填写完成才允许，提交后生成待 AI 评审的任务。"""
    attempts = TrainingAttemptRepository(session)
    stages = AttemptStageRepository(session)
    records = StudentProjectRepository(session)
    submissions = ProjectSubmissionRepository(session)
    modules = ProjectModuleRepository(session)
    ai_jobs = ReviewAiJobRepository(session)

    attempt = await attempts.get(attempt_id)
    if attempt is None:
        raise BusinessRuleError(f"闯关轮次 {attempt_id} 不存在")
    if attempt.status != "IN_PROGRESS":
        raise ConflictError("本轮已经提交过了")

    project_modules = await modules.list_of_project(
        (await records.get(attempt.student_project_id)).project_id  # type: ignore[union-attr]
    )
    required_ids = [module.id for module in project_modules if module.required]
    missing = await stages.unsaved_module_ids(attempt_id, required_ids)
    if missing:
        raise BusinessRuleError(f"还有 {len(missing)} 个必填关卡没有填写完成，不能提交")

    submission = await submissions.create(
        {
            "attempt_id": attempt_id,
            "submit_no": await submissions.next_submit_no(attempt_id),
            "status": "PENDING_AI",
            "submitted_at": now(),
        }
    )
    await attempts.update(attempt, {"status": "SUBMITTED", "submitted_at": now()})
    record = await records.get(attempt.student_project_id)
    if record is not None:
        await records.update(record, {"status": "SUBMITTED"})

    # 真实 AI 评审由异步任务消费，这里先建一条排队记录（评审接口可改状态）
    await ai_jobs.create({"submission_id": submission.id, "job_status": "QUEUED"})
    return submission


async def withdraw_submission(session: AsyncSession, *, submission_id: int) -> ProjectSubmission:
    """学生撤回整单提交，回到可编辑状态。"""
    submissions = ProjectSubmissionRepository(session)
    attempts = TrainingAttemptRepository(session)
    records = StudentProjectRepository(session)

    submission = await submissions.get(submission_id)
    if submission is None:
        raise BusinessRuleError(f"提交记录 {submission_id} 不存在")
    if submission.status in {"REVIEWED", "WITHDRAWN"}:
        raise ConflictError("已复审或已撤回的提交不能再次撤回")
    if submission.status in {"PENDING_REVIEW", "REVIEWING"}:
        raise ConflictError("已进入教师复核流程，不能再撤回；如需修改请联系任课教师")

    await submissions.update(submission, {"status": "WITHDRAWN", "withdrawn_at": now()})
    attempt = await attempts.get(submission.attempt_id)
    if attempt is not None:
        await attempts.update(attempt, {"status": "IN_PROGRESS", "submitted_at": None})
        record = await records.get(attempt.student_project_id)
        if record is not None:
            await records.update(record, {"status": "IN_PROGRESS"})
    return submission


async def raise_objection(session: AsyncSession, *, submission_id: int, reason: str) -> ProjectSubmission:
    """学生对 AI 评审结果提出异议：写留言并转入教师复核待办。

    AI 已经定稿（AI_PASSED / AI_FAILED）才能提异议；已经在复核中的可以补充/修改留言。
    """
    submissions = ProjectSubmissionRepository(session)
    submission = await submissions.get(submission_id)
    if submission is None:
        raise BusinessRuleError(f"提交记录 {submission_id} 不存在")
    if submission.status == "PENDING_AI":
        raise ConflictError("AI 评审还没出结果，暂时不能提异议")
    if submission.status in {"REVIEWED", "WITHDRAWN"}:
        raise ConflictError("该提交已经复审结束或已撤回，不能再提异议")

    await submissions.update(submission, {"status": "PENDING_REVIEW", "objection_reason": reason})
    return await submissions.get(submission_id)


async def claim_for_review(session: AsyncSession, *, submission_id: int) -> ProjectSubmission:
    """教师认领复核，状态置 REVIEWING，避免多人同时复审。"""
    submissions = ProjectSubmissionRepository(session)
    submission = await submissions.get(submission_id)
    if submission is None:
        raise BusinessRuleError(f"提交记录 {submission_id} 不存在")
    if submission.status in {"REVIEWED", "WITHDRAWN"}:
        raise ConflictError("该提交已经复审结束或已撤回")
    if submission.status == "PENDING_AI":
        raise ConflictError("AI 评审还没出结果，暂时不能复核")
    await submissions.update(submission, {"status": "REVIEWING"})
    return await submissions.get(submission_id)


async def finalize_review(
    session: AsyncSession, *, review: ReviewRecord
) -> tuple[ProjectSubmission, StudentProject | None]:
    """评审定稿：**按配置及格线重新判定 PASS/FAIL**，回写状态与分数；
    通过则项目完成并重算该项目关联的技能进度，改判不通过则撤销完成并回滚技能进度。"""
    if review.status != "FINAL":
        return (await ProjectSubmissionRepository(session).get(review.submission_id), None)  # type: ignore[return-value]

    submissions = ProjectSubmissionRepository(session)
    attempts = TrainingAttemptRepository(session)
    records = StudentProjectRepository(session)
    reviews = ReviewRecordRepository(session)

    submission = await submissions.get(review.submission_id)
    if submission is None:
        raise BusinessRuleError(f"提交记录 {review.submission_id} 不存在")
    if review.total_score is None:
        raise BusinessRuleError("定稿必须给出分数，才能按配置及格线判定是否通过")

    is_ai = review.review_kind == "AI"
    # 结论一律由后端按配置判定，评审人只给分数与评语
    conclusion, pass_score = await resolve_conclusion(
        session, submission_id=review.submission_id, total_score=review.total_score
    )
    if review.conclusion != conclusion:
        review = await reviews.update(review, {"conclusion": conclusion})

    if conclusion == "PASS":
        status = "AI_PASSED" if is_ai else "REVIEWED"
    else:
        status = "AI_FAILED" if is_ai else "REVIEWED"

    await submissions.update(
        submission,
        {
            "status": status,
            "final_conclusion": conclusion,
            "total_score": review.total_score,
            "reviewed_at": now(),
        },
    )

    attempt = await attempts.get(submission.attempt_id)
    record = await records.get(attempt.student_project_id) if attempt else None
    if attempt is None or record is None:
        return submission, None

    score = review.total_score
    was_completed = record.completed_at is not None
    if conclusion == "PASS":
        best = record.best_score if record.best_score is not None else Decimal(0)
        await records.update(
            record,
            {
                "status": "COMPLETED",
                "completed_at": now(),
                "completed_score": score,
                "total_score": score,
                "best_score": max(best, score) if score is not None else record.best_score,
                "progress": Decimal(100),
            },
        )
        await attempts.update(attempt, {"status": "COMPLETED", "finished_at": now(), "total_score": score})
        # 项目完成 → 该项目关联的技能点进度重算
        await sync_skills_after_project_completed(session, record.student_id, record.project_id)
    # 不通过：分两种情况，差别很大
    elif await _has_passing_review(session, student_id=record.student_id, project_id=record.project_id):
        # ① 此前有过通过的轮次 → **项目保持完成，技能进度与成绩快照都不动**。
        #    技能点亮表示"曾经达成过这个能力"；重复挑战是给学生一次刷高分的机会，
        #    失败了不该把已有成果抹掉，否则学生根本不敢重新挑战。
        #    这次不及格只体现在本轮 submission / attempt 上（学生看得到自己这轮多少分）。
        # completed_at 缺失时补回来，维持 "status=COMPLETED ⇒ completed_at 非空" 这个不变量
        # （技能进度的分子按 completed_at 统计，两者不能脱节）
        repaired = {"completed_at": now()} if record.completed_at is None else {}
        await records.update(record, {"status": "COMPLETED", **repaired})
        await attempts.update(attempt, {"status": "IN_PROGRESS", "total_score": score})
    else:
        # ② 一次都没通过（或唯一那条通过评审被教师改判了）→ 撤销完成并回滚技能进度
        stages = AttemptStageRepository(session)
        total_stages = len(await stages.list_of_attempt(attempt.id))
        rollback = {
            "status": "IN_PROGRESS",
            "total_score": score,
            "completed_at": None,
            "completed_score": None,
            "progress": _percent(attempt.filled_stage_count, total_stages),
        }
        await records.update(
            record,
            rollback if was_completed else {"status": "IN_PROGRESS", "total_score": score},
        )
        await attempts.update(attempt, {"status": "IN_PROGRESS", "total_score": score})
        if was_completed:
            # 项目不再是 COMPLETED，重算后进度会回落
            await sync_skills_after_project_completed(session, record.student_id, record.project_id)
    return submission, record


async def _has_passing_review(session: AsyncSession, *, student_id: int, project_id: int) -> bool:
    """这个学生在该项目下是否还有仍然有效的"通过"结论（跨所有闯关轮次）。

    判定口径：**每份提交只看它最新的一条定稿评审**，再看有没有哪份提交的结论是通过。

    - 重复挑战不通过 → 那是**新的一份提交**，旧提交上那条 PASS 依然有效 → 保持完成、技能不掉；
    - 教师在同一份提交上改判不通过 → 该提交的最新结论变成 FAIL，通过结论被覆盖
      → 如果所有提交都没有通过结论了，才撤销完成、回滚技能。

    这两件事性质不同：前者是"新一次没做好"，后者是"原来那个判定不成立"。
    只用"有没有出现过 PASS"判断会把教师改判漏掉；只用"最近一次"判断又会把重复挑战失败误判成回滚。
    """
    from sqlmodel import func, select

    # 每份提交的最新一条定稿评审（教师复审/改判会产出更新的记录，从而覆盖 AI 的结论）
    latest_final = (
        select(ReviewRecord.submission_id, func.max(ReviewRecord.id).label("latest_id"))
        .where(ReviewRecord.status == "FINAL")
        .group_by(ReviewRecord.submission_id)  # type: ignore[arg-type]
        .subquery()
    )

    stmt = (
        select(func.count())
        .select_from(ReviewRecord)
        .join(latest_final, latest_final.c.latest_id == ReviewRecord.id)  # type: ignore[arg-type]
        .join(ProjectSubmission, ProjectSubmission.id == ReviewRecord.submission_id)  # type: ignore[arg-type]
        .join(TrainingAttempt, TrainingAttempt.id == ProjectSubmission.attempt_id)  # type: ignore[arg-type]
        .join(StudentProject, StudentProject.id == TrainingAttempt.student_project_id)  # type: ignore[arg-type]
        .where(
            StudentProject.student_id == student_id,
            StudentProject.project_id == project_id,
            ReviewRecord.conclusion == "PASS",
        )
    )
    return int((await session.exec(stmt)).one()) > 0


__all__ = [
    "DEFAULT_PASS_SCORE",
    "PASS_IS_STRICTLY_GREATER",
    "claim_for_review",
    "finalize_review",
    "judge_conclusion",
    "pass_score_of_project",
    "raise_objection",
    "resolve_conclusion",
    "save_attempt_answers",
    "save_stage_answer",
    "start_attempt",
    "submit_attempt",
    "withdraw_submission",
]
