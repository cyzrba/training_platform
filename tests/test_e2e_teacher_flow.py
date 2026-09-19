"""端到端：教师建班 → 建分组 → Excel 导入学生（自动建号）→ 配岗位技能 → 学生选岗。"""

from io import BytesIO

import httpx
import pytest
from openpyxl import Workbook

from app.core.config import settings
from app.crud.account import UserRepository
from app.services.password import verify_password


def _student_xlsx(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["学号", "姓名", "专业", "手机号", "邮箱", "分组序号"])
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


@pytest.mark.asyncio
async def test_teacher_onboarding_flow(client: httpx.AsyncClient, db_session) -> None:
    # ① 建班级
    classroom = (
        await client.post(
            "/api/classes", json={"class_name": "人工智能2401班", "grade_year": 2024, "group_count": 2}
        )
    ).json()
    # ② 建分组
    await client.post(f"/api/classes/{classroom['id']}/groups", json={"group_name": "第一组"})
    await client.post(f"/api/classes/{classroom['id']}/groups", json={"group_name": "第二组"})

    # ③ 下载模板（教师拿到表头）
    template = await client.get("/api/classes/students/import-template")
    assert template.status_code == 200 and template.content[:2] == b"PK"

    # ④ 上传名单：先 dry-run 预检，再正式导入
    content = _student_xlsx(
        [
            ["2026001", "张三", "人工智能技术应用", "13800000001", "zs@example.com", 1],
            ["2026002", "李四", "人工智能技术应用", "", "", 1],
            ["2026003", "王五", "人工智能技术应用", "", "", 2],
        ]
    )
    files = {"file": ("students.xlsx", content, "application/vnd.ms-excel")}
    preview = await client.post(
        f"/api/classes/{classroom['id']}/students/import", files=files, data={"dry_run": "true"}
    )
    assert preview.json()["created_users"] == 3 and preview.json()["dry_run"] is True
    assert (await client.get(f"/api/classes/{classroom['id']}/students")).json()["total"] == 0

    imported = await client.post(
        f"/api/classes/{classroom['id']}/students/import", files=files, data={"dry_run": "false"}
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created_users"] == 3 and imported.json()["assigned_groups"] == 3

    # ⑤ 学生账号 + 在班记录 + 分组都到位
    roster = (await client.get(f"/api/classes/{classroom['id']}/students")).json()
    assert roster["total"] == 3
    students = UserRepository(db_session)
    for user_no in ("2026001", "2026002", "2026003"):
        student = await students.get_by(user_no=user_no)
        assert student is not None and student.user_type == "STUDENT"
        assert verify_password(settings.default_password, student.password_hash)
    assert {item["group_name"] for item in roster["items"]} == {"第一组", "第二组"}

    # ⑥ 建岗位 + 技能树 + 节点，并挂到岗位上
    job = (await client.post("/api/jobs", json={"job_name": "工业视觉工程师"})).json()
    tree = (await client.post("/api/skill-trees", json={"tree_name": "传统算法系"})).json()
    base = (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_name": "图像基础"},
        )
    ).json()
    edge = (
        await client.post(
            f"/api/skill-trees/{tree['id']}/nodes",
            json={"node_name": "边缘检测"},
        )
    ).json()
    await client.put(
        f"/api/skill-nodes/{edge['id']}/dependencies", json={"prerequisite_node_ids": [base["id"]]}
    )
    await client.put(f"/api/jobs/{job['id']}/skills", json={"skill_node_ids": [base["id"], edge["id"]]})
    assert (await client.get(f"/api/jobs/{job['id']}")).json()["skill_count"] == 2

    # ⑦ 学生选岗（主岗位唯一）
    student = roster["items"][0]
    selected = await client.post(
        f"/api/students/{student['student_id']}/jobs",
        json={"job_id": job["id"], "is_primary": True},
    )
    assert selected.status_code == 201
    assert selected.json()["job_name"] == "工业视觉工程师"
