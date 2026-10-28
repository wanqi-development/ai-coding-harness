#!/bin/bash
# 沙箱初始化：把环境变量里的令牌落到凭据文件，供 askpass 取用。
# 由应用在创建 / 重连沙箱后经 execute 调用（OpenSandbox 会覆盖 ENTRYPOINT，不能靠它执行）。
# 注意：本脚本必须不阻塞，不要 exec tail。
set -euo pipefail

mkdir -p /ai_coding/.secrets /ai_coding/project
umask 077

# 写入令牌（令牌只在运行时经环境变量注入，不落镜像层）。空值必须删除旧文件，
# 否则用户清空令牌后会发现沙箱仍在用上一次的凭据。
# 与平台无关：一个会话只装一个令牌（Gitee 或 GitHub），沙箱侧不必区分。
if [ -n "${GIT_TOKEN:-}" ]; then
  printf '%s' "$GIT_TOKEN" > /ai_coding/.secrets/git_token.tmp
  mv -f /ai_coding/.secrets/git_token.tmp /ai_coding/.secrets/git_token
  chmod 0400 /ai_coding/.secrets/git_token
else
  rm -f /ai_coding/.secrets/git_token /ai_coding/.secrets/git_token.tmp
fi

printf '%s' "${GIT_USERNAME:-oauth2}" > /ai_coding/.secrets/git_username.tmp
mv -f /ai_coding/.secrets/git_username.tmp /ai_coding/.secrets/git_username
chmod 0400 /ai_coding/.secrets/git_username

# Maven：把 settings.xml（阿里云镜像 + 本地仓库路径）放到 Maven 默认读取的位置，
# 这样任何 `mvn` 命令不用加 -s。本地仓库 /ai_coding/.m2/repository 在沙箱卷上，
# 容器重建后依赖仍在 —— 见 config 的 SANDBOX_MAVEN_CACHE_*。
mkdir -p /ai_coding/.m2/repository
if [ -f /ai_coding/maven/settings.xml ]; then
  mkdir -p "${HOME:-/root}/.m2"
  cp -f /ai_coding/maven/settings.xml "${HOME:-/root}/.m2/settings.xml"
fi
