"""闯关与评审链路测试：开始闯关 → 作答 → 提交 → 评审定稿 → 技能进度。"""

from decimal import Decimal

import httpx
import pytest

from tests import helpers

TEMPLATES = "/api/stage-templates"


async def _student(client: httpx.AsyncClient, user_no: str = "2026001") -> dict:
    response = await client.post(
        "/api/users", json={"user_no": user_no, "real_name": "张三", "user_type": "STUDENT"}
    )
    assert response.status_code == 201, response.text
    # 学生要能看到项目，得先在班里、并且老师把项目发给他（见 docs/方案设计.md §4.2）
    await helpers.enroll(client, user_no)
    return response.json()


async def _skill(client: httpx.AsyncClient, node_name: str = "图像基础") -> dict:
    tree = (await client.get("/api/skill-trees")).json()
    if tree["total"] == 0:
        tree = (await client.post("/api/skill-trees", json={"tree_name": "视觉系"})).json()
    else:
        tree = tree["items"][0]
    return (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_name": node_name},
        )
    ).json()


async def _template(client: httpx.AsyncClient, name: str) -> dict:
    """模块库按名称唯一：同名关卡直接复用，避免第二个项目建重复名称。"""
    listed = (await client.get(TEMPLATES, params={"keyword": name})).json()
    existing = next((item for item in listed["items"] if item["stage_name"] == name), None)
    if existing is not None:
        return existing
    return (await client.post(TEMPLATES, json={"stage_name": name})).json()


async def _project(
    client: httpx.AsyncClient,
    name: str,
    *,
    weights: tuple[int, int] = (50, 50),
    skill_ids: tuple[int, ...] = (),
    publish: bool = True,
    items: list[dict] | None = None,
) -> dict:
    """建一个"两个关卡"的项目，默认直接发布。"""
    first = await _template(client, "需求分析")
    second = await _template(client, "方案设计")
    project = (
        await client.post("/api/projects", json={"project_name": name, "project_level": "BASIC"})
    ).json()
    for template, weight in zip((first, second), weights, strict=True):
        await client.post(
            f"/api/projects/{project['id']}/modules",
            json={"template_id": template["id"], "weight": weight, "items_json": items or []},
        )
    if skill_ids:
        await client.put(f"/api/projects/{project['id']}/skills", json={"skill_node_ids": list(skill_ids)})
    if publish:
        published = await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
        assert published.json()["status"] == "PUBLISHED", published.text
        await helpers.publish(client, [project["id"]])
    return project


@pytest.mark.asyncio
async def test_start_attempt_creates_record_and_stages(client: httpx.AsyncClient) -> None:
    student = await _student(client)
    project = await _project(
        client,
        "缺陷检测实训",
        items=[{"title": "检测对象描述", "prompt": "工件与精度"}],
    )

    started = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    assert started["attempt_no"] == 1 and started["status"] == "IN_PROGRESS"
    assert started["project_name"] == "缺陷检测实训"
    assert [stage["stage_name"] for stage in started["stages"]] == ["需求分析", "方案设计"]
    assert started["stages"][0]["items_json"][0]["title"] == "检测对象描述"
    assert all(stage["is_filled"] is False for stage in started["stages"])

    record = (await client.get(f"/api/student-projects/{started['student_project_id']}")).json()
    assert record["status"] == "IN_PROGRESS" and record["attempt_count"] == 1
    assert len(record["attempts"]) == 1

    # 重新挑战：新开一轮，作答行重新给
    again = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    assert again["attempt_no"] == 2
    assert (await client.get(f"/api/student-projects/{started['student_project_id']}")).json()[
        "attempt_count"
    ] == 2

    # 草稿项目不能开始（发布后对所有学生开放，草稿 / 已下架仍然不开放）
    draft = await _project(client, "草稿项目", publish=False)
    blocked = await client.post(f"/api/students/{student['id']}/projects/{draft['id']}/start")
    assert blocked.json()["code"] == 422
    assert "还没发布" in blocked.json()["msg"]


