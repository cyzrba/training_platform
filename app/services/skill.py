"""学生技能进度计算。

进度公式（与字段清单一致）：``progress = 完成项目数 ÷ 关联项目总数 × 100``

- **关联项目**（分母的基数）：`project_skill` 关联到该技能节点、且项目处于已发布
  （PUBLISHED）状态的项目。学生端能看到全部已发布项目，所以分母就是"平台上所有练这个
  技能点的已发布项目"，与有没有下发任务无关（口径见 docs/方案设计.md §4.2 与 §12）；
  老师发的任务只决定"哪些项目是必修"，不影响分母；
- **分子**：该学生在这个集合里已经完成（``student_project.completed_at`` 有值）的项目数；
- **状态**：0 → LOCKED；0 < progress < 100 → ACTIVATED；progress = 100 → MASTERED，
  首次激活/精通时写入 activated_at / mastered_at，进度来源记 PROJECT。

什么时候会调用（即"学生技能进度何时更新"）：
1. **教师评审定稿为通过（PASS）→ 项目完成时**：自动重算该项目关联的所有技能点（见
   ``sync_skills_after_project_completed``）；
2. **项目发布 / 下架时**：分母变了，重算已经有相关技能记录的学生的进度
   （见 ``sync_skills_after_projects_changed``）；
3. **教师手工调整**：`PATCH /api/students/{id}/skills/{skill_id}`，source 记 MANUAL，不走本模块；
4. **手动重算**：`POST /api/students/{id}/skills/recalculate`，用于补历史数据或修正。
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import now
from app.models.attempt import StudentProject
from app.models.job_skill import ProjectSkill, SkillNode, StudentSkill
from app.models.project import TrainingProject


def _ratio(completed: int, total: int) -> Decimal:
    """完成比例，保留两位小数（0~100）。"""
    if total <= 0:
        return Decimal(0)
    return (Decimal(completed) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _related_project_ids(session: AsyncSession) -> set[int]:
    """该学生的"关联项目"：全部已发布（PUBLISHED）且未删除的项目 ID。

    学生端能看到全部已发布项目、随时可以闯关，所以分母与任务无关；任务只标"必修"。
    """
    stmt = select(TrainingProject.id).where(
        TrainingProject.status == "PUBLISHED",
        TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
    )
    return {int(project_id) for project_id in (await session.exec(stmt)).all()}


async def _node_project_counts(session: AsyncSession, project_ids: set[int]) -> dict[int, int]:
    """每个技能节点关联的项目数（分母）。"""
    if not project_ids:
        return {}
    stmt = (
        select(ProjectSkill.skill_node_id, func.count(func.distinct(ProjectSkill.project_id)))
        .where(ProjectSkill.project_id.in_(list(project_ids)))  # type: ignore[attr-defined]
        .group_by(ProjectSkill.skill_node_id)
    )
    return {int(node_id): int(count) for node_id, count in (await session.exec(stmt)).all()}


async def _completed_counts(session: AsyncSession, student_id: int, project_ids: set[int]) -> dict[int, int]:
    """该学生"已完成项目"覆盖到的技能节点计数（分子）。

    判定用 ``completed_at is not None`` 而**不是** ``status == "COMPLETED"``：
    学生重新挑战一个已完成项目时，``start_attempt`` 会把 status 拉回 IN_PROGRESS，
    但那个项目只是"正在重挑、还没出结果"，不该从完成数里被扣掉——
    否则他一边重挑项目甲、一边做项目乙，乙一通过就会把甲的进度一起抹掉。
    ``completed_at`` 只在首次通过时写入、改判不通过时清空，正好是"当前是否计入完成"的标记。
    """
    if not project_ids:
        return {}
    stmt = (
        select(ProjectSkill.skill_node_id, func.count(func.distinct(ProjectSkill.project_id)))
        .join(StudentProject, StudentProject.project_id == ProjectSkill.project_id)  # type: ignore[arg-type]
        .where(
            StudentProject.student_id == student_id,
            StudentProject.completed_at.is_not(None),  # type: ignore[attr-defined]
            ProjectSkill.project_id.in_(list(project_ids)),  # type: ignore[attr-defined]
        )
        .group_by(ProjectSkill.skill_node_id)
    )
    return {int(node_id): int(count) for node_id, count in (await session.exec(stmt)).all()}


async def recalculate_student_skills(
    session: AsyncSession,
    student_id: int,
    *,
    skill_node_ids: list[int] | None = None,
) -> list[StudentSkill]:
    """重算学生技能进度；``skill_node_ids`` 为空表示全部技能点。

    只会动被重算到的技能点，手工调整过的其它技能记录不受影响。
    """
    if skill_node_ids is None:
        stmt = select(SkillNode.id).where(SkillNode.deleted_at.is_(None))  # type: ignore[attr-defined]
        node_ids = [int(node_id) for node_id in (await session.exec(stmt)).all()]
    else:
        node_ids = list(dict.fromkeys(skill_node_ids))
    if not node_ids:
        return []

    related_projects = await _related_project_ids(session)
    totals = await _node_project_counts(session, related_projects)
    completed = await _completed_counts(session, student_id, related_projects)

    stmt = select(StudentSkill).where(
        StudentSkill.student_id == student_id,
        StudentSkill.skill_node_id.in_(node_ids),  # type: ignore[attr-defined]
    )
    existing = {skill.skill_node_id: skill for skill in (await session.exec(stmt)).all()}

    results: list[StudentSkill] = []
    for node_id in node_ids:
        total = totals.get(node_id, 0)
        done = min(completed.get(node_id, 0), total)
        progress = _ratio(done, total)
        payload = {"progress": progress, "source": "PROJECT"}

        skill = existing.get(node_id)
        if skill is None:
            skill = StudentSkill(student_id=student_id, skill_node_id=node_id, **payload)
            session.add(skill)
        else:
            for field, value in payload.items():
                setattr(skill, field, value)
            skill.updated_at = now()

        # 时间戳跟着进度走：进度归零（例如教师改判、项目完成被撤销）就清掉，
        # 否则会留下"进度 0 但达标时间还在"的脏数据
        if progress <= 0:
            skill.activated_at = None
            skill.mastered_at = None
        else:
            if skill.activated_at is None:
                skill.activated_at = now()
            if progress >= 100:
                if skill.mastered_at is None:
                    skill.mastered_at = now()
            else:
                skill.mastered_at = None
        results.append(skill)

    await session.flush()
    return results


async def project_skill_node_ids(session: AsyncSession, project_id: int) -> list[int]:
    """某个项目关联的技能节点 ID。"""
    stmt = select(ProjectSkill.skill_node_id).where(ProjectSkill.project_id == project_id)
    return [int(node_id) for node_id in (await session.exec(stmt)).all()]


async def sync_skills_after_project_completed(
    session: AsyncSession, student_id: int, project_id: int
) -> list[StudentSkill]:
    """项目被判定完成后，只重算该项目关联的技能点。"""
    node_ids = await project_skill_node_ids(session, project_id)
    return await recalculate_student_skills(session, student_id, skill_node_ids=node_ids)


async def sync_skills_after_projects_changed(session: AsyncSession, project_ids: Sequence[int]) -> int:
    """项目发布 / 下架 / 换关联技能后，重算受影响技能点的进度。

    学生的"关联项目"是**全部已发布项目**，所以项目上架或下架都会改变分母：
    - 新发布：分母变大，进度可能下降（"还有项目没做完"）；
    - 下架：分母变小，进度可能回升甚至到 100%（"能做的都做完了"）。

    **只重算已经有 ``student_skill`` 记录的学生**：没有记录说明他还没碰过这些技能点，
    进度本来就是 0，没必要因为老师发布一个项目就给全校铺一遍空行。

    返回被重算的学生数。
    """
    node_ids: list[int] = []
    for project_id in dict.fromkeys(int(pid) for pid in project_ids):
        node_ids.extend(await project_skill_node_ids(session, project_id))
    node_ids = list(dict.fromkeys(node_ids))
    if not node_ids:
        return 0

    stmt = select(StudentSkill.student_id).where(
        StudentSkill.skill_node_id.in_(node_ids)  # type: ignore[attr-defined]
    )
    student_ids = {int(student_id) for student_id in (await session.exec(stmt)).all()}

    affected = 0
    for student_id in student_ids:
        affected += int(await refresh_student_skills(session, student_id, skill_node_ids=node_ids) > 0)
    return affected


async def refresh_student_skills(
    session: AsyncSession,
    student_id: int,
    *,
    skill_node_ids: Sequence[int] | None = None,
    only_existing: bool = True,
) -> int:
    """重算某个学生的技能进度，返回被重算的技能点数。

    什么时候用它：**项目的发布状态变了**（发布 / 下架），或者需要按班级等维度兜底刷新。
    分母是"全部已发布项目"，不随任务 / 班级变化，所以入班转班对进度没有影响
    （见 docs/方案设计.md §12）。

    ``only_existing=True``（默认）只重算该学生已经有记录的技能点：还没碰过相关项目的技能点
    本来就显示 0，没必要因为入班给全班铺一遍空行（记录仍由"项目完成"时创建）。
    ``skill_node_ids`` 给定时只重算这些技能点，否则该学生的全部已有记录都重算。
    """
    if skill_node_ids is None and not only_existing:
        return len(await recalculate_student_skills(session, student_id))

    stmt = select(StudentSkill.skill_node_id).where(StudentSkill.student_id == student_id)
    if skill_node_ids is not None:
        wanted = list(dict.fromkeys(int(node_id) for node_id in skill_node_ids))
        if not wanted:
            return 0
        stmt = stmt.where(StudentSkill.skill_node_id.in_(wanted))  # type: ignore[attr-defined]
    node_ids = [int(node_id) for node_id in (await session.exec(stmt)).all()]
    if not node_ids:
        return 0
    return len(await recalculate_student_skills(session, student_id, skill_node_ids=node_ids))


__all__ = [
    "project_skill_node_ids",
    "recalculate_student_skills",
    "refresh_student_skills",
    "sync_skills_after_project_completed",
    "sync_skills_after_projects_changed",
]
