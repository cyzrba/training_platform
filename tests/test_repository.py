"""仓储层与 ORM 落库验证（内存 SQLite，外键与部分索引均生效）。"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.crud import BaseRepository
from app.models.account import SysUser
from app.models.job_skill import Job, StudentJob
from app.models.project import ProjectModule, ProjectStageTemplate, TrainingProject
from app.schemas.base import PageParams


class SysUserRepository(BaseRepository[SysUser]):
    model = SysUser
    soft_delete = True


@pytest.mark.asyncio
async def test_create_read_and_soft_delete(db_session) -> None:
    repo = SysUserRepository(db_session)
    user = await repo.create(
        {
            "user_no": "2026001",
            "real_name": "张三",
            "user_type": "STUDENT",
            "major_name": "人工智能技术应用",
        }
    )
    assert user.id is not None
    assert user.status == "ACTIVE"  # 默认值生效
    assert user.created_at is not None  # 时间戳按本地时间存库

    fetched = await repo.get(user.id)
    assert fetched is not None and fetched.user_no == "2026001"

    page = await repo.list_page(PageParams(page=1, page_size=10))
    assert page.total == 1 and page.pages == 1

    await repo.remove(user)
    assert await repo.get(user.id) is None


@pytest.mark.asyncio
async def test_unique_constraint_enforced(db_session) -> None:
    repo = SysUserRepository(db_session)
    payload = {"user_no": "2026002", "real_name": "李四", "user_type": "STUDENT"}
    await repo.create(payload)
    with pytest.raises(IntegrityError):
        await repo.create(payload)


@pytest.mark.asyncio
async def test_check_constraint_and_project_module(db_session) -> None:
    """difficulty 超出 1~5 触发 CHECK；项目模块只能挑模块库里的模板。"""
    project = TrainingProject(project_name="工业视觉缺陷检测实训", project_level="BASIC", difficulty=3)
    db_session.add(project)
    await db_session.flush()

    first_template = ProjectStageTemplate(stage_key="REQUIREMENT_ANALYSIS", stage_name="需求分析")
    second_template = ProjectStageTemplate(stage_key="SOLUTION_DESIGN", stage_name="方案设计")
    db_session.add_all(
        [
            first_template,
            second_template,
        ]
    )
    await db_session.flush()

    db_session.add_all(
        [
            ProjectModule(
                project_id=project.id,
                template_id=first_template.id,
                stage_no=1,
            ),
            ProjectModule(project_id=project.id, template_id=second_template.id, stage_no=2),
        ]
    )
    await db_session.flush()

    # stage_no 在项目内唯一
    db_session.add(ProjectModule(project_id=project.id, template_id=second_template.id, stage_no=2))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    db_session.add(TrainingProject(project_name="难度非法", project_level="BASIC", difficulty=9))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_partial_unique_index_for_primary_job(db_session) -> None:
    """uk_student_job_primary：同一学生只能有一个主岗位，可保留多个非主岗位。"""
    student = SysUser(user_no="2026003", real_name="王五", user_type="STUDENT")
    first_job = Job(job_name="工业视觉工程师")
    second_job = Job(job_name="算法工程师")
    db_session.add_all([student, first_job, second_job])
    await db_session.flush()

    db_session.add_all(
        [
            StudentJob(student_id=student.id, job_id=first_job.id, is_primary=True),
            StudentJob(student_id=student.id, job_id=second_job.id, is_primary=False),
        ]
    )
    await db_session.flush()

    db_session.add(StudentJob(student_id=student.id, job_id=second_job.id, is_primary=True))
    with pytest.raises(IntegrityError):
        await db_session.flush()
