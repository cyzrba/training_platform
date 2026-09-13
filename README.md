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
- 健康检查：`GET /api/health`、`GET /api/health/db`
- 枚举字典：`GET /api/enums`、`GET /api/enums/{key}`（如 `user_type`）

## 统一响应体

所有接口（除文件下载与 `/docs`）都返回 `{code, data, msg}`：

```jsonc
// 成功：code=200，业务数据在 data
{"code": 200, "data": {"ok": true, "app": "岗位闯关式实训平台"}, "msg": "ok"}

// 失败：HTTP 200 + code=422，错误提示在 msg，行级明细放 data
{"code": 422, "data": null, "msg": "用户 99999 不存在"}
{"code": 422, "data": [{"row": 3, "user_no": "2026001", "reason": "学号重复"}], "msg": "名单校验未通过"}

// 未预期异常：HTTP 500 + code=500，msg 只给通用提示，堆栈进服务端日志
{"code": 500, "data": null, "msg": "服务器内部错误，请稍后重试"}
```

- 实现位置：`app/core/response.py` 的 `EnvelopeRoute`（业务路由统一挂载）+ `app/schemas/base.py` 的 `ApiResponse[T]`（响应模型）
- HTTP 状态：成功保留真实语义（200/201）；**业务失败统一 HTTP 200**，失败与否只看 body 的 `code`
  （想改回 HTTP 422 只改 `app/core/response.py` 的 `FAILURE_HTTP_STATUS`）；未预期异常仍是 HTTP 500
- 不包壳的响应：Excel 模板下载等文件流、`/docs`、`/redoc`、`/openapi.json`
- 前端取数：判断 `code === 200` 用 `data`，否则用 `msg` 提示
- `/docs` 里每个接口的响应模型就是 `ApiResponse[X]`，与真实返回完全一致；422/500 用 `ErrorResponse` 描述

## 常用命令

| 目的 | 命令 |
| --- | --- |
| 安装/同步依赖 | `uv sync` |
| 建库 + 种子数据 | `uv run python -m app.db.init_db` |
| 写入演示数据（教师 / 班级 / 学生，幂等） | `uv run python -m app.db.seed_demo` |
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
├─ crud/          数据访问层：base（通用仓储）+ account / organization / job_skill
├─ services/      业务编排：password（哈希与默认密码）、student_import（Excel 名单导入）
├─ api/           接口层：deps（依赖）、router（路由汇总）、endpoints（按域拆的接口）
│  └─ endpoints/  health、enums、accounts、organization、job_skill
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
- **唯一例外是 JSON**：`dict` / `list` 不在 SQLModel 的类型映射表里，必须标注 `sa_type=JSON`
  （如 `review_record.dimension_json`）。换成 `typing.Dict` / `typing.List` 没有用——SQLModel 会先把
  泛型注解还原成 `dict` / `list` 这两个内置类再做匹配，`Dict[str, Any]`、`List[str]` 同样报
  `has no matching SQLAlchemy type`。标注 `sa_type=JSON` 同时也保留了注解带来的 NOT NULL 语义。
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

原 DDL 面向 PostgreSQL，落到 SQLite 时的处理集中在 `app/core/db.py` 与各模型文件：

| 原 DDL | 本项目做法 | 原因 |
| --- | --- | --- |
| `bigint ... IDENTITY` 主键 | `int` 主键（SQLite 的 `INTEGER PRIMARY KEY`） | 只有 `INTEGER PRIMARY KEY` 才是自增的 rowid 别名 |
| `smallint` / `bigint` / `int` | 统一 `INTEGER` | SQLite 只有整数一种存储类，宽度无意义 |
| `varchar(n)` / `text` | `VARCHAR(n)` 与 `VARCHAR`（由 `max_length` 决定） | SQLite 不校验长度，`TEXT` 与 `VARCHAR` 同族 |
| `timestamptz` | `DATETIME`，统一存**本地时间且不带时区标记**（见 `app/core/time.py`） | 项目单时区部署，不做时区换算；SQLite 也无时区类型 |
| `jsonb` | `JSON`（TEXT 存储） | SQLite 无 JSONB |
| `numeric(5,2)` | `NUMERIC`（SQLite 内部为浮点） | 精度由 Pydantic 约束与业务层保证 |
| `server_default now()` 等库级默认值 | 只保留 Python 侧默认值 | 减少声明；写入统一走 ORM |
| 表 / 列注释 | 字段 `description=` + 模型 docstring | SQLite 不支持注释 |
| 外键 | 每个连接执行 `PRAGMA foreign_keys=ON` | SQLite 默认不校验外键 |
| 并发 | `journal_mode=WAL`、`busy_timeout=5000` | 单写多读场景下减少锁冲突 |

状态字段统一用 `varchar` 存 code（与字段清单附录 A 一致），枚举定义在 `app/models/enums.py`
（每个 code 都带中文注释），并通过 `GET /api/enums` 暴露 code + 中文文案，前端无需硬编码。

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

## P1 交付范围（账户权限 / 教学组织 / 岗位技能）

- **账户与权限**：`/api/users`（含角色分配、单个与批量重置密码）、`/api/roles`（含角色权限覆盖式设置）、
  `/api/permissions`、`/api/system-configs`
