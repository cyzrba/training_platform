"""接口层数据体检：用接口把整套测试数据过一遍，看业务不变量有没有被破坏。

用法：
    uv run python scripts/check_data.py             # 全量检查
    uv run python scripts/check_data.py --quiet     # 只打印失败的项

与 ``check_db.py`` 的分工：那个看**表结构 / 索引 / 外键 / 种子**，这个看**数据本身对不对**
——项目权重是否合计 100、学生有没有入班选岗、评审有没有结算、技能进度有没有跟着项目推进。

不占端口：进程内直接挂 ASGI，走的是真实路由与响应序列化，所以它同时也在验证接口本身。
有失败项时进程退出码为 1，可以直接挂到 CI 或发布前手动跑一遍。
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.main import app  # noqa: E402

#: 体检脚本要看全量，用最大页宽；需要翻页时由 page_all 处理
PAGE_SIZE = 200


@dataclass
class Check:
    """一条体检结论。"""

    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


async def call(client: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> object:
    """调接口并解开统一响应体；业务失败直接抛错，避免体检脚本自己静默半通过。"""
    response = await getattr(client, method)(url, **kwargs)
    payload = response.json()
    if isinstance(payload, dict) and payload.get("code") not in (None, 200):
        raise SystemExit(f"{method.upper()} {url} 失败：{payload.get('msg')}")
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


async def page_all(client: httpx.AsyncClient, path: str, **params: object) -> list[dict]:
    """翻完所有页。体检不能只看第一页——漏掉的数据往往正是出问题的那批。"""
    items: list[dict] = []
    page_no = 1
    while True:
        payload = await call(client, "get", path, params={"page_size": PAGE_SIZE, "page": page_no, **params})
        items.extend(payload["items"])
        if page_no >= payload.get("pages", 1):
            return items
        page_no += 1


# ------------------------------------------------------------------ 账户与权限


async def check_accounts(client: httpx.AsyncClient, checks: list[Check]) -> None:
    students = await page_all(client, "/api/users", user_type="STUDENT")
    teachers = await page_all(client, "/api/users", user_type="TEACHER")
    admins = await page_all(client, "/api/users", user_type="ADMIN")
    checks.append(
        Check(
            "账号：学生 / 教师 / 管理员三类都有数据",
            bool(students and teachers and admins),
            f"学生 {len(students)}、教师 {len(teachers)}、管理员 {len(admins)}",
        )
    )

    missing_roles: list[str] = []
    for user in students + teachers:
        want = "STUDENT" if user["user_type"] == "STUDENT" else "TEACHER"
        roles = await call(client, "get", f"/api/users/{user['id']}/roles")
        if want not in {role["role_code"] for role in roles}:
            missing_roles.append(f"{user['user_no']}({want})")
    checks.append(Check("账号：学生 / 教师都挂了对应角色", not missing_roles, "、".join(missing_roles[:5])))


# -------------------------------------------------------------------- 教学组织


async def check_organization(client: httpx.AsyncClient, checks: list[Check]) -> list[dict]:
    classes = await page_all(client, "/api/classes")
    no_teacher = [item["class_name"] for item in classes if not item.get("head_teacher_id")]
    checks.append(Check("教学组织：每个班都配了负责教师", not no_teacher, "、".join(no_teacher)))

    students = await page_all(client, "/api/users", user_type="STUDENT")
    enrolled: set[int] = set()
    bad_roster: list[str] = []
    for classroom in classes:
        for row in await page_all(client, f"/api/classes/{classroom['id']}/students"):
            if row.get("status") != "ENROLLED":
                continue
            enrolled.add(row["student_id"])
            if row.get("account_status") != "ACTIVE":
                bad_roster.append(f"{row['user_no']} 账号 {row.get('account_status')}")
    missing = [f"{item['user_no']}({item['real_name']})" for item in students if item["id"] not in enrolled]
    checks.append(
        Check(
            "教学组织：学生都已入班",
            not missing,
            f"未入班 {len(missing)} 人：{'、'.join(missing[:5])}"
            if missing
            else f"{len(enrolled)} 名在班学生",
        )
    )
    checks.append(Check("教学组织：在班学生账号都是 ACTIVE", not bad_roster, "、".join(bad_roster[:5])))
    return students


# ------------------------------------------------------------------ 岗位与技能


async def check_jobs_and_skills(client: httpx.AsyncClient, students: list[dict], checks: list[Check]) -> None:
    jobs = await page_all(client, "/api/jobs")
    no_skill = [
        item["job_name"] for item in jobs if not await call(client, "get", f"/api/jobs/{item['id']}/skills")
    ]
    checks.append(Check("岗位：每个岗位都配了技能", not no_skill, "、".join(no_skill)))

    without_job: list[str] = []
    multi_primary: list[str] = []
    for user in students:
        selections = await call(client, "get", f"/api/students/{user['id']}/jobs")
        if not selections:
            without_job.append(user["user_no"])
        elif len([item for item in selections if item["is_primary"]]) > 1:
            multi_primary.append(user["user_no"])
    checks.append(Check("岗位：每个学生都选了岗位", not without_job, "、".join(without_job[:5])))
    checks.append(Check("岗位：主岗位唯一", not multi_primary, "、".join(multi_primary[:5])))

    bad_range: list[str] = []
    bad_activated: list[str] = []
    bad_mastered: list[str] = []
    for user in students:
        for skill in await call(client, "get", f"/api/students/{user['id']}/skills"):
            progress = float(skill["progress"])
            mark = f"{user['user_no']}·{skill.get('node_name')}"
            if not 0 <= progress <= 100:
                bad_range.append(f"{mark}={progress}")
            if progress <= 0 and skill.get("activated_at"):
                bad_activated.append(mark)
            if progress >= 100 and not skill.get("mastered_at"):
                bad_mastered.append(mark)
    checks.append(Check("技能：进度都落在 0~100", not bad_range, "、".join(bad_range[:5])))
    checks.append(Check("技能：进度为 0 的没有激活时间", not bad_activated, "、".join(bad_activated[:5])))
    checks.append(Check("技能：进度 100% 的都有精通时间", not bad_mastered, "、".join(bad_mastered[:5])))


# -------------------------------------------------------------------- 实训项目


async def check_projects(client: httpx.AsyncClient, checks: list[Check]) -> dict[int, set[int]]:
    projects = await page_all(client, "/api/projects")
    published = [item for item in projects if item["status"] == "PUBLISHED"]
    bad_weight: list[str] = []
    no_skill: list[str] = []
    no_module: list[str] = []
    skill_nodes: dict[int, set[int]] = {}
    for project in published:
        detail = await call(client, "get", f"/api/projects/{project['id']}")
        if float(detail.get("weight_total") or 0) != 100:
            bad_weight.append(f"{project['project_name']}={detail.get('weight_total')}")
        nodes = await call(client, "get", f"/api/projects/{project['id']}/skills")
        skill_nodes[project["id"]] = {node["id"] for node in nodes}
        if not nodes:
            no_skill.append(project["project_name"])
        if not detail.get("modules"):
            no_module.append(project["project_name"])
    checks.append(Check("项目：已发布项目的模块权重合计 100", not bad_weight, "、".join(bad_weight)))
    checks.append(Check("项目：已发布项目都有关卡模块", not no_module, "、".join(no_module)))
    checks.append(
        Check(
            "项目：已发布项目都挂了技能点",
            not no_skill,
            "、".join(no_skill) if no_skill else f"{len(published)} 个已发布项目",
        )
    )

    # 任务下发：已发布的任务必须仍带着项目和目标 —— 否则学生端要么看不到，要么看到个空任务
    tasks = await page_all(client, "/api/publish-tasks")
    published_tasks = [item for item in tasks if item["status"] == "PUBLISHED"]
    empty_tasks: list[str] = []
    for task in published_tasks:
        detail = await call(client, "get", f"/api/publish-tasks/{task['id']}")
        if not detail["projects"]:
            empty_tasks.append(f"{task['title']}（没有项目）")
        if not detail["targets"]:
            empty_tasks.append(f"{task['title']}（没有目标班级）")
    checks.append(
        Check(
            "任务下发：已发布任务都有项目与目标",
            not empty_tasks,
            "、".join(empty_tasks) if empty_tasks else f"{len(published_tasks)} 条已发布任务",
        )
    )
    scheduled = [item["title"] for item in tasks if item["status"] == "PENDING"]
    checks.append(Check("任务下发：定时任务待发布", True, "、".join(scheduled) if scheduled else "无"))

    docs = await page_all(client, "/api/knowledge/docs", doc_type="EVAL_CRITERIA")
    checks.append(Check("知识库：评分标准已入库", bool(docs), f"{len(docs)} 份"))
    not_ready = [item["title"] for item in docs if item["status"] != "READY"]
    checks.append(Check("知识库：评分标准都已向量化（READY）", not not_ready, "、".join(not_ready)))
    no_chunks = [
        item["title"]
        for item in docs
        if not (await call(client, "get", f"/api/knowledge/docs/{item['id']}/chunks"))["items"]
    ]
    checks.append(Check("知识库：评分标准都有切片", not no_chunks, "、".join(no_chunks)))
    return skill_nodes


# ------------------------------------------------------------------ 闯关与评审


async def check_attempts(
    client: httpx.AsyncClient, students: list[dict], skill_nodes: dict[int, set[int]], checks: list[Check]
) -> None:
    not_advanced: list[str] = []
    for user in students:
        records = await call(client, "get", f"/api/students/{user['id']}/projects")
        skills = {
            item["skill_node_id"]: float(item["progress"])
            for item in await call(client, "get", f"/api/students/{user['id']}/skills")
        }
        for record in records:
            if record.get("status") != "COMPLETED":
                continue
            nodes = skill_nodes.get(record["project_id"], set())
            if nodes and not any(skills.get(node, 0) > 0 for node in nodes):
                not_advanced.append(f"{user['user_no']}·项目{record['project_id']}")
    checks.append(
        Check(
            "闯关：完成的项目都推进了技能进度",
            not not_advanced,
            "、".join(not_advanced[:5]),
        )
    )

    submissions = await page_all(client, "/api/submissions")
    unsettled: list[str] = []
    mismatch: list[str] = []
    for item in submissions:
        detail = await call(client, "get", f"/api/submissions/{item['id']}")
        finalized = [review for review in detail.get("reviews", []) if review["status"] == "FINAL"]
        conclusion = detail.get("final_conclusion")
        if item["status"] in {"AI_PASSED", "AI_FAILED", "REVIEWED"} and not finalized:
            unsettled.append(f"提交{item['id']} 已结算但没有终审记录")
        if item["status"] == "AI_PASSED" and conclusion != "PASS":
            mismatch.append(f"提交{item['id']} AI_PASSED 但结论 {conclusion}")
        if item["status"] == "AI_FAILED" and conclusion != "FAIL":
            mismatch.append(f"提交{item['id']} AI_FAILED 但结论 {conclusion}")
    checks.append(Check("评审：已结算的提交都有终审记录", not unsettled, "、".join(unsettled[:5])))
    checks.append(Check("评审：提交状态与评审结论一致", not mismatch, "、".join(mismatch[:5])))


# -------------------------------------------------------------------- AI 问答


async def check_qa(client: httpx.AsyncClient, students: list[dict], checks: list[Check]) -> None:
    sessions: list[dict] = []
    for user in students:
        sessions.extend(await page_all(client, "/api/qa/sessions", student_id=user["id"]))
    if not sessions:
        checks.append(Check("问答：暂无会话数据", True, "跳过（先跑一次问答再来体检）", skipped=True))
        return

    bad_role: list[str] = []
    bad_token: list[str] = []
    empty_answer: list[str] = []
    for row in sessions:
        detail = await call(
            client,
            "get",
            f"/api/qa/sessions/{row['id']}",
            params={"student_id": row["student_id"], "message_limit": 200},
        )
        for message in detail["messages"]:
            if message["role"] not in {"USER", "ASSISTANT"}:
                bad_role.append(f"会话{row['id']}·消息{message['id']}")
            if message["role"] == "USER" and (message["prompt_tokens"] or message["completion_tokens"]):
                bad_token.append(f"会话{row['id']}·USER 行不该有 token")
            if (
                message["role"] == "ASSISTANT"
                and message["status"] == "COMPLETED"
                and not message["model_name"]
            ):
                empty_answer.append(f"会话{row['id']}·消息{message['id']} 缺 model_name")
    checks.append(Check("问答：消息角色只有 USER / ASSISTANT", not bad_role, "、".join(bad_role[:5])))
    checks.append(Check("问答：token 只写在回答行上", not bad_token, "、".join(bad_token[:5])))
    checks.append(Check("问答：成功的回答都记了模型名", not empty_answer, "、".join(empty_answer[:5])))

    usage = await call(client, "get", "/api/qa/usage", params={"student_id": sessions[0]["student_id"]})
    recent = usage["recent"]
    checks.append(
        Check(
            "问答：用量接口能返回汇总",
            recent["question_count"] >= 1 and recent["total_tokens"] >= 0,
            f"当日 {usage['today']['total_tokens']} tokens / 保留期内 {recent['total_tokens']} tokens",
        )
    )


# -------------------------------------------------------------------- 无接口的域


def note_uncovered(checks: list[Check]) -> None:
    checks.append(
        Check(
            "证书 / 通知 / 审计：接口尚未实现",
            True,
            "这三域只有表（student_certificate / notification / operation_log），没有路由，体检跳过",
            skipped=True,
        )
    )


async def run(*, quiet: bool) -> int:
    checks: list[Check] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://check", timeout=120) as client:
        await check_accounts(client, checks)
        students = await check_organization(client, checks)
        await check_jobs_and_skills(client, students, checks)
        skill_nodes = await check_projects(client, checks)
        await check_attempts(client, students, skill_nodes, checks)
        await check_qa(client, students, checks)
        note_uncovered(checks)

    failed = [item for item in checks if not item.ok]
    for item in checks:
        if item.skipped and quiet:
            continue
        mark = "❌" if not item.ok else ("⋯" if item.skipped else "✅")
        line = f"{mark} {item.name}"
        if item.detail:
            line += f" —— {item.detail}"
        print(line)
    print()
    print(
        f"体检完成：{len(checks) - len(failed)} 项通过、{len(failed)} 项失败"
        f"（跳过 {len([item for item in checks if item.skipped])} 项）"
    )
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="用接口把测试数据体检一遍")
    parser.add_argument("--quiet", action="store_true", help="只打印失败项")
    args = parser.parse_args()
    return asyncio.run(run(quiet=args.quiet))


if __name__ == "__main__":
    raise SystemExit(main())
