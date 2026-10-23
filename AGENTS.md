# AGENTS.md —— 本仓库的协作约定

给在本仓库里干活的编码智能体看的最小规则集。**只写稳定规则与实测事实**；
「这次改了什么、还有什么没做」一律看 `CHANGELOG.md`（最新在最上），不要写进本文件。

## 一、协作方式（最重要）

1. **凡是会写文件的操作，先给方案、等确认、再动手。** 方案要写清：改哪些文件、为什么、
   关键决策与取舍、要用户拍的点、风险与回退。
2. **只读排查不必先问**：读代码、看日志、量数据、跑测试看现状 —— 直接做。
3. 用户明确放行（「改吧」「开始做吧」「按这个来」）后，可以一次做到完并给验证结果；
   用户只是**提需求**时，默认先给方案。
4. 有多个口径可选时列成 a/b/c 并给出推荐，等用户点。

## 二、改完之后必须做的三件事

1. **跑测试**：见第三节。全量 `pytest` 必须全过；只改了某个模块时也至少跑相关文件。
2. **lint**：`ruff check` 本次改动的文件必须干净（全仓有若干既有告警，别新增）。
3. **写 CHANGELOG**：在 `CHANGELOG.md` 顶部（当日 `### 日期` 下、其他条目之上）追加一条，
   含 **动机 / 改动 / 关键决策 / 验证 / 遗留**。

## 三、环境与命令

- **Python 运行环境是 conda 环境 `aicoding`**（`D:\developmentBinary\miniconda3\envs\aicoding`）。
  本项目没有 `pip install -e .` 进这个环境，所以 `src` 要进 `PYTHONPATH`：

  ```powershell
  $env:PYTHONPATH='src'
  conda run -n aicoding --no-capture-output python -m pytest -q
  ```

- **lint**：`D:\python-venv\ai_coding\Scripts\ruff.exe check <改动过的文件>` —— 实测最稳。
  （`conda run -n aicoding --no-capture-output python -m ruff check …` 也能用，但命令非零退出、
  输出较多时会只回一句 conda 自己的 ERROR / 插件告警，把检查结果吞掉，别指望它稳定。）
  那套 venv（`D:\python-venv\ai_coding`）依赖也齐、且是把本项目 editable 装进去的，但它依赖的
  基座解释器曾经被删过一次；跑测试与跑代码默认仍走 conda。
- **提示词自检**：`python scripts/check_prompts.py` —— 渲染六份成品提示词并扫跨形态冲突，
  **必须是 0 条**。它零第三方依赖，任何 Python ≥3.11 都能跑。
- **前端**：`npm --prefix frontend run build`（含 `vue-tsc`）。PowerShell 里 `npm.ps1` 可能被
  执行策略挡住，那就用 `npm.cmd --prefix frontend run build`。dev 在 5173、后端在 8000，
  一键起用 `start.bat` / `start.sh`。**新增后端路由前缀必须同步加进
  `frontend/vite.config.ts` 的 `server.proxy`** —— 漏了的现象是「点开界面就报错，而 curl 后端
  200」（请求打到了 vite 自己身上，`/model-config` 就这么踩过一次）。dev 只监听 IPv6 回环，
  用 `http://localhost:5173`（`127.0.0.1:5173` 会被拒）。
- **设置是整页工作台，不是弹窗**：`frontend/src/components/settings/SettingsPage.vue`（入口＝左栏品牌行那颗齿轮，
  `Esc` 返回），左菜单三栏＝基本设置 / 外观 / 模型配置；切页时聊天区用 `v-show` 藏在原地，
  不卸 DOM。**基本设置与外观是本机偏好**（`frontend/src/lib/prefs.ts`，localStorage，改完立刻生效）；
  **模型配置存后端库**（改完要保存，且重启后端才生效）。主题只改 `<html data-theme>`，
  颜色全在 `style.css` 的两组令牌里 —— 组件里不判断主题，**新增颜色必须走令牌**，
  否则深色下会出「白块 / 看不见的字」。
- **配置**：模型 / Gitee / GitHub / OpenSandbox / PostgreSQL 的凭据都在 `.env`（不进版本库），
  键的说明看 `.env.example`。
