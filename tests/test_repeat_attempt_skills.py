"""同一个学生重复闯关，对技能进度的影响。

技能进度的公式是 ``progress = 完成项目数 ÷ 关联项目总数 × 100``（见 app/services/skill.py），
而"完成"看的是 ``student_project.status``——**同一个学生同一个项目只有一条实训记录**，
重复闯关不会新增记录，只会把这条记录重新拉回 IN_PROGRESS 再走一遍评审。
所以真正要验证的是三种情况：

1. 重复闯关**通过** → 技能保持点亮，最高分择优保留；
2. 重复闯关**不通过** → 之前点亮的技能要**回滚**（否则刷一次不及格也不掉进度）；
3. 回滚只影响这一个项目，同一技能下其它已完成项目不受牵连。
"""

import json

import httpx
import pytest

from app.services import ai_review, settings_store
from tests import helpers

#: 及格线兜底是 60（没有 growth_rule 时），90 通过、30 不通过
PASS_SCORE = 90.0
FAIL_SCORE = 30.0

CRITERIA_MD = """# 评分标准

## 一、需求分析（100 分）

要点齐全得满分；缺一项扣 20 分。
"""


class _FakeLlm:
    """可控分数的大模型替身：换分数只要改 ``score``。"""

    def __init__(self) -> None:
        self.score = PASS_SCORE
        self.calls = 0

    async def __call__(self, config, *, system_prompt: str, user_prompt: str) -> tuple[str, str | None]:
        self.calls += 1
        payload = {"total_score": self.score, "dimensions": [], "comment": f"得分 {self.score}"}
        return json.dumps(payload, ensure_ascii=False), None


def _stub_config(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(_session) -> settings_store.LLMConfig:
        return settings_store.LLMConfig(
            provider="openai-compatible",
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            api_key="sk-test",
            temperature=0.2,
            timeout=10,
            max_tokens=512,
        )

    monkeypatch.setattr(settings_store, "get_llm_config", _fake)


# ------------------------------------------------------------------ 造数据


async def _skill_node(client: httpx.AsyncClient) -> dict:
    trees = (await client.get("/api/skill-trees")).json()
    tree = (
        trees["items"][0]
        if trees["total"]
        else (await client.post("/api/skill-trees", json={"tree_name": "视觉系"})).json()
    )
    return (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_name": "引脚检测基础"},
        )
    ).json()


async def _template(client: httpx.AsyncClient, name: str) -> dict:
    """模块库按名称唯一：多个项目共用同一个关卡模板。"""
    listed = (await client.get("/api/stage-templates", params={"keyword": name})).json()
    existing = next((item for item in listed["items"] if item["stage_name"] == name), None)
    if existing is not None:
        return existing
    return (await client.post("/api/stage-templates", json={"stage_name": name})).json()


async def _project(client: httpx.AsyncClient, name: str, *, skill_id: int) -> dict:
    """建一个"单关卡 + 已发布 + 关联技能 + 带评分标准"的项目。"""
    template = await _template(client, "需求分析")
    project = (
        await client.post("/api/projects", json={"project_name": name, "project_level": "BASIC"})
    ).json()
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": template["id"], "weight": 100},
    )
    await client.put(f"/api/projects/{project['id']}/skills", json={"skill_node_ids": [skill_id]})
    await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    await helpers.publish(client, [project["id"]])
    uploaded = (
        await client.post(
            f"/api/projects/{project['id']}/files/upload",
            files={"file": ("评分标准.md", CRITERIA_MD.encode(), "text/markdown")},
            data={"file_kind": "SCORING_CRITERIA", "title": "评分标准"},
        )
    ).json()
    assert uploaded["knowledge_status"] == "READY", uploaded
    return project


async def _student(client: httpx.AsyncClient, user_no: str) -> dict:
    student = (
        await client.post(
            "/api/users", json={"user_no": user_no, "real_name": "重复闯关学生", "user_type": "STUDENT"}
        )
    ).json()
    await helpers.enroll(client, user_no)
    return student


