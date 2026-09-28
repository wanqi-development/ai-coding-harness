---
name: code-review
description: >
  基于本地 git diff 的代码审查。查看工作区改动，按代码规则与通用质量维度
  逐项审查（含 SOLID/架构、删除候选、安全与并发、代码质量），问题按 P0-P3
  分级，输出审查报告并评论到 PR。当用户要求代码审查、PR review、评审 diff
  或审查 Pull Request 时使用。只读审查，不改代码。
  Triggers on: 代码审查, code review, PR review, 评审 diff, review diff,
  审查 Pull Request, review pull request.
---

# 代码审查（code-review）

## 运行环境背景（务必先读）

本 skill 运行在 **OpenSandbox 沙箱**中：

- 工作区根目录：`/ai_coding`
- 项目目录：`/ai_coding/project`（目标仓库 clone 在此）
- 审查报告目录：`/ai_coding/review`（审查结果写到这里）
- 中间目录：`/ai_coding/tmp`（分析过程产生的临时文件放这里）
- 命令一律通过 deepagents 的 `execute` 工具执行，无交互。

## 命令限制（红线）

**只读审查**：除以下目录外，一切操作只读，不修改代码、不改仓库状态。

- 可写目录：`/ai_coding/review`（审查报告）、`/ai_coding/tmp`（中间过程）。
- 其余路径（尤其 `/ai_coding/project`）**只允许只读命令**。

### 只读命令白名单

```
git status / log / show / diff / diff --stat / diff --numstat / blame
git ls-remote / remote -v / branch -a / tag -l
ls / find / cat / head / tail / grep / rg / sed -n / awk（只打印）
```

### 禁止

- 写 `/ai_coding/project` 下任何文件。
- git 写命令（add/commit/checkout/push 等）。
- 安装依赖、运行有副作用的命令。

## 适用场景

- 用户要求代码审查、PR review、评审 diff、审查 Pull Request。
- 对工作区未提交的改动做检查。

## 执行步骤

### 1. 取改动范围

```bash
cd /ai_coding/project/<repo>
git status                 # 改动文件清单
git diff --stat            # 改动规模
git diff --numstat         # 各文件增删行数（评估是否分批）
```

### 2. 评估规模，决定是否分批

- **改动小**（不超过 10 个文件且不超过 500 行）→ 直接 `git diff` 全量审查。
- **改动大**（超过上述阈值）→ 先用 `git diff --stat` 出一份**文件级摘要**，再按模块/功能分批 `git diff -- <path>` 逐块审查，避免一次读入超长 diff 撑爆上下文。
- **改动跨多个不相关关注点** → 按**功能**归类输出，不要按文件顺序平铺。
- **`git diff` 为空** → 不要臆造审查结论。改查 `git diff --cached`，或向用户确认要审的 commit range；仍为空就明确告知「无改动可审」。

### 3. 逐项审查

对每处改动，按「审查维度」逐项核对，并结合 `/ai_coding/policies/` 代码规则（如有）。

涉及下列维度时，用 `execute` 载入对应深清单作为检查依据：

| 维度 | 深清单 |
|---|---|
| 架构、SOLID、坏味道 | `cat /ai_coding/skills/code-review/references/solid-checklist.md` |
| 安全、并发竞态、数据完整性 | `cat /ai_coding/skills/code-review/references/security-checklist.md` |
| 错误处理、性能与缓存、边界条件 | `cat /ai_coding/skills/code-review/references/code-quality-checklist.md` |
| 删除候选与迭代计划 | `cat /ai_coding/skills/code-review/references/removal-plan.md` |

改动小、维度明确的场景不必全量载入，按需取用即可。

### 4. 删除候选与迭代计划

找出改动中没用上、重复、或已被 flag 关掉的代码，**写成计划而不是直接删**，模板见 `references/removal-plan.md`：

- **现在就能删**：位置 / 理由 / 证据（无引用）/ 影响 / 删除步骤 / 验证方式
- **需要计划再删**：为什么缓 / 前置条件 / 破坏性变更 / 迁移方案 / 回滚方案

没有删除候选时跳过本节。

### 5. 问题分级与定位

每个问题标出：**级别 + 文件 + 行号 + 问题描述 + 修改建议**。

```
P0 严重 —— 安全漏洞、数据丢失、正确性错误。必须阻塞合并。
P1 高   —— 逻辑错误、明显架构/SOLID 违背、性能回退。合并前应修改。
P2 中   —— 代码坏味道、可维护性问题、轻微 SOLID 违背。本次修改或转后续跟进。
P3 低   —— 风格、命名、轻微建议。可选改进。
```

安全类问题必须同时说清**可利用性**和**影响面**，只写「存在注入风险」不算数。

### 6. 输出报告

写报告到 `/ai_coding/review/`（如 `review-<时间戳或PR号>.md`），并调用 `comment_pr` 工具把结论与关键问题评论到 PR。

## 审查维度（检查清单）

- **正确性**：逻辑是否正确，边界条件、空值、错误处理是否完整。
- **规范**：是否符合 `/ai_coding/policies/` 规则与命名规范；是否遵守编码分层（接口层/实现层/存储层单向依赖，禁止跨层）。
- **安全**：有无密钥/令牌/敏感信息混入，有无注入风险；并发场景有无竞态。
- **性能**：有无明显低效（N+1 查询、重复 IO、不必要的大对象）。
- **可测试性**：是否补测试、纯函数与 IO 是否分离。
- **可维护性**：可读性、重复代码、注释与 docstring 是否到位。

上文「执行步骤 3」的深清单是这些维度的展开，按需载入。

## 输出格式

写入 `/ai_coding/review/review-<标识>.md`：

```markdown
# 代码审查报告

## 结论
- 通过（无 P0/P1/P2）
- 仅评论（只有 P2/P3，不阻塞合并）
- 需修改（存在 P0/P1）

## 改动摘要
- （git diff --stat 的结论）

## 问题列表

### P0 严重
- [文件:行号] 问题描述 → 修改建议

### P1 高
- [文件:行号] 问题描述 → 修改建议

### P2 中
- [文件:行号] 问题描述 → 修改建议

### P3 低
- [文件:行号] 问题描述 → 修改建议

（编号跨节连续；无问题的级别写明「无」）

## 删除与迭代计划（可选，无则省略）
- （按 references/removal-plan.md 的模板）

## 亮点（可选）
- 值得肯定的地方

## 本次未覆盖（必写）
- 查了哪些维度、哪些没查（如「未验证数据库迁移」「未运行测试」）、残余风险
```

## 红线规则

- **只读**：不改 `/ai_coding/project` 下任何内容，不执行 git 写命令。
- 只写 `/ai_coding/review`（报告）和 `/ai_coding/tmp`（中间），不越界。
- 审查基于实际 diff，不臆造不存在的问题；不确定时标注「需确认」。
- 问题要具体、可执行，避免空泛（如「代码不够好」）。
- 只输出审查结论与建议，**不在本流程中修改代码**；用户要求修复时另行确认后再做。
