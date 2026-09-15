"""学生端的成长视图：岗位推荐、技能树进度、实训项目进度与项目详情（只读派生，不落库）。

五个视图分别服务五个接口：

- ``GET /api/students/{student_id}/job-recommendations`` —— 岗位推荐
- ``GET /api/students/{student_id}/skill-tree-progress`` —— 技能树总览
- ``GET /api/students/{student_id}/training-projects`` —— 实训项目列表（最高分 / 关卡进度）
- ``GET /api/students/{student_id}/job-project-progress`` —— 所选岗位上项目的分档进度
- ``GET /api/students/{student_id}/projects/{project_id}`` —— 单个项目的全量详情
  （任务简介、关卡与子标题、提交历史与 AI/教师评语）

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

from app.core.exceptions import NotFoundError
from app.models.account import SysUser
from app.models.attempt import (
    AttemptStage,
    AttemptStageFile,
    ProjectSubmission,
    StudentProject,
    TrainingAttempt,
)
from app.models.enums import ENUM_INDEX, LearningLevel
from app.models.job_skill import (
    Job,
    JobSkill,
    ProjectSkill,
    SkillNode,
    SkillTree,
    StudentJob,
    StudentSkill,
)
from app.models.project import ProjectModule, ProjectStageTemplate, TrainingProject
from app.models.review import ReviewRecord

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


def _level_labels() -> dict[str, str]:
    """层级文案（基础 / 进阶 / 拓展）从枚举字典取，避免在业务代码里硬编码中文。"""
    return {item.code: item.label for item in ENUM_INDEX["learning_level"].items}


def _ratio_percent(part: int, whole: int) -> float:
    """完成占比 0~100（保留两位小数）；分母为 0 时返回 0。"""
    if whole <= 0:
        return 0.0
    return round(part * 100 / whole, 2)


async def _resolve_job(session: AsyncSession, student_id: int, job_id: int | None) -> tuple[Job | None, bool]:
    """定位要统计的岗位：显式传 job_id 就用它，否则取学生当前主岗位（没有主岗位取最近选的）。

    返回值第二项表示"这个岗位是不是学生当前的主岗位"。
    """
    if job_id is not None:
        job = await session.get(Job, job_id)
        if job is None:
            raise NotFoundError(f"岗位 {job_id} 不存在")
        selection = (
            await session.exec(
                select(StudentJob).where(
                    StudentJob.student_id == student_id,
                    StudentJob.job_id == job_id,
                    StudentJob.is_primary.is_(True),  # type: ignore[attr-defined]
                )
            )
        ).first()
        return job, selection is not None

    stmt = (
        select(StudentJob)
        .where(StudentJob.student_id == student_id)
        .order_by(StudentJob.is_primary.desc(), StudentJob.id.desc())  # type: ignore[attr-defined]
        .limit(1)
    )
    selection = (await session.exec(stmt)).first()
    if selection is None:
        return None, False
    job = await session.get(Job, selection.job_id)
    return job, bool(selection.is_primary)


async def job_project_progress(session: AsyncSession, student_id: int, *, job_id: int | None = None) -> dict:
    """学生所选岗位上的实训项目进度：按基础 / 进阶 / 拓展三档统计"总数 / 已完成数"。

    - 岗位取 ``job_id`` 指定的那个；不传时取学生当前主岗位，没有主岗位则取最近选的一个；
      一个岗位都没选时返回 ``job_id=null`` 且三档全 0，前端可据此引导去选岗；
    - 传了不存在的 ``job_id`` 直接报"岗位不存在"，避免把笔误当成"没选岗位"；
    - 只统计该岗位下**已发布（PUBLISHED）**且未软删的项目；
    - 已完成 = 该学生 ``student_project.completed_at`` 有值（与技能进度的完成判定同源）。
    """
    job, is_primary = await _resolve_job(session, student_id, job_id)
    labels = _level_labels()

    totals: dict[str, int] = {}
    completed: dict[str, int] = {}
    if job is not None:
        filters = (
            TrainingProject.job_id == job.id,
            TrainingProject.status == "PUBLISHED",
            TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
        )
        total_stmt = (
            select(TrainingProject.project_level, func.count())
            .where(*filters)
            .group_by(TrainingProject.project_level)
        )
        totals = {str(level): int(count) for level, count in (await session.exec(total_stmt)).all()}

        done_stmt = (
            select(TrainingProject.project_level, func.count())
            .join(StudentProject, StudentProject.project_id == TrainingProject.id)  # type: ignore[arg-type]
            .where(
                *filters,
                StudentProject.student_id == student_id,
                StudentProject.completed_at.is_not(None),  # type: ignore[attr-defined]
            )
            .group_by(TrainingProject.project_level)
        )
        completed = {str(level): int(count) for level, count in (await session.exec(done_stmt)).all()}

    level_items = []
    for level in LearningLevel:
        level_total = totals.get(level.value, 0)
        level_done = completed.get(level.value, 0)
        level_items.append(
            {
                "level_type": level.value,
                "level_name": labels.get(level.value, level.value),
                "total": level_total,
                "completed": level_done,
                "percent": _ratio_percent(level_done, level_total),
            }
        )

    return {
        "student_id": student_id,
        "job_id": int(job.id) if job is not None else None,
        "job_name": job.job_name if job is not None else None,
        "is_primary": is_primary,
        "total": sum(item["total"] for item in level_items),
        "completed": sum(item["completed"] for item in level_items),
        "levels": level_items,
    }


def _sub_titles(raw: object) -> list[dict]:
    """把 ``project_module.items_json`` 归一化成 ``[{"title": ..., "prompt": ...}]``。

    只保留有标题的项；``prompt`` 就是子标题的填写简介（教师填关卡时写的引导说明）。
    """
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        prompt = entry.get("prompt")
        items.append({"title": title, "prompt": str(prompt).strip() if prompt else None})
    return items


def _float_or_none(value: object) -> float | None:
    return float(value) if value is not None else None  # type: ignore[arg-type]


async def _attempt_stage_state(
    session: AsyncSession, attempt_id: int | None
) -> tuple[dict[int, AttemptStage], dict[int, int]]:
    """某一轮闯关的作答状态：``{项目模块 ID: 作答行}`` 与 ``{作答行 ID: 附件数}``。"""
    if attempt_id is None:
        return {}, {}
    stages = list(
        (await session.exec(select(AttemptStage).where(AttemptStage.attempt_id == attempt_id))).all()
    )
    stage_ids = [int(stage.id) for stage in stages]
    file_counts: dict[int, int] = {}
    if stage_ids:
        file_stmt = (
            select(AttemptStageFile.attempt_stage_id, func.count())
            .where(AttemptStageFile.attempt_stage_id.in_(stage_ids))  # type: ignore[attr-defined]
            .group_by(AttemptStageFile.attempt_stage_id)
        )
        file_counts = {int(stage_id): int(count) for stage_id, count in (await session.exec(file_stmt)).all()}
    return {int(stage.project_module_id): stage for stage in stages}, file_counts


async def _review_history(session: AsyncSession, submission_ids: list[int]) -> dict[int, list[dict]]:
    """提交 → 该次提交上的评审记录（AI 与教师，按类型/版本排序）。"""
    if not submission_ids:
        return {}
    stmt = (
        select(ReviewRecord)
        .where(ReviewRecord.submission_id.in_(submission_ids))  # type: ignore[attr-defined]
        .order_by(ReviewRecord.submission_id, ReviewRecord.review_kind, ReviewRecord.version_no)
    )
    reviews = list((await session.exec(stmt)).all())

    reviewer_ids = sorted({int(item.reviewer_id) for item in reviews if item.reviewer_id is not None})
    reviewer_names: dict[int, str] = {}
    if reviewer_ids:
        user_stmt = select(SysUser).where(SysUser.id.in_(reviewer_ids))  # type: ignore[attr-defined]
        reviewer_names = {int(user.id): user.real_name for user in (await session.exec(user_stmt)).all()}

    grouped: dict[int, list[dict]] = {}
    for review in reviews:
        grouped.setdefault(int(review.submission_id), []).append(
            {
                "review_id": int(review.id),
                "review_kind": review.review_kind,
                "reviewer_id": review.reviewer_id,
                "reviewer_name": reviewer_names.get(int(review.reviewer_id))
                if review.reviewer_id is not None
                else None,
                "status": review.status,
                "version_no": review.version_no,
                "total_score": _float_or_none(review.total_score),
                "conclusion": review.conclusion,
                "comment": review.comment,
                "dimensions": review.dimension_json,
                "created_at": review.created_at,
                "finished_at": review.finished_at,
            }
        )
    return grouped


async def student_project_detail(session: AsyncSession, student_id: int, project_id: int) -> dict:
    """单个实训项目的全量详情（学生视角）。

    - **任务简介**取 ``training_project.description``，另附岗位与关联技能点；
    - **关卡**按 ``project_module.stage_no`` 排序，每关带模块库的名称/说明、作答要求与验收标准，
      以及 ``items_json`` 里的**子标题 + 子标题简介（prompt）**；
    - **当前状态**取该学生的实训记录与最新一轮闯关：每关是否已填写、作答内容、附件数；
    - **历史提交**按时间倒序，带提交日期与每次提交上的 **AI / 教师评语**（含各关卡维度得分与理由）。
    """
    project = await session.get(TrainingProject, project_id)
    if project is None or project.deleted_at is not None:  # type: ignore[attr-defined]
        raise NotFoundError(f"实训项目 {project_id} 不存在")

    module_stmt = (
        select(ProjectModule).where(ProjectModule.project_id == project_id).order_by(ProjectModule.stage_no)
    )
    modules = list((await session.exec(module_stmt)).all())

    template_map: dict[int, ProjectStageTemplate] = {}
    if modules:
        template_stmt = select(ProjectStageTemplate).where(
            ProjectStageTemplate.id.in_([module.template_id for module in modules])  # type: ignore[attr-defined]
        )
        template_map = {int(item.id): item for item in (await session.exec(template_stmt)).all()}

    record = (
        await session.exec(
            select(StudentProject).where(
                StudentProject.student_id == student_id,
                StudentProject.project_id == project_id,
            )
        )
    ).first()

    attempts: list[TrainingAttempt] = []
    if record is not None:
        attempt_stmt = (
            select(TrainingAttempt)
            .where(TrainingAttempt.student_project_id == record.id)  # type: ignore[arg-type]
            .order_by(TrainingAttempt.attempt_no)
        )
        attempts = list((await session.exec(attempt_stmt)).all())
    latest_attempt = attempts[-1] if attempts else None

    # 当前（最新一轮）每关的作答状态、附件数 —— 这就是"本轮已保存的作答"，进来自动带回
    stage_map, file_counts = await _attempt_stage_state(
        session, int(latest_attempt.id) if latest_attempt is not None else None
    )

    # 历史提交（跨轮次，按提交时间倒序）与每次提交的评审记录
    attempts_by_id = {int(attempt.id): attempt for attempt in attempts}
    submissions: list[ProjectSubmission] = []
    if attempts_by_id:
        submission_stmt = (
            select(ProjectSubmission)
            .where(ProjectSubmission.attempt_id.in_(list(attempts_by_id)))  # type: ignore[attr-defined]
            .order_by(ProjectSubmission.submitted_at.desc(), ProjectSubmission.id.desc())  # type: ignore[attr-defined]
        )
        submissions = list((await session.exec(submission_stmt)).all())
    reviews_by_submission = await _review_history(session, [int(item.id) for item in submissions])

    # 岗位与关联技能点
    job = await session.get(Job, project.job_id) if project.job_id is not None else None
    skills = (await _skills_of_projects(session, [project_id])).get(project_id, [])

    levels = []
    done_levels = 0
    for module in modules:
        module_id = int(module.id)
        template = template_map.get(int(module.template_id))
        stage = stage_map.get(module_id)
        is_filled = bool(stage.is_filled) if stage is not None else False
        if is_filled:
            done_levels += 1

        levels.append(
            {
                "project_module_id": module_id,
                "attempt_stage_id": int(stage.id) if stage is not None else None,
                "stage_no": module.stage_no,
                "stage_key": template.stage_key if template else None,
                "stage_name": template.stage_name if template else f"关卡 {module.stage_no}",
                "description": template.description if template else None,
                "requirement": module.requirement or (template.default_requirement if template else None),
                "accept_standard": module.accept_standard
                or (template.default_accept_standard if template else None),
                "weight": float(module.weight),
                "required": module.required,
                "sub_titles": _sub_titles(module.items_json),
                "is_filled": is_filled,
                "filled_at": stage.filled_at if stage is not None else None,
                # 本轮保存的作答（草稿或已填完都在这）：重新进来直接带回，接着往下写
                "answer_text": stage.answer_text if stage is not None else None,
                "answer_saved_at": stage.updated_at if stage is not None else None,
                "file_count": file_counts.get(int(stage.id), 0) if stage is not None else 0,
            }
        )

    labels = _level_labels()
    best_score = record.best_score if record is not None else None
    total_score = record.total_score if record is not None else None
    return {
        "student_id": student_id,
        "project_id": project_id,
        "project_name": project.project_name,
        "project_level": project.project_level,
        "level_name": labels.get(project.project_level, project.project_level),
        "difficulty": project.difficulty,
        "intro": project.description,
        "job_id": int(project.job_id) if project.job_id is not None else None,
        "job_name": job.job_name if job is not None else None,
        "status": record.status if record is not None else "NOT_STARTED",
        "best_score": _float_or_none(best_score),
        "total_score": _float_or_none(total_score),
        "progress": float(record.progress) if record is not None else 0.0,
        "level_total": len(modules),
        "level_done": done_levels,
        "attempt_count": len(attempts),
        # 前端拿 current_attempt_id 去调保存作答接口（PUT /api/attempts/{id}/answers）
        "current_attempt_id": int(latest_attempt.id) if latest_attempt is not None else None,
        "current_attempt_no": latest_attempt.attempt_no if latest_attempt is not None else None,
        "submission_count": len(submissions),
        "skill_nodes": skills,
        "levels": levels,
        "submissions": [
            {
                "submission_id": int(item.id),
                "attempt_no": attempts_by_id[int(item.attempt_id)].attempt_no,
                "submit_no": item.submit_no,
                "status": item.status,
                "final_conclusion": item.final_conclusion,
                "total_score": _float_or_none(item.total_score),
                "submitted_at": item.submitted_at,
                "reviewed_at": item.reviewed_at,
                "objection_reason": item.objection_reason,
                "is_starred": item.is_starred,
                "reviews": reviews_by_submission.get(int(item.id), []),
            }
            for item in submissions
        ],
    }


__all__ = [
    "MASTERED_PROGRESS",
    "job_project_progress",
    "recommend_jobs",
    "skill_tree_progress",
    "student_project_detail",
    "student_projects",
]
