#!/usr/bin/env bash
# Verify the native toolchain used by the standalone WSL deployment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FAILURES=0

fail() {
  echo "[缺失] $*"
  FAILURES=$((FAILURES + 1))
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "$1"
  fi
}

if ! grep -qi microsoft /proc/version 2>/dev/null; then
  fail "当前环境不是 WSL2"
fi

for command_name in \
  python3 git curl node npm java mvn go rg sqlite3 bwrap
do
  require_command "${command_name}"
done

if command -v python3 >/dev/null 2>&1; then
  if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    fail "Python 版本低于 3.11"
  fi
fi

if command -v node >/dev/null 2>&1; then
  node_major="$(node -p 'Number(process.versions.node.split(".")[0])')"
  if [ "${node_major}" -lt 20 ]; then
    fail "Node.js 版本低于 20"
  fi
fi

if command -v java >/dev/null 2>&1; then
  java_version="$(java -version 2>&1 | head -n 1)"
  case "${java_version}" in
    *\"21.*|*\"22.*|*\"23.*|*\"24.*|*\"25.*|*\"26.*)
      ;;
    *)
      fail "Java 版本低于 21：${java_version}"
      ;;
  esac
fi

for asset in \
  "sandbox/skills" \
  "sandbox/policies" \
  "sandbox/maven" \
  "sandbox/deploy/sandbox"
do
  if [ ! -e "${REPO_ROOT}/${asset}" ]; then
    fail "仓库资产 ${asset}"
  fi
done

if command -v bwrap >/dev/null 2>&1; then
  bwrap_args=(bwrap --die-with-parent --unshare-pid)
  for path in /usr /bin /sbin /lib /lib64 /etc; do
    if [ -e "${path}" ]; then
      bwrap_args+=(--ro-bind "${path}" "${path}")
    fi
  done
  bwrap_args+=(--proc /proc --dev /dev /bin/true)
  if ! "${bwrap_args[@]}" >/dev/null 2>&1; then
    fail "bubblewrap 无法创建隔离进程（检查内核 unprivileged user namespaces）"
  fi
fi

if [ "${FAILURES}" -ne 0 ]; then
  echo
  echo "独立版工具链检查失败：${FAILURES} 项。"
  echo "先在 WSL 中运行 scripts/wsl/bootstrap.sh，或查看 docs/standalone-wsl.md。"
  exit 1
fi

echo "独立版工具链检查通过。"
printf 'Python: '; python3 --version
printf 'Node:   '; node --version
printf 'Java:   '; java -version 2>&1 | head -n 1
printf 'Maven:  '; mvn --version | head -n 1
printf 'Go:     '; go version
printf 'bwrap:  '; bwrap --version
