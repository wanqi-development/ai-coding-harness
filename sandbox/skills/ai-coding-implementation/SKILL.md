---
name: ai-coding-implementation
description: >
  按 planner 的方案实施代码改动并在代码平台（Gitee / GitHub）提交 PR。当 coder 需要落地一个（多步）方案时使用。
  Covers: 取方案、建分支、按步骤实施、测试、更新仓库记忆、提交 PR。
  Triggers on: 按方案实施, 落地实施方案, 写代码实现方案, 提交代码提PR,
  gitee pr, 码云提交, github pr, implement plan and open PR.
---

# 代码实施 + PR 提交（Gitee / GitHub）

> 本 skill 只负责**按 planner 的方案实施**，不做需求分析 / 方案设计（那是 planner 的职责），也不与用户对话（主智能体才是唯一入口）。

## 运行环境背景（务必先读）

- 工作区根目录：`/ai_coding`
- 项目目录：`/ai_coding/project`（目标仓库 clone 在此）
- 凭据目录：`/ai_coding/.secrets/`（平台注入的令牌，**禁止读取**）
- 无头容器：命令必须通过 `execute` 工具非交互执行，弹提示的操作都会失败。
- 容器生命周期有限：每阶段完成即 commit + push，不把成果只留在容器文件系统里。

### 凭据与命令通道

令牌由平台注入沙箱环境变量，并由初始化脚本写入 `/ai_coding/.secrets/git_token`，供 **askpass 自动取用**（`GIT_ASKPASS` 已在镜像中配置）。令牌来自 Gitee 还是 GitHub 由会话决定，本流程两边一致。核心纪律：**绝不主动读取环境变量或 `.secrets`，绝不打印令牌。**

| 操作 | 用法 | 说明 |
|------|------|------|
| 读命令 | 普通 `git` | clone / fetch / status / diff 等 |
| 本地写命令 | 普通 `git` | `add` / `commit` |
| 建分支 / push | 普通 `git` | 需要令牌时由 askpass 自动提供，令牌不出现在命令行/URL |
| 建 PR | `create_pr` 工具 | 凭据由工具内部读取 |

> PR 的**审查与评论由 `reviewer` 负责**，本 skill（coder）不评论 PR。

## 凭据设施（平台预装，只验证不修改）

| 设施 | 路径 | 作用 |
|------|------|------|
| 令牌文件 | `/ai_coding/.secrets/git_token` | 会话 git 令牌（Gitee / GitHub，由 setup 脚本写入） |
| askpass 脚本 | `/usr/local/sbin/git-askpass` | git 询问凭据时回令牌（经 `GIT_ASKPASS`） |

remote 必须是无内嵌令牌的纯 HTTPS 地址。

## git 命令使用规则

### 读命令（直接执行）

```
git clone / fetch / ls-remote / remote -v
git status / log / show / diff / blame
git branch -a / tag -l / rev-parse / shortlog / describe
```

### 本地写命令（直接执行，不需要令牌）

```bash
git add -A
git commit -m "type(scope): 改动说明"
```

commit 规范：`type(scope): subject`，type ∈ feat/fix/refactor/perf/test/docs/chore。

### 创建分支

```bash
git checkout -b feature/<精简描述>   # 需求
git checkout -b fix/<精简描述>       # 缺陷修复
```

分支命名：需求用 `feature/` 前缀，缺陷用 `fix/` 前缀，描述精简、见名知义。

### 需要令牌的命令（askpass 自动供令牌）

```bash
git push origin feature/<精简描述>
# 私有仓库的认证 clone/fetch 同理：git clone / fetch ...
```

### 明确禁止

- **合并类命令一律不执行**（PR 合并由人工在代码平台上完成）：`git merge / rebase / cherry-pick / revert / reset --hard / stash / clean / rm / mv`。
- 不读 `/ai_coding/.secrets/` 下任何文件，不读环境变量，不读 askpass 输出。
- 不把令牌拼进任何 URL、命令或 remote 配置。

## 适用场景

- 由 planner 产出**多步方案**、需要落地为 PR 的改动。
- 目标仓库托管在 **Gitee（码云）或 GitHub**，最终交付物是一个 **Pull Request**。

简单单文件小改不走本流程，直接改完展示即可。

## 前置条件

- **仓库已就绪**：由 coder 流程的「准备仓库」完成（`ls /ai_coding/project/<repo>` 判断，不存在则 `git clone`，已存在则 `git pull` / `git fetch`）。
- **已取到方案**：通过 `get_unexecuted_plan`（入参 `owner`、`repo`、`session_id`）拿到 planner 写入 DB 的方案。
- 凭据设施在位（可选校验）：

```bash
ls -l /ai_coding/.secrets/git_token /usr/local/sbin/git-askpass
```

## 整体流程

```
取方案(get_unexecuted_plan) → 建分支 → 按步骤实施（每步回写 updatePlanStep）→ 测试
  → 更新仓库记忆 → 只读自检 → add/commit → git push
  → create_pr → updatePlanStatus → 回报主智能体
```

