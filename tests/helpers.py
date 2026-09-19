"""测试公共造数：把项目"点名"给学生，并补上入班 / 分组。

口径（见 docs/方案设计.md §4.2）：项目 ``status = 'PUBLISHED'`` 就对全体学生开放，
学生不用等任务；任务的作用是把项目标成该批学生的**必修**（``is_required``）。
所以这里的 helper 只是把"老师发任务点名"这件事造出来，闯关本身不依赖它。
"""

from typing import Any

import httpx

CLASSES = "/api/classes"
PUBLISH_TASKS = "/api/publish-tasks"

#: 用例默认班级名（同一用例内重复调用会复用同一个班）
DEFAULT_CLASS = "测试班"


async def class_id(client: httpx.AsyncClient, *, name: str = DEFAULT_CLASS) -> int:
    """按名字找班级，没有就建一个。"""
    listed = (await client.get(CLASSES, params={"keyword": name, "page_size": 50})).json()
    for item in listed["items"]:
        if item["class_name"] == name:
            return int(item["id"])
    created = await client.post(CLASSES, json={"class_name": name, "group_count": 3})
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


async def ensure_groups(client: httpx.AsyncClient, class_id_: int, count: int) -> list[dict]:
    """确保班级里有 1..count 这些分组（分组要显式建，class.group_count 只是展示用）。"""
    existing = (await client.get(f"{CLASSES}/{class_id_}/groups")).json()
    have = {int(item["group_no"]) for item in existing}
    for group_no in range(1, count + 1):
        if group_no in have:
            continue
        created = await client.post(
            f"{CLASSES}/{class_id_}/groups",
            json={"group_no": group_no, "group_name": f"第{group_no}组"},
        )
        assert created.status_code == 201, created.text
        existing.append(created.json())
    return existing


async def enroll(
    client: httpx.AsyncClient,
    user_no: str,
    *,
    name: str = DEFAULT_CLASS,
    group_no: int | None = None,
    class_id_: int | None = None,
) -> int:
    """把学生加入班级（账号已存在则复用）；返回班级 ID。"""
    cid = class_id_ if class_id_ is not None else await class_id(client, name=name)
    if group_no is not None:
        await ensure_groups(client, cid, group_no)
    payload: dict[str, Any] = {"user_no": user_no}
    if group_no is not None:
        payload["group_no"] = group_no
    response = await client.post(f"{CLASSES}/{cid}/students", json=payload)
    assert response.status_code == 201, response.text
    return cid


async def publish(
    client: httpx.AsyncClient,
    project_ids: list[int],
    *,
    name: str = DEFAULT_CLASS,
    class_id_: int | None = None,
    title: str = "测试任务",
    **extra: Any,
) -> dict:
    """发一条即时任务，把项目发给该班级（全班目标）。"""
    cid = class_id_ if class_id_ is not None else await class_id(client, name=name)
    payload = {
        "title": title,
        "targets": [{"class_id": cid, "target_type": "CLASS"}],
        "project_ids": list(project_ids),
        "publish_mode": "IMMEDIATE",
        **extra,
    }
    response = await client.post(PUBLISH_TASKS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def make_visible(
    client: httpx.AsyncClient,
    *,
    user_nos: list[str],
    project_ids: list[int],
    name: str = DEFAULT_CLASS,
    title: str = "测试任务",
) -> dict:
    """一步到位：学生入班 + 项目发给该班（需要客户端拉数据一律真实走接口）。"""
    cid = await class_id(client, name=name)
    for user_no in user_nos:
        await enroll(client, user_no, name=name, class_id_=cid)
    return await publish(client, project_ids, name=name, class_id_=cid, title=title)


__all__ = [
    "DEFAULT_CLASS",
    "class_id",
    "enroll",
    "ensure_groups",
    "make_visible",
    "publish",
]
