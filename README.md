# 岗位闯关式实训平台（后端）

面向单学院的实训教学平台后端：岗位与技能树 → 实训项目（关卡）→ 学生闯关与提交 →
AI / 教师评审 → 技能进度与证书，另配套一套 RAG 知识库（岗位资料问答、评分标准检索批改）。

本文只讲**怎么跑起来**和**用到哪些组件**，设计细节见 `docs/`。

## 用到的组件

| 组件 | 版本 | 干什么用 | 不装会怎样 |
| --- | --- | --- | --- |
| Python + uv | 3.12 / 0.10+ | 运行时与依赖管理 | 跑不起来 |
| FastAPI + Uvicorn | 0.141 / 0.52 | HTTP 接口与统一响应体 | 跑不起来 |
| SQLModel + SQLAlchemy + Alembic | 0.0.42 / 2.0 / 1.19 | ORM、迁移 | 跑不起来 |
| SQLite | 3.x | 业务数据真源（`data/app.db`） | 跑不起来 |
| MinIO | `RELEASE.2025-09-07T16-13-09Z` | S3 协议对象存储，存所有上传文件本体 | 上传/下载全废（存储层只有 S3 一种实现） |
| Milvus standalone | `v3.0.1`（配 etcd v3.5.25 + 内部 MinIO） | 向量库，RAG 检索的派生索引 | 知识切片入库与召回不可用 |
| BGE-M3 + bge-reranker-v2-m3 | BAAI | 向量化（稠密 + 稀疏）与重排 | 向量化与召回不可用 |
| PyTorch | 2.14.0+cu130（CPU 版也能跑，慢） | 模型推理运行时 | 同上 |
| LangChain + DeepSeek | langchain-openai + `deepseek-v4-flash` | AI 评审打分与 **AI 问答**（都走 `system_config` 里的 `ai.llm`） | 这两个接口报"还没配置大模型 api key"或"缺少大模型调用依赖" |

精确版本以 `pyproject.toml` 与 `uv.lock` 为准；容器镜像版本固定在 `deploy/docker-compose.rag.yml`。

## 跑起来

### 一步启动（推荐）

```bash
scripts/start_all.sh                 # 中间件 → 建库 + 种子 → 启动后端（Ctrl-C 停后端）
scripts/start_all.sh --with-demo     # 再灌一套演示数据（教师 / 班级 / 岗位 / 项目 / 闯关记录）
scripts/start_all.sh --no-rag        # 只起 MinIO（不碰知识库检索也能开发）
scripts/start_all.sh status          # 看各组件状态
scripts/start_all.sh stop            # 停后端 + 停中间件
```

脚本做的事：起 MinIO + Milvus（等端口就绪）→ 检查模型权重（只提示不自动下）→
`app.db.init_db` 建库与种子 → 启动 uvicorn 并等 `/api/health` 通过 → 打印访问地址。
**幂等**：随时再跑一次不会坏数据；**Ctrl-C 只停后端**，中间件留着（容器重启要几十秒）。
日志在 `data/run/backend.log`。

### 手动分步（想自己控制每一步时）

