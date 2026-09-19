"""教师工作台接口测试：任教班级 / 分组明细 / 任务完成进度 / 平均完成率。"""

from datetime import timedelta

import httpx
import pytest

from app.core.time import now

CLASSES = "/api/classes"
TASKS = "/api/publish-tasks"
STAGE_TEMPLATES = "/api/stage-templates"
USERS = "/api/users"
OVERVIEW = "/api/teachers/{teacher_id}/classes"


async def _teacher(client: httpx.AsyncClient, user_no: str = "T9001", name: str = "王老师") -> dict:
    response = await client.post(USERS, json={"user_no": user_no, "real_name": name, "user_type": "TEACHER"})
    assert response.status_code == 201, response.text
    return response.json()


async def _student(client: httpx.AsyncClient, user_no: str, name: str) -> dict:
    response = await client.post(USERS, json={"user_no": user_no, "real_name": name, "user_type": "STUDENT"})
    assert response.status_code == 201, response.text
    return response.json()


async def _project(client: httpx.AsyncClient, name: str) -> dict:
    template = await _template(client, "需求分析")
    project = (
        await client.post("/api/projects", json={"project_name": name, "project_level": "BASIC"})
    ).json()
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": template["id"], "weight": 100},
    )
    published = await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    assert published.json()["status"] == "PUBLISHED", published.text
    return project


async def _template(client: httpx.AsyncClient, name: str) -> dict:
    """模块库按名称唯一：多个项目共用同一个关卡模板。"""
    listed = (await client.get(STAGE_TEMPLATES, params={"keyword": name})).json()
    existing = next((item for item in listed["items"] if item["stage_name"] == name), None)
    if existing is not None:
        return existing
    return (await client.post(STAGE_TEMPLATES, json={"stage_name": name})).json()


