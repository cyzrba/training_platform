"""基础种子数据（幂等）。

覆盖：角色、权限点、角色权限、管理员账号、成长规则、七大标准模块库、系统配置，
以及**岗位 / 技能体系 / 技能点 / 实训项目**（数据底稿在 ``app/db/seed_growth.py``，
取自《岗位能力与技能点归纳》，见该模块的说明）。

标准模块库取值来自《需求确认书 0706》，如评审有调整，改这里重跑即可；
想知道"某个岗位/技能点/项目为什么长这样"，看 ``seed_growth.py``。

建库入口：``uv run python -m app.db.init_db``（迁移 + 本模块）；
连演示数据（教师 / 班级 / 学生 / 闯关记录）一起重建：``uv run python -m app.db.build_db``。
"""

from decimal import Decimal
from typing import Any

from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SessionLocal
from app.db.seed_growth import run_growth_seed
from app.models.account import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SystemConfig,
    SysUser,
    SysUserRole,
)
from app.models.job_skill import GrowthRule
from app.models.project import ProjectStageTemplate
from app.services.password import default_password_hash

ROLES: list[dict[str, str]] = [
    {"role_code": "STUDENT", "role_name": "学生"},
    {"role_code": "TEACHER", "role_name": "教师"},
    {"role_code": "ADMIN", "role_name": "管理员"},
]

PERMISSIONS: list[tuple[str, str]] = [
    ("USER_MANAGE", "用户管理"),
    ("ROLE_MANAGE", "角色与权限管理"),
    ("CLASS_MANAGE", "班级管理"),
    ("GROUP_MANAGE", "分组管理"),
    ("PROJECT_MANAGE", "实训项目管理"),
    ("JOB_MANAGE", "岗位管理"),
    ("SKILL_MANAGE", "技能体系管理"),
    ("GROWTH_RULE_MANAGE", "成长规则配置"),
    ("REVIEW_HANDLE", "实训结果审核"),
    ("CERT_MANAGE", "证书管理"),
    ("KNOWLEDGE_MANAGE", "知识库管理"),
    ("SYSTEM_CONFIG", "系统配置"),
]

#: 角色 -> 权限点（ADMIN 取全部）
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "STUDENT": [],
    "TEACHER": [
        "CLASS_MANAGE",
        "GROUP_MANAGE",
        "PROJECT_MANAGE",
        "JOB_MANAGE",
        "SKILL_MANAGE",
        "GROWTH_RULE_MANAGE",
        "REVIEW_HANDLE",
        "CERT_MANAGE",
        "KNOWLEDGE_MANAGE",
    ],
}

#: 七大标准实训模块（需求确认书 2.2「标准化实训模块配置」）
STAGE_TEMPLATES: list[dict[str, Any]] = [
    {
        "stage_name": "需求分析",
        "description": "明确任务目标、输入输出与约束条件",
        "default_weight": Decimal("10"),
        "sort_no": 1,
    },
    {
        "stage_name": "方案设计",
        "description": "给出技术路线、模型/算法选型与整体方案",
        "default_weight": Decimal("15"),
        "sort_no": 2,
    },
    {
        "stage_name": "数据处理",
        "description": "数据采集、清洗、标注与数据集构建",
        "default_weight": Decimal("15"),
        "sort_no": 3,
    },
    {
        "stage_name": "模型训练",
        "description": "训练环境搭建、参数配置与模型训练",
        "default_weight": Decimal("20"),
        "sort_no": 4,
    },
    {
        "stage_name": "模型优化",
        "description": "调参、蒸馏、剪枝等性能与精度优化",
        "default_weight": Decimal("15"),
        "sort_no": 5,
    },
    {
        "stage_name": "模型测试",
        "description": "指标评测、对比实验与问题分析",
        "default_weight": Decimal("15"),
        "sort_no": 6,
    },
    {
        "stage_name": "实训报告上传",
        "description": "整理过程记录与结论，上传实训报告及附件",
        "default_weight": Decimal("10"),
        "sort_no": 7,
    },
]