@pytest.mark.asyncio
async def test_save_stage_submit_and_withdraw(client: httpx.AsyncClient) -> None:
    student = await _student(client)
    project = await _project(client, "提交实训")
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    stages = attempt["stages"]

    # 填第一关：不传 is_filled，按有没有内容自动判定
    saved = (
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stages[0]['id']}",
            json={"answer_text": "检测对象是手机盖板"},
        )
    ).json()
    assert saved["is_filled"] is True and saved["filled_at"] is not None

    record = (await client.get(f"/api/student-projects/{attempt['student_project_id']}")).json()
    assert Decimal(str(record["progress"])) == Decimal(50)  # 两个关卡填了一个

    # 必填没填完不能提交
    blocked = await client.post(f"/api/attempts/{attempt['id']}/submit")
    assert blocked.json()["code"] == 422
    assert "必填关卡" in blocked.json()["msg"]

    await client.patch(
        f"/api/attempts/{attempt['id']}/stages/{stages[1]['id']}",
        json={"answer_text": "采用背光 + 2000 万像素相机"},
    )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()
    assert submission["status"] == "PENDING_AI" and submission["submit_no"] == 1

    # 提交后不能再改作答
    frozen = await client.patch(
        f"/api/attempts/{attempt['id']}/stages/{stages[0]['id']}", json={"answer_text": "改一下"}
    )
    assert frozen.json()["code"] == 422

    # 自动排了一条 AI 评审任务
    jobs = (await client.get(f"/api/submissions/{submission['id']}/ai-jobs")).json()
    assert [job["job_status"] for job in jobs] == ["QUEUED"]

    # 撤回 → 回到可编辑，再提交就是第 2 次
    await client.post(f"/api/submissions/{submission['id']}/withdraw")
    reopened = (await client.get(f"/api/attempts/{attempt['id']}")).json()
    assert reopened["status"] == "IN_PROGRESS"
    record = (await client.get(f"/api/student-projects/{attempt['student_project_id']}")).json()
    assert record["status"] == "IN_PROGRESS"
    resubmitted = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()
    assert resubmitted["submit_no"] == 2


@pytest.mark.asyncio
async def test_review_finalize_completes_project_and_updates_skills(
    client: httpx.AsyncClient,
) -> None:
    """教师评审定稿 PASS → 项目完成 → 该项目关联的技能点进度重算。"""
    first_skill = await _skill(client, "图像基础")
    second_skill = await _skill(client, "边缘检测")
    student = await _student(client)
    project = await _project(client, "缺陷检测实训", skill_ids=(first_skill["id"], second_skill["id"]))

    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答内容"}
        )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()

    # 草稿评审不产生任何结算
    draft = (
        await client.post(
            f"/api/submissions/{submission['id']}/reviews",
            json={"review_kind": "TEACHER", "status": "DRAFT", "total_score": 85, "comment": "待补充"},
        )
    ).json()
    assert draft["status"] == "DRAFT" and draft["version_no"] == 1
    assert (await client.get(f"/api/students/{student['id']}/skills")).json() == []

    # 定稿 PASS
    finalized = (
        await client.patch(
            f"/api/reviews/{draft['id']}",
            json={"status": "FINAL", "conclusion": "PASS", "total_score": 88},
        )
    ).json()
    assert finalized["status"] == "FINAL"

    detail = (await client.get(f"/api/submissions/{submission['id']}")).json()
    assert detail["status"] == "REVIEWED" and detail["final_conclusion"] == "PASS"

    record = (await client.get(f"/api/student-projects/{attempt['student_project_id']}")).json()
    assert record["status"] == "COMPLETED"
    assert Decimal(str(record["completed_score"])) == Decimal(88)
    assert record["completed_at"] is not None

    # 项目完成 → 两个技能点各推进 100%（唯一关联项目就是这个项目）
    skills = (await client.get(f"/api/students/{student['id']}/skills")).json()
    assert {skill["node_name"] for skill in skills} == {"图像基础", "边缘检测"}
    for skill in skills:
        assert Decimal(str(skill["progress"])) == Decimal(100)
        assert skill["source"] == "PROJECT"
        assert skill["mastered_at"] is not None


@pytest.mark.asyncio
async def test_skill_progress_uses_completed_over_related_projects(
    client: httpx.AsyncClient,
) -> None:
    """进度 = 完成项目数 ÷ 关联项目总数：只完成 2 个关联项目里的 1 个 → 50%。"""
    skill = await _skill(client, "模型训练")
    student = await _student(client)
    first = await _project(client, "项目甲", skill_ids=(skill["id"],))
    await _project(client, "项目乙", skill_ids=(skill["id"],))

    attempt = (await client.post(f"/api/students/{student['id']}/projects/{first['id']}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答"}
        )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()
    await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={"review_kind": "TEACHER", "status": "FINAL", "conclusion": "PASS", "total_score": 90},
    )

    skills = (await client.get(f"/api/students/{student['id']}/skills")).json()
    assert Decimal(str(skills[0]["progress"])) == Decimal(50)
    assert skills[0]["activated_at"] is not None
    assert skills[0]["mastered_at"] is None

    # 手动重算结果一致
    recalculated = (await client.post(f"/api/students/{student['id']}/skills/recalculate")).json()
    assert recalculated == {"student_id": student["id"], "updated": 1}


