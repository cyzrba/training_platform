"""学生技能进度计算。

进度公式（与字段清单一致）：``progress = 完成项目数 ÷ 关联项目总数 × 100``

- **分母**：模块库里 `project_skill` 关联到该技能节点、且项目已发布（PUBLISHED）的数量；
- **分子**：该学生在这些项目里已经完成（`student_project.status = COMPLETED`）的数量；
- **状态**：0 → LOCKED；0 < progress < 100 → ACTIVATED；progress = 100 → MASTERED，
  首次激活/精通时写入 activated_at / mastered_at，进度来源记 PROJECT。

什么时候会调用（即"学生技能进度何时更新"）：
1. **教师评审定稿为通过（PASS）→ 项目完成时**：自动重算该项目关联的所有技能点（见
   ``sync_skills_after_project_completed``）；
2. **教师手工调整**：`PATCH /api/students/{id}/skills/{skill_id}`，source 记 MANUAL，不走本模块；
3. **手动重算**：`POST /api/students/{id}/skills/recalculate`，用于补历史数据或修正。
"""

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


async def _published_project_counts(session: AsyncSession) -> dict[int, int]:
    """每个技能节点关联的"已发布项目数"（分母）。"""
    stmt = (
        select(ProjectSkill.skill_node_id, func.count())
        .join(TrainingProject, TrainingProject.id == ProjectSkill.project_id)  # type: ignore[arg-type]
        .where(TrainingProject.status == "PUBLISHED", TrainingProject.deleted_at.is_(None))  # type: ignore[attr-defined]
        .group_by(ProjectSkill.skill_node_id)
    )
    return {int(node_id): int(count) for node_id, count in (await session.exec(stmt)).all()}


async def _completed_counts(session: AsyncSession, student_id: int) -> dict[int, int]:
    """该学生"已完成项目"覆盖到的技能节点计数（分子）。

    判定用 ``completed_at is not None`` 而**不是** ``status == "COMPLETED"``：
    学生重新挑战一个已完成项目时，``start_attempt`` 会把 status 拉回 IN_PROGRESS，
    但那个项目只是"正在重挑、还没出结果"，不该从完成数里被扣掉——
    否则他一边重挑项目甲、一边做项目乙，乙一通过就会把甲的进度一起抹掉。
    ``completed_at`` 只在首次通过时写入、改判不通过时清空，正好是"当前是否计入完成"的标记。
    """
    stmt = (
        select(ProjectSkill.skill_node_id, func.count())
        .join(StudentProject, StudentProject.project_id == ProjectSkill.project_id)  # type: ignore[arg-type]
        .join(TrainingProject, TrainingProject.id == ProjectSkill.project_id)  # type: ignore[arg-type]
        .where(
            StudentProject.student_id == student_id,
            StudentProject.completed_at.is_not(None),  # type: ignore[attr-defined]
            TrainingProject.status == "PUBLISHED",
            TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
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

    totals = await _published_project_counts(session)
    completed = await _completed_counts(session, student_id)

    stmt = select(StudentSkill).where(
        StudentSkill.student_id == student_id,
        StudentSkill.skill_node_id.in_(node_ids),  # type: ignore[attr-defined]
    )
    existing = {skill.skill_node_id: skill for skill in (await session.exec(stmt)).all()}

    results: list[StudentSkill] = []
    for node_id in node_ids:
        total = totals.get(node_id, 0)
        done = min(completed.get(node_id, 0), total or completed.get(node_id, 0))
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


__all__ = [
    "project_skill_node_ids",
    "recalculate_student_skills",
    "sync_skills_after_project_completed",
]
