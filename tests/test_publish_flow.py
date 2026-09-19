"""任务下发链路测试：创建 / 定时 / 撤回 / 必修标记 / 草稿项目拦截。

口径（docs/方案设计.md）：项目 ``status = 'PUBLISHED'`` 即对全体学生开放（能看能做）；
任务 = **必修**，只把任务里的项目标成"老师点名要求完成"，不再决定可见性；岗位范围只用于
筛项目，不筛学生。
"""

from datetime import timedelta

import httpx
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import now
from app.models.notification import Notification, OperationLog
from app.models.publish import PublishTask
from app.services import publish as publish_service
from tests import helpers

TASKS = "/api/publish-tasks"
CLASSES = "/api/classes"
STAGE_TEMPLATES = "/api/stage-templates"


# --------------------------------------------------------------------- 造数


async def _student(client: httpx.AsyncClient, user_no: str, name: str = "张三") -> dict:
    response = await client.post(
        "/api/users", json={"user_no": user_no, "real_name": name, "user_type": "STUDENT"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _project(
    client: httpx.AsyncClient,
    name: str,
    *,
    level: str = "BASIC",
    publish: bool = True,
    job_id: int | None = None,
) -> dict:
    """建一个单关卡项目；publish=True 时把项目置为 PUBLISHED（= 编辑完成可被任务引用）。"""
    template = await _template(client, "需求分析")
    project = (
        await client.post(
            "/api/projects",
            json={"project_name": name, "project_level": level, "job_id": job_id},
        )
    ).json()
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": template["id"], "weight": 100},
    )
    if publish:
        patched = await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
        assert patched.json()["status"] == "PUBLISHED", patched.text
    return project


async def _template(client: httpx.AsyncClient, name: str) -> dict:
    """模块库按名称唯一：多个项目共用同一个关卡模板。"""
    listed = (await client.get(STAGE_TEMPLATES, params={"keyword": name})).json()
    existing = next((item for item in listed["items"] if item["stage_name"] == name), None)
    if existing is not None:
        return existing
    return (await client.post(STAGE_TEMPLATES, json={"stage_name": name})).json()


async def _job(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/jobs", json={"job_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _skill_node(client: httpx.AsyncClient, name: str = "图像基础") -> dict:
    trees = (await client.get("/api/skill-trees")).json()
    tree = (
        trees["items"][0]
        if trees["total"]
        else (await client.post("/api/skill-trees", json={"tree_name": "视觉系"})).json()
    )
    return (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_name": name},
        )
    ).json()


async def _complete_project(client: httpx.AsyncClient, student_id: int, project_id: int) -> None:
    """把项目做完并让教师定稿通过（走真实链路：作答 → 提交 → 评审 PASS）。"""
    attempt = (await client.post(f"/api/students/{student_id}/projects/{project_id}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答"}
        )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()
    await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={"review_kind": "TEACHER", "status": "FINAL", "conclusion": "PASS", "total_score": 90},
    )


async def _skill_progress(client: httpx.AsyncClient, student_id: int, skill_node_id: int) -> float:
    """学生某个技能点的进度；没有记录（还没做过相关项目）按 0 处理。"""
    rows = (await client.get(f"/api/students/{student_id}/skills")).json()
    row = next((item for item in rows if item["skill_node_id"] == skill_node_id), None)
    return float(row["progress"]) if row else 0.0


async def _tasks_of(client: httpx.AsyncClient, student_id: int) -> list[dict]:
    return (await client.get(f"/api/students/{student_id}/tasks")).json()


async def _student_projects(client: httpx.AsyncClient, student_id: int) -> list[dict]:
    """学生端的实训项目列表（口径：全部已发布项目，与任务无关）。"""
    return (await client.get(f"/api/students/{student_id}/training-projects")).json()


async def _visible_project_ids(client: httpx.AsyncClient, student_id: int) -> list[int]:
    return [item["project_id"] for item in await _student_projects(client, student_id)]


async def _required_project_ids(client: httpx.AsyncClient, student_id: int) -> list[int]:
    """学生端带「必修」标记的项目（= 老师发任务点名要求做的）。"""
    return [item["project_id"] for item in await _student_projects(client, student_id) if item["is_required"]]


async def _notifications(db: AsyncSession, *, biz_id: int) -> list[Notification]:
    stmt = select(Notification).where(Notification.biz_type == "TASK_PUBLISH", Notification.biz_id == biz_id)
    return list((await db.exec(stmt)).all())


# --------------------------------------------------------------------- 用例


