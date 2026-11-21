# ai-coding-harness

基于 [deepagents](https://github.com/langchain-ai/deepagents) 的本地优先、单部署多用户 coding agent harness。支持 WSL2 + SQLite 的 standalone 单 worker 部署，以及 PostgreSQL + Redis + OpenSandbox 的 multi 多 worker 部署。

## 是什么

每轮对话先读取仓库真实状态，再决定该走问答、方案还是编码；已有 PR 的审查、回评和修改都归编码形态。代码与命令只允许在隔离沙箱中执行，事件通过 SSE 流式返回。仓库记忆、方案、代码任务、PR 状态、历史记录和 token 账都持久化，刷新页面或重连后可以继续。

当前重点能力：

- 意图只负责识别 `qa / plan / coding`；当前 PR 状态不再改写意图，也不再当写操作闸门。
- 三种主形态：`qa`、`plan`、`orchestrate`；coding 挂 planner / coder / reviewer 三个子智能体。
- 代码任务持久化：每次开始写代码前由主控建立或绑定 PG 中的 work task；同一任务的 coder
  提交、累计摘要、分支与 PR 都归在同一条任务下。明显的新需求必须等当前任务创建并合并 PR
  后再开始。
- PR 感知：轮首读取最近 10 条 PR 用于记忆，另分页读取全部 open PR；PR 归属、方案关联和
  工作区同步水位持久化在 `pull_requests` 表。
- 版本化审查：reviewer 的结论独立落库并绑定目标分支 + 工作区 diff 指纹；审查只向用户
  提供 P0-P3 与修改建议，不阻断创建 PR。
- 上下文工程：中英混合 token 估算、在线校准、持久化工具裁剪事件、到线摘要与完整历史 offload。
- 流式可观测：正文、reasoning、待办、工具、方案、token、文件 diff、handoff 内层 run 树。
- Markdown 图表：完整回复里的 Mermaid 流程图与状态图渲染为可缩放 SVG，支持源码、复制、
  下载和全屏查看。
- 本地工作台：聊天、代码、记忆、PR、日志、Token，以及整页设置。
- 多用户与 RBAC：管理员维护账号与角色；数据库-backed 不透明会话、HttpOnly Cookie、
  CSRF、强制首次改密和会话归属隔离。

## 总体架构

- **接口**：FastAPI + SSE。`POST /chat/{session_id}` 起一轮并订阅，`GET /chat/{session_id}/stream?after=<帧号>` 刷新后接回；运行与 HTTP 请求生命周期解耦，终止必须调用 cancel。
- **模型路由**：按当前用户和智能体角色选择设置页里的模型行。主智能体不缓存，每轮现建，因此模型配置下一轮生效。
- **意图判定**：轮首先取 PR 快照和仓库记忆索引，再按强口令、JEV 或大模型识别意图。两关都失败或低置信度时进入 qa 意图澄清，不再用弱关键字或默认值猜意图。
- **仓库记忆**：主索引只保留基本信息、语义目录、压缩架构图和记忆目录；功能流程图、时序图、
  接口/数据契约和历史放在按需加载的子文档中。主索引每轮注入，详情由智能体按路径读取。
  主索引路径统一为 `/memories/<platform>/<owner>/<repo>/index.md`。
- **装配轴**：`agent/profiles.py` 决定主形态挂哪些工具、子智能体和提示词，避免多套分支漂移。
- **部署档位**：`standalone` 在 WSL2 内运行，使用单个 SQLite 文件、进程内运行状态和 bubblewrap 命令沙箱；`multi` 使用 PostgreSQL、Redis 与 OpenSandbox，可运行多个 worker。两套档位共用业务代码、工具和前端。
- **执行进程**：默认 `RUN_EXECUTION_MODE=inline`，单机保持原有单进程行为。需要把 SSE 网关和 Agent 计算分开时使用 `RUN_EXECUTION_MODE=distributed`：ASGI 提交 Redis Stream job 并转发事件，独立 Agent worker 执行 LangGraph、模型、工具与沙箱命令；Gateway 仍保留工作区准备/重连接口。详见 [docs/asgi-worker-split.md](docs/asgi-worker-split.md)。
- **沙箱**：standalone 每会话在 WSL ext4 建立隔离目录，文件工具直接操作该目录，Shell 命令经 bubblewrap 限制挂载与 namespace；multi 使用 Docker + OpenSandbox，每会话新建镜像 `opensandbox/code-interpreter:v1.1.0`。两套 provider 都使用 `/ai_coding` 虚拟根与会话资产。
- **右侧栏「代码」面板**（`infra/repo_cache.py`）：界面看代码**不经沙箱** —— 宿主机的只读缓存按平台用户隔离，默认 **`~/.ai-coding-harness/users/<user_id>/repo_cache`**，每个用户可在基本设置里改自己的路径和默认分支；读文件走 `git ls-tree` / `git cat-file`。沙箱没建好、已被回收、甚至建失败，代码照样看得到；令牌经环境里的 git 配置注入，不进 URL、也不落 `.git/config`。
- **代码平台**：Gitee（码云）与 GitHub。**平台由仓库地址判定**（`gitee.com` / `github.com`），PR 创建 / 评论 / 关闭走同一套 `create_pr` / `comment_pr` / `close_pr` 工具，内部按平台分流。令牌与 git 用户名按用户在设置页「代码平台」配置；建会话前先用当前用户凭据验证仓库访问权，失败不落会话。用户换令牌时会重写自己的活跃会话，并在下一轮把新凭据同步进沙箱。平台登录使用本地账号密码。
- **认证与安全**：所有业务 API 先经过数据库-backed 登录认证，写请求校验 CSRF；业务会话按用户隔离。沙箱侧另有危险命令 denylist（`rm -rf /`、`mkfs`、`dd` 写盘、`shutdown` 等）+ 按角色的边界中间件（越界工具直接不给）+ 凭据脱敏覆盖四条出口（工具返回值、模型输出、SSE、落库与日志）。
- **Git 中断审批**：`execute` 中的 Git 命令先规范化再分为 allow / approval / deny。普通开发命令直接执行；`reset --hard`、指定文件 restore/checkout、clean、history rewrite、force-with-lease 等暂停当前 run，前端弹出单项选择器；用户选择后同一 thread、同一 run 原地继续，不创建新的用户轮次。`filter-repo`、reflog expire、`gc --prune=now`、mirror push、受保护分支强推/删除等直接拒绝。审批前的 staged/unstaged patch、status、HEAD 和 clean dry-run 保存到 `.git/harness-recovery/`。
- **可观测**：日志记录会话 / 沙箱 / 智能体生命周期；trace 预留（LangSmith / OTel）。每次模型调用一行 `LLM 调用完成：输入 X / 输出 Y`（供应商真值，按执行主角归账）；上下文那两层各省了多少各有 grep 关键字 —— `CONTEXT_PRETRIM`（到压缩线后的应急裁剪：剪了几条、剪前剪后、免压还是继续压）与 `CONTEXT_SUMMARY`（压前压后 tokens、消息条数、摘要字数、逐出原文落盘路径）；两行同口径（含系统提示与工具 schema），可以直接对着看。压缩那次的摘要调用记成 `internal`（`摘要调用完成：…`）。
- **思考展示**：模型思考以 `reasoning` 帧单独下发，主控与子智能体按角色分段显示；它用于当轮前端与 RunHub 帧缓冲，**不进入后续模型请求，也不落业务表**。流式期间正文按纯文本显示，任务结束后一次性 Markdown 化。
- **文件后端**：CompositeBackend 将普通文件、`/memories/`、`/conversation_history/`、`/large_tool_results/` 路由到沙箱或持久 StoreBackend。
- **持久化**：standalone 用同一个 SQLite 文件保存业务表、LangGraph checkpoint 与 Store；multi 用 PostgreSQL 保存业务数据，并使用 PostgreSQL checkpoint/store。Session 使用 5 分钟版本化快照缓存。
- **Redis**：multi 必须配置 Redis，用于运行帧缓冲、会话运行权、模型熔断状态与加密 Session 快照，并要求 `REDIS_REQUIRED=true`；standalone 固定使用进程内运行状态，不连接 Redis。

## 意图与形态

| 意图 | 形态 | 主要职责 |
|---|---|---|
| `qa` | `qa` | 问答、解释、排查、需求澄清；可受控 clone/fast-forward 代码、沉淀代码验证过的分析记忆 |
| `plan` | `plan` | 方案讨论、方案落库、步骤设计；不写任务代码 |
| `coding` | `orchestrate` | 新任务实施与既有 PR 跟进：按当前 work task 决定派 planner、coder 或 reviewer；纯文档可跳过 planner；PR 仅在用户最后授权后由主控创建 |

约束：

- 分类阶段始终看全部真实意图，不按 PR 状态收窄候选。`qa` 在任何 PR 状态下都能执行。
- 写代码前必须由主控调用 `openWorkTask(relation=same|new|unclear)`。已有 active task 时，
  同一需求用 `same`；明显的新需求用 `new`，工具会拒绝并提示先完成当前任务、创建并合并 PR。
- 非本 Harness 登记的 open PR 不自动阻塞编码；它们只作为仓库快照提示。真正是否冲突看当前
  work task 与用户这轮需求。
- 当前没有独立 `review` 主形态；reviewer 是 coding 的内部子智能体。
- `qa` 可写经过代码验证的分析文档；`plan`、planner、coder、reviewer 只读。
  coding 主控还可写当前缺口和长期约束。
- **方案执行状态自动恢复**：coding 主控、planner、coder 进入时会把上一轮遗留的
  `executing` 方案按 PR 状态收口；coder 正常结束也会兜底。存在 PR 时方案记为 `done`，
  没有 PR 时恢复 `pending`。用户终止或异常未执行 `after_agent` 时，由下一轮
  `before_agent` 修复。
- **当前进度只保留一份**：`write_todos` 是当前清单快照，父任务被主控删除后其子清单
  同步消失；新方案落库会替代最新的 `pending/failed` 方案，旧方案标记为 `superseded`，
  不再出现在当前方案和聊天回放中。
- **本轮改动与耗时**：新交付按 `create_pr` 的 base/head、已有 PR 按目标 PR 的 base/head
  在本地代码缓存里计算文件清单；执行耗时写入 assistant 记录，实时与刷新回放都显示同一份。
- **Coder 与主控分工**：coder 负责修改、验证、commit、push，并回报 `task_id`、commit/head、
  branch/base、本次摘要、changed_files 与验证结果；主控用 `recordTaskSubmission` 保存不可变
  submission，并把本次摘要合并到任务累计摘要。`create_pr` 只在主控手里。
- **本地审查与交付**：reviewer 调用 `save_review` 持久化审查；review 是建议，不是 PR 门禁。
  主控结合当前 work task、完整对话交付目标和最近结果判断是继续修改还是交付 PR；
  目标明确后从 work task 读取 branch/base 创建或复用 PR，随后 reviewer 可对最终 PR Diff
  再做一次 `stage=pr` 审查。合并仍由用户手工完成。
- **纯文档直做**：任务只新增 / 修改 `AGENTS.md`、README、`docs/` 或其他 `.md` / `.mdx` /
  `.rst` / `.adoc`，且不涉及源码、测试、配置、依赖或构建文件时，coding 主控只维护任务清单并
  直接派 coder；不查询或创建方案，不派 planner，但仍派 reviewer 保存本地审查。
- coder 从 `docs/<主题>` 分支修改文档，校验链接、路径、命令与仓库事实；主控按当前交付目标
  决定是否创建 PR。
  若文档必须依赖代码改动才能成立，则停止直做并回到普通 coding 流程。

所有主形态都能使用联网工具和 `handoffTask`。handoff 的目标形态由服务端重新判定，不允许当前模型自行指定。

### 意图澄清弹窗

- 低置信度或必要业务口径采用分步弹窗：一页一个问题，点击选项后自动进入下一页。
- 第一步只负责确定 `qa / plan / coding` 意图；后续步骤收集无法从仓库推断的业务口径。
- 最后一页展示全部选择，确认后通过 `answers[{step_id,choice}]` 一次提交；全选完成后才消费待澄清状态。
- qa 不挂 `write_todos`；模型伪造的待办调用会被事件翻译层丢弃。澄清记录不保存原始
  `<CLARIFY>`、过程旁白或伪待办，历史回放只展示最终问题。

## 中间件

装配点在 `agent/graph.py`、`agent/subagents.py`，主要能力如下：

| 模块 | 作用 |
|---|---|
| `BoundaryMiddleware` | 按角色隐藏越界工具，执行前再校验目录与命令 |
| `MemoryMiddleware` | 加载并注入小型仓库记忆主索引；详情按需读取 |
| `ReadGuardMiddleware` | policies ≤80 行、project ≤200 行；拦整文件读取命令 |
| `DelegationGateMiddleware` | 主控调用 `task` 前必须有本步骤对应的 `write_todos` 更新 |
| `LoopGuardMiddleware` | 同一工具同一参数重复调用时先劝、再中止 |
| `ToolCallIdMiddleware` | 补空 tool-call id；invalid tool call 带错误原因最多重试 3 次，仍失败则降级成正文 |
| `ToolErrorGuardMiddleware` | 工具异常转换成错误 ToolMessage，不掀翻整个 superstep |
| `HistoryRepairMiddleware` | 修复历史消息中的空 id、孤儿工具调用与畸形内容块 |
| `IntentMiddleware` | 注入当前代码任务、PR 快照与澄清块；不注入抽象意图标签 |
| `RedactionMiddleware` | 工具返回与模型输出脱敏 |
| `HarnessSummarizationMiddleware` | 上下文裁剪事件、摘要、角色化触发线和历史 offload |
| `FallbackMiddleware` | 主模型失败时切兜底模型，并配合熔断 |
| `StreamUsageMiddleware` | 兼容不同网关的流式 token usage |

## 上下文工程

### 计数与触发

- token 估算按中英文实际比例混合：中文约 1.6 字符/token，代码/英文约 3.8。
- 每次真实模型调用后用供应商 usage 做在线 EMA 校准。
- 稳态角色：`qa` 与 coding 主控；窗口 70% 或绝对 200k，先到先触发。
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
- 思考只用于当轮展示，不写业务表；LangGraph checkpoint 可保留原始消息供审计，但每次
  发给模型前都会剥离 reasoning / thinking 历史。

## PR、仓库记忆与文件变更

- 每轮查询最近 10 条全部状态 PR，并分页查询完整 open PR；不做跨轮接口缓存。
- `pull_requests` 保存 PR 编号、状态、作者、head/base 分支、远端 `head_sha`、已同步
  `synced_head_sha`、来源（`agent / legacy_agent / external / unknown`）和产出它的 `plan_id`。
- `create_pr` 在平台成功后立即写 PR 表；一个方案可以关联一个或多个替代 PR。
- 当前 work task 已关联 PR，或用户明确写出 `#N` 时，平台先把工作区安全快进到 PR 最新 `head_sha`。
  工作区脏、远端 force-push 造成分叉或 fetch 失败时直接停止，绝不覆盖用户提交。
- 新 PR 批量生成摘要后增量写入 `history/changes.md`；已有 PR 只同步变化的状态，并更新主索引头。
- PR 同步入口只有两个：轮首记忆拦截点、coding 轮末。
- `save_repo_memory(kind, slug, content, ...)` 按一份语义文档写入；支持 profile、目录、
  架构、领域、功能、契约、运维、缺口、风险和 ADR。`gaps` / `risks` 必须带代码路径或
  验证提交作为证据，平台自动更新主索引和领域功能索引。
- `read_repo_memory(path)` 按主索引路径加载一份文档。主索引已常驻上下文，不提供重复的搜索工具。
- coding 若创建或更新 PR，本轮文件清单保存到本地代码缓存；刷新或沙箱销毁后仍可查看文件列表和按需 diff。

## Handoff 内嵌 Run 树

`handoffTask` 的内层运行与外层上下文隔离，内层过程以内嵌 Run 树展示：

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
- 代码面板支持目录、文件、分支切换和左右 diff；“本轮改动”使用左侧文件树 + 右侧左右
  Diff，目录可逐级或一键展开/折叠，文件树本身也可收起；
- 日志面板支持关键字、级别、HTTP 噪声开关和 run 树视图；
- “本轮改动”只在本轮创建 PR 时出现。

设置页是整页工作台，入口在左栏左下角的设置图标；同一行左侧的用户名点击后展开账号详情，
不会直接退出。详情显示显示名、用户名、角色、用户 ID、账号时间与当前登录会话信息，
并提供“修改密码 / 退出登录”两个操作：

- 基本设置；
- 外观；
- 模型配置；
- 提供商目录（仅管理员）。

基本设置与外观存本机 localStorage；模型配置和运行期参数存 PostgreSQL。

## 代码平台

支持 Gitee 与 GitHub，平台由仓库地址自动判定：

- PR 创建、评论、关闭、状态查询走统一接口，内部按平台分流；
- 不提供 OAuth 登录入口；
- 每个用户在「基本设置 → 代码平台」维护自己的 token 与 git 用户名，不跨用户兜底；
- 创建会话前先校验当前用户是否有仓库访问权，无权限或仓库不存在时弹窗提示；
- token 更新会同步该用户的活跃会话，当前轮保持旧凭据，下一轮刷新沙箱凭据；
- token 通过 git askpass/环境变量注入，不写进 URL、不写进 `.git/config`。

## API

```text
GET    /auth/config
POST   /auth/login
GET    /auth/me
GET    /me/profile
POST   /auth/logout
POST   /auth/change-password

GET    /me/settings
PUT    /me/settings

GET    /users
POST   /users
PATCH  /users/{id}
POST   /users/{id}/reset-password
POST   /users/{id}/revoke-sessions

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

权限边界：`/users`、`/app-settings` 和提供商目录写接口仅管理员；`/model-config`
按当前用户读写自己的模型行；`/me/settings` 维护当前用户自己的缓存路径和默认分支。

## 主要目录

```text
src/ai_coding_harness/
├── api/                 # FastAPI、SSE、认证与业务路由；model_config 按职责分包
├── auth/                # 密码、Opaque Token、认证中间件与数据库会话
├── server/              # 意图、业务编排、RunHub、PR 状态与记忆同步
│   └── turn/            # runner / intent / clarify / records / usage / changes mixin
├── agent/               # graph、llm、profiles、prompts、subagents、chat_model
│   └── middleware/      # 边界、上下文、记忆、工具完整性、循环守卫等
├── backends/            # 沙箱文件 / Shell 后端与 CompositeBackend
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
docs/                    # 设计、目录权限、PR API、认证与评测
alembic/                 # 数据库迁移
scripts/windows/         # Windows 合并 / 前后端独立启动脚本
scripts/linux/           # Linux 合并 / 前后端独立启动脚本
scripts/wsl/             # WSL 独立版初始化、工具链检查与后端启动
start.bat                # Windows 兼容入口
```

## 环境与启动

本地开发使用 uv，并固定使用仓库根目录的项目环境：

- Python：`.python-version` 固定为 3.14，解释器只取 `.venv`；不会回落到 conda、系统
  Python 或其他命名环境。
- Node：版本 20+，`node` 和 `npm` 必须可从 `PATH` 直接执行。
- 依赖事实源：`pyproject.toml + uv.lock`；`.venv` 和 `node_modules` 都不提交。

需要预先安装 [uv](https://docs.astral.sh/uv/) 和 Node.js 20+。先在仓库根目录同步
Python 环境并初始化数据库：

```bash
uv sync --extra dev
uv run alembic upgrade head
uv run python -m ai_coding_harness.manage create-admin --username admin --claim-existing
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
node --version
npm --version
npm --prefix frontend install
.\start.bat --check-env
.\start.bat
```

Linux：

```bash
cp .env.example .env
node --version
npm --version
npm --prefix frontend install
bash scripts/linux/start.sh
```

启动器只调用 `.venv\Scripts\python.exe`（Windows）或 `.venv/bin/python`
（Linux/macOS）。缺少 `.venv`、项目依赖、Node 或 npm 时，会停止在预检阶段并
打印修复命令，不会启动一个依赖错误环境的半成品服务。

Windows 的 `start.bat` 是启动监督器，而不是简单串联命令：

- 后端/前端作为受监控子进程运行，监督器、预检与失败时的服务输出摘要统一写入
  `logs/launcher.log`，每行包含时间与级别；
- 每次启动覆盖重建 `launcher.log`，不再为每次启动创建时间戳目录或多份子日志；
- 任一进程提前退出会立即终止本次启动并返回失败，不再盲目等待健康探测超时；
- 只有两个服务都健康才打开浏览器；
- 双击启动时窗口保留最终状态并等待按键；运行 `stop.bat` 可停止本次服务。
- `stop.bat` 会先请求后端优雅退出，等待运行任务收尾并保存工作区 recovery 快照；超时后才
  强制结束进程。coding 重启时若工作区仍在目标 PR head，会保留未提交改动并继续执行。

`.venv` 是生成目录，不提交到 Git。若在普通终端中 `--check-env` 报项目 Python 无法
加载 `ssl`，先确认底层 CPython 安装完整；修复解释器后删除 `.venv`，再执行
`uv sync --extra dev`。某些代码代理或第三方进程沙箱可能拦截工作区内解释器加载外部
DLL，这种环境差异不等同于解释器损坏，应直接在普通终端运行 `start.bat` 验证。

若主日志文件 `logs/api.log` 或 `logs/agent.log` 被旧进程/扫描器短暂占用，后端会降级到
`api.<pid>.log` / `agent.<pid>.log` 并在控制台告警，不会因为日志 sink 不可用而退出。

WSL2 standalone（后端运行在 WSL，Windows 只负责启动和打开界面）：

standalone 使用自己的固定 WSL venv，不复用开发机的 `.venv`；Python 支持 3.11+。
Windows 前端通过 `PATH` 中的 `node` / `npm` 启动。

```text
双击根目录 start-standalone.bat
```

也可以从终端执行：

```powershell
.\start-standalone.bat
```

入口会自动选择已安装的 Ubuntu WSL 发行版，并在首次运行时完成工具链初始化、管理员创建与
Windows 前端依赖安装。需要指定发行版时设置 `WSL_DISTRO` 或传入
`-Distro <发行版名称>`。

停止 standalone 前后端：

```text
双击根目录 stop-standalone.bat
```

或执行 `.\stop-standalone.bat`。直接关闭启动窗口不会可靠终止后台子进程，应使用 stop 入口。

standalone 首次没有用户时会自动创建 `admin`，随机密码同时显示在控制台并写入
`~/.ai-coding-harness/logs/initial-admin.txt`（权限 `0600`）。首次登录后必须修改密码。

standalone 的持久数据默认放在 Windows，而不是 WSL 内部：

```text
%LOCALAPPDATA%\ai-coding-harness\
├── data\harness.sqlite3
├── logs\
├── credentials\
└── backups\
```

可用绝对 Windows 路径覆盖：

```powershell
$env:HARNESS_HOST_HOME = "D:\ai-coding-harness-runtime"
.\start-standalone.bat
```

启动完成后控制台会打印实际 Data/Logs 路径。backend/frontend stdout、launcher、
api.log、agent.log 都落在同一个 Windows 日志目录。WSL 内部只保留需要 Linux 权限和
文件系统语义的临时 sandbox 会话。

分别启动：

```bash
# Windows
scripts\windows\start-backend.bat
scripts\windows\start-frontend.bat

# Linux
bash scripts/linux/start-backend.sh
bash scripts/linux/start-frontend.sh
```

Linux 脚本固定使用仓库 `.venv/bin/python`，Node 通过 `PATH` 中的 `npm` 启动：

```bash
bash scripts/linux/start.sh
```

后端：`http://127.0.0.1:8000`

前端：`http://localhost:5173`

multi 模式必须由运维人员先执行迁移并交互式创建管理员；已有历史会话可通过
`--claim-existing` 归给该账号。之后由管理员在设置页创建其他用户。multi 不会自动生成
密码，也不会把管理员凭据写入日志。详细流程见 `docs/auth-design.md`。

## 关键配置

### 登录认证

```env
AUTH_SESSION_COOKIE=harness_session
AUTH_CSRF_COOKIE=harness_csrf
AUTH_COOKIE_SECURE=false
AUTH_SESSION_TTL_HOURS=168
AUTH_IDLE_TIMEOUT_HOURS=24
AUTH_LOGIN_MAX_FAILURES=5
AUTH_LOGIN_LOCK_SECONDS=60
```

登录会话存业务数据库；multi 多 worker 不需要 sticky session。HTTPS 部署必须设置
`AUTH_COOKIE_SECURE=true`。

### 代码平台

代码平台认证使用用户级 access token，不从 `.env` 提供全局 token。进入「基本设置 →
代码平台」为当前账号配置 Gitee / GitHub token 和 git 用户名；打开设置页时 token
只回掩码，保存后不再回显，清空输入框再保存即删除。

### 模型

模型不在 `.env` 中配置。进入设置页「模型配置」，按 `qa / plan / coding / coder / review / unified / fallback / jev / classifier` 等行配置。没有“全仓默认”：

1. 先取该智能体自己的行；
2. 没配时再取“统一模型”；
3. 两边都没有则该角色本轮直接报错。

如果同一用户已经在**同一供应商、同一 `base_url`** 的另一行配置过 API Key，api_key
输入框会直接显示已保存 Key 的掩码，不需要重新粘贴；输入新 Key 会覆盖，留空保存时由后端
把同端点已有 Key 复制到当前行。

模型接入按**协议 Adapter** 分派，而不是把所有厂商都当成 OpenAI：

- `openai_compatible`：DeepSeek、GLM、Kimi、Qwen、自研兼容网关等，使用
  `ReasoningContentChatOpenAI` 提取第三方 `reasoning_content` 供当轮展示；历史思考
  不写回请求；
- `anthropic_messages`：Anthropic 原生 Messages API，使用 `ChatAnthropic` 和原生
  thinking content block 展示当轮思考，历史 thinking block 同样不回传；
- 主模型、兜底模型和意图分类共用 `build_chat_model()` 入口；分类固定关闭思考。

历史 reasoning 在模型请求副本中统一剥离，checkpoint 原文保持不变。这样切换模型或协议时
不会把另一家模型无法解释的思考块、签名或协议 ID 带进新请求。

管理员在「提供商」里配置该家的运行时协议、模型清单地址和额外请求头。模型行会保存实际选择
的 provider；旧数据没有该字段时仍按 `base_url` 反推，升级后重新保存即可写入显式值。

### 运行期参数

上下文告警线和意图澄清阈值等全局参数存 `app_settings`，仅管理员可改。
代码缓存路径、默认分支和模型配置按用户独立存储；提供商栏仅管理员可见，同时作为模型配置弹窗的只读目录。

### 部署档位

```env
DEPLOYMENT_MODE=standalone
# 手工在 WSL 内启动时才需要；Windows 启动器会自动注入 /mnt/... 路径
SQLITE_PATH=/mnt/d/ai-coding-harness-runtime/data/harness.sqlite3
SQLITE_BACKUP_DIR=/mnt/d/ai-coding-harness-runtime/backups
SANDBOX_PROVIDER=wsl
WSL_DISTRO=ai-coding-harness
WSL_USER=harness
WSL_ROOT=/home/harness/.ai-coding-harness/sessions
WSL_BWRAP=/usr/bin/bwrap
WSL_ALLOW_NETWORK=true
```

standalone 只支持单 worker，业务表、checkpoint 与 Store 共用一个 SQLite 文件；Windows
不能直接运行该档位的后端。首次安装、目录布局与排错见
[`docs/standalone-wsl.md`](docs/standalone-wsl.md)。

multi 部署：

```env
DEPLOYMENT_MODE=multi
OPENSANDBOX_*
SANDBOX_*
GIT_PROTECTED_BRANCHES
POSTGRES_*
POSTGRES_CONNECT_TIMEOUT_SECONDS=5
REDIS_URL=redis://127.0.0.1:6379/0
SESSION_CACHE_ENCRYPTION_KEY=
SESSION_CACHE_KEY_VERSION=1
REDIS_SESSION_CACHE_TTL_SECONDS=300
REDIS_REQUIRED=true
SANDBOX_PROVIDER=opensandbox
```

`REDIS_REQUIRED` 是 fail-closed 开关：存量部署只配置 `REDIS_URL` 仍可启动；生产多
worker 建议设为 `true`，避免 Redis 故障时静默退回进程内状态。

`POSTGRES_CONNECT_TIMEOUT_SECONDS` 控制建立数据库连接的最长等待时间，避免地址不可达时
CLI 或服务长时间无响应。

`SESSION_CACHE_ENCRYPTION_KEY` 接受 32 字节 base64 或 64 位十六进制。multi 启用 Redis
但没有有效密钥时服务直接启动失败，避免 token 明文进入 Redis 或多 worker 静默退回本地缓存。
可用 `uv run python -m ai_coding_harness.manage generate-session-cache-key` 生成。

multi 模式必须同时具备 PostgreSQL、Redis 和 OpenSandbox。设置
`REDIS_REQUIRED=true` 后，Redis 故障会直接阻止启动。

## 验证

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check <changed-files>
.\.venv\Scripts\python scripts/check_prompts.py
npm --prefix frontend run build
```

## 更多文档

- `CHANGELOG.md`：逐次改动、动机、验证与遗留。
- `docs/config-page-design.md`：设置页与模型配置设计。
- `docs/auth-design.md`：多用户、RBAC、多 worker 会话与外部 IdP 扩展边界。
- `docs/standalone-wsl.md`：WSL2 + SQLite standalone 的安装、目录、配置与安全边界。
- `docs/context-trim-event.md`：持久化裁剪事件。
- `docs/workspace-directories.md`：工作区目录与权限。
- `docs/code-platform-pr-api.md`：Gitee/GitHub PR API 差异。
- `docs/repo-memory-template.md`：仓库记忆模板。
