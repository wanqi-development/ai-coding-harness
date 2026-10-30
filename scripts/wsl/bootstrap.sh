#!/usr/bin/env bash
# One-time native bootstrap for the standalone WSL deployment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HARNESS_USER="${HARNESS_USER:-harness}"
NODE_MAJOR="${NODE_MAJOR:-22}"
GO_VERSION="${GO_VERSION:-1.25.5}"

if [ "${EUID}" -ne 0 ]; then
  exec sudo -E bash "$0" "$@"
fi

if [ ! -r /etc/os-release ]; then
  echo "[错误] 无法识别 Linux 发行版。" >&2
  exit 1
fi
source /etc/os-release
case "${ID:-}" in
  ubuntu|debian)
    ;;
  *)
    echo "[错误] bootstrap.sh 目前支持 Ubuntu / Debian，当前为 ${ID:-unknown}。" >&2
    exit 1
    ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  build-essential \
  bubblewrap \
  ca-certificates \
  curl \
  git \
  golang-go \
  jq \
  libffi-dev \
  libsqlite3-dev \
  libssl-dev \
  maven \
  pkg-config \
  python3 \
  python3-pip \
  python3-venv \
  ripgrep \
  sqlite3 \
  unzip \
  wget \
  xz-utils

if apt-cache show openjdk-21-jdk >/dev/null 2>&1; then
  apt-get install -y openjdk-21-jdk
fi

java21_home="$(
  find /usr/lib/jvm -maxdepth 1 -type d -name 'java-21-openjdk-*' \
    | sort | head -n 1
)"
if [ -n "${java21_home}" ]; then
  update-alternatives --set java "${java21_home}/bin/java" >/dev/null
  if [ -x "${java21_home}/bin/javac" ]; then
    update-alternatives --set javac "${java21_home}/bin/javac" >/dev/null
  fi
fi

if ! command -v java >/dev/null 2>&1; then
  echo "[错误] 没有可用的 Java 21。" >&2
  exit 1
fi
java_major="$(
  java -version 2>&1 \
    | awk -F'\"' '/version/ {split($2, parts, "."); print parts[1]; exit}'
)"
if [ "${java_major:-0}" -lt 21 ]; then
  echo "[错误] 需要 Java 21，当前主版本为 ${java_major:-unknown}。" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1 \
  || [ "$(node -p 'Number(process.versions.node.split(".")[0])')" -lt 20 ]
then
  nodesource_setup="$(mktemp)"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" \
    -o "${nodesource_setup}"
  bash "${nodesource_setup}"
  rm -f "${nodesource_setup}"
  apt-get install -y nodejs
fi

install_go="false"
if ! command -v go >/dev/null 2>&1; then
  install_go="true"
else
  current_go="$(go version | awk '{print $3}' | sed 's/^go//')"
  if ! printf '%s\n%s\n' "1.22.0" "${current_go}" | sort -V -C; then
    install_go="true"
  fi
fi

if [ "${install_go}" = "true" ]; then
  case "$(dpkg --print-architecture)" in
    amd64) go_arch="amd64" ;;
    arm64) go_arch="arm64" ;;
    *)
      echo "[错误] 不支持的 Go 架构：$(dpkg --print-architecture)" >&2
      exit 1
      ;;
  esac
  go_root="/opt/harness-go/${GO_VERSION}"
  go_archive="$(mktemp)"
  mkdir -p "${go_root}"
  curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz" \
    -o "${go_archive}"
  tar -xzf "${go_archive}" --strip-components=1 -C "${go_root}"
  rm -f "${go_archive}"
  ln -sfn "${go_root}/bin/go" /usr/local/bin/go
  ln -sfn "${go_root}/bin/gofmt" /usr/local/bin/gofmt
fi

if ! id "${HARNESS_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "${HARNESS_USER}"
fi

HARNESS_HOME="$(getent passwd "${HARNESS_USER}" | cut -d: -f6)"
APP_HOME="${HARNESS_HOME}/.ai-coding-harness"
VENV="${APP_HOME}/venv"
DB_PATH="${APP_HOME}/harness.sqlite3"
ADMIN_CREDENTIALS_FILE="${ADMIN_CREDENTIALS_FILE:-${APP_HOME}/logs/initial-admin.txt}"

install -d -m 700 -o "${HARNESS_USER}" -g "${HARNESS_USER}" \
  "${APP_HOME}" "${APP_HOME}/sessions" "${APP_HOME}/logs"

if [ ! -f "${REPO_ROOT}/.env" ]; then
  install -m 600 -o "${HARNESS_USER}" -g "${HARNESS_USER}" \
    "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  echo "已创建 ${REPO_ROOT}/.env；请按需填写搜索等部署级凭据。"
fi

runuser -u "${HARNESS_USER}" -- env \
  HOME="${HARNESS_HOME}" \
  HARNESS_REPO_ROOT="${REPO_ROOT}" \
  HARNESS_VENV="${VENV}" \
  bash -c '
    set -euo pipefail
    if [ ! -x "${HARNESS_VENV}/bin/python" ]; then
      python3 -m venv "${HARNESS_VENV}"
    fi
    "${HARNESS_VENV}/bin/python" -m pip install --upgrade pip
    "${HARNESS_VENV}/bin/python" -m pip install "${HARNESS_REPO_ROOT}[dev]"
  '

needs_admin() {
  if [ ! -f "${DB_PATH}" ]; then
    return 0
  fi
  local count
  count="$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM users;" 2>/dev/null || true)"
  [ "${count:-0}" -eq 0 ]
}

if needs_admin; then
  echo "创建首个 standalone 管理员（随机密码）..."
  runuser -u "${HARNESS_USER}" -- env \
    HOME="${HARNESS_HOME}" \
    PYTHONPATH="${REPO_ROOT}/src" \
    DEPLOYMENT_MODE=standalone \
    SQLITE_PATH="${DB_PATH}" \
    SANDBOX_PROVIDER=wsl \
    WSL_USER="${HARNESS_USER}" \
    WSL_ROOT="${APP_HOME}/sessions" \
    LOG_DIR="${APP_HOME}/logs" \
    "${VENV}/bin/python" -m ai_coding_harness.manage \
    ensure-standalone-admin \
    --username admin \
    --credentials-file "${ADMIN_CREDENTIALS_FILE}"
fi

echo
echo "standalone 初始化完成。"
echo "  用户：${HARNESS_USER}"
echo "  数据库：${DB_PATH}"
echo "  虚拟环境：${VENV}"
echo "  初始管理员凭据：${ADMIN_CREDENTIALS_FILE}"
echo "  启动：bash ${REPO_ROOT}/scripts/wsl/start-standalone.sh"
