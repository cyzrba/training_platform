"""演示数据脚本测试：数量正确 + 可重复执行（幂等）。"""

from decimal import Decimal

import pytest
from sqlmodel import func, select

from app.crud.account import RoleRepository
from app.crud.job_skill import (
    JobRepository,
    JobSkillRepository,
    ProjectSkillRepository,
    SkillNodeRepository,
)
from app.crud.organization import ClassGroupRepository, ClassRepository, ClassStudentRepository
from app.crud.project import (
    ProjectModuleRepository,
    StageTemplateRepository,
    TrainingProjectRepository,
)
from app.crud.review import ReviewRecordRepository
from app.db.seed_demo import (
    CLASSES,
    DEMO_OBJECTION,
    DEMO_SCORES,
    JOBS,
    PROJECT_FILES,
    PROJECT_MODULE_ITEMS,
    PROJECT_SKILLS,
    PROJECTS,
    SKILL_NODES,
    STUDENT_PROJECT_PLAN,
    TASKS,
    TEACHERS,
    run_demo_seed,
)
from app.models.attempt import ProjectSubmission, StudentProject
from app.models.job_skill import StudentJob, StudentSkill


@pytest.mark.asyncio
async def test_demo_seed_counts_and_idempotency(db_session) -> None:
    # 角色由基础种子提供，这里先补上，才能验证角色分配
    roles = RoleRepository(db_session)
    for code, name in (("STUDENT", "学生"), ("TEACHER", "教师")):
        await roles.create({"role_code": code, "role_name": name})

    first = await run_demo_seed(db_session)
    expected_students = sum(item["student_count"] for item in CLASSES)
    assert first["teachers"] == len(TEACHERS)
    assert first["classes"] == len(CLASSES)
    assert first["groups"] == sum(len(item["group_names"]) for item in CLASSES)
    assert first["students"] == expected_students
    assert first["enrollments"] == expected_students
    assert first["group_assignments"] == sum(item["student_count"] for item in CLASSES if item["group_names"])
    assert first["roles_assigned"] == len(TEACHERS) + expected_students
    assert first["skill_trees"] == 4  # 基础种子已建时这里应为 0
    assert first["stage_templates"] == 7  # 同上：完整库里这里是 0
    assert first["skill_nodes"] == sum(len(nodes) for nodes in SKILL_NODES.values())
    assert first["jobs"] == len(JOBS)
    assert first["job_skills"] == sum(len(job["skill_names"]) for job in JOBS)
    assert first["student_jobs"] >= expected_students
    assert first["projects"] == len(PROJECTS)
    assert first["project_modules"] == sum(len(item["modules"]) for item in PROJECTS)
    assert first["project_skills"] == sum(len(nodes) for nodes in PROJECT_SKILLS.values())
    assert first["student_projects"] == sum(end - start for _, _, (start, end), _ in STUDENT_PROJECT_PLAN)
    assert first["submissions"] == sum(
        end - start
        for _, _, (start, end), target in STUDENT_PROJECT_PLAN
        if target in {"SUBMITTED", "COMPLETED", "OBJECTED"}
    )
    assert first["reviews"] == sum(
        (end - start) * (2 if target == "COMPLETED" else 1)
        for _, _, (start, end), target in STUDENT_PROJECT_PLAN
        if target in {"COMPLETED", "OBJECTED"}
    )
    assert first["student_skills"] > 0
    assert first["project_files"] == sum(len(files) for files in PROJECT_FILES.values())
    # 任务下发：学生端能看到项目，靠的就是这些任务
    assert first["publish_tasks"] == len(TASKS)
    assert first["publish_task_targets"] == sum(len(item["class_names"]) for item in TASKS)
    assert first["publish_task_jobs"] == sum(len(item["job_names"]) for item in TASKS)
    assert first["publish_task_projects"] == sum(len(item["project_names"]) for item in TASKS)

    second = await run_demo_seed(db_session)
    assert second == dict.fromkeys(second, 0), "重复执行不应再新增数据"

    # 数据形态：部分班级有分组、部分没有
    classes = ClassRepository(db_session)
    groups = ClassGroupRepository(db_session)
    enrollments = ClassStudentRepository(db_session)
    grouped, ungrouped = 0, 0
    for item in CLASSES:
        classroom = await classes.get_by(class_name=item["class_name"])
        assert classroom is not None
        assert await enrollments.count_in_class(classroom.id) == item["student_count"]
        class_groups = await groups.list_by_class(classroom.id)
        assert len(class_groups) == len(item["group_names"])
        if item["group_names"]:
            grouped += 1
        else:
            ungrouped += 1
    assert grouped == 3 and ungrouped == 2

    # 技能 / 岗位关联规模
    nodes = SkillNodeRepository(db_session)
    jobs = JobRepository(db_session)
    job_skills = JobSkillRepository(db_session)
    for item in JOBS:
        job = await jobs.by_name(item["job_name"])
        assert job is not None
        assert await job_skills.count_of_job(job.id) == len(item["skill_names"])
    assert await nodes.by_name("图像基础") is not None