```bash
# 1. 依赖
uv sync                 # 基础环境（不含 torch）
uv sync --extra llm     # 只跑 AI 问答 / AI 评审（装 langchain-openai，不背 torch）
uv sync --extra rag     # 含 FlagEmbedding / pymilvus / LangChain / torch（做知识库才需要）

# 2. 中间件：MinIO + Milvus（etcd、Milvus 内部 MinIO 一起起来）
docker compose -f deploy/docker-compose.rag.yml up -d
#   MinIO 控制台 http://127.0.0.1:9001 （minioadmin / minioadmin123）
#   MinIO S3 API 9000，Milvus 19530 / 健康检查 9091
#   持久化数据在 deploy/data/（已 gitignore）

# 3. 模型权重（首次，约 4.3GB，走 hf-mirror 落到 models/）
uv run python scripts/download_rag_models.py

# 4. 配置（默认值就是本机 MinIO，可直接用）
cp .env.example .env            # Windows: Copy-Item .env.example .env

# 4.1 配大模型与 api key（存 system_config 表，改完即生效，不用重启）
uv run python scripts/set_llm_config.py --show
LLM_API_KEY=sk-xxx uv run python scripts/set_llm_config.py \
    --model deepseek-v4-flash --base-url https://api.deepseek.com/v1 --api-key-env LLM_API_KEY
# 再挂别家（AI 助教里可选）：只需写这一家的地址 / 模型名 / key
uv run python scripts/set_llm_config.py --model-key kimi --model kimi-latest \
    --base-url https://api.moonshot.cn/v1
LLM_API_KEY=sk-xxx uv run python scripts/set_llm_config.py --model-key kimi --api-key-env LLM_API_KEY

# 5. 建库 + 种子数据 + 启动
uv run python -m app.db.init_db          # 迁移到最新 + 写种子（幂等，可反复执行）
uv run uvicorn app.main:app --reload     # 接口文档 http://127.0.0.1:8000/docs
```

演示数据（教师、班级、学生、选岗、闯关与评审记录）：`uv run python -m app.db.seed_demo`。

**一键重建整库**（删库 → 迁移 → 全部种子 → 演示数据）：`uv run python -m app.db.build_db`；
`--without-demo` 只建到岗位 / 技能 / 项目，`--keep-db` 不删库、只把种子补齐（幂等）。

岗位 / 技能体系 / 技能点 / 实训项目的**数据底稿在 `app/db/seed_growth.py`**
（取自《岗位能力与技能点归纳》）：4 个技能体系、35 个技能点、10 个岗位、11 个示例项目，
岗位-技能矩阵（§6 的 ● 与 ○）、项目-技能关联、关卡的填写引导子标题也都在这个文件里。
基础种子 `app/db/seed.py` 与演示数据 `app/db/seed_demo.py` 都调它写库，改数据只改这一处。

注意：**MinIO 是必需组件**（存储层只实现了 S3 协议，没有本地磁盘后端），Milvus 与模型
只在用知识库时才需要。没连上 MinIO 时上传接口会直接报"未配置 S3 接入信息"。

### 自检与测试

```bash
uv run python scripts/check_db.py        # 表 / 索引 / 外键是否与模型一致
uv run python scripts/check_data.py      # 用接口把测试数据体检一遍（入班/选岗/权重/结算/技能进度）
uv run python scripts/check_milvus.py    # 稀疏向量、混合检索、标量过滤、别名切换
uv run python scripts/check_models.py    # BGE-M3 与重排模型能否加载并推理（含显存峰值）
uv run pytest                            # 全量测试；缺 Milvus / 模型的用例会自动跳过
uv run ruff check .                      # 静态检查（含格式检查：ruff format --check .）
```

### 常用维护命令

```bash
uv run alembic upgrade head                              # 执行迁移
uv run alembic revision -m "说明"                        # 新建迁移
uv run python scripts/check_schema_parity.py             # ORM 与 DDL 草稿比对
uv run python scripts/reindex_knowledge.py --pending      # 重建/补齐 Milvus 索引
uv run python scripts/set_llm_config.py --show           # 看大模型配置（默认模型 + kimi/mimo，key 打码）
uv run python scripts/prune_qa_history.py --dry-run      # 看会清理掉哪些过期问答会话
uv run python scripts/dispatch_publish_tasks.py --dry-run # 看有哪些定时任务到了要发布的点
uv run python scripts/dispatch_publish_tasks.py          # 发布到点的定时任务（挂 cron，每分钟一次）
uv run python scripts/prune_qa_history.py                # 清理保留期外的问答历史（挂 cron 用）
```

### 造一套完整案例数据（三端稳压管引脚检测）

按《工业视觉检测项目交付流程——三端稳压管案例版》一键走完全链路，用来验证环境是否真的通了：

