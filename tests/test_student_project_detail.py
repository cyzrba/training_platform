"""学生在实训项目上的全量详情接口测试。

覆盖：任务简介、关卡与子标题（含子标题简介）、当前作答状态、历史提交次数与日期、
每次提交上的 AI / 教师评语。
"""

from decimal import Decimal

import httpx
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crud.attempt import (
    AttemptStageFileRepository,
    AttemptStageRepository,
    FileAssetRepository,
    ProjectSubmissionRepository,
    StudentProjectRepository,
    TrainingAttemptRepository,
)
from app.crud.project import TrainingProjectRepository
from app.crud.review import ReviewRecordRepository
from tests import helpers

DETAIL = "/api/students/{student_id}/projects/{project_id}"


# --------------------------------------------------------------------- 造数


async def _user(client: httpx.AsyncClient, user_no: str, name: str, user_type: str = "STUDENT") -> dict:
    response = await client.post(
        "/api/users", json={"user_no": user_no, "real_name": name, "user_type": user_type}
    )
    assert response.status_code == 201, response.text
    if user_type == "STUDENT":
        # 学生要看到项目，得先在班里、并且老师把项目发给他（见 docs/方案设计.md §4.2）
        await helpers.enroll(client, user_no)
    return response.json()


async def _template(client: httpx.AsyncClient, name: str, *, default_requirement: str | None = None) -> dict:
    response = await client.post(
        "/api/stage-templates",
        json={
            "stage_name": name,
            "description": f"{name}要做什么的说明",
            "default_requirement": default_requirement,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _published_project(db: AsyncSession, name: str, description: str) -> dict:
    """直接落库建一个已发布项目（跳过发布校验，本用例不测发布规则）。"""
    project = await TrainingProjectRepository(db).create(
        {
            "project_name": name,
            "project_level": "BASIC",
            "difficulty": 4,
            "description": description,
            "status": "PUBLISHED",
        }
    )
    return {"id": project.id}


async def _module(
    client: httpx.AsyncClient, project_id: int, template_id: int, stage_no: int, items: list[dict]
) -> dict:
    response = await client.post(
        f"/api/projects/{project_id}/modules",
        json={"template_id": template_id, "stage_no": stage_no, "weight": 50, "items_json": items},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _attempt(
    db: AsyncSession,
    student_id: int,
    project_id: int,
    module_ids: list[int],
    *,
    best_score: str | None = None,
) -> dict:
    record = await StudentProjectRepository(db).create(
        {
            "student_id": student_id,
            "project_id": project_id,
            "status": "SUBMITTED",
            "progress": Decimal("100.00"),
            "best_score": Decimal(best_score) if best_score else None,
        }
    )
    attempt = await TrainingAttemptRepository(db).create(
        {"student_project_id": record.id, "attempt_no": 1, "status": "SUBMITTED"}
    )
    stages = []
    for module_id in module_ids:
        stages.append(
            await AttemptStageRepository(db).create(
                {
                    "attempt_id": attempt.id,
                    "project_module_id": module_id,
                    "is_filled": True,
                    "answer_text": f"关卡 {module_id} 的作答",
                }
            )
        )
    return {"record_id": record.id, "attempt_id": attempt.id, "stages": stages}


async def _extra_attempt(
    db: AsyncSession, record_id: int, attempt_no: int, module_ids: list[int], answers: dict[int, str]
) -> int:
    """再开一轮闯关：只给 ``answers`` 里列出的关卡写作答，其余留空（模拟"没写"）。"""
    attempt = await TrainingAttemptRepository(db).create(
        {"student_project_id": record_id, "attempt_no": attempt_no, "status": "IN_PROGRESS"}
    )
    for module_id in module_ids:
        text = answers.get(module_id)
        await AttemptStageRepository(db).create(
            {
                "attempt_id": attempt.id,
                "project_module_id": module_id,
                "is_filled": bool(text),
                "answer_text": text,
            }
        )
    return attempt.id


# ------------------------------------------------------------------ 用例


@pytest.mark.asyncio
async def test_student_project_detail_with_history_and_reviews(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """项目详情：任务简介 + 关卡（含子标题简介）+ 提交历史（次数/日期/评语）。"""
    student = await _user(client, "2026001", "张三")
    teacher = await _user(client, "T0001", "李老师", user_type="TEACHER")
    template_a = await _template(client, "需求分析", default_requirement="逐条写需求")
    template_b = await _template(client, "实训报告上传")

    project = await _published_project(db_session, "工业缺陷检测实训", "工业视觉缺陷检测全流程交付")
    await helpers.publish(client, [project["id"]])
    module_a = await _module(
        client,
        project["id"],
        template_a["id"],
        1,
        [
            {"title": "检测对象描述", "prompt": "工件名称、材质、尺寸范围"},
            {"title": "缺陷类型定义"},  # 没填简介 → 返回 null
        ],
    )
    module_b = await _module(client, project["id"], template_b["id"], 2, [{"title": "报告文件"}])

    attempt = await _attempt(
        db_session,
        student["id"],
        project["id"],
        [module_a["id"], module_b["id"]],
        best_score="88.5",
    )

    # 第一关挂一个附件，验证 file_count
    asset = await FileAssetRepository(db_session).create(
        {
            "uploader_id": student["id"],
            "bucket": "training-platform",
            "object_key": "submissions/1/report.pdf",
            "original_name": "实训报告.pdf",
            "biz_type": "SUBMISSION",
            "size_bytes": 1024,
        }
    )
    await AttemptStageFileRepository(db_session).create(
        {"attempt_stage_id": attempt["stages"][0].id, "file_asset_id": asset.id}
    )

    # 一次整单提交 + AI 评语 + 教师复审评语
    submission = await ProjectSubmissionRepository(db_session).create(
        {
            "attempt_id": attempt["attempt_id"],
            "submit_no": 1,
            "status": "REVIEWED",
            "final_conclusion": "PASS",
            "total_score": Decimal("88.5"),
        }
    )
    reviews = ReviewRecordRepository(db_session)
    await reviews.create(
        {
            "submission_id": submission.id,
            "review_kind": "AI",
            "status": "FINAL",
            "total_score": Decimal("86"),
            "conclusion": "PASS",
            "comment": "需求覆盖完整，光源方案缺少对比实验",
            "ai_model": "deepseek-v4-flash",
            "dimension_json": [{"name": "需求分析", "score": "45", "weight": "50", "reason": "要点齐全"}],
        }
    )
    await reviews.create(
        {
            "submission_id": submission.id,
            "review_kind": "TEACHER",
            "status": "FINAL",
            "reviewer_id": teacher["id"],
            "total_score": Decimal("88.5"),
            "conclusion": "PASS",
            "comment": "补充的光源对比实验可以支撑结论，按此定稿",
        }
    )

    detail = (await client.get(DETAIL.format(student_id=student["id"], project_id=project["id"]))).json()
    assert detail["project_name"] == "工业缺陷检测实训"
    assert detail["intro"] == "工业视觉缺陷检测全流程交付"
    assert (detail["project_level"], detail["level_name"], detail["difficulty"]) == ("BASIC", "基础", 4)
    assert (detail["status"], detail["best_score"]) == ("SUBMITTED", 88.5)
    assert (detail["level_total"], detail["level_done"], detail["attempt_count"]) == (2, 2, 1)
    assert (detail["current_attempt_id"], detail["current_attempt_no"]) == (attempt["attempt_id"], 1)

    # 关卡按 stage_no 排序，带说明 / 要求 / 验收标准 / 子标题与子标题简介
    first, second = detail["levels"]
    assert (first["stage_no"], first["stage_name"]) == (1, "需求分析")
    assert first["description"] == "需求分析要做什么的说明"
    assert first["requirement"] == "逐条写需求"
    assert first["weight"] == 50
    assert first["sub_titles"] == [
        {"title": "检测对象描述", "prompt": "工件名称、材质、尺寸范围"},
        {"title": "缺陷类型定义", "prompt": None},
    ]
    assert first["attempt_stage_id"] == attempt["stages"][0].id  # 保存作答用它
    assert (first["is_filled"], first["answer_text"], first["file_count"]) == (
        True,
        f"关卡 {module_a['id']} 的作答",
        1,
    )
    assert first["answer_saved_at"] is not None
    assert second["stage_name"] == "实训报告上传"
    assert second["sub_titles"] == [{"title": "报告文件", "prompt": None}]

    # 提交历史：次数、日期与 AI / 教师评语
    assert detail["submission_count"] == 1
    history = detail["submissions"][0]
    assert (history["submit_no"], history["status"], history["final_conclusion"]) == (1, "REVIEWED", "PASS")
    assert history["submitted_at"] and history["attempt_no"] == 1

    ai_review, teacher_review = history["reviews"]
    assert (ai_review["review_kind"], ai_review["comment"]) == ("AI", "需求覆盖完整，光源方案缺少对比实验")
    assert ai_review["dimensions"][0]["name"] == "需求分析"
    assert ai_review["reviewer_name"] is None
    assert (teacher_review["review_kind"], teacher_review["reviewer_name"]) == ("TEACHER", "李老师")
    assert teacher_review["comment"] == "补充的光源对比实验可以支撑结论，按此定稿"


@pytest.mark.asyncio
async def test_student_project_detail_for_untouched_student(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """学生没开始过的项目：状态 NOT_STARTED、没有提交历史，但关卡与子标题照常返回。"""
    student = await _user(client, "2026001", "张三")
    template = await _template(client, "需求分析")
    project = await _published_project(db_session, "工业缺陷检测实训", "任务简介")
    await helpers.publish(client, [project["id"]])
    await _module(
        client, project["id"], template["id"], 1, [{"title": "检测对象描述", "prompt": "写清楚工件"}]
    )

    detail = (await client.get(DETAIL.format(student_id=student["id"], project_id=project["id"]))).json()
    assert (detail["status"], detail["best_score"], detail["submission_count"]) == ("NOT_STARTED", None, 0)
    assert (detail["level_total"], detail["level_done"], detail["attempt_count"]) == (1, 0, 0)
    assert detail["levels"][0]["is_filled"] is False
    assert detail["submissions"] == []
    assert detail["levels"][0]["sub_titles"][0]["prompt"] == "写清楚工件"

    missing_project = await client.get(DETAIL.format(student_id=student["id"], project_id=999))
    assert missing_project.json()["code"] == 422
    missing_student = await client.get(DETAIL.format(student_id=999, project_id=project["id"]))
    assert missing_student.json()["code"] == 422


@pytest.mark.asyncio
async def test_save_answers_draft_and_resume(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """点「保存作答」把本轮填了一半的内容存下来，下次进来接着写（拿的是本轮已保存的作答）。"""
    student = await _user(client, "2026001", "张三")
    template_a = await _template(client, "需求分析")
    template_b = await _template(client, "实训报告上传")
    project = await _published_project(db_session, "工业缺陷检测实训", "任务简介")
    await helpers.publish(client, [project["id"]])
    module_a = await _module(client, project["id"], template_a["id"], 1, [{"title": "检测对象描述"}])
    module_b = await _module(client, project["id"], template_b["id"], 2, [{"title": "报告文件"}])

    record = await StudentProjectRepository(db_session).create(
        {
            "student_id": student["id"],
            "project_id": project["id"],
            "status": "IN_PROGRESS",
            "progress": Decimal("0.00"),
            "best_score": Decimal("70"),
        }
    )
    # 第 1 轮写过内容；第 2 轮重新挑战，两关都是空的
    await _extra_attempt(
        db_session, record.id, 1, [module_a["id"], module_b["id"]], {module_a["id"]: "第一轮写的需求分析"}
    )
    round2 = await _extra_attempt(db_session, record.id, 2, [module_a["id"], module_b["id"]], {})

    detail = (await client.get(DETAIL.format(student_id=student["id"], project_id=project["id"]))).json()
    assert (detail["current_attempt_id"], detail["current_attempt_no"], detail["attempt_count"]) == (
        round2,
        2,
        2,
    )
    assert [level["answer_text"] for level in detail["levels"]] == [None, None]

    stage_ids = [level["attempt_stage_id"] for level in detail["levels"]]

    # 只写了一半 → 点「保存作答」（只存文本，不改关卡完成状态）
    saved = (
        await client.put(
            f"/api/attempts/{round2}/answers",
            json={"answers": [{"attempt_stage_id": stage_ids[0], "answer_text": "第二轮写了一半的需求分析"}]},
        )
    ).json()
    assert saved["saved_count"] == 1
    assert (saved["filled_stage_count"], saved["stage_total"], saved["progress"]) == (0, 2, 0)
    assert saved["saved_at"]

    # 重新进来：拿到的是本轮之前保存的全部作答，而不是第一轮的
    again = (await client.get(DETAIL.format(student_id=student["id"], project_id=project["id"]))).json()
    assert again["levels"][0]["answer_text"] == "第二轮写了一半的需求分析"
    assert again["levels"][1]["answer_text"] is None
    assert again["levels"][0]["is_filled"] is False  # 草稿不会把关卡标记成已完成
    assert (again["level_done"], again["progress"]) == (0, 0)

    # 同一个接口也能顺手把写好的关卡标记为完成
    done = (
        await client.put(
            f"/api/attempts/{round2}/answers",
            json={
                "answers": [
                    {
                        "attempt_stage_id": stage_ids[0],
                        "answer_text": "第二轮写完了需求分析",
                        "is_filled": True,
                    }
                ]
            },
        )
    ).json()
    assert (done["filled_stage_count"], done["progress"]) == (1, 50.0)
    after = (await client.get(DETAIL.format(student_id=student["id"], project_id=project["id"]))).json()
    assert (after["level_done"], after["levels"][0]["is_filled"]) == (1, True)
    assert after["levels"][0]["answer_text"] == "第二轮写完了需求分析"

    # 边界：不是本轮的关卡 / 本轮已提交后再保存
    foreign = await client.put(
        f"/api/attempts/{round2}/answers",
        json={"answers": [{"attempt_stage_id": 99999, "answer_text": "x"}]},
    )
    assert foreign.json()["code"] == 422

    attempts = TrainingAttemptRepository(db_session)
    await attempts.update(await attempts.get(round2), {"status": "SUBMITTED"})
    locked = await client.put(
        f"/api/attempts/{round2}/answers",
        json={"answers": [{"attempt_stage_id": stage_ids[1], "answer_text": "x"}]},
    )
    assert locked.json()["code"] == 422
