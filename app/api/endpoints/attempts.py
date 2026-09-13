"""闯关过程接口：学生实训记录、闯关轮次、模块作答、整单提交、AI 评审任务。

学生流程：开始闯关（自动建记录 + 轮次 + 各关卡作答行）→ 逐关填写 → 整单提交 →
（AI 任务排队）→ 等待评审；教师侧在 /api/reviews 里评审定稿。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, PageDep
from app.core.exceptions import ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.crud.account import UserRepository
from app.crud.attempt import (
    AttemptStageFileRepository,
    AttemptStageRepository,
    FileAssetRepository,
    ProjectSubmissionRepository,
    StudentProjectRepository,
    TrainingAttemptRepository,
)
from app.crud.project import ProjectModuleRepository, StageTemplateRepository, TrainingProjectRepository
from app.crud.review import ReviewAiJobRepository, ReviewRecordRepository
from app.models.attempt import FileAsset, ProjectSubmission, StudentProject
from app.models.review import ReviewAiJob
from app.schemas.attempt import (
    AttemptDetail,
    AttemptStageDetail,
    AttemptStageSaveIn,
    FileAssetCreate,
    FileAssetRead,
    FileAssetUpdate,
    ProjectSubmissionDetail,
    ProjectSubmissionListRead,
    ProjectSubmissionRead,
    ProjectSubmissionUpdate,
    StudentProjectDetail,
    StudentProjectRead,
    StudentProjectUpdate,
    SubmissionObjectionIn,
)
from app.schemas.base import ApiResponse, MessageOut, Page, PageParams
from app.schemas.review import ReviewAiJobCreate, ReviewAiJobRead, ReviewAiJobUpdate
from app.services.attempt import (
    claim_for_review,
    raise_objection,
    save_stage_answer,
    start_attempt,
    submit_attempt,
    withdraw_submission,
)

router = APIRouter(route_class=EnvelopeRoute, tags=["闯关评审"])


# --------------------------------------------------------------------- 依赖


def student_project_repo(db: DbSession) -> StudentProjectRepository:
    return StudentProjectRepository(db)


def attempt_repo(db: DbSession) -> TrainingAttemptRepository:
    return TrainingAttemptRepository(db)


def attempt_stage_repo(db: DbSession) -> AttemptStageRepository:
    return AttemptStageRepository(db)


def stage_file_repo(db: DbSession) -> AttemptStageFileRepository:
    return AttemptStageFileRepository(db)


def submission_repo(db: DbSession) -> ProjectSubmissionRepository:
    return ProjectSubmissionRepository(db)


def file_asset_repo(db: DbSession) -> FileAssetRepository:
    return FileAssetRepository(db)


def ai_job_repo(db: DbSession) -> ReviewAiJobRepository:
    return ReviewAiJobRepository(db)


def review_repo(db: DbSession) -> ReviewRecordRepository:
    return ReviewRecordRepository(db)


def training_project_repo(db: DbSession) -> TrainingProjectRepository:
    return TrainingProjectRepository(db)


def project_module_repo(db: DbSession) -> ProjectModuleRepository:
    return ProjectModuleRepository(db)


def stage_template_repo(db: DbSession) -> StageTemplateRepository:
    return StageTemplateRepository(db)


def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


StudentProjectRepo = Annotated[StudentProjectRepository, Depends(student_project_repo)]
AttemptRepo = Annotated[TrainingAttemptRepository, Depends(attempt_repo)]
AttemptStageRepo = Annotated[AttemptStageRepository, Depends(attempt_stage_repo)]
StageFileRepo = Annotated[AttemptStageFileRepository, Depends(stage_file_repo)]
SubmissionRepo = Annotated[ProjectSubmissionRepository, Depends(submission_repo)]
FileAssetRepo = Annotated[FileAssetRepository, Depends(file_asset_repo)]
AiJobRepo = Annotated[ReviewAiJobRepository, Depends(ai_job_repo)]
ReviewRepo = Annotated[ReviewRecordRepository, Depends(review_repo)]
TrainingProjectRepo = Annotated[TrainingProjectRepository, Depends(training_project_repo)]
ProjectModuleRepo = Annotated[ProjectModuleRepository, Depends(project_module_repo)]
StageTemplateRepo = Annotated[StageTemplateRepository, Depends(stage_template_repo)]
UserRepo = Annotated[UserRepository, Depends(user_repo)]


# --------------------------------------------------------------------- 工具


async def _record_or_404(records: StudentProjectRepository, record_id: int) -> StudentProject:
    record = await records.get(record_id)
    if record is None:
        raise NotFoundError(f"学生实训记录 {record_id} 不存在")
    return record


async def _attempt_or_404(attempts: TrainingAttemptRepository, attempt_id: int) -> Any:
    attempt = await attempts.get(attempt_id)
    if attempt is None:
        raise NotFoundError(f"闯关轮次 {attempt_id} 不存在")
    return attempt


async def _submission_or_404(
    submissions: ProjectSubmissionRepository, submission_id: int
) -> ProjectSubmission:
    submission = await submissions.get(submission_id)
    if submission is None:
        raise NotFoundError(f"提交记录 {submission_id} 不存在")
    return submission


async def _attempt_detail(
    attempt: Any,
    *,
    records: StudentProjectRepository,
    stages: AttemptStageRepository,
    stage_files: AttemptStageFileRepository,
    modules: ProjectModuleRepository,
    templates: StageTemplateRepository,
    projects: TrainingProjectRepository,
) -> dict:
    record = await records.get(attempt.student_project_id)
    if record is None:
        raise NotFoundError("实训记录缺失")
    project = await projects.get(record.project_id)

    project_modules = {module.id: module for module in await modules.list_of_project(record.project_id)}
    template_map = {
        template.id: template
        for template in await templates.list_by_ids(
            [module.template_id for module in project_modules.values()], include_deleted=True
        )
    }

    items: list[dict] = []
    for stage in await stages.list_of_attempt(attempt.id):
        module = project_modules.get(stage.project_module_id)
        template = template_map.get(module.template_id) if module else None
        items.append(
            {
                **stage.model_dump(),
                "stage_no": module.stage_no if module else 0,
                "stage_key": template.stage_key if template else None,
                "stage_name": template.stage_name if template else None,
                "required": module.required if module else True,
                "weight": module.weight if module else 0,
                "items_json": module.items_json if module else [],
                "file_count": len(await stage_files.list_of_stage(stage.id)),
            }
        )
    items.sort(key=lambda item: item["stage_no"])
    return {
        **attempt.model_dump(),
        "student_id": record.student_id,
        "project_id": record.project_id,
        "project_name": project.project_name if project else None,
        "stages": items,
    }


# --------------------------------------------------------------- 学生实训记录


@router.post(
    "/students/{student_id}/projects/{project_id}/start",
    response_model=ApiResponse[AttemptDetail],
    status_code=status.HTTP_201_CREATED,
    summary="开始闯关（重新挑战会新建一轮）",
)
async def start_student_project(
    student_id: int,
    project_id: int,
    db: DbSession,
    students: UserRepo,
    records: StudentProjectRepo,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
    projects: TrainingProjectRepo,
) -> dict:
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    _, attempt = await start_attempt(db, student_id=student_id, project_id=project_id)
    return await _attempt_detail(
        attempt,
        records=records,
        stages=stages,
        stage_files=stage_files,
        modules=modules,
        templates=templates,
        projects=projects,
    )


@router.get(
    "/student-projects",
    response_model=ApiResponse[Page[StudentProjectDetail]],
    summary="学生实训记录分页列表",
)
async def list_student_projects(
    records: StudentProjectRepo,
    page: PageDep,
    student_id: Annotated[int | None, Query(description="按学生过滤")] = None,
    project_id: Annotated[int | None, Query(description="按项目过滤")] = None,
    status_: Annotated[
        str | None,
        Query(alias="status", description="NOT_STARTED / IN_PROGRESS / SUBMITTED / COMPLETED"),
    ] = None,
    keyword: Annotated[str | None, Query(description="项目名模糊搜索")] = None,
) -> Page[object]:
    return await records.list_records(
        page, student_id=student_id, project_id=project_id, status=status_, keyword=keyword
    )


@router.get(
    "/students/{student_id}/projects",
    response_model=ApiResponse[list[StudentProjectDetail]],
    summary="某个学生的全部项目记录",
)
async def list_projects_of_student(
    student_id: int, students: UserRepo, records: StudentProjectRepo, attempts: AttemptRepo
) -> list[dict]:
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    page = await records.list_records(PageParams(page=1, page_size=200), student_id=student_id)
    items = []
    for record in page.items:
        record_attempts = await attempts.list_of_record(record["id"])
        items.append({**record, "attempts": record_attempts})
    return items


@router.get(
    "/student-projects/{record_id}",
    response_model=ApiResponse[StudentProjectDetail],
    summary="实训记录详情（含各轮闯关）",
)
async def get_student_project(
    record_id: int,
    records: StudentProjectRepo,
    attempts: AttemptRepo,
    projects: TrainingProjectRepo,
) -> dict:
    record = await _record_or_404(records, record_id)
    project = await projects.get(record.project_id)
    return {
        **record.model_dump(),
        "project_name": project.project_name if project else None,
        "project_level": project.project_level if project else None,
        "attempts": await attempts.list_of_record(record_id),
    }


@router.patch(
    "/student-projects/{record_id}",
    response_model=ApiResponse[StudentProjectRead],
    summary="更新实训记录（教师修正进度/成绩）",
)
async def update_student_project(
    record_id: int, payload: StudentProjectUpdate, records: StudentProjectRepo
) -> StudentProject:
    record = await _record_or_404(records, record_id)
    return await records.update(record, payload.model_dump(exclude_unset=True))


@router.delete(
    "/student-projects/{record_id}",
    response_model=ApiResponse[MessageOut],
    summary="删除实训记录（连带轮次、作答与提交）",
)
async def delete_student_project(
    record_id: int,
    records: StudentProjectRepo,
    attempts: AttemptRepo,
    stages: AttemptStageRepo,
    submissions: SubmissionRepo,
) -> MessageOut:
    record = await _record_or_404(records, record_id)
    for attempt in await attempts.list_of_record(record_id):
        for submission in await submissions.list_of_attempt(attempt.id):
            await submissions.remove(submission)
        for stage in await stages.list_of_attempt(attempt.id):
            await stages.remove(stage)
        await attempts.remove(attempt)
    await records.remove(record)
    return MessageOut(message="实训记录已删除")


# ----------------------------------------------------------------- 闯关轮次


@router.get(
    "/attempts/{attempt_id}",
    response_model=ApiResponse[AttemptDetail],
    summary="一轮闯关详情（含各关卡作答与引导子标题）",
)
async def get_attempt(
    attempt_id: int,
    attempts: AttemptRepo,
    records: StudentProjectRepo,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
    projects: TrainingProjectRepo,
) -> dict:
    attempt = await _attempt_or_404(attempts, attempt_id)
    return await _attempt_detail(
        attempt,
        records=records,
        stages=stages,
        stage_files=stage_files,
        modules=modules,
        templates=templates,
        projects=projects,
    )


@router.get(
    "/student-projects/{record_id}/attempts",
    response_model=ApiResponse[list[AttemptDetail]],
    summary="某个实训记录的全部闯关轮次",
)
async def list_attempts_of_record(
    record_id: int,
    records: StudentProjectRepo,
    attempts: AttemptRepo,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
    projects: TrainingProjectRepo,
) -> list[dict]:
    await _record_or_404(records, record_id)
    return [
        await _attempt_detail(
            attempt,
            records=records,
            stages=stages,
            stage_files=stage_files,
            modules=modules,
            templates=templates,
            projects=projects,
        )
        for attempt in await attempts.list_of_record(record_id)
    ]


@router.patch(
    "/attempts/{attempt_id}/stages/{stage_id}",
    response_model=ApiResponse[AttemptStageDetail],
    summary="保存某个关卡的作答",
)
async def save_stage(
    attempt_id: int,
    stage_id: int,
    payload: AttemptStageSaveIn,
    db: DbSession,
    attempts: AttemptRepo,
    records: StudentProjectRepo,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
    projects: TrainingProjectRepo,
) -> dict:
    await save_stage_answer(
        db,
        attempt_id=attempt_id,
        stage_id=stage_id,
        answer_text=payload.answer_text,
        is_filled=payload.is_filled,
    )
    detail = await _attempt_detail(
        await _attempt_or_404(attempts, attempt_id),
        records=records,
        stages=stages,
        stage_files=stage_files,
        modules=modules,
        templates=templates,
        projects=projects,
    )
    return next(stage for stage in detail["stages"] if stage["id"] == stage_id)


@router.post(
    "/attempts/{attempt_id}/submit",
    response_model=ApiResponse[ProjectSubmissionRead],
    status_code=status.HTTP_201_CREATED,
    summary="整单提交（必填关卡全部填写完成才允许）",
)
async def submit(db: DbSession, attempt_id: int, attempts: AttemptRepo) -> ProjectSubmission:
    await _attempt_or_404(attempts, attempt_id)
    return await submit_attempt(db, attempt_id=attempt_id)


# ------------------------------------------------------------- 作答附件


@router.get(
    "/attempts/{attempt_id}/stages/{stage_id}/files",
    response_model=ApiResponse[list[FileAssetRead]],
    summary="关卡作答的附件列表",
)
async def list_stage_files(
    attempt_id: int,
    stage_id: int,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
    assets: FileAssetRepo,
) -> list[FileAsset]:
    stage = await stages.get(stage_id)
    if stage is None or stage.attempt_id != attempt_id:
        raise NotFoundError(f"模块作答 {stage_id} 不属于本轮闯关")
    files: list[FileAsset] = []
    for link in await stage_files.list_of_stage(stage_id):
        asset = await assets.get(link.file_asset_id)
        if asset is not None:
            files.append(asset)
    return files


@router.post(
    "/attempts/{attempt_id}/stages/{stage_id}/files/{file_asset_id}",
    response_model=ApiResponse[MessageOut],
    status_code=status.HTTP_201_CREATED,
    summary="给关卡作答挂一个已上传的文件",
)
async def attach_stage_file(
    attempt_id: int,
    stage_id: int,
    file_asset_id: int,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
    assets: FileAssetRepo,
) -> MessageOut:
    stage = await stages.get(stage_id)
    if stage is None or stage.attempt_id != attempt_id:
        raise NotFoundError(f"模块作答 {stage_id} 不属于本轮闯关")
    if await assets.get(file_asset_id) is None:
        raise NotFoundError(f"文件 {file_asset_id} 不存在")
    if await stage_files.link_exists(stage_id, file_asset_id):
        raise ConflictError("该文件已挂在这个关卡上")
    await stage_files.create({"attempt_stage_id": stage_id, "file_asset_id": file_asset_id})
    return MessageOut(message="附件已添加")


@router.delete(
    "/attempts/{attempt_id}/stages/{stage_id}/files/{file_asset_id}",
    response_model=ApiResponse[MessageOut],
    summary="解除关卡作答的附件",
)
async def detach_stage_file(
    attempt_id: int,
    stage_id: int,
    file_asset_id: int,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
) -> MessageOut:
    stage = await stages.get(stage_id)
    if stage is None or stage.attempt_id != attempt_id:
        raise NotFoundError(f"模块作答 {stage_id} 不属于本轮闯关")
    link = await stage_files.get_by(attempt_stage_id=stage_id, file_asset_id=file_asset_id)
    if link is None:
        raise NotFoundError("该附件不存在")
    await stage_files.remove(link)
    return MessageOut(message="附件已移除")


# ----------------------------------------------------------------- 文件台账


@router.get("/file-assets", response_model=ApiResponse[Page[FileAssetRead]], summary="文件台账分页列表")
async def list_file_assets(
    assets: FileAssetRepo,
    page: PageDep,
    biz_type: Annotated[
        str | None, Query(description="SUBMISSION / AVATAR / CERT_PDF / IMPORT / REPORT")
    ] = None,
    uploader_id: Annotated[int | None, Query(description="上传人 ID")] = None,
    keyword: Annotated[str | None, Query(description="文件名或对象键模糊搜索")] = None,
) -> Page[object]:
    return await assets.list_assets(page, biz_type=biz_type, uploader_id=uploader_id, keyword=keyword)


@router.post(
    "/file-assets",
    response_model=ApiResponse[FileAssetRead],
    status_code=status.HTTP_201_CREATED,
    summary="登记一个已上传到对象存储的文件",
)
async def create_file_asset(payload: FileAssetCreate, assets: FileAssetRepo) -> FileAsset:
    try:
        return await assets.create(payload.model_dump())
    except IntegrityError as exc:  # 唯一约束：同桶同 key 只能登记一次
        raise ConflictError(f"文件 {payload.object_key} 已登记") from exc


@router.patch("/file-assets/{asset_id}", response_model=ApiResponse[FileAssetRead], summary="更新文件信息")
async def update_file_asset(asset_id: int, payload: FileAssetUpdate, assets: FileAssetRepo) -> FileAsset:
    asset = await assets.get(asset_id)
    if asset is None:
        raise NotFoundError(f"文件 {asset_id} 不存在")
    return await assets.update(asset, payload.model_dump(exclude_unset=True))


@router.delete("/file-assets/{asset_id}", response_model=ApiResponse[MessageOut], summary="删除文件登记")
async def delete_file_asset(asset_id: int, assets: FileAssetRepo) -> MessageOut:
    asset = await assets.get(asset_id)
    if asset is None:
        raise NotFoundError(f"文件 {asset_id} 不存在")
    await assets.remove(asset)
    return MessageOut(message="文件登记已删除")


# ----------------------------------------------------------------- 整单提交


@router.get(
    "/submissions",
    response_model=ApiResponse[Page[ProjectSubmissionListRead]],
    summary="整单提交分页列表（教师看板用）",
)
async def list_submissions(
    submissions: SubmissionRepo,
    page: PageDep,
    status_: Annotated[str | None, Query(alias="status", description="提交/评审状态")] = None,
    is_starred: Annotated[bool | None, Query(description="只看教师标星的")] = None,
    student_id: Annotated[int | None, Query(description="按学生过滤")] = None,
    project_id: Annotated[int | None, Query(description="按项目过滤")] = None,
) -> Page[object]:
    return await submissions.list_submissions(
        page, status=status_, is_starred=is_starred, student_id=student_id, project_id=project_id
    )


@router.get(
    "/submissions/{submission_id}",
    response_model=ApiResponse[ProjectSubmissionDetail],
    summary="提交详情（含作答与评审记录）",
)
async def get_submission(
    submission_id: int,
    submissions: SubmissionRepo,
    attempts: AttemptRepo,
    records: StudentProjectRepo,
    stages: AttemptStageRepo,
    stage_files: StageFileRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
    projects: TrainingProjectRepo,
    reviews: ReviewRepo,
) -> dict:
    submission = await _submission_or_404(submissions, submission_id)
    attempt = await attempts.get(submission.attempt_id)
    stage_payload: list[dict] = []
    record = None
    if attempt is not None:
        detail = await _attempt_detail(
            attempt,
            records=records,
            stages=stages,
            stage_files=stage_files,
            modules=modules,
            templates=templates,
            projects=projects,
        )
        stage_payload = detail["stages"]
        record = await records.get(attempt.student_project_id)
    return {
        **submission.model_dump(),
        "attempt": attempt,
        "student_id": record.student_id if record else None,
        "project_id": record.project_id if record else None,
        "stages": stage_payload,
        "reviews": await reviews.list_of_submission(submission_id),
    }


@router.patch(
    "/submissions/{submission_id}",
    response_model=ApiResponse[ProjectSubmissionRead],
    summary="教师标记（标星/异议说明/状态）",
)
async def update_submission(
    submission_id: int, payload: ProjectSubmissionUpdate, submissions: SubmissionRepo
) -> ProjectSubmission:
    submission = await _submission_or_404(submissions, submission_id)
    return await submissions.update(submission, payload.model_dump(exclude_unset=True))


@router.post(
    "/submissions/{submission_id}/withdraw",
    response_model=ApiResponse[ProjectSubmissionRead],
    summary="学生撤回本次提交（回到可编辑）",
)
async def withdraw(submission_id: int, db: DbSession, submissions: SubmissionRepo) -> ProjectSubmission:
    await _submission_or_404(submissions, submission_id)
    return await withdraw_submission(db, submission_id=submission_id)


@router.post(
    "/submissions/{submission_id}/objection",
    response_model=ApiResponse[ProjectSubmissionRead],
    summary="学生对 AI 评审提异议（可留言，转教师复核）",
)
async def submit_objection(
    submission_id: int,
    payload: SubmissionObjectionIn,
    db: DbSession,
    submissions: SubmissionRepo,
) -> ProjectSubmission:
    await _submission_or_404(submissions, submission_id)
    return await raise_objection(db, submission_id=submission_id, reason=payload.reason)


@router.post(
    "/submissions/{submission_id}/claim",
    response_model=ApiResponse[ProjectSubmissionRead],
    summary="教师认领复核（状态转 REVIEWING）",
)
async def claim_submission(
    submission_id: int, db: DbSession, submissions: SubmissionRepo
) -> ProjectSubmission:
    await _submission_or_404(submissions, submission_id)
    return await claim_for_review(db, submission_id=submission_id)


# --------------------------------------------------------------- AI 评审任务


@router.get(
    "/submissions/{submission_id}/ai-jobs",
    response_model=ApiResponse[list[ReviewAiJobRead]],
    summary="AI 评审任务列表",
)
async def list_ai_jobs(
    submission_id: int, submissions: SubmissionRepo, ai_jobs: AiJobRepo
) -> list[ReviewAiJob]:
    await _submission_or_404(submissions, submission_id)
    return await ai_jobs.list_of_submission(submission_id)


@router.post(
    "/submissions/{submission_id}/ai-jobs",
    response_model=ApiResponse[ReviewAiJobRead],
    status_code=status.HTTP_201_CREATED,
    summary="重新排一次 AI 评审任务",
)
async def create_ai_job(
    submission_id: int, payload: ReviewAiJobCreate, submissions: SubmissionRepo, ai_jobs: AiJobRepo
) -> ReviewAiJob:
    await _submission_or_404(submissions, submission_id)
    data = payload.model_dump(exclude={"submission_id"})
    return await ai_jobs.create({"submission_id": submission_id, **data})


@router.patch(
    "/ai-jobs/{job_id}",
    response_model=ApiResponse[ReviewAiJobRead],
    summary="回写 AI 任务状态（真实调用由异步 worker 负责）",
)
async def update_ai_job(job_id: int, payload: ReviewAiJobUpdate, ai_jobs: AiJobRepo) -> ReviewAiJob:
    job = await ai_jobs.get(job_id)
    if job is None:
        raise NotFoundError(f"AI 评审任务 {job_id} 不存在")
    return await ai_jobs.update(job, payload.model_dump(exclude_unset=True))


__all__ = ["router"]
