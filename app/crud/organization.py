"""教学组织仓储：班级、分组、在班学生、学生分组。"""

from collections.abc import Sequence
from typing import Any

from sqlmodel import delete, func, or_, select

from app.crud.base import BaseRepository
from app.models.account import SysUser
from app.models.organization import (
    ClassGroup,
    ClassInfo,
    ClassStudent,
    ClassStudentGroup,
)
from app.schemas.base import Page, PageParams


class ClassRepository(BaseRepository[ClassInfo]):
    """班级仓储，软删表。"""

    model = ClassInfo
    soft_delete = True

    async def list_classes(
        self,
        params: PageParams,
        *,
        keyword: str | None = None,
        status: str | None = None,
        head_teacher_id: int | None = None,
    ) -> Page[Any]:
        filters: list[Any] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(or_(ClassInfo.class_name.like(pattern), ClassInfo.remark.like(pattern)))
        if status:
            filters.append(ClassInfo.status == status)
        if head_teacher_id is not None:
            filters.append(ClassInfo.head_teacher_id == head_teacher_id)
        return await self.list_page(params, *filters)


class ClassGroupRepository(BaseRepository[ClassGroup]):
    """班级分组仓储。"""

    model = ClassGroup

    async def list_by_class(self, class_id: int) -> list[ClassGroup]:
        stmt = select(ClassGroup).where(ClassGroup.class_id == class_id).order_by(ClassGroup.group_no)
        return list((await self.session.exec(stmt)).all())

    async def by_no(self, class_id: int, group_no: int) -> ClassGroup | None:
        return await self.get_by(class_id=class_id, group_no=group_no)

    async def next_group_no(self, class_id: int) -> int:
        stmt = select(func.max(ClassGroup.group_no)).where(ClassGroup.class_id == class_id)
        current = (await self.session.exec(stmt)).one()
        return int(current or 0) + 1

    async def group_nos(self, class_id: int) -> set[int]:
        stmt = select(ClassGroup.group_no).where(ClassGroup.class_id == class_id)
        return set((await self.session.exec(stmt)).all())


