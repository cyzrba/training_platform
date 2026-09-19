"""「我的实训」清单测试：自己挑的 ∪ 老师点名必修，两者互不覆盖。

口径（见 app/services/student_overview.py::my_projects）：
- 清单表 ``student_project_pick`` 只记"学生主动挑的"；必修是任务实时算的，不写表；
- 一个项目在列表里只有一条，``picked`` / ``is_required`` / ``sources`` 说明它为什么在这里；
- 排序：必修置顶 → 学生自定义 ``sort_no`` → 加入时间新的在前。
"""

import httpx
import pytest

from tests import helpers

MY_PROJECTS = "/api/students/{student_id}/my-projects"
TASKS = "/api/publish-tasks"
STAGE_TEMPLATES = "/api/stage-templates"


async def _student(client: httpx.AsyncClient, user_no: str = "2028001") -> dict:
    response = await client.post(
        "/api/users", json={"user_no": user_no, "real_name": "张三", "user_type": "STUDENT"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _template(client: httpx.AsyncClient, name: str = "需求分析") -> dict:
    listed = (await client.get(STAGE_TEMPLATES, params={"keyword": name})).json()
    existing = next((item for item in listed["items"] if item["stage_name"] == name), None)
    if existing is not None:
        return existing
    return (await client.post(STAGE_TEMPLATES, json={"stage_name": name})).json()


async def _project(client: httpx.AsyncClient, name: str, *, publish: bool = True) -> dict:
    """建一个单关卡项目；publish=True 时置为 PUBLISHED（= 学生可见）。"""
    template = await _template(client)
    project = (
        await client.post("/api/projects", json={"project_name": name, "project_level": "BASIC"})
    ).json()
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": template["id"], "weight": 100},
    )
    if publish:
        patched = await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
        assert patched.json()["status"] == "PUBLISHED", patched.text
    return project


async def _mine(client: httpx.AsyncClient, student_id: int) -> list[dict]:
    return (await client.get(MY_PROJECTS.format(student_id=student_id))).json()


@pytest.mark.asyncio
async def test_add_remove_and_idempotency(client: httpx.AsyncClient) -> None:
    """加入 / 重复加入 / 移除 / 重复移除都幂等；只能加已发布项目。"""
    student = await _student(client)
    first = await _project(client, "项目甲")
    second = await _project(client, "项目乙")

    assert await _mine(client, student["id"]) == []

    added = await client.post(
        MY_PROJECTS.format(student_id=student["id"]), json={"project_ids": [first["id"], second["id"]]}
    )
    assert added.status_code == 201, added.text
    items = added.json()
    # 默认排序：加入时间新的在前（同一次批量加入时后一个更晚，所以排在前面）
    assert [item["project_id"] for item in items] == [second["id"], first["id"]]
    assert all(item["picked"] and item["sources"] == ["SELF"] for item in items)
    assert items[0]["picked_at"] is not None

    # 重复加入：不报错、不新增（HTTP 200）
    again = await client.post(
        MY_PROJECTS.format(student_id=student["id"]), json={"project_ids": [first["id"]]}
    )
    assert again.status_code == 200, again.text
    assert [item["project_id"] for item in again.json()] == [second["id"], first["id"]]

    # 移除：列表里消失；再移除一次同样 200
    removed = await client.delete(f"{MY_PROJECTS.format(student_id=student['id'])}/{first['id']}")
    assert removed.status_code == 200 and "已从我的实训移除" in removed.json()["message"]
    assert [item["project_id"] for item in await _mine(client, student["id"])] == [second["id"]]
    assert (
        await client.delete(f"{MY_PROJECTS.format(student_id=student['id'])}/{first['id']}")
    ).status_code == 200

    # 草稿项目加不进来；不存在的项目报错
    draft = await _project(client, "草稿项目", publish=False)
    blocked = await client.post(
        MY_PROJECTS.format(student_id=student["id"]), json={"project_ids": [draft["id"]]}
    )
    assert blocked.json()["code"] == 422
    assert "还没发布" in blocked.json()["msg"]
    missing = await client.post(MY_PROJECTS.format(student_id=student["id"]), json={"project_ids": [9999]})
    assert missing.json()["code"] == 422

    # 学生不存在
    assert (await client.get(MY_PROJECTS.format(student_id=999))).json()["code"] == 422


@pytest.mark.asyncio
async def test_required_project_joins_my_list_without_pick(client: httpx.AsyncClient) -> None:
    """老师点名的必修项目会出现在「我的实训」里，但 picked=false（学生没自己加过）。"""
    student = await _student(client, "2028002")
    class_id = await helpers.enroll(client, "2028002")
    project = await _project(client, "必修项目")

    await helpers.publish(client, [project["id"]], class_id_=class_id, title="第 3 周任务")
    items = await _mine(client, student["id"])
    assert [item["project_id"] for item in items] == [project["id"]]
    assert items[0]["is_required"] is True and items[0]["picked"] is False
    assert items[0]["sources"] == ["TEACHER"]
    assert items[0]["required_task_titles"] == ["第 3 周任务"]

    # 学生主动加一遍 → 两个来源叠加，仍然是同一条项目
    await client.post(MY_PROJECTS.format(student_id=student["id"]), json={"project_ids": [project["id"]]})
    items = await _mine(client, student["id"])
    assert len(items) == 1
    assert items[0]["picked"] is True and items[0]["is_required"] is True
    assert items[0]["sources"] == ["SELF", "TEACHER"]

    # 撤回任务：自己加过的仍然留着（回到"只有自己挑的"）
    tasks = (await client.get(f"/api/students/{student['id']}/tasks")).json()
    assert (await client.post(f"{TASKS}/{tasks[0]['id']}/cancel")).status_code == 200
    items = await _mine(client, student["id"])
    assert [item["project_id"] for item in items] == [project["id"]]
    assert items[0]["picked"] is True and items[0]["is_required"] is False
    assert items[0]["sources"] == ["SELF"]


@pytest.mark.asyncio
async def test_removing_required_project_keeps_it_in_list(client: httpx.AsyncClient) -> None:
    """必修项目移出后仍在列表里（必修是任务实时算的），接口给出提示。"""
    student = await _student(client, "2028003")
    class_id = await helpers.enroll(client, "2028003")
    project = await _project(client, "必修项目")
    await helpers.publish(client, [project["id"]], class_id_=class_id, title="必修任务")

    removed = await client.delete(f"{MY_PROJECTS.format(student_id=student['id'])}/{project['id']}")
    assert removed.status_code == 200
    assert "仍会出现在列表里" in removed.json()["message"]
    items = await _mine(client, student["id"])
    assert [item["project_id"] for item in items] == [project["id"]]
    assert items[0]["picked"] is False and items[0]["is_required"] is True

    # 任务撤回后才真正从列表里消失
    task = (await client.get(f"/api/students/{student['id']}/tasks")).json()[0]
    await client.post(f"{TASKS}/{task['id']}/cancel")
    assert await _mine(client, student["id"]) == []


@pytest.mark.asyncio
async def test_unpublished_project_disappears_but_record_kept(client: httpx.AsyncClient) -> None:
    """项目下架后不在「我的实训」里出现；重新上架自动回来（清单记录没被删）。"""
    student = await _student(client, "2028004")
    project = await _project(client, "可下架项目")
    await client.post(MY_PROJECTS.format(student_id=student["id"]), json={"project_ids": [project["id"]]})
    assert [item["project_id"] for item in await _mine(client, student["id"])] == [project["id"]]

    await client.patch(f"/api/projects/{project['id']}", json={"status": "OFF_SHELF"})
    assert await _mine(client, student["id"]) == []

    await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    items = await _mine(client, student["id"])
    assert [item["project_id"] for item in items] == [project["id"]]
    assert items[0]["picked"] is True


@pytest.mark.asyncio
async def test_reorder_and_required_pinned(client: httpx.AsyncClient) -> None:
    """排序：必修置顶；其余按学生自定义 sort_no（覆盖式），没排过的按加入时间新的在前。"""
    student = await _student(client, "2028005")
    class_id = await helpers.enroll(client, "2028005")
    first = await _project(client, "项目甲")
    second = await _project(client, "项目乙")
    third = await _project(client, "项目丙")
    await client.post(
        MY_PROJECTS.format(student_id=student["id"]),
        json={"project_ids": [first["id"], second["id"], third["id"]]},
    )
    # 默认（都没排过）：后加入的在前
    items = await _mine(client, student["id"])
    assert [item["project_id"] for item in items] == [third["id"], second["id"], first["id"]]

    reordered = await client.patch(
        f"{MY_PROJECTS.format(student_id=student['id'])}/order",
        json={"project_ids": [first["id"], third["id"], second["id"]]},
    )
    assert reordered.status_code == 200, reordered.text
    items = reordered.json()
    assert [item["project_id"] for item in items] == [first["id"], third["id"], second["id"]]
    assert [item["sort_no"] for item in items] == [1, 2, 3]

    # 没加进来的项目不能排序
    other = await _project(client, "项目丁")
    blocked = await client.patch(
        f"{MY_PROJECTS.format(student_id=student['id'])}/order",
        json={"project_ids": [first["id"], other["id"]]},
    )
    assert blocked.json()["code"] == 422
    assert "不在你的「我的实训」里" in blocked.json()["msg"]

    # 必修置顶：老师点名"项目乙"后，它排在自己挑的项目前面
    await helpers.publish(client, [second["id"]], class_id_=class_id, title="必修任务")
    items = await _mine(client, student["id"])
    assert items[0]["project_id"] == second["id"]
    assert items[0]["is_required"] is True and items[0]["picked"] is True
    assert [item["project_id"] for item in items[1:]] == [first["id"], third["id"]]


@pytest.mark.asyncio
async def test_student_project_library_marks_picked(client: httpx.AsyncClient) -> None:
    """项目库列表也带 picked / sources，前端能直接显示"已加入我的实训"。"""
    student = await _student(client, "2028006")
    picked = await _project(client, "已加入项目")
    other = await _project(client, "没加入项目")
    await client.post(MY_PROJECTS.format(student_id=student["id"]), json={"project_ids": [picked["id"]]})

    library = (await client.get(f"/api/students/{student['id']}/training-projects")).json()
    by_id = {item["project_id"]: item for item in library}
    assert by_id[picked["id"]]["picked"] is True and by_id[picked["id"]]["sources"] == ["SELF"]
    assert by_id[other["id"]]["picked"] is False and by_id[other["id"]]["sources"] == []

    # 项目详情页同样带来源字段
    detail = (await client.get(f"/api/students/{student['id']}/projects/{picked['id']}")).json()
    assert detail["picked"] is True and detail["picked_at"] is not None
