"""学生端的成长视图：岗位推荐、技能树进度、实训项目进度（只读派生，不落库）。

三个视图分别服务三个接口：

- ``GET /api/students/{student_id}/job-recommendations`` —— 岗位推荐
- ``GET /api/students/{student_id}/skill-tree-progress`` —— 技能树总览
- ``GET /api/students/{student_id}/training-projects`` —— 实训项目列表（最高分 / 关卡进度）

统一口径（与 ``app/services/skill.py`` 一致，别再另起一套算法）：

- **技能点进度**取 ``student_skill.progress``（0~100，权威值；由
  ``recalculate_student_skills`` 按"完成项目数 ÷ 关联项目总数"维护，教师手工调整记 MANUAL）；
- **技能树进度 / 整体进度 / 岗位匹配度**都是"相关技能点进度的均值"，
  与前端技能树页面的口径一致（展示的是均值，不是按技能点加权）；
- **岗位的关联项目** = ``training_project.job_id`` 指向该岗位、且已发布（PUBLISHED）的项目；
  已完成 = 该学生 ``student_project.completed_at`` 有值（与技能进度的完成判定同源）；
- **关卡进度** = 项目已启用关卡数（``project_module``）与最新一轮闯关已填写关卡数
  （``attempt_stage.is_filled``），所以重新挑战会从 0 重新计，而最高分保留。
"""

from collections.abc import Iterable

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.attempt import AttemptStage, StudentProject, TrainingAttempt
from app.models.job_skill import Job, JobSkill, ProjectSkill, SkillNode, SkillTree, StudentSkill
from app.models.project import ProjectModule, TrainingProject

#: 进度达到 100 才算"已完成 / 已精通"
MASTERED_PROGRESS = 100.0


def _percent(values: Iterable[float]) -> float:
    """一组进度的均值（0~100，保留两位小数）；空集合按 0 处理。"""
    items = list(values)
    if not items:
        return 0.0
    return round(sum(items) / len(items), 2)


async def _student_progress_map(session: AsyncSession, student_id: int) -> dict[int, float]:
    """学生各技能点的进度；没有记录（没产生过进度）的技能点由调用方按 0 处理。"""
    stmt = select(StudentSkill.skill_node_id, StudentSkill.progress).where(
        StudentSkill.student_id == student_id
    )
    return {int(node_id): float(progress) for node_id, progress in (await session.exec(stmt)).all()}


async def _published_project_job_ids(session: AsyncSession) -> dict[int, set[int]]:
    """已发布项目 → 岗位 ID（只含绑定了岗位的项目）。"""
    stmt = select(TrainingProject.id, TrainingProject.job_id).where(
        TrainingProject.status == "PUBLISHED",
        TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
        TrainingProject.job_id.is_not(None),  # type: ignore[attr-defined]
    )
    grouped: dict[int, set[int]] = {}
    for project_id, job_id in (await session.exec(stmt)).all():
        grouped.setdefault(int(job_id), set()).add(int(project_id))
    return grouped


async def _published_node_project_ids(session: AsyncSession) -> dict[int, set[int]]:
    """技能点 → 培养它的已发布项目 ID（job/技能树视图里"关联项目数"的来源）。"""
    stmt = (
        select(ProjectSkill.skill_node_id, ProjectSkill.project_id)
        .join(TrainingProject, TrainingProject.id == ProjectSkill.project_id)  # type: ignore[arg-type]
        .where(
            TrainingProject.status == "PUBLISHED",
            TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
        )
    )
    grouped: dict[int, set[int]] = {}
    for node_id, project_id in (await session.exec(stmt)).all():
        grouped.setdefault(int(node_id), set()).add(int(project_id))
    return grouped


async def _completed_project_ids(session: AsyncSession, student_id: int) -> set[int]:
    """学生已完成的项目 ID（``completed_at`` 有值即算完成，见 services.skill 的说明）。"""
    stmt = select(StudentProject.project_id).where(
        StudentProject.student_id == student_id,
        StudentProject.completed_at.is_not(None),  # type: ignore[attr-defined]
    )
    return {int(project_id) for project_id in (await session.exec(stmt)).all()}


