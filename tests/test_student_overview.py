"""学生成长视图接口测试：岗位推荐、技能树总览、实训项目列表。

三个视图都是纯派生：技能点进度取 ``student_skill.progress``，岗位匹配度与技能树进度取
相关技能点进度的均值；岗位关联项目按 ``training_project.job_id`` + PUBLISHED 统计，
关卡进度按 ``project_module`` 与最新一轮 ``attempt_stage.is_filled`` 统计。
"""

from decimal import Decimal

import httpx
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import now
from app.crud.attempt import AttemptStageRepository, StudentProjectRepository, TrainingAttemptRepository
from app.crud.job_skill import ProjectSkillRepository, StudentSkillRepository
from app.crud.project import ProjectModuleRepository, TrainingProjectRepository
from tests import helpers

RECOMMEND = "/api/students/{student_id}/job-recommendations"
OVERVIEW = "/api/students/{student_id}/skill-tree-progress"
PROJECTS = "/api/students/{student_id}/training-projects"
PROJECT_PROGRESS = "/api/students/{student_id}/project-progress"


# --------------------------------------------------------------------- 造数


async def _student(client: httpx.AsyncClient, user_no: str = "2026001") -> dict:
    response = await client.post(
        "/api/users", json={"user_no": user_no, "real_name": "张三", "user_type": "STUDENT"}
    )
    assert response.status_code == 201, response.text
    # 学生要看到项目，得先在班里、并且老师把项目发给他（见 docs/方案设计.md §4.2）
    await helpers.enroll(client, user_no)
    return response.json()


