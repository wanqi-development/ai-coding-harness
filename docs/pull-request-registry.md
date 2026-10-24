# PR 归属、方案关联与代码同步

## 1. 为什么需要 PR 表

PR 创建者显示的是 token 所属账号；人工和 Agent 共用同一个 token 时，二者无法靠 `user`
字段区分。标题前缀容易被编辑，也不能作为稳定事实。

`pull_requests` 表以平台上稳定的 PR 坐标
`(platform, repo_owner, repo_name, number)` 为唯一键，保存：

- 状态、标题、链接、创建者；
- `head_ref` / `base_ref`；
- 远端最新 `head_sha`；
- 最后一次安全同步进沙箱的 `synced_head_sha`；
- `session_id` 与产出它的 `plan_id`；
- `source`：`agent / legacy_agent / external / unknown`。

一个 plan 可以关联多个替代 PR，因此 `plan_id` 不设唯一约束。

## 2. 归属规则

- `create_pr` 新建或复用 PR 成功后立即登记为 `agent`，同时写本轮 `plan_id` 和 head SHA。
- 历史 PR 首次成为 open PR 时，平台读取 commits：
  - 所有 commit 的 author name/email 都匹配沙箱静态 git 身份：`legacy_agent`；
  - 明确不匹配：`external`；
  - 查询失败或无提交：`unknown`。
- 以后只读数据库中的 `source`，不反复查询 commits。
- 外部或未知的 open PR 会在意图识别前硬停止，防止基于无法确认的仓库状态继续 coding /
  plan / rework。

## 3. 每轮 PR 查询

轮首分别查询：

1. `state=all` 最近 10 条：用于仓库记忆增量同步；
2. `state=open` 全量分页：用于 PR 表、归属闸门、评论状态与工作区同步。

最近 10 条如果与 PR 表上一轮最大编号完全没有重叠，说明两轮之间新增超过一页，会继续向
后翻页直到覆盖旧水位；正常情况下仍只请求 10 条。

不做跨轮接口缓存。查询失败时本轮只保留只读 `qa` 候选，不能把失败当成“没有 PR”。

## 4. 用户向 PR 追加提交

平台比较平台最新 `head_sha` 与数据库 `synced_head_sha`。rework 有唯一目标 PR，或用户明确
写出 `#N` 时，Agent 启动前执行：

1. 检查工作区是否干净；
2. `git fetch origin <head_ref>`；
3. 校验 fetch 到的提交就是 PR 当前 `head_sha`；
4. 本地分支不存在则创建；存在时只允许它是远端祖先；
5. 切到 PR 的 `head_sha`；
6. 成功后更新 `synced_head_sha`。

工作区有未提交修改、force-push 后分叉、同步期间 head 再变化或 fetch 失败时，本轮不会进入
Agent。禁止用 reset / rebase / 强推覆盖用户提交。

## 5. 相关代码

- ORM：`src/ai_coding_harness/db/models.py`
- DAO：`src/ai_coding_harness/db/dao/pull_requests.py`
- 轮首快照与归属：`src/ai_coding_harness/server/repo_state.py`
- 工作区同步：`src/ai_coding_harness/server/pr_workspace.py`
- PR 创建登记：`src/ai_coding_harness/tools/git.py`
- 意图前拦截：`src/ai_coding_harness/server/turn/runner.py`