- **生效时机**：提示词与沙箱资产是**建会话时**读 / 推的，后端配置与装配改动要**重启后端**；
  前端是 vite 热更新，改完刷新即可。

## 四、硬约束与踩过的坑

- **提示词是行为契约**：改 `src/ai_coding_harness/agent/prompts.py` 的措辞等于改行为 ——
  必须跑 `tests/test_prompts.py` 与 `scripts/check_prompts.py`。
- **`invalid_tool_calls` 不能进入 provider 历史**：LangChain 会把它序列化成
  `invalid_tool_call` content part，OpenAI-compatible 网关直接 400。模型出口与历史修复
  都必须把它降级成普通正文、清空字段，并且不要为没有 `tool_calls` 的错误调用伪造
  `ToolMessage`。见 `agent/middleware/tool_integrity.py` 与 `tests/test_tool_integrity.py`。
- **沙箱资产建会话时整棵推入**：`sandbox/policies`、`sandbox/skills`、`sandbox/maven`、
  `sandbox/deploy/sandbox/*` 改完要**新建会话**才保证生效。脚本类的资产有编码要求：
  `start.bat` 是 **GBK**，改动必须保住原编码，不能拿 UTF-8 直接覆盖（否则中文乱码）。
- **仓库记忆的写入口径**（2026-10-20 起是**按节增量写**）：`save_repo_memory(section, payload)`
  只收 `change` / `gaps` / `decisions` / `basic` 四节，平台负责合并、去重、渲染与限长
  （基本信息 2000 / 缺口 1500 / 长期约束 1500 / 全文 6000 字，变更记录只留最近 8 条）。
  **整篇覆盖写已经取消**；模型往「基本信息」塞本次改动会被白名单拒掉。
  平台维护文件头（最后更新 / 最近 PR）、PR 状态格，并摘掉老模板的两张表。
  正文只在 **coding / rework 的收尾**由主控写；`qa`、`plan` 与三个子智能体都不写。
  **注入按形态分档**（`memory_sections_for`）：qa 全文；coding / plan 主控与
  planner / coder 只给「基本信息 + 当前缺口 + 长期约束」；reviewer 子智能体
  **不注入**（`MemoryMiddleware(sections=())`，连读都不读）。
- **拦截一律是代码拦，不是提示词**：`BoundaryMiddleware` 管「这个角色能用哪些工具、能写哪些
  目录」，`LoopGuardMiddleware` 管空转。PR 状态拦截在意图判定后由
  `server/intent.gate_intent_for_state` 一次性完成；`MemoryMiddleware` 只加载 / 注入记忆。
- **两份记忆模板必须逐字一致**：`docs/repo-memory-template.md` 与
  `sandbox/policies/repo-memory-template.md`（有测试钉着）。
- **模型配置按智能体存库，而且没有「出厂默认」**（2026-10-15 用户口径：干掉 `.env` 那套回落）：
  表 `model_endpoints`（键：qa / plan / review / coding / coder / unified / fallback /
  jev / classifier / `intent_mode`，行清单一处定义在 `config.MODEL_AGENT_ROWS`）；**启动时**读进
  `Settings`（`api/app.py::apply_model_endpoints`），界面上保存 / 清空之后**当场再刷一次**
  （那个函数是**先清空再落**）。因为**主智能体每轮现建、不缓存**（`session/manager.py::get_agent`），
  所以**下一轮就生效、不用重启**；正在跑的那一轮不换模型。
  **一行没配 = 那一行没有模型可用**：用到这个角色的那一轮当场报错，报错文案指认「哪一行 + 去哪儿配」
  （`agent/llm._config_for` / `_unconfigured`），**不许再偷偷回落到 `.env`**。解析顺序是
  **① 该智能体那一行 → ② 「统一模型」那一行**（用户口径 2026-10-15：统一模型**不是总闸**，
  只兜底**没配**的那些行；配了的行按自己的走）。`tests/test_model_config.py::TestResolution` 与
  `tests/test_llm_config.py` 钉着。DB 里的 key 会进脱敏词表。
