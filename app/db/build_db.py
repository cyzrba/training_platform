"""一键重建数据库：删库 → 迁移 → 基础种子 → 演示数据。

用法::

    uv run python -m app.db.build_db                  # 全量重建（含教师 / 班级 / 学生 / 闯关记录）
    uv run python -m app.db.build_db --without-demo   # 只建到岗位 / 技能 / 项目，不铺演示班级与学生
    uv run python -m app.db.build_db --keep-db        # 不删库，只把种子补齐（幂等）

**默认会删掉数据库文件**（SQLite 的 app.db 与 -wal / -shm），库里的账号、班级、闯关与评审
记录都会重来 —— 这正是"一运行就得到一个完整、干净、数据齐全的库"的意思。
只想补数据、不想清库时加 ``--keep-db``（等价于 ``app.db.init_db`` + ``app.db.seed_demo``）。

数据来源：

- 角色 / 权限 / 管理员 / 标准模块库 / 成长规则 / 系统配置 → ``app.db.seed``
- 岗位 / 技能体系 / 技能点 / 实训项目（含岗位-技能与项目-技能） → ``app.db.seed_growth``
  （底稿取自《岗位能力与技能点归纳》，见该模块说明）
- 教师 / 班级 / 学生 / 选岗 / 项目附件 / 任务下发 / 闯关与评审 → ``app.db.seed_demo``
"""

import argparse
import asyncio
from pathlib import Path
from typing import Any

from sqlmodel import func, select

from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.db.init_db import run_migrations
from app.models.attempt import StudentProject
from app.models.job_skill import Job, JobSkill, SkillNode, SkillTree
from app.models.project import TrainingProject


def _reset_sqlite_file() -> None:
    """删掉 SQLite 数据库文件（连同 WAL / SHM），非 SQLite 直接报错让人工处理。"""
    if not settings.resolved_database_url.startswith("sqlite"):
        raise SystemExit(
            "当前 DATABASE_URL 不是 SQLite，脚本不会替你删库："
            f"{settings.resolved_database_url}\n"
            "请手工清库后再执行 `uv run python -m app.db.init_db` + `app.db.seed_demo`。"
        )
    removed: list[str] = []
    base = settings.sqlite_file
    for path in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    print(f"已删除数据库文件：{'、'.join(removed) if removed else '（无）'}")


async def _seed(*, with_demo: bool) -> dict[str, int]:
    """写全部种子数据；结束前释放连接池，让 SQLite 把 -wal 落盘。"""
    from app.db.seed import run_seed

    stats: dict[str, int] = {}
    try:
        for key, value in (await run_seed()).items():
            stats[key] = stats.get(key, 0) + value
        if with_demo:
            from app.db.seed_demo import run_demo_seed

            for key, value in (await run_demo_seed()).items():
                stats[key] = stats.get(key, 0) + value
    finally:
        await engine.dispose()
    return stats


async def _summary() -> None:
    """打印库里的规模，顺便证明每个岗位都有已发布项目可练。"""
    try:
        async with SessionLocal() as session:

            async def count(model: type, *conditions: Any) -> int:
                stmt = select(func.count()).select_from(model)
                for condition in conditions:
                    stmt = stmt.where(condition)
                return int((await session.exec(stmt)).one())

            jobs = list((await session.exec(select(Job).order_by(Job.id))).all())
            project_rows = (
                await session.exec(
                    select(TrainingProject.job_id, func.count())
                    .where(TrainingProject.status == "PUBLISHED")
                    .group_by(TrainingProject.job_id)
                )
            ).all()
            published_by_job = {int(job_id): int(total) for job_id, total in project_rows if job_id}
            job_skill_rows = (
                await session.exec(select(JobSkill.job_id, func.count()).group_by(JobSkill.job_id))
            ).all()
            skills_by_job = {int(job_id): int(total) for job_id, total in job_skill_rows}

            print(
                "库内规模："
                f"技能体系 {await count(SkillTree)} 个、"
                f"技能点 {await count(SkillNode)} 个、"
                f"岗位 {len(jobs)} 个、"
                f"实训项目 {await count(TrainingProject)} 个"
                f"（已发布 {await count(TrainingProject, TrainingProject.status == 'PUBLISHED')} 个）、"
                f"学生实训记录 {await count(StudentProject)} 条"
            )
            print("岗位覆盖（技能点数 / 已发布项目数）：")
            for job in jobs:
                job_id = int(job.id)
                print(
                    f"  · {job.job_name}（{job.recommended_level}）"
                    f"技能 {skills_by_job.get(job_id, 0)} 个 / 项目 {published_by_job.get(job_id, 0)} 个"
                )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="一键重建数据库：删库 → 迁移 → 基础种子 → 演示数据")
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="不删库，只把种子数据补齐（幂等）",
    )
    parser.add_argument(
        "--without-demo",
        action="store_true",
        help="不写演示数据（教师 / 班级 / 学生 / 闯关记录）",
    )
    args = parser.parse_args()

    settings.sqlite_file.parent.mkdir(parents=True, exist_ok=True)
    if not args.keep_db:
        _reset_sqlite_file()

    run_migrations()
    stats = asyncio.run(_seed(with_demo=not args.without_demo))

    print(f"数据库就绪：{settings.sqlite_file}")
    for key, value in stats.items():
        if value:
            print(f"  新增 {key:22} {value}")
    asyncio.run(_summary())


if __name__ == "__main__":
    main()