async def recommend_jobs(session: AsyncSession, student_id: int, *, limit: int = 3) -> list[dict]:
    """按学生技能进度推荐岗位，默认取前三名。

    排序：匹配度倒序 → 已达 100% 的技能点数倒序 → 岗位热度倒序 → 岗位 ID 升序（保证结果稳定）。
    只推荐启用中（ENABLED）、未软删、且**已经关联技能点**的岗位 ——
    没关联技能的岗位算不出匹配度，不参与推荐（此时前端展示"还没有推荐岗位"）。
    """
    stmt = select(Job).where(Job.status == "ENABLED", Job.deleted_at.is_(None))  # type: ignore[attr-defined]
    jobs = list((await session.exec(stmt)).all())
    if not jobs:
        return []

    links = (await session.exec(select(JobSkill.job_id, JobSkill.skill_node_id))).all()
    job_skill_ids: dict[int, list[int]] = {}
    for job_id, node_id in links:
        job_skill_ids.setdefault(int(job_id), []).append(int(node_id))

    nodes = list(
        (await session.exec(select(SkillNode).where(SkillNode.deleted_at.is_(None)))).all()  # type: ignore[attr-defined]
    )
    node_map = {int(node.id): node for node in nodes}
    trees = list(
        (await session.exec(select(SkillTree).where(SkillTree.deleted_at.is_(None)))).all()  # type: ignore[attr-defined]
    )
    tree_map = {int(tree.id): tree for tree in trees}

    progress_map = await _student_progress_map(session, student_id)
    projects_by_job = await _published_project_job_ids(session)
    completed_projects = await _completed_project_ids(session, student_id)

    items: list[dict] = []
    for job in jobs:
        job_id = int(job.id)
        node_ids = sorted(dict.fromkeys(job_skill_ids.get(job_id, [])))
        if not node_ids:
            continue
        progresses = [round(progress_map.get(node_id, 0.0), 2) for node_id in node_ids]

        # 按技能树（四大体系）分组：只返回该岗位真正用到的体系，空体系不出现在结果里
        groups: dict[int, list[dict]] = {}
        for node_id in node_ids:
            node = node_map.get(node_id)
            if node is None:
                continue
            groups.setdefault(node.tree_id, []).append(
                {
                    "skill_node_id": node_id,
                    "node_code": node.node_code,
                    "node_name": node.node_name,
                    "progress": round(progress_map.get(node_id, 0.0), 2),
                }
            )

        skills_by_tree = []
        for tree_id in sorted(groups, key=lambda value: (tree_map.get(value) is None, value)):
            tree = tree_map.get(tree_id)
            skills = sorted(groups[tree_id], key=lambda item: item["skill_node_id"])
            skills_by_tree.append(
                {
                    "tree_id": tree_id,
                    "tree_code": tree.tree_code if tree else None,
                    "tree_name": tree.tree_name if tree else None,
                    "skill_total_count": len(skills),
                    "skill_done_count": sum(1 for item in skills if item["progress"] >= MASTERED_PROGRESS),
                    "skills": skills,
                }
            )

        job_projects = projects_by_job.get(job_id, set())
        items.append(
            {
                "job_id": job_id,
                "job_name": job.job_name,
                "direction_tag": job.direction_tag,
                "recommended_level": job.recommended_level,
                "scene": job.scene,
                "description": job.description,
                "heat": job.heat,
                "match_score": _percent(progresses),
                "skill_total_count": len(node_ids),
                "skill_done_count": sum(1 for value in progresses if value >= MASTERED_PROGRESS),
                "project_total_count": len(job_projects),
                "project_done_count": len(job_projects & completed_projects),
                "skill_groups": skills_by_tree,
            }
        )

    items.sort(
        key=lambda item: (
            -item["match_score"],
            -item["skill_done_count"],
            -item["heat"],
            item["job_id"],
        )
    )
    return items[: max(limit, 0)]


