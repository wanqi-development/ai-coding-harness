# ai-coding-harness

基于 [deepagents](https://github.com/langchain-ai/deepagents) + [OpenSandbox](https://open-sandbox.ai/) 的 coding 辅助 harness 智能体。

## 是什么

单用户 HTTP 服务。每轮先按仓库 PR 状态收口候选，再判定**意图**（`qa` / `plan` / `coding` / `rework`），由意图映射到**形态**取对应装配；文件与命令操作全部在隔离的 OpenSandbox 沙箱内执行，结果经 SSE 流式返回（支持断线续传与中途终止）。

## 架构

- **接口**：FastAPI，SSE 流式。一轮对话分两段：`POST /chat/{id}` 起一轮并就地订阅，`GET /chat/{id}/stream` 供刷新后接回（帧带自增 id 作游标）。暂无鉴权。
- **LLM**：按**角色 → 设置页里的一行**选模型（映射只有一份：`agent/llm.py::_AGENT_KEY`，行清单见 `config.MODEL_AGENT_ROWS`）。**没有 `.env` 出厂默认**（2026-10-15 起）：一行没配，用到这个角色的那一轮就当场报错并指认是哪一行。解析顺序 **① 该行的配置 → ②「统一模型」那一行（只兜底没配的行）**。全部走 OpenAI 兼容协议（`langchain-openai`）。主模型重试耗尽时切设置页「兜底模型」那一行；连续失败到阈值开**熔断**，冷却期内不再撞主导。
- **意图判定**（`server/intent.py`）：每轮只查一次全量 PR，不做跨轮接口缓存。轮首同步仓库记忆：比记忆水印新的 PR 批量摘要一次并落盘，已有 PR 只修正不一致的状态；随后按 open PR 收口候选（无 open PR：`qa / plan / coding`，有 open PR：`qa / rework`，查询失败：`qa`）。再走口令 → **JEV 或 大模型（严格二选一）** → 关键字口径和函数闸门。
- **形态**（`agent/profiles.py`）：**唯一的装配轴**，一处定下挂哪些子智能体、给哪些主工具、用哪套角色提示词。三种形态：`qa`（只读问答 / 解释 / 排查）、`plan`（自己出方案并落库）、`orchestrate`（唯一挂 planner / coder / reviewer 的形态）。`coding` 与 `rework` 共用 `orchestrate`，差别由逐轮意图指令决定。
- **中间件**（`agent/middleware/` 与 `agent/summarization.py`，装配点在 `agent/graph.py` 与 `agent/subagents.py`，逐角色各建实例）：

| 模块 | 作用 |
|---|---|
| `context_inject` | 每次模型调用前注入**本轮意图**与澄清块；能力差异由基础提示词的通用规则约束（历史轮次的工具可用性不作数） |
| `redaction` | 凭据脱敏的两条出口：工具返回值、模型输出 |
| `tool_integrity` | 工具调用配对自洽：空 id 归一、invalid call 最多重试 3 次后降级为正文、工具异常兜底、历史整表修复 |
| `boundary` | 按智能体的活动范围：从可见工具里摘掉越界的，执行前再判一次 |
| `read_guard` | 读文件红线：policies ≤80 行、project ≤200 行；`cat` 等整文件命令直接拦 |
| `guard` | 本轮**空转刹车**：同一工具同一参数反复调用时，先劝、再停 |
| `fallback` | 主导失败时切兜底，并留一条 `MODEL_FALLBACK` 的痕 |
| `summarization` | 压缩（摘要）。按角色分档：`qa` / `coding` / `orchestrate` / `rework` 稳态（窗口 70%），主控 `plan` 与子智能体 `planner` / `coder` / `reviewer` 爆发（窗口 40%）。到线先写**持久化工具裁剪事件**：只记录工具调用 ID，运行时把大工具结果 / 大入参投影成占位符，原始 checkpoint 不变；没有新增可裁项或仍超线才摘要 |

- **沙箱**：Docker + OpenSandbox（`opensandbox` SDK），每会话新建；镜像 `opensandbox/code-interpreter:v1.1.0`，沙箱根 `/ai_coding`（含 `project/`、`policies/`、`skills/`、`output/`、`review/`、`tmp/`）。沙箱需要的资产（askpass / setup.sh、policies、skills）集中在仓库 `sandbox/` 下，建会话时整棵推入。
- **文件后端**：混合 `CompositeBackend` —— default 走自定义 OpenSandbox 后端，`/memories/` 走 `StoreBackend`。
- **Redis（可选）**：多 worker 时的**跨进程旁路状态** —— 一轮对话的帧缓冲（刷新/断线后落到哪个 worker 都能带 `after` 游标接回）、会话运行权（同一会话不许两个 worker 同时跑）、模型熔断状态。帧号由 `INCR` 发，缓冲有上限与 TTL。**不配 `REDIS_URL` 就全部留在进程内**（单 worker 的行为，默认）；配了但连不上会退回进程内并记 warning，`REDIS_REQUIRED=true` 则直接拒绝启动。单条命令失败后退回进程内是**「冷却一段时间再试一次」而不是永久降级**（默认 60s）—— 走隧道 / 在远端的 Redis 一次瞬时抖动，不该让这个进程此后彻底失去跨进程能力。注意：**沙箱句柄仍在各 worker 里**（同一会话落到别的 worker 会另建容器，工作区靠命名卷共享），所以真要横向扩容还得配 sticky 路由或「谁持锁谁干活」的约束。
- **右侧栏「代码」面板**（`infra/repo_cache.py`）：界面看代码**不经沙箱** —— 宿主机自己 `git clone --bare` 一份只读缓存（按 `owner/repo`，`REPO_VIEW_DIR` 默认 **`~/.ai-coding-harness/repo_cache`**，即用户个人目录，不写进项目目录），切分支只是换个 ref，读文件走 `git ls-tree` / `git cat-file`。沙箱没建好、已被回收、甚至建失败，代码照样看得到；令牌经环境里的 git 配置注入，不进 URL、也不落 `.git/config`。
- **代码平台**：Gitee（码云）与 GitHub。**平台由仓库地址判定**（`gitee.com` / `github.com`），PR 创建 / 评论 / 关闭走同一套 `create_pr` / `comment_pr` / `close_pr` 工具，内部按平台分流；token 请求优先、平台配置兜底。**两个平台的 OAuth 登录后端都已就绪**（`/auth/gitee/*` 与 `/auth/github/*`，换回的 token 随 `create_session` 的 `gitee_token` / `github_token` 带上）；**前端还没接登录入口**，所以眼下实际用的仍是 `.env` 里的 `GITEE_ACCESS_TOKEN` / `GITHUB_ACCESS_TOKEN`（见 CHANGELOG 遗留项）。
- **安全**：危险命令 denylist（`rm -rf /`、`mkfs`、`dd` 写盘、`shutdown` 等）+ 按角色的边界中间件（越界工具直接不给）+ 凭据脱敏覆盖四条出口（工具返回值、模型输出、SSE、落库与日志）。
- **可观测**：日志记录会话 / 沙箱 / 智能体生命周期；trace 预留（LangSmith / OTel）。每次模型调用一行 `LLM 调用完成：输入 X / 输出 Y`（供应商真值，按执行主角归账）；上下文那两层各省了多少各有 grep 关键字 —— `CONTEXT_PRETRIM`（裁剪事件新增多少大结果 / 大入参、剪前剪后、免压还是继续压）与 `CONTEXT_SUMMARY`（压前压后 tokens、消息条数、摘要字数、逐出原文落盘路径）；两行同口径（含系统提示与工具 schema），可以直接对着看。压缩那次的摘要调用记成 `internal`（`摘要调用完成：…`）。`handoffTask` 内层正文、思考、工具和日志以 `harness-run` 事件内嵌展示，run_id / parent_run_id 同时写入事件与日志。
- **思考展示**：模型思考以 `reasoning` 帧单独下发，主控与子智能体按角色分段显示；它只存在于前端与 RunHub 的临时帧缓冲，**不落业务库**。流式期间正文按纯文本显示，任务结束后一次性 Markdown 化。

## 意图与形态

所有形态都带联网工具（`web_search_tool` / `fetch_url`）与 `handoffTask`（这一轮做不了时交接给合适的形态），下表只列各形态**特有**的平台工具。

| 意图 | 说明 | 形态 | 该形态特有的工具 |
|---|---|---|---|
| `qa` | 只读问答、解释、排查、需求澄清；也回答仓库 / PR / 记忆现状 | `qa` | `get_pr_info`、`list_pr_comments`；可写 `/ai_coding/tmp` 取证台账，不改任务代码 |
| `plan` | 技术方案 / 设计 | `plan` | `get_pr_info`、`storePlan`、`getPlan`、`updatePlan`、`updatePlanSteps`、`deletePlan`（方案增删改查全归这一态；**不写记忆** —— 这一轮还没动代码） |
| `coding` | 写代码 | `orchestrate` | `task`（派子智能体）、`get_pr_info`、`list_pr_comments`、`comment_pr`、`close_pr`、`get_unexecuted_plan`（**只读**：主控先查有没有可执行的方案）、`save_repo_memory`。明确要动手时：查到方案直接派 coder；没有先派 planner，**落库后不等确认**，一路 coder → reviewer 跑完；只是描述需求时，方案出来先停下等确认 |
| `rework` | PR 跟进：审查 open PR、回评、按意见或用户新要求继续改既有分支 | `orchestrate`（与 `coding` 共用装配） | 同上。只审查时派 reviewer；要改代码时派 coder，改完再派 reviewer 复查；不新建分支 / PR |

`qa` 与 `plan` 都拿不到记忆写工具；仓库记忆正文只在 `coding` / `rework` 收尾由主控写回。PR 的新增记录与状态由轮首 / 轮末平台同步维护。

意图判定走**状态候选 + 四关瀑布**：候选集先按 open PR 收口，再走 **口令** → **JEV / SystemOne** → **大模型** → **关键字口径**。每一关的结论都如实标在日志的 `source=` 上（`keyword` / `jev` / `llm` / `fallback` / `state`）。

> 三个子智能体（`planner` / `coder` / `reviewer`）由 `coding` 与 `rework` 通过 `task` 派下去，各自取自己的模型（见 `agent/llm.py` 的角色路由表）。

## 行为约束

- **先摸清再动手**：主控先用 `write_todos` 写出本轮清单，再自己处理或派子智能体（子智能体的清单要挂在这份上才显示得出来）。这份清单是界面上唯一的进度，条目按用户原话里的对象写。
- **空转刹车**：同一工具、同一参数在本轮重复到 `LOOP_GUARD_REPEATS`（默认 3，`0` = 关掉）次时先收到一条不执行的警告，再重复一次即本轮收尾。
- **终止**：`POST /sessions/{id}/cancel` 中断当前这一轮。运行刻意剥在请求生命周期之外 —— 断开流只断订阅，停不掉它，必须显式取消。

## API

```
POST   /sessions                    {"repo_url": "...", "gitee_token": "可选", "github_token": "可选"}  # 建会话（同仓库已有活跃会话则复用）
GET    /sessions                    ?limit=50                                   # 会话列表
GET    /sessions/{id}                                                           # 会话详情（含 running，刷新后据此决定是否接回直播）
DELETE /sessions/{id}                                                           # 销毁会话
POST   /sessions/{id}/workspace                                                 # 准备 / 取代码工作区
GET    /sessions/{id}/records?limit=20&before_id=<id>                           # 历史一页（最近 20 条；before_id 向前翻）
GET    /sessions/{id}/clarification                                             # 当前待回答的意图澄清（刷新恢复弹窗）
GET    /sessions/{id}/plan                                                      # 最新方案（没有则 null）
POST   /sessions/{id}/cancel                                                    # 终止当前这一轮
GET    /sessions/{id}/repo                                                      # 右侧栏「代码」：本机缓存状态 + 分支（不联网）
POST   /sessions/{id}/repo/sync                                                 # 下载（首次 clone）或更新（之后 fetch）这份缓存
GET    /sessions/{id}/repo/tree             ?ref=<分支>&path=<目录>             # 列一层目录
GET    /sessions/{id}/repo/file             ?ref=<分支>&path=<文件>             # 读一个文件（太大 / 二进制只回标记）
GET    /sessions/{id}/memory                                                    # 右侧栏「记忆」：仓库记忆全文（只读）
GET    /sessions/{id}/pulls                                                     # 右侧栏「PR」：本仓库 PR 列表与状态
GET    /sessions/{id}/logs                  ?limit=300&q=<关键字>&with_http=false # 右侧栏「日志」：本会话日志（默认滤掉 HTTP 噪声）
GET    /sessions/{id}/logs/download                                             # 下载整份日志（按时间线合并成一份）
GET    /sessions/{id}/usage                                                     # 右侧栏「Token」：本会话消耗（按执行主角聚合）
GET    /model-config                                                            # 模型配置：每行「库里填了什么」+「现在真的用谁」（effective）
PUT    /model-config   {"rows": {"qa": {"base_url":…, "model":…, "api_key":…}}, "intent_mode": "jev"}  # 保存（留空＝不改）
POST   /model-config/probe  {"base_url":…, "api_key":…}                          # 测试连接：拉厂商 /models（顺带读窗口）
DELETE /model-config/{agent}                                                    # 清空这一行（界面上叫「清空这一行」）
GET    /model-config/providers                                                  # 提供商目录全量（含停用）：设置页「提供商」栏用它
PUT    /model-config/providers/{provider}                                       # 写一行提供商（整行覆盖；键不可改）
POST   /chat/{id}                   {"message": "..."}                          # 起一轮（SSE 流式）
                                     # 澄清选项：再加 {"clarification_id":"...","choice":0}
GET    /chat/{id}/stream            ?after=<帧号>                               # 续订阅（刷新后接回）
GET    /auth/gitee/authorize                                                    # 跳转 Gitee 授权页（带自签 state）
GET    /auth/gitee/callback             ?code=...&state=...                     # 授权回调，验 state 后拿 code 换 token
GET    /auth/github/authorize                                                   # 跳转 GitHub 授权页（同上）
GET    /auth/github/callback            ?code=...&state=...                     # 授权回调（GitHub 必须回 Accept: application/json）
GET    /health
```

> 意图由服务端**自动判定**，请求里没有 `task_type` 这类参数。

## 目录

```
src/ai_coding_harness/
├── api/            # FastAPI app、路由（model_config 按 agents / providers / probe 分包）、SSE、schemas
├── server/         # 意图判定 + 业务编排（turn/ 按职责拆 mixin）+ 运行宿主（RunHub）
├── agent/          # graph、llm、profiles、prompts、subagents（一主三从）
│   └── middleware/ # 上下文注入 / 脱敏 / 工具自洽 / 边界 / 空转刹车 / 兜底
├── db/             # 业务库 ORM / 引擎；dao/ 按表分组（会话 / 方案 / 记录 / 模型 / 澄清）
├── backends/       # OpenSandbox 后端 + 混合路由
├── sandbox/        # 沙箱供给 + SDK 客户端 + 工作区卷
├── tools/          # PR（Gitee / GitHub）/ 方案 / 记忆 / 交接 / web 搜索 / 工具注册
├── session/        # 会话管理 + checkpointer / store
├── safety/         # 命令与工具边界策略 + 凭据脱敏
├── core/           # 跨层契约：SSE 事件、git 平台判定、模型类型
├── infra/          # 宿主机基础设施：Redis、仓库只读缓存、熔断
└── observability/  # 日志 + trace

frontend/           # Vue3 + Vite 聊天界面（DeepSeek 风格：正文流、待办树、方案面板、终止 / 续传）
sandbox/            # 推入沙箱的资产：deploy / policies / skills
alembic/            # 数据库迁移
docs/               # 参考资料（业务流程图、代码平台 PR API、沙箱镜像、工作区目录、记忆模板）
start.sh / start.bat  # 一键启动前后端
```

## Setup

```bash
cp .env.example .env      # 填入模型 / Gitee 或 GitHub / OpenSandbox / PostgreSQL 配置
pip install -e ".[dev]"
alembic upgrade head      # 建业务表（PostgreSQL）
npm --prefix frontend install
```

> **依赖名注意（踩过坑）**
> `pip install -e ".[dev]"` 会按 `pyproject.toml` **重装依赖**，因此依赖要写**发行名**，不是代码里的导入名。
> 例：智谱 SDK 发行名是 **`zai-sdk`**（代码里 `import zai`）。若误写成 `zai`，PyPI 上的 `zai` 是个**空占位包**，会覆盖真 SDK 的文件 → `import` 失败 → **后端启动即崩**。
> 另外：导入可选/第三方 SDK 建议放在**函数内懒加载**（本项目 `tools/web_search.py` 即如此），避免单个包出问题拖垮整个应用启动。

## 启动

```bash
./start.sh                # Git Bash / Linux：一键起后端 + 前端
start.bat                 # Windows：双击即可
```

或分别启动：

```bash
python -m ai_coding_harness.main       # 后端 http://127.0.0.1:8000
npm --prefix frontend run dev          # 前端 http://localhost:5173
```

`.env` 关键配置：

- **模型**：**不在 `.env` 里配了**（2026-10-15 起）—— 谁用哪个模型在设置页 →「模型配置」里按智能体填，存表 `model_endpoints`、**下一轮生效**。唯一还用 `.env` 的是 `GLM_API_KEY` / `GLM_BASE_URL`（web 搜索那条工具）；`DEEPSEEK_*` / `KIMI_*` / `GPT_*` / `QWEN_*` / `*_CONTEXT_WINDOW` / `FALLBACK_PROVIDER` / `INTENT_MODEL|BASE_URL|API_KEY` / `TYPESAFE_API_KEY|BASE_URL|MODEL_NAME` 均已作废（`.env.example` 里逐条标注）。压缩（摘要）按角色分两档：**稳态** `qa` / 主控 `orchestrate`（coding / rework）按窗口 **70%** 触发，**爆发** 主控 `plan` 与子智能体 `planner` / `coder` / `reviewer` 按窗口 **40%** 触发；压完保留最近 20k，摘要那次调用最多吃 40k（`CONTEXT_STEADY_TRIGGER_FRACTION` / `CONTEXT_BURST_TRIGGER_FRACTION`，窗口解析不出来时才用 `CONTEXT_SUMMARY_TRIGGER_TOKENS` 兜底）。**平时不再做 40k 常规剪裁**，到压缩线的提醒线（默认 90%）先提醒模型写工作笔记与 `KEEP:` 清单，到线后的应急裁剪保留当前步骤 + KEEP 点名的工具返回，剪完低于线就不摘要。压缩那份中间件的取值由我们自己构造，上游那份按类排掉（`agent/summarization.py`）。两条**只提醒不改行为**的线不变：`CONTEXT_WARN_RATIO`（默认 0.6，输入框上方画细进度条）/ `CONTEXT_ALERT_RATIO`（默认 0.8，给警示语）。
- **兜底与熔断**：兜底只有设置页「兜底模型」那一行（留空＝关掉）；`FALLBACK_CIRCUIT_THRESHOLD` / `FALLBACK_CIRCUIT_COOLDOWN_SECONDS` 仍从 `.env` 读。
- **意图分类**：**严格二选一**（设置页 →「模型配置」→ 意图识别里选）—— JEV 那一行（供应商锁 `typesafe`，填 base_url + api_key）或「意图识别（大模型）」那一行。`INTENT_JEV_ENABLED` / `INTENT_LLM_ENABLED`（关掉某一侧）、`INTENT_TIMEOUT_SECONDS` / `INTENT_HISTORY_TURNS` / `TYPESAFE_TIMEOUT_SECONDS` 仍在 `.env`。
- **运行约束**：`AGENT_RECURSION_LIMIT`、`LOOP_GUARD_REPEATS`（`0` = 关掉空转刹车）、`HANDOFF_RESULT_MAX`。
- **业务**：`GLM_*`（智谱，web 搜索也用它）、`OPENSANDBOX_*` + `SANDBOX_*`（沙箱镜像 / 资产 / 工作区卷）、`GITEE_*` / `GITHUB_*`（两个平台的 PR 兜底 token、OAuth 登录凭据，GitHub 侧要先建 OAuth App 并把回调地址填成 `GITHUB_OAUTH_REDIRECT_URI`）、`POSTGRES_*`（业务库）。
- **Redis（可选，多 worker 才需要）**：`REDIS_URL`（留空 = 进程内）、`REDIS_PREFIX`、`REDIS_REQUIRED`、`REDIS_TIMEOUT_SECONDS`（默认 5s；**Redis 走隧道 / 在远端时必须放大**）、`REDIS_DEGRADE_COOLDOWN_SECONDS`（一次失败后退回进程内多久再试一次，默认 60s；设 0 = 旧的「永久降级」），以及运行缓冲/运行权的 `RUN_BUFFER_TTL_SECONDS`、`RUN_BUFFER_MAX_FRAMES`、`RUN_LOCK_TTL_SECONDS`、`RUN_POLL_INTERVAL_SECONDS`。