GROWTH_RULES: list[dict[str, Any]] = [
    {
        "level_type": "BASIC",
        "unlock_condition_json": {},
        "skill_max_level": 1,
        "pass_score": Decimal("60"),
        "level_description": "基础实训：默认开放，完成全部关卡即可提交评审",
    },
    {
        "level_type": "ADVANCED",
        "unlock_condition_json": {"completed_level": "BASIC", "min_count": 1},
        "skill_max_level": 2,
        "pass_score": Decimal("60"),
        "level_description": "进阶实训：至少完成 1 个基础项目后自动解锁",
    },
    {
        "level_type": "EXPANDED",
        "unlock_condition_json": {"completed_level": "ADVANCED", "min_count": 1},
        "skill_max_level": 3,
        "pass_score": Decimal("60"),
        "level_description": "拓展实训：至少完成 1 个进阶项目后自动解锁",
    },
]

SYSTEM_CONFIGS: list[dict[str, Any]] = [
    {
        "config_key": "cert.no_rule",
        "config_value": {"prefix": "SZPU", "pattern": "{prefix}-{job_code}-{year}-{seq:04d}"},
        "description": "证书编号规则",
    },
    {
        "config_key": "review.ai",
        "config_value": {"model": "IndustryGPT", "fallback": "DeepSeek V4 Pro", "pass_score": 60},
        "description": "AI 评审参数",
    },
    {
        "config_key": "qa.limits",
        "config_value": {"model": "Kimi 2.6", "context_rounds": 3, "retention_days": 7},
        "description": "AI 问答上下文与保留策略",
    },
    {
        "config_key": "skill.progress_rule",
        "config_value": {"formula": "completed_projects / related_projects * 100"},
        "description": "技能进度计算规则",
    },
    {
        "config_key": "ai.embedding",
        "config_value": {
            "provider": "flagembedding",
            "model": "BAAI/bge-m3",
            "model_path": "",  # 留空则优先用 <项目根>/models/bge-m3
            "device": "auto",
            "batch_size": 32,
            "max_length": 8192,
            "normalize": True,
            "dim": 1024,
            "version": "bge-m3-v1",  # 换模型必须换版本，Milvus 集合物理名带版本
        },
        "description": "向量模型（BGE-M3）参数",
    },
    {
        "config_key": "ai.reranker",
        "config_value": {
            "provider": "flagembedding",
            "model": "BAAI/bge-reranker-v2-m3",
            "model_path": "",
            "device": "auto",
            "batch_size": 16,
            "max_length": 1024,
        },
        "description": "重排模型（bge-reranker-v2-m3）参数",
    },
    {
        "config_key": "ai.llm",
        "config_value": {
            "provider": "openai-compatible",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-flash",
            "api_key": "",  # 由管理员在系统配置里填；接口读取一律掩码
            "temperature": 0.2,
            "timeout": 60,
            "max_tokens": 4096,
            # 以上是**默认模型**（DeepSeek）那套；下面按学生端选项 id 挂别家，
            # 每项只需写 base_url / model / api_key（api_key 必须各自填，不继承默认的）
            "models": {
                "kimi": {
                    "label": "Kimi",
                    "provider": "openai-compatible",
                    "base_url": "https://api.moonshot.cn/v1",
                    "model": "kimi-latest",
                    "api_key": "",
                },
                "mimo": {
                    "label": "MiMo",
                    "provider": "openai-compatible",
                    "base_url": "",
                    "model": "",
                    "api_key": "",
                },
            },
        },
        "description": "大模型配置：默认模型（DeepSeek）+ 可选模型 kimi / mimo 各自的地址与 key",
    },
    {
        "config_key": "rag.vector_store",
        "config_value": {
            "provider": "milvus",
            "uri": "http://127.0.0.1:19530",
            "collection_alias": "knowledge_chunk",
            "collection_version": "v1",
            "metric": "COSINE",
            "index": "HNSW",
            "consistency": "Bounded",
        },
        "description": "Milvus 连接与集合参数（物理名带版本，对外用别名）",
    },
    {
        "config_key": "rag.retrieval",
        "config_value": {
            "retrieve_top_k": 50,
            "rerank_top_k": 30,
            "context_top_n": 6,
            "score_threshold": 0.3,
            "rrf_k": 60,
            "criteria_max_chars": 60000,
            "criteria_top_k_per_dimension": 4,
        },
        "description": "检索与重排参数",
    },
    {
        "config_key": "ai.qa",
        "config_value": {
            "system_prompt": "",  # 留空则用 app/services/qa.py 的内置提示词
            "history_rounds": 3,  # 上下文窗口：最近 3 轮（1 轮 = 1 问 + 1 答）
            "history_max_chars": 6000,  # 历史字符双保险，超出按"轮"丢弃
            "history_retention_days": 7,  # 历史保留天数：查询层过滤 + 定时清理
            "max_question_chars": 2000,
            "temperature": 0.3,
            "timeout": 60,
            "context_provider": "none",  # 一期 none（不检索）/ 二期 knowledge
            "context_top_n": 6,
            "score_threshold": 0.3,
            "rate_limit_per_minute": 10,
        },
        "description": "AI 问答参数（上下文窗口、保留期、频控；token 只统计不限制）",
    },
]


