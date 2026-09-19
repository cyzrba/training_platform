"""教学组织域接口测试：班级、分组、在班学生、Excel 名单导入。"""

from io import BytesIO

import httpx
import pytest
from openpyxl import Workbook

from app.core.config import settings
from app.crud.account import UserRepository
from app.services.password import verify_password
from app.services.student_import import TEMPLATE_COLUMNS

CLASSES = "/api/classes"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(rows: list[list[object]], header: list[str] | None = None) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(header or list(TEMPLATE_COLUMNS))
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


async def _create_class(client: httpx.AsyncClient, name: str = "人工智能2401班") -> dict:
    response = await client.post(CLASSES, json={"class_name": name, "grade_year": 2024, "group_count": 2})
    assert response.status_code == 201, response.text
    return response.json()


async def _import(
    client: httpx.AsyncClient,
    class_id: int,
    content: bytes,
    *,
    dry_run: bool = False,
    reuse_existing: bool = False,
) -> httpx.Response:
    return await client.post(
        f"{CLASSES}/{class_id}/students/import",
        files={"file": ("students.xlsx", content, XLSX_MIME)},
        data={"dry_run": str(dry_run).lower(), "reuse_existing": str(reuse_existing).lower()},
    )


@pytest.mark.asyncio
async def test_class_crud_and_detail(client: httpx.AsyncClient) -> None:
    classroom = await _create_class(client)
    assert classroom["status"] == "ACTIVE" and classroom["group_count"] == 2

    detail = await client.get(f"{CLASSES}/{classroom['id']}")
    assert detail.json()["student_count"] == 0

    listed = (await client.get(CLASSES, params={"keyword": "人工智能", "status": "ACTIVE"})).json()
    assert listed["total"] == 1
    assert (await client.get(CLASSES, params={"status": "ARCHIVED"})).json()["total"] == 0

    patched = await client.patch(f"{CLASSES}/{classroom['id']}", json={"remark": "春季班"})
    assert patched.json()["remark"] == "春季班"

    assert (await client.delete(f"{CLASSES}/{classroom['id']}")).status_code == 200
    assert (await client.get(f"{CLASSES}/{classroom['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_groups_and_membership_guard(client: httpx.AsyncClient) -> None:
    classroom = await _create_class(client)

    first = await client.post(f"{CLASSES}/{classroom['id']}/groups", json={"group_name": "第一组"})
    second = await client.post(f"{CLASSES}/{classroom['id']}/groups", json={"group_name": "第二组"})
    assert [first.json()["group_no"], second.json()["group_no"]] == [1, 2]  # 序号自动分配

    duplicate = await client.post(
        f"{CLASSES}/{classroom['id']}/groups", json={"group_name": "重复组", "group_no": 2}
    )
    assert duplicate.status_code == 200

    listed = (await client.get(f"{CLASSES}/{classroom['id']}/groups")).json()
    assert [item["student_count"] for item in listed] == [0, 0]

    # 单人加学生并直接分组
    added = await client.post(
        f"{CLASSES}/{classroom['id']}/students",
        json={"user_no": "2026001", "real_name": "张三", "group_no": 1},
    )
    assert added.status_code == 201, added.text
    assert added.json()["group_name"] == "第一组"

    # 组内有人时不允许删组
    blocked = await client.delete(f"/api/groups/{first.json()['id']}")
    assert blocked.status_code == 200

    # 学生转组
    moved = await client.put(
        f"/api/class-students/{added.json()['id']}/group", json={"group_id": second.json()["id"]}
    )
    assert moved.status_code == 200
    assert moved.json()["group_id"] == second.json()["id"]

    # 跨班分组要拒绝
    other = await _create_class(client, "另一个班")
    other_group = (await client.post(f"{CLASSES}/{other['id']}/groups", json={"group_name": "外班组"})).json()
    cross = await client.put(
        f"/api/class-students/{added.json()['id']}/group", json={"group_id": other_group["id"]}
    )
    assert cross.status_code == 200


@pytest.mark.asyncio
async def test_group_member_view_and_assignment(client: httpx.AsyncClient) -> None:
    """建班 → 导学生 → 建分组 → 把学生加进分组（教师端当前流程）。"""
    classroom = await _create_class(client)
    content = _xlsx(
        [
            ["2026041", "学生甲", "", "", ""],
            ["2026042", "学生乙", "", "", ""],
            ["2026043", "学生丙", "", "", ""],
        ]
    )
    assert (await _import(client, classroom["id"], content)).status_code == 200

    roster = (await client.get(f"{CLASSES}/{classroom['id']}/students")).json()
    class_student_ids = [item["id"] for item in roster["items"]]

    first = (await client.post(f"{CLASSES}/{classroom['id']}/groups", json={"group_name": "第一组"})).json()
    second = (await client.post(f"{CLASSES}/{classroom['id']}/groups", json={"group_name": "第二组"})).json()

    # 批量加人：两名学生进第一组，一名进第二组
    added = await client.post(
        f"/api/groups/{first['id']}/students",
        json={"class_student_ids": class_student_ids[:2]},
    )
    assert added.json() == {"group_id": first["id"], "added": 2, "member_total": 2}
    await client.post(
        f"/api/groups/{second['id']}/students", json={"class_student_ids": class_student_ids[2:]}
    )

    # 看某个分组里有哪些学生
    members = (await client.get(f"/api/groups/{first['id']}/students")).json()
    assert members["total"] == 2
    assert sorted(item["user_no"] for item in members["items"]) == ["2026041", "2026042"]
    assert all(item["group_name"] == "第一组" for item in members["items"])

    # 一次性拉「分组 + 组内成员」
    view = (await client.get(f"{CLASSES}/{classroom['id']}/groups", params={"with_students": "true"})).json()
    assert [group["student_count"] for group in view] == [2, 1]
    assert [len(group["students"]) for group in view] == [2, 1]
    assert view[0]["students"][0]["real_name"] == "学生甲"

    # 不带参数时只有人数、没有成员明细
    plain = (await client.get(f"{CLASSES}/{classroom['id']}/groups")).json()
    assert all(group["students"] == [] for group in plain)

    # 移出分组 → 回到未分组
    removed = await client.delete(f"/api/groups/{first['id']}/students/{class_student_ids[0]}")
    assert removed.status_code == 200
    assert (await client.get(f"/api/groups/{first['id']}/students")).json()["total"] == 1
    ungrouped = (
        await client.get(f"{CLASSES}/{classroom['id']}/students", params={"ungrouped": "true"})
    ).json()
    assert [item["user_no"] for item in ungrouped["items"]] == ["2026041"]

    # 移出不在组里的学生 → 报错
    not_in_group = await client.delete(f"/api/groups/{first['id']}/students/{class_student_ids[0]}")
    assert not_in_group.json()["code"] == 422

    # 跨班学生加进本组 → 拒绝
    other_class = await _create_class(client, "另一个班")
    outsider = (
        await client.post(
            f"{CLASSES}/{other_class['id']}/students",
            json={"user_no": "2026099", "real_name": "外班学生"},
        )
    ).json()
    cross = await client.post(
        f"/api/groups/{first['id']}/students",
        json={"class_student_ids": [outsider["id"]]},
    )
    assert cross.json()["code"] == 422
    assert "不属于" in cross.json()["msg"]

    # ungrouped 与 group_id 互斥
    conflict = await client.get(
        f"{CLASSES}/{classroom['id']}/students",
        params={"ungrouped": "true", "group_id": first["id"]},
    )
    assert conflict.json()["code"] == 422


@pytest.mark.asyncio
async def test_add_student_creates_account_with_default_password(
    client: httpx.AsyncClient, db_session
) -> None:
    classroom = await _create_class(client)
    response = await client.post(
        f"{CLASSES}/{classroom['id']}/students",
        json={"user_no": "2026010", "real_name": "王五", "major_name": "人工智能技术应用"},
    )
    assert response.status_code == 201
    enrollment = response.json()
    assert enrollment["status"] == "ENROLLED" and enrollment["account_status"] == "ACTIVE"

    student = await UserRepository(db_session).get_by(user_no="2026010")
    assert student is not None and student.user_type == "STUDENT"
    assert verify_password(settings.default_password, student.password_hash)

    # 同一学生重复加入不会产生第二条在读记录
    again = await client.post(f"{CLASSES}/{classroom['id']}/students", json={"user_no": "2026010"})
    assert again.status_code == 201
    assert (await client.get(f"{CLASSES}/{classroom['id']}/students")).json()["total"] == 1

    # 没有姓名且账号不存在时明确报错
    missing_name = await client.post(f"{CLASSES}/{classroom['id']}/students", json={"user_no": "2026011"})
    assert missing_name.status_code == 200
    assert missing_name.json()["code"] == 422


@pytest.mark.asyncio
async def test_roster_leave_class_and_password_reset(client: httpx.AsyncClient, db_session) -> None:
    classroom = await _create_class(client)
    content = _xlsx([["2026021", "赵六", "人工智能技术应用", "", "", ""]])
    assert (await _import(client, classroom["id"], content)).status_code == 200

    roster = (await client.get(f"{CLASSES}/{classroom['id']}/students")).json()
    assert roster["total"] == 1
    row = roster["items"][0]
    assert row["user_no"] == "2026021" and row["class_name"] == "人工智能2401班"

    reset = await client.post(
        f"{CLASSES}/{classroom['id']}/students/password/reset", json={"new_password": "xue123456"}
    )
    assert reset.json()["updated"] == 1
    student = await UserRepository(db_session).get_by(user_no="2026021")
    assert student is not None and verify_password("xue123456", student.password_hash)

    left = await client.delete(f"/api/class-students/{row['id']}")
    assert left.status_code == 200
    assert (await client.get(f"{CLASSES}/{classroom['id']}/students")).json()["total"] == 0
    assert (await client.get(f"{CLASSES}/{classroom['id']}/students", params={"status": "LEFT"})).json()[
        "total"
    ] == 1
    assert (await client.get(f"{CLASSES}/{classroom['id']}")).json()["student_count"] == 0


@pytest.mark.asyncio
async def test_import_creates_accounts_without_group_column(client: httpx.AsyncClient, db_session) -> None:
    """教师流程：先导名单（不分组），分组稍后再建。"""
    classroom = await _create_class(client)

    content = _xlsx(
        [
            ["2026031", "学生甲", "人工智能技术应用", "13800000001", "a@example.com"],
            ["2026032", "学生乙", "", "", ""],
            ["2026033", "学生丙", "", "", ""],
        ]
    )
    response = await _import(client, classroom["id"], content)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == {
        "dry_run": False,
        "class_id": classroom["id"],
        "total": 3,
        "created_users": 3,
        "reused_users": 0,
        "created_class_students": 3,
        "assigned_groups": 0,
        "ignored_group_hints": 0,
    }

    # 账号按默认密码建好、状态在班、全体未分组
    students = UserRepository(db_session)
    imported = await students.get_by(user_no="2026031")
    assert imported is not None and verify_password(settings.default_password, imported.password_hash)

    roster = (await client.get(f"{CLASSES}/{classroom['id']}/students")).json()
    assert roster["total"] == 3
    assert all(item["group_id"] is None for item in roster["items"])
    assert (await client.get(f"{CLASSES}/{classroom['id']}")).json()["student_count"] == 3

    ungrouped = (
        await client.get(f"{CLASSES}/{classroom['id']}/students", params={"ungrouped": "true"})
    ).json()
    assert ungrouped["total"] == 3


@pytest.mark.asyncio
async def test_import_legacy_group_column_honors_existing_groups(
    client: httpx.AsyncClient,
) -> None:
    """历史模版带「分组序号」列时：分组已建就顺手分组，没建就忽略并计数。"""
    classroom = await _create_class(client)
    group = (await client.post(f"{CLASSES}/{classroom['id']}/groups", json={"group_name": "第一组"})).json()

    content = _xlsx(
        [
            ["2026035", "学生甲", "", "", "", 1],
            ["2026036", "学生乙", "", "", "", 1],
            ["2026037", "学生丙", "", "", "", 9],
        ],
        header=["学号", "姓名", "专业", "手机号", "邮箱", "分组序号"],
    )
    result = (await _import(client, classroom["id"], content)).json()
    assert result["assigned_groups"] == 2
    assert result["ignored_group_hints"] == 1  # 第 9 组还没建，按未分组导入

    members = (await client.get(f"/api/groups/{group['id']}/students")).json()
    assert sorted(item["user_no"] for item in members["items"]) == ["2026035", "2026036"]
    assert members["total"] == 2

    ungrouped = (
        await client.get(f"{CLASSES}/{classroom['id']}/students", params={"ungrouped": "true"})
    ).json()
    assert [item["user_no"] for item in ungrouped["items"]] == ["2026037"]


@pytest.mark.asyncio
async def test_import_dry_run_does_not_write(client: httpx.AsyncClient, db_session) -> None:
    classroom = await _create_class(client)
    content = _xlsx([["2026041", "预检学生", "", "", "", ""]])

    preview = await _import(client, classroom["id"], content, dry_run=True)
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert preview.json()["created_users"] == 1
    assert (await UserRepository(db_session).get_by(user_no="2026041")) is None
    assert (await client.get(f"{CLASSES}/{classroom['id']}/students")).json()["total"] == 0


@pytest.mark.asyncio
async def test_import_rejects_duplicate_user_no(client: httpx.AsyncClient, db_session) -> None:
    classroom = await _create_class(client)
    content = _xlsx(
        [
            ["2026051", "学生甲", "", "", "", ""],
            ["2026052", "学生乙", "", "", "", ""],
            ["2026051", "学生甲重复", "", "", "", ""],
        ]
    )
    response = await _import(client, classroom["id"], content)
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 422
    reasons = [item["reason"] for item in payload["data"]]
    assert any("与第 2 行重复" in reason for reason in reasons)

    # 整批撤回：没有落任何数据
    assert (await client.get(f"{CLASSES}/{classroom['id']}/students")).json()["total"] == 0
    assert (await UserRepository(db_session).get_by(user_no="2026052")) is None


@pytest.mark.asyncio
async def test_import_rejects_existing_user_and_bad_rows(client: httpx.AsyncClient, db_session) -> None:
    classroom = await _create_class(client)
    await UserRepository(db_session).create(
        {"user_no": "2026061", "real_name": "已建号学生", "user_type": "STUDENT"}
    )

    existing = await _import(client, classroom["id"], _xlsx([["2026061", "已建号学生", "", "", "", ""]]))
    assert existing.status_code == 200
    assert "已存在" in existing.json()["data"][0]["reason"]

    missing_name = await _import(client, classroom["id"], _xlsx([["2026062", "", "", "", "", ""]]))
    assert missing_name.status_code == 200
    assert "姓名不能为空" in missing_name.json()["data"][0]["reason"]

    # 分组序号对不上不再是错误：按未分组导入，并在结果里提示忽略了几行
    stale_group = await _import(
        client,
        classroom["id"],
        _xlsx(
            [["2026063", "学生丙", "", "", "", 9]],
            header=["学号", "姓名", "专业", "手机号", "邮箱", "分组序号"],
        ),
    )
    assert stale_group.status_code == 200
    assert stale_group.json()["ignored_group_hints"] == 1
    assert stale_group.json()["assigned_groups"] == 0

    not_a_number = await _import(
        client, classroom["id"], _xlsx([["2026064", "学生丁", "", "", "", "第一组"]])
    )
    assert not_a_number.status_code == 200

    wrong_header = await _import(
        client, classroom["id"], _xlsx([["2026065", "学生戊"]], header=["编号", "名字"])
    )
    assert wrong_header.status_code == 200
    assert "缺少必填列" in wrong_header.json()["msg"]

    broken = await _import(client, classroom["id"], b"not-an-excel-file")
    assert broken.status_code == 200
    assert "无法解析 Excel" in broken.json()["msg"]


@pytest.mark.asyncio
async def test_import_reuse_existing_users_into_new_class(client: httpx.AsyncClient) -> None:
    """换老师 / 转班场景：reuse_existing=true 时，已存在的学号复用账号并加入新班。"""
    old_class = await _create_class(client, "老班")
    new_class = await _create_class(client, "新班")
    content = _xlsx([["2026081", "老班学生", "人工智能技术应用", "", ""]])

    first = await _import(client, old_class["id"], content)
    assert first.status_code == 200 and first.json()["created_users"] == 1

    # 默认行为不变：已存在的学号仍然整批拒绝
    rejected = await _import(client, new_class["id"], content)
    assert rejected.status_code == 200
    assert "已存在" in rejected.json()["data"][0]["reason"]
    assert (await client.get(f"{CLASSES}/{new_class['id']}/students")).json()["total"] == 0

    # 显式复用：账号不新建，只是多一条新班的在班记录
    reused = await _import(client, new_class["id"], content, reuse_existing=True)
    assert reused.status_code == 200, reused.text
    payload = reused.json()
    assert (payload["created_users"], payload["reused_users"]) == (0, 1)
    assert payload["created_class_students"] == 1

    roster = (await client.get(f"{CLASSES}/{new_class['id']}/students")).json()
    assert [(item["user_no"], item["real_name"]) for item in roster["items"]] == [("2026081", "老班学生")]
    # 同一个学生账号，在两个班里各有一条在班记录
    assert (
        roster["items"][0]["student_id"]
        == (await client.get(f"{CLASSES}/{old_class['id']}/students")).json()["items"][0]["student_id"]
    )

    # 文件内重复学号仍然是错误（防止名单里混进重复行）
    duplicated = await _import(
        client,
        new_class["id"],
        _xlsx([["2026082", "甲", "", "", ""], ["2026082", "乙", "", "", ""]]),
        reuse_existing=True,
    )
    assert duplicated.status_code == 200
    assert "重复" in duplicated.json()["data"][0]["reason"]


@pytest.mark.asyncio
async def test_import_template_download(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/classes/students/import-template")
    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MIME
    assert "student-import-template.xlsx" in response.headers["content-disposition"]
    assert response.content[:2] == b"PK"  # xlsx 本质是 zip
