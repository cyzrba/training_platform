"""跨班学生测试：一个学生同时在多个班级里时，任务会不会串味、必修标记与技能进度对不对。

覆盖三种"多班"形态：

1. 两个老师的班级（各自发各自的任务）；
2. 同一个老师的两个班级（任务不重复、必修不重复）；
3. 入班 / 换组 / 离班只改必修名单，不改技能分母（分母 = 全部已发布项目）。
"""

from datetime import timedelta

import httpx
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import now
from app.models.notification import Notification

CLASSES = "/api/classes"
TASKS = "/api/publish-tasks"
USERS = "/api/users"
STAGE_TEMPLATES = "/api/stage-templates"
OVERVIEW = "/api/teachers/{teacher_id}/classes"
SKILL_TREES = "/api/skill-trees"


# --------------------------------------------------------------------- 造数


async def _teacher(client: httpx.AsyncClient, user_no: str, name: str) -> dict:
    response = await client.post(USERS, json={"user_no": user_no, "real_name": name, "user_type": "TEACHER"})
    assert response.status_code == 201, response.text
    return response.json()


async def _student(client: httpx.AsyncClient, user_no: str, name: str) -> dict:
    response = await client.post(USERS, json={"user_no": user_no, "real_name": name, "user_type": "STUDENT"})
    assert response.status_code == 201, response.text
    return response.json()