- **「编码」一主三从的模型归属**（2026-10-14 用户口径）：**主控与 coder 各占一行**
  （`coding` / `coder`），**planner 复用 `plan`、reviewer 复用 `review`** —— 四个角色都有出处，
  但只有两行要配。映射在 `agent/llm._AGENT_KEY`（守卫测试逐个角色钉住；界面上的分组与
  「这一行服务哪些角色」由 `config.MODEL_AGENT_ROLES` / `CODING_GROUP` 下发，**前端不硬编码行名**），
  别再把三个子智能体都塞进 `coding`。
- **意图识别是状态候选 + 严格二选一**（2026-10-22 用户口径）：先按仓库 open PR 状态收口候选
  （无 open PR：qa / plan / coding；有 open PR：qa / rework；未知：qa），JEV / 大模型各自一行配置（JEV 那一行供应商
  锁死 `typesafe`），选了哪一侧就只走哪一侧 —— 没配齐 / 判不出来一律落**关键字口径**，
  **不互借**（`server/intent.detect_intent`）。JEV 的凭据也走界面（`typesafe_*` 由那一行灌入），
  `.env` 的 `TYPESAFE_*` / `INTENT_MODEL|BASE_URL|API_KEY` 全部作废。
- **PR 列表每轮只查一次，不做接口缓存**（`server/repo_state.py`）：轮首查全量 PR，
  同一份列表同时用于意图候选与仓库记忆同步。`server/repo_memory_sync.py` 按记忆里最新 PR
  编号识别新增 PR，批量调用一次 `qa` 模型生成摘要；已有 PR 只更新与平台不一致的状态，
  有差异就真实写回 StoreBackend。PR 查询失败时本轮只保留 qa。
- **仓库记忆的 PR 同步只有两个入口**：轮首记忆拦截点，以及 coding / rework 轮末。
  `save_repo_memory` 不再查 PR，只合并模型提交的正文与落盘。
- **运行期配置是表 `app_settings`**（迁移 `e3f4a5b6c7d8`）：代码缓存路径 / 默认分支 / 上下文告警线
  与各家窗口。**键与类型只认 `config.RUNTIME_SETTINGS` 那一张表**，接口 `GET/PUT /app-settings`，
  写入后赋回 `Settings` 实例 → **改完立刻生效**（配合「agent 不缓存」）。新增可配项就在那张表里加一行，
  并在设置页「基本设置」里出现（分组名由它带出来）。直接改库也能用：GET 时会重新落一遍。
- **「这一轮改了什么」在本地代码缓存里算，不碰沙箱**：`infra/repo_cache.compare/file_diff` +
  `GET /sessions/{id}/repo/diff`，`base...head` 两支取 `create_pr` 的入参（三点式＝相对共同祖先，
  这才是 PR 的语义）。只组建成 PR 的轮次、只存**文件清单**（diff 正文按需拉，脱敏 + 上限），
  所以**沙箱释放之后 diff 照样点得开**。**中止的轮次只要本轮建过 PR 也照样算**
  （`_record_interrupted` 收 `changes`）；rework 更新既有 PR、不新建，因此没有这一块。
  要动这块先看 `tests/test_turn_changes.py` 与
  `tests/test_repo_cache.py::TestCompare`。
