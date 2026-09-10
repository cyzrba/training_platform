"""仓储层与 ORM 落库验证（内存 SQLite）。"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.crud import BaseRepository
from app.models.a_account import SysUser
from app.models.d_project import ProjectModule, TrainingProject
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
    assert user.status == "ACTIVE"  # 数据库默认值生效
    assert user.created_at.tzinfo is not None  # TZDateTime 读回为 aware

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
async def test_check_constraint_and_nested_write(db_session) -> None:
    """difficulty 超出 1~5 触发 CHECK；项目与模块一次写入。"""
    project = TrainingProject(
        project_name="工业视觉缺陷检测实训",
        project_level="BASIC",
        difficulty=3,
        modules=[
            ProjectModule(module_name="需求分析", stage_no=1, stage_key="REQUIREMENT_ANALYSIS"),
            ProjectModule(module_name="自定义模块", stage_no=2),
        ],
    )
    db_session.add(project)
    await db_session.flush()
    assert project.id is not None
    assert len(project.modules) == 2

    bad = TrainingProject(project_name="难度非法", project_level="BASIC", difficulty=9)
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.flush()