class ClassStudentRepository(BaseRepository[ClassStudent]):
    """在班学生仓储（含转出历史）。"""

    model = ClassStudent

    async def active_enrollment(self, class_id: int, student_id: int) -> ClassStudent | None:
        """该学生在班级里的在读记录（唯一约束 uk_class_student_active 保证至多一条）。"""
        return await self.get_by(class_id=class_id, student_id=student_id, status="ENROLLED")

    async def student_ids(self, class_id: int, *, status: str = "ENROLLED") -> list[int]:
        stmt = select(ClassStudent.student_id).where(
            ClassStudent.class_id == class_id, ClassStudent.status == status
        )
        return list((await self.session.exec(stmt)).all())

    async def count_in_class(self, class_id: int, *, status: str = "ENROLLED") -> int:
        stmt = (
            select(func.count())
            .select_from(ClassStudent)
            .where(ClassStudent.class_id == class_id, ClassStudent.status == status)
        )
        return int((await self.session.exec(stmt)).one())

    async def list_details(
        self,
        class_id: int,
        params: PageParams,
        *,
        status: str | None = None,
        group_id: int | None = None,
        ungrouped: bool = False,
        keyword: str | None = None,
    ) -> Page[Any]:
        """班级花名册：在班记录 join 学生账号 join 分组。

        ``group_id`` 只看某个分组；``ungrouped=True`` 只看还没分组的学生（两者互斥）。
        """
        conditions: list[Any] = [ClassStudent.class_id == class_id]
        if status:
            conditions.append(ClassStudent.status == status)
        if group_id is not None:
            conditions.append(ClassStudentGroup.group_id == group_id)
        if ungrouped:
            conditions.append(ClassStudentGroup.id.is_(None))  # type: ignore[union-attr]
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            conditions.append(or_(SysUser.user_no.like(pattern), SysUser.real_name.like(pattern)))

        stmt = (
            select(ClassStudent, SysUser, ClassStudentGroup, ClassGroup, ClassInfo)
            .join(SysUser, SysUser.id == ClassStudent.student_id)  # type: ignore[arg-type]
            .join(ClassInfo, ClassInfo.id == ClassStudent.class_id)  # type: ignore[arg-type]
            .outerjoin(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)  # type: ignore[arg-type]
            .outerjoin(ClassGroup, ClassGroup.id == ClassStudentGroup.group_id)  # type: ignore[arg-type]
            .where(*conditions)
            .order_by(ClassStudent.id)
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
        count_stmt = (
            select(func.count())
            .select_from(ClassStudent)
            .join(SysUser, SysUser.id == ClassStudent.student_id)  # type: ignore[arg-type]
            .outerjoin(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)  # type: ignore[arg-type]
            .where(*conditions)
        )

        rows = (await self.session.exec(stmt)).all()
        total = int((await self.session.exec(count_stmt)).one())
        items = [
            {
                "id": enrollment.id,
                "class_id": enrollment.class_id,
                "student_id": enrollment.student_id,
                "status": enrollment.status,
                "enrolled_at": enrollment.enrolled_at,
                "left_at": enrollment.left_at,
                "created_at": enrollment.created_at,
                "updated_at": enrollment.updated_at,
                "user_no": user.user_no,
                "real_name": user.real_name,
                "major_name": user.major_name,
                "class_name": class_info.class_name if class_info else None,
                "account_status": user.status,
                "group_id": group.id if group else None,
                "group_name": group.group_name if group else None,
            }
            for enrollment, user, _membership, group, class_info in rows
        ]
        return Page.build(items=items, total=total, params=params)


class ClassStudentGroupRepository(BaseRepository[ClassStudentGroup]):
    """学生分组归属仓储（一个在班记录对应一个分组）。"""

    model = ClassStudentGroup

    async def by_class_student(self, class_student_id: int) -> ClassStudentGroup | None:
        return await self.get_by(class_student_id=class_student_id)

    async def set_group(
        self, class_student_id: int, group_id: int, *, assigned_by: int | None = None
    ) -> ClassStudentGroup:
        """有则改、无则建。"""
        membership = await self.by_class_student(class_student_id)
        if membership is None:
            return await self.create(
                {
                    "class_student_id": class_student_id,
                    "group_id": group_id,
                    "assigned_by": assigned_by,
                }
            )
        return await self.update(membership, {"group_id": group_id, "assigned_by": assigned_by})

    async def count_in_group(self, group_id: int) -> int:
        stmt = (
            select(func.count()).select_from(ClassStudentGroup).where(ClassStudentGroup.group_id == group_id)
        )
        return int((await self.session.exec(stmt)).one())

    async def member_rows(
        self, group_id: int, *, status: str = "ENROLLED"
    ) -> list[tuple[int, int, str, str, str | None]]:
        """组内学生：(在班记录 ID, 学生 ID, 学号, 姓名, 专业)。"""
        stmt = (
            select(
                ClassStudent.id,
                SysUser.id,
                SysUser.user_no,
                SysUser.real_name,
                SysUser.major_name,
            )
            .select_from(ClassStudent)  # 显式指定主表，避免多实体 select 时 FROM 推断出重复的表
            .join(ClassStudentGroup, ClassStudentGroup.class_student_id == ClassStudent.id)  # type: ignore[arg-type]
            .join(SysUser, SysUser.id == ClassStudent.student_id)  # type: ignore[arg-type]
            .where(ClassStudentGroup.group_id == group_id, ClassStudent.status == status)
            .order_by(ClassStudent.id)
        )
        return [
            (int(row[0]), int(row[1]), row[2], row[3], row[4])
            for row in (await self.session.exec(stmt)).all()
        ]

    async def remove_member(self, class_student_id: int) -> None:
        """把学生移出分组（回到未分组）。"""
        await self.session.exec(
            delete(ClassStudentGroup).where(ClassStudentGroup.class_student_id == class_student_id)
        )
        await self.session.flush()

    async def group_counts(self, group_ids: Sequence[int]) -> dict[int, int]:
        if not group_ids:
            return {}
        stmt = (
            select(ClassStudentGroup.group_id, func.count())
            .where(ClassStudentGroup.group_id.in_(list(group_ids)))  # type: ignore[attr-defined]
            .group_by(ClassStudentGroup.group_id)
        )
        return {int(group_id): int(count) for group_id, count in (await self.session.exec(stmt)).all()}


__all__ = [
    "ClassGroupRepository",
    "ClassRepository",
    "ClassStudentGroupRepository",
    "ClassStudentRepository",
]
