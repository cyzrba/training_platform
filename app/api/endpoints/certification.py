"""证书规则、证书申请与发放接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, or_, select

from app.api.deps import DbSession, PageDep
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.core.time import now
from app.crud.account import UserRepository
from app.crud.certification import (
    CertificateApplicationRepository,
    CertificateRuleRepository,
    CertificateSkillRepository,
    CertificationOverviewRepository,
)
from app.crud.job_skill import JobRepository, SkillNodeRepository
from app.models.account import SysUser
from app.models.certificate import StudentCertificate
from app.models.certification import (
    APPLICATION_STATUS_APPROVED,
    APPLICATION_STATUS_PENDING_INTERVIEW,
    APPLICATION_STATUS_REJECTED,
    APPLICATION_STATUS_UNDER_REVIEW,
    CertificateApplication,
    CertificateRule,
)
from app.models.job_skill import Job, StudentJob
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.certification import (
    CertificateApplicationCreate,
    CertificateApplicationRead,
    CertificateApplicationReviewIn,
    CertificateRuleCreate,
    CertificateRuleDetail,
    CertificateRuleRead,
    CertificateRuleUpdate,
    CertificationOverview,
    IssuedCertificateRead,
)

router = APIRouter(route_class=EnvelopeRoute, tags=["证书认证"])


def rule_repo(db: DbSession) -> CertificateRuleRepository:
    return CertificateRuleRepository(db)


def skill_repo(db: DbSession) -> CertificateSkillRepository:
    return CertificateSkillRepository(db)


def application_repo(db: DbSession) -> CertificateApplicationRepository:
    return CertificateApplicationRepository(db)


def overview_repo(db: DbSession) -> CertificationOverviewRepository:
    return CertificationOverviewRepository(db)


def job_repo(db: DbSession) -> JobRepository:
    return JobRepository(db)


def node_repo(db: DbSession) -> SkillNodeRepository:
    return SkillNodeRepository(db)


def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


RuleRepo = Annotated[CertificateRuleRepository, Depends(rule_repo)]
CertSkillRepo = Annotated[CertificateSkillRepository, Depends(skill_repo)]
ApplicationRepo = Annotated[CertificateApplicationRepository, Depends(application_repo)]
OverviewRepo = Annotated[CertificationOverviewRepository, Depends(overview_repo)]
JobRepo = Annotated[JobRepository, Depends(job_repo)]
NodeRepo = Annotated[SkillNodeRepository, Depends(node_repo)]
UserRepo = Annotated[UserRepository, Depends(user_repo)]


async def _rule_or_404(rules: RuleRepo, rule_id: int) -> CertificateRule:
    rule = await rules.get(rule_id)
    if rule is None:
        raise NotFoundError(f"证书规则 {rule_id} 不存在")
    return rule


async def _job_or_404(jobs: JobRepo, job_id: int) -> Job:
    job = await jobs.get(job_id)
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在")
    return job


async def _student_or_404(users: UserRepo, student_id: int) -> SysUser:
    student = await users.get(student_id)
    if student is None or student.user_type != "STUDENT":
        raise NotFoundError(f"学生 {student_id} 不存在")
    return student


async def _assert_nodes_exist(nodes: NodeRepo, node_ids: list[int]) -> None:
    for node_id in dict.fromkeys(node_ids):
        if await nodes.get(node_id) is None:
            raise BusinessRuleError(f"技能点 {node_id} 不存在")


async def _rule_detail(rule: CertificateRule, skills: CertSkillRepo) -> dict:
    return {
        **rule.model_dump(),
        "skills": await skills.list_skill_nodes(rule.id),
        "skill_count": len(await skills.list_skill_nodes(rule.id)),
        "skill_node_ids": [item["skill_node_id"] for item in await skills.list_skill_nodes(rule.id)],
    }


async def _job_name_of(db: DbSession, job_id: int) -> str | None:
    job = (await db.exec(select(Job).where(Job.id == job_id))).first()
    return job.job_name if job else None


async def _application_read(
    db: DbSession, application: CertificateApplication
) -> dict:
    student = (await db.exec(select(SysUser).where(SysUser.id == application.student_id))).first()
    rule = (
        await db.exec(
            select(CertificateRule).where(CertificateRule.id == application.certificate_rule_id)
        )
    ).first()
    job_name = None
    if rule is not None:
        job_name = await _job_name_of(db, rule.job_id)
    return {
        **application.model_dump(),
        "student_name": student.real_name if student else None,
        "student_no": student.user_no if student else None,
        "job_name": job_name,
        "cert_name": rule.cert_name if rule else None,
    }


async def _cert_lit(
    student_id: int,
    rule: CertificateRule,
    skills: CertSkillRepo,
    overview: OverviewRepo,
) -> dict:
    skill_nodes = await skills.list_skill_nodes(rule.id)
    progress_map = await overview.skill_progress_map(student_id)
    items = []
    done = 0
    for node in skill_nodes:
        progress = float(progress_map.get(node["skill_node_id"], 0))
        lit = progress >= 100
        if lit:
            done += 1
        items.append(
            {
                "skill_node_id": node["skill_node_id"],
                "node_name": node["node_name"],
                "description": node["description"],
                "progress": progress,
                "lit": lit,
            }
        )
    return {
        "skill_total": len(skill_nodes),
        "skill_done": done,
        "lit": bool(skill_nodes) and done == len(skill_nodes),
        "skills": items,
    }


# ------------------------------------------------------------ 证书规则（教师端）


@router.get("/cert-rules", response_model=ApiResponse[Page[CertificateRuleRead]], summary="证书规则分页列表")
async def list_cert_rules(
    rules: RuleRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="证书名或岗位名模糊搜索")] = None,
    status_: Annotated[str | None, Query(alias="status", description="ENABLED / DISABLED")] = None,
) -> Page[dict]:
    return await rules.list_rules(page, keyword=keyword, status=status_)


@router.post(
    "/cert-rules",
    response_model=ApiResponse[CertificateRuleRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建证书规则（含所需技能点）",
)
async def create_cert_rule(
    payload: CertificateRuleCreate,
    rules: RuleRepo,
    skills: CertSkillRepo,
    jobs: JobRepo,
    nodes: NodeRepo,
) -> dict:
    await _job_or_404(jobs, payload.job_id)
    if await rules.by_job(payload.job_id) is not None:
        raise ConflictError("该岗位已经配置过证书规则（一个职业一张证书）")
    await _assert_nodes_exist(nodes, payload.skill_node_ids)
    data = payload.model_dump(exclude={"skill_node_ids"})
    rule = await rules.create(data)
    await rules.replace_skills(rule.id, payload.skill_node_ids)
    return await _rule_detail(rule, skills)


@router.get(
    "/cert-rules/{rule_id}",
    response_model=ApiResponse[CertificateRuleDetail],
    summary="证书规则详情",
)
async def get_cert_rule(rule_id: int, rules: RuleRepo, skills: CertSkillRepo, db: DbSession) -> dict:
    rule = await _rule_or_404(rules, rule_id)
    detail = await _rule_detail(rule, skills)
    detail["job_name"] = await _job_name_of(db, rule.job_id)
    return detail


@router.patch(
    "/cert-rules/{rule_id}",
    response_model=ApiResponse[CertificateRuleDetail],
    summary="更新证书规则",
)
async def update_cert_rule(
    rule_id: int,
    payload: CertificateRuleUpdate,
    rules: RuleRepo,
    skills: CertSkillRepo,
    nodes: NodeRepo,
    db: DbSession,
) -> dict:
    rule = await _rule_or_404(rules, rule_id)
    data = payload.model_dump(exclude_unset=True)
    skill_node_ids = data.pop("skill_node_ids", None)
    if data:
        try:
            rule = await rules.update(rule, data)
        except IntegrityError as exc:
            raise ConflictError("更新失败：数据冲突") from exc
    if skill_node_ids is not None:
        await _assert_nodes_exist(nodes, skill_node_ids)
        await rules.replace_skills(rule.id, skill_node_ids)
    detail = await _rule_detail(rule, skills)
    detail["job_name"] = await _job_name_of(db, rule.job_id)
    return detail


@router.delete("/cert-rules/{rule_id}", response_model=ApiResponse[MessageOut], summary="删除证书规则")
async def delete_cert_rule(rule_id: int, rules: RuleRepo, db: DbSession) -> MessageOut:
    rule = await _rule_or_404(rules, rule_id)
    application_count = int(
        (
            await db.exec(
                select(func.count())
                .select_from(CertificateApplication)
                .where(CertificateApplication.certificate_rule_id == rule_id)
            )
        ).one()
    )
    if application_count > 0:
        raise BusinessRuleError("已有申请记录，不能删除该证书规则")
    await rules.delete_skills(rule_id)
    await rules.remove(rule)
    return MessageOut(message="证书规则已删除")


# ------------------------------------------------------ 证书申请与审核（教师端）


@router.get(
    "/cert-applications",
    response_model=ApiResponse[Page[CertificateApplicationRead]],
    summary="证书申请分页列表",
)
async def list_cert_applications(
    applications: ApplicationRepo,
    page: PageDep,
    status_: Annotated[
        str | None,
        Query(alias="status", description="PENDING_INTERVIEW / UNDER_REVIEW / APPROVED / REJECTED"),
    ] = None,
    keyword: Annotated[str | None, Query(description="学生姓名/学号/证书名")] = None,
) -> Page[dict]:
    return await applications.list_applications(page, status=status_, keyword=keyword)


@router.get(
    "/cert-applications/{application_id}",
    response_model=ApiResponse[CertificateApplicationRead],
    summary="证书申请详情",
)
async def get_cert_application(
    application_id: int, applications: ApplicationRepo, db: DbSession
) -> dict:
    application = await applications.get(application_id)
    if application is None:
        raise NotFoundError(f"申请记录 {application_id} 不存在")
    return await _application_read(db, application)


@router.post(
    "/cert-applications/{application_id}/review",
    response_model=ApiResponse[CertificateApplicationRead],
    summary="人工审核：通过发证 / 驳回",
)
async def review_cert_application(
    application_id: int,
    payload: CertificateApplicationReviewIn,
    applications: ApplicationRepo,
    db: DbSession,
) -> dict:
    application = await applications.get(application_id)
    if application is None:
        raise NotFoundError(f"申请记录 {application_id} 不存在")
    if application.status != APPLICATION_STATUS_UNDER_REVIEW:
        raise BusinessRuleError("只有待人工审核的申请才能审核")

    data: dict = {"review_comment": payload.review_comment}
    if payload.status == APPLICATION_STATUS_REJECTED:
        data["status"] = APPLICATION_STATUS_REJECTED
        application = await applications.update(application, data)
    else:
        rule = (
            await db.exec(
                select(CertificateRule).where(
                    CertificateRule.id == application.certificate_rule_id
                )
            )
        ).first()
        if rule is None:
            raise NotFoundError("证书规则不存在")
        certificate = StudentCertificate(
            student_id=application.student_id,
            job_id=rule.job_id,
            certificate_no=f"SPU-{now():%Y%m%d%H%M%S}-{application.student_id}",
            cert_name=rule.cert_name,
            issue_org_name=rule.issue_org_name,
            status="VALID",
            issued_by=application.reviewer_id,
        )
        db.add(certificate)
        await db.flush()
        await db.refresh(certificate)
        data["status"] = APPLICATION_STATUS_APPROVED
        data["student_certificate_id"] = certificate.id
        application = await applications.update(application, data)

    return await _application_read(db, application)


# ------------------------------------------------------------ 学生端


@router.get(
    "/students/{student_id}/certifications",
    response_model=ApiResponse[CertificationOverview],
    summary="学生证书概览（所选职业的证书 + 所需技能进度 + 最近申请）",
)
async def student_certifications(
    student_id: int,
    db: DbSession,
    users: UserRepo,
    rules: RuleRepo,
    skills: CertSkillRepo,
    overview: OverviewRepo,
    applications: ApplicationRepo,
) -> dict:
    await _student_or_404(users, student_id)

    selections = list(
        (
            await db.exec(
                select(StudentJob, Job)
                .join(Job, Job.id == StudentJob.job_id)  # type: ignore[arg-type]
                .where(StudentJob.student_id == student_id)
                .order_by(StudentJob.id)
            )
        ).all()
    )
    items: list[dict] = []
    for _selection, job in selections:
        rule = await rules.by_job(job.id)
        if rule is None:
            continue
        lit_info = await _cert_lit(student_id, rule, skills, overview)
        latest = await applications.latest_for(student_id, rule.id)
        items.append(
            {
                "certificate_rule_id": rule.id,
                "job_id": job.id,
                "job_name": job.job_name,
                "cert_name": rule.cert_name,
                "issue_org_name": rule.issue_org_name,
                "description": rule.description,
                "skill_total": lit_info["skill_total"],
                "skill_done": lit_info["skill_done"],
                "lit": lit_info["lit"],
                "skills": lit_info["skills"],
                "application": await _application_read(db, latest) if latest else None,
            }
        )
    return {"student_id": student_id, "items": items}


@router.post(
    "/students/{student_id}/cert-applications",
    response_model=ApiResponse[CertificateApplicationRead],
    status_code=status.HTTP_201_CREATED,
    summary="学生提交证书申请（需证书已点亮）",
)
async def apply_certificate(
    student_id: int,
    payload: CertificateApplicationCreate,
    db: DbSession,
    users: UserRepo,
    rules: RuleRepo,
    skills: CertSkillRepo,
    overview: OverviewRepo,
    applications: ApplicationRepo,
) -> dict:
    await _student_or_404(users, student_id)
    rule = await _rule_or_404(rules, payload.certificate_rule_id)
    lit_info = await _cert_lit(student_id, rule, skills, overview)
    if not lit_info["lit"]:
        raise BusinessRuleError("证书尚未点亮，完成所有所需技能后才能申请")
    if await applications.has_approved(student_id, rule.id):
        raise ConflictError("该职业的证书已通过审核")
    if await applications.has_active(student_id, rule.id):
        raise ConflictError("已有进行中的申请，请勿重复提交")
    application = await applications.create(
        {
            "student_id": student_id,
            "certificate_rule_id": rule.id,
            "status": APPLICATION_STATUS_PENDING_INTERVIEW,
        }
    )
    return await _application_read(db, application)


@router.post(
    "/students/{student_id}/cert-applications/{application_id}/interview",
    response_model=ApiResponse[CertificateApplicationRead],
    summary="AI 面试（本期占位：直接通过进入人工审核）",
)
async def interview_certificate(
    student_id: int,
    application_id: int,
    db: DbSession,
    users: UserRepo,
    applications: ApplicationRepo,
) -> dict:
    await _student_or_404(users, student_id)
    application = await applications.get(application_id)
    if application is None or application.student_id != student_id:
        raise NotFoundError(f"申请记录 {application_id} 不存在")
    if application.status != APPLICATION_STATUS_PENDING_INTERVIEW:
        raise BusinessRuleError("当前状态不能进行面试")
    application = await applications.update(
        application,
        {
            "status": APPLICATION_STATUS_UNDER_REVIEW,
            "interview_note": "AI 面试占位：本期直接通过",
        },
    )
    return await _application_read(db, application)


@router.get(
    "/students/{student_id}/certificates",
    response_model=ApiResponse[list[IssuedCertificateRead]],
    summary="学生已获得的证书",
)
async def student_issued_certificates(
    student_id: int, users: UserRepo, overview: OverviewRepo
) -> list[dict]:
    await _student_or_404(users, student_id)
    return await overview.issued_certificates(student_id)


@router.get(
    "/certificates",
    response_model=ApiResponse[Page[IssuedCertificateRead]],
    summary="证书台账（已发放证书分页列表）",
)
async def list_certificates(
    db: DbSession,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="学生姓名/学号/证书名")] = None,
) -> Page[dict]:
    stmt = (
        select(StudentCertificate, SysUser, Job)
        .join(SysUser, SysUser.id == StudentCertificate.student_id)  # type: ignore[arg-type]
        .join(Job, Job.id == StudentCertificate.job_id)  # type: ignore[arg-type]
    )
    count_stmt = select(func.count()).select_from(StudentCertificate)

    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        condition = or_(
            SysUser.real_name.like(pattern),
            SysUser.user_no.like(pattern),
            StudentCertificate.cert_name.like(pattern),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int((await db.exec(count_stmt)).one())
    rows = list(
        (
            await db.exec(
                stmt.order_by(StudentCertificate.id.desc())
                .offset((page.page - 1) * page.page_size)
                .limit(page.page_size)
            )
        ).all()
    )
    items = [
        {
            "id": cert.id,
            "job_id": cert.job_id,
            "job_name": job.job_name,
            "cert_name": cert.cert_name,
            "issue_org_name": cert.issue_org_name,
            "certificate_no": cert.certificate_no,
            "issue_date": cert.issue_date,
            "status": cert.status,
            "student_name": student.real_name,
            "student_no": student.user_no,
        }
        for cert, student, job in rows
    ]
    return Page.build(items=items, total=total, params=page)


__all__ = ["router"]
