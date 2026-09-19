"""闯关过程接口：学生实训记录、我的实训清单、闯关轮次、模块作答、整单提交、AI 评审任务。

学生流程：开始闯关（自动建记录 + 轮次 + 各关卡作答行）→ 逐关填写 → 整单提交 →
（AI 任务排队）→ 等待评审；教师侧在 /api/reviews 里评审定稿。
"""

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, PageDep
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.crud.account import UserRepository
from app.crud.attempt import (
    AttemptStageFileRepository,
    AttemptStageRepository,
    FileAssetRepository,
    ProjectSubmissionRepository,
    StudentProjectPickRepository,
    StudentProjectRepository,
    TrainingAttemptRepository,
)
from app.crud.project import ProjectModuleRepository, StageTemplateRepository, TrainingProjectRepository
from app.crud.publish import PublishTaskRepository
from app.crud.review import ReviewAiJobRepository, ReviewRecordRepository
from app.models.attempt import FileAsset, ProjectSubmission, StudentProject
from app.models.review import ReviewAiJob
from app.schemas.attempt import (
    AttemptAnswersSaveIn,
    AttemptAnswersSaveResult,
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
    StudentProjectPickIn,
    StudentProjectPickOrderIn,
    StudentProjectRead,
    StudentProjectUpdate,
    StudentTrainingProject,
    StudentTrainingProjectDetail,
    SubmissionObjectionIn,
)
from app.schemas.base import ApiResponse, MessageOut, Page, PageParams
from app.schemas.review import ReviewAiJobCreate, ReviewAiJobRead, ReviewAiJobUpdate
from app.services import publish as publish_service
from app.services import storage
from app.services.attempt import (
    claim_for_review,
    raise_objection,
    save_attempt_answers,
    save_stage_answer,
    start_attempt,
    submit_attempt,
    withdraw_submission,
)
from app.services.student_overview import my_projects, student_project_detail, student_projects

router = APIRouter(route_class=EnvelopeRoute, tags=["闯关评审"])


# --------------------------------------------------------------------- 依赖


def student_project_repo(db: DbSession) -> StudentProjectRepository:
    return StudentProjectRepository(db)


def project_pick_repo(db: DbSession) -> StudentProjectPickRepository:
    return StudentProjectPickRepository(db)


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
ProjectPickRepo = Annotated[StudentProjectPickRepository, Depends(project_pick_repo)]
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
    # 项目发布后对所有学生开放，不等任务（任务 = 必修标记，口径见 docs/方案设计.md §4.2）
    await publish_service.ensure_project_published(db, project_id)
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
    "/students/{student_id}/training-projects",
    response_model=ApiResponse[list[StudentTrainingProject]],
    summary="学生的实训项目列表（最高分 / 关卡进度 / 所属岗位 / 关联技能点 / 状态）",
)
async def list_training_projects_of_student(student_id: int, db: DbSession, students: UserRepo) -> list[dict]:
    """已发布的实训项目 + 该学生的最高分、当前关卡进度（总/完成）、岗位、技能点与项目状态。

    学生没开始过的项目也会返回（``status=NOT_STARTED``、成绩为 null、进度 0/关卡总数），
    前端按需过滤即可；``level_done`` 取最新一轮闯关已填写的关卡数，重新挑战会从 0 重新计。
    """
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    return await student_projects(db, student_id)


# --------------------------------------------------------------- 我的实训


async def _published_project_or_422(projects: TrainingProjectRepository, project_id: int) -> None:
    """只能把自己看得到（已发布）的项目加进「我的实训」。"""
    project = await projects.get(project_id)
    if project is None:
        raise NotFoundError(f"实训项目 {project_id} 不存在")
    if project.status != "PUBLISHED":
        raise BusinessRuleError(f"《{project.project_name}》还没发布，不能加入我的实训")