async def skill_tree_progress(session: AsyncSession, student_id: int) -> dict:
    """全部技能树与技能节点 + 该学生的进度统计（技能树页面的右上角统计面板）。

    - ``trees[].percent``：该技能树下技能点进度的均值；
    - ``overall_percent``：全部技能点进度的均值（四个体系的整体进度）；
    - ``total_nodes`` / ``done_nodes``：技能点总数 / 进度已达 100% 的个数。
    """
    trees = list(
        (
            await session.exec(
                select(SkillTree)
                .where(SkillTree.deleted_at.is_(None))  # type: ignore[attr-defined]
                .order_by(SkillTree.id)
            )
        ).all()
    )
    nodes = list(
        (
            await session.exec(
                select(SkillNode)
                .where(SkillNode.deleted_at.is_(None))  # type: ignore[attr-defined]
                .order_by(SkillNode.tree_id, SkillNode.id)
            )
        ).all()
    )
    nodes_by_tree: dict[int, list[SkillNode]] = {}
    for node in nodes:
        nodes_by_tree.setdefault(node.tree_id, []).append(node)

    progress_map = await _student_progress_map(session, student_id)
    node_projects = await _published_node_project_ids(session)
    completed_projects = await _completed_project_ids(session, student_id)

    tree_items: list[dict] = []
    all_progress: list[float] = []
    done_nodes = 0
    for tree in trees:
        tree_nodes = nodes_by_tree.get(int(tree.id), [])
        node_items = []
        progresses: list[float] = []
        for node in tree_nodes:
            node_id = int(node.id)
            progress = round(progress_map.get(node_id, 0.0), 2)
            projects = node_projects.get(node_id, set())
            node_items.append(
                {
                    "skill_node_id": node_id,
                    "node_code": node.node_code,
                    "node_name": node.node_name,
                    "description": node.description,
                    "status": node.status,
                    "progress": progress,
                    "project_total": len(projects),
                    "project_done": len(projects & completed_projects),
                }
            )
            progresses.append(progress)
            all_progress.append(progress)

        tree_done = sum(1 for value in progresses if value >= MASTERED_PROGRESS)
        done_nodes += tree_done
        tree_items.append(
            {
                "tree_id": int(tree.id),
                "tree_code": tree.tree_code,
                "tree_name": tree.tree_name,
                "description": tree.description,
                "status": tree.status,
                "total": len(tree_nodes),
                "done": tree_done,
                "percent": _percent(progresses),
                "nodes": node_items,
            }
        )

    return {
        "student_id": student_id,
        "tree_count": len(tree_items),
        "total_nodes": len(all_progress),
        "done_nodes": done_nodes,
        "overall_percent": _percent(all_progress),
        "trees": tree_items,
    }


async def _level_totals(session: AsyncSession, project_ids: list[int]) -> dict[int, int]:
    """每个项目的关卡总数（= 项目里选中的模块数）。"""
    stmt = (
        select(ProjectModule.project_id, func.count())
        .where(ProjectModule.project_id.in_(project_ids))  # type: ignore[attr-defined]
        .group_by(ProjectModule.project_id)
    )
    return {int(project_id): int(count) for project_id, count in (await session.exec(stmt)).all()}


async def _latest_attempts(session: AsyncSession, record_ids: list[int]) -> dict[int, TrainingAttempt]:
    """学生每条实训记录的最新一轮闯关（``attempt_no`` 最大的那一轮）。"""
    if not record_ids:
        return {}
    stmt = select(TrainingAttempt).where(
        TrainingAttempt.student_project_id.in_(record_ids)  # type: ignore[attr-defined]
    )
    latest: dict[int, TrainingAttempt] = {}
    for attempt in (await session.exec(stmt)).all():
        record_id = int(attempt.student_project_id)
        current = latest.get(record_id)
        if current is None or attempt.attempt_no > current.attempt_no:
            latest[record_id] = attempt
    return latest


async def _filled_stage_counts(session: AsyncSession, attempt_ids: list[int]) -> dict[int, int]:
    """每一轮闯关里已填写（``is_filled``）的关卡数。"""
    if not attempt_ids:
        return {}
    stmt = (
        select(AttemptStage.attempt_id, func.count())
        .where(
            AttemptStage.attempt_id.in_(attempt_ids),  # type: ignore[attr-defined]
            AttemptStage.is_filled.is_(True),  # type: ignore[attr-defined]
        )
        .group_by(AttemptStage.attempt_id)
    )
    return {int(attempt_id): int(count) for attempt_id, count in (await session.exec(stmt)).all()}