- **教学组织**：`/api/classes`、`/api/classes/{id}/groups`、`/api/classes/{id}/students`、
  `/api/class-students/{id}`（离班、调组）、`/api/classes/{id}/students/import`（Excel 导入）、
  `/api/classes/students/import-template`（模板下载）、`/api/classes/{id}/students/password/reset`、
  `/api/groups/{id}/students`（查看/批量加入/移出分组成员）
- **岗位与技能成长**：`/api/jobs`（含岗位技能覆盖式设置）、`/api/skill-trees`、`/api/skill-nodes`
  （含前置依赖 DAG 校验）、`/api/growth-rules`、`/api/students/{id}/jobs`、`/api/students/{id}/skills`
- **模块库（关卡模板）**：`/api/stage-templates` 增删改查 —— 模块库是关卡的唯一来源，
  教师要先在这里新增关卡，项目里才能挑到；项目模块组成表只存"被选中的模板"（`template_id` 必填、
  无 `enabled` 字段），关卡名称/编码统一读模板，不能在项目里临时自定义
- **实训项目**：`/api/projects` 增删改查（软删）、`/api/projects/{id}/modules`（从模块库挑关卡、
  调整顺序/权重/必填/要求覆盖、移除后自动补位）；项目改为 `PUBLISHED` 时校验"至少一个关卡 +
  权重合计 = 100"；`/api/projects/{id}/skills` 维护"项目要训练哪些技能"（完成项目即按
  `完成数 ÷ 关联项目总数` 推进这些技能）；模块库条目软删，被项目引用时不允许删除
- **填写引导子标题**：`project_module.items_json` 存本关卡的子标题，**由教师在项目里手填**
  （模板只定义编码/名称/默认权重/默认要求，不预设子标题 —— 不同岗位方向的实训项目子标题差别
  很大）；不同项目各填各的、互不影响，子标题只做填写引导，不参与校验与评分
- 密码：`sys_user.password_hash` 只存 PBKDF2-SHA256 哈希，新建账号与导入学生的默认密码取
  `settings.default_password`（默认 123456）；接口出参不含任何密码字段
- 教师端导学链路：建班级 → 下载模板 → 上传名单（先整批校验，学号重复即整批拒绝，**名单不含分组序号**）
  → 自动建学生账号与在班记录 → 再建分组 → 把学生加入分组（单个或批量）
- 分组视图：`GET /api/classes/{id}/groups?with_students=true` 一次拿到「分组 + 组内学生」，
  `GET /api/groups/{id}/students` 看单个分组，`GET /api/classes/{id}/students?ungrouped=true` 看未分组学生

后续阶段：证书 / 知识库 / 通知（P4）、登录鉴权（当前接口未加鉴权）。

## P3 交付范围（闯关与评审）

- **闯关过程**：`POST /api/students/{id}/projects/{id}/start`（开始/重新挑战，自动建实训记录 +
  轮次 + 各关卡作答行）、`PATCH /api/attempts/{id}/stages/{stage_id}`（保存作答并回写进度）、
  `POST /api/attempts/{id}/submit`（必填关卡填完才允许，自动排 AI 评审任务）、
  `POST /api/submissions/{id}/withdraw`（撤回）
- **学生实训记录**：`/api/student-projects` 增删改查、`/api/students/{id}/projects`、
  `/api/student-projects/{id}/attempts`
- **评审**：`/api/submissions`（教师看板：状态/标星/学生/项目筛选）、`/api/submissions/{id}`（详情
  含作答与评审）、`/api/submissions/{id}/reviews`（AI / 教师评审，`status=FINAL` 即定稿结算）、
  `/api/reviews/{id}`、`/api/submissions/{id}/ai-jobs`（AI 任务排队与状态回写）
- **判定口径**：`PASS/FAIL` 表示**这个项目是否通过**（不是"是否完成了评审"），由后端按
  `growth_rule.pass_score` 判定（阈值取项目 `project_level` 对应层级，缺配置兜底 60）：
  **`分数 >= 及格线` 即通过**。AI 和教师都只提交**分数 + 评语**，`conclusion` 由程序算；
  定稿不给分数直接 422
- **异议与复核**：`POST /api/submissions/{id}/objection`（学生提异议并可留言，**AI 判通过或不通过
  都能提**）、`POST /api/submissions/{id}/claim`（教师认领复核）；教师复核同样只给分数与评语、
  同样按及格线判定——**改判不通过会撤销项目完成并回滚技能进度**
- **附件**：`/api/file-assets`（文件台账登记）+ 关卡作答挂/摘附件
- **技能进度自动更新**：评审定稿且分数过线 → 项目完成 → 该项目关联的技能点按
  `完成项目数 ÷ 关联项目总数 × 100` 重算；另提供 `POST /api/students/{id}/skills/recalculate` 手动重算。
  技能点**只看进度（0~100），不再分三态**；`activated_at` / `mastered_at` 语义为"首次产生进度"
  与"首次达到 100%"，进度回落时清空

## 待确认

`project_stage_template` 的七大模块 `stage_key`、名称与默认权重取自《需求确认书 0706》
（需求分析、方案设计、数据处理、模型训练、模型优化、模型测试、实训报告上传，权重 10/15/15/20/15/15/10），
如与最终评审口径不同，改 `app/db/seed.py` 后重新执行建库命令即可。
