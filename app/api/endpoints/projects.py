"""实训项目接口：模块库（关卡模板）与项目、项目模块组成。

设计约定：模块库是关卡的唯一来源 —— 项目只能"挑"模板，不能现场造关卡。
因此模板库是完整 CRUD（软删），项目侧只做"选择 + 排序 + 权重/要求覆盖"。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, PageDep
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.crud.attempt import FileAssetRepository
from app.crud.job_skill import ProjectSkillRepository, SkillNodeRepository
from app.crud.knowledge import KnowledgeDocRepository
from app.crud.project import (
    ProjectFileRepository,
    ProjectModuleRepository,
    StageTemplateRepository,
    TrainingProjectRepository,
)
from app.models.attempt import FileAsset
from app.models.enums import KnowledgeDocType, ProjectFileKind
from app.models.job_skill import SkillNode
from app.models.project import (
    ProjectFile,
    ProjectModule,
    ProjectStageTemplate,
    TrainingProject,
)
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.job_skill import JobSkillSetIn, SkillNodeRead
from app.schemas.project import (
    ProjectFileAddIn,
    ProjectFileRead,
    ProjectFileUpdate,
    ProjectModuleAddIn,
    ProjectModuleDetail,
    ProjectModuleOrderIn,
    ProjectModuleUpdate,
    ProjectStageTemplateCreate,
    ProjectStageTemplateDetail,
    ProjectStageTemplateRead,
    ProjectStageTemplateUpdate,
    TrainingProjectCreate,
    TrainingProjectDetail,
    TrainingProjectRead,
    TrainingProjectUpdate,
)
from app.services import knowledge_ingest, storage
from app.services import skill as skill_service
from app.services.project import (
    build_module_details,
    ensure_publishable,
    module_detail,
    weight_total,
)

router = APIRouter(route_class=EnvelopeRoute, tags=["实训项目"])


# --------------------------------------------------------------------- 依赖


def stage_template_repo(db: DbSession) -> StageTemplateRepository:
    return StageTemplateRepository(db)


def project_module_repo(db: DbSession) -> ProjectModuleRepository:
    return ProjectModuleRepository(db)


def training_project_repo(db: DbSession) -> TrainingProjectRepository:
    return TrainingProjectRepository(db)


def project_file_repo(db: DbSession) -> ProjectFileRepository:
    return ProjectFileRepository(db)


def file_asset_repo(db: DbSession) -> FileAssetRepository:
    return FileAssetRepository(db)


def project_skill_repo(db: DbSession) -> ProjectSkillRepository:
    return ProjectSkillRepository(db)


def skill_node_repo(db: DbSession) -> SkillNodeRepository:
    return SkillNodeRepository(db)


StageTemplateRepo = Annotated[StageTemplateRepository, Depends(stage_template_repo)]
ProjectModuleRepo = Annotated[ProjectModuleRepository, Depends(project_module_repo)]
TrainingProjectRepo = Annotated[TrainingProjectRepository, Depends(training_project_repo)]
ProjectSkillRepo = Annotated[ProjectSkillRepository, Depends(project_skill_repo)]
SkillNodeRepo = Annotated[SkillNodeRepository, Depends(skill_node_repo)]
ProjectFileRepo = Annotated[ProjectFileRepository, Depends(project_file_repo)]
FileAssetRepo = Annotated[FileAssetRepository, Depends(file_asset_repo)]


# --------------------------------------------------------------------- 工具


async def _template_or_404(templates: StageTemplateRepository, template_id: int) -> ProjectStageTemplate:
    template = await templates.get(template_id)
    if template is None:
        raise NotFoundError(f"模块库条目 {template_id} 不存在")
    return template


async def _project_or_404(projects: TrainingProjectRepository, project_id: int) -> TrainingProject:
    project = await projects.get(project_id)
    if project is None:
        raise NotFoundError(f"项目 {project_id} 不存在")
    return project


async def _project_module_or_404(
    modules: ProjectModuleRepository, project_id: int, module_id: int
) -> ProjectModule:
    module = await modules.get(module_id)
    if module is None or module.project_id != project_id:
        raise NotFoundError(f"项目 {project_id} 下的关卡 {module_id} 不存在")
    return module


async def _project_detail(
    project: TrainingProject,
    modules: ProjectModuleRepository,
    templates: StageTemplateRepository,
    files: ProjectFileRepository,
    assets: FileAssetRepository,
) -> dict:
    """项目详情：带模块组成（关卡名取模块库）、权重合计与附件清单。"""
    items = await modules.list_of_project(project.id)
    return {
        **project.model_dump(),
        "modules": await build_module_details(items, templates),
        "weight_total": await weight_total(items),
        "files": await _project_files(project.id, files, assets),
    }


async def _project_files(
    project_id: int,
    files: ProjectFileRepository,
    assets: FileAssetRepository,
    *,
    file_kind: str | None = None,
) -> list[dict]:
    """项目附件：把 file_asset 的文件名/大小/下载地址拼出来。"""
    result: list[dict] = []
    for link in await files.list_of_project(project_id, file_kind=file_kind):
        asset = await assets.get(link.file_asset_id)
        if asset is None:
            continue
        result.append(
            {
                **link.model_dump(),
                "original_name": asset.original_name,
                "content_type": asset.content_type,
                "size_bytes": asset.size_bytes,
                "download_url": f"/api/file-assets/{asset.id}/download",
            }
        )
    return result


def _checked_file_kind(value: str) -> ProjectFileKind:
    """附件用途只认枚举里的值，避免落库出现拼错的新用途。"""
    try:
        return ProjectFileKind(value)
    except ValueError as exc:
        allowed = " / ".join(item.value for item in ProjectFileKind)
        raise BusinessRuleError(f"未知的附件用途 {value}，可选：{allowed}") from exc


def _knowledge_fields(result: knowledge_ingest.IngestResult) -> dict:
    """把入库结果摊成上传响应里的几个字段（切片状态对老师可见）。"""
    doc = result.doc
    return {
        "knowledge_doc_id": doc.id,
        "knowledge_status": doc.status,
        "chunk_count": doc.total_chunks,
        "knowledge_error": doc.parse_error,
    }


async def _ingest_scoring_criteria(session: DbSession, *, asset: FileAsset, link: ProjectFile) -> dict:
    """把已经在文件台账里的文件当评分标准入库（同一文件重复挂不会建两份）。"""
    docs = KnowledgeDocRepository(session)
    existing = await docs.by_file_asset(asset.id)
    if existing is not None:
        return _knowledge_fields(
            knowledge_ingest.IngestResult(
                doc=existing,
                chunk_count=existing.total_chunks,
                ok=not existing.parse_error,
                error=existing.parse_error,
            )
        )
    ingested = await knowledge_ingest.create_doc_from_bytes(
        session,
        content=storage.read_bytes(asset.bucket, asset.object_key),
        filename=asset.original_name,
        title=link.title or asset.original_name,
        doc_type=KnowledgeDocType.EVAL_CRITERIA,
        file_asset_id=asset.id,
        uploaded_by=link.uploaded_by,
        description=link.remark,
    )
    return _knowledge_fields(ingested)


# ------------------------------------------------------------------- 模块库


@router.get(
    "/stage-templates",
    response_model=ApiResponse[Page[ProjectStageTemplateRead]],
    summary="模块库列表（教师自定义的关卡模板）",
)
async def list_stage_templates(
    templates: StageTemplateRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="模块名称模糊搜索")] = None,
) -> Page[object]:
    return await templates.list_templates(page, keyword=keyword)


@router.post(
    "/stage-templates",
    response_model=ApiResponse[ProjectStageTemplateRead],
    status_code=status.HTTP_201_CREATED,
    summary="新增模块库条目（新关卡先加到这里，再被项目选用）",
)
async def create_stage_template(
    payload: ProjectStageTemplateCreate, templates: StageTemplateRepo, response: Response
) -> ProjectStageTemplate:
    if await templates.by_name(payload.stage_name) is not None:
        raise ConflictError(f"模块 {payload.stage_name} 已存在")
    data = payload.model_dump()
    if not data.get("sort_no"):
        data["sort_no"] = await templates.next_sort_no()

    # 之前软删过的同名模块：直接恢复并覆盖内容，避免"删掉了就再也建不了同名关卡"
    removed = await templates.by_name_including_deleted(payload.stage_name)
    if removed is not None:
        response.status_code = status.HTTP_200_OK
        return await templates.restore(removed, data)
    try:
        return await templates.create(data)
    except IntegrityError as exc:
        raise ConflictError(f"模块 {payload.stage_name} 已存在") from exc


@router.get(
    "/stage-templates/{template_id}",
    response_model=ApiResponse[ProjectStageTemplateDetail],
    summary="模块库条目详情（含被引用次数）",
)
async def get_stage_template(
    template_id: int, templates: StageTemplateRepo, modules: ProjectModuleRepo
) -> dict:
    template = await _template_or_404(templates, template_id)
    return {
        **template.model_dump(),
        "used_by_projects": await modules.count_by_template(template_id),
    }


@router.patch(
    "/stage-templates/{template_id}",
    response_model=ApiResponse[ProjectStageTemplateRead],
    summary="修改模块库条目（已选该模板的项目会跟着变化）",
)
async def update_stage_template(
    template_id: int, payload: ProjectStageTemplateUpdate, templates: StageTemplateRepo
) -> ProjectStageTemplate:
    template = await _template_or_404(templates, template_id)
    data = payload.model_dump(exclude_unset=True)
    new_name = data.get("stage_name")
    if new_name and new_name != template.stage_name and await templates.by_name(new_name) is not None:
        raise ConflictError(f"模块 {new_name} 已存在")
    try:
        return await templates.update(template, data)
    except IntegrityError as exc:
        raise ConflictError(f"模块 {new_name} 已存在") from exc


@router.delete(
    "/stage-templates/{template_id}",
    response_model=ApiResponse[MessageOut],
    summary="删除模块库条目（软删；被项目选中过时不允许删除）",
)
async def delete_stage_template(
    template_id: int, templates: StageTemplateRepo, modules: ProjectModuleRepo
) -> MessageOut:
    template = await _template_or_404(templates, template_id)
    used = await modules.count_by_template(template_id)
    if used:
        raise ConflictError(f"模块「{template.stage_name}」已被 {used} 个项目选中，先从这些项目里移除再删除")
    await templates.remove(template)
    return MessageOut(message="模块库条目已删除（软删，可从列表隐藏）")


# --------------------------------------------------------------------- 项目


@router.get("/projects", response_model=ApiResponse[Page[TrainingProjectRead]], summary="项目分页列表")
async def list_projects(
    projects: TrainingProjectRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="项目名或描述模糊搜索")] = None,
    project_level: Annotated[str | None, Query(description="BASIC / ADVANCED / EXPANDED")] = None,
    status_: Annotated[str | None, Query(alias="status", description="DRAFT / PUBLISHED / OFF_SHELF")] = None,
    job_id: Annotated[int | None, Query(description="绑定岗位 ID")] = None,
    creator_id: Annotated[int | None, Query(description="创建人 ID（教师）")] = None,
) -> Page[object]:
    return await projects.list_projects(
        page,
        keyword=keyword,
        project_level=project_level,
        status=status_,
        job_id=job_id,
        creator_id=creator_id,
    )


@router.post(
    "/projects",
    response_model=ApiResponse[TrainingProjectRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建项目（默认草稿，之后从模块库挑关卡）",
)
async def create_project(payload: TrainingProjectCreate, projects: TrainingProjectRepo) -> TrainingProject:
    if await projects.by_name(payload.project_name) is not None:
        raise ConflictError(f"项目 {payload.project_name} 已存在")
    try:
        return await projects.create(payload.model_dump())
    except IntegrityError as exc:
        raise ConflictError(f"项目 {payload.project_name} 已存在") from exc


@router.get(
    "/projects/{project_id}",
    response_model=ApiResponse[TrainingProjectDetail],
    summary="项目详情（含关卡组成、权重合计与附件）",
)
async def get_project(
    project_id: int,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
    files: ProjectFileRepo,
    assets: FileAssetRepo,
) -> dict:
    project = await _project_or_404(projects, project_id)
    return await _project_detail(project, modules, templates, files, assets)


@router.patch(
    "/projects/{project_id}",
    response_model=ApiResponse[TrainingProjectRead],
    summary="更新项目（改成 PUBLISHED 会校验关卡与权重）",
)
async def update_project(
    project_id: int,
    payload: TrainingProjectUpdate,
    db: DbSession,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
) -> TrainingProject:
    project = await _project_or_404(projects, project_id)
    data = payload.model_dump(exclude_unset=True)
    new_name = data.get("project_name")
    if new_name and new_name != project.project_name and await projects.by_name(new_name) is not None:
        raise ConflictError(f"项目 {new_name} 已存在")
    old_status = project.status
    new_status = data.get("status")
    if new_status == "PUBLISHED" and old_status != "PUBLISHED":
        await ensure_publishable(project_id, modules)
    updated = await projects.update(project, data)
    # 上架 / 下架都会改变学生技能进度的分母（分母 = 全部已发布项目），要重算已有记录的学生
    if new_status is not None and new_status != old_status:
        await skill_service.sync_skills_after_projects_changed(db, [project_id])
    return updated


@router.delete(
    "/projects/{project_id}",
    response_model=ApiResponse[MessageOut],
    summary="删除项目（软删，同时清掉关卡组成）",
)
async def delete_project(
    project_id: int,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
    files: ProjectFileRepo,
) -> MessageOut:
    project = await _project_or_404(projects, project_id)
    if project.status == "PUBLISHED":
        raise BusinessRuleError("已发布的项目不能直接删除，请先下架")
    await modules.delete_of_project(project_id)
    await files.delete_of_project(project_id)
    await projects.remove(project)
    return MessageOut(message="项目已删除（软删，附件关联已清理，文件台账保留）")


# --------------------------------------------------------------- 项目附件


@router.get(
    "/projects/{project_id}/files",
    response_model=ApiResponse[list[ProjectFileRead]],
    summary="项目附件列表（报告模板、数据文件等）",
)
async def list_project_files(
    project_id: int,
    projects: TrainingProjectRepo,
    files: ProjectFileRepo,
    assets: FileAssetRepo,
    file_kind: Annotated[
        str | None,
        Query(description="按用途过滤：REPORT_TEMPLATE / DATASET / GUIDE / SCORING_CRITERIA / OTHER"),
    ] = None,
) -> list[dict]:
    await _project_or_404(projects, project_id)
    return await _project_files(project_id, files, assets, file_kind=file_kind)


@router.post(
    "/projects/{project_id}/files",
    response_model=ApiResponse[ProjectFileRead],
    status_code=status.HTTP_201_CREATED,
    summary="把已上传的文件挂到项目上",
)
async def add_project_file(
    project_id: int,
    payload: ProjectFileAddIn,
    session: DbSession,
    projects: TrainingProjectRepo,
    files: ProjectFileRepo,
    assets: FileAssetRepo,
) -> dict:
    await _project_or_404(projects, project_id)
    file_kind = _checked_file_kind(payload.file_kind)
    asset = await assets.get(payload.file_asset_id)
    if asset is None:
        raise NotFoundError(f"文件 {payload.file_asset_id} 不存在，请先上传")
    if await files.by_asset(project_id, asset.id) is not None:
        raise ConflictError(f"文件「{asset.original_name}」已经挂在这个项目上了")
    data = {**payload.model_dump(), "project_id": project_id, "file_kind": file_kind.value}
    if not data.get("sort_no"):
        data["sort_no"] = await files.next_sort_no(project_id)
    link = await files.create(data)
    result = {
        **link.model_dump(),
        "original_name": asset.original_name,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "download_url": f"/api/file-assets/{asset.id}/download",
    }
    # 评分标准挂上来也要入库：允许"先上传到文件台账，再挂到项目"这条路径
    if file_kind == ProjectFileKind.SCORING_CRITERIA:
        result.update(await _ingest_scoring_criteria(session, asset=asset, link=link))
    return result


@router.post(
    "/projects/{project_id}/files/upload",
    response_model=ApiResponse[ProjectFileRead],
    status_code=status.HTTP_201_CREATED,
    summary="一步到位：上传文件并挂到项目（报告模板 / 数据文件）",
)
async def upload_project_file(
    project_id: int,
    session: DbSession,
    projects: TrainingProjectRepo,
    files: ProjectFileRepo,
    assets: FileAssetRepo,
    file: Annotated[UploadFile, File(description="要上传的文件")],
    file_kind: Annotated[
        str,
        Form(
            description=(
                "REPORT_TEMPLATE 报告模板 / DATASET 数据文件 / GUIDE 说明 / "
                "SCORING_CRITERIA 评分标准（上传即解析切片入库）/ OTHER"
            )
        ),
    ] = "OTHER",
    title: Annotated[str | None, Form(description="展示名称，留空用文件名")] = None,
    filename: Annotated[
        str | None,
        Form(description="文件名覆盖（客户端文件名编码异常时用它传真实文件名）"),
    ] = None,
    remark: Annotated[str | None, Form(description="备注")] = None,
    uploaded_by: Annotated[int | None, Form(description="上传人 ID")] = None,
) -> dict:
    project = await _project_or_404(projects, project_id)
    kind = _checked_file_kind(file_kind)
    content = await file.read()
    original_name = storage.clean_upload_name(filename or file.filename)
    # 评分标准：先把解析与切片算出来（纯 CPU，不占数据库写锁），再落文件与台账
    plan = (
        knowledge_ingest.build_plan(content, original_name)
        if kind == ProjectFileKind.SCORING_CRITERIA
        else None
    )
    # 项目文件按项目分目录：projects/<项目ID>-<项目名>/<用途>/
    stored = storage.save_bytes(
        content,
        filename=original_name,
        biz_type=file_kind,
        scope=storage.project_scope(project.id, project.project_name),
    )
    asset = await assets.create(
        {
            "uploader_id": uploaded_by,
            "bucket": stored.bucket,
            "object_key": stored.object_key,
            "original_name": original_name,
            "content_type": file.content_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "biz_type": file_kind,
        }
    )
    link = await files.create(
        {
            "project_id": project_id,
            "file_asset_id": asset.id,
            "file_kind": kind.value,
            "title": title,
            "remark": remark,
            "sort_no": await files.next_sort_no(project_id),
            "uploaded_by": uploaded_by,
        }
    )
    result = {
        **link.model_dump(),
        "original_name": asset.original_name,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "download_url": f"/api/file-assets/{asset.id}/download",
    }
    if plan is not None:
        ingested = await knowledge_ingest.persist_plan(
            session,
            plan,
            title=title or original_name,
            doc_type=KnowledgeDocType.EVAL_CRITERIA,
            file_asset_id=asset.id,
            uploaded_by=uploaded_by,
            description=remark,
        )
        result.update(_knowledge_fields(ingested))
    return result


@router.patch(
    "/projects/{project_id}/files/{project_file_id}",
    response_model=ApiResponse[ProjectFileRead],
    summary="调整项目附件（改名 / 换用途 / 排序 / 备注）",
)
async def update_project_file(
    project_id: int,
    project_file_id: int,
    payload: ProjectFileUpdate,
    projects: TrainingProjectRepo,
    files: ProjectFileRepo,
    assets: FileAssetRepo,
) -> dict:
    await _project_or_404(projects, project_id)
    link = await files.get(project_file_id)
    if link is None or link.project_id != project_id:
        raise NotFoundError(f"项目 {project_id} 下的附件 {project_file_id} 不存在")
    updated = await files.update(link, payload.model_dump(exclude_unset=True))
    asset = await assets.get(updated.file_asset_id)
    return {
        **updated.model_dump(),
        "original_name": asset.original_name if asset else "",
        "content_type": asset.content_type if asset else None,
        "size_bytes": asset.size_bytes if asset else 0,
        "download_url": f"/api/file-assets/{updated.file_asset_id}/download",
    }


@router.delete(
    "/projects/{project_id}/files/{project_file_id}",
    response_model=ApiResponse[MessageOut],
    summary="解除项目附件（文件台账与磁盘文件保留）",
)
async def remove_project_file(
    project_id: int, project_file_id: int, projects: TrainingProjectRepo, files: ProjectFileRepo
) -> MessageOut:
    await _project_or_404(projects, project_id)
    link = await files.get(project_file_id)
    if link is None or link.project_id != project_id:
        raise NotFoundError(f"项目 {project_id} 下的附件 {project_file_id} 不存在")
    await files.remove(link)
    return MessageOut(message="附件已从项目移除")


# --------------------------------------------------------- 项目的关卡组成


@router.get(
    "/projects/{project_id}/modules",
    response_model=ApiResponse[list[ProjectModuleDetail]],
    summary="项目已选关卡列表",
)
async def list_project_modules(
    project_id: int,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
) -> list[ProjectModuleDetail]:
    await _project_or_404(projects, project_id)
    return await build_module_details(await modules.list_of_project(project_id), templates)


@router.post(
    "/projects/{project_id}/modules",
    response_model=ApiResponse[ProjectModuleDetail],
    status_code=status.HTTP_201_CREATED,
    summary="从模块库挑一个关卡加入项目",
)
async def add_project_module(
    project_id: int,
    payload: ProjectModuleAddIn,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
) -> ProjectModuleDetail:
    await _project_or_404(projects, project_id)
    template = await _template_or_404(templates, payload.template_id)
    if await modules.by_template(project_id, template.id) is not None:
        raise ConflictError(f"项目里已经有「{template.stage_name}」这个关卡了")

    stage_no = payload.stage_no or await modules.next_stage_no(project_id)
    if await modules.by_stage_no(project_id, stage_no) is not None:
        raise ConflictError(f"项目里已经存在第 {stage_no} 个关卡位置")

    data = {
        "project_id": project_id,
        "template_id": template.id,
        "stage_no": stage_no,
        # 子标题由教师手填，模板不提供默认值
        "items_json": [item.model_dump() for item in (payload.items_json or [])],
        # 没传的字段用模板默认值
        "required": template.default_required if payload.required is None else payload.required,
        "weight": template.default_weight if payload.weight is None else payload.weight,
        "requirement": payload.requirement
        if payload.requirement is not None
        else template.default_requirement,
        "accept_standard": (
            payload.accept_standard
            if payload.accept_standard is not None
            else template.default_accept_standard
        ),
    }
    module = await modules.create(data)
    return await module_detail(module, templates)


@router.patch(
    "/projects/{project_id}/modules/{module_id}",
    response_model=ApiResponse[ProjectModuleDetail],
    summary="调整关卡顺序 / 权重 / 必填 / 要求覆盖",
)
async def update_project_module(
    project_id: int,
    module_id: int,
    payload: ProjectModuleUpdate,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
) -> ProjectModuleDetail:
    await _project_or_404(projects, project_id)
    module = await _project_module_or_404(modules, project_id, module_id)
    data = payload.model_dump(exclude_unset=True)
    new_stage_no = data.get("stage_no")
    if new_stage_no and new_stage_no != module.stage_no:
        occupied = await modules.by_stage_no(project_id, new_stage_no)
        if occupied is not None and occupied.id != module.id:
            raise ConflictError(f"项目里已经存在第 {new_stage_no} 个关卡位置")
    updated = await modules.update(module, data)
    return await module_detail(updated, templates)


@router.put(
    "/projects/{project_id}/modules/order",
    response_model=ApiResponse[list[ProjectModuleDetail]],
    summary="按传入顺序重排关卡",
)
async def reorder_project_modules(
    project_id: int,
    payload: ProjectModuleOrderIn,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
    templates: StageTemplateRepo,
) -> list[ProjectModuleDetail]:
    await _project_or_404(projects, project_id)
    current = await modules.list_of_project(project_id)
    if sorted(payload.module_ids) != sorted(item.id for item in current):
        raise BusinessRuleError("排序列表必须刚好包含该项目下的全部关卡，不多不少")
    await modules.reorder(project_id, payload.module_ids)
    return await build_module_details(await modules.list_of_project(project_id), templates)


@router.delete(
    "/projects/{project_id}/modules/{module_id}",
    response_model=ApiResponse[MessageOut],
    summary="把关卡从项目里移除（后面关卡自动补位）",
)
async def remove_project_module(
    project_id: int,
    module_id: int,
    projects: TrainingProjectRepo,
    modules: ProjectModuleRepo,
) -> MessageOut:
    await _project_or_404(projects, project_id)
    module = await _project_module_or_404(modules, project_id, module_id)
    await modules.remove(module)
    await modules.renumber(project_id)
    return MessageOut(message="关卡已从项目移除")


# ---------------------------------------------------------- 项目所需技能


@router.get(
    "/projects/{project_id}/skills",
    response_model=ApiResponse[list[SkillNodeRead]],
    summary="项目所需技能（完成项目即推进这些技能）",
)
async def list_project_skills(
    project_id: int,
    projects: TrainingProjectRepo,
    project_skills: ProjectSkillRepo,
) -> list[SkillNode]:
    await _project_or_404(projects, project_id)
    return await project_skills.list_nodes_of_project(project_id)


@router.put(
    "/projects/{project_id}/skills",
    response_model=ApiResponse[list[SkillNodeRead]],
    summary="覆盖式设置项目所需技能",
)
async def set_project_skills(
    project_id: int,
    payload: JobSkillSetIn,
    db: DbSession,
    projects: TrainingProjectRepo,
    nodes: SkillNodeRepo,
    project_skills: ProjectSkillRepo,
) -> list[SkillNode]:
    project = await _project_or_404(projects, project_id)
    for node_id in dict.fromkeys(payload.skill_node_ids):
        if await nodes.get(node_id) is None:
            raise BusinessRuleError(f"技能节点 {node_id} 不存在")
    await project_skills.replace_skills(project_id, payload.skill_node_ids)
    if project.status == "PUBLISHED":
        await skill_service.sync_skills_after_projects_changed(db, [project_id])
    return await project_skills.list_nodes_of_project(project_id)


@router.post(
    "/projects/{project_id}/skills/{skill_node_id}",
    response_model=ApiResponse[MessageOut],
    status_code=status.HTTP_201_CREATED,
    summary="给项目追加一个技能",
)
async def add_project_skill(
    project_id: int,
    skill_node_id: int,
    db: DbSession,
    projects: TrainingProjectRepo,
    nodes: SkillNodeRepo,
    project_skills: ProjectSkillRepo,
) -> MessageOut:
    project = await _project_or_404(projects, project_id)
    node = await nodes.get(skill_node_id)
    if node is None:
        raise NotFoundError(f"技能节点 {skill_node_id} 不存在")
    if await project_skills.link_exists(project_id, skill_node_id):
        raise ConflictError(f"项目已关联技能「{node.node_name}」")
    await project_skills.add_skill(project_id, skill_node_id)
    if project.status == "PUBLISHED":
        await skill_service.sync_skills_after_projects_changed(db, [project_id])
    return MessageOut(message="技能已关联")


@router.delete(
    "/projects/{project_id}/skills/{skill_node_id}",
    response_model=ApiResponse[MessageOut],
    summary="解除项目与技能的关联",
)
async def remove_project_skill(
    project_id: int,
    skill_node_id: int,
    db: DbSession,
    projects: TrainingProjectRepo,
    project_skills: ProjectSkillRepo,
) -> MessageOut:
    project = await _project_or_404(projects, project_id)
    await project_skills.remove_skill(project_id, skill_node_id)
    if project.status == "PUBLISHED":
        await skill_service.sync_skills_after_projects_changed(db, [project_id])
    return MessageOut(message="技能已解除")


__all__ = ["router"]
