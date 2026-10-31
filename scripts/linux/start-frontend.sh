#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

if [ "${HARNESS_SKIP_PORT_CHECK:-0}" != "1" ]; then
  check_port "${FRONTEND_PORT}" "前端"
fi
if [ "${HARNESS_SKIP_PREFLIGHT:-0}" != "1" ]; then
  preflight_frontend
fi

cd "${REPO_ROOT}"
echo "[前端] ${FRONTEND_URL}"
export HARNESS_FRONTEND_PORT="${FRONTEND_PORT}"
export HARNESS_BACKEND_URL="${HARNESS_BACKEND_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}"
exec npm --prefix frontend run dev
