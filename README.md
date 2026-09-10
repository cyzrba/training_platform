# 岗位闯关式实训平台 · 后端（P0）

基于 `docs/数据库表字段清单.md`（38 张表）搭建的 FastAPI + SQLAlchemy 2.0 + SQLite 后端脚手架。

- 运行环境：Python 3.12（uv 管理）
- 数据库：SQLite（异步驱动 aiosqlite）
- ORM：SQLAlchemy 2.0 声明式模型，字段与 DDL 逐字段对齐
- 迁移：Alembic（SQLite 走 batch 模式）

## 快速开始

```bash
# 1. 安装依赖（首次会创建 .venv）
uv sync

# 2. 建库 + 写入种子数据（幂等，可重复执行）
uv run python -m app.db.init_db

# 3. 启动服务
uv run uvicorn app.main:app --reload
```

- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：`GET /health`、`GET /api/v1/health/db`
- 枚举字典：`GET /api/v1/enums`、`GET /api/v1/enums/{key}`（如 `user_type`）

## 常用命令

| 目的 | 命令 |
| --- | --- |
| 安装/同步依赖 | `uv sync` |
| 建库 + 种子数据 | `uv run python -m app.db.init_db` |
| 新增迁移 | `uv run alembic revision --autogenerate -m "描述"` |
| 升级到最新 | `uv run alembic upgrade head` |
| 回滚一版 | `uv run alembic downgrade -1` |
| 单元测试 | `uv run pytest` |
| 代码检查 | `uv run ruff check .` |
| 代码格式化 | `uv run ruff format .` |
| ORM ↔ DDL 一致性 | `uv run python scripts/check_schema_parity.py` |
| 数据库体检 | `uv run python scripts/check_db.py` |
| 启动服务 | `uv run uvicorn app.main:app --reload` |

## 目录结构

```
app/
├─ core/        配置、数据库会话、自定义列类型、命名规范、异常
├─ models/      38 张表 ORM，按 A/B/C/D/E/F/H/I 域拆分 + enums.py 枚举字典
├─ schemas/     Pydantic v2 模型（每表 Base/Create/Update/Read）
├─ crud/        通用仓储基类（分页、软删、部分更新）
├─ api/v1/      v1 路由：health、enums（各业务域接口在 P1 补充）
└─ db/          init_db（迁移 + 种子）、seed（幂等种子数据）
alembic/        迁移脚本
scripts/        一致性校验、数据库体检
tests/          元数据、DDL 对齐、接口冒烟、仓储落库
docs/           需求确认书、DDL、字段清单、实施方案
```

## 数据库约定（SQLite 适配要点）

原 DDL 面向 PostgreSQL，落到 SQLite 时的处理集中在 `app/core/types.py` 与 `app/core/db.py`：

| 原 DDL | 本项目做法 | 原因 |
| --- | --- | --- |
| `bigint ... IDENTITY` 主键 | `Integer PRIMARY KEY` | SQLite 只有 `INTEGER PRIMARY KEY` 才是自增的 rowid 别名 |
| `timestamptz` | `TZDateTime`：库内按 UTC naive 存储，读回带时区 | SQLite 无时区类型，避免时间语义漂移 |
| `jsonb` | SQLAlchemy `JSON`（TEXT 存储），别名 `JSONB` | SQLite 无 JSONB |
| `numeric(5,2)` | `Numeric(5,2)`（SQLite 内部为浮点） | 精度由 Pydantic 与业务层约束 |
| `COMMENT ON` | 列 `comment=` 与模型 docstring | SQLite 不支持表注释 |
| 外键 | 每个连接执行 `PRAGMA foreign_keys=ON` | SQLite 默认不校验外键 |
| 并发 | `journal_mode=WAL`、`busy_timeout=5000` | 单写多读场景下减少锁冲突 |

状态字段统一用 `varchar` 存 code（与字段清单附录 A 一致），枚举定义在 `app/models/enums.py`，
并通过 `GET /api/v1/enums` 暴露 code + 中文文案，前端无需硬编码。

> 表达式索引（`DESC` 排序、部分索引）SQLite 反射不支持，Alembic autogenerate 会跳过，
> 首个迁移里已手工补齐；改动这类索引后需要手工写进迁移，并用 `scripts/check_schema_parity.py` 复核。

## P0 交付范围

- 脚手架、异步数据库会话、统一异常与分页结构
- 38 张表 ORM + 38×4 Pydantic Schema + 枚举字典接口
- Alembic 首版迁移（38 表 / 33 索引 / 57 外键）
- 种子数据：角色、权限点与角色权限、管理员账号、成长规则、技能树四大体系、七大标准模块、系统配置
- 测试：元数据规模、ORM 与 DDL 一致性、接口冒烟、仓储落库（含唯一约束与 CHECK 约束）

后续阶段：P1 基础域 CRUD 与 mock 登录、P2 岗位技能与项目接口、P3 闯关评审与证书/知识库/通知。

## 待确认

`project_stage_template` 的七大模块 `stage_key`、名称与默认权重取自《需求确认书 0706》
（需求分析、方案设计、数据处理、模型训练、模型优化、模型测试、实训报告上传，权重 10/15/15/20/15/15/10），
如与最终评审口径不同，改 `app/db/seed.py` 后重新执行建库命令即可。
