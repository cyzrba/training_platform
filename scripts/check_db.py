"""运行期体检：确认 SQLite 已建表、外键约束已生效、种子数据就位。

用法：uv run python scripts/check_db.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import engine  # noqa: E402

EXPECTED_TABLES = 44  # 40 张业务表 + J 域任务下发 4 张（publish_task*）
EXPECTED_INDEXES = 41
EXPECTED_FOREIGN_KEYS = 68


def sqlite_stats(db_file: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_file)
    try:
        query = lambda sql, *args: conn.execute(sql, args).fetchone()[0]  # noqa: E731
        tables = [
            row[0]
            for row in conn.execute(
                # alembic_version 是迁移版本表，不计入业务表数量
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            )
        ]
        fk_count = sum(len(conn.execute(f"PRAGMA foreign_key_list('{name}')").fetchall()) for name in tables)
        return {
            "tables": len(tables),
            "indexes": query(
                "SELECT count(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ),
            "foreign_keys": fk_count,
            "roles": query("SELECT count(*) FROM sys_role"),
            "permissions": query("SELECT count(*) FROM sys_permission"),
            "role_permissions": query("SELECT count(*) FROM sys_role_permission"),
            "users": query("SELECT count(*) FROM sys_user"),
            "stage_templates": query("SELECT count(*) FROM project_stage_template"),
            "skill_trees": query("SELECT count(*) FROM skill_tree"),
            "growth_rules": query("SELECT count(*) FROM growth_rule"),
            "system_configs": query("SELECT count(*) FROM system_config"),
        }
    finally:
        conn.close()


async def pragma_check() -> dict[str, int]:
    async with engine.connect() as conn:
        foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
        journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
    return {"foreign_keys": int(foreign_keys), "journal_mode": str(journal_mode)}


def main() -> int:
    db_file = settings.sqlite_file
    if not db_file.exists():
        print(f"数据库文件不存在：{db_file}\n请先执行：uv run python -m app.db.init_db")
        return 1

    stats = sqlite_stats(db_file)
    pragma = asyncio.run(pragma_check())

    print(f"数据库文件：{db_file}")
    for key, value in stats.items():
        print(f"  {key:16} = {value}")
    print(f"  foreign_keys pragma = {pragma['foreign_keys']}（1 表示外键约束已开启）")
    print(f"  journal_mode        = {pragma['journal_mode']}")

    problems = []
    if stats["tables"] != EXPECTED_TABLES:
        problems.append(f"表数量应为 {EXPECTED_TABLES}，实际 {stats['tables']}")
    if stats["indexes"] != EXPECTED_INDEXES:
        problems.append(f"索引数量应为 {EXPECTED_INDEXES}，实际 {stats['indexes']}")
    if stats["foreign_keys"] != EXPECTED_FOREIGN_KEYS:
        problems.append(f"外键数量应为 {EXPECTED_FOREIGN_KEYS}，实际 {stats['foreign_keys']}")
    if pragma["foreign_keys"] != 1:
        problems.append("外键约束未开启")

    if problems:
        print("\n发现问题：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print("\n体检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
