"""教学组织接口：班级、分组、在班学生、Excel 名单导入（/api/classes 等）。

教师端主流程：建班级 → 建分组 → 下载模板 → 上传名单（先校验后落库）→ 学生自动建号入班。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, PageDep
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.response import EnvelopeRoute
from app.core.time import now
from app.crud.account import UserRepository
from app.crud.organization import (
    ClassGroupRepository,
    ClassRepository,
    ClassStudentGroupRepository,
    ClassStudentRepository,
)
from app.models.organization import ClassGroup, ClassInfo, ClassStudent, ClassStudentGroup
from app.schemas.account import PasswordResetResult
from app.schemas.base import ApiResponse, MessageOut, Page
from app.schemas.organization import (
    ClassGroupCreateIn,
    ClassGroupRead,
    ClassGroupUpdate,
    ClassGroupWithStudents,
    ClassInfoCreate,
    ClassInfoDetail,
    ClassInfoRead,
    ClassInfoUpdate,
    ClassPasswordResetIn,
    ClassStudentAddIn,
    ClassStudentDetail,
    ClassStudentGroupRead,
    ClassStudentGroupSetIn,
    ClassStudentRead,
    ClassStudentUpdate,
    GroupMemberBatchIn,
    GroupMemberBatchResult,
    StudentImportResult,
)
from app.services.password import default_password_hash, hash_password
from app.services.skill import refresh_student_skills
from app.services.student_import import (
    build_template_xlsx,
    import_students,
    parse_students,
    validate_students,
)

router = APIRouter(route_class=EnvelopeRoute, tags=["教学组织"])


# --------------------------------------------------------------------- 依赖


def class_repo(db: DbSession) -> ClassRepository:
    return ClassRepository(db)


def group_repo(db: DbSession) -> ClassGroupRepository:
    return ClassGroupRepository(db)


def class_student_repo(db: DbSession) -> ClassStudentRepository:
    return ClassStudentRepository(db)


def student_group_repo(db: DbSession) -> ClassStudentGroupRepository:
    return ClassStudentGroupRepository(db)


def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


ClassRepo = Annotated[ClassRepository, Depends(class_repo)]
GroupRepo = Annotated[ClassGroupRepository, Depends(group_repo)]
ClassStudentRepo = Annotated[ClassStudentRepository, Depends(class_student_repo)]
StudentGroupRepo = Annotated[ClassStudentGroupRepository, Depends(student_group_repo)]
UserRepo = Annotated[UserRepository, Depends(user_repo)]


# --------------------------------------------------------------------- 工具


async def _class_or_404(classes: ClassRepository, class_id: int) -> ClassInfo:
    classroom = await classes.get(class_id)
    if classroom is None:
        raise NotFoundError(f"班级 {class_id} 不存在")
    return classroom


async def _group_or_404(groups: ClassGroupRepository, group_id: int) -> ClassGroup:
    group = await groups.get(group_id)
    if group is None:
        raise NotFoundError(f"分组 {group_id} 不存在")
    return group


async def _enrollment_or_404(enrollments: ClassStudentRepository, class_student_id: int) -> ClassStudent:
    enrollment = await enrollments.get(class_student_id)
    if enrollment is None:
        raise NotFoundError(f"在班记录 {class_student_id} 不存在")
    return enrollment


async def _enrollment_detail(
    enrollment: ClassStudent,
    *,
    classes: ClassRepository,
    students: UserRepository,
    groups: ClassGroupRepository,
    memberships: ClassStudentGroupRepository,
) -> dict[str, object]:
    """把在班记录拼成花名册的一行（学生信息 + 分组 + 班级名）。"""
    student = await students.get(enrollment.student_id)
    if student is None:
        raise NotFoundError(f"学生 {enrollment.student_id} 不存在")
    classroom = await classes.get(enrollment.class_id)
    membership = await memberships.by_class_student(enrollment.id)
    group = await groups.get(membership.group_id) if membership else None
    return {
        "id": enrollment.id,
        "class_id": enrollment.class_id,
        "student_id": enrollment.student_id,
        "status": enrollment.status,
        "enrolled_at": enrollment.enrolled_at,
        "left_at": enrollment.left_at,
        "created_at": enrollment.created_at,
        "updated_at": enrollment.updated_at,
        "user_no": student.user_no,
        "real_name": student.real_name,
        "major_name": student.major_name,
        "class_name": classroom.class_name if classroom else None,
        "account_status": student.status,
        "group_id": membership.group_id if membership else None,
        "group_name": group.group_name if group else None,
    }


# ------------------------------------------------------------------- 班级


@router.get("/classes/students/import-template", summary="下载学生名单导入模板")
async def download_import_template() -> Response:
    return Response(
        content=build_template_xlsx(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="student-import-template.xlsx"'},
    )


@router.get("/classes", response_model=ApiResponse[Page[ClassInfoRead]], summary="班级分页列表")
async def list_classes(
    classes: ClassRepo,
    page: PageDep,
    keyword: Annotated[str | None, Query(description="班级名或备注模糊搜索")] = None,
    status_: Annotated[str | None, Query(alias="status", description="ACTIVE / ARCHIVED")] = None,
    head_teacher_id: Annotated[int | None, Query(description="负责教师 ID")] = None,
) -> Page[object]:
    return await classes.list_classes(page, keyword=keyword, status=status_, head_teacher_id=head_teacher_id)


@router.post(
    "/classes",
    response_model=ApiResponse[ClassInfoRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建班级",
)
async def create_class(payload: ClassInfoCreate, classes: ClassRepo) -> ClassInfo:
    return await classes.create(payload.model_dump())


@router.get(
    "/classes/{class_id}", response_model=ApiResponse[ClassInfoDetail], summary="班级详情（含在班人数）"
)
async def get_class(class_id: int, classes: ClassRepo, enrollments: ClassStudentRepo) -> dict:
    classroom = await _class_or_404(classes, class_id)
    data = classroom.model_dump()
    data["student_count"] = await enrollments.count_in_class(class_id)
    return data


@router.patch("/classes/{class_id}", response_model=ApiResponse[ClassInfoRead], summary="更新班级")
async def update_class(class_id: int, payload: ClassInfoUpdate, classes: ClassRepo) -> ClassInfo:
    classroom = await _class_or_404(classes, class_id)
    return await classes.update(classroom, payload.model_dump(exclude_unset=True))


@router.delete("/classes/{class_id}", response_model=ApiResponse[MessageOut], summary="归档删除班级（软删）")
async def delete_class(
    class_id: int, db: DbSession, classes: ClassRepo, enrollments: ClassStudentRepo
) -> MessageOut:
    classroom = await _class_or_404(classes, class_id)
    student_ids = await enrollments.student_ids(class_id)
    await classes.remove(classroom)
    # 班级删掉后，发给这个班的任务不再覆盖它的学生 → 分母变小 → 重算技能进度
    # （学生换班 / 换老师场景里，"旧班撤掉、新班还没发任务"的空窗期靠这一步保持一致）
    for student_id in student_ids:
        await refresh_student_skills(db, student_id)
    return MessageOut(message="班级已删除")


# ------------------------------------------------------------------- 分组


@router.get(
    "/classes/{class_id}/groups",
    response_model=ApiResponse[list[ClassGroupWithStudents]],
    summary="班级分组列表（可带组内学生）",
)
async def list_groups(
    class_id: int,
    classes: ClassRepo,
    groups: GroupRepo,
    memberships: StudentGroupRepo,
    with_students: Annotated[
        bool, Query(description="true 时每个分组带上组内学生，便于一次拉全分组视图")
    ] = False,
) -> list[dict]:
    await _class_or_404(classes, class_id)
    items = await groups.list_by_class(class_id)
    counts = await memberships.group_counts([group.id for group in items])
    result: list[dict] = []
    for group in items:
        students: list[dict] = []
        if with_students:
            students = [
                {
                    "class_student_id": class_student_id,
                    "student_id": student_id,
                    "user_no": user_no,
                    "real_name": real_name,
                    "major_name": major_name,
                }
                for class_student_id, student_id, user_no, real_name, major_name in (
                    await memberships.member_rows(group.id)
                )
            ]
        result.append(
            {
                **group.model_dump(),
                "student_count": counts.get(group.id, 0),
                "students": students,
            }
        )
    return result


@router.post(
    "/classes/{class_id}/groups",
    response_model=ApiResponse[ClassGroupRead],
    status_code=status.HTTP_201_CREATED,
    summary="新建分组（序号可自动分配）",
)
async def create_group(
    class_id: int, payload: ClassGroupCreateIn, classes: ClassRepo, groups: GroupRepo
) -> ClassGroup:
    await _class_or_404(classes, class_id)
    group_no = payload.group_no or await groups.next_group_no(class_id)
    if await groups.by_no(class_id, group_no) is not None:
        raise ConflictError(f"该班级已存在第 {group_no} 组")
    try:
        return await groups.create(
            {"class_id": class_id, "group_no": group_no, "group_name": payload.group_name}
        )
    except IntegrityError as exc:
        raise ConflictError(f"该班级已存在第 {group_no} 组") from exc


@router.patch("/groups/{group_id}", response_model=ApiResponse[ClassGroupRead], summary="更新分组")
async def update_group(group_id: int, payload: ClassGroupUpdate, groups: GroupRepo) -> ClassGroup:
    group = await _group_or_404(groups, group_id)
    data = payload.model_dump(exclude_unset=True)
    new_no = data.get("group_no")
    if new_no and new_no != group.group_no and await groups.by_no(group.class_id, new_no) is not None:
        raise ConflictError(f"该班级已存在第 {new_no} 组")
    return await groups.update(group, data)


@router.delete("/groups/{group_id}", response_model=ApiResponse[MessageOut], summary="删除分组")
async def delete_group(group_id: int, groups: GroupRepo, memberships: StudentGroupRepo) -> MessageOut:
    group = await _group_or_404(groups, group_id)
    count = await memberships.count_in_group(group_id)
    if count:
        raise ConflictError(f"该分组下还有 {count} 名学生，请先调整分组")
    await groups.remove(group)
    return MessageOut(message="分组已删除")


@router.get(
    "/groups/{group_id}/students",
    response_model=ApiResponse[Page[ClassStudentDetail]],
    summary="查看分组内的学生",
)
async def list_group_students(
    group_id: int,
    groups: GroupRepo,
    enrollments: ClassStudentRepo,
    page: PageDep,
    status_: Annotated[
        str | None, Query(alias="status", description="ENROLLED（默认，在班）/ LEFT（已离班）")
    ] = "ENROLLED",
    keyword: Annotated[str | None, Query(description="学号或姓名模糊搜索")] = None,
) -> Page[object]:
    group = await _group_or_404(groups, group_id)
    return await enrollments.list_details(
        group.class_id, page, status=status_, group_id=group.id, keyword=keyword
    )


@router.post(
    "/groups/{group_id}/students",
    response_model=ApiResponse[GroupMemberBatchResult],
    summary="批量把学生加入分组（已在别的组会自动调整过来）",
)
async def add_group_students(
    group_id: int,
    payload: GroupMemberBatchIn,
    db: DbSession,
    groups: GroupRepo,
    enrollments: ClassStudentRepo,
    memberships: StudentGroupRepo,
) -> GroupMemberBatchResult:
    group = await _group_or_404(groups, group_id)
    added = 0
    for class_student_id in dict.fromkeys(payload.class_student_ids):
        enrollment = await _enrollment_or_404(enrollments, class_student_id)
        if enrollment.class_id != group.class_id:
            raise BusinessRuleError(f"在班记录 {class_student_id} 不属于分组 {group.group_name} 所在的班级")
        await memberships.set_group(class_student_id, group.id)
        # 换了组 → "哪些任务覆盖到我"可能变了 → 重算该学生的技能进度（见 docs/方案设计.md §12）
        await refresh_student_skills(db, enrollment.student_id)
        added += 1
    return GroupMemberBatchResult(
        group_id=group.id, added=added, member_total=await memberships.count_in_group(group.id)
    )


@router.delete(
    "/groups/{group_id}/students/{class_student_id}",
    response_model=ApiResponse[MessageOut],
    summary="把学生移出分组（回到未分组）",
)
async def remove_group_student(
    group_id: int,
    class_student_id: int,
    db: DbSession,
    groups: GroupRepo,
    enrollments: ClassStudentRepo,
    memberships: StudentGroupRepo,
) -> MessageOut:
    group = await _group_or_404(groups, group_id)
    enrollment = await _enrollment_or_404(enrollments, class_student_id)
    membership = await memberships.by_class_student(class_student_id)
    if membership is None or membership.group_id != group.id:
        raise NotFoundError(f"该学生不在分组 {group.group_name} 中")
    if enrollment.class_id != group.class_id:
        raise BusinessRuleError("该学生不属于此分组所在的班级")
    await memberships.remove_member(class_student_id)
    await refresh_student_skills(db, enrollment.student_id)
    return MessageOut(message="已移出分组")


# --------------------------------------------------------------- 在班学生


@router.get(
    "/classes/{class_id}/students",
    response_model=ApiResponse[Page[ClassStudentDetail]],
    summary="班级花名册（分页）",
)
async def list_class_students(
    class_id: int,
    classes: ClassRepo,
    enrollments: ClassStudentRepo,
    page: PageDep,
    status_: Annotated[
        str | None, Query(alias="status", description="ENROLLED（默认，在班）/ LEFT（已离班）")
    ] = "ENROLLED",
    group_id: Annotated[int | None, Query(description="按分组过滤")] = None,
    ungrouped: Annotated[bool, Query(description="只看还没分组的学生（与 group_id 互斥）")] = False,
    keyword: Annotated[str | None, Query(description="学号或姓名模糊搜索")] = None,
) -> Page[object]:
    await _class_or_404(classes, class_id)
    if ungrouped and group_id is not None:
        raise BusinessRuleError("ungrouped 与 group_id 不能同时使用")
    return await enrollments.list_details(
        class_id, page, status=status_, group_id=group_id, ungrouped=ungrouped, keyword=keyword
    )


@router.post(
    "/classes/{class_id}/students",
    response_model=ApiResponse[ClassStudentDetail],
    status_code=status.HTTP_201_CREATED,
    summary="加入单个学生（账号不存在则用默认密码新建）",
)
async def add_class_student(
    class_id: int,
    payload: ClassStudentAddIn,
    db: DbSession,
    classes: ClassRepo,
    students: UserRepo,
    enrollments: ClassStudentRepo,
    groups: GroupRepo,
    memberships: StudentGroupRepo,
) -> dict[str, object]:
    await _class_or_404(classes, class_id)
    student = await students.get_by(user_no=payload.user_no)
    if student is None:
        if not payload.real_name:
            raise BusinessRuleError(f"学号 {payload.user_no} 尚未建号，请同时提供姓名以便创建账号")
        student = await students.create(
            {
                "user_no": payload.user_no,
                "real_name": payload.real_name,
                "user_type": "STUDENT",
                "major_name": payload.major_name,
                "phone": payload.phone,
                "email": payload.email,
                "password_hash": default_password_hash(),
            }
        )

    enrollment = await enrollments.active_enrollment(class_id, student.id)
    if enrollment is None:
        enrollment = await enrollments.create(
            {"class_id": class_id, "student_id": student.id, "status": "ENROLLED"}
        )
    if payload.group_no is not None:
        group = await groups.by_no(class_id, payload.group_no)
        if group is None:
            raise BusinessRuleError(f"分组序号 {payload.group_no} 在该班级中不存在")
        await memberships.set_group(enrollment.id, group.id)
    # 入班 / 换组 → 分母变了 → 重算技能进度（见 docs/方案设计.md §12）
    await refresh_student_skills(db, enrollment.student_id)
    return await _enrollment_detail(
        enrollment, classes=classes, students=students, groups=groups, memberships=memberships
    )


@router.patch(
    "/class-students/{class_student_id}", response_model=ApiResponse[ClassStudentRead], summary="更新在班记录"
)
async def update_class_student(
    class_student_id: int, payload: ClassStudentUpdate, db: DbSession, enrollments: ClassStudentRepo
) -> ClassStudent:
    enrollment = await _enrollment_or_404(enrollments, class_student_id)
    updated = await enrollments.update(enrollment, payload.model_dump(exclude_unset=True))
    if "status" in payload.model_dump(exclude_unset=True):
        # 在班 / 离班一变，"哪些任务覆盖到我"就变了 → 重算技能进度
        await refresh_student_skills(db, updated.student_id)
    return updated


@router.delete(
    "/class-students/{class_student_id}", response_model=ApiResponse[MessageOut], summary="学生离班"
)
async def leave_class(class_student_id: int, db: DbSession, enrollments: ClassStudentRepo) -> MessageOut:
    enrollment = await _enrollment_or_404(enrollments, class_student_id)
    if enrollment.status == "LEFT":
        return MessageOut(message="该学生已离班")
    await enrollments.update(enrollment, {"status": "LEFT", "left_at": now()})
    # 离班后这个班不再把项目"发给他" → 分母变小 → 重算技能进度
    await refresh_student_skills(db, enrollment.student_id)
    return MessageOut(message="学生已离班")


@router.put(
    "/class-students/{class_student_id}/group",
    response_model=ApiResponse[ClassStudentGroupRead],
    summary="分配 / 调整学生分组",
)
async def set_student_group(
    class_student_id: int,
    payload: ClassStudentGroupSetIn,
    db: DbSession,
    enrollments: ClassStudentRepo,
    groups: GroupRepo,
    memberships: StudentGroupRepo,
) -> ClassStudentGroup:
    enrollment = await _enrollment_or_404(enrollments, class_student_id)
    group = await _group_or_404(groups, payload.group_id)
    if group.class_id != enrollment.class_id:
        raise BusinessRuleError("目标分组不属于该学生所在班级")
    membership = await memberships.set_group(class_student_id, group.id, assigned_by=payload.assigned_by)
    # 换组 → 分组目标的任务覆盖可能变了 → 重算技能进度（见 docs/方案设计.md §12）
    await refresh_student_skills(db, enrollment.student_id)
    return membership


# --------------------------------------------------------------- Excel 导入


@router.post(
    "/classes/{class_id}/students/import",
    response_model=ApiResponse[StudentImportResult],
    summary="Excel 导入学生名单（先校验，重复即整批拒绝）",
)
async def import_class_students(
    class_id: int,
    classes: ClassRepo,
    students: UserRepo,
    db: DbSession,
    file: Annotated[UploadFile, File(description="填写好的学生名单 xlsx")],
    dry_run: Annotated[bool, Form(description="true=只校验不落库")] = False,
    reuse_existing: Annotated[
        bool,
        Form(
            description=(
                "true=学号已存在时复用账号并加入本班（换老师 / 转班场景）；"
                "false（默认）时已存在的学号按整批失败处理"
            )
        ),
    ] = False,
) -> StudentImportResult:
    await _class_or_404(classes, class_id)
    rows, errors = parse_students(await file.read())
    existing_nos = await students.existing_user_nos([row.user_no for row in rows if row.user_no])
    errors.extend(validate_students(rows, existing_user_nos=existing_nos, allow_existing=reuse_existing))
    if errors:
        raise BusinessRuleError(
            f"名单校验未通过，共 {len(errors)} 处问题，已全部撤回未导入",
            detail=[item.model_dump() for item in errors],
        )
    return await import_students(db, class_id, rows, dry_run=dry_run)


# ------------------------------------------------------------- 班级批量操作


@router.post(
    "/classes/{class_id}/students/password/reset",
    response_model=ApiResponse[PasswordResetResult],
    summary="批量重置本班学生密码",
)
async def reset_class_students_password(
    class_id: int,
    classes: ClassRepo,
    students: UserRepo,
    enrollments: ClassStudentRepo,
    payload: ClassPasswordResetIn | None = None,
) -> PasswordResetResult:
    await _class_or_404(classes, class_id)
    student_ids = await enrollments.student_ids(class_id)
    if not student_ids:
        raise NotFoundError("该班级还没有在班学生")
    password_hash = hash_password(payload.new_password) if payload and payload.new_password else None
    if password_hash is None:
        password_hash = default_password_hash()
    for student_id in student_ids:
        student = await students.get(student_id)
        if student is None:
            continue
        await students.update(student, {"password_hash": password_hash})
    return PasswordResetResult(updated=len(student_ids), user_ids=student_ids)


__all__ = ["router"]
