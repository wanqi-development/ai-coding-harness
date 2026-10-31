#!/usr/bin/env bash
# Shared configuration and checks for the Linux launchers.
set -euo pipefail

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${COMMON_DIR}/../.." && pwd)"

# Python is project-local only. Environment managers materialize this exact .venv.
PYTHON="${REPO_ROOT}/.venv/bin/python"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:${FRONTEND_PORT}}"

check_port() {
  local port="$1" name="$2" listing pids ans pid
  if command -v ss >/dev/null 2>&1; then
    listing="$(ss -ltnH "sport = :${port}" 2>/dev/null || true)"
    [ -z "${listing}" ] && return 0
    pids="$(ss -ltnpH "sport = :${port}" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | sort -u || true)"
    if [ -z "${pids}" ]; then
      echo "[错误] 端口 ${port} 已被占用，但当前权限无法识别 PID。"
      return 1
    fi
  elif command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  else
    echo "[错误] 需要 ss 或 lsof 才能检查端口 ${port}。"
    return 1
  fi

  [ -z "${pids}" ] && return 0
  echo "[警告] 端口 ${port}（${name}）已被占用：PID ${pids}"
  if [ ! -t 0 ]; then
    echo "[错误] 当前不是交互终端，无法询问是否结束占用进程。"
    return 1
  fi
  read -r -p "      是否结束这些进程并继续？(y/N) " ans
  case "${ans}" in
    y|Y)
      for pid in ${pids}; do
        kill "${pid}" >/dev/null 2>&1 || true
      done
      sleep 1
      echo "      已结束占用进程：${pids}"
      ;;
    *)
      echo "取消启动。"
      return 1
      ;;
  esac
}

preflight_backend() {
  if [ ! -f "${REPO_ROOT}/.env" ]; then
    echo "[警告] 未找到 .env，后端可能无法启动。请先由 .env.example 复制并填写配置。"
  fi
  if [ ! -x "${PYTHON}" ]; then
    echo "[错误] 未找到项目虚拟环境：${PYTHON}"
    echo "       先执行：uv sync --extra dev"
    return 1
  fi
  if ! "${PYTHON}" -c \
    "import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)" \
    >/dev/null 2>&1; then
    echo "[错误] Python 3.14 无法启动：${PYTHON}"
    echo "       用 Python 3.14 重新创建项目 .venv。"
    return 1
  fi
  if ! "${PYTHON}" -c "import ssl" >/dev/null 2>&1; then
    echo "[错误] 项目 Python 无法加载 SSL 模块：${PYTHON}"
    echo "       解释器构建不完整，执行 uv sync --extra dev 重建 .venv。"
    return 1
  fi
  if ! (cd "${REPO_ROOT}" && "${PYTHON}" -c "import ai_coding_harness.main") \
    >/dev/null 2>&1; then
    echo "[错误] 项目依赖未安装到：${PYTHON}"
    echo "       执行：uv sync --extra dev"
    return 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "[警告] 未找到 git：右侧栏「代码」面板用不了（装 git，或设 REPO_VIEW_GIT）。"
  fi
}

preflight_frontend() {
  if ! node --version; then
    echo "[错误] node 无法运行；请安装 Node.js 20+ 并加入 PATH。"
    return 1
  fi
  if ! npm --version; then
    echo "[错误] npm 无法运行；请确认 Node.js 20+ 的 npm 已加入 PATH。"
    return 1
  fi
}

wait_http() {
  local host="$1" port="$2" path="$3" tries="$4"
  local i
  for i in $(seq 1 "${tries}"); do
    if curl -fsS --connect-timeout 1 --max-time 2 \
      "http://${host}:${port}${path}" >/dev/null 2>&1; then
      printf "\n"
      return 0
    fi
    printf "."
    sleep 1
  done
  printf "\n"
  return 1
}