- **提供商目录是表，不是代码常量，而且是「每条请求现读库」**：表 `model_providers`（界面上
  「提供商」那一栏：显示名 / 默认地址 / 清单路径 / 鉴权头 / 分组排序 / 启停）。**直接改库 + 刷新界面即生效，
  不用重启**；`Settings.providers`（启动时那份）只在读库失败时兜底。种子文件
  `config/model_providers.toml` 只在**表为空时**灌一次，之后以库为准；改了种子文件要
  `POST /model-config/providers/reload` 对齐（同名行整行覆盖，**库里手改的同名行会被覆盖掉**）。
  **2026-10-15 起目录本身在界面上就能改**（设置页第 4 栏「提供商」，`GET/PUT
  /model-config/providers[/{p}]`，整行覆盖、不删除只停用）—— 所以「改目录」不要再去动库或
  种子文件，那条路只留给「新库初始化」。种子文件从此只是**新库的初始快照 + 参考**。
  键仍是智能体 —— 这张表**不决定谁用哪个模型**。
  目录行的 `models_path` **可以写绝对 URL**（qwen 的清单在 `…/api/v1/models`，不在兼容入口那个
  根下面）；返回形状认两种（`data[]` 与 qwen 原生的 `output.models[].model_info`），窗口在
  **任意层级**里找 `context_length` / `context_window` / `max_context_length` / `max_input_tokens`
  （实测：deepseek 用 `context_window`、glm / kimi 用 `context_length`、**openai 什么都不回**）。
  实测路径：glm 是 `…/api/paas/v4/v1/models`（**多一层 `/v1`**）、qwen 是绝对地址
  `https://dashscope.aliyuncs.com/api/v1/models`、deepseek / kimi 默认 `/models` 即可。
- **「测试连接」的门槛只有两样**：`base_url` 有值 + 有可用 key（库里那把或界面上刚填的）。
  目录行怎么写都不拦：`is_dynamic_model_list` 只是「这家官方有没有 /models」的标注，
  `/probe` 一定会发请求，拉不到就回错误原文（**不再回内置候选** —— 那份 `default_models`
  静态名单 2026-10-15 已删，模型名只认「刷新清单」拉到的那份）。别再用目录字段去藏这个按钮。
- **上下文长度按「模型」查，不是按供应商**：表 `provider_models`（`provider` 外键 → 稳定键 +
  `model` 主键，另有 `model_type` / `context_window` / `source` / `status`）。解析顺序
  （`llm.resolve_window`）：行内显式 → `(供应商, 模型)` → 模型名（治自建网关）→ `.env` 那家（兜底）
  → 兜底值。同一家不同模型窗口差很远（glm-5.3 是 1M、glm-4.6 是 128k），按家取值会把压缩线推错。
  清单**只在点「刷新」时拉**（`POST /model-config/providers/{p}/models/refresh`，key 借用 agent 行），
  非文本模型也落表并标类型（`core/model_types.py`，判据只能按名字），界面上只显示 `chat`。
- **压缩中间件是我们自己构造的那一份，上游那份被按类排掉**（`agent/summarization.py` +
  `agent/graph.py` 里的 `excluded_middleware`）：**别改回上游工厂** —— 它的 `trigger` / `keep` /
  `trim_tokens_to_summarize` 三个默认值都不合用，而且**都没暴露成参数**（工厂把最后一格默认
  成 `None` ＝摘要那次调用完全不裁）。排除是**按类精确匹配**，所以我们那个子类实例不会被
  误伤；上游若改了这份装配，`_verify_excluded_middleware_coverage` 会在建 agent 时直接
  `ValueError`（当场红，不会静默变成两份压缩同时挂着）。也因此 **`pyproject.toml` 必须钉住
  `deepagents` / `langchain` 的版本** —— `pip install -e ".[dev]"` 是按它重装依赖的，
  抬了版本这套就静默失效。
- **上下文计数是「中英混合」口径**（`middleware/context_trim.py::context_token_counter`）：
  中文按 1.6 字符/token、其余按 3.8，按当轮实际比例折一个等效系数。**别改回单一常量** ——
  统一按 1.6 时实测把**代码**高估 2.49 倍，而压缩的窗口比例共用这把尺子，线会被推歪。
  估算之上还有一层**按模型**的在线校准（`agent/middleware/context_calibration.py`）：用每次调用
  `usage_metadata.input_tokens` 的真值做 EMA（α=0.3），样本只认「刚发出去的那一份」；
  没有样本时系数 1.0、行为与校准前一致。**别拿预剪前的估算配预剪后的真值** —— 比例会被放大。
