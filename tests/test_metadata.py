"""ORM 元数据与字段清单的规模校验。"""

from app.models import Base


def test_table_count_is_39() -> None:
    """38 张业务表 + P3 新增的 project_file（项目附件）。"""
    assert len(Base.metadata.tables) == 39


def test_domain_table_names() -> None:
    expected = {
        # A 账户与权限
        "sys_user",
        "sys_role",
        "sys_permission",
        "sys_user_role",
        "sys_role_permission",
        "system_config",
        # B 教学组织
        "class_info",
        "class_group",
        "class_student",
        "class_student_group",
        # C 岗位与技能
        "job",
        "student_job",
        "skill_tree",
        "skill_node",
        "skill_node_dependency",
        "student_skill",
        "job_skill",
        "growth_rule",
        "project_skill",
        # D 实训项目
        "training_project",
        "project_stage_template",
        "project_module",
        "project_file",
        # E 闯关与评审
        "file_asset",
        "student_project",
        "training_attempt",
        "attempt_stage",
        "attempt_stage_file",
        "project_submission",
        "review_record",
        "review_ai_job",
        # F 证书
        "student_certificate",
        # H 知识库
        "knowledge_doc",
        "knowledge_chunk",
        "ai_qa_session",
        "ai_qa_message",
        "ai_qa_citation",
        # I 通知与审计
        "notification",
        "operation_log",
    }
    assert set(Base.metadata.tables) == expected


def test_index_and_foreign_key_count() -> None:
    indexes = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    assert len(indexes) == 34

    foreign_keys = sum(len(table.foreign_keys) for table in Base.metadata.tables.values())
    assert foreign_keys == 60


def test_soft_delete_tables() -> None:
    """带 deleted_at 的主数据表清单（字段清单约定）。"""
    soft_delete_tables = {
        "sys_user",
        "class_info",
        "job",
        "skill_tree",
        "skill_node",
        "project_stage_template",
        "training_project",
        "knowledge_doc",
    }
    for name in soft_delete_tables:
        assert "deleted_at" in Base.metadata.tables[name].columns

    for table in Base.metadata.tables.values():
        assert ("deleted_at" in table.columns) == (table.name in soft_delete_tables)
