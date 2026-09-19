"""AI 评审：RAG 召回评分标准 → 大模型打分 → 写回数据库。

大模型调用一律用假实现替换（``monkeypatch`` 掉 ``ai_review.call_llm``），
所以这组用例不消耗真实 token、离线也能跑；真实连通性由 ``scripts/check_models.py``
与手工端到端验证覆盖。
"""

import json
import time

import httpx
import pytest
from sqlmodel import select

from app.core.config import settings
from app.core.exceptions import BusinessRuleError
from app.models.attempt import ProjectSubmission
from app.services import ai_review, settings_store
from app.services.ai_review import StageAnswer, StageAttachment, SubmissionContext
from tests import helpers

#: 测试用的大模型配置：key 是假的，真实调用一律被 monkeypatch 掉
TEST_LLM_CONFIG = settings_store.LLMConfig(
    provider="openai-compatible",
    base_url="https://example.invalid/v1",
    model="deepseek-v4-flash",
    api_key="sk-test-key",
    temperature=0.2,
    timeout=10,
    max_tokens=1024,
)


def _stub_llm_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """内存测试库没有 system_config，直接给一个已配置的 ai.llm。"""

    async def _fake(_session) -> settings_store.LLMConfig:
        return TEST_LLM_CONFIG

    monkeypatch.setattr(settings_store, "get_llm_config", _fake)


CRITERIA_MD = """# 实训报告评分标准

## 一、需求分析（20 分）

需求描述完整、边界清晰得满分；缺少关键约束每处扣 5 分。

## 二、方案设计（30 分）

系统架构合理、技术选型有依据得满分；架构描述含糊扣 10 分。
"""

GOOD_REPLY = json.dumps(
    {
        "total_score": 85.0,
        "dimensions": [
            {"name": "需求分析", "score": 34, "weight": 40, "reason": "边界清晰，缺少一处约束"},
            {"name": "方案设计", "score": 51, "weight": 60, "reason": "技术选型有依据"},
        ],
        "comment": "整体完成度较好，补充缺失约束即可",
    },
    ensure_ascii=False,
)


# ------------------------------------------------------------------ 纯函数


def test_parse_llm_json_tolerates_fences_and_noise() -> None:
    """模型爱加 Markdown 代码块或前后解释，都要能抠出 JSON。"""
    assert ai_review.parse_llm_json('{"a": 1}') == {"a": 1}
    assert ai_review.parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai_review.parse_llm_json('好的，结果如下：\n{"a": 1}\n以上。') == {"a": 1}


def test_parse_llm_json_rejects_garbage() -> None:
    with pytest.raises(BusinessRuleError):
        ai_review.parse_llm_json("模型今天不想输出 JSON")


def _context() -> SubmissionContext:
    return SubmissionContext(
        submission_id=1,
        attempt_id=1,
        student_id=1,
        project_id=1,
        project_name="测试项目",
        answers=[
            StageAnswer(1, "需求分析", 40, None, "作答A", True),
            StageAnswer(2, "方案设计", 60, None, "作答B", True),
        ],
    )


def _dimensions() -> list[dict]:
    return ai_review.build_dimensions(json.loads(GOOD_REPLY), _context())


def test_total_score_prefers_model_value() -> None:
    total = ai_review.total_score_of({"total_score": 85.0}, _dimensions())
    assert float(total) == pytest.approx(85.0, abs=0.01)


def test_total_score_falls_back_to_sum_of_points() -> None:
    """模型忘了给总分时按"各维度得分之和"算（维度满分就是权重分）。"""
    # 34 + 51 = 85，权重合计 100 时即百分制总分
    total = ai_review.total_score_of({}, _dimensions())
    assert float(total) == pytest.approx(85.0, abs=0.01)


def test_dimensions_clamp_and_fill_weight_from_project() -> None:
    """分数越界要夹住；模型没给权重时用项目里配的权重。"""
    payload = {
        "dimensions": [
            {"name": "需求分析", "score": 999, "reason": "越界"},
            {"name": "方案设计", "score": -5},
        ]
    }
    dimensions = ai_review.build_dimensions(payload, _context())
    assert dimensions[0]["score"] == "40.00"  # 夹到该维度权重
    assert dimensions[0]["weight"] == "40.00"
    assert dimensions[1]["score"] == "0.00"
    assert dimensions[1]["weight"] == "60.00"


