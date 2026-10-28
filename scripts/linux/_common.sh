#!/usr/bin/env bash
# Shared configuration and checks for the Linux launchers.
set -euo pipefail

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${COMMON_DIR}/../.." && pwd)"

# 可设 PYTHON 覆盖；默认使用当前已激活的 Python 环境（项目推荐 aicoding）
PYTHON="${PYTHON:-python3}"
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
  if ! "${PYTHON}" -c "import sys" >/dev/null 2>&1; then
    echo "[错误] Python 无法启动：${PYTHON}"
    echo "       先激活项目环境（推荐 conda aicoding），或设置 PYTHON=/path/to/python。"
    return 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "[警告] 未找到 git：右侧栏「代码」面板用不了（装 git，或设 REPO_VIEW_GIT）。"
  fi
}

preflight_frontend() {
  if ! command -v npm >/dev/null 2>&1; then
    echo "[错误] 未找到 npm，无法启动前端。"
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