async def _attempt_and_submit(
    client: httpx.AsyncClient, student_id: int, project_id: int, *, answer: str
) -> dict:
    """开始一轮闯关 → 填满所有关卡 → 整单提交。"""
    attempt = (await client.post(f"/api/students/{student_id}/projects/{project_id}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}", json={"answer_text": answer}
        )
    return (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()


async def _skill_progress(client: httpx.AsyncClient, student_id: int, skill_node_id: int) -> dict:
    rows = (await client.get(f"/api/students/{student_id}/skills")).json()
    return next(item for item in rows if item["skill_node_id"] == skill_node_id)


def _progress_of(row: dict) -> float:
    """progress 是 Decimal，序列化后是字符串，比较前统一转 float。"""
    return float(row["progress"])


async def _record(client: httpx.AsyncClient, student_id: int, project_id: int) -> dict:
    project = (await client.get(f"/api/projects/{project_id}")).json()
    rows = (await client.get("/api/student-projects", params={"student_id": student_id})).json()
    return next(item for item in rows["items"] if item["project_id"] == project["id"])


# ------------------------------------------------------------------ 用例


@pytest.mark.asyncio
async def test_repeat_pass_keeps_skill_and_keeps_best_score(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复闯关通过：进度不掉，最高分只升不降。"""
    llm = _FakeLlm()
    monkeypatch.setattr(ai_review, "call_llm", llm)
    _stub_config(monkeypatch)

    skill = await _skill_node(client)
    other = await _project(client, "另一个项目", skill_id=skill["id"])  # 只作分母
    project = await _project(client, "重复闯关项目", skill_id=skill["id"])
    student = await _student(client, "2026501")

    # 第一次：80 分通过 → 2 个关联项目完成 1 个 → 50%
    llm.score = 80.0
    first = await _attempt_and_submit(client, student["id"], project["id"], answer="第一轮作答")
    assert (await client.post(f"/api/submissions/{first['id']}/ai-review")).status_code == 201
    assert _progress_of(await _skill_progress(client, student["id"], skill["id"])) == 50.0
    assert float((await _record(client, student["id"], project["id"]))["best_score"]) == 80.0

    # 第二次：90 分通过 → 进度不变、最高分抬到 90
    llm.score = 90.0
    second = await _attempt_and_submit(client, student["id"], project["id"], answer="第二轮作答")
    assert (await client.post(f"/api/submissions/{second['id']}/ai-review")).status_code == 201
    assert _progress_of(await _skill_progress(client, student["id"], skill["id"])) == 50.0
    assert float((await _record(client, student["id"], project["id"]))["best_score"]) == 90.0

    # 第三次：70 分仍然通过 → 进度不变、最高分不被拉低
    llm.score = 70.0
    third = await _attempt_and_submit(client, student["id"], project["id"], answer="第三轮作答")
    assert (await client.post(f"/api/submissions/{third['id']}/ai-review")).status_code == 201
    skill_row = await _skill_progress(client, student["id"], skill["id"])
    assert _progress_of(skill_row) == 50.0
    record = await _record(client, student["id"], project["id"])
    assert float(record["best_score"]) == 90.0, "最高分应保留历史最好成绩"
    assert float(record["total_score"]) == 70.0, "当前成绩按最近一次评审"
    assert record["status"] == "COMPLETED"
    assert record["attempt_count"] == 3

    # 另一个项目没被碰过：连实训记录都还没建
    rows = (await client.get("/api/student-projects", params={"student_id": student["id"]})).json()
    assert all(item["project_id"] != other["id"] for item in rows["items"])


@pytest.mark.asyncio
async def test_repeat_fail_keeps_skill(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """重复闯关不通过：技能保持点亮，项目保持完成。

    技能点亮表示"曾经达成过这个能力"，重复挑战只是给学生一次刷高分的机会。
    失败了就抹掉已有成果的话，学生根本不敢重新挑战。
    """
    llm = _FakeLlm()
    monkeypatch.setattr(ai_review, "call_llm", llm)
    _stub_config(monkeypatch)

    skill = await _skill_node(client)
    await _project(client, "分母项目", skill_id=skill["id"])
    project = await _project(client, "重复挑战的项目", skill_id=skill["id"])
    student = await _student(client, "2026502")

    llm.score = PASS_SCORE
    first = await _attempt_and_submit(client, student["id"], project["id"], answer="认真作答")
    await client.post(f"/api/submissions/{first['id']}/ai-review")
    before = await _skill_progress(client, student["id"], skill["id"])
    assert _progress_of(before) == 50.0
    assert before["activated_at"] is not None

    # 重新挑战但交白卷 → 30 分不通过
    llm.score = FAIL_SCORE
    second = await _attempt_and_submit(client, student["id"], project["id"], answer="略")
    result = (await client.post(f"/api/submissions/{second['id']}/ai-review")).json()
    assert result["review"]["conclusion"] == "FAIL"

    after = await _skill_progress(client, student["id"], skill["id"])
    assert _progress_of(after) == 50.0, "重复闯关不及格不应影响已点亮的技能"
    assert after["activated_at"] == before["activated_at"], "达标时间也不该被动"

    record = await _record(client, student["id"], project["id"])
    assert record["status"] == "COMPLETED", "项目仍然算完成"
    assert record["completed_at"] is not None
    assert float(record["completed_score"]) == PASS_SCORE, "完成时分数快照保留"
    assert float(record["best_score"]) == PASS_SCORE
    assert record["attempt_count"] == 2

    # 本轮不及格只体现在这次提交上，学生看得到自己这轮没通过
    submission = (await client.get(f"/api/submissions/{second['id']}")).json()
    assert submission["status"] == "AI_FAILED"
    assert float(submission["total_score"]) == FAIL_SCORE

    # 再挑战一次通过 → 分数被刷新，进度依旧
    llm.score = 95.0
    third = await _attempt_and_submit(client, student["id"], project["id"], answer="重新认真作答")
    await client.post(f"/api/submissions/{third['id']}/ai-review")
    assert _progress_of(await _skill_progress(client, student["id"], skill["id"])) == 50.0
    assert float((await _record(client, student["id"], project["id"]))["best_score"]) == 95.0


@pytest.mark.asyncio
async def test_regrade_rolls_back_only_when_no_pass_left(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有"唯一那条通过评审被改判"才回滚技能——重复挑战失败不算。

    两者性质不同：前者是"这个判定本来就不成立"，后者是"新一次没做好"。
    """
    llm = _FakeLlm()
    monkeypatch.setattr(ai_review, "call_llm", llm)
    _stub_config(monkeypatch)

    skill = await _skill_node(client)
    alpha = await _project(client, "项目甲", skill_id=skill["id"])
    beta = await _project(client, "项目乙", skill_id=skill["id"])
    student = await _student(client, "2026503")

    reviews: dict[int, dict] = {}
    for project in (alpha, beta):
        submission = await _attempt_and_submit(client, student["id"], project["id"], answer="通过作答")
        reviews[project["id"]] = (await client.post(f"/api/submissions/{submission['id']}/ai-review")).json()[
            "review"
        ]

    mastered = await _skill_progress(client, student["id"], skill["id"])
    assert _progress_of(mastered) == 100.0
    assert mastered["mastered_at"] is not None

    # 重新挑战项目甲失败 → 之前通过的轮次还在 → 什么都不该变
    llm.score = FAIL_SCORE
    retry = await _attempt_and_submit(client, student["id"], alpha["id"], answer="略")
    await client.post(f"/api/submissions/{retry['id']}/ai-review")
    assert _progress_of(await _skill_progress(client, student["id"], skill["id"])) == 100.0
    assert (await _record(client, student["id"], alpha["id"]))["status"] == "COMPLETED"

    # 教师把项目甲那条唯一的通过评审改判为不通过 → 才回滚到 50%
    await client.patch(f"/api/reviews/{reviews[alpha['id']]['id']}", json={"total_score": FAIL_SCORE})
    dropped = await _skill_progress(client, student["id"], skill["id"])
    assert _progress_of(dropped) == 50.0
    assert dropped["mastered_at"] is None, "不再是 100% 就要清掉精通时间"
    assert dropped["activated_at"] is not None, "仍有完成项目，保持已激活"
    assert (await _record(client, student["id"], alpha["id"]))["status"] == "IN_PROGRESS"
    assert (await _record(client, student["id"], beta["id"]))["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_reattempt_in_flight_does_not_downgrade_skill(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重新挑战期间，该项目仍应算"已完成"。

    进度公式是全局的"完成项目数 ÷ 关联项目总数"，而 ``sync_skills_after_project_completed``
    每次都按当前全局状态重算。如果重算时把"正在重新挑战"的项目当成未完成，
    那学生只要一边重新挑战项目甲、一边做项目乙，乙一通过就会把甲的进度也一起抹掉。
    """
    llm = _FakeLlm()
    monkeypatch.setattr(ai_review, "call_llm", llm)
    _stub_config(monkeypatch)

    skill = await _skill_node(client)
    alpha = await _project(client, "正在重挑的项目", skill_id=skill["id"])
    beta = await _project(client, "同步进行的新项目", skill_id=skill["id"])
    student = await _student(client, "2026504")

    # 完成项目甲 → 2 个关联项目里完成 1 个 → 50%
    first = await _attempt_and_submit(client, student["id"], alpha["id"], answer="第一版作答")
    await client.post(f"/api/submissions/{first['id']}/ai-review")
    assert _progress_of(await _skill_progress(client, student["id"], skill["id"])) == 50.0

    # 开始重新挑战项目甲，但**先不提交**（此刻实训记录已是 IN_PROGRESS）
    retry = (await client.post(f"/api/students/{student['id']}/projects/{alpha['id']}/start")).json()
    for stage in retry["stages"]:
        await client.patch(
            f"/api/attempts/{retry['id']}/stages/{stage['id']}", json={"answer_text": "重挑中"}
        )
    assert (await _record(client, student["id"], alpha["id"]))["status"] == "IN_PROGRESS"

    # 此时完成项目乙 → 触发技能重算
    second = await _attempt_and_submit(client, student["id"], beta["id"], answer="乙的作答")
    await client.post(f"/api/submissions/{second['id']}/ai-review")

    progress = _progress_of(await _skill_progress(client, student["id"], skill["id"]))
    assert progress == 100.0, (
        f"甲只是「正在重新挑战、还没出结果」，不该从完成数里被扣掉；实际进度 {progress}%"
    )
