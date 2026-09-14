#!/usr/bin/env bash
# 一键把整个项目拉起来：中间件（MinIO + Milvus）→ 建库 + 种子 → 启动后端服务。
#
# 用法：
#   scripts/start_all.sh                   # 全套启动，前端就绪后按 Ctrl-C 停后端
#   scripts/start_all.sh --with-demo       # 额外灌演示数据（岗位 / 技能树 / 班级 / 闯关记录）
#   scripts/start_all.sh --no-rag          # 只起 MinIO，不跑知识库检索也能开发
#   scripts/start_all.sh --no-reload       # 关掉热重载（想跑得稳一点时用）
#   scripts/start_all.sh --port 8001       # 换端口
#   scripts/start_all.sh stop              # 停后端 + 停中间件
#   scripts/start_all.sh status            # 看各组件状态
#   scripts/start_all.sh logs milvus-standalone   # 跟中间件日志（不填服务名看全部）
#
# 设计取舍：
# - **幂等**：中间件用 docker compose up -d、建库与种子都是可重复执行的，随时再跑一次不会坏数据。
# - **不自动下模型**：权重 4.3GB，只做提示，要下自己跑 scripts/download_rag_models.py。
# - **中间件与后端分开停**：Ctrl-C 只停后端（容器重启慢，开发时通常想留着），
#   要一起停用 `scripts/start_all.sh stop`。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="deploy/docker-compose.rag.yml"
RUN_DIR="data/run"
PID_FILE="$RUN_DIR/backend.pid"
LOG_FILE="$RUN_DIR/backend.log"

# 中间件：服务名与容器名一一对应，容器名在"旧 compose 项目占用了同名容器"时要用到
RAG_SERVICES=(training-minio milvus-etcd milvus-minio milvus-standalone)
RAG_CONTAINERS=(training-minio milvus-etcd milvus-minio milvus-standalone)
MINIO_ONLY=(training-minio)

HOST="127.0.0.1"
PORT=8000
WITH_RAG=1
WITH_DEMO=0
RELOAD=1

# --------------------------------------------------------------------- 输出

green() { printf '\033[32m%s\033[0m' "$1"; }
yellow() { printf '\033[33m%s\033[0m' "$1"; }
red() { printf '\033[31m%s\033[0m' "$1"; }
info() { printf '%s\n' "$1"; }
step() { printf '\n▶ %s\n' "$1"; }
ok() { printf '  %s %s\n' "$(green ✓)" "$1"; }
warn() { printf '  %s %s\n' "$(yellow !)" "$1"; }
die() { printf '\n%s %s\n' "$(red ✗)" "$1" >&2; exit 1; }

# ------------------------------------------------------------------- Python

# 优先用项目内的 .venv；没有就退回 uv run（README 里的标准用法）
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  command -v uv >/dev/null 2>&1 || die "找不到 .venv 也没有 uv，先执行：uv sync"
  PYTHON=""
fi

run_py() {
  if [[ -n "$PYTHON" ]]; then
    "$PYTHON" "$@"
  else
    uv run python "$@"
  fi
}

port_open() {
  run_py -c 'import socket, sys; s = socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$1"
}

wait_port() {
  local port="$1" name="$2" timeout="${3:-120}" waited=0
  printf '  等待 %s（:%s）' "$name" "$port"
  until port_open "$port"; do
    if (( waited >= timeout )); then
      printf ' %s\n' "$(red ✗)"
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
    printf '.'
  done
  printf ' %s\n' "$(green ✓)"
}

# ------------------------------------------------------------------- 中间件

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

ensure_docker() {
  command -v docker >/dev/null 2>&1 || die "没装 docker；中间件（MinIO / Milvus）必须用它跑"
  docker info >/dev/null 2>&1 || die "docker 守护进程连不上，先把 Docker Desktop 起起来"
}

start_middleware() {
  step "启动中间件"
  ensure_docker
  local services=("${MINIO_ONLY[@]}")
  (( WITH_RAG )) && services=("${RAG_SERVICES[@]}")
  info "  服务：${services[*]}"

  # compose up 在"容器名已被另一个 compose 项目占用"时会报 Conflict（本机就踩过），
  # 这种情况直接 docker start 同名容器即可，没必要删容器重建。
  if ! compose up -d "${services[@]}" >/dev/null 2>&1; then
    warn "compose up 失败（多半是容器名被旧 compose 项目占用），退回 docker start"
    for name in "${RAG_CONTAINERS[@]}"; do
      docker start "$name" >/dev/null 2>&1 || true
    done
  fi

  wait_port 9000 "MinIO"
  if (( WITH_RAG )); then
    wait_port 19530 "Milvus"
  else
    warn "按 --no-rag 跳过 Milvus：知识库入库 / 向量检索 / 评分标准批改链路会不可用"
  fi
}

check_models() {
  step "检查模型权重"
  if [[ -d models/bge-m3 && -d models/bge-reranker-v2-m3 ]]; then
    ok "models/ 下已有 BGE-M3 与 bge-reranker-v2-m3"
  else
    warn "models/ 下缺少权重，向量化与重排会失败；需要时执行：uv run python scripts/download_rag_models.py（约 4.3GB）"
  fi
}

# --------------------------------------------------------------------- 数据