@pytest.mark.asyncio
async def test_review_fail_keeps_project_open(client: httpx.AsyncClient) -> None:
    skill = await _skill(client, "图像滤波")
    student = await _student(client)
    project = await _project(client, "未通过实训", skill_ids=(skill["id"],))
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答"}
        )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()

    await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={"review_kind": "TEACHER", "status": "FINAL", "conclusion": "FAIL", "total_score": 40},
    )
    record = (await client.get(f"/api/student-projects/{attempt['student_project_id']}")).json()
    assert record["status"] == "IN_PROGRESS" and record["completed_at"] is None

    # 没通过就不推进技能进度
    skills = (await client.get(f"/api/students/{student['id']}/skills")).json()
    assert skills == [] or all(Decimal(str(skill["progress"])) == Decimal(0) for skill in skills)


@pytest.mark.asyncio
async def test_ai_review_and_teacher_star(client: httpx.AsyncClient) -> None:
    student = await _student(client)
    project = await _project(client, "AI 评审实训")
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答"}
        )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()

    # AI 定稿通过 → 提交状态 AI_PASSED
    await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={
            "review_kind": "AI",
            "status": "FINAL",
            "conclusion": "PASS",
            "total_score": 92,
            "ai_model": "gpt-4o",
            "dimension_json": [{"name": "需求完整度", "score": 90, "weight": 50, "reason": "较完整"}],
        },
    )
    detail = (await client.get(f"/api/submissions/{submission['id']}")).json()
    assert detail["status"] == "AI_PASSED" and detail["final_conclusion"] == "PASS"

    # 教师标星 + 看板筛选
    starred = (await client.patch(f"/api/submissions/{submission['id']}", json={"is_starred": True})).json()
    assert starred["is_starred"] is True
    board = (await client.get("/api/submissions", params={"is_starred": "true"})).json()
    assert board["total"] == 1
    assert board["items"][0]["project_name"] == "AI 评审实训"
    assert board["items"][0]["attempt_no"] == 1

    # 已定稿的评审不能删除，草稿可以
    reviews = (await client.get(f"/api/submissions/{submission['id']}/reviews")).json()
    blocked = await client.delete(f"/api/reviews/{reviews[0]['id']}")
    assert blocked.json()["code"] == 422


@pytest.mark.asyncio
async def test_objection_then_teacher_review_can_rollback(client: httpx.AsyncClient) -> None:
    """异议路线：AI 判通过 → 学生留言提异议 → 教师复核改判不通过 → 撤销完成并回滚技能进度。"""
    skill = await _skill(client, "图像基础")
    student = await _student(client)
    project = await _project(client, "异议实训", skill_ids=(skill["id"],))
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答"}
        )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()

    # AI 判定通过 → 按方案 A 直接算完成、技能进度推进
    await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={
            "review_kind": "AI",
            "status": "FINAL",
            "conclusion": "PASS",
            "total_score": 82,
            "comment": "整体完成度较高",
            "ai_model": "gpt-4o",
        },
    )
    record = (await client.get(f"/api/student-projects/{attempt['student_project_id']}")).json()
    assert record["status"] == "COMPLETED"
    assert Decimal(
        str((await client.get(f"/api/students/{student['id']}/skills")).json()[0]["progress"])
    ) == Decimal(100)

    # 学生提异议：必须带留言；还没复审前可以改留言
    objection = (
        await client.post(
            f"/api/submissions/{submission['id']}/objection",
            json={"reason": "我的光源选型方案写了对比实验，AI 没有算进去，申请人工复核"},
        )
    ).json()
    assert objection["status"] == "PENDING_REVIEW"
    assert "光源选型" in objection["objection_reason"]

    updated = (
        await client.post(
            f"/api/submissions/{submission['id']}/objection",
            json={"reason": "补充说明：对比实验在第 2 关作答末尾"},
        )
    ).json()
    assert "补充说明" in updated["objection_reason"]

    # 空留言不允许
    empty = await client.post(f"/api/submissions/{submission['id']}/objection", json={"reason": ""})
    assert empty.json()["code"] == 422

    # 已进入复核流程的提交不能撤回
    blocked_withdraw = await client.post(f"/api/submissions/{submission['id']}/withdraw")
    assert blocked_withdraw.json()["code"] == 422
    assert "复核" in blocked_withdraw.json()["msg"]

    # 教师认领 → 复核中
    claimed = (await client.post(f"/api/submissions/{submission['id']}/claim")).json()
    assert claimed["status"] == "REVIEWING"

    # 教师改判不通过 → 撤销完成、技能进度回落到 0
    await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={
            "review_kind": "TEACHER",
            "status": "FINAL",
            "conclusion": "FAIL",
            "total_score": 55,
            "comment": "方案缺少对比实验数据，需补充后重新提交",
        },
    )
    detail = (await client.get(f"/api/submissions/{submission['id']}")).json()
    assert detail["status"] == "REVIEWED" and detail["final_conclusion"] == "FAIL"
    assert "对比实验" in detail["reviews"][-1]["comment"]

    rolled = (await client.get(f"/api/student-projects/{attempt['student_project_id']}")).json()
    assert rolled["status"] == "IN_PROGRESS"
    assert rolled["completed_at"] is None and rolled["completed_score"] is None
    assert Decimal(str(rolled["progress"])) == Decimal(100)  # 关卡还是填满的，只是没通过

    skills = (await client.get(f"/api/students/{student['id']}/skills")).json()
    assert Decimal(str(skills[0]["progress"])) == Decimal(0)
    assert skills[0]["mastered_at"] is None

    # 学生改完可以重新提交（新的一次 submit_no）
    resubmitted = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()
    assert resubmitted["submit_no"] == 2