- **思考正文靠 `agent/chat_model.py` 补**：`ChatOpenAI` 只保留官方字段，第三方网关的
  `reasoning_content` 在流式转换里会被丢掉。`ReasoningChatOpenAI` 覆写
  `_convert_chunk_to_generation_chunk` 把它塞回 `additional_kwargs`，
  `ReasoningAIMessageChunk.content_blocks` 再还原成 reasoning 块（**只补 kwargs 不够**：
  OpenAI 的 provider translator 不读它）。因此 `langchain-openai` 钉在 1.3.x；
  升级先跑 `tests/test_chat_model.py`。**别忘了补 role**：子类会让 `_stream` 把
  `default_chunk_class` 设成子类，而父类只认**精确** `AIMessageChunk`；流式 tool-call
  chunk 的 `role` 常是 `None` —— 交给父类前必须补 `role="assistant"`，否则 `tool_calls`
  会被静默丢掉（2026-10-19 真机：空正文 + 0 工具）。
- **思考与正文同属前端的 `parts`**（`kind='reasoning'`），运行中按到达顺序交错渲染；
  **跑完隐藏思考、只留正文并 Markdown 化**；不落库、刷新即消失。别再单独维护
  `message.reasoning` 或把思考固定渲染在正文上方（2026-10-19 用户口径）。
- **不要常规剪裁**（2026-10-17 用户口径）：平时**不**按 40k 阈值剪；只有到达压缩线时，
  压缩中间件才生成一次**裁剪事件**（`plan_context_trim_event`）。2026-10-19 起按角色分两档：
  主控 `plan` 与子智能体 `planner` / `coder` / `reviewer` 走**爆发**（窗口 40%），
  `qa` 与主控 `orchestrate`（coding / rework）走**稳态**（窗口 70%）。
  **2026-10-20 起再叠一条绝对上限**：爆发 120k、稳态 200k，与比例线是 OR（先到先触发）——
  1M 窗口的 40% 是 40 万，真机一轮 131 次调用能涨到 194k 都不压。到压缩线的 90% 先注入
  `PRETRIM_NOTICE`，让模型写 `<working-note>` + `KEEP:` 清单；下一轮裁剪保留
  **当前步骤 + KEEP 点名的工具返回**（总额约 20k tokens，最近优先），未点名的一律换成
  带身份占位符。2026-10-22 起裁剪是 **`_context_trim_event` 持久化投影**：checkpoint
  的 `messages` 保留原文，事件只存 `tool_call_id`；每次请求先应用摘要事件、再应用裁剪事件。
  **工具结果 / 入参小于 2000 字符不裁**，大入参只压字符串字段并保留 `file_path/path/
  offset/limit/timeout`。剪完**落到触发线的 70% 以下才免压**；没有新增可裁项或仍超线时
  直接摘要，不再使用“三次应急裁剪”计数器。**不要**改成「最后一条 `HumanMessage`
  之后全部保留」—— 子智能体整轮可能有一百多条工具返回，等于没有裁剪。它跟随
  `CONTEXT_TRIM_TOOL_RESULTS` 总开关；`CONTEXT_TRIM_TRIGGER_TOKENS` / `CONTEXT_TRIM_KEEP`
  / `CONTEXT_TRIM_ROLES` 已作废，别再加回独立剪裁中间件。`CONTEXT_SUMMARY_TRIGGER_TOKENS`
  只在窗口解析不出来时兜底；有窗口时用「比例线 或 绝对上限」。每轮结束会打一条
  `本轮成本`（输入/输出/调用/峰值），超过 `TURN_COST_WARN_TOKENS`（默认 300 万）再记 WARNING。
- **主控每次 `task` 前都要有一段新的 `write_todos`**（2026-10-20 用户口径，
  `DelegationGateMiddleware`）：第一次派发看最近一条 `HumanMessage` 之后；之后每次派发看
  上一次 `task` 结果之后。同一条 AI 消息里并行的 `write_todos` + `task` 不算，同一批并行
  的多个 `task` 共享同一段清单更新。被拦的 `task` 不计入循环次数；只约束主控，子智能体
  不受影响。`LOOP_GUARD_REPEATS=0` 只关重复计数，不关这道闸门。排查时先看日志里有没有
  `委派闸门已挂载`：没有这行就是进程没重载。