```bash
uv run python scripts/seed_case_project.py                 # 建项目 → 传评分标准 → 学生作答 → 传报告 → AI 评审
uv run python scripts/seed_case_project.py --no-review     # 只造数据，不调大模型
```

项目名唯一，重复执行要换 `--project-name`。

### 评一条实训报告（AI 评审）

学生整单提交后调一次即可，链路是"RAG 召回该项目的评分标准 → DeepSeek 打分 → 写回
`review_record` → 结算提交状态与技能进度"：

```bash
curl -X POST http://127.0.0.1:8000/api/submissions/{submission_id}/ai-review
```

返回里带 `criteria_doc_ids`（这次用了哪份评分标准）与 `warnings`（向量链路降级时的提示）。
评分标准没上传、api key 没配时返回可读的失败原因，不会让模型瞎猜。

### 学生 AI 问答（一期：不接 RAG）

会话式多轮问答，上下文窗口是**最近 3 轮**（1 轮 = 1 问 + 1 答），回答来自大模型的通用
知识、**不检索知识库**（`citations` 恒为空数组，`warnings` 里会写明"未接入知识库检索"）。
接口全在 `/api/qa/`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/qa/sessions` | 新建会话 |
| GET | `/api/qa/sessions` | 我的会话列表（只含 7 天内活动过的） |
| GET | `/api/qa/sessions/{id}` | 会话详情（默认带最近 30 条消息） |
| GET | `/api/qa/sessions/{id}/messages` | 历史消息，`before_id` 游标往前翻 |
| PATCH | `/api/qa/sessions/{id}` | 改名 / 关闭 / 重开 |
| DELETE | `/api/qa/sessions/{id}` | 删除会话（连带消息与引用） |
| POST | `/api/qa/sessions/{id}/ask` | 提问，默认 SSE 流式；`stream=false` 返回一次性 JSON。`model` 选 `deepseek` / `kimi` / `mimo`（见 `ai.llm.models`），留空用默认模型 |
| GET | `/api/qa/usage` | token 用量统计（当日 / 保留期内，只统计不限制） |

```bash
# 非流式（脚本化验收、排查问题用这个）
curl -X POST http://127.0.0.1:8000/api/qa/sessions/1/ask \
  -H 'Content-Type: application/json' \
  -d '{"student_id":1,"question":"什么是三端稳压管？","stream":false,"model":"deepseek"}'

# 流式：POST + fetch 读流（SSE 的事件是 meta → delta* → done / error，
# meta 里带 model / model_key / model_label，前端据此显示"这条是谁回答的"）
curl -N -X POST http://127.0.0.1:8000/api/qa/sessions/1/ask \
  -H 'Content-Type: application/json' \
  -d '{"student_id":1,"question":"BGE-M3 稀疏向量怎么用？","stream":true,"model":"kimi"}'