async def _job(client: httpx.AsyncClient, name: str, **extra: object) -> dict:
    response = await client.post("/api/jobs", json={"job_name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def _tree(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/skill-trees", json={"tree_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _node(client: httpx.AsyncClient, tree_id: int, name: str) -> dict:
    response = await client.post(f"/api/skill-trees/{tree_id}/nodes", json={"node_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _project(
    db: AsyncSession,
    name: str,
    job_id: int,
    skill_node_ids: list[int],
    *,
    status: str = "PUBLISHED",
    level: str = "BASIC",
) -> dict:
    """直接落库建项目（跳过发布校验），只为拿到"岗位 → 项目 → 技能点"的关联数据。"""
    project = await TrainingProjectRepository(db).create(
        {
            "project_name": name,
            "project_level": level,
            "job_id": job_id,
            "status": status,
        }
    )
    await ProjectSkillRepository(db).replace_skills(project.id, skill_node_ids)
    return {"id": project.id, "status": project.status}


async def _set_progress(db: AsyncSession, student_id: int, node_id: int, progress: float) -> None:
    await StudentSkillRepository(db).create(
        {
            "student_id": student_id,
            "skill_node_id": node_id,
            "progress": progress,
            "source": "MANUAL",
        }
    )


async def _complete_project(db: AsyncSession, student_id: int, project_id: int) -> None:
    await StudentProjectRepository(db).create(
        {
            "student_id": student_id,
            "project_id": project_id,
            "status": "COMPLETED",
            "completed_at": now(),
        }
    )


def _group_of(item: dict, tree_id: int) -> dict:
    return next(group for group in item["skill_groups"] if group["tree_id"] == tree_id)


async def _stage_template(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/stage-templates", json={"stage_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _module(db: AsyncSession, project_id: int, template_id: int, stage_no: int) -> dict:
    module = await ProjectModuleRepository(db).create(
        {"project_id": project_id, "template_id": template_id, "stage_no": stage_no, "weight": 50}
    )
    return {"id": module.id}


async def _record(
    db: AsyncSession, student_id: int, project_id: int, *, status: str, best_score: str | None
) -> dict:
    record = await StudentProjectRepository(db).create(
        {
            "student_id": student_id,
            "project_id": project_id,
            "status": status,
            "progress": Decimal("50.00"),
            "total_score": Decimal("76"),
            "best_score": Decimal(best_score) if best_score else None,
        }
    )
    return {"id": record.id}


async def _attempt(
    db: AsyncSession, record_id: int, attempt_no: int, module_ids: list[int], filled: int
) -> None:
    attempt = await TrainingAttemptRepository(db).create(
        {"student_project_id": record_id, "attempt_no": attempt_no, "status": "IN_PROGRESS"}
    )
    for index, module_id in enumerate(module_ids):
        await AttemptStageRepository(db).create(
            {
                "attempt_id": attempt.id,
                "project_module_id": module_id,
                "is_filled": index < filled,
            }
        )


# ------------------------------------------------------------------ 用例


@pytest.mark.asyncio
async def test_job_recommendations_top3_with_grouped_skills(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """默认返回前三名：按匹配度倒序，带岗位画像、技能/项目统计与按体系分组的技能点。"""
    student = await _student(client)
    optical = await _tree(client, "光学成像系")
    algorithm = await _tree(client, "传统算法系")
    light = await _node(client, optical["id"], "光源与打光")
    filters = await _node(client, algorithm["id"], "图像滤波")
    edges = await _node(client, algorithm["id"], "边缘检测")

    job_high = await _job(
        client,
        "视觉算法工程师",
        direction_tag="工业视觉",
        recommended_level="ADVANCED",
        scene="缺陷检测",
        description="负责产线缺陷检测",
        heat=100,
    )
    job_mid = await _job(client, "视觉调试工程师", direction_tag="工业视觉", heat=10)
    job_low = await _job(client, "数据标注专员", direction_tag="数据工程", heat=500)
    await _job(client, "无技能岗位", direction_tag="其它", heat=9999)

    await client.put(f"/api/jobs/{job_high['id']}/skills", json={"skill_node_ids": [light["id"]]})
    await client.put(
        f"/api/jobs/{job_mid['id']}/skills", json={"skill_node_ids": [light["id"], filters["id"]]}
    )
    await client.put(f"/api/jobs/{job_low['id']}/skills", json={"skill_node_ids": [edges["id"]]})

    # 岗位高：1 个已发布项目（已完成）；岗位中：2 个已发布项目（完成 1 个）+ 1 个草稿（不计）
    project_high = await _project(db_session, "项目-高-1", job_high["id"], [light["id"]])
    project_mid_a = await _project(db_session, "项目-中-1", job_mid["id"], [light["id"]])
    project_mid_b = await _project(db_session, "项目-中-2", job_mid["id"], [filters["id"]])
    await _project(db_session, "项目-中-草稿", job_mid["id"], [filters["id"]], status="DRAFT")
    # 发任务：草稿项目发不出去，也不该被学生看到
    await helpers.publish(client, [project_high["id"], project_mid_a["id"], project_mid_b["id"]])

    await _complete_project(db_session, student["id"], project_high["id"])
    await _complete_project(db_session, student["id"], project_mid_a["id"])

    # 学生进度：光源与打光 100、图像滤波 50、边缘检测无记录（按 0）
    await _set_progress(db_session, student["id"], light["id"], 100)
    await _set_progress(db_session, student["id"], filters["id"], 50)

    listed = (await client.get(RECOMMEND.format(student_id=student["id"]))).json()
    assert [item["job_id"] for item in listed] == [job_high["id"], job_mid["id"], job_low["id"]]

    top = listed[0]
    assert top["job_name"] == "视觉算法工程师"
    assert top["direction_tag"] == "工业视觉"
    assert top["recommended_level"] == "ADVANCED"
    assert top["scene"] == "缺陷检测"
    assert top["description"] == "负责产线缺陷检测"
    assert top["heat"] == 100
    assert top["match_score"] == 100
    assert (top["skill_total_count"], top["skill_done_count"]) == (1, 1)
    assert (top["project_total_count"], top["project_done_count"]) == (1, 1)
    assert [group["tree_name"] for group in top["skill_groups"]] == ["光学成像系"]
    assert top["skill_groups"][0]["skills"] == [
        {"skill_node_id": light["id"], "node_name": "光源与打光", "progress": 100}
    ]

    second = listed[1]
    assert second["match_score"] == 75  # (100 + 50) ÷ 2
    assert (second["skill_total_count"], second["skill_done_count"]) == (2, 1)
    assert (second["project_total_count"], second["project_done_count"]) == (2, 1)
    # 按技能树体系分组：两个体系各 1 个技能点
    assert _group_of(second, optical["id"])["skill_done_count"] == 1
    algorithm_group = _group_of(second, algorithm["id"])
    assert algorithm_group["tree_name"] == "传统算法系"
    assert algorithm_group["skill_done_count"] == 0
    assert algorithm_group["skills"][0]["progress"] == 50

    assert listed[2]["match_score"] == 0  # 边缘检测没有进度记录

    # 没关联技能的岗位不算候选（热度再高也不出现）
    all_items = (await client.get(RECOMMEND.format(student_id=student["id"]), params={"limit": 20})).json()
    assert {item["job_name"] for item in all_items} == {
        "视觉算法工程师",
        "视觉调试工程师",
        "数据标注专员",
    }

    # 返回条数可调
    only_one = (await client.get(RECOMMEND.format(student_id=student["id"]), params={"limit": 1})).json()
    assert [item["job_id"] for item in only_one] == [job_high["id"]]

    missing = await client.get(RECOMMEND.format(student_id=999))
    assert missing.json()["code"] == 422


@pytest.mark.asyncio
async def test_skill_tree_progress_overview(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """技能树总览：全部技能树与节点 + 节点进度、技能树总进度、整体进度与技能点统计。"""
    student = await _student(client)
    optical = await _tree(client, "光学成像系")
    algorithm = await _tree(client, "传统算法系")
    light = await _node(client, optical["id"], "光源与打光")
    camera = await _node(client, optical["id"], "相机标定")
    filters = await _node(client, algorithm["id"], "图像滤波")

    job = await _job(client, "视觉算法工程师")
    project = await _project(db_session, "项目-1", job["id"], [light["id"], filters["id"]])
    await helpers.publish(client, [project["id"]])
    await _complete_project(db_session, student["id"], project["id"])

    await _set_progress(db_session, student["id"], light["id"], 100)
    await _set_progress(db_session, student["id"], camera["id"], 40)

    overview = (await client.get(OVERVIEW.format(student_id=student["id"]))).json()
    assert overview["student_id"] == student["id"]
    assert overview["tree_count"] == 2
    assert overview["total_nodes"] == 3
    assert overview["done_nodes"] == 1
    assert overview["overall_percent"] == 46.67  # (100 + 40 + 0) ÷ 3

    optical_out, algorithm_out = overview["trees"]
    assert optical_out["tree_name"] == "光学成像系"
    assert (optical_out["total"], optical_out["done"], optical_out["percent"]) == (2, 1, 70)
    assert [node["node_name"] for node in optical_out["nodes"]] == ["光源与打光", "相机标定"]

    light_out = optical_out["nodes"][0]
    assert light_out["progress"] == 100
    assert (light_out["project_total"], light_out["project_done"]) == (1, 1)
    camera_out = optical_out["nodes"][1]
    assert camera_out["progress"] == 40
    assert (camera_out["project_total"], camera_out["project_done"]) == (0, 0)

    assert (algorithm_out["total"], algorithm_out["done"], algorithm_out["percent"]) == (1, 0, 0)
    filters_out = algorithm_out["nodes"][0]
    assert filters_out["progress"] == 0
    assert (filters_out["project_total"], filters_out["project_done"]) == (1, 1)

    missing = await client.get(OVERVIEW.format(student_id=999))
    assert missing.json()["code"] == 422


@pytest.mark.asyncio
async def test_student_training_projects_view(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """实训项目列表：已发布项目 + 该学生的最高分、关卡进度（总/完成）、岗位、技能点与状态。"""
    student = await _student(client)
    job = await _job(client, "工业视觉工程师")
    tree = await _tree(client, "光学成像系")
    light = await _node(client, tree["id"], "光源与打光")
    camera = await _node(client, tree["id"], "相机标定")
    template_a = await _stage_template(client, "需求分析")
    template_b = await _stage_template(client, "数据处理")

    # 已发布项目：2 个关卡、2 个技能点，学生已开始（最新一轮只填了 1 关）
    started = await _project(db_session, "成像系统搭建实训", job["id"], [light["id"], camera["id"]])
    module_a = await _module(db_session, started["id"], template_a["id"], 1)
    module_b = await _module(db_session, started["id"], template_b["id"], 2)
    record = await _record(db_session, student["id"], started["id"], status="IN_PROGRESS", best_score="88.5")
    await _attempt(db_session, record["id"], 1, [module_a["id"], module_b["id"]], filled=2)
    await _attempt(db_session, record["id"], 2, [module_a["id"], module_b["id"]], filled=1)

    # 已发布项目：学生还没开始
    untouched = await _project(db_session, "表面缺陷分类进阶", job["id"], [light["id"]])
    await _module(db_session, untouched["id"], template_a["id"], 1)

    # 草稿项目：学生看不到
    draft = await _project(db_session, "未发布项目", job["id"], [], status="DRAFT")
    await _module(db_session, draft["id"], template_a["id"], 1)
    # 发任务：只发两个已发布项目（草稿发不出去）
    await helpers.publish(client, [started["id"], untouched["id"]])

    listed = (await client.get(PROJECTS.format(student_id=student["id"]))).json()
    assert [item["project_id"] for item in listed] == [started["id"], untouched["id"]]

    first = listed[0]
    assert first["project_name"] == "成像系统搭建实训"
    assert first["project_level"] == "BASIC"
    assert (first["job_id"], first["job_name"]) == (job["id"], "工业视觉工程师")
    assert first["status"] == "IN_PROGRESS"
    assert first["best_score"] == 88.5
    assert first["total_score"] == 76
    assert (first["level_total"], first["level_done"]) == (2, 1)  # 以最新一轮为准
    assert first["skill_nodes"] == [
        {
            "skill_node_id": light["id"],
            "node_name": "光源与打光",
            "tree_id": tree["id"],
            "tree_name": "光学成像系",
        },
        {
            "skill_node_id": camera["id"],
            "node_name": "相机标定",
            "tree_id": tree["id"],
            "tree_name": "光学成像系",
        },
    ]

    second = listed[1]
    assert second["status"] == "NOT_STARTED"
    assert second["best_score"] is None
    assert second["progress"] == 0
    assert (second["level_total"], second["level_done"]) == (1, 0)
    assert [node["node_name"] for node in second["skill_nodes"]] == ["光源与打光"]

    missing = await client.get(PROJECTS.format(student_id=999))
    assert missing.json()["code"] == 422


@pytest.mark.asyncio
async def test_student_project_progress_by_level(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """实训项目进度：按三档分组，分母支持"全部 / 我自主选择的 / 老师下发的"三种口径。"""
    student = await _student(client)
    job = await _job(client, "工业视觉工程师")
    other_job = await _job(client, "数据标注专员")

    basic_done = await _project(db_session, "基础-已完成", job["id"], [])
    basic_open = await _project(db_session, "基础-未完成", job["id"], [])
    advanced_open = await _project(db_session, "进阶-未完成", job["id"], [], level="ADVANCED")
    expanded_a = await _project(db_session, "拓展-未完成A", job["id"], [], level="EXPANDED")
    expanded_b = await _project(db_session, "拓展-未完成B", job["id"], [], level="EXPANDED")
    # 已发布但没发给这个班、学生也没自己挑：只在"全部"口径里出现
    unassigned = await _project(db_session, "基础-未下发", job["id"], [])
    await _project(db_session, "基础-草稿", job["id"], [], status="DRAFT")
    other_done = await _project(db_session, "其它岗位-已完成", other_job["id"], [])
    # 发任务：本条任务覆盖两个岗位的已发布项目（草稿项目不参与）
    await helpers.publish(
        client,
        [
            basic_done["id"],
            basic_open["id"],
            advanced_open["id"],
            expanded_a["id"],
            expanded_b["id"],
            other_done["id"],
        ],
    )

    await _complete_project(db_session, student["id"], basic_done["id"])
    await _complete_project(db_session, student["id"], other_done["id"])
    # 自己加进「我的实训」的项目（含一个已完成、一个未完成、一个老师没下发的）
    picked = await client.post(
        f"/api/students/{student['id']}/my-projects",
        json={"project_ids": [basic_done["id"], basic_open["id"], unassigned["id"]]},
    )
    assert picked.status_code in (200, 201), picked.text

    # 选岗：把工业视觉工程师设为主岗位 —— 主岗位不影响这个视图的分母
    selected = await client.post(
        f"/api/students/{student['id']}/jobs", json={"job_id": job["id"], "is_primary": True}
    )
    assert selected.status_code == 201, selected.text

    # 默认口径 = 全部已发布项目：4 个基础（含其它岗位那个、老师没下发那个）+ 1 进阶 + 2 拓展
    progress = (await client.get(PROJECT_PROGRESS.format(student_id=student["id"]))).json()
    assert progress["scope"] == "ALL"
    assert (progress["total"], progress["completed"]) == (7, 2)
    assert progress["levels"] == [
        {"level_type": "BASIC", "level_name": "基础", "total": 4, "completed": 2, "percent": 50.0},
        {"level_type": "ADVANCED", "level_name": "进阶", "total": 1, "completed": 0, "percent": 0.0},
        {"level_type": "EXPANDED", "level_name": "拓展", "total": 2, "completed": 0, "percent": 0.0},
    ]

    # 老师下发的口径：那条任务点了 6 个项目，未下发的那个不算；草稿也不算
    required = (
        await client.get(PROJECT_PROGRESS.format(student_id=student["id"]), params={"scope": "TEACHER"})
    ).json()
    assert required["scope"] == "TEACHER"
    assert (required["total"], required["completed"]) == (6, 2)
    assert [item["total"] for item in required["levels"]] == [3, 1, 2]

    # 我自主选择的口径：只在「我的实训」清单里挑的项目，含老师没下发的那个
    mine = (
        await client.get(PROJECT_PROGRESS.format(student_id=student["id"]), params={"scope": "SELF"})
    ).json()
    assert mine["scope"] == "SELF"
    assert (mine["total"], mine["completed"]) == (3, 1)
    assert mine["levels"] == [
        {"level_type": "BASIC", "level_name": "基础", "total": 3, "completed": 1, "percent": 33.33},
        {"level_type": "ADVANCED", "level_name": "进阶", "total": 0, "completed": 0, "percent": 0.0},
        {"level_type": "EXPANDED", "level_name": "拓展", "total": 0, "completed": 0, "percent": 0.0},
    ]

    # 没选岗位、也没自己挑项目的学生：全部 / 老师下发两种口径照旧有分母，自主选择为 0
    idle = await _student(client, user_no="2026999")
    empty = (await client.get(PROJECT_PROGRESS.format(student_id=idle["id"]))).json()
    assert (empty["total"], empty["completed"]) == (7, 0)
    assert [item["total"] for item in empty["levels"]] == [4, 1, 2]
    idle_mine = (
        await client.get(PROJECT_PROGRESS.format(student_id=idle["id"]), params={"scope": "SELF"})
    ).json()
    assert (idle_mine["total"], idle_mine["completed"]) == (0, 0)

    # 非法口径与不存在的学生都要报错
    bad = await client.get(PROJECT_PROGRESS.format(student_id=student["id"]), params={"scope": "X"})
    assert bad.json()["code"] == 422
    missing = await client.get(PROJECT_PROGRESS.format(student_id=999))
    assert missing.json()["code"] == 422