@router.get(
    "/students/{student_id}/my-projects",
    response_model=ApiResponse[list[StudentTrainingProject]],
    summary="我的实训（自己挑的 + 老师点名必修的项目）",
)
async def list_my_projects(student_id: int, db: DbSession, students: UserRepo) -> list[dict]:
    """「我的实训」清单 = 学生自己挑的 ∪ 老师发任务点名必修的（都是已发布项目）。

    每条带 ``picked``（是否自己加的）、``is_required``（是否老师点名）与组合标签 ``sources``
    （``SELF`` / ``TEACHER``，一个项目可能两者都是）。排序：必修置顶 → 学生自定义顺序 →
    加入时间新的在前。项目下架后不再出现（记录保留，重新上架自动回来）。
    """
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    return await my_projects(db, student_id)


@router.post(
    "/students/{student_id}/my-projects",
    response_model=ApiResponse[list[StudentTrainingProject]],
    summary="把项目加入我的实训（批量、幂等）",
)
async def add_my_projects(
    student_id: int,
    payload: StudentProjectPickIn,
    response: Response,
    db: DbSession,
    students: UserRepo,
    projects: TrainingProjectRepo,
    picks: ProjectPickRepo,
) -> list[dict]:
    """批量把自己挑的项目加进「我的实训」；已经加过的跳过（幂等）。

    全部都是新加时返回 **201**，一条都没新增（全重复）时返回 **200**。
    项目不存在 → 404；项目还没发布 → 422（学生本来也看不到它）。
    """
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    project_ids = list(dict.fromkeys(int(pid) for pid in payload.project_ids))
    for project_id in project_ids:
        await _published_project_or_422(projects, project_id)
    created = await picks.add_projects(student_id, project_ids)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return await my_projects(db, student_id)


@router.patch(
    "/students/{student_id}/my-projects/order",
    response_model=ApiResponse[list[StudentTrainingProject]],
    summary="调整我的实训里自己挑的项目的顺序（覆盖式）",
)
async def reorder_my_projects(
    student_id: int,
    payload: StudentProjectPickOrderIn,
    db: DbSession,
    students: UserRepo,
    picks: ProjectPickRepo,
) -> list[dict]:
    """按传入顺序写 ``sort_no``（1..N，覆盖式）。

    只对"学生自己加入的项目"生效：老师点名必修、但他没自己加过的项目不在清单表里，
    排序接口会返回 422 让他先加进来（这类项目本来就一直置顶）。
    """
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    missing = await picks.reorder(student_id, payload.project_ids)
    if missing:
        raise BusinessRuleError(f"这些项目不在你的「我的实训」里，加进来再排序：{missing}")
    return await my_projects(db, student_id)


@router.delete(
    "/students/{student_id}/my-projects/{project_id}",
    response_model=ApiResponse[MessageOut],
    summary="把项目移出我的实训（幂等）",
)
async def remove_my_project(
    student_id: int,
    project_id: int,
    db: DbSession,
    students: UserRepo,
    picks: ProjectPickRepo,
) -> MessageOut:
    """移出「我的实训」；本来就不在清单里也算成功（幂等）。

    老师点名的必修项目移出后仍会出现在列表里（必修是任务实时算的），返回消息会说明这一点。
    闯关记录、成绩、技能进度都不受影响。
    """
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    await picks.remove_project(student_id, project_id)
    if project_id in await PublishTaskRepository(db).visible_project_ids_of_student(student_id):
        return MessageOut(message="已从我的实训移除；该项目是老师点名的必修，仍会出现在列表里")
    return MessageOut(message="已从我的实训移除")