```

两个运维口径：

- **历史只保留 7 天**（`ai.qa.history_retention_days`）。查询层已经过滤，学生看不到过期
  会话；空间回收靠 cron 跑 `scripts/prune_qa_history.py`，按会话粒度清理（一周内有活动
  的会话整体保留）。前端列表上要标注"仅保留最近 7 天"。
- **token 只统计不限制**：`prompt_tokens` / `completion_tokens` 在回答结束回填，
  `GET /api/qa/usage` 汇总；兼容端点不返回 usage 时按字符估算（`estimated=true`）。

### 任务下发（发布任务）

**实训项目管理里的"发布"只代表项目已经编辑好、存在平台里**（`training_project.status =
'PUBLISHED'`），学生看不到；教师还要**发布任务**，项目才会出现在目标学生的列表里。草稿
（DRAFT）/ 已下架（OFF_SHELF）的项目不能发布任务。

一条任务由三块组成：

| 要素 | 字段 | 默认值 |
| --- | --- | --- |
| 发给谁 | `targets`：班级 + 可选分组（`target_type=CLASS/GROUP`） | 班级必选；不选分组 = 全班 |
| 发什么 | `job_ids`（岗位范围）+ `project_level`（层级）+ `project_ids`（可手工指定） | 岗位留空 = 全部岗位；项目留空 = 按「岗位 × 层级」自动挑已发布项目 |
| 什么时候发 | `publish_mode` + `scheduled_at` | `IMMEDIATE` 创建即生效；`SCHEDULED` 到点生效 |

> **岗位范围只用于筛项目**（`training_project.job_id ∈ job_ids`），不筛学生：学生只要在目标
> 班级 / 分组里，这批量项目对他就带上"必修"标记，跟他自己选了什么岗位无关。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/publish-tasks` | 任务列表（`status` / `project_level` / `creator_id` / 关键词 + 分页） |
| POST | `/api/publish-tasks` | 创建并下发（即时 / 定时） |
| GET | `/api/publish-tasks/{id}` | 详情：目标班级 / 分组、岗位范围、项目快照、覆盖学生数 |
| PATCH | `/api/publish-tasks/{id}` | 未发布可全量改；已发布只允许改说明 / 截止时间 / 备注 |
| POST | `/api/publish-tasks/{id}/publish` | 立即发布（定时任务也可以提前发） |
| POST | `/api/publish-tasks/{id}/cancel` | 撤回（不再算必修；项目本身照旧开放，闯关记录保留） |
| DELETE | `/api/publish-tasks/{id}` | 软删（仅限从没发布过的任务） |
| POST | `/api/publish-tasks/preview-students` | 选完班级 / 分组后预览覆盖学生数 |
| GET | `/api/publish-tasks/publishable-projects` | 可发布项目（只返回 PUBLISHED，按岗位 × 层级筛） |
| GET | `/api/students/{student_id}/tasks` | 学生端「我的任务」 |

```bash
# 即时发布：把「工业缺陷检测实训」发给 1 班全班，7 天后截止
curl -X POST http://127.0.0.1:8000/api/publish-tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"第 3 周 · 基础实训","project_level":"BASIC",
       "targets":[{"class_id":1,"target_type":"CLASS"}],
       "publish_mode":"IMMEDIATE","deadline_at":"2026-09-22T23:59:59"}'

# 定时发布：到点才生效
curl -X POST http://127.0.0.1:8000/api/publish-tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"下周开课","job_ids":[1],"project_ids":[1],
       "targets":[{"class_id":1,"target_type":"GROUP","group_id":2}],
       "publish_mode":"SCHEDULED","scheduled_at":"2026-09-22T08:00:00"}'
```

定时发布靠宿主 cron 驱动（**脚本没跑 = 任务不生效**，不做"读的时候顺便判定"的双口径）：

```cron
* * * * * cd /path/to/training_platform && .venv/bin/python scripts/dispatch_publish_tasks.py >> data/run/publish.log 2>&1
```

任务口径：**任务 = 必修**。一条 `status='PUBLISHED'` 的任务覆盖到某个学生（班级命中，或分组
命中）后，任务里的项目对这个学生就是"老师点名要求完成"的必修项（学生端项目列表 `is_required=true`
并带 `required_task_titles` / `required_deadline_at`）；撤回后必修标记消失。项目本身只要处于
`PUBLISHED` 就**对所有学生开放**，不看任务（见下面的「学生成长视图」）。落点表是
`publish_task` / `publish_task_target` / `publish_task_job` / `publish_task_project`，
设计与取舍见 [方案设计](docs/方案设计.md)。

教师端的班级 / 分组 / 任务进度看板：`GET /api/teachers/{teacher_id}/classes`（一次返回任教班级、
学生总数、每个分组的学生明细、每个任务的完成进度与平均完成率），口径与示例见
[接口文档](docs/教师端接口文档.md) §5。