def test_answer_document_includes_attachments() -> None:
    """关卡附件要进 prompt：能解析的给正文，解析不了的也要显式说明。

    直接跳过读不出来的附件，模型会以为学生没交，按"缺项"扣分——那是误判。
    """
    context = SubmissionContext(
        submission_id=1,
        attempt_id=1,
        student_id=1,
        project_id=1,
        project_name="测试项目",
        answers=[
            StageAnswer(
                1,
                "数据处理",
                100,
                None,
                "见附件",
                True,
                attachments=[
                    StageAttachment(
                        1, "实验报告.md", "text/markdown", 1024, text="数据集按 800/200/200 划分"
                    ),
                    StageAttachment(2, "现场照片.png", "image/png", 2048, error="有待 OCR"),
                ],
            )
        ],
    )
    document = context.answer_document
    assert "【附件：实验报告.md】" in document
    assert "数据集按 800/200/200 划分" in document
    assert "【附件：现场照片.png】" in document
    assert "无法解析" in document and "有待 OCR" in document


def test_system_prompt_forbids_full_marks_without_criteria() -> None:
    """提示词里必须明确禁止"没找到依据就给满分"。

    实测踩过：召回只覆盖部分章节时，模型对没拿到条款的维度照样给满分，
    评审直接失去区分度。
    """
    assert "绝不能因为" in ai_review.SYSTEM_PROMPT
    assert "评分标准未覆盖" in ai_review.SYSTEM_PROMPT
    assert "满分" in ai_review.SYSTEM_PROMPT


#: 评分标准按"每个模块多个 ### 细则"写：切片条数由标题个数决定，
#: 七个模块各三节就是 21+ 片，远超过去那个写死的 12 片上限。
MANY_CHUNK_CRITERIA = (
    "# 评分标准\n\n总分 100 分。\n\n"
    + "\n\n".join(
        f"## {index}、{name}（{weight} 分）\n\n"
        f"### 必需项\n【{name}条款】必须写清 {name} 的全部要点。\n\n"
        f"### 扣分\n每缺一项扣 {weight // 10} 分。\n\n"
        f"### 说明\n本维度不写口号，只认可核对的数据。"
        for index, (name, weight) in enumerate(
            [
                ("需求分析", 10),
                ("方案设计", 15),
                ("数据处理", 15),
                ("模型训练", 20),
                ("模型优化", 15),
                ("模型测试", 15),
                ("实训报告", 10),
            ],
            start=1,
        )
    )
    + "\n"
)


#: 七个模块与上面评分标准的七个章节一一对应
SEVEN_MODULES = [
    ("需求分析", 10),
    ("方案设计", 15),
    ("数据处理", 15),
    ("模型训练", 20),
    ("模型优化", 15),
    ("模型测试", 15),
    ("实训报告", 10),
]


async def _build_many_chunk_doc(client: httpx.AsyncClient, project_name: str) -> tuple[dict, dict]:
    """建一个七模块项目 + 一份"每模块三节"的评分标准（切片数远超旧的 12 片上限）。"""
    project = (
        await client.post("/api/projects", json={"project_name": project_name, "project_level": "BASIC"})
    ).json()
    for order, (name, weight) in enumerate(SEVEN_MODULES, start=1):
        template = (
            await client.post(
                "/api/stage-templates",
                json={"stage_name": name},
            )
        ).json()
        await client.post(
            f"/api/projects/{project['id']}/modules",
            json={"template_id": template["id"], "stage_no": order, "weight": weight},
        )
    await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    await helpers.publish(client, [project["id"]])
    uploaded = (
        await client.post(
            f"/api/projects/{project['id']}/files/upload",
            files={"file": ("评分标准.md", MANY_CHUNK_CRITERIA.encode(), "text/markdown")},
            data={"file_kind": "SCORING_CRITERIA", "title": "评分标准"},
        )
    ).json()
    return project, uploaded


