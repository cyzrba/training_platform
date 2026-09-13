"""岗位与技能成长域接口测试：岗位、技能树/节点/依赖、成长规则、学生选岗与技能进度。"""

from decimal import Decimal

import httpx
import pytest

JOBS = "/api/jobs"
TREES = "/api/skill-trees"
NODES = "/api/skill-nodes"
RULES = "/api/growth-rules"


async def _create_job(client: httpx.AsyncClient, name: str = "工业视觉工程师") -> dict:
    response = await client.post(
        JOBS,
        json={
            "job_name": name,
            "direction_tag": "机器视觉",
            "recommended_level": "BASIC",
            "scene": "缺陷检测实训",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_tree(client: httpx.AsyncClient, code: str = "CV_BASIC") -> dict:
    response = await client.post(
        TREES, json={"tree_code": code, "tree_name": "传统算法系", "description": "图像算法技能树"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_node(client: httpx.AsyncClient, tree_id: int, code: str, name: str) -> dict:
    response = await client.post(f"{TREES}/{tree_id}/nodes", json={"node_code": code, "node_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _create_student(client: httpx.AsyncClient, user_no: str = "2026001") -> dict:
    response = await client.post(
        "/api/users", json={"user_no": user_no, "real_name": "张三", "user_type": "STUDENT"}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_job_crud_and_filters(client: httpx.AsyncClient) -> None:
    job = await _create_job(client)
    assert job["status"] == "ENABLED" and job["heat"] == 0

    duplicate = await client.post(JOBS, json={"job_name": "工业视觉工程师"})
    assert duplicate.status_code == 200

    detail = await client.get(f"{JOBS}/{job['id']}")
    assert detail.json()["skill_count"] == 0

    listed = (await client.get(JOBS, params={"keyword": "视觉", "recommended_level": "BASIC"})).json()
    assert listed["total"] == 1
    assert (await client.get(JOBS, params={"recommended_level": "ADVANCED"})).json()["total"] == 0
    assert (await client.get(JOBS, params={"direction_tag": "机器视觉"})).json()["total"] == 1

    patched = await client.patch(f"{JOBS}/{job['id']}", json={"remark": "热门岗位", "heat": 3})
    assert patched.status_code == 200
    assert patched.json()["heat"] == 3

    assert (await client.delete(f"{JOBS}/{job['id']}")).status_code == 200
    assert (await client.get(f"{JOBS}/{job['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_job_skills_cover_and_append(client: httpx.AsyncClient) -> None:
    job = await _create_job(client)
    tree = await _create_tree(client)
    node_a = await _create_node(client, tree["id"], "IMG_FILTER", "图像滤波")
    node_b = await _create_node(client, tree["id"], "EDGE_DETECT", "边缘检测")

    assert (await client.put(f"{JOBS}/{job['id']}/skills", json={"skill_node_ids": []})).json() == []

    covered = await client.put(
        f"{JOBS}/{job['id']}/skills", json={"skill_node_ids": [node_a["id"], node_b["id"]]}
    )
    assert [item["node_code"] for item in covered.json()] == ["IMG_FILTER", "EDGE_DETECT"]
    assert (await client.get(f"{JOBS}/{job['id']}")).json()["skill_count"] == 2

    # 覆盖式：只留一个
    await client.put(f"{JOBS}/{job['id']}/skills", json={"skill_node_ids": [node_a["id"]]})
    assert len((await client.get(f"{JOBS}/{job['id']}/skills")).json()) == 1

    # 追加 / 解除 / 重复追加冲突
    assert (await client.post(f"{JOBS}/{job['id']}/skills/{node_b['id']}")).status_code == 201
    repeated = await client.post(f"{JOBS}/{job['id']}/skills/{node_b['id']}")
    assert repeated.status_code == 200
    assert (await client.delete(f"{JOBS}/{job['id']}/skills/{node_b['id']}")).status_code == 200
    assert len((await client.get(f"{JOBS}/{job['id']}/skills")).json()) == 1

    missing = await client.put(f"{JOBS}/{job['id']}/skills", json={"skill_node_ids": [9999]})
    assert missing.status_code == 200


@pytest.mark.asyncio
async def test_skill_tree_and_nodes(client: httpx.AsyncClient) -> None:
    tree = await _create_tree(client)
    duplicate = await client.post(TREES, json={"tree_code": "CV_BASIC", "tree_name": "重复树"})
    assert duplicate.status_code == 200

    node = await _create_node(client, tree["id"], "IMG_FILTER", "图像滤波")
    duplicate_node = await client.post(
        f"{TREES}/{tree['id']}/nodes", json={"node_code": "IMG_FILTER", "node_name": "重复节点"}
    )
    assert duplicate_node.status_code == 200

    listed = (await client.get(f"{TREES}/{tree['id']}/nodes")).json()
    assert len(listed) == 1
    assert listed[0]["tree_name"] == "传统算法系"
    assert listed[0]["prerequisite_ids"] == []

    detail = await client.get(f"{NODES}/{node['id']}")
    assert detail.json()["node_code"] == "IMG_FILTER"

    patched = await client.patch(f"{NODES}/{node['id']}", json={"unlock_note": "完成图像基础模块后解锁"})
    assert patched.json()["unlock_note"] == "完成图像基础模块后解锁"

    assert (await client.delete(f"{NODES}/{node['id']}")).status_code == 200
    assert (await client.get(f"{NODES}/{node['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_skill_dependencies_dag_guards(client: httpx.AsyncClient) -> None:
    tree = await _create_tree(client)
    first = await _create_node(client, tree["id"], "IMG_BASE", "图像基础")
    second = await _create_node(client, tree["id"], "IMG_FILTER", "图像滤波")
    third = await _create_node(client, tree["id"], "EDGE_DETECT", "边缘检测")

    result = await client.put(
        f"{NODES}/{second['id']}/dependencies", json={"prerequisite_node_ids": [first["id"]]}
    )
    assert result.status_code == 200
    assert [item["node_code"] for item in result.json()] == ["IMG_BASE"]
    assert (await client.get(f"{NODES}/{second['id']}/dependencies")).json()[0]["id"] == first["id"]

    # 自环拒绝
    self_loop = await client.put(
        f"{NODES}/{second['id']}/dependencies",
        json={"prerequisite_node_ids": [second["id"]]},
    )
    assert self_loop.status_code == 200

    # 环路拒绝：third ← second ← third
    await client.put(f"{NODES}/{third['id']}/dependencies", json={"prerequisite_node_ids": [second["id"]]})
    cycle = await client.put(
        f"{NODES}/{second['id']}/dependencies", json={"prerequisite_node_ids": [third["id"]]}
    )
    assert cycle.status_code == 200
    assert "循环依赖" in cycle.json()["msg"]

    # 清空前置
    cleared = await client.put(f"{NODES}/{second['id']}/dependencies", json={"prerequisite_node_ids": []})
    assert cleared.json() == []


@pytest.mark.asyncio
async def test_growth_rules_crud(client: httpx.AsyncClient) -> None:
    created = await client.post(
        RULES,
        json={
            "level_type": "BASIC",
            "unlock_condition_json": {"min_projects": 1},
            "skill_max_level": 3,
            "pass_score": 60,
            "level_description": "基础层级",
        },
    )
    assert created.status_code == 201
    rule = created.json()
    assert Decimal(str(rule["pass_score"])) == Decimal("60")

    duplicate = await client.post(RULES, json={"level_type": "BASIC"})
    assert duplicate.status_code == 200

    listed = (await client.get(RULES)).json()
    assert listed["total"] == 1

    patched = await client.patch(f"{RULES}/{rule['id']}", json={"pass_score": 70})
    assert Decimal(str(patched.json()["pass_score"])) == Decimal("70")

    assert (await client.delete(f"{RULES}/{rule['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_student_job_selection_keeps_single_primary(client: httpx.AsyncClient, db_session) -> None:
    student = await _create_student(client)
    first_job = await _create_job(client, "工业视觉工程师")
    second_job = await _create_job(client, "算法工程师")

    first = await client.post(
        f"/api/students/{student['id']}/jobs", json={"job_id": first_job["id"], "is_primary": True}
    )
    assert first.status_code == 201
    assert first.json()["job_name"] == "工业视觉工程师" and first.json()["is_primary"] is True

    # 选第二个岗位并设为主岗位 → 原来的主岗位自动降级
    second = await client.post(
        f"/api/students/{student['id']}/jobs", json={"job_id": second_job["id"], "is_primary": True}
    )
    assert second.status_code == 201
    selections = (await client.get(f"/api/students/{student['id']}/jobs")).json()
    primaries = [item for item in selections if item["is_primary"]]
    assert len(selections) == 2 and len(primaries) == 1
    assert primaries[0]["job_name"] == "算法工程师"

    # 已选过的岗位不再报错：改成更新它的主岗位标记（幂等），不会新增第二条
    duplicate = await client.post(f"/api/students/{student['id']}/jobs", json={"job_id": second_job["id"]})
    assert duplicate.status_code == 200
    assert duplicate.json()["is_primary"] is True
    assert len((await client.get(f"/api/students/{student['id']}/jobs")).json()) == 2

    # 把已选的非主岗位升为主岗位：原来的主岗位自动降级
    promoted = await client.patch(
        f"/api/students/{student['id']}/jobs/{first.json()['id']}", json={"is_primary": True}
    )
    assert promoted.status_code == 200
    assert promoted.json()["is_primary"] is True
    selections = (await client.get(f"/api/students/{student['id']}/jobs")).json()
    assert [item["job_name"] for item in selections if item["is_primary"]] == ["工业视觉工程师"]

    # 取消主岗位标记：允许出现"没有主岗位"的中间状态
    demoted = await client.patch(
        f"/api/students/{student['id']}/jobs/{first.json()['id']}", json={"is_primary": False}
    )
    assert demoted.json()["is_primary"] is False
    selections = (await client.get(f"/api/students/{student['id']}/jobs")).json()
    assert all(item["is_primary"] is False for item in selections)

    # 恢复主岗位后再删除，删除后剩余记录不受影响
    await client.patch(f"/api/students/{student['id']}/jobs/{first.json()['id']}", json={"is_primary": True})

    removed = await client.delete(f"/api/students/{student['id']}/jobs/{first.json()['id']}")
    assert removed.status_code == 200
    assert len((await client.get(f"/api/students/{student['id']}/jobs")).json()) == 1

    missing_student = await client.get("/api/students/9999/jobs")
    assert missing_student.status_code == 200


@pytest.mark.asyncio
async def test_student_skill_list_and_manual_patch(client: httpx.AsyncClient, db_session) -> None:
    from app.crud.job_skill import StudentSkillRepository

    student = await _create_student(client, "2026002")
    tree = await _create_tree(client)
    node = await _create_node(client, tree["id"], "IMG_FILTER", "图像滤波")

    # 进度记录由业务链路（P2 项目完成）生成，这里直接落一条再走接口
    from app.models.job_skill import StudentSkill

    skill = StudentSkill(student_id=student["id"], skill_node_id=node["id"], state="LOCKED")
    db_session.add(skill)
    await db_session.flush()

    listed = (await client.get(f"/api/students/{student['id']}/skills")).json()
    assert listed[0]["node_name"] == "图像滤波" and listed[0]["tree_name"] == "传统算法系"

    patched = await client.patch(
        f"/api/students/{student['id']}/skills/{skill.id}",
        json={"progress": 50, "level": 1},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["source"] == "MANUAL"
    assert Decimal(str(body["progress"])) == Decimal("50")

    stored = await StudentSkillRepository(db_session).get(skill.id)
    assert stored is not None and Decimal(str(stored.progress)) == Decimal("50")

    wrong_student = await client.patch(f"/api/students/{student['id']}/skills/9999", json={"progress": 80})
    assert wrong_student.status_code == 200