async def _class(client: httpx.AsyncClient, name: str, teacher_id: int) -> dict:
    response = await client.post(
        CLASSES, json={"class_name": name, "head_teacher_id": teacher_id, "group_count": 2}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _groups(client: httpx.AsyncClient, class_id: int, count: int = 2) -> list[dict]:
    created = []
    for group_no in range(1, count + 1):
        response = await client.post(
            f"{CLASSES}/{class_id}/groups",
            json={"group_no": group_no, "group_name": f"第{group_no}组"},
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created


async def _enroll(
    client: httpx.AsyncClient, class_id: int, user_no: str, *, group_no: int | None = None
) -> dict:
    payload: dict = {"user_no": user_no}
    if group_no is not None:
        payload["group_no"] = group_no
    response = await client.post(f"{CLASSES}/{class_id}/students", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _skill_node(client: httpx.AsyncClient, name: str) -> dict:
    trees = (await client.get(SKILL_TREES)).json()
    tree = (
        trees["items"][0]
        if trees["total"]
        else (await client.post(SKILL_TREES, json={"tree_name": "视觉系"})).json()
    )
    response = await client.post(f"{SKILL_TREES}/{tree['id']}/nodes", json={"node_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _template(client: httpx.AsyncClient, name: str) -> dict:
    """模块库按名称唯一：多个项目共用同一个关卡模板。"""
    listed = (await client.get(STAGE_TEMPLATES, params={"keyword": name})).json()
    existing = next((item for item in listed["items"] if item["stage_name"] == name), None)
    if existing is not None:
        return existing
    return (await client.post(STAGE_TEMPLATES, json={"stage_name": name})).json()


async def _project(
    client: httpx.AsyncClient,
    name: str,
    *,
    skill_ids: tuple[int, ...] = (),
    job_id: int | None = None,
) -> dict:
    template = await _template(client, "需求分析")
    project = (
        await client.post(
            "/api/projects",
            json={"project_name": name, "project_level": "BASIC", "job_id": job_id},
        )
    ).json()
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": template["id"], "weight": 100},
    )
    if skill_ids:
        linked = await client.put(
            f"/api/projects/{project['id']}/skills", json={"skill_node_ids": list(skill_ids)}
        )
        assert linked.status_code in (200, 201), linked.text
    published = await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    assert published.json()["status"] == "PUBLISHED", published.text
    return project


async def _publish(
    client: httpx.AsyncClient,
    title: str,
    project_ids: list[int],
    targets: list[dict],
    *,
    mode: str = "IMMEDIATE",
    job_ids: list[int] | None = None,
) -> dict:
    payload: dict = {
        "title": title,
        "targets": targets,
        "project_ids": project_ids,
        "publish_mode": mode,
    }
    if job_ids:
        payload["job_ids"] = job_ids
    if mode == "SCHEDULED":
        payload["scheduled_at"] = (now() + timedelta(days=1)).isoformat()
    response = await client.post(TASKS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _complete(client: httpx.AsyncClient, student_id: int, project_id: int) -> None:
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


async def _student_projects(client: httpx.AsyncClient, student_id: int) -> list[dict]:
    """学生端实训项目列表（口径：全部已发布项目，与任务 / 班级无关）。"""
    return (await client.get(f"/api/students/{student_id}/training-projects")).json()


async def _visible_projects(client: httpx.AsyncClient, student_id: int) -> list[int]:
    listed = (await client.get(f"/api/students/{student_id}/training-projects")).json()
    return sorted(item["project_id"] for item in listed)


async def _required_projects(client: httpx.AsyncClient, student_id: int) -> list[int]:
    """带「必修」标记的项目（= 老师发任务点名要求做的）。"""
    return sorted(
        item["project_id"] for item in await _student_projects(client, student_id) if item["is_required"]
    )


async def _task_titles(client: httpx.AsyncClient, student_id: int) -> list[str]:
    tasks = (await client.get(f"/api/students/{student_id}/tasks")).json()
    return [item["title"] for item in tasks]


async def _progress(client: httpx.AsyncClient, student_id: int, skill_id: int) -> float:
    rows = (await client.get(f"/api/students/{student_id}/skills")).json()
    row = next((item for item in rows if item["skill_node_id"] == skill_id), None)
    return float(row["progress"]) if row else 0.0


async def _task_rows(client: httpx.AsyncClient, teacher_id: int) -> dict[str, dict]:
    """教师总览里"任务标题 → 任务行"，同名任务只会有一条，方便断言。"""
    overview = (await client.get(OVERVIEW.format(teacher_id=teacher_id))).json()
    rows: dict[str, dict] = {}
    for classroom in overview["classes"]:
        for task in classroom["tasks"]:
            rows[f"{classroom['class_name']}·{task['title']}"] = task
    return rows


# --------------------------------------------------------------------- 用例


@pytest.mark.asyncio
async def test_student_in_two_teachers_classes_does_not_mix_tasks(client: httpx.AsyncClient) -> None:
    """一个学生在两个老师的班里：两个班的任务互不串味。

    项目对所有学生开放（都能看到、都能做），任务只决定"必修"；技能分母 = 全部已发布项目。
    """
    teacher_a = await _teacher(client, "T7001", "甲老师")
    teacher_b = await _teacher(client, "T7002", "乙老师")
    shared = await _student(client, "2027001", "跨班学生")
    classmate = await _student(client, "2027002", "甲班同学")

    class_a = await _class(client, "甲的班", teacher_a["id"])
    class_b = await _class(client, "乙的班", teacher_b["id"])
    shared_enroll_a = await _enroll(client, class_a["id"], "2027001")
    await _enroll(client, class_a["id"], "2027002")
    await _enroll(client, class_b["id"], "2027001")

    skill = await _skill_node(client, "图像基础")
    project_a = await _project(client, "甲班项目", skill_ids=(skill["id"],))
    project_b = await _project(client, "乙班项目", skill_ids=(skill["id"],))
    await _publish(
        client,
        "甲老师的任务",
        [project_a["id"]],
        [{"class_id": class_a["id"], "target_type": "CLASS"}],
    )
    await _publish(
        client,
        "乙老师的任务",
        [project_b["id"]],
        [{"class_id": class_b["id"], "target_type": "CLASS"}],
    )

    # 项目本身对两个学生都开放（都发布过）；任务只点自己班的学生
    assert await _visible_projects(client, shared["id"]) == [project_a["id"], project_b["id"]]
    assert await _required_projects(client, shared["id"]) == [project_a["id"], project_b["id"]]
    assert sorted(await _task_titles(client, shared["id"])) == ["乙老师的任务", "甲老师的任务"]
    assert await _visible_projects(client, classmate["id"]) == [project_a["id"], project_b["id"]]
    assert await _required_projects(client, classmate["id"]) == [project_a["id"]]
    assert await _task_titles(client, classmate["id"]) == ["甲老师的任务"]

    # 教师总览各自只见自己的班与自己的任务
    overview_a = (await client.get(OVERVIEW.format(teacher_id=teacher_a["id"]))).json()
    overview_b = (await client.get(OVERVIEW.format(teacher_id=teacher_b["id"]))).json()
    assert [item["class_name"] for item in overview_a["classes"]] == ["甲的班"]
    assert [item["class_name"] for item in overview_b["classes"]] == ["乙的班"]
    assert list(await _task_rows(client, teacher_a["id"])) == ["甲的班·甲老师的任务"]
    assert list(await _task_rows(client, teacher_b["id"])) == ["乙的班·乙老师的任务"]
    assert overview_a["student_count"] == 2 and overview_b["student_count"] == 1

    # 技能分母 = 全部已发布项目（2 个）：做完甲班项目 → 50%
    await _complete(client, shared["id"], project_a["id"])
    assert await _progress(client, shared["id"], skill["id"]) == 50.0
    await _complete(client, shared["id"], project_b["id"])
    assert await _progress(client, shared["id"], skill["id"]) == 100.0

    # 同班同学分母同样是 2 个已发布项目（另一个项目他也能做），做完甲班项目 → 50%
    await _complete(client, classmate["id"], project_a["id"])
    assert await _progress(client, classmate["id"], skill["id"]) == 50.0

    # 换班关系不变时，重复查询结果稳定（没有"跑一次涨一点"的漂移）
    before = await _progress(client, shared["id"], skill["id"])
    await client.post(f"/api/students/{shared['id']}/skills/recalculate")
    assert await _progress(client, shared["id"], skill["id"]) == before
    assert shared_enroll_a["class_id"] == class_a["id"]


@pytest.mark.asyncio
async def test_student_in_same_teacher_two_classes(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同一个老师的两个班：同一任务只出现一次、通知只发一条、分母不重复计数。"""
    teacher = await _teacher(client, "T7003", "王老师")
    shared = await _student(client, "2027003", "两头跑的学生")
    second = await _student(client, "2027004", "二班同学")
    class_one = await _class(client, "王老师一班", teacher["id"])
    class_two = await _class(client, "王老师二班", teacher["id"])
    await _enroll(client, class_one["id"], "2027003")
    await _enroll(client, class_two["id"], "2027003")
    await _enroll(client, class_two["id"], "2027004")

    skill = await _skill_node(client, "模型训练")
    project_one = await _project(client, "一班项目", skill_ids=(skill["id"],))
    project_two = await _project(client, "二班项目", skill_ids=(skill["id"],))

    await _publish(
        client,
        "只发一班",
        [project_one["id"]],
        [{"class_id": class_one["id"], "target_type": "CLASS"}],
    )
    await _publish(
        client,
        "只发二班",
        [project_one["id"], project_two["id"]],
        [{"class_id": class_two["id"], "target_type": "CLASS"}],
    )
    # 同一条任务同时发给两个班：学生在两个班都命中，但任务只该出现一次
    both = await _publish(
        client,
        "两个班都发",
        [project_two["id"]],
        [
            {"class_id": class_one["id"], "target_type": "CLASS"},
            {"class_id": class_two["id"], "target_type": "CLASS"},
        ],
    )

    titles = await _task_titles(client, shared["id"])
    assert sorted(titles) == ["两个班都发", "只发一班", "只发二班"]
    assert titles.count("两个班都发") == 1
    assert await _task_titles(client, second["id"]) == ["两个班都发", "只发二班"]
    # 项目对所有学生开放，任务只是标记必修；两个项目各被点名过 → 两人都是两条必修
    assert await _visible_projects(client, shared["id"]) == [project_one["id"], project_two["id"]]
    assert await _required_projects(client, shared["id"]) == [project_one["id"], project_two["id"]]
    assert await _required_projects(client, second["id"]) == [project_one["id"], project_two["id"]]

    # 通知：命中两个班也只发一条
    notifications = (
        await db_session.exec(
            select(Notification).where(
                Notification.biz_id == both["id"], Notification.biz_type == "TASK_PUBLISH"
            )
        )
    ).all()
    assert [item.recipient_user_id for item in notifications] == [shared["id"], second["id"]]

    # 技能分母 = 已发布项目数（2），不是"任务数 × 项目数"（3）；做完一班项目 → 50%
    await _complete(client, shared["id"], project_one["id"])
    assert await _progress(client, shared["id"], skill["id"]) == 50.0

    # 教师总览：两个班各算各的覆盖人数与完成率，任务数去重
    overview = (await client.get(OVERVIEW.format(teacher_id=teacher["id"]))).json()
    assert overview["class_count"] == 2
    assert overview["student_count"] == 3  # 跨班不去重：一班 1 人 + 二班 2 人
    assert overview["task_count"] == 3  # 三条任务，两个班都发的只算一次
    rows = await _task_rows(client, teacher["id"])
    assert rows["王老师一班·两个班都发"]["student_count"] == 1
    assert rows["王老师二班·两个班都发"]["student_count"] == 2
    assert rows["王老师一班·两个班都发"]["task_id"] == rows["王老师二班·两个班都发"]["task_id"]
    assert rows["王老师一班·只发一班"]["completed_count"] == 1
    assert rows["王老师一班·只发一班"]["completion_rate"] == 100.0
    # 二班任务：2 人 × 2 项目 = 4 对，只有跨班学生做完了一班项目 → 1 对 → 25%
    assert rows["王老师二班·只发二班"]["completion_rate"] == 25.0


@pytest.mark.asyncio
async def test_enrollment_and_group_change_only_affect_required_flag(
    client: httpx.AsyncClient,
) -> None:
    """入班 / 换组 / 离班只改「必修」名单，不改技能分母（分母 = 全部已发布项目）。"""
    teacher = await _teacher(client, "T7005", "李老师")
    student = await _student(client, "2027005", "转来转去的学生")
    class_one = await _class(client, "李老师一班", teacher["id"])
    class_two = await _class(client, "李老师二班", teacher["id"])
    groups_one = await _groups(client, class_one["id"])
    await _groups(client, class_two["id"])

    enrollment = await _enroll(client, class_one["id"], "2027005", group_no=1)
    skill = await _skill_node(client, "数据处理")
    project_one = await _project(client, "一班项目", skill_ids=(skill["id"],))
    project_two = await _project(client, "二班项目", skill_ids=(skill["id"],))
    project_three = await _project(client, "一组专项项目", skill_ids=(skill["id"],))

    # 三个项目都已发布 → 分母是 3，谁都能做；任务只决定必修名单
    assert await _visible_projects(client, student["id"]) == [
        project_one["id"],
        project_two["id"],
        project_three["id"],
    ]

    # 只发一班 → 必修只有"一班项目"
    await _publish(
        client,
        "一班任务",
        [project_one["id"]],
        [{"class_id": class_one["id"], "target_type": "CLASS"}],
    )
    assert await _required_projects(client, student["id"]) == [project_one["id"]]
    await _complete(client, student["id"], project_one["id"])
    assert await _progress(client, student["id"], skill["id"]) == 33.33  # 1 ÷ 3

    # 发到二班（学生还没入二班）→ 进度不受影响
    await _publish(
        client,
        "二班任务",
        [project_two["id"]],
        [{"class_id": class_two["id"], "target_type": "CLASS"}],
    )
    assert await _required_projects(client, student["id"]) == [project_one["id"]]
    assert await _progress(client, student["id"], skill["id"]) == 33.33

    # 学生转入二班 → 二班项目也变成必修；分母不变（项目本来就都能做）
    await _enroll(client, class_two["id"], "2027005")
    assert await _required_projects(client, student["id"]) == [project_one["id"], project_two["id"]]
    assert await _progress(client, student["id"], skill["id"]) == 33.33

    # 一组的专项任务：学生在第一组 → 专项项目也标必修
    await _publish(
        client,
        "一组专项",
        [project_three["id"]],
        [{"class_id": class_one["id"], "target_type": "GROUP", "group_id": groups_one[0]["id"]}],
    )
    assert await _required_projects(client, student["id"]) == [
        project_one["id"],
        project_two["id"],
        project_three["id"],
    ]
    assert await _progress(client, student["id"], skill["id"]) == 33.33

    # 调到第二组 → 失去一组专项的必修标记，进度仍然不变
    moved = await client.put(
        f"/api/class-students/{enrollment['id']}/group", json={"group_id": groups_one[1]["id"]}
    )
    assert moved.status_code == 200, moved.text
    assert await _required_projects(client, student["id"]) == [project_one["id"], project_two["id"]]
    assert await _progress(client, student["id"], skill["id"]) == 33.33

    # 离班（二班）→ 只剩一班任务是必修
    enrollments = (await client.get(f"{CLASSES}/{class_two['id']}/students")).json()["items"]
    second_enrollment = next(item for item in enrollments if item["user_no"] == "2027005")
    left = await client.delete(f"/api/class-students/{second_enrollment['id']}")
    assert left.status_code == 200, left.text
    assert await _required_projects(client, student["id"]) == [project_one["id"]]
    assert await _progress(client, student["id"], skill["id"]) == 33.33


@pytest.mark.asyncio
async def test_teacher_takeover_keeps_student_progress(client: httpx.AsyncClient) -> None:
    """甲老师离职、乙老师接班：学生用同一账号进新班 + 任务复用同一个项目 → 进度全部保留。

    完成状态挂在 ``student_project``（学生 × 项目）上，不挂班级 / 任务 / 老师，
    项目也是发布即可见，所以撤班撤任务都不影响进度；新任务一发布，乙老师就能看到这些学生"已完成"。
    """
    teacher_a = await _teacher(client, "T7006", "甲老师")
    teacher_b = await _teacher(client, "T7007", "乙老师")
    student = await _student(client, "2027006", "接班班学生")
    job = (await client.post("/api/jobs", json={"job_name": "工业视觉工程师"})).json()

    class_a = await _class(client, "甲的班", teacher_a["id"])
    await _enroll(client, class_a["id"], "2027006")
    skill = await _skill_node(client, "图像基础")
    project = await _project(client, "工业缺陷检测实训", skill_ids=(skill["id"],), job_id=job["id"])
    await _publish(
        client,
        "甲老师的基础实训",
        [project["id"]],
        [{"class_id": class_a["id"], "target_type": "CLASS"}],
        job_ids=[job["id"]],
    )
    await _complete(client, student["id"], project["id"])
    assert await _progress(client, student["id"], skill["id"]) == 100.0

    before = (await client.get(f"/api/students/{student['id']}/projects/{project['id']}")).json()
    assert before["status"] == "COMPLETED" and before["best_score"] == 90.0
    assert before["submission_count"] == 1

    # 甲老师离职：班撤了，学生暂时看不到任何项目（但记录与成绩都还在库里）
    assert (await client.delete(f"{CLASSES}/{class_a['id']}")).status_code == 200
    # 项目已经发布过 → 撤班后学生照样能看能做，只是不再是必修
    assert await _visible_projects(client, student["id"]) == [project["id"]]
    assert await _required_projects(client, student["id"]) == []
    assert await _task_titles(client, student["id"]) == []
    assert await _progress(client, student["id"], skill["id"]) == 100.0

    # 乙老师接班：新建班级，用同一个学号把学生加进来（账号复用，不是新建账号）
    class_b = await _class(client, "乙的班", teacher_b["id"])
    await _enroll(client, class_b["id"], "2027006")
    assert await _visible_projects(client, student["id"]) == [project["id"]]  # 还没发任务也看得到

    # 发"同样的任务"：同一个项目（同一岗位）
    await _publish(
        client,
        "乙老师的基础实训",
        [project["id"]],
        [{"class_id": class_b["id"], "target_type": "CLASS"}],
        job_ids=[job["id"]],
    )

    # 之前的进度全部保留：可见项目、完成状态、最高分、提交历史、技能进度
    assert await _visible_projects(client, student["id"]) == [project["id"]]
    assert await _required_projects(client, student["id"]) == [project["id"]]
    assert await _task_titles(client, student["id"]) == ["乙老师的基础实训"]
    after = (await client.get(f"/api/students/{student['id']}/projects/{project['id']}")).json()
    assert after["status"] == "COMPLETED" and after["best_score"] == 90.0
    assert after["submission_count"] == 1 and after["progress"] == 100.0
    assert await _progress(client, student["id"], skill["id"]) == 100.0

    # 乙老师的看板：新任务一发布就是 100%（完成按"学生 × 项目"记，不按班级/任务重算）
    rows = await _task_rows(client, teacher_b["id"])
    task_row = rows["乙的班·乙老师的基础实训"]
    assert (task_row["completed_count"], task_row["total_count"]) == (1, 1)
    assert task_row["completion_rate"] == 100.0
    assert task_row["students"][0]["status"] == "COMPLETED"
    assert task_row["students"][0]["completed_count"] == 1

    # 甲老师这边已经没有任何班可看
    overview_a = (await client.get(OVERVIEW.format(teacher_id=teacher_a["id"]))).json()
    assert overview_a["class_count"] == 0 and overview_a["classes"] == []


@pytest.mark.asyncio
async def test_takeover_with_copied_project_loses_progress(client: httpx.AsyncClient) -> None:
    """反例：乙老师把项目复制成一个**新项目**（新 id），进度不会自动跟过来。

    完成记录是按 ``project_id`` 关联的，所以要么复用原来那个已发布项目，
    要么接受学生在新项目上重新闯关一遍；两个项目都在平台上，学生都能做。
    """
    teacher_a = await _teacher(client, "T7008", "甲老师")
    teacher_b = await _teacher(client, "T7009", "乙老师")
    student = await _student(client, "2027007", "又一位接班学生")

    class_a = await _class(client, "甲的班", teacher_a["id"])
    await _enroll(client, class_a["id"], "2027007")
    skill = await _skill_node(client, "模型训练")
    original = await _project(client, "工业缺陷检测实训", skill_ids=(skill["id"],))
    await _publish(
        client,
        "甲老师的任务",
        [original["id"]],
        [{"class_id": class_a["id"], "target_type": "CLASS"}],
    )
    await _complete(client, student["id"], original["id"])
    assert await _progress(client, student["id"], skill["id"]) == 100.0

    # 甲老师离职，乙老师"复制"了一个同名同内容的项目（新 id）
    assert (await client.delete(f"{CLASSES}/{class_a['id']}")).status_code == 200
    copied = await _project(client, "工业缺陷检测实训（乙老师复制版）", skill_ids=(skill["id"],))
    class_b = await _class(client, "乙的班", teacher_b["id"])
    await _enroll(client, class_b["id"], "2027007")
    await _publish(
        client,
        "乙老师的任务",
        [copied["id"]],
        [{"class_id": class_b["id"], "target_type": "CLASS"}],
    )

    # 两个项目都已发布 → 都能看到；分母变成 2，老项目那份完成只算一半
    assert await _visible_projects(client, student["id"]) == [original["id"], copied["id"]]
    assert await _required_projects(client, student["id"]) == [copied["id"]]
    assert await _progress(client, student["id"], skill["id"]) == 50.0
    rows = await _task_rows(client, teacher_b["id"])
    assert rows["乙的班·乙老师的任务"]["completion_rate"] == 0.0

    # 老项目上的成绩没丢，学生仍然能打开它，也能随时把新项目补上
    kept = (await client.get(f"/api/students/{student['id']}/projects/{original['id']}")).json()
    assert kept["status"] == "COMPLETED" and kept["best_score"] == 90.0
    assert kept["is_required"] is False
    await _complete(client, student["id"], copied["id"])
    assert await _progress(client, student["id"], skill["id"]) == 100.0