- **成本纪律**（2026-10-20）：读码先 `grep` 定位、再用 `read_file` 的 `offset/limit`
  读片段，同一文件本轮不重读；coder 按模块批量验证，别每改一行跑一次全量；
  `CONTEXT_TRIM_CLEAR_INPUTS` 默认关（开启也只做超大字段选择性压缩，不把 `args` 清成 `{}`）；
  rework 的任务描述只给 PR / 问题编号，审查原文由 coder 用 `list_pr_comments` 自取
  （coder **只读**评论，`comment_pr` 仍归主控）。
- **coder 的 rework 按模块编排**（`prompts._CODER_ROLE` B 流程）：先核验确认问题，再按
  模块 / 目录分组（**不按 P0/P1**），`write_todos` 一项对应一个模块；同模块改完先跑该
  模块测试，全部模块后再做一次全量。
- **读文件红线由中间件拦**（`ReadGuardMiddleware`，主控与三个子智能体都挂）：
  `read_file` 读 `/ai_coding/policies` 必须显式给 `limit` 且 ≤80 行、`/ai_coding/project`
  ≤200 行；`execute` 里的 `cat` / `less` / `more` / `nl` / `tac` 读这两处一律拦，
  `head` / `tail` 超上限与 `sed -n '1,$p'` 也拦。提示词只做前置说明，真正的边界在这一层。
- **reviewer 按 diff hunk 审查**（`prompts._REVIEW_METHOD` +
  `sandbox/skills/code-review/SKILL.md`）：先 `git diff base..head` 看改动块，`read_file`
  只补被改函数 / 直接调用点；同一文件同一范围不重读，**用不同 offset 把整个文件扫一遍
  也算重读**；大 PR 分模块收口、报告增量写，开头给覆盖清单。skill 的步骤 3 也写了同一条
  读码边界，命令白名单已对齐（`cat` 只用于 `/ai_coding/skills` 与 tmp，project / policies
  用 `sed -n` / `head` / `grep`）。
- **推理规则在基础提示词里**（`prompts._BASE_HEAD`，所有形态共享）：先过**影响面闸门**
  （会不会改变本次交付 / 低后果还是高后果 / 能不能验证）再决定展开深度；待决清单按
  `问题 | 影响面 | 处置(答/改/记/上报)` 列；不可验证的问题最多一次检索 + 一次查源码，
  超预算按固定模板记「残余风险」；同一不确定点连续两次没有新证据就停止推演；
  确认性检查只写结论 + 证据位置；任何推理必须落到答复 / 代码 / 测试 / 风险记录 / 上报。
  coder 的 rework 分两阶段：核验阶段只出覆盖清单 + 缺口 + 风险，不设计修法；实施阶段
  只做确认要改的项、先定验收方式再改。
- **每轮能力不写进运行期文本**：能力差异由 `prompts._BASE_HEAD` 的通用规则约束（历史轮次
  的工具可用性不作数），具体动作由 `intent_directive` 给出。`task` 是否存在以装配为准
  （`profile.subagents` + `SubAgentMiddleware`）；模型若说“没有 task”，先看日志里的
  `form=` 与实际 `task` 调用，不要直接怀疑形态映射。`IntentMiddleware` 只注入意图指令与
  澄清块，**不再注入时间**（记忆头行由平台写，散文日期让模型跑 `date`）。
- **意图判定的两个锚点**（`server/intent.py` / `service._last_intent`）：无状态限制时默认值是
  `coding`，有 open PR 时状态候选会把默认兜底压到 `qa`；判定输入带**上一轮判定**
  `previous_intent`（最近一条 assistant 记录的 `extra.intent`），JEV 与大模型两条路都看它。
  `qa` 的裸「为什么」不覆盖
  「为什么不派 / 为什么没改」这类对上一轮动作的追问。
- **意图上下文是最近两轮对话**（`dao.recent_dialogue` / `service._dialogue`）：用户消息
  **全文**，助手消息用 `head_tail_excerpt`（前 200 / 后 300 字）截断。语义口径：
  `qa` 合并原 `analysis`，管已有数据问答、解释功能/用户疑问、排查原因与需求澄清；
  **计划、方案、任务完成度一律 plan**；
  「继续 / 修正 / 纠正 / 重来」按未完成轮次沿用上一轮可执行意图。短追问（B/确认事实/
  修复吧）不要因为 target 漂移换形态；明确「实施/执行方案」无 open PR 时归 coding，
  有 open PR 时归 rework；「只想知道/不要做」归 qa。