@pytest.mark.asyncio
async def test_immediate_task_marks_project_required_and_notifies(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """即时发布：任务立刻生效，目标学生的这个项目被标成「必修」并收到 TASK_PUBLISH 通知。"""
    student = await _student(client, "2026001")
    class_id = await helpers.enroll(client, "2026001")
    project = await _project(client, "基础实训项目")

    created = await client.post(
        TASKS,
        json={
            "title": "第 3 周 · 基础实训",
            "description": "7 天内完成",
            "project_level": "BASIC",
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "project_ids": [project["id"]],
            "publish_mode": "IMMEDIATE",
            "deadline_at": (now() + timedelta(days=7)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "PUBLISHED" and task["published_at"] is not None
    assert task["project_count"] == 1 and task["target_count"] == 1
    assert task["student_count"] == 1
    assert [item["project_id"] for item in task["projects"]] == [project["id"]]
    assert task["targets"][0]["class_name"] == helpers.DEFAULT_CLASS

    # 学生侧：任务出现在「我的任务」，项目在实训项目列表里（发布即可见，任务只加必修标记）
    assert [item["title"] for item in await _tasks_of(client, student["id"])] == ["第 3 周 · 基础实训"]
    assert await _visible_project_ids(client, student["id"]) == [project["id"]]
    assert await _required_project_ids(client, student["id"]) == [project["id"]]
    listed = (await _student_projects(client, student["id"]))[0]
    assert listed["required_task_titles"] == ["第 3 周 · 基础实训"]
    assert listed["required_deadline_at"] is not None

    # 通知与审计
    notifications = await _notifications(db_session, biz_id=task["id"])
    assert [item.recipient_user_id for item in notifications] == [student["id"]]
    assert notifications[0].title.startswith("新任务")
    logs = list(
        (
            await db_session.exec(
                select(OperationLog).where(
                    OperationLog.module == "PUBLISH", OperationLog.target_id == task["id"]
                )
            )
        ).all()
    )
    assert [log.action for log in logs] == ["PUBLISH"]


@pytest.mark.asyncio
async def test_scheduled_task_marks_required_only_after_dispatch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """定时发布：创建后不算必修（项目本身照样能做），到点由调度翻成 PUBLISHED 才标必修。"""
    student = await _student(client, "2026002")
    class_id = await helpers.enroll(client, "2026002")
    project = await _project(client, "定时发布项目")

    created = await client.post(
        TASKS,
        json={
            "title": "下周开课",
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "project_ids": [project["id"]],
            "publish_mode": "SCHEDULED",
            "scheduled_at": (now() + timedelta(hours=2)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "PENDING" and task["published_at"] is None
    # 项目已发布 → 学生现在就能看到 / 能做，只是还没被点名成必修
    assert await _visible_project_ids(client, student["id"]) == [project["id"]]
    assert await _required_project_ids(client, student["id"]) == []
    assert await _tasks_of(client, student["id"]) == []

    # 还没到点：dry-run 与真实调度都不该动它
    assert await publish_service.dispatch_due_tasks(db_session, at=now(), dry_run=True) == []
    assert await publish_service.dispatch_due_tasks(db_session, at=now()) == []

    # 到点：发布 + 通知
    results = await publish_service.dispatch_due_tasks(db_session, at=now() + timedelta(hours=3))
    assert [(item["task"].id, item["published"]) for item in results] == [(task["id"], True)]
    assert await _required_project_ids(client, student["id"]) == [project["id"]]
    assert len(await _notifications(db_session, biz_id=task["id"])) == 1

    # 再跑一次不会重复发布、也不会重复通知
    assert await publish_service.dispatch_due_tasks(db_session, at=now() + timedelta(hours=4)) == []
    assert len(await _notifications(db_session, biz_id=task["id"])) == 1


@pytest.mark.asyncio
async def test_group_target_only_marks_that_group_required(client: httpx.AsyncClient) -> None:
    """目标分组：项目对全班都开放，但只有该组学生被点名为必修。"""
    first = await _student(client, "2026011", "第一组学生")
    second = await _student(client, "2026012", "第二组学生")
    class_id = await helpers.class_id(client)
    await helpers.enroll(client, "2026011", class_id_=class_id, group_no=1)
    await helpers.enroll(client, "2026012", class_id_=class_id, group_no=2)
    project = await _project(client, "分组任务项目")
    groups = (await client.get(f"{CLASSES}/{class_id}/groups")).json()
    second_group = next(item for item in groups if item["group_no"] == 2)

    created = await client.post(
        TASKS,
        json={
            "title": "第二组专项",
            "targets": [{"class_id": class_id, "target_type": "GROUP", "group_id": second_group["id"]}],
            "project_ids": [project["id"]],
            "publish_mode": "IMMEDIATE",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["student_count"] == 1

    # 两个人都在实训项目列表里看到它，但只有第二组带必修标记
    assert await _visible_project_ids(client, second["id"]) == [project["id"]]
    assert await _visible_project_ids(client, first["id"]) == [project["id"]]
    assert await _required_project_ids(client, second["id"]) == [project["id"]]
    assert await _required_project_ids(client, first["id"]) == []

    # 全班任务补上以后，两个学生都是必修
    await helpers.publish(client, [project["id"]], class_id_=class_id, title="全班补发")
    assert await _required_project_ids(client, first["id"]) == [project["id"]]
    assert await _required_project_ids(client, second["id"]) == [project["id"]]


@pytest.mark.asyncio
async def test_draft_project_cannot_be_published(client: httpx.AsyncClient) -> None:
    """草稿项目不能发布任务：手工指定被拒，按岗位 × 层级自动筛也筛不到它。"""
    class_id = await helpers.class_id(client)
    draft = await _project(client, "草稿项目", publish=False)
    published = await _project(client, "已发布项目")

    blocked = await client.post(
        TASKS,
        json={
            "title": "包含草稿的任务",
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "project_ids": [published["id"], draft["id"]],
            "publish_mode": "IMMEDIATE",
        },
    )
    assert blocked.json()["code"] == 422
    assert "草稿" in blocked.json()["msg"] and "草稿项目" in blocked.json()["msg"]

    # 自动筛选只认已发布项目
    created = await client.post(
        TASKS,
        json={
            "title": "基础层级全发",
            "project_level": "BASIC",
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "publish_mode": "IMMEDIATE",
        },
    )
    assert created.status_code == 201, created.text
    assert [item["project_id"] for item in created.json()["projects"]] == [published["id"]]


@pytest.mark.asyncio
async def test_job_scope_filters_projects(client: httpx.AsyncClient) -> None:
    """岗位范围只用于筛项目：命中范围内的项目进任务，范围外的手工指定会被拒绝。"""
    class_id = await helpers.class_id(client)
    vision = await _job(client, "工业视觉工程师")
    data_job = await _job(client, "数据标注专员")
    vision_project = await _project(client, "视觉岗位项目", job_id=vision["id"])
    data_project = await _project(client, "数据岗位项目", job_id=data_job["id"])

    listed = (await client.get(f"{TASKS}/publishable-projects", params={"job_ids": vision["id"]})).json()
    assert [item["id"] for item in listed] == [vision_project["id"]]
    assert listed[0]["job_name"] == "工业视觉工程师"

    created = await client.post(
        TASKS,
        json={
            "title": "视觉岗位任务",
            "job_ids": [vision["id"]],
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "publish_mode": "IMMEDIATE",
        },
    )
    assert created.status_code == 201, created.text
    assert [item["job_name"] for item in created.json()["jobs"]] == ["工业视觉工程师"]
    assert [item["project_id"] for item in created.json()["projects"]] == [vision_project["id"]]

    # 手工把范围外的项目塞进来 → 拒绝
    blocked = await client.post(
        TASKS,
        json={
            "title": "越界任务",
            "job_ids": [vision["id"]],
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "project_ids": [data_project["id"]],
            "publish_mode": "IMMEDIATE",
        },
    )
    assert blocked.json()["code"] == 422
    assert "岗位范围" in blocked.json()["msg"]


@pytest.mark.asyncio
async def test_cancel_task_clears_required_flag(client: httpx.AsyncClient) -> None:
    """撤回任务：必修标记与任务列表立刻消失；项目本身照样能看能做（记录不删）。"""
    student = await _student(client, "2026003")
    class_id = await helpers.enroll(client, "2026003")
    project = await _project(client, "撤回演示项目")
    created = await helpers.publish(client, [project["id"]], class_id_=class_id, title="待撤回任务")
    assert await _required_project_ids(client, student["id"]) == [project["id"]]

    cancelled = await client.post(f"{TASKS}/{created['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert await _visible_project_ids(client, student["id"]) == [project["id"]]
    assert await _required_project_ids(client, student["id"]) == []
    assert await _tasks_of(client, student["id"]) == []

    # 撤回后不能重新发布（要重新发就新建任务）
    again = await client.post(f"{TASKS}/{created['id']}/publish")
    assert again.json()["code"] == 422


@pytest.mark.asyncio
async def test_published_task_is_locked_but_deadline_can_change(client: httpx.AsyncClient) -> None:
    """已发布任务锁定目标 / 项目，只允许改说明与截止时间。"""
    class_id = await helpers.class_id(client)
    project = await _project(client, "锁定演示项目")
    task = await helpers.publish(client, [project["id"]], class_id_=class_id, title="锁定任务")

    locked = await client.patch(
        f"{TASKS}/{task['id']}",
        json={"targets": [{"class_id": class_id, "target_type": "CLASS"}], "title": "偷偷改名"},
    )
    assert locked.json()["code"] == 422
    assert "锁定" in locked.json()["msg"]

    deadline = (now() + timedelta(days=3)).isoformat()
    patched = await client.patch(f"{TASKS}/{task['id']}", json={"deadline_at": deadline})
    assert patched.status_code == 200, patched.text
    assert patched.json()["deadline_at"].startswith(deadline[:16])


@pytest.mark.asyncio
async def test_student_can_start_any_published_project(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """没发任务也能做：已发布项目对所有学生开放（闯关、详情都不再被任务卡住）。

    草稿 / 已下架项目仍然拦（学生看不到也不该能做）。
    """
    student = await _student(client, "2026004")
    class_id = await helpers.enroll(client, "2026004")
    project = await _project(client, "别人的项目")

    # 没发任何任务，也能开始闯关、能看详情
    started = await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")
    assert started.status_code == 201, started.text

    detail = await client.get(f"/api/students/{student['id']}/projects/{project['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_required"] is False

    # 另一个班的学生同样能看到（可见性与班级 / 任务无关）
    other_class = (await client.post(CLASSES, json={"class_name": "另一个班"})).json()
    other = await _student(client, "2026005", "别的班学生")
    await helpers.enroll(client, "2026005", class_id_=other_class["id"])
    await helpers.publish(client, [project["id"]], class_id_=other_class["id"], title="别的班任务")
    assert await _visible_project_ids(client, student["id"]) == [project["id"]]
    assert await _visible_project_ids(client, other["id"]) == [project["id"]]
    # 必修只落在别的班
    assert await _required_project_ids(client, student["id"]) == []
    assert await _required_project_ids(client, other["id"]) == [project["id"]]

    # 草稿项目不开放：闯关 / 详情都拦
    draft = await _project(client, "草稿项目", publish=False)
    blocked = await client.post(f"/api/students/{student['id']}/projects/{draft['id']}/start")
    assert blocked.json()["code"] == 422
    assert "还没发布" in blocked.json()["msg"]
    draft_detail = await client.get(f"/api/students/{student['id']}/projects/{draft['id']}")
    assert draft_detail.json()["code"] == 422
    assert "不存在" in draft_detail.json()["msg"]

    assert await helpers.class_id(client) == class_id
    assert (await db_session.exec(select(PublishTask))).all()  # 任务确实落库了


@pytest.mark.asyncio
async def test_preview_and_list_and_delete(client: httpx.AsyncClient) -> None:
    """覆盖学生数预览、任务列表过滤、未发布任务的软删。"""
    await _student(client, "2026021")
    await _student(client, "2026022", "李四")
    class_id = await helpers.class_id(client)
    await helpers.enroll(client, "2026021", class_id_=class_id)
    await helpers.enroll(client, "2026022", class_id_=class_id)
    project = await _project(client, "预览演示项目")

    preview = await client.post(
        f"{TASKS}/preview-students",
        json={"targets": [{"class_id": class_id, "target_type": "CLASS"}]},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["student_count"] == 2
    assert preview.json()["class_count"] == 1 and preview.json()["group_count"] == 0

    scheduled = await client.post(
        TASKS,
        json={
            "title": "待发布任务",
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "project_ids": [project["id"]],
            "publish_mode": "SCHEDULED",
            "scheduled_at": (now() + timedelta(days=1)).isoformat(),
        },
    )
    scheduled_task = scheduled.json()
    assert scheduled_task["can_edit"] is True

    listed = (await client.get(TASKS, params={"status": "PENDING"})).json()
    assert [item["id"] for item in listed["items"]] == [scheduled_task["id"]]
    assert listed["items"][0]["student_count"] == 2

    # 未发布的任务可以删掉
    assert (await client.delete(f"{TASKS}/{scheduled_task['id']}")).status_code == 200
    assert (await client.get(f"{TASKS}/{scheduled_task['id']}")).json()["code"] == 422

    # 定时任务缺发布时间 → 拒绝
    invalid = await client.post(
        TASKS,
        json={
            "title": "缺时间的定时任务",
            "targets": [{"class_id": class_id, "target_type": "CLASS"}],
            "project_ids": [project["id"]],
            "publish_mode": "SCHEDULED",
        },
    )
    assert invalid.json()["code"] == 422
    assert "必须指定发布时间" in invalid.json()["msg"]

    # 目标分组不属于该班级 → 拒绝
    other_class = (await client.post(CLASSES, json={"class_name": "别的班"})).json()
    other_group = (
        await client.post(f"{CLASSES}/{other_class['id']}/groups", json={"group_name": "第一组"})
    ).json()
    mismatch = await client.post(
        TASKS,
        json={
            "title": "分组对不上",
            "targets": [{"class_id": class_id, "target_type": "GROUP", "group_id": other_group["id"]}],
            "project_ids": [project["id"]],
            "publish_mode": "IMMEDIATE",
        },
    )
    assert mismatch.json()["code"] == 422
    assert "不属于班级" in mismatch.json()["msg"]


@pytest.mark.asyncio
async def test_skill_progress_counts_all_published_projects(
    client: httpx.AsyncClient,
) -> None:
    """技能进度 = 已完成项目 ÷ **全部已发布**的关联项目（与任务无关）。

    分母只看项目的发布状态：学生端能看到全部已发布项目，所以练这个技能点的已发布项目都算数；
    发任务 / 撤回任务只影响"必修"标记，不再改分母。
    """
    student = await _student(client, "2026041")
    class_id = await helpers.enroll(client, "2026041")
    skill = await _skill_node(client)
    first = await _project(client, "技能项目甲")
    second = await _project(client, "技能项目乙")
    for project in (first, second):
        linked = await client.put(
            f"/api/projects/{project['id']}/skills", json={"skill_node_ids": [skill["id"]]}
        )
        assert linked.status_code in (200, 201), linked.text

    # 两个项目都还等着做：分母 2，进度 0
    await client.post(f"/api/students/{student['id']}/skills/recalculate")
    assert await _skill_progress(client, student["id"], skill["id"]) == 0.0

    # 完成甲（不需要任何任务）→ 1/2
    first_task = await helpers.publish(client, [first["id"]], class_id_=class_id, title="项目甲任务")
    await _complete_project(client, student["id"], first["id"])
    assert await _skill_progress(client, student["id"], skill["id"]) == 50.0

    # 乙也是必修 → 只是多一个必修标记，分母不变
    await helpers.publish(client, [second["id"]], class_id_=class_id, title="项目乙任务")
    assert await _skill_progress(client, student["id"], skill["id"]) == 50.0

    # 撤回甲的任务 → 必修标记没了，进度不动（分母仍看发布状态）
    cancelled = await client.post(f"{TASKS}/{first_task['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert await _skill_progress(client, student["id"], skill["id"]) == 50.0
    assert await _required_project_ids(client, student["id"]) == [second["id"]]

    # 完成乙 → 2/2
    await _complete_project(client, student["id"], second["id"])
    assert await _skill_progress(client, student["id"], skill["id"]) == 100.0

    # 项目下架 → 分母少一个 → 回到 1/1，手动重算与自动结果一致
    off_shelf = await client.patch(f"/api/projects/{second['id']}", json={"status": "OFF_SHELF"})
    assert off_shelf.status_code == 200, off_shelf.text
    assert await _skill_progress(client, student["id"], skill["id"]) == 100.0
    await client.patch(f"/api/projects/{first['id']}", json={"status": "OFF_SHELF"})
    await client.post(f"/api/students/{student['id']}/skills/recalculate")
    assert await _skill_progress(client, student["id"], skill["id"]) == 0.0


@pytest.mark.asyncio
async def test_deleted_class_only_clears_required_flag(client: httpx.AsyncClient) -> None:
    """班级被删除（软删）后：任务不再点名它的学生（必修标记消失），但项目照样能看能做。"""
    student = await _student(client, "2026051")
    class_id = await helpers.enroll(client, "2026051")
    project = await _project(client, "删班演示项目")
    await helpers.publish(client, [project["id"]], class_id_=class_id, title="发给 1 班的任务")
    assert await _required_project_ids(client, student["id"]) == [project["id"]]

    deleted = await client.delete(f"{CLASSES}/{class_id}")
    assert deleted.status_code == 200, deleted.text
    assert await _visible_project_ids(client, student["id"]) == [project["id"]]
    assert await _required_project_ids(client, student["id"]) == []
    assert await _tasks_of(client, student["id"]) == []

    # 在班记录与学生账号都还在（软删班级不删人），项目也照样可以开始闯关
    started = await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")
    assert started.status_code == 201, started.text