@router.get(
    "/students/{student_id}/projects/{project_id}",
    response_model=ApiResponse[StudentTrainingProjectDetail],
    summary="学生在某个实训项目上的全量详情（任务简介 / 关卡与子标题 / 提交历史与评语）",
)
async def get_student_training_project(
    student_id: int, project_id: int, db: DbSession, students: UserRepo
) -> dict:
    """项目详情页要的所有信息，一次取全。

    - **任务简介** = ``training_project.description``，另附所属岗位与关联技能点；
    - **关卡**按顺序返回，每关带模块库的名称/简介、作答要求、验收标准，以及
      ``items_json`` 里的**子标题 + 子标题简介（prompt）**；
    - 每关还带该学生最新一轮的填写状态、作答内容与附件数；
    - **历史提交**按时间倒序，带提交次数、日期与每次提交上的 AI / 教师评语（含各关卡维度得分与理由）。
    """
    if await students.get(student_id) is None:
        raise NotFoundError(f"学生 {student_id} 不存在")
    return await student_project_detail(db, student_id, project_id)


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


@router.put(
    "/attempts/{attempt_id}/answers",
    response_model=ApiResponse[AttemptAnswersSaveResult],
    summary="保存作答（草稿，一次可存多个关卡；不改动关卡完成状态）",
)
async def save_answers(attempt_id: int, payload: AttemptAnswersSaveIn, db: DbSession) -> dict:
    """学生点「保存作答」时调用：把本轮填了一半的内容存下来，下次进来接着写。

    只更新 body 里带到的关卡，其余关卡不动；默认**只存文本、不改关卡完成状态**，
    因此保存草稿不会推进关卡进度，也不会让整单提交提前放行。
    想在同一次调用里把某关标记为完成，就在该条目上传 ``is_filled=true``。
    """
    record, attempt, stage_total, saved_ids = await save_attempt_answers(
        db,
        attempt_id=attempt_id,
        answers=[item.model_dump() for item in payload.answers],
    )
    return {
        "attempt_id": int(attempt.id),
        "attempt_no": attempt.attempt_no,
        "saved_count": len(saved_ids),
        "saved_stage_ids": sorted(saved_ids),
        "filled_stage_count": attempt.filled_stage_count,
        "stage_total": stage_total,
        "progress": float(record.progress),
        "saved_at": attempt.updated_at,
    }


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


@router.post(
    "/file-assets/upload",
    response_model=ApiResponse[FileAssetRead],
    status_code=status.HTTP_201_CREATED,
    summary="上传文件（写对象存储 + 登记文件台账）",
)
async def upload_file_asset(
    assets: FileAssetRepo,
    file: Annotated[UploadFile, File(description="要上传的文件")],
    biz_type: Annotated[
        str, Form(description="业务类型：REPORT_TEMPLATE / DATASET / GUIDE / SUBMISSION / REPORT ...")
    ] = "OTHER",
    uploader_id: Annotated[int | None, Form(description="上传人 ID")] = None,
) -> FileAsset:
    """对象写入 ``<bucket>/misc/<biz_type>/<年月>/<uuid>_<安全文件名>``，并写 file_asset 台账。"""
    content = await file.read()
    original_name = storage.clean_upload_name(file.filename)
    stored = storage.save_bytes(content, filename=original_name, biz_type=biz_type)
    return await assets.create(
        {
            "uploader_id": uploader_id,
            "bucket": stored.bucket,
            "object_key": stored.object_key,
            "original_name": original_name,
            "content_type": file.content_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "biz_type": biz_type,
        }
    )


@router.get(
    "/file-assets/{asset_id}/download",
    summary="下载文件（返回文件流，不包统一响应体）",
)
async def download_file_asset(asset_id: int, assets: FileAssetRepo) -> Response:
    """从对象存储读出对象，按附件的原始文件名作为下载名返回。"""
    asset = await assets.get(asset_id)
    if asset is None:
        raise NotFoundError(f"文件 {asset_id} 不存在")
    content = storage.read_bytes(asset.bucket, asset.object_key)
    media_type = asset.content_type or "application/octet-stream"
    # HTTP 头只能是 latin-1，中文文件名要按 RFC 5987 编码
    quoted = quote(asset.original_name)
    disposition = (
        f"attachment; filename*=utf-8''{quoted}"
        if quoted != asset.original_name
        else f'attachment; filename="{asset.original_name}"'
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


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