async def _submit_all_stages(client: httpx.AsyncClient, project: dict, *, user_no: str) -> dict:
    """七个关卡全部作答并提交（必填校验要求每关都填）。"""
    student = (
        await client.post(
            "/api/users", json={"user_no": user_no, "real_name": "长标准学生", "user_type": "STUDENT"}
        )
    ).json()
    await helpers.enroll(client, user_no)
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    for stage in attempt["stages"]:
        await client.patch(
            f"/api/attempts/{attempt['id']}/stages/{stage['id']}",
            json={"answer_text": f"{stage['stage_name']}：按标准逐项作答，数据可核对。"},
        )
    return (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()


@pytest.mark.asyncio
async def test_long_criteria_keeps_every_dimension_covered(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评分标准很长时，按关卡分维度挑依据——不能有哪个维度一条条款都拿不到。

    以前是"召回 top-12 片"，标准一长，排在后面的维度就被静默丢掉，
    模型没依据只能凭感觉打分（实测出现过"标准未覆盖"却给满分）。

    正常情况下评分标准会整份作为一块（``knowledge_whole_doc_max_chars``），
    这里把上限压小，模拟"标准写得太长、只能回落到结构化切分"的情形。
    """
    captured: dict[str, str] = {}

    async def _fake_call_llm(config, *, system_prompt: str, user_prompt: str) -> tuple[str, str | None]:
        captured["user"] = user_prompt
        return GOOD_REPLY, None

    # 把字符预算压到很小，强制走"按维度挑选"那条分支（真实默认是 6 万字符）
    async def _fake_retrieval_config(_session) -> settings_store.RetrievalConfig:
        return settings_store.RetrievalConfig(
            retrieve_top_k=50,
            rerank_top_k=30,
            context_top_n=6,
            score_threshold=0.3,
            rrf_k=60,
            # 构造的标准共 900 字左右，这里压到 400 字强制走"分维度挑选"分支
            criteria_max_chars=400,
            criteria_top_k_per_dimension=4,
        )

    monkeypatch.setattr(settings_store, "get_retrieval_config", _fake_retrieval_config)
    monkeypatch.setattr(ai_review, "call_llm", _fake_call_llm)
    _stub_llm_config(monkeypatch)
    monkeypatch.setattr(settings, "knowledge_whole_doc_max_chars", 100)

    suffix = time.time_ns()
    project, uploaded = await _build_many_chunk_doc(client, f"长标准实训{suffix}")
    assert uploaded["chunk_count"] > 12, "构造的标准应当超过旧的 12 片上限"
    submission = await _submit_all_stages(client, project, user_no=f"20{suffix % 10**8:08d}")

    response = await client.post(f"/api/submissions/{submission['id']}/ai-review")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["warnings"], "超出预算时应当回提示"

    prompt = captured["user"]
    missing = [
        name
        for name in ["需求分析", "方案设计", "数据处理", "模型训练", "模型优化", "模型测试", "实训报告"]
        if f"【{name}条款】" not in prompt
    ]
    assert not missing, f"这些维度没拿到评分依据：{missing}"


# ------------------------------------------------------------------ 端到端


async def _criteria_project(client: httpx.AsyncClient, name: str) -> tuple[dict, dict]:
    """建一个带评分标准的已发布项目：一个关卡 + 一份 SCORING_CRITERIA 附件。"""
    template = (
        await client.post(
            "/api/stage-templates",
            json={"stage_name": "需求分析"},
        )
    ).json()
    project = (
        await client.post("/api/projects", json={"project_name": name, "project_level": "BASIC"})
    ).json()
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": template["id"], "weight": 100},
    )
    await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    await helpers.publish(client, [project["id"]])
    uploaded = (
        await client.post(
            f"/api/projects/{project['id']}/files/upload",
            files={"file": ("评分标准.md", CRITERIA_MD.encode(), "text/markdown")},
            data={"file_kind": "SCORING_CRITERIA", "title": "实训报告评分标准"},
        )
    ).json()
    assert uploaded["knowledge_status"] == "READY", uploaded
    return project, uploaded


async def _submitted_attempt(client: httpx.AsyncClient, project: dict, *, user_no: str) -> dict:
    """建学生 → 开始闯关 → 作答 → 整单提交，返回提交记录。"""
    student = (
        await client.post(
            "/api/users", json={"user_no": user_no, "real_name": "李四", "user_type": "STUDENT"}
        )
    ).json()
    await helpers.enroll(client, user_no)
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    await client.patch(
        f"/api/attempts/{attempt['id']}/stages/{attempt['stages'][0]['id']}",
        json={"answer_text": "需求：检测工件表面缺陷，精度 0.1mm，节拍 2s，未说明光照条件。"},
    )
    return (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()


async def _upload_and_attach(client: httpx.AsyncClient, attempt: dict, *, filename: str, body: bytes) -> dict:
    """给第一个关卡传一个附件（走学生作答附件那条既有链路）。"""
    asset = (
        await client.post(
            "/api/file-assets/upload",
            files={"file": (filename, body, "text/markdown")},
            data={"biz_type": "SUBMISSION"},
        )
    ).json()
    stage_id = attempt["stages"][0]["id"]
    attached = await client.post(f"/api/attempts/{attempt['id']}/stages/{stage_id}/files/{asset['id']}")
    assert attached.status_code == 201, attached.text
    return asset


@pytest.mark.asyncio
async def test_ai_review_feeds_stage_attachments_to_llm(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关卡附件的正文必须进 prompt——学生把关键内容放在文件里是常态。"""
    captured: dict[str, str] = {}

    async def _fake_call_llm(config, *, system_prompt: str, user_prompt: str) -> tuple[str, str | None]:
        captured["user"] = user_prompt
        return GOOD_REPLY, None

    monkeypatch.setattr(ai_review, "call_llm", _fake_call_llm)
    _stub_llm_config(monkeypatch)

    project, _uploaded = await _criteria_project(client, "附件评审实训")
    student = (
        await client.post(
            "/api/users", json={"user_no": "2026199", "real_name": "王五", "user_type": "STUDENT"}
        )
    ).json()
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    await client.patch(
        f"/api/attempts/{attempt['id']}/stages/{attempt['stages'][0]['id']}",
        json={"answer_text": "详见附件。"},
    )
    await _upload_and_attach(
        client,
        attempt,
        filename="数据说明.md",
        body="数据集划分：训练 800、验证 200、测试 200，标注由两名工程师交叉完成。".encode(),
    )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()

    assert (await client.post(f"/api/submissions/{submission['id']}/ai-review")).status_code == 201

    assert "【附件：数据说明.md】" in captured["user"]
    assert "训练 800、验证 200、测试 200" in captured["user"]


@pytest.mark.asyncio
async def test_ai_review_records_attachment_provenance(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw_json 要留下附件台账：事后能证明"这次评审到底看没看附件"。"""

    async def _fake_call_llm(config, *, system_prompt: str, user_prompt: str) -> tuple[str, str | None]:
        return GOOD_REPLY, None

    monkeypatch.setattr(ai_review, "call_llm", _fake_call_llm)
    _stub_llm_config(monkeypatch)

    project, _uploaded = await _criteria_project(client, "附件台账实训")
    student = (
        await client.post(
            "/api/users", json={"user_no": "2026200", "real_name": "赵六", "user_type": "STUDENT"}
        )
    ).json()
    attempt = (await client.post(f"/api/students/{student['id']}/projects/{project['id']}/start")).json()
    stage_id = attempt["stages"][0]["id"]
    await client.patch(f"/api/attempts/{attempt['id']}/stages/{stage_id}", json={"answer_text": "见附件"})
    asset = await _upload_and_attach(
        client, attempt, filename="需要OCR的扫描件.png", body=b"\x89PNG\r\n\x1a\n not-a-real-image"
    )
    submission = (await client.post(f"/api/attempts/{attempt['id']}/submit")).json()

    review = (await client.post(f"/api/submissions/{submission['id']}/ai-review")).json()["review"]
    provenance = review["raw_json"]["attachments"]
    assert [item["name"] for item in provenance] == ["需要OCR的扫描件.png"]
    assert provenance[0]["readable"] is False
    assert provenance[0]["error"], "读不出来的附件要记下原因"
    assert provenance[0]["file_asset_id"] == asset["id"]


@pytest.mark.asyncio
async def test_ai_review_end_to_end_writes_record(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """完整链路：评分标准入库 → 提交 → AI 评审 → review_record 落库 + 结算。"""
    calls: dict[str, str] = {}

    async def _fake_call_llm(config, *, system_prompt: str, user_prompt: str) -> tuple[str, str | None]:
        calls["system"] = system_prompt
        calls["user"] = user_prompt
        calls["model"] = config.model
        return GOOD_REPLY, "req-test"

    monkeypatch.setattr(ai_review, "call_llm", _fake_call_llm)
    _stub_llm_config(monkeypatch)

    project, uploaded = await _criteria_project(client, "AI评审实训")
    submission = await _submitted_attempt(client, project, user_no="2026100")
    assert submission["status"] == "PENDING_AI"

    response = await client.post(f"/api/submissions/{submission['id']}/ai-review")
    assert response.status_code == 201, response.text
    payload = response.json()

    assert payload["model"] == "deepseek-v4-flash"
    assert payload["criteria_doc_ids"] == [uploaded["knowledge_doc_id"]]
    # 评分标准不长时应当整份交给模型：按 top-N 截断会丢掉"措辞不像"的维度
    assert payload["recalled_chunks"] == uploaded["chunk_count"]

    review = payload["review"]
    assert review["review_kind"] == "AI"
    assert review["status"] == "FINAL"
    assert review["ai_model"] == "deepseek-v4-flash"
    assert float(review["total_score"]) == pytest.approx(85.0, abs=0.01)
    assert review["conclusion"] == "PASS"
    assert [item["name"] for item in review["dimension_json"]] == ["需求分析", "方案设计"]
    # 用到的评分标准与模型原始输出都留了痕，事后能解释这个分数怎么来的
    assert review["raw_json"]["criteria_doc_ids"] == [uploaded["knowledge_doc_id"]]
    assert review["raw_json"]["llm_raw"] == GOOD_REPLY

    # 召回线索确实进了 prompt：批改用的是"该项目的评分标准"
    assert "评分标准" in calls["user"]
    assert "需求分析" in calls["user"]
    assert "0.1mm" in calls["user"]  # 学生作答也进 prompt 了

    # 提交状态被结算
    submission_after = (
        await db_session.exec(select(ProjectSubmission).where(ProjectSubmission.id == submission["id"]))
    ).first()
    assert submission_after.status == "AI_PASSED"
    assert submission_after.final_conclusion == "PASS"


@pytest.mark.asyncio
async def test_ai_review_requires_criteria(client: httpx.AsyncClient) -> None:
    """项目没传评分标准时给出可读提示，而不是让模型瞎猜。"""
    template = (await client.post("/api/stage-templates", json={"stage_name": "需求分析"})).json()
    project = (
        await client.post("/api/projects", json={"project_name": "没有评分标准", "project_level": "BASIC"})
    ).json()
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": template["id"], "weight": 100},
    )
    await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    await helpers.publish(client, [project["id"]])
    submission = await _submitted_attempt(client, project, user_no="2026101")

    response = await client.post(f"/api/submissions/{submission['id']}/ai-review")
    assert response.json()["code"] == 422
    assert "评分标准" in response.json()["msg"]


@pytest.mark.asyncio
async def test_ai_review_is_not_run_twice(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一份提交不重复评审，避免刷分。"""

    async def _fake_call_llm(config, *, system_prompt: str, user_prompt: str) -> tuple[str, str | None]:
        return GOOD_REPLY, None

    monkeypatch.setattr(ai_review, "call_llm", _fake_call_llm)
    _stub_llm_config(monkeypatch)

    project, _uploaded = await _criteria_project(client, "重复评审实训")
    submission = await _submitted_attempt(client, project, user_no="2026102")
    assert (await client.post(f"/api/submissions/{submission['id']}/ai-review")).status_code == 201

    again = await client.post(f"/api/submissions/{submission['id']}/ai-review")
    assert again.json()["code"] == 422
    assert "已经有过" in again.json()["msg"]