### 学生成长视图（岗位推荐 / 技能树 / 实训项目 / 岗位项目进度 / 项目详情）

五个只读派生接口，全部按学生维度返回，口径统一收在 `app/services/student_overview.py`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/students/{student_id}/job-recommendations` | 岗位推荐，默认前三名（`limit` 可调），按技能匹配度倒序 |
| GET | `/api/students/{student_id}/skill-tree-progress` | 全部技能树与技能点 + 单树进度、整体进度、技能点统计 |
| GET | `/api/students/{student_id}/training-projects` | 已发布实训项目 + 最高分、关卡进度（总/完成）、所属岗位、关联技能点、项目状态 |
| GET | `/api/students/{student_id}/my-projects` | 我的实训（自己挑的 ∪ 老师点名必修的），带 `picked` / `is_required` / `sources` |
| POST / PATCH / DELETE | `/api/students/{student_id}/my-projects…` | 加入（批量幂等）/ 覆盖式排序 / 移出 |
| GET | `/api/students/{student_id}/project-progress` | 实训项目分档进度：按基础 / 进阶 / 拓展各多少个、该学生完成多少个。`scope` 选分母口径：`ALL` 全部已发布项目（默认）/ `SELF` 我自主选择的 / `TEACHER` 老师下发的（都不按岗位过滤） |
| GET | `/api/students/{student_id}/projects/{project_id}` | 项目详情：任务简介 + 关卡（含每个子标题的简介）+ 本轮已保存的作答 + 历史提交次数/日期与 AI、教师评语 |

```bash
curl http://127.0.0.1:8000/api/students/1/job-recommendations
curl http://127.0.0.1:8000/api/students/1/skill-tree-progress
curl http://127.0.0.1:8000/api/students/1/training-projects
curl http://127.0.0.1:8000/api/students/1/my-projects
curl "http://127.0.0.1:8000/api/students/1/project-progress?scope=TEACHER"
curl http://127.0.0.1:8000/api/students/1/projects/1
```

口径：技能点进度取 `student_skill.progress`（"完成项目数 ÷ 关联项目总数"×100，其中**分母 = 该
技能点关联的全部已发布（PUBLISHED）项目**，与任务无关；手工调整记 MANUAL）；
岗位匹配度、技能树进度、整体进度都是**相关技能点进度的均值**；岗位的关联项目 =
`training_project.job_id` 指向该岗位的已发布项目；
关卡进度 =
`project_module` 的关卡数与最新一轮闯关 `attempt_stage.is_filled` 的个数（重新挑战从 0 重新计，
最高分保留）。没关联技能的岗位不参与推荐；学生没开始过的项目也会在实训项目列表里返回
（`status=NOT_STARTED`、成绩 null、进度 0/关卡总数）。实训项目进度（`project-progress`）
的分母由 `scope` 决定，三种口径都只算**已发布项目**、都与岗位无关 —— 所以学生做完别的岗位
的项目也会算进 `ALL`；岗位维度的项目数看 `job-recommendations` 的 `project_total_count` /
`project_done_count`。`SELF` 取学生自己加进「我的实训」的项目，`TEACHER` 取任务点名必修的项目。

> **口径**：`training_project.status = 'PUBLISHED'` 就是**对学生开放**——教师发布项目后，
> 学生端实训项目列表里立刻能看到并开始闯关，不用等任务；任务只把项目标成"必修"，
> 与"平时自己刷项目"共用同一份闯关记录（完成状态、最高分、提交历史都按 `学生 × 项目` 记）。
> 草稿（DRAFT）/ 已下架（OFF_SHELF）的项目既不开放闯关，也不能发布任务。

项目变多以后，学生从项目库里把自己喜欢的挑进「我的实训」（`student_project_pick`）：
**清单表只存"学生主动挑的"**，老师点名必修的项目是实时算出来的，展示时并集 —— 一条项目只出现
一次，用 `picked`（自己加的）/ `is_required`（老师点名）/ `sources`（`SELF`、`TEACHER` 可同时存在）
说明它为什么在这里。必修项目移出后仍在列表里（任务撤回后才消失），自己挑过的项目在任务撤回后
也留着；项目下架后列表里不显示但记录保留。加入清单**不影响技能进度**（分母仍是全部已发布项目）。

项目详情里的关卡子标题取自 `project_module.items_json`（**由教师在项目里手填，模块库不预设**），
每项是 `{"title": "检测对象描述", "prompt": "工件名称、材质、尺寸范围"}`：`prompt` 就是子标题的
填写简介，学生端在子标题下直接展示。案例项目（`scripts/seed_case_project.py`）的七个关卡、
34 个子标题已经全部写好简介；示例项目的子标题见 `app/db/seed_growth.py` 的 `PROJECT_MODULE_ITEMS`。

项目详情里每关的 `answer_text` 就是**本轮已保存的作答**（草稿也算），`current_attempt_id` +
每关的 `attempt_stage_id` 用来调下面的保存接口；重新进来时直接把这批内容填回作答框即可接着写。

### 学生保存作答（草稿保存）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| PUT | `/api/attempts/{attempt_id}/answers` | 一次保存本轮多个关卡的作答；默认只存文本、不改关卡完成状态 |

```bash
curl -X PUT http://127.0.0.1:8000/api/attempts/12/answers \
  -H 'Content-Type: application/json' \
  -d '{"answers":[{"attempt_stage_id":101,"answer_text":"第二轮写了一半的需求分析"}]}'
