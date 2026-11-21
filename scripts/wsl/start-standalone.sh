#!/usr/bin/env bash
# Start the backend inside WSL with the standalone profile.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HARNESS_USER="${HARNESS_USER:-$(id -un)}"
HARNESS_HOME="$(getent passwd "${HARNESS_USER}" | cut -d: -f6)"
APP_HOME="${HARNESS_HOME}/.ai-coding-harness"
VENV="${HARNESS_VENV:-${APP_HOME}/venv}"
DATA_DIR="${HARNESS_DATA_DIR:-}"
LOG_DIR="${HARNESS_LOG_DIR:-}"
CREDENTIALS_DIR="${HARNESS_CREDENTIALS_DIR:-}"
BACKUP_DIR="${HARNESS_BACKUP_DIR:-}"
DB_PATH="${HARNESS_SQLITE_PATH:-${DATA_DIR:+${DATA_DIR}/harness.sqlite3}}"
PYTHON="${VENV}/bin/python"
ADMIN_CREDENTIALS_FILE="${ADMIN_CREDENTIALS_FILE:-${CREDENTIALS_DIR:+${CREDENTIALS_DIR}/initial-admin.txt}}"

if [ -z "${DATA_DIR}" ] || [ -z "${LOG_DIR}" ] || [ -z "${CREDENTIALS_DIR}" ] || [ -z "${BACKUP_DIR}" ]; then
  echo "[错误] standalone 持久目录必须由 Windows 启动器提供。" >&2
  echo "       需要 HARNESS_DATA_DIR / HARNESS_LOG_DIR / HARNESS_CREDENTIALS_DIR / HARNESS_BACKUP_DIR。" >&2
  exit 1
fi

if [ ! -x "${PYTHON}" ]; then
  echo "[错误] Python 虚拟环境不存在：${PYTHON}" >&2
  echo "先运行 bash ${REPO_ROOT}/scripts/wsl/bootstrap.sh" >&2
  exit 1
fi

if ! bash "${SCRIPT_DIR}/verify-toolchain.sh"; then
  exit 1
fi

if ! "${PYTHON}" -c \
  'import deepagents, langgraph, typesafe_sdk' >/dev/null 2>&1; then
  if [ "${HARNESS_SKIP_DEPENDENCY_REPAIR:-0}" = "1" ]; then
    echo "[错误] standalone venv 缺少项目依赖，且已设置 HARNESS_SKIP_DEPENDENCY_REPAIR=1。" >&2
    exit 1
  fi
  echo "standalone venv 缺少项目依赖，正在按当前 pyproject 修复..."
  "${PYTHON}" -m pip install -e "${REPO_ROOT}[dev]"
fi

if ! "${PYTHON}" -c \
  'import deepagents, langgraph, typesafe_sdk' >/dev/null 2>&1; then
  echo "[错误] 项目依赖修复后仍无法导入 deepagents / langgraph / typesafe_sdk。" >&2
  exit 1
fi

install -d -m 700 "${APP_HOME}" "${APP_HOME}/sessions"

prepare_host_dir() {
  local path="$1"
  mkdir -p "${path}"
  case "${path}" in
    /mnt/*)
      # Windows drvfs does not implement Linux chmod/chown. The directory ACL
      # comes from the Windows account that created it in the launcher.
      ;;
    *)
      chmod 700 "${path}" 2>/dev/null || true
      ;;
  esac
}

prepare_host_dir "${DATA_DIR}"
prepare_host_dir "${LOG_DIR}"
prepare_host_dir "${CREDENTIALS_DIR}"
prepare_host_dir "${BACKUP_DIR}"

LEGACY_DB="${APP_HOME}/harness.sqlite3"
if [ ! -f "${DB_PATH}" ] && [ -f "${LEGACY_DB}" ]; then
  echo "迁移 WSL 旧 SQLite 到 Windows 持久目录..."
  sqlite3 "${LEGACY_DB}" ".backup '${DB_PATH}'"
fi
if [ -d "${APP_HOME}/logs" ]; then
  cp -an "${APP_HOME}/logs/." "${LOG_DIR}/" 2>/dev/null || true
fi
if [ ! -f "${ADMIN_CREDENTIALS_FILE}" ] && [ -f "${APP_HOME}/logs/initial-admin.txt" ]; then
  cp -f "${APP_HOME}/logs/initial-admin.txt" "${ADMIN_CREDENTIALS_FILE}"
  case "${CREDENTIALS_DIR}" in
    /mnt/*) ;;
    *) chmod 600 "${ADMIN_CREDENTIALS_FILE}" 2>/dev/null || true ;;
  esac
fi

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
export SQLITE_BACKUP_DIR="${BACKUP_DIR}"
export SANDBOX_PROVIDER=wsl
export WSL_USER="${HARNESS_USER}"
export WSL_DISTRO="${WSL_DISTRO:-ai-coding-harness}"
export WSL_ROOT="${WSL_ROOT:-${APP_HOME}/sessions}"
export WSL_BWRAP="${WSL_BWRAP:-/usr/bin/bwrap}"
export WSL_ALLOW_NETWORK="${WSL_ALLOW_NETWORK:-true}"
export REDIS_URL=
export REDIS_REQUIRED=false
export LOG_DIR
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
echo "              logs=${LOG_DIR}"
echo "              sandbox=WSL2 + bubblewrap"
exec "${PYTHON}" -m ai_coding_harness.main
