"""模块库（关卡模板）CRUD 与"项目只能挑模板"的约束测试。"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.project import ProjectModule, ProjectStageTemplate, TrainingProject

TEMPLATES = "/api/stage-templates"


async def _create_template(
    client: httpx.AsyncClient, key: str = "CUSTOM_STAGE", name: str = "自定义关卡"
) -> dict:
    response = await client.post(
        TEMPLATES,
        json={
            "stage_key": key,
            "stage_name": name,
            "description": "教师自定义的关卡",
            "default_weight": 20,
            "default_requirement": "提交方案说明",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_stage_template_crud(client: httpx.AsyncClient) -> None:
    first = await _create_template(client, "REQUIREMENT_ANALYSIS", "需求分析")
    second = await _create_template(client, "SOLUTION_DESIGN", "方案设计")
    assert first["sort_no"] == 1 and second["sort_no"] == 2  # 序号自动递增
    assert str(first["default_weight"]) == "20.0000000000"

    listed = (await client.get(TEMPLATES)).json()
    assert listed["total"] == 2
    assert [item["stage_name"] for item in listed["items"]] == ["需求分析", "方案设计"]  # 按 sort_no
    assert (await client.get(TEMPLATES, params={"keyword": "方案"})).json()["total"] == 1

    # 编码唯一
    duplicate = await client.post(
        TEMPLATES, json={"stage_key": "REQUIREMENT_ANALYSIS", "stage_name": "重复关卡"}
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["code"] == 422
    assert "已存在" in duplicate.json()["msg"]

    # 详情带被引用次数
    detail = (await client.get(f"{TEMPLATES}/{first['id']}")).json()
    assert detail["used_by_projects"] == 0

    # 修改（改名、调权重）
    patched = (
        await client.patch(
            f"{TEMPLATES}/{first['id']}",
            json={"stage_name": "需求分析（改）", "default_weight": 15},
        )
    ).json()
    assert patched["stage_name"] == "需求分析（改）"

    # 删除
    assert (await client.delete(f"{TEMPLATES}/{second['id']}")).status_code == 200
    assert (await client.get(TEMPLATES)).json()["total"] == 1
    assert (await client.get(f"{TEMPLATES}/{second['id']}")).json()["code"] == 422


@pytest.mark.asyncio
async def test_template_in_use_cannot_be_deleted(client: httpx.AsyncClient, db_session) -> None:
    template = await _create_template(client, "MODEL_TESTING", "模型测试")

    # 造一个选了该模板的项目（项目域接口在 P2，这里直接落库）
    project = TrainingProject(project_name="缺陷检测实训", project_level="BASIC")
    db_session.add(project)
    await db_session.flush()
    db_session.add(ProjectModule(project_id=project.id, template_id=template["id"], stage_no=1))
    await db_session.flush()

    detail = (await client.get(f"{TEMPLATES}/{template['id']}")).json()
    assert detail["used_by_projects"] == 1

    blocked = await client.delete(f"{TEMPLATES}/{template['id']}")
    assert blocked.status_code == 200
    assert blocked.json()["code"] == 422
    assert "已被 1 个项目选中" in blocked.json()["msg"]

    # 项目里移除后即可删除
    module = await db_session.get(ProjectModule, 1)
    assert module is not None
    await db_session.delete(module)
    await db_session.flush()
    assert (await client.delete(f"{TEMPLATES}/{template['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_project_module_must_come_from_template_library(db_session) -> None:
    """项目模块的 template_id 必填且必须真实存在 —— 不能在项目里临时造关卡。"""
    project = TrainingProject(project_name="检测实训", project_level="BASIC")
    db_session.add(project)
    await db_session.flush()

    # 不给模板 → NOT NULL 拒绝
    db_session.add(ProjectModule(project_id=project.id, stage_no=1, template_id=None))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    # 模板不存在 → 外键拒绝
    db_session.add(ProjectModule(project_id=project.id, template_id=9999, stage_no=1))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_same_template_cannot_be_added_twice(db_session) -> None:
    template = ProjectStageTemplate(stage_key="DATA_PROCESSING", stage_name="数据处理")
    project = TrainingProject(project_name="数据处理实训", project_level="BASIC")
    db_session.add_all([template, project])
    await db_session.flush()

    db_session.add(ProjectModule(project_id=project.id, template_id=template.id, stage_no=1))
    await db_session.flush()

    db_session.add(ProjectModule(project_id=project.id, template_id=template.id, stage_no=2))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_stage_template_soft_delete_and_restore(client: httpx.AsyncClient) -> None:
    """删掉是软删：列表里消失，但用同一个编码再建会把原条目恢复出来。"""
    template = await _create_template(client, "MODEL_OPTIMIZATION", "模型优化")
    assert (await client.delete(f"{TEMPLATES}/{template['id']}")).status_code == 200

    listed = (await client.get(TEMPLATES)).json()
    assert [item["stage_key"] for item in listed["items"]] == []
    assert (await client.get(f"{TEMPLATES}/{template['id']}")).json()["code"] == 422

    # 同一个编码再建一次 → 恢复原条目（HTTP 200，而不是新建 201）
    restored = await client.post(
        TEMPLATES,
        json={"stage_key": "MODEL_OPTIMIZATION", "stage_name": "模型优化（新版）", "default_weight": 25},
    )
    assert restored.status_code == 200
    assert restored.json()["id"] == template["id"]
    assert restored.json()["stage_name"] == "模型优化（新版）"
    assert (await client.get(TEMPLATES)).json()["total"] == 1


@pytest.mark.asyncio
async def test_project_builds_modules_from_template_library(client: httpx.AsyncClient) -> None:
    """项目流程：建项目 → 从模块库挑关卡 → 调权重顺序 → 权重满 100 才能发布。"""
    first = await _create_template(client, "REQUIREMENT_ANALYSIS", "需求分析")
    second = await _create_template(client, "SOLUTION_DESIGN", "方案设计")
    third = await _create_template(client, "MODEL_TRAINING", "模型训练")

    project = (
        await client.post(
            "/api/projects",
            json={
                "project_name": "工业缺陷检测实训",
                "project_level": "BASIC",
                "difficulty": 3,
                "description": "端到端缺陷检测",
            },
        )
    ).json()
    assert project["status"] == "DRAFT"

    detail = (await client.get(f"/api/projects/{project['id']}")).json()
    assert detail["modules"] == [] and Decimal(str(detail["weight_total"])) == Decimal(0)

    # 挑关卡：不传权重/必填就用模板默认值
    added = (
        await client.post(f"/api/projects/{project['id']}/modules", json={"template_id": first["id"]})
    ).json()
    assert added["stage_no"] == 1
    assert added["stage_key"] == "REQUIREMENT_ANALYSIS" and added["stage_name"] == "需求分析"
    assert Decimal(str(added["weight"])) == Decimal(20)  # 模板默认权重
    assert added["required"] is True

    await client.post(
        f"/api/projects/{project['id']}/modules", json={"template_id": second["id"], "weight": 30}
    )
    await client.post(
        f"/api/projects/{project['id']}/modules",
        json={"template_id": third["id"], "weight": 30, "required": False, "requirement": "提交训练日志"},
    )

    # 同一个模板不能重复加
    duplicate = await client.post(f"/api/projects/{project['id']}/modules", json={"template_id": first["id"]})
    assert duplicate.json()["code"] == 422
    # 模块库里没有的模板
    missing = await client.post(f"/api/projects/{project['id']}/modules", json={"template_id": 9999})
    assert missing.json()["code"] == 422

    # 权重合计 80，不能发布
    blocked = await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    assert blocked.json()["code"] == 422
    assert "权重合计" in blocked.json()["msg"]

    # 把需求分析调到 40 → 合计 100，可以发布
    modules = (await client.get(f"/api/projects/{project['id']}/modules")).json()
    await client.patch(f"/api/projects/{project['id']}/modules/{modules[0]['id']}", json={"weight": 40})
    published = await client.patch(f"/api/projects/{project['id']}", json={"status": "PUBLISHED"})
    assert published.json()["status"] == "PUBLISHED"
    total = (await client.get(f"/api/projects/{project['id']}")).json()["weight_total"]
    assert Decimal(str(total)) == Decimal(100)

    # 已发布项目不能直接删除
    blocked_delete = await client.delete(f"/api/projects/{project['id']}")
    assert blocked_delete.json()["code"] == 422
    assert "先下架" in blocked_delete.json()["msg"]


@pytest.mark.asyncio
async def test_project_module_order_and_removal(client: httpx.AsyncClient) -> None:
    templates = [
        await _create_template(client, "REQUIREMENT_ANALYSIS", "需求分析"),
        await _create_template(client, "DATA_PROCESSING", "数据处理"),
        await _create_template(client, "REPORT_UPLOAD", "实训报告上传"),
    ]
    project = (
        await client.post("/api/projects", json={"project_name": "排序实训", "project_level": "BASIC"})
    ).json()
    for template in templates:
        await client.post(f"/api/projects/{project['id']}/modules", json={"template_id": template["id"]})

    modules = (await client.get(f"/api/projects/{project['id']}/modules")).json()
    assert [item["stage_no"] for item in modules] == [1, 2, 3]

    # 倒序排列
    reversed_ids = [item["id"] for item in modules][::-1]
    ordered = (
        await client.put(f"/api/projects/{project['id']}/modules/order", json={"module_ids": reversed_ids})
    ).json()
    assert [item["stage_name"] for item in ordered] == ["实训报告上传", "数据处理", "需求分析"]
    assert [item["stage_no"] for item in ordered] == [1, 2, 3]

    # 排序列表必须刚好包含全部关卡
    partial = await client.put(
        f"/api/projects/{project['id']}/modules/order", json={"module_ids": reversed_ids[:2]}
    )
    assert partial.json()["code"] == 422

    # 移除中间关卡后，序号自动补齐
    removed_id = ordered[1]["id"]
    assert (await client.delete(f"/api/projects/{project['id']}/modules/{removed_id}")).status_code == 200
    remaining = (await client.get(f"/api/projects/{project['id']}/modules")).json()
    assert [item["stage_no"] for item in remaining] == [1, 2]

    # 删除项目：软删后查不到
    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 200
    assert (await client.get(f"/api/projects/{project['id']}")).json()["code"] == 422


@pytest.mark.asyncio
async def test_project_skills(client: httpx.AsyncClient) -> None:
    """项目所需技能：覆盖式设置 + 单条增删，决定"完成项目推进哪些技能"。"""
    tree = (await client.post("/api/skill-trees", json={"tree_code": "CV", "tree_name": "视觉系"})).json()
    first = (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_code": "IMG_BASE", "node_name": "图像基础"},
        )
    ).json()
    second = (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_code": "EDGE_DETECT", "node_name": "边缘检测"},
        )
    ).json()
    third = (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_code": "MODEL_TRAIN", "node_name": "模型训练"},
        )
    ).json()

    project = (
        await client.post("/api/projects", json={"project_name": "缺陷检测实训", "project_level": "BASIC"})
    ).json()
    assert (await client.get(f"/api/projects/{project['id']}/skills")).json() == []

    covered = await client.put(
        f"/api/projects/{project['id']}/skills",
        json={"skill_node_ids": [first["id"], second["id"]]},
    )
    assert [node["node_code"] for node in covered.json()] == ["IMG_BASE", "EDGE_DETECT"]

    # 追加单个技能；重复追加报错；不存在的技能节点报错
    assert (await client.post(f"/api/projects/{project['id']}/skills/{third['id']}")).status_code == 201
    duplicate = await client.post(f"/api/projects/{project['id']}/skills/{third['id']}")
    assert duplicate.json()["code"] == 422
    assert (await client.post(f"/api/projects/{project['id']}/skills/9999")).json()["code"] == 422
    assert len((await client.get(f"/api/projects/{project['id']}/skills")).json()) == 3

    # 覆盖式设置：只留一个
    await client.put(f"/api/projects/{project['id']}/skills", json={"skill_node_ids": [first["id"]]})
    assert len((await client.get(f"/api/projects/{project['id']}/skills")).json()) == 1

    # 解除关联
    assert (await client.delete(f"/api/projects/{project['id']}/skills/{first['id']}")).status_code == 200
    assert (await client.get(f"/api/projects/{project['id']}/skills")).json() == []

    # 不存在的项目
    assert (await client.get("/api/projects/9999/skills")).json()["code"] == 422


@pytest.mark.asyncio
async def test_stage_template_has_no_default_items(client: httpx.AsyncClient) -> None:
    """模板只定义通用信息，不预设填写子标题（不同岗位方向的子标题差别太大）。"""
    created = (
        await client.post(
            TEMPLATES,
            json={
                "stage_key": "REQUIREMENT_ANALYSIS",
                "stage_name": "需求分析",
                "default_weight": 10,
                # 即使老前端传了子标题，也应该被忽略
                "default_items_json": [{"title": "不该被保存"}],
            },
        )
    ).json()
    assert "default_items_json" not in created
    detail = (await client.get(f"{TEMPLATES}/{created['id']}")).json()
    assert "default_items_json" not in detail
    assert str(detail["default_weight"]).startswith("10")


@pytest.mark.asyncio
async def test_project_module_items_are_typed_by_teacher(client: httpx.AsyncClient) -> None:
    """子标题全部由教师在项目里手填：不传就是空，填了就是那一套，不同项目互不影响。"""
    template = (
        await client.post(TEMPLATES, json={"stage_key": "REQUIREMENT_ANALYSIS", "stage_name": "需求分析"})
    ).json()
    second_template = (
        await client.post(TEMPLATES, json={"stage_key": "SOLUTION_DESIGN", "stage_name": "方案设计"})
    ).json()

    first_project = (
        await client.post("/api/projects", json={"project_name": "项目甲", "project_level": "BASIC"})
    ).json()
    second_project = (
        await client.post("/api/projects", json={"project_name": "项目乙", "project_level": "BASIC"})
    ).json()

    # 不传 items_json → 空数组，等教师填写
    blank = (
        await client.post(
            f"/api/projects/{first_project['id']}/modules", json={"template_id": template["id"]}
        )
    ).json()
    assert blank["items_json"] == []

    # 教师手填 → 就用这一套
    custom = (
        await client.post(
            f"/api/projects/{second_project['id']}/modules",
            json={
                "template_id": template["id"],
                "items_json": [{"title": "缺陷类别清单"}, {"title": "数据规模与来源"}],
            },
        )
    ).json()
    assert [item["title"] for item in custom["items_json"]] == ["缺陷类别清单", "数据规模与来源"]

    # 另一个没填的关卡依然是空
    empty = (
        await client.post(
            f"/api/projects/{first_project['id']}/modules",
            json={"template_id": second_template["id"]},
        )
    ).json()
    assert empty["items_json"] == []

    # 项目里改子标题：删掉一个、加一个
    patched = (
        await client.patch(
            f"/api/projects/{first_project['id']}/modules/{blank['id']}",
            json={
                "items_json": [
                    {"title": "检测对象描述", "prompt": "工件与尺寸"},
                    {"title": "检测精度要求", "prompt": "最小可检尺寸"},
                ]
            },
        )
    ).json()
    assert [item["title"] for item in patched["items_json"]] == ["检测对象描述", "检测精度要求"]

    # 改子标题不影响另一个项目
    other = (await client.get(f"/api/projects/{second_project['id']}/modules")).json()
    assert [item["title"] for item in other[0]["items_json"]] == ["缺陷类别清单", "数据规模与来源"]

    # 子标题必须有 title
    invalid = await client.patch(
        f"/api/projects/{first_project['id']}/modules/{blank['id']}",
        json={"items_json": [{"prompt": "没有标题"}]},
    )
    assert invalid.json()["code"] == 422
