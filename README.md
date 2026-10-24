# ai-coding-harness

基于 [deepagents](https://github.com/langchain-ai/deepagents) + [OpenSandbox](https://open-sandbox.ai/) 的本地优先、单租户 coding agent harness。

## 是什么

每轮对话先读取仓库真实状态，再决定该走问答、方案、编码还是既有 PR 返工；代码与命令只允许在隔离沙箱中执行，事件通过 SSE 流式返回。仓库记忆、方案、PR 状态、历史记录和 token 账都持久化，刷新页面或重连后可以继续。

当前重点能力：

- 状态优先意图：无 open PR 时使用 `qa / plan / coding`，有 open PR 时使用 `qa / rework`。
- 三种主形态：`qa`、`plan`、`orchestrate`；coding/rework 共用 orchestrate，并挂 planner / coder / reviewer 三个子智能体。
- PR 感知：轮首读取最近 10 条 PR 用于记忆，另分页读取全部 open PR；PR 归属、方案关联和
  rework 工作区同步水位持久化在 `pull_requests` 表。
- 上下文工程：中英混合 token 估算、在线校准、持久化工具裁剪事件、到线摘要与完整历史 offload。
- 流式可观测：正文、reasoning、待办、工具、方案、token、文件 diff、handoff 内层 run 树。
- 本地工作台：聊天、代码、记忆、PR、日志、Token，以及整页设置。

## 总体架构

- **接口**：FastAPI + SSE。`POST /chat/{session_id}` 起一轮并订阅，`GET /chat/{session_id}/stream?after=<帧号>` 刷新后接回；运行与 HTTP 请求生命周期解耦，终止必须调用 cancel。
- **模型路由**：按智能体角色选择设置页里的模型行。主智能体不缓存，每轮现建，因此模型配置下一轮生效。
- **意图判定**：轮首先取 PR 快照和仓库记忆状态，再按候选集、口令、JEV 或大模型、关键字兜底依次判定。外部 / 归属未知的 open PR 在分类前直接停止；PR 状态不一致时再由纯函数闸门拦截写操作。
- **装配轴**：`agent/profiles.py` 决定主形态挂哪些工具、子智能体和提示词，避免多套分支漂移。
- **沙箱**：Docker + OpenSandbox（`opensandbox` SDK），每会话新建；镜像 `opensandbox/code-interpreter:v1.1.0`，沙箱根 `/ai_coding`（含 `project/`、`policies/`、`skills/`、`output/`、`review/`、`tmp/`）。沙箱需要的资产（askpass / setup.sh、policies、skills）集中在仓库 `sandbox/` 下，建会话时整棵推入。
- **右侧栏「代码」面板**（`infra/repo_cache.py`）：界面看代码**不经沙箱** —— 宿主机自己 `git clone --bare` 一份只读缓存（按 `owner/repo`，`REPO_VIEW_DIR` 默认 **`~/.ai-coding-harness/repo_cache`**，即用户个人目录，不写进项目目录），切分支只是换个 ref，读文件走 `git ls-tree` / `git cat-file`。沙箱没建好、已被回收、甚至建失败，代码照样看得到；令牌经环境里的 git 配置注入，不进 URL、也不落 `.git/config`。
- **代码平台**：Gitee（码云）与 GitHub。**平台由仓库地址判定**（`gitee.com` / `github.com`），PR 创建 / 评论 / 关闭走同一套 `create_pr` / `comment_pr` / `close_pr` 工具，内部按平台分流。令牌与 git 用户名在设置页「代码平台」那一栏配（`.env` 的同名键只是初始值），**建会话时快照进库**，之后改配置不影响已有会话。OAuth 登录已删（2026-10-20）。
- **安全**：危险命令 denylist（`rm -rf /`、`mkfs`、`dd` 写盘、`shutdown` 等）+ 按角色的边界中间件（越界工具直接不给）+ 凭据脱敏覆盖四条出口（工具返回值、模型输出、SSE、落库与日志）。
- **可观测**：日志记录会话 / 沙箱 / 智能体生命周期；trace 预留（LangSmith / OTel）。每次模型调用一行 `LLM 调用完成：输入 X / 输出 Y`（供应商真值，按执行主角归账）；上下文那两层各省了多少各有 grep 关键字 —— `CONTEXT_PRETRIM`（到压缩线后的应急裁剪：剪了几条、剪前剪后、免压还是继续压）与 `CONTEXT_SUMMARY`（压前压后 tokens、消息条数、摘要字数、逐出原文落盘路径）；两行同口径（含系统提示与工具 schema），可以直接对着看。压缩那次的摘要调用记成 `internal`（`摘要调用完成：…`）。
- **思考展示**：模型思考以 `reasoning` 帧单独下发，主控与子智能体按角色分段显示；它只存在于前端与 RunHub 的临时帧缓冲，**不落业务库**。流式期间正文按纯文本显示，任务结束后一次性 Markdown 化。
- **文件后端**：CompositeBackend 将普通文件、`/memories/`、`/conversation_history/`、`/large_tool_results/` 路由到沙箱或持久 StoreBackend。
- **持久化**：PostgreSQL 保存会话、仓库、方案、PR 映射、记录、澄清、模型配置和运行配置；LangGraph checkpoint 保存会话状态。
- **Redis（可选）**：多 worker 时保存运行帧缓冲、会话运行权与模型熔断状态；未配置或连接失败时退回进程内实现。

## 意图与形态

| 意图 | 形态 | 主要职责 |
|---|---|---|
| `qa` | `qa` | 只读问答、解释、排查、需求澄清，以及查询现有仓库/PR/文件/任务状态 |
| `plan` | `plan` | 方案讨论、方案落库、步骤设计；不写任务代码 |
| `coding` | `orchestrate` | 没有 open PR（或只有尚待收尾的 Agent PR）时实施新任务；普通代码走 planner → coder → reviewer，纯文档由 coder 直做并创建 PR |
| `rework` | `orchestrate` | 跟进已有 open PR：审查、回评、按用户要求或审查意见修改既有分支 |

约束：

- 有 open PR 时，`coding` / `plan` 会被状态闸门拦截，写操作只能走 `rework`。
- 非本 Harness 创建或归属无法确认的 open PR 会在意图分类前硬拦截，提示用户先处理；
  历史 PR 的归属通过一次 commit 作者核验后写入 `pull_requests`，之后不再重复猜。
- 例外：open PR **全部没有评论**时，说明 coder 可能已建 PR 但 reviewer 还没完成；
  此时 `coding` 继续可用，用于收尾同一任务；没有审查评论时 `rework` 暂不可用。
  任一 open PR 已有评论后，coding 才被拦回 `rework`。
- 没有 open PR 时不能进入 `rework`。
- `review` 不再是独立主控形态；reviewer 是 coding/rework 的内部子智能体。
- `qa` 和 `plan` 不写仓库记忆；仓库记忆正文只在 coding/rework 收尾写回。
- **方案执行状态自动恢复**：coding 主控、planner、coder 进入时会把上一轮遗留的
  `executing` 方案按 PR 状态收口；coder 正常结束也会兜底。存在 PR 时方案记为 `done`，
  没有 PR 时恢复 `pending`。用户终止或异常未执行 `after_agent` 时，由下一轮
  `before_agent` 修复。
- **纯文档直做**：任务只新增 / 修改 `AGENTS.md`、README、`docs/` 或其他 `.md` / `.mdx` /
  `.rst` / `.adoc`，且不涉及源码、测试、配置、依赖或构建文件时，coding 主控只维护任务清单并
  直接派 coder；不查询或创建方案，不派 planner，也不派 reviewer。
- coder 从 `docs/<主题>` 分支修改文档，校验链接、路径、命令与仓库事实后提交并创建 PR。
  若文档必须依赖代码改动才能成立，则停止直做并回到普通 coding 流程。

所有主形态都能使用联网工具和 `handoffTask`。handoff 的目标形态由服务端重新判定，不允许当前模型自行指定。

### 意图澄清弹窗

- 低置信度或必要业务口径采用分步弹窗：一页一个问题，点击选项后自动进入下一页。
- 第一步只负责确定 `qa / plan / coding / rework` 意图；后续步骤收集无法从仓库推断的业务口径。
- 最后一页展示全部选择，确认后通过 `answers[{step_id,choice}]` 一次提交；全选完成后才消费待澄清状态。
- qa 不挂 `write_todos`；模型伪造的待办调用会被事件翻译层丢弃。澄清记录不保存原始
  `<CLARIFY>`、过程旁白或伪待办，历史回放只展示最终问题。

## 中间件

装配点在 `agent/graph.py`、`agent/subagents.py`，主要能力如下：

| 模块 | 作用 |
|---|---|
| `BoundaryMiddleware` | 按角色隐藏越界工具，执行前再校验目录与命令 |
| `MemoryMiddleware` | 按形态加载、裁剪并注入仓库记忆 |
| `ReadGuardMiddleware` | policies ≤80 行、project ≤200 行；拦整文件读取命令 |
| `DelegationGateMiddleware` | 主控调用 `task` 前必须有本步骤对应的 `write_todos` 更新 |
| `LoopGuardMiddleware` | 同一工具同一参数重复调用时先劝、再中止 |
| `ToolCallIdMiddleware` | 补空 tool-call id；invalid tool call 带错误原因最多重试 3 次，仍失败则降级成正文 |
| `ToolErrorGuardMiddleware` | 工具异常转换成错误 ToolMessage，不掀翻整个 superstep |
| `HistoryRepairMiddleware` | 修复空 id、孤儿工具调用、旧 invalid content block |
| `IntentMiddleware` | 注入本轮形态、实际可用工具和澄清块；不允许模型沿用上一轮能力结论 |
| `RedactionMiddleware` | 工具返回与模型输出脱敏 |
| `HarnessSummarizationMiddleware` | 上下文裁剪事件、摘要、角色化触发线和历史 offload |
| `FallbackMiddleware` | 主模型失败时切兜底模型，并配合熔断 |
| `StreamUsageMiddleware` | 兼容不同网关的流式 token usage |

## 上下文工程

### 计数与触发

- token 估算按中英文实际比例混合：中文约 1.6 字符/token，代码/英文约 3.8。
- 每次真实模型调用后用供应商 usage 做在线 EMA 校准。
- 稳态角色：`qa`、coding/rework 主控；窗口 70% 或绝对 200k，先到先触发。
- 爆发角色：`plan` 与 `planner / coder / reviewer`；窗口 40% 或绝对 120k，先到先触发。

### 裁剪事件

裁剪不是常规中间件，也不会直接覆盖 checkpoint：

1. 到压缩线后先生成 `_context_trim_event`；
2. 事件只存稳定的 `tool_call_id`；
3. 本次请求动态拼接“摘要投影 + 裁剪投影”；
4. 原始 `messages` 保持不变；
5. 没有新的可裁项或仍高于安全区时直接摘要。

默认阈值：

- 工具结果小于 2000 字符不裁；
- 入参小于 2000 字符不压缩；
- 历史入参压缩默认关闭；开启后只压超大字符串字段，保留路径、offset、limit、timeout；
- 当前步骤、`KEEP` 点名项和 `KEEP_TOOLS` 永不进入裁剪事件。

### 摘要与恢复

- 摘要与 offload 始终读取裁剪前的原始消息。
- 被逐出的历史写到 `/conversation_history/{thread_id}.md`。
- 摘要消息里保留历史文件路径，模型需要时可以回读。
- 思考只实时展示，不写业务库，也不进入持久化上下文。

## PR、仓库记忆与文件变更

- 每轮查询最近 10 条全部状态 PR，并分页查询完整 open PR；不做跨轮接口缓存。
- `pull_requests` 保存 PR 编号、状态、作者、head/base 分支、远端 `head_sha`、已同步
  `synced_head_sha`、来源（`agent / legacy_agent / external / unknown`）和产出它的 `plan_id`。
- `create_pr` 在平台成功后立即写 PR 表；一个方案可以关联一个或多个替代 PR。
- rework 目标唯一或用户明确写出 `#N` 时，平台先把工作区安全快进到 PR 最新 `head_sha`。
  工作区脏、远端 force-push 造成分叉或 fetch 失败时直接停止，绝不覆盖用户提交。
- 新 PR 批量生成摘要后增量写入仓库记忆；已有 PR 只同步变化的状态。
- PR 同步入口只有两个：轮首记忆拦截点、coding/rework 轮末。
- `save_repo_memory(section, payload)` 只接受 `change / gaps / decisions / basic`，平台负责合并、去重、渲染和限长。
- coding/rework 若创建了 PR，本轮文件清单保存到本地代码缓存；刷新或沙箱销毁后仍可查看文件列表和按需 diff。

## Handoff 内嵌 Run 树

`handoffTask` 的内层运行与外层上下文隔离，但不再完全隐形：

- 内层模型正文、reasoning、工具、待办和生命周期通过 `harness-run` 事件回传；
- 每个节点带 `run_id / parent_run_id / root_run_id`；
- 前端在 handoff 工具卡内嵌展示，不与外层正文混排；
- reasoning 只实时展示，持久化时剔除；
- 刷新后从记录 `extra.parts` 重建；
- 文件日志带 `[run=... parent=...]`，日志面板默认按 run 父子树缩进。

内层 token 会合并进本轮总账，并在 `by_actor` 中保留 planner/coder/reviewer 等角色。

## 前端

启动页是 Vue 3 + Vite 聊天工作台：

- 输入框随内容增高，长历史向上滚动分页；
- 正文流式纯文本，结束后整体 Markdown 化；
- reasoning 与正文按到达顺序交错，结束后隐藏 reasoning；
- 待办树、方案、澄清弹窗、handoff 内嵌 run 树；
- 右侧栏：代码、记忆、PR、日志、Token；
- 代码面板支持目录、文件、分支切换和左右 diff；
- 日志面板支持关键字、级别、HTTP 噪声开关和 run 树视图；
- “本轮改动”只在本轮创建 PR 时出现。

设置页是整页工作台，入口在左栏品牌行的齿轮：

- 基本设置；
- 外观；
- 模型配置；
- 提供商目录。

基本设置与外观存本机 localStorage；模型配置和运行期参数存 PostgreSQL。

## 代码平台

支持 Gitee 与 GitHub，平台由仓库地址自动判定：

- PR 创建、评论、关闭、状态查询走统一接口，内部按平台分流；
- 不提供 OAuth 登录入口；
- 新会话使用 `.env` 中配置的 `GITEE_ACCESS_TOKEN` / `GITHUB_ACCESS_TOKEN`；
- token 通过 git askpass/环境变量注入，不写进 URL、不写进 `.git/config`。

## API

```text
POST   /sessions
       {"repo_url":"..."}
GET    /sessions?limit=50
GET    /sessions/{id}
DELETE /sessions/{id}
POST   /sessions/{id}/workspace
POST   /sessions/{id}/cancel

GET    /sessions/{id}/records?limit=20&before_id=<id>
GET    /sessions/{id}/clarification
GET    /sessions/{id}/plan

GET    /sessions/{id}/repo
POST   /sessions/{id}/repo/sync
GET    /sessions/{id}/repo/tree?ref=<branch>&path=<dir>
GET    /sessions/{id}/repo/file?ref=<branch>&path=<file>
GET    /sessions/{id}/repo/diff?base=<ref>&head=<ref>&path=<file>

GET    /sessions/{id}/memory
GET    /sessions/{id}/pulls
GET    /sessions/{id}/logs?limit=300&level=&q=&with_http=false
GET    /sessions/{id}/logs/download
GET    /sessions/{id}/usage

GET    /app-settings
PUT    /app-settings

GET    /model-config
PUT    /model-config
DELETE /model-config/{agent}
POST   /model-config/probe

GET    /model-config/providers
PUT    /model-config/providers/{provider}
GET    /model-config/providers/{provider}/models
PUT    /model-config/providers/{provider}/models
POST   /model-config/providers/{provider}/models/refresh
POST   /model-config/providers/reload

POST   /chat/{id}
       {"message":"..."}
       {"message":"...", "clarification_id":"...", "choice":0}
GET    /chat/{id}/stream?after=<frame>

GET    /health
```

意图由服务端自动判定，客户端不传 `task_type`。

## 主要目录

```text
src/ai_coding_harness/
├── api/                 # FastAPI、SSE、schemas、routes；model_config 按职责分包
├── server/              # 意图、业务编排、RunHub、PR 状态与记忆同步
│   └── turn/            # runner / intent / clarify / records / usage / changes mixin
├── agent/               # graph、llm、profiles、prompts、subagents、chat_model
│   └── middleware/      # 边界、上下文、记忆、工具完整性、循环守卫等
├── backends/            # OpenSandbox 后端与 CompositeBackend
├── db/                  # ORM、engine、按表拆分的 dao
├── infra/               # Redis、本地只读 repo_cache、熔断
├── observability/       # 日志、run log、日志筛选、tracing
├── sandbox/             # 沙箱供给、代理、卷
├── safety/              # 工具/目录策略、命令安全、脱敏
├── session/             # 会话管理与 checkpointer/store
└── tools/               # PR、方案、记忆、handoff、web、工具注册

frontend/src/
├── components/
│   ├── panels/          # 代码 / 记忆 / PR / 日志 / Token
│   ├── settings/        # 整页设置工作台
│   └── HandoffRun.vue
├── lib/                 # API、Markdown、流块、run 树、偏好
└── App.vue

sandbox/                 # 建会话时推入沙箱的 policies / skills / deploy 资产
docs/                    # 设计、目录权限、PR API、评测、backlog
alembic/                 # 数据库迁移
start.sh / start.bat     # 一键启动
```

## 环境与启动

推荐 Python 3.11+ 和 conda 环境 `aicoding`：

```bash
cp .env.example .env
pip install -e ".[dev]"
alembic upgrade head
npm --prefix frontend install
```

一键启动：

```bash
./start.sh
start.bat
```

分别启动：

```bash
python -m ai_coding_harness.main
npm --prefix frontend run dev
```

后端：`http://127.0.0.1:8000`

前端：`http://localhost:5173`

## 关键配置

### 代码平台

```env
GITEE_ACCESS_TOKEN=
GITEE_USERNAME=
GITHUB_ACCESS_TOKEN=
GITHUB_USERNAME=
```

不再使用 OAuth client id/secret，也不接受创建会话时临时传入 token。

### 模型

模型不在 `.env` 中配置。进入设置页「模型配置」，按 `qa / plan / coding / coder / review / unified / fallback / jev / classifier` 等行配置。没有“全仓默认”：

1. 先取该智能体自己的行；
2. 没配时再取“统一模型”；
3. 两边都没有则该角色本轮直接报错。

### 运行期参数

上下文告警线、默认分支、代码缓存路径等存 `app_settings`，通过设置页或 `/app-settings` 修改，立即生效。

### 沙箱与持久化

```env
OPENSANDBOX_*
SANDBOX_*
POSTGRES_*
REDIS_URL=
```

Redis 留空时使用进程内状态；生产多 worker 建议配置 Redis。

## 验证

```powershell
$env:PYTHONPATH='src'
conda run -n aicoding --no-capture-output python -m pytest -q

D:\python-venv\ai_coding\Scripts\ruff.exe check <changed-files>
python scripts/check_prompts.py
npm.cmd --prefix frontend run build
```

## 更多文档

- `CHANGELOG.md`：逐次改动、动机、验证与遗留。
- `docs/config-page-design.md`：设置页与模型配置设计。
- `docs/context-trim-event.md`：持久化裁剪事件。
- `docs/workspace-directories.md`：工作区目录与权限。
- `docs/code-platform-pr-api.md`：Gitee/GitHub PR API 差异。
- `docs/repo-memory-template.md`：仓库记忆模板。
