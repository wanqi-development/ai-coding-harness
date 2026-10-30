# ai-coding-harness

基于 [deepagents](https://github.com/langchain-ai/deepagents) 的本地优先、单部署多用户 coding agent harness。支持 WSL2 + SQLite 的 standalone 单 worker 部署，以及 PostgreSQL + Redis + OpenSandbox 的 multi 多 worker 部署。

## 是什么

每轮对话先读取仓库真实状态，再决定该走问答、方案、编码还是既有 PR 返工；代码与命令只允许在隔离沙箱中执行，事件通过 SSE 流式返回。仓库记忆、方案、PR 状态、历史记录和 token 账都持久化，刷新页面或重连后可以继续。

当前重点能力：

- 状态优先意图：无 open PR 时使用 `qa / plan / coding`，有 open PR 时使用 `qa / rework`。
- 三种主形态：`qa`、`plan`、`orchestrate`；coding/rework 共用 orchestrate，并挂 planner / coder / reviewer 三个子智能体。
- PR 感知：轮首读取最近 10 条 PR 用于记忆，另分页读取全部 open PR；PR 归属、方案关联和
  rework 工作区同步水位持久化在 `pull_requests` 表。
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
- **意图判定**：轮首先取 PR 快照和仓库记忆状态，再按候选集、口令、JEV 或大模型、关键字兜底依次判定。外部 / 归属未知的 open PR 在分类前直接停止；PR 状态不一致时再由纯函数闸门拦截写操作。
- **装配轴**：`agent/profiles.py` 决定主形态挂哪些工具、子智能体和提示词，避免多套分支漂移。
- **部署档位**：`standalone` 在 WSL2 内运行，使用单个 SQLite 文件、进程内运行状态和 bubblewrap 命令沙箱；`multi` 使用 PostgreSQL、Redis 与 OpenSandbox，可运行多个 worker。两套档位共用业务代码、工具和前端。
- **沙箱**：standalone 每会话在 WSL ext4 建立隔离目录，文件工具直接操作该目录，Shell 命令经 bubblewrap 限制挂载与 namespace；multi 使用 Docker + OpenSandbox，每会话新建镜像 `opensandbox/code-interpreter:v1.1.0`。两套 provider 都使用 `/ai_coding` 虚拟根与会话资产。
- **右侧栏「代码」面板**（`infra/repo_cache.py`）：界面看代码**不经沙箱** —— 宿主机的只读缓存按平台用户隔离，默认 **`~/.ai-coding-harness/users/<user_id>/repo_cache`**，每个用户可在基本设置里改自己的路径和默认分支；读文件走 `git ls-tree` / `git cat-file`。沙箱没建好、已被回收、甚至建失败，代码照样看得到；令牌经环境里的 git 配置注入，不进 URL、也不落 `.git/config`。
- **代码平台**：Gitee（码云）与 GitHub。**平台由仓库地址判定**（`gitee.com` / `github.com`），PR 创建 / 评论 / 关闭走同一套 `create_pr` / `comment_pr` / `close_pr` 工具，内部按平台分流。令牌与 git 用户名按用户在设置页「代码平台」配置；建会话前先用当前用户凭据验证仓库访问权，失败不落会话。用户换令牌时会重写自己的活跃会话，并在下一轮把新凭据同步进沙箱。平台登录使用本地账号密码。
- **认证与安全**：所有业务 API 先经过数据库-backed 登录认证，写请求校验 CSRF；业务会话按用户隔离。沙箱侧另有危险命令 denylist（`rm -rf /`、`mkfs`、`dd` 写盘、`shutdown` 等）+ 按角色的边界中间件（越界工具直接不给）+ 凭据脱敏覆盖四条出口（工具返回值、模型输出、SSE、落库与日志）。
- **可观测**：日志记录会话 / 沙箱 / 智能体生命周期；trace 预留（LangSmith / OTel）。每次模型调用一行 `LLM 调用完成：输入 X / 输出 Y`（供应商真值，按执行主角归账）；上下文那两层各省了多少各有 grep 关键字 —— `CONTEXT_PRETRIM`（到压缩线后的应急裁剪：剪了几条、剪前剪后、免压还是继续压）与 `CONTEXT_SUMMARY`（压前压后 tokens、消息条数、摘要字数、逐出原文落盘路径）；两行同口径（含系统提示与工具 schema），可以直接对着看。压缩那次的摘要调用记成 `internal`（`摘要调用完成：…`）。
- **思考展示**：模型思考以 `reasoning` 帧单独下发，主控与子智能体按角色分段显示；它只存在于前端与 RunHub 的临时帧缓冲，**不落业务库**。流式期间正文按纯文本显示，任务结束后一次性 Markdown 化。
- **文件后端**：CompositeBackend 将普通文件、`/memories/`、`/conversation_history/`、`/large_tool_results/` 路由到沙箱或持久 StoreBackend。
- **持久化**：standalone 用同一个 SQLite 文件保存业务表、LangGraph checkpoint 与 Store；multi 用 PostgreSQL 保存业务数据，并使用 PostgreSQL checkpoint/store。Session 使用 5 分钟版本化快照缓存。
- **Redis**：multi 必须配置 Redis，用于运行帧缓冲、会话运行权、模型熔断状态与加密 Session 快照，并要求 `REDIS_REQUIRED=true`；standalone 固定使用进程内运行状态，不连接 Redis。

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
  历史 PR 首次纳管时按 commit 作者核验归属，核验结果写入 `pull_requests`。
- 例外：open PR **全部没有评论**时，说明 coder 可能已建 PR 但 reviewer 还没完成；
  此时 `coding` 继续可用，用于收尾同一任务；没有审查评论时 `rework` 暂不可用。
  任一 open PR 已有评论后，coding 才被拦回 `rework`。
- 没有 open PR 时不能进入 `rework`。
- 当前没有独立 `review` 主形态；reviewer 是 coding/rework 的内部子智能体。
- `qa` 和 `plan` 不写仓库记忆；仓库记忆正文只在 coding/rework 收尾写回。
- **方案执行状态自动恢复**：coding 主控、planner、coder 进入时会把上一轮遗留的
  `executing` 方案按 PR 状态收口；coder 正常结束也会兜底。存在 PR 时方案记为 `done`，
  没有 PR 时恢复 `pending`。用户终止或异常未执行 `after_agent` 时，由下一轮
  `before_agent` 修复。
- **当前进度只保留一份**：`write_todos` 是当前清单快照，父任务被主控删除后其子清单
  同步消失；新方案落库会替代最新的 `pending/failed` 方案，旧方案标记为 `superseded`，
  不再出现在当前方案和聊天回放中。
- **本轮改动与耗时**：coding 按 `create_pr` 的 base/head、rework 按目标 PR 的 base/head
  在本地代码缓存里计算文件清单；执行耗时写入 assistant 记录，实时与刷新回放都显示同一份。
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
| `HistoryRepairMiddleware` | 修复历史消息中的空 id、孤儿工具调用与畸形内容块 |
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
- 代码面板支持目录、文件、分支切换和左右 diff；
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

multi 模式或手工部署推荐 Python 3.11+ 和 conda 环境 `aicoding`：

```bash
cp .env.example .env
pip install -e ".[dev]"
alembic upgrade head
python -m ai_coding_harness.manage create-admin --username admin --claim-existing
npm --prefix frontend install
```

一键启动：

```bash
bash scripts/linux/start.sh     # Linux
start.bat                       # Windows（也可直接运行 scripts\windows\start.bat）
```

WSL2 standalone（后端运行在 WSL，Windows 只负责启动和打开界面）：

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

分别启动：

```bash
# Windows
scripts\windows\start-backend.bat
scripts\windows\start-frontend.bat

# Linux
bash scripts/linux/start-backend.sh
bash scripts/linux/start-frontend.sh
```

Linux 脚本默认使用当前环境里的 `python3`；conda 环境未激活时可这样覆盖：

```bash
PYTHON=/path/to/aicoding/bin/python bash scripts/linux/start.sh
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

### 运行期参数

上下文告警线和意图澄清阈值等全局参数存 `app_settings`，仅管理员可改。
代码缓存路径、默认分支和模型配置按用户独立存储；提供商栏仅管理员可见，同时作为模型配置弹窗的只读目录。

### 部署档位

```env
DEPLOYMENT_MODE=standalone
SQLITE_PATH=/home/harness/.ai-coding-harness/harness.sqlite3
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
POSTGRES_*
POSTGRES_CONNECT_TIMEOUT_SECONDS=5
REDIS_URL=redis://127.0.0.1:6379/0
SESSION_CACHE_ENCRYPTION_KEY=
SESSION_CACHE_KEY_VERSION=1
REDIS_SESSION_CACHE_TTL_SECONDS=300
REDIS_REQUIRED=true
SANDBOX_PROVIDER=opensandbox
```

`POSTGRES_CONNECT_TIMEOUT_SECONDS` 控制建立数据库连接的最长等待时间，避免地址不可达时
CLI 或服务长时间无响应。

`SESSION_CACHE_ENCRYPTION_KEY` 接受 32 字节 base64 或 64 位十六进制。multi 启用 Redis
但没有有效密钥时服务直接启动失败，避免 token 明文进入 Redis 或多 worker 静默退回本地缓存。
可用 `python -m ai_coding_harness.manage generate-session-cache-key` 生成。

multi 模式必须同时具备 PostgreSQL、Redis 和 OpenSandbox，启动时会拒绝静默退回单 worker
或进程内状态。

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
- `docs/auth-design.md`：多用户、RBAC、多 worker 会话与外部 IdP 扩展边界。
- `docs/standalone-wsl.md`：WSL2 + SQLite standalone 的安装、目录、配置与安全边界。
- `docs/context-trim-event.md`：持久化裁剪事件。
- `docs/workspace-directories.md`：工作区目录与权限。
- `docs/code-platform-pr-api.md`：Gitee/GitHub PR API 差异。
- `docs/repo-memory-template.md`：仓库记忆模板。
