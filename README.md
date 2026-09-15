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

# 4.1 配主模型与 api key（存 system_config 表，改完即生效，不用重启）
uv run python scripts/set_llm_config.py --show
LLM_API_KEY=sk-xxx uv run python scripts/set_llm_config.py \
    --model deepseek-v4-flash --base-url https://api.deepseek.com/v1 --api-key-env LLM_API_KEY

# 5. 建库 + 种子数据 + 启动
uv run python -m app.db.init_db          # 迁移到最新 + 写种子（幂等，可反复执行）
uv run uvicorn app.main:app --reload     # 接口文档 http://127.0.0.1:8000/docs
```

演示数据（岗位、技能树、项目、闯关记录等）：`uv run python -m app.db.seed_demo`。

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
uv run python scripts/set_llm_config.py --show           # 看主模型配置（api key 打码）
uv run python scripts/prune_qa_history.py --dry-run      # 看会清理掉哪些过期问答会话
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
| POST | `/api/qa/sessions/{id}/ask` | 提问，默认 SSE 流式；`stream=false` 返回一次性 JSON |
| GET | `/api/qa/usage` | token 用量统计（当日 / 保留期内，只统计不限制） |

```bash
# 非流式（脚本化验收、排查问题用这个）
curl -X POST http://127.0.0.1:8000/api/qa/sessions/1/ask \
  -H 'Content-Type: application/json' \
  -d '{"student_id":1,"question":"什么是三端稳压管？","stream":false}'

# 流式：POST + fetch 读流（SSE 的事件是 meta → delta* → done / error）
curl -N -X POST http://127.0.0.1:8000/api/qa/sessions/1/ask \
  -H 'Content-Type: application/json' \
  -d '{"student_id":1,"question":"BGE-M3 稀疏向量怎么用？","stream":true}'
```

两个运维口径：

- **历史只保留 7 天**（`ai.qa.history_retention_days`）。查询层已经过滤，学生看不到过期
  会话；空间回收靠 cron 跑 `scripts/prune_qa_history.py`，按会话粒度清理（一周内有活动
  的会话整体保留）。前端列表上要标注"仅保留最近 7 天"。
- **token 只统计不限制**：`prompt_tokens` / `completion_tokens` 在回答结束回填，
  `GET /api/qa/usage` 汇总；兼容端点不返回 usage 时按字符估算（`estimated=true`）。

### 学生成长视图（岗位推荐 / 技能树 / 实训项目）

三个只读派生接口，全部按学生维度返回，口径统一收在 `app/services/student_overview.py`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/students/{student_id}/job-recommendations` | 岗位推荐，默认前三名（`limit` 可调），按技能匹配度倒序 |
| GET | `/api/students/{student_id}/skill-tree-progress` | 全部技能树与技能点 + 单树进度、整体进度、技能点统计 |
| GET | `/api/students/{student_id}/training-projects` | 已发布实训项目 + 最高分、关卡进度（总/完成）、所属岗位、关联技能点、项目状态 |

```bash
curl http://127.0.0.1:8000/api/students/1/job-recommendations
curl http://127.0.0.1:8000/api/students/1/skill-tree-progress
curl http://127.0.0.1:8000/api/students/1/training-projects
```

口径：技能点进度取 `student_skill.progress`（"完成项目数 ÷ 关联项目总数"×100，手工调整记
MANUAL）；岗位匹配度、技能树进度、整体进度都是**相关技能点进度的均值**；岗位的关联项目 =
`training_project.job_id` 指向该岗位且已发布（PUBLISHED）的项目；关卡进度 =
`project_module` 的关卡数与最新一轮闯关 `attempt_stage.is_filled` 的个数（重新挑战从 0 重新计，
最高分保留）。没关联技能的岗位不参与推荐；学生没开始过的项目也会在实训项目列表里返回
（`status=NOT_STARTED`、成绩 null、进度 0/关卡总数）。

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
- [数据库表字段清单](docs/数据库表字段清单.md) ｜ [DDL 草稿](docs/database_schema_draft.sql)
- [后端脚手架实施方案](docs/后端脚手架实施方案.md)
- [系统架构与请求处理链路](docs/系统架构与请求处理链路.md)
- [P1 账户权限-教学组织-岗位技能 CRUD 实施方案](docs/P1-账户权限-教学组织-岗位技能-CRUD实施方案.md)
- 需求确认书与早期方案（`知识入库与切片实施方案.md`、`RAG知识库实施方案-BGE-M3-Milvus.md`）
  已被上面第一份方案取代，保留作历史对照

## 待确认

`project_stage_template` 的七大模块 `stage_key`、名称与默认权重取自《需求确认书 0706》
（需求分析、方案设计、数据处理、模型训练、模型优化、模型测试、实训报告上传，
权重 10/15/15/20/15/15/10）。如与最终评审口径不同，改 `app/db/seed.py` 后重新执行
`uv run python -m app.db.init_db` 即可（种子幂等，不会重复插入）。