@pytest.mark.asyncio
async def test_demo_seed_student_job_rules(db_session) -> None:
    """选岗规则：人人有且只有一个主岗位；部分学生多看一个岗位；少数学生换过主岗位。"""
    await RoleRepository(db_session).create({"role_code": "STUDENT", "role_name": "学生"})
    await run_demo_seed(db_session)

    student_ids = select(StudentJob.student_id).distinct()
    student_count = len((await db_session.exec(student_ids)).all())

    primary_counts = select(StudentJob.student_id, func.count()).where(
        StudentJob.is_primary.is_(True)  # type: ignore[attr-defined]
    )
    primary_counts = primary_counts.group_by(StudentJob.student_id)
    assert {int(row[1]) for row in (await db_session.exec(primary_counts)).all()} == {1}
    assert len((await db_session.exec(primary_counts)).all()) == student_count

    total = select(func.count()).select_from(StudentJob)
    assert int((await db_session.exec(total)).one()) > student_count  # 有人选了第二个岗位

    switched = (
        select(func.count())
        .select_from(StudentJob)
        .where(
            StudentJob.is_primary.is_(False),  # type: ignore[attr-defined]
            StudentJob.switched_at.is_not(None),  # type: ignore[union-attr]
        )
    )
    assert int((await db_session.exec(switched)).one()) > 0  # 换过主岗位，原岗位留了记录


@pytest.mark.asyncio
async def test_demo_seed_projects(db_session) -> None:
    """项目演示数据：关卡顺序连续；已发布项目的权重合计必须是 100，草稿故意没配平。"""
    await RoleRepository(db_session).create({"role_code": "TEACHER", "role_name": "教师"})
    await run_demo_seed(db_session)

    projects = TrainingProjectRepository(db_session)
    modules = ProjectModuleRepository(db_session)
    for item in PROJECTS:
        project = await projects.by_name(item["project_name"])
        assert project is not None
        assert project.status == item["status"]

        items = await modules.list_of_project(project.id)
        assert len(items) == len(item["modules"])
        assert [module.stage_no for module in items] == list(range(1, len(items) + 1))

        total = sum((module.weight for module in items), Decimal(0))
        expected = sum(Decimal(str(weight)) for _, weight, _ in item["modules"])
        assert total == expected
        assert (total == Decimal(100)) is (item["status"] == "PUBLISHED")

    # 项目所需技能：对齐岗位技能，且都是模块库/技能树里真实存在的节点
    project_skills = ProjectSkillRepository(db_session)
    for project_name, node_names in PROJECT_SKILLS.items():
        project = await projects.by_name(project_name)
        assert project is not None
        nodes = await project_skills.list_nodes_of_project(project.id)
        assert {node.node_name for node in nodes} == set(node_names)


@pytest.mark.asyncio
async def test_demo_seed_stage_items(db_session) -> None:
    """填写引导子标题：模板不预设，全部由教师在各项目里手填；同一关卡跨项目不一样。"""
    await RoleRepository(db_session).create({"role_code": "TEACHER", "role_name": "教师"})
    await run_demo_seed(db_session)

    projects = TrainingProjectRepository(db_session)
    modules = ProjectModuleRepository(db_session)
    templates = StageTemplateRepository(db_session)

    # 演示项目的每个关卡都要有教师填好的子标题
    for project_item in PROJECTS:
        project = await projects.by_name(project_item["project_name"])
        assert project is not None
        items = await modules.list_of_project(project.id)
        assert len(items) == len(project_item["modules"])
        for module, (stage_name, _, _) in zip(items, project_item["modules"], strict=True):
            expected = PROJECT_MODULE_ITEMS[(project_item["project_name"], stage_name)]
            assert [item["title"] for item in module.items_json] == [item["title"] for item in expected]

    # 同一个"需求分析"模板，在检测类和分类类项目里的子标题不同
    requirement_template = await templates.by_name("需求分析")
    assert requirement_template is not None
    first = await projects.by_name("工业缺陷检测实训")
    second = await projects.by_name("表面缺陷分类进阶")
    assert first is not None and second is not None
    first_module = await modules.by_template(first.id, requirement_template.id)
    second_module = await modules.by_template(second.id, requirement_template.id)
    assert first_module is not None and second_module is not None
    assert [item["title"] for item in first_module.items_json] != [
        item["title"] for item in second_module.items_json
    ]