async def _get_or_create(
    session: AsyncSession,
    model: type[SQLModel],
    defaults: dict[str, Any],
    **keys: Any,
) -> tuple[Any, bool]:
    """按 keys 查找，不存在则用 keys + defaults 创建（幂等）。"""
    statement = select(model)
    for field, value in keys.items():
        statement = statement.where(getattr(model, field) == value)
    existing = (await session.exec(statement)).first()
    if existing is not None:
        return existing, False
    obj = model(**{**keys, **defaults})
    session.add(obj)
    await session.flush()
    return obj, True


async def run_seed(session: AsyncSession | None = None) -> dict[str, int]:
    """写入种子数据，返回各类新增数量。"""
    own_session = session is None
    session = session or SessionLocal()
    stats = {
        "roles": 0,
        "permissions": 0,
        "role_permissions": 0,
        "users": 0,
        "stage_templates": 0,
        "skill_trees": 0,
        "skill_nodes": 0,
        "jobs": 0,
        "job_skills": 0,
        "projects": 0,
        "project_modules": 0,
        "project_skills": 0,
        "growth_rules": 0,
        "system_configs": 0,
    }

    try:
        role_map: dict[str, SysRole] = {}
        for item in ROLES:
            role, created = await _get_or_create(
                session, SysRole, {"role_name": item["role_name"]}, role_code=item["role_code"]
            )
            role_map[item["role_code"]] = role
            stats["roles"] += int(created)

        perm_map: dict[str, SysPermission] = {}
        for code, name in PERMISSIONS:
            perm, created = await _get_or_create(session, SysPermission, {"perm_name": name}, perm_code=code)
            perm_map[code] = perm
            stats["permissions"] += int(created)

        # ADMIN 拥有全部权限
        role_perm_map = {**ROLE_PERMISSIONS, "ADMIN": [code for code, _ in PERMISSIONS]}
        for role_code, perm_codes in role_perm_map.items():
            for code in perm_codes:
                _, created = await _get_or_create(
                    session,
                    SysRolePermission,
                    {},
                    role_id=role_map[role_code].id,
                    permission_id=perm_map[code].id,
                )
                stats["role_permissions"] += int(created)

        admin, created = await _get_or_create(
            session,
            SysUser,
            {
                "real_name": "系统管理员",
                "user_type": "ADMIN",
                "password_hash": default_password_hash(),
            },
            user_no="admin",
        )
        stats["users"] += int(created)
        _, created = await _get_or_create(
            session, SysUserRole, {}, user_id=admin.id, role_id=role_map["ADMIN"].id
        )

        for item in STAGE_TEMPLATES:
            _, created = await _get_or_create(
                session,
                ProjectStageTemplate,
                {
                    "description": item["description"],
                    "default_required": True,
                    "default_weight": item["default_weight"],
                    "sort_no": item["sort_no"],
                },
                stage_name=item["stage_name"],
            )
            stats["stage_templates"] += int(created)

        # 岗位 / 技能体系 / 技能点 / 实训项目：底稿见 app/db/seed_growth.py
        for key, value in (await run_growth_seed(session)).items():
            stats[key] = stats.get(key, 0) + value

        for item in GROWTH_RULES:
            _, created = await _get_or_create(
                session,
                GrowthRule,
                {
                    "unlock_condition_json": item["unlock_condition_json"],
                    "skill_max_level": item["skill_max_level"],
                    "pass_score": item["pass_score"],
                    "level_description": item["level_description"],
                },
                level_type=item["level_type"],
            )
            stats["growth_rules"] += int(created)

        for item in SYSTEM_CONFIGS:
            _, created = await _get_or_create(
                session,
                SystemConfig,
                {
                    "config_value": item["config_value"],
                    "description": item["description"],
                    "updated_by": admin.id,
                },
                config_key=item["config_key"],
            )
            stats["system_configs"] += int(created)

        await session.commit()
    finally:
        if own_session:
            await session.close()

    return stats


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(run_seed()))
