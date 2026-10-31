#!/usr/bin/env bash
# Start the backend inside WSL with the standalone profile.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HARNESS_USER="${HARNESS_USER:-$(id -un)}"
HARNESS_HOME="$(getent passwd "${HARNESS_USER}" | cut -d: -f6)"
APP_HOME="${HARNESS_HOME}/.ai-coding-harness"
VENV="${HARNESS_VENV:-${APP_HOME}/venv}"
DB_PATH="${HARNESS_SQLITE_PATH:-${APP_HOME}/harness.sqlite3}"
PYTHON="${VENV}/bin/python"
ADMIN_CREDENTIALS_FILE="${ADMIN_CREDENTIALS_FILE:-${APP_HOME}/logs/initial-admin.txt}"

if [ ! -x "${PYTHON}" ]; then
  echo "[错误] Python 虚拟环境不存在：${PYTHON}" >&2
  echo "先运行 bash ${REPO_ROOT}/scripts/wsl/bootstrap.sh" >&2
  exit 1
fi

if ! bash "${SCRIPT_DIR}/verify-toolchain.sh"; then
  exit 1
fi

install -d -m 700 "${APP_HOME}" "${APP_HOME}/sessions" "${APP_HOME}/logs"

needs_admin() {
  if [ ! -f "${DB_PATH}" ]; then
    return 0
  fi
  local count
  count="$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM users;" 2>/dev/null || true)"
  [ "${count:-0}" -eq 0 ]
}

export HOME="${HARNESS_HOME}"
export PYTHONPATH="${REPO_ROOT}/src"
export DEPLOYMENT_MODE=standalone
export SQLITE_PATH="${DB_PATH}"
export SANDBOX_PROVIDER=wsl
export WSL_USER="${HARNESS_USER}"
export WSL_DISTRO="${WSL_DISTRO:-ai-coding-harness}"
export WSL_ROOT="${WSL_ROOT:-${APP_HOME}/sessions}"
export WSL_BWRAP="${WSL_BWRAP:-/usr/bin/bwrap}"
export WSL_ALLOW_NETWORK="${WSL_ALLOW_NETWORK:-true}"
export REDIS_URL=
export REDIS_REQUIRED=false
export LOG_DIR="${LOG_DIR:-${APP_HOME}/logs}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"

if needs_admin; then
  echo "首次启动：创建 standalone 管理员（随机密码）..."
  "${PYTHON}" -m ai_coding_harness.manage \
    ensure-standalone-admin \
    --username admin \
    --credentials-file "${ADMIN_CREDENTIALS_FILE}"
fi

cd "${REPO_ROOT}"
echo "[standalone] http://${HOST}:${PORT}"
echo "              SQLite=${DB_PATH}"
echo "              sandbox=WSL2 + bubblewrap"
exec "${PYTHON}" -m ai_coding_harness.main
