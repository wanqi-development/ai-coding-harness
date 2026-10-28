# 沙箱工作区目录与读写权限

沙箱根目录为 `/ai_coding`。下表汇总各目录的用途与读写权限。

**这张表是执行口径，不只是约定**：每个智能体的可写范围写在
`src/ai_coding_harness/safety/policy.py` 的策略表里，由 `BoundaryMiddleware` 两层落实 ——
越界的写工具直接从模型的工具清单里撤掉（看不见），命令与参数的越界在调用前拦下并返回一条
可读的拒绝理由。`safety/permissions.py` 另有一层与角色无关的底线（凭据、提权、毁机器、
破坏性 git）。

## 目录权限总览

| 目录 | 用途 | 权限 | 说明 |
|------|------|------|------|
| `/ai_coding/project/` | 项目代码（仓库 clone） | **条件读写** | 只有主控编码形态与 coder 可读写，其余只读 |
| `/ai_coding/policies/` | 代码规则 | 只读 | 平台预置，任何智能体都不许改 |
| `/ai_coding/skills/` | 技能 | 只读 | 平台预置，任何智能体都不许改 |
| `/ai_coding/output/` | 输出资料 | **谁也不写** | 没有回传通路（API / 前端一处都不读它），写进去用户取不到 —— 结论一律进回复 |
| `/ai_coding/review/` | 审查报告 | 条件可写 | code-review 写审查报告 |
| `/ai_coding/tmp/` | 临时/中间过程 | 条件可写 | 分析中间文件 |
| `/ai_coding/.secrets/` | 凭据 | **不可读不可写** | root 所有，会话 git 令牌保密 |
| `/ai_coding/.m2/` | Maven 本地仓库缓存 | 不设限 | 挂在卷上跨容器重建存活，模型不用管 |
| `/tmp`、`/dev/*` | 沙箱自带临时区与设备文件 | 不设限 | 随沙箱销毁、没有读方，逐角色计较无意义 |

## 按智能体的读写矩阵

主控按**形态**（`agent/profiles.py`）、子智能体按**角色**各取一行。

| 智能体 | project | tmp | output | review |
|--------|---------|-----|--------|--------|
| 主控 `orchestrate`（编码） | **读写** | 可写 | — | 可写 |
| 主控 `plan`（出方案） | — | — | — | — |
| 主控 `qa`（问答 / 解释 / 排查） | — | 可写（取证台账） | — | — |
| 子智能体 `planner` | — | — | — | — （写工具已撤掉） |
| 子智能体 `coder` | **读写** | 可写 | — | — |
| 子智能体 `reviewer` | — | 可写 | — | 可写 |

空白格 = 该智能体不写这个目录：写文件类工具会被撤掉，`execute` 里的写动词 / 重定向
在调用前被拦下。**没登记名字的智能体按最保守处理**（一律不写），而不是放开。

> **这张表只管沙箱目录。** 仓库记忆（`/memories/<platform>/<owner>/<repo>.md`）在 StoreBackend 上，
> 不在沙箱里、也不受上表约束：模型正文由 `orchestrate`（coding / rework）写；
> PR 新增记录与状态由轮首 / 轮末平台同步写。`qa` 与 `plan` 没有写正文的工具。
> 工具子集见 `tools/register.py` 的 `_FORM_TOOLS`。

> 意图 `rework`（PR 跟进）**复用** `orchestrate` 这一行：它与 `coding` 共用装配，
> 差别由逐轮意图指令和意图后的状态闸门决定。别名登记见
> `src/ai_coding_harness/agent/profiles.py` 的 `FORM_ALIASES`。

> 沙箱之外还有两个前缀，**不在沙箱里、也不受上表约束**：`/conversation_history/`
> （会话压缩流水，deepagents 摘要中间件写）与 `/large_tool_results/`（过大的工具结果）。
> 它们被 `backends/composite.py` 路由到 `StoreBackend`（与仓库记忆同一个持久库、不同
> namespace），所以沙箱销毁或重建后仍在 —— 别把它们当沙箱路径去 `/ai_coding/` 下找。

## 关键规则

1. **`/ai_coding/.secrets/`**：存放平台注入的会话 git 令牌（Gitee / GitHub，0400）。智能体**绝不能读取、复制或引用**其内容；命令层按路径关键字直接拒绝，`read_file` / `ls` 这类文件接口也单独拦一道。凭据只由 askpass / create_pr / comment_pr 在受控路径内消费。
2. **`/ai_coding/policies/`、`/ai_coding/skills/`**：平台预置的规则与技能，谁都不许改（`execute` 写到这两个目录同样被拒）。
3. **`/ai_coding/project/`**：只有主控编码形态与 `coder` 可读写；出方案、分析、审查一律只读。
4. **出方案不落文件**：方案经 `storePlan` 落库，不另存一份文件 —— 主控 `plan` 与子智能体 `planner` 同形，写工具都已撤掉。仓库记忆另走 `save_repo_memory`（StoreBackend），与沙箱文件不是一回事。
5. **`/ai_coding/output/` 谁也不写**：它**没有回传通路** —— API / 前端一处都不读它，卷也只挂 `project`，写进去的文件随沙箱销毁、用户取不到，落成文件等于把答案藏起来。结论、报告一律进回复；`/ai_coding/tmp/` 是中间过程文件（按角色给，见上表），`/ai_coding/review/` 是审查报告。
## 已知缺口

判定是「反滑不反敌」：拦的是模型手滑与越界意图，不是铁了心绕的对手。

- 判不出落点的相对路径放过：命令里没有 `cd`、或 `cd` 到了我们看不懂的地方（`cd $DIR`、`cd ~`）时，相对目标不拦。沙箱当前工作目录不在我们手里，硬判会误伤大量正当命令。
- 间接执行看不出：`find … | xargs rm`、`python -c "open(...,'w')"`、`base64 -d | sh` 这类绕法不管。真正不可挽回的那批由 `permissions.py` 的底线兜。
- 同一件事的另一层：`OpenSandboxBackend.aexecute` 仍跑一遍 `CommandSafety`，是底线规则的第二道（这里的中间件拒绝能给出可读理由，那边只能把理由塞进命令输出）。**按角色的目录范围只有中间件这一道** —— 后端是会话级共享的（一个会话一个后端，换形态不重建），挂不上按角色的范围。

## 与 skill 的对应

- `ai-coding-implementation`（coding）：写 `/ai_coding/project`，其余只读 —— 由主控编码形态与 `coder` 执行。
- `repo-bootstrap-analysis`：project 只读，中间文件写 `/ai_coding/tmp`，**报告进回复、不落文件** —— 执行者（编码形态 / qa / `planner`）口径一致；`planner` 只跑其中的只读部分，产物并入方案。结论最终由 coding / rework 收尾写回仓库记忆。
- `code-review`：project 只读，写 `/ai_coding/review` + `/ai_coding/tmp` —— 由 `reviewer` 执行。
