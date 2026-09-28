"""学生技能进度计算（按项目关卡得分判定）。

进度公式：``progress = 达标关卡数 ÷ 关联关卡总数 × 100``

- **关联关卡**（分母的基数）：`project_module_skill` 关联到该技能点、且所属项目处于
  已发布（PUBLISHED）状态的关卡。跨项目汇总，与有没有下发任务无关（任务只决定"哪些
  项目是必修"）。学生端能看到全部已发布项目，所以分母就是"平台上所有练这个技能的关卡"。
- **达标**：该关卡的历史最高得分率 ≥ 所在项目层级的及格线，其中
  得分率 = 该关卡得分 ÷ 该关卡权重 × 100；及格线取 `growth_rule.pass_score`
  （按项目层级 BASIC / ADVANCED / EXPANDED 配置，默认 60）。
- **取分规则**：同一次提交里教师定稿分优先于 AI 分；同一关卡多次提交取**历史最高**的
  得分率（技能点亮是对外凭证，重考失手不应该把已经拿到的成果扣掉）。
- **只进不退**：技能一旦点亮（``mastered_at`` 有值）就保持点亮；后续老师新增项目 /
  关卡导致分母变大时，已点亮的技能不会被撤销（进度仍显示 100%）。
- **状态**：0 → LOCKED；0 < progress < 100 → ACTIVATED；progress = 100 → MASTERED。

什么时候会调用（即"学生技能进度何时更新"）：
1. **评审定稿 → 项目完成时**：`sync_skills_after_project_completed`；
2. **项目发布 / 下架 / 改关卡技能时**：分母变了，`sync_skills_after_projects_changed`；
3. **教师手工调整**：`PATCH /api/students/{id}/skills/{skill_id}`，source 记 MANUAL；
4. **手动重算**：`POST /api/students/{id}/skills/recalculate`。
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import now
from app.crud.project import StageTemplateRepository
from app.models.attempt import ProjectSubmission, StudentProject, TrainingAttempt
from app.models.job_skill import GrowthRule, SkillNode, StudentSkill
from app.models.project import ProjectModule, ProjectModuleSkill, TrainingProject
from app.models.review import ReviewRecord

#: 找不到层级对应成长规则时的兜底及格线
DEFAULT_PASS_SCORE = Decimal(60)


def _ratio(done: int, total: int) -> Decimal:
    """完成比例，保留两位小数（0~100）。"""
    if total <= 0:
        return Decimal(0)
    return (Decimal(done) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


# ------------------------------------------------------------ 关联关卡（分母）


async def _related_modules_of_nodes(
    session: AsyncSession, node_ids: Sequence[int]
) -> tuple[dict[int, list[tuple[int, int]]], dict[int, int]]:
    """返回 ``({技能点: [(关卡 id, 项目 id)]}, {关卡 id: 项目 id})``。

    只统计"已发布且未删除"的项目下的关卡，与旧口径"分母只算已发布项目"一致。
    """
    if not node_ids:
        return {}, {}
    stmt = (
        select(ProjectModuleSkill.skill_node_id, ProjectModule.id, ProjectModule.project_id)
        .join(ProjectModule, ProjectModule.id == ProjectModuleSkill.project_module_id)  # type: ignore[arg-type]
        .join(TrainingProject, TrainingProject.id == ProjectModule.project_id)  # type: ignore[arg-type]
        .where(
            ProjectModuleSkill.skill_node_id.in_(list(node_ids)),  # type: ignore[attr-defined]
            TrainingProject.status == "PUBLISHED",
            TrainingProject.deleted_at.is_(None),  # type: ignore[attr-defined]
        )
    )
    grouped: dict[int, list[tuple[int, int]]] = {}
    module_project: dict[int, int] = {}
    for node_id, module_id, project_id in (await session.exec(stmt)).all():
        grouped.setdefault(int(node_id), []).append((int(module_id), int(project_id)))
        module_project[int(module_id)] = int(project_id)
    return grouped, module_project


async def _pass_score_by_level(session: AsyncSession) -> dict[str, Decimal]:
    stmt = select(GrowthRule)
    return {
        rule.level_type: _decimal(rule.pass_score) for rule in (await session.exec(stmt)).all()
    }


# ------------------------------------------------------- 每个关卡的历史最高得分率


async def _module_best_rates(session: AsyncSession, student_id: int) -> dict[int, Decimal]:
    """``{关卡 id: 历史最高得分率%}``。

    数据链路：学生实训记录 → 闯关轮次 → 整单提交 → 评审记录（FINAL）。
    同一次提交教师定稿优先于 AI；同一关卡取历史最高。
    """
    records = list(
        (
            await session.exec(select(StudentProject).where(StudentProject.student_id == student_id))
        ).all()
    )
    if not records:
        return {}
    sp_project = {int(record.id): int(record.project_id) for record in records}

    attempts = list(
        (
            await session.exec(
                select(TrainingAttempt).where(
                    TrainingAttempt.student_project_id.in_(list(sp_project))  # type: ignore[attr-defined]
                )
            )
        ).all()
    )
    if not attempts:
        return {}
    attempt_sp = {int(item.id): int(item.student_project_id) for item in attempts}

    submissions = list(
        (
            await session.exec(
                select(ProjectSubmission).where(
                    ProjectSubmission.attempt_id.in_(list(attempt_sp)),  # type: ignore[attr-defined]
                    ProjectSubmission.status != "WITHDRAWN",
                )
            )
        ).all()
    )
    if not submissions:
        return {}
    sub_ids = [int(item.id) for item in submissions]

    reviews = list(
        (
            await session.exec(
                select(ReviewRecord).where(
                    ReviewRecord.submission_id.in_(sub_ids),  # type: ignore[attr-defined]
                    ReviewRecord.status == "FINAL",
                )
            )
        ).all()
    )
    # 同一次提交：教师定稿优先，其次 AI，再比版本号
    chosen: dict[int, tuple[tuple[int, int], ReviewRecord]] = {}
    for review in reviews:
        rank = (1 if review.review_kind == "TEACHER" else 0, int(review.version_no or 1))
        current = chosen.get(int(review.submission_id))
        if current is None or rank > current[0]:
            chosen[int(review.submission_id)] = (rank, review)
    if not chosen:
        return {}

    # 关卡名 → 关卡 id（同一项目内关卡名唯一）
    project_ids = {sp_project[attempt_sp[int(item.attempt_id)]] for item in submissions}
    modules = list(
        (
            await session.exec(
                select(ProjectModule).where(
                    ProjectModule.project_id.in_(list(project_ids))  # type: ignore[attr-defined]
                )
            )
        ).all()
    )
    template_map = {
        int(template.id): template.stage_name
        for template in await StageTemplateRepository(session).list_by_ids(
            [int(module.template_id) for module in modules], include_deleted=True
        )
    }
    name_to_module: dict[tuple[int, str], int] = {}
    modules_of_project: dict[int, list[int]] = {}
    for module in modules:
        modules_of_project.setdefault(int(module.project_id), []).append(int(module.id))
        stage_name = template_map.get(int(module.template_id))
        if stage_name:
            name_to_module[(int(module.project_id), stage_name.strip())] = int(module.id)

    rates: dict[int, Decimal] = {}
    for submission in submissions:
        entry = chosen.get(int(submission.id))
        if entry is None:
            continue
        review = entry[1]
        project_id = sp_project[attempt_sp[int(submission.attempt_id)]]
        matched = False
        for item in review.dimension_json or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            weight = _decimal(item.get("weight"))
            if not name or weight <= 0:
                continue
            module_id = name_to_module.get((project_id, name))
            if module_id is None:
                continue
            matched = True
            rate = (_decimal(item.get("score")) * 100 / weight).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if rate > rates.get(module_id, Decimal(0)):
                rates[module_id] = rate
        if not matched:
            # 兜底：评审只给了总分、没有逐关维度（简化的教师评分 / 历史数据），
            # 就按总分判定该项目下每个关卡是否达标。
            total = _decimal(review.total_score)
            if total > 0:
                for module_id in modules_of_project.get(project_id, []):
                    if total > rates.get(module_id, Decimal(0)):
                        rates[module_id] = total
    return rates


# ------------------------------------------------------------------ 主流程


async def recalculate_student_skills(
    session: AsyncSession,
    student_id: int,
    *,
    skill_node_ids: list[int] | None = None,
) -> list[StudentSkill]:
    """重算学生技能进度；``skill_node_ids`` 为空表示全部技能点。"""
    if skill_node_ids is None:
        stmt = select(SkillNode.id).where(SkillNode.deleted_at.is_(None))  # type: ignore[attr-defined]
        node_ids = [int(node_id) for node_id in (await session.exec(stmt)).all()]
    else:
        node_ids = list(dict.fromkeys(int(node_id) for node_id in skill_node_ids))
    if not node_ids:
        return []

    related, module_project = await _related_modules_of_nodes(session, node_ids)
    pass_by_level = await _pass_score_by_level(session)
    rates = await _module_best_rates(session, student_id)

    # 项目层级 → 及格线
    project_ids = set(module_project.values())
    project_level: dict[int, str] = {}
    if project_ids:
        rows = await session.exec(
            select(TrainingProject.id, TrainingProject.project_level).where(
                TrainingProject.id.in_(list(project_ids))  # type: ignore[attr-defined]
            )
        )
        project_level = {int(pid): str(level) for pid, level in rows.all()}

    existing = {
        int(skill.skill_node_id): skill
        for skill in (
            await session.exec(
                select(StudentSkill).where(
                    StudentSkill.student_id == student_id,
                    StudentSkill.skill_node_id.in_(node_ids),  # type: ignore[attr-defined]
                )
            )
        ).all()
    }

    results: list[StudentSkill] = []
    for node_id in node_ids:
        pairs = related.get(node_id, [])
        total = len(pairs)
        done = 0
        for module_id, project_id in pairs:
            threshold = pass_by_level.get(project_level.get(project_id, ""), DEFAULT_PASS_SCORE)
            rate = rates.get(module_id, Decimal(0))
            if rate > 0 and rate >= threshold:
                done += 1
        progress = _ratio(done, total)

        skill = existing.get(node_id)
        previously_mastered = skill is not None and skill.mastered_at is not None
        if previously_mastered and done > 0:
            # 只进不退：点亮即固化。新增项目 / 关卡导致分母变大时不撤销；
            # 但教师改判把所有关卡都判成不达标（done 归零）时仍允许回退。
            progress = Decimal(100)

        payload: dict[str, object] = {"progress": progress, "source": "PROJECT"}
        if skill is None:
            skill = StudentSkill(student_id=student_id, skill_node_id=node_id, **payload)  # type: ignore[arg-type]
            session.add(skill)
        else:
            for field, value in payload.items():
                setattr(skill, field, value)
            skill.updated_at = now()

        if progress <= 0:
            skill.activated_at = None
            skill.mastered_at = None
        else:
            if skill.activated_at is None:
                skill.activated_at = now()
            if progress >= 100:
                if skill.mastered_at is None:
                    skill.mastered_at = now()
            elif not previously_mastered:
                skill.mastered_at = None
        results.append(skill)

    await session.flush()
    return results


async def project_skill_node_ids(session: AsyncSession, project_id: int) -> list[int]:
    """某个项目关联的技能节点 ID（关卡绑定的并集，老数据回退到 project_skill）。"""
    stmt = (
        select(ProjectModuleSkill.skill_node_id)
        .join(ProjectModule, ProjectModule.id == ProjectModuleSkill.project_module_id)  # type: ignore[arg-type]
        .where(ProjectModule.project_id == project_id)
    )
    node_ids = {int(node_id) for node_id in (await session.exec(stmt)).all()}
    if node_ids:
        return sorted(node_ids)
    from app.models.job_skill import ProjectSkill

    fallback = select(ProjectSkill.skill_node_id).where(ProjectSkill.project_id == project_id)
    return sorted({int(node_id) for node_id in (await session.exec(fallback)).all()})


async def sync_skills_after_project_completed(
    session: AsyncSession, student_id: int, project_id: int
) -> list[StudentSkill]:
    """评审定稿后，只重算该项目关联的技能点。"""
    node_ids = await project_skill_node_ids(session, project_id)
    return await recalculate_student_skills(session, student_id, skill_node_ids=node_ids)


async def sync_skills_after_projects_changed(session: AsyncSession, project_ids: Sequence[int]) -> int:
    """项目发布 / 下架 / 关卡技能变化后，重算受影响技能点的进度。

    只重算已经有 ``student_skill`` 记录的学生：没有记录说明他还没碰过这些技能点。
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
    """重算某个学生的技能进度，返回被重算的技能点数。"""
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
    "DEFAULT_PASS_SCORE",
    "project_skill_node_ids",
    "recalculate_student_skills",
    "refresh_student_skills",
    "sync_skills_after_project_completed",
    "sync_skills_after_projects_changed",
]