@pytest.mark.asyncio
async def test_demo_seed_project_files(db_session) -> None:
    """项目附件演示数据：每个项目都有报告模板，文件台账与磁盘文件都在。"""
    from app.crud.project import ProjectFileRepository
    from app.services import storage

    await RoleRepository(db_session).create({"role_code": "TEACHER", "role_name": "教师"})
    await run_demo_seed(db_session)

    files = ProjectFileRepository(db_session)
    projects = TrainingProjectRepository(db_session)
    for project_name, expected in PROJECT_FILES.items():
        project = await projects.by_name(project_name)
        assert project is not None
        links = await files.list_of_project(project.id)
        assert [(link.file_kind, link.title) for link in links] == [
            (item["file_kind"], item["title"]) for item in expected
        ]
        # 报告模板必须有
        assert any(link.file_kind == "REPORT_TEMPLATE" for link in links)

    # 抽查一条：对象真实落在对象存储里，且大小与台账一致
    sample = await files.list_of_project((await projects.by_name("工业缺陷检测实训")).id)
    from app.crud.attempt import FileAssetRepository

    asset = await FileAssetRepository(db_session).get(sample[0].file_asset_id)
    assert asset is not None
    assert len(storage.read_bytes(asset.bucket, asset.object_key)) == asset.size_bytes


@pytest.mark.asyncio
async def test_demo_seed_attempts_and_skill_progress(db_session) -> None:
    """学生项目记录/提交/评审/技能点：三种进度状态都有，技能进度按公式算出来。"""
    await RoleRepository(db_session).create({"role_code": "TEACHER", "role_name": "教师"})
    await RoleRepository(db_session).create({"role_code": "STUDENT", "role_name": "学生"})
    await run_demo_seed(db_session)

    statuses = select(StudentProject.status, func.count()).group_by(StudentProject.status)
    counted = {status: int(count) for status, count in (await db_session.exec(statuses)).all()}
    assert counted["COMPLETED"] > 0
    assert counted["IN_PROGRESS"] > 0
    assert counted["SUBMITTED"] == 1

    # 已完成的项目：分数来自评审，进度 100
    completed = (
        await db_session.exec(select(StudentProject).where(StudentProject.status == "COMPLETED").limit(1))
    ).first()
    assert completed is not None
    assert completed.completed_at is not None
    assert str(completed.completed_score) in {str(Decimal(score)) for score in DEMO_SCORES} or Decimal(
        str(completed.completed_score)
    ) in {Decimal(score) for score in DEMO_SCORES}
    assert Decimal(str(completed.progress)) == Decimal(100)

    # 技能点：只看进度（0 / 中间值 / 100 都出现了），达标时间跟着进度走
    full_stmt = select(StudentSkill).where(StudentSkill.progress >= 100).limit(1)
    partial_stmt = select(StudentSkill).where(StudentSkill.progress > 0, StudentSkill.progress < 100).limit(1)
    zero_stmt = select(StudentSkill).where(StudentSkill.progress == 0).limit(1)
    full = (await db_session.exec(full_stmt)).first()
    partial = (await db_session.exec(partial_stmt)).first()
    zero = (await db_session.exec(zero_stmt)).first()
    assert full is not None and Decimal(str(full.progress)) == Decimal(100)
    assert full.mastered_at is not None
    assert partial is not None and partial.activated_at is not None and partial.mastered_at is None
    assert zero is not None and zero.activated_at is None and zero.mastered_at is None

    # 异议案例：AI 已判通过、学生留言提异议、状态停在待教师复核
    objected = (
        await db_session.exec(select(ProjectSubmission).where(ProjectSubmission.status == "PENDING_REVIEW"))
    ).first()
    assert objected is not None
    assert objected.objection_reason == DEMO_OBJECTION
    reviews = await ReviewRecordRepository(db_session).list_of_submission(objected.id)
    assert [review.review_kind for review in reviews] == ["AI"]