@pytest.mark.asyncio
async def test_objection_guards(client: httpx.AsyncClient) -> None:
    """AI 还没评完不能提异议；已复审的也不能再提。"""
    student = await _student(client)
    project = await _project(client, "异议校验实训")
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答"}
        )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()

    early = await client.post(
        f"/api/submissions/{submission['id']}/objection", json={"reason": "AI 还没出结果"}
    )
    assert early.json()["code"] == 422
    assert "还没出结果" in early.json()["msg"]

    await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={"review_kind": "TEACHER", "status": "FINAL", "conclusion": "PASS", "total_score": 90},
    )
    after = await client.post(f"/api/submissions/{submission['id']}/objection", json={"reason": "太晚了"})
    assert after.json()["code"] == 422
    assert "已经复审结束" in after.json()["msg"]


@pytest.mark.asyncio
async def test_pass_fail_judged_by_configured_score(client: httpx.AsyncClient, db_session) -> None:
    """项目是否通过由配置及格线判定（达到即通过）：评审人只给分数与评语，塞结论也无效。"""
    from app.crud.job_skill import GrowthRuleRepository

    await GrowthRuleRepository(db_session).create(
        {"level_type": "BASIC", "pass_score": Decimal(60), "skill_max_level": 1}
    )
    student = await _student(client)
    project = await _project(client, "分数线实训")

    async def new_attempt() -> dict:
        """判定可能把轮次判结束，所以每个用例都重新开一轮。"""
        attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
        for stage in attempt["stages"]:
            await client.patch(
                f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": "作答"}
            )
        return attempt

    record_id = (await new_attempt())["student_project_id"]

    async def submit_and_review(score: str, kind: str = "TEACHER", **extra: object) -> dict:
        attempt = await new_attempt()
        submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()
        review = (
            await client.post(
                f"/api/submissions/{submission['id']}/reviews",
                json={
                    "review_kind": kind,
                    "status": "FINAL",
                    "total_score": score,
                    "comment": "评语内容",
                    **extra,
                },
            )
        ).json()
        return {
            "review": review,
            "detail": (await client.get(f"/api/submissions/{submission['id']}")).json(),
        }

    # 差一点到线：59.99 → FAIL，项目不完成
    below = await submit_and_review("59.99")
    assert below["review"]["conclusion"] == "FAIL"
    assert below["detail"]["final_conclusion"] == "FAIL"
    record = (await client.get(f"/api/student-projects/{record_id}")).json()
    assert record["status"] == "IN_PROGRESS"

    # 分数正好等于及格线：达到即通过 → PASS，项目完成
    equal = await submit_and_review("60")
    assert equal["review"]["conclusion"] == "PASS"
    assert equal["detail"]["final_conclusion"] == "PASS"
    record = (await client.get(f"/api/student-projects/{record_id}")).json()
    assert record["status"] == "COMPLETED"

    # 高于及格线 → 同样 PASS
    above = await submit_and_review("60.01")
    assert above["review"]["conclusion"] == "PASS"

    # 评审人硬塞 conclusion 无效：55 分写 PASS 也判 FAIL
    forced = await submit_and_review("55", kind="AI", conclusion="PASS")
    assert forced["review"]["conclusion"] == "FAIL"
    assert forced["detail"]["status"] == "AI_FAILED"

    # 定稿不给分数直接被拒
    attempt = await new_attempt()
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()
    no_score = await client.post(
        f"/api/submissions/{submission['id']}/reviews",
        json={"review_kind": "AI", "status": "FINAL", "comment": "没给分"},
    )
    assert no_score.json()["code"] == 422
    assert "必须给出分数" in no_score.json()["msg"]
