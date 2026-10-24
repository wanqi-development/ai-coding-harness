# 沙箱运行时：原生镜像 + 资产注入

## 目标

沙箱直接用 **原生镜像** `opensandbox/code-interpreter:v1.1.0`，不再维护定制镜像。
skill、代码规则、凭据脚本等平台资产由**应用在会话创建 / 沙箱重连时注入**。

这样做的原因：定制镜像的每次内容变更都要在构建机上重新 `docker build` + 分发，改一个
skill 就得重建整镜像。而镜像一旦与仓库脱节，容器里那份就是旧的——2026-09-14 的会话
测试就踩到过：仓库里的 skill 早已去掉 `sudo gitwrite`，镜像里那份还是旧版，agent 照着
旧文档执行、命令不存在，白折腾一轮。

## 原生镜像自带什么（实测于 2026-09-14）

| 项 | 结果 |
| --- | --- |
| 系统 | Ubuntu 24.04.4 LTS，`execute` 默认 root |
| git | 2.43.0 |
| Java / Maven | OpenJDK 21.0.11 / Maven 3.9.2 |
| Go | 1.25.5 |
| Node / npm | v22.2.0 / 10.7.0 |
| Python | 3.12.3 |
| `/ai_coding/` 布局 | `project` `policies` `skills` `output` `review` `tmp` 已建好（空目录） |
| `/usr/local/sbin` | 空 —— askpass 与 setup 脚本必须运行时推入 |
| sudo | 未安装（`execute` 已是 root，用不到） |

## 注入的资产

源都在本仓库内，配置项见 `.env.example` 的 `SANDBOX_*` 段。

| 源 | 沙箱目标 | 说明 |
| --- | --- | --- |
| `../sandbox/deploy/sandbox/git-askpass` | `<SANDBOX_SBIN_DIR>/git-askpass`（0755） | git 经 `GIT_ASKPASS` 调它取令牌 |
| `../sandbox/deploy/sandbox/setup.sh` | `<SANDBOX_SETUP_COMMAND>`（0755） | 把注入的令牌写成 `.secrets/git_token`，并把 Maven settings.xml 摆到位 |
| `../sandbox/skills/` | `<SANDBOX_LOCAL_SKILLS_DIR>` → `/ai_coding/skills` | 智能体的技能文档，整棵推入 |
| `../sandbox/policies/` | `<SANDBOX_LOCAL_POLICIES_DIR>` → `/ai_coding/policies` | 代码规则与仓库记忆模版 |

注入由 `SandboxProvider.initialize()` 完成，顺序是 **建工作目录 → 推资产 → 跑 setup 脚本**，
随后执行 `git config --system` 写入静态 git 身份（只有 `commit` 需要，与认证无关）。

**create 与 reconnect 两条路径都会跑**：容器被重建后非卷文件会丢失（只有
`/ai_coding/project` 与 Maven 依赖缓存挂在工作区/缓存卷上），所以重连后必须重推。整个流程幂等（覆盖写）。

**依赖缓存卷**（2026-09-25 起）：`/ai_coding/.m2`。默认跨会话共享一个卷，容器重建后依赖仍在 ——
不然每次构建都要重新下依赖（见 `.env.example` 的 `SANDBOX_MAVEN_CACHE_*`）。源配了国内代理：
Maven 走 `settings.xml` 的阿里云镜像。

> 上传的目标路径直接取自配置（`git_askpass_path` / `sandbox_setup_command` / `sandbox_skills_dir` …），
> 配置指向哪个路径就推哪个路径，两边不会漂移。

## 凭据流程

```
会话 token
  → 经 Sandbox.create(env=...) 注入 GIT_TOKEN（与 GIT_ASKPASS / GIT_TERMINAL_PROMPT 同一通道）
  → setup.sh 写入 /ai_coding/.secrets/git_token（0400）
  → git 需要认证时经 GIT_ASKPASS 调 git-askpass 读该文件
```

令牌不出现在命令行、URL 或工具调用参数里；**令牌绝不落镜像层**。

**与平台无关**：一个会话只装一个令牌（Gitee 或 GitHub，由仓库地址决定），沙箱侧只认
「令牌文件 + askpass」，平台差异留在工具层（`tools/git.py` 的 `build_git_client`）。

环境变量名与 git 身份：

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `GIT_TOKEN` | 会话 token | 由 setup 脚本消费后写成凭据文件 |
| `GIT_USERNAME` | `oauth2`（Gitee）/ `x-access-token`（GitHub）/ 平台用户名 | askpass 回给 git 的用户名（PAT 场景用真实用户名） |
| `GIT_ASKPASS` | `<SANDBOX_SBIN_DIR>/git-askpass` | git 询问凭据时调用的脚本 |
| `GIT_TERMINAL_PROMPT` | `0` | 禁止交互式提示，避免命令挂死 |

## 沙箱内布局

根目录 `/ai_coding/`：

- `project/`：目标仓库代码（**唯一挂卷持久化的目录**）
- `../sandbox/policies/`、`../sandbox/skills/`：规则与技能（**只读**，运行时注入）
- `output/`、`review/`、`tmp/`：可写输出目录

## 验证

起一个会话后进沙箱核对：

```bash
ls -l /usr/local/sbin                                  # askpass 与 setup.sh 在位
grep -c gitwrite /ai_coding/skills/ai-coding-implementation/SKILL.md   # 应为 0
ls /ai_coding/policies /ai_coding/skills
ls -l /ai_coding/.secrets/gitee_token                  # 0400
git config --system --get user.name
git ls-remote https://gitee.com/<owner>/<repo>.git     # 公开仓库无需令牌
```

## 安全说明（方案 A：令牌落在沙箱）

本方案**接受「令牌对沙箱内进程可见」**这一事实，不做「令牌不落沙箱」的隔离。理由与缓解：

- **现状**：沙箱内 `execute` 默认以 **root** 运行，任何文件权限都拦不住模型读取令牌。
- **缓解 1**：**每沙箱只服务一个租户**（不池化）→ 令牌只在本租户沙箱内。
- **缓解 2**：使用**最小权限、可轮换的 access token** → 即使泄露，影响有界。
- **缓解 3**：**prompt / skill 明令禁止**读取环境变量与 `.secrets`、禁止打印令牌。

若将来需要「令牌完全不落沙箱」，改用 OpenSandbox 凭据保险库（需服务端 egress sidecar
以 `dns+nft` 模式部署）。

## 变更历史

- **2026-09-14**：由「定制镜像 `ai-coding/code-interpreter:v1`」改为「原生镜像 + 运行时注入」。
  定制镜像及其 `sandbox-image/` 构建上下文（Dockerfile / policies / skills / files）作废，
  资产源改由本仓库 `../sandbox/deploy/sandbox/`、`../sandbox/skills/`、`../sandbox/policies/` 提供，无需构建机与 registry 分发。
