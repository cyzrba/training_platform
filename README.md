# 岗位闯关式实训平台 · 后端（P0）

基于 `docs/数据库表字段清单.md`（38 张表）搭建的 FastAPI + SQLModel + SQLite 后端脚手架。

- 运行环境：Python 3.12（uv 管理）
- ORM：SQLModel（表模型与校验模型同源），异步 SQLite 驱动 aiosqlite
- 迁移：Alembic（SQLite 走 batch 模式）
- 数据校验：Pydantic v2（SQLModel 内置）

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
| 模型与迁移是否一致 | `uv run alembic check` |
| 单元测试 | `uv run pytest` |
| 代码检查 | `uv run ruff check .` |
| 代码格式化 | `uv run ruff format .` |
| ORM ↔ DDL 一致性 | `uv run python scripts/check_schema_parity.py` |
| 数据库体检 | `uv run python scripts/check_db.py` |
| 启动服务 | `uv run uvicorn app.main:app --reload` |

## 目录结构

```
app/
├─ core/          配置、数据库会话、自定义列类型、命名规范、异常
├─ models/        38 张表的 SQLModel 表模型，按业务域分文件
│  ├─ account.py        用户、角色、权限点、系统配置（6）
│  ├─ organization.py   班级、分组、在班学生、学生分组（4）
│  ├─ job_skill.py      岗位、技能树、技能节点、学生技能、成长规则（9）
│  ├─ project.py        实训项目、模块库、项目模块（3）
│  ├─ attempt.py        文件、实训记录、闯关轮次、模块作答、整单提交（6）
│  ├─ review.py         评审记录、AI 评审任务（2）
│  ├─ certificate.py    证书台账（1）
│  ├─ knowledge.py      知识文档、切片、问答会话/消息/引用（5）
│  └─ notification.py   站内通知、操作日志（2）
├─ schemas/       与 models 同名的 Create/Update/Read 模型
├─ crud/          通用仓储基类（分页、软删、部分更新）
├─ api/v1/        v1 路由：health、enums（各业务域接口在 P1 补充）
└─ db/            init_db（迁移 + 种子）、seed（幂等种子数据）
alembic/          迁移脚本
scripts/          一致性校验、数据库体检
tests/            元数据、DDL 对齐、接口冒烟、仓储落库
docs/             需求确认书、DDL、字段清单、实施方案
```

## SQLModel 使用约定

每个业务域的模型文件里成对出现两类类，Schema 直接复用前者，避免字段定义写两遍：

```python
class SysUserBase(SQLModel):        # 业务字段（非表），Schema 复用
    user_no: str = Field(max_length=50, description="学号/工号")

class SysUser(Base, TimestampMixin, SoftDeleteMixin, SysUserBase, table=True):
    __tablename__ = "sys_user"
    __table_args__ = (UniqueConstraint("user_no", name="uk_sys_user_no"), ...)
    id: int | None = Field(default=None, primary_key=True)
```

```python
class SysUserCreate(SysUserBase): ...                                # 入参
class SysUserUpdate(SQLModel): ...                                   # 全字段可选
class SysUserRead(TimestampRead, SoftDeleteRead, SysUserBase): id: int  # 出参
```

几条落地时的注意点：

- **列类型**：交给 SQLModel 按注解自动映射即可——`str→VARCHAR`、`int→INTEGER`、`Decimal→NUMERIC`、
  `bool→BOOLEAN`、`datetime→DATETIME`、`date→DATE`。SQLite 不区分这些整数/字符串的宽度，不必写
  `sa_type=Text/SmallInteger/Numeric(5,2)` 之类的精确声明。
- **唯一例外是 JSON**：`dict` / `list` 没有默认映射，必须标注 `sa_type=JSON`（如 `review_record.dimension_json`）。
- **命名约束**：复合唯一约束、CHECK、索引必须放在 `__table_args__` 里显式命名（`uk_* / chk_* / idx_*`），
  与 DDL 原文保持一致，`Field(unique=True)` 生成的名字不符合字段清单。
- **默认值**：只用 Python 侧的 `default=` / `default_factory=`，不写库级 `server_default`。
  `updated_at` 由仓储层在 `update()` 里刷新（见 `app/crud/base.py`），不依赖数据库的 ON UPDATE。