### 1. 取方案

调用 `get_unexecuted_plan`（工具已绑定当前会话，无需传参）取出 planner 写入的方案，返回结果里带 `plan_id` 和带编号的步骤列表，**按其步骤逐条实施**。

### 2. 创建分支

```bash
git checkout -b feature/<精简描述>   # 需求
git checkout -b fix/<精简描述>       # 缺陷修复
```

### 3. 按步骤实施（强要求）

1. **严格按方案步骤逐条执行** —— 不跳步、不合并、不擅自改序；每步完成后确认其「完成标准」再进入下一步。
2. **逐步回写进度（强要求）**：每步**开始时**调用 `updatePlanStep(plan_id, index, "running")`，**完成时**改为 `"done"`，**失败时**改为 `"failed"`；`index` 用 `get_unexecuted_plan` 结果里的编号（从 1 开始）。这是给用户看的实时进度，**必须一步一回写**，不要攒到最后一次性补齐。
3. 遵循仓库现有结构、命名、代码风格；优先复用已有工具函数。
4. 复杂逻辑写注释，公共接口写 docstring；错误处理完整。
5. 同步补测试：覆盖新增分支与边界条件。

### 4. 测试（必须全过）

```bash
cd /ai_coding/project/<repo>
python -m pytest -q / npm test / go test ./...
ruff check . / eslint . / go vet ./...
npm run build / go build ./... / cargo build
```

任一失败必须修复后再进入下一步，**不得带病提交**。若仓库无测试框架，至少做编译/语法检查，并在 PR 描述中注明。

### 5. 回报稳定结论（不要自己写记忆）

仓库记忆统一由主智能体写回：把本次改动的稳定结论（技术栈、测试命令、关键文件、约定、最近结论）随结果回报主智能体，由它用 `save_repo_memory` 写回 `/memories/${owner}/${repo_name}.md`（结构见 `/ai_coding/policies/repo-memory-template.md`）。

### 6. 只读自检

```bash
git status              # 改动文件清单
git diff --stat         # 改动规模
git diff                # 逐文件核对：无密钥/令牌/大文件(>10MB)/临时文件(.env/.idea 等)
```

### 7. 提交与推送

```bash
git add -A
git commit -m "feat(scope): 改动内容与动机（遵循仓库 commit 规范）"
git push origin feature/<精简描述>          # 需要令牌时由 askpass 自动提供
git ls-remote origin feature/<精简描述>      # 核对远端分支
```

### 8. 创建 Pull Request（调用 create_pr 工具）

调用 `create_pr` 工具，正文包含：

1. 改动摘要（对应 git diff --stat 的结论）
2. 本地验证结果（测试/lint/构建命令与结论）
3. 关联 issue / 风险点 / 需要 reviewer 关注的地方

PR 建成后调用 `updatePlanStatus(plan_id, "done")` 标记方案已完成；中途因需求冲突中止实施则用 `"failed"`。

### 9. 回报主智能体

把 **PR 链接 + 改动摘要 + 验证结果** 回报给主智能体。（PR 的审查与评论由 `reviewer` 负责，本 skill 不做。）

## 红线规则

- 所有操作限定在 `/ai_coding/project` 内，不越出工作区；凭据设施只验证存在性，不读内容。
- **绝不直接接触令牌**：不读环境变量（如 `GIT_TOKEN`）、不读 `/ai_coding/.secrets/`、不读 askpass 输出、不把令牌拼进任何 URL 或命令，也不在回复/日志中打印令牌。
- 合并类命令（merge/rebase 等）一律不执行 —— PR 合并由人工在代码平台上完成。
- 不提交密钥、令牌、大文件（>10MB）、临时文件。
- 测试/lint 不过不提交；不夹带方案范围之外的改动。
- 容器可能随时销毁：每个阶段完成后立即 commit + push。

## 故障排查

| 现象 | 处理 |
|------|------|
| push 时反复要求输入用户名密码或 401 | askpass 未生效（`GIT_ASKPASS`/`GIT_TERMINAL_PROMPT` 应已由镜像配置）；报告主智能体检查镜像配置，不要自己配凭据 |
| push 403 | 令牌无 `projects` 权限或仓库无写权限，报告主智能体 |
| push 被拒（non-fast-forward） | 与目标分支分歧：本 skill 不执行合并，**报告主智能体** |
| create_pr 返回 401/403 | 报告主智能体检查平台侧令牌有效性与 `projects` 权限 |
| create_pr 返回 404/422 | 仓库名/分支名错误或源分支未 push，先 `git ls-remote origin` 核对 |
| 工具参数不确定 | 查看工具注册的 schema，不要猜；仍不明确则回报主智能体 |
| git fetch/ls-remote 超时 | 与已确认的连通性预期不符，重试 1-2 次仍失败则报告主智能体 |
| 方案不清 / 缺步骤 | 回报主智能体，不要自行脑补方案 |
