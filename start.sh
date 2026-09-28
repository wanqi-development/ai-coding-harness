#!/usr/bin/env bash
# ai-coding-harness 一键启动：后端(FastAPI) + 前端(Vite)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ---- 可按需修改（也可用环境变量覆盖）----
# 项目运行环境 = conda 环境 aicoding（`conda env list` 可见）；换环境时改这一行，或设 PYTHON 覆盖
PYTHON="${PYTHON:-D:/developmentBinary/miniconda3/envs/aicoding/python.exe}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:${FRONTEND_PORT}}"
# ----------------------------------------

# 检查端口占用；占用则询问是否结束占用进程
check_port() {
  local port="$1" name="$2" pids ans p
  pids="$(netstat -ano 2>/dev/null | grep LISTENING | grep ":${port} " | awk '{print $5}' | sort -u || true)"
  [ -z "$pids" ] && return 0
  echo "[警告] 端口 ${port}（${name}）已被占用：PID ${pids}"
  read -r -p "      是否结束这些进程并继续？(y/N) " ans
  case "$ans" in
    y|Y)
      for p in $pids; do
        taskkill //F //PID "$p" >/dev/null 2>&1 || kill "$p" >/dev/null 2>&1 || true
      done
      sleep 1
      echo "      已结束占用进程：${pids}"
      ;;
    *)
      echo "已取消启动。"
      exit 1
      ;;
  esac
}

check_port "$BACKEND_PORT" "后端"
check_port "$FRONTEND_PORT" "前端"

if [ ! -f .env ]; then
  echo "[警告] 未找到 .env，后端可能无法启动。请先由 .env.example 复制并填写配置。"
fi

if ! "$PYTHON" -c "import sys" >/dev/null 2>&1; then
  echo "[错误] Python 无法启动：${PYTHON}"
  echo "       项目运行环境是 conda 环境 aicoding（用 conda env list 查看）；换环境时改上面的 PYTHON。"
  exit 1
fi

# git 自检（只警告）：右侧栏「代码」面板靠**本机** git 把仓库缓存下载下来（不经沙箱），
# 没有 git 只是那个面板用不了 —— 对话、方案、沙箱链路都不受影响，所以不拦启动。
if ! command -v git >/dev/null 2>&1; then
  echo "[警告] 未找到 git：右侧栏的「代码」面板用不了（装 git，或设 REPO_VIEW_GIT 指定 git 可执行文件）。"
fi

echo "[1/2] 启动后端 http://${BACKEND_HOST}:${BACKEND_PORT} ..."
PYTHONPATH=src "$PYTHON" -m ai_coding_harness.main &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT

# 等待后端就绪（最多 30s）
for _ in $(seq 1 30); do
  if curl -s "http://${BACKEND_HOST}:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    echo "      后端就绪。"
    break
  fi
  sleep 1
done

echo "[2/2] 启动前端 ${FRONTEND_URL} ...（Ctrl+C 退出，会一并停掉后端）"
npm --prefix frontend run dev
