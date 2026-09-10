"""ORM 与 DDL 原文一致性测试。"""

from app.models import Base
from scripts.check_schema_parity import compare, parse_ddl


def test_orm_matches_ddl() -> None:
    issues = compare(Base.metadata, parse_ddl())
    assert issues == [], "\n".join(issues)