- **字段说明**：统一用 Pydantic 的 `description=`（会进 OpenAPI），不写库注释——SQLite 也不支持列注释。
- **不加 relationship**：多张表存在"两个外键指向同一张表"（如 `sys_user_role`、`skill_node_dependency`），
  显式声明关系容易踩到 SQLModel 自动生成关系的歧义问题；关联数据用显式 join / 二次查询获取，
  需要时再按 `sa_relationship_kwargs={"foreign_keys": ...}` 补声明。
- **sqlalchemy 只出现在两处**：`sqlalchemy.ext.asyncio` 的 `create_async_engine / async_sessionmaker`
  （SQLModel 未再导出）和少量 SQLAlchemy 专有事件（连接级 PRAGMA）。其余全部来自 `sqlmodel`。

## 数据库约定（SQLite 适配要点）

原 DDL 面向 PostgreSQL，落到 SQLite 时的处理集中在 `app/core/types.py` 与 `app/core/db.py`：

| 原 DDL | 本项目做法 | 原因 |
| --- | --- | --- |
| `bigint ... IDENTITY` 主键 | `int` 主键（SQLite 的 `INTEGER PRIMARY KEY`） | 只有 `INTEGER PRIMARY KEY` 才是自增的 rowid 别名 |
| `smallint` / `bigint` / `int` | 统一 `INTEGER` | SQLite 只有整数一种存储类，宽度无意义 |
| `varchar(n)` / `text` | `VARCHAR(n)` 与 `VARCHAR`（由 `max_length` 决定） | SQLite 不校验长度，`TEXT` 与 `VARCHAR` 同族 |
| `timestamptz` | `DATETIME`，全库统一存 **UTC 且不带时区标记** | SQLite 无时区类型；需要本地时间时在接口层转换 |
| `jsonb` | `JSON`（TEXT 存储） | SQLite 无 JSONB |
| `numeric(5,2)` | `NUMERIC`（SQLite 内部为浮点） | 精度由 Pydantic 约束与业务层保证 |
| `server_default now()` 等库级默认值 | 只保留 Python 侧默认值 | 减少声明；写入统一走 ORM |
| 表 / 列注释 | 字段 `description=` + 模型 docstring | SQLite 不支持注释 |
| 外键 | 每个连接执行 `PRAGMA foreign_keys=ON` | SQLite 默认不校验外键 |
| 并发 | `journal_mode=WAL`、`busy_timeout=5000` | 单写多读场景下减少锁冲突 |

状态字段统一用 `varchar` 存 code（与字段清单附录 A 一致），枚举定义在 `app/models/enums.py`，
并通过 `GET /api/v1/enums` 暴露 code + 中文文案，前端无需硬编码。

> **表达式索引**（`DESC` 排序、部分索引）SQLite 无法反射，Alembic autogenerate 会跳过或误判。
> 首版迁移里已手工补齐这 4 条：`idx_project_submission_status`、`idx_project_submission_starred`、
> `idx_review_record_submission`、`idx_qa_session_student`；`alembic/env.py` 用 `include_object`
> 把它们排除在自动比对之外。新增这类索引时，请同时改模型、手工写进迁移，并跑一次
> `scripts/check_schema_parity.py` 与 `scripts/check_db.py`。

## P0 交付范围

- 脚手架、异步数据库会话、统一异常与分页结构
- 38 张表 SQLModel 模型 + 38 套 Create/Update/Read Schema + 27 组枚举字典接口
- Alembic 首版迁移（38 表 / 33 索引 / 57 外键），`alembic check` 无差异
- 种子数据：角色、权限点与角色权限、管理员账号、成长规则、技能树四大体系、七大标准模块、系统配置
- 测试：元数据规模、ORM 与 DDL 一致性、接口冒烟、仓储落库（唯一约束、CHECK、部分唯一索引）

后续阶段：P1 基础域 CRUD 与 mock 登录、P2 岗位技能与项目接口、P3 闯关评审与证书/知识库/通知。

## 待确认

`project_stage_template` 的七大模块 `stage_key`、名称与默认权重取自《需求确认书 0706》
（需求分析、方案设计、数据处理、模型训练、模型优化、模型测试、实训报告上传，权重 10/15/15/20/15/15/10），
如与最终评审口径不同，改 `app/db/seed.py` 后重新执行建库命令即可。
