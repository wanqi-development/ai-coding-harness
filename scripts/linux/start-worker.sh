#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

cd "${REPO_ROOT}"
echo "[Agent Worker] consumer on ${RUN_JOB_STREAM:-agent-jobs}"
echo "               使用 ${PYTHON}"
export PYTHONPATH=src
export RUN_EXECUTION_MODE=distributed
export APP_ROLE=worker
exec "${PYTHON}" -m ai_coding_harness.worker_main