- **会话 active task**（`service._active_tasks`）：只记最近一次**可执行**意图
  （coding/rework/plan），qa 不覆盖；短追问/“继续”优先沿用它。PR 已合并/已关闭后的
  收尾、关闭/删除方案时清空。路由 JSON 解析失败重试一次。
- **低置信度转 qa 澄清**（`config.intent_clarify_confidence`，默认 **0.55**，0=关闭）：
  JEV/LLM 的 confidence 低于阈值时转 `qa` + `SOURCE_CLARIFY`，qa 用**提示词澄清模式**
  （先只读了解上下文、列 2~3 个按可能性排序的候选、最后只问一个问题），**不把
  `write_todos` 还给 qa**。`active_task_override` 命中时优先继续干活，不追问。
  阈值来自 200 条实测：JEV <0.55 的 16 条只对 43.8%，≥0.55 的 147 条对 91.2%。
- **澄清是结构化弹窗，不是自由文本追问**（2026-10-19）：qa 仍可自由措辞，但必须输出
  `<CLARIFY>` + JSON（`question` + 2~3 个 `{intent,label,description}`，intent 还必须属于
  本轮状态候选集）；后端解析后落表 `clarifications`（每会话最多一条）
  并发 SSE `clarification` 帧。**任何新的用户消息先让旧 pending 失效**；前端点候选项走
  `clarification_id + choice` 确定性映射、跳过意图模型，用**原始用户消息**执行；点“其他”
  或关掉弹窗后自由输入，才重新走意图识别。别把它做成“让模型自己理解第一个/第二个”。
- **大模型分类输出是四行**：意图 / 置信度 / 候选 / 理由；`parse_reply` 仍兼容旧两行。
  改提示词或解析要同时跑 `tests/test_intent_detection.py`。
- **意图评测先固定轴**：`eval_intent.py run --out` 保存逐样本路由轴，`policy` 用固定轴
  离线重算策略（不调模型）；`--normal-only` 只跑现有判定。**别拿单次端到端波动直接调策略**
  —— 同批样本的路由轴会漂（实测 coding→plan 从 0 到 7）。
- **意图评测与影子路由**（`server/intent_router.py` / `scripts/eval_intent.py`）：把
  一句话拆成 `speech_act / wants`（change / lookup / analyze / decide）/ `target` /
  `continuation` 四个轴，策略表只给**建议**；
  `INTENT_ROUTER_SHADOW=true` 时只记 `INTENT_ROUTER` 日志，**不改变本轮形态**（默认关）。
  真实评测样本写 `docs/intent-eval.jsonl`（已 gitignore，别提交用户消息）；示例文件是
  `docs/intent-eval.example.jsonl`。
- **目录权限口径**看 `docs/workspace-directories.md`。**沙箱里的 Maven 已配好**（阿里云镜像 +
  卷上的本地仓库 `/ai_coding/.m2/repository`），不要让模型自己另配或清空。

## 五、去哪儿看

- `README.md`：项目是什么、API、目录结构、启动方式。
- `CHANGELOG.md`：每一次改动（最新在最上）—— 动手前先读顶部几条，能避开大部分重复劳动。
- `docs/`：目录权限、沙箱镜像、代码平台（Gitee / GitHub）PR API、记忆模板，
  以及**设置页的设计**（`docs/config-page-design.md`：三栏工作台、模型配置弹窗、
  窗口与候选的解析口径、每样东西存在哪 / 什么时候生效、接口一览）。
- `docs/backlog.md`：**已经想清楚、但还没动手**的事（立项清单）—— 用户说「立项 / 记遗留」
  就写到那里；每条要写清「要解决什么、为什么当时不做、动手的抓手在哪」。
- `scripts/`：`check_prompts.py`（提示词跨形态冲突自检）、`verify_opensandbox.py`（沙箱连通性验证）。
