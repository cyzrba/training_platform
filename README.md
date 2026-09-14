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
| LangChain / DeepSeek | 已装、待接线 | 二期问答编排与生成 | 现在无影响 |

精确版本以 `pyproject.toml` 与 `uv.lock` 为准；容器镜像版本固定在 `deploy/docker-compose.rag.yml`。

## 跑起来

```bash
# 1. 依赖
uv sync                 # 基础环境（不含 torch）
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
```

## 目录结构

```
app/
  api/endpoints/   业务域接口：health / enums / accounts / organization / job_skill /
                   projects / knowledge / attempts / reviews
  core/            配置、数据库会话、统一响应体、异常处理
  crud/            仓储层（分页、软删、部分更新）
  models/          SQLModel 表模型（表结构的唯一来源）
  schemas/         请求 / 响应模型
  services/        业务服务：storage、parsing、splitting、embedding、vector_store、
                   retrieval、knowledge_ingest、settings_store、skill、project …
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

## 现状与已知缺口

- **没有鉴权层**：接口目前都能匿名调用，评分标准等不应外露的内容没有权限隔离。
- **知识入库是同步的**：解析与切片在请求内完成（毫秒级到秒级），超大文件的异步任务表还没做。
- **检索阈值未生效**：`rag.retrieval.score_threshold` 只是配置项，问答链路（二期）才会用它。
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
