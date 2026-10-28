#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

if [ "${HARNESS_SKIP_PORT_CHECK:-0}" != "1" ]; then
  check_port "${BACKEND_PORT}" "后端"
fi
if [ "${HARNESS_SKIP_PREFLIGHT:-0}" != "1" ]; then
  preflight_backend
fi

cd "${REPO_ROOT}"
echo "[后端] ${BACKEND_HOST}:${BACKEND_PORT}"
echo "       使用 ${PYTHON}"
export PYTHONPATH=src
exec "${PYTHON}" -m ai_coding_harness.main