prepare_data() {
  step "建库与种子数据（幂等）"
  [[ -f .env ]] || { cp .env.example .env; ok "已从 .env.example 生成 .env"; }
  run_py -m app.db.init_db
  ok "迁移到最新 + 基础种子（角色 / 权限 / 关卡模板 / 技能树 / 系统配置）"
  if (( WITH_DEMO )); then
    run_py -m app.db.seed_demo >/dev/null
    ok "演示数据已就绪（教师 / 班级 / 学生 / 岗位 / 项目 / 闯关记录）"
  else
    warn "跳过演示数据（要造数据加 --with-demo）"
  fi
}

# --------------------------------------------------------------------- 后端

stop_backend() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      sleep 1
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$PID_FILE"
  fi
  # 兜底：pid 文件丢了也要能把残留的 uvicorn 收干净
  pkill -f "uvicorn app.main:app" >/dev/null 2>&1 || true
}

start_backend() {
  step "启动后端服务"
  mkdir -p "$RUN_DIR"
  stop_backend

  local args=(-m uvicorn app.main:app --host "$HOST" --port "$PORT")
  (( RELOAD )) && args+=(--reload)
  if [[ -n "$PYTHON" ]]; then
    "$PYTHON" "${args[@]}" >"$LOG_FILE" 2>&1 &
  else
    uv run python "${args[@]}" >"$LOG_FILE" 2>&1 &
  fi
  echo $! >"$PID_FILE"

  if ! wait_port "$PORT" "后端"; then
    printf '\n后端启动失败，最后 20 行日志：\n'
    tail -n 20 "$LOG_FILE" || true
    die "后端没起来（完整日志：$LOG_FILE）"
  fi
  ok "后端已就绪（日志：$LOG_FILE）"
}

banner() {
  local reload_note="热重载 关"
  (( RELOAD )) && reload_note="热重载 开"
  printf '\n%s\n' "$(green '================ 项目已启动 ================')"
  printf '  接口文档   http://%s:%s/docs\n' "$HOST" "$PORT"
  printf '  健康检查   http://%s:%s/api/health\n' "$HOST" "$PORT"
  if (( WITH_RAG )); then
    printf '  MinIO 控制台  http://127.0.0.1:9001  （minioadmin / minioadmin123）\n'
    printf '  Milvus       127.0.0.1:19530\n'
  fi
  printf '  %s\n' "$reload_note"
  printf '  停后端     Ctrl-C（中间件继续跑）\n'
  printf '  全停       scripts/start_all.sh stop\n'
  printf '  数据体检   uv run python scripts/check_data.py\n'
  printf '%s\n\n' "$(green '===========================================')"
}

# --------------------------------------------------------------- 子命令

cmd_stop() {
  step "停止服务"
  stop_backend
  ok "后端已停"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    # 按容器名停，而不是 compose stop：本机的容器可能是用旧 compose 项目名（甚至 docker run）
    # 建的，compose stop 会漏掉它们。
    for name in "${RAG_CONTAINERS[@]}"; do
      docker stop "$name" >/dev/null 2>&1 || true
    done
    ok "中间件已停（容器保留，下次启动更快）"
  fi
}

cmd_status() {
  step "组件状态"
  local state
  for item in "MinIO:9000" "MinIO 控制台:9001" "Milvus:19530" "后端:$PORT"; do
    name="${item%%:*}"
    port="${item##*:}"
    if port_open "$port"; then state="$(green 运行中)"; else state="$(red 未运行)"; fi
    printf '  %s（:%s）  %s\n' "$name" "$port" "$state"
  done
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    printf '\n  容器：\n'
    for name in "${RAG_CONTAINERS[@]}"; do
      state="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo '未创建')"
      printf '    %s  %s\n' "$name" "$state"
    done
  fi
}

cmd_logs() {
  ensure_docker
  if [[ -n "${1:-}" ]]; then
    compose logs -f "$1"
  else
    compose logs -f
  fi
}

usage() {
  # 打印文件头的注释块（跳过 shebang，遇到第一行非注释就停）
  awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

# --------------------------------------------------------------------- 入口

ACTION="start"
while (($#)); do
  case "$1" in
    start | stop | status | logs) ACTION="$1"; shift ;;
    --with-demo) WITH_DEMO=1; shift ;;
    --no-rag) WITH_RAG=0; shift ;;
    --no-reload) RELOAD=0; shift ;;
    --port) [[ -n "${2:-}" ]] || die "--port 后面要跟端口号"; PORT="$2"; shift 2 ;;
    --host) [[ -n "${2:-}" ]] || die "--host 后面要跟地址"; HOST="$2"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) die "不认识的参数：$1（-h 看用法）" ;;
  esac
done

case "$ACTION" in
  stop) cmd_stop ;;
  status) cmd_status ;;
  logs) cmd_logs "${1:-}" ;;
  start)
    printf '%s\n' "$(green '启动岗位闯关式实训平台')"
    start_middleware
    (( WITH_RAG )) && check_models
    prepare_data
    start_backend
    banner
    # 前台等后端：Ctrl-C 只停后端，中间件留着（重启容器要几十秒）
    trap 'stop_backend; printf "\n%s 后端已停；中间件还在跑，要一起停：scripts/start_all.sh stop\n" "$(yellow ·)"' INT TERM
    wait
    ;;
esac
