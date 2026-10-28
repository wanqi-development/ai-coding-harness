#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

preflight_backend
preflight_frontend
check_port "${BACKEND_PORT}" "后端"
check_port "${FRONTEND_PORT}" "前端"

echo "[1/3] 启动后端 http://${BACKEND_HOST}:${BACKEND_PORT} ..."
HARNESS_SKIP_PORT_CHECK=1 HARNESS_SKIP_PREFLIGHT=1 \
  bash "${SCRIPT_DIR}/start-backend.sh" &
BACKEND_PID=$!
cleanup() {
  kill "${BACKEND_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "      等待后端就绪（最多 30 秒）..."
if wait_http "${BACKEND_HOST}" "${BACKEND_PORT}" /health 30; then
  echo "后端就绪。"
else
  echo "[警告] 后端未就绪，仍继续启动前端；请检查后台报错。"
fi

echo "[2/3] 启动前端 ${FRONTEND_URL} ..."
HARNESS_SKIP_PORT_CHECK=1 HARNESS_SKIP_PREFLIGHT=1 \
  bash "${SCRIPT_DIR}/start-frontend.sh"
