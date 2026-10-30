#!/usr/bin/env bash
# Stop only the ai-coding-harness backend listening on PORT.
set -euo pipefail

PORT="${1:-8000}"
pids=""

if command -v ss >/dev/null 2>&1; then
  pids="$(
    ss -ltnpH "sport = :${PORT}" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | sort -u
  )"
fi

if [ -z "${pids}" ] && command -v fuser >/dev/null 2>&1; then
  pids="$(fuser -n tcp "${PORT}" 2>/dev/null || true)"
fi

found="false"
for pid in ${pids}; do
  command_line="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
  case "${command_line}" in
    *ai_coding_harness.main*)
      found="true"
      echo "停止 standalone 后端：pid=${pid} port=${PORT}"
      kill "${pid}" 2>/dev/null || true
      ;;
    *)
      echo "跳过端口 ${PORT} 上的其他进程：pid=${pid} ${command_line}" >&2
      ;;
  esac
done

if [ "${found}" = "false" ]; then
  echo "未发现占用端口 ${PORT} 的 standalone 后端。"
  exit 0
fi

for _ in 1 2 3 4 5; do
  sleep 1
  if command -v ss >/dev/null 2>&1 \
    && ! ss -ltnH "sport = :${PORT}" 2>/dev/null | grep -q .
  then
    echo "standalone 后端已停止。"
    exit 0
  fi
done

for pid in ${pids}; do
  command_line="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
  case "${command_line}" in
    *ai_coding_harness.main*)
      echo "后端未在宽限期内退出，强制停止：pid=${pid}" >&2
      kill -9 "${pid}" 2>/dev/null || true
      ;;
  esac
done

echo "standalone 后端已停止。"
