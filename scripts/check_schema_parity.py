"""比对 ORM 模型与 PostgreSQL DDL（docs/database_schema_draft.sql）的一致性。

用法：uv run python scripts/check_schema_parity.py

由于 SQLite 无法反射表达式索引，Alembic autogenerate 会跳过 DESC / 部分索引，
本脚本以 DDL 原文为准做全量比对：表、字段、可空性、索引名。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import MetaData  # noqa: E402

from app.models import Base  # noqa: E402

DDL_PATH = Path(__file__).resolve().parents[1] / "docs" / "database_schema_draft.sql"

_TABLE_RE = re.compile(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", re.S)
_INDEX_RE = re.compile(r"CREATE (?:UNIQUE )?INDEX (\w+)\s+ON\s+(\w+)", re.M)
_CONSTRAINT_START = re.compile(r"^\s*(CONSTRAINT|PRIMARY KEY|UNIQUE|CHECK|FOREIGN KEY)\b")
_NAMED_CONSTRAINT_RE = re.compile(r"^CONSTRAINT\s+(\w+)\s+(UNIQUE|CHECK|FOREIGN\s+KEY|PRIMARY\s+KEY)\b", re.I)
_TYPE_WORDS = {
    "bigint",
    "smallint",
    "int",
    "integer",
    "boolean",
    "varchar",
    "text",
    "timestamptz",
    "date",
    "numeric",
    "jsonb",
}


@dataclass
class DdlTable:
    name: str
    columns: dict[str, bool] = field(default_factory=dict)  # 列名 -> 是否可空
    constraints: dict[str, str] = field(default_factory=dict)  # 约束名 -> 类型


@dataclass
class DdlSchema:
    tables: dict[str, DdlTable] = field(default_factory=dict)
    indexes: dict[str, str] = field(default_factory=dict)  # 索引名 -> 表名


def parse_ddl(path: Path = DDL_PATH) -> DdlSchema:
    sql = path.read_text(encoding="utf-8")
    schema = DdlSchema()

    for table_name, body in _TABLE_RE.findall(sql):
        table = DdlTable(name=table_name)
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            # 跳过空行与 SQL 注释
            if not line or line.startswith("--"):
                continue
            named = _NAMED_CONSTRAINT_RE.match(line)
            if named:
                table.constraints[named.group(1)] = named.group(2)
                continue
            if _CONSTRAINT_START.match(line):
                continue
            column = line.split()[0]
            # PRIMARY KEY 隐含 NOT NULL
            is_primary = "PRIMARY KEY" in line.upper()
            table.columns[column] = (not is_primary) and ("NOT NULL" not in line.upper())
        schema.tables[table_name] = table

    for index_name, table_name in _INDEX_RE.findall(sql):
        schema.indexes[index_name] = table_name

    return schema


def compare(metadata: MetaData, ddl: DdlSchema) -> list[str]:
    issues: list[str] = []

    orm_tables = set(metadata.tables)
    ddl_tables = set(ddl.tables)
    for name in sorted(ddl_tables - orm_tables):
        issues.append(f"缺失表：{name}")
    for name in sorted(orm_tables - ddl_tables):
        issues.append(f"多余表：{name}")

    for name in sorted(orm_tables & ddl_tables):
        table = metadata.tables[name]
        orm_columns = {column.name: column.nullable for column in table.columns}
        ddl_columns = ddl.tables[name].columns

        for column in sorted(set(ddl_columns) - set(orm_columns)):
            issues.append(f"{name}: 缺失字段 {column}")
        for column in sorted(set(orm_columns) - set(ddl_columns)):
            issues.append(f"{name}: 多余字段 {column}")

        for column in sorted(set(ddl_columns) & set(orm_columns)):
            orm_nullable = orm_columns[column]
            ddl_nullable = ddl_columns[column]
            # 主键在 ORM 中 nullable=False，DDL 里写作 NOT NULL，二者一致
            if orm_nullable != ddl_nullable:
                issues.append(
                    f"{name}.{column}: 可空性不一致 ORM={'NULL' if orm_nullable else 'NOT NULL'} "
                    f"DDL={'NULL' if ddl_nullable else 'NOT NULL'}"
                )

    orm_indexes = {index.name for table in metadata.tables.values() for index in table.indexes}
    for name in sorted(set(ddl.indexes) - orm_indexes):
        issues.append(f"缺失索引：{name}")
    for name in sorted(orm_indexes - set(ddl.indexes)):
        issues.append(f"多余索引：{name}")

    # 具名表级约束（uk_* / chk_*）比对
    orm_constraints = {
        constraint.name: type(constraint).__name__
        for table in metadata.tables.values()
        for constraint in table.constraints
        if constraint.name and type(constraint).__name__ in {"UniqueConstraint", "CheckConstraint"}
    }
    ddl_constraints = {
        name: kind for table in ddl.tables.values() for name, kind in table.constraints.items()
    }
    for name in sorted(set(ddl_constraints) - set(orm_constraints)):
        issues.append(f"缺失约束：{name}（DDL {ddl_constraints[name]}）")
    for name in sorted(set(orm_constraints) - set(ddl_constraints)):
        # class_student_group.class_student_id 在 DDL 中是列级 UNIQUE，无名字
        if name.startswith("uk_class_student_group"):
            continue
        issues.append(f"多余约束：{name}（ORM {orm_constraints[name]}）")

    return issues


def main() -> int:
    ddl = parse_ddl()
    issues = compare(Base.metadata, ddl)

    print(f"DDL 表数：{len(ddl.tables)}，ORM 表数：{len(Base.metadata.tables)}")
    print(f"DDL 索引数：{len(ddl.indexes)}")
    fk_count = sum(len(table.foreign_keys) for table in Base.metadata.tables.values())
    print(f"ORM 外键数：{fk_count}")

    if issues:
        print(f"\n发现 {len(issues)} 处差异：")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    print("\nORM 模型与 DDL 完全一致（表 / 字段 / 可空性 / 索引）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