```

口径：只更新 body 里带到的关卡，其余不动；不传 `is_filled` 时**只存草稿**——关卡不会变成已完成、
进度不会推进、整单提交也不会提前放行；想在同一次调用里把关卡标记完成就传 `is_filled=true`。
返回 `saved_count` / `filled_stage_count` / `progress` / `saved_at`。本轮已提交或已完成后不能再改
（返回 422），不属于本轮的关卡 ID 同样报错。

## 目录结构

```
app/
  api/endpoints/   业务域接口：health / enums / accounts / organization / job_skill /
                   projects / knowledge / qa / attempts / reviews
  core/            配置、数据库会话、统一响应体、异常处理
  crud/            仓储层（分页、软删、部分更新）
  models/          SQLModel 表模型（表结构的唯一来源）
  schemas/         请求 / 响应模型
  services/        业务服务：storage、parsing、splitting、embedding、vector_store、
                   retrieval、knowledge_ingest、settings_store、skill、project、
                   llm、qa、qa_context …
  db/              建库入口、种子数据、演示数据
alembic/           迁移脚本
deploy/            docker-compose（MinIO + Milvus standalone）
docs/              方案、数据字典、字段清单
scripts/           模型下载、索引重建、各类自检脚本
tests/             pytest 用例
data/              本地 SQLite 与上传目录（gitignore）
models/            本地模型权重（gitignore）
```

## 必须知道的几条约定

- **统一响应体**：所有接口返回 `{code, data, msg}`；`code=200` 成功、`code=422` 业务失败
  （HTTP 状态码可能仍是 200，判定看 body）。文件下载接口例外，直接返回文件流。
- **SQLite 是唯一真源**，Milvus 只是可随时重建的派生索引；知识切片正文以 `knowledge_chunk`
  为准，检索命中的主键要回表取正文。
- **文件本体不入库**：对象存储存字节，`file_asset` 存 `bucket + object_key + sha256`，
  `project_file` 存"哪个项目用了它、用途是什么"。项目附件的归属信息只存一处。
- **改表结构要三处同步**：`app/models/` → `alembic` 迁移 → `docs/database_schema_draft.sql`，
  改完必须 `uv run python scripts/check_schema_parity.py` 通过（有测试守着）。
- **枚举 code 只在 `app/models/enums.py` 定义**，落库是 varchar，前端文案走 `GET /api/enums`。
- **配置分两处**：引导性配置（数据库地址、对象存储接入、切片长度）在 `.env`；
  运行时可改的模型与检索参数在 `system_config` 表，改完即生效（读缓存 60 秒）。
- **切片策略按文档类型分流**：评分标准（`EVAL_CRITERIA`）是"整份对照着用"的文档，
  正文不超过 `KNOWLEDGE_WHOLE_DOC_MAX_CHARS`（默认 6000 字）就**整份作为一块**，
  避免按标题拆成几十片后超出上下文预算、后面的关卡拿不到条款；
  知识库资料（`KNOWLEDGE`）可能有几万字，仍按标题结构化细切。
  切分策略记在 `knowledge_doc.chunk_strategy`（`whole_document` / `v1_structural_800`）。

## 现状与已知缺口

- **没有鉴权层**：接口目前都能匿名调用，评分标准等不应外露的内容没有权限隔离。
  这是当前最大的待办——`/api/knowledge/*` 与 `/api/reviews/*` 都应挂到管理端权限下。
- **知识入库是同步的**：解析与切片在请求内完成（毫秒级到秒级），超大文件的异步任务表还没做。
- **检索阈值未生效**：`rag.retrieval.score_threshold` 只是配置项，问答链路（二期）才会用它。
- **AI 评审是同步的**：`POST /api/submissions/{id}/ai-review` 在请求内跑完"召回 + 打分"，
  单次约几秒；并发上来后应改成消费 `review_ai_job` 的后台任务。
- **`review_record` 没有记评分标准版本**：目前把用到的 `criteria_doc_ids` 存在 `raw_json` 里；
  评分标准改版后要能解释历史分数，建议后续加一列 `criteria_doc_id`。
- 证书、通知、操作日志等域的表与接口骨架已就位，业务规则待细化。

## 文档

- [RAG 系统实施方案](docs/RAG系统实施方案.md)：向量库选型、切片策略、检索链路、分期计划
- [平台接口文档（全量 177 个接口）](docs/教师端接口文档.md)：系统与枚举字典、账户权限、教学组织（班级/名单/分组）、岗位与技能、实训项目、任务下发、闯关评审、知识库、AI 问答、教师看板
- [数据库表字段清单](docs/数据库表字段清单.md) ｜ [DDL 草稿](docs/database_schema_draft.sql)
- [后端脚手架实施方案](docs/后端脚手架实施方案.md)
- [系统架构与请求处理链路](docs/系统架构与请求处理链路.md)
- [P1 账户权限-教学组织-岗位技能 CRUD 实施方案](docs/P1-账户权限-教学组织-岗位技能-CRUD实施方案.md)
- 需求确认书与早期方案（`知识入库与切片实施方案.md`、`RAG知识库实施方案-BGE-M3-Milvus.md`）
  已被上面第一份方案取代，保留作历史对照

## 待确认

`project_stage_template` 的七大模块名称（唯一）与默认权重取自《需求确认书 0706》
（需求分析、方案设计、数据处理、模型训练、模型优化、模型测试、实训报告上传，
权重 10/15/15/20/15/15/10）。如与最终评审口径不同，改 `app/db/seed.py` 后重新执行
`uv run python -m app.db.init_db` 即可（种子幂等，不会重复插入）。

岗位 / 技能体系 / 技能点取自《岗位能力与技能点归纳》§5.1 与 §5.2（去掉 PM 与「项目管理系」），
岗位-技能矩阵取 §6 的 ● 与 ○；其中 11 个实训项目里只有 3 个是文档点名的既有项目
（工业缺陷检测实训 / 表面缺陷分类进阶 / 成像系统搭建实训），其余 8 个是**示例数据**
（按"每个岗位至少 1 个已发布项目、35 个技能点都有项目覆盖"补的）。要换成真实项目，
改 `app/db/seed_growth.py` 的 `PROJECTS` 与 `PROJECT_SKILLS` 即可。
