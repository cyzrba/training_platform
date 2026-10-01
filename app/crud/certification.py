"""证书规则 / 证书技能 / 证书申请的仓储与查询。"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlmodel import delete, func, or_, select

from app.crud.base import BaseRepository
from app.models.account import SysUser
from app.models.certificate import StudentCertificate
from app.models.certification import (
    APPLICATION_STATUS_PENDING_INTERVIEW,
    APPLICATION_STATUS_UNDER_REVIEW,
    CertificateApplication,
    CertificateRule,
    CertificateSkill,
)
from app.models.job_skill import Job, SkillNode, SkillTree, StudentSkill
from app.schemas.base import Page, PageParams


class CertificateRuleRepository(BaseRepository[CertificateRule]):
    """证书规则仓储。"""

    model = CertificateRule

    async def by_job(self, job_id: int) -> CertificateRule | None:
        return await self.get_by(job_id=job_id)

    async def list_rules(
        self, params: PageParams, *, keyword: str | None = None, status: str | None = None
    ) -> Page[dict]:
        stmt = (
            select(CertificateRule, Job)
            .join(Job, Job.id == CertificateRule.job_id)  # type: ignore[arg-type]
        )
        count_stmt = (
            select(func.count())
            .select_from(CertificateRule)
            .join(Job, Job.id == CertificateRule.job_id)  # type: ignore[arg-type]
        )

        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(
                or_(CertificateRule.cert_name.like(pattern), Job.job_name.like(pattern))
            )
        if status:
            filters.append(CertificateRule.status == status)
        for condition in filters:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        total = int((await self.session.exec(count_stmt)).one())
        rows = list(
            (
                await self.session.exec(
                    stmt.order_by(CertificateRule.id.desc())
                    .offset((params.page - 1) * params.page_size)
                    .limit(params.page_size)
                )
            ).all()
        )

        rule_ids = [rule.id for rule, _ in rows]
        skill_ids_map = await self._skill_node_ids_of_many(rule_ids)
        skill_count_map = await self._skill_count_of_many(rule_ids)

        items = [
            {
                **rule.model_dump(),
                "job_name": job.job_name,
                "skill_count": skill_count_map.get(rule.id, 0),
                "skill_node_ids": skill_ids_map.get(rule.id, []),
            }
            for rule, job in rows
        ]
        return Page.build(items=items, total=total, params=params)

    async def _skill_node_ids_of_many(self, rule_ids: Sequence[int]) -> dict[int, list[int]]:
        if not rule_ids:
            return {}
        stmt = select(CertificateSkill.certificate_rule_id, CertificateSkill.skill_node_id).where(
            CertificateSkill.certificate_rule_id.in_(list(rule_ids))  # type: ignore[arg-type]
        )
        grouped: dict[int, list[int]] = {}
        for rule_id, node_id in (await self.session.exec(stmt)).all():
            grouped.setdefault(int(rule_id), []).append(int(node_id))
        return grouped

    async def _skill_count_of_many(self, rule_ids: Sequence[int]) -> dict[int, int]:
        if not rule_ids:
            return {}
        stmt = (
            select(CertificateSkill.certificate_rule_id, func.count())
            .where(CertificateSkill.certificate_rule_id.in_(list(rule_ids)))  # type: ignore[arg-type]
            .group_by(CertificateSkill.certificate_rule_id)
        )
        return {int(rule_id): int(count) for rule_id, count in (await self.session.exec(stmt)).all()}

    async def skill_node_ids(self, rule_id: int) -> list[int]:
        stmt = select(CertificateSkill.skill_node_id).where(
            CertificateSkill.certificate_rule_id == rule_id
        )
        return [int(node_id) for node_id in (await self.session.exec(stmt)).all()]

    async def replace_skills(self, rule_id: int, skill_node_ids: Sequence[int]) -> int:
        await self.session.exec(
            delete(CertificateSkill).where(CertificateSkill.certificate_rule_id == rule_id)
        )
        for skill_node_id in skill_node_ids:
            self.session.add(
                CertificateSkill(certificate_rule_id=rule_id, skill_node_id=skill_node_id)
            )
        await self.session.flush()
        return len(skill_node_ids)

    async def delete_skills(self, rule_id: int) -> None:
        await self.session.exec(
            delete(CertificateSkill).where(CertificateSkill.certificate_rule_id == rule_id)
        )
        await self.session.flush()


class CertificateSkillRepository:
    """证书所需技能点明细查询。"""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def list_skill_nodes(self, rule_id: int) -> list[dict]:
        stmt = (
            select(SkillNode, SkillTree)
            .join(CertificateSkill, CertificateSkill.skill_node_id == SkillNode.id)  # type: ignore[arg-type]
            .join(SkillTree, SkillTree.id == SkillNode.tree_id)  # type: ignore[arg-type]
            .where(
                CertificateSkill.certificate_rule_id == rule_id,
                SkillNode.deleted_at.is_(None),  # type: ignore[attr-defined]
            )
            .order_by(SkillNode.tree_id, SkillNode.id)
        )
        return [
            {
                "skill_node_id": node.id,
                "node_name": node.node_name,
                "description": node.description,
                "tree_name": tree.tree_name,
            }
            for node, tree in (await self.session.exec(stmt)).all()
        ]


class CertificateApplicationRepository(BaseRepository[CertificateApplication]):
    """证书申请仓储与查询。"""

    model = CertificateApplication

    async def list_applications(
        self, params: PageParams, *, status: str | None = None, keyword: str | None = None
    ) -> Page[dict]:
        stmt = (
            select(CertificateApplication, SysUser, Job, CertificateRule)
            .join(SysUser, SysUser.id == CertificateApplication.student_id)  # type: ignore[arg-type]
            .join(CertificateRule, CertificateRule.id == CertificateApplication.certificate_rule_id)  # type: ignore[arg-type]
            .join(Job, Job.id == CertificateRule.job_id)  # type: ignore[arg-type]
        )
        count_stmt = select(func.count()).select_from(CertificateApplication)

        filters: list[Any] = []
        if status:
            filters.append(CertificateApplication.status == status)
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    SysUser.real_name.like(pattern),
                    SysUser.user_no.like(pattern),
                    CertificateRule.cert_name.like(pattern),
                )
            )
        for condition in filters:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        total = int((await self.session.exec(count_stmt)).one())
        rows = list(
            (
                await self.session.exec(
                    stmt.order_by(CertificateApplication.id.desc())
                    .offset((params.page - 1) * params.page_size)
                    .limit(params.page_size)
                )
            ).all()
        )
        items = [
            {
                **application.model_dump(),
                "student_name": student.real_name,
                "student_no": student.user_no,
                "job_name": job.job_name,
                "cert_name": rule.cert_name,
            }
            for application, student, job, rule in rows
        ]
        return Page.build(items=items, total=total, params=params)

    async def latest_for(self, student_id: int, rule_id: int) -> CertificateApplication | None:
        stmt = (
            select(CertificateApplication)
            .where(
                CertificateApplication.student_id == student_id,
                CertificateApplication.certificate_rule_id == rule_id,
            )
            .order_by(CertificateApplication.id.desc())
            .limit(1)
        )
        return (await self.session.exec(stmt)).first()

    async def has_active(self, student_id: int, rule_id: int) -> bool:
        stmt = select(func.count()).select_from(CertificateApplication).where(
            CertificateApplication.student_id == student_id,
            CertificateApplication.certificate_rule_id == rule_id,
            CertificateApplication.status.in_(
                [APPLICATION_STATUS_PENDING_INTERVIEW, APPLICATION_STATUS_UNDER_REVIEW]
            ),
        )
        return int((await self.session.exec(stmt)).one()) > 0

    async def has_approved(self, student_id: int, rule_id: int) -> bool:
        stmt = select(func.count()).select_from(CertificateApplication).where(
            CertificateApplication.student_id == student_id,
            CertificateApplication.certificate_rule_id == rule_id,
            CertificateApplication.status == "APPROVED",
        )
        return int((await self.session.exec(stmt)).one()) > 0


class CertificationOverviewRepository:
    """学生证书点亮与申请概览。"""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def skill_progress_map(self, student_id: int) -> dict[int, Decimal]:
        stmt = select(StudentSkill.skill_node_id, StudentSkill.progress).where(
            StudentSkill.student_id == student_id
        )
        return {
            int(node_id): Decimal(progress)
            for node_id, progress in (await self.session.exec(stmt)).all()
        }

    async def issued_certificates(self, student_id: int) -> list[dict]:
        stmt = (
            select(StudentCertificate, Job)
            .join(Job, Job.id == StudentCertificate.job_id)  # type: ignore[arg-type]
            .where(StudentCertificate.student_id == student_id)
            .order_by(StudentCertificate.id.desc())
        )
        return [
            {
                "id": cert.id,
                "job_id": cert.job_id,
                "job_name": job.job_name,
                "cert_name": cert.cert_name,
                "issue_org_name": cert.issue_org_name,
                "certificate_no": cert.certificate_no,
                "issue_date": cert.issue_date,
                "status": cert.status,
            }
            for cert, job in (await self.session.exec(stmt)).all()
        ]


__all__ = [
    "CertificateApplicationRepository",
    "CertificateRuleRepository",
    "CertificateSkillRepository",
    "CertificationOverviewRepository",
]