async def _skills_of_projects(session: AsyncSession, project_ids: list[int]) -> dict[int, list[dict]]:
    """项目 → 关联技能点（带所属技能树，方便前端按体系取色/分组）。"""
    stmt = (
        select(ProjectSkill.project_id, SkillNode, SkillTree)
        .join(SkillNode, SkillNode.id == ProjectSkill.skill_node_id)  # type: ignore[arg-type]
        .join(SkillTree, SkillTree.id == SkillNode.tree_id)  # type: ignore[arg-type]
        .where(
            ProjectSkill.project_id.in_(project_ids),  # type: ignore[attr-defined]
            SkillNode.deleted_at.is_(None),  # type: ignore[attr-defined]
        )
        .order_by(ProjectSkill.project_id, SkillNode.tree_id, SkillNode.id)
    )
    grouped: dict[int, list[dict]] = {}
    for project_id, node, tree in (await session.exec(stmt)).all():
        grouped.setdefault(int(project_id), []).append(
            {
                "skill_node_id": int(node.id),
                "node_code": node.node_code,
                "node_name": node.node_name,
                "tree_id": int(tree.id),
                "tree_code": tree.tree_code,
                "tree_name": tree.tree_name,
            }
        )
    return grouped


async def student_projects(session: AsyncSession, student_id: int) -> list[dict]:
    """学生的实训项目列表：项目信息 + 该学生的最高分、当前关卡进度与状态。

    - 列出所有已发布（PUBLISHED）且未软删的项目；学生没开始过的项目也返回，
      ``status=NOT_STARTED``、成绩为 null、关卡进度 0/总数（前端可以自行过滤）；
    - ``level_total`` = 项目选中的关卡数（``project_module``）；
    - ``level_done`` = 最新一轮闯关已填写的关卡数（``attempt_stage.is_filled``）；
    - ``best_score`` 只升不降，重新挑战也不会被清掉。
    """
    stmt = (
        select(TrainingProject)
        .where(
            TrainingProject.status == "PUBLISHED",
            TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
        )
        .order_by(TrainingProject.id)
    )
    projects = list((await session.exec(stmt)).all())
    if not projects:
        return []
    project_ids = [int(project.id) for project in projects]

    level_totals = await _level_totals(session, project_ids)

    record_stmt = select(StudentProject).where(
        StudentProject.student_id == student_id,
        StudentProject.project_id.in_(project_ids),  # type: ignore[attr-defined]
    )
    records = list((await session.exec(record_stmt)).all())
    record_by_project = {int(record.project_id): record for record in records}
    latest_attempts = await _latest_attempts(session, [int(record.id) for record in records])
    filled_counts = await _filled_stage_counts(
        session, [int(attempt.id) for attempt in latest_attempts.values()]
    )

    job_ids = sorted({int(project.job_id) for project in projects if project.job_id is not None})
    job_names: dict[int, str] = {}
    if job_ids:
        job_stmt = select(Job).where(Job.id.in_(job_ids))  # type: ignore[attr-defined]
        job_names = {int(job.id): job.job_name for job in (await session.exec(job_stmt)).all()}

    skills_by_project = await _skills_of_projects(session, project_ids)

    items: list[dict] = []
    for project in projects:
        project_id = int(project.id)
        record = record_by_project.get(project_id)
        attempt = latest_attempts.get(int(record.id)) if record is not None else None
        level_total = level_totals.get(project_id, 0)
        level_done = min(filled_counts.get(int(attempt.id), 0), level_total) if attempt else 0
        best_score = record.best_score if record is not None else None
        total_score = record.total_score if record is not None else None
        items.append(
            {
                "project_id": project_id,
                "project_name": project.project_name,
                "project_level": project.project_level,
                "job_id": int(project.job_id) if project.job_id is not None else None,
                "job_name": job_names.get(int(project.job_id)) if project.job_id is not None else None,
                "status": record.status if record is not None else "NOT_STARTED",
                "best_score": float(best_score) if best_score is not None else None,
                "total_score": float(total_score) if total_score is not None else None,
                "progress": float(record.progress) if record else 0.0,
                "level_total": level_total,
                "level_done": level_done,
                "skill_nodes": skills_by_project.get(project_id, []),
            }
        )
    return items


__all__ = [
    "MASTERED_PROGRESS",
    "recommend_jobs",
    "skill_tree_progress",
    "student_projects",
]