async def _add_student(
    client: httpx.AsyncClient, class_id: int, user_no: str, *, group_no: int | None = None
) -> dict:
    payload: dict = {"user_no": user_no}
    if group_no is not None:
        payload["group_no"] = group_no
    response = await client.post(f"{CLASSES}/{class_id}/students", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _complete_project(client: httpx.AsyncClient, student_id: int, project_id: int) -> None:
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


async def _make_class(client: httpx.AsyncClient, teacher_id: int, name: str) -> dict:
    response = await client.post(
        CLASSES, json={"class_name": name, "head_teacher_id": teacher_id, "group_count": 2}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_teacher_classes_overview(client: httpx.AsyncClient) -> None:
    """班级名 / 学生总数 / 分组明细 / 任务完成进度 / 平均完成率 都要对得上。"""
    teacher = await _teacher(client)
    other_teacher = await _teacher(client, "T9002", "李老师")
    first = await _student(client, "2026111", "张三")
    second = await _student(client, "2026112", "李四")
    third = await _student(client, "2026113", "王五")

    classroom = await _make_class(client, teacher["id"], "人工智能2401班")
    class_id = classroom["id"]
    groups = []
    for group_no, name in ((1, "第一组"), (2, "第二组")):
        created = await client.post(
            f"{CLASSES}/{class_id}/groups", json={"group_no": group_no, "group_name": name}
        )
        groups.append(created.json())
    await _add_student(client, class_id, "2026111", group_no=1)
    await _add_student(client, class_id, "2026112", group_no=2)
    await _add_student(client, class_id, "2026113")  # 故意不分组

    # 别的老师的班：不能出现在结果里
    await _make_class(client, other_teacher["id"], "别人的班")

    project_a = await _project(client, "缺陷检测实训")
    project_b = await _project(client, "表面缺陷分类进阶")

    task_all = (
        await client.post(
            TASKS,
            json={
                "title": "第 3 周 · 基础实训",
                "targets": [{"class_id": class_id, "target_type": "CLASS"}],
                "project_ids": [project_a["id"], project_b["id"]],
                "publish_mode": "IMMEDIATE",
                "deadline_at": (now() + timedelta(days=7)).isoformat(),
            },
        )
    ).json()
    task_group = (
        await client.post(
            TASKS,
            json={
                "title": "第一组专项",
                "targets": [{"class_id": class_id, "target_type": "GROUP", "group_id": groups[0]["id"]}],
                "project_ids": [project_a["id"]],
                "publish_mode": "IMMEDIATE",
            },
        )
    ).json()
    task_scheduled = (
        await client.post(
            TASKS,
            json={
                "title": "下周开课（定时）",
                "targets": [{"class_id": class_id, "target_type": "CLASS"}],
                "project_ids": [project_a["id"]],
                "publish_mode": "SCHEDULED",
                "scheduled_at": (now() + timedelta(days=3)).isoformat(),
            },
        )
    ).json()

    # 张三把两个项目都做完，李四只做了 project_a，王五没开始
    await _complete_project(client, first["id"], project_a["id"])
    await _complete_project(client, first["id"], project_b["id"])
    await _complete_project(client, second["id"], project_a["id"])

    overview = (await client.get(OVERVIEW.format(teacher_id=teacher["id"]))).json()
    assert overview["teacher_id"] == teacher["id"] and overview["teacher_name"] == "王老师"
    assert overview["class_count"] == 1 and overview["student_count"] == 3
    assert overview["task_count"] == 3  # 三条任务都命中这个班

    data = overview["classes"][0]
    assert data["class_id"] == class_id and data["class_name"] == "人工智能2401班"
    assert data["status"] == "ACTIVE" and data["grade_year"] is None
    assert data["student_count"] == 3 and data["group_count"] == 2 and data["ungrouped_count"] == 1

    # 分组情况：每组有哪些学生（id + 名字），以及未分组的人
    first_group, second_group = data["groups"]
    assert (first_group["group_no"], first_group["group_name"]) == (1, "第一组")
    assert first_group["member_count"] == 1
    assert [
        (member["student_id"], member["user_no"], member["real_name"]) for member in first_group["members"]
    ] == [(first["id"], "2026111", "张三")]
    assert [(member["student_id"], member["real_name"]) for member in second_group["members"]] == [
        (second["id"], "李四")
    ]
    assert [(member["student_id"], member["real_name"]) for member in data["ungrouped_students"]] == [
        (third["id"], "王五")
    ]

    tasks = {item["title"]: item for item in data["tasks"]}
    assert set(tasks) == {"第 3 周 · 基础实训", "第一组专项", "下周开课（定时）"}

    # 全班任务：3 人 × 2 项目 = 6 对，完成 3 对（张三 2 + 李四 1）→ 50%
    all_task = tasks["第 3 周 · 基础实训"]
    assert all_task["task_id"] == task_all["id"] and all_task["status"] == "PUBLISHED"
    assert all_task["project_count"] == 2
    assert all_task["project_names"] == ["缺陷检测实训", "表面缺陷分类进阶"]
    assert all_task["student_count"] == 3
    assert (all_task["total_count"], all_task["completed_count"]) == (6, 3)
    assert (all_task["in_progress_count"], all_task["not_started_count"]) == (0, 3)
    assert all_task["completion_rate"] == 50.0
    by_student = {item["user_no"]: item for item in all_task["students"]}
    assert by_student["2026111"]["completed_count"] == 2
    assert by_student["2026111"]["completion_rate"] == 100.0
    assert by_student["2026111"]["status"] == "COMPLETED"
    assert by_student["2026111"]["group_name"] == "第一组"
    assert by_student["2026112"]["status"] == "IN_PROGRESS"
    assert by_student["2026112"]["completion_rate"] == 50.0
    assert by_student["2026113"]["status"] == "NOT_STARTED"
    assert by_student["2026113"]["group_id"] is None

    # 分组任务只覆盖第一组：1 人 × 1 项目，张三已完成 → 100%
    group_task = tasks["第一组专项"]
    assert group_task["task_id"] == task_group["id"]
    assert group_task["student_count"] == 1
    assert (group_task["total_count"], group_task["completed_count"]) == (1, 1)
    assert group_task["completion_rate"] == 100.0
    assert [item["user_no"] for item in group_task["students"]] == ["2026111"]

    # 定时任务还没生效：列出来（effective=false），进度按"项目实际完成情况"算，但不参与平均
    scheduled_task = tasks["下周开课（定时）"]
    assert scheduled_task["task_id"] == task_scheduled["id"]
    assert scheduled_task["status"] == "PENDING" and scheduled_task["effective"] is False
    assert scheduled_task["completion_rate"] == 66.67  # 全班 3 人里 2 人已完成 project_a
    assert all_task["effective"] is True

    # 平均完成率 = 两条已发布任务完成率的平均 = (50 + 100) ÷ 2
    assert data["average_completion_rate"] == 75.0
    assert overview["average_completion_rate"] == 75.0  # 只有一个班


@pytest.mark.asyncio
async def test_teacher_classes_overview_edge_cases(client: httpx.AsyncClient) -> None:
    """非教师 / 不存在的用户直接报错；没有班级的教师返回空壳而不是 404。"""
    teacher = await _teacher(client, "T9010", "没有班的高老师")
    student = await _student(client, "2026121", "赵六")

    empty = (await client.get(OVERVIEW.format(teacher_id=teacher["id"]))).json()
    assert empty["class_count"] == 0 and empty["classes"] == []
    assert empty["average_completion_rate"] == 0.0

    not_teacher = await client.get(OVERVIEW.format(teacher_id=student["id"]))
    assert not_teacher.json()["code"] == 422
    assert "没有任教的班级" in not_teacher.json()["msg"]

    missing = await client.get(OVERVIEW.format(teacher_id=999))
    assert missing.json()["code"] == 422
    assert "不存在" in missing.json()["msg"]


@pytest.mark.asyncio
async def test_teacher_classes_can_skip_task_students(client: httpx.AsyncClient) -> None:
    """with_task_students=false 时不返回逐学生明细，汇总数字不变。"""
    teacher = await _teacher(client, "T9020", "省流量老师")
    await _student(client, "2026131", "钱七")
    classroom = await _make_class(client, teacher["id"], "大数据2301班")
    await _add_student(client, classroom["id"], "2026131")
    project = await _project(client, "成像系统搭建实训")
    await client.post(
        TASKS,
        json={
            "title": "成像实训",
            "targets": [{"class_id": classroom["id"], "target_type": "CLASS"}],
            "project_ids": [project["id"]],
            "publish_mode": "IMMEDIATE",
        },
    )

    full = (await client.get(OVERVIEW.format(teacher_id=teacher["id"]))).json()
    slim = (
        await client.get(OVERVIEW.format(teacher_id=teacher["id"]), params={"with_task_students": "false"})
    ).json()
    assert full["classes"][0]["tasks"][0]["students"] != []
    assert slim["classes"][0]["tasks"][0]["students"] == []
    for key in ("student_count", "total_count", "completed_count", "completion_rate"):
        assert slim["classes"][0]["tasks"][0][key] == full["classes"][0]["tasks"][0][key]
    assert slim["classes"][0]["average_completion_rate"] == full["classes"][0]["average_completion_rate"]
