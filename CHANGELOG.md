# 项目修改记录

本文档按时间倒序记录项目的每次改动。约定：

- 每完成一个阶段，在「修改记录」最上方追加一条（最新在最上）。
- 每条记录尽量写清：**改动内容 / 涉及文件 / 关键决策 / 遗留事项**。

## 进度总览

| 模块 | 状态 |
| --- | --- |
| 脚手架 + 配置 | ✅ 完成 |
| sandbox/ 沙箱层 | ✅ 完成 |
| backends/ 后端层 | ✅ 完成 |
| tools/ 工具层 | ✅ 完成 |
| agent/ 智能体核心 | ✅ 完成 |
| session/ 会话管理 | ✅ 完成 |
| memory/ 记忆 | ❌ 不做（StoreBackend + 仓库记忆文件已覆盖） |
| api/ HTTP 接口 | ✅ 完成 |
| safety/ 安全 | ✅ 完成（全局底线 + 按智能体的工具 / 目录边界） |
| observability/ 可观测 | ✅ 完成 |
| tests/ 测试 | ✅ 完成（1546 条全过） |

## 修改记录

### 2026-10-19

#### 收口：13 条既有失败 —— 测试对齐当前提示词与策略

- **动机**：PR #1 的全量结果是 1533 passed / 13 failed。13 条都不是新引入的：10 条是
  `TODO_PROMPT` 重写成「先看清 / 推进 / 收尾」三条之后、测试仍在断言旧文案；3 条是
  2026-10-17 明确改过工具面（orchestrate 隐藏 `edit_file/write_file`、analysis 隐藏
  `write_todos`）之后没同步。合并前必须让分支回到全绿。
- **改动**：
  - `tests/test_prompts.py`：断言改到当前契约。主控的前置约束改成「先看清这一轮要做什么 +
    没写清单不许 `task`」；清单规则改到三条新版；**方案步骤粒度只归 `CODER_PROMPT`**
    （通用段不替 coder 规定）；analysis 改成「与 qa 一样不挂清单」；「先取回审查原文」
    改到 `intent_directive("rework")`（它本来就只拼在主控的 rework 轮）。
  - `tests/test_boundary.py`：orchestrate 的隐藏工具补 `edit_file / write_file`，analysis 补
    `write_todos`；「没有隐藏工具时不改请求对象」那条改用 `execute`（原用例拿 `write_file`
    在 orchestrate 下已经会被隐藏，前提不成立）。
  - `src/ai_coding_harness/agent/prompts.py`：修 `TODO_PROMPT` 错字（「推推荐」→「任务状态
    要及时更新」）；删掉 `CODER_PROMPT` 里指向「任务清单规则第 3 条」的失效引用（第 3 条
    现在是收尾规则）；同步段前注释。
  - `src/ai_coding_harness/safety/policy.py`：orchestrate 行的注释改成「写码一律派 coder」，
    与「隐藏写工具」的实际行为一致。
- **验证**：`pytest tests/test_prompts.py tests/test_boundary.py` **198 passed**；
  `scripts/check_prompts.py` **0 条冲突**；全量 `pytest` **1546 passed**；`ruff check`
  改动的 4 个文件全清。
- **遗留**：analysis 不再有进度清单（靠 `code-tracing-analysis` 的取证台账）。真机上若
  仍需要可见进度，再单独评估，不在本次把旧断言改回去。

#### 委派闸门从循环守卫里拆出来

- **动机**：`LoopGuardMiddleware` 只管「同一工具同参数反复调用」这一件事；当天加进去的
  `task` 前置检查是**执行前置条件**，判据、开关、失败方式都不一样
  （`LOOP_GUARD_REPEATS=0` 关不掉它）。继续放一起，名字与职责就对不上了。
- **改动**：
  - 新增 `agent/middleware/delegation_gate.py::DelegationGateMiddleware`：主控专用。
    本轮没有**成功执行过** `write_todos` 时，`task` 不执行，回一条
    `ToolMessage(status="error")` 说明先写清单；`write_todos` 的成功记账也由它自己做
    （仍落在 `TurnState` 上，与循环计数共用同一份本轮状态）。
  - `LoopGuardMiddleware` 还原成纯刹车：只数重复、先劝后停，不再关心 `task`。
  - `agent/graph.py` 的装配顺序改为 `Boundary → Memory → DelegationGate → LoopGuard → …`：
    被闸门拦下的 `task` 不占重复额度，模型补完清单后第一次派发仍是第 1 次。
  - 子智能体不挂这一层（它们没有 `task` 可调），`subagents.py` 不变。
  - 测试拆成 `tests/test_loop_guard.py`（22 条）与 `tests/test_delegation_gate.py`（9 条）；
    新增「`Command` 返回值算成功」「一张清单允许本轮多次派发」「被拒的 `task` 不占额度」
    三个用例，另在 `test_profiles.py` 钉住子智能体不挂闸门。
- **验证**：定向 109 passed；全量 `pytest` **1533 passed / 13 failed**（与改动前同一批
  既有失败：`test_prompts` 10 条、`test_boundary` 3 条）；`ruff check` 改动的 7 个文件全清。
- **遗留**：无。

#### 主控调用 `task` 前强制 `write_todos`

- **动机**：主智能体通过 `task` 派子智能体时经常忘记先调用 `write_todos`，于是这一轮
  没有用户可见的任务清单，进度也无处落。用户要求在 `awrap_tool_call` 里拦。
- **改动**：
  - `TurnState` 新增按角色分账的 `_successful_tools` / `mark_tool_success()` /
    `has_successful_tool()`；只认工具**成功返回**，失败或只发起调用不算。
  - `LoopGuardMiddleware.awrap_tool_call()` 在主控调用 `task` 时检查本轮是否已有成功
    的 `write_todos`；没有就返回 `ToolMessage(status="error")`，说明必须先写清单，
    `task` 不执行。被拦的 `task` 不进入循环计数，补完清单后第一次 `task` 仍是第 1 次。
    （同日稍后拆到 `DelegationGateMiddleware`，见上一条。）
  - 闸门只针对 `MAIN_AGENT`；子智能体不挂这道限制。`LOOP_GUARD_REPEATS=0` 只关循环
    计数，不关这道任务闸门。
- **验证**：`tests/test_loop_guard.py` **27 passed**（覆盖拒绝、成功放行、失败的
  `write_todos` 不放行、子智能体不拦、循环守卫关闭时仍拦）。

#### 历史会话少了几轮：records 改成分页取最近 20 条

- **动机**：当前 wanqi-mall 会话 `cb4c905c` 实际有 **207 条** session_records，但
  `GET /sessions/{id}/records` 默认 `limit=200`，DAO 又按时间**正序** `LIMIT 200`
  —— 被截掉的是**最新的 7 条**，所以界面看起来像最近几轮丢失；token 聚合复用同一
  查询，也会少算这几条。用户 2026-10-19 口径：首屏只取最近 20 条，向上滚动再翻页，
  页面加载也应该更快。
- **改动**：
  - 新增 `dao.list_records_page(..., before_id, limit=20)`，按 `id < before_id` 取
    更早一页，多取一条判断 `has_more`，返回按时间正序的条目 + `next_before_id`。
  - `GET /sessions/{id}/records` 默认返回 `RecordPage {items, has_more, next_before_id}`，
    默认 20 条；`before_id` 是继续向前翻的游标。
  - 前端首屏只请求 20 条；消息区滚到顶部（`scrollTop <= 80`）自动加载更早 20 条，
    保留滚动位置；顶部也保留「加载更早的 20 条」按钮作为兜底。
  - `list_records` / `list_records_with_detail` 继续保留“最近 N 条再翻回正序”的口径，
    token 聚合不再从最老的 200 条开始。
- **验证**：同一会话 `list_records_page(limit=20)` 返回 20 条、`has_more=True`、
  `next_before_id=596`，首条 596 / 末条 615；`list_records_with_detail(limit=500)`
  返回 207 条，最后一条是 id=614。

#### 意图澄清改成「qa 固定协议 + 前端弹窗 + 一次性 pending」

- **动机**：低置信度澄清不能只让 qa 自由写一段追问——用户很难稳定回答“第一个/第二个”，
  后端也无法确定用户选的是哪个意图。改成弹窗选项后，选项点击是确定性映射；用户关掉弹窗
  自由输入时，才重新走意图识别。qa 仍然可以自由写问题、label 和 description，只是输出
  必须包在固定协议里。
- **改动**：
  - 新增 `clarification.py`：qa 输出 `<CLARIFY>` + JSON（`question` + 2~3 个
    `{intent,label,description}`），解析器校验意图白名单、选项数量、重复项。
  - `clarification_block` 改为强制上面的格式；qa 仍可读只读上下文、自由措辞。
  - 新增表 `clarifications`（迁移 `a1b2c3d4e5f6`）：每会话最多一条 pending，保存
    原始用户消息、问题、候选；**任何新的用户消息先删除它**，结构化选择则消费。
  - 新增 SSE `clarification` 帧与 `GET /sessions/{id}/clarification`（刷新恢复弹窗）。
  - `POST /chat/{id}` 请求增加 `clarification_id` / `choice`：选项走确定性映射并跳过
    JEV/大模型；`other` 或普通消息清 pending 后走正常判定。
  - 前端新增 `ClarificationDialog.vue`：候选按钮 + “其他”；关掉后回到输入框。
  - 选择候选时用原始用户消息执行对应形态，不拿“第一个”作为任务内容。
- **关键决策**：pending 是**一次性状态**，不靠“新任务”语义判断；只有明确 choice 才消费，
  其他任何消息都作废。qa 不直接执行/改文件/建方案。
- **验证**：
  - `tests/test_clarification.py` + `tests/test_intent_middleware.py`：**22 passed**；
  - 全量：**1528 passed / 13 failed**（仍是既有 `prompts.py` 10 条 + `boundary.py` 3 条）；
  - 前端 `npm.cmd --prefix frontend run build`：通过（`vue-tsc` + vite）。

#### 按角色分两档：qa/analysis 与主控 orchestrate 稳态 70%，plan/planner/coder/reviewer 爆发 40%

- **动机**：120K 绝对上限对读文件密集的 plan / planner / coder / reviewer 太小，日志里出现过
  `CONTEXT_PRETRIM` 313 次、`CONTEXT_SUMMARY` 只有 2 次的极端比例：每步都在裁，
  但 checkpoint 仍是完整历史，压不下去。qa / analysis 通常不会一次读大量文件，
  不该和代码类角色共用同一条线。
- **改动**：
  - `config.context_steady_trigger_fraction`（默认 **0.70**）与
    `config.context_burst_trigger_fraction`（默认 **0.40**）；`CONTEXT_SUMMARY_TRIGGER_TOKENS`
    降级为“窗口解析不出来时”的兜底线，不再作为大窗口模型的固定小上限。
  - `summarization.resolve_context_mode`：主控按 `form` 选（qa/analysis/orchestrate 稳态，
    plan/review 爆发），子智能体按角色选（planner/coder/reviewer 爆发）。主控与三个
    子智能体上下文隔离，主控 orchestrate 只收 `task` 的交接结果，不需要跟子智能体一起爆发。
  - 主控装配把 `profile.form` 传给 `build_summarization`。
  - `CONTEXT_SUMMARY` / `CONTEXT_PRETRIM` 的触发线文字带上「稳态 / 爆发」，便于按模式
    统计压缩与裁剪数量。
  - 更新 `.env.example`、README、AGENTS 与测试；稳态/爆发各钉一条触发比例，角色映射
    也单独钉住。
- **关键决策**：先只做角色分档，不接动态增长判定；下一轮再根据实际日志决定是否引入
  “读文件次数 / 增长速率”的动态切换。
- **验证**：
  - 定向（`tests/test_summarization.py` + `tests/test_context_calibration.py`）：
    **52 passed**；`ruff check` 干净。
  - 全量：**1514 passed / 13 failed**（仍是既有 `prompts.py` 10 条 + `boundary.py`
    3 条）；验证口径是同一会话里 `CONTEXT_PRETRIM` 与 `CONTEXT_SUMMARY` 的出现比例，
    以及两种模式的触发线日志。
- **遗留**：爆发档目前仍是“先裁剪、剪完低于线就不摘要”，如果实际日志里仍然频繁
  pretrim，再决定是否改成“每轮一次免压 pretrim，第二次越线强制摘要”。

#### 压缩保留清单 + 工作笔记 KEEP：裁剪从「只保当前步骤」改成「保模型点名的结果」

- **动机**：reviewer 连续被 `LoopGuardMiddleware` 中止的根因，是旧版应急裁剪只保当前
  步骤 —— 刚读过的关键文件在下一步就被换成占位符，模型只能反复重读同一批文件。用户口径：
  临近压缩线时先提醒模型总结自己读到的东西，并点名下一轮仍需要的工具返回；不再按
  「最后一条 `HumanMessage` 之后全部保留」切（子智能体整轮可能有一百多条工具返回，
  那样等于没裁）。
- **改动**：
  - 摘要提示词新增 `SUMMARY_MUST_KEEP`（插在默认提示词的 `<messages>` 之前）：逐字保留
    用户显式约束 / 纠正（含密钥时打码）、精确标识符、当前目标 / plan / PR / 分支 /
    commit、未完成事项、已确认 / 已否决决策与原因、失败与风险、工具名 + 目标 +
    `tool_call_id` 指针；明确完整文件 / diff / 日志可丢，长度 800~1200 字。
  - `context_pretrim_notice_ratio`（默认 **0.9**）进入运行期设置「上下文压缩配置」：
    到压缩线的提醒线（默认 90%）注入 `PRETRIM_NOTICE`，让模型在正常回复里写 `<working-note>` 和
    `KEEP:` 清单；同一轮、同一智能体只提醒一次。
  - `pretrim_tool_results(..., keep_items=...)`：保留**当前步骤 + KEEP 点名的工具返回**，
    支持 `tool_call_id`、`工具名:目标`、纯路径 / 命令；总额约 **20k tokens**，最近优先。
    子智能体那条任务 `HumanMessage` 只作为轮次边界，不会被当成「整轮全部保留」。
  - `keep_items_from_messages()` 只扫描当前轮最近的 AI 消息；旧轮次的 KEEP 不会被误用。
  - 提醒轮如果已经过压缩线，先不裁剪直接调一次模型，给它写笔记的机会；真实溢出再回落
    压缩。日志标签：`CONTEXT_PRETRIM_NOTICE` / `CONTEXT_PRETRIM`。
- **关键决策**：不额外调用模型做「工具选择器」；提醒发生在 90%，不是到线才提醒；
  KEEP 只影响本次请求，不改 checkpoint / 业务库 / `tool_call_id` 配对。
- **验证**：
  - 定向（`tests/test_context_trim.py` + `tests/test_summarization.py`）：**56 passed**；
  - 全量：**1509 passed / 13 failed**（仍是既有 `prompts.py` 10 条 + `boundary.py` 3 条）；
  - `ruff check` 本次改动文件干净。
- **遗留**：摘要调用目前绕过了 `ModelRedactionMiddleware`，密钥打码只靠提示词约束；
  下一步可在摘要输出上补代码级脱敏。真机还要看 reviewer 是否不再重复读同一批文件。

#### 低置信度转 qa 澄清（阈值 0.55，数据支持）

- **动机**：JEV / 大模型判得很虚时，直接按猜出来的形态干活风险高。用户口径：置信度低
  就转 qa，让 qa 先判断意图、按可能性排序后向用户追问；不新建“澄清智能体”。
- **阈值依据（200 条实测，JEV 163 条有 confidence）**：
  - `< 0.55`：16 条，原判定只对 **43.8%**；
  - `>= 0.55`：147 条，准确率 **91.2%**；
  - 对照：0.50 会问 11 条（36.4% 正确），0.60 会问 23 条（52.2% 正确）。
  - 0.55 落在“低置信准确率明显塌陷、追问量约 10%”的拐点上，所以取 0.55（不是拍脑袋）。
- **改动**：
  - `config.intent_clarify_confidence` 默认 **0.55**（0=关闭），并加进
    `RUNTIME_SETTINGS` 的「意图识别」组（设置页可调）。
  - `IntentDecision` 增加 `candidates` / `needs_clarification`；JEV 把
    `ChoiceAnswer.probabilities` 前 3 名带出来。
  - 大模型分类提示词改成四行：**意图 / 置信度 / 候选 / 理由**；`parse_reply`
    兼容旧两行格式，新增置信度与候选解析。
  - `service`：`active_task_override` 之后才做低置信判断（明确“继续/修复”优先，不追问）；
    命中后转 `qa` + `SOURCE_CLARIFY`，把 `confidence/candidates` 经
    `configurable.clarification` 传给中间件。
  - `IntentMiddleware` 最后注入「本轮先澄清意图」块：先只读了解上下文，列出 2~3 个按
    可能性排序的候选（各一句理由），最后只问一个问题；**不要执行/改文件/建方案**。
  - qa 的“任务”是**提示词澄清模式**，没有把 `write_todos` 还给 qa（qa 仍然没有待办树）。
  - `eval_intent.py` 增加 `--normal-only`（只跑现有判定）与 clarify 模拟统计；
    `run --out` + `policy` 继续用于固定轴离线调策略。
  - 新增/更新测试：`should_clarify`、`clarification_decision`、四行解析、JEV 候选、
    澄清块注入、阈值默认值。
- **验证**：
  - 真实 LLM 探针：四行输出可解析为 `analysis / 0.78 / qa 0.20, review 0.02`；
  - 真实 JEV 探针：候选能带出来（如 `qa 0.64 / analysis 0.16 / plan 0.12`）；
    但同一低置信样本两次跑出 0.21 / 0.57 —— **置信度本身不稳定**，0.55 是策略旋钮，
    不是校准保证；
  - 定向（意图判定/中间件/路由/设置）：**209 passed**；
  - 全量：**1499 passed / 13 failed**（仍是 `prompts.py` 10 条 + `boundary.py` 3 条既有失败）；
  - `ruff check` 干净。
- **遗留**：阈值是在 200 条上定的，样本再多要复核；JEV 的 confidence 会跨次抖动
  （同一句 0.21 / 0.57），大模型自报置信度也未校准 —— 想更稳可以看 top1-top2 的 margin
  或跑两次取一致；qa 是否真的按“排序追问”执行、追问率是否可接受，需要真机跑几轮看。

#### 意图路由：active_intent 进入策略表 + 固定轴评测

- **动机**：200 条评测里短追问/继续类错误集中在 `rework/review → coding` —— 根因是
  `suggest_intent()` 只收到 `previous_intent`，看不到会话里更早的未完成任务；同时路由轴
  每次跑都会抖动，盲调策略分不清「模型判错」还是「策略判错」。
- **改动**：
  - `suggest_intent(..., active_intent=...)`：短追问分两类 —— 选择题/确认（「b」「确认事实」
    「这个」）跟紧邻的上一轮；动作型（「继续」「修复」「实施」「执行」「开始」）跟未完成任务；
    长消息默认按未完成任务。
  - plan 上 `wants=change` + `target=execution/code` → coding（实施/执行方案不是改方案对象）。
  - 长句「也不对，你应该先查记忆、再派 coder 执行」若 `wants=change`，按新指令走，
    不因「不对」被当成单纯纠正。
  - 裸「为什么/根因/原理」→ analysis；明确的「有什么/是什么/有哪些」→ qa（即使 router 判成
    decide）；PR 已合并/已关闭的收尾不再要求 `target=pr_status/memory`，直接 analysis。
  - `service.py` / `eval_intent.py` 都传 `active_intent`。
  - `eval_intent.py` 新增 `run --out`（保存逐样本轴/建议）与 `policy`（用固定轴离线重算策略，
    不调模型）；`policy` 与 `run` 一样剔除 sync。
  - 新增 6 条策略测试。
- **关键决策**：不再靠 prompt few-shot 硬拉 —— 实测加示例后路由轴反而漂得更多
  （同一批 `coding→plan` 从 0 变 7），已回退到原来的单示例；先固定轴调确定性策略，
  再端到端复测。
- **验证**：
  - 端到端 200 条（多次）：85.6% / 88.1% / 88.9% / 91.0% / **89.7%**（最后一次 174/194，
    4 条解析失败、2 条 sync 跳过）；
  - 固定轴离线：一次 dump **173/189 = 91.5%**，另一次 174/194 = 89.7% —— 策略本身没问题，
    差异全部来自路由轴抖动；
  - 定向意图测试：**148 passed**；`ruff check` 干净。
- **遗留**：路由轴稳定性（4~9 条解析失败 / 同批样本漂移）与 qa/plan/analysis 标注边界
  （如「有哪些未完成的工作」按口径应 plan）仍是主要瓶颈；影子路由**不切线上**，
  下一步应做 hybrid gating（只在继续/短追问等价值区间采用路由建议）或先复核标注。

#### 思考与正文按到达顺序交错（前端）

- **动机**：用户要的是「思考（浅色）→ 正文 → 思考 → 正文」的流式交错；之前思考单独存在
  `Message.reasoning` 里，固定渲染在正文上方的一个可折叠块中，两套顺序无法交错。
- **改动**：
  - `Part` 新增 `{ kind: 'reasoning', agent, text }`；删掉 `Message.reasoning`、
    折叠状态与顶部思考块。
  - `attachStream` 的 100ms 缓冲改为**一条有序队列**：text / reasoning 按到达顺序入队，
    连续同一角色的思考合并；flush 按队列顺序写入 `parts`，不会被重排。
  - 渲染改 `flowEntries(m)`：连续 text 仍合成一个气泡（保留 Markdown 分组与封口优化），
    思考段原地插入；待办 / 方案仍固定在底部。
  - 样式：`--text-muted` 浅色、13px、`pre-wrap`、左侧细线；每段一个「思考过程」小标签，
    子智能体带角色；始终展开。
- **关键决策**：思考不落库、刷新即消失（沿用现有口径）；子智能体思考也交错展示（带角色）；
  **跑完隐藏思考、只留正文并 Markdown 化**（`flowEntries` 在 `m.at` 后跳过 reasoning，
  但不切断正文分组，前后正文仍合成一个 Markdown 气泡）；不做折叠。
- **验证**：`npm.cmd --prefix frontend run build`（vue-tsc + vite）通过。
- **遗留**：真机看一眼交错顺序；历史消息仍只有正文。

#### 修复：ReasoningChatOpenAI 丢工具调用（正文为空的真因）

- **现象**：接入思考后，模型实际要调两个 `execute`，应用里却是「空正文 + 0 工具」，
  看起来像模型只思考不干活。
- **根因**：父类 `_convert_delta_to_message_chunk` 只在 `role == "assistant"` **或
  `default_class == AIMessageChunk`（精确相等）** 时才解析 `tool_call_chunks`。
  我们返回 `ReasoningAIMessageChunk` 后，`_stream` 把 `default_chunk_class` 设成了这个子类；
  后续 tool-call chunk 的 `role` 是 `None`，于是走最后的兜底分支 —— 正文保留、
  工具调用被静默丢掉（模型没有停，是调用丢了）。
- **改动**：`_convert_chunk_to_generation_chunk` 在调用父类前，给缺少 `role` 的 delta 补
  `role="assistant"`，让父类走带 `tool_call_chunks` 的分支。
- **验证**：
  - A/B（同 payload、同工具）：旧 `ChatOpenAI` 返回 2 个 `execute`；修复前新类 0 个；
    修复后新类返回 2/1 个，且思考正文仍在；
  - 新增 2 条回归测试（无 role 的 tool-call chunk、思考与工具调用合并）；
  - 全量 `pytest -q`：**1488 passed / 9 failed**（仍是那 9 条既有失败）；
  - `ruff check` 干净。
- **遗留**：当前后端进程仍加载旧代码，需重启后再发一轮确认界面上的正文与工具调用。

#### 思考正文：ReasoningChatOpenAI 补回 reasoning_content

- **动机**：前端「思考过程」块一直空着。查下来不是网关不给：原始 SSE 的
  `delta.reasoning_content` 有文本，但 LangChain 的 `ChatOpenAI` 只面向官方 OpenAI 规范，
  模块 docstring 明写第三方字段（`reasoning_content` / `reasoning_details`）**不提取、不保留**；
  `_convert_delta_to_message_chunk` 只搬 `content` / `function_call` / `tool_calls`。
  `init_chat_model(provider="openai")` 造出来的就是 `ChatOpenAI`，不是工厂的问题。
- **改动**：
  - 新增 `agent/chat_model.py`：
    - `ReasoningChatOpenAI` 覆写 `_convert_chunk_to_generation_chunk`：从原始 chunk 的
      `choices[0].delta.reasoning_content` 取文本，累加进 `additional_kwargs`，并把消息换成
      `ReasoningAIMessageChunk`；
    - `ReasoningAIMessageChunk` 覆写 `content_blocks`，把这段文本还原成 reasoning 块。
      **只补 `additional_kwargs` 不够**：OpenAI 的 provider translator 只产出 text / tool_call。
  - `llm._model_from` 改用 `ReasoningChatOpenAI` 直接构造（主模型与兜底共用）；classifier
    仍是普通 `ChatOpenAI`（关思考）。
  - `pyproject.toml` 钉 `langchain-openai>=1.3.2,<1.4`（覆写的是私有钩子）。
  - 新增 `tests/test_chat_model.py` 6 条：reasoning 块、流式合并、无思考不加空块、
    已有块不重复、`build_model` 用子类、v3 事件里出现 `reasoning-delta`。
- **关键决策**：
  ① 不用 `ChatDeepSeek` 等专用包：只覆盖一家，Kimi / GLM / 自建网关仍没有；
  ② 不改 `response_metadata["model_provider"]` 去绕开 OpenAI translator —— 会破坏压缩中间件里
     「上一轮自报用量」的 provider 匹配；
  ③ 只补流式：主智能体走流式；摘要 / 分类的 `ainvoke` 不展示思考。非流式要补时再加
     `ReasoningAIMessage`，两处共用同一套 reasoning 块逻辑。
- **验证**：
  - 定向（chat_model + llm + 流式用量 + 压缩）：**76 passed**；
  - 全量 `pytest -q`：**1486 passed / 9 failed**（仍是那 9 条既有失败）；
  - `ruff check` 干净；
  - v3 事件层实测出现 `content-block-delta` → `reasoning-delta`，文本正确。
  - **真机探针**（2026-10-19，PG / 后端起来后）：`deepseek-v4-pro` 返回思考正文
    **122 字**、正文 `425`、用量 `96/40`（其中思考 38 tokens）；同一份流里
    `content-block-delta` 的 `reasoning-delta` 正确；`V3Translator` 产出
    `{"type": "reasoning", "content": "...", "agent": "main"}`。
- **遗留**：等下一次真实会话在界面上确认思考块；非流式响应仍不带思考（见模块注释）。

### 2026-10-18

#### 意图识别的模型构造独立出来

- **动机**：`build_classifier_model` 之前与主智能体共用 `init_chat_model` 工厂。意图识别那一行
  有自己的开关（思考 / `max_tokens` / 不挂工具），通用工厂的默认值会混进来；非法字段（如
  `stream`）还会被塞进 `model_kwargs` 并给出警告。
- **改动**：`agent/llm.py::build_classifier_model` 改为直接用 `ChatOpenAI(...)` 构造，不再经过
  `init_chat_model`；主智能体那条路不变。思考**关掉**（`extra_body` 用实测有效的
  `chat_template_kwargs.enable_thinking=False`，不走 `reasoning_effort`），温度按
  `temperature_for(cfg.model, 0)` —— 常规模型就是 0，kimi 系仍按 1（它只收 1）。
- **验证**：新增守卫用例——把 `init_chat_model` 换成会抛错的桩，classifier 仍能建出来；
  `test_llm_config.py + test_model_config.py + test_stream_usage.py`：**165 passed**；
  全量 `pytest -q`：**1480 passed / 9 failed**（仍是那 9 条既有失败）；
  `ruff check` 干净。
- **遗留**：无。

#### 上下文估算的在线校准（CONTEXT_CALIBRATION）

- **动机**：字符估算的固定系数（中文 1.6、其余 3.8）在不同 tokenizer 上偏差不同 —— 实测
  deepseek-v4-pro 低估约 5%，deepseek-flash 低估约 28%~32%。剪裁/压缩的绝对阈值与
  「剪完能不能免压」都吃这把尺子，差 30% 时 120k 的线实际约 154k 才触发。
- **改动**：
  - 新增 `agent/context_calibration.py`：
    - `CalibrationStore`：按 `(base_url, model)` 记 EMA 系数（α=0.3，样本与系数都夹在
      0.5~3.0）；估算 <1000 tokens 的请求不记（固定开销占主导）；首样本、每 10 个样本、
      系数变化 ≥5% 时留一条 `CONTEXT_CALIBRATION`。
    - `CalibratedTokenCounter`：先调 `context_token_counter` 拿原始估算，再乘当前系数；
      `record(response, estimated)` 从 `usage_metadata.input_tokens` 取真值。没有样本时
      系数为 1.0，行为与校准前完全一致。
  - `build_summarization` 的 `token_counter` 换成上述计数器（每个角色一份，账本共享）。
  - `HarnessSummarizationMiddleware` 在三条真正发请求的路径上记样本：正常调用、只有
    上一轮自报值过线而跳过压缩、应急裁剪后免压；压缩路径用**压缩后**的消息估算记样本。
  - 兜底模型答的响应不记到主导模型账上（`response_metadata.model_name` 不匹配就跳过，
    并提示一次「可能是兜底或网关别名」）。
  - 新增 `tests/test_context_calibration.py`（13 条）；`test_the_counter_is_ours` 改成
    断言「没有样本时与原字符估算逐字一致」。
- **关键决策**：
  ① 不用上游 `use_usage_metadata_scaling`：它拿「上一条 AI 消息的 total_tokens / 估算」
     当比例，而我们的应急裁剪会改写历史消息，剪过的历史与那笔真值对不上，系数会被放大；
     这里只认「刚发出去的那一份」。
  ② 记录时把**校准后的估算反推成原始估算**再算比例，避免把同一个系数乘两次。
  ③ 按模型而不是按角色：tokenizer 是模型属性；角色只写进日志。
  ④ 系数只存进程内，重启后重新学（通常几次调用就收敛）；没有真值就退回 1.0，
     不因为「没样本」而让压缩失效。
- **验证**：
  - 定向（校准 + 压缩 + 剪裁 + 流式用量）：**65 passed**；
  - 全量 `pytest -q`：**1478 passed / 9 failed**；9 条仍是 `prompts.py` 6 条 +
    `safety/policy.py` 3 条既有失败，本次未新增；
  - `ruff check` 改动文件干净。
- **遗留**：
  - 真实网关上还没看到系数收敛（本机 PostgreSQL 未启动，后端没跑）；下次启动后看
    `CONTEXT_CALIBRATION` 的 `ratio` 与 `CONTEXT_PRETRIM` 的估算差是否缩小。

#### 流式响应的 token 用量（stream_usage）

- **动机**：日志里大量「LLM 调用完成：（无用量信息）」，压缩的「上一轮自报值」触发与在线
  校准都拿不到真值。根因不是供应商不给：LangChain 的 `ChatOpenAI` 只在默认 `api.openai.com`
  时自动打开 `stream_usage`，本项目全走自定义 `base_url`，流式请求根本不发
  `stream_options.include_usage`；同一个 kimi 调用一次有、一次没有，就是流式/非流式路径的差别。
- **改动**：
  - `agent/llm.py::_model_from`：构造模型时显式 `stream_usage=True`（主模型与兜底共用这一处）。
  - 新增 `agent/middleware/stream_usage.py`：网关以 400/422 拒绝 `stream_options` 时，按
    `base_url` 记住并**重试一次**不带该字段的模型副本；同一地址后续直接降级，不再先撞一次。
    缓存是进程内模块级集合，重启后重试一次（网关升级支持就自动恢复）。
  - 主控与三个子智能体都挂（`agent/graph.py` / `agent/subagents.py`），位置在**兜底中间件
    内侧**、整条链最内层。
  - `tests/test_stream_usage.py` 新增 6 条：工厂开关、拒绝后重试并记住、下次直接降级、
    无关 400 不吞、422 同样识别、无地址不重试、降级后仍失败就抛。
  - `tests/test_model_fallback.py` 的装配断言同步：兜底由「最内层」改为「倒数第二层」，
    最内层是流式用量开关。
- **关键决策**：
  ① 默认打开、失败自动降级，不新增配置项、不动数据库 —— 这一步只解决「拿得到用量」；
  ② 重试放在兜底内侧：网关不认字段不是「主导模型挂了」，不能让 `FallbackMiddleware`
     记成主导故障而切模型；
  ③ 只对 400/422 且报错文本点名 `stream_options` / `include_usage` / `stream_usage` 的
     错误降级，别的错误原样抛；取不到 `base_url` 时不重试；
  ④ 降级只影响 usage 采集，估算与压缩判据不变 —— 没有用量的模型仍按字符估算工作。
- **验证**：
  - `tests/test_stream_usage.py + test_llm_config.py + test_model_fallback.py`：**62 passed**；
  - 全量 `pytest -q`：**1465 passed / 9 failed**；9 条仍是 `prompts.py` 6 条文案 +
    `safety/policy.py` 3 条未同步（工作区既有改动），本次未新增失败；
  - `ruff check` 本次改动文件干净。
- **遗留**：
  - 本机 PostgreSQL（127.0.0.1:5432）当时未启动，没能跑真实网关探针；后端起来后看日志
    应不再出现「（无用量信息）」，若某家网关拒绝字段会出现一条 `STREAM_USAGE` 告警并自动降级。
  - 「在线校准」（用真值给估算乘系数）是下一步，等这一步拿到稳定用量后再做。

### 2026-10-17

#### 会话 active task + 路由 JSON 重试（200 条 router-only 90.5%）

- **动机**：短追问（“继续吧”“B”“确认事实”）只看 `previous_intent` 不够 —— 上一轮可能是
  analysis/qa，但真正未完成的是更早的 coding/rework。另：200 条里有 7~8 条路由回复不是合法
  JSON，直接丢弃太浪费。
- **改动**：
  - `service` 增加会话级 `_active_tasks`：记录最近一次**可执行**意图（coding/rework/plan/review）；
    analysis/qa 不覆盖它。进程重启后从最近 10 条 assistant 记录的 `extra.intent` 回溯。
  - `intent.active_task_override()`：只在显式“继续/修正/短追问/短选择”时沿用 active task；
    上一轮是 plan 而用户说“修复/实施/执行”时落到 coding；正常问句不碰。
  - 系统提示新增“当前未完成任务：…”；普通判定、handoff 重判、影子路由都带上。
  - PR 已合并/已关闭后的 analysis 收尾、关闭/删除方案时清空 active task。
  - `intent_router.route()` 对解析失败**重试一次**（只对失败路径，正常路径不多付调用）；
    重试仍失败才返回 None。
  - `eval_intent.py` 新增 `--ids`（迭代错例）、`--router-only`（只跑路由），
    `enrich` 补 `active_intent`。
- **评测（200 条，全部标注 + 两轮对话）**：
  - 现有判定：**88.0%**（未重跑全量，沿用上一轮）
  - 路由 router-only：**90.5%**（172/190；8 条解析失败、2 条 sync 跳过）
  - 路由相对 v3 的 89.5% 再 +1.0；相对现有判定 +2.5。
  - 硬错例子集（17 条）：normal / router 都从 7/17 提到 8/17；解析失败 3→2。
- **关键决策**：
  ① active task 只记**可执行**意图，analysis/qa 不覆盖；避免一次解释轮把 coding 任务冲掉；
  ② 只有显式继续/短追问才兜底，正常问句仍交给 JEV/LLM；
  ③ JSON 重试只在解析失败时发生，不让正常路径多付一次调用；
  ④ 路由仍**不切换形态**，继续影子模式（领先 2.5 点但仍有 8 条解析失败）。
- **验证**：
  - 意图 + 路由 + 中间件 + 记忆闸门定向：**184 passed**；
  - 全量 `pytest -q`：**1459 passed / 9 failed**；9 条仍是 `prompts.py` 6 条文案 +
    `safety/policy.py` 3 条未同步（工作区既有改动）；
  - `ruff check` 改动文件干净。
- **遗留**：
  - active task 仍只有 intent、没有 target/PR/plan_id；“继续吧”在上一轮 plan、更早 coding
    的样本里仍会有歧义，需要真正的 task 对象与完成状态；
  - 8 条 JSON 解析失败说明重试还不够，下一步可加更严格的输出约束或第二次修复提示；
  - 200 条样本量仍小，扩到 300~500 条人工复核后再决定是否切换路由。

#### 路由策略 v3：短追问继承上一轮、实施方案归 coding、只观察归 analysis

- **动机**：200 条完整评测里，剩余错例集中在短追问（“B”“确认事实”“修复吧”）、
  “按方案实施/开始实施”和“只想知道判定结果/不要做”。这些不是词表问题，而是
  **短句语义 + 上一轮上下文** 的策略问题。
- **改动**（`server/intent_router.py`）：
  - 明确“实施方案”命令（`开始实施 / 按方案实施 / 执行这个方案 / 按这个方案做`）归 coding；
    若上一轮已是 rework/coding，则沿用上一轮（“开始实施”在 PR 返工里仍是 rework）。
  - 短追问（≤8 字，“B / 确认事实 / 修复吧”）在 `target=plan` 漂移时，默认沿用上一轮可执行
    意图；其中“修复/实施/执行/做/改”在上一轮是 plan 时归 coding（实施/修复代码，不是改方案文本）。
  - “只想知道 / 只看 / 判定结果 / 不要做”在 target=execution 时归 analysis；疑问句
    （是不是/能不能/是否/吗/呢）不按 change 处理。
  - “如何初始化/怎么启动/方法/步骤”这类 general 问题归 analysis；“短信模板有什么建议”
    这类 general + decide 归 qa。
  - 修掉 `_CONTINUE_MARKERS` 的误命中：“不是”不再命中“是不是”（后者是疑问句）。
  - `eval_intent.py` 新增 `--ids`（只跑指定错例）与 `--router-only`（只跑路由，节省一半调用）。
- **完整评测（200 条，两轮对话 + 全部标注）**：
  - 现有判定：**88.0%**
  - 路由 v3（router-only）：**89.5%**（171/191；7 条 JSON 解析失败、2 条 sync 跳过）
  - 路由首次小幅反超 1.5 个百分点；但样本只有 200 条、仍有 7 条解析失败，**继续影子模式**，
    不切形态。
- **验证**：
  - `pytest tests/test_intent_router.py tests/test_intent_detection.py`：**124 passed**；
  - 全量 `pytest -q`：**1450 passed / 9 failed**；9 条仍是 `prompts.py` 6 条文案 +
    `safety/policy.py` 3 条未同步（工作区既有改动）；
  - `ruff check` 改动文件干净。
- **遗留**：
  - 短追问仍受“上一轮 intent 与用户期望不一致”影响（例如“继续吧”上一轮是 analysis、
    用户却想继续 coding），需要 active task 状态而不是只看一条 previous_intent；
  - 7 条路由 JSON 解析失败可加重试/更宽松解析；
  - 路由器只在 AI/人工混合标注的 200 条上领先 1.5 点，扩大样本后再决定切换。

#### 意图上下文改“最近两轮对话” + 语义口径收口（200 条完整评测）

- **动机**（用户口径）：只看当前消息 + 上一轮回复不够 —— 有一条“继续把”的上一轮 AI
  回复是“账户余额不足（429）”，少一轮上下文根本判断不出这是未完成的编码轮。要求改为
  **最近两轮对话 + 本轮问题**：用户消息全文，助手消息首尾各留一段。
- **改动**：
  - `dao.recent_dialogue`：按时间正序取最近 4 条 user/assistant 记录；`service._dialogue`
    把用户消息保留全文、助手消息用 `head_tail_excerpt`（前 200 / 后 300 字）截断，
    传给普通判定、`handoffTask` 重判与影子路由。
  - `intent._user_prompt` / `intent_router._user_prompt` 都优先渲染“最近两轮对话”；
    老的 `history` / `previous_reply` 仍保留作兼容。
  - 语义口径写进 `_INTENT_SPECS` / `_BOUNDARY` / 路由提示词：
    * **analysis**：解释功能、解释用户疑问、刷新仓库记忆、**PR 已合并/已关闭之后的收尾动作**
      （更新记忆、同步、清理）；不是“刚提交/待合并”的 PR；
    * **qa**：只对**已有数据**做问答（已具备哪些功能、有哪些 PR、文件/提交/记忆里写了什么）；
    * **plan**：计划、方案、任务完成度**一律**归 plan；
    * 用户说「继续 / 接着 / 修正 / 纠正 / 重来 / 再试一次」→ 按未完成轮次沿用上一轮可执行意图。
  - `suggest_intent` 增加显式的 continuation/correction 标记、PR 合并/关闭标记；
    `target=plan` 一律 plan；`general + decide` 归 plan。
  - `eval_intent.py` 的 `enrich` 改为补最近两轮 `dialogue`（用户全文、助手首尾截断），
    同时保留 `previous_intent` / `previous_reply`；`format` 统一缩进 JSON 数组。
  - `docs/intent-eval.jsonl` 已补齐 200/200 条标注：其中“继续把”（之前因余额不足看不出）
    按“继续=未完成轮次”标成 coding；另外三条查看提交/记忆原文标 qa、新增收货地址标 coding。
- **完整评测结果**（200 条人工/AI 混合标注，含两轮对话）：
  - 关键字口径：**38.0%**
  - 现有判定：**88.0%**
  - 影子路由：**88.0%**（169/192；6 条 JSON 解析失败、2 条 sync 跳过）
  - 结论：两轮对话把现有判定从 83.3% 抬到 88.0%；路由器目前**持平而不是反超**，
    所以继续影子模式，不切形态。
  - 剩余错例集中在短追问（“B”“确认事实”“继续吧”“修复吧”）、“按方案实施”被看成 plan、
    “你作为主智能体不应该自己写 code”这类反馈被看成 change；属于策略表/短句语义问题。
- **验证**：
  - 意图 + 路由 + 中间件 + 记忆闸门定向：**167 passed**；
  - 全量 `pytest -q`：**1442 passed / 9 failed**；9 条仍是 `prompts.py` 6 条文案 +
    `safety/policy.py` 3 条未同步（工作区既有改动）；
  - `ruff check` 改动文件干净。
- **遗留**：
  - 路由器与现有判定持平，暂不切换；下一步优先处理短追问与“实施方案”这两类错例；
  - 6 条 JSON 解析失败可以加重试/更宽松的解析；
  - JEV 置信度阈值与 active task 状态仍未做。

#### 影子路由器 v2：lookup/analyze 拆轴 + 首轮真实评测（84 条）

- **动机**：按计划跑第 1、2 步。先导出 200 条真实用户消息、预标 84 条，跑关键字 / 现有判定 /
  影子路由三路；首轮错例显示 `qa` 与 `analysis` 被一个 `explain` 混在一起，“按方案实施”
  被路由当成 `plan`。这两处是**轴的问题**，不是某句话的词表问题。
- **改动**：
  - `intent_router.py` 的 `wants` 从 `change / explain / decide / unknown` 拆成
    `change / lookup / analyze / decide / unknown`：查看/列举/是什么是 `lookup`（qa 倾向），
    为什么/根因/怎么跑通是 `analyze`（analysis 倾向）。
  - 路由提示词补三条总则：`target=plan` 只表示方案对象本身，实施/执行方案归 `code`；
    “对助手上一轮行为的反馈/批评”归 `meta_question + execution`；最后给了一个明确的 JSON 示例。
  - `suggest_intent()` 按新轴重写：new_task + lookup + code → qa；+ analyze → analysis；
    follow_up/meta_question + plan + lookup/decide → plan；+ analyze → analysis；
    status 更新带 `previous_intent` 时优先沿用上一轮可执行意图。
  - `scripts/eval_intent.py` 修了三处独立脚本问题：`extra` 在 `SessionRecordDetail` 而不是
    `SessionRecord`；Windows 下 psycopg 异步要 `SelectorEventLoop`；模型连接在库里，脚本要
    先 `apply_model_endpoints` 只读载入。新增 `enrich` 子命令给已有标注补上一轮
    `previous_intent` / `previous_reply`，以及 `run --limit`、`format`（把 JSONL 统一成
    缩进 JSON 数组，读取端两种都认）。
  - 影子路由模型：优先「意图识别（大模型）」那一行；没配就借一条已配置的 agent 行
    （qa → analysis → coding → plan → review），保证原型能跑。
- **首轮结果**（84 条 AI 预标，**待人工复核**）：
  - 关键字口径：**52.4%**
  - 现有判定：**83.3%**
  - 影子路由：**86.2%**（80 条可评；3 条 JSON 解析失败、1 条 sync 跳过）
  - 主要剩余错例：两条“为什么不派 coder / 上一轮不是 rework 吗”被 policy 判 analysis
    （产品语义可改）；“开始实施 / 修复吧”仍偶尔把 target 看成 pr_change/plan；
    “你作为主智能体不应该自己写 code”这类反馈被看成 change；sync 的“代码拉取到本地”
    不在关键字表里。
- **关键决策**：
  ① 路由器仍**不切换形态**，只记录；这轮结果只说明“轴 + 策略表”方向可行，样本还是 AI 预标；
  ② 先修轴与提示词，不回到逐句加关键词；
  ③ 真实评测文件继续 gitignore，示例文件进库。
- **验证**：
  - `pytest tests/test_intent_router.py`：**15 passed**；
  - `pytest tests/test_intent_detection.py`：**94 passed**；
  - 全量 `pytest -q`：**1435 passed / 9 failed**；9 条仍是 `prompts.py` 的 6 条文案 +
    `safety/policy.py` 3 条未同步（工作区既有改动）。
  - `ruff check` 改动文件干净。
- **遗留**：
  - 人工复核 84 条 AI 预标（尤其两条 meta-question 的“解释 vs 继续”口径），再扩大样本；
  - 根据错例继续调策略表：status 更新延续上一轮、反馈类 meta_question、sync 口令；
  - 路由结果稳定优于现有判定后才切形态；JEV 置信度阈值仍未做。

#### 意图评测与影子路由器：先量再改，不直接切形态

- **动机**（用户口径）：上一轮的关键词/提示词整改像打补丁，只能解决单个说法。确认先做
  第 1、2 步：建真实评测集与基线；加一个只记录、不改变线上形态的影子路由器。
- **改动**：
  - 新增 `server/intent_router.py`：把一句话拆成 `speech_act` / `wants` / `target` /
    `continuation` 四个正交轴，模型只输出 JSON；`parse_router_reply` 容错代码围栏与
    前后解释，`suggest_intent` 是一版**集中式策略表**（轴 → 建议意图）。
  - `service.py`：`INTENT_ROUTER_SHADOW=true` 时在独立 task 里跑一次影子路由，只记
    `INTENT_ROUTER normal=… source=… axes=… suggested=…`；**不采用建议、不改变 intent**，
    失败只记日志。默认关闭，打开后多一次分类模型调用但不占本轮首字节。
  - `config.py` / `.env.example`：`INTENT_ROUTER_SHADOW`（默认 false）与
    `INTENT_ROUTER_TIMEOUT_SECONDS`（默认 5s）。
  - 新增 `scripts/eval_intent.py`：
    - `collect` 从业务库只读导出最近用户消息（`expected` 留空待人工标）；
    - `run` 跑关键词 / 现有判定 / 影子路由三路，输出准确率、混淆矩阵与最多 20 条错例；
    - 不写库、不改线上行为。
  - 新增 `docs/intent-eval.md` 与 `docs/intent-eval.example.jsonl`；真实样本
    `docs/intent-eval.jsonl` 默认进 `.gitignore`，避免把用户消息/代码片段提交到公开仓库。
  - `tests/test_intent_router.py`：解析、轴 → 意图策略、假模型端到端路由。
- **关键决策**：
  ① 影子层只观测，不参与判定；评测证明更准之前不切形态；
  ② 策略表只依赖轴，不依赖具体措辞，新说法落在同样的轴上就能复用；
  ③ 真实评测文件不进库，示例文件进库；
  ④ 评测脚本只读业务库，`run` 完全不碰库。
- **验证**：
  - `pytest tests/test_intent_router.py`：**14 passed**。
  - 意图 + 中间件 + 记忆闸门定向：**159 passed**。
  - `scripts/eval_intent.py run docs/intent-eval.example.jsonl`（关闭模型，仅关键字口径）
    可正常输出 60% 基线与混淆矩阵。
  - 全量 `pytest -q`：**1434 passed / 9 failed**；9 条均为工作区里的既有改动 ——
    6 条 `prompts.py` 文案测试，另 3 条是 `safety/policy.py` 20:07 改成
    orchestrate 隐藏 `edit_file/write_file`、analysis 隐藏 `write_todos` 后
    `tests/test_boundary.py` 还没同步（不是本次评测/路由改动引入）。
  - `ruff check` 改动文件干净。
- **遗留**：
  - 需要跑 `collect` 并人工标注一批真实样本，确认路由器的轴判得稳，再决定是否切形态；
  - JEV 置信度/差距阈值仍未做；
  - `policy.py` 与 `prompts.py` 的既有改动要各自收口，测试才能全绿。

#### 意图判定第一批准确率整改：默认 coding、追问不再误落 analysis、带上“上一轮判定”

- **动机**（用户口径）：确认先做「对齐默认值 / 收窄为什么 / 把上一轮意图喂给判定器」。
  实测「你为什么不派 coder 执行」被 JEV 判成 analysis，于是拿到只读形态、真的没有
  `task`；而「上一轮，你的意图不是 rework 吗」这类追问又缺少上一轮判定的锚点。
- **改动**：
  - `DEFAULT_INTENT` 从 `qa` 改回 `coding`，与注释、README 和测试口径一致；判不出来时
    按「用户多半是要干活」处理。
  - `analysis` 规则新增排除词 `为什么不 / 为什么没 / 为什么没有 / 怎么没`：追问上一轮动作
    不再因为裸的「为什么」掉进 analysis；「为什么这里会超时」这类真正的排查问题仍保留。
  - `_BOUNDARY` 增加一条“对上一轮动作的追问”判据：先看上一轮意图与回复，判断用户是
    要继续/纠正动作还是只要解释，**不要仅因为出现「为什么」就判 analysis**。
  - 判定上下文新增 `previous_intent`：`service._last_intent()` 从最近一条 assistant
    记录的 `extra.intent` 读取，`detect_intent` 同时传给 JEV 与大模型两条路
    （`_user_prompt` 里显示“上一轮判定：…”），重判/`handoffTask` 也带上。
  - 顺手修正瀑布分支：`force_llm=True` 现在真的跳过 JEV；`mode=jev` 但 JEV 没配齐时
    直接落关键字口径、不再借用大模型；`force_llm` 但没有可用模型时来源标 `fallback`。
    删除 JEV 异常分支里的调试 `print` 与未使用的 `llm` 导入。
- **关键决策**：
  ① `previous_intent` 只作上下文，不硬编码成当前意图 —— 纯解释类追问仍可判 analysis；
  ② 仅排除“为什么不/为什么没”这类元问题，不把「为什么」整体拿掉，避免误伤真正的排查；
  ③ 默认值与瀑布分支的修正属于“判得准”的前提，和提示词整改一起做。
- **验证**：
  - `pytest tests/test_intent_detection.py`：**94 passed**。
  - `pytest tests/test_memory_gate.py tests/test_intent_detection.py`：**135 passed**。
  - 全量 `pytest -q`：**1423 passed / 6 failed**；6 条全部是工作区里 `prompts.py` 的既有
    文案改动（`MAIN_AGENT_PROMPT` / `TODO_PROMPT` / analysis 提示词），与本轮意图整改无关。
  - `ruff check` 改动文件干净。
- **遗留**：
  - JEV 置信度/差距阈值与真实评测集尚未做；下一步按计划补“低置信度不静默定案 + 混淆矩阵”。
  - `prompts.py` 的 6 条既有失败等提示词那批改动收口后再对齐。

#### 每轮注入“本轮能力”：形态 / 有无 task / 可派子智能体

- **动机**（用户口径）：rework 轮里模型沿用了上一轮 analysis 的“我是只读形态、没有 task”
  结论，明明 `form=orchestrate` 且工具列表里有 `task`，却没有派 coder；最新一条
  「你为什么不派 coder 执行」又被 JEV 判成 analysis，模型于是继续说没有 task。需要把
  **当前这一轮真实能力**写进系统提示，并明确“上一轮的结论不适用于本轮”。
- **改动**（`agent/middleware/context_inject.py`）：
  - 新增 `capability_block()`：注入“意图 / 形态 / `task` 有没有 / 可派哪些子智能体”，
    并加一句“上一轮如果说过没有 task、只读或不能改代码，那是上一轮的形态，不适用于本轮”。
  - `task` 是否存在**以本次模型调用实际绑定的 `request.tools` 为准**，不按意图猜；
    子智能体名单取自 `profile_for(intent).subagents`。
  - `IntentMiddleware` 的注入顺序变成：原系统提示 → 当前时间 → 意图指令 → 本轮能力
    （能力块放最后，压过历史里上一轮的自我描述）。
  - 测试补两条：rework + 三从时写“有 task / planner / coder / reviewer”；
    analysis 时写“没有 task / 可派子智能体：无”。
- **关键决策**：
  ① 只做能力注入，不改意图判据（用户口径：先只做 1）；
  ② 能力以实际工具列表为准，装配与提示不一致时模型看到的是真实情况；
  ③ 不把“上一轮意图”写死成当前意图，避免把追问强行拉回旧形态。
- **验证**：
  - `pytest tests/test_intent_middleware.py`：**10 passed**。
  - `ruff check` 改动文件干净。
- **遗留**：JEV / 大模型的意图判据尚未调整；`DEFAULT_INTENT` 当前实际值是 `qa`，
  与注释/测试里的 `coding` 不一致，属于既有未完成改动，后续一起收。

#### 思考内容上界面：主控与子智能体都展示，只走 SSE、不落库

- **动机**（用户口径）：把模型的思考内容展示到界面上，与正文从样式上区分开；**数据库不存**；
  任务结束时正文一次性 Markdown 化。子智能体的思考先加上，用来判断它到底有没有价值。
- **改动**：
  - `stream.py`：`reasoning-delta` 不再丢弃，改为下发瞬时事件
    `{"type": "reasoning", "content": …, "agent": "main|coder|…"}`；主控与子智能体都发，
    中间件内部调用（摘要等）继续丢弃。**不写入 `StreamParts`**，所以不会进 `parts`。
  - `server/service.py`：reasoning 先过 `redact()` 再发 SSE；**不累加进 `text`**、不进
    `extra.parts`、不进业务记录。重连时靠 RunHub 的临时帧缓冲重放，服务器进程/Redis TTL
    过后自然消失。
  - 前端 `api.ts` / `App.vue` / `style.css`：新增 `reasoning` 事件与按角色分段的思考块
    （主控 / planner / coder / reviewer），灰色弱化背景 + 左侧细边框，和正文明确区分；
    运行中默认展开，任务结束后默认收起，可手动展开；标题旁标“仅本次运行可见，不保存”。
  - 正文渲染：流式期间按纯文本显示（`white-space: pre-wrap`，不解析 Markdown）；任务
    结束（`m.at` 落上）后，每个连续正文组拼回完整字符串，**一次性** `renderMarkdown`，
    避免半截列表 / 表格 / 代码围栏逐帧闪烁。
  - 测试：`test_stream_v3.py` 覆盖主控 / 子智能体 / 内部 reasoning；`test_plan_events.py`
    覆盖 SSE 下发顺序与角色；`test_redaction.py` 覆盖思考内容脱敏且不落记录。
- **关键决策**：
  ① 子智能体思考也展示（用户要先看效果），前端按角色分段而不是混成一段；
  ② 思考只在前端与临时帧缓冲里，**不写 PostgreSQL**；刷新后历史记录里没有；
  ③ 正文 Markdown 延迟到任务结束一次性渲染，牺牲一点“边流边排版”的观感，换稳定输出；
  ④ 推理内容同样走脱敏，不能因为换了通道就漏凭据。
- **验证**：
  - 定向 `pytest tests/test_stream_v3.py tests/test_plan_events.py tests/test_redaction.py`：
    **145 passed**。
  - 全量 `pytest -q`：**1415 passed / 9 failed**；9 条均为本轮之前工作区里的未提交改动
    （6 条 intent 口径 + 3 条 `TODO_PROMPT` / analysis 提示词测试），与本轮思考展示无关。
  - `npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过。
  - `ruff check` 改动文件干净。
- **遗留**：
  - 真机看一轮：子智能体的思考是否真的有用、长度是否吵；若噪音大，下一步加“只看主控”
    或按角色折叠的开关。
  - 未提供 reasoning 的模型（部分 OpenAI 兼容网关）这一块自然为空，不做占位。
  - 刷新发生在运行中时，临时缓冲里已产生的思考会重放；更早被 TTL 清掉的部分不会补。
  - RunHub 的帧缓冲上限仍是 2000 帧，思考分片会多占帧；真机若发现长思考把早期正文挤出
    重连窗口，再把 reasoning 改成按 100ms / 200 字合并下发。

#### 删除常规剪裁：只在压缩线做一次应急裁剪

- **动机**（用户口径）：「常规剪裁可以不要。」上一版虽然把「压缩前应急裁剪」放进了压缩
  中间件，但平时的 40k / keep 12 剪裁仍是一个独立中间件；用户要的是**只在到达压缩线时
  剪一次**，不要平时那层。
- **改动**：
  - 删除 `HarnessContextTrimMiddleware`、`build_context_trim`、`context_trim_roles`；
    `agent/graph.py` / `agent/subagents.py` / `session/manager.py` 不再接收、也不再挂
    `context_trim`，运行时栈里只剩压缩中间件。
  - `agent/middleware/context_trim.py` 退成纯函数模块：保留 `pretrim_tool_results`、
    `context_token_counter`、占位符与计数器；不再依赖 `ContextEditingMiddleware` /
    `ClearToolUsesEdit`。
  - `config.py` 删除 `context_trim_trigger_tokens` / `context_trim_keep` /
    `context_trim_roles`；`.env.example` 对应的三个键标成已作废。保留
    `CONTEXT_TRIM_TOOL_RESULTS`（应急裁剪总开关）与 `CONTEXT_TRIM_CLEAR_INPUTS`。
  - 测试删掉常规剪裁那一批，保留应急裁剪与计数器/占位符测试；装配测试改为断言
    「没有独立剪裁中间件」。
  - README / AGENTS.md / docs 同步为「平时不剪，只在压缩线剪」。
- **关键决策**：
  ① 完全按用户口径删掉常规剪裁，不把它并进压缩中间件保留 40k 阈值；
  ② 应急裁剪仍跟随 `CONTEXT_TRIM_TOOL_RESULTS`，关掉就退回上游「到线直接压」；
  ③ 40k～120k 之间不再剪，历史工具结果会随每次调用重发 —— 这是换取简单结构的代价，
     真机如果发现 token 成本/延迟明显上升，再考虑恢复一个单阈值剪裁。
- **验证**：
  - 定向 `pytest tests/test_context_trim.py tests/test_summarization.py tests/test_app_settings.py`：
    **63 passed**。
  - 全量 `pytest -q`：**1413 passed / 7 failed**；7 条仍是本轮之前就存在的 intent / analysis
    提示词测试（同上一条的既有失败），本轮相关测试全绿。
  - `ruff check` 改动文件全干净；`scripts/check_prompts.py` 0 冲突。
- **遗留**：
  - 真机观察一轮：40k～120k 区间的输入 token 与延迟变化、`CONTEXT_PRETRIM` 的免压率。
  - 若成本不可接受，最小回退是把「常规剪裁」作为一个**可选**的单阈值开关加回同一个压缩
    中间件，而不是恢复独立中间件。

#### 压缩节点优先应急裁剪工具返回：剪完不到线就不摘要

- **动机**（用户口径）：「到达上下文压缩的节点时，优先进行工具裁剪；如果裁剪后已经达不到压缩的
  范围，那就不进行压缩；如果达到了，那就继续压缩。」现状是 `context_trim` 已经在压缩外侧，但
  它保留最近 `CONTEXT_TRIM_KEEP=12` 条，最近 12 条工具返回自己就可能把上下文顶过压缩线；而且
  上游判断还会看最后一条 AIMessage 的 `usage_metadata.total_tokens`（上一轮的真值），剪完仍可能
  被旧值强制压缩。
- **改动**：
  - `agent/middleware/context_trim.py` 新增 `pretrim_tool_results()`：只在压缩线处调用，保留
    **末尾连续的 ToolMessage**（当前这一步，含并行调用的多条），其余工具返回换成带身份占位符；
    不删消息、不改 `tool_call_id`、不动状态。`keep_recent` 默认 0（逃生门，当前不开放配置）。
  - `agent/summarization.py` 重构 `HarnessSummarizationMiddleware.awrap_model_call`：复刻上游
    前半段（取有效消息 → 计数 → 截断工具入参），到压缩线后先做应急裁剪；用与压缩同口径的
    计数器复算，**低于线则直接调用模型、本轮不摘要**，仍高于才走上游压缩。新增
    `_should_summarize_estimate()`（副本里清掉最后一条 AIMessage 的 `usage_metadata`，只按
    估算值判断，避免上一轮自报值强制压缩）；免压路径捕获 `ContextOverflowError`，真实溢出
    时回退到压缩。新增 `CONTEXT_PRETRIM` 日志（剪几条 / 剪前剪后 / 免压还是继续压）。
  - `build_summarization` 把 `pretrim` 跟随 `CONTEXT_TRIM_TOOL_RESULTS`、把
    `pretrim_clear_inputs` 跟随 `CONTEXT_TRIM_CLEAR_INPUTS` 传入。
  - 文档：`README.md`、`.env.example`、`docs/config-page-design.md` 各补一句行为说明。
- **关键决策**：
  ① 应急裁剪只保**当前步骤**、不再保最近 N 条 —— 目标是最大化「免压」概率；占位符带身份，
     配合 2026-10-16 的「你已经读过它」口径降低重读。
  ② 仍超线时**从预剪前那份历史开始压缩**：摘要与 `/conversation_history` 保留更多原文，
     不把预剪占位符带进摘要；预剪只承担「能否免压」这一次判断。
  ③ 跟随 `CONTEXT_TRIM_TOOL_RESULTS` 总开关：关掉工具裁剪就退回上游原来的「到线直接压」。
  ④ 供应商自报 token 只用来触发「到压缩节点」，剪完后按估算值决定；估算偏小导致真实调用
     溢出时，回退压缩兜底。
- **验证**：
  - 定向 `pytest tests/test_context_trim.py tests/test_summarization.py`：**60 passed**
    （新增 14 条：应急裁剪 8 条 + 压缩前裁剪 6 条）。
  - 全量 `pytest -q`：**1427 passed / 7 failed**；7 条均为本轮之前就存在的未提交改动所致
    —— 6 条 `tests/test_intent_detection.py`（2026-10-16 条目已记录的 JEV / 关键字口径冲突），
    1 条 `tests/test_prompts.py::TestQaHasNoTodoList::test_analysis_keeps_the_list`
    （未提交的 `prompts.py` 已把 analysis 的 `TODO_PROMPT` 摘掉，测试仍按旧口径要求它）。
    本轮改动的剪裁 / 压缩相关测试全绿。
  - `ruff check` 改动文件（含测试）全干净。
- **遗留**：
  - 真机观察一轮：`CONTEXT_PRETRIM` 的免压率、以及「只保当前步骤」是否让模型重读变多；
    若重读上升，把 `PRETRIM_KEEP_RECENT_TOOL_RESULTS` 调到 1~2 再量。
  - 摘要 offload 仍从正常剪裁后的消息出发（老工具结果可能是占位符）——这是本次之前就存在的
    交互，本次没有把「原文另存一份给 offload」一起做；要做需在压缩路径保留原始内容映射。

### 2026-10-16

#### 借鉴 codebase-analyzer：analysis 形态新增 code-tracing-analysis skill 与三条取证纪律

- **动机**：用户给了外部 skill（github.com/AthenDrakomin-hub/agent-skills 的 `codebase-analyzer`，
  SCOPE → ROUTE → EFFECT → BREAK → SHIP）让看有什么可借鉴。对照后方法论有一半与本项目重合
  （只读边界、检索纪律、边取证边下结论、repo-bootstrap-analysis 的结构摸底），但四样是本项目缺的、
  且正对着踩过的坑：① 证据分级与激活轴（「实现存在 ≠ 已装配 ≠ 当前选中 ≠ 线上在跑」）；② 效果主张的
  分支门（调用表达式只证明「发起了调用」）；③ 否定式收口门（不勾「我已确认」，查正文里可观察的缺失）；
  ④ 上下文检查点（与 2026-10-16 剪裁整改同一根因的另一解法 —— 模型在对话里自己发明过一次
  `/ai_coding/tmp/auth-evidence.md`，但没进提示词）。
- **改动**：
  - 新增 `sandbox/skills/code-tracing-analysis/`（SKILL.md + references/claim-ledger.md /
    closure-card.md / final-audit.md）：方法论中文版、证据权限表、激活轴、责任闭合卡、callee 分支门、
    否定式收口门、PARTIAL/BLOCKED 与检查点口径；重方法论放 references 按需读。
  - `agent/prompts.py::_ROLE_ANALYSIS` 分析工作法加三条：证据状态四档 + 激活轴（第 4 步）、效果主张
    分支门（第 5 步）、检查点写 `/ai_coding/tmp/evidence-ledger.md`（第 7 步）；成篇顺序改成
    「范围与证据状态 → 直接答案 → 最短主链 → 未知与验证动作」（第 8 步）；并加一句流程 / 行为类问题
    指向新 skill。系统提示词只承担这几句，闭合卡与审计清单在 skill 里按需读，不每轮付 token。
- **关键决策**：
  - **不借的**：它「不做诊断 / 不出方案」的边界（本项目 analysis 要做可行性、喂方案）；「每条答案都带
    证据矩阵大表」（聊天区太吵，只借首屏顺序）；「不得自设步数上限」（与平台 recursion_limit 冲突，
    改成检查点 + PARTIAL）。
  - 原第 5 步「区分已证实与待验证」并进新第 4 步的四档，避免两套口径；「已证实」字样保留
    （tests/test_profiles.py 钉着）。
- **验证**：
  - `scripts/check_prompts.py` 冲突扫描 **0 条**；改动文件 `ruff check` 干净。
  - 全量 pytest **1414 过 / 6 败**：败的 6 条全是 `tests/test_intent_detection.py` 的既有失败
    （上一会话未提交的 `server/intent.py` 改动与老测试口径冲突，与本轮无关，按约定不顺带修）；
    本轮一度改坏 `tests/test_profiles.py::test_analysis_form_is_the_one_readonly_form_that_writes_the_memory`
    （删掉了它钉的「已证实」措辞），已在同轮补回并复跑通过。
  - 行为面未验证：skill 是**建会话时**推入沙箱、提示词要**重启后端**才生效。
- **遗留**：
  - 6 条 intent 既有失败等 JEV 整改那份方案落地时一起收。
  - 模型是否真读新 skill、填闭合卡，要在下一轮 analysis 里观察；不读的话把「首屏顺序」提一句进
    `_READONLY_RETRIEVAL` 硬规则。

#### 代码块横向滚动条不再掉出屏幕：pre 加高度上限

- **动机**：上一节把超宽气泡封顶后，长代码行改在 `pre` 内横向滚；但横向滚动条画在容器底边，
  代码块一高（几十行）它就掉到屏幕外 —— 想往右拖得先把页面滚到块底。用户口径：「可以让
  滚动条一直保持在界面内嘛」，选了「加高度上限」这条路。
- **改动**（只动 `frontend/src/style.css`）：`.markdown pre` 由 `overflow-x: auto` 改为
  `max-height: min(60vh, 560px); overflow: auto` —— 代码块变成自包含滚动窗，横向滚动条贴在
  这个窗的可见底边（滚动条画在容器边、不画在内容底），块最高半屏多，读它时底边基本在屏内。
- **关键决策**：
  - **`60vh` 与 `560px` 取小**：矮窗口按视口比例收，高窗口不让块高到一屏装不下。
  - **只封顶不缩**：`max-height` 不产生多余纵向滚动条，短代码块外观与原来完全一致。
  - **先例**：`.msg-change-diff` 早就是 `max-height: 320px; overflow: auto`，同款语义。
- **验证**：
  - 真实浏览器复测（窗口 898px 高，上限 = 539）：会话里最高的代码块（98 行、内容 2219px）
    被压成 523px 的窗，`scrollHeight 2219 > clientHeight 523`（块内纵向滚生效）、
    `scrollWidth 1482 > clientWidth 655`（横向滚仍在），滚到视口中央时块底 662 < 898，
    横向滚动条在屏内；短块 `clientHeight == scrollHeight`，不受影响。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过。
- **遗留**：
  - 高代码块从此是「块内纵向滚 + 页面纵向滚」两层滚动；若以后嫌烦，删掉 `max-height` 与
    `overflow` 两行即回退到上一节的状态。

#### 对话区横向溢出：给 .msg 的直接子块补横向封顶

- **动机**：含长代码行的 AI 回复会比其他消息宽出一截（实测最新一条气泡 1498px，而列内容宽
  只有 949px），并把 `.messages` 撑出 221px 的横向滚动条 —— 整个对话区能左右拖，观感就是
  「最新那条特别宽」。60 条历史里 4 条中招（973 / 972 / 1455 / 1498）。
- **改动**（只动 `frontend/src/style.css`）：`.msg` 规则之后新增 `.msg > * { max-width: 100% }`，
  把气泡与其余直接子块（`.msg-changes` / `.msg-usage` / `.msg-time`）一起封顶到列宽；顺手把
  `.bubble` 上那句「min-width:auto 会撑破整列」的注释改准 —— 纵向 flex 里它只卡主轴，横向
  从来不归它管。
- **关键决策**：
  - **根因是交叉轴定宽，不是 pre 缺滚动**：`.msg` 是 `flex-direction: column` +
    `align-items: flex-start`，子项按 fit-content 定宽、下限是 min-content；`pre` 是
    `white-space: pre`，min-content 等于最长那一行，一旦超过列宽气泡就跟着长出去。所以修法是
    **封顶**而不是再加滚动 —— `pre` 本来就有 `overflow-x: auto`，style.css 里「把滚动交回 pre」
    那段注释说的就是这套设计，只是此前气泡被撑开、这个滚动条从来没用上。
  - **为什么用户气泡从不出事**：`.msg.user .bubble` 早有 `max-width: min(85%, 860px)`、
    `.msg-changes` 也早有 `max-width: 100%` —— 同一个坑此前踩过两次、各补一处；这次用
    `.msg > *` 一次兜住所有直接子块，防以后新增子块重犯。
  - **只封顶不拉伸**：短消息仍按内容贴合，只有超宽的被压回列宽；超宽代码块变成块内横向滚动
    （实测最长行 1482px → `pre` 的 `scrollWidth 1482 / clientWidth 948`）。
- **验证**：
  - 真实浏览器（用户正在用的标签页，窗口 1707px）复测：超宽气泡从 4 条降到 0，最大气泡 =
    列内容宽 949；`.messages` 的 `scrollWidth == clientWidth`（1423），页面 `scrollWidth ==
    clientWidth`（1707），横向滚动条消失。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过。
  - 前端无测试套件；CSS-only 改动，未跑全量 pytest。
- **遗留**：
  - 超宽代码块现在靠块内横向滚动看全；若以后嫌「滚着看」难受可再考虑软换行
    （`white-space: pre-wrap`），但那会让代码行的列对齐失真，默认不做。

#### 那轮 analysis 触顶的真因是「重读」：占位符带身份 ＋ 分析边取证边下结论 ＋ keep 6→12

- **动机**（用户口径：「问题的本质不是这个，是大模型读到的工具信息都是剪裁过的，导致需要
  重新读」，并给了两条办法：抬高剪裁保留的条数、分析时逐步下结论）。复核日志后确认这个判断
  成立，而且机制比「keep 太小」更具体一层 —— 本节下面那条把这轮定性成「步数用完」，那只是
  **症状**：烧掉步数的是重读，而重读是剪裁的副作用。三个环节：
  ① **占位符不带身份**：当时那句是「（此处原本是一次工具返回的内容，为省上下文已裁剪；
  需要时重新读一次 / 重新跑一次即可。）」—— 不说剪掉的是哪个工具、哪个文件，还**明写
  「重新读一次」**，模型分不清「我读过、结论已经有了」与「我没读」，只能防御性重取；
  ② **`keep=6` 太窄**：一轮 126 次工具调用只留得住最后 6 条原文；
  ③ **分析工作法把结论放在最后一步**（「先摆假设 → 逐条取证 → …… → 第 5 步才成篇写结论」），
  等于要求它把全部证据一直揣在上下文里 —— 而**工具返回会被裁、AI 消息不会**，结论没有及时
  落到自己的话里，材料一被裁就只能重读。
- **实测量到的重读规模**（trace `07fa6a67b1c2`）：`read_file` 44 次只覆盖 **14 个文件**
  （`AuthServiceImpl.java` 8 次、`AuthContextInterceptor.java` 7 次、`AuthController.java` 6 次）；
  `execute` 82 次里 dump 出 **160 个** `=====X=====` 段落标记、**去重只有 42 个**（核心那几个
  文件各被 dump 6~7 次）；开头 23:22:27–23:22:52 读过的那批，结尾 23:26:27–23:26:35 **又从头
  读了一遍**。空转刹车一次都没出手（「同样参数调用过 N 次」那行 0 次）：重读带的是**不同的
  `offset` / `limit`**，而守卫的键是「工具名 + 入参精确 JSON」，分页读取就这么绕过了它。
- **改了什么**（三件是一套，缺一件都还会重读）：
  - **① 占位符带身份、且不再劝重读**（`agent/middleware/context_trim.py`）：新增
    `trimmed_placeholder(tool, target, chars)` 与 `_target_of()` / `_trim_facts()`，由
    `HarnessContextTrimMiddleware._label_trimmed` 把上游填的无名占位符逐条换成
    `〔已裁剪〕read_file → /…/AuthServiceImpl.java（原文 12,480 字）。你已经读过它：先用你
    已经得出的结论往下走，只有确实缺关键细节时才重取。`。身份从**剪之前**那份按
    `tool_call_id` 反查（工具返回本身已是占位符，而入参在发起它的那条 AI 消息上）；
    `TRIM_PLACEHOLDER` 退化成「交给上游去填的那句 ＋ 认不出原主时的兜底」。
  - **② 改的时机必须在调 handler 之前**：间谍 handler 截到的是副本，事后再改模型看不到 ——
    所以那一层从 `handler(inner)` 改成 `handler(inner.override(messages=…))`。只换 content：
    条数、顺序、`tool_call_id` 与工具名一概不动（与上游那次改写同一层面的安全保证），
    孤儿 tool_call 认不出原主就留着无名那句，不硬编。
  - **③ 原文大小用 `len()` 而不是 token 估算**：这一步每次模型调用都要对上百条重跑一遍，
    为一句提示去扫全部原文不划算；字数只是给它一个「重取代价多大」的量级感。
  - **④ `CONTEXT_TRIM_KEEP` 6 → 12**（`config.py` / `.env.example`）：抬高保留窗口。
  - **⑤ 提示词那一半**（`agent/prompts.py`）：`_READONLY_RETRIEVAL`（qa 与 analysis 共用那段
    检索纪律）加两条 —— **「读过就别重读」**（讲清占位符是省上下文的正常动作、不等于没读过；
    确需重取要一次读全，并点名「别拿 `offset` / `limit` 反复翻同一个文件、别用 `execute` 把
    同一批文件再 `cat` 一遍」这两个实测到的反模式）与**「结论要落在你自己的话里」**（工具返回
    会被裁、AI 消息不会）；`_ROLE_ANALYSIS` 的「分析工作法」插入第 3 步**「边取证边下结论，
    不要攒到最后」**，收尾那步改成「把小结论**串成整体**，不是重新去读一遍材料」。
  - **⑥ 占位符里的路径按尾部截断**（`_shorten_path()`，`file_path` / `path` 两个键走它）：
    对象封顶 120 字，而按头部截断（`_one_line` 那套）留下的正好是「沙箱里几十条一字不差的
    仓库根」，反倒把文件名切掉 —— 烟测里就出现过 `…/AuthServiceImpl.jav…` 这种既认不出、
    也没法拿去重取的半截路径。改成从尾部往前逐段收，收不下就断，前缀换成 `…/`。
- **关键决策**：
  ① **三件一起做**，因为它们打的是同一条因果链的三个环节：认得出剪掉的是什么（占位符）、
  手上留得多一点（keep）、结论不依赖会被裁掉的材料（提示词）。只抬 keep 的话，保留窗口再宽
  也总有老化的一刻，重读照旧发生。
  ② **`keep` 只抬到 12、不按角色分档**：这一格是全角色共用的，coder 当初那 5.4M token 就是
  历史工具结果反复重发出来的，抬 keep 对它是纯成本；分档要新增配置格，与「别再堆配置格」的
  口径冲突（用户拍的也是 12）。下一轮拿新加的 `CONTEXT_TRIM` 那行日志量准了再决定要不要继续抬。
  ③ **占位符变长是有意的取舍**：每条多 40~60 字（上百条 ≈ +4~6k token），换掉的是「一次
  3k token 的重读」—— 止住两次重读就回本。
  ④ **递归上限那两条遗留先不动**：重读消失后 200 步大概率够用；先量一轮再决定要不要改
  「触顶收口」，免得把症状当病根治。
  ⑤ **占位符变长换来的信息必须可用**：所以路径按**尾部**留（改动 ⑥）—— 认出「我读过哪个
  文件」靠的是文件名与它上面那一两层目录，仓库根那一段几十条占位符一字不差，没有信息量。
- **验证**：`pytest` **1420 passed**（新增 13 条 —— 剪裁 7 条：占位符带工具名 / 对象 / 字数、
  不再出现「重新读一次」而改说「你已经读过它」、换占位符后条数与 `tool_call_id` 配对不变、
  `_target_of` 压成一行且封顶、长路径留住文件名、单段超长路径一样封顶、端到端剪完仍看得见
  文件名且不再带仓库根；提示词 6 条：占位符口径、结论落点、边取证边下结论、两个反模式
  被点名、这几条不漂到 coder / planner / reviewer 那几份）；`python scripts/check_prompts.py`
  **0 冲突**；`ruff check` 改动文件全干净。本次没有前端改动，未跑 `npm run build`。
- **没验证的**：真机那一轮 —— 重读次数是否真的降下来、`keep=12` 把稳态上下文抬高了多久
  （`CONTEXT_TRIM` 那行会直接给剪前剪后的数），以及这两行的估算值与供应商真值差多少。
- **遗留**：空转刹车对「同一文件、不同 `offset` / `limit`」仍然是瞎的（`docs/backlog.md` 二·五，
  已把优先级降下来）—— 这一批是从**诱因**那头断掉重读，守卫那道网还没补；提示词与占位符
  都劝不住的时候，它才是最后一道。

#### 剪裁 / 压缩各自记一条账（CONTEXT_TRIM / CONTEXT_SUMMARY）＋那次 analysis 报错的定性

- **动机**（用户：「剪裁压缩的中间件日志要记录，发生时间，原来上下文长场后，剪裁或压缩后
  上下文长度」「现在这个分析报错也查一下原因，再看一下剪裁+压缩是否真的生效了」）。两件事
  其实是同一件：**这两层省下多少，原先在任何一处存量数据里都看不见** —— 剪裁只改「这一次
  请求」（`request.override`），压缩也不改状态（上游只把逐出的原文写进
  `/conversation_history/{thread_id}.md`、在私有字段里记一个事件），所以「压缩到底触发了没有、
  省了多少」一直只能靠推断。这次要查的那轮报错正好把这件事顶到了台面上。
- **那轮报错的定性**（会话 `cb4c905c`，2026-10-15 23:21:49–23:26:42，intent=analysis）：
  **不是上下文炸了，是步数用完了** —— `GraphRecursionError: Recursion limit of 200 reached`
  （`AGENT_RECURSION_LIMIT` 默认 200）。这一轮 **66 次模型调用、126 次工具调用**
  （execute 82、read_file 44），模型一直在反复捞同一批认证相关的文件，直到超级步耗尽。
  **44 次 read_file 只覆盖 14 个文件**：`AuthServiceImpl.java` 读了 8 次、
  `AuthContextInterceptor.java` 7 次、`AuthController.java` 6 次。
- **剪裁与压缩都确实在跑**（这轮日志就是证据，也正是上一批欠着的那条真机验证）：
  ① **压缩触发过一次**：23:22:21 `摘要调用完成：输入 40019 / 输出 3617 tokens` —— 输入正好卡在
  `CONTEXT_SUMMARY_INPUT_TOKENS=40000` 那个上限上，说明「摘要调用不许不裁」这条生效了
  （上游工厂那一档是 `None` ＝完全不裁）；压完第一次模型调用输入 **38,521**，而上一轮
  （19:0x 那次 6.9M token 的事故轮）末次输入是 **152,340** —— 继承下来的历史被压掉了四分之三。
  之后整轮峰值只到 **76,565**，没再够到 120,000 那条线，所以只压了这一次。
  ② **剪裁在生效**：输入曲线是**锯齿**而不是单调上涨（第 38 次 64,833 → 第 39 次 62,450、
  第 42 次 67,053 → 第 43 次 63,325、第 50 次 75,213 → 第 51 次 71,857）—— 只有「老工具返回
  被换成占位符」能让一次调用的输入比上一次更小。
  ③ 后端进程 23:21:14 启动，晚于上一批改动的落盘时间（22:40 / 22:42），跑的确实是新代码。
- **顺带查出的一条真问题**（记进 `docs/backlog.md` 二·五）：**空转刹车对分页读取是瞎的** ——
  `LoopGuardMiddleware` 的键是「工具名 + 入参精确 JSON」，而模型反复读同一个文件时带的是
  **不同的 `offset` / `limit`**，于是 8 次读同一个文件一次都没被数成重复（本轮日志里
  「本轮已经用同样的参数调用过 N 次」那行出现 **0 次**）。200 步就是这么烧完的。
- **改了什么**：
  - **① 剪裁记账**（`agent/middleware/context_trim.py`）：`ContextEditingMiddleware` 换成子类
    `HarnessContextTrimMiddleware`，覆写 `awrap_model_call`，用一层「间谍 handler」截下
    **实际交给内层的那份消息**（上游把剪完的消息直接 `override` 给 handler，既不写回状态、
    也不回传给我们），与进来那份各数一次，记成一行 INFO：`CONTEXT_TRIM 剪掉 5 条历史工具
    返回：31200 → 4800 tokens（省 26400；触发线 40000、保留最近 6 条；消息 17 条不变，
    耗时 12ms）`。**发生时间**由日志格式串的 `%(asctime)s` 带（不另写一份，免得两处时钟），
    角色落进 `%(agent)s` 那一列（`build_context_trim(…, agent=)`，装配点按角色给）。
  - **② 压缩记账**（`agent/summarization.py`）：`HarnessSummarizationMiddleware` 覆写
    `awrap_model_call`，同一套间谍 handler。「压前」按 `_get_effective_messages` 取
    （上一次压缩事件已经生效的那一份 —— 直接数 `request.messages` 会把早已逐出的原文再算一遍，
    那个数没有意义）；token 数借上游自己的 `_count_tokens`（含系统提示与工具 schema，
    与它判触发时同一个口径）。三种结局分行记：**压缩**（带摘要字数、逐出原文落盘路径、耗时）、
    **只截断工具入参**（上游那份 `truncate_args_settings`）、**没动上下文＝一行都不记**。
    触发线在构造时渲染成人话（`describe_trigger`：`窗口 991808×0.85=843036 或 120000 tokens`）。
  - **③ 只在真动了手的时候才数 token**：这两层每次模型调用都跑，而计数要把全部历史
    （压缩那边还要连工具 schema）序列化一遍，十几万 token 就是几十毫秒。判「动没动」都走
    便宜办法 —— 剪裁数上游打在 `response_metadata` 上的 `cleared` 标记，压缩逐条比**对象身份**
    （上游不改状态，没动手时交给内层的就是进来那一批对象）。
  - **④ 接线**：`subagents.context_trim_factory` 从 `() -> 中间件` 改成 `(角色) -> 中间件 | None`
    （角色名要进日志那一列），顺手补掉一个真漏：开关关掉时 `build_context_trim` 返回 `None`，
    而旧代码会把这个 `None` 直接塞进子智能体的 middleware 列表。`session/manager.py` 主控那两份
    传 `MAIN_AGENT`、子智能体传角色名；`agent/middleware/__init__.py` 导出新名字。
- **关键决策**：
  ① **两行的 token 口径故意不同**，各自与自己的触发线同口径（剪裁只数消息，因为
  `ClearToolUsesEdit` 判触发就是 `count_tokens(messages)`；压缩含系统提示与工具 schema，
  因为上游那边判触发就是这么算的）—— 每行都能直接对着自己的阈值调参，代价是**两行不能
  横着比**，所以两处 docstring 都写明了。
  ② **只记 INFO、不记「没动手」的 DEBUG**：压力曲线已经有一份更准的（`run_log` 每次调用记的
  供应商真值 usage），再记一遍估算值只是噪声。
  ③ **不覆写 `_acreate_summary` 去捞摘要统计**：那条路要在实例上存状态，而同一个压缩中间件
  实例可能被同角色的并发子智能体共用（`memory_factory` 之所以是工厂就是这个原因）；改从上游
  回传的 `_summarization_event` 里取（摘要那条消息与落盘路径都在里面），无状态，也不碰
  `asyncio.gather` 的上下文传播。
  ④ 用上游私有的 `_count_tokens` / `_get_effective_messages`：版本已在 `pyproject.toml` 钉住
  （`deepagents>=0.6.11,<0.7`），换来的是「日志里的数 ＝ 它判触发时数的那个数」。
  ⑤ 只实现异步版（与 `boundary.py` / `fallback.py` 同一约定）：服务端统一走 `astream_events`。
- **验证**：`pytest` **1407 passed**（新增 9 条 —— 剪裁：「剪了要报出剪前剪后与省了多少 /
  没剪一行都不记 / 不传角色名按 `main` 记」；压缩：「压缩要报出前后、条数、落盘路径 /
  没动一行都不记 / 只截断入参是另一行 / 触发线渲染三条」）；`ruff check` 改动文件全干净；
  `python scripts/check_prompts.py` 0 冲突。本次**没有前端改动**，未跑 `npm run build`。
- **没验证的**：真机再跑一轮，看这两行的估算值与供应商真值差多少（计数器口径的校准）——
  这轮之前没有这两行日志，差多少还只能靠上面那条锯齿曲线间接推。
- **遗留**（都记进 `docs/backlog.md` 二·五）：① 空转刹车认不出「同一文件不同 offset/limit」；
  ② `AGENT_RECURSION_LIMIT=200` 对「分析整个仓库」这类形态偏紧，而且**触顶是抛错、不是收口** ——
  用户拿到的是一句报错而不是已经查到的结论，值得改成「快到上限就先出结论」。

### 2026-10-15

#### 压缩这条线的三条根因：自己构造那份中间件（排掉上游的）＋计数器换中英混合口径

- **动机**（用户：「看一下压缩中间件的部分，有没有什么建议」）。上一批（本节下面那条）把
  deepagents 的压缩工厂换了层壳、塞进中文计数器，但**只解决了计数口径**；把上游源码
  （`deepagents` 0.6.11 / `langchain` 1.4.0）逐行读下来之后，发现真正的三个坑都在**上游工厂的
  默认值**里，而且都没暴露成参数（签名只有 `summary_prompt` / `trim_tokens_to_summarize` /
  `token_counter`）：
  ① **trigger 只有窗口比例**（`compute_summarization_defaults`：带 profile 时 `("fraction", 0.85)`）。
  窗口现在按模型查，`qwen3.8-max` 是 991808 → 比例线落在 **843k**，而那轮 6.9M token 的
  **单次峰值只有 152k**，所以「换中文计数器」对大窗口那几行**近乎空转** —— 上一批那条
  「压缩会在 ~108k 触发」的待验证项，只对解析到 128k 兜底值的行成立；
  ② **`trim_tokens_to_summarize` 被工厂静默设成 `None`** —— 工厂默认 `None` 并**显式**传给类，
  把类自己的 4000 覆盖掉，而 langchain 那边 `None` = 完全不裁：压缩触发那一刻会额外发一次
  「输入 ≈ 被逐出的全部历史」的调用，用的还是主控那个贵模型；`awrap_model_call` 只对
  **主调用**捕 `ContextOverflowError`，摘要调用自己溢出是往上抛的 —— **恰好在系统自救那一刻
  把整轮带崩**（与上一批 kimi 温度 400 的失败形状同类）；
  ③ **`keep=("fraction", 0.10)`**：1M 窗口下等于「压完还留 100k」，与 120k 的触发线只差一点，
  压了等于没压。
  另外量到一条**上一批自己的漏**：`CJK_CHARS_PER_TOKEN=1.6` 是**一个常量喂两种文本** ——
  实测中文高估 2.48 倍、**读本项目源码高估 2.49 倍**（代码真实约 3.5~4 字符/token）。
  这个上下文里绝大部分是代码，于是剪裁的绝对阈值（40000）折算成真 token 只有约 16k
  ＝**剪得远比设计早**，而压缩的比例线折算下来只有 0.34×窗口＝**压得比设计早**，
  两条线朝相反方向歪，且歪多少取决于当轮的中文/代码配比 —— 调参没有意义。
- **改了什么**：
  - **① 计数器换中英混合口径**（`middleware/context_trim.py`）：`cjk_token_counter` →
    `context_token_counter`，新增 `effective_chars_per_token()` —— 按当轮中文占比在 1.6 与 3.8
    之间折一个等效系数，仍委托上游 `count_tokens_approximately`，所以「每条消息固定开销 /
    工具 schema / 图片按张计」那套账不必重写。实测：纯中文 1.6、纯 ASCII 代码 3.8、
    中文注释多的源码 3.0（同一份文件原先按 1.6 估 2663，现在 1420）。
  - **② 新增 `agent/summarization.py`**：`HarnessSummarizationMiddleware`（继承上游公开别名
    `SummarizationMiddleware`）+ `build_summarization()` / `summarization_defaults()` /
    `profile_window()`。trigger = **`[("fraction", 0.85), ("tokens", 120000)]`**（上游
    `TriggerClause` 的 list 就是 OR 语义），keep = **`("tokens", 20000)`**，
    `trim_tokens_to_summarize` = **40000**；`truncate_args_settings` 照抄上游默认值。
    模型没有 `profile` 时退回**纯绝对值**那一档 —— 上游 `__init__` 见到 fraction 而拿不到
    `max_input_tokens` 是直接 `ValueError`（不是忽略），漏这一档就是建 agent 当场崩。
  - **③ 排掉上游那份、位置自己定**（`agent/graph.py`）：删掉上一批那层猴补壳，改成在
    `HarnessProfile.excluded_middleware` 里加 `SummarizationMiddleware`（**按类精确匹配**，
    所以我们的子类实例不会被误伤 —— 上游 `name` 属性也正是这么设计的：子类回落到
    `type(self).__name__`）。我们那份挂在**剪裁的内侧、兜底的外侧**（主控与子智能体同序）：
    上游把压缩挂在用户中间件外面，它数的是「还没剪的账」，两层高估叠在一起。
  - **④ 接线**：`agent/subagents.py` 加 `summarization_for(role, model)`，顺手把「每角色建两次
    模型实例」改成建一次（压缩要按**那个模型自己的窗口**算触发线，必须是同一个实例）；
    `session/manager.py` 主控与子智能体各挂一份；`config.py` 新增 `CONTEXT_SUMMARY_*` 五个键
    （**不上界面**，与 `CONTEXT_TRIM_*` 同属「调参」那档）。
  - **⑤ 钉版本**：`pyproject.toml` 里 `deepagents` / `langchain` 从裸名字改成 `>=0.6.11,<0.7` /
    `>=1.4.0,<2`。`requirements.txt` 早就钉了，但 `pip install -e ".[dev]"` 是按 **pyproject**
    重装依赖的（AGENTS.md 记着这个坑）—— 我们既按类排掉上游中间件、又依赖 langchain 摘要层
    的取值语义，不钉就是等着一次重装把它静默弄失效。
  - **⑥ 顺手修两处过期注释**：`agent/llm.py` 那句「按窗口比例（80%）触发」（实际 85%）、
    `session/manager.py` 那句「默认不含 main」（上一批已改成四个角色都给）；`config.py` 里
    压缩口径那段注释按新取值重写。
- **关键决策**：
  ① **不用上游工厂、自己构造** —— 三个默认值都不暴露成参数，改壳只能覆盖 `token_counter` 与
  `trim_tokens_to_summarize` 两格，`trigger` / `keep` 非得绕过工厂不可；而绕过工厂就必然要处理
  「上游那份还在」，所以「排除 + 子类身份」是配套的，不是两件事。
  ② **子类而不是实例** —— 排除按类精确匹配，直接构造上游那个类的实例会被自己排掉。
  ③ **trigger 保留比例那条、只加上绝对那条**（OR）—— 绝对那条上游判它时会顺带看上一条
  `AIMessage` 里供应商**自报**的 `usage_metadata.total_tokens`（真值），所以即便我们的估算偏了
  真值也能触发；比例那条继续跟着窗口走，小窗口模型（128k）不必硬等 120k。
  ④ **计数器用「折一个等效系数」而不是分段重写** —— 复用上游那套账，少一处会与上游漂移的
  实现。工具 schema 不进比例（量级远小于历史消息），按折出来的系数一并计，是有意的取舍。
  ⑤ **`CONTEXT_SUMMARY_*` 不上界面** —— 与 `CONTEXT_TRIM_*` 同档，`docs/config-page-design.md`
  第九节写着「调参不上界面」，用户也说过别再堆配置格。
- **验证**：`pytest` **1398 passed**（新增 `tests/test_summarization.py` **17 条**：绝对上限在 1M
  窗口下确实能压下 152k 那个峰值 / 没 profile 时退回纯绝对值且**真的建得起来** / 摘要上限不是
  `None` 且**对照钉住上游工厂给的就是 `None`** / 我们那份在按类排除下活下来而上游那份被排掉 /
  主控与子智能体的挂载顺序都在剪裁内侧 / 不传就是不压。`tests/test_context_trim.py` 的计数器
  三条改成「中文按 1.6、代码按 3.8、混合按比例、无文本退回 3.8」，并删掉已失效的
  `test_the_summarization_factory_gets_our_counter`）；`ruff check` 改动文件全干净。
  另外两处假模型跟着改真：`tests/test_profiles.py` 的 `_fake_model_for` 换成带 `role` 字段的
  `GenericFakeChatModel` 子类、`tests/test_app_settings.py` 补一行 `build_summarization` 打桩 ——
  子智能体装配现在会**真造**压缩中间件，而上游 `__init__` 第一件事就是 `model.with_retry()`，
  `SimpleNamespace` / `None` 撑不到断言那一步。
- **没验证的**：**真机那一轮**（沙箱禁网）—— 压缩到底在什么位置触发、压完省了多少、
  `read_file` 次数有没有因为剪裁变早而上升，都要看日志里的「摘要调用完成」与 Token 面板。
  本次**没有前端改动**，所以没跑 `npm run build`。
- **遗留**（都记进 `docs/backlog.md` 的「二·五」）：① **摘要正文没过脱敏** ——
  `_acreate_summary` 直接 `ainvoke`、不经 handler，绕过 `ModelRedactionMiddleware`，而压缩现在
  **真的会触发了**，这条优先级比之前高；② 摘要用的是主控那个贵模型（要省钱得单开一行，
  与「别堆配置格」冲突）；③ `truncate_args_settings`（上游那份，开着）与
  `CONTEXT_TRIM_CLEAR_INPUTS`（我们那份，关着）是两套入参截断，口径要说清。

#### 那次 analysis 轮的 6.9M token：四条整改（剪裁给主控 / 压缩用中文口径 / kimi 温度 / qwen 窗口）

- **动机**（用户：「看一下最近的一次 analysis 任务，这个 token 量太大了吧」）。查下来的账：
  会话 `4b9e01152dff` 18:53–19:09 那一轮 **54 次模型调用、103 次工具调用（read_file 53、grep 34）、
  输入合计 6,895,459 tokens**，单次输入从 91k 一路涨到 152k；而且**最后是崩掉的**：
  `MODEL_FALLBACK primary=qwen3.8-max fallback=kimi-k2.6 error=BadRequestError/400` → kimi 回
  `invalid temperature: only 1 is allowed` → 整轮失败（记忆倒是写成功了）。四个原因：
  ① 一个会话共用一个 thread，历史从不清理，这轮**第一次调用就带 91k**（前几轮 coding/rework 的残骸），
  之后每加一条工具结果就要重发整份历史（O(n²)）；
  ② **压缩从来没触发过** —— 上游 `count_tokens_approximately` 是英文口径（`chars_per_token=4.0`），
  中文实测 ≈1.6 字/token，152k 在它眼里只有 ~55k，永远够不到 85%×128k 那条线；
  ③ **剪裁只给了 coder**（`CONTEXT_TRIM_ROLES=coder` 默认），analysis 主控一条历史工具结果都不剪；
  ④ 那 53 次 `read_file` 的整份文件原文全躺在上下文里。
- **改了什么**：
  - **① 剪裁给全部四个角色**（`config.context_trim_roles` 默认 `main,planner,coder,reviewer`）、
    `.env.example` 同步。剪的只是**这一次请求**里的老工具结果，不动 checkpoint / 业务库，
    最近 6 条照旧保留。
  - **② 压缩换中文计数器**：`context_trim._count_tokens` 提成公开的 `cjk_token_counter`（多认一个
    上游会传的 `tools=`），`agent/graph.py` 里**换掉 deepagents 的 `create_summarization_middleware`
    工厂**给它塞 `token_counter` —— 上游 `create_deep_agent` 没暴露这个旋钮，而它内部三处构造
    （主控 / 子智能体 / 通用子智能体）都走这个模块属性，换一次全覆盖；其余参数照传。
  - **③ kimi 温度**：新增 `config.TEMPERATURE_HINTS` / `temperature_for()`（按**模型名**判，
    与 `model_types` 同套路），在 `llm._model_from` 与 `build_classifier_model` 两处生效 ——
    界面上的行没有温度这一格，而 kimi 系只收 1，以前这条信息写在 `.env` 的 `kimi_model` 里，
    删掉 `.env` 那套时丢了（我的漏）：现在任何 kimi 行（含**兜底行**、含分类器）都不会再 400。
  - **④ qwen 窗口刷进表**（用户点的那件事，我直接调了本机接口）：`POST /model-config/providers/qwen/models/refresh`
    → 20 个模型带窗口，`qwen3.8-max = 991808`。顺带把窗口显示取整（`991808` → `969K`，
    以前会显示 `968.6K` 那种假精度）。
- **关键决策**：① 剪裁**默认全开**（四个角色）—— 只给 coder 的做法在当时是"先在一处观察"，
  现在有了真机证据（主控形态也会通读仓库），观察期结束；② 压缩计数器**换工厂**而不是自己拼中间件
  —— 上游的工厂还带「被挤出的历史 offload 到 `/conversation_history/`」「上下文溢出自动摘要重试」
  这些行为，自己拼会丢；③ kimi 温度按**名字**判（不新增界面字段、不动库表 —— 用户刚要求过别再堆配置格）。
- **验证**：`pytest` **1380 passed**（新增：压缩工厂确实拿我们的计数器 / 中文口径比上游估得多 /
  kimi 行与分类器都是 1 / 剪裁名单默认四个角色）；`ruff check` 干净；
  `npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过；真机核过 qwen 的刷新结果（上面那 20 行）。
- **没验证的**：① 压缩**真的会在 ~108k 触发**这件事要下一轮真机跑（看日志里出现摘要调用）；
  ② 剪裁对主控的实际影响（省了多少 token）也要下一轮真机看；③ 观感（没有浏览器）。
- **遗留**：`qwen3.8-max` 在 dashscope 报的窗口是 991808（≈969K），而这一行的**兜底窗口**口径
  （`fallback_context_window`，默认 128k）只在一行没配、模型也查不到时才用 —— 现在查得到了，
  所以 analysis 那一行的压缩线会按 969K 算。

#### 修「选完模型窗口不刷新」+ 删掉「内置候选」那份种子清单

- **动机**（用户反馈）：「还是有问题，选了模型之后，上下文窗口没有跟着刷新，以 kimi k3 为例，
  数据库中是 256K，那个种子清单删掉」。
- **① 真 bug**：`ensureModels()` 命中缓存就 `return` 了，**从不去刷窗口回显** —— 于是
  「选完模型去查后台」这一步只在**第一次**打开弹窗时发生（之后不管你换哪个模型都不动）。
  现在缓存命中也会按当前选中的模型刷一次；点「刷新清单」之后也立刻刷。
- **② 清单那一格改成现读库**：新加 `_models_payload_fresh`（读表），`GET /providers/{p}/models`
  与刷新/手改两个接口都改用它 —— 内存那份镜像只在启动 / 刷新 / 手改时对齐，直接改库它不知道，
  于是会出现「库里明明有 256K，界面看不到」（用户报过）。镜像仍留着给**同步**的
  `resolve_window` 用（它读不了库）。顺手删掉已经没人用的 `_models_payload`。
- **③ 删掉「内置候选」**（`default_models`）：`ProviderMeta` / `ProviderOption` / `ProviderUpsert` /
  `dao.upsert_model_provider` / 种子文件里的那一列都去掉，`seed_provider_models`（把候选名灌进
  模型详情表）一并删；「测试连接」拉不到时**不再拿内置候选顶上**，老实回 `ok=false` + 错误原文
  （`source` 从 `builtin` 收成 `live` / `none`）；界面上候选只剩「刷新清单拉到的 + 表里已登记的
  文本模型」两档，「提供商」栏那格文本框也撤了。**DB 列留着**（`model_providers.default_models`
  改注释标废弃，不写迁移 —— 删列要动库，不值）。
- **关键决策**：模型名**只有一个出处** —— 刷新清单拉到的那份（表 `provider_models`）。
  那份静态候选会过期、又和实时清单重复，维护它没有收益（用户口径：删掉）。
- **验证**：`pytest` **1376 passed**（改了几条钉内置候选的用例：拉失败 / 空清单都改成
  `ok=false` + `source=none`；删掉种子里的候选断言）；`ruff check` 干净；
  `npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过；真库核对：glm 17 个模型全带窗口
  （glm-5.3 = 1M）、kimi 4 个（kimi-k3 = 1M、k2.6 = 256K）、**qwen 还是 0**（还没刷）。
- **没验证的**：观感（没有浏览器）；qwen 那条路径要重启后端后点一次「刷新清单」才算验过。
- **遗留**：`model_providers.default_models` 这一列还在库里（已废弃、不再读写）；
  `POST /model-config/providers/reload` 与种子文件仍保留（新库初始化用）。

#### 模型配置五点收口：修「统一模型显示成别人的」+ 窗口只回显真值 + 去「清除 key」+ 红星 + 弹窗不点外面关

- **动机**（用户逐条）：「① 统一模型里边明明是 deepseek，外边显示的却是 glm；② 窗口什么时候会刷新？
  保存之后外边还是显示 128K，这个 128K 从哪来，里边没数据，按道理选完模型就该查后台然后回显；
  ③ 为什么会有一个清除 apikey 的操作，换了供应商下边的值不都应该清空嘛，key 内部显示一个缩略的
  就行；④ 必填加个红色星，窗口里边不需要提示信息；⑤ 弹窗必须点 X 才能关，不能点一下其他地方
  就没了，那我不得重新配置嘛」。
- **改了什么**：
  - **① 是真 bug**：`MODEL_AGENT_EFFECTIVE_ROLE["unified"]` 原来借 ``orchestrate`` 去算「生效值」——
    优先级翻成「行优先」之后，它取到的是「编码主控」那一行的 glm。现在「统一模型」那一行
    **看它自己**（`llm.effective_for` 给它一个 ``unified`` 特例，`source` 记 ``row``）。
  - **② 窗口来源**：`llm._row_window` 返回 ``(窗口, 来源)``，来源四态
    ``row`` / ``model`` / ``fallback`` / ``none``，并随 `effective.window_source` 下发；
    **界面只回显 row / model 两种真值**，兜底值（我们保守取的 128k，不是厂商给的数）不再显示 ——
    查不到就留空，要准确就点「刷新清单」（那是唯一把厂商窗口写进表的入口）或手填。
  - **③** 撤掉「清除 key」按钮：换供应商时界面清空 + 保存送空串已经能把库里那把清掉；
    key 那一格只把**缩略值**（`sk-…e2b7`）当占位符，不再写「留空＝不改（现在 …）」。
  - **④** 必填只用一个红 `*`（模型名总是必填；JEV 那一行的 base_url / api_key 也必填），
    模型名那格的长占位符与窗口那格的占位符都删掉（窗口只在填错格式时给一行红字）。
  - **⑤** 两个弹窗（模型配置 / 提供商）都**取消「点遮罩关闭」**，只认 X 与 `Esc`。
- **关键决策**：界面**不显示我们自己的兜底值** —— 它是"没数时的保守值"，显示出来读者只会以为
  查到了；要区分「查到」与「没查到」就得把来源一起带出来（`window_source`）。
- **验证**：`pytest` **1376 passed**（新增两条：「统一模型」那一行看自己 / 窗口来源三态）；
  `ruff check` 干净；`npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过。
- **没验证的**：观感（没有浏览器）—— 红星、缩略 key、窗口留空、弹窗不点外面关，都只过了类型检查。
- **遗留**：glm / qwen / kimi 那几家的窗口要**重启后端 + 点一次「刷新清单」**才会进表；
  在那之前它们的窗口那一格是空的（以前显示的是 128K 兜底值）。

#### 设置页第 4 栏「提供商」：目录本身在界面上就能改（口径 b）

- **动机**（用户口径）：「改路径」这件事以前只有两条路 —— 手改库，或改 `config/model_providers.toml`
  再 `POST /model-config/providers/reload` 对齐；而「清单路径」「鉴权头」这两格**只有真机试过
  才知道怎么填**（GLM 要多一层 `/v1`、qwen 在另一个根下面、有的网关是 `x-api-key`）——
  让用户手改库不对。用户选了 b：界面上能改目录行，种子退回「新库初始快照」。
- **改了什么**：
  - **后端**：`GET /model-config/providers`（**全量、含停用** —— 停用的家要看得见才能改回来）；
    `PUT /model-config/providers/{p}`（整行覆盖，复用 `dao.upsert_model_provider`；键只收
    `a-z 0-9 _ -` 且**不可改**：模型行 / agent 行都按它挂）；`ProviderOption` 补
    `models_path` / `auth_header` / `auth_prefix` / `status` / `is_dynamic_model_list`；
    新增入参 `ProviderUpsert`。`_provider_metas` 多一个 `only_enabled` 开关（下拉只要启用的、
    编辑那一栏要全量）。
  - **顺手修一处「改完即生效」是假的**：`_known_provider` / `_borrowed_key` 原本读的是
    **启动时的** `Settings.providers` —— 在界面上改完 `models_path` 之后，「刷新清单」
    「测试连接」还会拿着旧路径去请求（得重启才对）。现在都读**现读库**那份，写接口末尾再
    刷一次内存那份（`apply_model_providers`），于是真的不用重启。
  - **前端**：新增设置页第 4 栏「提供商」（`ProvidersPanel.vue`）：列表（显示名 · 键 ·
    默认地址 · 清单路径 · 内置候选条数 · 停用标记）+「新增提供商」+ 编辑弹窗（8 格，含
    鉴权头 / 前缀 / 分组 / 排序 / 内置候选 / 启停）。`viewState.ts` 的 `SettingsPanel` 加
    `'providers'`（hash 支持 `#settings/providers`）。弹窗开着时按 `Esc` 只关弹窗
    （捕获阶段拦一下，别再犯「连整页一起关掉」那个错）。
- **关键决策**：① **不删除、只停用** —— `provider` 是外键目标，删一行会牵出「那些行怎么办」；
  ② **整行覆盖**（与 DAO 现有语义一致），不留老值赖着的坑；③ 键不可改（新增时可填）；
  ④ **无迁移** —— 表与列早就都在，这次只加接口与界面。
- **验证**：`pytest` **1374 passed**（新增 4 条：全量含停用 / 整行覆盖+内存立刻跟上 /
  新增与坏输入 400 / **改完路径后刷新立刻用新地址**）；`ruff check` 干净；
  `npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过；真库只读跑了一遍
  `GET /model-config/providers`（13 家全在，glm 已是 `/v1/models`、qwen 已是绝对地址）。
- **没验证的**：界面观感（没有浏览器）—— 列表、弹窗那 8 格、停用标记都没看过渲染结果。
- **遗留**：`custom` 那一行的默认地址是空的（选中它不会带出地址，一直如此，不是这次的改动）；
  目录仍不支持删除（要下架就停用）。

#### 窗口清单：认 qwen 原生那种嵌套返回，qwen 的清单地址可写绝对 URL

- **动机**（用户实测反馈）：「openai 的是真的查不到，不过国内的都有的——glm 的 `/v1/models` 会返回
  `context_length`；deepseek 返回 `context_window`；kimi 返回 `context_length`；qwen 有点特殊，
  要调 `/api/v1/models`，里面是 `output → models → model_info → context_window`」。
  对着库核了一遍：deepseek 的窗口**已经**拉到了（`source=probe`，两个模型各 1M）；
  **kimi / qwen 那几行是种子行（`source=seed`），从来没点过「刷新清单」**；
  glm 是 probe 过的但全 0 —— 说明我们请求的那个地址（`…/api/paas/v4/models`）没回窗口。
  解析器那边确实缺一块：只认平铺的 `data[].<窗口键>`，读不到 qwen 埋在 `model_info` 里的值。
- **改了什么**：
  - `routes/model_config.py`：新增 `_model_items`（认 `data[]` 与 `output.models[]` 两种形状）、
    `_name_of` / `_deep_window`（名字与窗口**任意层级**里找；同一层仍按 `_WINDOW_KEYS` 顺序，
    保证可复现），`_positive_int`（有的网关把窗口回成字符串）；`parse_models` /
    `parse_model_entries` 都改走这一套。
  - `config/model_providers.toml`：qwen 的 `models_path` 写成绝对地址
    `https://dashscope.aliyuncs.com/api/v1/models`（清单不在兼容入口那个根下面）。
    `models_path` 一直支持绝对地址（`_fetch_models_payload` 里那句判断）。
    glm 的 `models_path` 写成 `/v1/models`（用户给了完整地址
    `https://open.bigmodel.cn/api/paas/v4/v1/models` —— **比默认多一层 `/v1`**，
    默认那个 `…/api/paas/v4/models` 只回模型名、不带窗口，这正是 glm 那 11 行全 0 的原因）。
- **关键决策**：**认得出就认，认不出不猜** —— 只认这两种已知形状，别的形状回空列表；
  窗口在任意层级里找但顺序固定，免得「这次读到 1M、下次读到 128k」这种不可复现的行为。
- **验证**：`pytest` **1370 passed**（新增四条：qwen 原生形状的名字+嵌套窗口+非文本落表、
  字符串窗口、清单地址的拼接、种子文件里那两条实测路径）；`ruff check` 改动文件干净（全仓那 5 条既有告警在 `db/models.py` /
  `observability/tracing.py`，没新增）；真库核对了 `provider_models` 里各家的 `source` 与
  `context_window`（见上面的动机）。
- **没验证的**：真实厂商返回（沙箱禁网）—— qwen / glm 的新路径要**重启后端**、把目录行同步过去
  （改库或 `POST /model-config/providers/reload`）之后点一次「刷新清单」才算验过。

#### 修：窗口那一格改成**回显当前生效值**、提示文字去重（不再写库）

- **动机**（用户反馈）：「窗口大小去查询了嘛，为什么不回显？而且竟然提示了两次，输入框内一次、
  下边一次」。查过原因有两层：① 我上一版把回显写成「只在输入框为空时显示」—— 一旦那一格有值
  就什么都不显示了；② 提示确实重复（`placeholder="留空＝按模型查"` ＋ 下面一行
  「留空＝按模型查（1M）」）。另外真库里 `provider_models` 931 行**只有 deepseek 的两个模型有窗口**
  （glm / kimi / qwen / openai / typesafe 全是 0：厂商的清单接口不回窗口，实测如此），
  所以「按模型查」对多数行本来就没值可查。
- **改了什么**（纯前端）：窗口那一格现在**始终显示这一行当前会用的窗口**（后端 `effective.window`
  已经解析好：行内手填 → 按模型查 → 兜底值），**不需要先点「刷新清单」**；整条提示文字删掉
  （只留「认不出这个写法」那条错误），占位符缩成 `1M / 128K` 一个格式例子。
  新增 `window_auto`：这一格**回显**时保存**不写库**（写下去会变成覆盖值，以后换模型就成脏数据），
  手一改就变成「我要覆盖它」；清空 = 撤掉覆盖。「测试连接」读到的窗口也改成只回显不写库
  （它取自清单里第一个带窗口的模型，未必是你选的那个）。打开弹窗时按新模型刷一次回显。
- **关键决策**：那一格回答的是「**现在按多少算**」，不是「你填过什么」—— 所以要显示解析结果；
  要不要把它**冻**成这一行的值，由「手有没有动过」决定，而不是由「格子有没有内容」决定。
- **验证**：`npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过；真库核对了两种行：
  `deepseek` 那几行会显示 `1M`（表里有窗口），`glm / qwen / openai` 那几行会显示 `128K`
  （查不到 → 后端保守兜底值，也正是压缩线现在用的值）。
- **没验证的**：观感（没有浏览器）。
- **顺带说明**：想让 glm / kimi / qwen 这些行显示真实窗口，只有两条路 —— 手填那一格，
  或者等厂商的清单接口愿意回窗口（现在读不到，表里记 0）。

#### 改口径：「统一模型」是**兜底**，不是总闸

- **动机**（用户口径）：「统一模型并不是覆盖掉所有的行，只是覆盖没有填的部分」。
  上一批的界面把「被统一模型覆盖」这件事显示出来了，用户看到自己的配置全被压掉 ——
  口径本身就该是**只补空档**：配了的行按自己的走，没配的行才落到统一模型。
- **改了什么**：`agent/llm._row_key_for` 的解析顺序从 `unified → 该行` 改成
  **`该行 → unified`**（`resolve_model` / `effective_for` / `_config_for` 的文档同步）；
  界面文案跟着改 —— 次行从「本行 X · 实际生效 Y（被「统一模型」覆盖）」变成
  「`Y · 本行没配，走「统一模型」`」，「统一模型」那一行自己写「`X · 只兜底没配的行`」；
  上一批加的「清空「统一模型」」那条警告与按钮**删掉**（不需要了）。
  `AGENTS.md` / `README.md` / `docs/config-page-design.md` 里「填了就所有智能体都用它」的表述
  一并改成兜底口径。**没有**动 `.env` 与其它任何东西。
- **关键决策**：兜底而不是总闸 —— 「一处切换所有角色」那个用法要靠把各行清空才成立，
  但用户真正要的是「配了的就按配的跑」，兜底不打扰已做的选择。
- **验证**：`pytest` **1366 passed**（改了 `test_model_config.py` 的三条：
  本行优先、没配的行落统一模型、生效值的 source）；`npm.cmd --prefix frontend run build`
  （含 `vue-tsc`）通过；用真库核了一遍 —— 改之前五行全被 unified 压着，
  改之后 `问答 → qwen3.8-max`、`方案 → gpt-5.6-luna`、`审查 → glm-5.3-flash`、
  `编码主控 → glm-5.3`，只有没配的 `编码(coder)` 落到 `deepseek-flash`（统一模型）。
- **没验证的**：观感（没有浏览器）。

#### 修：列表只显示「生效值」，被「统一模型」覆盖的行看起来像没保存

- **动机**（用户反馈）：「有一个很严重的 bug，里边配置了，外边还是显示默认的统一模型」。
  查库确认**保存是成功的**（`qa` 那一行 2026-10-15 08:13 UTC 写进了 `qwen3.8-max`），
  真正的原因是 `unified`（统一模型）那一行填了 deepseek-flash —— 它的语义就是**覆盖所有行**，
  所以「问答」那一轮实际用的确实是 deepseek-flash。**界面没把这层说清楚**：上一批把列表改成
  「只显示生效值」之后，`ModelEntry` 里那个 `configuredModel` prop 传了却没用上，
  「我自己配了什么」一个字都不露 —— 看起来就像配置没生效。
- **改了什么**（纯前端）：`ModelEntry` 的次行改成**两个都写** —— `本行 qwen3.8-max ·
  实际生效 deepseek-flash（被「统一模型」覆盖）`；两者一致时只写一行。`unified` 那行的次行
  写「`模型 · 覆盖所有行`」；「各智能体」那一栏顶上，只要统一模型填着就多一句
  「现在「统一模型」（X）在覆盖下面所有行」＋一颗**「清空「统一模型」」按钮**（一键解除覆盖，
  不用再去「全局」找那一行）。
- **关键决策**：**没有**改解析优先级 —— 「填了就所有智能体都用它」是 2026-10-13 定的口径，
  运行时行为也是它，这次只修「看不出来」。要改成「只兜底没配的行」是另一个决定，等用户拍。
- **验证**：`npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过；用真库快照核对了四种
  组合的文案（本行＝生效 / 本行被统一模型覆盖 / 本行没配走统一模型 / 本行没配且没有统一模型）。
- **没验证的**：观感（没有浏览器）。
- **遗留**：`qa` 那一行**没有 api_key**（库里为空）—— 清空统一模型之后它会拿 dashscope 的地址
  去请求但没有任何凭据；要用得先把 key 补上。

#### 模型配置：干掉「出厂默认」、每行显示**生效值**、窗口按 K/M 显示、意图识别严格二选一

- **动机**（用户逐条口径）：①「不需要出厂默认这个东西，干掉它」；②「取消窗口自动填 ——
  指的是窗口大小那个吧，如果是的话依旧回填，只不过 1M/128K 这样子展示」；③「没模型名字的置空
  没什么问题」；④「生效值，做；换供应商，旧 key 就清掉；编码组按角色显示」；⑤「意图识别的模型
  配置确实有问题，应该是 JEV 和大模型二选一的，不用 .env 的凭据 —— 我在数据库里配了一个新的
  供应商 typesafe，这个就是 JEV 的供应商，JEV 的配置和其他的大模型保持一样的格式，只不过
  供应商不允许改」。这批的起点是上一轮那次只读设计评审（诊断表见对话）。
- **改了什么**：
  - **解析层去掉 `.env` 回落**（`agent/llm.py`）：`_config_for` 的第三档（`<供应商>_*` 那套
    出厂默认）删掉，一行没配＝**当场报错**，文案指认「哪一行 + 去哪儿配」（`_unconfigured`）；
    `resolve_window` 也去掉「家那格」，只留 `行内手填 → 按模型查 → 保守兜底值`；
    `build_fallback` 只剩「兜底模型」那一行（`FALLBACK_PROVIDER` 作废）；
    `build_classifier_model` 只剩「意图识别（大模型）」→ `unified`；
    删掉 `_ROLE_MODEL_ATTR` / `_provider_attr` / `_FALLBACK_PROVIDERS` 与 `Settings` 上那六个
    `*_model` / `classifier_model` 属性 —— 「谁用哪一行」只剩 `_AGENT_KEY` 一份映射。
  - **生效值**（`llm.effective_for` + 快照里的 `effective`）：每行回「现在真的用谁」，
    `source` 三态 `row` / `unified` / `unset`；`unified` 时界面上写清「被统一模型顶掉」。
  - **行的元数据由后端下发**（`config.MODEL_AGENT_ROLES` / `MODEL_AGENT_SECTION` /
    `MODEL_SECTIONS` / `CODING_GROUP` / `ROLE_LABELS` / `MODEL_AGENT_PROVIDER_LOCK`）：
    前端不再硬编码行名，`coding_group` 直接给出「编码一主三从」的 4 个角色 → 落在哪一行、
    是不是复用。
  - **意图识别严格二选一**：新增 `jev` 那一行（供应商锁 `typesafe`），`apply_model_endpoints`
    把它的 base_url / api_key / 模型名装进 `typesafe_*`；`server/intent.detect_intent` 里
    `mode="jev"` 且这一侧不可用 → **直接落关键字口径，不借大模型**（`build_jev_decider` 也改成
    「地址 + key 两样都要」才算可用）。
  - **界面**（`SettingsPage.vue` / `ModelConfigDialog.vue` / `ModelEntry.vue` / 新增
    `modelWindow.ts`）：列表一行两段（名字 + **生效值**）；去掉「已配置 / 出厂默认」chip 与
    那些话术；窗口那一格按 `1M` / `128K` 显示与解析（进制 1024；查到的原值留着，文本没改过就按
    原值存，免得 `195.3K` 这类来回换算失真）；**换供应商 = 保存时清掉旧 key**，另加「清除 key」；
    「恢复默认」改名「清空这一行」；报错改成**就地显示在弹窗里**；按 `Esc` 只关弹窗（以前会把整页
    连草稿一起关掉）；「编码」那一组按角色列 4 行（规划 / 评审标「复用」并指向同一行）。
  - **`.env`**：把作废的键逐行注释掉（**值原样保留**）：`DEEPSEEK_*` / `KIMI_*` / `GPT_*` /
    `QWEN_*` / `GLM_MODEL_NAME` / 五个 `*_CONTEXT_WINDOW` / `INTENT_MODEL|BASE_URL|API_KEY` /
    `TYPESAFE_API_KEY|BASE_URL|MODEL_NAME`。**保留** `GLM_API_KEY` / `GLM_BASE_URL`（web 搜索在用）
    与 `*_TIMEOUT_SECONDS` / `INTENT_*_ENABLED`。`.env.example` 逐条标注「已作废」。
- **关键决策**：① 宁可**报错也不回落** —— 回落会让「界面上什么都没配」也能跑，用户看不出来自己在用
  哪个模型；② 生效值**读内存那份**（启动 / 保存时从库里落的），所以直接改库要重启才反映到生效值
  （提供商目录仍是每条请求现读库）；③ 窗口显示用 1024 进制 —— 各家报的 1048576 / 131072 都是
  1024 的整数倍，按 1000 换算会显示成 `1048.6K` 读不出来；④ JEV 走 SDK、不建 `BaseChatModel`，
  所以它的连接**不进 `agent_models`**，而是灌进 `typesafe_*`。
- **验证**：`pytest` **1365 passed**（改动 8 个测试文件：`test_llm_config` 重写成「行 → 模型」、
  `test_model_config` 的解析 / 窗口 / 兜底 / 接口几节、`test_model_fallback` 的 `_settings`、
  `test_provider_models` 的窗口顺序、`test_token_usage` 与 `test_plan_events` 的桩件补行、
  `test_intent_detection` 加「未显式给 `mode` 时按桩件推断」那一层 + 改写四关里那条）。
  `ruff check` 改动文件干净（全仓既有告警未新增）；`npm.cmd --prefix frontend run build`
  （含 `vue-tsc`）通过；真机读了一次库确认 `typesafe` 那家（显示名 JEV）与现有 8 行连接。
- **没验证的**：界面的实际观感（没有浏览器）：两段式列表、编码组的四行、窗口 K/M 那一格、
  弹窗里报错的位置 —— 都没看过渲染结果。
- **遗留**：① 弹窗只做了「打开时聚焦第一格」，**没做焦点陷阱**（Tab 能跑到弹窗外）；
  ② 直接改 `model_endpoints` 表要重启后端才会反映到「生效值」那一列；③ `typesafe` 那家目录行
  的 `default_base_url` 是 `https://openrouter.ai/api`，与它的 `models_path`（`/models`）拼起来
  拉不到清单 —— 「刷新清单」对 JEV 那一行大概会 409，需要时改目录行（或手填地址）。

### 2026-10-14

#### 新增 `docs/config-page-design.md`：把设置页的设计写成文档

- **动机**（用户口径）：「讲配置页面的设计写成文档，放到 docs 下边」。
- **改了什么**：新增 `docs/config-page-design.md`，八节 —— ① 左菜单三栏各自装什么；
  ② 基本设置（界面 / 代码缓存配置 / 上下文窗口配置）；③ 模型配置（行是智能体、一主三从的归属、
  一行只留名字+按钮+弹窗三类、测试连接的门槛、模型清单点了才刷）；④ 两处解析口径
  （窗口的五级顺序、供应商反推、候选来源）；⑤ **每样东西存在哪 / 什么时候生效**（一张表）；
  ⑥ 接口一览（9 个端点 + vite proxy 那条坑）；⑦ 几个刻意的决定与理由；⑧ 界面上**故意**没有的东西。
  `AGENTS.md` 的「去哪儿看」里加了指向。
- **验证**：文档里的每条结论都对着代码复核过（行名与映射取自 `config.MODEL_AGENT_ROWS` 与
  `agent/llm._AGENT_KEY`；生效时机取自 `api/app.py` 的三个 `apply_*` 与 `session/manager.get_agent`；
  接口表对着路由逐个核；窗口顺序是真机验过的那五级）。这次只加文档，没动代码。

#### 「编码」一主三从拆开：主控与编码各一行，规划 / 评审复用「方案」「审查」

- **动机**（用户口径）：「编码改四行，只不过计划和审查复用外部的，只有主控和编码需要配置」。
  起因是上一批发现的**信息损失**：原来 `orchestrate` / `planner` / `coder` / `reviewer`
  **四个角色全指向 `coding` 那一行**，而 `.env` 的默认是各用各的（主控/planner=GLM、coder=DeepSeek、
  reviewer=Kimi）—— 一旦配了「编码」这一行，coder 的 DeepSeek 与 reviewer 的 Kimi 就都被顶掉了。
- **改了什么**：
  - `agent/llm._AGENT_KEY`：`orchestrate → coding`、`coder → coder`（新行）、
    **`planner → plan`、`reviewer → review`**（复用已有的两行）。
  - `config.MODEL_AGENT_ROWS`：`coding` 的中文名改成「**编码主控**」，新增一行「**编码**」（`coder`）。
  - 界面：各智能体那一栏多出一行「编码主控 / 编码」，下面加一句事实说明
    「编码这一组的规划、评审，分别复用「方案」「审查」两行」——**四个角色都有出处，只有两行要配**。
- **关键决策**：复用而不是新增四行 —— planner / reviewer 的 `.env` 默认（glm / kimi）与
  「方案」「审查」两行的默认本来就一致，复用它两个角色既能配、又不引入两份同义的行。
- **验证**：`pytest` **1365 passed**（改了 `test_model_config.py` 里那两条钉映射与行名的断言）。
  前端 build（含 `vue-tsc`）通过。
  **真机验过三种状态**（`_probe_coding_roles.py`）：① 一行没配 → 四个角色各回 .env 默认
  （主控/planner=glm-5.3、coder=deepseek-v4-pro、reviewer=kimi-k2.6）；② 只配「编码主控」与「编码」
  两行 → 主控与 coder 跟着变，**planner / reviewer 不受影响**；③ 再改「审查」那一行 →
  reviewer 跟着它变（复用关系成立）。
- **没验证的**：界面上那一栏的观感（没有浏览器）。

#### 模型弹窗三点收口：换供应商就清空、基本设置只留阈值、模型清单从页面撤掉

- **动机**（用户三点）：① 「供应商一旦切换，那配置项全部清空」；② 「基本设置里，上下文窗口部分，
  只留阈值的配置就可以了」；③ 「模型配置里，供应商的模型清单列表是不需要的」。
- **改了什么**：
  - **换供应商＝清空**（`pickProvider`）：模型名 / key / 窗口 / 刚拉到的实时清单 / 探测反馈全清 ——
    它们都属于上一家，留着就是错的数据；地址换成**新那家的默认地址**（选中供应商的意义就在这儿），
    那家没配地址就留空。
  - **基本设置的「上下文窗口配置」只剩两条阈值**（告警线 / 危险线）：兜底窗口与五家供应商窗口
    从 `RUNTIME_SETTINGS` 里撤掉（`.env` 那几格仍生效，只是不再上界面）。理由与这批的主题一致：
    窗口是**模型**的属性 —— 按模型查（`provider_models`）→ 行内手填覆盖 → 兜底值。
  - **页面上的「供应商的模型清单」整块撤掉**：它的用途（拿候选、看/改窗口）现在都在弹窗里 ——
    弹窗第三类「模型 / 窗口大小」旁边多了个「**刷新清单**」按钮（点了才拉，拉完候选与窗口回显
    都跟着更新）。顺手清掉只给它用的 `.model-*` 样式与 `toggleModels` / `saveModelWindow`。
- **验证**：`pytest` **50 passed**（跑的是受影响的两个文件：`test_app_settings.py` 里那几条
  跟着改成「阈值两条」，另加一条断言「上下文那一组正好两条」；`test_provider_models.py` 不动）；
  前端 build（含 `vue-tsc`）通过；`rg` 确认 `.model-*` 与那几个函数已无引用。
- **没验证的**：弹窗与列表的实际观感（没有浏览器）。
- **仍等你拍的一件事**（上一批提的）：**编码那一组要不要拆成四行**（主控 / 规划 / 编码 / 评审）——
  现在四个角色共用「编码」这一行，一旦配了它，coder 的 deepseek 与 reviewer 的 kimi 默认就都被顶掉。

#### 模型弹窗：保存挪进弹窗、选完模型回显窗口；顺带答复「编码一主三从的模型怎么共用」

- **动机**（用户三点）：① 「保存按钮要位于每个配置弹窗的内部」；② 「选择完模型后，对应的上下文长度
  就要开始调用了，然后回显」；③ 问「编码智能体是一主三从，现在配置的编码智能体，主控和编码是共用
  一个吗」。
- **改了什么**：
  - **保存进弹窗**：`ModelConfigDialog` 底部变成「恢复默认 | 关闭 | **保存**」，保存只提交**这一行**
    改过的字段，成功后关掉弹窗（弹窗开着而没保存是两处状态，容易让人以为已经生效）。页面顶栏那个
    保存按钮**只留给「基本设置」**（模型那一栏不再有页面级保存）。
  - **意图识别那一关**（JEV / 大模型）是个下拉、没有弹窗可放保存按钮 → 改成**改完当场存**。
  - **选完模型回显窗口**：模型名那一格失焦/选定后，让父组件确保这家的清单已拉过（没拉过才拉），
    然后按模型名在清单里查窗口并**回显**：窗口输入框下面一行写「按模型查到 1048576」，
    同时把它塞进占位符（`留空＝按模型查（1048576）`）。查不到就说「这个模型还没登记窗口：
    去清单刷新一次，或手填」；手填过则写「这一行手填了，按它算」。**不自动填进输入框** ——
    留空＝按模型查，填上＝覆盖，这两种语义要分得开。
- **③ 的答复（现状与代价）**：`llm._AGENT_KEY` 里 `orchestrate`（主控）、`planner`、`coder`、
  `reviewer` **四个角色都指向 `coding` 那一行** —— 所以**是共用一个**。而 `.env` 那套默认是
  四个角色各用各的（主控/planner=glm、coder=**deepseek**、reviewer=**kimi**），于是：
  **一行都没配时行为不变**；一旦在界面上配了「编码」这一行，四个角色全用它 ——
  coder 不再用 deepseek、reviewer 不再用 kimi。这是这次改动里发现的**信息损失**，
  要不要拆开（拆成主控 / 规划 / 编码 / 评审四行）等你点，见下。
- **验证**：`npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过；`pytest` **1365 passed**
  （这两点是前端，后端没动）。
- **没验证的**：弹窗与回显的实际观感（没有浏览器）。
- **等你拍**：**编码那一组要不要拆成四行**（主控 / 规划 / 编码 / 评审）？
  a（推荐）拆 —— 恢复「每个角色配各的」的可配性，动 `_AGENT_KEY` + 那几行登记 + 界面分组 + 测试；
  b 保持一行，但在界面上写明「主控与三个子智能体共用这一行」。

#### 回复最上面加「working… 计时」；模型配置页改成「一行一句 + 三类弹窗」

- **动机**（用户口径）：① 「加一个执行时间计时，放在 AI 回复的最上面，就叫 working…；页面打开场景下
  就直接计时，未打开场景下突然打开，从现在开始计时 + 从用户发的消息算，不需要很精确」；
  ② 「模型设置页面要调整一下，现在信息太多太乱 —— 问答模型只显示模型名称和配置按钮，没配置就只显示
  配置按钮；点配置弹窗，分三类展示：供应商 / base_url+apikey / 窗口大小」。
- **改了什么**：
  - **计时**：`Message` 多两格 —— `startedAt`（这一轮从什么时候开始跑的）与 `elapsedMs`（跑完冻结的
    总时长）。自己发的那轮取发送时刻；**重新打开时正跑着的那轮取用户那条消息的时间**（于是显示的是
    「这一轮已经跑了多久」，而不是「我看了多久」）。回复最上面一行：跑着是 `working… 12s`（秒针只在
    有活在跑时开，跑完停掉），跑完冻结成 `用时 12s`；刷新回放时照实显示落库前后的那个数。
  - **模型配置页**：列表里一行只留「名字 + 模型名（配过才有）+ 配置按钮」；点「配置」弹出
    `ModelConfigDialog.vue`，**三类**：① 供应商（分组下拉）② base_url / api_key（+「测试连接」与它的
    反馈）③ 模型 / 窗口大小。原来那个把六格摊在页面上的 `ModelRow.vue` 删掉（连同只给它用的
    `.mrow*` / `.field-action` 样式），`.chip` 等通用件留着给弹窗用。
    保存仍是页面顶栏那一个按钮（弹窗只改草稿、点「完成」关掉）—— 一个页面一套提交口径，
    不让「改了但没保存」有两处来源。
- **关键决策**：**不引任何东西**做计时（`setInterval` + 一个 `nowTick`），也不为弹窗引组件库 ——
  复用既有的 `.field/.input/.btn/.chip` 与设计令牌。弹窗里 `模型` 与 `窗口大小` 合成第三类，
  因为窗口本来就是**模型**的属性（与上一批「按模型查窗口」同一个口径）。
- **验证**：`pytest` **1365 passed**（这两项是纯前端，后端没动）；`npm.cmd --prefix frontend run build`
  （含 `vue-tsc`）通过；`rg` 确认 `ModelRow` 与 `.mrow*` 已无引用（模板与样式都清干净），
  三个新/改组件的类名与 CSS 交叉核对无缺口。
- **没验证的**：这两块的实际观感（没有浏览器）—— 请刷新看一眼：计时那一行是否够轻、
  弹窗三类分组是否清楚、列表里「没配过」的行是否只剩按钮。

#### 上下文窗口改成「按模型查」：新增供应商的模型详情表 + 刷新按钮

- **动机**（用户口径）：「上下文长度本质上是和模型相关的，不是和供应商相关的，应该用模型去查对应的
  上下文长度」；并给了表的形状：「不需要 model_context 表，而是**供应商的对应的模型详情表** ——
  每个模型的名字、上下文长度、归属供应商、模型类型；界面上提供刷新按钮，**不主动刷新**」。
  **这条是真的会出错**：同一家 `glm-5.3` 是 1M、`glm-4.6` 是 128k，按家取值时同一家里两个智能体
  配不同模型会拿到同一个窗口 —— 压缩线被推到错的地方，请求早在真窗口之外就报错了。
- **改了什么**：
  - **新表 `provider_models`**（迁移 `f4a5b6c7d8e9`）：`provider`（外键 → `model_providers.provider`，
    即**稳定键**）+ `model` 组成主键，另有 `model_type` / `context_window` / `source`
    （`probe`/`manual`/`seed`）/ `status` / `updated_at`。迁移顺手把 `model_providers.default_models`
    那串名字**逐条搬进新表**（类型按名字判、窗口未知记 0、来源 `seed`）——那列不动，但从此不再被读。
  - **类型**（`model_types.py`）：`chat` + `embedding / rerank / image / video / audio / other`
    （`other` 是我加的：OCR / 审核这类不该掉进 chat 里、出现在下拉中）。判据只能按名字
    （`/models` 不回类型）—— 原来那套「非文本直接丢」的片段规则搬过来并按类型归好，
    **顺手修了一个次序 bug**：`bge-reranker` 会先命中 `bge-` 被判成向量，所以重排要在向量之前判。
  - **窗口解析改成**：① 那一行显式填的 → ② 按 **`(供应商, 模型)`** 查 → ③ 按**模型名**全局查
    （治自建网关：地址对不上任何一家，但同名模型的窗口一样）→ ④ 家那格（`.env`，降级为兜底）
    → ⑤ 兜底值。模型那份是**内存镜像**（`Settings.provider_models`），因为 `resolve_window` 是同步函数。
  - **刷新按钮**（`POST /model-config/providers/{p}/models/refresh`）：拿这家的地址去调清单接口，
    结果**逐条 upsert**（非文本也落表并标类型）；key **借用现有 agent 行的**（一行都没配就看
    `.env` 里这家的凭据）——用户口径：借用 agent 的就行，不新增第二处凭据存放点。**点了才刷**。
  - 另有 `GET .../models`（清单）与 `PUT .../models`（手填/手改窗口、类型、启停，记 `source=manual`）。
    刷新**不删**本地多出来的行（可能是老模型或别处在用）；`context_window=0` **不覆盖**已有的非 0 值
    （很多入口不回窗口，照写会把之前读到的抹掉）。
  - **界面**：设置页「模型配置」末尾多一块「供应商的模型清单」——一家一行（模型数 + 「查看/刷新」），
    展开是模型列表（名字 / 类型 / **窗口可直接手改**）；「模型」输入框的候选改成
    「刚拉到的实时清单 → 这家已登记的文本模型 → 种子那串名字」。
- **关键决策**：窗口的**权威顺序**把「行内显式」放在最前（专治特例），然后是模型表；供应商那格只做兜底，
  这样 `.env` 旧配置行为不变（一行都没配时结果与以前逐字相同）。
- **验证**：`pytest` **1365 passed**（新增 `tests/test_provider_models.py` 33 条：17 组名字→类型、
  类型集合、六种窗口解析情形（含「禁用模型不算数」「自建网关按名查到」「行内覆盖最优先」）、
  清单/刷新/手改/未知供应商/坏类型/无地址/拉不到 409 且不写半条、**空窗口不覆盖已有值**）。
  `ruff` 干净；前端 build 通过。
  **真机验过**（真拉两家 + 真库）：`deepseek` 读到 2 个模型且**入口真回了窗口**
  （`deepseek-flash` / `deepseek-v4-pro` 都是 1048576，落表 `source=probe`）；
  `glm` 读到 11 个模型、**入口不回窗口**（留「未知」并提示手填 —— 这正是那张表存在的理由）；
  库 `provider_models` 里 deepseek 3 条（2 条 probe + 1 条 seed 保留）；窗口解析按角色复核：
  plan/orchestrate(glm-5.3)=1048576、review(kimi-k2.6)=262144、qa(gpt-5.6-luna)=128000（模型表里
  那条窗口是 0 → 退回家那格）。
- **没验证的**：设置页那一块的实际观感（没有浏览器）。
- **遗留**：① `model_providers.default_models` 那列留着没删（回滚要能回去），但已不再被读；
  要不要彻底删列，等你说；② 模型清单只支持「刷新 / 手改」，没有删除与批量停用。

#### 消息下挂「这一轮改了什么」：在**本地代码缓存**里算 PR 的 diff，可点开看单个文件

- **动机**（用户口径）：六条里的 ② —— 「每次改动代码后，把带动的那部分（git diff 的文件列表）
  放到 AI 的消息下边」，要能**点开看 diff**；并且「只针对改了代码的场景」。**只看已提交的**：
  用户明确「展示的前提是代码写完并提了 PR，不存在未提交就该展示的场景」。
- **改了什么**：
  - **数据源是宿主机那份代码缓存**（`repo_cache`，代码面板用的同一份裸仓），**不碰沙箱**
    （用户口径：「本地有缓存的代码，为什么不能本地执行 git diff」—— 对，就该这样；顺带的好处是
    **沙箱被回收之后这些 diff 照样点得开**，而沙箱里算的方案做不到）。
  - `repo_cache` 新增 `compare()`（`base...head` 的文件清单：`--numstat` 给增删行数 +
    `--name-status` 给状态码，按路径并起来）与 `file_diff()`（单个文件的正文，路径先过
    `clean_path`）；两支分支都过 `_resolve_ref`（挡住不存在的分支与 `-` 开头的参数注入）。
    **三点式 `...` 而不是两点式**：PR 讲的是「相对共同祖先改了什么」，两点式会把 base 上
    别人新提交的内容算进来。
  - **base / head 取 `create_pr` 的入参**（`_pr_refs`：dict 与 JSON 字符串两种形状都认）——
    那是**这一轮 PR 自己的声明**，既不用回头猜「最新那个 PR」，也不用多打一次平台接口。
  - 收尾时算一次：本地还没缓存过这个仓库 → 整块不出现（一次 clone 可能很大，不该塞在对话收尾里）；
    缺那两支分支（coder 刚推的）→ 先 `sync()` fetch 一次再算（顺带刷新代码面板）；
    算不出来只记日志，**不影响本轮收尾**。文件数超过 200 只列前 200 并带上真实总数。
  - 结果写进这一轮记录的 `extra["changes"]`（只存**文件清单**，不存 diff 正文 —— 历史记录不该被
    几兆的 diff 撑大），并推一帧 `{"type":"changes"}`，紧挨在收尾那帧 `usage` 之前。
  - **界面**：消息底部多一块（只有建成 PR 的轮次才有）—— 折叠时一行
    `改动 3 个文件 · +42 −7`，点开是文件列表（一位状态码 A/M/D + 路径 + 增删行数），
    **再点某个文件**才去拉它的 diff（`GET /sessions/{id}/repo/diff`，等宽、+/− 着色、
    超过 20 万字符显式截断并说明）。刷新页面走记录的 `extra.changes` 回放，同一套渲染。
- **关键决策**：
  - **不落 diff 正文，只落清单**：正文按需拉（拉的时候顺带脱敏 + 上限），历史记录保持小。
  - **脱敏口径与日志/记录一致**：会话令牌 + 该平台的兜底令牌 + 模型凭据（`secret_terms`）。
  - **不展示未提交的改动**（用户明确的口径）；因此也不需要「轮次起点 HEAD」这种记账 ——
    两边都由 PR 声明。
- **验证**：`pytest` **1332 passed**（新增：`tests/test_turn_changes.py` 15 条 —— base/head 两种入参
  形状与坏输入、本地无缓存则不出现、清单与状态、缺分支先 fetch 再重试一次、失败不影响收尾、
  文件数上限、**建了 PR 才推那一帧且紧挨在收尾账之前**；
  `tests/test_repo_cache.py::TestCompare` 5 条（真 git：文件清单与状态、同一支为空、
  没同步过说人话、只看一个文件、`..` 路径被拒）；
  `tests/test_repo_routes.py::TestDiffForAFile` 4 条（接口层：正例、缺分支 409、没缓存 409、
  越界路径 409））。`ruff` 干净；前端 build（含 `vue-tsc`）通过。
  **真机验过**（用你本机已有的 `wanqi-mall` 缓存，14 支分支逐支跟 master 比）：
  已合入的分支都是 **0 个文件**（三点式语义正确），未合入的两支分别 **18 / 9 个文件**；
  对 `README.md` 取 diff 也正常返回（`+### 3.4 用户登出黑名单降级验证…`）。
- **没验证的**：这一块在聊天里的**实际观感**（没有浏览器）—— 请刷新后找个建过 PR 的轮次看看
  那一行的位置与展开后的样子。

#### 基本设置的运行期配置（代码缓存 / 上下文窗口）＋ 配置热生效（不再重启）

- **动机**（用户口径）：「沙箱镜像加速地址不用了；代码缓存就两个 —— **默认分支**和**代码缓存路径**；
  上下文窗口按你列的来；**热生效**，每次都创建一个新的 agent 就行，不缓存 agent」。
- **改了什么**：
  - **新表 `app_settings`**（迁移 `e3f4a5b6c7d8`，键值对）+ `GET/PUT /app-settings`。键与类型只认
    `config.RUNTIME_SETTINGS` 那一张表（分组 / 中文名 / 类型 / 落到 `Settings` 哪个字段都在那儿），
    认不出的键或转不动的值回 400、**不落半格**。新路由前缀同步加进了 `vite.config.ts` 的 proxy。
  - **两类，按用户口径**：**代码缓存配置** = 代码缓存路径（`repo_view_dir`）+ 默认分支（Gitee / GitHub
    各一格）；**上下文窗口配置** = 告警线 0.6 / 危险线 0.8 + 兜底窗口 + 五家供应商窗口。
    界面：设置页「基本设置」里两组各一行一个输入框，改过的格子标「已改过」，顶栏「保存」写入。
  - **热生效怎么做的（两半）**：① 值赋回**同一个 `Settings` 实例**（读那些字段的地方一行都不用改）；
    ② **主智能体每轮现建、不缓存**（`session/manager.py::get_agent` 去掉了按形态的缓存，
    `SessionRuntime` 不再持有 `agents`）—— 模型与窗口都是建实例时读的，缓存住就换不动。
  - **顺手把「模型配置」也接上热生效**：`put/delete_model_config` 写完之后当场 `apply_model_endpoints`
    刷新内存那份，并且那个函数改成**先清空再落**（否则「恢复默认」会在内存里留下幽灵连接）；
    界面上那句「重启后端后生效」改成「已保存，下一轮生效」。
  - **默认分支真的被用上了**：`repo_cache.default_branch()` 现在是「**设置页配了就用它** → 远程探测
    → master」，并把 `GITEE_BASE_BRANCH`/`GITHUB_BASE_BRANCH` 的代码默认值与 `.env.example` 改成
    **留空**（留空＝按远程探测，推荐）。你 `.env` 里现在是 `master`/`main`，与你自己两个仓库的默认
    分支一致，所以行为不变；想让某个仓库跟随远程，把那一格清空即可。
  - `resolve_window` 的兜底从写死的常量改成读 `settings.fallback_context_window`（填 0 仍退回常量：
    窗口 0 会让压缩永不触发）。
- **关键决策**：
  - **不做镜像加速地址**（用户明确不用了），也没动沙箱资产推入那套。
  - **不加 TTL 缓存**：值就在内存里，写入路径（PUT）自己刷新，启动时再读一次；直接改库时打开设置页
    （GET）也会重新落一遍 —— 不需要「隔几秒回源」这种机制。
  - **环图的告警线由服务端给**：`_turn_context` 把 `warn_ratio` / `alert_ratio` 一起发下来，
    前端不再写死 0.6 / 0.8（阈值只有一个出处，改设置页立刻影响环图变色）。
- **验证**：`pytest` **1308 passed**（新增 `tests/test_app_settings.py` 17 条：键表指向真实字段 /
  只认三种类型 / 类型转换与非法值 / 落值 / 坏行不连坐 / GET 列全并标 source / PUT 当场生效且不动别的键 /
  GET 会把库里的值重新落 / 认不出的键与坏值 400 且不落半格 / 空串是合法值 / 兜底窗口跟着设置 /
  默认分支跟着设置 / **`SessionRuntime` 不再持有 agents** 与**两次调用两个实例**；
  另改 `test_profiles.py` 那个「按形态缓存」的用例类为「每次现建」、并在 `test_model_config.py`
  加 2 条「保存/恢复默认当场刷新内存」）。`ruff` 干净；前端 build（含 `vue-tsc`）通过。
  **真库验过**（ASGI 直接打路由）：GET 回 11 格（两组）、`source` 全 `default`；
  `PUT glm_context_window=262144` → 库里有行、`source=db`，**同一个进程里 `Settings` 当场就变成
  262144**（没重启）；验完把探针写的那行删掉、值落回 1048576，库是干净的。
- **没验证的**：设置页那一屏的实际观感（没有浏览器）—— 请刷新看看两组输入的排布与「已改过」标记。
- **遗留**：① 「模型配置」现在是**下一轮**生效（正在跑的那一轮不换）—— 与 backlog 1.1 的「热切换」
  基本等价，落地后再看要不要把 1.1 关掉；② 六条里的 ②（消息下挂 git diff 文件列表）还没做，下一步。

#### 聊天区四件：token 动态推帧、末行标模型、上下文环图、刷新回到原页

- **动机**（用户六条里的 3 / 4 / 5 / 6）：① token 消耗要**通过 SSE 动态返回**；② AI 回复最下边
  要有**回答的模型**；⑤ 上下文使用情况改**饼图**、放在**发送框右边**；⑥ 刷新**回到原来那一页**
  （刚才在设置页，刷新还在设置页）。剩下两条（基本配置扩项、消息下挂 git diff 文件列表）
  按用户给的补充口径单独做，见文末。
- **改了什么**：
  - **动态推账（③）**：`server/service.py` 在 `message-finish` 与 `tool-finished` 两处各推一帧
    `{"type":"usage"}`，`_live_usage()` 按 `_USAGE_FRAME_MIN_GAP`（0.5s）节流。前端那条分支本来就
    在更新消息角标，界面不用改 —— 数字会跟着跑起来长。收尾那一帧仍在最后（权威值）。
    **顺手修了一个真 bug**：`_turn_context` 的压缩次数是 `+=`，中间帧要多算几次就会重复累加
    → 加了 `commit=False` 的只读路径（连 `_context_state` 里那一格都不建），收尾才落账。
  - **末行标模型（④）**：`agent/llm.py` 新增 `resolve_model()`（与 `resolve_window` 同一套优先级：
    unified → 该智能体那一行 → `.env`），`_turn_usage` 把它写进 `usage.model`（每帧都带），
    前端把模型名放进消息**末行**（与时间同一行，形如 `glm-5.3 · 20:14`）。
    **不受「显示用量」开关影响** —— 它是「谁答的」，不是账。
  - **上下文环图（⑤）**：输入框上方那条 `ctxbar`（横条 + 一句常驻文字）**去掉**，改成发送框右边的
    SVG 环图（`pathLength="100"` 让 dasharray 直接用百分比）；<60% 中性、≥60% 警告色、≥80% 危险色
    （阈值与服务端算 `ratio` 的口径一致），原来那些文字挪进 `title`/`aria-label`，信息没丢。
  - **刷新回到原页（⑥）**：新增 `frontend/src/viewState.ts` —— 「在哪一页」记在 URL hash 上
    （`#settings/basic|look|models`，空 hash ＝ 聊天）。用 hash 不用 localStorage：不会发到服务端
    （后端零改动）、刷新天然还原、还能贴给别人；**写用 `replaceState`**，否则在设置里点三个面板
    要按三次返回才回聊天。当前栏位由 `App.vue` 持有（它负责写 hash），`SettingsPage` 改成收 prop + 发事件。
- **关键决策**：
  - **只推本轮累计**（用户口径）。会话累计仍在右栏 Token 面板，那里是聚合视图、数据源是各轮记录。
  - **模型名标的是「解析值」**：真走了兜底这里不体现 —— 兜底是运行时的第二跳，账与调用都记在同一个
    角色名下，实测的 v3 事件载荷里没有「这次是谁答的」这一格。schema 注释里写明了这一点。
  - **不引路由库**：总共两页，一个 hash 够用。
- **验证**：`pytest` **1289 passed**（新增 4 条：末行模型名按形态取、中间帧不写跨轮累计、
  0.5s 节流、**跑动中确实推了中间帧**（至少 3 帧且最后一帧是收尾那帧）；另改了 3 条原来钉
  帧序列的用例，改成「不看 usage 帧数量、只看它排在最后」）。`ruff` 干净；
  `npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过。
  **hash 逻辑拿真代码验过**（`tsc` 编译 `viewState.ts` 后用 node 跑）：9 个解析用例
  （空 / `#` / `#settings` / 三栏 / 大小写 / 认不出的栏位 / 认不出的 hash）全对，
  写回序列也验了「同值不重复写」与「回聊天清掉 hash」。
- **没验证的**：环图与末行模型名的**实际观感**（这台机器没有浏览器）—— 请刷新看一眼：
  环图在发送框右边是否对齐、60%/80% 换色是否符合直觉、模型名与时间同一行是否够轻。
- **遗留**：六条里的 ①（基本配置扩项）与 ②（消息下挂 git diff）见下一条。

#### 「测试连接」拉回来的清单只留文本模型

- **动机**（用户口径）：「查询出来的模型只需要文本模型，不需要其他的类型，需要过滤一下」。
- **改了什么**：`api/routes/model_config.py` 新增 `_NON_TEXT_MARKERS` + `is_text_model()`，
  并把「解析 ``/models`` 返回」抽成 `parse_models()`（HTTP 那半仍在 `fetch_provider_models` 里）——
  解析完就地滤掉图像 / 视频 / 语音 / 向量 / 重排 / OCR / 审核这些非文本模型。
- **关键决策**：
  - **只能按名字判**：OpenAI 兼容的 ``/models`` **只回名字、没有类型字段**（实测这台网关回 888 个
    名字、一个类型字段都没有），所以用片段匹配而不是整词 —— 厂商命名基本是
    ``<系列>-<版本>-<档位>``，片段才盖得住 ``gpt-image-2.5-sunburst`` / ``doubao-seedance-2-5`` /
    ``text-embedding-3-small`` 这些写法。
  - **宁可漏、不可误杀**：凡是「既是文本又是别的」的片段一律不进表 —— `seed`（`doubao-seed-2-1-pro`
    是聊天模型）、`vl` / `vision` / `omni`（多模态**聊天**）、`flash` / `mini` / `lite`（只是档位）、
    裸 `wan`（只认 `wanx` / `wan2`）。误杀文本模型比漏掉一个图像模型更难发现。
  - **只筛拉回来的实时清单，不筛手写的 `default_models`** —— 那是人挑过的，写进去又看不见才叫怪。
- **验证**：`pytest` **1285 passed**（新增：非文本 35 个名字必须被滤、文本 21 个名字必须留下、
  两种返回形状 + 窗口取值、坏 payload 回空）；`ruff` 干净。
  **真机验过**（用 `.env` 里那个网关的实时清单）：厂商回 **888** 个 → 留 **743** 个文本模型、
  滤掉 **145** 个；按命中片段归类逐个看过，**没有一例误杀**：
  `image` 39、`realtime` 17、`audio` 12、`tts` 11、`gpt-image` 11、`wanx` 9、`seedance` 9、
  `video` 6、`seedream` 6、`kling` 6、`wan2` 5、`embedding` 4、`transcribe` 3、`sora` 2、
  `dall-e` 2、`whisper` 1、`banana` 1、`seededit` 1。留下的头几个是
  `gpt-6.1-sol` / `gpt-6-astra` / `gpt-6-sol` / `gpt-6-luna` / `gpt-5.6-luna`。
- **要你做的一件事**：**重启后端**（同上一批改动一起生效）。
- **遗留**：网关把同一代模型的**日期快照**也全列出来了（`gpt-5.5-2026-04-23` 这种），现在会一起
  留在候选里 —— 数量多但都是文本模型。要不要再收一道（只留不带日期后缀的别名 / 或每个系列只留最新
  几个）？这属于口味问题，等你说。

#### 目录改成「每条请求现读库」＋「测试连接」的门槛只认地址与 key

- **动机**（用户两个问题）：① 「这个目录的作用是什么，不可以直接读库嘛」；② 「只要有 apikey 和
  base_url，就可以支持测试连接的功能」。
- **目录是干什么的**（回答①）：它就是界面上「提供商」那一栏 —— 下拉里有哪些家、选中带出哪个地址、
  拉清单走哪个路径与鉴权头、拉不到时给哪几个候选名字、分组排序与启停。**它不参与运行时**：
  不决定谁用哪个模型，也不影响任何 LLM 客户端。当初放进内存只有两个理由（启动时「表为空灌一次
  种子」本来就得读写库；当时把「改完要重启」当成统一口径）—— 没有一条要求它必须缓存。**所以改成现读库。**
- **改了什么**：
  - `config.py`：把排序抽成模块级 `sort_provider_metas()`（启用项 + `ui_group → sort_order →
    provider`），现读库那份与内置兜底那份共用同一个出处；`Settings.provider_metas()` 降级为
    **兜底**（读库失败 / 根本没有种子文件的老环境）。
  - `api/routes/model_config.py`：新增 `_provider_metas(settings, db)`，`GET/PUT/DELETE /model-config`
    与 `POST /model-config/probe`、`/providers/reload` **全都现读库**。读库失败只告警并退回启动时
    那份 —— 一个下拉框不该让整页打不开。
  - `POST /probe` **不再看 `is_dynamic_model_list`**：只要发得起请求就发（拉不到是预期结果之一，
    照旧回内置候选 + 错误原文）。
  - 接口瘦身：`ProviderOption` 去掉 `is_dynamic_model_list` 与 `requires_key`（前者不再影响任何行为、
    后者被「地址 + key」这条口径取代）；前端 `ModelRow.vue` 的「测试连接」**永远显示**，
    缺 `base_url` 写「先填 base_url 再测」、缺 key 写「先填 api_key 再测」，两样齐了就置灰解除。
  - 种子文件：把你在库里手填的两个地址同步回来（MiniMax / 小米 MiMo）—— 否则谁打一次 reload
    就会把它们冲成空。`is_dynamic_model_list` 的注释改成「纯标注位」。
- **关键决策**：
  - **不改 `model_endpoints` 的生效时机**。「模型连接」那八行仍然启动时读进 `Settings.agent_models`
    （它要影响正在跑的智能体，属于热切换，见 `docs/backlog.md` 1.1）；**只有目录改成现读**。
    两者字段都在设置页里，所以界面上的差别是：改库后目录刷新即见，模型连接要重启。
  - `is_dynamic_model_list` **保留**（表里那一列、种子里那几行都不动），但明确降级为标注位
    「这家官方有没有 `/models`」；它的**唯一**作用是不再有作用 —— 想删列要单独给迁移，不值得
    为它再动一次表结构。
- **验证**：`pytest` **1227 passed**（新增 3 条：目录每条请求现读、坏目录退回兜底、探测不受开关
  影响；另改了 4 条原来靠 `settings.providers` 喂数据的接口测试）；`ruff` 干净；
  `npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过。
  **真库上端到端验过**（ASGI 直打路由，**故意不跑启动装配**）：
  ① 第一次请求就回 13 家（＝库里当前的行数，说明不靠启动缓存）；
  ② `UPDATE model_providers SET display_name='★临时改的名字' WHERE provider='custom'` 之后
  **下一个请求**就回新名字，进程没重启、页面没刷新（验完已还原）；
  ③ 对小米（库里那行 `is_dynamic_model_list=false`）探测 —— 请求真的发出来了
  （`base_url=https://api.xiaomimimo.com/v1`、`path=/models`、`Authorization: Bearer`），
  结果 `ok=true / source=live`。
- **要你做的一件事**：**重启后端**（这次改的是后端路由，vite 热更新不够）。前端刷新即可。
- **发现一件事，需要你确认**：库里现在是 **13 行** —— 你删掉了 `anthropic`、`step`、
  **`gpt`（自用网关）** 三行。那两行没有地址、删掉合理；但 `gpt` 那一行是 `.env` 里你正在用的
  网关地址（删它不影响运行，出厂默认仍走 `.env`）。**种子文件里这三节我暂时没删** ——
  一旦有人打 `POST /model-config/providers/reload`，它们会被重新插回库里。要不要我把这三节
  也从种子文件里删掉（让文件与你现在的库一致）？你说一声就改。

#### 提供商目录按用户给的清单补齐：16 家，地址/模型名都填上

- **动机**（用户口径）：「各家提供商对应 OpenAI 的模型配置我已经写到 config 目录下另外一个文件中了，
  你根据这个，填充一下提供商和默认的 url」—— 源文件是 `config/20261014161953.md`。
- **改了什么**：`config/model_providers.toml` 重排成 16 家（国内 10 + 海外 5 + 逃生口 1），
  `default_base_url` 与 `default_models` 按源文件填；`tests/test_model_config.py` 里钉种子内容
  的那条断言跟着改成新事实，并新增一条「种子文件形状」检查（键唯一且合法、地址是干净的 https、
  模型名无空白、**国内分组整组排在海外分组前面**）。
- **关键决策**（都是「源文件没写就不编」的落地）：
  - **没给地址的留空 + 标 `# TODO`**（Anthropic / MiniMax / 小米 MiMo / 阶跃星辰 / 科大讯飞星火），
    界面照常出现这一项、模型候选也在，只是地址要用户自己填 —— 猜一个地址写进去，代价是用户点
    「测试连接」拿到 404。
  - **Qwen 是唯一一处「我补的地址」**：源文件只在正文里提到百炼平台、没给 base_url，这里填的是
    百炼的 OpenAI 兼容入口 `https://dashscope.aliyuncs.com/compatible-mode/v1` 并**在文件里标了
    「请核对」**。Kimi 同理由：地址沿用 `.env` 里正在用的那个。
  - **`gpt` 与新增的 `openai` 是两个入口，不是重复**：`gpt` 是 `.env` 里那个自用网关
    （实测 /models 能列出来，候选清单写的是实测见到的名字），`openai` 是官方直连
    （`https://api.openai.com/v1`）。**同一个 key 拿错边会 401**，所以把显示名区分为
    「GPT（自用网关）」与「OpenAI 官方」，而不是把原来那一项改指官方 —— 那会让你现在能用的
    那一项变成一把打不开的钥匙。
  - **`is_dynamic_model_list` 按源文件那条口径分**：文件说 OpenAI / Anthropic / Gemini / DeepSeek /
    火山方舟这类**有 `/models`** 的给 `true`（界面给「测试连接」）；文件说「国内厂商模型名变化快、
    规范不统一 → 本地维护」的那几家（混元 / MiniMax / MiMo / 阶跃 / 星火）给 `false`，
    界面直接用候选清单、不给一个点了多半会失败的按钮。
  - **Mistral 没建条目**：源文件自己写着「未取到最新模型名」，地址和模型都没有 —— 加了就是个空壳。
- **验证**：`pytest` **1225 passed**（新增 1 条形状检查 + 改了 1 条内容断言）；
  `ruff check tests/test_model_config.py` 干净。真库上跑了
  `app.reload_model_providers`（幂等）：**表里 16 行**，分组与排序与文件一致
  （国内 10/20/…/100 → 海外 110/120/…/150 → 无分组 900），`/model-config` 的同一段代码回出来的
  顺序也一致。种子解析打印过一遍，地址与模型名逐条对上。
- **要你做的一件事**：**重启后端**（或对着跑着的那份 `POST /model-config/providers/reload`）——
  这次对齐的是库，正在跑的进程里的 `Settings.providers` 还是旧的。前端刷新即可。
- **遗留**：① 5 家地址待补（文件里已标 TODO），补完再跑一次 reload 即可；
  ② 你 `.env` 里在用的模型名（`deepseek-flash` / `gpt-5.6-luna` 等）**没有动** —— 那是运行时的
  出厂默认，与「界面上可选的候选清单」是两回事；要跟着换新代模型名的话，改 `.env` 再重启。

#### 设置改成工作台：左菜单三栏（基本设置 / 外观 / 模型配置）＋深色主题

- **动机**（用户口径）：「设置里，基本设置、外观、模型配置，放在左侧，每个菜单单独展开，
  像工作台的样子」。上一版虽然是整页，但只有模型配置一栏、全堆在一列里。
- **改了什么**：
  - **工作台骨架**：`SettingsPage.vue` 左菜单三项 + 右内容区（`--settings-max` 960 → 1120，
    菜单 200 + 内容自适应）；<720px 菜单改成横向可滚的标签条，<640px 设置项上下堆叠。
    菜单是数组驱动的，以后加一栏只加一行。**不引路由**，切换是页面内状态。
  - **基本设置**（用户点名要的两项，**不做 JEV** —— 意图识别整块留在模型配置里）：
    ① 默认打开的右栏标签（以前每次加载都回「代码」）；② 恢复默认布局（两个栏宽 + 收起状态
    + 退出右栏全屏；只改 `usePanelWidth` 的状态，落盘照旧走它自己的 watcher，不写第二套口径）。
  - **外观**：主题（浅色 / 深色 / 跟随系统）、代码字号（12–16，默认 14）、对话列宽
    （紧凑 / 标准 / 宽，标准档就是原来那个 `clamp(760px, 60vw, 1400px)`），都是**改完立刻生效**。
  - **偏好存在本机**：新增 `frontend/src/prefs.ts`（localStorage，容错读写真到一个 try/catch，
    隐私模式下记不住也不白屏），`main.ts` 在挂载**之前**调 `initPrefs()` 落到 `<html>` 上，
    免得先按默认画一帧再跳。`App.vue` 的初始右栏标签改成读这个偏好。
  - **深色主题**：只换令牌、不换结构（组件里没有一处判断主题）。`style.css` 末尾加
    `:root[data-theme='dark']` 覆盖全部颜色令牌 + `color-scheme: dark`（滚动条与原生控件跟着变深）
    + 逐类覆盖 highlight.js 的 token 颜色（`markdown.ts` 引的 github.css 是浅色主题，
    `.hljs` 有 `background:#fff`，不覆盖的话深色下会是一块白板）。
- **关键决策**：
  - **强调色拆成两个令牌**：`--accent` 专门当**文字/图标**（深底上要亮），`--accent-solid`
    专门当**按钮底与用户气泡底**（够深，白字才够对比）。同一个颜色干两种活在深色下必然顾此失彼
    （亮蓝当底 → 白字 2:1；深蓝当字 → 4:1 都不到）。浅色下两者同值，观感**逐字不变**。
  - **顺手把写死的颜色收编成令牌**（深色下它们本来会变成刺眼的白块与看不见的深灰）：
    `#fff` → `--surface`（8 处）、`#1a9e5c` → `--ok`、`#d64545` → `--danger`/`--danger-text`、
    `#a8700d` → `--warn`、`#c8c8c8` → `--dot-idle`、`rgba(0,0,0,.055/.06)` → `--code-inline-bg`、
    `rgba(0,0,0,.08)` → `--hover-strong`、`rgba(214,69,69,.07)` → `--danger-soft`。
    这些令牌的**浅色取值与原字面量完全相同**，所以浅色页面一处没动。
- **验证**：`npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过。
  **对比度按 WCAG 公式实算**（不是眼估）：深色下正文 13.5:1、次要文字 6.41:1、链接 6.79:1、
  危险文字 8.02:1、成功 7.01:1、警告 8.19:1、白字落在实心按钮上 5.67:1；浅色下正文 16.4:1、
  次要文字 4.97:1、链接 5.67:1 —— **全部 ≥ 4.5:1**。
- **没验证的**：**深色下的实际观感没看**（这台机器没有浏览器、也看不了图片）：
  三栏工作台在两个断点下的排布、深色下代码块与右栏各面板的配色、原生下拉/滚动条的深浅，
  都要你刷新后扫一眼。理论上不该有「看不见的字」——所有颜色都走令牌了，但装饰性的
  `plan-pulse` 光环仍是写死的 `rgba(77,107,254,…)`（深底下是一圈蓝光，读数不影响）。
- **遗留**：左栏收起成竖标签时仍然没有设置入口（上一轮就记了，这次按范围没动）。

#### 设置从弹窗改成整页，并删掉界面上的解释性文案

- **动机**（用户反馈）：「设置页面要新开一页；把界面的多余的解释性语言先去掉；现在看着界面又小又乱」。
  按 `zack-design-router` 路由到 `zack-redesign-audit`（改版流程）+ `zack-layout-and-rhythm`
  （密度与留白）。诊断结论是**密度与层级**问题，不是审美问题：940px 弹窗里 8 行 × 6 个字段横排，
  行内 padding 只有 8/10px，字号全压在 12px，再加上四段口径说明 —— 所有东西等权重，看不出结构。
- **改了什么**：
  - **整页**：`SettingsModal.vue` → `frontend/src/SettingsPage.vue`。顶部固定条（返回 / 设置 / 保存），
    内容区居中 960 + gutter，滚动只发生在内容区；`App.vue` 用一个 `view` 开关切页，齿轮的
    `title`/`aria-label` 从「模型配置」改回「设置」，`Esc` 也能返回（整页没有「点空白关掉」这条退路）。
  - **切页用 `v-show` 藏聊天区**（`v-if` 会卸掉 DOM）：正在跑的流还在往 App 的状态里写，
    卸了再挂回来只是徒增空引用风险。
  - **字段重排**：一行六格 → **一格一个字段、一行两格**（960 容器里每格约 460），窗口那一格限宽
    160（数字框占满一整格看着像坏了）。四个小节合成三个（各智能体 / 全局 / 意图识别）。
  - **删解释性文案**（用户口径）：弹窗副标题、四个分节的说明小字、页脚「key 只以打码值回显」、
    输入框里的「留空＝出厂默认」「留空＝128k」、「候选来自目录（可能过时）」、
    「这家没有实时清单接口，可直接用下面的候选」。probe 结果也压成一行：
    `已连接 · N 个模型 · 窗口 X 已填上` / `未拉到清单 · 内置候选 N 个` / `连不上 · <原文>`。
  - **只留操作反馈**：报错与保存结果、按钮为什么置灰（`先填 api_key 再测`）、连不上的错误原文、
    状态 chip、key 打码值；「JEV 未配置」缩短成一行。
  - **字号收敛**：13（正文/输入/按钮）与 15（节标题）两级，12 只给字段标签、chip、打码值。
  - **间距登记成令牌**：`:root` 加 `--s-1..--s-7`（4/8/12/16/24/32/40）与 `--settings-max`
    /`--settings-gutter`；显示上「段与段 40、行与行 12」≈ 3.3 倍，结构分得开。
  - 顺手清掉的死代码：`.modal-mask` / `.modal` / `.modal-head` / `.modal-sub` / `.modal-banner`
    / `.modal-scroll` / `.modal-foot` / `.foot-hint` / `.section-head` / `.field-select`，
    以及模板里从没有样式对应的 `mrow-configured`、`field-wide`。
- **关键决策**：**做应用内整页（方案 A），不引 vue-router**。真路由要吃一个新依赖，而且后端目前
  不托管前端产物，将来用 nginx 托管 `dist` 还得配 SPA fallback（否则刷新 `/settings` 404）——
  收益只是地址栏可见，不值这个风险。范围只动这一屏：没碰左栏、右栏、聊天区、后端与依赖。
- **验证**：`npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过；`rg` 确认
  `modal-` / `section-head` / `field-select` / `field-wide` / `SettingsModal` 在 `frontend/` 下
  已无任何引用（样式与模板都对齐了）。改前把四个前端文件备份到仓库外，要退直接覆盖回去。
- **没验证的**：**1440 / 1024 / 390 三个宽度的实际观感没看** —— 这台机器没有浏览器、也看不了图片，
  布局是按 `--settings-max: 960` 与两个断点（640 单列）推的。请你刷新看一眼，哪一格还挤就说，
  我按断点调。同时**后端没重启**（本轮只动前端，vite 热更新即可）。
- **遗留**：左栏收起成竖标签时**没有设置入口**（齿轮只在展开态的品牌行里）—— 这是老问题，
  这次没顺手改，要的话下次把齿轮也放进收起态的竖标签条。

#### 提供商目录改放表：`config/model_providers.toml` 只做种子，界面按目录分组、按需拉清单

- **动机**（用户三个口径）：① 提供商目录**放表**（不是代码常量）；② 那份「内置候选模型清单」
  由用户手填、平台只给格式；③ 模型清单**只在点「测试连接」时拉**，不轮询、保存时也不拉。
  实测依据：`api.deepseek.com/models` 不带 key 回 `401`，而 api_key 只能由厂商签发给用户 ——
  平台不可能替用户拿到 ⇒ 必须有「拉不到时给你一份候选」这条退路，否则没填清单的用户只能手打模型名。
- **改了什么**：
  - **新表** `model_providers`（迁移 `d2e3f4a5b6c7`）＋**种子文件** `config/model_providers.toml`
    （6 家：deepseek / kimi / glm / gpt / qwen / custom；字段＝显示名 / 默认地址 / 分组 / 排序 /
    启停 / 拉不拉实时清单 / 拉的路径与鉴权头 / 内置候选）。`default_models` 已按 `.env` 里**正在用**
    的模型名先垫上，用户按需增删。
  - `config.py`：`ProviderMeta`、`load_provider_seed()`（白名单字段、坏节跳过）、
    `provider_meta_of` / `provider_metas_of`（表行 → 目录项）、`Settings.providers`；
    `provider_catalog()` → **`provider_metas()`**，并在**后端**排序（`ui_group` → `sort_order` →
    `provider`）—— 下拉的分组顺序只有一个出处。
  - `db/dao.py`：`list_model_providers` / `count_model_providers` / `upsert_model_provider`
    （整行覆盖）/ `seed_model_providers`。
  - `api/app.py`：`apply_model_providers`（启动时读表；**表为空才灌一次种子**）＋
    `reload_model_providers`（按种子文件对齐：同名行覆盖、表里多出来的行不动）。
  - `api/routes/model_config.py`：新增 `POST /model-config/providers/reload`；
    `fetch_provider_models` 按目录行带 `models_path` / `auth_header` / `auth_prefix`（有的网关是
    `/v1/models`、有的用 `x-api-key`）；快照的 `providers` 多带 `ui_group` / `sort_order` /
    `is_dynamic_model_list` / `requires_key` / `default_models`；`/probe` 三条退路并用 `source` 标明。
  - **前端**：`ModelRow.vue` 下拉按 `ui_group` 画 `optgroup`（无分组的「自定义」**排在最后** ——
    它是逃生口，不该占默认位置）；这家的目录行说没有实时清单、或要 key 而 key 没填时，
    「测试连接」置灰并写明原因；模型候选**优先用实时清单**，否则用内置候选并标注「可能过时」。
    `SettingsModal.vue` 把选中的提供商一起发给 `/probe`，并按 `source` 说人话（没真连上就不写「连上了」）。
  - **顺手修的**：上一轮把 `ModelProvider` 的类定义插进了 `AgentSession` 中间，等于把会话表的
    `last_active_at` / `expires_at` / `repository` 三格切给了提供商表。已挪到 `ModelEndpoint` 之后。
- **关键决策**：
  - **表为空才灌种子**：库里非空就以库为准（用户在库里的改动不该被下次启动顶掉），要重新对齐走
    reload 接口；这是「种子文件」与「配置源」的分界。
  - **键仍然是智能体**：提供商表只管「界面怎么挑、去哪拉清单」，**不决定谁用哪个模型** ——
    避免出现两套配置轴互相打架。
  - **拉不到 ≠ 报错了事**：连不上时 `ok=false` + 错误原文照回，同时把内置候选一起给出去；
    「能不能连上」和「有没有名字可选」是两件事。
- **验证**：`pytest` **1224 passed**（新增 17 条：种子解析 / 白名单 / 表优先于种子 / 停用项不出现在
  目录 / 排序 / 表行→目录项 / 启动「空表灌种子」与「非空不灌」/ probe 按目录行取路径与鉴权头 /
  三条退路 / reload 与种子缺失回 400）。`ruff check` 本次改动文件干净（`models.py` 只剩既有的
  4 条告警）。`npm.cmd --prefix frontend run build`（含 `vue-tsc`）通过。真库 `alembic upgrade head`
  到 `d2e3f4a5b6c7` 成功；用 ASGI 直接打真库的 `GET /model-config`：目录 6 行按
  「国内厂商(10/20/30/50) → 海外厂商(40) → 无分组(90)」返回，八行智能体仍是 `source=default`（不预填）。
  **真机探过「测试连接」三条路**：拿 qa 那一行的出厂 key 探它自己那家网关 →
  `ok=true / source=live`，拉回 200 个模型名（前几个是 `gpt-6.1-sol` / `gpt-6-astra` / `gpt-6-sol` /
  `gpt-6-luna` / `gpt-5.6-luna` —— 比 `.env` 里在用的那个新，正好说明「内置候选只是兜底、实时更准」）；
  拿同一把 key 去探 deepseek、以及匿名探 deepseek → 都是 `ok=false` + `401` 原文 + 内置候选，
  **不是 500**。
- **没验证的**：**界面没截图**（这台机器没有浏览器、也无法查看图片）—— 分组下拉、置灰按钮、
  「可能过时」标注的观感请刷新看一眼。**正在跑的后端还是旧代码**（进程里仍是 `provider_catalog`），
  要重启后端才会走新装配。
- **遗留**：① `config/model_providers.toml` 里 `default_models` 由用户补全（现在垫的是 `.env` 里
  在用的那几个）；② 模型**热切换**仍立项（`docs/backlog.md` 1.1）。

### 2026-10-12

#### 模型配置界面重做：先选提供商，再填它那一套（+ 样式改用项目既有令牌）

- **动机**（用户反馈）：① 界面很丑；② 少了「提供商」那一层 —— 应该**先选提供商**，再填对应的配置。
  按 `zack-design-router` 路由到 `zack-anti-ai-look`（诊断）+ `zack-redesign-audit`（改版流程）。
- **诊断**（扫描对象＝上一版 `SettingsModal.vue` 与它的样式）：

  | 位置 | 问题 | 依据 | 严重度 |
  | --- | --- | --- | --- |
  | 齿轮按钮 | 用 emoji「⚙」当图标 | 铁律 6 /「emoji 当图标」 | P1 |
  | 整个弹窗 | 缺「提供商」这一层：要用户自己知道并手填 base_url | 用户反馈（功能缺失） | P0 |
  | 整个弹窗 | 8 行 × 5 个输入框平铺、全靠 placeholder 没有 label；四类信息挤在一列 | 「处处等量」＝没有层级 | P1 |
  | 样式 | 游离色值 `#64748b` / `#b45309` / `rgba(15,23,42,…)`，不用项目令牌 | 「直接用框架默认调色板」 | P1 |
  | 状态 | 只有 disabled；没有 `:focus-visible`；「恢复默认」与「测试连接」同款皮肤（危险动作没区分） | 状态缺失 | P1 |

- **改了什么**：
  - **接口** `api/routes/model_config.py` + `config.py`：快照多两格 —— `providers`（内置厂商目录：
    key / 中文名 / base_url，**base_url 取自 `.env`** 里你正在用的那个地址）与每行的 `provider`
    （**按 base_url 反推**：纯界面提示、不存库 ⇒ 不必加列、不必再迁移）。
  - **界面**：`SettingsModal.vue` 重写 + 新增 `ModelRow.vue`（一行连接一个组件，四个小节复用，
    免得同样的标记抄四遍漂掉）。每行字段顺序＝**提供商 → base_url → 模型 → key → 窗口**：
    选中提供商即带出它的默认地址（仍可改），「测试连接」拉到的模型进 `datalist`、读到窗口就替你填。
  - **分节**：各智能体（5 行）/ 统一模型 / 兜底模型 / 意图识别（select + 未配置提示 + 分类模型那行）。
  - **样式**（`style.css`）：整段换成**项目既有令牌**（`--surface` / `--border` / `--accent` /
    `--danger` / `--r-*`），一个强调色只给主按钮，危险动作（恢复默认）用 `--danger` + 二次确认；
    字号收成 12 / 13 / 15 / 20 四级；补 `:focus-visible`；`transition` 只写具体属性并给
    `prefers-reduced-motion` 关掉；<720px 断点把六个字段改两列堆叠；齿轮换成内联 SVG。
- **关键决策**：**不把「提供商」存库** —— 它只影响界面带出哪个 base_url，真正决定用哪套连接的是
  base_url + 模型 + key（见 `llm._config_for`）；按 base_url 反推既满足界面需求又零 schema 变更。
- **验证**：`pytest` **1207 passed**（新增 2 条：目录取自 `.env`、provider 由 base_url 反推）；
  `npm --prefix frontend run build`（含 `vue-tsc`）通过；`/model-config` 经 vite 代理回 200。
- **没验证的**：**三宽（1440 / 1024 / 390）截图没看** —— 这台机器没有浏览器可用、也无法查看
  图片，布局是按网格与断点推的。请刷新界面看一眼；哪一格挤了告诉我，我按断点调。
- **不建议改的**（诊断的一部分）：**不动**项目既有的字号 / 色阶 / 圆角刻度（上一轮改版刚定的，
  这次是沿用它）；**不动**左栏 / 右栏 / 聊天区的样式（这次只说设置弹窗）；**不引**任何前端依赖。
- **遗留**：热切换不变（`docs/backlog.md` 1.1）。

#### 修「点开模型配置就报错」：vite 的开发代理白名单漏了 `/model-config`

- **现象**（用户报障）：重启后端后，点左上角齿轮立即报错。
- **定位**：后端本身是好的 —— 直接 `curl http://127.0.0.1:8000/model-config` 回 200 与那八行
  数据；而 `frontend/vite.config.ts` 的 `server.proxy` 是**逐个前缀列的白名单**
  （`/sessions` `/chat` `/health` `/auth`），`/model-config` 不在里面 ⇒ 前端 `fetch('/model-config')`
  打到了 vite 自己，拿到 404 HTML ⇒ 弹窗一开就显示「读取模型配置失败：404」。
- **改动**：`vite.config.ts` 的 proxy 加上 `'/model-config': BACKEND`，并在那一行上面写清
  「**后端路由前缀要逐个列这里，漏一个就是『前端 404、curl 200』**」；`AGENTS.md` 的「前端」
  那条也补了同样的规则（外加一句：dev 只监听 IPv6 回环，用 `localhost:5173`）。
- **验证**：vite 自己热重启后，经代理访问 `POST http://localhost:5173/model-config/probe`
  回 200（我故意不带 key，拿到的是 `ok=false` + 可读的 401 文字 ⇒ 错误路径也走通、不是 500）；
  `GET /model-config` 经代理回 200 与八行数据。
- **遗留**：无（vite 改配置会自己重启；若你手动起的 dev 没重启，重启一次即可）。

#### 模型配置按**智能体**落地：界面填、存 `model_endpoints`、意图识别改三关

- **动机**（用户口径，逐条）：配置的键是**智能体**（不是供应商）；行名用中文；上下文窗口
  **不让用户填**（先读、读不到再让他填）；**不给默认值**（没填就维持今天的行为）；兜底与
  意图识别各占一行；JEV 那套不动；意图识别**二选一**（优先 JEV，没有 JEV 就用大模型）——
  于是变成**三关**；模型热切换只立项。
- **改动**：
  - **表**：`model_endpoints`（`agent` 主键 + base_url / model_name / api_key / context_window），
    一条 alembic `c1d2e3f4a5b6`（**已在真库跑过** `alembic upgrade head`）。
  - **解析层**（`agent/llm.py`）：`agent_key()`（形态 → 行；三个子智能体跟 `coding` 那一行）、
    `_row_for()`（`unified` → 该智能体那一行）、`resolve_window()`（行 → 出厂默认那家的窗口 → 128k）、
    `_config_for()`；`build_fallback()` 先用界面的「兜底模型」那一行，没有才走老的
    `FALLBACK_PROVIDER`；`build_classifier_model()` 先用「意图识别（大模型）」那一行，其次
    `unified`，再回落出厂默认。**一行都没配时行为与改动前完全一致**。
  - **启动加载**：`api/app.py::apply_model_endpoints()`（启动时读一次；读库失败只告警、
    照常用 `.env` 起）。`model_config` 表里的 `intent_mode` 行决定意图识别走哪一关。
  - **窗口**：`service._main_window` 改用 `resolve_window`（不再绕 `_provider_attr`）；
    墙上那句「读不到」由界面的「测试连接」承担（拉厂商 `/models`，读到就替用户填上）。
  - **接口**（`api/routes/model_config.py` + schemas）：`GET /model-config`（key 只回打码值；
    每行给 `source=db|default`）、`PUT`（**只写给了的字段**；空串＝清掉）、
    `DELETE /model-config/{agent}`（恢复出厂默认）、`POST /model-config/probe`
    （一次验连通性 + 给模型清单 + 读窗口；失败是**结果**不是 500）。
  - **界面**（`frontend/src/SettingsModal.vue`）：左上角齿轮 → 模态：8 行（问答 / 分析 / 方案 /
    审查 / 编码 / 统一模型 / 兜底模型 / 意图识别）+ 每行「测试连接 / 恢复默认」+ 意图识别下拉
    （JEV / 大模型）+ `JEV 未配置 → 将用默认大模型做意图分类` 的提示。
  - **脱敏**：`Settings.model_secret_values()`（`env` 几家 + 界面配的行 + JEV/分类）接进两处
    `secret_terms` 调用点 —— 模型 key 以前不进词表，现在进了。
  - **意图识别三关**（`server/intent.py`）：`mode="jev"`（默认）＝ 口令 → JEV → 关键字口径
    （JEV 判不出来**不再**回退大模型；JEV 不可用才用大模型）；`mode="llm"` ＝ 口令 → 大模型 → 关键字。
    `force_llm`（`handoffTask` 的重判）固定走大模型 —— 重判要纠正的正是口令层。
- **关键决策**：
  - **「留空＝不改」**：一行都没配时逐字沿用今天的角色→供应商映射（qa→gpt、plan/coding→glm、
    review→kimi、coder→deepseek…），所以这是**可回退**的改动（DELETE 一行即回默认）。
  - **密钥明文存库 + 只回打码值**：与 `sessions` 表那两个 git token 同口径；界面永不回明文，
    写回留空＝保持原值；**并把它纳入脱敏词表**（这是「明文可接受」的前提）。
  - **窗口不给用户填是「先读」**：`/model-config/probe` 真去问厂商（实测 **deepseek 的
    `/models` 回了 1048576**，读得到），读不到才让用户填；解析兜底 128k。
  - **三关不四关**：用户口径如此。代价是「JEV 超时/认不出标签」时直接落关键字口径 ——
    已在 CHANGELOG 此处记明，要四关是 `detect_intent` 里加一跳的事。
- **验证**：
  - 全量 `pytest` **1205 passed**（新增 `tests/test_model_config.py` 26 条：解析优先级
    「unified → 该行 → 出厂默认」、子智能体跟 `coding`、窗口三档、兜底行与同主模型去重、
    分类模型顺序、脱敏词表、启动加载（含表坏了只告警）、接口打码 / 留空不改 / 清空 key /
    非法键 400 / 删除回默认 / probe 三条路径；另有 4 条既有用例按三关口径改写）；
    `ruff` 干净；`npm --prefix frontend run build`（含 `vue-tsc`）通过。
  - **真库端到端**（临时写、跑完删）：`GET` 八行全 `default` + `JEV 可用=True`；写一行 qa 后
    回读 `api_key_masked=sk-…7890`（无明文）、`source=db`；`apply_model_endpoints` 后
    `_config_for(qa)` 取到 `probe-model / 窗口 4096`、`plan` 仍 `glm-5.3`；**probe 真出网
    `ok=True 窗口=1048576`**；`DELETE` 后回 `default`，表里清空。
- **遗留**：
  - **要重启后端才生效**（保存后界面会提示）。
  - **模型热切换**：只立项（`docs/backlog.md` 1.1）—— 已建的智能体按 `(会话, 形态)` 缓存，
    改配置要重启或重建实例。
  - 界面只做了「配置 + 测试连接」；**没有做「保存后自动重建智能体」**（那是热切换那一档）。
  - 意图识别选 JEV 且 JEV 运行时失败 → 直接落关键字口径（三关的代价，见上）。

#### 立项清单落地：新增 `docs/backlog.md`（压缩口径 / 模型配置化 / 更早两条遗留）

- **动机**（用户口径）：压缩的计数口径「立项」；模型配置化先出方案、热切换只立项。
- **改动**：
  - 新增 `docs/backlog.md`：放「**已经想清楚、但还没动手**」的事，每条写清「要解决什么 /
    为什么当时不做 / 动手的抓手在哪」；`AGENTS.md` 的「去哪儿看」加一行指向它
    （「这次改了什么」仍看 CHANGELOG；「想清楚但没动手」看 backlog）。
  - 入册的第一批：
    1. **模型配置化**（**按智能体为键**：5 个主控形态 qa / analysis / plan / review / coding
       各一行「base_url / model / api_key / 窗口」，另可指定一个**统一模型**给所有智能体共用；
       界面上填、本地场景存业务库）—— 附待拍决策（密钥怎么存、与 `.env` 的优先级、保存后
       什么时候生效、子智能体与兜底归谁）；
       口径已定（2026-10-13）：行名用**中文**（问答 / 分析 / 方案 / 审查 / 编码 + 统一模型 /
       兜底模型 / 意图分类 / JEV），**所有行默认 deepseek v4.1 flash**，**兜底与意图分类各占
       一行**，**JEV 那套不动**（不填就提示「用默认大模型做意图分类」）。
        **1.1 模型热切换**（不做，只立项）与「保存后对新建会话生效」那一档留待评估。
    2. **上下文压缩的计数口径**：上游 `SummarizationMiddleware` 用 4 字符/token 估中文，
       低估约 3 倍（实测那几轮到 86% 也没触发压缩）；抓手是给它传我们自己的 `token_counter`
       （与 `context_trim.py` 同源），风险面与剪裁不同，故单列。
    3. 更早的两条：前端登录入口未接、沙箱测试数据配置外置（方向＝远端配置中心，
       「平台怎么拿到 Nacos 凭据」未定）、以及 `/conversation_history/` 导出未脱敏。
- **遗留**：模型配置化的**方案**见本轮对话（未动手），等用户拍板。

#### glm-5.3 的输入窗口按 1M 校正（原来是保守估的 128k）

- **动机**：用户确认 **glm-5.3 的输入窗口是 1M** —— 此前配置里按 128k 保守取值（那时的理由：
  glm / gpt / qwen 三家接口都不暴露窗口，只能往小里猜）。
- **改动**：`config.py` 的默认 `GLM_CONTEXT_WINDOW` `128000 → 1048576`（注释里记「glm 是例外：
  用户确认过的真值；gpt / qwen 仍是保守估值」）；`.env` 与 `.env.example` 同步。
- **哪条口径跟着变、哪条不变**（重要）：
  - **跟着变**：`ModelProfile.max_input_tokens`（上游压缩按它算 85% 触发）→ 压缩线从约 109k
    挪到约 **892k**；压力条的两条线（60% / 80%）→ 从 77k / 102k 挪到 **600k / 800k**。
    实测那一轮的输入峰值是 110k，只占 1M 的 **11%** ⇒ **压力条基本不会再亮**了 ——
    这是「离窗口上限还有多远」的诚实反映；要看「这一轮花了多少」是 Token 面板的事。
  - **不受影响**：刚上线的**剪裁阈值是绝对值 40k**（这正是当初不用窗口比例的理由：coder 是
    1M 窗口的 deepseek，按比例算等于永不触发）。
- **验证**：全量 `pytest` **1178 passed**、`ruff` 干净。其中一条测试原本把「86% 的输入」写成
  110,000（隐含 128k 窗口），改成按 `int(window * 0.86)` 算 —— 以后再调窗口它不会跟着红。
- **遗留**：**要重启后端才生效**。另外压缩线的**计数口径**仍对中文低估约 3 倍（上游用 4 字符/
  token），也就是说 892k 那条线实际上还要再打三折才可能触发 —— 与上一批的遗留同一条，单独立项。

#### 历史工具结果剪裁上线：按阈值把较早的工具返回换成一行占位符（先只给 coder）

- **动机**（用户口径）：按方案实施 —— 「大工具结果做中间件剪裁、省 token」。
- **实测依据（方案里的那张表，这里复述结论）**：coder 的上下文里 **46%~55% 是历史工具结果**
  （末次调用的输入 110,441 tokens 里约 60,500 是累积的工具返回），而同一份文件内容会跟着之后
  **每一次**调用重发（一轮 79 次调用＝重发 79 遍）—— coder 一轮 5.4M 输入 token 主要来自这里。
- **改动**：
  - 新增 `agent/middleware/context_trim.py`：薄包装上游 `ContextEditingMiddleware` +
    `ClearToolUsesEdit`（Anthropic 的 clear_tool_uses），**中文口径的 token 计数器**、
    排除小工具、中文占位符。开关关掉就返回 `None`（不挂）。
  - 挂载：`agent/graph.py`（主控，按配置）与 `agent/subagents.py`（子智能体，按角色），
    都放在**最内侧**（兜底之前）—— 边界 / 脱敏 / 历史修复先跑完，剪裁只动「要给模型看的那一份」。
    装配在 `session/manager.py`（每个角色现给一个实例）。
  - 配置（`.env.example` 已写清依据）：`CONTEXT_TRIM_TOOL_RESULTS=true`、
    `CONTEXT_TRIM_TRIGGER_TOKENS=40000`、`CONTEXT_TRIM_KEEP=6`、
    `CONTEXT_TRIM_CLEAR_INPUTS=false`、`CONTEXT_TRIM_ROLES=coder`。
- **关键决策**：
  - **只改这一次请求**：上游在 `awrap_model_call` 里 `deepcopy` 出要发的消息、只换被剪那几条的
    **内容**，再 `request.override(...)` ⇒ 不动 checkpoint、不动业务库、消息条数与 `tool_call_id`
    配对都不变（不会造出孤儿 tool_call）。所以回退＝关一个开关，**数据零风险**。
  - **阈值用绝对值（40k）而不是窗口比例**：coder 是 1M 窗口的 deepseek，按比例算等于永不触发
    （实测它的输入峰值也就 110k）。
  - **必须自带 token 计数器**：上游默认 `chars_per_token=4.0` 是英文口径，中文实测 ≈1.6
    （GLM 上量到 0.45~0.68 token/字）—— 照默认值算会把中文低估约 3 倍，阈值永远不到。
  - **第一版不剪工具入参**（`CONTEXT_TRIM_CLEAR_INPUTS=false`）：剪入参收益更大，但历史 AI 消息的
    args 会变空，万一将来有重放 / 重试路径读 args 会踩空。观察一轮再定。
  - **排除 `write_todos` / `updatePlanStep` / `updatePlanStatus` / `planExists`**：都极小，剪了没收益，
    而 `write_todos` 是给模型看的进度骨架。
  - **先只给 coder**（`CONTEXT_TRIM_ROLES=coder`）：收益集中，主控那侧只占 7%~14% —— 先在一处观察，
    出问题好定位；要放开就改这一格配置，不动代码。
- **验证**：
  - 全量 `pytest` **1178 passed**（新增 `tests/test_context_trim.py` 10 条：阈值之下一条都不动；
    超过阈值时「最近 N 条原样 + 更老的变占位符」；**消息条数与 `tool_call_id` 配对不变**；
    被排除的工具不动；默认不剪入参；中文计数器比上游默认估得多 2 倍以上；
    开关关掉返回 `None`；参数来自配置；角色表解析；**挂载按角色**（coder 有、planner 没有））。
    `ruff` 干净。
  - **真配置自检**：`.env` 读出来是 `true / 40000 / 6 / false / coder`，中间件构造为
    `ContextEditingMiddleware` + `ClearToolUsesEdit`，排除列表与占位符都在。
- **遗留**：
  - **要重启后端才生效**；重启后跑一轮 coding，对比 coder 的**输入 token 合计**（上一轮 5.4M）、
    末次输入（110k）与「重复 `read_file` 的比例」——预期合计降 40%~60%、末次输入 ≤ 60k。
    若模型明显开始重复读文件，就把 `CONTEXT_TRIM_KEEP` 加到 10 或把阈值提到 60k。
  - **上游压缩线的计数口径还没校准**（同一个坑）：`SummarizationMiddleware` 也用
    `count_tokens_approximately`（4 字符/token），中文被低估约 3 倍 —— 实测那几轮输入到
    110k/128k（86%）却一次压缩都没触发。影响的是「什么时候压缩」，与这一刀不同，建议单独立项。
  - 用户把 GLM 从 `glm-5.3-flash` 换成了 **`glm-5.3`**：`GLM_CONTEXT_WINDOW` 仍是保守值 128000。
    这一刀的阈值是绝对值、不受影响；但**压力条与压缩线**按这个窗口算 —— 若 glm-5.3 的窗口更大，
    要把 `GLM_CONTEXT_WINDOW` 改准。

#### 新工具 `planExists`：按 id 判「这份方案还能不能实施」，只回一个布尔、不拉正文

- **动机**（用户口径）：加一个按 id 查任务是否存在的工具，**只返回有效状态（bool）** ——
  plan 是大对象（一份一万字），不必每次都把正文拖回来；判完存在性就可以直接派 coder。
- **改动**：
  - `db/dao.py::plan_status()`：**只 `SELECT status`**（少选一列就少传一万字），没这条记录返回
    ``None``。
  - `tools/plan.py::planExists(plan_id) -> bool`（放进 `build_plan_tools` 那口池子）：
    **存在且状态是 pending → `True`**；不存在 / 不属于本会话 / 已软删（`closed`）/ 已执行完
    （`done`）→ `False`。docstring 写明语义，并指明「正文让 coder 自己
    `get_unexecuted_plan` 取」。
  - `tools/register.py`：`_PLAN_TOOL_NAMES` 收进 `planExists`；**coding 主控**与 **plan 主控**
    都拿到它（前者用于「派 coder 之前确认还能实施」，后者用于「只要判存在性就别读全文」）。
    planner / coder 两个子智能体不给：前者只写新方案，后者本来就要取全文。
  - 提示词三处：coding 指令第 1 步与编码流程 c 各补一句「**已知 `plan_id` 时先用
    `planExists(plan_id)` 判定**（只回布尔），`true` 就直接派 coder、正文让 coder 自己取；
    这一步是你自己一条工具调用的事，别为此派子智能体」；plan 形态的「查」那一节指到它。
- **关键决策**：
  - **语义定成「还能不能拿去实施」（`pending`）**，而不是「记录在不在」：主控问这句话的目的
    就是「能不能派 coder」，`done` / `closed` 都该是 `False`。名字仍按用户口径叫 `exists`，
    三种 `False` 在 docstring 里写清。
  - **只 SELECT 状态列**：判存在性不该把正文拖回来 —— 这正是提这条的初衷。
- **验证**：
  - 全量 `pytest` **1168 passed**（新增 5 条：`pending`→`True`；`closed` / `done` / 不存在→
    `False`；并且断言它**不会**调 `get_plan`（即不拉正文）；注册表两条：两个形态都拿到、
    别的形态不误拿）。`ruff` 干净、`scripts/check_prompts.py` 冲突 0 条。
  - **真库回放**：`39/40/44`（done）、`41/42/43`（closed）、`9999`（不存在）全部 `False`；
    本会话与全库当前都没有 pending 的方案（`True` 那条由单测覆盖）。顺带确认：**plan 44
    已经跑完并被标成 `done`** —— 那一轮实施完成了。
- **遗留**：**要重启后端才生效**。

#### write_todos 子树挂错：父项改成「按角色找那一步」，不再看「此刻谁 in_progress」

- **动机**（用户口径）：树稳定挂错，要处理。
- **现象（真机 2026-10-13，trace `99f2f0a268d7`）**：主控的清单是
  `[in_progress] 确认 plan_id=44 处于待执行状态` / `[pending] 派 coder 按方案实施（写码/测试/提 PR）`
  / `[pending] 派 reviewer 审查 PR` …，而 **coder 的六步子树整棵挂到了「确认…」底下**。
- **根因**：父项取的是「委派那一刻 `in_progress` 的那一项」，而**主控推进清单是滞后的** ——
  它习惯在子智能体返回之后才更新清单。五次实测里三次因此挂错（派 planner 时 in_progress 还停在
  「查询是否存在未执行的方案」；派 coder 时还停在「确认 plan_id=44 处于待执行状态」）。上一批
  记过这条、当时选择先不动；现在改成由**角色**定。
- **改动**（`stream.py`）：`note_delegation` 的父项改由 `_delegation_parent(role)` 决定 ——
  1. 在清单里找**第一条未完成**、**不是前置动作开头**（`确认`/`查询`/`查看`/`检查`/`核对`/`获取`/
     `准备`/`拉取`/`读取`/`了解`/`弄清`）、且**含该角色词**的项
     （planner: 方案/计划/设计/规划；coder: 实施/编码/写码/写代码/开发/落地/提 pr/提交 pr/改代码；
     reviewer: 审查/评审/复核）；
  2. 找不到就**回落到老规则**（`in_progress` 那项 → 最后一个未完成项 → `None`）—— 最差也只是
     回到现状，不会让子清单整棵消失。

  角色是委派那一刻就确定的事实，比主控的清单推进可靠；而且这是**确定性规则**，不依赖模型把清单写对。
- **关键决策**：
  - **不做「延迟锚定」**（等主控下一次清单更新再决定父项）：子树是**实时**下发的，事后再挪就变成
    用户以前抱怨过的「跑完就漂移」。
  - 关键词表**只服务于这一件事**（选父项）且带兜底；一主三从的流程是固定的，换角色才需要改这张表。
  - 前置动词只**跳过**明显以「确认 / 查询 / 获取」开头的项，不做更聪明的语义猜测。
- **验证**：
  - 全量 `pytest` **1163 passed**（新增 4 条：coder / planner 各一条「前置项还停在 in_progress 时
    也要挂到那一步」、没有角色词时回落老规则、已完成项不再收子树）；`ruff` 干净。
  - **真数据回放**：拿这一轮真实的清单 + coder 六步跑一遍，子树从「确认 plan_id=44 处于待执行状态」
    挪到了「派 coder 按方案实施（写码/测试/提 PR）」下，前置项保持无子树。
- **遗留**：**要重启后端才生效**（`stream.py` 在后端进程里）；**已经在跑的那一轮不会变**（它的树
  已经发下去了）。

#### 意图判据里 `plan` 一档改成「方案对象的增删改查都归它」（真 JEV 六例验证）

- **动机**（用户口径）：JEV 里针对 planner 那一档的判定要写详细 —— **方案的增删改查都要判给
  这一档**。
- **改动**（`server/intent.py`，JEV 的 `criteria` 与「大模型判定」那一档共用同一份说明）：
  - `_INTENT_SPECS` 的 `plan` 从「要方案 / 计划 / 设计 / 步骤（还没动手做）」扩成四件事：
    **出方案**（要计划 / 设计 / 步骤）、**查方案**（最新的是哪一份、某份的正文与步骤）、
    **改方案**（改正文 / 标题 / 步骤、修掉乱码或过时内容）、**删方案**（删掉 / 作废某一份），
    各带一句「常说的样子」，并写明**只管方案文本本身**（要动代码就是 coding）。
  - `_BOUNDARY` 加一条分界：plan 管方案对象本身；**查仓库文件 / PR / 提交归 qa**；
    **按方案改代码 / 提 PR 归 coding** —— 方案文本的增删改只有 plan 这一档有工具。
  - 为什么是这一档：只有 plan 形态的主控手里有 `getPlan` / `updatePlan` / `updatePlanSteps` /
    `deletePlan`；coding 形态对方案只读（`get_unexecuted_plan`），让它去改方案只能让 planner
    另存一份（实测就是这么造出 plan 42 / 43 两份「修复版」的）。
  - **没动关键字层**：强关键字命中的那一路会**跳过模型判定**，而「删掉那份方案」这类自然说法
    本来就匹配不到固定串（要写成 `删掉方案` 才行）—— 交给 JEV 更稳，也就不加固定串去抢。
- **验证**：
  - 全量 `pytest` **1159 passed**（新增 2 条：判据里四件事齐全 + 分界那句话在；
    `ruff` 干净）。
  - **真 JEV 六例**（直接调判定器，只读）：
    ```
    出方案      → plan    p=1.00      对照·coding → coding  p=1.00
    查方案      → plan    p=1.00      对照·qa     → qa      p=1.00
    改方案      → plan    p=1.00
    删方案      → plan    p=1.00
    ```
    四件事全部落到 plan，两条对照没有被 plan 抢走。
- **遗留**：**要重启后端才生效**（判定器在建会话时读配置与提示词）。

#### plan 41 为什么坏：正文没人校验（a）+ 正文限长（b）+ 失锁后不再"无主"跑（c）

- **动机**：先查清 plan 41 为什么会坏，再按用户口径把 a（正文自检）/ b（限长与精简）/
  c（运行权失锁）一起做掉。
- **定位（plan 41 的坏法，证据链）**：正文 9,521 字，**写到第 4 步（共 6 步）就断了** ——
  没有步骤 5/6、没有验收节；步骤 3 写了两遍（`### 步骤 3（更正稿）：` 空标题 +
  `### 歨骤 3 更正稿（以此为准）：…`，错字「歨」）；路径写成 `wanqi-male-user-service`；
  结尾是一句自我引用「…（更正稿见正文）」。
  写它的那次调用（21:01:40，`ae71fa67882b` 那轮的第二次 planner）：输入 54,748 /
  **输出 5,159 tokens**，**工具参数 11,499 字符全都收到**（我加的那行「本次产出」量的），
  `steps` 6 项齐全并通过校验、工具成功、无重试无兜底 ⇒ **不是存储截断、不是传输截断、
  不是输出上限**（同模型当晚还有 17,635 / 10,194 输出 token 的调用），是**模型自己写坏 +
  没有任何一层校验正文**。
- **a. 落库前的正文自检**（`tools/plan.py`）：
  - 新增 `MAX_CONTENT_CHARS = 6000` 与 `_check_content(content, steps)`，`storePlan` 与
    `updatePlan` 都走它（与 `_normalize_steps` 同一套做法：不合格返回可读理由、方案不写入，
    由模型重写）。判据全是确定性的：太短（<100 字）/ 带自我修补字样（「更正稿」「以此为准」
    「（更正）」）/ 结尾是半句（以「（」「、」「：」「…」收尾）/ **「步骤 N」小节没覆盖到
    `steps` 的最后一步** / 超长。
  - 顺序**先结构、后长度**：既超长又带修补片段时，先让它「重写一份干净的」（那也自然压下来），
    而不是只收到一句「压缩」。
  - 只认**标题行**里的「步骤 N」（正文里顺口提到不算），避免误拒。
- **b. 提示词限长与精简**（`_PLAN_METHOD` / `_PLAN_RULES`，planner 与 plan 形态共用）：
  正文 **4000 字以内**（硬上限 6000，超了工具拒）；写明**该写什么 / 不该写什么** ——
  读码过程、文件片段、逐行分析、背景复述不进正文（coder 自己会读码）；一次写完写干净、
  不追加更正片段；正文写到第几步就覆盖到 `steps` 的第几步。
- **c. 运行权失锁**（`server/runs.py`）：
  - 起因（真机）：20:43:50 起 `renew` 每 30 秒失败一次、一直失败到轮次结束 —— 钥匙在 Redis
    抖动时 TTL 到点消失，而 `renew` 只「查+续」、不会重建 ⇒ 那一轮**「无主」跑了 95 分钟**，
    UI 的订阅还先收尾了（界面显示成跑完了，其实还在跑）。
  - `_watch`：续期失败 → **先 `acquire` 重新拿回**（自己的钥匙过期时这一下就拿回来，记 INFO）；
    拿不回（真被别的 worker 持有）→ 取消本轮 + 清缓冲 + warning，不无主地继续写同一个会话。
  - `subscribe`：**本进程在跑的轮次不再拿 `held()` 二次判定**（本地 task 才是权威）；只有
    「不是本进程在跑」时才用运行权当存活信号 —— 否则锁一抖，正在跑的这轮就被自己的订阅者
    判成「已结束」。
- **验证**：
  - 全量 `pytest` **1157 passed**（新增 13 条：正文自检 7 条、运行权 3 条、提示词 3 条 ——
    含「提示词里的硬上限必须等于工具里的常量」这条耦合测试）；`ruff` 干净；
    `scripts/check_prompts.py` 渲染 8 份、**冲突 0 条**。
  - **真库回放**：plan 41（9519 字）被「自我修补字样」拦下、plan 42 / 43 被「超长」拦下 ——
    库里那三份坏正文现在都进不来。
- **遗留 / 风险**：
  - 6000 字是**硬拒**：模型要是反复写超长会被连着拒（提示词给了 4000 的目标值、拒绝理由也
    具体；真出现反复被拒就把上限放宽，或把超长改成只警告）。
  - **要重启后端才生效**。
  - 盘子里那三份坏方案（41 残缺 / 42 v2 / 43 v3）**已按用户要求清掉**：走 `deletePlan` 同一条
    dao 路径软删（状态改 `closed`，行留在库里，改回状态即可恢复）。清完之后
    `get_unexecuted_plan` 返回「没有未执行的方案」，`/sessions/{id}/plan` 回落到 plan 40（阿里云短信，
    done）。聊天里原有的「方案」链接不受影响 —— 每条 plan 记录自己带着正文，前端不按 id 回查。

#### 意图判定带上「助手上一轮的末尾」：用户回一句「修复吧」不再判错形态

- **动机**（用户口径）：按方案 A 做（**不做**纯规则的兜底 C）。
- **定位（实测 2026-10-12）**：助手上一轮结尾问「要不要我把方案正文修复一遍再给你确认？」，
  用户回「修复吧」→ 判定只看这一句 → 判成 `coding`（JEV p=0.96）→ 走了**编码形态**去「修方案
  文本」，而那个形态没有写方案的工具、planner 又只能新建（`storePlan`）→ 白折腾 25 分钟、
  多造出一份方案（41 残缺 + 42 修复版），树上因此多出一个 plan 子任务。
  - 代码事实：判定用的历史是 `dao.recent_user_summaries`（**只取 `type="user"`** 的最近 4 条），
    助手那侧一条都没有；而 assistant 记录的 `summary` 是 `content[:200]`，那句反问在 1,400 字
    回复的**末尾** —— 取 summary 也读不到（真库核对过）。
- **改动**：
  - `db/dao.py::latest_assistant_tail()`：最近一条 `type == "assistant"` 记录的**末尾**若干字
    （Python 切片，不用 PG 专属的 `right()`；一行最多几千字，取回再切划算）。
  - `server/intent.py`：`PREVIOUS_REPLY_CHARS=300` / `PREVIOUS_REPLY_SLACK=100` /
    `tail_excerpt()` —— 取末尾 300 字左右，**尽量从句末标点之后开始**（不留半句；
    一整段没有标点就不切，免得把要说的话也切没）。
  - `_user_prompt()` 多一格「助手上一轮回复（末尾节选）」（放在「本轮消息」之前）；
    `_system_prompt()` 加一条规则：短肯定 / 否定 / 指代句（「修复吧」「好的」）很可能是在
    **回答助手上一轮的那个问题** —— 按那个问题所指的动作归类，不要只看这一句的字面。
  - **两条判定路径一起吃到**：JEV 的 `decide(previous_reply=…)`（协议 + 实现）与大模型那一档
    共用同一份 `_user_prompt`，所以一处改动两处同时生效。
  - **重判（`handoffTask` 的回调）也带上**：回调契约仍是 `(message, history)` 两个参数，
    `chat()` 用 `functools.partial` 把 session 与「助手上一轮」先绑进去。
  - 与用户历史**同一个开关**：`INTENT_HISTORY_TURNS=0` 时不碰库（库连不上时那一跳会白等一个
    超时）；读失败只降级成「不带这段上下文」，不打断本轮。
- **关键决策**：
  - **不「先改写再判」**（用户提的另一条路）：改写做的事就是把上下文揉进一句话，而现在直接把
    上下文交给判定器 —— 省一次模型调用与 1~2 秒延迟，也少一处会漂的措辞。
  - **取尾部而不是 `summary`**：那句反问在末尾，`summary` 只有前 200 字，正好切掉它。
  - **不加规则兜底（C）**：按用户口径不做。
  - 成本：判定输入 +~200 token（实测那次判定是 1,358 token 输入）。
- **验证**：
  - 全量 `pytest` **1143 passed**（新增 14 条：节选取尾 / 按句切 / 无标点不切、prompt 里有它
    且在「本轮消息」之前、没有就不加那一格、system prompt 含新规则、JEV 与大模型两条路都拿到、
    service 只读一次且参数正确、窗口 0 不碰库、读失败降级、重判回调仍是两个参数）；`ruff` 干净。
  - **真库自检**：那次误判的回复（536 字）现在会被摘成一段尾部节选，**结尾正是
    「要不要我把方案正文修复一遍再给你确认？」** —— 判定终于看得到指代对象。
- **遗留**：
  - **要重启后端才生效**。
  - 只解决「指代句」这一类误判；**plan 41 的正文为什么会被写坏**（长输出的自我修补 + 尾部截断）
    是另一件事，仍未处理。

#### 方案正文不再被抄来抄去：planner 只回报摘要、主控不把方案贴进 coder 的任务

- **动机**（用户口径）：先做「减少上下文 token」清单里的前两条。
- **定位（实测那一轮 `a9379a36ddec`，阿里云短信）**：同一份方案在上下文里**出现 4 次、留下
  2 份副本** —— planner 生成（`storePlan`）→ planner 回报主控（**6,270 字**）→ 主控抄进 coder
  的任务描述 → coder 又 `get_unexecuted_plan` 取回库里那份（**10,915 字**，与上面那份还不是
  同一版本）。之后**每次都重发**：主控 8 次、coder **79 次**、planner 1~2 次。
  两处 token 跳变独立吻合（≈0.68 token/字）：主控 `57,832 → 62,072`（+4,240 / 6,270 字）、
  coder `14,819 → 22,211`（+7,392 / 10,915 字）→ **光这一项 ≈96 万 input tokens**
  （占全轮 6.97M 的 13.8%），还埋着「两份版本谁为准」的隐患。
- **改动**（都在 `agent/prompts.py`，不动工具与代码）：
  1. `_PLAN_METHOD` 新增第 6 条「**回报 / 答复只写摘要，不抄方案全文**」：子智能体形态回报
     `plan_id` + 3~8 行摘要（做什么 / 关键决策 / 风险与不确定项 / 待确认点）；plan 形态同样只给
     摘要 —— 全文由「方案」链接承载。
  2. 主控编排段（编码流程 c）与 coding 意图指令第 3 步：派 coder 的任务描述**只给 `plan_id` 与
     目标 / 边界，不要抄方案全文**（coder 自己 `get_unexecuted_plan` 取，那是唯一权威的那份）。
  3. 「调度原则」加一条通用约束：**不搬运长文**（方案全文、清单原文、大段日志、文件内容），
     只给引用（`plan_id` / PR 编号 / 路径 / 命令）。
  4. coder 取计划那一步补一句：**方案正文只有这一个来源**，任务描述里不会带全文，别因此停下来反问。
  5. `_ROLE_PLAN_MAIN`：把「把方案呈给用户」明确成「把**摘要**呈给用户（全文在库 / 链接里）」。
- **关键决策**：
  - **plan 形态也给摘要**（这是一处行为变化）：用户在聊天里看到的是摘要 + 方案链接，而不是上万字
    的方案墙；全文仍可点开 —— 与「方案改成聊天里的一条链接」的既有口径一致。要恢复成全文照旧的话，
    只回退这一处即可（planner 与主控那两条可以留着）。
  - **只改提示词、不动工具契约**：coder 本来就该调 `get_unexecuted_plan`（它的工作流第 2 步写着），
    所以这一刀不引入新的失败模式，也不改任何写路径。
- **验证**：
  - `tests/test_prompts.py` **116 passed**（新增 4 条钉住这几句，防止后续编辑悄悄回退）；
  - `scripts/check_prompts.py`：**渲染 8 份成品提示词、冲突扫描 0 条**（AGENTS.md 的硬要求）；
  - 全量 `pytest` **1129 passed**；`ruff` 干净。
- **遗留**：
  - **要重启后端才生效**（提示词在建会话时读取）。
  - **收益待下一轮真机量化**：看 coder 第一次调用的输入是否少了那 ~4.2k、以及 `get_unexecuted_plan`
    的结果是否成了唯一那份方案。
  - 清单（`write_todos`，每次整份重发，几百 token）与「老工具结果出上下文」没做 —— 后者是最大
    的一刀（coder 5.4M / 79 次），要单独立项定「保留规则」。

#### Redis 命令加一层兜底超时：`socket_timeout` 管不到写阻塞（真机实测一条命令等了 556 秒）

- **动机**（真机观察到的）：隧道被重建的那一刻，降级日志里出现
  `运行缓冲 不可用（这条命令花了 **556243 ms** 才失败）` —— 配置的 `socket_timeout` 是 5 秒，
  它没兜住这一条。
- **定位**：`socket_timeout` 只管**读**。隧道半死时**写**会一直阻塞（asyncio 的 drain 要等
  TCP 自己放弃，默认十几分钟量级），于是那一条命令的结果能拖到九分钟才回来。它不影响事件
  循环（别的东西照跑），但没有任何一层在管「一条命令最多等多久」。
- **改动**：
  - `redis_cache.py` 新增 `COMMAND_GUARD_FACTOR = 3` 与 `command_guard_seconds(settings)`；
    `RedisBacked.run()` 用 `asyncio.wait_for` 把**整条命令**兜住，超时按失败处理 → 走进程内
    与冷却纪律（`_degraded_until`），不新增一条出口。
  - 三个实现（熔断 / 运行缓冲 / 运行权）与装配处（`api/app.py`）透传 `guard_seconds`，
    与上一批 `cooldown_seconds` 同一套走法。
- **关键决策**：
  - **兜底跟着配置的 socket 超时走（×3），不写死常数**：把超时放大到 30 秒的部署（远端 /
    慢隧道），不该被一个 15 秒的兜底先掐掉 —— 写死就与配置的意图相反。再乘 3 是因为一条
    命令可能是「取号 + 入列 + 过期」这种几次往返。
  - `REDIS_TIMEOUT_SECONDS=0`（redis-py 语义＝不超时）时兜底也是 0 ＝ 不兜底，与配置一致。
  - 兜底只是**上限**，不改降级纪律：超时那次照样记「花了多少毫秒才失败」并进冷却。
- **验证**：全量 `pytest` **1125 passed**（新增 3 条：挂住的命令被掐掉且日志里记的是
  `TimeoutError`、正常的慢命令（隧道量级）不被掐、兜底值跟随配置）；`ruff` 干净。
- **遗留**：**要重启后端才生效**。

#### 「本次产出」第一版有两处字段名是猜的：真机一轮就露馅（含「那批大输出是思考」的结论）

- **动机**：用户重启后跑了一轮新的 coding 任务，让我观察上一批那行诊断。第一个数据点就
  自相矛盾：planner 那次调用**输出 3,936 tokens**，这行却写「可见正文 0 字 / 思考 0 字 /
  工具参数 185 字」。
- **两处都错在「照自己的想当然写字段名」，而不是读上游契约**：
  1. **思考那一格读错了键**。v3 的 `reasoning-delta` 把文本放在 **`reasoning`** 字段里，
     正文才是 `text-delta` + `text`（见 `langchain_core.language_models._compat_bridge
     ._to_content_delta`）。我照正文的字段名去读思考 → **永远是 0 字**。
     **测试里的假事件也写错了同一个字段名**（`tests/v3_events.py::reasoning` 用的是 `text`），
     于是代码与测试一起错、跑多少个用例都是绿的 —— 这正是「假事件照着自己想当然写」的代价。
  2. **用量里思考 token 的两个键都不对**。v3 事件里那份用量是 LangChain 归一后的
     `UsageMetadata`：思考在 `output_token_details` 里、键就叫 `reasoning`（按服务档位加
     前缀，如 `priority_reasoning`，见 `langchain_openai.chat_models.base
     ._create_usage_metadata`）。我查的是 `completion_tokens_details` /
     `output_tokens_details`（那是 OpenAI 原始形状与 Responses 形状）→ 这一格也一直不出。
  3. 顺手修一个少算：**工具参数原来跨 block 取最大值**，而一次消息里可以并行多个工具调用
     （本轮实测三个并行 `read_file`/`grep`）→ 改成**逐 block 求和**（每个 block 自己仍取
     累加值的最大值）。
- **结论（用户问的那件事）**：正文那一格是**读对了**的，真机读到 0 字；而桥接保证
  text/reasoning 块**一定**发一条 delta（只有 tool_call/image 这类已定型的块才只发 finish）
  ⇒ **那次调用根本没产出正文块** ⇒ 3,936 − 465（工具参数）≈ 3,400 tokens 只能落在**思考**
  上。上一轮那 4.8 万输出 tokens 同理：**主要是模型的思考 token，不是被丢掉的旁白草稿**。
  所以改法方向是「管住思考」（关/降 GLM 的思考、给 planner 换模型、或收短探索预算），
  而不是「约束子智能体别写草稿」。
- **改动**：`observability/run_log.py` 的 `_note_delta`（读 `reasoning` 键）、
  `reasoning_tokens_of`（改认 `output_token_details.reasoning`，并保留两种原始形状作兜底）、
  `_take_output_detail`（工具参数逐 block 求和）；`tests/v3_events.py::reasoning` 改成真形状
  并把「**不要改回 `text`**」写进它的说明。
- **验证**：全量 `pytest` **1122 passed**（新增 4 条：字段名与夹具形状、服务档位前缀、
  OpenAI 原始形状、并行工具调用求和）；`ruff` 干净。
- **遗留**：**还要再重启一次**这版才生效（这一轮跑完再重启即可），之后「思考 N 字」与
  「网关记的思考 token N」才会有数。

#### 诊断「一轮 64 分钟」：给大调用补一行「产出明细」（可见正文 / 思考 / 工具参数各多少字）

- **动机**（用户口径）：上一批分析出那一轮 64 分钟里 planner 的 4 次调用吐了 **4.8 万输出
  tokens、占 20 分钟**，但**分不清**那 4.8 万是什么 —— 是模型的**思考 token**、是子智能体
  **被前端丢掉的旁白**，还是**工具参数**？三种可能对应三种完全不同的改法（关思考 / 改提示词
  约束草稿 / 换模型）。所以先补证据、再动手（用户拍：「先做 1」）。
- **改动**（`observability/run_log.py`，都在同一份事件流上，不碰 SSE 契约）：
  - `on_message` 现在也认 `content-block-delta`：`text-delta` 与 `reasoning-delta` 都是
    **增量**，各按 `namespace` 分桶累计字数；`block-delta` 的 `args` 是**累加值**（bridge
    已按 block 下标把 `tool_call_chunks` 拼好 —— `stream.py` 同一处注释也是这么说的），
    所以取**最大值**而不是求和，否则同一份 JSON 会被数好几遍。
  - `message-finish` 时**无条件取走并清零**（小调用也要清）：不清的话，一串小调用的增量会
    攒到后面某个大调用头上，那一行就成了假账。
  - **输出 ≥ 2000 tokens** 的调用多记一行
    `本次产出：可见正文 N 字 / 思考 M 字 / 工具参数 K 字`。门槛是模块常量
    `DETAIL_OUTPUT_TOKENS`（正常调用只有几百 token，不该被这行淹掉；想每次都看就设 0）。
  - 网关若单列了思考 token（`completion_tokens_details.reasoning_tokens`，也认
    `output_tokens_details`）就一并写出来 —— 那是比字数更硬的证据。**没报就不写**
    （「没报」与「报了 0」不是一件事）。
  - 真的一个增量都没收到时，如实写 `没有收到任何输出增量`，而不是假装 0 字。
  - `_accumulate` 改收调用方解好的 `(输入, 输出)`：同一条事件既记账又判明细，解两遍容易
    两处各认一半。
- **关键决策**：
  - **门槛式，不是全量 dump**：这行是一个长期可用的诊断（「这次调用为什么慢」），不是一次
    性的调试输出，所以默认只在大调用上出现。
  - **分三类数 delta，而不是只看 usage**：在 usage 里「思考」与「旁白」长得一模一样（都算
    output tokens），只有 delta 的**类型**分得开 —— 这正是这次要回答的问题。
  - 不改日志既有那行的格式：`LLM 调用完成：输入 … / 输出 … tokens` 有测试逐字钉着，明细
    单独一行，两者互不干扰。
- **验证**：
  - 全量 `pytest` **1118 passed**（新增 7 条）；`ruff check` 本次改动文件干净。
  - 新用例：正文与思考分开计、小调用不记这行、计数不串到下一次调用、子智能体那次归到
    **它自己**名下（planner）、网关报了思考 token 才写那一格、工具参数按累加值只算一次。
  - 事件形状照 `tests/v3_events.py`（照真机探针实测造的）—— 与 `stream.py` 读同一批字段
    （`payload["index"]` / `delta["fields"]["args"]`），口径只有一处。
- **遗留**：
  - **真机确认要等下一次运行**：这行只在真实一轮里才有数据。重启后端跑一轮，
    `grep 本次产出 logs/agent.log`（或直接看右栏「日志」面板）就能看到那 4 次大调用的里子，
    再按结论决定改哪一处。
  - 本轮没动任何影响行为的东西：不提速、不换模型、不改提示词。

#### Token 面板复核：主控的**真名**没被认出来，上下文压力整块是死的（+ 三处小账）

- **动机**（用户口径）：「token 面板已经做了，你看一下」—— 复核时在**真机**上发现
  「上下文压力」那一整块静默失效，面板里第一行还挂着一个英文名。
- **定位（三条真机证据，不是读代码猜的）**：
  1. 真库 + 真接口：`GET /sessions/cb4c905c…/usage` 回
     `context.max_input = 0 / ratio = 0.0`，`actors[0].key = "ai-coding-harness"` ——
     而同一轮的 `logs/agent.log` 明明写着「LLM 调用完成：输入 26432」。
  2. `agent/graph.py` 建主智能体时传了 `name="ai-coding-harness"`，于是**主控那一路模型
     调用的 `lc_agent_name` 就是它**（当日日志里 3094 行 `[ai-coding-harness]`；只有
     `service.py` 手写的那几行是 `[main]`）。
  3. 而 `run_log._account_key` 只把**占位名** `MAIN_AGENT`（`"main"`）归成 `main`，
     `_main_peak_input` 又只在 key == `main` 时累加 → 真机上峰值恒 0。对照实验：
     同一条事件喂 `agent="None"` → 归 `main`、峰值 26432；喂 `agent="ai-coding-harness"`
     → 归到英文名、**峰值 0**。
  - **为什么测试没抓到**：测试助手的默认署名就是占位串 `"None"`，而代码正是照那个假设写的；
    `run_log` 文档里那句「主智能体是字符串 `"None"`」在设了 `name=` 之后就不成立了。
- **改动**：
  - **主智能体的真名只有一处定义**：`observability/logging.py` 新增 `MAIN_AGENT_NAME`，
    `graph.py` 的 `name=` 改用它；`run_log` 判「是不是主控」用
    `{MAIN_AGENT, MAIN_AGENT_NAME}`（归账与峰值共用这一处），并修掉两句过期注释。
  - **旧账按同一规则归一次**（读路径）：新增 `run_log.account_key()`，写账与读账
    （会话级聚合、单条记录的 `usage`）共用 —— 本功能刚上线的那几轮把主控记在真名下，
    不归的话面板与消息角标里会冒出一个英文名。
  - **`totals.calls` 补上**：轮账与聚合都给了这一格（聚合以前恒为 0，而 `by_actor` 里明明有）。
  - **「意图分类」也算进总数**：以前它只进 `by_actor`，于是 `totals` 小于各角色之和 ——
    界面上的「合计」与下面那些行自相矛盾。
  - **压缩之后不再虚高**：新增 `_main_last_input`（本轮主控**末值**），`_turn_context` 在
    「这一轮压过」时改用末值 —— 峰值是压缩前那一次，用它会让提示永久停在「接近压缩线」，
    而历史其实已经掉到 10%。
  - **JEV 那一关的账有人接了**：`decide(usage=…)` 由 `detect_intent` 把自己的累加器传下去；
    删掉 `JevDecider` 上那份没人读、还从不 reset 的 `self.usage`（将来谁接上就会把历史几轮
    一起算进当轮），顺手修掉 `usage` 变量被后面日志那几行覆盖的写法。
  - 文档：README 的 API 清单补上 `/memory`、`/pulls`、`/logs`、`/logs/download`、`/usage`
    （前四个是上一批就漏的）。
- **关键决策**：
  - 主控名字**只在归类处**归一（推荐 a）：日志行仍署真名（它是有信息量的），账与压力用归类名。
  - **读路径也归一次**，不是只修写路径：库里已经躺着的那几轮正是这次复核的对象，界面上
    不能两种名字并存。规则只有 `account_key()` 一处，不算多一份口径。
  - 压缩口径按推荐 b（末值）：**实时那条压力条取最近一轮的账**，所以压完会跟着降下来；
    面板里那一段仍取会话内**最紧**的一档 —— 它自己写着「峰值输入」，两个标签口径不同，
    已经在各自的说明里写清。
  - **不算钱**、**缺数据不当 0** 这些原有的口径没动。
- **验证**：
  - 全量 `pytest` **1111 passed**（本次净增 5 条：真名归类、压缩后回落、旧账合并、
    JEV 累加器、接口合计调用数）；`ruff check` 本次改动文件干净。
  - **真机（真库 + 真路由，只读）**：聚合回 `actors[0].key = "main"`、`totals.calls = 1`；
    单条记录的 `by_actor` 也归成 `main`；对照探针里两种署名现在都归 `main` 且峰值 26432
    （修前真名那份是 0）。
  - `npm --prefix frontend run build`（`vue-tsc -b && vite build`）**通过**。
  - 顺带把上一批记错的两条遗留结掉：`npm run build` 现在跑得通；pytest 那 60 条
    `%TEMP%` setup 错误也消失了（全量 1111 条 0 error，进度表已从 1046 改成 1111）。
- **遗留**：
  - **要重启后端才生效**（8000 上跑的还是旧进程）。重启后跑一轮：主控那行会显示「主控」，
    输入框上方的压力条会出现。
  - 旧那一轮（`cb4c905c` 的 17:15 qa）**存下来的** `context.max_input` 与 `totals.calls`
    都是 0（旧代码写的），回填不出来；新轮次正常。消息角标对老记录按 `by_actor` 自己数调用数，
    所以那一轮显示 1 次调用，不是 0。
  - 会话 `cb4c905c` 仍 `running=True`：10-12 17:16 那条 coding 消息没有对应记录，日志停在
    17:37:18 planner 的最后一次工具返回（后端进程 17:14 起、还活着）。看着像那一轮卡住了 ——
    与本次改动无关，待单独排查。

#### 消耗记到消息上 + 上下文压力提醒（顺手修掉一个「80% 触发压缩」的错口径）

- **动机**（用户两条口径）：① 会话的 token 消耗应该**展示在每条消息记录上**，不能只在右栏
  面板里；② 上下文压缩是超出窗口才发生的，那么**快到压缩时应该给用户提个醒**（原话里还
  带一条「可以归档本次聊天记录」，后经用户拍板**归档先不做**，本次只做提醒）。
- **顺带修掉的错口径（实测核对，不是猜的）**：项目里两处写着「压缩在窗口 **80%** 触发」
  —— README 与 `config.py` 的注释。真身在上游
  `deepagents/middleware/summarization.py:243` 的 `_summarization_defaults`：
  `trigger=("fraction", 0.85)` / `keep=("fraction", 0.10)`（模型带 `max_input_tokens`
  时走这套；不带 profile 时改用固定 `170000` tokens —— 所以窗口值必须准）。两处一起改成
  **85% 触发、压缩后保留 10%**，`.env.example` 也补上了新的两个阈值键。
- **改动（记账层）**：
  - `observability/run_log.py`：`AgentRunLog` 新增两个计数 —— **主控单次调用的输入峰值**
    （`peak_input`）与**本轮的压缩次数**（`compressions`）。两个判断都写进了注释：
    压力**只看主控**（子智能体各有窗口，coder 是 1M，拿它的输入比主控窗口会得出「永远
    安全」的错结论）；压缩的判据用**现成的内部调用信号**（摘要只在压缩时发起），不去上游
    埋钩子。`usage_snapshot(context=…)` 多一格上下文。
  - `server/service.py`：新增 `_main_window()`（按**本轮主控形态**取模型窗口，形态→角色
    复用 `llm._provider_attr`，只有一处口径）、`_ratio()`、`_turn_context()`。
    `_turn_usage()` 现在带 `context`；`_context_state`（会话 → [峰值, 压缩次数]）**跨轮**
    存在服务实例上 —— 压力问的是「这个会话涨到多大了」，逐轮清零只看得到本轮那点量。
  - `config.py`：新增 `context_warn_ratio`（0.6）/ `context_alert_ratio`（0.8），
    **只提醒、不改行为**（压缩仍由上游在 85% 触发）。
- **改动（出口）**：
  - **消息记录带上账**：`schemas.RecordItem.usage`（新 `UsageDetail` / `UsageTokens` /
    `UsageContext`），`get_records` 用 `_usage_detail()` 从 `extra["usage"]` 提出来 ——
    前端不必知道内层形状；形状不对一律 `None`（老记录只该让角标不显示，不该报错）。
  - **实时也带上**：`_run_agent` 正常收尾后补一帧 `{"type": "usage", …}`。发在正文**之后**
    → 天然贴在回复末尾；不刷新页面就能看到本轮的账。
  - `/sessions/{id}/usage` 的聚合新增 `context`：取**各轮里最紧的一档**（峰值最大、窗口
    最小、压缩次数最多）—— 用户问的是「现在有多满」，报最松的那轮等于粉饰；`ratio` 由
    服务端按取到的极值**重算**，不抄某一轮那个（否则会出现 105000/256000 这种假比例）。
- **改动（界面）**：
  - **消息角标**（`App.vue` + `style.css`）：助手消息的时间行上方一行很轻的元信息
    `● 输入 12.3k · 输出 0.9k · 8 次调用`，点开就地展开按角色的明细（最多 4 项）。中断的轮
    带「（中断）」、缺用量的带「N 次无用量」。**默认显示**（藏起来等于没做），开关在 Token
    面板里，选择记在本地（复用既有的 `readPref` / `writePref`）；展开态用**绝对下标**
    （`hiddenCount + i`），与待办折叠同一套 —— 窗口往前滑时下标不能跟着变。
  - **上下文压力条**（输入框上方，`ctxbar`）：<60% 什么都不画，≥60% 一条细进度条，≥80%
    转警示色并写明「接近 85% 压缩线：下一条消息可能触发压缩，回复会变短」；已压缩过就带上
    「已压缩 N 次」。取**最近一次拿到的账**（本轮还没账时留上一轮的），否则会一闪一闪。
  - `UsagePanel.vue` 加「上下文」一段（进度条 + 峰值/窗口/占比 + 压缩次数）与那个开关。
  - **顺手修掉上一批的一个真 bug**：`api.ts` 里 `UsageSnapshot` 写的是 camelCase
    （`roundsWithUsage`），而接口回的是 snake_case —— 读一个不存在的键得到 `undefined`，
    「另有 N 轮没有用量数据」那句**永远不显示**，而且不报错。接口字段一律照原样（注释里
    写清为什么：改名的活在前端做只会多一处会漂的地方，漂了还不报错）。
- **关键决策**：
  - **阈值分两档**：单档要么太晚（85% 时下一条就压了，来不及做任何决定），要么太早
    （刚开口就报警，成了噪声）。60% 起让人**有预期地看着它涨**，80% 才给一句话。
  - **压力按「主控峰值输入」算，不自己估消息长度**：系统提示 + 仓库记忆 + 工具 schema
    都是输入的一部分，估算必然偏；而实测的 `input_tokens` 就是真实占用。
  - **归档不做**（用户拍板）：所以 80% 那条提示**目前没有可点的动作**，纯粹是让人有预期。
    要恢复的话，`/conversation_history/{thread_id}.md` 里存着被压掉的原文（每次压缩追加
    一节）—— 但那份**没过脱敏**，导出会成为一个新的凭据出口，届时必须重过一遍脱敏。
  - **`UsageDetail` 与 `UsageSnapshot` 是两个类型**：一个是那一轮的原始账、一个是把它加
    起来的视图；共用 `UsageTokens` / `UsageActor`，免得同一个概念长出两种字段名。
- **验证**：
  - 全量 `pytest` **1046 passed，0 failed**（本次净增 7 条：`tests/test_token_usage.py`
    从 14 → 21；另有 6 条既有断言因多了一帧 `usage`／`extra.usage` 多了 `context` 而更新）。
    `ruff check --no-cache` 本次改动文件干净。那 60 条 setup 错误仍是 pytest 临时目录被本机
    文件沙箱挡住，与本次无关。
  - 新增用例：**子智能体的 90 万输入不算进压力**（把「拿子智能体窗口比主控」这个错结论钉死）、
    压缩次数来自内部调用、窗口跟着**本轮主控形态**走（coding/plan→GLM、review→Kimi、
    qa→GPT）、峰值与压缩次数**跨轮累计**、**按会话隔离**（不串台）、窗口未知时不瞎算比例、
    接口取最紧的一档且**重算 ratio**。
  - 前端：`vue-tsc` 对 app 与 node 两份配置**均通过**（仍是临时 tsconfig 绕开
    `node_modules/.tmp` 写不进去的问题，临时文件已删）。
- **遗留**：
  - **要重启后端才生效**（8000 上跑的仍是旧进程）；前端刷新即可。
  - **`_context_state` 在进程内存里**，重启归零：重启后第一次拿到的压力会偏低（要等下几轮
    涨回来）。它是**提示**不是账，所以没为此建表 —— 真要跨重启准，得把峰值落进会话行。
  - **峰值只反映「主控调过的最贵那一次」**，不等于「下一条消息的输入」：下一轮还会带上这轮
    的新内容。所以 85% 附近那条提示说「**可能**触发压缩」而不是「一定」。
  - **没有真机跑一轮验证界面**：本机后端要重启，本轮只做了逻辑层核对与类型检查。重启后
    发一条消息，看回复下面有没有那行角标、输入框上方有没有压力条即可确认。
  - 归档（含导出 `/conversation_history/`）仍待做，见上面「关键决策」里的安全说明。

#### 右栏新增「Token」标签：按执行主角记账（右栏视图列表里最后一个）

- **动机**（用户口径，四条都点过）：右栏视图里最后一个没做的是 Token 消耗。它也是复盘时
  唯一能给出**量化**的那一项 —— 在此之前项目里没有任何成本数字可引用。
- **改动（记账）**：
  - `observability/run_log.py`：新增 `usage_numbers()`（**两种字段名都认**：
    `input_tokens`/`prompt_tokens`、`output_tokens`/`completion_tokens`），`usage_of()`
    改成调它、只管渲染日志那一行。`AgentRunLog` 开始**记账**：每次 LLM 调用的用量按
    执行主角累加，新增 `usage_snapshot()` 输出本轮那份账。归属**沿用日志那套 `actor()`**
    （`lc_agent_name` + `namespace` 反查），所以账与日志不会各说各话。
  - 归类名三个常量：`MAIN_ACTOR`（主控，账上不用日志那个中文占位名）、`INTERNAL_ACTOR`
    （中间件自己的调用，目前只有上下文摘要）、`INTENT_ACTOR`（意图判定，流外调用）。
  - `server/service.py`：`_turn_usage()` 把「智能体链路（按主角）+ 意图判定那一笔」合并
    成记录里的 `extra["usage"]`；**三处出口都写**：正常收尾、`TurnStop` 中止、异常中断
    （`_record_interrupted` 新增 `usage=` 参数）。每轮开头清空判定累加器
    （`self._intent_usage`，它挂在服务实例上，因为重判要跨调用累加）。
  - `server/intent.py`：`detect_intent()` 新增可选 `usage` 累加器，`_record_usage()` 解
    `usage_metadata` 或 SDK 原始对象；JEV 那条路在**请求成功处**就记（后面「认不出的标签」
    会返回 `None` 交给下一关，但那次调用是真花了钱）。
- **改动（接口 + 面板）**：
  - `GET /sessions/{id}/usage`（`api/routes/sessions.py` 的 `_aggregate_usage`）+
    `api/schemas.py` 的 `UsageActor` / `UsageSnapshot`：聚合各轮记录，按主角求和、
    **花得多的排前面**（与 PR 面板「未合并排最前」同一考虑）。
  - `frontend/src/UsagePanel.vue`（新）：三段总数 + 按角色的横条 + 明细表；`api.ts` 加
    `getUsage()` / `UsageSnapshot`；`App.vue` 加第五个标签（`rightTab` / `panelTabs` /
    `activeTab` / `panel-body` 各一行 —— 这套就是为此设计的）；`style.css` 加
    `.usage-*` 一族（无图表库，横条是一层 div）。
- **关键决策**：
  - **不加表、不加列、不需要迁移**：一条记录一份 usage，与 `session_records` 是 1:1
    （同一个 `(session_id, created_at)` 轴），直接放进本来就在的 `extra` JSONB。拆表只多一次
    join、还要多一条迁移；将来要跨会话做「按天 / 按模型」统计时再迁。
  - **不算钱**（用户点）：各家计价口径与缓存折扣不同，显示一个错的金额比不显示更糟。
  - **中断的轮次也算**（用户点）：钱真花了，只如实标 `interrupted`，界面说明这段数字不完整。
  - **缺数据绝不当 0**：平台没回用量 → `missing` 计数；本功能上线前的记录 → `rounds` 与
    `rounds_with_usage` 分开回报，界面写「另有 N 轮没有用量数据」。这是本次最容易糊弄、
    也最容易看错的一处。
  - **摘要调用单列**：实测单次就有 **7 万~8 万输入 tokens**，并进「主控」名下等于把最大的
    一笔开销藏起来。**第一次手动核对就证实了这个判断**：主控 24,895 + coder 12,000 +
    摘要 72,301 的一轮里，摘要占 **65%**。
  - **先不做 SSE 实时推**（用户点）：本轮结束刷新面板即可；实时推要动 SSE 帧契约。
- **验证**：
  - 全量 `pytest` **1039 passed，0 failed**（本次净增 14 条：`tests/test_token_usage.py`；
    另 2 条既有断言因记录里多了 `usage` 而更新）。`ruff check --no-cache` 本次改动文件干净。
    另有 60 条 setup 错误全部是 `PermissionError: WinError 5 → %TEMP%\pytest-of-<user>`，
    即 pytest 临时目录被本机文件沙箱挡住，**与本次改动无关**（改动前即如此）。
  - 新增 `tests/test_token_usage.py` **14 例**：两种字段名都认且认不出返回 `None`（不是
    `(0,0)`）、账初始为空、按主角分组（主控/子智能体）、摘要单列、**认不出的用量记
    `missing` 且不进总数**、中断标记、判定那笔单独归一类、口令命中不产生该项、中断轮次的账
    跟着记录落库，以及接口层：求和与排序、缺数据的轮次如实上报、空会话、
    **脏数据（JSONB 不干净）不打挂面板**、非法 id 400。
  - 前端：`vue-tsc` 对 app 与 node 两份配置**均通过**（`npm run build` 见遗留 —— 卡在
    `vite build` 的进程派生受限，不是类型问题）。
  - 手动端到端核对（假事件走真记账 + 真聚合）：`main 24,895/2,645`、`coder 12,000/900`、
    `internal 72,301/3,339`，总数 `109,196/6,884`，排行 `internal > main > coder` —— 与
    `logs/agent.log` 里既有那两类行（「LLM 调用完成」「摘要调用完成」）逐条对得上。
- **遗留**：
  - **要重启后端才生效**（8000 上跑的仍是旧进程）；前端是 vite 热更新，刷新即可。
  - **`npm --prefix frontend run build` 在本机跑不通**（两处都不是本次改动引起的）：
    ① `vue-tsc` 写 `frontend/node_modules/.tmp/*.tsbuildinfo` 被拒（该目录写不进去，已用
    诊断脚本补过权限仍未过）；② `vite build` 在 `optimizeSafeRealPathSync` 里
    `spawn EPERM`（沙箱不允许捕获子进程输出）。本次改用**临时 tsconfig 把 tsbuildinfo 指到
    可写处**完成了两份类型检查（临时文件已删），但**产物未构建**。要出产物请在本机不被沙箱
    限制的终端里跑一次。
  - **本功能上线前的会话看不到账**：那些记录里没有 `extra.usage`，且**回填不出来**（当时
    没记）。面板对这类会话显示空态，不会显示成「消耗 0」。
  - **JEV 判定那笔没入账**：它走 SDK、用量对象形状未核对；本次只记了分类模型那笔
    （`intent` 这一类）。顺带记：`classifier_model` 当前实现是**临时直连 deepseek**，
    所以本轮「意图分类」这一项其实花在主模型上。
  - **没有真机跑一轮验证面板**：本机后端要重启、本轮只做了逻辑层核对与类型检查；
    重启后点开「Token」标签对一眼 `logs/agent.log` 的用量行即可确认。

### 2026-10-11

#### Redis 降级根因：`127.0.0.1:6379` 其实是 SSH 隧道，1 秒超时 + 「一次失败即永久降级」把它变成一锤子买卖

- **动机**（上一条留的遗留项）：「Redis 上的 运行缓冲 不可用，本进程后续退回进程内实现」这条
  WARNING 反复出现（用户说「redis 本身是可以连接的」），而它有真实后果 —— 正是它留下了
  那个骗了近一小时的半成品缓冲（见上一条）。
- **定位（实测，不是猜的）**：
  1. 日志里 3 次同样的 WARNING，都是**一轮对话开始后 1~2 秒**：`12:54:43` / `20:43:39` /
     `21:20:43`，异常是 `TimeoutError: Timeout reading from 127.0.0.1:6379`。
  2. 但服务端一点都不慢：Redis **慢日志是空的**；客户端 `PING` 往返 20 次的分布是
     **min 59.3ms / median 62.0ms / p95 92.7ms / max 94.0ms**。本机回环不该有 60ms。
  3. `netstat` 找到了那 60ms：`127.0.0.1:6379` 与 `127.0.0.1:5432` 由**同一个 `ssh` 进程**
     （PID 50028）转发 —— 所谓「本机 Redis / 本机 PG」其实是 SSH 隧道另一端。
  4. 于是「1.0 秒超时」的真实含义是：只给 16 倍余量，**一次 TCP 重传（通常 ≥200ms、
     连丢可到 1s 以上）就能把一条命令顶过超时**。
  5. 而旧纪律是 **「第一次失败即永久降级」** —— 一次瞬时抖动 = 这个进程此后彻底失去跨进程
     能力（跨 worker 可见性、运行权、帧重放全没了），要**重启后端**才回来。代价与收益完全
     不成比例。（顺带排除了一个错误假设：JEV / SystemOne 不阻塞事件循环 —— 实测调用 2.62s
     期间事件循环最大延迟 63ms、Redis 命令最大 93ms。）
- **改动**：
  - **放大超时**：`config.py` 的 `redis_timeout_seconds` 默认 `1.0 → 5.0`（注释里写清上面这组
    实测数字）；`.env.example` 同步成 `REDIS_TIMEOUT_SECONDS=5.0` 并说明「远端 / 走隧道必须
    放大」。
  - **降级改成「失败 → 冷却 → 再试一次」**：`redis_cache.py::RedisBacked` 新增
    `cooldown_seconds`（默认 60s，配置键 `REDIS_DEGRADE_COOLDOWN_SECONDS`）。失败时记下
    「冷却截止时刻」，窗口内直接走进程内（不白撞超时），**窗口过了再试一次**，成功即恢复并留
    一条 `已恢复（冷却结束后的重试成功）` 的 info；再失败就重新进冷却。`degraded` 属性由
    「一个布尔位」变成「现在是否在冷却窗口内」。
  - **失败日志带上耗时**：`（这条命令花了 %.0f ms 才失败）` —— 只说「不可用」分不清是服务端慢
    还是链路抖，而 62ms 基线与 1000ms 超时之间差的正是一次重传。
  - `cooldown_seconds` 从 `api/app.py` 的装配处一路传给三个实现（`RedisCircuitStore` /
    `RedisRunBuffer` / `RedisRunLock` 的构造函数各加一个参数，默认 60）。
- **关键决策**：
  - 保留一个**逃生开关**：`REDIS_DEGRADE_COOLDOWN_SECONDS=0` ＝ 退回旧纪律「一次失败即永久
    降级」（`_degraded_until = inf`）—— 想要「宁可不要跨进程能力也别每帧白等」的部署不用改代码。
  - 冷却而不是「指数退避 + 每次必试」：那套需要状态机，而这里要防的只是「半死时每帧白等一次
    超时」，一个窗口就够了。
  - 超时与冷却都做成**配置**而不是常量：隧道质量是部署环境的事，不该写死在代码里。
- **验证**：
  - `pytest` 全量 **1085 passed**（此前 1080；新增 5 条）；`ruff check` 本次改动文件干净。
  - 新增回归（`tests/test_redis_cache.py`）：失败 → 冷却窗口内**不再撞 Redis** → 窗口过后重试
    成功且 `degraded` 回到 False；恢复只记一条 info；冷却后再失败会**重新进冷却**；失败日志里
    有真实的 `312 ms 才失败`；`cooldown=0` 时过了任意久都不再撞 Redis。
  - **真机自检**（conda `aicoding` + 真 Redis，探针不入库）：`REDIS_TIMEOUT=5.0` 已生效、
    `socket_timeout=5.0`、PING 中位 62ms（再次证实隧道）；失败 → 冷却内不白撞 → 冷却结束
    自动恢复，三个状态都按预期走。
  - **修掉一个隐蔽 bug**：上一版补 `time.monotonic()` / `perf_counter()` 时忘了 `import time`，
    `RedisBacked.run` 每次都 `NameError` —— 是这批测试（含既有的运行缓冲 / 熔断用例）先红出来的。
- **遗留**：
  - **要重启后端才生效**（8000 上跑的还是旧进程）；`.env` 我已加上 `REDIS_TIMEOUT_SECONDS=5`。
  - Redis / PG 走 SSH 隧道本身是**部署形态问题**（用户机器上 `localhost:6379` 与 `:5432` 由同一个
    ssh 进程转发）。长期方案是把它们放到本机或直连，隧道只当开发期过渡 —— 本批只是让平台在
    这个形态下不被打死。

#### 界面卡在「正在接续本轮」：残留的运行缓冲骗了 `is_running`，用运行权当存活信号

- **动机**（用户报障）：「界面显示正在继续本轮，到底在干嘛呢」——一直挂在那个状态，什么也没发生。
- **定位（三步都拿到了硬证据）**：
  1. 那句话是前端 `App.vue` 的 `activity.value = '正在接续本轮'`，由 `attachRunning()` 设置 ——
     它只在 `GET /sessions/{id}` 回 `running: true` 时走到这儿。
  2. 后端确实回了 `running = True`，但 **Redis 里这个会话只剩一把键**：
     ```
     harness:run:<sid>:seq = "0"   TTL=1757     （没有 frames、没有 done、也没有 lock）
     ```
     `seq=0` 是 ``open()`` 写的 —— 开了一轮，却**一个帧都没有、也没打 done、运行权也没了**。
  3. 结合历史记录（那一轮**正常跑完并落库**，回复完整）与那条 WARNING
     （`Redis 上的 运行缓冲 不可用，本进程后续退回进程内实现`），因果链是：
     **运行期那次 Redis 调用失败 → 帧全走进程内缓冲（所以那一轮没受影响）→ Redis 上只剩
     `open()` 写的 `seq=0` 半成品**。后端重启后新进程（Redis 没降级）一读：有 seq、没 done
     → `is_running` 永远 True；`subscribe` 的循环 `read`（空）→ `is_done`（False）→ sleep
     → **空转到 seq 的 TTL 到期**（还剩近半小时）。
- **改动**：**运行权当存活信号**。运行权带 TTL、由持有者定期续期（默认 120s / 30s），持有者
  进程被 kill / 重启后最多一个 TTL 就没了 —— 而缓冲只是数据、没有心跳。
  - `server/run_store.py`：`RunLock` 协议加 `held(session_id)`；`RedisRunLock` 实现为
    `EXISTS lock`（Redis 降级时返回 True —— 那会儿缓冲也已退回进程内、本地注册表才准），
    `MemoryRunLock` 恒 True（进程内没有「持有者死了」这回事，本地注册表已经说清）。
  - `server/runs.py`：`is_running` 与 `subscribe` 在「缓冲还在、没打 done」之后再加一问
    「运行权还在吗」，不在就按已结束处理（订阅立刻收尾，不空转）并记一条 warning。
- **关键决策**：不引心跳、也不加新键 —— 运行权本来就有 TTL + 续期，就是现成的存活信号。
  两条防线都加（`is_running` 管「要不要接回」，`subscribe` 管「已经接上的那个别空转」）。
- **验证**：
  - `pytest` 全量 **1080 passed**；新增回归 `test_a_run_whose_owner_died_is_not_reported_as_running`
    （锁过期后 `is_running` 为假、订阅立刻收尾）；既有 11 例（含「另一个 worker 在跑时可见」、
    「TTL 到期读成空闲」）照旧通过 —— 正常在跑的轮次不受影响。
  - `ruff` 干净。
  - **用户那个真实卡住的状态当活体验证**：同一会话、同一份残留，新代码 `running = False`
    （你的 8000 仍是 `True`）；`GET /chat/{sid}/stream` **0.73 秒收尾**（旧代码会挂到 TTL）。
    历史记录完好（那一轮本来就跑完了），所以用户看到的是「这轮的回复在，只是不再显示接续中」。
- **遗留**：
  - **要重启后端才生效**（8000 上跑的是改动前的进程）。
  - **触发这个残留的根因（运行期某次 Redis 调用失败）还没查**：它有真实后果了 —— 除了在控制台
    刷一条 WARNING，还会留下一个能骗近一小时的半成品缓冲。上一批把它记成了遗留，现在建议排一次
    （那条 WARNING 现在也能在右栏「日志」面板里直接看到）。

#### 三个 bug 修完：日志按会话展示（滤掉噪声）、日志关键字搜索、仓库记忆读不出来

- **动机**（用户报障，三条一起）：① 日志要按会话分开展示，「打开哪个会话看哪个会话的」，
  不要一屏像是全部；② 日志要能按关键字搜；③ 仓库记忆明明存在，面板却刷不出来
  （`zzpfordevelopment/wanqi-mall`）。
- **③ 记忆读不出来 —— 根因（实测抓到的硬证据）**：
  ```
  store 里实际存的 key：  ('memories', '/zzpfordevelopment/wanqi-mall.md')
  我的接口去读的 key：    ('memories', '/memories/zzpfordevelopment/wanqi-mall.md')
  ```
  `CompositeBackend` 命中路由前缀后**先把前缀剥掉**再交给子后端（上游 `_route_for_path` 的
  说明），所以 store 里的 key 是去前缀那半截。上一批我直接按 `memory_path()`（带前缀）去
  `aget`，对**已经有记忆**的仓库永远返回「还没写过」。
  - **修法**：新增 `backends/composite.py::memory_store_key()`（从 `memory_path` 剥前缀，
    路径形状仍只有一处定义），`read_memory` 改用它；接口回给前端的 `path` 仍是**逻辑路径**
    （带 `/memories`，与 `save_repo_memory` 同一口径）—— 两个概念分开，不再混用一个值。
  - **为什么上一批的测试没抓到**：它把错误假设抄成了断言（两边用同一个表达式，必然相等）。
    现在改成**经复合后端写进去、用 `read_memory` 读回来**（`tests/test_composite_backend.py`）
    —— 口径对不上就会红。同文件里早有 `HISTORY_KEY = "/<thread>.md"` 这条「去前缀」的既有
    口径，我漏看了。
- **① + ④（④是用户补充的：日志查询自己不该出现在面板里）**：`observability/log_tail.py`
  加两条噪声口径：
  - **日志接口自己的请求**（路径含 `/sessions/<id>/logs`）—— **永远滤掉，不给开关**。实测：
    验证时调了 ~55 次 `/logs`，给那个会话留下 **178 行**自己造的日志，正好把要看的东西淹了。
  - **HTTP 请求噪声**（`api.middleware` + `uvicorn.access`）—— **默认滤掉**，面板上给开关。
    一个请求打三行，而前端每 1.5 秒轮询一次工作区状态：实测某会话 **324 行里 247 行（76%）**
    是它，真正有用的运行日志只有 62 行。
  - 顺带把 logger 名解析出来（`LogLine.logger`）：噪声规则要它，接口也一并回给前端。
- **② 关键字搜索**：接口加 `q=`（大小写不敏感子串）与 `with_http=` 两个参数；前端加搜索框
  与「含 HTTP 请求行」勾选框。**关键字先筛再截尾** —— 否则「搜出 0 行」只是尾部那 300 行里
  没有，而不是整个会话里没有。
- **验证**：
  - `pytest` 全量 **1079 passed**；`ruff` 干净；`npm --prefix frontend run build` 通过。
  - 新增/改写的测试：复合后端记忆往返（会抓到上面那个 key bug）、HTTP 噪声默认滤掉 /
    开关打开才回来、日志接口自己的行**永远**不出现（就算开了开关）、关键字先筛后截尾、
    接口把 `q`/`with_http` 传下去并回 `logger`。
  - **真后端实数据验证**（同一个真会话 `cb4c905c…`，8001 跑新代码、8000 是你起着的旧进程）：
    默认命中 **354 行 → 77 行**（构成：62 行 `run_log` 工具/LLM + 6 行轮次 + 会话/记忆/intent 各几行）；
    `with_http=true` → 182 行且其中 `/logs` 自己的行 **0**；`q=wanqi` → 18 行，首行正是命中的那条；
    记忆接口从 `content: null` 变成 **5091 字符**的真记忆。
- **遗留**：
  - **要重启后端才生效**：你本机 8000 上跑的是**你自己的进程**（我起的那个因端口占用退出了），
    它用的是改动前的代码 —— 上面三条修复都得重启才看得到。我临时起的 8001 已停掉（两个进程
    写同一个库不合适）。
  - **下载**与面板同一套噪声口径（整份，但不含 HTTP 请求行，也没有 limit / 关键字）；
    要看请求行本身就去 `logs/api.log`。

#### 右栏新增「日志」标签 + 日志补上「会话」维度（a+c）

- **动机**（用户口径，选 a+c）：右栏第四个视图；外加一个「下载日志」的出口。
- **先说一个事实（决定了做法）**：日志里**原先几乎没有会话维度** —— `agent.log` 共
  28,907 行，只有 **877 行（≈3%）**带 `public_id=`；工具调用、LLM 调用、沙箱操作这些
  真正想看的行只有 `[trace_id] [agent]`，而 trace_id 是**请求级**的、没落库，事后没法从
  会话反查。**按会话过滤只能捞到 3%** —— 那样的面板会像「这一轮什么都没发生」，比没有
  更误导。所以先补维度（a），再做视图。
- **改动（a：补维度）**：
  - `observability/context.py` 加 `session_id` 的 ContextVar（与既有 `trace_id` 同一套
    `set/reset/get`，无上下文时 `-`）。
  - `observability/logging.py`：格式串加一格 → `[%(trace_id)s] [%(session_id)s] [%(agent)s]`，
    `_TraceIdFilter` 一并注入 `record.session_id`。
  - **设置点只有一处**：`api/session_lookup.resolve_session()`。每个会话级请求都从那儿过，
    所以连 `/workspace` 触发的 provisioning 也算这个会话的日志；`RunHub` 起的那一轮跑在
    `create_task` 里，创建时会把当前上下文复制过去，于是那一轮的日志也带着它。起初写在
    `service.chat()` 里，后来发现**覆盖不到路由层的 provisioning**（那才是出事最多的阶段），
    就搬到了这里 —— `service.chat` 只留一句注释指向它。
- **改动（视图）：**
  - 新增 `observability/log_tail.py`：按会话挑行 + 把两个文件**按时间合并**成一条线 +
    级别阈值 + 尾部 limit。命中规则最后收敛成**一条**：行里出现了这个会话 id（它同时覆盖
    `[<id>]`、`/sessions/<id>`、`/chat/<id>`、`public_id=<id>`，还认裸 id —— 一开始按
    「带装饰的几种写法」逐个匹配，单测当场抓到它连 traceback 的续行都漏）。
  - 接口 `GET /sessions/{id}/logs?limit=300&level=` 与 `GET /sessions/{id}/logs/download`；
    两者**返回前都再过一遍脱敏**（词表 = 会话令牌 + 该平台兜底令牌）—— 日志落盘时脱敏过，
    但读出来是**第五条出口**。
  - 前端新增 `LogPanel.vue`（右栏第四个标签）：级别下拉（阈值语义）、等宽显示、按级别着色
    （INFO 灰、WARNING 黄、ERROR 红）、读出来自动贴到底、顶部「最后 N 行 / 共 M 行」、
    **「下载」按钮**指向下载接口（服务端 `Content-Disposition` 管附件名）。
- **关键决策**：
  - **只读当前那份日志文件**，不追 `.1`/`.2` 备份：把备份读进来会让「尾部 N 行」的含义
    取决于轮转状态。要看更早的走下载（按钮就在面板上），去 `logs/` 目录拿。
  - 「命中总数」与「返回行数」**两个数都给**：界面才能说清「最后 300 行 / 共 1240 行」。
  - 续行（traceback 等没有自己时间戳的行）**沿用上一行的时间戳**，否则它会被排到最前、
    还可能被 tail 丢掉。
- **验证**：
  - `pytest` 全量 **1073 passed**；新增 `tests/test_logs_route.py` **9 例**：真实
    `setup_logging` 下「设置了会话 id 之后每行都带 `[<id>]`」、四种写法都认、级别阈值、
    尾部与总数、续行跟随父行、文件缺失＝空、接口脱敏（令牌不回显）、下载是附件且给全、
    会话不存在 404、`resolve_session` 会把会话 id 写进上下文。
  - `ruff` 改动文件干净；`npm --prefix frontend run build`（含 `vue-tsc`）通过。
  - **真后端端到端**（PG / Redis 这次都起了，8000 空着）：起真后端 → 建一个真会话
    （`ai-coding-probe/does-not-exist`，故意 clone 不上的仓库）→ `POST /workspace` 触发
    provisioning → 发一条 `同步仓库`（**direct intent，不经智能体、不烧 token**）
    → 查该会话的日志：**命中 15 行**，其中 `POST /workspace` 之后的行都带
    `[4486991a-…]` 这一格（`uvicorn.access`、`api.middleware ←`、
    `server.service: 意图分析 intent=sync source=keyword` 都在），而路由前打的
    `→ POST /workspace` 与建会话那两行靠**路径 / public_id** 命中；
    `level=WARNING` 命中 1 行；`/logs/download` 返回 200 +
    `attachment; filename="ai-coding-probe-does-not-exist-logs.txt"`（3952 字节）。
  - 顺带说明**这次没出图**：本会话的浏览器自动化工具没了（`cua` / playwright 都不可用），
    而我看不到图片 —— 用命令行盲截一张我无法核对的图，等于交付一张未验证的图。所以改用
    上面的接口实测数据作证据；要看画面可以直接起前端点「日志」（后端还开着，见下面遗留）。
- **遗留**：
  - **验收用的那个会话留在库里了**：`ai-coding-probe/does-not-exist`
    （`public_id=4486991a-e53a-42d9-a648-db58db759e41`，db_id=43）—— 留着是为了让它成为
    你看「日志」面板的现成样本；点右上「删除会话」即可清掉。
  - **本次起着的后端没停**（8000，PID 34844）：留着方便你直接看日志面板。前端按你平时的
    方式起（5173 被你另一个项目占着，`start.bat` 会问你是否结束占用进程）。
  - 面板里第一条 WARNING 就是老熟人：`Redis 上的 运行缓冲 不可用，本进程后续退回进程内
    实现`。**启动时 ping 是通的**，说明是运行期某次调用失败了 —— 这条以前只在控制台能看到，
    现在面板里一眼可见，值得照着这条线索再查一次（另记）。

### 2026-10-10

#### 右栏新增「PR」标签：本仓库 PR 列表（查不到 ≠ 没有）

- **动机**（用户口径）：第二批右栏视图的第二个 —— 之前想知道「还开着哪些 PR」只能去
  Gitee/GitHub 页面看，或者从仓库记忆的文件头里猜。
- **改动**：
  - 后端新增 `GET /sessions/{id}/pulls` → `{open_count, pulls[]}`；每条含编号 / 标题 /
    状态码 / 链接 / 分支 / 作者 / 时间。
  - `api/schemas.py` 加 `PullItem` / `PullList`；`frontend/src/api.ts` 加 `getPulls()`。
  - 前端新增 `PrPanel.vue`（右栏第三个标签）：未合并的排最前（后端排序）、状态徽章 +
    左侧色条（待合并=accent、已合并=绿）、标题是新窗口打开平台链接、「重新读取」重拉接口。
  - `App.vue`：`panelTabs` 加「PR」、`rightTab` 类型放宽、`.panel-body` 加一路分派。
- **关键决策**：
  - **状态判定收敛到一处**：把「一条 PR 是什么状态」搬到 `tools/pr_client.pull_state()`
    （平台差异在那里消化一次：Gitee 把合并态写在 `state=merged` 上，GitHub 看 `merged_at`），
    `agent/memory_reconcile._pull_state()` 改成调它、只负责翻成中文标签。原来这套判定在
    reconcile 里私有实现，面板要复用得再写一遍 —— 两处判定一漂，就会出现「面板说还开着、
    闸门说没有未合入的」这种自相矛盾，而那正是最难查的一类 bug（测试里直接拿两份实现
    对照断言）。`_pull_fields` 同理改为调 `pull_summary()`。
  - **查不到 ≠ 没有（硬约束）**：查询失败走 502 + 原因，**绝不返回空列表**。面板把它显示成
    一条错误 + 「先确认凭据 / 网络」，而不是「这个仓库还没有 PR」。
  - 接口回**状态码**（`open`/`merged`/`closed`/`unknown`）而不是中文标签：展示口径留在前端，
    与 `workspace_status` / `plan.status` 的既有做法一致。认不出的状态一律 `unknown`，
    显示成「状态未知」并保持中性色 —— 不猜。
- **验证**：
  - 新增 `tests/test_pulls_route.py` 4 例：归一 + 排序（未合并最前、其余编号倒序、脏数据跳过）、
    **查询失败 502 而不是空列表**、**面板与闸门判定逐条一致**（含 Gitee 的 `state=merged`、
    未知状态两边都不猜）、会话不存在 404。
  - `pytest` 全量 **1064 passed**；`ruff check` 改动文件干净（含 `memory_reconcile` 的重构）；
    `npm --prefix frontend run build`（含 `vue-tsc`）通过。
  - **真机渲染验证**（假后端 + 临时 vite 配置，用完即删）：三个标签都在、点「PR」后记忆面板
    正确卸载、顶部「未合并 1 / 共 3」、三条的徽章（待合并/已合并/已关闭）与左侧色条
    （`rgb(59,91,219)` / `rgb(26,158,92)` / 中性）都对、标题链接指向 PR 页面。
- **遗留**：
  - 主区顶部那个「未合并 PR N」角标**这次没做**：它要在 App 层轮询（面板只在切过去时才挂载），
    属于另一套成本，等右栏三个视图齐了再看要不要加。
  - 右栏还剩 **日志** 视图；之后是 Token 消耗（要插桩 + 迁移）。

#### 右栏新增「记忆」标签：把仓库记忆显示出来（只读）

- **动机**（用户口径）：第二批右栏视图的第一个 —— 仓库记忆原先**只活在系统提示词里**：
  模型看得到、用户看不到（想知道里面写了什么，只能去翻 store）。
- **改动**：
  - 后端新增 `GET /sessions/{id}/memory`，返回 `{path, content}`；`content` 为 `null` ＝
    这个仓库还没写过记忆（不是错误、也不是空串）。
  - `backends/composite.py` 新增 `MEMORIES_NAMESPACE` 常量与 `read_memory()`；
    `build_composite_backend` 里那条记忆路由也改用同一个常量。
  - `api/schemas.py` 加 `MemorySnapshot`；`frontend/src/api.ts` 加 `getMemory()`。
  - 前端新增 `MemoryPanel.vue`（右栏第二个标签）：顶部一行「路径 + 重新读取」，正文用现成的
    `renderMarkdown` 渲染；空态给出「它会在编码/审查收尾时写出来」的说明。
  - `App.vue`：`panelTabs` 加「记忆」一项、`rightTab` 类型放宽、`.panel-body` 按 `activeTab`
    分派（代码 / 记忆）—— 上次改版留的「加标签只加一行」这次第一次用上。
- **关键决策**：
  - **直连 `store.aget(namespace, key)`，不复用 `StoreBackend.aread`**。后者解析 namespace
    要走 LangGraph 的**运行上下文**（`get_runtime()`），而 HTTP 接口没有那个上下文 —— 硬用会
    静默落到 `("filesystem",)` 这个错的 namespace 上，结果是永远显示「没有记忆」，**不报错**。
    所以按 `MEMORIES_NAMESPACE` + `memory_path()` 直接定位，与中间件读写的是同一把 key。
  - namespace 抽成常量给两处共用（写入路由与只读接口），与 `memory_path` 同一条纪律：
    两处各写一份，就会出现「写入成功、没人读得到」那种事故。
  - **只读**：不给编辑、也不给「手动刷新记忆」—— 正文只在 coding / review 收尾由主控写，
    文件头由平台每轮开工前按 PR 实况重算（AGENTS.md 的写入口径）。面板上那个按钮叫
    **「重新读取」**（重新拉一次接口），刻意不叫「刷新」，免得与「刷新仓库记忆」混淆。
  - 老数据兼容交给库自己的 `file_data_to_string`（认得 `list[str]` 那种旧内容形状），
    不自己写一份归一化。
- **验证**：
  - `pytest` 全量 **1060 passed**；新增 `tests/test_memory_route.py` 6 例：**读的 key 与中间件
    写的是同一把**（断言 `(namespace, key)` 逐字相等）、没记忆返回 `content: null`、老的
    `list[str]` 内容能读、非法 id 400 / 会话不存在 404（且**不去读 store**）/ 缺仓库信息 409、
    store 里形状不对时按「没有记忆」处理。
  - `ruff check` 改动文件干净；`npm --prefix frontend run build`（含 `vue-tsc`）通过。
  - **真机渲染验证**（同上一批：假后端 8010 + 临时 vite 配置 5174，用完即删）：两个标签都在、
    点「记忆」后**代码面板正确卸载**、标题/三个章节/PR 链接/路径都渲染出来、正文区高 653px
    可滚动、rail 仍贴最右（x=1280）。
- **遗留**：
  - 记忆正文里的文件路径不可点（正文是 Markdown 文本；代码面板的跳转仍靠方案步骤里的
    「涉及文件」）。
  - 右栏还剩 **PR / 日志** 两个视图（都不改库），以及 Token 消耗（要插桩 + 迁移）。

### 2026-10-06

#### 界面改版第一批：样式方案 B + 取消计划面板（方案进聊天流）+ 快捷动作条

- **动机**（用户口径，逐条拍过）：样式选 **B**（去气泡化 + 色相统一 + 建立刻度）；**计划面板
  取消**，方案改成在聊天列表里以**链接**出现；快捷动作**只留两个**（刷新仓库记忆 / 同步代码）
  并挪到**输入框上方**（可折叠）；右侧功能列表挪到**最右边**。功能顺序：仓库记忆 / PR / 日志
  先做，Token 消耗最后做。
- **改动**：
  - `frontend/src/style.css`：令牌整套换成 B —— 三层底（`#f7f9fc` 主区 / `#eef2f9` 侧栏 /
    `#ffffff` 卡片）、灰全部换成**偏蓝的那一族**（中性灰压在蓝底上会发脏）、圆角收敛成
    **4/8/12/999** 四档、字号收敛成 **12/13/14/15/16/20/26**、补上 `--danger`/`--danger-hover`
    （原来只写了 `var(--danger, #d64545)` 兜底、**从没定义**过）、加一层唯一的轻阴影。
  - 视觉三处重排：**用户气泡实心 accent + 白字**（右下角收一个圆角）、**AI 正文去气泡**
    （直接排在页面上，只有代码块与待办成卡片）、**待办块**从整块粉色改成中性卡 + 左侧 3px
    accent 条（粉色是全页唯一暖色，跟冷调底子是两套体系）。
  - 右栏 `.right-panel` 改 `flex-direction: row-reverse`、`.panel-tabs` 的 `border-right` 换
    `border-left` —— 竖排标签条贴到**窗口最右边缘**，内容在它左边。**只换方向不动 DOM 顺序**。
  - `frontend/src/App.vue`：
    - `Part` 加第三种块 `plan`；新增 `pushPlan`（同一份方案**只占一块**，就地替换）、
      `planFromRecord`（落库那条记录 → 方案块）、`planPart/planTitle/planStatus/planSteps/
      planMarkdown/planProgress` 一组取值助手（模板里不写非空断言）。
    - 聊天流里渲染方案链接（`📋 方案：<标题> ｜ 状态 ｜ 已完成 3/7`），点开**就地展开**步骤与
      全文；「涉及文件」仍然一键跳到右侧代码面板。
    - 删掉方案面板那一整套：`plan` 状态、`activeTab` 的 plan 分支、`panelTabs` 的「计划」项、
      面板版模板；`resetPlan()` 改名 `resetPanels()`（它管的是跳转目标 / 方案步骤展开 /
      待办折叠）。
    - 记录 → 消息时放开 `type === 'plan'`；不再需要 `getPlan()` 拉面板数据（方案随记录来）。
    - 输入框上方新增快捷条：默认只剩「快捷 ▸」开关，展开后是**刷新仓库记忆 / 同步代码**两个
      动作 —— 各自发一句**命中关键字层强口令**的消息（`刷新仓库记忆` → analysis；
      `同步仓库` → sync **直接执行**，不经智能体、不烧 token），判定确定，不依赖分类模型。
  - 后端（方案要能进聊天流，就得落库）：
    - `server/service.py::plan_payload` 带上 `content`（方案全文）：实时帧与刷新后读记录
      **同一份数据**，前端不必再补一次请求；`_record(...)` 加 `ref_id` 透传。
    - 新增 `_record_plan_once`：方案首次生成时写一条 `session_records`（`type='plan'`、
      `ref_id=plan_id`、`content=全文`、`extra={plan_id,status,steps}`）。
    - `db/dao.py::plan_record_exists`：按 `(session_id, plan_id)` 判重 —— 一次新的运行里内存
      标记从零起步，**跨轮去重只能靠库里查**。
    - `api/schemas.py::PlanInfo` 与 `frontend/src/api.ts` 同步加 `content`。
- **关键决策**：
  - **只在该方案首次生成时写记录**（用户点过：不是「每次更新都插一条」）。方案会被
    `updatePlan` / `updatePlanSteps` 反复改，追加式落库会把聊天流刷成「方案已更新」的流水。
  - 方案块与记录**同一份形状**：SSE 帧、`/plan` 接口、落库记录三者一致，前端一条渲染路径。
  - 快捷动作**直接发送**而不是预填：这两条都是「无参数、点了就该发生」，多一次回车没有价值。
- **验证**：
  - 后端：`pytest` 全量 **1054 passed**（新增 2 例：方案首次写记录且同 plan_id 不重复、
    删除方案不写第二条）；`ruff check` 改动文件干净。
  - 前端：`npm --prefix frontend run build`（含 `vue-tsc`，模板类型也过）。
  - **用真实应用出图验证**：本机 8000 被另一个项目占着、沙箱也连不上 Postgres，所以起了一个
    **假后端**（只答 `/sessions`、`/sessions/{id}`、`/records`、`/workspace`、`/repo`）加一份
    **临时 vite 配置**（代理指向假后端，跑在 5174；用完即删、未进版本库），让**真 App.vue +
    真 style.css** 渲染出来截图。computed style 逐项核对：`用户气泡 rgb(59,91,219) / 白字`、
    `AI 正文 rgba(0,0,0,0) 且边框 0px`、`待办块 rgb(244,246,250) + 左边框 2.67px rgb(59,91,219)`、
    `方案链接左边框 accent 且展开态存在`、`rail 在最右（x=1236，窗口宽 1280）`、三栏合计无溢出。
- **遗留**：
  - **升级前已有的会话看不到方案链接**：那些方案只在 `plans` 表里、没有对应的 `plan` 记录，
    所以聊天流里不会出现（刷新也不会）。开发阶段，新建会话即可；要兼容老会话得再写一次回填。
  - 方案 Markdown 正文里的文件路径不可点（结构化步骤里的「涉及文件」可以点，跳代码面板）。
  - 右栏现在只剩「代码」；第二批（仓库记忆 / PR / 日志）往 `panelTabs` 加项即可，模板不用动。
  - Token 消耗面板仍待做（要先在 `_run_agent` 里把 `message-finish` 的 `usage` 累加落库，
    加一列 JSONB + 迁移）。

### 2026-09-28

#### 平台名不再写死：加 `platform_label()`，7 处运行期文案 + 4 处提示词清零

- **动机**（用户口径）：全仓搜「固定打印 gitee / github」的地方，做一个**取平台名字的工具**，
  那些写死的地方统一改用它。
- **搜索结论**（只读排查，全仓 `rg` 过一遍）：
  - **运行期文案里写死的 7 处**：`agent/middleware/memory.py` 5 处（那条
    `logger.info("仓库记忆已按 Gitee 的实际状态刷新")`，以及三个用户可见 marker ——
    闸门拦下 / 查不到 PR / rework 没有可修对象）；两个客户端的
    「Gitee / GitHub access token 未配置」各 1 处。
    **GitHub 仓库跑这些路，文案一律说「Gitee」——真写错了**，会让人去错的地方找 PR。
  - **提示词 / 分类器里写死的 4 处**：`agent/prompts.py` 2 处、`server/intent.py` 2 处。
  - **1 处兜底域名**：`repo_cache.py` 的 `urlparse(spec.url).netloc or "gitee.com"` ——
    不是打印，但 URL 畸形时会给 GitHub 仓库的认证头挂上 gitee.com 域名，等于**静默失效**。
  - **不动的**：`codeplatform.py` 的 `Platform` / `_HOSTS` / `_LABELS`（唯一的源头）、
    `config.py` 的 base_url、`db/models.py` 的列默认值、`gitee_token` 这类**字段名与列名**
    （改名要动 DB/API，不在本次）、`redact.py` 的令牌前缀、`auth.py` 的端点与路由路径
    （`/auth/gitee/*` 必须叫这个）；以及大量**注释 / docstring** 里的设计说明（按用户口径
    不动 —— 改了是纯噪音）。
- **改动**：
  - `codeplatform.py`（叶子模块，谁都 import 得动）新增 **`platform_label(source)`**：接
    `Platform` / `RepoSpec` / 库里的字符串（`"gitee"`/`"github"`）/ `None`，认不出的取值一律
    按 Gitee（与 `Platform.of` 同口径），**永不抛异常** —— 它服务的都是日志与给用户看的文案，
    那里最不该因为一个畸形取值再炸一次。
  - `memory.py`：`MemoryMiddleware` / `build_memory_middleware` / `refresh_memory` 增加
    `platform` 参数（`build_memory_middleware` 手上本来就有 `repo`，一路往下传），三个 marker
    与那条日志改用 `platform_label`。
  - `tools/pr_client.py` 新增 `missing_token_message(repo)`：两个客户端的「令牌未配置」文案
    由**一处**生成，平台名与两个字段名都从 `repo` 派生 ——
    `<平台小写>_token`（建会话入参）与 `<平台大写>_ACCESS_TOKEN`（.env 兜底键）。
  - `repo_cache.RepoSpec` 增加 `platform` 字段（`from_session` 带上），兜底域名改成
    `spec.platform.host`。
- **关键决策**：
  - **提示词改成中性措辞，而不是按平台渲染**（用户点）：`prompts.py` 的两处改成「按 **PR 的
    实况**」，`intent.py` 的两处写成「Gitee / GitHub」并列。理由：提示词是**静态文本、建会话
    时渲染一次**，中性写法永远正确、也不动渲染链路（按平台渲染要把平台接进
    `build_main_prompt`，收益只是措辞更贴切）。分类器那两处并列两个平台，是为了让用户说
    「把 GitHub 上的 PR 补进记忆」时也能命中。
  - `platform_label` 放在 `codeplatform.py`：它是**叶子模块**（`config` / `session` / `tools`
    都依赖它、它不依赖任何人），不会成环。
- **验证**：
  - 新增 **6 条**用例：`test_memory_gate.py` 3 条（闸门 / 盲区 / rework 无可修对象三条文案
    各自验「GitHub 仓库说 GitHub、且不出现 Gitee」，两条平台都跑）、`test_codeplatform.py`
    2 条（`platform_label` 认各种输入且不抛；`missing_token_message` 的平台名与字段名）、
    `test_repo_cache.py` 1 条（认证头的兜底域名跟着平台走）。
  - 全量 `pytest` **1052 passed**；`ruff check` 改动文件干净；提示词动过，故
    `scripts/check_prompts.py` **冲突扫描 0 条**。
  - 顺带确认 `sandbox/` 那批资产（skills / policies / setup.sh / git-askpass）本来就是
    「Gitee / GitHub」并列或平台无关的写法，不用改。
- **遗留**：注释与 docstring 里仍写着 Gitee 的地方按约定**不动**（那是设计说明）；
  `gitee_token` / `gitee_username` 这类**字段名与 DB 列名**仍带平台前缀（历史命名，
  改名要动 API 与迁移，本次不做）。

#### GitHub OAuth 登录（后端）：与 Gitee 对称的两个路由，顺带给两边都加上 `state`

- **动机**（用户口径）：GitHub 也要能登录；范围点：**只做后端这一半（A）**，前端登录入口
  先不做、**记成遗留项**；其余未点的按之前的推荐来（→ 加 `state`、GitHub scope 用 `repo`）。
- **改动**：
  - `api/routes/auth.py`：原来只有 Gitee 两个路由，且是「逐字一份」的写法。改成一份
    `_OAuthApp` 描述（端点常量 + 三件套）承载公共逻辑，四个薄入口各一行 —— 加平台不必
    再复制一遍拼参/换码/错误归一。新增 `/auth/github/authorize`、`/auth/github/callback`。
  - **`state` 两边都加**（防 CSRF）：`<时间戳>.<随机串>.<HMAC>`，**自签、不落存储**。
    不存服务端是权衡过的：多个 worker 之间没有共享内存，而 Redis 是可选件 —— 自签的
    state 无状态、跨进程跨重启都能验，代价只是多一个 HMAC。签名密钥用**该平台的
    `client_secret`**，于是 Gitee 签发的 state 在 GitHub 回调上也过不了，不必再加「这份
    state 属于哪个平台」的字段。有效期 10 分钟；校验失败**统一回一句 400**（不区分签名错 /
    过期 / 格式错 —— 分开说等于给探测的人送情报），而且**先验 state 再换码**，不把可疑的
    授权码外发。
  - `config.py` 加 `github_oauth_client_id` / `_secret` / `_redirect_uri`；`.env.example`
    补三行，并把「怎么建 GitHub OAuth App、回调地址必须一字不差、开发期建议填
    `http://localhost:5173/auth/github/callback`（vite 已把 `/auth` 代理到后端）」写在注释里。
  - `README.md`：API 表补上 GitHub 两个路由与回调参数；「GitHub 目前只支持
    `GITHUB_ACCESS_TOKEN`」那句改成实况（两个平台后端都就绪、**前端还没接**）。
- **关键决策**：
  - **GitHub 换 token 必须显式带 `Accept: application/json`**，否则回的是
    `application/x-www-form-urlencoded` 文本；而且授权码无效时它**回 200**、错误在 body 里
    （`{"error": "bad_verification_code"}`）—— **只看状态码会拿着 `access_token=None`
    往下走**。两条都单独处理，并各有用例钉着。响应不是 JSON 时回 502 且话里点明原因。
  - 两个平台的返回体**同一个形状**（`access_token` / `scope` / `expires_in`），前端不必按
    平台分支；GitHub 不回 `expires_in`，那里就是 `None`。
  - **`authorize` 也要求配齐 `client_secret`**（原来只要求 client_id + redirect_uri）：
    没有 secret 就签不出 state，跳出去也只会卡在回调那一步。这是本次唯一的**行为变更**，
    而现实中没有任何调用方（前端一直没接），所以没有兼容问题。
- **验证**：
  - 新增 `tests/test_auth_routes.py`（**14 例**，全程离线：路由里建客户端的那一步留了可替换
    的工厂，用例挂 `httpx.MockTransport` 截获）：两个平台的 authorize 跳转（地址 / scope /
    response_type / redirect_uri / state 都在）、三件套没配时的 500 措辞、GitHub 换码
    （断言请求头要了 JSON、表单字段齐全）、Gitee 不额外要 JSON、**200 带 error 当失败**、
    非 JSON 回 502、端点 4xx 透传、伪造 state / 跨平台 state / 过期 state 一律 400 且
    **不外发请求**、每次 authorize 的 state 都不同。
  - 全量 `pytest` **1046 passed**；`ruff check` 改动文件干净。
  - 走真实 `create_app` 确认四条 `/auth/*` 都挂上了（整站 lifespan 要连 Postgres，离线机器
    上跑不起来，所以只做到挂载这一层）。
- **遗留**：
  - **前端登录入口没做（本次明确不做）**：两个平台都没有按钮、没有 token 存储、建会话时
    也没带 `gitee_token` / `github_token`。所以**眼下实际生效的还是 `.env` 里的
    `GITEE_ACCESS_TOKEN` / `GITHUB_ACCESS_TOKEN`（兜底那份）**，OAuth 这条路目前只有
    curl 能走通。接前端时要一起定的两件事：token 存哪（`localStorage` / 只存内存）、
    以及回调页形态（现在回调直接回 JSON，弹窗里看不懂，得改成一段会
    `window.opener.postMessage` 的极小 HTML）。
  - **生产（Linux）的 `redirect_uri` 怎么填还没定**：后端**不托管前端**（没有 StaticFiles
    挂载），所以要么 nginx 反代 `/auth`、要么把 redirect 指到后端 8000 并处理跨源。
    开发期按 `.env.example` 里写的 `http://localhost:5173/auth/github/callback` 走。
  - GitHub 侧要用户自己建 OAuth App 并把凭据填进 `.env`，回调地址必须与
    `GITHUB_OAUTH_REDIRECT_URI` **逐字一致**（否则 GitHub 回 `redirect_uri_mismatch`）。
  - `state` 目前**没有绑用户会话**（自签即验、一次性靠过期）。等前端接登录、有了会话概念
    之后可以再收紧成「与本次会话的 state 一一对应」——现在的形态防的是「别人构造回调 URL
    骗你点」，跨站伪造那一步已经挡住了。

#### Redis 旁路状态：降级告警落盘 + 修掉「边写边读」时的帧号错位

- **动机**（用户报障）：控制台总是出现「Redis 上的 运行缓冲 不可用，本进程后续退回进程内
  实现」，但 Redis 明明连得上；想在日志里查，又**一个字都搜不到**。
- **排查结论**（只读 + 真机实测，脚本留在 `_bench/probe_redis_*.py`）：
  - 这句话有两个版本：启动期 `build_redis` 的「Redis 不可用（addr）：…；本次退回进程内实现」
    与运行期 `RedisBacked.run` 的「Redis 上的 <what> 不可用…」。**"运行缓冲" 两处都出现**，
    用户引的句子是两者的混合。
  - 落不了盘的原因在 `observability/logging.py`：文件 handler 按 **logger 名前缀**分流，
    `ai_coding_harness.redis_cache` 既不在 api 前缀也不在 agent 前缀里 —— 它只走控制台，
    窗口一关证据就没了。两个日志文件全文搜过，确实一条都没有。
  - 「连得上却降级」的真相是**认证**：本机 Redis 要 AUTH，不带密码的 `ping` 报
    `AuthenticationError: HELLO must be called with the client already authenticated…`。
    实测 ①不发 AUTH → 失败 ②带 `.env` 里的密码 → 成功 ③错密码 → 失败。
    `.env` 是进程启动时读一次，**补了 `REDIS_PASSWORD` 之后没重启**的后端会一直用旧那份。
- **改动**：
  - `observability/logging.py`：`_AGENT_PREFIXES` 补上 `ai_coding_harness.redis_cache` ——
    运行缓冲 / 熔断状态的连接与降级告警从此进 `agent.log`（不进 api.log）。
  - `server/run_store.py`：**帧的「取号 + 入列」改成一次事务**（`INCR`+`RPUSH`+`LTRIM`+
    两条 `EXPIRE` 走一个 `pipeline(transaction=True)`），**读侧的「长度 + 号」也放进一个事务**。
    并给 `_open_redis`（清旧帧 + 置号）加了事务，避免订阅者撞进「帧没了、号也没了」而被
    误判成「本轮已结束」。
- **关键决策**：这是**真 bug，不是告警的副作用**。读侧靠 `首帧号 = 号 - 长度 + 1` 反推，
  写侧两条命令之间那道缝（或读侧两条读之间夹进一次完整写入）都会让首帧号偏一号，游标跟着
  跳过去、**尾巴上的帧被丢掉**。真机实测过：订阅者拿到 `(2, 首帧)、(3, 次帧)`，`done` 帧不见了。
  - 没引 Lua：`MULTI/EXEC` 就能把「读到的两个值属于同一瞬间」这件事解决，少一层复杂度。
  - **已知窄缝（写在 `_read_redis` 的 docstring 里）**：`LRANGE` 仍在事务外（它是追加语义，
    晚读只会多拿新帧）。若正好在这次读之间触发截断（要一轮攒够 `RUN_BUFFER_MAX_FRAMES`，
    默认 2000 帧），首帧号会算高、下一轮轮询补齐 —— 截断与入列同在写事务里，窗口是微秒级，
    按当前用法不值当再引 Lua。
- **验证**：
  - 先写回归用例、**确认它在旧代码上失败**：`test_frame_numbers_survive_reading_while_a_frame_is_being_written`
    报 `[(2,'0'),(3,'1'),(4,'3'),(5,'4'),(6,'5')]` —— 帧号整体偏一，`'2'` 那帧真丢了。
  - 改完再跑：该用例通过；真机探针（`_bench/probe_redis_runhub.py`，连 127.0.0.1:6379）
    订阅期间拿到 **(1,2,3) 完整且编号正确**，`degraded=False`。
  - `tests/fake_redis.py` 补了两样语义：`round_trip=True`（每条命令让出一次事件循环，
    模拟网络往返 —— 不加这个，命令之间根本没有交错点，这类 bug 在测试里复现不出来）与
    `pipeline()`（命令排队、`execute()` 一口气跑完，模拟 `MULTI/EXEC` 的原子性）。
  - `tests/test_logging_pipeline.py` 新增 2 例：降级告警**必须**进 `agent.log`、**不许**进 `api.log`。
  - 全量 `pytest` **1032 passed**；`ruff check` 改动文件干净。
- **遗留**：
  - `.env` 里 `REDIS_PASSWORD` 已配好，**重启后端**才会生效；启动时若还看到那句告警，
    现在可以直接翻 `logs/agent.log` 看 `：` 后面的异常名与文本。
  - 顺手记一条相关的坑：`Settings` 的 `env_file=".env"` 是**相对进程 CWD** 解析的，
    所以后端必须从仓库根启动（`start.bat` / `start.sh` 都 `cd` 过去了）；换成从别处启动的
    部署方式（systemd 之类）要显式指定工作目录，否则 `.env` 读不到、Redis 会**静默地**不启用
    （那条路径连告警都不会打）。

#### 回退：整套「项目配置」（会话那份 + Nacos 远端 + 配置闸门）撤掉，改记遗留项

- **动机**（用户口径）：这套配置链路先不做了 —— 「配置这个做成遗留事项吧，先把本次和配置
  有关的代码回退掉」。范围点：① 三层一起撤；② 加一条反向迁移把三列 drop 掉；③ Go / Node
  的依赖源与缓存**也一并撤**，回退之后**单独重做**（用户原话：「回退之后的单独做 go/node 这部分」）。
- **撤掉的东西**：
  - **注入机制**：删 `sandbox/project_env.py`；`SandboxProvider` 去掉 `project_env` 参数与
    `_install_project_env`；`OpenSandboxProxy` / `OpenSandboxBackend.aexecute` 去掉
    「每条命令前 source `/ai_coding/.sandbox.env`」；`safety` 去掉该文件的读写保护；`Settings`
    去掉 `sandbox_env_path`。
  - **远端配置（Nacos）**：删 `sandbox/nacos.py`；`Settings` 去掉 `config_nacos_*`；
    `.env.example` 去掉那一段。
  - **会话字段与接口**：`sessions` 去掉 `sandbox_env` / `config_source` / `config_confirmed_at`；
    删 `dao.set_session_config` / `find_last_sandbox_env` / `find_last_config_source` /
    `repo_has_confirmed_config`；`Session` 去掉三个字段；`CreateSessionRequest` 去掉两个字段；
    `SessionDetail` 去掉 `sandbox_env_keys` / `remote_config`；删 `GET /sessions/sandbox-env`
    与 `PATCH /sessions/{id}/sandbox-env`（连同 `SandboxEnvHint` / `UpdateSandboxEnvRequest`）。
  - **配置闸门**：删 `sandbox/config_keys.py` 与 `server/service.py` 里的
    `CONFIG_GATE_*` / `_pending_config_request` / `_config_requirements` / `_scan_config_keys` /
    `_config_gate_message`；`need_config` 帧不再下发。
  - **前端**：新建对话页去掉「项目配置」文本域与 Nacos 四格；删配置弹框与 `need_config` 分支；
    `api.ts` 去掉 `createSession` 的两个可选参数、`getSandboxEnvKeys`、`updateSandboxEnv`、`ConfigItem`。
  - **Go / Node**：`Settings` 去掉 `sandbox_go_*` / `sandbox_npm_*`；`volumes.py` 去掉
    `build_go_cache_volume` / `build_npm_cache_volume`；`_runtime_inputs` 不再挂这两个卷。
- **保留的东西**：Maven 那一整套（`settings.xml` 阿里云镜像 + `.m2` 缓存卷 + setup.sh 摆位）
  原样保留；GitHub 支持、右侧栏代码面板、会话复用、记忆闸门等与本次无关的改动一律不动。
- **关键决策**：
  - **三条老迁移文件不删**（`d1e2b3c4a5f6` / `e2f3a4b5c6d7` / `f3a4b5c6d7e8`）：库里的
    `alembic_version` 已经指向它们，删文件会让 `alembic upgrade` 直接报错。改为**在上面加一条
    反向迁移** `b5c6d7e8f9a0`（drop 三列，downgrade 加回来；列能回来、**值回不来**）。
  - **本仓库没有 git**（`.git` 不存在），回退是手工改的 —— 动手前把待删的 8 个文件
    （3 个模块 + 1 份文档 + 4 个测试）备份到了工作区外
    `…\visualizations\…\_config-revert-backup\`，以防要捡回来。
  - `dataclasses` 的 `field` / `db.dao` 的 `Any` / `service` 的 `REWORK_INTENT` 等**因此变成未使用**
    的导入一并清掉（`REWORK_INTENT` 自身还在，`middleware/memory.py` 仍用它）。
- **验证**：
  - 全量 `pytest` **1029 passed**（撤掉 4 个测试文件、适配 8 个：卷、代理、provider、会话复用、
    记忆闸门、脱敏、workspace runtime）。
  - `ruff check` 改动文件：只剩 `db/models.py` 的 4 条**既有**告警（`__table_args__` 与引号注解），
    本次没新增。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过；`scripts/check_prompts.py` 冲突扫描 0 条。
  - 全仓再扫一遍 `sandbox_env` / `nacos` / `project_env` / `need_config` / `conda` 这些关键字，
    只剩 alembic 迁移文件按设计提到列名。
- **遗留**：
  - **项目配置（含远端配置中心与配置闸门）整体待重做**：这次的做法是「会话上存值 + 可选 Nacos
    拉一份 + 拼成 `.sandbox.env` 每命令 source + 没配过就拦」；重做时按新设计重新加列，不复活
    这三列。注意用户此前明确过的两条口径仍然成立：**配置由用户提供、平台不代为保管文件**；
    真要做远端，优先**配置中心**而不是往代码仓里塞文件。
  - **Go / Node 的依赖源与缓存也待重做**：原来是 `GOPROXY` / `npm_config_registry` + go/npm
    缓存卷，走的是 `.sandbox.env` 这套通道，跟着一起撤了。重做时建议直接落成**容器环境变量**
    （它们是静态值，不像用户配置那样会变），缓存卷仍照 Maven 的 `_cache_volume_name` 口径写。
  - 需要 `alembic upgrade head`（到 `b5c6d7e8f9a0`）与**重启后端**才生效。用户 `.env` 里残留的
    `CONFIG_NACOS_*` 因 `Settings` 是 `extra="ignore"` 而无害，可自行删掉。

#### 配置弹框保存 500 修复：`remote_config` 声明窄成 `dict[str, str]`，带 `keys` 列表就校验失败

> ⚠️ **本条随下面的配置回退一并撤销**（`remote_config` 连字段一起删了）。留在这里是记录当时
> 的排查结论：PATCH 的值已写库成功，炸在「回详情」那一步的 Pydantic 校验上。

- **动机**（用户报障）：配置闸门弹框里填完信息点保存，后端 **500**。
- **定位**（只读排查，`logs/api.log`）：`PATCH /sessions/{id}/sandbox-env` 的**值已经写库成功**
  （上一行日志「会话配置已更新…值 6 项」），炸在末尾「回详情」那一步 ——
  `sessions.py:126` 构造 `SessionDetail(remote_config=manager.remote_config_status(...))` 时
  Pydantic 报 `remote_config.keys: Input should be a valid string, input_value=[], input_type=list`。
  即 `api/schemas.py` 把 `remote_config` 声明成 `dict[str, str]`，而产出方
  `session/manager.py::remote_config_status()` 里 `keys` 是**键名列表**。用户看到的是「保存失败」，
  实际已经存进去了。
- **改动**：
  - `api/schemas.py`：`SessionDetail.remote_config` → `dict[str, Any] | None`（`keys` 是列表，
    `error` 可缺省），注释补上 `keys` 的含义。
  - `frontend/src/api.ts`：同一字段 → `Record<string, unknown> | null`（`App.vue` 只读 `.status`
    / `.error`，放宽不影响）。
- **关键决策**：**放宽响应模型**而不是把 `keys` 从状态里删掉 —— 配置弹框要拿它标
  「Nacos 里已经有这些」，删了就少了这条信息；值仍然不回显。
- **验证**：
  - 新增回归用例 `tests/test_config_gate.py::test_patch_survives_remote_status_with_keys_list`
    （远端状态带 `keys` 列表时 PATCH 仍 200、键名原样返回、值不回显）。**此前 9 例全绿是因为
    `FakeManager` 一律返回 `None`，走不到带 `keys` 的那条路**。
  - 全量 `pytest` **1070 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
- **遗留**：无（需 `alembic upgrade head` 与重启后端才生效）。

#### 配置闸门：coding 时没配置过就拦下弹框，展示「要配哪些」并自动重发

> ⚠️ **本条已随上面的配置回退整体撤销**（闸门、弹框、`PATCH /sandbox-env` 都已删除）。
> 留在这里是记录当时的口径与判据，重做时可直接参考。

- **动机**（用户口径）：配置内容要让用户知道 —— Nacos 这些"**需要 coding 的时候**给用户
  **弹框配置**，并**展示要配置的内容**"。
- **口径**（用户点）：① 会话没配置过就拦；② **后端拦**；③ 保存后**自动重发**；④ 标记**来源**。
- **改动**：
  - `server/service.py`：`chat()` 在运行时就绪后、派发之前加**配置闸门**。intent ∈
    {`coding`, `rework`} 且「会话没填过值 ∧ 远端没拉到 ∧ 这个仓库从没确认过」→ 拦下本轮：
    记一条 assistant 记录 → 先发说明文本（`CONFIG_GATE_MARKER` 开头）→ 再发
    `{"type": "need_config", "config": [...]}` → 结束本轮（与记忆闸门同一套"停下并说清"）。
  - 新增 `sandbox/config_keys.py`：**只读**扫描命令（grep/find，扫 `application*.yml` 的
    `${VAR}`/`${VAR:默认值}` 与 `.env.example` 类模板）+ 解析成清单（**没有默认值 = 必填**、
    `.env.example` 里空值 = 必填、同一个键取严的那条）。
  - 弹框清单 = 扫到的模板键 + 标记「必填 / 远端已有 / 已设置」（**只有键名与出处，没有值**）。
  - `sessions` 加 `config_confirmed_at`（alembic `f3a4b5c6d7e8`）：用户填过值或点过
    「不需要配置」都盖章；**按仓库继承**（`dao.repo_has_confirmed_config`）—— 同一仓库的
    后续会话不再被弹，避免每开一个会话问一次。
  - 新增 `PATCH /sessions/{id}/sandbox-env`：会话内合并保存值 / 远端位置（`confirm_only=true`
    表示"这个项目不需要配置"），保存后**内存里那份会话对象同步更新**（下一轮闸门与注入直接读它）。
  - 前端：`need_config` 事件 → 弹框（逐项：键名 / 出处 / 必填标记 / 远端已有标记；口令类
    输入框按密码显示）+ 「补充其他键」文本框 + 「不需要配置 / 稍后 / 保存并继续」；
    保存后**自动重发**刚才那条消息（`send()` 里记下原文）。
- **关键决策**：
  - 「配置过」= 会话上有值 ∨ 远端拉到 ∨ 该仓库确认过（含"不需要配置"）：一次确认是对
    **这个仓库**的表态，不必每会话重问。
  - 清单来源优先**扫项目模板**（唯一可信来源），扫不到就空表 + 手填框；
    **扫模板失败不影响拦不拦**（判据是"配置过没有"）。
  - 值仍然**不回显、不进仓库**；`config_confirmed_at` 只记时刻、不记内容。
- **验证**：
  - 新增 `tests/test_config_gate.py`（9 例：模板解析与必填判定、扫描命令只读且安全引用目录名、
    四种"已配置过"不拦、缺配置时清单带标记、扫描失败仍拦、闸门文案、PATCH 合并 /
    confirm_only / 值不回显）。
  - 新增 `tests/conftest.py`：测试**统一关掉远端配置中心**（开发者本机 `.env` 开了 Nacos 时，
    provisioning 会真去连一次，把 20ms 的用例拖挂 —— 实测踩到过）。
  - 全量 `pytest` **1069 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
  - ⚠️ 三条迁移要一起跑：`d1e2b3c4a5f6`（会话上的项目配置）、`e2f3a4b5c6d7`（远端位置）、
    `f3a4b5c6d7e8`（确认时刻）。
- **遗留**：
  - 会话内**手动**改配置的 UI 入口还没做（`PATCH` 已就绪）—— 现在两个入口是"新建对话时填"
    与"闸门弹框"；已确认过"不需要配置"的仓库想再配，暂时只能新建会话。
  - 弹框清单依赖沙箱里的工作区（扫模板要它）；工作区没建好时清单为空，只能手填或点"不需要配置"。

### 2026-09-27

#### 远端配置中心（Nacos）：平台按会话去拉，与「会话上那份」合并后注入沙箱

- **动机**（用户口径）：项目配置的归宿最好是**远端配置中心**，平台去拉；选型定 **Nacos**，
  并要求确认 Python / Go / JS-TS 是否支持、以及账号密码怎么让用户告知。
- **结论（先确认，再动手）**：
  - **语言不构成障碍**：平台拉回来注入的是**环境变量**（Java `${DB_HOST:…}`、Python `.env`/`os.environ`、
    Go `os.Getenv`、Node/TS `process.env` 都吃得到），所以不需要各语言都有 Nacos 客户端 ——
    只有"让被测项目自己去连 Nacos"那种用法才吃客户端，而 Python / Node 的客户端成熟度确实参差。
  - **平台侧不引 SDK，走 HTTP OpenAPI**：平台的活只是"按位置拉一份文本"，少一个依赖、少一个版本坑。
  - **凭据分两类**（用户口径：混合）：Nacos **账号密码由平台 `.env` 配（只读账号）**，
    用户只告知"配置放哪"（地址 / 命名空间 / 分组 / dataId，都可留空用平台推的默认）。
- **实测（用户的 Nacos 是 3.x，`/nacos/` 回 "Console default port is 8081"）**：
  - `GET /nacos/v1/cs/configs`、`/nacos/v2/cs/config` → **404 `No endpoint`**（3.x 里已不存在）；
  - `GET /nacos/v3/client/cs/config` → **200**，可**匿名**读；远端没有这份时回
    `{"code":20004,"message":"resource not found"}`；
  - `GET /nacos/v3/admin/cs/config` → 403（admin 接口要账号）；客户端接口**只读**（POST/DELETE 405）；
  - 登录入口：3.x `/nacos/v3/auth/user/login`（403=账号不对）、2.x `/nacos/v1/auth/users/login`、
    1.x `/nacos/v1/auth/login` 都还在。
  - 综上：**实现按「v3 优先、v1 兜底」**；多数 3.x 部署读配置**根本不需要账号**，账号只在
    "客户端读也被锁"时才用得上。
- **改动**：
  - 新增 `sandbox/nacos.py`：`NacosSource`（位置，不含凭据）、`resolve_source()`（平台按会话推
    默认、会话可覆盖）、`fetch_config()`（v3 优先 → v1 兜底；`20004`/404 = 远端确实没这份 → 空；
    401/403/5xx/连不上 = 失败抛 `NacosConfigError`）、`clean_config_source()`（只认四个字段，多写报错）、
    登录按 v3 → v1 依次尝试（**只有 404 才换下一个**，凭据被拒当场报错）。
  - `config.py` + `.env.example`：`CONFIG_NACOS_ENABLED / _ADDR / _USERNAME / _PASSWORD / _DATA_ID / _TIMEOUT_SECONDS`
    （默认关闭；**ADDR 是 API 端口 8848，不是 3.x 控制台的 8081**）。
  - `sessions` 加一列 `config_source`（JSONB，位置四个字段；alembic `e2f3a4b5c6d7`）；
    同仓库下一次建会话沿用上次那份（`dao.find_last_config_source`）。
  - `session/manager._compose_project_env()`：**平台默认 → Nacos → 会话上那份**（后者覆盖前者）；
    拉取失败只跳过远端那份并把原因记进 `remote_config_status`（`SessionDetail.remote_config` 可见，
    前端在状态条上提示一句）。
  - API：`POST /sessions` 收 `config_source`（多余字段 400）；`SessionDetail` 回 `remote_config`。
  - 前端：新建对话加「远端配置中心（可选）」四个输入（地址/命名空间/分组/dataId），
    placeholder 按仓库地址实时推默认值（`gitee__owner` / `repo` / `sandbox-env`），全留空即平台默认。
  - 文档：`docs/project-env-keys.md` 增「放到远端配置中心（Nacos）」一节（命名约定、控制台怎么建、
    环境靠命名空间、优先级与失败纪律）；README 增远端说明。
- **关键决策**：
  - 位置按会话推（`namespace=<平台>__<owner>`、`group=<repo>`、`dataId=sandbox-env`），
    带平台前缀是为了同名 owner 在 Gitee/GitHub 上不撞车；要分环境就换命名空间。
  - 账号只在平台 `.env`（只读），**不进会话、不进沙箱、不回显**；用户填的那四个位置字段本身不含凭据。
  - 失败纪律与 PR 查询一致：**拉不到 ≠ 远端没有**，宁可吵也不静默降级。
- **验证**：
  - 新增 `tests/test_nacos.py`（13 例）：位置推导与覆盖格式、禁用即整条跳过、字段校验、
    v3 读成功并解析、`20004` 视为"远端没这份"、v3 404 → v1 兜底、两个版本都缺 = 空、
    登录取 token 且口令只走表单体、登录被拒即报错（不跨版本掩盖）、连不上/5xx 必抛错、读被拒带凭据提示。
  - **真机验证**（用户的 Nacos 3.x，经 ssh 隧道 `127.0.0.1:8848`）：平台推的位置正是
    `ns=gitee__zzpfordevelopment group=wanqi-mall dataId=sandbox-env`；会话覆盖命名空间生效；
    远端没有这份时返回 **0 个键**（而不是报错）。"远端有这份"的路径等控制台建好配置后再跑一次即可。
  - 全量 `pytest` **1060 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
  - ⚠️ 要跑 `alembic upgrade head`（`d1e2b3c4a5f6` + `e2f3a4b5c6d7`）后端才能建会话。
- **遗留**：
  - 远端那份的**解析结果没有缓存**：Nacos 挂了这一轮就只用另外两份（日志与详情里会写明）。
    要不要"缓存上次拉到的内容、挂了也能起"待定。
  - 平台读的是**已发布的配置**；Nacos 的配置变更通知（长轮询 / 推送）没接 —— 现有链路是
    "建会话 / 重连沙箱时拉一次"，够用。
  - 之前记的「远端配置中心」优化点由本条落地；仍待办的是"把配置**加密**托管"（库里那份仍是明文，
    与 token 同一口径）。

#### 项目配置改由**用户提供**并存在会话上（平台不保管文件）+ 记「远端配置中心」优化点

- **动机**（用户口径，连着三条）：① 那份配置**不能放平台代码仓库**；② 它是**用户的**测试库与
  测试数据，**不能由平台维护**；③ 理想形态是放**远端**、平台去拉，但眼下没有远端服务 ——
  先按「用户填、随会话走」实现，远端那条记成优化点。
- **改动**：
  - **数据**：`sessions` 加一列 `sandbox_env`（JSONB，可空；明文，与 token 同一口径），
    alembic `d1e2b3c4a5f6`（`server_default` 不涉及，老会话零迁移）。
  - **API**：`POST /sessions` 增 `sandbox_env`（用户填的那份）；新增
    `GET /sessions/sandbox-env?repo_url=…` 只回**键名**（供「新建对话」提示"上次填过哪些键"）；
    `SessionDetail` 增 `sandbox_env_keys`（同样只回键名）。键名不合法直接 400。
  - **沿用上次**：建会话时用 `dao.find_last_sandbox_env(owner, repo)` 取该仓库**上一次**那份打底，
    用户这次填的覆盖它 —— 口令这类不必每次重打；值只在服务端用，**不回显给前端**。
  - **沙箱注入**：`compose_env(settings, user_env)` = 平台默认（Go/npm 源与缓存路径） + 用户那份
    （后者覆盖前者）；`session.manager._provision` 从会话上取，provider/proxy 照旧一路带到
    容器里那份 `/ai_coding/.sandbox.env`。空值**丢弃**（不注入成空串）——
    `${DB_HOST:127.0.0.1}` 这类占位符只在变量根本不存在时才用默认值。
  - **前端**：新建对话加「项目配置（可选）」输入框（一行一条 `KEY=value`）+ 上次键名提示；
    地址填完 `@blur` 拉一次提示。
  - **删掉上一版的设计**：宿主机 `.env.sandbox` / `.env.sandbox.d/<owner>__<repo>.env`
    与 `SANDBOX_ENV_FILE` / `SANDBOX_ENV_DIR` 全部移除（`.gitignore` 里那两条也撤），
    `.env.sandbox.example` 挪成 `docs/project-env-keys.md`（**只讲键名与怎么捞**，不含值）。
- **关键决策**：
  - 归属：值由用户提供、存在会话上；平台只做「校验 → 合成 → 送进沙箱」，不保管宿主机文件。
  - 沿用而不是回显：同仓库上次那份在**服务端**打底（用户只填要改的），避免口令进前端。
  - 空值即"不注入"（也即"沿用上次"）：把"用项目默认值"这条留给项目自己。
  - 键名非法一律 400：静默丢弃会把用户的笔误变成"配了但没生效"。
- **验证**：
  - 新增 `tests/test_project_env.py`（10 例：键名校验与空值语义、单引号转义、平台默认为 Go/npm、
    用户覆盖平台、**用 Git 自带 bash 真跑**生成的片段、`aexecute` 包装与"检查跑在原命令上"）
    与 `tests/test_sandbox_env_route.py`（3 例：**提示只回键名、口令不出现**、空结果、非法地址 400）；
    `tests/test_session_reuse.py` 的假件补上 `sandbox_env` / `find_last_sandbox_env`。
  - 全量 `pytest` **1047 passed**；`ruff` 改动文件干净；`npm --prefix frontend run build` 通过。
  - ⚠️ **要跑 `alembic upgrade head`**（`d1e2b3c4a5f6`）后端才能建会话 —— 模型里有这一列。
- **遗留 / 优化点**：
  - **远端配置中心**（用户点名记下）：理想形态是把用户的测试环境清单放远端、平台按仓库去拉 ——
    多台部署共用一份、不用逐台填。眼下没有远端服务，先落在会话上；将来接上远端时，
    只要把 `compose_env` 的"用户那份"换成拉取结果即可，注入链路不用动。
  - 库里的这份是明文（与 token 同一口径）；要加密/托管到密钥服务一并算远端那条。
  - **会话内改配置**没做（本期只做"建会话时填"）：要改就新建会话（会自动沿用上次那份）。

#### 沙箱支持 Go / Node 项目：国内源 + 依赖缓存卷 + 项目配置按环境变量注入

- **动机**（用户两条口径）：① 被测项目不止 Python / Java，**Go 与 JS/TS 也要能在沙箱里顺畅构建、
  测试** —— 镜像自带 Go 1.25.5 / Node 22，但每次构建都从国外源拉依赖（与当初 Maven 那个问题同源）；
  ② 项目配置要**按 example 建好后以环境变量注入沙箱**，且对四种语言都成立（Python 用 `.env`、
  Java 用 `application.yml` 的 `${DB_HOST:…}`、Go 用 `os.Getenv`、Node/TS 用 `process.env`）。
- **改动**：
  - **依赖源与缓存**（与 Maven 那套对称）：`config.py` 新增 `SANDBOX_GO_PROXY`（`goproxy.cn`）、
    `SANDBOX_NPM_REGISTRY`（`registry.npmmirror.com`）与 Go / npm 各自的
    `*_CACHE_ENABLED / SHARED / PREFIX / MOUNT_PATH`；`sandbox/volumes.py` 抽出
    `_cache_volume_name` 并新增 `build_go_cache_volume`（挂 `/ai_coding/go`）、
    `build_npm_cache_volume`（挂 `/ai_coding/.npm`），默认跨会话共享；
    `session/manager._runtime_inputs` 一并挂上 —— 现在建沙箱挂四个卷：`project` + `.m2` + `go` + `.npm`。
  - **项目配置注入（口径 C）**：新增 `sandbox/project_env.py`（解析 `.env` 风格文本 →
    `platform_env()` 给 Go/npm 源与缓存路径 → 与宿主机 `.env.sandbox` 合成 `compose_env()`
    （**后者覆盖前者**）→ `render_source_file()` 渲染成 `export K='v'`）；
    `SandboxProvider._install_project_env()` 在 create 与 reconnect 都写 `/ai_coding/.sandbox.env`（0400）；
    `OpenSandboxBackend.aexecute()` 在**每条命令前** `. <该文件> 2>/dev/null || true;`
    —— 权限检查仍在**原命令**上做，前缀在检查之后才加。
  - **安全边界**：`/ai_coding/.sandbox.env` 进 `PROTECTED_READ` / `PROTECTED_WRITE`，
    命令层加 `\.sandbox\.env` 拒绝规则（模型自己去 `cat` 会被拦，且不会发往沙箱）。
  - 资产与文档：新增 `.env.sandbox.example`（**含「怎么填」的四步说明** + 按 Java / Python / Go /
    Node 分段）、`.gitignore` 加 `.env.sandbox` 与 `.env.sandbox.d/`、`.env.example` 补
    `SANDBOX_ENV_*` 与 Go/npm 项；`docs/workspace-directories.md` 与 `docs/sandbox-image-guide.md`
    同步（新目录、缓存卷、凭据流程）；提示词通用规则加一条（配置由平台注入、四种语言怎么读、
    不要自己造配置文件、不要把值写进仓库、依赖源已配好）。
  - **多仓库怎么分**（用户口径 a）：项目配置两层 —— 全局 `.env.sandbox` 打底，
    `.env.sandbox.d/<owner>__<repo>.env` 覆盖它（`project_env_files()` 决定顺序，
    `session/manager._provision` 按**本会话仓库**取；provider/proxy 把合成结果当成
    `env` 的同级参数往下传，沙箱重建时照原样重写）。
- **关键决策**：
  - 项目配置走**「写文件 + 每条命令 source」**，不塞容器 env：容器 env 建好就固定，改配置不生效
    （与这次 GitHub 令牌踩到的是同一类坑）；写文件后**改完重连即生效**，不必重建容器。
  - 平台默认与用户配置**合成同一份**文件（用户键写在后面即覆盖），少一层机制、少一处漂移。
  - 缓存卷默认**跨会话共享**（与 Maven 一致）；存储不支持多机同挂就把 `*_CACHE_SHARED=false`。
  - 关掉某个生态的缓存开关时**不设**它的路径变量（用镜像默认），避免「以为在共享缓存、其实在容器里」。
  - 平台注入的 registry / GOPROXY **优先于仓库自带的 `.npmrc`**（环境变量 > 项目配置），
    这是用户确认接受的加速口径。
  - 按仓库分文件（而不是把值塞进请求或前端表单）：多仓库各填各的、平台侧维护，改动最小；
    将来要开放给端用户，再把值改成建会话时传入即可（同一条合成链路）。
- **验证**：
  - 新增 `tests/test_project_env.py`（10 例）：解析口径（注释 / 空行 / `export` / 引号 / 值里带 `=` /
    非法键）、单引号转义、`load`（未配或文件不存在都返回空）、平台默认含 Go/npm 源、
    用户覆盖平台、**用 Git 自带 bash 真跑**生成的片段（值含空格、`$`、单引号仍原样取出）、
    `aexecute` 包装、无配置时不包装、权限检查跑在原命令上（去 `cat` 那份文件会被拦且不发往沙箱）。
  - `tests/test_sandbox_volumes.py` +2、`tests/test_sandbox_provider.py` +2（写文件与 chmod、
    无配置时什么都不写）、`tests/test_workspace_runtime.py` 更新为「四个卷 + 项目配置随建/重连带上」、
    `tests/test_sandbox_proxy.py` 加「重建时项目配置一并重写」。
  - 全量 `pytest` **1043 passed**；`ruff check` 改动文件干净；`scripts/check_prompts.py` 冲突扫描 **0 条**。
- **遗留**：
  - **沙箱内的真实验证待你在有网环境跑**（我这边外网被挡）：`go env GOPROXY GOMODCACHE`、
    `npm config get registry`，以及 `go mod download` / `npm ci` 在**容器重建后**是否仍命中缓存。
  - 只认配置文件、完全不读环境变量的工程（个别 Go / Java 项目），仍需 agent 自己按仓库里的 example
    生成配置文件（值从环境变量取）；平台只保证「值已在环境变量里」+「依赖源与缓存已配好」。

#### 代码平台双支持：仓库地址判平台，PR 工具内部按 Gitee / GitHub 分流

- **动机**（用户口径）：支持 GitHub。git 工具**还是同一套**，内部按平台分类 —— gitee 走 gitee
  的逻辑、github 走 github 的（工具名、入参、返回文案对模型完全一致）。
- **改动**：
  - 新增 `codeplatform.py`（**叶子模块**，不 import config/tools，避免成环）：
    `Platform` 枚举（`GITEE` / `GITHUB`，带 `host` / `pr_segment` / `default_git_username`，
    认不出的取值一律回落 Gitee）+ `RepoSpec`（owner/repo/token/git_username/platform，
    `platform` 默认 Gitee、`git_username` 留空按平台填默认）+ `parse_repo_url` /
    `parse_pr_url`（按域名判平台；PR 链接段 `pulls`（Gitee）与 `pull`（GitHub）都认）。
  - PR 客户端拆三层：`tools/pr_client.py`（共用：`PrLookupError`、`PrClient` 协议、
    `pulls_or_raise`、`find_open_pull`、三个可读格式化），`tools/gitee_client.py`（原
    `GiteeClient` 搬来），`tools/github_client.py`（新）。
  - `tools/git.py`：五个工具 `create_pr` / `comment_pr` / `list_pr_comments` /
    `get_pr_info` / `close_pr` 的**名字、入参、返回文案一律不变**，内部只认
    `build_git_client(settings, repo)` 这一个分叉点；`get_pr_info` 贴链接时**按链接判平台**
    （可以查另一个平台的 PR，用该平台配置里的令牌）；`GiteeRepo` 保留为 `RepoSpec` 的旧名
    （既有 import 与测试无需改动）。
  - GitHub 的差异全封在客户端里：`Authorization: Bearer` + `Accept` + `X-GitHub-Api-Version`；
    **评论走 issues 端点**（`/issues/{n}/comments`，GitHub 的 `/pulls/{n}/comments` 是 diff 行级
    review 评论）；关闭 PR 用 **body** 传 `{"state":"closed"}`；`state` 没有 `merged` —— 要合并态
    就查 `closed` 再按 `merged_at` 本地过滤；`mergeable=null` 如实回「未知（平台还在计算）」。
  - 会话与库：`sessions` 新增 `git_platform`（`server_default='gitee'`，老会话零数据迁移，
    alembic `c7d1a54e8f21`）；`POST /sessions` 支持 `github_token`；`manager.create_session`
    按平台取令牌（请求优先、`.env` 兜底）与 git 用户名。
  - **沙箱层做成平台无关**：环境变量 `GIT_TOKEN` / `GIT_USERNAME`、单个 `git-askpass`、
    凭据文件 `.secrets/git_token`（替换原 `GITEE_TOKEN` / `GITEE_GIT_USERNAME` / `gitee-askpass` /
    `.secrets/gitee_token`）；`safety/permissions.py` 的 askpass 保护名与
    `docs/sandbox-image-guide.md` 同步。
  - 记忆链路（`MemoryMiddleware` 的闸门、`server/service.py` 的收尾回填、`save_repo_memory`
    的前置检查）只换装配点，逻辑一行未改。
  - 文案中性化：`agent/prompts.py`（「平台边界」段重写）、两个 SKILL、两份必须逐字一致的
    记忆模板、`tools/memory.py`、`memory_reconcile` 注释、README、`.env.example`（新增
    `GITHUB_*`）、前端新建会话的 placeholder；`docs/gitee-pr-api-reference.md` →
    `docs/code-platform-pr-api.md`（补 GitHub 差异表）。
- **关键决策**：
  - 平台由**仓库地址**判定并记在会话行上（一个会话一个平台）；PR 链接另行判定（工具入参级）。
  - 只加一列 `git_platform`（用户口径）：token / username 继续用既有两列（注释改为平台通用）
    —— 少一列、少一次数据迁移，老行由 `server_default` 兜住。
  - 沙箱**不感知平台**：一个会话只装一个令牌，askpass 与凭据文件保持单一；平台差异只留在
    工具层，避免两套脚本各写一遍。
  - GitHub 凭据本次只走 `.env`（请求也可带 `github_token`），**不做 OAuth 登录**（用户口径）。
- **验证**：
  - 新增 `tests/test_codeplatform.py`（平台判定回落、域名解析、`RepoSpec` 默认值、工厂分派）
    与 `tests/test_github_client.py`（7 个方法的 method+path+认证头、**issues 评论端点**、关闭用
    body、`merged` 本地过滤、fork 同名分支过滤、查 PR 失败必抛 `PrLookupError`、无令牌回可读
    错误、`merged_at` / `mergeable=null` 的渲染口径）。
  - 全量 `pytest` **1029 passed**（基线 1007 + 新增 22）；`ruff check` 全部改动文件干净；
    `scripts/check_prompts.py` 冲突扫描 **0 条**；`npm --prefix frontend run build` 通过。
  - 数据库现状：`alembic current` = `b41d7e2c9f30`，本次只差 `c7d1a54e8f21`
    —— **必须跑 `alembic upgrade head` 才能建会话**（模型已带非空列）。
- **遗留**：
  - **GitHub OAuth 登录**：按用户口径本次不做。GitHub 凭据只来自 `.env` 的
    `GITHUB_ACCESS_TOKEN`（或请求带 `github_token`），前端暂无 GitHub 登录入口。
  - **真实联调未做**：github.com 的真实 API（建 PR / 评论 / 读评论 / 关 PR）需要在有网环境
    用测试仓库走一遍；本机只用 `httpx.MockTransport` 钉了客户端行为。
  - **代码面板（repo_cache）仍不带令牌**：私有仓库（Gitee / GitHub 同样）在右侧栏「代码」
    面板里 clone 不下来 —— 现状如此，本次未变。
  - 自建实例（GitHub Enterprise / Gitee 私有部署）不在支持范围，域名认不出会直接报错。

#### 已完成的待办清单不再占地方（跑完就不画，没做完才留）

- **动机**（用户要求）：针对已经完成的任务，`write_todos` 的任务列表不再展示在界面上 ——
  每轮跑完气泡下面都留着一份全绿的清单，读过一次就是噪音。
- **改动**：
  - 新增 `frontend/src/todos.ts`：`todosAllDone()`（整棵树含子项都 completed）+ `shouldShowTodos(items, finished)`。
  - `frontend/src/App.vue`：待办块那行的 `v-if` 从 `todosPart(m).length` 换成 `showsTodos(m)`
    （= `shouldShowTodos(todosPart(m), Boolean(m.at))`）。
  - 顺带修一处既有 bug：折叠箭头的 `:class` 用的是 `isTodoOpen(m, k, i)`，而 `:aria-expanded`
    用的是 `hiddenCount + i` —— 会话被 `visibleTail` 截断时（hiddenCount > 0）箭头与展开状态
    对不上，统一成绝对下标。
- **关键决策**：
  - 口径：整棵树（主项 + 子项）全完成 **且这一轮已收尾** 才不画；只要还有一项 pending /
    in_progress 就照旧显示 —— 那是「哪儿没做完」的唯一线索。
  - 为什么带「已收尾」这个条件：只看全绿的话，模型中途把全部标成 completed 的那一瞬间列表会
    闪一下消失，随后又加一项再冒出来。收尾时间（`Message.at`）在流结束后才落上、历史消息一律
    带着它，所以实时与历史是同一套判定。
  - 数据层不动：后端照旧把待办树写进 `extra.parts`（刷新回放、记忆、审计都要），只在展示层不画。
- **验证**：
  - 判定真值表 18 项（Node 24 直跑 `frontend/src/todos.ts`）：空树、运行中全绿（不许闪）、
    收尾全绿（不画）、收尾有 pending / in_progress / 孙辈没完 / 父项没完（都画）、未知状态不当作
    完成，并用这一轮真实落库的那棵树（record 369）正反两面各验一次。
  - 真实页面核对：可见窗口（最后 60 条消息）里共 12 份待办树、其中 9 份全完成 —— 改后页面只渲染
    **3 份**，与数据侧预测一致，剩下的正是没做完的那几轮。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过；全量 `pytest` **1007 passed**。
- **遗留**：无。

#### 子任务挂靠不再因主控改写条目文字而漂移（认亲改「归一化 + 锚定」）

- **动机**（用户反馈）：最近一轮里 `write_todos` 子任务开始时挂在对应父任务下，跑完就漂移。
  查真实日志与落库记录（record 367 / 369）定位：`stream.py::tree()` 用**父项文字逐字相等**
  当挂靠键，而主控每次 `write_todos` 都是全量重写、还习惯把结果追加进条目文字
  （`生成用户资料编辑实施方案` → `…（plan_id=36）`；`实施用户资料编辑代码并提 PR` → `…（PR #9）`）。
  子智能体一返回主控就更新清单，文字一变匹配失败 → 子清单被当成「挂不上落点」追加成**树尾容器**
  （`规划子任务` / `编码子任务`），界面上就是「跑完就漂移」。文字始终没变的那一项
  （`代码审查并呈报审查结论`）一直挂得住 —— 正好反证机制。
- **改动**（`src/ai_coding_harness/stream.py`）：
  - 新增 `_todo_text_key()`：条目文字的挂靠键 —— 去空白 + 去结尾的补充说明（连续多层的
    `（…）` / `[…]` / `【…】`）+ 去首尾标点。
  - `tree()` 改为按**下标**挂靠，新增 `_locate()`：认亲顺序 = **锚**（上次挂上那一项的文字）→
    **委派那一刻**那串，各自先 逐字 → 归一化（**唯一命中**才算）→ 前缀（短的那串 ≥4 字、
    **唯一命中**才算）；都认不出时，若主清单**条数没变**，沿用锚记下的位置。
  - 挂上就记锚 `(那一项当时的文字, 下标, 当时条数)` —— 父项随后再被改写也不失联。
  - 真正认不出（主清单为空、已全部完成、那一项被整条删掉、条数变了、归一化撞键）照旧走
    树尾容器兜底：宁可位置难看，也不能把子项挂到别的父项下。
- **关键决策**：
  - 「唯一命中」是硬条件：两串归一到同一个键时不猜，落容器。
  - 前缀认亲设 4 字下限，避免 `改代码` 被当成 `改代码并提交` 的父项。
  - 只在条数没变时按位置沿用 —— 项被拆开/删掉就不猜。
  - **行为变更**：主项被整句改写（`改代码` → `重构代码`）时不再落容器，改为沿用原位
    （旧口径见 `tests/test_stream_v3.py::test_child_todos_survive_a_renamed_main_item`，
    本次按新口径改写为 `test_a_renamed_main_item_keeps_its_children`）。
- **验证**：
  - 新增 5 个用例（尾部补充、无括号续写按前缀、整句改写沿用原位、同键歧义不猜、短标题不走
    前缀）+ 按新口径更新 1 个既有用例；`test_stream_v3.py` + `test_plan_events.py` +
    `test_chat_stream_route.py` **98 项全过**。
  - 用这一轮**真实的**标题 / 委派序列回放修复后的 translator（16:26 → 17:12）：最终树是 5 条
    主项，planner / coder / reviewer 三份子清单各归其父项，**兜底容器 0 个**；而当时实际落库的
    record 367 里 `规划子任务` / `编码子任务` 是漂在树尾的。
  - 全量 `pytest` **1007 passed**；`ruff check` 两个改动文件干净。
- **遗留**：新一轮里主控「先委派、后落清单」时（17:01 那轮 17:03 委派 reviewer、17:10 才写
  自己那份清单），子清单仍会以树尾容器出现 —— 那是另一条（委派时主清单为空，没有父项可挂），
  本次按用户选择（只做 a+b）未处理。

#### 前端流式卡顿：正文分块渲染 + 追底不再硬拽 + 长会话只渲染最近 60 条

- **动机**（用户反馈）：「每次开始执行任务的时候，前端界面都比较卡，上下拉、点按钮都会这样」。
  只读排查后定位到根因：流式期间每 100ms 会把「正在长的那条回复」**整段重渲染**一遍
  （marked 解析 → hljs 高亮 → DOMPurify 消毒 → 整段 innerHTML 替换），紧接着
  `scrollTop = scrollHeight` 又强制一次重排版。实测一帧 15~17ms（其中强制重排版 8~9ms、
  且随会话变长上涨），按 10 帧/秒折算每秒 150ms 以上都花在这一件事上 —— 滚动与点击撞上这些帧
  就掉帧。另外 `flush()` 里是**无条件**追底：用户往上翻历史时每 100ms 被拽回底部。
- **改动**：
  - 新增 `frontend/src/streamblocks.ts`：流式正文分块。块长到 `BLOCK_SOFT_LIMIT`（1500 字符）
    就在**安全切点**封口（空行；且不落在未闭合的代码围栏里、不切在列表项/表格行/引用段之前、
    右侧不缩进）；封口块的内容不再变，模板上的 `v-memo` 自然跳过它，每帧只重渲染还在长的
    那一小块。同时导出 `fenceClosed()` 供正文渲染判定「围栏还没闭合」。
  - `frontend/src/App.vue`：`Part` 的 text 支加 `sealed`（`pushText` 只往未封口的块续写，
    `flush()` 里调 `splitLongText`）；新增 `textGroups()` —— **连续**的 text 块合成一组渲染成
    一个气泡，切开之后视觉上仍是一条连续正文（块间距与段间距一致），待办隔开的仍是两个气泡
    （与改动前一致）；`scrollToBottom()` 加 `nearBottom()` 判断（离底部 48px 内才追）与同 tick
    合并，`flush()` 先读「是否贴底」再决定追不追；会话只渲染最近 `VISIBLE_TAIL = 60` 条，更早的
    折叠为顶部「显示更早的 N 条」按钮（`key` 与待办折叠 key 一律用**绝对**下标，避免窗口滑动时
    状态串位；切会话时展开量复位）。
  - `frontend/src/markdown.ts`：围栏**没闭合**（流式中的代码块）先按纯文本渲染，闭合那一帧再
    上色 —— 否则每帧都是 hljs 缓存未命中（实测 4.6ms/块/帧）。
  - `frontend/src/style.css`：`.md-block` 的首尾边距规则、`.load-earlier` 按钮样式。
- **关键决策**：
  - 切点宁可少切也不错切：找不到安全点就让这一块继续长（切进围栏/表格/列表会渲染错，而流式
    视图是用户正在读的东西）。
  - 分块只改渲染、不改数据：`textGroups()` 保证「切开」与「没切开」观感一致；落库与刷新回放
    路径完全不变。
  - 阈值取 1500：实测单帧中位 3000→1.40ms、1500→0.80ms、1000→0.70ms、600→0.60ms，再往下收益
    就平了，而块数持续上涨（13KB 正文：3000→5 块、1500→12 块、600→15 块）。
  - 节流仍保持 100ms：分块之后单帧已经很轻，调到 200ms 只会让流式显得更顿。
- **验证**：
  - 分块逻辑 30 项断言（Node 24 直跑 `frontend/src/streamblocks.ts`）：围栏开闭判定、各类不安全
    切点、切完内容一字不丢、连续流式 400 段后每块围栏成对。
  - 真实数据对比（把真实会话的 180 条消息铺进 DOM，其中一条 13 KB 回答按每帧 40 字符喂入）：
    普通回答整条重渲染中位 **4.90ms → 1.40ms**（合计 1680ms → 537ms）；代码型回答（尾部是一个
    还在长的 Java 代码块，43KB/1104 帧）中位 **17.30ms → 2.60ms**，单帧 >16ms 的掉帧帧数
    **594/1104 → 5/1104**，合计 19.5s → 3.3s。
  - `npm --prefix frontend run build`（含 `vue-tsc`）通过；全量 `pytest` **1002 passed**。
- **遗留**：
  - 没用 `content-visibility: auto`：它按估算尺寸参与滚动高度，而每个 flush 都要读 `scrollHeight`
    追底，容易让滚动位置跳；改用「只渲染最近 60 条」这条确定性路径。
  - 日常仍跑 Vite dev（`start.bat` / `start.sh` 走 `npm run dev`）：dev 运行时比生产包贵一截，
    是否改成 `build + preview`（会失去 HMR）待定。
  - `/records` 仍是一次性全量返回（这次会话 500 KB 级 JSON），首屏还得等它。

#### 意图判定换四关瀑布：口令 → JEV（SystemOne）→ 大模型 → 关键字口径（默认值改 `coding`）

- **动机**（用户口径）：用 **JEV**（决策型模型，SDK：`typesafe-sdk`）替掉原来「大模型直接判」那一档；
  顺序定成 **关键字 → JEV → 大模型 → 默认值**，且**默认值从 `qa` 改成 `coding`**。
- **改动**：
  - `server/intent.py`：`detect_intent` 改成四关（新增 `jev=` 参数）。新增 `IntentDecider` 协议与
    `JevDecider`：一个 `Choice` 问题，候选就是能判的那 6 个意图（`_LLM_INTENTS`，**不含 sync**），
    `instructions` **直接复用 `_system_prompt()`**（与第三关逐字同一份口径）；拿回 `.choice` /
    `.confidence` / `.probabilities` 并把置信度与次高候选记进日志。`IntentDecision` 加 `confidence`；
    新增 `SOURCE_JEV="jev"`；`DEFAULT_INTENT` 由 `qa` 改 **`coding`**；新增 `build_jev_decider(settings)`
    （没启用 / 没配 key / 装不上 SDK → `None`，跳过这一关，服务照起）。SDK **懒加载**。
  - `config.py` + `.env.example`：`INTENT_JEV_ENABLED`、`TYPESAFE_API_KEY`、`TYPESAFE_BASE_URL`、
    `TYPESAFE_MODEL_NAME`（回落 SDK 的 `TYPESAFE_DEFAULT_MODEL`，再回落 `jev-latest`）、
    `TYPESAFE_TIMEOUT_SECONDS`（默认 10）。
  - `server/service.py`：`HarnessService(intent_jev=…)`；`_detect_intent` 与 `_rejudge_intent`（`handoffTask`
    的重判）都把 JEV 传进去 —— 重判仍跳过口令档，先问 JEV 再问大模型。
  - `api/app.py`：lifespan 里造判定器，退出时 `aclose()`（SDK 的异步客户端要收）。
  - 文档：README 补「四关瀑布」与环境变量清单；`docs/business-flow.md` §二 从「三档」改成「四关」。
- **关键决策**：
  - **用 `Choice` 而不是 `Score` / `Noul`**：7 个意图是互斥枚举，`Choice` 直接给标签 + 置信度 +
    每个候选的概率（都进日志，比大模型自述的一句话更硬）。
  - **候选里没有 `sync`**：与第三关同一口径 —— 直接执行路径（不经智能体、直接在沙箱跑命令）
    只能由口令产出，从源头堵死「模型猜出个直接执行」。
  - **`instructions` 复用 `_system_prompt()`**：两关判的必须是同一件事，口径一漂就会
    「换个模型结论就变」，事后从日志也看不出是哪一层判的。
  - **任何失败都只降级**：JEV 没配 / 超时 / 报错 / 回认不出的标签 → 交给下一关；构造失败也不让
    服务起不来（SDK 懒加载，与 `tools/web_search.py` 同一态度）。
  - **默认值改 `coding` 的连带影响**（这是用户明确要的口径，写在这里免得日后被当成 bug）：
    两个模型都判不出来**且**关键字没命中时，这一轮按 `coding` 走 —— 也就是 `orchestrate` 形态、
    要过记忆闸门、可能真的派 planner/coder；升级前这种句子落 `qa`（只答不做）。
    关键字口径本身没变：`classify()` 命中弱词仍按词表判，只有「一条都没命中」才落默认值。
- **验证**：全量 `pytest` **1002 条全过**（改前 991，新增 11 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` **冲突 0 条**（本次不动提示词，跑一遍确认没被牵连）。新增用例：
  口令命中不问 JEV / JEV 判出来不再问大模型 / JEV 抛错退到大模型 / JEV 回认不出的标签同样退 /
  两个模型都挂 → 关键字口径 → 默认值 `coding` / 弱词与最近几轮历史会传给 JEV / `force_llm` 跳过口令
  但仍走 JEV / 没配 JEV 时是升级前那两条路 / 真 SDK 造一次判定器（钉住构造参数名）/ 没 key 或关掉时
  返回 `None`。**并用真 `.env` 实测**：`build_jev_decider(Settings())` 拿到 `JevDecider`
  （`model=jev-latest`、base 已配、timeout=10），说明包在、参数名与真 SDK 对得上。
- **遗留**：
  - **真调 JEV 没在沙箱里跑通**（我这边网络受限，只有本地回环可用）：JEV 那一关只验到「判定器能造出来 +
    失败能干净降级」。真 key 的联调在你那边 —— 重启后端跑一轮，日志里应出现
    `意图分析：intent=… source=jev …` 与 `JEV 判定：intent=… p=0.93 次高 plan=0.05 model=… tokens=…/…`。
  - **低置信度照用**（只记日志，不做额外动作）；要改成「低置信度再问一次大模型」是后续一步的事。
  - 配置改动要**重启后端**才生效。

#### 加 `docs/coding-sequence.drawio`：给 draw.io 原生打开的那份时序图（含首版打不开的修正）

- **动机**（用户要求）：Mermaid 源码只有装了插件的编辑器才看得到图；用户要一份**能用 draw.io 打开/编辑**的。
- **改动**：
  - 新增 `scripts/make_drawio.py`：把 coding 时序图（与 `docs/business-flow.md` §五 同源的数据）算成
    draw.io 的 mxGraphModel XML —— 8 条泳道（表头 + 虚线生命线）、41 条消息（调用实线 / 返回虚线、
    自调用带折线）、6 条阶段灰带、3 个 `alt` / `loop` 方框（`else` 用虚线分隔）。`--out` 可改落点。
  - 新增产物 `docs/coding-sequence.drawio`：draw.io 桌面版/网页版直接打开即可编辑，
    也能在 draw.io 里另存为 `.drawio.svg` 嵌文档。
  - `docs/business-flow.md` §五 末尾加一条互链：改图时 Mermaid 与 `.drawio` 一起改。
- **首版打不开的修正**（用户实测反馈「不好使」，2026-09-27 同日改）：本机找不到 draw.io Desktop
  （注册表 / 常见安装路径 / Start Menu 都没有），网页版在后台标签里跑不起来，**没能在这里复现用户看到的现象**；
  按最可能的三个原因改掉并重新生成（1330×2348，119 个 mxCell）：
  1. **消息文字不再当边标签**：draw.io 的边标签默认压在线上，41 条消息按 34px 行距会互相压 —— 现在每条消息的
     文字是**独立一格**（白底、无边框，落在箭头上方），与 Mermaid 的排法一致；行距 34 → **44**。
  2. **补文件头**：加 `<?xml version="1.0" encoding="UTF-8"?>` 与 `<mxfile version=…>`，避免不同版本/工具
     对编码与格式的猜测。
  3. `else` 分隔线改成 `endArrow=none`（原来会多画一个箭头）。
- **兜底路线**（不依赖我生成的 XML）：draw.io 里 `Insert → Advanced → Mermaid…`，把
  `docs/business-flow.md` §五 那段贴进去，draw.io 自己会转成可编辑图形。
- **关键决策**：
  - **几何用脚本算，不手写坐标**：41 条消息的 y、阶段带的跨度、分组框的包围盒都由同一份数据推出来 ——
    手写 XML 迟早错一行，而错一行在 draw.io 里就是一条线跑偏。
  - **用「浮动边 + 显式端点」画消息**（`sourcePoint` / `targetPoint`，自调用再加 `Array` 折点），
    而不是把边挂在两个表头之间：前者能把每条消息钉在**指定的 y**，后者会被 draw.io 自动路由拉走。
  - **数据两处同源、人工同步**：Mermaid（给人读）与 `.drawio`（给 draw.io 打开）各存一份，改图时一起改；
    没有做「从 Mermaid 反解」的自动转换 —— 那要写个 Mermaid 解析器，收益不抵复杂度。
- **验证**：`scripts/make_drawio.py` 跑通（首版 78 → 修正后 **119 个 mxCell**）；产物用 `xml.etree` 解析：
  格式合法、页面 1330×2348、66 个顶点（8 表头 + 8 生命线端点 + 6 阶段带 + 3 分组框 + **41 个消息文字格**）
  ＋ 51 条边（8 生命线 + 2 个 `else` 分隔 + **41 条消息**，与图上的消息数一致）、坐标齐全；
  `ruff check scripts/make_drawio.py` 干净；全量 `pytest` **991 条全过**（只加脚本与文档，未动运行时代码）。
- **遗留**：**没有在 draw.io 里真打开验证过** —— 本机找不到 draw.io Desktop（可能是 VS Code 的
  Draw.io 插件、或装在我枚举不到的位置），`app.diagrams.net` / `viewer.diagrams.net` 在这个环境的
  后台标签里起不来（受限上下文中 `fetch` 都不可用，无法驱动其前端）。**下一步要看用户打开后的具体现象**
  （打不开报什么错 / 打开了但排版乱 / 中文乱码）才能对症再改。

#### `docs/business-flow.md` 增补：coding 模式时序图（从用户访问到结束）

- **动机**（用户要求）：总览图与 coding 环路图回答的是「有哪几条分支」，看不出**谁在什么时候找谁**、
  以及 SSE 与运行是两条线这件事。补一张时序图。
- **改动**：`docs/business-flow.md` 新增 §五「coding 模式时序图」，Mermaid `sequenceDiagram`，
  参与者从左到右是：用户（浏览器）→ 前端 → 后端 API → RunHub → service → 中间件 → 主控模型 →
  子智能体 → 工具 → 沙箱 → Postgres → Gitee。六段（`rect` 分组）：
  ① 建会话（复用活跃会话 / 后台建沙箱 / 轮询到 ready）② 发消息（凭据拒收、`RunHub.start`、
  同会话并发 409、SSE 按序号订阅）③ 开工前（意图分类 → coding、记忆刷新与闸门拦放行、按形态裁工具）
  ④ 跑这一轮（模型 ↔ 工具循环 → `get_unexecuted_plan` 分叉：有方案直接 coder / 没有先 planner
  且**不等确认** → coder 改码提 PR → reviewer 回评）⑤ 收尾（先呈结论，再 `save_repo_memory`，
  平台轮末打底与落库，最后 done）⑥ 之后（终止 / 刷新接回 / 删会话 / 人工合并 / rework 另起一轮）。
  图后附三点读图提醒（只有 ① 是「用户访问」、SSE 与运行两条线、平台与模型各写哪部分）。
  为放这张图，原 §五～§七 顺延为 §六～§八，正文里的交叉引用（`§五` → `§六`）一并改掉。
  同时修正了 §四 环路图：两条入口（「要方案」vs「直接做」）画清楚 —— 后者先查方案、没有就 planner
  落库后**不等确认**，与前一轮刚改的 coding 口径对齐。
- **验证**：代码块围栏 3 对已核对；章节号与交叉引用（§二/§三/§四/§六）全对上；图中每条消息都对着
  实际调用复核过（`sessions.py` 的建会话与 workspace 轮询、`chat.py` 的 start/stream、`RunHub` 的
  409、`middleware/memory.py` 的闸门、`register.py` 的 `get_unexecuted_plan`、coder 的 create_pr、
  reviewer 的 comment_pr、`save_repo_memory` 的写盘打底）。本次只改文档，未动代码。
- **遗留**：本机没有 Mermaid 渲染器（要联网 + Chromium），**没有渲染出图看过** —— 只做了语法与
  逻辑复核；在 Gitee 仓库页或带 Mermaid 插件的编辑器里打开 `docs/business-flow.md` 即可确认观感。

#### coding 意图改口径：明确要动手时主控先查方案，没有就 planner→coder→reviewer 一路跑完

- **动机**（用户口径）：原来「明确要动手」那条路是**跳过 planner、直接派 coder**，由 coder 自己
  `get_unexecuted_plan`；取不到方案才回报、主控改派 planner，然后**停下等用户确认** —— 明明用户
  已经说了「直接做」，却还要再确认一次。改成：**主控先查方案**，有就直接派 coder，没有就先派
  planner 出方案、**落库后不等确认**，接着 coder → reviewer，这一轮跑完为止。
- **改动**：
  - `tools/register.py`：`ORCHESTRATE_FORM` 的工具表加**只读的** `get_unexecuted_plan`
    （主控据此判「直接派 coder」还是「先派 planner」）；`build_main_tools` 里 plan 工具池的构建
    条件从「有 `storePlan`」放宽成「有 plan 类工具」（`_PLAN_TOOL_NAMES`），池子整口建、按表挑。
    **方案的增删改仍不给主控**：`storePlan` / `getPlan` / `updatePlan` / `updatePlanSteps` /
    `deletePlan` 一件都不在表里（那是 planner 与 plan 形态的活）。
  - `agent/prompts.py` 的 `coding` 指令重写：**明确要动手** → ① 主控先 `get_unexecuted_plan`
    查一次；② 有方案 → 直接派 `coder`；③ 没有 → 派 `planner` 落库（**不停下等确认**）→ 接着派
    `coder` 实施到提 PR → 再派 `reviewer` 审查 → 结论呈给用户，本轮结束（不自动回环修复）。
    **没明确要动手**那条路**保持不变**：planner 出方案 → 停下等「确认实施」→ coder → reviewer。
    coder 的兜底也不变（取不到方案就停下回报，不硬做；主控据此改派 planner）。
- **关键决策**：
  - **查方案由主控做、而不是让 coder 先撞一次**：省掉「派了 coder → 它说没有方案 → 再改派 planner」
    那一圈；`get_unexecuted_plan` 是纯只读查询，给主控没有越权风险。
  - **planner 落库后不确认**：用户已经用「实施 / 直接做 / 确认实施」表达过「现在就去改」，
    再停一次等于把同一句话说两遍。方案照旧落库（`storePlan`），方案面板上用户仍看得到它。
  - 非明确路径不动：只是描述需求或问怎么做时，仍然「先出方案、等确认」——那是唯一能让用户
    在动手前拦一道的地方。
- **验证**：全量 `pytest` **991 条全过**（改前 989，新增 2 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` **冲突 0 条**。新增/更新的用例：`orchestrate` 的完整工具清单里多出
  `get_unexecuted_plan`（`test_profiles` / `test_tool_registry` 两处精确清单都跟着更新）；
  新用例钉住「主控能查未执行方案、但拿不到 `storePlan` 等方案 CRUD」；`test_prompts` 里把
  coding 两条分支的断言改成新口径（先查方案 / 落库不等确认 / 接着 coder / 再派 reviewer /
  中途不停下来问用户），并保留「没明确 → 停下等确认」那条。
- **遗留**：
  - 提示词工具子集是**进程启动时装配**的：改完要**重启后端**；已在跑的老会话建议新开一个。
  - 「明确要动手」现在会一路跑到 PR 建好 + 审查完，中途没有人能拦一下那份方案 —— 这是用户
    明确要的口径；要反悔就把它改回「planner 落库后停下等确认」。
  - 顺手修了一处**外部引入的语法错误**：`server/intent.py` 末尾多出一个半截空函数
    （`async def detect_intent_by_jev():`，函数体为空），会让整个包 import 失败 —— 已删除。

#### 新增 `docs/business-flow.md`：从建会话到一轮结束的流程图（含各处分叉）

- **动机**：端到端链路散在 `api/routes/`、`server/service.py`、`server/intent.py`、
  `agent/middleware/`、`agent/profiles.py` 五处，读代码才能拼出「一条消息进来会走哪条路、
  在哪些地方分叉」。用户要一张业务视角的流程图。
- **改动**：新增 `docs/business-flow.md` ——
  ① 一张 Mermaid 总览图（建会话 → 发消息 → 凭据拒收 / 意图分类 → 运行宿主与 409 → 工作区 →
  直接执行(sync) 或派智能体 → 记忆闸门 → 模型↔工具循环 → 子智能体编排 → 收尾落库与记忆打底 →
  结束后的四种去向：继续问 / 终止 / 刷新接回 / 删会话，以及「人工合并后下一轮刷新状态」）；
  ② 三张速查表：意图判定三档 + 七个意图的形态/工具/子智能体、记忆闸门四种组合、仓库记忆四个写入时机；
  ③ coding / rework 的编排环路小图（planner → coder → reviewer → 用户点问题 → rework 回路）；
  ④ 失败与降级分支表（含「消息含凭据」「同会话并发」「沙箱建不出来」「Gitee 查不到」「模型全败/熔断」
  「本轮越界」「Redis 不可用」「DB 不可用（含 130s 超时这条已知问题）」「刷新接回」「用户终止」）；
  ⑤ 按流程顺序的文件索引（哪一步落在哪段代码）。README 的 `docs/` 说明同步提到它。
- **关键决策**：
  - **画「代码实况」而不是「设计意图」**：图里每一步都标了落在哪个文件，改流程时连同本文一起改。
  - **用 Mermaid 而不是 ASCII**：Gitee 仓库页与带 Mermaid 插件的编辑器能直接渲染；同时刻意写成
    **扁平 flowchart**（不用 subgraph）—— 跨 subgraph 的前向引用在部分渲染器里会把节点摆错组。
- **验证**：`docs/business-flow.md` 的代码块配对已核对（2 对围栏）；文中每个分叉都对着代码复核过
  （`service.chat` 的出口与分流、`_direct_sync`、`RunHub.start` 的 409、`middleware/memory.py` 的闸门表、
  `profiles.py` + `register.py` 的形态/工具表、`_MEMORY_WRITE_FORMS` 的打底口径）。
  本次只加文档，未动代码：全量 `pytest` **989 条全过**（与改动前一致）。
- **遗留**：本机没装 Mermaid 渲染器（要联网 + Chromium），所以只做了语法与逻辑复核、**没有渲染出图看过**；
  有 Mermaid 插件的编辑器里打开即可确认观感。

#### Redis 支持密码：`REDIS_PASSWORD`（+ URL 缺 scheme 的兜底与日志脱敏）

- **动机**：用户指出 Redis 配置里少了密码项（.env 里已加 `REDIS_PASSWORD`）。核对时另外查出两件事：
  - `REDIS_URL=127.0.0.1:6379` 这种**没写 scheme** 的写法会让 redis-py 的 `parse_url` 直接抛
    `ValueError: Redis URL must specify one of the following schemes (redis://, rediss://, unix://)`
    → 被 `build_redis` 接住 → **静默退回进程内**（只在日志里留一行）——「配了 Redis 其实没生效」
    这类问题最难发现；
  - 实测 `from_url(url, password=…)` **不会覆盖 URL 里的密码**（URL 里那个赢），不处理就会出现
    「改了 `REDIS_PASSWORD` 却不生效」。
- **改动**：
  - `config.py`：新增 `redis_password`（`REDIS_PASSWORD`）。
  - `redis_cache.py`：新增 `normalize_url`（缺 scheme 补 `redis://`）、`strip_password`、`mask_url`；
    `default_client_factory` 的口径定死：**显式 `REDIS_PASSWORD` 优先**（两边都写就先把 URL 里那份剥掉），
    **没配就不传 `password`**（空串会被 redis-py 当成真密码去 AUTH）；日志与报错里的地址统一走
    `mask_url` —— **密码绝不进日志**。
  - `.env.example`：补 `REDIS_PASSWORD=`，并写明「URL 不写 scheme 也行」「密码别塞进 URL（URL 会进日志）」。
- **关键决策**：
  - **密码单独一个键、优先级高于 URL**：显式的键更好排障，也避免「两个地方都能写」带来的歧义；
    只写在 URL 里的那种老写法仍然有效（没配 `REDIS_PASSWORD` 时就用 URL 里的）。
  - **缺 scheme 自动补而不是报错**：那是很自然的写法，为它让人读一段 redis-py 的报错不值当；
    归一化后地址会打码进日志，看得到实际用了什么。
- **验证**：`tests/test_redis_cache.py` 从 6 条扩到 **14 条**；全量 `pytest` **990 条全过**
  （改前 982，新增 8 条）；`ruff check` 改动文件干净。新增用例：密码确实进 `connection_kwargs`；
  两边都写时以 `REDIS_PASSWORD` 为准且**用户名保留**；只写在 URL 里的密码不丢；缺 scheme 能被归一化
  （用真 redis-py 断言 host/port）；没配就不传 `password`；`strip_password` / `mask_url` 的边界
  （IPv6 方括号、没有用户名、解析不了的原样返回）；**URL 带密码时日志里只有 `***`**、明文一个字节都不出现。
  - **并用真 `.env` 实测**：`build_redis(Settings())` 连上了用户本机的 Redis（`connected = True`），
    日志那行是 `已连上 Redis，运行缓冲与熔断状态走 redis://127.0.0.1:6379`（不含密码）。
  - 顺手修了一处**测试卫生**问题：`_settings()` 改成 `Settings(_env_file=None)` —— 默认会读本机 `.env`，
    实测把真实密码渗进了用例（断言失败时被原样打进 pytest 输出）。
- **遗留**：
  - **要重启后端**才生效（配置在进程启动时读）；重启后运行缓冲 / 会话运行权 / 熔断状态三样都走 Redis。
  - 密码是明文放在 `.env` 里的（与 POSTGRES_* 同一口径，不进版本库）；Redis 侧的 ACL / TLS（`rediss://`）
    没做，需要的话再说。

#### 仓库记忆两个刷新点：收尾按本轮 PR 回执记账 + 刷新记忆归入 `analysis`

- **动机**（两条都是当天真实故障，日志可查）：
  - **收尾漏记**：PR #7 在 `00:03:19` 建好（`create_pr` 回执里就有 `…/pulls/7（编号 7）`），
    但那一轮主控**一次 `save_repo_memory` 都没调**。隔天 `10:08` 用户说「按 P2 评审意见修改」，
    记忆闸门照记忆判定「仓库没有待合并的 PR」→ 把 `rework` 拦下了（闸门只看记忆里记着的 PR，
    记忆里没有 #7，于是判成「没有可修的对象」）。
  - **用户点名刷新没人接**：`10:10:36` 那条 15 字消息被判成 `intent=qa`（理由「刷新仓库记忆属于
    查看仓库信息」）→ qa 形态**没有 `save_repo_memory`** → 模型拉完 `get_pr_info` 想交接，被
    「重判仍是本形态」挡回来，整轮对记忆什么都没做。
- **改动**：
  - **刷新点 1（coding / review 收尾）**：`agent/prompts.py` 的 `_MEMORY_WRITE_BACK`（coding /
    review / rework 共用）收紧两条 —— ①「变更记录」的**编号与链接一律取本轮真实来源并原样引用**
    （coder 回报里的 `create_pr` 回执 `PR 已创建：<链接>（编号 N）` / `PR 已存在，直接复用：…
    （编号 N，未新建）`，审查轮取 `get_pr_info` 的返回），**不许凭印象编号、不许自己拼链接**；
    ② 新增**收尾自检**：本轮建过 PR 而提交的记忆里没有那一条 = 这轮记忆没写完；回报里缺编号/链接
    时先补齐再写（coding 轮用 `task` 让 coder 把回执原样重发一次，审查轮用 `get_pr_info`）。
  - **刷新点 2（用户主动要求）**：**归入 `analysis` 意图**（`server/intent.py`）——
    把「刷新仓库记忆 / 刷新记忆 / 更新仓库记忆 / 同步仓库记忆 / 整理仓库记忆」等口令挂到
    analysis 规则的 ``strong`` 上；`sync` 的 excludes 加「记忆」（与既有「同步机制 / 同步锁」
    同一口径），否则「同步一下记忆」会被 sync 的**直接执行**路径抓走（那条路不经智能体，
    记忆一个字都不会动）；LLM 那一档的 analysis 说明与分界也补上「**要求更新**记忆归 analysis、
    只是**查看**记忆归 qa」。`agent/prompts.py` 的 analysis 指令加一节「用户点名刷新记忆时」：
    先 `get_pr_info(<仓库地址>, state="all")` 拉全量 → 记忆里没有的 PR 补进「变更记录」（编号/链接
    取列表原文、日期取创建日期、写不实的留空不编）→ `save_repo_memory` 整篇写回 → 回复里给账并
    点名「还开着而记忆里原本没有」的 PR；同时给「analysis 不写变更记录」那条纪律留一个显式例外。
- **关键决策**：
  - **刷新点 1 不额外查 Gitee**：PR 信息就在 `create_pr` 的回执里（已核对 8 次真实调用，形状稳定），
    coding 轮**零额外调用**；只有回执缺项时才让 coder 重发一次（一次 `task`，比猜或漏记便宜）。
  - **不新开意图/形态，归入 analysis**：analysis 本来就有 `get_pr_info` 与 `save_repo_memory`
    这件组合，而且天然只读、天然不在记忆闸门里（刷新记忆恰恰发生在仓库还有未合入 PR 的时候 ——
    那时 plan / coding 会被拦下，这一轮必须放行）。
    （先加过一版独立的 `refresh_memory` 意图 + `FORM_ALIASES["refresh_memory"] = "analysis"`，
    **同日按用户口径撤回**：多一个意图等于多一份分类面与装配登记，收益为零。）
  - **「查看」与「更新」的分界写进两层**（口令词表 + LLM 分界说明）：只是**看**记忆内容的仍归 qa，
    只有**要求更新**才走这一档 —— 否则「仓库记忆里怎么说的」会被配上写工具。
  - 平台侧仍不做「强制」（比如 `save_repo_memory` 拒绝写一份缺本轮 PR 的记忆）：那要在平台侧
    跟踪「本轮建过 PR」，比提示词复杂得多；本次按提示词层做，硬保证留作后续选项。
- **验证**：全量 `pytest` **982 条全过**（改前 972，新增 10 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` **冲突 0 条**。新增用例：口令命中「刷新仓库记忆 / 刷新一下仓库记忆 /
  更新仓库记忆 / 同步仓库记忆 / 整理仓库记忆」→ 全部 `analysis`（且 `source=keyword`，
  一次模型都不调）；「同步一下记忆 / 同步一下仓库记忆」不再落到 `sync` 的直接执行路径，而
  「同步仓库」「同步代码」「拉取代码」仍是 `sync`；「仓库记忆里怎么说的 / 记忆里有哪些 PR」
  仍是 `qa`；**任务类型表里没有多出任何意图**（`refresh_memory` 不存在），analysis 不在
  `DIRECT_INTENTS` 也不在 `BLOCKING_FORMS`；分类提示词里带上与 qa 的第二条分界；三条收尾指令里都钉了
  「编号/链接取 `create_pr` 回执」「不许凭印象编号」「收尾自检：本轮的 PR 必须落到变更记录里」。
  README 的意图表与 `docs/workspace-directories.md` 的记忆写入形态同步更新。
- **遗留**：
  - **提示词与意图规则是进程启动时加载的**：改完要**重启后端**；已在跑的老会话建议新开一个。
  - 自然措辞（「把 Gitee 上没记的 PR 补进记忆」）走 LLM 那一档；**分类模型不可用**时回落到关键字
    口径，只有那 10 个口令能被认出来 —— 其余写法会落回 qa（那一轮又写不了记忆）。
  - 刷新点 1 仍是**提示词层的要求**：模型若整轮不调 `save_repo_memory`，本轮 PR 依然会漏记
    （平台轮末只刷文件头，不补正文）。要硬保证得做上面那条「平台侧强制」。

### 2026-09-26

#### 引入 Redis（可选）：多 worker 下的运行缓冲 / 会话运行权 / 熔断状态

- **动机**：兑现 CHANGELOG 里那两条「上多 worker 时这份缓冲要挪到 Redis」（RunHub 那条与熔断
  那条）。三样旁路状态原先都在进程内：RunHub 的帧缓冲（刷新后落到别的 worker 会拿到空流、
  `cancel` 也找不到那一轮）、`RunInProgress` 的**进程内**互斥（两个 worker 能同时跑同一会话 ——
  两个容器写同一个工作区）、模型熔断计数（各记一份等于没有熔断）。
- **改动**：
  - 新增 `redis_cache.py`：`build_redis`（`REDIS_URL` 留空 = 不启用；建好先 `ping` 验一次；
    **建客户端失败（没装 redis 包 / 地址写错）与连不上同一口径**：没声明必需就记 warning 返回
    `None`，`REDIS_REQUIRED=true` 才启动失败）+ `RedisBacked`（「一次失败即永久退回进程内」的
    包装 —— 否则 Redis 半死时每帧都白等一次超时）。
  - 新增 `server/run_store.py`：`RunBuffer` / `RunLock` 两个协议 + 内存/Redis 两套实现。键
    `harness:run:<session_id>:{seq,frames,done,cancel,lock}`，全部带 TTL；**帧号由 `INCR` 发**
    （`LTRIM` 丢老帧之后 `LLEN` 与帧号当场脱钩，拿下标当帧号前端续传就会重复或跳帧）；运行权
    `SET NX EX` + 续期；跨 worker 终止靠 `cancel` 标记，由持有者轮询到之后自己取消。
  - `server/runs.py`：帧改存 buffer；`task` 句柄仍在本地注册表（取消与退出必须够得着），
    **本进程的轮次仍走事件驱动**（不加轮询延迟），只有订阅别的 worker 的轮次才轮询；`start` /
    `cancel` / `is_running` / `shutdown` 改成 async（路由相应 `await`）。
  - 新增 `agent/circuit.py`：`CircuitStore` + 内存/Redis 实现。状态机仍只有 `middleware/fallback.py`
    一份（`_may_try` / `_note_primary` 改成 async），默认那份**仍是模块级单例** —— 各角色各 new
    一个中间件实例，状态跟着实例走就串不成一份了。
  - 装配：`api/app.py` lifespan 里建客户端并注入（**三样一起换**：只换一半更危险）；`config.py`
    与 `.env.example` 加 `REDIS_*` / `RUN_*`；`pyproject.toml` 加 `redis>=5`。
- **关键决策**：
  - **Redis 是可选件**：`REDIS_URL` 留空 = 与之前逐字相同的行为（现有用例语义一条没改）。
  - **降级优先于报错**：旁路状态存不进去不该弄丢一轮对话；第一次失败即整体降级（不再每条命令
    重试）。`REDIS_REQUIRED=true` 给多 worker 部署留了「宁可起不来」的口径 —— 静默退回进程内
    意味着「你以为共享的东西其实各 worker 一份」，比启动失败更难查。
  - **取消分两条路**：本进程的立即 `cancel`（用户不用等轮询）；跨 worker 的只能留标记，由持有者
    在 `_watch` 里轮询到后自己取消，外加丢掉那轮的缓冲（与本地取消对齐）。默认 0.2s 一轮。
  - **缓冲有上限与 TTL**（默认 2000 帧 / 1 小时），键全带前缀，不会无限堆积。
  - 已知窄缝写在 `RedisRunLock` 的 docstring 里：续期是「`GET` 看是不是我的 → `EXPIRE`」两条命令，
    理论上可能刚好过期并被别人抢走后、把**别人**的锁续一次（后果是接管推迟最多一个 TTL）；
    持有者进程被 kill 后锁最多存活一个 TTL（默认 120s）。
- **验证**：全量 `pytest` **972 条全过**（改前 951，新增 21 条）；`ruff check` 改动文件干净；
  `scripts/check_prompts.py` 不涉及（未改提示词）。替身见 `tests/fake_redis.py`（内存版 Redis +
  手推时钟），**两个 `RunHub` 实例共用一份 = 两个 worker**，于是跨进程语义在没装 Redis 的机器上
  也能验：跨 worker 追流、游标接回、第二个 worker 被拒、锁过期后能接管、另一个 worker 看得见
  running、**跨 worker 终止真的停住且丢掉缓冲**、帧号在截断后仍连续、缓冲 TTL 到期读作空闲、
  Redis 报错降级到进程内；熔断侧另钉了副本语义、跨实例共享、降级、以及「别的 worker 开的闸
  这一轮连主导都不撞」。`build_redis` 的四条路径（没配 / 连不上 / 必需 / 没装包）用注入的假
  工厂验。
  - **`redis` 包已装**（conda 环境 `aicoding`，实测 8.1.0），并用**真客户端**跑了一次
    `build_redis`：本机没有 Redis 实例，链接 1s 超时 → 记一条 warning → 返回 `None`
    （即「退回进程内」这条降级路径在真库上也是这个行为）。
- **遗留**：
  - **没有真 Redis 的端到端验证**：本机没有 Redis 实例，所以「连上之后」的读写、
    以及「两个进程各跑一个 worker」的真场景只在替身（`tests/fake_redis.py`）上验过。
    有实例之后建议手动冒烟一次：
    `REDIS_URL=redis://127.0.0.1:6379/0` 起服务 → 发一轮 → reload 页面看能不能接回。
  - **本次不含 sticky 路由**：沙箱句柄仍在各 worker 里（同一会话落到别的 worker 会另建一个容器，
    工作区靠命名卷共享），真要横向扩容还得配 sticky 或者「谁持锁谁干活」的约束（README 已写明）。
  - 熔断状态是**近似共享**：并发读改写会丢一次计数、探针可能被两个 worker 同时放出去 —— 它只是
    省等待的启发式，不影响正确性。

#### 仓库记忆：PR 行的状态按 Gitee 实况回填；没有要核对的 PR 就不再查 Gitee

- **动机**：用户要求 `MemoryMiddleware` 除了文件头，还维护「变更记录」里 PR 行的**状态** ——
  写着「待合并」的行，线上已经合并 / 已关闭的要改成实际结论；并且如果仓库记忆里**没有待合并的
  PR**，就完全不必查 PR 列表；闸门也跟着「没有待合并的 PR 就不拦」。
- **改动**：
  - `agent/memory_reconcile.py`：新增「变更记录」行扫描（`_PrRow` / `_row_of` / `_pr_rows` /
    `_is_change_section`，只扫标题以「变更记录」开头的那一节）、`rows_to_verify()`（列出要核对的
    编号）、`_states_by_number()`、`_restate()`（**只替换状态那一格**），并新增 `strip_legacy()`
    给「不必查 PR」的调用方（只摘两张老表，头行一个字不碰）。`reconcile()` 现在会回填状态，
    `Reconciled` 多一个 `restated`（这轮改了哪几个编号，供日志与断言）。状态词统一成模版口径
    `待合并 / 已合并 / 已关闭`（三个常量的值原来还是「待合入 / 已合入 / 已废弃」，与模版两套词）。
  - `agent/middleware/memory.py`：`refresh_memory` 读盘后先看 `rows_to_verify()` —— **没有要核对
    的 PR 就不调 `all_prs()`、不写盘**，返回 `pending=()`，于是 plan / coding 不拦（`rework`
    照旧是「没有待合并的 PR 才拦」）。**没写过记忆的仓库仍然查一次**（用户选的 1b：那一档没有
    任何记忆可依据，是闸门唯一还独立于记忆的地方）。
  - `tools/memory.py`：同一条纪律 —— 提交内容里没有要核对的 PR 时省掉那次查询，落盘前只摘老表 +
    盖时间戳；回执里会说明「N 条变更记录的状态已按实际结果更新」。
  - 口径同步（模型读的那几处）：`agent/prompts.py` 的「仓库记忆文件规则」与 `_MEMORY_WRITE_BACK`、
    `docs/repo-memory-template.md` 与 `sandbox/policies/repo-memory-template.md`（逐字一致）、
    `docs/workspace-directories.md` 里那段闸门说明。
- **关键决策**（用户拍的三点：1b / 2a / 一起做）：
  - **「要核对」= 状态不是「已合并 / 已关闭」的行**（含老格式里没写状态那一格的）——「不确定」
    不能当成「已经完事」，宁可多查一次。
  - **只改写着「待合并」的行，且只改状态那一格**：人工改过的结论不回改；线上状态认不出、或列表里
    没有这个编号时**保持原样**（不猜）；认不出状态字段位置的行只触发查询、不改写。状态那一格
    **加不加粗都认**（认的是这一格，不是排版），替换的是「状态值」本身 —— 与下一个字段之间的
    全角空格、以及同行后面的字段一个字节都不动。
  - **跳过查询的代价写在这里**：文件头的「最近 PR」保持原样（没查就不知道新值，也不写成「—」）；
    闸门判据从「Gitee 实况」变成「记忆里记着的待合并 PR」—— 人工在 Gitee 上开的 PR、或记忆漏记的
    PR 不再拦住 plan / coding 轮（这是省掉每轮一次 Gitee 往返换来的，用户已知悉）。
- **验证**：`npm` 无关，后端改动。全量 `pytest` **951 条全过**（改前 927，新增 24 条）；
  `ruff check` 改动文件干净；`scripts/check_prompts.py` **冲突 0 条**。新增用例覆盖：只扫变更记录节 /
  没写状态算要核对 / 没编号跳过、已合并与已关闭两向回填、仍开着不动、认不出的状态不动、
  人工写死的结论不回改、状态格不加粗也认且不吞全角空格、**只改状态也算「变了」**（否则盘上永远是旧状态）、回填幂等、
  `strip_legacy` 无老表时逐字恒等；闸门侧钉了「没有要核对的 PR 时 `all_prs` 一次都不被调用」
  （桩故意抛异常）、「线上已合入 → 回填且不拦」、「还开着 → 拦」、「没记忆仍查一次」。
- **遗留**：
  - 老格式（只写了 PR、没写状态那一格）的记忆每轮都会多查一次 Gitee —— 那一行认不出字段位置改不了，
    要等下一轮 coding / review 把这条改写成新格式（或人工补一格）才会停下。
  - 跳过查询那几轮不再刷新「最近 PR」；要看最新的就等一次有待核对 PR 的轮次。
  - 提示词与沙箱模版是**建会话时**读 / 推的：改完要**重启后端 + 新建会话**才生效。

#### 代码展示区上下铺满（去掉 60vh 上限，滚动收进代码块）

- **动机**：用户反馈右侧栏「代码」的文件内容框太小（一条条文只占中间一小块），要求**上下方向铺满**。
- **改动**（`frontend/src/RepoPanel.vue` + `style.css`，纯前端）：
  - 文件视图里把「太大 / 二进制」提示与 `<pre class="repo-code">` 包进新的 `.repo-file` 容器。
  - `.repo-panel` 加 `flex: 1`（撑满 `panel-body`），`.repo-file` 与 `.repo-code` 都加 `flex: 1; min-height: 0`；`.repo-code` **删掉 `max-height: 60vh`**。
  - 净效果：**文件头（`← 路径 大小`）固定在上面，正文吃掉剩下全部高度，滚动发生在代码块内部**（原来是整块面板跟着滚、代码只有 60vh）。
- **关键决策**：
  - **用 flex 撑满、不给 `max-height` 换个大数字**：面板高度是可变的（拖宽会换行、全屏会变成整窗），写死任何一个 vh 都会在某个高度下不贴合。
  - **`.repo-panel` 必须一起 `flex: 1`**：`.panel-body` 是列向 flex，父层不长大，子层的 `flex: 1` 就没有可比的高度（这是「铺满」能不能生效的关键一步）。
  - **目录视图不动**：只改文件视图；目录 / 面包屑仍按原来的方式滚（`.panel-body` 是 `overflow-y: auto`）。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。在 5173 真实页面上用 Playwright 量几何（不是看图）：
  - 打开 `deploy/nacos/README.md`：`panel-body` 高 **897**（`scrollHeight` 也是 897，说明没有外层滚动）、代码块 `top=101 / bottom=877 / h=775 / scrollHeight=2002` —— 正好从文件头下方铺到面板底部（底部 20px 是 `panel-body` 的 `padding-bottom`）。
  - 对照改前：`max-height: 60vh` 会把代码块卡在 **≈538px**，与面板高度无关。
  - 全屏态：`.right-panel.fullscreen` 是 `inset: 0`，面板高度等于窗口高度（本机同样 897），所以纵向铺满逻辑与上面一致；Esc 退出后类名回到 `right-panel`、按钮标题回到「全屏」、原先的宽度与标签状态照旧。
  - 目录视图回归：根目录 19 项、逐层进到 `.../product/repository/impl` 都正常渲染，`panel-body` 高度与 `scrollHeight` 相等（未出现异常滚动条或被裁切）。
- **遗留**：
  - **目录列表没做自己的滚动容器**：本仓最大的目录只有 13 项（远不够撑满面板），所以「超长目录由面板整体滚动」这条路没在真实页面上触发过；换到单目录几百个文件的仓库时，滚动会落在面板而不是列表上（能用，但不如文件视图那样「头部固定」）。
  - 代码块高度随面板走，**面板很矮时代码区也会很矮**（不再有 60vh 兜底）；窄屏靠拖拽宽度解决。

#### 右侧栏加「全屏」按钮（面板盖满窗口，Esc 退出）

- **动机**：用户要求右侧栏加一个全屏按钮 —— 看方案步骤 / 读代码时能让面板占满窗口。
- **改动**（`frontend/src/App.vue` + `style.css`，纯前端）：
  - 标签条里「收起/展开」按钮下面再加一个同款 32×32 方按钮，图标是**内联 SVG**（四角向外＝进入、四角向内＝退出；不用字体里的 ⛶ 这类符号，免得缺字变方块）。
  - 新增 `panelFullscreen` 状态 + `togglePanelFullscreen()`：进入时若面板是收起的先展开（否则「全屏」只剩一条 44px 条，看不出全屏）；全屏期间**隐藏拖拽热区**、内联宽度让位；**Esc 也能退出**（监听只在全屏期间挂，平时不留全局 keydown）。
  - `style.css`：`.right-panel.fullscreen { position: fixed; inset: 0; z-index: 20; border-left: none }`；`.panel-toggle-btn.active` 高亮当前态。
- **关键决策**：
  - **用 `position: fixed` 覆盖，不把宽度设成 100vw**：后者会把左侧会话栏与对话区挤出视口（它们都是 `flex-shrink: 0`），还得动拖拽宽度的状态。覆盖方案退出后原宽度原样回来 —— 实测退出时拖拽热区（`Value: 260`）确实回来了。
  - **全屏状态不持久化**：它是一次临时操作，刷新即退出（与「左栏收起不记、宽度记」那套偏好分开）。
  - **图标用 SVG**：跨平台稳，且能跟着 `currentColor` 变 hover/激活色。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。在 5173 用无障碍树实测：按钮在标签条第二位（`button 收起面板` 下面）；点一下 → 标题变**退出全屏（Esc）**、**拖拽热区消失**（说明 `fullscreen` 类真的生效）；再点一下 → 标题回到**全屏**、热区恢复（`splitter ... Value: 260`）。
- **遗留**：
  - **Esc 退出没被自动化验到**（CUA 的 `pressKey` 需要最新快照里的节点 id，两次都判 stale）；代码就三行（`keydown` → `Escape` → 关），你手点一下按钮进来、按 Esc 试一次即可。
  - 全屏时面板是**盖住**左侧栏与对话区的（不是把它们挤开）：这是有意的（看代码时更专注），要「并排更宽」那种就用拖拽改宽度。

#### 代码缓存搬到用户个人目录（默认 `~/.ai-coding-harness/repo_cache`）

- **动机**：用户指出右侧栏「代码」面板在本机拉的那份仓库缓存落到了**项目目录**里（`D:\python-project\ai-coding-harness\repo_cache`）—— 那是下载来的第三方代码，混在项目里既会被误当成项目文件，也会跟着项目被备份/同步走。要求缓存到**用户个人目录**，用的时候从本地加载。
- **改动**：
  - `src/ai_coding_harness/config.py`：`repo_view_dir` 的默认值从相对路径 `"repo_cache"` 改成**用户目录** `~/.ai-coding-harness/repo_cache`（用 `Field(default_factory=_default_repo_view_dir)` 算出来 —— 这是跟着机器走的路径，写不成字面量）。`REPO_VIEW_DIR` 照旧可覆盖；**给了相对路径仍按当前工作目录解析**（`repo_cache.cache_root()` 会转绝对路径，上一轮那个「相对路径被解析两次」的坑仍被盯着）。
  - `src/ai_coding_harness/repo_cache.py`：模块说明里把默认位置写清（不是项目目录），并保留那条 cwd 教训的注释。
  - `README.md`：代码面板那条把默认写成的路径。
  - **删掉了项目目录里那份旧缓存** `D:\python-project\ai-coding-harness\repo_cache\`（≈700KB，含之前错位的那份嵌套克隆；纯缓存、可重新下载）。删除前核对过：解析后的绝对路径正好是工作区下的 `repo_cache`，不是工作区根。
- **关键决策**：
  - **按用户目录、不按系统规范**：Linux 的规矩是 `~/.cache/...`、Windows 是 `%LOCALAPPDATA%`，但那要维护两套规则、排障时还得多想一步；`~/.ai-coding-harness/…` 一套写法两个平台都好找（用户也选了这条）。
  - **默认值用工厂算**：路径随机器变（还要吃 `HOME`/`USERPROFILE`），所以不能写成 `log_dir` 那种字面量默认值。
- **验证**：`Settings().repo_view_dir` 实测为 `C:\Users\52387\.ai-coding-harness\repo_cache`，`repo_cache.cache_root()` 解析一致；`REPO_VIEW_DIR=D:/x/cache` 覆盖仍有效；`tests/test_repo_cache.py` / `test_repo_routes.py` 等 **81 条全过**（它们用临时目录，不受默认值影响）；全量 `pytest` **927 条全过**；`ruff check` 改动文件干净。
- **遗留**：
  - **要重启后端**才用新默认值；重启后第一次点「同步」会在用户目录下重新 clone 一份（旧的已删，所以是全新下载）。
  - 缓存仍然**不会自动清理**（多仓库会持续占磁盘）：现在要清就是删 `~/.ai-coding-harness/repo_cache` 下对应目录。
  - 若把 `HOME`/`USERPROFILE` 指到别处（少见），缓存会跟着走 —— 这是预期行为，写绝对路径到 `REPO_VIEW_DIR` 可钉死。

#### 收起 / 展开按钮挪到两侧栏顶部，做成 32px 方按钮

- **动机**：用户反馈右侧栏的收起按钮在**最下边**（`margin-top: auto` 顶到底），不好找；要求挪到上边、做大一点，左侧一起改。
- **改动**（`frontend/src/App.vue` + `style.css`，纯前端）：
  - 右侧：按钮从竖标签条**最下边**挪到**最上面**（`v-for` 之前的第一项），去掉 `margin-top: auto`。
  - 两侧统一成同一个 `.panel-toggle-btn`：**32×32**（原来右栏 26、左栏是无边框的 16px 字符）、带边框与圆角、悬停变强调色；方向语义不变（箭头指向面板会往哪边缩：左栏展开 `‹` / 收起 `›`，右栏展开 `›` / 收起 `‹`）。
  - 左侧收起态：rail 顶部也放同一个按钮（原来只有竖排「会话」文字可点）；「会话」竖排文字保留（仍是可点的第二入口），「＋」跟在它下面。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）；在 5173 上用无障碍树确认两侧按钮都在**最上面**（左栏首个节点 `button › 展开会话栏`、右栏标签条首个节点 `button › 收起面板`）。
- **遗留**：无（像素观感请你自己扫一眼：32px 是否够显眼、箭头方向是否符合直觉）。

#### 初次加载恢复上次的页面；左栏每次默认展开

- **动机**：用户要求两件事 —— ① 界面初次加载时**恢复上次打开的页面**；② 左侧会话栏**默认加载**（展开），而不是继承上次收起的状态。
- **改动**：
  - `frontend/src/App.vue`：新增两个 localStorage 键 —— `ui.lastSessionId`（上次打开的会话）与 `ui.newChatPending`（上次停在「新建对话」页）。`openSession()` 记下会话、`startNewChat()` 清掉会话并记下「新建对话」、新建成功与删除当前会话时同步维护。`onMounted` 从 `onMounted(reloadSessions)` 改成「先 `reloadSessions()`，再 `restoreLastView()`」：有会话就 `await openSession(id)`（**复用现成那套恢复逻辑**：历史 + 方案 + 「本轮还在跑就接回直播流」+ 工作区准备），上次停在新建对话页就回到那一页。
  - 恢复失败（会话已删 / 已失效）**清掉记录、复位到欢迎页并给一条轻提示**（「上次打开的会话已失效，已回到列表」）—— 不让用户停在打不开的页面上，也不每次刷新再试一遍同一个死会话。
  - `frontend/src/splitter.ts`：`usePanelWidth` 新增 `persistCollapsed`（默认 `true`）；左栏传 `false` —— **收起状态不记**（每次加载都是展开的），宽度仍然记住。右栏保持记住。
  - localStorage 读写都包了 try/catch：隐私模式 / 被禁时读不到、写不进都只当「没记住」，不影响功能。
- **关键决策**：
  - **恢复直接复用 `openSession`**，不另写一套「轻量恢复」：那一条路径已经把「历史 / 方案 / 接回直播 / 工作区准备」都处理好了，另写一套必然漂（何况「刷新接回直播」本来就是它设计的用途）。
  - **左栏不记收起、右栏记**：左栏是导航（每次打开都该看到会话列表），右栏是内容面板（收起往往是刻意的）。
  - **失败要留痕**：清记录 + 一条 6 秒自动消失的提示，比静默跳回欢迎页更容易让人明白发生了什么。
- **验证**：
  - `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。
  - **在真前端（5173）用新标签页模拟「初次加载」**：先在一个标签里打开会话（`你好`）并把左栏**收起**；再开一个新标签 → 它自动回到该会话（历史 1128 项与右侧计划面板 8/8 步都渲染出来），左栏是**展开**的（显示「收起会话栏」与会话列表），宽度 235px 也被记住（该值是被窄屏 `45vw` 夹取后的结果 —— 夹取也顺带验到了）。
- **遗留**：
  - 恢复会**自动触发一次工作区准备**（`POST /sessions/{id}/workspace`）：沙箱不在时要等几秒到十几秒（界面显示现成的「代码工作区准备中…」）。与「点一下会话」是同一条路径，不新增等待。
  - localStorage 那几个键（`ui.lastSessionId` / `ui.newChatPending` / splitter 的两组）都是**按浏览器**存的：换浏览器或清站点数据就回到欢迎页。多标签页同开时各自维护自己这边的键（最后一个动作覆盖），没做跨标签同步（`storage` 事件），暂不做。

#### 左右侧栏可收起、可拖宽；右侧标签改成竖排（加标签只改一个数组）

- **动机**：用户要三件事 —— 左侧会话栏能收起；右侧的标签**竖着排**（现有计划 / 代码，以后加第三个只需往下加）；左右两条侧栏都能**手动调宽**。
- **改动**：
  - `frontend/src/splitter.ts`（**新增**）：左右共用的宽度逻辑 —— 指针拖拽（`setPointerCapture`，拖动期间给 `body` 挂 `resizing` 免得选中文字）、**双击复位**、**方向键 ±16（Shift ±48）**、宽度与收起状态写 localStorage；夹取上限跟窗口走（`min(上限, 45vw)`），窄屏不会把中间的对话列挤没。
  - `frontend/src/App.vue`：左栏加 rail（竖排「会话」+「＋」）与收起按钮；右侧从「横标签 + 另一套折叠 rail」两套状态改成**一套** —— `[竖标签条][内容]`，「收起」＝收内容、标签留着（点标签就展开到那一个）。标签由 `panelTabs` **数组**驱动（`[{计划, 状态点?}, {代码}]`），加第四个标签只往数组里加一项；两条 6px 拖拽热区带 `role="separator"` + `aria-valuemin/max/now`；`resetPlan()` 不再重置面板收起状态（现在是记住的偏好，切会话不会把它拉回来）。
  - `frontend/src/style.css`：竖标签条 / rail / 拖拽热区 / 内容区（滚动归内容区，标签条不跟着滚）；删掉旧的横标签 `.panel-head`、旧 `.panel-rail` 与窄屏写死宽度那两条规则（宽度已由拖拽 + 45vw 夹取接管）。
- **关键决策**：
  - **收起只收内容**：标签条永远在，点标签即展开到该标签 —— 原来「折叠 rail」与「横标签」两套状态并存，用户得先猜点哪儿。
  - **竖排文字只加在文字上**（`.panel-tab-text`）：写在按钮上会让 flex 主轴跟着旋转，标签旁的色点位置就不可控。
  - **宽度与收起状态持久化**（`ui.sidebar.*` / `ui.right.*`）：隐私模式下写失败只影响记忆，不影响功能。
- **验证**：
  - `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。
  - **用无障碍树在真前端（5173，你那个 dev server）上走了一遍**：左栏收起 → 只剩「会话」「＋」，再点「会话」展开；右侧竖标签「计划 / 代码 / ›」在，切到「代码」后出现分支下拉（master + 7 支）、「同步」按钮、「上次同步 2026-09-26 15:50」、面包屑与目录树（带文件大小）；两条 separator 的 `Value` 分别是 260 / 360。
  - 走查顺带抓到并修掉一个真 bug：separator 只给了 `aria-valuenow` 没给 `min/max`，无障碍层按百分比处理，260 被截成 **100**；补上后正确显示 260 / 360。
  - 顺带确认上一批那个缓存修复**在真机生效**：面板显示「上次同步 2026-09-26 15:50」，分支与文件树都读得出来。
- **遗留**：
  - **拖拽与方向键这两条路径没有自动化验证到**：CUA 的无障碍快照是**增量 diff**，热区的节点 id 拿不到（点它会被判 stale）。手感请手动试：拖那条 6px 缝隙、点一下缝隙再按 ←/→、双击缝隙复位。
  - 我用应用内浏览器开了个标签停在 5173 做走查，**没有关闭 API**，你顺手关掉即可。

#### 代码字号抬一档：面板 12→14px，聊天代码 13→14px

- **动机**：用户反馈「代码的字体有点小」。查下来确实偏小、而且**两处不一致**：代码面板正文是 12px，聊天里的代码块与行内代码是 13px。
- **改动**（`frontend/src/style.css`，纯样式）：
  - `:root` 新增 `--code-size: 14px`，**面板正文**（`.repo-code`）与**聊天里的代码**（`.markdown code`，代码块里的 `code.hljs` 也走这条）共用它 —— 以后再调只改一处；行高 1.5 → 1.6。
  - 面板里的目录条目 / 路径 / 面包屑 / 分支下拉 / 同步按钮 / 提示文案：12px → **13px**；次要信息（文件大小、上次同步时间、提示条、错误文案）：11px → **12px**。
  - 右侧栏宽度 340 → **360px**（窄屏那条媒体查询 240 → 260px）：字号变大后长行更容易横向滚动，宽 20px 换回一点可读性。
- **验证**：`npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`，37 个模块）；纯 CSS 改动，后端与 `pytest` 不受影响（未重跑）。样式表里 `--code-size` 只出现在 `:root` 与那两处使用点上。
- **遗留**：14px 是按「面板 + 聊天一起统一」定的；嫌大或嫌小改 `--code-size` 一处即可（次要信息那几档是写死的，要一起调再说）。面板宽度的 20px 是顺手的，不想要就回 340。

#### 代码缓存落到了嵌套目录：相对路径被「换 cwd 跑 git」解析了两次

- **动机**：用户报第二处错 —— `fatal: destination path 'repo_cache\zzpfordevelopment\wanqi-mall' already exists and is not an empty directory.` 看磁盘发现克隆其实**成功了**，只是落在了
  `repo_cache\zzpfordevelopment\repo_cache\zzpfordevelopment\wanqi-mall`（连 551KB 的 pack 都在）。
- **真因**（两个，都在 `repo_cache.py`）：
  1. **相对路径被解析两次**：`REPO_VIEW_DIR` 默认值 `repo_cache` 是相对路径，而每条 git 命令都是「换 `cwd` 再跑」（克隆用 `cwd=目录父级`），于是作为**目标**传进去的相对路径被 git 相对那个 cwd 又拼了一次 → 落到嵌套目录。紧接着按 cwd 找 `HEAD` 找不到（`is_cloned` 为假）→ 又克隆一次 → 嵌套路径已在 → 报「already exists」。**两处各自都对，合起来错。**
  2. **清残留会静默失败**：`shutil.rmtree(directory, ignore_errors=True)` 在 Windows 上删不掉 git 的**只读**打包对象（实测属性 `-ar--`），于是「先清掉半成品再克隆」这一步没有真的清掉，兜底也变成报「already exists」。
- **改动**（`src/ai_coding_harness/repo_cache.py`）：
  - `cache_root()` 把配置里的路径解析成**绝对路径**（`expanduser().resolve()`），并把这个坑写进注释 —— 换 cwd 跑命令时，相对路径会被解释两次。
  - 新增 `_remove_tree()` 替代 `shutil.rmtree(..., ignore_errors=True)`：先整棵清掉只读位再删；**真删不掉就抛可读错误并指出路径**（让人手动处理），而不是留半个仓库继续往下跑。克隆前清残留、克隆失败后清半成品都改用它。
- **验证**：
  - 新增两条回归用例：`test_a_relative_cache_dir_is_resolved_once`（`REPO_VIEW_DIR=cache` + chdir，断言落点是 `<cwd>/cache/acme/demo`、**没有**再套一层同名目录、分支能列出来）与 `test_a_leftover_directory_with_readonly_files_is_cleared`（先造一个含只读文件的残留目录，断言能清掉并克隆成功）。**这两条在修前都会红** —— 之前那批用例全用绝对临时路径，所以一直没碰到。
  - 全量 `pytest` **927 条全过**（上一批 925，净增 2）；改动文件 `ruff check` 干净。
- **遗留**：
  - 真机上那份**错位的克隆**要手工删：`repo_cache\zzpfordevelopment\repo_cache\`（只占几百 KB，删掉即可；下次点「同步」会克隆到正确位置 `repo_cache\zzpfordevelopment\wanqi-mall`，旁边一个 `wanqi-mall.json` 记上次同步时间）。
  - 这一类「相对路径 × 换 cwd」的坑只有真机验证能抓到（单测当时全用绝对路径），与上一版 `create_subprocess_exec` 那个是同一批教训：**新功能上线前先拿真环境点一遍**。

#### 代码面板「同步失败 500」：git 改走线程，不再依赖事件循环的子进程支持

- **动机**：用户报「界面处的代码是空的，显示同步失败 500」。`api.log` 里一行就够定位：
  `repo.py:100 → repo_cache.py:341 → repo_cache.py:279 raise NotImplementedError`。
- **真因**：`repo_cache._run` 用的是 `asyncio.create_subprocess_exec`，而它要求**事件循环支持子进程**；Windows 上只有 `ProactorEventLoop` 支持。本项目的后端在 Windows 上**故意**用 `SelectorEventLoop`（`main.selector_loop_factory`：uvicorn 0.36+ 在 Windows 默认 Proactor，会忽略事件循环策略，而 psycopg 异步要 Selector），于是这条调用在真机上必然抛 `NotImplementedError`，路由只捕 `RepoViewError`，于是 500。**这是我上一版没在真机验证留下的洞**：单测跑在 pytest 自己的 Proactor 循环上，永远碰不到。
- **改动**（`src/ai_coding_harness/repo_cache.py`）：`_run` 换成 `asyncio.to_thread(subprocess.run, …)` —— 与事件循环无关，两个平台同一条代码路径；超时改由 `subprocess.run(timeout=…)` 负责（`TimeoutExpired` → 可读文案），`OSError` 仍映射成「git 启动失败」。模块注释里把这段来龙去脉写清楚，免得以后有人「优化」回 `create_subprocess_exec`。
- **验证**：
  - 新增回归用例 `tests/test_repo_cache.py::TestEventLoopIndependence`：**在一个真的 `SelectorEventLoop` 里**跑一次完整同步（正是后端那种循环）。改前必红（`NotImplementedError`），改后绿。
  - 另拿真仓库跑了一次探针（会话 `78780d7a…` 绑的 `zzpfordevelopment/wanqi-mall`，凭据从库里取、在 Selector 循环里跑）：git **确实被执行**（错误已经是 git 自己的输出），这一侧只差网络 —— 探针所在环境连不上 `gitee.com:443`（`Could not connect to server`），所以 Gitee 的认证那一段仍需你在真机上点一次「同步」确认。报错文案可读、且已按令牌脱敏。
  - 全量 `pytest` **925 条全过**（上一批 924，净增 1）；改动文件 `ruff check` 干净。
- **遗留**：
  - **重启后端**才会加载新代码（工具与路由都是进程内对象）；重启后点「同步」应能跑通，失败也会是 409 + 一句人话，而不是 500。
  - 线程方案下**超时杀不掉子 git 进程**（`subprocess.run` 只杀它直接拉起的那个）：`git fetch` 超时时可能留下一个孤儿 git 进程，几分钟后自己结束。真要收干净得自己 `kill` 进程组，暂不做。

#### 仓库记忆定型四节：分析结论进记忆，analysis 轮也能写回

- **动机**：用户把记忆的内容口径一次说清 —— 记忆 = **分析出来的仓库信息 + 变更记录**；`repo-bootstrap-analysis` skill 的结论不再另存文件，而是**沉淀进记忆**；顺带给 analysis 轮开记忆写权限（否则「只问不做」的那一轮分析完就丢了，而它产出的正是记忆的正文）。上一版方案里那个「仓库内单独放一份分析文档」的做法据此作废。
- **改动**：
  - `sandbox/policies/repo-memory-template.md` + `docs/repo-memory-template.md`（逐字一致）：从五节改成**四节** —— 一、**仓库基本信息**（来自按 skill 做的分析：技术栈 / 目录结构 / 启动方式 / 测试构建 / 已知约定，并写明「变了才改」，别每轮重写）／二、**变更记录**（PR + 状态，格式不动）／三、**当前缺口**（按模块列未完成的功能，下一轮该做什么的唯一出处；原「当前进度」改名而来）／四、**关键决策**。原「约定与注意事项」并进第一节的「已知约定」。
  - `src/ai_coding_harness/tools/register.py`：`ANALYSIS_FORM` 增加 `save_repo_memory` —— 分析轮是**唯一带记忆写回的只读形态**（仓库与 PR 仍全是只读；记忆在 StoreBackend 上，`policy_for("analysis")` 的沙箱写根仍是空的，两边不冲突）。
  - `src/ai_coding_harness/server/service.py`：`_MEMORY_WRITE_FORMS` 加 `ANALYSIS_FORM`，于是 analysis 收尾也会按 Gitee 实况打底刷新记忆的文件头。
  - `src/ai_coding_harness/agent/prompts.py`：新增 `_INTENT_DIRECTIVES["analysis"]`（本轮只读分析 + **收尾把「这个仓库是什么」沉淀进记忆** + 明确**不写变更记录**，因为这一轮没有改动、也没有 PR）；`_READONLY_BOUNDARY` 从「没有 `save_repo_memory`」改成只列 PR 写工具，并新增只给 analysis 的 `_ANALYSIS_MEMORY_WRITE` 段；基础提示词的记忆规则改成「写回只在 **coding / review / analysis** 三类轮的收尾」，并补上**首次建档**口径（没有注入记忆块＝这个仓库还没写过记忆，按模版从头写一份）；「当前进度」的提法全部改成「当前缺口」；skill 那四处点名改成**结论沉淀进记忆的两节**（不再指向独立文件）。
  - 口径随动：`tools/memory.py`、`agent/memory_reconcile.py`、`agent/middleware/memory.py` 的注释、`docs/workspace-directories.md`（补一条「这张表只管沙箱目录，记忆在 StoreBackend 上、能写它的形态有哪四个」+ skill 说明）、`README.md`（意图表 analysis 行、只读形态的写工具口径）、`scripts/check_prompts.py` 的 C5 说明。
- **关键决策**：
  - **「当前缺口」是记忆的一部分，不是独立文档**（用户口径）：它是分析结论的另一半（这个仓库现在缺什么），而「下一轮该做什么」正指着它 —— 放记忆里自动注入，比让每轮多读一个文件更稳。
  - **basic info 只在真变了时改**：技术栈 / 目录 / 启动测试方式每轮重写只会把上下文浪费在复述上，还会覆盖掉别人写的细节。提示词里因此写死「变了才改」。
  - **analysis 只多一件写工具**：`save_repo_memory` 走 StoreBackend，不进沙箱、不进目标仓库，所以「只读形态不碰仓库」这条边界不变；`create_pr` / `comment_pr` / 写文件仍然一律没有。
  - **analysis 不写变更记录**：这一轮没有代码改动、也没有 PR，而「一次改动一定带着一个 PR」这条纪律刚在上一条改动里立稳 —— 让它写条空条目等于把那个口子又开回去。
- **验证**：全量 `pytest` **924 条全过**（上一批 920，净增 4）；改动文件 `ruff check` 干净；`scripts/check_prompts.py` 8 份提示词、**0 条冲突**；两份记忆模版按字节比对一致。新增/改写的用例：analysis 的工具子集（有 `save_repo_memory`、没有 PR 与文件写工具）、analysis 提示词里「唯一的一处写入」段与 qa 的对照、analysis 意图指令（含「不写变更记录」）、写回轮次四处指令（coding / review / rework / analysis）都带「收尾写回 + 当前缺口 + 本轮只写一次」、轮末打底把 analysis 归入写回那组、模版四节的断言。
- **遗留**：
  - **真机未验证**：analysis 轮要重启后端 + 新建会话才生效；验证点是「问一句『这仓库是做什么的』→ 回复里有结论 → 记忆的第一节被写出来」。
  - **旧记忆不迁移**：用户会自己删掉那三份旧记忆、由模型重建 —— 正好一次性对齐四节格式；删掉之后**下一次 coding / review / analysis 收尾**才会落盘（平台的刷新只盖文件头，不凭空建档）。
  - 「关键决策」独立成节是用户选的（1b），于是它和变更记录里的「变更内容」有可能各说一遍同一件事；真觉得重复再合并。

#### 变更记录格式：每条都带 PR 与状态，取消「没有 PR」这一档

- **动机**：用户指出模板里那两个口径不对 —— ① 变更记录的 PR 那行写着「未提 PR 的改动写「未提 PR」」，可**写码与提 PR 是同一条流程**，留这一档等于给模型一个「这轮可以先不提 PR」的正当理由（真实记忆里就这么被用过：`wanqi-mall` 有一条「已推送、PR 待人工创建」，更早的 `java_test_demo` 里干脆整段 `- **PR**：无`）；② 每条变更记录缺 **PR 状态**，而 PR 后来合没合上只有人知道，需要留一格人工修订的位置。
- **改动**：
  - `sandbox/policies/repo-memory-template.md` + `docs/repo-memory-template.md`（两份逐字一致，测试钉着）：PR 那行改成 `- **PR**：{[#31](链接)}　｜　**状态**：{待合并 | 已合并 | 已关闭}`，并在条目下面补一段说明 —— 一定带着一个 PR、**没有「没有 PR」这种写法**；本轮 PR 没建成时**这条变更记录不写**（失败与原因写进「当前进度」和回复）；`状态` 写回时一律填 `待合并`，**之后由人工改**；平台只维护文件头那一行。
  - `src/ai_coding_harness/agent/prompts.py` 两处口径随动：基础提示词的「仓库记忆文件规则」（每条改动写清 PR 编号 / 链接 / **状态**）与收尾写回指令 `_MEMORY_WRITE_BACK`（**状态一律先填「待合并」**；PR 没建成时**不要**写这条变更记录，也不要写「没有 PR」这种条目）。
  - `tests/test_memory_reconcile.py` 新增「模板里有 PR 状态行 + 之后由人工改 + PR 没建成不写 + `未提 PR` 不许回来」；`tests/test_prompts.py` 新增「三条写回指令都带状态口径，基础提示词同口径」。
- **关键决策**：
  - **状态用词取 Gitee 那套**：`待合并 / 已合并 / 已关闭`（用户在需求里一直用「待合并」）。代码内部 `memory_reconcile.PENDING/MERGED/DISCARDED` 的「待合入 / 已合入 / 已废弃」只出现在实现与日志里、不落记忆，故本次**没动**它 —— 要统一再单独做。
  - **状态不做自动化**（用户明确要求人工改）：平台每轮确实查得到 PR 实况，但它只用来自动回填**文件头**的「最近 PR」；条目里的状态是人写给人看的，自动回填会把「当时算的状态」和「事后的事实」混在一起。
  - **取消「没有 PR」档，且给出失败时的出口**：光删掉这一档还不够 —— 模型遇到 PR 建不出来时得有地方写，所以明确「失败写进「当前进度」与回复」，而不是让它编一个 PR 编号或写一个占位条目。
- **验证**：`tests/test_memory_reconcile.py` + `tests/test_prompts.py` **134 条全过**；全量 `pytest` **920 条全过**；改动文件 `ruff check` 干净；`scripts/check_prompts.py` 8 份提示词、**0 条冲突**；两份模板按字节比对仍一致。
- **遗留**：
  - **已有记忆里的旧条目不改写**：三份真实记忆（`wanqi-mall` / `java-for-test` / `java_test_demo`）里那些没有状态、或写着「PR：无」的历史条目保持原样 —— 它们是发生过的事实，重写反而失真。新写的条目按新格式走。
  - 状态靠人工改，所以**它迟早会过期**（人工合并后忘了改回去）。要收口的话有两条路：平台每轮按 Gitee 实况自动回填条目状态（与「人工改」这条要求冲突，要先定谁优先），或给一个「状态复核」的提醒。现在都不做。

#### 右侧栏「代码」面板：宿主机自己 clone 一份仓库，切分支只是换个 ref

- **动机**：用户要「界面右侧栏能看代码，clone 下载、之后 fetch 更新、可以切分支 —— **不必经沙箱**」。这条要求本身就点出了问题：代码一直只存在于沙箱里（`/ai_coding/project/`），而沙箱会没（TTL 到期回收、会话结束销毁、首次创建失败），那时连「这个仓库长什么样」都看不到；而且从沙箱里往宿主机搬文件，等于给一个随时会消失的东西再加一条传输链。
- **改动**：
  - `src/ai_coding_harness/repo_cache.py`（**新增**，约 380 行）：宿主机侧的只读仓库缓存。**裸仓**（`git clone --bare`）+ 只读查询 —— 克隆/更新 `sync()`、分支 `list_branches()`（默认分支取裸仓 HEAD）、目录 `tree()`（`ls-tree -l -z`，实测记录之间用 NUL、记录内用 TAB，名字带空格与中文都稳）、文件 `read_file()`（先 `cat-file -t/-s` 判类型与大小，再 `cat-file blob` 取正文）。
  - `src/ai_coding_harness/api/routes/repo.py`（**新增**）+ `api/schemas.py` + `api/app.py`：四个接口 `GET /sessions/{id}/repo`（状态 + 分支，不联网）、`POST …/repo/sync`（没有就 clone、有就 fetch）、`GET …/repo/tree`、`GET …/repo/file`。
  - `src/ai_coding_harness/config.py`：`REPO_VIEW_ENABLED` / `REPO_VIEW_DIR`（默认 `repo_cache`，与 `log_dir` 同口径按 CWD 解析）/ `REPO_VIEW_GIT` / `REPO_VIEW_TIMEOUT_SECONDS`（默认 300，首次 clone 可能很慢）/ `REPO_VIEW_FILE_MAX_BYTES`（默认 1 MiB）。
  - 前端：新增 `frontend/src/RepoPanel.vue`（分支下拉 + 同步按钮 + 面包屑 + 惰性目录树 + 文件视图，高亮复用 `highlight.js`）；`App.vue` 的右侧栏从「只在有方案时才出现的计划面板」改成**计划 / 代码两个标签**的常驻面板（折叠态是竖排标签，点哪个展开到哪个）；`api.ts` 加四个接口；`style.css` 加代码面板样式；`markdown.ts` 导出 `renderCode()`（高亮 + DOMPurify，与正文同一条底线）。
  - 顺手做的两件（方案里说过）：`start.bat` / `start.sh` 各加一段**只警告**的 git 自检；计划步骤里的「涉及文件」变成可点按钮，点一下切到代码标签并打开那个文件（`/ai_coding/project/<repo>/…` 这类沙箱路径会被剥成仓库内相对路径）。
  - `README.md`：补上四个新接口、「代码面板」这条能力、`rework` 意图，并把意图表里几行过期的工具清单（plan 的增删改查、review 的写回）改回与 `tools/register.py` 一致。
- **关键决策**：
  - **裸仓 + `git show`，不是「克隆到工作区再 checkout」**：切分支退化成「换个 ref 参数」，服务端**零状态** —— 两个标签页能各看各的分支，也没有脏工作区、并发 checkout 打架。读操作不需要锁（`git fetch` 只在对象全部落盘后才动 ref）；只有 clone / fetch 之间互斥（`asyncio.Lock`，按 `owner/repo`），并发两次同步只会克隆一次（用例钉了）。
  - **显式 refspec 是必须的**：本机实测 `git clone --bare` **不写** `remote.origin.fetch`（为空），不带 refspec 的 `git fetch` 只更新 `FETCH_HEAD` —— 分支列表会永远停在克隆那一刻。故 `fetch --prune origin '+refs/heads/*:refs/heads/*'`，远程删分支也跟着消失。
  - **令牌不进 URL**（硬要求）：clone 的 URL 会被 git 记进 `remote.origin.url`，拼令牌等于写在磁盘上。改成经**环境里的 git 配置**注入（`GIT_CONFIG_*` + `http.https://<host>/.extraheader`，git ≥ 2.31），于是不进 URL、不进 argv、也不落 `.git/config`；用 `GIT_CONFIG_*` 而不是 `-c` 是因为 `-c` 的值会出现在命令行参数里（同机器其他进程读得到）。另加一层：git 的 stderr 回给调用方前按令牌及其 base64 替换（用例覆盖），`GIT_TERMINAL_PROMPT=0` 保证缺凭据时直接失败而不是挂住。
  - **路径穿越在结构上就不可能**：用户给的路径从不落到文件系统，只喂给 git 去树里查（实测 `git rev-parse --verify 'master:../readme.md'` 被 git 拒掉）；外面再加一层 `..` / 绝对路径 / 反斜杠 / NUL 的字面拒绝，`ref` 必须在分支白名单里（顺带挡住 `-` 开头的参数注入），`owner` / `repo` 过字符集才拿去拼磁盘路径。
  - **一次只发一层目录**：`ls-tree` 逐层展开，不把整棵文件树一次拉下来（大仓库会几万条）；文件超上限（1 MiB）与二进制（含 NUL）**只回标记与大小**，不把正文塞进 JSON —— 前端照实说「太大 / 二进制」，不假装能显示。
  - **缓存按 `owner/repo` 一份**：平台本来就「一仓库一个活跃会话」，多余的隔离只会重复下载；旁边一份 `<repo>.json` 记上次成功同步的时间（写失败只记日志，不影响浏览）。
  - **只在打开「代码」标签时才下载**（用户选的口径）：建会话不额外等几十秒；打开时先给状态、没缓存就下（带「同步中…」），之后每次打开都是秒开，点「同步」才 fetch。
- **验证**：
  - 新增 `tests/test_repo_cache.py`（30 条）与 `tests/test_repo_routes.py`（13 条），都是**真 git**：夹具在 `tests/git_repo.py`（临时仓库 + 带空格/中文的目录 + 二进制 + 大文件），没有 git 的机器整文件 `skipif`。覆盖：首次克隆 / 二次 fetch 认出新分支与新提交、并发同步只克隆一次、克隆失败不留半成品、**挪走远程后读操作照样可用**（证明读不联网）、fetch 真的会去远程（证明不是假装的）、目录与文件（含分支不同内容不同）、太大 / 二进制 / 目录 / 不存在的路径、路径与 ref 的各类注入、`_scrub` 与「令牌不进 `.git/config`」、没装 git 的文案；路由层覆盖状态 / 同步 / 目录 / 文件 / 409 文案 / 503（没 git）/ 404（开关关掉、会话不存在）。
  - 全量 `pytest`：**917 条通过、1 条既有失败**（见「遗留」第一条，与本次改动无关）；`ruff check` 改动文件干净；`scripts/check_prompts.py` 0 条冲突；前端 `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`，37 个模块）。
- **遗留**：
  - **仓库里有一处与本改动无关的红灯**：`docs/repo-memory-template.md`（今天 12:20 被改过）与 `sandbox/policies/repo-memory-template.md` 的第 28 行不再逐字一致 —— docs 那行是 `- **PR**：{[#31](链接)；}{状态(open,closed,merged) }`，sandbox 那行是 `- **PR**：{[#31](链接)；未提 PR 的改动写「未提 PR」}`。`tests/test_memory_reconcile.py::test_the_two_template_copies_are_identical` 因此失败。**本次没动这两个文件**（那是别人的改动，且沙箱里那份才是真正生效的），等确认保留哪一版再对齐。
  - **真机未验证**：私有仓库的认证走的是 `http.extraHeader`（本机拿不到能验证的 Gitee 网络），要重启后端 + 打开「代码」标签看首次 clone 是否成功；公开仓库可先拿它验证浏览 / 切分支 / 同步。
  - 面板**只看不搜**：没有按路径搜索、没有 diff、没有历史提交列表（都不在本次口径里）。文件大小上限 1 MiB，超了只提示。
  - 缓存**不会自动清理**：仓库越看越多、磁盘只增不减；要清就是删 `repo_cache/` 下对应目录（下次打开会重新下）。也没设「多久没看就踢掉」的过期策略。
  - 浅克隆（`--filter=blob:none` / `--depth`）没做：大仓库首次 clone 慢就是这个原因，真觉得慢再加开关。

#### 新增意图 `rework`：按评审意见改代码，闸门两头都管

- **动机**：用户提出「要在审查结论之后**改代码**」这条路上被闸门夹住了。实测现有判定（拿真词表跑）：`按评审意见修改一下` → `coding` → 走 orchestrate 形态 → **有未合入的 PR 就拦下本轮**，而它要改的正是那份还开着的 PR —— 等于把自己永久挡在门外；`根据审查结论把代码改了` / `评审提的几个问题都修一下` → `review`（**跑去审，不改**，因为「评审 / 审查」是 review 的强口令，而 review 的排除词只挡得住「修改 / 修复 / 修正」这几种写法）。用户进一步要求：**没有待合并的 PR 时直接拒绝这次修复** —— 确实，那时没有可修的对象，模型拿不到审查原文，只会空转或凭记忆编一份「评审意见」出来改。
- **改动**：
  - `src/ai_coding_harness/server/intent.py`：`TASK_TYPES` 增加 `rework`（并导出 `REWORK_INTENT` 常量）；`_RULES` 增加一条**排在 `review` 之前**的规则；`_INTENT_SPECS` 与 `_BOUNDARY` 各补一条（rework vs coding / rework vs review 的分界）。
  - `src/ai_coding_harness/agent/profiles.py`：新增 `FORM_ALIASES = {"rework": "coding"}`，别名在 `_PROFILES` 建好之后挂上**同一个对象** —— 装配与 coding 一字不差（同一批工具、同一段角色段、同样三个子智能体），差别只在闸门；真要给 rework 别的工具，就得摘出别名、单开形态。
  - `src/ai_coding_harness/agent/middleware/memory.py`：闸门从「形态级一刀切」改成**形态决定挂不挂、本轮意图决定拦不拦**。`plan` / `coding` 照旧（有未合入的 PR 就拦）；`rework` 反过来（**没有**才拦，新增 `NOTHING_TO_FIX_MARKER` 标记与 `no_pending_pull` 中断原因）；`review` 照旧不挂闸门；读不到意图时按正向判（保守）。记忆的**刷新**不受影响 —— 拦不拦是判 PR 状态的结论，不是不刷新。
  - `src/ai_coding_harness/agent/prompts.py`：新增 `_INTENT_DIRECTIVES["rework"]`（先取回审查原文且不得凭记忆复述 → 派 `coder` 在**既有分支**上只改用户点名的问题 → `comment_pr` 逐条回评 → 收尾写记忆；本轮不派 `planner`；闸门归平台判，别自己再查一遍）。
  - 口径随动：`agent/middleware/__init__.py`（导出与模块说明）、`server/service.py`（轮末打底的形态注释）、`docs/workspace-directories.md`（rework 复用 orchestrate 那一行）。
- **关键决策**：
  - **强口令带动词，弱线索才含糊**。光秃秃的「评审意见」**不**进强口令，否则「看看有哪些评审意见」（查现状）会被判成「去改代码」；而「评审提的 / 评审提了 / 评审提出」是**引用别人的审查输出**、不是在下达审查命令，关键字层分不出「按它改」还是「只是看看」，所以把它们放进 review 的排除词、只留给模型判（模型侧有 `rework(评审提)` 这条线索与两条分界规则）。于是两侧都保住：查现状仍是 review，真正的修改请求落 rework —— 兜底口径也跟着钉了测试。
  - **共用装配而不是新开形态**：形态一多，工具子集、角色段、模型路由、`scripts/check_prompts.py` 的渲染表都要各加一份，而它们本可以一字不差。代价写下来：同一会话里 coding 与 rework 是**同一个缓存 agent**（缓存键是形态），所以「这一轮不派 planner」只能靠意图指令，不能靠裁掉工具。
  - **闸门按轮读 `configurable.intent`**：意图是逐轮的，中间件（连同智能体）是按形态建好并缓存的，形态级知识分不开「coding 要拦 / rework 要放行」。原先模块里那句「`abefore_agent` 不读 runtime / state」的自我约束跟着改成「不读 state；唯一的例外是读本轮意图」，并在类 docstring 里写明理由（意图不是依赖，它每轮都变）。副作用：`rework` 的拦下发生在**刷新之后** —— 与其他闸门一致（记忆先按事实刷新，再决定放不放行）。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**875 条全过**（上一批 851，净增 24）；`ruff check` 改动文件干净；`scripts/check_prompts.py` 仍是 **0 条冲突**。
  - 新增/改写的用例：`rework` 口令直通（5 句真话：按评审意见 / 根据审查结论 / 按审查意见 / 修复评审意见 / 返工）且**不调分类模型**；review 的命令与「看看有哪些评审意见」**没被抢走**；「评审提的…」关键字层让位给模型、线索进提示词、模型可选 rework、兜底口径两侧都成立；`parse_reply` 认 `rework`；映射表与「只有登记过的别名才共用形态」；别名拿到**同一个 profile 对象**、同形三个子智能体、同形 tool 清单（真建图跑一轮取 `task` 描述）；coding 与 rework **共用同一个缓存 agent**；rework 指令的四条要点与记忆回写口径；**穿图**验证闸门四态（rework + 有 open PR → 放行且记忆仍刷新；rework + 无 open PR → 拦下且 `model.calls == 0`；coding + 有 open PR → 仍拦；缺 intent → 仍拦）。
- **遗留**：
  - **真机未验证**（沙箱连不上）：重启后端 + 新建会话后，说一句「按评审意见把第 2 条改一下」看是否判成 `rework`、是否落在既有 PR 的分支上、以及仓库没有 open PR 时是否看到那条停下标记。
  - `rework` 只知道「仓库里有待合并的 PR」，**不知道是哪一份**：多个 PR 同时开着时，靠模型从用户原话 / `list_pr_comments` 里自己认（这条与 `review` 形态一直以来的处境相同，没做「指认 PR」的参数）。
  - 「评审提的…」这类句子现在**必定**走一次分类模型（关键字层让位）。分类模型不可用时会退到兜底口径：带修正意味的引用句落 `rework`、纯查看那句落 `review` —— 前者在降级模式下偏激进，记在这里备查。

#### 前端 Markdown 卡顿：渲染调用从模板里挪走，两种缓存挡住重算

- **动机**：用户反馈长会话（实测那个会话 125 条消息 / 59 个 Markdown 块）里界面发黏，打字、滚动都跟不上。
- **真因（读代码得出，不是猜的）**：`App.vue` 的块循环里写的是 `v-html="renderMarkdown(p.content)"` —— **渲染函数直接写在模板里**，于是**组件每次重渲染都会把会话里每一条消息整体重来一遍**：`marked` 重新 parse、`DOMPurify` 重新消毒、`highlight.js` 把每个代码块重新高亮。而重渲染的触发点极密：正文 `flush` 每 100ms 一次（`RENDER_INTERVAL_MS`），外加每个工具帧、待办帧、方案帧、活动帧。也就是说**一条消息不但在自己流式时反复渲染，还被别人的每一帧连累重渲染**。
- **改动**（都在前端，两个文件）：
  - `frontend/src/App.vue`：给 Markdown 那个 `<div>` 加 `v-memo="[p.content]"`。依赖只有 `p.content`，所以只有正文真变了的那条（正在流式的那条）才重跑，**其余消息原样跳过**。同页那个「还没出任何块」的占位气泡（正文恒为 `…`）加 `v-memo="[]"`（等价 `v-once`），省掉它跟着每一帧重跑一次 parse + 消毒。
  - `frontend/src/highlight.ts`：给 `highlightCode` 加一层记忆化（`Map`，键 `${语言}\u0000${代码}`，`CACHE_LIMIT=200`，到顶按插入顺序丢最早一条）。已闭合的代码块内容一个字都没变，不该每帧重新高亮；`null`（语言不认识 / 高亮失败）也缓存，免得反复重试。容量上限是防止长会话把内存吃光。
- **关键决策**：
  - **没有动 `marked` / `DOMPurify` 这一层的调用方式**，只改「什么时候调用」—— 语义、消毒强度、输出 HTML 都保持不变，纯性能改动。
  - **不缓存整篇渲染结果**：流式那条消息的正文每次都在变，缓存整篇就得逐帧失效、还得管消毒结果的复用，收益小风险大；`v-memo` 已经把它收敛成「每条消息各自看自己」。
  - `highlight.ts` 的缓存**按语言 + 正文**做键，不按消息 id —— 同一段代码在不同消息里出现也能命中复用。
- **验证**：
  - 前端 `npm.cmd --prefix frontend run build` 通过（含 `vue-tsc`）。
  - **前后耗时的对比数据不可用**：拿自动化量出来的「打开会话 1108ms → 878ms」「连打 31 键 3438ms → 3426ms」两组数字**并不能说明问题** —— 那个时间量的是 CDP 往返，不是应用自己的开销；页面内也拿不到 `performance` / `requestAnimationFrame` 做干净采样（page-evaluate 沙箱是只读的）。所以这次的结论只立在**代码路径**上：改动前每次重渲染 O(全部消息)，改动后 O(正在流式的那条)；真实体感请你在自己的 5173 dev server 上看（已热更新）。
- **遗留**：
  - 真机体感未由我确认。若仍觉卡，可选 **Tier 2**：流式期间**跳过高亮**（先出纯文本、`done` 后补高亮），或把流式那条消息按「已定型前缀 + 正在长出来的尾巴」切开 —— 前者最省、后者最平滑，都还没做。
  - `v-memo` 的依赖是**引用比较**：`p.content` 若是新字符串（流式分片拼接）就会重渲染，这是期望行为；但若日后有人改成原地改写同一个字符串对象，`v-memo` 会失灵，届时需要换成内容哈希或显式的版本号。

#### 任务树的子任务名与挂靠：两套 id 从来对不上，改用子智能体自报的角色

- **动机**：界面任务树里，子任务容器一直叫「子智能体任务」，用户要求按角色显示（「编码子任务」「评审子任务」「规划子任务」）。查下去发现不只是名字问题 —— **子清单从来没有挂到委派它的那一项下面**，两者同一个根因。
- **排查（都是查真会话得出的，不是推测）**：
  - 名字有两个来源，键却是两套：`task` 工具事件的入参（`subagent_type`）记在 `_names`，键是**模型的 `tool_call_id`**；而容器起名时用的键来自 `_sub_items` = `delegation_id(namespace)`。
  - 会话 `78780d7a…` 的库里：子智能体 namespace 是 `tools:<UUID>`（25 个，如 `tools:723ad355-ec07-…`），而主控 state 里 `task` 的 id 是 `call_ded5238e…` / `call_00_ET_…`。把那几个 UUID 拿到主控的 `checkpoint_blobs` 与 `checkpoint_writes` 里搜 —— **0 命中**，两套 id 毫无关系。
  - 那串 UUID 是 langgraph 自己的 task id：`langgraph/pregel/_algo.py` 里就是 `task_checkpoint_ns = f"{checkpoint_ns}:{task_id}"`。
  - 顺带反证了两件事：日志里 `委派子智能体 → planner / coder / reviewer` 名字都在（不是 `?`），说明**入参没问题**；日志里 `[coder]` 5340 行、`[planner]` 1971 行，说明**子智能体的事件自带 `lc_agent_name`**（它和 namespace 是同一把键，天然对得上）。
  - 于是两个后果同时成立：容器名取不到角色 → 落「子智能体任务」；`_parents` 的键也对不上 → 子清单永远进不了主清单项。CHANGELOG 早先那条「子智能体清单挂到了兜底容器项、而非某个主委派项」的真因就在这里。
- **改动**（全在 `src/ai_coding_harness/stream.py`）：
  - `feed()`：namespace 非空时先调 `_note_child_agent(namespace, event)`。
  - 新增 `_note_child_agent()`：从 `metadata["lc_agent_name"]` 取角色名，`setdefault` 进 `_names[delegation_id(ns)]`（**与 `_sub_items` 同一把键**）；再认领一次委派。
  - `note_delegation()`：原有登记保留作兜底，另把 `(角色, 委派时主清单里 in_progress 的那一项)` 压进新增的 `_pending` 表。
  - 新增 `_pending_index()` + 认领逻辑：子智能体自报角色时，按角色取第一个还没被认领的委派（**同角色 FIFO**），把它的 `_parents` 指过去；同一个子智能体只认领一次（`_claimed`），角色取不到就不认领。
  - 新增 `_NO_AGENT_NAMES = {"", "None", "none"}`（与 `run_log._NO_AGENT` 同一口径；`run_log` 反过来 import 本模块，所以不能 import 那个常量）。
- **关键决策**：
  - **名字与挂靠都以「子智能体这一侧」为准**：它的事件与 `_sub_items` 共用 namespace 键，不存在 id 对齐问题；`task` 入参那份退成兜底（它仍然有用：子智能体还没开口时，至少日志与容器有名字）。
  - **认领靠角色 + FIFO**：两套 id 无法关联，只能靠角色牵线；同一角色并发委派时最坏挂到同角色的另一项下（可接受），认领不上就照旧走容器兜底 —— **不硬猜**。
  - **测试里的「假通过」一并修掉**：夹具原本让 `sub_ns()` 用与 `tool_start` 相同的 id（`v3_events.py` 里那句注释还写着「必须与 call_id 对上」），等于假设两套 id 相等 —— 真机不成立。新用例故意用不同的 id，并覆盖 FIFO 认领、认领不上、以及「两处都取不到名字」的兜底。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**851 条全过**（上一批 847，净增 4）；`ruff check` 改动文件干净；`scripts/check_prompts.py` 仍 0 条冲突（与提示词无关，按约定跑一遍）。
  - 新用例：子 ns id ≠ 委派 call id 时，容器名仍是「编码子任务」；同样形状下子清单挂到 in_progress 那一项下且树尾没有多余容器；同角色两次委派按先后各挂一项；角色对不上时不硬认领、容器名仍用自报角色。
- **遗留**：
  - **真机未验证**（沙箱连不上）：重启后端 + 新建会话跑一轮 coding，看任务树里是否出现「编码子任务」并且**挂在主项下面**。旧会话已经落库的树不会被改写（树是每轮全量下发的快照）。
  - 同一角色**并发**委派时靠 FIFO，顺序与实际派发顺序不一致时会挂到同角色的另一项下；要更准就得能在两侧共用同一个 id（langgraph 侧的事，暂不做）。

#### 新增 `AGENTS.md`：把协作约定写成仓库里的规则

- **动机**：用户明确要求「后续所有改代码的操作先出方案、等确认再改」。这类约定只放在对话里会随会话丢掉，而 Codex 会自动读取仓库根的 `AGENTS.md`，所以落成文件最稳；顺带把这段时间实测出来的命令与踩过的坑一并记下，省得下次重新摸一遍。
- **改动**：
  - **新增 `AGENTS.md`**（仓库根，中文），五节：① 协作方式（先方案后动手 / 只读排查不必问 / 明确放行后一次做完 / 多口径列 a-b-c）；② 改完必须做的三件事（全量 `pytest`、改动文件 `ruff` 干净、`CHANGELOG` 追加一条）；③ 环境与命令（conda 环境 `aicoding` + `PYTHONPATH=src`、`scripts/check_prompts.py`、前端 `npm.cmd --prefix frontend run build`、生效时机）；④ 硬约束与踩过的坑（提示词即行为契约、沙箱资产建会话时推入、`start.bat` 是 GBK、记忆写入口径、拦截是代码拦、两份模板逐字一致、Maven 已配好）；⑤ 去哪儿看（README / CHANGELOG / docs / scripts）。
  - **没有改任何代码**：这是一份纯文档改动。
- **关键决策**：
  - **只写稳定规则与实测事实，不写进度与待办**：进度归 `CHANGELOG.md`，AGENTS.md 一旦开始记进度，两处必然各说各话。
  - **命令都写成实测过的形状**：Python 走 conda 环境 `aicoding`（`pytest` 与 `ruff` 都在里面，已核）；前端注明 PowerShell 的 `npm.ps1` 可能被执行策略挡住、要退到 `npm.cmd`。
  - **只放 `AGENTS.md`，不建 `CLAUDE.md`**：同一份内容两份必然漂移。
  - **多写了一节「去哪儿看」**（原方案只有四节）：几条指路比在 AGENTS.md 里复述项目结构便宜，也不容易过期。
- **验证**：本次无代码改动，仍按新约定跑了一遍全量 `pytest`（**847 条全过**）与 `ruff check`（改动文件只有新增的 Markdown，不涉及 Python 文件）；`scripts/check_prompts.py` 仍是 **0 条冲突**。
- **遗留**：无。后续若发现某条约定过时或缺失，仍按「先方案、后改」走。

### 2026-09-25

#### 沙箱里的 Maven 加速与缓存：阿里云镜像 + 卷上的本地仓库

- **动机**：沙箱自带 Maven，但**每次构建都要重新下载依赖**，一轮编码里大半时间花在等网络上。两条原因：① 镜像走 Maven Central（国内慢）；② 本地仓库 `~/.m2/repository` 在容器里，而容器是按会话新建 / 可能被重建的 —— 依赖每次都得重下一遍。
- **改动**：
  - `sandbox/maven/settings.xml`（**新资产**）：`<mirror>` 指阿里云公共仓库（`https://maven.aliyun.com/repository/public`，`mirrorOf=*`）+ `<localRepository>/ai_coding/.m2/repository</localRepository>`。provider 把它推进 `/ai_coding/maven/settings.xml`，`setup.sh` 再复制到 `$HOME/.m2/settings.xml` —— 那是 Maven 默认就会读的位置，**任何 `mvn` 命令都不必加 `-s`**（skill 里写死的命令、coder 临时敲的命令一起受益）。
  - `sandbox/deploy/sandbox/setup.sh`：`mkdir -p /ai_coding/.m2/repository` 并把 settings.xml 摆到 `${HOME:-/root}/.m2/settings.xml`（幂等；建会话与重连都会跑，所以重连回来的容器也一定有）。
  - `src/ai_coding_harness/sandbox/volumes.py`：把「按后端造卷」抽成 `_build_volume`，新增 `build_maven_cache_volume` / `maven_cache_volume_name` —— **Maven 缓存默认跨会话共享一个卷**（`ai-coding-m2`，挂 `/ai_coding/.m2`），`SANDBOX_MAVEN_CACHE_SHARED=false` 退化成按会话各挂一个（`ai-coding-m2-<public_id>`）。
  - `src/ai_coding_harness/session/manager.py`：`_runtime_inputs` 现在挂**两个**卷 —— 工作区（`/ai_coding/project`）+ Maven 缓存（`/ai_coding/.m2`）；回填给库的 `workspace_volume` 仍是工作区那个（删会话的回收线索不变）。
  - `src/ai_coding_harness/config.py` + `.env.example`：`SANDBOX_MAVEN_DIR`、`SANDBOX_MAVEN_CACHE_ENABLED`、`SANDBOX_MAVEN_CACHE_SHARED`、`SANDBOX_MAVEN_CACHE_PREFIX`、`SANDBOX_MAVEN_CACHE_MOUNT_PATH`。
  - `src/ai_coding_harness/agent/prompts.py`：沙箱规则里补一条 —— 「Maven 已配好，直接用 `mvn`；不要自己再写 settings.xml / 换 `localRepository` / 清空本地仓库」。
  - 测试：`tests/test_sandbox_volumes.py`（**新增** 5 条：工作区卷不动、缓存卷默认共享、可退化按会话、可关、后端 none 时都不挂）、`tests/test_sandbox_provider.py`（3 条：资产里确实推了 settings.xml、那份 xml 同时含镜像与本地仓库、setup.sh 会把配置摆到 `$HOME/.m2`）、`tests/test_workspace_runtime.py`（1 条：建沙箱时两个卷都在）。
- **关键决策**：
  - **本地仓库路径写死在 `/ai_coding/.m2/repository`，与卷的挂载点是同一个口径**：卷挂上时它落在卷里（跨重建存活），没挂（`ENABLED=false` / 后端 `none`）时它就是个容器内目录，配置不用改，只是不持久。
  - **默认共享缓存卷**：依赖包与会话无关，共享之后第二、第三个会话一上来就是热的 —— 这正是「每次构建都要重下」的解药。代价是要求存储能多机同时挂（Docker 单机没问题；K8s 要么 RWX，要么把共享关掉）。多会话并发写同一个本地仓库有理论风险（Maven 自己有文件锁，最坏是某个包重下一次），所以给了开关。
  - **不把缓存放进 `project/`**：那会污染目标仓库（`git status` 里多一堆文件、`git add -A` 还可能把它带上），所以才另挂一个卷。
  - **不用 `-Dmaven.repo.local` / `MAVEN_ARGS`**：写 `~/.m2/settings.xml` 是 Maven 的默认口径，覆盖面最大（谁敲 `mvn` 都生效），也少一个「命令里忘了带参数」的失败模式。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**847 条全过**（上一批 838，净增 9）；本次改动文件 `ruff check` 干净（顺手清掉两条既有告警：`volumes.py` 的 import 顺序、`provider.py` 的 BLE001）。
  - 静态自检：settings.xml 里镜像 id / `mirrorOf` / URL / `localRepository` 四样都在；setup.sh 里有建目录与复制两步。
- **遗留**：
  - **真机没验证**（本机连不上 OpenSandbox）：新建会话后在容器里 `cat ~/.m2/settings.xml`、`mvn help:effective-settings | head`（应能看到 aliyunmaven 与 `/ai_coding/.m2/repository`），再跑两次构建对比第二次是否明显更快。
  - **共享缓存卷在多会话并发构建同一仓库**时有理论风险（Maven 文件锁 + 半成品文件）；先按默认共享跑，真撞上再 `SANDBOX_MAVEN_CACHE_SHARED=false`。
  - 删会话不删卷（与工作区卷同口径，`SANDBOX_VOLUME_DELETE_ON_TERMINATION=false`）：共享缓存卷本来就该留着；若切成按会话，会攒孤儿卷。
  - Python / Node / Go 没有对应的缓存卷配置（`pip` / `npm` / `go mod` 同理受益），需要的话按同一套模式再加。

#### 沙箱续期：活跃会话不再因为 TTL 到期被回收（`renew_if_due`）

- **动机**：沙箱有 TTL（建时传 `SANDBOX_TIMEOUT_MINUTES`，默认 60 分钟），到期就被 OpenSandbox 侧回收 —— 注意 `expires_at` 是**绝对时间**，不是「空闲多久才回收」。到期后再调用虽然能靠 `OpenSandboxProxy` 的「探活失败 → 重连 → 重建」恢复，但**重建会丢容器里非卷的内容**（`/ai_coding/tmp`、`/ai_coding/review`、临时装的依赖；只有 `project/` 在卷上）。三种场景本来都会白白重建/被杀：
  1. **用户隔一阵回来接着聊** → 轮首就该把寿命接上；
  2. **一轮里工具调用很密**（长会话） → 每次调用前顺手续；
  3. **一次工具调用自己跑很久**（一次 `mvn test` / 一次大 diff / 一次全量构建） → 这期间**没有别的沙箱调用**，前两条都轮不到，而到期是掐着绝对时间来的 —— 命令会被中途杀掉。这是用户补的一句「还有一个场景：一个任务长时间运行」。
- **改动**：
  - `src/ai_coding_harness/sandbox/client.py`：`SandboxClient` 新增 `renew(timeout) -> datetime`，包住 SDK 的 `Sandbox.renew` —— 语义是**重设**到期时间为「现在 + timeout」（不是叠加），返回新的绝对到期时间。
  - `src/ai_coding_harness/sandbox/proxy.py`：`OpenSandboxProxy` 新增公开方法 `renew_if_due()` 与内部 `_maybe_renew()` —— 距上次续期超过 `SANDBOX_RENEW_INTERVAL_SECONDS` 就续一次（续期目标取 `SANDBOX_TIMEOUT_MINUTES`）。**失败只记日志**（客户端类错误，如限流 / 参数错）不影响本次调用；若是「句柄已失效」那类（传输层异常，或 404/410/502/503），照既有纪律触发恢复（重连 → 重建）并重置续期时钟。续期用独立的 `_renew_lock` 单飞（与恢复那把锁分开，避免互相等），且**先记「尝试过」再发请求** —— 失败也不会让后续每个调用都重试一遍。
  - 同上文件：`_ensure_alive()` 每次沙箱调用前先过一遍 `_maybe_renew()`（节流窗口内只是一次浮点比较），所以**一轮里跑多久都会被续上**；`_rebuild()` 成功后在两个分支都重置 `_renewed_at`（换了句柄就重新计时，不让它刚接手就续）。
  - `src/ai_coding_harness/session/manager.py`：`ensure_runtime()` 取「已存在的运行时」时先 `await runtime.sandbox.renew_if_due()` —— **轮首续期**，覆盖「用户刚回来、还没跑任何工具」那一刻。
  - 同上文件（**调用进行中的心跳**）：`_call()` 用 `async with self._renewing()` 包住真正的那次调用 —— 进入时起一个心跳任务、退出时取消它，心跳每隔 `RENEW_TICK_SECONDS`（30 秒，或比间隔更小）检查一次「该续期了吗」（真正的节流仍在 `_maybe_renew` 里）。心跳里的任何异常都只记日志：**它绝不能让正在跑的那次调用失败**。调用本身仍是**直接 await**（不另起任务），所以「用户终止 → 取消本轮」的语义没变。
  - `src/ai_coding_harness/config.py` + `.env.example`：新增 `SANDBOX_RENEW_INTERVAL_SECONDS`（默认 1800 秒 = 默认 TTL 的一半；**0 = 关掉续期**）。
- **关键决策**：
  - **懒续期为主、轮首续期为辅**，不起后台心跳任务：所有沙箱调用都经代理，活跃会话自然被续；空闲会话本来就不需要保活，为它养一个 asyncio 任务不值。
  - **续期失败不算故障**：它是保命动作，不是本轮的业务结果；真过期了还有既有恢复路径。只有「句柄失效」那类才升级为恢复。
  - **失败也推进节流时钟**：否则一次限流会让后续每个工具调用都打一次续期请求。
  - **续期目标复用 `SANDBOX_TIMEOUT_MINUTES`**，不另立「续期时长」配置 —— 口径只有一个。
- **验证**：
  - `tests/test_sandbox_proxy.py` 新增 `TestTheRenewal`（假沙箱 / 假 provider + 可控时钟）：刚接手的沙箱第一次调用不续；到点续一次、间隔内不重复；`0` 关掉续期；`renew_if_due`（轮首入口）本身可用；客户端类错误（429 / 400 / 403）只记日志、不触发恢复、本次调用照跑；失败后同一间隔内不反复重试；句柄失效（传输层异常）走重连；重建后续期时钟重置；并发调用只续一次；**一次长调用（`run_delay`）进行中被续多次**、**调用一结束心跳就停**、**短调用一次都不续**、**心跳里续期失败不影响这次调用**、**关掉续期时没有心跳**。
  - `tests/test_workspace_runtime.py` 新增一条：`ensure_runtime` 取已存在运行时时会调一次 `renew_if_due`。
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**838 条全过**（上一批 821，净增 17）；本次改动文件 `ruff check` 干净。
  - 踩到一个测试环境的坑，值得记下来：那几条「长调用」用例**不能用**文件里那个冻结时钟的 `clock` fixture —— Windows 的 asyncio 定时器也走 `time.monotonic`，时钟一冻结 `await asyncio.sleep(...)` 就永远不醒（实测试跑挂住，得杀进程）。所以它们改用真实时钟 + 很小的续期间隔（0.05s）。
- **遗留**：
  - **没做真机验证**：本机到 OpenSandbox（`192.168.72.128:8080`）的 socket 被环境挡了（`WinError 10013`），续期请求没实打实发出去过。要确认两件事：① 你们那台 OpenSandbox 是否支持 `POST /sandboxes/{id}/renew-expiration`；② 续期后 `get_info().expires_at` 确实推后。跑一轮长会话、在 `logs/agent.log` 里找 `沙箱已续期：public_id=… 到期=…` 即可。
  - **续期只延长寿命，不解决「已经丢过的容器内状态」**：想让 `/ai_coding/tmp`、`/ai_coding/review` 跨重建存活，得把它们也放进卷（现在卷只挂 `project/`）—— 另一件事。
  - 间隔默认 30 分钟是拍的量（TTL 的一半）；若某类会话一轮常跑超过 60 分钟，可以把 `SANDBOX_TIMEOUT_MINUTES` 一起调大。
  - 心跳的 tick 目前是模块常量（30 秒），没做成配置项：它只是「多久检查一次」，真正决定续不续的是 `SANDBOX_RENEW_INTERVAL_SECONDS`，暂时不值得多一个旋钮。

#### 记忆写入改在「收尾」：出方案轮不写、coding 与 review 收尾各写一次、刷新归中间件

- **动机**：用户看过写入点之后定了口径 ——
  1. **出方案轮不该写记忆**：那时还没动代码，写进去的是「打算做的事」而不是事实；
  2. **内容只在 coding 与 review 之后写**（这两轮才有落地的事实：PR、审查结论）；
  3. **其他智能体开工时的记忆刷新归中间件**（`before_agent`），不该由模型管。
  另外：**plan 形态仍然要拦**（方案以 master 为基准，仓库还有未合入的 PR 时出的方案会建在错前提上）。
- **改动**：
  - `src/ai_coding_harness/tools/register.py`：`PLAN_FORM` **去掉** `save_repo_memory`；`REVIEW_FORM` **加上**它（审查结论是稳定事实，收尾要写回）。
  - `src/ai_coding_harness/agent/prompts.py`：`_MEMORY_GATE`（「第 0 步：先沉淀记忆」，开工前写）**换成 `_MEMORY_WRITE_BACK`**（「收尾时写回仓库记忆（本轮的最后一件事）」，写明在代码改完 / 审查出结论之后写、只写一次、不自己查 PR）；意图指令改为 —— **plan 不带**（并明写「不写仓库记忆」）、**coding 与 review 都带**；`_PLAN_WORKDIR` 的记忆行回到「（StoreBackend，**只读**）」；`_PLAN_RULES`、`_ROLE_PLAN_MAIN` 的可写范围收成「只有方案那一处」；`_ROLE_REVIEW_MAIN` 新增「## 收尾时写回仓库记忆」，措辞从「全程只读」改成「不改代码」（这一轮确实要写一次记忆）；`_REVIEWER_ROLE` 明写记忆只读；`_BASE_ENV` 的写回归属改成「只在 coding 与 review 两轮的收尾时由主控完成」。
  - `src/ai_coding_harness/agent/middleware/memory.py`：把原来揉在一起的「刷新」与「拦本轮」拆开 —— `REFRESH_FORMS`（**所有主控形态**）开工前都按 Gitee 实况刷新记忆（文件头 + 摘两张老表），`BLOCKING_FORMS`（`plan` / `orchestrate`）才在刷新后拦本轮；`MemoryMiddleware` 新增 `blocking` 开关。**`review` 刻意不拦**：它要审的那个 PR 本身就是 open 的，按「有 open PR 就拦」判会把自己永久挡在门外。子智能体（`form=None`）仍是纯只读注入（不查 PR）。
  - `src/ai_coding_harness/agent/middleware/__init__.py`、`agent/graph.py`：导出与文档同步（`GATED_FORMS` → `REFRESH_FORMS` / `BLOCKING_FORMS`）。
  - `src/ai_coding_harness/server/service.py`：轮末打底的判据从「形态是 orchestrate」改成「形态在 `_MEMORY_WRITE_FORMS` = {orchestrate, review}」。
  - `scripts/check_prompts.py`：C5 规则跟着反向（原来查「plan 记忆写成只读 = 与第 0 步相悖」，现在查「出方案轮**不得**声称要写记忆、且必须写清记忆只读」）。
  - 测试：`test_prompts.py` 的 `TestMemoryWritebackGate` 重写（只有 coding / review 带收尾写回、plan 不带、收尾在派发说明**之后**、平台已查过 PR、按模块列未完成功能、一轮只写一次）；`test_memory_gate.py` 拆出 `_refresh_only` 那条路并新增「只刷新不拦」用例、装配点改成「每个形态都刷新、只有两个形态拦」、轮末打底的参数化改成 coding/review 触发；`test_profiles.py` / `test_tool_registry.py` 的工具集与提示词断言同步；`test_intent_middleware.py` 的 plan 指令断言改成「你自己出方案、派不了 `task`」。
- **关键决策**：
  - **拦与写彻底解耦**。它们本来就只是「都需要查 PR」才被放在一起：现在 plan 只拦不写、review 只写不拦、coding 两样都有。三者的判据各自写在 `BLOCKING_FORMS` / `_MEMORY_WRITE_FORMS` 两个集合里，注释写明理由。
  - **review 不拦**是硬约束不是偏好：审查轮的输入就是 open PR，拦了这轮永远跑不起来。
  - **写入时机从「开工前」改成「收尾」**：原来要求开工第一件事就写记忆（理由是让 planner / coder 读到本轮刚沉淀的版本）；现在改成收尾 —— 这一轮产出的事实（PR 编号、审查结论）在收尾时才存在，开工前根本没得写。代价是同一轮里子智能体读到的仍是上一轮的记忆，而这正是用户要的（“之后才可以写入”）。
  - **刷新给所有主控形态**（含 qa / analysis / plan / review）：它们自己不写正文，但读到的记忆必须是新的（文件头 `最后更新 / 最近 PR` 与两张老表的清理都由它保证）。代价是每轮多一次 Gitee 查询 —— 用户明确要的就是这个（“其他智能体开始时由 middleware 负责更新”）。子智能体不加：同一轮主控已经刷过，让三个子智能体各再查一趟只是白花钱，而且 coder 刚建完 PR、reviewer 再查一遍会把自己挡在门外。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**821 条全过**（上一批 817，净增 4）；本次改动文件的 `ruff check` 干净。
  - `scripts/check_prompts.py` 冲突扫描 **0 条**（八份成品提示词）。
  - 行为面（假后端 / 假 Gitee，不碰网络）：`_gate`（plan / orchestrate 那条路）有 open PR 照样抛 `TurnStop`；`_refresh_only`（qa / analysis / review）同样把记忆刷新了文件头，但 `abefore_agent` 返回 `None`、不拦；轮末打底只在 coding / review 两轮触发。
- **遗留**：
  - **`qa` / `analysis` 每轮都多一次 Gitee 查询**（刷新用），只读问答轮的延迟会多一跳网络往返；要收窄就把 `REFRESH_FORMS` 改回子集。
  - **同一轮内子智能体读到的记忆仍是上一轮的**：写入挪到收尾之后的必然结果。如果哪一轮需要「本轮先沉淀、子智能体再读」，得另设计（例如收尾后再跑一次注入）。
  - 提示词里的「收尾时写」是**软约束**（模型可能忘）；工具层面没有强制（`save_repo_memory` 仍是随时可调）。要不要在轮末由平台检查「这一轮该写没写」，**待定**。
  - 尚未在活的后端生效（提示词构建期读、工具装配建会话时定）：重启 + 新建会话才算数。

#### 前端对话列宽改成自适应（原来写死 760 / 800px）

- **动机**：窗口越宽，对话区反而显得越窄 —— `.msg` / `.thinking` / `.composer` / `.notice-bar` / `.workspace-bar` 五处的 `max-width` 都是写死的 `760px` / `800px`，再配上 `margin: 0 auto`，屏幕变大只是两侧留白变多。
- **改动**（全在 `frontend/src/style.css`）：`:root` 新增两个变量 —— `--chat-max: clamp(760px, 60vw, 1400px)` 与 `--chat-pad: clamp(24px, 2.2vw, 48px)`；上面五处改用它们（左右留白也跟着 vw 缩放，宽屏上文字不贴边）。用户气泡另加绝对上界：`max-width: min(85%, 860px)` —— 贴一大段日志时不会跟着列宽横到 1400。
- **关键决策**：
  - **跟窗口走，但留上界**。`60vw` 保证「窗口变大就变宽」，`1400px` 封顶避免一行长到没法读（这个应用里既有散文也有代码块，1600+ 就开始难扫）。窄屏时 clamp 落到 760 下限，行为与改动前一致。
  - **留白也一起自适应**：列宽变大而 padding 不变会显得字贴边，所以 padding 用同一套 `clamp` 缩放。
  - **只动宽度，不碰别处**：侧栏 260px、计划面板 300px、窄屏那条 `max-width: 1100px` 的媒体查询都没动 —— 这次的问题只是「列本身不跟着长」。
- **验证**：
  - `npm --prefix frontend run build`（含 `vue-tsc` 类型检查）通过。
  - **真实浏览器 + 真实样式**量了四档视口（临时探针页 `frontend/public/width-probe.html` + 内嵌浏览器的 viewport 覆盖，量完即删、服务已停）：

    | 视口 | 消息列 | 输入区 | 用户气泡（短文本） |
    | --- | --- | --- | --- |
    | 1280 | 768 | 768 | 88 |
    | 1920 | 1152 | 1152 | 88 |
    | 2560 | 1400 | 1400 | 88 |
    | 3440 | 1400 | 1400 | 88 |

    改动前这五处一律 760 / 800（1920 屏上少用了近 400px，2560 屏上少用了 600px）。
  - 用户气泡那一列说明上界策略生效：列变宽不影响短消息的贴合宽度。
- **遗留**：
  - 只实测了 1280 及以上的视口；更窄的窗口靠块级元素「不超过容器」这条保证不会溢出（clamp 的下限 760 比容器宽时按容器走），没有逐档回归。
  - 1400 这个上界是拍的量：想更宽（比如 1600）或更窄，改 `--chat-max` 一个变量即可。

#### 方案对象归 plan 智能体：补齐增删改查（软删除走 `closed`）

- **动机**：方案一直是「只能新建」的产物 —— ``plan`` 形态的主控手里只有 ``storePlan``（加 ``get_pr_info`` / ``save_repo_memory``），用户说「把刚才那份方案第 2 步改一下」「那份方案别做了，删掉」时它**连方案全文都读不到**（``get_unexecuted_plan`` 只认 pending，而且只给了 coder）。于是把方案这个对象的**增删改查**收到 ``plan`` 形态手里。
- **改动**：
  - `src/ai_coding_harness/db/dao.py`：新增 `get_plan`（按 id + 限定 session，**含已删除的**）、`update_plan_content`（改正文 / 标题）、`set_plan_steps`（整份替换步骤）；`set_plan_status` 增加可选 `session_id` 限定并返回是否命中；`get_latest_plan` **排除 `closed`** —— 软删之后面板要回落到上一条或空，而不是继续显示一份「已删除」的方案。
  - `src/ai_coding_harness/tools/plan.py`：新增四个工具 —— `getPlan`（给 id 取那一条 / 不给取最新一份）、`updatePlan`（正文 / 标题）、`updatePlanSteps`（整份替换步骤，**会把状态重置为 pending**）、`deletePlan`（**软删**：状态改 `closed`，记录留在库里）。模块 docstring 写明三家的分工。原有的 `storePlan` / `get_unexecuted_plan` / `updatePlanStatus` / `updatePlanStep` 一个没动。
  - `src/ai_coding_harness/tools/register.py`：`PLAN_FORM` 的工具集加上后四个（`planner` 子智能体仍只有 `storePlan` —— 它跑的是 plan → coder → reviewer 那一整套的第一段，写方案就够）。
  - `src/ai_coding_harness/server/service.py`：`_PLAN_TOOLS` 补上 `updatePlan` / `updatePlanSteps` / `deletePlan`（改完推一帧快照给前端）；**快照为 `None` 也要推**（`plan: null`）—— 否则删掉最后一份方案后，前端会一直停在那份已经不存在的方案上。`getPlan` 不进这个集合：它只读，不该动面板。
  - `src/ai_coding_harness/agent/prompts.py`：`_ROLE_PLAN_MAIN` 新增「## 方案由你管到底（增删改查）」段 —— 四个工具各自的用途、**删除前必须先向用户说明是哪一份方案并拿到明确同意**、正在执行的方案不许改步骤、只改用户点名的那一处；「可写范围」同步改成三处写（记忆 / 方案 / 结论）。
  - 测试：`tests/test_plan_tools.py` 新增 `TestPlanCrud`（软删成 closed、重复删有话说、未找到、改正文改标题、两个字段都不给直接回错、删后不许改、执行中拒绝改步骤、按 id 读得到已删的那份、默认取最新）+ 工具清单改成八条；`tests/test_profiles.py` 的 `_WRITE_TOOLS` 与 plan 形态工具集同步，并新增「别的形态一个 CRUD 工具都没有」；`tests/test_tool_registry.py` 新增 plan 形态拿到五个方案工具 + 其余四形态一个都没有；`tests/test_plan_events.py` 新增「每个写方案的工具都推快照」「`getPlan` 不推」「删除后推一帧 `plan: null`」。
- **关键决策**：
  - **实施进度仍由 coder 回写**（你选的 a）。coding 轮里在场的是 orchestrate 主控 + planner / coder，planner 只在出方案时跑一次 —— 进度若也归 plan 侧就没人写了，右侧面板会停在 pending。所以这次只把**方案对象本身**的增删改查收过去，`updatePlanStatus` / `updatePlanStep` 留在 coder 手里。
  - **删除是软删**（你定的 `closed`）：记录留在库里、可追溯，`get_latest_plan` 与 `get_unexecuted_plan` 都不再认它（前者排除 closed，后者本来就只认 pending）。
  - **执行中不许改步骤**：`updatePlanSteps` 在 `executing` 状态直接拒绝 —— 改了会让 coder 手上的计划与库里的对不上，而这正是「进度由 coder 回写」那条分工的代价，只能靠拒绝来守。
  - **每个写工具都带 session 限定**：模型给的 `plan_id` 可能来自别的会话或纯属猜的，跨会话改 / 删方案不该发生；`WHERE` 里加了 `session_id`，命中不了就回「未找到方案 N（或它不属于本会话）」。
  - **删除要用户点头**（提示词里的硬要求，与 `close_pr` 同款）：软删虽然可追溯，但用户看到的是「方案没了」，不能由模型替他决定。
  - **顺手清掉了 8 条既有的 `RUF100`（unused noqa）**：都在本次改动的文件里（`tools/plan.py` 与 `tests/test_plan_events.py`），属于「改了就得让它干净」；全仓既有告警从 16 条降到 8 条。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**817 条全过**（上一批 792，净增 25）；本次改动文件的 `ruff check` 干净。
  - 交互面（假 dao，不碰真库）：软删确实写成 `closed` 且只改那一行；重复删 / 未找到 / 已删后再改都有明确回执；执行中改步骤被拒且**没有**触发写库；`getPlan` 默认取最新、按 id 能读到已删的那份。
  - 事件面：六个写方案的工具各推一帧快照、`getPlan` 一帧都不推、删除后推 `plan: null`。前端那侧 `plan.value = ev.plan ?? null` 本来就吃得下 null（未做前端实测）。
- **遗留**：
  - **没有「列本会话所有方案」的工具**：`getPlan` 只能按 id 或取最新一份。用户说「把第三份方案删了」时，模型手上没有编号，只能先问或先取最新 —— 要不要加 `listPlans`，等你定。
  - **软删只进不出**：`closed` 的方案会一直留在 `plans` 表里，没有清理策略（和会话、记录一样，先不删）。
  - **`updatePlanSteps` 是整份替换**：没有「只改某一步的 goal」这种细粒度工具；改完进度重置为 pending，执行中的方案还会被拒。
  - **尚未在活的后端生效**（提示词是构建期读盘，工具装配是建会话时定），要重启后端 + 新建会话才算数。

#### 仓库记忆改格式：两张表都撤掉，「未完成的功能」由模型写进「当前进度」

- **动机**：仓库记忆的模版一直在两张表上打转（``需求列表`` / ``功能列表``：前者平台按 PR 状态回填四列，后者是它的派生视图），而这两张表都不是用户要的东西：
  - ``功能列表`` 与「变更记录」重复 —— 变更记录本来就是以 PR 为角度的（每条改动挂着 PR 编号 / 链接），再列一张功能表只会两处各说各话；
  - ``需求列表`` 本质是「Gitee 上开着哪些 PR」的一份**缓存**，而闸门真要的那件事（仓库有没有还没落地的改动）可以直接查 PR 列表，不必先落到表里再读回来。
  真正缺的是第三样：**「当前进度」里要能看到每个模块还没做完的功能** —— 那是下一轮「该做什么」的出处，只有模型知道（模块怎么划、哪些事没做完），平台从 PR 状态推不出来。
- **改动**：
  - `sandbox/policies/repo-memory-template.md` 与 `docs/repo-memory-template.md`（**两份同时重写，且内容逐字一致**）：节次收成五节 —— 一、项目概览／二、变更记录（**以 PR 为角度**，每条写清 PR 编号 / 链接）／三、当前进度（**未完成的功能按模块全部列出**，另留一行可选的「最近一轮」）／四、约定与注意事项／五、关键决策。头行仍由平台维护，模版里明说这一点、也明说两张老表会被摘掉。此前两份内容跑偏了三周（docs 那份还留着功能列表 + 需求列表，sandbox 那份只有需求列表），现在加了一条比对测试钉住。
  - `src/ai_coding_harness/agent/memory_reconcile.py`（**大幅收窄**）：平台维护的部分只剩**文件头那一行**（`最后更新` / `最近 PR`，时间戳仍只在别的内容真变了时才前进）。删掉整张表机制 —— `Need` / `_NEED_HEADER` / 表解析与渲染（`_cells` / `_span` / `_rows` / `_put_table` / `_need_row`）/ 状态回填（`_reconcile_need`）/ 补行（`_seed`）/ 裁剪（`_trim`）/ 建表（`_build_needs`）。新增 `OpenPull`（编号 / 标题 / 分支 / 链接）与 `_open_pulls()`：**闸门判据直接取本轮查到的 PR 列表**（`state=open` 才算），与记忆里写了什么无关。`_drop_legacy()` 现在摘**两节**（`需求列表` 与 `功能列表`，仍是「去掉节号后正好等于」的严格匹配，模型自己写的「历史功能列表」不牵连）。
  - `src/ai_coding_harness/agent/middleware/memory.py`：闸门改用 `OpenPull`；marker 文案从「下面这些需求还挂在 PR 上」改成「下面这些 PR 还开着，改动尚未落地」（分支取 PR 的 `head.ref`，不再是表里那列）；「拦之前先刷记忆」的语义不变。
  - `src/ai_coding_harness/tools/memory.py`（含 `tools/register.py` 的一处注释）：落盘前的对账仍跑，但现在只做「重算文件头 + 摘两张老表」；工具描述改成「文件头归平台盖章；其余内容（含按模块列出的未完成功能）由你写；两张老表会被摘掉，别再写」。拒写条件不变（查不到 PR 就整篇不写）。
  - `src/ai_coding_harness/agent/prompts.py`（四处）：记忆规则里那句「四列由平台回填、你只管 `需求` 那一列」删掉，换成「只有文件头那一行由平台维护；其余全部由你写 —— 变更记录以 PR 为角度、**当前进度里按模块列出所有未完成的功能**」；`_MEMORY_GATE` 同口径；另一处「记忆块里的 `## 一、项目概览`……、需求列表」改成「……、「当前进度」里按模块列出的未完成功能就是这一轮该做什么的出处」。
  - 测试：`tests/test_memory_reconcile.py` **重写**（24 条）—— 散文逐字保留、**不再生成任何表**、「两张老表整节摘掉且算作 changed」、「正好叫表名」才摘、文件头三种形态、头行时间戳只在真变了时前进，以及闸门判据（只认 `state=open`、认不出的状态不算、没编号不算、merged/closed 不拦）；新增「两份模版逐字一致」与「模版要求按模块列未完成功能」两条。`tests/test_memory_gate.py` / `tests/test_memory_tool.py` 的 fixture 换成**不带表的记忆**（头行过期 → 对账该把它刷新），断言改成看文件头；「平台补一行」「模型伪造已合入被改回」两类用例换成「平台不再往记忆里写任何表」「模型写回两张老表会被摘掉」。`tests/test_prompts.py` 的「四列由平台回填」改成「头行归平台 + 要求按模块写未完成功能」。
- **关键决策**：
  - **闸门换锚点，行为等价**。旧机制里 `_seed` 会把每个 open PR 补成「待合入」行，`pending` 就是这些行；现在直接判 PR 列表里的 `state=open` —— 拦不拦与以前一致，但少了一层会腐烂的缓存（表里漏一行就不拦，是旧机制真实存在过的口子）。
  - **两张表都摘，不是只摘一张**。功能列表与变更记录重复这一点由用户点明；需求列表则是「平台不再维护的缓存」，留着只会被当成散文保留下来、里面的状态永远是死的（这正是 2026-09-18 摘功能列表时的理由，现在同样适用于需求列表）。
  - **平台只留文件头，是把「事实」与「判断」分开**：`最后更新` / `最近 PR` 是平台能核的事实；「哪些模块还没做完」是判断，归模型。两条互不纠缠，也就不需要平台再按 PR 状态去推导表里的状态。
  - **模版两份必须逐字一致**（沙箱那份生效、`docs/` 那份给人看）。此前它们各自演化过一次，跑偏三周没人发现；现在由一条测试钉住，改一份不改另一份当场红。
  - **不做迁移脚本**：老记忆里的两张表会在下一次写记忆（plan / coding 轮的闸门刷新或 `save_repo_memory`）时被自愈摘掉；「当前进度」里只剩「待办 / TODO」一行的老记忆，等模型下一轮写记忆时补成按模块的未完成功能。
- **验证**：
  - 全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**792 条全过**（上一批 796：删掉了表机制的 20 余条、新增 8 条，净减 4）；`ruff check` 本次改动文件干净（全仓仍是那 16 条既有告警，全在未改动文件里）。
  - 模版条文自检：两份逐字一致、含「未完成的功能（按模块，全部列出）」与「以 PR 为角度」。
  - 行为自检（真代码、不碰网络）：老记忆（功能列表 + 需求列表）过一遍对账 → 两节连标题一起消失、散文一行不少、`changed=True`；再跑一遍 → `changed=False`（稳态）；PR 列表里只有 open 的才进 `pending`。
- **遗留**：
  - **只读形态不会触发对账**（qa / analysis / review 主控与三个子智能体只读注入）：老记忆里的两张表要等下一次 plan / coding 轮才会被摘掉；在那之前它们读到的是带表的老文本。
  - **老记忆的「当前进度」不会自动补齐**：没有迁移脚本，按模块的未完成功能要等模型下一轮写记忆时补。
  - **两份模版仍是两个文件**（内容由测试保证一致）；要合成一份得改上传路径口径（`config.sandbox_local_policies_dir` 指向 sandbox/policies）。
  - **尚未在活的后端生效**：提示词与模版是构建期 / 建会话时读的，要重启后端 + **新建会话**才算数。

#### 压缩流水改落 StoreBackend：沙箱会没，store 不会（`/conversation_history/` + `/large_tool_results/`）

- **动机**：deepagents 的摘要中间件把两类「溢出产物」写在沙箱里 —— `/conversation_history/<thread_id>.md`（被压缩掉的原始消息，每次压缩追加一节）与 `/large_tool_results/…`（`ContextOverflowError` 时过大的工具结果）。而沙箱**会话结束就销毁、卷只挂 `/ai_coding/project`**：于是摘要消息里那句「完整历史已保存到 <路径>，需要细节时可回看」在沙箱重建后是一句空话；2026-09-21 那次实测里，`/conversation_history/…md` 的 404 甚至被沙箱代理当成「沙箱没了」触发了一次重建（该缺口记在 2026-09-18 那条的遗留里）。会话一重建、进程一重启，这份流水就没有第二份副本。
- **改动**：
  - `src/ai_coding_harness/backends/composite.py`：`build_composite_backend` 新增两条路由 —— `"/conversation_history/"` → `StoreBackend(namespace=("conversation_history",))`、`"/large_tool_results/"` → `StoreBackend(namespace=("large_tool_results",))`，并**显式传 `artifacts_root="/"`**（前缀是按它拼的，把契约钉在我们这一侧）。前缀提成模块常量 `HISTORY_PREFIX` / `LARGE_TOOL_RESULTS_PREFIX`，模块注释写清它们与 deepagents 那两处写盘的对应关系。**没动 deepagents、没改提示词、摘要消息里的路径一字不变**（路径经 `CompositeBackend` 路由到 store，写/读/编辑/`ls`/`grep` 全走通）。
  - `tests/test_composite_backend.py`（**新增 8 条**）：两条前缀确实落进各自 namespace、默认后端一个字节没收到；其它路径（`/ai_coding/project/…`）照旧走默认后端；**换一个 composite 实例仍读得到**（沙箱重建 / 进程重启的等价断言）；摘要中间件的真实调用序列在 store 上成立（首次 `adownload` 得到 `file_not_found` 而**不是异常**、第二次 `aedit` 追加且 `occurrences=1`、两节都在）；模型侧 `aread` / `als` 读得回；`artifacts_root` 被钉在 `"/"`；最后一条直接拿 `create_summarization_middleware` 算出来的 `_history_path_prefix` / `_large_tool_results_prefix` 与我们的路由常量比对 —— 上游改默认值或改前缀名时当场红。
- **关键决策**：
  - **走路由，不动 `artifacts_root` 去蹭 `/memories/`**。另一条更省事的路是把 `artifacts_root` 设成 `"/memories/"`，让流水顺着现有记忆路由落库 —— 但那样「会话流水」与「仓库长期记忆」共用一个 namespace，摘要消息里还会出现 `/memories/conversation_history/…` 这种看着就像仓库记忆的路径（我们刚把记忆口径收紧过，不值得为省一条路由把语义搅浑）。
  - **不自建摘要中间件**。也可以排除上游那份、自己挂一个 backend 固定成 StoreBackend 的实例，但那要复刻 deepagents 的整层包装（offload、非破坏性 state、内部调用事件标记），而我们刚查过的「摘要调用绕过兜底」这类问题只会更难定位 —— 收益与代价不成比例。
  - **`StoreBackend` 缺异步 `adownload_files` 这个坑是实测踩出来的**：offload 用 `await backend.adownload_files([path])` 读旧内容来追加，而 `StoreBackend` 只实现了同步 `download_files`。它能走通是因为协议基类给了默认实现 `asyncio.to_thread(download_files)`——**这件事不写进测试就没人记得**，所以单测里专门钉了「首次读返回 `file_not_found`、不抛异常」与「二次追加不覆盖」两条。要是哪天上游去掉那个默认实现，offload 会静默退化成「每次覆盖」，这两条会红。
  - **前缀不做成配置项**。路径由 deepagents 决定（`artifacts_root` + 硬编码名），配置化只会让两边口径可能不一致；要跟上游对齐就对齐在代码常量 + 一条比对测试上。
  - **namespace 按「数据归属」分**：仓库记忆是跨会话的长期事实（`("memories",)`），另外两个是会话级流水（key 里带 `thread_id`）——同库不同 namespace，互不干扰，也不影响记忆的读写路径。
  - **脱敏口径补一句**：流水里是被压缩的**历史消息**（工具输出在进 state 之前已经过 `RedactionMiddleware`），所以内容本身已脱敏；但改动之后它多了一个落点（Postgres 的 store），此前 CHANGELOG 说的「四条出口」在这里应理解为「落库这条出口又多了一处写入方」。
- **验证**：
  - 单测：`tests/test_composite_backend.py` **8 条全过**；全量 `pytest`（conda 环境 `aicoding` + `PYTHONPATH=src`）**796 条全过**（上一批 788，净增 8）；`ruff check` 改动文件干净。
  - 另跑了一段不碰沙箱的真代码探针（`InMemoryStore` + `StoreBackend` + `CompositeBackend`）：`awrite` → `adownload`（首次 `content=None, error=file_not_found`）→ `aedit`（`occurrences=1`）→ 追加后两节都在 → **换一个 composite 实例仍读得到** → 模型侧 `aread` / `als` / `agrep` 全部正常。这就是「沙箱释放后还读得到」的等价验证（真机上换成 Postgres store，与仓库记忆同一套）。
- **遗留**：
  - **会话销毁时不清理这两份流水**（本次按决定先不做）：`StoreBackend` 没有 delete，要清就在 `session/manager.py::destroy_session` 里用底层 store 删 `("conversation_history",)/<thread_id>.md` 与 `("large_tool_results",)/<thread_id>/…`。现状是删了会话、checkpoint 与流水都留在库里 —— 好处是可复盘，代价是孤儿数据随会话数增长。
  - **流水体量没有上限**（本次按决定先不做）：每条 thread 的 `/conversation_history/<thread_id>.md` 随压缩次数线性增长（每次追加一整批被压缩消息）。要设上限得在写入后裁剪（保留最近 N 节），那要么包一层中间件、要么等 deepagents 出配置。
  - **主控与子智能体共用同一个 backend 前缀**：`deepagents/graph.py` 给子智能体也挂了同一套摘要中间件，而 thread_id 取自运行上下文 —— 同一会话里主控与子智能体的流水**可能落在同一个 key 上**（也可能按各自的上下文分开，未实测）。要不要按角色/run 分开 key，等真机看到实际写入形状再定。
  - **迁移**：本次改动只影响**新的写**。改动之前已经落在某个活沙箱里的那份流水不在 store 里，摘要消息指向的路径会读到 `file_not_found`，直到下一次压缩追加时才补上；没做「rehydrate 时把沙箱里那份搬进 store」的一次性搬迁（收益小、路径在容器根、还得经沙箱代理）。
  - **尚未在真机验证**：需要重启后端 + 跑一轮长会话触发一次压缩，看 store 里出现 `("conversation_history",)` 的记录、且沙箱 `/conversation_history/` 是空的。
  - 与本次改动相邻的老缺口没动：**摘要模型的调用绕过兜底中间件**（`MODEL_FALLBACK` 管不到摘要那一跳）。

#### 提示词自检：清掉六组「同一份提示词里互相打架」的指令（新增 `scripts/check_prompts.py`）

- **动机**：把五个主控形态与三个子智能体的**成品提示词**（经 `build_main_prompt` 拼装后的）逐个核了一遍，找出六组「同一份提示词内部」互相打架的指令。这类矛盾比「少写一句话」贵得多：模型每次只挑一句信，挑中哪句随上下文，行为就成了随机的 —— CHANGELOG 里记着的几次事故（清单塌成「执行计划中」、记忆写回被掐断、只读形态收到写命令）都是同一形状。分六组：
  1. **清单口径自相矛盾**：通用规则第 2 条末尾「清单不要照抄方案的步骤 …… 这时清单只写你自己的动作」与第 3 条「一项对应方案的一步：把方案的每一步各列一条」是对同一份文本的**相反要求**；而第 3 条给的示例（「执行方案步骤 2：改密钥存储」）本身就是一次复述。这条口径此前改过两版，两版都在真机上失败过（只留措辞 → 塌成一条占位；只留粒度 → 整段照抄）。
  2. **技能规则按 orchestrate 主控的口吻写，却挂在八个智能体上**：`_BASE_TAIL` 第 3 条要求「必须优先委派 `reviewer` 子 Agent」，第 4/5 条要求「主 Agent 用 `list_pr_comments` 取回原文、用 `comment_pr` 在 PR 上回复」。可 qa / analysis / plan / review 四个形态都没有 `task`，其中 qa / analysis / plan 也没有 `comment_pr`（`tools/register.py` 的 `_FORM_TOOLS`），review 形态与 reviewer 自己的角色段写的又都是「你自己审」—— `REVIEWER_PROMPT` 因此叫 reviewer **去委派 reviewer**。
  3. **plan 形态「零写操作」对撞「第一件事是写记忆」**：`_PLAN_WORKDIR` 把记忆写成「**只读**」，`_PLAN_RULES` 写「不产生任何写操作」、`_ROLE_PLAN_MAIN` 写「本轮一个文件都不落」，而 intent=plan 时由 `IntentMiddleware` 注入的 `_MEMORY_GATE` 要求「本轮的**第一件事**是用 `save_repo_memory` 更新一次记忆」—— plan 形态确实持有这件工具。模型照「零写操作」跳过第 0 步是**合理读法**（与「记忆写回路径被掐断」那次同形）。
  4. **`GIT_PROMPT` 把「怎么写代码」挂在四个不写代码的形态上**：共用段里列着 `git clone` / `checkout -b` / `add -A` / `commit -m` / `push origin`，而 orchestrate 主控（不亲自写代码）、plan 主控与 planner（出方案零动手）、review 主控与 reviewer（全程只读）都收到了这份说明书；`_PLAN_METHOD` 第 3 步还让 plan 形态自己去 `git push` + `create_pr`（它没有 `create_pr`）。
  5. **共享「工作区规则」逐目录声称可写，与策略层不符**：`/ai_coding/review/`：「可写」、`/ai_coding/tmp/`：「可写」两行是写给所有形态的，而 `safety/policy.py` 里 qa 写根为空、plan 写根为空、analysis 只有 tmp。同一段的「成果要及时落地：代码 commit + push」也是无条件句，与只读形态自己的「不 `git add` / `git commit` / `git push`」正相反。
  6. **三处「第一件事」竞争 + 记忆归属两说**：`_MEMORY_GATE` 说第一件事是写记忆，`TODO_PROMPT` 与主控角色段说先把这一轮看懂再落清单（都是绝对句，先后只能靠模型自己排）；`_BASE_ENV` 说「写回只由主智能体用 `save_repo_memory` 完成」，只读形态的能力边界又说「没有 `save_repo_memory`」；联网搜索规则把记忆**文件路径**列为「优先读」，而仓库记忆规则刚说「不要再 `read_file` 读它」。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`（清单口径）：`TODO_PROMPT` 第 2 条末尾与第 3 条**合成一条**——粒度（一项对应方案的一步、不许塌成占位）与措辞（用你自己的话、一条一行、不搬 goal / done_criteria 原文）写在同一句里，示例改成「步骤 2：改密钥存储」（对两条都成立）；`_CODER_ROLE` 第 3 步同口径，第 5 步那条「你的清单只管自己的动作，不复述方案」改写成「清单与方案的分工」（方案原文与逐步状态在方案面板，清单写一步在动什么），不再出现「只写你自己的动作 / 不复述方案」这类二值禁令。
  - 同上文件（技能规则归属）：`_BASE_TAIL` 的「技能规则」收敛成 4 条通用话（哪件事用哪套 skill + skill 只是方法），**删掉委派与回评两条**；`_ROLE_ORCHESTRATE` 的「职责一」补上「**审查一律派 `reviewer`**（用 `code-review` skill 的方法），主控自己不做审查」与「先取回审查原文（`list_pr_comments` 或 `/ai_coding/review/` 下的报告），**不得凭记忆复述**，据此再派 `coder`；修复范围等于用户点名的问题，随后 `comment_pr` 回评」；`_REVIEW_METHOD` 第 2 步点名 `code-review` skill 的检查清单（review 主控与 reviewer 共用，两处同时落地）。
  - 同上文件（plan 记忆）：`_PLAN_WORKDIR` 那行不再写「只读」，改成「（StoreBackend）——本角色的读写权限见「你的角色」段」；`_PLANNER_ROLE` 新增「## 仓库记忆只读」段（StoreBackend，**只读**；写回由主智能体负责）；`_PLAN_RULES` 与 `_ROLE_PLAN_MAIN` 的写白名单改成「本轮的写**只有两处**：`save_repo_memory`（第 0 步）与 `storePlan`」，并把「一个文件都不落」限定成「**不落沙箱文件**」。
  - 同上文件（git 段拆分）：`GIT_PROMPT` 只留规则与**只读**命令（`git status` / `diff` / `diff --stat` / `log` / `show`）+ 凭据禁令 + 分支命名 + 「PR 用工具创建、合并人工」；新增 `_GIT_WRITE_STEPS`（克隆 / 更新 / 建分支 / 提交 / 推送五条命令），**只拼进 `CODER_PROMPT`**。`_PLAN_METHOD` 第 3 步写明这一步**由 coder 执行**。
  - 同上文件（工作区与记忆口径）：`_BASE_ENV` 的「工作区规则」不再逐目录声称可写，改成「**你能写进去哪些目录，以你这份提示词的「工作目录」段为准**」，只保留硬不变量（policies / skills 只读、output 谁也不写、.secrets 不可读写）；生命周期那句改成「**这一轮改了代码就及时 commit + push**（只有改码的形态适用）」；`_BASE_ENV` 的记忆写回归属改成「写回只由**持有 `save_repo_memory` 的主控形态**完成……只读形态与三个子智能体都不写记忆」；联网搜索规则的「仓库上下文」改成指向注入的「仓库记忆」块（别再读盘）。
  - 同上文件（工作目录补全）：`_ROLE_ORCHESTRATE` 的「工作目录」补上 review（可写，审查原文）与 tmp（可写），project 那行从「**只读**」改成「**本轮不写**（写码由 coder 负责；核实现状只用只读命令）」—— 边界允许写、口径不许写，说「只读」是句假话；`_REVIEW_WORKDIR` 补上仓库记忆（**只读**）；`_ROLE_QA` / `_ROLE_ANALYSIS` 的工作目录各补一行仓库记忆（**只读**，写回不归这一轮）。
  - 同上文件（顺序）：`_MEMORY_GATE` 第一句改成「在 `write_todos` 之前、`storePlan` 之前，也在派 `planner` / `coder` / `reviewer` 之前（它是记忆写回，不算「落文件」）」—— 把三处「第一件事」的先后钉死，并点明它不违反「不落文件」。
  - `start.bat` / `start.sh`：`PYTHON=` 默认值从已经失效的 `D:\python-venv\ai_coding\Scripts\python.exe` 改成 conda 环境 `aicoding` 的解释器（`D:\developmentBinary\miniconda3\envs\aicoding\python.exe`，`conda env list` 可见），并各加一段**启动前自检**：真跑一次 `python -c "import sys"`，失败就打印「Python 无法启动 + 项目运行环境是 conda 环境 aicoding」并退出。用「试跑」而不是「判文件在不在」是有理由的：那个坏掉的 venv 里 `Scripts\python.exe` **文件是在的**（255KB），`if not exist` 判不出来，只有真跑一次才知道它起不来。`start.bat` 是 **GBK 编码**，改动是用 `[System.Text.Encoding]::GetEncoding(936)` 读—改—写完成的，校验过：无 BOM（首三字节 `40 65 63`）、无 U+FFFD、按 UTF-8 严格解码仍失败（说明编码没被改坏）、`启动后端` / `未找到 .env` 等既有中文全部完好。
  - `scripts/check_prompts.py`（**新增**）：渲染八个成品提示词并扫七类跨形态冲突（C1 清单口径 / C2 越界委派 / C3 越界回评 / C4 越界写命令 / C5 plan 记忆 / C6 目录可写 vs `policy_for` 的实际写根 / C7 只读形态的落地要求）。它只加载 `profiles` / `prompts` / `policy` 三个纯模块（`config` 打桩），因此**不依赖第三方包**，在装不起依赖的机器上也能跑；`--grep 仓库记忆` 可按关键字打印各形态的原文。改前 44 条、改后 0 条。
  - 测试：`tests/test_prompts.py` 的 `TestTheListCarriesThePlanSteps` 按合并后的口径重写（新增「相反要求不许回来」一条：既钉「一步一条」在，也钉「清单只写你自己的动作 / 不复述方案」**不在**）；新增 `TestSkillRulesBelongToTheFormThatDoesTheWork`（只有 orchestrate 提委派与回评、`code-review` skill 落在审查那三份）与 `TestGitWriteCommandsOnlyReachTheCoder`（写命令只在 `CODER_PROMPT`、只读命令留在共用段、plan 末步写明由 coder 执行）；`TestNoDanglingReferences` 的「子 Agent 名字要对」改钉 `MAIN_AGENT_PROMPT`（名字归唯一挂子智能体的形态）；`TestReviewReply` 的「不得凭记忆复述」改钉 `MAIN_AGENT_PROMPT` + `REVIEW_AGENT_PROMPT`（原断言钉在共用段，而那句已经挪走）；记忆写回归属那条断言随文案更新。`tests/test_profiles.py` 的 `test_plan_form_lands_no_files_at_all` 改写成 `test_plan_form_writes_only_the_memory_and_the_plan`，并新增三条（plan 记忆不再写成只读、review 形态的写目录与记忆只读、共用目录表指向按形态的「工作目录」段）与一条「没有形态收到无条件 commit + push」。
- **关键决策**：
  - **清单口径走「一步一条 + 用自己的话」，不改回任何一版**。粒度不能丢（丢了两版实测都塌成占位），措辞也不能丢（丢了就整段照抄）；两者能同时成立的写法只有「条目 = 步骤号 + 你自己的一句话」，所以示例也必须换成对两条都成立的形状 —— 原来那个「执行方案步骤 2：改密钥存储」正是矛盾的另一半。
  - **委派与回评搬进 `_ROLE_ORCHESTRATE`，而不是给共用段加限定词**。共用段加「你这条不适用」的补丁，下一个人加形态时还会漏；搬进唯一具备该能力的形态段是结构性的（`_FORM_TOOLS` 与 `profiles.py` 已经保证「只有 orchestrate 挂子智能体、只有它拿 `comment_pr`」）。
  - **plan 的记忆写成「见你的角色段」而不是一律改成读写**。`_PLAN_WORKDIR` 是 plan 主控与 planner **共用**的，而两者权限相反（主控持 `save_repo_memory`、planner 连写工具都没有）—— 共用段不能替谁下结论，只报事实（路径 + StoreBackend），权限归各自角色段。
  - **git 写命令只给 coder，而不是给各形态各写一句「你别照着做」**。与仓库一贯的取舍一致（先撤选项）：plan / review 需要的只是「方案末步是提交 PR」和「怎么读 diff」，`git add` / `commit` / `push` 的写法对它们是纯噪声 —— 这次是从共享文本里**移走**，不是再加一句劝阻。
  - **共用段不再逐目录声称可写**，改成指向按形态的「工作目录」段。目录权限本来就随形态变（同一张表不可能对五个形态都成立），沿用「说不清就别在这里说」的老口径（与上一批撤 output 同因）。
  - **orchestrate 的 project 写根这次没动 policy，只把提示词里的「只读」改成「本轮不写」**。policy 仍留着 project 写根（它可能在派发前后自己跑 git 写操作，撤掉有真实风险），但提示词不能再说「只读」—— 那是句与边界相反的假话。这一处的最终形态（撤写根 vs 保留写根 + 明说口径）仍待你定，本批只是消掉「假话」那一半。
- **验证**：
  - `scripts/check_prompts.py`：改前 **44 条**冲突（C1×8、C2×7、C3×3、C4×13、C5×1、C6×9、C7×2，去重后逐条打印），改后 **0 条**。
  - 另一组按新测试口径写的 13 条不变量自检全部通过（orchestrate 独占委派与回评、`code-review` skill 落在审查三份、plan 记忆不再只读且写回进白名单、planner 记忆仍只读、写命令只在 coder 那份、清单两条不再互相否定、四个单智能体形态都无无条件 commit + push）。
  - `ruff check` 本次改动文件（`prompts.py` / `scripts/check_prompts.py` / 两个测试文件）**All checks passed**；`py_compile` 四个文件通过。
  - **全量 `pytest`：788 条全过**（`PYTHONPATH=src`，解释器 `D:\developmentBinary\miniconda3\envs\aicoding\python.exe`，9.48s；两条 warning 是既有的 v3 流式协议实验性提示，与本次改动无关）。跑第一遍时抓到一条**真失败**：`TestReviewReply::test_reply_must_quote_source_not_memory` 原来钉「不得凭记忆复述」在 `BASE_PROMPT` 里 —— 那句正是本批从共用段挪进 orchestrate 角色段的，断言跟着改成钉两个能回评的形态（已一并修正后才全绿）。
  - 运行环境（用户确认 + 本机实测）：项目的运行环境是 **conda 环境 `aicoding`**（`conda env list` 可见，实质路径 `D:\developmentBinary\miniconda3\envs\aicoding`，Python 3.14.0，依赖齐全）。标准跑法：`conda activate aicoding` 之后 `PYTHONPATH=src python -m pytest -q` —— 这个环境里**没有**把本项目 `pip install -e .` 进去，`src` 必须进 `PYTHONPATH`（`start.bat` / `start.sh` 里本来就是这么设的）。实测 `conda run -n aicoding --no-capture-output python -m pytest -q` → **788 passed**。
  - 另一套环境（顺手把话说准，免得下次误判）：`D:\python-venv\ai_coding` 这个 venv **现在能用** —— 它把本项目 editable 装了（`ai-coding-harness 0.1.0 → D:\python-project\ai-coding-harness`），pytest / deepagents 齐全，`-m pytest` 可直接跑。（**2026-09-24 我第一次检查时它是坏的**：当时 `pyvenv.cfg` 的 `home` 指向已被删除的 `D:\development_software\python3.4.17`，`Scripts\python.exe` 报「did not find executable」；现在该文件指向 `D:\developmentBinary\python3.14`。本批的启动脚本按用户指定的 conda 环境改，两套都能跑，要换回 venv 只需改那一行。）
- **遗留**：
  - **改动尚未在活的后端生效**（活进程比这些文件旧），要重启才算数；提示词是构建期读盘，新建会话即拿新的，重启只是让活进程也换过来。
  - **`start.sh` 没能做语法检查**：本机 PATH 上没有 `bash`（`Get-Command bash` 为空），`bash -n start.sh` 跑不了，改动靠与既有行同形 + 人工核对。`start.bat` 那半的 `if errorlevel 1` 构造用等价的 `cmd /v:on` 一次性命令验过（conda 解释器 → `OK`）。
  - **两个启动脚本本批都改了，但它们各自指向的解释器需要你确认**：现在指 conda 环境 `aicoding`（按你说的口径）；如果你日常其实用 `D:\python-venv\ai_coding`（那套是 editable 装的，能直接 `import ai_coding_harness`），把 `PYTHON=` 换回它即可 —— 两套我都实测过能跑。
  - **`orchestrate` 的 project 写根仍与它的口径并存**（本批只把「只读」改成「本轮不写」）：要彻底一致就得撤 policy 的写根，风险是主控在派发前后自己跑 git 写操作时会被拒 —— **等你定**。
  - **共用段与角色段的划分仍是手工维护的**：`scripts/check_prompts.py` 只覆盖这七类已知冲突，新模式（例如新形态、新技能）还得靠人看；如果以后这类矛盾再出现，优先往这个脚本里加一条规则，而不是只加测试。
  - 上一批记的两条仍未动：`save_repo_memory` 的「不像记忆就拒写」守卫、需求表已有行的「分支 / 链接」纠正。
  - README 的中间件表仍缺 `middleware/memory.py`、进度总览的测试数仍停在 692（与现状不符），本批没碰。

### 2026-09-18

#### `/ai_coding/output/` 全形态收口：一个文件都不落（qa / analysis / plan / orchestrate）

- **动机**：两件事，同一根。
  1. **2026-09-22 22:34 那轮**：用户说「给我一份仓库记忆文件」，qa 形态把整份记忆复制进 `/ai_coding/output/wanqi-mall-memory.md`，回了一句「仓库记忆文件已整理并写入：`/ai_coding/output/wanqi-mall-memory.md`」—— 这正是 `_ROLE_QA` 里明令禁止的那句话形状。它不是绕过了守卫，而是**从例外里钻出去的**：那行「`/ai_coding/output/`：本轮不用 —— 结论写在回复里；**只有用户明确要文件时才写入**」的后半句，把「这一轮没有交付物，只有回答」整条规则吃掉了。策略层同一处也留着口子（qa 的写根里有 output），于是模型照例外走时边界层一路放行。（记忆库本身没被写脏：qa 从来没调过 `save_repo_memory`，它只是把记忆全文复制到了别处。）
  2. **顺着这条线查下去，`/ai_coding/output/` 根本没有回传通路**：全仓只有三处碰它 —— `config.py` 的默认值、`policy.py` 的目录映射、`provider.py` 建目录时的 `mkdir -p`；API / service / 前端**一处都不读它**，工作区卷也只挂 `project`（`volumes.py:30`），写进去的文件随沙箱销毁。而 analysis 的口径一直是「成篇的产物可以落成文件」，技能 `repo-bootstrap-analysis` 第 6 步还明文要求把报告写成 `bootstrap-<repo>.md` —— 那是个**用户取不到**的落点，与 qa 那次「把答案藏进文件」是同一件事，只是还没人踩。
  3. **同一根理由也适用于 `plan` 与 `orchestrate`**。这两处当时仍留着 output 的写根，`plan` 尤其自相矛盾：它自己的 `_PLAN_WORKDIR`（「产出：方案写入 DB，不落文件」）与 `_PLAN_RULES`（「不落文件…不在 `/ai_coding/tmp` 里搭工程」）两处都说不落文件，只有「可写范围」那段写着 output 可写；而做同一件事的子智能体 `planner` 早就是空写根 + 撤写工具（注释：「方案写库，不再落文件（写文件等于同一件事记两遍）」）。于是两处一并收掉。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`（`_ROLE_QA`，2 处）：工作目录那行改成「`/ai_coding/output/`：**本轮不用、也不写** —— 问答的交付物是回复本身」；回答口径那行补上落点 ——「用户点名要某份文件（仓库记忆、某段代码、一张表）时，**要的是里面的内容**：把全文贴进回复（需要时说明它出自哪个路径），不要落成文件再把路径给出去」。
  - `src/ai_coding_harness/safety/policy.py`：「qa」行改成 `((), frozenset({"write_todos", "write_file", "edit_file"}))` —— 写根置空 + 撤掉写工具（与 planner 同形），并带一段注释说明为什么这里是「撤选项」而不是「再加一句劝阻」。`write_todos` 一并撤掉：一轮问答没有多步进度可报，清单只会多出一份「查一下、答一下」的空转。
  - `src/ai_coding_harness/agent/prompts.py`（`_ROLE_ANALYSIS`，3 处）：角色段「成篇的产物可以落成文件」→「交付物就是这份结论本身，写在回复里 / 成篇的报告也一样进回复，不落文件」；工作目录段的「`/ai_coding/output/`：分析产物（**可写**）」→「**本轮不写**」，并补一行「`/ai_coding/tmp/`：中间过程文件（**可写**）—— 取证的中间产物放这里，不必进回复」；分析工作法第 5 条从「成篇产物写进 output 后，回复里仍要讲清」改成「成篇的结论直接写进回复（长报告也一样）：不要先落成文件、再回一句「已写入文件」把答案藏起来」。
  - `src/ai_coding_harness/agent/prompts.py`（`_BASE_ENV`，全形态共用）：output 那行先改成「**问答与分析这两轮不写**（各自的「工作目录」段写明）；其余形态按需可写」；本批再收一步成「**任何形态都不写** —— 那个目录没有回传通路（API 与前端一处都不读它），写进去的东西用户取不到，结论一律进回复」—— 此时「其余形态」已经没有可指的对象了。同一段里「结论写入对应输出目录」也改成「结论写进回复 —— 落成文件的话随沙箱一起销毁，用户取不到」。
  - `src/ai_coding_harness/agent/prompts.py`（`_READONLY_BOUNDARY`，qa / analysis 共用）：「本轮你**只有只读工具**」→「本轮你**不与仓库发生写入**」。因为 analysis 保留 tmp 的写根之后，手里确实有 `write_file` / `edit_file`，原话成了假话；`没有 `task`（派不了子智能体）` 那半句原样保留（测试钉着它）。**没有**把 `write_file` / `edit_file` 加进这句枚举 —— 加了对 analysis 又会是假话。
  - `src/ai_coding_harness/agent/prompts.py`（`_ROLE_ORCHESTRATE` 工作目录）：那行先去掉了「问答」，本批**整行删掉** —— 多智能体主控不写任何结论文件。
  - `src/ai_coding_harness/safety/policy.py`（「analysis」行）：`(("output", "tmp"), frozenset())` → `(("tmp",), frozenset())`，注释写明为什么连 output 一起撤（用户取不到）。写工具**留着** —— 它还要写 tmp。
  - `sandbox/skills/repo-bootstrap-analysis/SKILL.md`（5 处）：目录声明的「输出目录」、红线段的「可写目录」、执行步骤第 6 步（标题也从「输出结论到 `/ai_coding/output`」改成「输出结论」）、「输出格式」段的引导句、末尾红线 —— 一律从「写 `/ai_coding/output/bootstrap-<repo>.md`」改成「按这个结构直接回复」。
  - `docs/workspace-directories.md`：先把 `qa` 与 `analysis` 的 output 两格改空（analysis 的 tmp 保留），并在规则 5 里写上「那个目录没有回传通路」这条理由；本批再把总览表的 output 行从「条件可写」改成「**谁也不写**」，矩阵里 `orchestrate` 与 `plan` 两行的 output 格清空，规则 4 改写成「出方案不落文件（主控 `plan` 与 `planner` 同形）」，规则 5 改写成「`/ai_coding/output/` 谁也不写」。
  - 测试：`tests/test_profiles.py::test_qa_closes_down_the_output_directory` 加钉「例外不见了」与「要文件时有落点」；把它旁边那条 `test_analysis_keeps_the_output_directory` **反过来钉**成 `test_analysis_closes_down_the_output_directory_too`（output 不写 + tmp 可写 + 结论进回复 + 旧口径都清了）；`tests/test_prompts.py::test_it_says_which_tools_are_missing` 从钉「只有只读工具」改成钉「不与仓库发生写入」并断言「只有只读工具」**不在**；`tests/test_boundary.py`（`test_each_name_resolves_to_its_own_scope` 的参数表）qa / analysis 两行期望值同步改成 `(QA_FORM, (), ("write_todos", "write_file", "edit_file"))` 与 `(ANALYSIS_FORM, ("/ai_coding/tmp",), ())`。本批又把 `orchestrate` 那行的 output 去掉、`plan` 那行换成 `(PLAN_FORM, (), ("edit_file", "write_file"))`，并在 `tests/test_profiles.py` 新增两条：`test_plan_form_lands_no_files_at_all`（plan 提示词里「**本轮一个文件都不落**」在、旧的「（分析产物）可写」不在）与 `test_no_form_is_told_the_output_directory_is_writable`（五个形态一律：共用段写着「任何形态都不写」，且不再出现「结论写入对应输出目录」）。
  - `src/ai_coding_harness/safety/policy.py`（本批）：`"orchestrate"` 由 `(("project", "tmp", "output", "review"), frozenset())` 收成 `(("project", "tmp", "review"), frozenset())`；`"plan"` 由 `(("output",), frozenset())` 收成 `((), frozenset({"edit_file", "write_file"}))` —— 与子智能体 `planner` 完全同形，注释里写明了「三处两说」这件事。
  - `src/ai_coding_harness/agent/prompts.py`（本批）：`_ROLE_PLAN_MAIN` 的「可写范围」段从「只有 `/ai_coding/output/`（分析产物）可写…除了这一个目录，出方案阶段不落任何文件」改成「**本轮一个文件都不落** —— 写文件的工具已撤掉，调了会被直接拒绝…方案用 `storePlan` 落库，分析结论写进回复」；`_PLAN_METHOD` 里「分析产物与仓库记忆由主智能体落盘」改成「分析结论与仓库记忆由主智能体负责（前者进回复，后者用 `save_repo_memory` 写回）」。
  - `src/ai_coding_harness/agent/profiles.py`（本批）：`analysis` 与 `plan` 两行注释同步 —— 「产出可落 `/ai_coding/output/`」→「产出只进回复（output 没有回传通路）」，`plan` 补上「不落文件 —— 与子智能体 planner 同形」。
  - `adhd/AGENT.md`（本批）：输出形态规则的**范围**那句删掉「写进 `/ai_coding/output/` 的文件产物」，只留 PR 评论与仓库记忆 —— 那种产物不再存在，留着等于给模型一个落点暗示。
- **关键决策**：
  - **两层一起收，不只改提示词。** 只劝不撤选项，下一个「用户明确要文件」还会照旧走一遍 —— 本批的起因正是一行例外吃掉了一整条规则。
  - **但「要文件」这个正当需求不能变成死路。** 撤掉 output 之后，「给我一份仓库记忆」仍然得答得出来，所以口径补的是**内容进回复**（需要时带出处），不是「给不了」。
  - **撤 output 的硬依据是「用户取不到」，不是「文件不好」。** output 既没有回传通路、也不随卷保留，写进去不如写进回复 —— 两个只读形态先撤，`plan` 与 `orchestrate` 本批跟着撤。后者真正的交付物本来就有别的通路（方案落库 + 右侧面板、PR + 审查面板），output 只是顺带的落点；收完之后这条通路**既没有写方、也没有读方**。
  - **`plan` 与 `planner` 必须同形。** 两者做的是同一件事（`storePlan` 落库），分叉只会让「出方案到底落不落文件」随意图变。
  - **analysis 保留 `tmp` 与写工具。** 取证的中间产物（大段 grep、命令输出）需要一个**不进上下文**的落脚点；`tmp` 从来没被当成交付物，撤了只会让长分析无处堆放。代价是那句「只有只读工具」不能再留 —— 于是改成「不与仓库发生写入」，而不是往枚举里加 `write_file` / `edit_file`（加了对 analysis 又是假话）。
  - **技能按「三种执行者都成立」改，不按形态分叉。** `repo-bootstrap-analysis` 由编码形态、分析形态、`planner` 三处执行，改成「结论进回复」后三者口径一致 —— 比给每个形态各写一份说明划算得多。
- **验证**：`tests/test_profiles.py` / `tests/test_boundary.py` / `tests/test_prompts.py` 三份 **229 条全过**；全量 `pytest` **775 条全过**；本次改动文件 `ruff check` 干净。另做了一次渲染检查：五个形态的成品提示词里都出现共用段的「任何形态都不写」，`成篇的产物可以落成文件` / `分析产物（**可写**）` / `只有只读工具` / 那条例外句 / `结论写入对应输出目录` 一律**不在**，plan 的「可写范围」段也不再提 output；没有引入 4 空格缩进（那会让紧跟的 `##` 标题失效）。
- **遗留**：
  - **改动尚未在活的后端生效**（活进程是 2026-09-22 18:10 起的，比这几个文件旧），要重启才算数（重启由你定）。
  - 上一批记的「`_BASE_ENV` 那句自相矛盾的话」**本批已解**（就是上面那条 `_BASE_ENV` 的改动）。
  - `plan` / `orchestrate` 的 output **本批已收**（见上），收完之后**没有任何形态**能写 `/ai_coding/output/`。
  - **`/ai_coding/output/` 这条通路本身留着没删**：`config.sandbox_output_dir`、`policy._DIR_ATTRS["output"]` / `_DEFAULT_ROOTS["output"]`、`provider.py` 建目录那行现在都成了死代码（无写方、也无读方）。没删的理由是删配置字段会牵连 `.env` / `.env.example` 的键，而收益只是清爽；要彻底摘掉另开一批。
  - **`orchestrate` 的 `/ai_coding/project` 写根与它自己的提示词不一致**（既有问题，本批没动）：policy 给了写根、文档矩阵也跟着写「读写」，但提示词 :165 写着 project **只读**（「写码由 coder 负责」），同段「约束」还要求「核实现状只用只读命令」。撤 policy 的写根有实际风险（它可能在派发前后自己跑 git 写操作），改提示词又会与同段的只读要求打架 —— 要么撤写根、要么改提示词并写明理由，等你定。
  - 同一类的轻口子：`orchestrate` 的 `tmp` 与 `review` 写根没写进它那份「工作目录」列表（边界允许，提示词没提）。
  - 「`save_repo_memory` 加『不像记忆就拒写』守卫」（起因是 2026-09-21 那次 `content='OK'` 的写坏）**仍没做** —— 之前提过、当时建议先不动。

#### 提示词还原 + 记忆时间戳改由平台盖章

- **动机**：两件事，都是「谁在写这份长期记忆」的问题。
  1. 你手工改过一版提示词，其中几处改坏了：几段文字被顶到 4 空格缩进（Markdown 里那是代码块，会让紧跟的 `##` 标题失效），`_MEMORY_GATE` 整段被删。后者不只是文案问题 —— **plan / coding 两个形态从此再没有任何指令要求写记忆**，而 `refresh_memory` 只刷新已存在的文件、从不建档，于是 `save_repo_memory` 这条写回路径等于被掐断。
  2. 记忆文件头的 `最后更新` 仍由模型填。它没有可靠的钟（只能读注入的「当前时间」或跑 `date`），填出来的是一个**猜的时刻**；下一轮再看那个文件，谁也说不清那一行是本轮盖的还是上一轮编的。「更新于」列同理。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`（7 处）：`TODO_PROMPT` 第 3 条（执行方案时一步一条）与 `_PLAN_METHOD` 第 1 步（先看系统提示里的「仓库记忆」块、不要再 `read_file`）按原措辞还原；`_PLANNER_ROLE` / `_ROLE_PLAN_MAIN` / `_REVIEWER_ROLE` / `_ROLE_REVIEW_MAIN` 里 4 处泄漏的 4 空格续行缩进去掉（常量本身的代码级缩进没动）；**重建 `_MEMORY_GATE`**（「出方案 / 派发前必须完成」「禁止颠倒顺序或与派发并行」「平台已经在每轮开始前替你查过」「不要再自己查一遍」「不要断言『仓库没有未合并的 PR』」「由平台按 Gitee 的实际状态回填」「本轮只写一次」「不要为措辞、空格、排版微调重复写回」），拼进 `plan` / `coding` 两条指令（`review` 不带，不变）。「本轮的第一件事是用 `save_repo_memory`」刻意写在**任何「派 `coder`」之前** —— 这个顺序本身是被测试钉住的不变量。
  - `src/ai_coding_harness/agent/prompts.py`（`_BASE_ENV` 的「仓库记忆文件规则」）：时间戳口径改写 —— 「`更新于` 与 `最后更新` 都是平台在写盘那一刻盖的，**时间戳一律不用你填**，也不必为它们跑 `date`」；散文里的日期（「变更记录」那种）另说，一律取自注入的「当前时间」或 `date`，**不许凭记忆编写**。
  - `src/ai_coding_harness/agent/memory_reconcile.py`：新增 `_replace_stamp(line, now)`（换掉一行里的时间戳，**其余字段逐字保留**）/ `_stamp_lines(text, now)`（没有头行时**原样返回**）/ `restamp(text, *, now)` / `same_except_stamp(old, new)`（两边都挖空时间戳再比，任一侧没有头行时退回逐字比较）；`_seed` 补出来的行盖上 `updated=now` —— 那一行是平台本轮刚发现的，那一刻就是它进表的时刻，留空渲染成 `—` 等于说「不知道什么时候进来的」，而这件事我们恰好知道。
  - `src/ai_coding_harness/tools/memory.py`：落盘前统一 `restamp`；「内容没变就不写盘」的判据换成 `same_except_stamp`；`noted`（「已按 Gitee 实际状态回填」）**收进 `pulls is not None` 分支** —— 原来放在外面，`pulls is None` 时 `restamp` 也会让 `filled != content`，于是没查过 Gitee 的回执里也会冒出「已回填」这句假话；docstring 与工具描述同步改成「时间由平台盖，你写的不生效」。
  - `sandbox/policies/repo-memory-template.md`、`docs/repo-memory-template.md`：头行示例改成 `{由平台盖写盘时间}　｜　{由平台回填编号/链接}`，`更新于` 单元格改成 `{由平台盖}`，表下说明点明「`更新于` 与文件头的时间戳由平台在写盘那一刻盖章」。
- **关键决策**：
  - **还原措辞，不放宽测试。** 那 15 条失败是提示词被改坏导致的；改测试等于把一处功能缺口（没人再写记忆）固化下来。
  - **`restamp` 只换时间，不重建整行。** 重建是 `reconcile` 的职责 —— 只有它知道「最近 PR」该写什么。这里凭空补一行会把「没有 PR」说成事实；老格式里那个多余的 `当前分支` 也照原样带着，等 `reconcile` 下次重建时清掉。
  - **「没变」要在挖掉时间戳之后比。** 否则那一次盖章让每轮内容都「变了」，「没变就不写盘」当场失效，还连带毁掉 `changed` 这个事实（轮末打底正是靠它决定写不写）。
  - **不给「更新于」列做整体盖章。** 需求行这格记的是**状态变化那一刻**，状态没变的行本来就该原样带过；每轮重盖会让每次写入都成为一次内容变更，正是上一条要避免的。所以提示词说的是「不用你填」，而不是「你填了也会被改」—— **状态没变的那一行，模型写进去的值暂时不动它**。
  - **`_seed` 盖 `now`** 与「已存在的行不重盖」不冲突：那是「这一轮刚发现的新事实」，有确定时刻。
- **验证**：`tests/test_prompts.py` **94 条全过**（还原后无需改测试）；`tests/test_memory_tool.py` 新增 `TestTheWriteStampsItsOwnTime`（模型编的时刻不落盘、头行的「最近 PR」由平台按查到的列表重建、只差一个过时时间戳不算内容变更）+「没接 PR 查询那条路也盖章、但回执不能说『回填』」，并加了一个钉住时钟的 fixture（`STAMP`），写盘的断言从「时间大概对了」变成逐字相等；`tests/test_memory_reconcile.py` 新增 7 条（`restamp` 四种形态、`same_except_stamp` 的严格与宽松两侧、模型写的时间在 `reconcile` 这一层**原样留着**、盖章由调用方在之后做），并给「补进来的行」补上 `更新于 == now` 的断言。全量 `pytest` **773 条全过**（上一批 763，净增 10）；本次改动文件 `ruff check` 干净。
- **遗留**：
  - **改动尚未在活的后端生效**，要重启才算数（重启由你定）。
  - 「已有需求行的分支 / 链接不纠正」这条**仍然存在**（前几批就记着），本批没碰。
  - 「`更新于` 在状态没变的行上仍可被模型写脏」是上面那条决策的代价，要收紧就得接受每轮都算一次内容变更。

#### 记忆全文注入系统提示：所有智能体都挂，模型不再自己读那份文件

- **动机**：仓库记忆一直靠模型自己 `read_file` 去读，于是每轮开头都白花一次工具往返（每多一次工具调用，就是整份上下文再发一遍）。而记忆全文其实在 `abefore_agent` 就已经被读到手里了（plan / coding 两个主控为了对账本来就要读它）—— 读到手的东西再让模型跑一次工具取一遍，没有任何收益。
- **改动**：
  - `src/ai_coding_harness/agent/middleware/memory_gate.py` → 重命名为 **`memory.py`**（内容不再只是闸门）：`MemoryReconcileMiddleware` 改名 `MemoryMiddleware` 并新增 `awrap_model_call`，把记忆块追加到系统消息末尾（与 `IntentMiddleware` 同一套做法）；`all_prs` 变成可空 —— `None` 表示「这次没有查 PR 的能力」，只读注入、不查不写不拦。新增 `MEMORY_BLOCK_TITLE`、`_memory_block(path, text)`、`_read_text(backend, path)`、`Refreshed`（`result` + 盘上原来那份 `current`）；`refresh_memory` 改为返回 `Refreshed`。`build_memory_gate(...)` 换成 `build_memory_middleware(settings, repo, backend, form=None)`，**每个形态都返回一个实例**（不再有「其余形态返回 None」）。
  - `src/ai_coding_harness/agent/middleware/__init__.py`：导出改名 + `MEMORY_BLOCK_TITLE` / `Refreshed` / `build_memory_middleware`；模块清单里 `memory_gate` 一条改写为 `memory`（注入 + 闸门两件事），「每个角色各 new 一个」的例外类补上 `MemoryMiddleware`。
  - `src/ai_coding_harness/agent/graph.py`：`build_agent` 的 `reconcile=` 改名 `memory=`（语义从「启动闸门」变成「本轮看不看得到记忆」）；列表位置不变，仍在 `BoundaryMiddleware` 之后。
  - `src/ai_coding_harness/agent/subagents.py`：`build_subagents` 新增 `memory_factory`，每个角色在边界之后各挂**一个新实例**（`form=None` ⇒ 不带 `all_prs`）。
  - `src/ai_coding_harness/session/manager.py`：主控传 `memory=build_memory_middleware(..., profile.form)`；子智能体传 `memory_factory=lambda: build_memory_middleware(...)`（同一个工厂给每个角色现造一份）。
  - `src/ai_coding_harness/agent/prompts.py`：「仓库记忆文件规则」整段改写为「系统提示里有一段「仓库记忆」块 —— 每个智能体、每个形态每轮开始都有，就是该文件的当前全文，**不要再 `read_file` 读它**」；写回口径同步改成「不必先读盘，拿块当底稿合并」；`_MEMORY_GATE` 去掉「本形态每轮注入」这句已经过时的限定；`PLANNER_PROMPT` / `CODER_PROMPT` 里「用 `read_file` 读取仓库记忆」的两步改成「看系统提示里的「仓库记忆」块」。
  - `src/ai_coding_harness/server/service.py`：只改 docstring 里的模块路径引用。
- **关键决策**：
  - **注入每个智能体都挂**（主控三个只读形态 + 三个子智能体，共 8 处），**闸门仍只挂 plan / orchestrate 两个主控**。闸门只挂主控的理由没变（coder 刚建完 PR、reviewer 再查一遍就会把自己挡在门外）；而注入没有这个副作用 —— 只读形态与子智能体多花的只是**一次 store 读**，换来的是省掉一次工具调用往返。它们本来就不写记忆，为一次注入多查一趟 Gitee 更不划算。
  - **注入的是「刷新后」的文本，不是盘上原来那份。** 闸门那条路 `reconcile` 已经算出新文本；若写回失败，盘上还是旧的，而照着旧事实让模型去合并写回比不注入更糟。只读那条路没有对账，注入的就是盘上那份。
  - **「没有记忆文件」与「记忆是空的」分开处理。** `_read_text` 返回 `None`（没写过）时不注入任何东西 —— 对账器此时也会产出一张只有表头的需求表，可那是「我们打算建的」，不是盘上的事实，讲给模型听等于骗它。空文件（`""`）照注入。
  - **注入块的标题不写「已按 Gitee 实际状态刷新」。** 只有两个主控那条路真的刷了，其余六处只是读盘；挂一句刷新过的话，会让它们以为自己看到的是刚核过的事实。PR 那件事由闸门的提示词去说。
  - **子智能体各拿一份新实例**（因此是 `memory_factory` 而不是传实例列表）：同一回合主控可能一次派几个 `task` 出去，那几个子智能体并发跑，而注入块按轮存在 `self._memory` 上，共用一个实例会互相覆盖。
  - **代价（已知，接受）**：记忆全文随每一次模型调用一起发出去，一轮里有多少次工具往返就发多少遍，且**没有长度上限**。先按实际记忆体量观察，不急着设上限或做摘要 —— 上限设小了会截掉需求表，反而是错的事实。
- **验证**：
  - `tests/test_memory_gate.py`：新增 `TestTheInjection` 5 条（全文贴进去且带「别再读一遍」、追加而不是替换（`SYS` 仍在前）、没写过记忆就不注入、只读那条路不写盘、注入的是刷新后的文本（用写盘必败的后端钉住「盘上旧、注入真」））；`TestTheAssemblyPoint` 重写为「每个形态都挂、闸门只给两个形态」+「每个子智能体各一个不同实例」（用真 `build_subagents` 跑）；`TestThroughTheGraph` 新增一条拿真图跑、从假模型收到的消息里找注入块的用例。
  - `tests/test_prompts.py`：`test_memory_is_not_claimed_to_be_injected`（钉住旧措辞「由运行时注入」不许回来）改写为 `test_the_block_is_claimed_to_exist_for_every_agent`，并新增子智能体两步不能再要求 `read_file` 读记忆的断言。
  - 全量 `pytest` **763 条全过**（上一批 755，净增 8）；本次改动文件 `ruff check` 干净。
- **遗留**：
  - **代码改动尚未在活的后端生效**，要重启才算数（重启由你定）；重启前后端跑的还是「模型自己 `read_file` 读记忆」那一套。
  - 「少一次工具调用」是设计意图，**不是实测结论**；真实会话里 `read_file` 调用次数少了多少，要重启后看日志才算数。
  - 记忆体量上限、以及注入块与 `IntentMiddleware` 的先后（现在是记忆在前、时间 / 意图在后），都留待真实记忆长起来之后再看。

#### 删掉「功能列表」：与需求列表重复的派生视图

- **动机**：功能表与需求表的状态由**同一批 PR** 推出来，功能表的「相关 PR」与需求表的「PR」指的也是同一件事。两张表共存，只多出一条「不许各说各话」的不变量和一整套状态推导代码；功能表独有的「已具备 / 待合入」是需求行「已合入 / 待合入」的同义反复，它的 `说明` 列（「怎么用、依赖什么」）本来就该待在散文节里。
- **改动**：
  - `src/ai_coding_harness/agent/memory_reconcile.py`：删 `READY`、`_FUNC_HEADER`、`_derive_func_state()`、`_reconcile_func()`、`_build_funcs()`；`reconcile()` 只剩「重写 / 补出需求表」一条路径。新增 `_drop_legacy()`：标题**去掉节号后正好等于**「功能列表」的那一节整节摘掉（连标题），并给前一节补一行空行，免得下一个标题贴在上一条内容后面。这里比 `_find()` 严一档（认整名不认尾巴）—— 摘节是删东西，模型自己写的「历史功能列表」不该被牵连。
  - `sandbox/policies/repo-memory-template.md`：删「二、功能列表」整节与表下那条「派生视图」说明；需求表升为「二」、变更记录→三、当前进度→四、约定→五、关键决策→六。表下的平台回填说明改为单表口径（回填 `PR` / `分支` / `状态` 三列，模型只管 `需求` 那一列的名字）。`docs/repo-memory-template.md` 同步，按先前定的顺序（变更记录留「二」，需求表→三、当前进度→四、约定→五、关键决策→六）。
  - `src/ai_coding_harness/agent/prompts.py`、`tools/memory.py`、`tools/register.py`、`agent/middleware/memory_gate.py`：文案从「两张表…你只管功能名 / 需求名 / `说明`」收敛为「需求表…你只管 `需求` 那一列的名字」。**顺带修掉一处不实承诺**：需求表没有 `说明` 列，提示词却让模型「只管需求名 / 说明」。
- **关键决策**：
  - **老记忆里已经写下的「功能列表」整节删掉（自愈），而不是留着不管。** 它已经没有消费者（模板不再有、提示词不再提），留着只会被当成普通散文原样保留，里面的「已具备 / 待合入」永远停在写下的那一刻 —— 一份没人维护、状态必定过期的表比没有更糟。摘节**必然**让 `changed` 为真，所以下一次进 plan / coding 形态时会被真的写掉。
  - **不把 `说明` 搬进需求表。** 需求表是「PR 视角的事实表」：一行一件事，状态可核对。掺进「怎么用」这类描述会让它重新变成半散文，也就没法可靠地按 PR 对账了。
- **验证**：`tests/test_memory_reconcile.py` 删掉 4 条功能表推导用例、新增 3 条（整节摘掉且下一个标题前留空行、别的什么都没变时摘节也算 `changed`、「历史功能列表」这类节名不被牵连）；`with_tables` 改名 `with_table`，并留一份 `LEGACY` 老格式片段专门喂摘节用例。`tests/test_memory_gate.py` 的 `STALE`、`tests/test_memory_tool.py` 的 `MEMORY` 去掉功能表那几节。全量 `pytest` **755 条全过**（上一批 756，净减 1 = 删 4 增 3）；`ruff check` 本次改动文件干净。
- **遗留**：
  - **代码改动尚未在活的后端生效**，要重启才算数（重启由你定）。模板走的是「建会话时整棵上传」，新开的会话拿到的就是新形态，不必重建镜像。
  - 上一批遗留的「已有需求行的分支 / 链接不纠正」**仍然存在**，本批没碰它。

#### 仓库记忆的文件头也交给平台：删掉「当前分支」，时间戳只在真变了时才前进

- **动机**：上一批把两张表从模型手里收回来了，但文件头那一行 `> 最后更新：… ｜ 当前分支：… ｜ 最近 PR：…` 还是模型填。这一行是三样东西里最像「事实」的：时间戳现在由 `agent/middleware/context_inject.py` 逐次注入，模型只要哪一轮忘了读、或按训练记忆写一个日期，盘上就留下一句假话；`当前分支` 更糟 —— 它是**沙箱工作树**的状态，与 Gitee 的 PR 状态、与计划库各有各的口径，写进长期记忆只会互相打脸。留着它就是留一个每轮都要模型手工维护、且没人能核对的字段。
- **改动**：
  - `src/ai_coding_harness/agent/memory_reconcile.py`：`reconcile()` 增加头行重算。按 `> 最后更新` **前缀**定位（不认整行逐字）：有就整行替换、没有就补在标题后 —— 老记忆里带 `当前分支` 的三段式头行因此被整行清掉（自愈，不是丢字）。新头行 = `> 最后更新：{now}　｜　最近 PR：{[#N](url)}`，`最近 PR` 取 `number` 最大的那个（Gitee 编号单调递增）；一个 PR 都没有时写 `—`，认不出编号的条目跳过。
  - `sandbox/policies/repo-memory-template.md`：头行删掉 `当前分支` 一段；表下的平台回填说明补一条 —— 头行同样由平台回填、每次刷新整行重写、别手改。
  - `src/ai_coding_harness/agent/prompts.py`：`_BASE_ENV` 的「仓库记忆文件规则」把「四列由平台回填」扩成「四列 + 文件头的 `最后更新` / `最近 PR`」，并**删掉**「模板头的 `> 最后更新：{YYYY-MM-DD HH:mm}` 用它填」——模型不再被要求碰那一行，不然两方会抢同一行、每轮互相覆盖。`_MEMORY_GATE` 里同口径的一句一并改掉。
  - `src/ai_coding_harness/tools/memory.py`：只改文案（docstring / 工具描述 / 回填写回话术）。**逻辑一行没动** —— 它本来就调同一个 `reconcile()`，闸门与轮末打底也各调一次，三处口径天然一致。
- **关键决策**：
  - **时间戳只在「别的内容真的变了」时才前进。** 反直觉，但必须这样：每轮都刷新 `最后更新`，`changed` 就**恒为真**，「没变就不写盘」这条优化当场失效 —— 每轮白付两次存储往返（闸门一次、轮末打底一次），而且「这一轮到底改没改」这个事实也一起失真（打底正是靠 `changed` 决定写不写）。实现上先拿**原文自己的时间戳**把正文试算一遍，逐字相同才算没变；真的变了才改用本轮的 `now`。副作用是「老格式头行被清」这类自愈会让时间戳前进一次，合理。
  - **删字段而不是留着不管。** 与「补一个 `当前进度` 栏目」这类半接管不同：留着 `当前分支` 就要在每一轮维护一个跨系统口径的字段，收益是零、出错面是全量。删掉之后头行只剩两个都能从平台侧算出真值的栏位。
  - **头行也算「散文之外」的一处例外**，但要写明白：`memory_reconcile.py` 的模块文档与 `prose_lines` 测试助手都点出「`> 最后更新` 行不属逐字保留范围」，免得后来人把它当成 bug。
- **验证**：`tests/test_memory_reconcile.py` 新增 3 条（老格式头行整行换掉且 `当前分支` 消失、缺头行补在标题下、没有 PR 时写 `—`），并把两条「散文逐字保留」用例的比对改成排除头行、给「没变就没变」那条补上「时间戳不许前进」的断言；`tests/test_memory_gate.py` 的 `STALE` 补上头行（否则它每轮都算「变了」，`test_an_unchanged_memory_is_not_written` 会假失败），并在刷新用例里钉住「头行随本轮前进」；`tests/test_memory_tool.py` 的 `MEMORY` 同上；`tests/test_prompts.py` 把「提示词让模型填 `{YYYY-MM-DD HH:mm}`」改为「提示词声明头行归平台，且不再出现那个占位符」。全量 `pytest` **756 条全过**（上一批 753）；`ruff check` 本次改动文件干净。另跑了模板实录：拿真模板当记忆喂 `reconcile`，头行正确落位，第二轮 `changed=False`。
- **遗留**：
  - **改动尚未在活的后端生效**，要重启才算数（重启由你定）。
  - 上一批的「历史记忆不刷」现在更轻了：老记忆下一次进 plan / coding 形态时，两张表与头行会被同一次对账一起补上 —— 但**只在真的写了盘之后**才算数，只读形态（qa / analysis / review）不挂对账，那类会话里的记忆保持原样。
  - 上一批遗留的「已有需求行的分支 / 链接不纠正」**仍然存在**，本批没碰它。

#### 仓库记忆加两张结构化表：需求列表是权威，功能列表是它的派生视图

- **动机**：仓库记忆此前是一份**纯散文** Markdown。「哪个功能已经具备、哪个需求还挂在 PR 上、挂在哪个分支」全靠模型在散文里写 —— 换个措辞、少写一句，事实就丢了。同时「派发前先查 PR、有未合入的就先合并」只是提示词里的一句**请求**，模型可以跳过、可以顺序颠倒、可以把「查不到」讲成「没有」。
- **改动**：
  - `src/ai_coding_harness/agent/memory_reconcile.py`（**新增**）：纯函数对账器（无 IO、不过模型）。`reconcile(text, pulls, *, now) -> Reconciled(text, changed, pending)` —— 认 `## …功能列表` / `## …需求列表` 两个尾巴定位区块，重写表体，**其余散文逐字不动**；两张表缺哪张补哪张（补在「项目概览」之后）。状态由 PR 推导：open → `待合入`、merged → `已合入`、closed 未合入 → `已废弃`（保留不删）；`pulls` 里查不到的编号**一个字符都不改**（查不到 ≠ 已合入，与 `open_pulls` 拒绝把失败返回成空列表是同一条纪律）。功能行状态由它引用的那些 PR 推导（全合入 → `已具备`、有未合入 → `待合入`、全废弃 → `已废弃`），所以两张表不可能各说各话。表里缺的**未合入** PR 会补成待合入行（别人在 Gitee 上手开的 PR 也算未结清的需求）；已合入的不补（那是「变更记录」的事）。体量有界：待合入全留，已结清留最近 `_KEEP_CLOSED = 10` 条。
  - `src/ai_coding_harness/agent/middleware/memory_gate.py`（**新增**）：`MemoryReconcileMiddleware`（只实现 `abefore_agent`）+ 模块级 `refresh_memory(backend, path, all_prs)` + `build_memory_gate(settings, repo, backend, form)`。流水：读记忆 → 查 PR → 对账 → 变了就 `aedit` 整篇写回 → `pending` 非空就抛 `TurnStop`。`PrLookupError` 原样抛出（两个调用方处置相反，写死在里面必然有一处是错的）。marker 自己说清编号 / 标题 / 分支 / 链接与「合并由人工在 Gitee 上完成」—— 它原样进 SSE 的 `ai` 帧与记录正文，不靠前后文（`_MAX_LISTED = 5`，多的折成「另有 N 个未列出」）。
  - `src/ai_coding_harness/agent/graph.py`：`build_agent` 新增 `reconcile=` 参数，实例插在 `BoundaryMiddleware` 之后（越界的先被拒，再谈本轮要不要开始）。
  - `src/ai_coding_harness/session/manager.py`：`get_agent` 里按形态构造 —— **只有 plan / orchestrate 挂闸门**，其余形态传 `None`。
  - `src/ai_coding_harness/tools/git.py`：把 `open_pulls` 里那段 `try/except httpx.* → PrLookupError` 提成 `_pulls_or_raise(*, state, limit)`，新增 `all_pulls()` 走 `state="all"` —— `PrLookupError` 的纪律只留一处，两个入口共享。
  - `src/ai_coding_harness/tools/memory.py`：拒写规则收敛成**只保留「查不到就拒写」**（「有未关闭 PR 就拒写」删掉——coding 收尾那一刻必然存在刚建好的未合入 PR，照原规则模型的补充写入会被一律拒绝）；落盘前用同一个 `reconcile()` 规范化两张表，模型因此**伪造不了「已合入」**。
  - `src/ai_coding_harness/server/service.py`：新增 `_refresh_repo_memory`（轮末打底），在 `chat()` 的智能体分支**事件流抽干之后**按 `profile_for(intent).form == ORCHESTRATE_FORM` 触发。
  - `src/ai_coding_harness/agent/prompts.py`：`_MEMORY_GATE` 第 0 步改写 —— 删掉「自己查 PR、有未合入就停下等合并」，改为「平台已经在每轮开始前替你查过，**不要再自己查一遍**，也不要对用户断言『仓库没有未合并的 PR』」；补「两张表的 PR 编号 / 链接 / 分支 / 状态四列由平台回填，你只管功能名 / 需求名 / 说明」。
  - `sandbox/policies/repo-memory-template.md`：插入「二、功能列表」「三、需求列表」两节（原四~七顺延），表下注明三个状态取值、两张表的派生关系、哪几列由平台回填。
- **关键决策**：
  - **闸门只挂主控，子智能体不挂对账也不挂闸门**。主控闸门抛异常时本轮已经结束，子智能体根本进不去；而同一轮内 coder 刚建完 PR、主控接着派 reviewer，若 reviewer 也查一遍，就会把「刚建好的 PR」当成未合入 PR 把自己挡在门外 —— 评审这步直接做不了。
  - **闸门从提示词挪进 `before_agent`，而不是继续加强措辞**。「软约束」这件事连写十遍也还是软的；抛 `TurnStop` 走的是既有出口（`service.py` 的 `except TurnStop` 包住整个 `astream_events` 循环，收尾按 `state.aborted` 发 `ai` 帧并写进记录正文），**不需要改 stream 消费端，也不需要 `jump_to: "end"`**。已用真图实测：`before_agent` 抛的 `TurnStop` **原样穿出** `astream_events`（此前只从 `wrap_tool_call` 抛过），且闸门一响模型一次都没被调用。
  - **「查不到」不放行**。`PrLookupError` 既不能当成「有」也不能当成「没有」，故拦下本轮且**一个字节都不写**。代价是 Gitee 抖动会让整轮不可用 —— 这是有意的。
  - **只刷新已存在的记忆，不凭空建档**。建档是 `save_repo_memory` + 模板的事；在 `before_agent` 造一份没有「项目概览」的残篇会占住路径，让模型下一轮以为自己已经写过了。注意：**有没有记忆**与**拦不拦**无关 —— 仓库有未合入的 PR 就该拦。
  - **轮末打底的判据是形态，不是「本轮建出过 PR」**（计划里的原判据）。计划里也写明了这条可放宽；实测确认必须放宽：识别 `create_pr` 是否成功要对工具返回文案做字符串匹配（它失败时返回 `创建 PR 失败：…` 而不抛异常），**改一次文案就会静默失效**。改成 `profile_for(intent).form == ORCHESTRATE_FORM`（coding 即「这轮可能在动代码」）与用户那句「只要是动代码了就更新」一致。
  - **打底失败不改本轮结论**。记忆是副产物：查不到 PR、写盘报错都只记日志，交给下一轮或下一次 `save_repo_memory` 再刷。
  - **打底不复用闸门的异常处置**，只复用 `refresh_memory` 本身 —— 两个调用方对 `PrLookupError` 的处置正好相反（闸门拦下，打底放行），把处置写进 `refresh_memory` 必然错一处。
  - **对账器不建「功能表从方案步骤播种」**（计划里的一条）。方案步骤的标题是**任务形状**（「补测试」「改配置」）而非功能形状，且对账器没有 `functions` 参数、功能名本来就归模型。凭空补出一批「补测试」当功能名，比空着更糟。
  - **需求表的列是 `需求 | PR | 分支 | 状态 | 更新于`**：计划里的 `#` 行号列去掉了 —— 它是渲染产物、不是数据，重排一次就全变，还会诱导模型去引用行号。
  - **没有 PR 编号的待合入行不触发闸门**（`pending` 只看 `number and state == PENDING`）。那种行无从合并，拦下来会把这一轮**永久锁死**（闸门在 `before_agent` 结束本轮，模型根本没机会改那一行）。
  - **刷新成功也留一条日志**，且记在 `_write` 里而不是调用方。`refresh_memory` 在**没有记忆**时也会算出 `changed=True`（那是「算出来变了」，不是「写进去了」），拿 `changed` 当「刷过没刷」的判据会撒谎。
- **验证**：`tests/test_memory_reconcile.py`（对账器纯函数，最厚）；`tests/test_memory_gate.py`（**新增 19 条**：拦得住 / 拦之前先刷记忆 / 没记忆也拦但不建档 / 写盘失败不改判定 / `PrLookupError` 抛且零写入 / 只在两个形态装配 / **真图确认 `before_agent` 的 `TurnStop` 穿得出来且模型零调用** / 干净轮次照跑 / 轮末打底三条（刷新、查不到零写入、后端抛异常不外溢）/ 判据是形态（coding 触发，qa·analysis·review 不触发））；`tests/test_memory_tool.py` 重写（含「模型伪造的『已合入』被改回『待合入』」「查不到就拒写」）；`tests/test_prompts.py` 的 `TestMemoryWritebackGate` 跟着改写。全量 `pytest` **753 条全过**（基线 733）。`ruff check` 本次改动文件干净（`src/ai_coding_harness/db/`、`observability/tracing.py`、`sandbox/provider.py`、`tools/plan.py` 里那 9 条是既有的，不属本次改动）。
- **遗留**：
  - **改动尚未在活的后端生效**，要重启才算数（重启由你定）。
  - **待实调确认一处**：Gitee 的 `state="all"` 是否真返回已合并项。若不返回，`all_pulls()` 要退回 `open` + `closed` 两次查询（`_pulls_or_raise` 已支持按 state 取，改的是那一个入口）。重启后跑一轮就能看出来。
  - **已知的一处口径差**：需求表里**已有**的行，对账时只更新 `状态`（与 `更新于`），**分支 / 链接保持表内原值**；只有新补进来的行才从 PR 取 `head.ref` 与 `html_url`。于是模型把已有行的分支写错时不会被纠正 —— 与模板里「四列由平台回填」的说法有出入。要收紧就是在 `_reconcile_need` 里比对 `url` / `branch`，**待你定**。
  - 历史记忆不刷：本轮之前写下的记忆里没有这两张表，下一次进入 plan / coding 形态时由对账器补上（补在「项目概览」之后）。

#### 工具提示按角色说话：委派时显示「正在编码 / 正在评审 / 正在规划」

- **动机**：输入框上方那行活动提示（「正在执行命令」「正在读取文件」…）在委派子智能体时一律显示「正在处理子任务」—— 派的是谁、在做什么，一个字都没说。上一批已经让**任务列表**里的容器项说中文角色名（「编码子任务」），但**提示那行是另一条通道**，没跟着改。上一条遗留里写明的就是这个缺口。
- **改动**：
  - `src/ai_coding_harness/stream.py`：把 `ToolNames._call_id` 提成模块级函数 `call_id_of(event)`（`ToolNames` 两个方法改用它）—— 服务层也要拿这个键，不该去蹭一个类的私有 staticmethod。
  - `src/ai_coding_harness/server/service.py`：新增循环内的 `delegated: dict[str, str]`（`task` 的 `tool_call_id` → 角色）；`tool-started` 时若工具是 `TASK_TOOL`，用现成的 `subagent_name()` 从入参取 `subagent_type` 记下，并把 `"agent": "coder"` 加进工具帧；收尾帧 `pop` 出来同样带上。**其它工具的帧形状不变**（不加空字段）。
  - `frontend/src/api.ts`：`ChatEvent` 加可选 `agent?: string`（注明只有委派帧带）。
  - `frontend/src/App.vue`：新增 `SUBAGENT_ACTIVITY = {planner: '正在规划', coder: '正在编码', reviewer: '正在评审'}`；工具分支改为 `SUBAGENT_ACTIVITY[ev.agent ?? ''] ?? TOOL_ACTIVITY[ev.name ?? ''] ?? '正在处理'`；`TOOL_ACTIVITY` 上那句「不提是谁在做」的注释改掉（`task` 那一格从此就提了），并注明它的兜底作用。
- **关键决策**：
  - **角色由后端带上，前端不猜**。`TOOL_ACTIVITY` 是按**工具名**查表的，`task` 只有一格；前端手里没有「这次派的是谁」的任何信息。原先估「改一行文案就够」是估错了 —— 必须先在 SSE 帧上补这个字段（`tool` 帧原本只发 `name` / `content`）。
  - **收尾帧也带角色**。只带起手帧的话，子智能体一返回提示就翻回「正在处理子任务」，而主智能体接着要想几秒才开口 —— 那几秒显示的是「还在处理」，活其实干完了。带上则停在「正在编码」直到主智能体自己说话。两档都不完美（没有「已完成」这一档），取**稳定不闪**的。
  - **字段名用 `agent`**，沿用仓库既有口径（run_log 的 `record.agent`、metadata 的 `lc_agent_name`），不新造词。
  - **取不到角色就不带这个键**，而不是推一个空串过去 —— 前端那层自然会落回 `TOOL_ACTIVITY['task']`（「正在处理子任务」）。那张表里的三行与后端 `_ROLE_LABELS` 是**平行的两份**，不复用：两边文本本来就不同（「编码子任务」是清单里的**物件名**，「正在编码」是**进行时的动作**），而且前端不依赖后端的中文。
- **验证**：`tests/test_stream_v3.py` 新增 `test_call_id_of_pairs_the_two_frames`；`tests/test_plan_events.py` 新增三条 —— 委派起止两帧都带角色、`task` 入参取不到角色时**没有** `agent` 键（三个畸形入参参数化）、普通工具帧没有 `agent` 键。全量 `pytest` **707 条全过**（基线 701）；`ruff check` 改动文件干净（`tests/test_plan_events.py` 仍是那 6 条既有的 `RUF100`，不属本次改动）；前端 `vue-tsc -b && vite build` 通过。前端没有测试框架，这块没补。
- **遗留**：**后端的改动尚未在活的后端生效**，要重启才算数。前端不用构建 —— 跑的是 Vite dev server（`frontend/vite.config.ts` 代理到 `127.0.0.1:8000`），`App.vue` 改完即热更新；后端重启前它拿不到 `agent` 字段，落回「正在处理子任务」，不会出错。历史记录不受影响：回放吃的是 `parts`（只有正文与待办树），`tool` 帧本来就不落库，无需回填。

#### 任务列表里的子任务容器改用中文角色名

- **动机**：挂不上主委派项的子清单会补一个容器项（`stream.py` 的 `_container`），那行文字此前把**内部角色名**摊在了用户面前 —— `「coder」子任务`，取不到角色名时则是一句「子智能体任务」。给用户看的一行应该说清**在做什么**（编码 / 评审 / 规划），而不是复述我们内部的英文角色名。
- **改动**（`src/ai_coding_harness/stream.py`）：新增 `_ROLE_LABELS = {"planner": "规划", "coder": "编码", "reviewer": "评审"}`；`_container` 取名分三档 —— 认识的三个角色 → `编码子任务`；表里没有的角色 → 照旧 `「changelog-writer」子任务`；连名字都取不到（`task` 入参畸形）→ 仍是 `子智能体任务`。
- **关键决策**：
  - **改在后端，前端不动**。那行文字由 `_container` 生成、随 SSE 下发、也落进 `parts`（历史回放吃的是同一份），改这一处同时管住两头；前端是 `{{ t.content }}` 原样渲染，本来也没有可插手的余地。
  - **标签表放在 `stream.py`，不 import `agent/subagents.py` 的 `_ROLES`**。角色名的权威清单在 `agent` 层，而 `stream` 是协议层，不反向依赖它；代价是三个名字重了一份，注释里点明了权威清单在哪。
  - **认识的翻中文，不认识的保留原名**。宁可显示 `「changelog-writer」子任务`，也别塌成辨识不出的兜底话 —— 兜底话只留给「连名字都没有」这一种情况（将来加角色时不至于看不出是什么）。
  - **已经落库的历史记录不刷**。历史里那行仍是旧文字；为改显示去写业务库不划算，且那是当时真实显示过的东西。
- **验证**：`tests/test_stream_v3.py` 新增 `test_container_name_is_the_chinese_role_label`（三个角色各自的标签 + 未知角色保留原名）；四处既有断言跟着改（`test_stream_v3.py` 三处、`test_plan_events.py` 一处），「没名字」那两条仍断言 `子智能体任务`。全量 `pytest` **701 条全过**；`ruff check` 改动文件干净。
- **遗留**：**尚未在活的后端生效**，要重启才算数（前端无需重启）。输入框上方那个「正在处理子任务」的工具提示（`frontend/src/App.vue` 的 `task`）不在任务列表里，本次未动。

#### 摘要调用不再混进助手正文

- **动机**：2026-09-21 那轮真实会话（轮次 `0030de3a3183`）里，助手答复**开头 5293 字是摘要中间体** —— `## SESSION INTENT` / `## NEXT STEPS` 那一整段，真实答案从 `## 方案要点（8 步，user-service）` 才开始。根因是一条**上游缺声明**：`langchain` 的 `SummarizationMiddleware` 在类上写了 `transformers = (InternalCallTransformer,)`（`langchain/agents/middleware/summarization.py:245`），靠它把中间件自己的模型调用事件从流里丢掉；`deepagents` 的 `_DeepAgentsSummarizationMiddleware` 只把活转包给内层那个实例（`deepagents/middleware/summarization.py` 的 `_lc_helper`），**自己那条 `transformers` 没有** —— 于是 `create_agent` 收集 transformer 时（`langchain/agents/factory.py:1849-1856`）漏掉它，没人丢：摘要正文顺着根层的 `text-delta` 流下来，被我们的消费端当成了助手在说话。
- **时间线佐证**：22:45:33 那次调用的用量（输入 106752 / 输出 3506 tokens）与 `/conversation_history/...md` 的 404 同秒；22:48:19 下一次调用输入掉到 83034，即历史已被替换。落库记录 id=267 共 6414 字 = 前述摘要 5293 字 + 真答复约 1100 字。
- **改动**：
  - `src/ai_coding_harness/stream.py`：新增 `INTERNAL_CALL_KEY = "lc_internal_call"` 与判定函数 `is_internal_call(event)` —— 只认 metadata 里那个**键在不在**（键值是一次性随机 token，只有 langchain 内部拿得到，我们不必比对值；`tools` 通道的 `data` 是单值、没有 metadata，判定天然为假）；`V3Translator.feed()` 开头把这类事件**整条丢掉**。丢在这里而不是 `service` 里，是因为「哪些事件算正文」是事件形状的知识，归这个模块；也顺带免了它那条 `message-start` 把主智能体的待办块登记清掉。
  - `src/ai_coding_harness/observability/run_log.py`：`message-finish` 分两支 —— 内部调用记「摘要调用完成：…」，其余照旧「LLM 调用完成：…」。**日志这边故意不筛**：一次十万输入的调用正是要能查账的，只是得看得出是谁花的。
- **关键决策**：
  - **在消费端兜，不动上游**。也可以给 `_DeepAgentsSummarizationMiddleware` 补一条 `transformers`，但那要改第三方库代码（或 monkey-patch）；判定键是 langchain 公开的约定，自己兜更稳，也更贴近「我们只认哪些事件算正文」这条职责。
  - **不清理已经落库的那条记录（id=267）**。它是当时真实发生过的事，改了反而看不出根因；新的一轮起不再出现即可。
  - **摘要调用的 token 照旧计入 `llm_calls`**，只是在日志里另署一个名。
- **验证**：`tests/v3_events.py` 的造事件器加 `internal` 开关（造「中间件自己发起」的那类事件）；`tests/test_stream_v3.py` 新增 `TestInternalModelCallsAreNotTheAnswer` 5 条（内部调用三帧都不产出、夹在两段正文之间不会把正文切成两块、普通调用照常流、工具事件判定恒假、判定只看那个键）；`tests/test_run_logging.py` 新增 `TestInternalCallLines` 3 条（摘要署名 / 普通署名 / 归属仍按 `lc_agent_name`）。全量 `pytest` **700 条全过**（基线 692）；ruff 改动文件干净（另顺手删掉 `tests/v3_events.py` 里一条对未启用规则 `A002` 的无效 `noqa`）。
- **遗留**：**改动尚未在活的后端生效**，要重启才算数。本轮监控另看到两处，**未纳入本次改动**：主清单最后两项（3「制定方案并 `storePlan` 落库」、4「向用户呈现方案要点」）实际都做完了却留在 `in_progress` / `pending`；以及子智能体清单挂到了兜底容器项、而非某个主委派项。

#### 清单口径回改：coder 一项对应方案的一步；qa 不再挂清单

- **动机**：上一版（见下下条「实施完成后面板自动收起；coder 的清单不再复述方案步骤」）要求 coder 执行方案时「不要照抄方案的步骤」，理由是方案进度已由 `updatePlanStep` 回写方案面板，两处各刷一遍。**实测那样会塌成一条「执行计划中」的占位** —— 用户在清单上看不出走到哪儿了。另一头同时补上：qa 形态挂 `write_todos` 是纯空转（「查一下、答一下」两步，清单只会多出一份）。
- **改动**：
  - `src/ai_coding_harness/agent/prompts.py`：基础提示词**拆成五段再拼**（`_BASE_HEAD` + `TODO_PROMPT` + `_BASE_ENV` + `GIT_PROMPT` + `_BASE_TAIL`），清单规则与 git 小节各自独立成段，让某个形态能**整段摘掉**它不需要的小节，而不是在通用规则里写反向禁令；写侧拼出的 `BASE_PROMPT` 与拆分前**逐字一致**。新增 `READONLY_TODO_BASE_PROMPT`（analysis 用：只读、留清单）；`READONLY_BASE_PROMPT`（qa 用）照旧两段都不含。`_BASE_HEAD` 里摘掉清单两条后重新编号为 1-5。
  - 同上文件新增 `TODO_PROMPT`，**两条并存、各管一头**：第 2 条末尾保留上一版那句「**执行已有方案时，清单不要照抄方案的步骤** —— 方案进度由 `updatePlanStep` 逐条回写、显示在方案面板上，再抄一遍等于同一件事说两处；这时清单只写你自己的动作，说明「正在执行方案的哪一步」与收尾即可」（管**措辞**）；新增第 3 条「**执行已有方案时，一项对应方案的一步**：把方案的每一步各列一条，条目写这一步在动什么（如「执行方案步骤 2：改密钥存储」）—— 不要只留一条「执行计划中」这样的占位，那样用户在清单上看不出走到哪儿了」（管**粒度**）。
  - 同上文件 `_CODER_ROLE` 工作流程**插入第 3 步**「**落清单**：取到方案后立刻调 `write_todos` 写出本轮清单」（位置是用户指定的：第 2 步取计划之后、动手之前），原 3-7 顺延为 4-8；「按步骤实施」下保留原来的子条目「**你的清单只管自己的动作，不复述方案**：…清单不是它的副本」并补一条「**清单同步**：同一步把清单里对应的那一条也改成 `running` / `completed`，收尾时再整份调一次 `write_todos`」。
  - `safety/policy.py`：qa 行的 `hidden_tools` 由空改为 `frozenset({"write_todos"})` —— 提示词摘段是「劝它别选」，这条是「选了也不执行」，与 planner 撤掉写文件工具同一手法。
- **关键决策**：
  - **粒度与措辞两条都要，谁单独放着都走偏**。只有措辞那条 →塌成「执行计划中」一行；只有粒度那条 →整段照抄方案。两条条件相同（都是「执行已有方案时」）但约束不同的东西，故没合成一条。
  - **撤工具，不只删提示词**。上游 `TodoListMiddleware` 给每个智能体（主控与全部子智能体）都装了 `write_todos`，而 `HarnessProfile.excluded_tools` 的键是**模型**（主控与 planner 共用一个模型），表达不了「按形态」。仓库既有的按角色撤工具杠杆就是 `BoundaryMiddleware`（既隐藏又拦截），qa 走它。
  - **残留（自觉取舍）**：上游那段 TodoList 的系统提示词片段写死在中间件的 `wrap_model_call` 里、摘不掉；工具藏起来后它调不到，代价与 planner 的 `write_file` / `edit_file` 相同，接受。
  - **analysis 保留清单**：一轮分析常是四五步取证（实测清单 4-6 条且有用），摘的是问答，不是全部只读形态。
- **验证**：`tests/test_boundary.py` 的 qa 行补 `("write_todos",)`；`tests/test_prompts.py` 把 `TestTodosDoNotDuplicateThePlan` 换成 `TestTheListCarriesThePlanSteps`（粒度在、措辞也在、落清单的位置钉在第 3 步）与 `TestQaHasNoTodoList`（qa 提示词里不出现 `write_todos` 与「任务清单规则」；analysis 与其余六个形态都还在）；全量 `pytest` **692 条全过**；ruff 改动文件干净（全仓仍是那 16 条既有告警）。
- **遗留**：
  - **「不复述方案」那半句 2026-09-21 期间被误删过一次**（手滑），本次一并补回 —— 补回前后它的措辞未变，只是位置从「通用规则第 5 条末尾」挪进 `TODO_PROMPT` 第 2 条末尾。
  - 清单规则那段从「通用规则」挪进 `TODO_PROMPT` 后内容一字未动（含收尾那半句与「条目就用他话里的对象」）。
  - 提示词与边界两半**尚未在活的后端生效**，要重启才算数。

#### 子任务折叠：开关认「哪一项」，不再认文本

- **动机**：展开一个子清单时，界面上同时展开好几个 —— 不是点错了，是折叠态**按条目文本**记的（`todoOpen[t.content]`）。文本相同的项共用一个开关，而文本相同这件事在真实数据里很常见。
- **现场证据**（那条真实会话 68 条记录）：一轮里会落下**多个同名的兜底容器** —— 子智能体名取不到时容器都叫「子智能体任务」（`stream.py` 的 `_container`），该会话里这个名字出现 **3 次**；跨消息重名的顶层条目另有 **5 种**（如「核实仓库现状…」出现 2 次）。修前实测：点其中一个「子智能体任务」的开关，**三个**同时展开（子项 6 / 7 / 4 全部渲染出来）。
- **改动**（全在 `frontend/src/App.vue`）：
  - 折叠态的 key 换成 `${消息下标}:${文本}#${同文本第几次出现}`（新增 `todoKey`）；`isTodoOpen` / `toggleTodo` 改为收「消息 + 下标」，模板三处调用跟着传参。
  - `resetPlan()` 补一行清空 `todoOpen`（跨会话留着会让新会话的同位项莫名是展开的），与既有的 `expandedSteps` 做法对齐。
- **关键决策**：
  - **不给条目加稳定 id，也不动后端的容器命名**。主清单项在多次 `write_todos` 之间会重排，后端也给不出稳定 id；容器叫什么与「点一个只开一个」无关。键在前端算得出来，够用且改动最小。
  - **默认展开规则（父项或任一子项在跑就展开）不变** —— 子智能体干活时正要看那份进度。
- **验证**：修后在同一界面上复验 —— 点消息 59 的容器只开它自己（子项 6），另外两个保持原状；点消息 69 里那个已展开的同名容器只关它自己。浏览器控制台无报错；`npm --prefix frontend run build`（含 `vue-tsc` 类型检查）通过。
- **遗留**：`.claude/launch.json` 新增两条 —— `frontend-attach`（指向 5173，本机未启用 URL 附加，留着备用）与 `frontend-preview`（在 5174 起第二个 vite 实例，实测用的就是它；你的 5173 没被碰过）。

#### 实施完成后面板自动收起；coder 的清单不再复述方案步骤

- **动机**：两件收尾的事。一是方案落终态之后，右侧那块面板一直占着宽度，把正文挤窄，而这时你多半已经不需要它了；二是 coder 执行方案时把方案步骤原样搬进 `write_todos`，方案面板上的进度（`updatePlanStep` 逐条回写）与清单说的是同一件事，两处各刷一遍。
- **改动**：
  - `frontend/src/App.vue`：`runStream` 的收尾块里，当前会话本轮结束且 `plan.status` 为 `done` / `failed` 时置 `planCollapsed = true`（点侧栏 rail 随时展开回来）。
  - `src/ai_coding_harness/agent/prompts.py` 两处：通用规则第 5 条补一句带条件的口径 —— 「**执行已有方案时，清单不要照抄方案的步骤** —— 方案进度由 `updatePlanStep` 逐条回写、显示在方案面板上，再抄一遍等于同一件事说两处；这时清单只写你自己的动作，说明「正在执行方案的哪一步」与收尾即可」；`_CODER_ROLE` 第 4 条后补一条同口径的子条目（离 coder 更近；通用规则里的话单放着被无视过，故刻意重复）。
- **关键决策**：
  - **只在方案落终态时收，不是「一轮结束就收」**。`pending`（刚出方案，正要读）与 `executing`（半途）都不收 —— 收了正好把要看的东西藏起来。
  - **收起的时机放在轮末，不放在 `plan` 帧到达时**。帧到达时回答可能还在写，那一刻收会显得突兀。
  - **清单口径写成条件句，不写成通用禁令**。qa / analysis 那几轮手上根本没有方案，无条件禁止等于没说清单该怎么写。
- **验证**：`tests/test_prompts.py` 新增 `TestTodosDoNotDuplicateThePlan` 4 条（条件句在、两处口径一致、方案面板被点名为进度视图、方案是步骤的出处而非清单的副本）；全量 `pytest` **688 条全过**；ruff 改动文件干净；`npm --prefix frontend run build`（含 `vue-tsc` 类型检查）通过。
- **遗留**：
  - **提示词那半改动尚未在活的后端生效** —— 要重启后端才算数（前端那半 vite 热更即生效）。
  - 没加「只自动收一次」的标记：方案 done 之后手动展开回看，再跑一轮结束时仍会被收起（点一下即回）。若嫌烦再加。
- **已生效**：前端（App.vue）随 `npm run dev` / 热更立即生效；提示词待重启后端。

#### 待办清单改成「先分析、再落清单」，并写明它是给用户看的

- **动机**：`write_todos` 此前被写成**开场动作**（「每个任务都必须先使用 `write_todos` 工具生成清单」），模型据此一上来先摆一份清单 —— 那时需求还没读清，摆出来的是猜的。而这份清单在界面上是**唯一一份进度**，用户看的就是它，条目却常写成「处理任务」「执行构建」这类内部口径，甚至直接拿工具名当条目，看了等于没看。
- **改动**（全在 `src/ai_coding_harness/agent/prompts.py`，三处）：
  - 通用规则第 4 条：改为「先看清这一轮要做什么，再把清单落下来：判断请求类型、读清需求之后才调 `write_todos` —— 一上来就写，写出来的是一份猜的清单」，并点明「这份清单是给用户看的（界面上就这一份进度）」。收尾那半句（以最后一次调用为准 / 做完标 `completed` / 不许把 `in_progress` 原样留着）**一字未动**。
  - 通用规则第 5 条：保留「按意图分类列步骤」，补上「也贴合他的说法 …… 既然是他看的，条目就用他话里的对象（哪个接口、哪个文件、哪件事），不写「处理任务」「执行流程」这类内部口径，也不把工具名当条目」。
  - 主控角色段的「动手前先规划」：改为「先把这一轮要做什么分析清楚（判断请求类型、读清需求），再用 `write_todos` 写出本轮清单，然后动手」；后半句「不允许一次 `write_todos` 都没调就直接派子智能体」原样保留。
- **关键决策**：
  - **只改「什么时候写」，不改「必须写」**。派子智能体之前自己那份清单要先有，这条是防「子清单没有落点、整份显示不出来」的老毛病，动它会把已经修好的显示问题重新打开。
  - **补的是顺序与口径，不是机制**。清单是猜的还是读懂了的，代码判不出来，只能靠提示词写死；两个旧写法都留了反向断言（`每个任务都必须先使用 write_todos 工具生成` / `先自己用 write_todos 写出本轮清单再动手` 都不许再出现）。
- **验证**：`tests/test_prompts.py` 新增 `TestTodosComeAfterTheTaskIsUnderstood` 4 条；全量 `pytest` **684 条全过**；ruff 改动文件干净（全仓仍是那 16 条既有告警）。
- **遗留**：另有一条「只在状态真有变化时才重发清单」**已定：不写** —— 怕被当成省事的口子（该更新的时候也不更新）。
- **已生效**：2026-09-21 重启后端后加载（`logs/backend_20260921_r3.out.log`）。

#### 空转刹车：同一工具同一参数反复调用时，先劝、再停

- **动机**：那一轮 686 秒（见下一条「终止」）真正的病灶不是「停不下来」，而是**它自己在原地打转**：日志里 `execute git status` 与 `git log` 交错着来回转，每一圈同样的输入、同样的输出，模型却当成进展。用户点的「终止」只是事后收尾，没人拦得住它继续转。要的是**判得出原地打转、并且当场收尾**。
- **改动**：
  - 新增 `src/ai_coding_harness/agent/middleware/guard.py`：`TurnState`（每轮一份：`message` / `history` / `repeats` / `max_handoffs` / `rejudge` 回调 / `aborted` 标记，`note_call` 数重复、`note_handoff` 扣交接额度、`abort` 造中止）、`TurnStop(Exception)`（`marker` + `extra`）、常量 `ABORT_REASON = "repeated_tool"` 与 `LOOP_ABORT_MARKER`、`LoopGuardMiddleware`（`awrap_tool_call`）。
  - `src/ai_coding_harness/config.py`：新增 `loop_guard_repeats`（`LOOP_GUARD_REPEATS`，默认 3，**0 = 关掉**）；`.env.example` 补键与说明。
  - `agent/graph.py`、`agent/subagents.py`：主智能体与三个子智能体各挂一份 `LoopGuardMiddleware`。
  - `src/ai_coding_harness/server/service.py`：`chat()` 每轮新建 `TurnState`（带上 `message` / `history` / 阈值 / 重判回调），经 `configurable["turn_state"]` 注入（智能体是长期存活对象，逐轮变化的东西只能走 config）；`_run_agent` 新增 `state` 必填关键字参数与 `except TurnStop` 出口；收尾那步**再看一眼** `state.aborted`。
  - `_record_interrupted` 增第三条分支：`aborted` 路径的 `extra` 带 `aborted` / `tool` / `repeats`，措辞与「取消」「故障」两条都不同（`state.aborted` 有值即中止，异常被某层吃成 ToolMessage 时靠它兜住）。
- **关键决策**：
  - **数的是本轮出现次数，不要求相邻**。只数「连续重复」会把那次事故整个漏掉 —— `git status` 与 `git log` 中间隔着别的调用，按相邻判就永远数不到第二次。
  - **第 N 次只劝、第 N+1 次才停**。第 N 次返回一条 `status="error"` 的 `ToolMessage`（**不执行**，并说清是哪个工具、第几次、为什么不再执行），模型据此换做法；一上来就抛 `TurnStop`，模型连改的机会都没有。
  - **键是 `(工具名, 参数)`，参数用 `json.dumps(sort_keys=True)` 归一**。参数顺序由模型生成、每次都不同，按顺序比较等于每次都算新调用，这层就永远数不到 3。不可序列化的参数退化为 `repr`，不在计数上炸。
  - **阈值与开关挂在 `TurnState` 上，没有轮次上下文就整层直通**。单测、脚本直接跑图时 `configurable` 里没有 `turn_state`，那时必须完全不受影响。
  - **中止既不是取消也不是故障**：另有一套标记（`LOOP_ABORT_MARKER`），`extra` 里既不带 `cancelled` 也不带 `error`。`state.aborted` 上留痕是纵深 —— 异常万一被某层吃成 `ToolMessage`，本轮会以「智能体完成」记进库、用户看到一句没有下文的正文。
- **验证**：新增 `tests/test_loop_guard.py` 22 条（计数与键归一、阈值 0 关闭、无上下文直通、第 N 次拒且工具**真没执行**、第 N+1 次抛且 `state.aborted` 同步、交错重复仍被拦、换个参数放行、警告措辞）；`tests/test_interrupted_record.py` 新增 `TestLoopAbortRecord` 4 条（抛出路径、无产出路径、异常被吞后靠标记收尾、健康轮不受影响）。
- **遗留**：
  - **改动尚未在活的后端生效** —— 要重启后端才算数（本次未重启）。阈值 3 合不合适、真机上打转是不是这个形状，都得重启后拿真实会话看。
  - 守卫只认**参数全等**的重复：换个说法（`git status` → `git status --short`）或换个等价命令就绕过了。它拦的是那次事故那种「一模一样的输入」，不是通用的「没有进展」。

#### 能力交接：`handoffTask` —— 形态做不了这一轮时，交给合适的形态接着做

- **动机**：形态是按意图裁出来的（`agent/profiles.py`），一旦裁得不合适，模型手里就没有那件工具。光在提示词里写「做不到就说做不到」不够 —— 它要么两手一摊，要么硬着头皮承诺一个做不出的事（686 秒那轮就是：plan 形态手上没有写权限，于是把方案复述一遍再问用户，绕不出来）。得给它一条**真能走**的路。
- **改动**：
  - 新增 `src/ai_coding_harness/tools/handoff.py`：`handoffTask(reason, notes="")` 与 `HandoffContext`（会话 / 运行时 / 管理器 / 设置 / 当前形态）。工具自己**不做路由**：调 `detect_intent(..., force_llm=True)` 重新判一次意图，只有重判结果可用时才放行。
  - `src/ai_coding_harness/server/intent.py`：`detect_intent` 新增 `force_llm` —— **跳过口令直通那一层**，直接问分类模型。
  - `src/ai_coding_harness/server/service.py`：`_rejudge_intent`（`force_llm=True` 那条路，超时与模型都在这一层）与 `chat()` 里的接线。
  - `tools/register.py`：`build_main_tools(..., handoff=None)` 末尾按需追加 `handoffTask` —— 五个形态**都**拿得到（`_FORM_TOOLS` 那张表裁的是「这个形态有什么能力」，交接恰恰是能力不够时的出路，所以它不在表里）。`handoff=None` 时不挂，脚本与单测直接建工具不必凑一个会话上下文。
  - `config.py`：新增 `handoff_result_max`（`HANDOFF_RESULT_MAX`，默认 4000）；`.env.example` 补键。
  - `agent/prompts.py`：新增 `_CAPABILITY_EXIT` 段（**五个形态共用**，接在 `_MAIN_CORE` 之后）；`_READONLY_BOUNDARY` 补第三条 —— 把「用户明确要改代码」（该交接）、「用户在问能不能改」（该请他明说「开始实施」）、「需要的是方案」（该请他明说「出方案」）三种情况分开写。
- **关键决策**：
  - **只有模型重判（`SOURCE_LLM`）才放行**。要纠正的常常正是口令层（句子里一个「实施」把意图钉成 coding，真实诉求是「先出方案」），拿它的复读去换形态等于换个姿势再猜一次。分类器不可用、退回关键字口径（`SOURCE_FALLBACK`）时一律拒。
  - **工具上不带「目标形态」参数**。目标由重判结果决定，不由模型的自我诊断决定 —— 让它自己说是该交给 plan 还是 review，就又回到「猜」上了。
  - **内层跑在派生线程上**（`…::handoff-1`）。直接蹭外层线程的话 `checkpoint_ns` 会被重置成 `""`，两边落到同一个根槽上。
  - **跑内层前把 `var_child_runnable_config` 摘干净，跑完还原**。实测：内层 `ainvoke` 的模型事件会带着**空 namespace** 混进外层 v3 流，被 `V3Translator` 当成外层自己的回答下发并落库（正是要避免的「一轮里冒出别人的话」）；`callbacks=[]` **挡不住**，摘掉那个 ContextVar 才挡得住，且内层图照常跑。
  - **额度扣在「尝试」上**，同一轮第二次交接直接拒（被拒之后还再试的多半是原地甩锅）；所有拒绝都返回**普通字符串**，措辞里不出现「失败 / 错误」—— 说成故障模型会当成环境问题去重试。
  - **结果截断在 4000**：内层是完整的一轮运行，正文可能上万字，而它在外层只是一个工具返回值，外层要的是结论。
  - **原计划里两件事拆掉了**：`HandoffMiddleware` 不必要（`LoopGuardMiddleware` 一个类就够，交接的判定与装配都在工具与 `register.py` 里）；`server/stream.py` 把 `handoffTask` 认成委派父节点也不必做 —— 内层事件既然被摘干净了，外层流里根本没有它的 todo / 工具事件可挂。
- **验证**：新增 `tests/test_handoff_tool.py` 22 条（六种拒绝情形与「拒绝不是错误」的措辞、按重判结果取形态、派生线程 id、内层 configurable 带 `intent` / `redact_terms` / 同一个 `TurnState`、自己的递归上限、**内层那一刻的环境 config 是 `{}` 且跑完已还原**、原话与历史与 `notes` 全部转达、空结果与截断、正文提取的六种形状）；`tests/test_tool_registry.py` 新增 3 条（五个形态都拿得到、不给 `handoff` 就不挂、子智能体拿不到）；`tests/test_intent_detection.py` 新增 `TestForceLlmRejudges` 7 条与 `_rejudge_intent` / `_intent_history` 的接线 4 条；`tests/test_prompts.py` 新增四组提示词守卫 14 条。
- **遗留**：
  - **改动尚未在活的后端生效**（本次未重启）。真机上跑一次 `handoffTask` 还没做 —— 「内层不进外层事件流」是拿假内层智能体在单测里钉的，真机的 v3 流没验过。
  - 交接**每轮一次**：额度固定为 1（`TurnState.max_handoffs` 上可传，暂未开配置项）。接手的那一轮看不到外层上下文，全靠 `notes`。

#### 补回主控提示词里丢掉的前置规划约束

- **动机**：`tests/test_prompts.py::TestMainPlansBeforeItDelegates` 已连续三批记着同一条红：`_ROLE_ORCHESTRATE` 那句「动手前先规划」在某次重写「工作流程」时丢了，全仓已无该串。它对应的是一次真实事故（主控一份清单都没写就直接派 `coder`，子清单因此没有落点、整份显示不出来），所以**补回约束**、而不是把断言改掉。
- **改动**：`agent/prompts.py` 的 `_ROLE_ORCHESTRATE`「## 工作流程」加回一句前置规划（步骤与编号不动）。
- **验证**：全量 `pytest` **680 条全过**，这条既有红消失。

#### 终止：输入框那一格在跑动时变成「终止」，点了就停住这一轮

- **动机**：跑飞的一轮**没有刹车**。实测栽过一次（2026-09-21）：某会话一轮 coding 跑了 686 秒还在重复 `execute git status` + 同一份 `write_todos`，用户既停不掉也刷不掉（刷新断的只是订阅，运行照跑、token 照烧），最后只能重启后端 —— 重启是唯一有效的动作，这显然不对。
- **改动**：
  - `src/ai_coding_harness/server/runs.py`：`RunHub.cancel` 改为返回 `bool`（**是否真有一轮在跑**）；签名不变，仍从注册表里 pop 掉那一轮再取消它的 task。
  - `src/ai_coding_harness/api/routes/sessions.py`：新增 `POST /sessions/{id}/cancel`，回 `{"cancelled": bool}`。**不查库、不改会话状态** —— 会话与历史都留着（要拆会话走 `DELETE`）。
  - `src/ai_coding_harness/server/service.py`：`_run_agent` 新增 `except asyncio.CancelledError` 分支 —— 调 `_record_interrupted` 补一条记录后**原样抛出**；`_record_interrupted` 新增 `exc: BaseException | None = None`，`exc is None` 即终止路径：正文尾巴/单独一条写 `CANCELLED_BY_USER = "〔已由用户终止〕"`，`extra` 里 `cancelled: True`（**不带** `error`，措辞里也不出现异常名）。故障路径的措辞与形状一字未动。
  - `frontend/src/api.ts`：`postChat` / `resumeChat` / `consumeStream` 加可选 `signal`；新增 `cancelChat(id)`（读回 `cancelled`）。`consumeStream` 的 catch 先判 `signal?.aborted` —— 用户主动断的不重连，否则白跑一个请求。
  - `frontend/src/App.vue`：`stopping` / `streamAbort` 两个状态；`stopRun()` 先 `await cancelChat(id)` 再 `abort()`；`runStream` 收尾按 `stopping` 决定「补 `〔已由用户终止〕`」与「不发那行 `[错误]`」；`send()` 的 catch 里 `stopping` 单独一条路（响应头还没回来就点了终止：**不**摘掉那两条消息、**不**弹窗）；模板发送位在 `loading` 时换成「终止」（`:disabled="stopping"`）。
  - `frontend/src/style.css`：`.send.stop` 走危险色（`var(--danger, #d64545)`）。
- **关键决策**：
  - **必须有后端路由，前端断流不算数**。运行宿主（`RunHub`）刻意把这一轮从请求生命周期里剥出来了，`EventSourceResponse` 那套约定是「请求断了断的只是订阅」。所以「终止」＝ `cancelChat` **且** 本地 `abort()`：前者真停，后者立刻松手。取消指令没送到也照停本地，但要提示「那一轮可能仍在运行」。
  - **中止要落一条记录**。不然库里就剩一条用户消息空着，刷新后看不出这轮出过事。复用 `_record_interrupted` 而不是新写一条路 —— 它已经管住了脱敏、`parts` 回放与 `summary` 口径。
  - **取消不是故障**：不产 `error` 帧（`CancelledError` 是 `BaseException`，pump 的 `except Exception` 本来就接不住它，`finally` 照跑 → 已挂着的订阅者看到 `done`、流干净结束），措辞里不出现 `CancelledError`，也不分「有产出 / 未产出」两句。
  - **重按幂等**：没在跑就回 `{"cancelled": false}`，不是错误 —— 用户点慢了、这一轮刚好自己跑完，都走这条。
  - **只清自己那份句柄**：`runStream` 收尾与 `send()` 的 catch 都按 `streamAbort === controller` / `signal` 判一下再清 —— 切走会话后旧流的收尾不能去动新会话的 `stopping`，否则新会话里点终止会被当成传输故障、正文后面多一行「错误」。
- **验证**：`ruff check` 改动的 5 个文件干净（`src` + `tests` 口径另有 16 条**既有**告警，全在本次未改动的文件里）。全量 `pytest` **600 条：599 passed / 1 failed** —— 那 1 条仍是既有的 `tests/test_prompts.py::TestMainPlansBeforeItDelegates`（`动手前先规划` 全仓已无该串），与本次改动无关。`npm run build`（含 `vue-tsc`）通过。
  - 新增 7 条用例。`tests/test_run_hub.py`：挂着订阅者时取消 → 流干净收尾且**无 error 帧**；`cancel` 的布尔值（从没跑过 / 真在跑 / 已跑完 / 重按）；`POST /sessions/{id}/cancel` 的 `{"cancelled": …}` 形状（前端读的就是这个键）。
  - `tests/test_interrupted_record.py`：终止且有正文 → `content == "前半段正文\n\n〔已由用户终止〕"`、`extra.cancelled is True`、**无** `error` 键、无 `CancelledError`/「错误：」字样；一帧都没有就终止 → 只留标记（不写「未产出内容」）；有待办 → 标记补进 `parts` 尾部；`CancelledError` 必须**原样抛出**（吞掉的话 `RunHub` 就分不出用户终止，前端按钮不复位）。另给故障路径加了一条守卫：`extra` 里不得出现 `cancelled`。
- **遗留**：
  - **改动尚未在活的后端生效** —— 要重启后端并重新构建前端才算数（本次未重启）。浏览器上的实际点击验证因此也还没做。
  - 取消只作用于本地这一轮的 task：沙箱里那一次 `execute` 是否随之终止**未做验证**（后面真撞上再查）。「停编排」与「停容器里的进程」不是一件事。
  - 运行宿主是**进程内**一份：多 worker 或重启后 `cancel` 找不到那一轮，会回 `false`（与既有缓冲的边界相同）。
  - 取消与 `_record_interrupted` 那次写库之间有极窄的窗口：期间**再**被取消一次，那条标记会丢（丢了也只是个标记）。

#### 熔断：主导连续失败就开闸，冷却期内不再撞它

- **动机**：降级兜底解决了「某家挂了这一轮还能答」，但没解决**每次都要先撞一次死掉的主导**。智能体一轮对话要跑多次模型节点（`model → tools → model …`），于是死着的主导会被反复撞：每一轮都白等一次 SDK 重试耗尽，这段等待全额计进用户感知延迟；主导**半路断流**那种情形还会把半截句子重复吐进流里。持续故障时该做的是**暂时不打它**。
- **改动**：
  - `src/ai_coding_harness/agent/middleware/fallback.py`：模块级 `_CIRCUITS`（键是**主导模型名**）与 `_Circuit(failures / opened_at / probing)`；`_may_try`（这会儿能不能撞主导）、`_note_primary`（记这一次的结局）、`_log_skip`（开闸期的留痕）；新留痕关键字 `CIRCUIT_LOG_TAG = "MODEL_CIRCUIT"`；占位异常 `_PrimarySkipped`；模块级时钟 `_now`（单测替换它，不用真等冷却）；`reset_circuits()` 给单测清状态。构造参数新增 `circuit_threshold`（默认 0＝关）与 `circuit_cooldown`。
  - `src/ai_coding_harness/config.py` + `.env.example`：`FALLBACK_CIRCUIT_THRESHOLD`（默认 3，`0` 关掉）与 `FALLBACK_CIRCUIT_COOLDOWN_SECONDS`（默认 60）。
  - `src/ai_coding_harness/session/manager.py`：`_fallback_middleware` 构造时把两个值传进去。
  - `tests/test_model_fallback.py`：新增 8 条，带可控时钟 `_Clock`。
- **关键决策**：
  - **不加依赖，自己写**（40 行）。先查过三家（`aiobreaker` / `pybreaker` / `circuitbreaker`）：`pybreaker` 纯同步；`circuitbreaker 2.1.3` 的 `call_async` 是 `with self:`，而 `__enter__` 返回 `None`、**不判开闸状态**，`__exit__` 无事还 `self.reset()` —— 实测 `failure_threshold=2` 跑三次一次都没熔断，只有装饰器那条路才拦；`aiobreaker 1.2.0` 在 3.14 上确实能用（阈值后不再调用下游、`exclude=[GraphBubbleUp]` 生效、半开探针成功即闭合），但它的元数据只声明到 3.6，且**熔断那一次会把原异常换成 `CircuitBreakerError`**（`raise … from`）—— 我们就少了 `MODEL_FALLBACK error=` 里那个真实原因。
  - **熔断那一刻仍抛原始异常**：不换成「已开路」异常，上游 `super()` 照常走它自己的兜底循环 —— 我们只在最前面加一道闸，判据仍旧归上游。这正是自己写相对 aiobreaker 的一处实际好处。
  - **按主导模型名共享、进程内一份**：同一家挂了，用这家的所有角色一起受益（`reviewer` 撞死 kimi 开到闸，别的 kimi 角色也免撞）。代价是多个 worker 各记一份，要跨进程得进 Redis —— 与 memory 里「无状态 worker 方向」一致，现在不做。
  - **只记主导的账**：兜底那次失败不算到主导头上，否则一家坏会连带把另一家的闸也拉下来。**`GraphBubbleUp`（暂停/恢复/嵌套图跳转）不算失败**：它跟供应商健康无关，算进去会被无关的中断顶开闸；但它也不留痕、且要**放掉探针标志**，否则那一轮之后不再有探针，熔断窗就永不开闸。
  - **冷却到点放一次探针**（`probing` 标志保证同时只有一个）：探针成功即 `state=closed` 并归零，失败即 `state=reopen` **重新计时**（不是把冷却加长）。
  - **开闸期的轮次不再打 `MODEL_FALLBACK`**：那条痕的 `error=` 要填真实原因，而这里压根没发生过模型调用。改打 `MODEL_CIRCUIT role=… primary=… state=open failures=3 age=12s fallback=…` —— 一轮的路由仍然查得到，一个关键字即可 `grep MODEL_CIRCUIT logs/agent.log`。
  - **阈值默认 3**：给「一次偶发 429」留余量，同一家连续三次失败才认定它在坏。
- **验证**：`ruff check` 改动的 4 个文件干净（`src` + `tests` 口径另有 16 条**既有**告警、`alembic/` 里 15 条，全在本次未改动的文件里）。`tests/test_model_fallback.py` 由 16 条增至 24 条。全量 `pytest` **593 条：592 passed / 1 failed** —— 那 1 条仍是既有的 `tests/test_prompts.py::TestMainPlansBeforeItDelegates`（`动手前先规划` 全仓已无该串），与本次改动无关。
  - 8 条新用例（注入假时钟，不真等 60 秒）：阈值 2 时前两轮各撞一次主导、**第 3 轮起一下都不撞**（`tried` 序列钉住）；中间成功一次即归零（否则零散故障会攒出一场熔断）；阈值 0 ＝ 每轮都撞且零 `MODEL_CIRCUIT`；冷却到点探针成功 → `state=closed`；探针失败 → `state=reopen` 且重新计时；兜底失败不计入主导的账；`GraphBubbleUp` 不计数不成瘾；探针被控制流异常打断后仍探得到。
  - 单测抓到一处真 bug：开闸那行的 `fallback=` 起初打成了**主导名**（那一刻 `handler` 还没拿到兜底请求），改成构造时存下兜底模型名。
- **遗留**：
  - 改动已生效（2026-09-21 重启后端后加载）。
  - **活体验证试过一次，没能撞开熔断（2026-09-21）**。做法：把 qa / analysis 的主导（gpt）临时指到一个打不通的地址（只动 `.env` 的 `GPT_BASE_URL` 一行，验完已还原、备份已删、后端已用还原后的配置重启），往已有会话连发 4 轮 qa 问题。实测：**轮 1 正常走兜底**（22.5s，首字 22.3s，`MODEL_FALLBACK role=qa primary=gpt-5.6-luna fallback=deepseek-flash error=APIConnectionError`，正文 76 字）；**轮 2~4 各 27~28s、一个字都没吐**（SSE 只有 2 帧、无 `ai` 分片），日志里**既无 `MODEL_FALLBACK` 也无 `MODEL_CIRCUIT`**，只有 3 条 `ai_coding_harness.server.service: 处理失败`。全程只记到 **1 次**主导失败，阈值 3 未到 —— 所以**熔断仍未活体验证**。
  - 为什么只记到 1 次：失败点不在主模型那条链上。轮 2~4 死在 `deepagents/middleware/summarization.py:1209` 的摘要中间件（`_summary_model.ainvoke` → `httpx.ConnectError`，重试约 20s 后冒出 → 「处理失败」），**根本没走到主模型节点**，熔断自然无账可记。根因单列一条，见下「摘要模型的调用绕过兜底」。**离撞开只差两轮能活到模型节点的对话**。

#### 降级兜底：某家模型彻底失败时换一家再试，并在日志里留一条可查的痕

- **动机**：模型的可用性不是我们能保证的（429 限流、5xx、连接断）。SDK 自带重试是**同一条链路**重试，供应商整家挂掉时重试多少次都一样，结果就是这一轮对用户直接失败。需要的是**换一家**再试一次，而且要能事后查出来「这一轮到底跑在哪家模型上」—— 否则用户说「今天答得怪」，我们无从判断是不是降级了。
- **改动**：
  - 新增 `src/ai_coding_harness/agent/middleware/fallback.py`：`FallbackMiddleware`（子类化上游 `langchain.agents.middleware.ModelFallbackMiddleware`）。留痕关键字 `FALLBACK_LOG_TAG = "MODEL_FALLBACK"`，`grep MODEL_FALLBACK logs/agent.log` 即查。
  - `src/ai_coding_harness/agent/llm.py`：把建模型那段抽成 `_config_for` / `_model_from`（原来全在 `build_model` 里，兜底要复用同一套构造细节：温度逐家、窗口 profile）；新增 `build_fallback(settings, *, role)` 与 `_FALLBACK_PROVIDERS`。
  - `src/ai_coding_harness/config.py` + `.env.example`：新增 `fallback_provider`（默认 `deepseek`，留空＝关掉）。
  - 装配：`agent/graph.py` 的 `build_agent` 新增 `fallback` 关键字参数（排在我们这批中间件的**最内侧**）；`agent/subagents.py` 的 `build_subagents` 新增 `fallbacks` 映射（缺键＝该角色没有兜底）；`session/manager.py` 新增必填 `fallback_for` 工厂与 `_fallback_middleware(role, agent)`；`api/app.py` 接线 `build_fallback`。
  - `tests/test_model_fallback.py`：新增 16 条。既有三个测试文件（`test_profiles` / `test_session_reuse` / `test_workspace_runtime`）的 `SessionManager(...)` 调用点补上新的必填参数。
- **关键决策**：
  - **不用 `with_fallbacks` 包装，改子类化上游中间件**：`deepagents._models.resolve_model` 要求 `isinstance(model, BaseChatModel)`，包一层的 `RunnableWithFallbacks` 在 `bind_tools` 那步就炸（实测 `AttributeError: 'ChatOpenAI' object has no attribute 'count'`）。子类化则是把留痕**加在已有机制旁边**：上游的 `awrap_model_call` 本来就是 `try 主导 → 逐个兜底 → 全败抛最后一个异常`，重试循环一个字不动，我们只覆写它以插一条痕。
  - **留痕靠包一层计数闭包，不重写重试循环**：把 `handler` 包成 `counted`，第 2 次被调用即「主导已失败、兜底正接手」，此刻记一条 WARNING；`cause` 由闭包捕获上一次的异常。这样判据归上游、痕迹归我们，上游以后改重试策略我们不用跟着改。带 `extra={"agent": …}` 是为了让这条落在 `%(agent)s` 列里，与其他运行日志同口径。
  - **只记异常类型与 HTTP 状态码，不记异常原文**：供应商的报错体常把 api key 原样回显（`invalid api key: sk-…`），而这是条不落盘的红线。`RateLimitError` / `InternalServerError` / `APITimeoutError` / `APIConnectionError` 四者已经指认了原因，够用。这条也钉在单测里（合成密钥必须不出现在摘要里）。
  - **挂最内侧**：降级重试跑的是它内层的 handler，不该把我们的边界检查与脱敏前处理再跑一遍；反过来，外层的 `ModelRedactionMiddleware` 因此永远看到「最终赢的那个回答」，一次脱敏覆盖所有模型来源。
  - **全局一条保底，不链式**：`FALLBACK_PROVIDER` 只有一个值。链式降级要处理「哪几家、什么顺序、失败怎么记」，收益是极少数「两家同时挂」的场景，先不做。
  - **`coder` 没有兜底**：它的主导模型就是 deepseek，兜底也是 deepseek ＝ 拿同一个模型再试一次，等于没兜。**代价是 deepseek 整家挂掉时编码这一环仍然失败**（主控与 planner 会降级到 deepseek，所以那时编码反而成了唯一的硬点）。这是全局一条保底规则的直接后果，不是漏挂。
  - **配坏了 fail-open，名字打错 fail-loud**：目标供应商名不在白名单（`_FALLBACK_PROVIDERS`）里直接 `ValueError` —— `.env` 写错字属于构建期的错，静默的后果是「以为有兜底，其实一直没挂」，比当场失败难查。而「白名单内但没配齐」（缺模型名或 key/base_url）只记一条 WARNING 就返回 `None`：兜底本身是降级功能，不该因为它把整场会话的构建拖死。
  - **只实现异步版**：服务端统一走 `astream_events`（与 `boundary.py` 同一约定），`wrap_model_call` 不覆写。
- **验证**：`ruff check` 改动的 9 个文件干净（全仓另有 16 条**既有**告警，全在本次未改动的文件里）。全量 `pytest` **585 条：584 passed / 1 failed** —— 那 1 条是既有的 `tests/test_prompts.py::TestMainPlansBeforeItDelegates`（断言 `动手前先规划` 出现在主控提示词里，而全仓已无该串），与本次改动无关。
  - 行为：主导抛 → 返回兜底的回答且 `tried == [主导, 兜底]`；主导成功 → 零日志、兜底模型一次都没被碰；两个都抛 → 上抛的是**兜底那次**的异常（上游 `raise last_exception` 的语义，容易误读成主导的）。
  - 留痕：一条 WARNING 且 `role=` / `primary=` / `fallback=` / `error=RateLimitError/429` 都在，`record.agent == "main"`（主控）与角色名（子智能体）各一例。
  - 挑模型：`reviewer`（主导 kimi）→ deepseek 的模型名、温度取 **deepseek 自己的 0.3**（不是 kimi 的 1，kimi 收 0.3 直接 400）、窗口 profile 取 deepseek 的；`coder` / 留空 / `plan` 对 `glm` → 全是 `None`；白名单外的名字与未登记的角色 → `ValueError`。
  - 装配：猴补 `create_deep_agent` 抓 `middleware=`，确认有兜底时它排最后、没有兜底时最后一项是 `HistoryRepairMiddleware`；子智能体侧确认给定角色的末项是兜底、缺键角色一个 `FallbackMiddleware` 都没有。
- **遗留**：
  - 改动已生效（2026-09-21 重启后端后加载）。
  - **摘要模型的调用绕过兜底（2026-09-21 实测，已知缺口，未做）**：`deepagents` 的会话摘要挂在 `create_summarization_middleware(model, backend)` 上（`deepagents/graph.py:702` 传的**就是主模型本身**），而摘要中间件是**直接** `self._summary_model.ainvoke()`（`deepagents/middleware/summarization.py:1209` → `:432` → `langchain/agents/middleware/summarization.py:886`），**不经过 `FallbackMiddleware` 所在的那条中间件链**。后果：主导整家挂、且该轮跨过摘要阈值时，整轮就是硬失败 —— 重试耗尽后 `httpx.ConnectError` 直接冒到 `service`，记一条「处理失败」，前端一个字都收不到，兜底一点用不上。这恰恰是兜底该挡的场景（比「半路断流漏半句」严重得多）。实测触发条件：`/conversation_history/<thread_id>.md` 是 deepagents 自己的历史文件，**旧沙箱销毁后新建的沙箱里没有它**，轮 2 读它 404 →（我们的沙箱代理把 404 当「沙箱没了」触发恢复）→ 重连后仍 404 → 摘要路径启动（那次上下文 11.5 万 token）。怎么修没定：给摘要单独配一个也带兜底的模型，或把摘要调用也纳入同一套中间件 —— 先出方案再定。
  - **「半路断流」会造成重复文本（已实测；消不消仍待定）**：用探针驱动真实 agent 实测（`astream_events(v3)` 与 `stream_mode="updates"` 两条路径）。主导**先吐两段再抛**（405，报错体里塞了假 key）时，节点序列是 `model → after_model → tools → model → after_model → tools → model → after_model`，**每一轮各漏一次半句** —— 漏几次＝模型节点跑了几次，不是固定 2 次或 3 次；中间轮次兜底答的全是工具调用（所以那 47 片流一片都没落到前端，消费端看不到正文）。漏出的半句进了 SSE 流与落库正文（`service.py:358-364` 是 `ai` 分片的唯一出口），但**不进**模型可见的 state（工具结果与 checkpoint 里都没有它）。反过来说：常见的 405 / 429 配额、连接断都是在**连接期**就失败，**一片都不漏** —— 只有「已经吐了字才报错」才漏。想彻底消掉得在降级前发一个「丢弃已输出」的信号，代价是前端渲染逻辑要动。判据清楚了，**先不做**。
  - **熔断**：本次已做，见上面「熔断：主导连续失败就开闸」那条。

#### 一轮跑到一半报错：把已产出的部分补进记录，别让它在历史里消失

- **动机**：会话 `78780d7a` 的 `#223` 是活样本 —— 用户消息在库里（正文 70 字），**后面没有任何 assistant 记录**，直接跳到下一条用户消息。根因是落库的位置：用户消息在开跑前就写（`chat()`），助手那轮的正文字符串只在 `async for` 循环**之外**写一次（`_run_agent` 末尾），异常一跳过那个位置就永远不执行。后果不只是「这一轮没记录」：浏览器上已经逐字渲染出来的内容，刷新后从库里回放就没了，用户看到的是自己问了一句、底下空白。同一形态的 `#221`/`#222`（两条「重试」）、`#224`/`#225`（重复提问）都是这么留下的。
- **改动**（`src/ai_coding_harness/server/service.py` 一个文件）：
  - `_run_agent`：把「取流 + 消费流」整段包进 `try`，`except Exception` 里先落一条中断记录，再 **`raise` 原样抛出** —— 错误帧的产生仍然只留在 `chat()` 那一处，SSE 实时行为一个字没变。取流那一跳（`astream_events`）也包进来：它炸的时候正文是空的，但「这一轮失败了」这件事同样该留痕。
  - 新增 `_record_interrupted()`：落一条 `type="assistant"`（**不新增 type**，前端与接口 schema 都不用动）。`content` = 已产出的正文 + 中断标记 + 脱敏后的异常文本；`extra` = `{"intent", "partial": True, "error": {"type", "message"}}`。
  - 新增模块级 `_interrupted_tail()` 与两个常量 `INTERRUPTED_PARTIAL` / `INTERRUPTED_EMPTY`（「〔本轮中断，以上为部分输出〕」/「〔本轮中断，未产出内容〕」）。
- **关键决策**：
  - **标记与错误都写进 `content`，不藏在 `extra` 里**：前端 `partsOf()`（`App.vue:141`）只在**没有 parts** 时才退回 `content`，而 `extra` 目前只被用来读 `parts` —— 写进 `content` 前端零改动就看得见。顺带与实时视图对齐：前端收到 error 帧做的正是 `append("\n[错误] …")`（`App.vue:523`），所以刷新前后看到的东西一致。`extra` 里同时留结构化字段，以后做角标或排查有据可依。
  - **有 parts 的回合要补一块**：待办树存在时前端按 parts 回放、`content` 整段被忽略，标记得追加到块序列尾部，否则「这轮出过事」整个看不见。
  - **空正文也记一条**：不然时间线上又是「用户消息后面空着」，看不出这轮出过事 —— 那正是原来最难查的形态。
  - **「未产出内容」的判据是「用户看到过东西没有」**，正文或待办树都算：只有待办、没有正文时标记得说部分输出，说「未产出内容」会和上面那个待办框自相矛盾。
  - **异常文本原样给前端，与实时一视同仁**：`redact(str(exc), terms)` 之后再落库与下发，异常原文只进日志（`logger.exception`）—— 异常文本常把工具返回值整段带上，脱敏这道不能省。`type(exc).__name__` 是类名，安全。
  - **取消路径不记**：`asyncio.CancelledError` 是 `BaseException`，`except Exception` 天然不碰它。这是想要的 —— 删会话时那条记录会变成孤儿；代价是进程重启时在跑的那轮仍然丢部分输出，与现状一致。
- **验证**：`ruff check` 改动的两个文件干净（`service.py`、新测试文件；全仓另有 16 条**既有**告警，全在本次未改动的文件里）。新增 `tests/test_interrupted_record.py` 5 条：有正文、无正文、异常里夹合成令牌（落库必须是脱敏版）、有 parts 补块、正常收尾不被误标 `partial`。`tests/v3_events.py` 加一个 `ExplodingAgent` 假件（`after=N` 吐 N 帧后炸；`after=None` 在取流那步就炸）。全量 `pytest` **569 条：563 passed / 6 failed** —— 那 6 条是 `src/ai_coding_harness/stream.py` 今天 22:12 的在途改动（里面还留着 `print("sub text:", ...)` 调试行）造成的既有失败，与本次改动无关。
- **遗留**：
  - **改动尚未生效**（需重启后端）。
  - **`sync` 直接执行路径是同一个病，本次没修**：`_execute_direct` 抛错时同样只有 user 记录、没有 assistant 记录（保护它的 `_record` 在 `chat()` 里，异常时被跳过）。同一把刀能修，但这是你没点名的部分，等你点头再动。

#### 按智能体划边界：每个角色能用的工具与能写的目录（外加一层全局底线）

- **动机**：原来只有一条**与角色无关**的危险命令黑名单，拦的是「谁都别干」的那几件事。可代码工作区里真正的越界不是「危险」，而是**错位**：出方案的智能体去改源码、审查的智能体去动工程目录、问答的智能体往仓库里落盘。这类越界黑名单一个都拦不住 —— 命令本身完全正当。定下的口径是「拦截范围根据智能体而定，不只是拦截危险命令，而是拦截每个智能体不能执行的工具，以及目录范围」。
- **改动**：
  - `src/ai_coding_harness/safety/permissions.py`：`DANGEROUS_COMMANDS` 由 7 条扩到 18 条，按四组归类 —— **毁机器**（原 7 条）、**凭据**（`.secrets` / `gitee-askpass` / `.git-credentials`，提到就拒）、**提权**（`sudo`）、**破坏性 git**（`push --force` / `+refspec` / `reset --hard` / `clean -f` / `checkout|restore .` / `branch -D`）。docstring 写清与 `policy.py` 的分工。`CommandSafety` 本身一行未动。
  - 新增 `src/ai_coding_harness/safety/policy.py`（纯函数，无副作用）：`AgentPolicy`（`write_roots` + `hidden_tools`，`frozen=True`）、策略表 `_ROOTS`、`policy_for(name, settings)`、目录判定 `_under_any`、命令落点 `command_write_targets`、以及 `write_targets(tool_name, args)`。装配名一张表管到底：主控用**形态名**（`orchestrate` / `plan` / `qa` / `analysis` / `review`，见 `profiles.py`），子智能体用**角色名**（`planner` / `coder` / `reviewer`）。
  - 新增 `src/ai_coding_harness/agent/middleware/boundary.py`：`BoundaryMiddleware` 一个类两件事 —— `awrap_model_call` 把该角色不该用的工具从 `request.tools` 里摘掉（**看不见** 就不会选），`awrap_tool_call` 在真要执行时再判一次（历史里带回来的调用、模型凭记忆硬写的调用名、以及 `execute` 里那条命令**往哪儿写**，这三种都只有执行前拦得住）。拦下返回 `ToolMessage(status="error")` 而不是抛异常：模型看得到理由、能换做法，图也不因为一个被拒的调用塌掉。
  - `agent/middleware/__init__.py`：加入重新导出与 `__all__`。`agent/graph.py`：`build_agent` 新增必填关键字参数 `policy`（与可选 `safety`），`BoundaryMiddleware` 排在我们这批中间件的**最外侧**。`agent/subagents.py`：`build_subagents` 新增必填 `policies`，三个角色各挂自己的那一份（主智能体的自定义中间件不下传）。`session/manager.py`：装配点按 `profile.form` / 角色名取策略传下去。
  - `agent/prompts.py`：planner 的禁写从「劝阻」改成事实 —— 方案经 `storePlan` 落库，不再另存一份文件；`_ROLE_PLAN_MAIN` 补「## 可写范围」段（只有 `/ai_coding/output/`）。
  - `docs/workspace-directories.md`：从「约定」改写成**执行口径**，补按智能体的读写矩阵、关键规则、已知缺口（相对路径判不出落点时放过、管道与间接执行、后端第二道只覆盖底线）与 skill 对应。
  - `tests/test_boundary.py`：新增 73 条（策略表逐名断言、未知名字 fail-closed、目录前缀/`..` 归一、凭据与平台资产的读写双向、命令落点的 13 个形状、中间件两层、全局底线接在 `execute` 上、策略对象不可变）。既有四个测试文件的 `build_agent` / `build_subagents` 调用点补上新的必填参数。
- **关键决策**：
  - **不用上游机制**（都查过源码，不是疏漏）：`FilesystemMiddleware(_permissions=…)` 与 `SubAgent["permissions"]` 在后端 `supports_execution` 时直接 `NotImplementedError`（我们的 `OpenSandboxBackend` 继承 `BaseSandbox`，正是这种），而且它只覆盖 fs 工具的读写、管不到 `execute`；`HarnessProfile.excluded_tools` 的**键是模型**，主控与 planner 共用一个模型，表达不了「按角色」。
  - **职责分工，一处规则只写一遍**：`permissions.py` 管与会话相关的底线，`policy.py` 管按智能体的范围。按角色的范围**只在中间件上落地** —— 后端是会话级共享的（一个会话一个后端、换形态不重建），挂不上按角色的判断；底线与会话绑定也成立，所以 `aexecute` 上另有一道，那是纵深不是替代。
  - **未知装配名 fail-closed**：没登记的名字按「不写文件」处理，而不是放开。新加角色忘了登记时，代价是它写不了文件、在工具返回里看到理由 —— 比默默拿到全目录写权限小得多。同理 `build_subagents` 取不到 `policies[role]` 要 `KeyError`，不做默认。
  - **豁免 `/tmp` 与 `/dev/*`**：随沙箱销毁、没有读方，逐角色计较没有意义；`/ai_coding/tmp` 是另一回事（有角色归属，见表）。
  - **只拦确定的凭据落点，不做通用读范围**：读方向只列 `/ai_coding/.secrets` 与 `/usr/local/sbin/gitee-askpass`。收窄读取会误伤 `cat /etc/os-release`、`git --version`、`pip list` 这类正当命令，收益却近乎为零。走 `execute` 的那条路由 `permissions.py` 的凭据规则兜 —— 顺带补上计划里没点名的缺口：`read_file` / `ls` / `glob` / `grep` 走的是文件接口、根本不经过命令检查。
  - **明说「反滑不反敌」**：拦的是模型手滑与越界意图，不是铁了心绕的对手。相对路径判不出落点时放过（沙箱当前工作目录不在我们手里，硬判会误伤大量正当命令）、`~` 目标放过、管道与间接执行看不出。真正不可挽回的那批由 `permissions.py` 的底线兜。
- **实测修掉的两个判定 bug**（都是探针跑出来的，不是想出来的）：
  - `sed -i 's|/a|/b|' file` 判出的写目标是**空**：按 `|` 硬切段把这条命令切成了三段、写目标丢了。改成**引号感知**的切段（`_segments`），另加 `_unquoted` 只给重定向扫描用（`echo "x > /a"` 里的 `>` 不是重定向）。
  - `cmd 2> err.log` 与 `cmd &> all.log` 的重定向**漏判**：原正则把数字与 `&` 排除在「前缀字符」之外，而 `2>&1` 不会误判的真正原因是它的目标是 `&1`、本来就不匹配。收紧成只排除 `>` 之后，`2>` / `&>` 能判了，`2>&1` / `>&2` 仍然不判。
- **验证**：`ruff check` 全仓只剩 18 条**既有**告警，全在本次未改动的文件里（`agent/profiles.py` 两条未用导入、`db/`、`sandbox/`、`tools/plan.py` 与两个测试文件）；全量 `pytest` **564 passed**（原 491 + 新增 73）。
  - **纯函数层**：13 个命令形状逐条核对，另拿 26 个真实命令样本跑探针；`sed -i 's|/a|/b|'`、`2>` / `&>`、`2>&1`、引号里的重定向与管道、heredoc 正文这几条都是探针逼出来的。
  - **装配层**：五个形态各自建图 —— 编码形态的三个子智能体中间件里都有 `BoundaryMiddleware`，`policies` 少给一个角色当场 `KeyError`。主控那份不生成独立图节点（只有 `wrap_*`，没有 `before_agent`），所以主控这一侧改**实跑**验证：`qa` 写 `/ai_coding/output/a.md` 成功落盘、写 `/ai_coding/project/a.py` 被拒（理由「qa 只能写入 /ai_coding/output，…不在其中」）；`coding` 写 `/ai_coding/skills/x/SKILL.md` 被拒、读 `/ai_coding/.secrets/gitee_token` 被拒、`git push --force` 被底线拒；`mkdir -p /ai_coding/tmp/x` 过了边界（只在 `backend=None` 的探针里因不支持执行而失败，与边界无关）。中间件链的实际顺序（由外到内）也借这次探针印了出来：deepagents 默认栈在**外**，我们这批在**内**，`BoundaryMiddleware` 排在我们这批的最外侧。
- **遗留**：
  - **改动尚未生效**（需重启后端）。
  - **分析形态的 `tmp` 是审阅时补上的**：`repo-bootstrap-analysis` 技能里写「中间文件放 `/ai_coding/tmp`」，而分析形态起初只给了 `output` —— 照 skill 写会被拒（理由会告诉它改道 `output`，不算坏，但两边口径不一致）。已确认并在 `_ROOTS["analysis"]` 里加上 `"tmp"`，代码、文档、测试三处同步。判据是「有没有读方」：`/ai_coding/tmp` 没有读方、随沙箱销毁，放开的代价近乎为零。
  - **按角色的范围只有中间件这一道**：`OpenSandboxBackend` 是会话级共享的，挂不上按角色的判断（见「关键决策」）。唯一不经中间件的沙箱调用是 `service.py::_direct_sync`（sync 意图），但它跑的是**我们写死的** `test -d` / `git pull` / `git clone` 三条、模型选不了命令，所以这里不是可被利用的绕过，只是"以后别往那条路上加模型可控的参数"这条约束没人强制。
  - **`agent/profiles.py` 有两条未用导入**（`BaseChatModel` / `BaseChatPromptTemplate`，第 22-23 行），是早先几次改动留下的，本次没动它们。

#### 工具调用配对自洽：三个钩子堵住「孤儿调用」造成的永久 400

- **动机**：会话 `78780d7a` 的**每一次**请求都 400 —— `An assistant message with 'tool_calls' must be followed by tool messages ...`，缺 id 清单**是空的**、`param: 'messages.[200].role'`，重试也不自愈。按 `checkpoint_blobs` 快照 + `checkpoint_writes` 增量重放出真历史（**383 条**，不是读快照得到的旧值）后定位：`[370]` 一条 `task` 调用带的 id 是**空串**、它后面**没有任何 ToolMessage**（下一条就是人类消息），是一个落在历史**中间**的孤儿。三个方向同时漏：
  1. **模型出口**吐出空 id 的调用（qwen 时代 v3 装配器浅覆盖的产物，见本日「多智能体主控从 qwen 换到 glm」那条，整批五条都是空串、**一个 None 都没有**）；
  2. **工具异常**：`ToolNode` 默认只把参数校验失败转成 tool 响应，其它异常（沙箱掉线等）一律上抛，整个 superstep 连同同批结果一起丢；
  3. **上游 `PatchToolCallsMiddleware` 的判据被空串骗过**：它算的是「**全历史**所有 tool 响应的 id 集合」，而历史里已经有过空 id 的响应 —— 于是这条空 id 调用被判成**已答**，每轮都跑、每轮都判「无事可做」，一个字节不写。实测把它的 gate 在真历史上复算：**能触发的 0 条**，与「17:06 那几个 checkpoint 只有新人类消息一次写入」吻合。
  - 顺带纠正两处早先的判断：读快照里的「337 条」是旧值（真值 383）；「补丁与模型调用同 superstep、写入被丢弃」是错的，`before_agent` 的写入会提交，它不生效纯粹是判据错。
- **改动**：
  - 新增 `src/ai_coding_harness/agent/middleware/tool_integrity.py`（模块 docstring 写全三个漏向与各自的取舍）：
    - `ToolCallIdMiddleware`（`awrap_model_call`）：模型出口把空/`None` 的 id 换成 `call_` + 24 位 hex（与端点同形的 29 字符）。目的是**同一轮内**就配得上 —— `ToolNode` 按 state 里那条 AI 消息的 id 写回响应；
    - `ToolErrorGuardMiddleware`（`awrap_tool_call`）：工具抛异常时补一条 `ToolMessage(status="error")` 而不是上抛；`GraphBubbleUp`（中断）照原样上抛，吞了就没法打断。排在 `RedactionMiddleware` **内侧**，合成的异常文本照样过脱敏；
    - `HistoryRepairMiddleware`（`abefore_agent`）：开工前把历史修成自洽的，`RemoveMessage(REMOVE_ALL_MESSAGES)` + 全量列表整表重写（孤儿在中间，而图输入只能追加）；`repair_messages` 返回 `None` 时**一个字节都不写**；
    - 纯函数 `repair_messages`：先给空 id 补真 id（**两侧同时换** —— 只换一边等于制造新的不匹配），再让空 id 的响应按顺序认领**空 id 的调用**，再判「答没答」，最后给仍没人答的补一条说明性响应（参数坏掉的与「跑到一半被打断」措辞分开）。
  - `agent/middleware/__init__.py`：三个类加入重新导出与 `__all__`。
  - `agent/graph.py`：主智能体挂三件；`register_harness_profile("openai", ...)` 加 `excluded_middleware=frozenset({PatchToolCallsMiddleware})` **排除上游那份**（同一件事的错判据，留着只会白写一遍且写的是错的）。
  - `agent/subagents.py`：planner / coder / reviewer 三个角色各挂一份（主智能体的自定义中间件**不下传**，而它们自己也跑工具）。
  - `tests/test_tool_integrity.py`：新增 29 条（空 id 配对、`None`、上游判据漏掉的那条回归、invalid 调用措辞与归档、邻近/非邻近「已答」、幂等、三个钩子的分支、装配点与子智能体逐角色）。
- **关键决策**：
  - **不照抄「id 在集合里就跳过」这个判据**，否则复刻同一个坑。「已答」只看**这条 AI 消息之后**的响应；紧跟 AI 的那一串 ToolMessage 只是**补配对时的落点**（provider 要求它紧随），不是「答没答」的依据。
  - **空 id 与真 id 不混认**：空 id 的响应只认领空 id 的调用，不能让它去认领真 id 的调用（那会把配对错位）。
  - **只在需要时整表重写**：每轮都重写会让每个 superstep 多出一次全量 checkpoint 写入；修好的历史下一轮就是干净的，所以只对「已经坏掉的那一轮」付费。
  - 三个钩子都**只实现异步版**（服务端统一走 `astream_events`，与 `redaction` 同因）；上游排除走**类形式**（`excluded_middleware` 的类形式按精确类型匹配，deepagents 的覆盖校验会在条目没匹配到任何栈时直接抛错）。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **491 passed**（原 462 + 新增 29）。拿**真实 checkpoint** 复算：真历史 383 条、修前**仅 1 条**位置性孤儿（`[370] task`，空 id，紧随响应 0 条）；`repair_messages` 输出 384 条（补 1 条、丢 0 条），修后孤儿 **0** 条，再跑一遍返回 `None`（幂等）。装配探针：主图节点有 `HistoryRepairMiddleware.before_agent`、**没有** `PatchToolCallsMiddleware.before_agent`，三个子智能体各自带全三件。
- **遗留**：
  - **改动尚未生效**（需重启后端）。重启后该会话**下一轮对话即自愈** —— 修复节点跑在模型调用之前。
  - **`ToolCallIdMiddleware` 能否拦住 v3 装配器那条路未经验证**：空 id 有可能在装配器里、也就是在模型调用的装配**之后**才出现。拦不住也不影响这条会话（真历史必然经过整表修复），但「模型出口就补好」这层可能落空。
  - **上游装配器的浅覆盖没修**：谁再把某个角色指回 qwen、或换成同样「续片带空 id」的供应商，都会再造出一批空 id（可由整表修复兜住，但那是善后不是预防）。

#### 温度改成逐家可配（kimi 取 1，其余仍 0.3）

- **动机**：`build_model` 原先无条件写 `temperature=0.3`，而 kimi-k2.6 只收 `temperature=1` —— **review / reviewer 两个角色每调一次模型就 400**（`invalid temperature: only 1 is allowed for this model`），不是偶发而是必坏。`logs/` 里搜不到这条报错，说明 review 形态至今没被真实跑过。上一条换主控模型时实测发现。
- **改动**：
  - `config.py`：`ModelConfig` 新增 `temperature: float = 0.3`（注释写明为什么是逐家的：kimi 只收 1，其余几家 0.3 是调优结果）；`kimi_model` 属性显式传 `temperature=1`；`qwen_model` 的 docstring 改成「当前没有角色取用」（上一条换主控的连带，配置与 `.env` 键仍保留）。
  - `agent/llm.py`：`build_model` 由写死的 `0.3` 改读 `cfg.temperature`。
  - `tests/test_llm_config.py`：新增 `test_kimi_only_accepts_temperature_one`（逐 review / reviewer 钉住 `1`），原有 `test_temperature_is_low`（coder 的 0.3）不动。
- **关键决策**：**不新增 `KIMI_TEMPERATURE` 之类的环境键**。温度是「这家模型收什么」的接口事实，不是运维旋钮；写进代码、由属性给出，改起来只碰一处。分类模型走的是另一个入口（`build_classifier_model` 自己传 `temperature=0`），不受本次改动影响。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **462 passed**（原 460，新增 2 条）；拿**真实 `.env`** 跑 v3 探针逐角色实测：`review` / `reviewer` 由 400 变为正常出 id（kimi-k2.6，22 字符），`orchestrate` / `plan` / `planner`（glm-5.3）29、`coder`（deepseek-flash）32 均不变。
- **遗留**：**改动尚未生效**（需重启后端）。另：该探针复跑时 `qa` 的 gpt 端点两次 `APIConnectionError`（`api.ztoken.pro` 抖动，前一次同角色 id 正常 29 字符），与本次改动无关。

#### 多智能体主控从 qwen 换到 glm（qwen 在 v3 事件流下丢 tool_call id）

- **动机**：页面报错 `[错误] Tool call ID is required for subagent invocation`。根因**不在我们这层**，而在 `astream_events(version="v3")` 的装配路径上：
  1. 后端用 `agent.astream_events(..., version="v3")`（`server/service.py`）消费事件流；
  2. v3 的装配器 `langchain_core/language_models/chat_model_stream.py::ChatModelStream._assemble_message` 从「按 index 累加的分片表」里取工具 id；
  3. DashScope（qwen）只在每个 index 的**第一个**分片带真 id，后续分片带 `id: ""`；而 `_merge_block_delta_into_store` 是**浅覆盖**（非 `None` 就写入），空串把真 id 冲掉了；
  4. 于是那一轮 AIMessage 的 `tool_calls[*].id` 全为 `""`，对应 ToolMessage 的 `tool_call_id` 也是 `""`（从 checkpoint 里解出来的原始值，不是渲染问题）；
  5. 同一轮里 `get_pr_info` / `execute` / `write_todos` 都照常跑通 —— **只有 deepagents 的 `atask`（`deepagents/middleware/subagents.py`）硬校验 `runtime.tool_call_id`**，所以坏的只有「委派子智能体」这一条线。
- **改动**：`agent/llm.py` 的 `_ROLE_MODEL_ATTR["orchestrate"]` 由 `qwen_model` 改 `glm_model`（多智能体主控与 plan / planner 同款 glm-5.3）；`agent/subagents.py` 与 `tests/test_profiles.py` 里「主智能体在 coding 形态下是 qwen」的陈旧注释同步改；`tests/test_llm_config.py` 的 `_EXPECTED["orchestrate"]` 与窗口参数化行（`qwen_context_window` → `glm_context_window`）跟着改。`qwen_model` 配置属性与 `.env` 的 `QWEN_MODEL_NAME` **保留不动**——成了没人取用的休眠配置，日后要换回来只改路由表一行。
- **关键决策 / 实测**：v3 下逐角色实测（同一张图、只换模型）——`orchestrate`/qwen3.8-flash **id 长度 0**（丢）、`plan`+`planner`/glm-5.3 29、`qa`/gpt-5.5 29、`coder`/deepseek-flash 32；换成 glm 后 `orchestrate` 复测 **29**。选 glm 而非 deepseek 的理由：glm 已在真机 plan 形态跑过、v3 下 id 保住、且接受 `temperature=0.3`（kimi 不收，见遗留）。旁证：失败那条 AIMessage 带 `output_version="v1"` 元数据，正是 v3 装配器盖的指纹；同一模型走 v2 事件流 id 完好。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **460 passed**；拿**真实 `.env`** 逐角色建模型，8 个角色路由符合预期（`orchestrate`/`plan`/`planner`→glm-5.3@open.bigmodel.cn，其余不变）；v3 探针复测 orchestrate 的 id 已回长。
- **遗留**：
  - **这是回避不是根治**：上游装配器的合并覆盖没修，谁再把某个角色指回 qwen、或换成同样「续片带空 id」的供应商，都会重犯。
  - **`review` / `reviewer` 当时是必坏的**：`build_model` 无条件写 `temperature=0.3`，而 kimi-k2.6 只收 `temperature=1`（实测 400 `invalid temperature: only 1 is allowed for this model`；换成 1 后正常出 id）。`logs/` 里搜不到这条报错，说明 review 形态至今没被真实跑过。**已在上一条修掉**（`ModelConfig` 加 `temperature` 字段）。
  - **改动尚未生效**（需重启后端）；重启后跑真机验证会落到 coding 形态，可能真去 commit / 开 PR，需单独授权。

#### 意图分类**临时**降级到 deepseek（本机分类器不启动时）

- **动机**：本机 vLLM 分类器（`localhost:6006`）这阵子不启动；没起时每一轮都白等 5s 超时再退回关键字口径（见 09-17 那条的遗留）。临时把分类这一跳交给已配好的 deepseek，`INTENT_*` 原样留着当回切路径。
- **改动**：`config.py` 的 `classifier_model` 从「`INTENT_*` 优先、缺失才回落主模型」改成**直接取 deepseek 三项**（docstring 注明是临时的、以及回切怎么改）；`.env` 一字未动，三个 `INTENT_*` 键仍在、此刻被忽略。`llm.build_classifier_model` 的参数（`temperature=0`、`max_tokens=512`、关思考的 `extra_body`）保持原样。
- **关键决策 / 实测**：
  - **关思考那行 `extra_body` 不能删**：裸调对比，带它 `finish=stop, content='OK'`（0.7~0.9s）；不带则 `finish=length, content=''` —— `deepseek-flash` 同样是思考型模型，`max_tokens` 全被思考吃掉、正文一个字没出。顺带修正 09-05~09-17 那条的措辞：这个键不只 vLLM 认，deepseek 也认（不报 400、确实生效）。
  - 判定质量与耗时：三条样板消息（qa / coding / plan 各一）全 `source=llm`，0.89~1.29s（本机 vLLM 是 0.3s，超时线仍是 5.0s）。强关键字依旧在 LLM 之前短接（`match_strong`），口令类消息一次云调用都不发。
  - 代价：分类与 coder 角色共用同一家 key 与模型 —— 「先降级」可接受的耦合，不为此另立配置项。
- **验证**：重启后端后一轮真实对话（会话 `78780d7a`，qa 形态）：日志 `意图分析：intent=qa source=llm 理由=询问现有登录逻辑，属查看/答疑类` —— 模型判定真生效（老口径此刻会是 `source=fallback` 加 5s 超时）。该轮 11 次 LLM 调用 / 23 次工具 / 3215 字，checkpoint 里 11 条模型名全是 `gpt-5.5`，qa 角色的路由未受影响。`tests/test_intent_detection.py` 36 条通过、ruff 干净。
- **遗留**：`intent_timeout_seconds` 仍 5.0s（云上留 4 倍余量）；「干脆关掉 LLM 判定走纯关键字」的开关 `INTENT_LLM_ENABLED` 未动。

#### 模型按角色路由：一家一个岗位

- **动机**：原先只有 DeepSeek（主）与 Kimi（备）两家，且全部智能体共用一个模型实例 —— 出方案、写码、审查、问答跑在同一家上。用户补了 qwen / gpt / glm 三家配置并指定用途：qwen 做多智能体主控、glm 做 planner、kimi 做 review（主与子）、gpt 做 qa / analysis、coder 仍是 deepseek。
- **改动**：
  - `agent/llm.py`：新增 `_ROLE_MODEL_ATTR` 路由表（角色 → `Settings` 上哪个 `*_model` 属性）+ `_config_for`；`build_model(settings, *, role)` 取代原 `backup: bool`。
  - `config.py`：`ModelConfig` **删掉 `extra_body` 字段**（`build_model` 本来也没转发过它 —— 给 glm 配的 `thinking` / `reasoning_effort` 一直是死配置）；`primary_model` → `deepseek_model`、`backup_model` → `kimi_model`（Kimi 已不是「备用」而是 review 岗，改名后与新增的 `glm_model` / `gpt_model` 同口径）；补 `qwen_model`；新增 `glm_context_window` / `gpt_context_window` / `qwen_context_window` 并接进各自属性。
  - `agent/subagents.py`：`build_subagents(*, tools_by_role, model_for)` 增加 `model_for`，每个角色带**自己的** `model`（原先不指定即继承主智能体）。
  - `session/manager.py`：`SessionManager` 的 `model: BaseChatModel` 换成 `model_for: Callable[[str], BaseChatModel]`，主智能体按 `profile.form` 取模型。
  - `api/app.py`：lifespan 不再预建一个共用模型，改传 `lambda role: build_model(settings, role=role)`。
  - `.env` / `.env.example`：补三个窗口键；顺手修掉 `.env.example` 里 `DEEPSEEK_MODEL` / `KIMI_MODEL` 两个**旧键名**（真键是 `*_MODEL_NAME`，照抄示例会静默拿不到模型名）。
  - `tools/web_search.py`：两处错误提示里的 `ZHIPU_API_KEY` / `ZHIPU_BASE_URL` 改 `GLM_*`。
- **关键决策**：
  - **路由挂在「形态」那根轴上，不另立词汇**：主智能体的角色名就是 `profile.form`，子智能体的角色名就是 `_ROLES` 的键，两者共用一个 `_ROLE_MODEL_ATTR`；缓存键本就是形态名，于是换形态必然连模型一起换，不会出现「用着 qa 的工具、跑着 plan 的模型」。
  - **未登记的角色不回落，直接报错**：静默落回某个模型会让某一轮悄悄跑在错的模型上，比当场失败难查得多；守卫测试逐个形态、逐个角色钉住登记齐全。
  - **窗口取保守值**（glm / gpt 128000、qwen 1048576）：三家接口都不暴露上下文窗口 —— `/models` 只回模型名，能问到的 131072（glm / qwen）与 128000（gpt）是**输出**上限不是窗口，文档也取不到（`docs.bigmodel.cn`、`alibabacloud.com` 均被网络策略挡）。偏小只是早压一点，偏大是真的把请求撑溢出。
  - **子智能体给模型实例而不是字符串**：`SubAgent.model` 声明是 `str | BaseChatModel`，但传字符串会走 deepagents 自己的 `resolve_model` 重建，丢掉 api_key / base_url。
  - **qwen / glm 不留思考开关**：qwen 在云端默认开启，glm 是服务端默认，`build_model` 本来就不转发任何额外参数。
- **验证**：`ruff check` 改动文件全过；全量 `pytest` **460 passed**（原 426）；拿**真实 `.env`** 逐角色建模型，8 个角色全部落到预期家（`orchestrate`→qwen3.8-flash@dashscope、`plan`/`planner`→glm-5.3@open.bigmodel.cn、`qa`/`analysis`→gpt-5.5@api.ztoken.pro、`review`/`reviewer`→kimi-k2.6@api.moonshot.cn、`coder`→deepseek-flash@api.deepseek.com），窗口逐家生效；gpt 端点 `api.ztoken.pro` 实测连通（早先那次连接失败是探针自身拼错了路径，不是端点不通）。
- **遗留**：**改动尚未生效**（需重启后端）；三个窗口值是保守估值，日后拿到真实窗口应当校准。

#### policies 补齐四种语言的风格指南（Python / TS / JS / Go 三份）

- **动机**：`/ai_coding/policies/` 此前只有一份 `java-style.md`，而平台声明的支持范围是 Java / Python / JavaScript·TypeScript / Go —— 写码与审查的共同依据缺了另外几门。
- **改动**：`sandbox/policies/` 新增六份译制文件（均纯 LF、无 CR）：

| 文件 | 行数 | 字节 | 代码块 | 标题 | 锚点行 | 站内引用 |
| --- | --- | --- | --- | --- | --- | --- |
| `python-style.md` | 3085 | 105130 | 140 | 152 | 490 | 39 |
| `ts-style.md` | 3028 | 99645 | 207 | 167 | 15 | 23 |
| `js-style.md` | 3054 | 113756 | 153 | 214 | 31 | 40 |
| `go-style.md` | 429 | 24933 | 6 | 21 | 24 | 3 |
| `go-decisions.md` | 3088 | 117023 | 146 | 87 | 122 | 37 |
| `go-best-practices.md` | 3181 | 129032 | 140 | 74 | 75 | 33 |

- **做法**：译文一律不手抄代码 —— 中文单元里只留升序的 `@@BLOCKnn@@` 占位符，`splice.py` 从英文原文把代码块逐字节回填。Python 那份还核了 `en/` 与上游原始 markdown 的关系：3689 行正是 3778 行的 `[89:3778]` **连续切片**，切掉的头部 89 行是上游的 AUTHORS 注释块。
- **关键决策**：
  - **上游是三个页面的（Go）就出三份文件**，不硬合成一份 —— 概览、风格决策、最佳实践各自独立引用更顺。
  - **锚点行贴在标题之上**：上游 `#anchor` 引用指向的是旧页面 id，不补就是满地断链（`go-decisions.md` 一度少 62 个锚点、93 处断链）。
  - **体例随上游**：Google 自己的 JS/TS/Python 指南没有「强制/推荐」分级，就不套 java 手册那套三分级；只在开头加出处与 CC BY 3.0 署名，TS 那份保留上游「本指南已不再更新，建议迁移 TypeScript」的提示。
  - **复核另起一套判据**：`verify_anchors.py` 不复用 `assemble.py` 自己的断言，直接拿上游原文重新数代码块、标题层级、节号序列、锚点多重集与站内引用落地情况。
- **验证**：六份的独立复核全部通过 —— 代码块与英文原文逐字节一致且顺序相同；标题层级与节号逐条相符（Python 的 152 条节号与上游逐条相同）；锚点计数一致；除上游自带的悬空引用（TS 6 个、Python 1 个 `from-future-imports`）外，站内引用全部落地；纯 LF、无 CR、无占位符残留。
- **遗留**：都还没在真实会话里让智能体读过。`policies/` 是**建会话时整棵上传**的，要到**新建会话**才进沙箱，与后端重启无关。

#### 仓库记忆写入加 PR 闸门

- **动机**：仓库记忆记的是「已合并的现实」。仓库还有未关闭的 PR 时，仓库正处在待审状态，此时写记忆等于把尚未落定的东西写成既成事实 —— 下一轮再读回来，它就成了依据。
- **改动**：
  - `tools/git.py`：新增 `GiteeClient.open_pulls()`（与 `_MEMORY_GATE` 第 1 步 `get_pr_info(<repo>, state="open")` 同口径）；新增 `PrLookupError(RuntimeError)`，把接口返回的 `{"error": ...}` 与非 2xx 都归一成该异常。
  - `tools/memory.py`：`MemoryContext` 加 `open_prs` 回调；`save_repo_memory` 在 `aread` **之前**先探 PR，有未关闭 PR 就直接拒绝 —— 不读也不写。拒绝文案列最多 5 条 `#n [open] 标题 (作者, 头 → 基)`，超出用「等 N 个」收口，并写明理由。
  - `tools/register.py`：注入 `GiteeClient(...).open_pulls`；工具描述补一句闸门说明。
- **关键决策**：探测放在**读之前**（否则白读一遍）；查询失败按「拒绝写入」处理，而不是按「没有未关闭 PR」放行 —— 宁可挡下，也不在状态未确认时写入。
- **验证**：`tests/test_memory_tool.py` 加 autouse fixture stub 掉 `register.GiteeClient`，新增用例覆盖：有开放 PR 时不写也不读、拒绝文案带编号与理由、无 PR 照旧放行、探测抛错时的策略、超过 5 条截断、工具描述含闸门。提示词未动（`TestMemoryWritebackGate` 仍成立）；令牌仍只存在 `GiteeRepo` 里，不进上下文与日志。
- **遗留**：尚未在真实会话里验证（要重启后端才生效）。

#### 委派的子任务挂进待办树

- **动机**：主控派子智能体时，子智能体自己的清单此前没有落点，`_apply` 会把整份丢掉 —— 前端只看得见主清单，看不见子任务里的推进。
- **改动**：
  - `server/stream.py`：`_apply` 去掉「没落点就整份丢掉」的提前返回，子清单一律存下（嵌套 ≥2 层仍丢弃）；`note_delegation` 记下 `input.subagent_type`；`tree()` 把无落点的子清单收进容器项；新增 `_container()` / `_rollup()` / `subagent_name()`。
  - 补掉两个边界：主项被改名 → 建容器；子清单为空 → 不建空壳。
  - `prompts.py`：`_ROLE_ORCHESTRATE` 的「## 工作流程」加一句前置规划约束（步骤 a–g 不动、不重编号）。
  - 前端：由「正文块 + 待办块」改成「正文块按先后 + 一棵待办树挂在最后」，整帧整体渲染。
- **关键决策**：挂靠用**两个锚点** —— `task` 的 `tool_call_id` 认出是哪一次委派，主清单里 `in_progress` 的那一项决定父节点；脱敏要递归进 `children`；整棵树按帧整体下发，不做增量。
- **验证**：2 个旧用例改写，`test_note_delegation_ignores_other_tools_and_nested_agents` 重新钉成「不挂在主项下、进容器」；新增 7 条；**417 passed**；改动文件的 ruff 干净（`test_plan_events.py:101–175` 的 6 条 RUF100 是既有告警，未动）。前端无改动 —— 前半程那版在 5174 端口用 stub 数据在浏览器里看过：DOM 顺序 `[div.ai-part.text, ul.ai-part.todos]`，`in_progress` 的父项展开、`pending` 折叠，有子项才给折叠按钮，标签 `1/3`、`1/1`；当时 pytest 409 passed、`vue-tsc` + `vite build` 通过。
- **遗留**：真实委派的活体验证待重启后做。

#### 输出形态规则集中到 adhd/AGENT.md

- **动机**：输出形态（怎么分行、怎么收尾、指代怎么写）此前散在各个角色段里，改一处漏一处。
- **改动**：规则集中到 `adhd/AGENT.md`，由 `build_main_prompt` 读盘（`settings.agent_output_style_file`）拼进主 prompt。五种主智能体形态都经过该函数，三个子智能体的提示词各是独立常量、不经过 —— 「只约束主智能体」是结构保证的，不靠在别处记得别加。刻意不用 deepagents 的 `memory=`（那是它自己的记忆注入通道，把输出规范混进「记忆」语义里会串味）。
- **关键决策**：加一句范围限定 —— 该段只管**面向读者的正文**，PR 评论、仓库记忆、写进 `/ai_coding/output/` 的文件各按自己的格式。这样就不必再按 review / orchestrate 两种形态各写一遍（`comment_pr` 的调用点都不产出用户可见正文：review 形态与 orchestrate 回评各一处，编码流的审查评论归 reviewer 子智能体）。同时新增第 10 条「指代对方写『你』，不要写『用户』」。
- **验证**：`tests/test_profiles.py` 54 passed。

#### 发送即时上屏；409 改成「接回本轮」而不是死胡同提示

- **动机**：用户反馈发消息后自己的话迟迟不进聊天记录，隔一会儿重发却提示「本轮仍在进行中」。
- **根因**（排除法定位，不是后端慢）：中间件是纯 ASGI（`ApiLogMiddleware` 自带注释说明不用 `BaseHTTPMiddleware` 就是怕影响 SSE 流式）、`RunHub.start` 是同步非阻塞的 `create_task`；实测探针显示 **响应头 0.116s 到达**。真正的问题是前端顺序：`send()` 把「push 用户消息 + 置 `loading`」放在 `await postChat` **之后**，而 `loading` 正是输入框与发送按钮 `:disabled` 的闸门 —— 等 POST 回来的那段窗口里 UI 还是活的，用户以为没发出去于是再点一次，第二次点击真能发出去，后端那一轮已经在跑，于是 409。
- **改动**：
  - `frontend/src/App.vue` 的 `send()`：清空输入框、push 用户那条与 AI 占位、置 `loading`、`scrollToBottom()` 全部提到 POST **之前**；POST 失败时按下标摘掉刚放上去的两条、把文字还原回输入框（`loading` 期间无人能动末尾两条，按下标摘是安全的）。
  - 同文件新增 `isRunInProgress(id)`：409 有两种成因共用同一个状态码 —— 「本轮已在跑」（接回来就好）与「会话已失效 / 缺仓库信息」（接不回，只能报错）。唯一干净的判据是详情里的 `running`，与 `openSession` 判断「刷新后要不要接回」同口径，不另立一套。
  - 409 且 `running` 为真 → `attachRunning(id)` 接回直播流（用户那条后端本就没收到，内容留在输入框里，等这轮完了他自己再发）。取详情失败则当作接不回，走报错。
  - `frontend/src/api.ts`：`postChat` 原来把**所有** 409 都换成固定文案「本轮仍在进行中…」，导致会话已失效也报这句话，还会让上面那个自动接回逻辑误判。新增 `errorDetail()` 原样带出服务端的 `detail`。
- **关键决策**：用户那条的时间戳取**发送时刻**、AI 那条留空等流结束再补 —— 与后端「回合收尾才写记录」对齐，刷新回来不会看出两套时间。
- **验证**（stub 掉 `window.fetch` 驱动真实 UI，不建沙箱、不耗模型 token）：POST 延迟 3003ms，气泡在 60ms 采样点就已在 DOM 里（比 POST 返回早 2676ms），输入框与发送按钮点击后立即禁用，第二次点 `.send` 的请求数仍是 1（没发出去，自然不会 409）。409 两条分支各跑一遍：
  - `running: true` → 消息数 1→2（接回那一轮的占位），发出 1 次 `/chat/{id}/stream?after=0`，**无 alert**，输入框还原成原文。
  - `running: false` → 消息数 2→2（乐观渲染的两条摘干净、未接回），**无** `/stream` 请求，alert 里是服务端原句「会话已失效（destroyed），请新建会话。」。
  - `npm --prefix frontend run build` 通过。

#### 配色定稿：冷调三层底 + 待办块做全页唯一的暖色点

- **动机**：整页此前只有 `#fff` 与 `--bg` 两档底，正文块、待办块、代码块全糊在一起，看不出层次；待办块本身还是蓝底，和右侧计划面板抢同一个品牌色。
- **改动**（只动 `frontend/src/style.css`）：
  - 三层冷调底：`--page-bg: #eef4fe`（消息区）/ `--panel-bg: #e9f0fd`（侧栏 + 计划面板，压深一档）/ `--bubble: #eaecf1`（正文块与用户气泡，再深一档）。
  - 待办块 `--todos: #fbdae5`；块内状态圈改用新变量 `--todo-mark: #c2456b`，品牌蓝 `--accent` 不动（发送按钮、计划面板状态标签仍是 `#4d6bfe`）；圈口由冷灰 `#b9c4d6` 换暖灰 `#c9b6be` —— 冷灰压在粉底上会发青。
  - 块间距 `--part-gap: 16px`（比消息间距 20px 小一档，保住「块内成组、消息间分开」的层次）；待办块内边距 `5px 14px`。
  - 内联 `code` 底改半透明 `rgba(0, 0, 0, .055)`（写死冷灰在深底上显脏）；代码块头 / `pre` / 表头统一走 `--code-bg: rgba(255, 255, 255, .62)`。
  - 退役 `--bg` 与 `--sidebar-bg`：`--bg` 原有三个消费点 —— `body` 改 `--page-bg`，`.plan-status` 与 `.step-files code` 内联补 `#fff`。
- **关键决策**：
  - 气泡走**冷灰**不走中性灰 —— 中性灰压在蓝底上会发闷发绿。
  - 状态圈**新开变量**而不是复用 `--accent`：待办块变粉后若沿用品牌蓝，发送按钮与计划状态标签会跟着变，影响面超出这次改动。
- **修掉一个真 bug**：`.ai-part.todos { margin: 0 }`（清 `ul` 默认外边距）与 `.ai-part + .ai-part` 同优先级、源序更靠后，把块间距整个盖成 **0px** —— 前一轮「10px / 20px 没啥效果」的真正原因，等于白调了一轮。补一条更高优先级的 `.ai-part + .ai-part.todos { margin-top: var(--part-gap) }` 顶回来。
- **验证**：浏览器探针读 computed style（不靠截图肉眼判断）—— 页面 `rgb(238, 244, 254)`、侧栏 `rgb(233, 240, 253)`、正文块 `rgb(234, 236, 241)`、待办块 `rgb(251, 218, 229)` + 边框 `rgba(0, 0, 0, .07)`、状态圈 `rgb(194, 69, 107)`、内联 code `rgba(0, 0, 0, .055)`、代码块头/底 `rgba(255, 255, 255, .62)`；块间距修后待办块上/下都是 16px、首个块仍是 0px。`npm run build`（vue-tsc + vite）通过。

#### 一个仓库一个活跃会话：再建会话即复用

- **动机**：同一仓库重复「新建对话」会各建一条会话，各自拉一个沙箱、各占一份工作区卷，又都去动同一个仓库；用户真正要的是「回到原来那条上接着聊」。
- **改动**：
  - `db/dao.py`：新增 `lock_repo`（按 `owner/name` 归一化后加事务级咨询锁）与 `find_active_session_by_repo`。
  - `session/manager.py`：`create_session` 改成**先拿锁、再查** —— 命中活跃会话就直接复用（`CreatedSession(session, reused=True, workspace_status=<库里那份>)`），不再落库、不再建沙箱；查不到才走原路。凭据与 `repo_url` 都取库里那份，**不回落 `.env`**，否则旧会话会突然用上后来换过的令牌。
  - `api/routes/sessions.py`：`POST /sessions` 响应加 `reused`；复用时**不踢工作区**（它可能早就是 `ready`），并把既有会话登记进 `app.state.sessions`。
  - 前端：复用命中时顶部出提示条「该仓库已有进行中的会话，已为你打开它」。
- **关键决策**：**拿锁必须在查之前** —— 顺序写反，并发两边都会查空、各建一条会话，唯一性形同虚设。锁到事务结束才释放，跨度正是「查 + 写」这一整个事务。
- **验证**：
  - 新增 `tests/test_session_reuse.py`：新建落一行；同仓库第二个会话复用（id / 凭据 / 工作区状态都取库里那份，且不再落行）；`.git`、末尾斜杠、大小写换个写法绕不开复用；并发建同一仓库只落一行、且恰好一个 `reused=False`；路由层复用时不调 `start_provisioning`。假库里断言存的是占位符而不是真令牌 —— pytest 失败会打 locals，令牌不许进任何输出。
  - 重启后端跑真实链路：日志 3 行「会话已落库」+ 3 行「直接复用」，与预期的新建/复用路径对得上。
  - 全量 `pytest` 398 条通过，前端 `npm run build` 通过。

#### 意图：否定语境下的口令不算口令

- **动机**：实测样本「请给这个仓库出一个增加 GET /hello 接口的方案，先不要动手改代码。」被判成 `coding`。根因**不在模型** —— `match_strong` 命中了强口令 `动手改` 与 `改代码`，两个都在**否定句**里，于是 `source=keyword` 当场定案，qwen3-8b 压根没被调用过。把同一句直接喂给模型，判的是 `plan`。
- **改动**：
  - `server/intent.py` 新增 `_hit(text, word)`：逐个 `find` 出现位置，只有**前面 4 个字里不含否定词**的那一次才算命中。`match_strong` 与回落的 `classify()` 都改走它 —— 两份口径共用同一份词表，守卫必须同时管住。否定词表 8 个：不 / 无 / 别 / 勿 / 甭 / 毋 / 莫 / 免。
  - 同文件补 `_BOUNDARY` 判定要点并接进 `_system_prompt()`：plan 与 coding 的分界**看这一轮的交付物，不看句子里提到了什么功能**（「出一个…方案」是 plan，「按这个方案做」是 coding），各列常见说法。
- **关键决策**：
  - 窗口**只开 4 个字**：开大容易把「不要动 A，动手改 B」这类真指令一起吞掉。被吞掉也不可怕 —— 退回 LLM 判定本来就是自然措辞该走的那条路（词表只管口令）。
  - 提示词的 `_BOUNDARY` 与否定守卫是**两件事**：前者治「模型看见功能名词就判 coding」，后者治「词表不认否定」。只改前者治不了这个样本 —— 命中发生在关键字档，模型没被问过。
  - `weak_hints` **不动**（仍是 `word in text`）：它只当线索与兜底依据，不在这次的口径里。
- **验证**：
  - `tests/test_intent_detection.py` 新增 8 条：否定短语不命中；「不要动 A，动手改 B」仍判 coding；普通口令不受影响；`classify` 回落口径一致；被否定的口令**会交给模型**（`model.calls == 1`）。提示词侧另 3 条钉住分界规则与两组变体。
  - 活体直调本机 qwen3-8b：同一句 `来源=llm`、`实得=plan`。
  - 真实链路一轮（会话 `6cae02b2`，java_test_demo）：否定守卫**确实拦住了直通** —— 若没拦，这一轮会是 `source=keyword`、`intent=coding`；本轮本机分类模型未启动，5s 超时后回落成 `qa`。
- **遗留**：本机分类模型（`localhost:6006`）没起时，一切自然措辞都退到关键字口径，判定质量取决于那个进程 —— 要不要给它加熔断/降级提示另议。

#### 待办项带状态 + 块号：进度块看得出推进

- **动机**：`write_todos` 的每一项此前只有文字，看不出哪条在做、哪条做完了；一个回合里模型反复调它汇报进度，早先的做法还会把上一块顶掉。
- **改动**：
  - `stream.py`：`todo_items(args)` 用正则从**流式累加的 JSON 片段**里抠 `content` 与 `status` —— 这份 args 随时可能半截，不能 `json.loads`；只认已闭合的 `content`（半截的先不显示，免得最后一项逐字抖动）。status 排在 content 之后，还没流到时按 `pending` 占位，收尾的 `content-block-finish` 帧（args 已是 dict）订正成权威值。状态只认 `pending / in_progress / completed`。
  - 新增 `StreamParts`：正文块与待办块按出现先后排；待办块**每次调用新开一块**（`open_todos` 给出块下标），块内全量替换；`has_todos` 决定纯正文的回合不写 `parts`。
  - `V3Translator` 给每次 `write_todos` 编一个**整轮递增的块号**，下发 `{"type":"todos","id":N,"items":[…]}` 全量帧（与 plan 事件同一个惯用法）。
  - 前端 `App.vue` + `style.css`：按**块号定位**、整块替换（找不到才开新块）；状态位纯 CSS 画 —— 空圈 pending / 半亮圈 in_progress / 实心勾 completed。历史记录里只有字符串的老待办归一化成 `pending`。
- **关键决策**：待办块**追加不原地改写** —— 追加才看得出推进过程。`message-start` 清空「块下标」表但**不清块号**：下标按消息编号，块号是整轮编号。
- **验证**：
  - 真实一轮（会话 `6cae02b2`，1155 帧、id 1..1155 连续）：`todos` 229 帧落在**两块**上 —— 块 1 首帧 0 项（空帧即分隔符）、末帧 4 项且状态为 `in_progress + 3×pending`；块 2 末帧 4 项全 `completed`。SSE 帧与落库 `parts` 形状一致。
  - `tests/test_stream_v3.py`（24 条）覆盖半截 args、状态缺省与订正、块号递增、块追加。

#### 一轮对话脱离 HTTP 请求的生命周期（RunHub）

- **动机**：`EventSourceResponse` 收到 `http.disconnect` 会取消整个 task group，而 `service.chat()` 原来正挂在那个 group 上 —— **浏览器一刷新，这一轮就被杀掉**，收尾的落库走不到，成果与 token 一起丢。
- **改动**：
  - 新增 `server/runs.py` 的 `RunHub`：一轮跑在自己的 `asyncio.Task` 里，产出的帧按序号攒在缓冲里；HTTP 侧只是**订阅**这份缓冲，请求断了只少一个订阅者。同一会话并发两轮抛 `RunInProgress`（同一个 thread_id、同一个工作区，会互相踩）→ 路由回 409。
  - `api/routes/chat.py`：`POST /chat/{id}` 只负责启动 + 订阅；新增 `GET /chat/{id}/stream?after=N` 供刷新 / 断线后带游标接回（本轮已结束也能用，拿到的是完整重放）。SSE 的 `id:` 改由服务端编号（帧号即游标）—— 某帧解析失败被跳过时本地计数会错位。
  - `SessionDetail` 加 `running`；`destroy_session` 顺手 `runs.cancel()`；进程退出前 `shutdown()` 收掉在跑的轮次，别在后端拆池子时还去写库。
- **关键决策**：订阅者**不各持一个队列**（那要复制全部帧），共用一份缓冲 + 一个 `asyncio.Event` 当唤醒信号；清事件与查缓冲之间没有 `await`，生产者插不进来，故不会漏唤醒。
- **出界**：缓冲在**进程内存**里，多 worker 或重启即失效（那时退化成读库，已落库的还在）。上多 worker 时这份缓冲要挪到 Redis。
- **验证**：同一轮（1155 帧）实测三种游标 —— `after=0` 从 id 1 起 1155 帧（与原始流一致）；`after=80` 从 **id 81** 起 1075 帧；`after=1154` 只 1 帧（id 1155）。`tests/test_run_hub.py` 10 条覆盖启动 / 并发拒绝 / 断订不影响运行 / 取消 / 收尾。

#### 方案步骤目标收短成「一行标签」

- **动机**：`goal` 直接显示在右侧进度面板上，可它此前被当成了给 coder 的说明书。实测一份 8 步的方案 goal 135–332 字、另一份 7 步的 168–274 字，塞着类名、注解、依赖坐标与 git 命令，面板一行读不完。细节的归宿本是方案全文（`content`）与 `done_criteria` —— coder 用 `get_unexecuted_plan` 取方案时两者都拿得到，不靠 goal。
- **改动**：`prompts.py` 的 planner 段与 `storePlan` 的工具描述同时钉住「**一行短句、约 15 字以内、只写「做什么」**」，并说明 goal 的读者是用户面板、实现细节写进 content 与 `done_criteria`；`_format_plan` 回显步骤时带上 `[status]`。长度只在提示词与工具描述里约束，`_normalize_steps` 不做硬拒。
- **关键决策**：提示词与工具描述**两处口径必须一致** —— 工具描述离模型更近，只改一处模型就只听得到一半。为此专门加了断言把两边钉在一起。
- **验证**：`tests/test_prompts.py` 的 `TestPlanGoalIsALabel` 4 条钉住措辞与两处一致。活体（会话 `e3c20479`，plan 26）：5 步，goal 字数 **19 / 12 / 8 / 14 / 5** —— 「新增 ByeController 端点」「补充 /bye 集成测试」「全量构建验证通过」「同步 README 接口文档」「提交 PR」。

#### 消息加时间：今天只给时刻，跨年才补年份

- **动机**：消息流长了分不清先后、也看不出某轮是刚发的还是昨天的。
- **改动**：
  - 前端 `App.vue`：`Message` 加 `at: string`（ISO 串）。`formatTime()` 按**本地日历日**判「今天」——今天给 `HH:mm`，今年给 `MM-dd HH:mm`，跨年才 `yyyy-MM-dd HH:mm`；一律 24 小时制。历史记录取 `created_at`，实时发的那条取本地钟。
  - 前端 `App.vue`（发送路径）：用户那条在 **push 时**就盖上发送时刻；AI 那条先留空串，在 `finally` 里补上流结束时刻 —— 与后端「回合收尾才写记录」对齐，失败/中断也补得上。
  - 前端 `style.css`：`.msg` 由「一行」改成纵向堆叠（`flex-direction: column`），左右对齐交给 `align-items`；新增 `.msg-time` —— 气泡下方的灰色小字，`tabular-nums` 免得数字宽度跳动。用户气泡贴右、AI 靠左的既有观感不变。
- **关键决策**：**解析不出就不渲染**（`v-if="formatTime(m.at)"`），不占位。正在流式的 AI 那条正好落在「`at` 是空串」这个分支上，于是它天然不会有时间，直到流结束。
- **验证**：
  - 历史：会话 `fb3c8215` 的 17 条消息**每条**都带 `.msg-time`，今天的记录显示 `21:26` / `21:44` / `21:47`（`HH:mm`）。用户那条的时间与气泡**右边缘齐平**、AI 的左边缘齐平（时间是同一 flex 列的末项，跟着 `align-items` 走）。
  - 三种格式分支（桩 `/records` 响应，时区 `Asia/Shanghai` 偏移 +8）：今天 → `21:59`；同年 `2026-03-05T18:20` → `03-05 18:20`；跨年 `2025-06-01T10:00` → `2025-06-01 10:00`。
  - 实时路径（桩 SSE，流故意不关）：发送瞬间用户那条已是 `22:01`，AI 那条**整块不渲染**（无时间元素、也不占位）；关掉流后 AI 那条补上 `22:01`，`.thinking` 与 `loading` 一起清掉。
  - 构建：`vue-tsc -b && vite build` 通过。

#### 流式协议升 v3，AI 消息按「正文 / 待办」分块渲染

- **动机**：两件事绑在一起做。① `astream_events` 此前跑的是 v2 老协议，LangChain 官方对**前端消费**场景推荐 `version="v3"`：新协议把各类事件归一成 `{type, event_id, seq, method, params}`，一条事件一个方法名，消费端不用再靠一堆 `on_*` 名字去猜形状。② 前端此前只能拿到**一段拼好的正文**，模型中途用 `write_todos` 排出的待办要么揉进正文、要么根本不显示。
- **改动**：
  - `server/service.py`：消费端整体改 v3。`method == "messages"` 取正文与工具调用 —— `content-block-delta` 的 `text-delta` 是正文增量；`block-delta.fields.args` 是**累计值而非增量**，收尾的 `content-block-finish` 里 args 已是 dict，以它为准。`method == "tools"` 的 `tool-started` / `tool-finished` 里**只有前者带工具名**，所以按 `tool_call_id → name` 配一张表。`message-finish` 取 `usage`。子智能体归属看 `lc_agent_name`（主控是字符串 `"None"`；`langgraph_node` 两边都是 `model`，区分不了）。
  - `server/stream.py`：新增 `todo_contents` + `PartBuilder`，把「正文 / 待办」抽成**有顺序的块序列**。
  - `observability/run_log.py`：`AgentRunLog` 按 v3 形状解析（原先是 v2 的 `on_tool_start/end`、`on_chat_model_stream`）。
  - 落库：出现过待办的回合把块序列写进 `extra.parts`（形如 `[{"kind":"todos","items":[…]}, {"kind":"text","content":…}]`）；没写 `parts` 的老回合仍只读 `content`，前端两条路都认。
  - 前端 `App.vue`：AI 消息由「一个气泡」改成**有序块列表** —— 正文块灰底（Markdown 渲染），待办块蓝底圆点列表，按出现顺序自上而下追加。不做原地改写、不做勾选/状态 UI。工具帧照旧下发（`{"type":"tool"}`）但前端不渲染；右侧计划面板与它无关，未动。
- **关键决策**：
  - 待办块是**追加**而非原地刷新：一次 `write_todos` 对应一块，块内逐帧全量替换。参数没流完时后端会推**空数组**的帧（一轮真实回话里连出过 21 条），所以判据是「末尾已是待办块**且**这一帧不是新调用的开头那条空帧」—— 少了后半句，每条空帧都会多顶出一个空框。
  - **已知边界**：两次 `write_todos` 之间没有正文、且第二次的首帧非空时，两块会并成一块。这是拿「首帧必空」当分隔符换掉的方案（另一条路是给 SSE 帧加 block `index`），代价就是这个边界。
- **验证**（会话 `fb3c8215` 的 `/hello2` 轮，后端已跑 v3 代码）：
  - 流：3054 帧 = `ai` 2921 / `todos` 112 / `tool` 20 / `done` 1；待办轨迹 `[] → 1 条 → 2 条 → 3 条 → 4 条`，**累计语义**在真实流里成立；子智能体的正文照旧被压掉（无英文旁白）；本轮没有 `task` 帧，即没派子智能体。
  - 库：新的 assistant 记录 `extra.parts = [todos(4 条), text(6193 字)]`，`content` 6193 字；更早的记录只有 `intent`，回退路径正常。
  - 前端（浏览器实看该会话）：最后一条 AI 消息渲染成 `[待办蓝框 4 条] + [正文灰框]`，顺序与落库一致，整页只有 1 个待办框。
  - 前端（桩 SSE）：21 条空帧 + `[] → [A] → [A,B] → [A,B,C]` 只产生 **1** 个框；两次连续 `write_todos`（中间无正文）产生 **2** 个框。
  - 构建：`vue-tsc -b && vite build` 通过。
- **测试**：新增 v3 事件夹具 `tests/v3_events.py`（`FakeAgent.astream_events` 必须是**协程**、返回异步迭代器 —— 写成异步生成器会直接 `TypeError`）；`tests/test_logging_pipeline.py`、`tests/test_redaction.py` 跟着改写。全量 **362** 条通过（此前 352 通过 / 10 失败，那 10 条正是按 v2 形状写的）。

#### 取消 `inspect` 直接执行路径：这类问题一律交给智能体

- **动机**：`inspect` 是**关键字独占的直接执行**路径 —— 命中强关键字（工作区 / 目录结构 / 仓库记忆 / 记忆文件 / 列出文件 / 检查工作区 / inspect）就绕开模型，直接 `ls -la` + 读仓库记忆。两个问题：① 它只会固定那两下，「当前工作目录是什么」「有哪些项目」这类稍作展开的问法答不了；② 已经出现误命中 —— 「…是还在 PR 里待合并还是已合并到 master 了？」期望 `qa`、实得 `inspect`。而 `qa` 形态的主控本来就有 `ls` / `grep` / `read_file` 与读仓库记忆的能力，答得比固定模板好。
- **改动**：
  - `server/intent.py`：`TASK_TYPES` 删 `"inspect"`；`DIRECT_INTENTS` 由 `{sync, inspect}` 收成 `frozenset({"sync"})`；删掉 `IntentRule("inspect", strong=(…))` 整条规则（那些词随即不再口令直通，改由 LLM 判定）；`match_strong` 的注释改成只说 `sync`。
  - `server/service.py`：删 `_direct_inspect`，`_execute_direct` 去掉 inspect 分支（见 `service.py:261`，现在只剩 `sync`）；顺带清掉只被它用到的 `memory_path` 导入。
  - `agent/profiles.py` / `agent/prompts.py`：注释里的「sync / inspect」改成不含 inspect。
- **关键决策**：**不是**把 inspect 放开给 LLM 判（那是另一条路），而是整个撤掉 —— 留着这个意图名本身就是风险：`profile_for` 对未知意图兜底成**全量装配**（`create_pr` + 三个子智能体），一个残留的「inspect」会静默挂上一整套编码权限。
- **验证**（会话 `ae35cc1f`，后端已跑新代码，分类器 `localhost:6006` 返回 200）：
  - 「列出项目根目录下的文件」→ `intent=qa source=llm 理由=查看仓库文件结构或回答基础问题`，`form=qa`；智能体自己 `execute` 一次 `ls -1a /ai_coding/project/java_test_demo/`，LLM 2 次 / 工具 1 次，整轮 **12.4s**。
  - 「看看工作区有哪些文件」（改动前**确定**会口令直通 inspect 的措辞）→ `intent=qa source=llm 理由=查看仓库文件列表，回答基础问题`；智能体自己跑 `ls -la /ai_coding/` 与 `find /ai_coding/skills -type f | sort`，LLM 3 次 / 工具 2 次，整轮 **7.2s**；答出整棵工作区（project / policies / skills + 三个空目录），比原固定模板细。
  - 两轮都没有走直接执行路径；也都没有 `todos` 帧（模型这轮没调 `write_todos`）。
- **测试**：`tests/test_intent_detection.py` 新增 `TestInspectIsGoneEntirely`（4 条）：`DIRECT_INTENTS == {"sync"}`、`"inspect" not in TASK_TYPES`、旧强关键字不再短接（`看下工作区` / `列出文件` / `检查工作区` / `仓库记忆里是怎么说的` 一律 `match_strong() is None` 且 `classify() == "qa"`）、撤掉口令后要**真交模型判**（`detect_intent` 对 `看下工作区` 会调一次分类模型且 `source=llm`）。全量 **362** 条通过。

#### 意图说明改口径：qa 覆盖「查仓库现状」，review 收紧成「只做审查」

- **动机**：分类器的 LLM 那一档恢复后（此前本机 vLLM 没起，路由一直退化成纯关键字）实测发现，「展示一下最近没有关闭的 PR」被判成 `review`，理由是「查看未关闭的PR列表进行评审」。**这不只是形态名不同**：`review` 形态的主控手里有 `comment_pr`（写工具），而 `qa` 形态没有 —— 一句纯查看的请求被配上了回评 PR 的权限。根因在 `_INTENT_SPECS`：`qa` 只写着「基础问题（概念、用法、原理、环境、工具的提问）」，通篇是**知识问答**，于是「查仓库和 PR 的当前状态」既够不上 qa，落进 review 的「PR 评审」里也不奇怪。
- **改动**：`server/intent.py` 的 `_INTENT_SPECS` 改两行。
  - `qa` → 「查看仓库相关信息（文件、PR、提交、仓库记忆），或回答基础问题（概念、用法、原理、环境、工具）」。PR **不单列**，作为「仓库相关信息」里的一项，与文件、提交、仓库记忆并列。
  - `review` → 「对已有的 PR 或代码改动做审查、给结论、回评（只做审查：不改代码、不出方案）」，把「不做别的」写死在说明里。
- **关键决策**：修描述而**不是**调选项顺序 —— 靠调整列出顺序去赌分类器的偏好是投机，改口径才是治本。
- **验证**：
  - 离线 9 例（直调分类器，不启后端）：8 例命中，含本次要修的「展示一下最近没有关闭的 PR」→ `qa`（0.8s、`source=llm`）、「…是还在 PR 里待合并还是已合并」→ `qa`、「就按这个改吧」→ `coding`、「分析一下这个仓库的代码结构」→ `analysis`。
  - 唯一「差异」是「仓库记忆里对这个项目是怎么说的？」→ `inspect`：那是强关键字「仓库记忆」的**口令直通**（既有设计，走直接执行路径读记忆），不经 LLM，读到的内容一样，不是本次改动引起的。
  - **live 轮**（会话 `3da409ae`）：`意图分析：intent=qa source=llm 理由=查看仓库相关信息（PR状态）` → `form=qa`，工具序列 `get_pr_info ×2`（无 `task`、无写工具），21.0s，列出唯一一条 open PR 即 PR #1，并主动补了「master 停在初始提交、合并需人工在 Gitee 操作」。
- **注意**：分类器端点（本机 `localhost:6006`）当天**反复掉线又恢复**，量之前先 `curl http://127.0.0.1:6006/v1/models` 确认有 200 —— 端口处于 LISTENING 不代表能用。
- **测试**：`tests/test_intent_detection.py` 新增 `TestIntentSpecsDrawTheQaBoundary`（4 条）：qa 的说明必须含「查看仓库相关信息」与文件 / PR / 提交 / 仓库记忆，且仍覆盖基础问题；review 的说明必须点明「只做审查」「不改代码」；两条旧措辞不得再出现。全量 **331** 条通过（原 327）。

#### qa / analysis 补上 PR 的**读**工具（写侧仍然一个不给）

- **动机**：上一段把主控工具改按形态给之后，`qa` 与 `analysis` 只剩两个联网工具 —— 「给我看看最近没关闭的 PR」「当前代码是在 PR 里还是已经合并了」这两类很常见的问法，只能靠提交历史猜，答不准也答不出筛选口径。
- **改动**：
  - `tools/register.py`：`_FORM_TOOLS` 的 `QA_FORM` 与 `ANALYSIS_FORM` 由「只有联网工具」改为 `("get_pr_info", "list_pr_comments")`。**读**工具给、**写**工具（`create_pr` / `close_pr` / `comment_pr` / `save_repo_memory` / `storePlan`）一概不给。顺带删掉 `build_main_tools` 里已成死代码的提前返回。
  - `agent/prompts.py`：新增 `_READONLY_PR` 段并接到 `QA_AGENT_PROMPT` / `ANALYSIS_AGENT_PROMPT`（与 `_READONLY_GIT` 同一路子：不讲清「PR 能读」，模型会把「只读」理解成「PR 一律别碰」，于是连着两个工具都不敢用）。段里写明 `get_pr_info` 传仓库地址时按 `state`（open / closed / merged / all，默认 all）列 PR、传 PR 链接时读详情，`list_pr_comments` 传编号读评论，以及「不评论、不关闭、不创建、不合并」。`_ROLE_QA` 的「怎么查」补第 3 条、`_ROLE_ANALYSIS` 补一段收尾：PR 状态以 `get_pr_info` 的实际返回为准，**不要凭提交历史推测**。
  - 同一次编辑修掉一段过期指路：`_READONLY_BOUNDARY` 里的「由 planner 产出分步方案」—— plan 已是单智能体，照这句找 `planner` 会扑空。
- **关键决策**：少给工具比在提示词里劝阻有效 —— 写侧工具**看不见**，模型就没有「嘴上答应不动手、实际调了」的余地。
- **验证**（真实会话 `3da409ae`，仓库 `zzpfordevelopment/java_test_demo`，后端重启后跑）：
  - **qa 轮**（「展示一下最近没有关闭的 PR」）：`form=qa`，工具序列 `get_pr_info ×4`（**无 `task`**、无写工具），21.2s，答出唯一一条 open PR 即 PR #1，并指明「已可合并、未合并、最近更新是两条审查评论之后」。
  - **analysis 轮**（「分析一下这个仓库当前处在什么阶段：代码是还在 PR 里待合并，还是已经合并到 master 了？」）：`form=analysis`，工具序列含 `get_pr_info` 与 `list_pr_comments`（**无 `task`**、无写工具），33.9s，四条独立证据（`ls-remote` 分支指针、master 文件清单、README 行数差异、PR 实时状态）得出「仍在 PR 里待合并，master 至今是空仓库」。
- **附带发现（环境，非代码问题）**：这两轮的意图判定都是 `source=fallback 理由=TimeoutError`。单独量过分类器（绕过 5s 超时给 120s）得到 `Connection error.`，每次约 8.2s —— `INTENT_BASE_URL` 指向的本机 vLLM（`localhost:6006`，qwen3-8b）**没在运行**，所以 5s 超时是假象，调大超时也救不回来。服务没起时路由**完全退化成关键字口径**：无强关键字的自然措辞一律落 `qa`，`analysis` 只有弱关键字（分析 / 排查 / 为什么 / 原因 / 根因…），弱词不直通、只在回落档生效。**（同日晚些时候该服务已启动，分类恢复 `source=llm`：三次实测 0.2~0.8s。）**
- **测试**：`tests/test_profiles.py` 的只读形态断言改为期望两个 PR 读工具，并加一条「与写侧工具集合不相交」；`tests/test_prompts.py` 新增 `TestReadonlyFormsCanReadPrs`（5 条：两个读工具被点名、`state` 取值被写明、写侧仍写明禁止、「不要凭提交历史推测」在位、过期指路已消失）。全量 **327** 条通过（原 322）。

### 2026-09-17

#### 装配形态合一轴：`form` 一个字段定工具 / 子智能体 / 提示词 / 缓存

- **动机**：此前「只读与否」与「任务类型」是两个各管一摊的字段，能各自漂移（例如只读表改了、提示词没跟上）。现在只剩一个轴 —— `form`，它同时决定：① 挂哪些子智能体；② 主控拿哪些工具；③ 用哪份角色提示词；④ 每会话的智能体缓存键。
- **改动**：
  - `agent/profiles.py`：`AgentProfile` 只留 `form` 与 `subagents` 两个字段（`key` / `readonly` 删除）；`_PROFILES` 以**意图名**为键（与 `server/intent.py` 的 `TASK_TYPES` 同名），`coding` → 形态 `orchestrate`；新增 `PROFILED_INTENTS` 与 `profile_for(intent)`（未知意图回落最全的 `coding`）。
  - `tools/register.py`：新增 `_FORM_TOOLS` 表与 `build_main_tools(..., form=...)`。主控工具按形态给：`qa` / `analysis` 只有两个联网工具（写侧一个不给；**次日改成另给两个 PR 读工具**，见 2026-09-18 那条）；`plan` 给 `get_pr_info` + `save_repo_memory` + `storePlan`；`review` 给 `get_pr_info` + `list_pr_comments` + `comment_pr`；`orchestrate` 给全部 7 个。
  - `session/manager.py`：`SessionRuntime.agents` 的缓存键由「提示词」改成 `profile.form`；装配时把 `form` 传下去；构建日志带上 `form=`。
- **关键决策**：`storePlan` 原本只挂在 `planner` 子智能体上。`plan` 改成单智能体后，若不把它注入主控，**方案面板与 `get_unexecuted_plan` 会同时断**（一个没人写、一个取不到）。故 `plan` 形态的主控工具里补上 `storePlan`，以及与它同属一个流程的 `save_repo_memory`。

#### plan / review 改单智能体；coding 增「直通」分支

- **改动**：
  - `plan` 与 `review` **不挂子智能体**：主控自己出方案、自己审。deepagents 在无子智能体时 `task` 工具**真的会消失**（不是留着空转），已有测试钉住这一点。
  - `agent/prompts.py`：主控提示词按形态各写一份（`QA_AGENT_PROMPT` / `ANALYSIS_AGENT_PROMPT` / `PLAN_AGENT_PROMPT` / `REVIEW_AGENT_PROMPT` / `MAIN_AGENT_PROMPT`）。plan / review 的主控与对应子智能体**共用同一批方法段**（`_PLAN_WORKDIR` / `_PLAN_METHOD` / `_PLAN_RULES`、`_REVIEW_WORKDIR` / `_REVIEW_METHOD`），保证「谁来做都是同一套做法」。plan / review 的主控仍用带 git 小节的 `BASE_PROMPT`（方案的末步是「提交 PR」、审查要读 diff 与分支状态，少了这一段讲不了）。
  - `coding` 增加分支判断，写在 `_INTENT_DIRECTIVES["coding"]` 里 —— 智能体按 `(会话, 形态)` 长期缓存，意图指令是唯一能逐轮注入的地方：
    - 用户这轮的话里**带直接执行的意思**（「实施」「动手改」「开始改」「按这个方案做」「就按这个改吧」「直接做」「确认实施」）→ **跳过 `planner`**，直接派 `coder`，它用 `get_unexecuted_plan` 取上一轮那份未执行方案来实施，一路做到提 PR，随后派 `reviewer`。
    - 话里**没有**这层意思 → 先派 `planner` 出方案并呈给用户，**停下等「确认实施」**，再派 `coder` 实施，最后派 `reviewer`。
    - coder 取不到可执行的未执行方案（本来没有方案、或方案已执行过）→ **停下回报主控**，主控改派 `planner`，回到「等确认」那条路；不让 coder 硬做。
  - 记忆回写门槛（`_MEMORY_GATE`）表述收紧为「**第 0 步**」：先 `get_pr_info` 查未合并 PR → 再 `save_repo_memory` → 才出方案 / 派发子智能体；未完成不得出方案，也不得派 `planner` / `coder`。
- **关键决策**：`save_repo_memory` 归**主控**持有（`coding` 与 `plan` 两种形态都有），`review` 去掉，三个子智能体从来不带。理由是链路上的先后关系：只要进编码就一定要读方案，而读方案之前必须先把记忆回写掉 —— 于是不管这轮是出方案还是直通实施，回写都发生在主控派发之前，后续谁读到的都是本轮刚刷新的版本。
- **验证**（真实会话 `3da409ae`，仓库 `zzpfordevelopment/java_test_demo`，跑完整三轮）：
  - **plan 轮**：`intent=plan source=keyword`、`form=plan`，工具序列里**没有 `task`**，顺序为 `get_pr_info → … → save_repo_memory → storePlan`（门槛顺序正确），73s，落库 `plan_id=25`（6 步），前端方案面板可见。
  - **coding 直通轮**（用户发「确认实施」）：`intent=coding`、`form=orchestrate`，**整轮只出现两次 `task`：`coder` 与 `reviewer`，没有 `planner`**；coder 第 4 个动作即 `get_unexecuted_plan` 取到 plan 25，随后逐条 `updatePlanStep` 推进，`mvn test` 全绿（1/1）、`mvn package` 出 jar、容器内真机 `curl` 得 200 + `{"status":"ok"}`，`create_pr` 建成 PR #1，coder 耗时 174.5s / 整轮 349s；`reviewer` 只读审查后 `comment_pr` 评论到 PR（P0 0 / P1 0 / P2 2 / P3 7）；主控把结论原样呈给用户并以「哪些需要修改（未提及的我不动）」停下等待。
  - **review 轮**（同会话同 thread，用户发「审查一下 PR #1」）：`intent=review`、`form=review`（缓存未命中、新建了一套装配，thread 不变所以历史连续），**无 `task`**；主控自己读 8 个改动文件、逐条 grep `policies/java-style.md` 核对，并在副本里独立复跑 `mvn -o test` / 打包 / jar 启动 curl / 边界探针（405 / 404 / 406），自己 `write_file` 报告到 `/ai_coding/review/`，自己 `comment_pr`（P2 2 / P3 9）—— 全程未改 `/ai_coding/project/` 下任何文件，162s，28 次工具调用。
- **测试**：`tests/test_profiles.py` 补主控工具子集（五种形态各一份断言）、形态互异性、「单智能体轮次里没有 `task`」等；`tests/test_prompts.py` 补 plan / review 单智能体轮次与 coding 双分支（含取不到方案的回退）断言。全量 **322** 条通过（原 262）。

### 2026-09-05 ~ 2026-09-17（补记）

- **说明**：这一段是事后补记。项目不是 git 仓库，无法把改动还原到具体某一天，故合并成一段（条目仍按时间倒序：最新在最上）。下方原有条目**保持原样**，不改写历史；其中「agent 层：模型参数（禁用思考 + 温度）」里 `llm.py` 的参数说法已被本段第一条取代。

#### 移除思考结果通道（SSE / 落库 / 前端）+ `llm.py` 迁 `init_chat_model`

- **改动**：
  - `server/service.py`：`_split_chunk()` 改为 `_chunk_text()` —— `content` 是字符串就直接用，是内容块列表就只拼文本块；**推理类型块**（`reasoning` / `thinking` / `reasoning_content`）与 `additional_kwargs.reasoning_content` 一律丢弃。不再单独推推理事件，`summary` 与 `content` 只留正文。
  - 前端：`Message` 去掉 `reasoning` 字段，SSE 事件联合类型去掉 `'reasoning'`（现为 `'ai' | 'tool' | 'plan' | 'done' | 'error'`），`.think*` 一组样式删除，历史记录只还原 `role` / `content`。
  - `agent/llm.py`：`build_model` 改用 `init_chat_model(model_provider="openai")`（主 / 备），`temperature` 1.1 → **0.3**，**不传** `thinking`。
  - 删除 `ReasoningChatOpenAI` 子类与 `TestReasoningContent` 类（相关测试同步删除）。
- **关键决策**：
  - 不再传 `extra_body={"thinking": {"type": "disabled"}}` —— 实测不带它也返回 `reasoning_content`，而该键不在 OpenAI 规范内，传了只是多依赖一条未验证的接口契约。
  - 思考结果当初打算在聊天里展示，后确认**不需要**，因此整条通道**去掉**（而不是隐藏）：少一条链路、少一份落库体积，也不会再有「推理块夹带正文」这类坑。
- **验证**：全量 pytest **262** 条通过；前端 `vue-tsc` + `vite build` 干净；真实会话流内除 `ai` 外无文本帧、页面 0 个 `.think` 元素；日志完成行由「…输出 181 字 / 推理 595 字」变为不再带推理字数。

#### 意图分类：强/弱关键字 + LLM 判定，分类模型关思考

- **改动**：`server/intent.py` 关键词分「强 / 弱」两档并支持排除词，未命中则调 LLM 判定；`llm.py` 新增 `build_classifier_model()`（独立必配 `INTENT_MODEL*`，温度 0、`max_tokens=512`、不挂工具）；`classify` 回传 `source=llm|keyword|fallback` 与判定理由，进日志与 `session_records.extra.intent`。
- **关键坑（vLLM 方言）**：关思考**必须**写 `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`。顶层未知字段（含 DeepSeek 那套 `thinking`）会被 vLLM **静默忽略**，qwen3-8b 照旧把 `<think>…</think>` 内联写进正文 —— 首行不是意图词，`parse_reply` 必然失败，还白占 token（服务端 `max_model_len=1024`，抬 `max_tokens` 会直接 400），且拿满 5s 超时。
- **验证**：换键后实测 2.8~5.0s → 0.3s，解析直接命中；三次真实判定 `plan`/560ms、`coding`/312ms、`analysis`/396ms 均正确；关键字快路径（如「开始实施」）不调模型。
- **收尾**：删掉死配置 `intent_thinking`（连同 `.env.example` 的 `INTENT_THINKING`）；`build_classifier_model` 的 docstring、`config.classifier_model` 的 docstring 与 `INTENT_*` 段注释一并改写 —— 去掉「1.18s → 0.83s / 靠 `reasoning_content` 分通道 / 前端要展示思考过程」等旧说法，以及「三项留空逐项回落主模型」的错说法（实际不回落，未配置直接报错）。

#### 代码工作区状态（后台建 + 前端状态条）

- **改动**：`sessions` 加 `workspace_status` / `workspace_error` 两列（迁移 `b41d7e2c9f30`）+ dao `set_workspace_status`；`session/manager.py` 把「建会话」与「建代码工作区」拆开 —— 工作区放后台任务（`_on_build_done` 回调收尾）并新增 `ensure_runtime()`；`sandbox/provider.py` 支持**按 id 终止**沙箱；`service` 改为**先查状态再取运行时**；API 回执与 `SessionDetail` 带 `workspace_status`；前端加动态状态条。
- **关键决策**：建工作区约 10s，接口立即返回、前端按状态显示进度，不让创建会话的请求阻塞在那儿。
- **验证**：`tests/test_workspace_runtime.py` 覆盖「建成 / 重连失败回落新建 / 失败落 `failed` 并记 `workspace_error`」；前端构建通过。

#### 凭据脱敏层（令牌不落输出）

- **动机**：红线是「令牌不落输出」，而此前**只有提示词在防**（「别读 `.secrets`」）。模型一旦 `cat` 了 `/ai_coding/.secrets/gitee_token`、跑 `git remote -v` 或打印环境变量，令牌会流向四条出口：① 工具返回值 → ToolMessage → **Postgres checkpoint**（下次会话读回还会再喂给模型）；② 同一份文本 → `logs/agent.log`（工具**入参**也记）；③ SSE → 前端；④ 不经智能体的**直接执行路径**（`sync` / `inspect`）stdout 直接下发 + 落库。全部无人拦截。
- **改动**：
  - 新增 `redact.py`（纯函数，无状态可单测）：**值匹配**（会话级 Gitee 令牌 + settings 兜底令牌）+ **锚定上下文模式** —— 已知凭据前缀（`ghp_` / `github_pat_` / `glpat-` / `sk-` / `xox[bp]-` / `AKIA` / `LTAI` / `AIza`）、`Bearer <token>`、URL 用户信息（只打码口令段，保留用户名与主机）、赋值上下文 `token|secret|password|api_key = "…"`。
  - `agent/middleware/redaction.py` 具名中间件：`ToolMessage` 分支改写 `content`（`str` 与内容块列表两种形态都处理，用 `model_copy` **保住 `tool_call_id`**，丢了无法与 AI 的 tool call 配对）；`Command` 分支用 `dataclasses.replace` **保住 `goto` / `graph` / `resume`**；无命中时原样返回同一对象。
  - **挂载点**：主智能体 + **每个子智能体**（`SubAgent` 有独立 `middleware` 字段，主智能体的**不下传**）—— 读写沙箱的正是 coder / reviewer，漏掉它们等于没做。
  - `observability/run_log.py` 新增 `_safe()`：**先脱敏再截断**（顺序反了会把跨截断点的令牌留半截），覆盖委派描述 / 工具入参 / 工具返回三处。
  - `server/service.py` 四处出口：SSE 正文、SSE 工具帧、业务库记录与 `summary`、直接执行返回值与异常文本。
- **关键决策**：
  - **不复用** `langchain` 自带的 `PIIMiddleware`（已查源码，非疏漏）：它挂在 `before_model` / `after_model` 上，改写的是「送给模型的消息」，**没有 `wrap_tool_call`** —— 原始工具返回值照样进 state 与 checkpoint，正是要堵的第一条；且它管不到日志落盘与直接执行路径，内置检测器（email / 信用卡 / IP / MAC / URL）套在编码场景还会把仓库 URL 全打码。
  - 脱敏中间件实际落在 `FilesystemMiddleware` **内侧**（默认栈排在前面、我方传入的追加在后，而列表第一个才最外层）。这反而正合需要：原始输出先被打码，外层转存 `/large_tool_results/` 的内容也是脱敏后的。
  - **明确不打码**裸高熵长串与 40 位 hex —— 不加白名单机制就天然免疫 git SHA / sha256，否则 diff 与测试输出会被打花。赋值上下文的右侧也只认字面量形态，`api_key = os.getenv("X")` 这类代码不被误伤。
  - **不做**：流式分片跨块的令牌（工具结果进 state 前已打码，模型根本没见过令牌，不会回吐）；用户输入不打码（那是用户自己的输入）；不加开关配置项；不加 `/ai_coding/.secrets/**` 路径级拒绝（打码已足够）；提示词不动（「别读 `.secrets`」保留为前置劝阻）。
- **验证**：新增 `tests/test_redaction.py` —— 值匹配命中、四类模式各命中、**不误伤**（40 位 hex / SHA、`os.getenv("X")`、普通代码与 diff 原样返回）、空词表不报错；中间件两分支脱敏且 `tool_call_id` 保留、无命中返回原对象；`AgentRunLog` 三处；service 的 SSE / 落库 / 直接执行 / error 分支。**活体验证只查误伤面**（跑一轮真实对话确认正常输出没被打码）—— 真阳性路径一律用**合成令牌**在单测里覆盖，不拿沙箱真令牌去试（那正是会泄露的那条路）。

#### 方案面板（plans 表 → 工具 → 事件 → 右侧面板）

- **改动**：`plan.py` 加 `_normalize_steps` 与 `storePlan` 硬约束；`dao.set_plan_step_status` + `updatePlanStep` 工具；`dao.get_latest_plan` + `GET /sessions/{id}/plan`；`service` 在 `updatePlanStep` 之后读库推 `plan` 事件（同一状态**去重**只发一帧，读库失败不打断事件流）。
- **验证**：`tests/test_plan_events.py` 覆盖字段映射、脏步骤跳过、非数组 `steps` 归一、事件去重、只对方案类工具触发、读库异常不影响流；前端右侧固定面板按 `steps` 渲染状态。

#### PR 流程调整（回评 + 两个方向的工具增减）

- **改动**：新增 `list_pr_comments` 工具；`create_pr` 改为**先查后建**（已有 open PR 则复用，不重复开）；删除 `merge_pr` 工具链（合并交回人工）；`get_pr_info` 归主智能体并支持传仓库 / PR 链接；`prompts.py` 调整 PR 相关约束（PR 为末步、用户点单、回评）。
- **验证**：各工具名与参数 schema 正确；未配置令牌时返回友好错误；真实 PAT 端到端走通。

#### 仓库记忆写入去工具化

- **改动**：新增 `save_repo_memory` 工具（写 `/memories/<repo>.md`）；主智能体工具接线 backend；`prompts.py` 把「记忆写入与合并」从工具编排改回提示词约束。
- **验证**：全量 pytest；会话中确认记忆文件写入、并在后续会话可被读回。

#### 沙箱：资产注入 + 可用性代理 + skill 同步

- **资产注入**：`policies` 补齐 + `deploy/sandbox` 脚本；`config.py` 增加资产注入配置；`sandbox/provider.py` 建会话时注入；`session/manager.py` 注入 askpass 环境变量；`.env` / `.env.example` 切原生镜像；文档与提示词同步纠正。
- **可用性代理**：新增 `sandbox/proxy.py` 的 `OpenSandboxProxy` —— 沙箱是不可靠资源（虚拟机重启、容器被清），代理负责探活与**按状态码白名单恢复**（仅 404 / 410 / 502 / 503 才重建，其余原样抛出）；`SandboxClient` / `SandboxProvider` 支持探活与重连；接到 backend 与会话管理；`tests` 用假沙箱覆盖。
- **skill 同步**：skill / policies / askpass 集中在仓库 `sandbox/` 下，**建会话时整棵上传** —— 改仓库即生效，无需重建镜像（此前靠镜像 COPY 到 `/ai_coding/skills/`，改一次就得重打镜像）。
- **关键决策**：代理只对**可安全重建**的状态码动手，其余故障不吞成重建，避免掩盖真实问题。
- **验证**：代理单测覆盖白名单内外两类状态码；一轮真实对话确认中间件从沙箱加载到新 skill。

#### 前端：Markdown 渲染 + 代码高亮 + 复制

- **改动**：接入 `marked` + `dompurify` + `highlight.js`；新增 `markdown.ts`（渲染 + 消毒）与 `highlight.ts`；流式更新改**节流缓冲**（每来一个分片就整篇重渲染太费）；代码块复制用事件委托；`style.css` 补代码块与按钮样式。
- **验证**：构建通过；XSS 探针（`<script>` / `onerror`）被消毒；流式 token 逐个显示无卡顿；复制按钮可用。

#### 前端：会话标题与侧栏

- **改动**：首条用户消息写入 `sessions.title`；对已有会话做一次性**回填**；侧栏显示首条消息摘要 + 仓库地址入框；用户消息贴右、AI 头像改 `aicoding`。
- **验证**：重启后端 + 浏览器实际验证列表显示与回填结果。

#### 中间件改写为具名类 + 运行时缓存修复

- **改动**：`agent/middleware/`（`context_inject.py` 意图注入 / `redaction.py` 脱敏）改为**继承 `AgentMiddleware` 的具名类**，不再用 `@wrap_model_call` / `@wrap_tool_call` 装饰器；装配点改为传**实例**；修 `session/manager.py` 的 `_on_build_done`，让后台建好的运行时真正进缓存。
- **关键决策**：具名类在装配点显式传实例更清楚，单测可直接实例化，也避开装饰器在子智能体链路里的作用域歧义。
- **验证**：全量 pytest；真实会话确认意图注入与脱敏均生效。

#### skill 与 references 整理

- **改动**：改写 `sandbox/skills/code-review/SKILL.md`，新增 `references/` 四份中文清单（`code-quality-checklist.md` / `removal-plan.md` / `security-checklist.md` / `solid-checklist.md`）；删除 `code-review-expert` 目录。
- **验证**：目录结构与 skill 加载正常；上传到沙箱后可被中间件读到。

### 2026-09-04

#### Server 层：意图分析 + 直接执行 + 记录

- **位置**：新增 `src/ai_coding_harness/server/`（api 与 agent 之间的业务层，API 路由只做 HTTP 转换）。
- **意图分析**（`server/intent.py`）：
  - 任务类型（新架构命名）：`coding / review / plan / qa / analysis / inspect / sync`。
  - `classify(message)` 关键字匹配（按规则顺序，先匹配先胜，默认 `qa`）；`DIRECT_INTENTS = {sync, inspect}`。
- **服务层**（`server/service.py`）：`HarnessService.chat(session, message)` 产出事件流：
  1. 分类意图 → 记录 `session_records`（type=user，extra.intent）；
  2. **直接执行**：`sync`（clone/pull）、`inspect`（列工作区 + 读仓库记忆）不经智能体，直接跑沙箱返回；
  3. 其余 → 调主智能体，**intent 经 `configurable.intent` 注入**；智能体调用用 **`astream_events(version="v2")`** 事件流（`on_chat_model_stream` 出 token、`on_tool_start/end` 出工具），config 用 `RunnableConfig` 对象；
  4. 回复记录到 `session_records`（type=assistant）。
- **接入**：`api/routes/chat.py` 改走 `app.state.service`；`api/app.py` 装配 `HarnessService`；`tools/context.py` 加 `get_intent()`。
- **验证**（端到端）：`同步一下仓库` → 直接 `git clone`；`看看工作区有哪些文件` → 直接列工作区+记忆；`你好…` → 走主智能体；`session_records` 落库 6 条（user/assistant × 3，带 intent，sync/inspect 标 direct）。

#### 可观测：接口日志 / 智能体日志双流 + trace_id

- **两条日志流**（按 logger 名前缀路由，各自滚动文件）：
  - `logs/api.log` —— 接口日志：`ai_coding_harness.api.*`、`uvicorn.access`；
  - `logs/agent.log` —— 智能体运行日志：`ai_coding_harness.{agent,server,session,sandbox,backends,tools,memory,safety}.*` + `opensandbox`/`httpx`/`langchain`/`langgraph`/`deepagents`；
  - 控制台：输出全部。
- **trace_id**：`observability/context.py`（ContextVar）+ 日志 `Filter` 注入；**接口中间件**（`api/middleware.py`，**纯 ASGI**，避免影响 SSE）为每个请求生成 trace_id，并记录 `→/← 方法 路径 状态 耗时 客户端`。
- **细粒度智能体日志**（`server/service.py`）：派发、**每次工具调用**（名称/参数摘要/耗时）、**每次 LLM 调用**（token 用量）、完成汇总（LLM/工具次数、输出字数）。
- **配置**：`LOG_API_FILE` / `LOG_AGENT_FILE`（替换原 `LOG_FILE`）。
- **验证**：起后端 + 调 `/health` 与 `/chat` → `api.log`（`[trace] → POST /chat …`、`← … 200 9461ms`）与 `agent.log`（`LLM 调用完成：输入 18111 / 输出 69 tokens`、`智能体完成：LLM 1 次 / 工具 0 次`）**同一 trace_id** 贯穿两文件。

#### 可观测：日志改为「控制台 + 滚动文件」

- **改动**：`observability/logging.py` 的 `setup_logging(settings)` 配置根日志为**双通道**：
  - 控制台：`StreamHandler(stdout)`；
  - 文件：`RotatingFileHandler`（按大小滚动，`encoding="utf-8"`）；
  - **uvicorn 自带 handler 移除、改为冒泡到根** → uvicorn / access 日志并入同一套输出（控制台与文件）。
- **配置**：新增 `LOG_LEVEL` / `LOG_DIR` / `LOG_FILE` / `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT`（`.env.example` 同步；`logs/` 加入 `.gitignore`）。
- **验证**：起后端后 `logs/harness.log` 生成，含 `Application startup complete` / `Uvicorn running` / access 日志（`GET /health`、`GET /sessions`）；格式 `时间 [级别] logger: 消息`；控制台输出与文件一致。

#### 修复：重启后旧会话 `/chat` 报 404（会话重建）

- **现象**：调用 `/chat/{id}` 返回 404（如 `a72ebc3e-...`）。
- **根因**：`/chat` 从**内存注册表** `app.state.sessions` 取会话；该表只存**本进程创建**的会话，**后端一重启就空**。而会话列表来自 **DB**（持久化）→ 界面能列出旧会话、点进去发消息就 404（库里该会话其实是 `active`）。
- **修复（方案 C：先重建、失败标失效）**：
  - `SessionManager.rehydrate(...)`：用库里存的 `repo_url`/`gitee_token`/`gitee_username` **重建沙箱 + 混合后端**；卷名由 `public_id` 决定 → **复用同一工作区卷**（克隆的仓库/未提交改动仍在），回填 `sandbox_id`。
  - `/chat` 路由：未命中内存 → 查库；`active` 则重建并注册；**重建失败**则把该会话标记 `expired` 并返回 409（明确提示新建会话）；库里没有 → 404；非 active → 409。
  - `DELETE /sessions/{id}`：不在内存时也能清理（仅标记库状态 `destroyed`）。
- **验证**：重启后端（内存清空）后对 `a72ebc3e-...` 调 `/chat` → **正常流式返回**（非 404）；库里 `sandbox_id` 更新为新沙箱、**卷名不变**（工作区复用）。

#### 启动脚本：加端口占用检查

- **改动**：`start.sh` / `start.bat` 启动前检查后端(8000)/前端(5173)端口；**被占用则打印占用 PID 并询问是否结束**（y 结束并继续，其余取消启动）。
- **验证**：`start.sh` 实测 —— 端口被占用时提示「端口 8000（后端）已被占用：PID 11624」，输入 `n` 后「已取消启动」并退出；`bash -n` 语法通过；`start.bat` 仍为 GBK 编码（含新增内容）。

#### 前端：会话列表加「复制仓库地址」按钮

- **后端**：`SessionListItem` 加 `repo_url`（取 `repositories.url`）。
- **前端**：每个会话项右侧加「复制」按钮（悬停显示，与「删除」并列）；点击用 `navigator.clipboard.writeText` 复制仓库地址（无 clipboard API 时降级 `execCommand`），按钮短暂显示「已复制」。
- **验证**：`/sessions` 返回 `repo_url`；浏览器实测 13 个会话均有按钮，点击后标签变「已复制」。

#### 修复：start.bat 中文乱码

- **现象**：运行 `start.bat` 时 echo 的中文显示为乱码。
- **根因**：`.bat` 存成了 **UTF-8**，而 cmd 用**系统代码页（中文 Windows 为 GBK/936）**逐行读取批处理文件 → UTF-8 的中文字节被按 GBK 解码 → 乱码。（此前加的 `chcp 65001` 也救不了：cmd 中途切换代码页有已知解析问题。）
- **修复**：把 `start.bat` 改为 **GBK 编码（无 BOM、CRLF）**，并把 `chcp 65001 >nul` 换成 **`chcp 936 >nul`**（与文件编码一致，确定性输出）。
- **验证**：回读校验 —— 无 BOM、UTF-8 无法解码（确为 GBK）、含「一键启动/启动后端/启动前端/打开浏览器/完成…」等中文、行尾为 CRLF。

#### 修复：后端起不来（zai 包冲突）+ web_search 懒加载

- **现象**：`python -m ai_coding_harness.main` 启动即报 `ImportError: cannot import name 'ZhipuAiClient' from 'zai'`。
- **根因**：`pyproject.toml` 的依赖**把发行名写成了导入名** —— 写的是 `zai`（PyPI 上是空占位包 0.0.2），而真 SDK 的发行名是 **`zai-sdk`**（`import zai`）。两者共用 `zai/` 目录，占位包的**空 `__init__.py` 覆盖了真 SDK 的导出**。README 的 Setup 步骤 `pip install -e ".[dev]"` 会按 pyproject 装依赖 → 装上占位包 → 覆盖 → 崩。而 `tools/web_search.py` 在**模块顶层**导入它 → 整条导入链崩 → 后端起不来。
- **修复环境**：`pip uninstall -y zai`（卸占位包）+ `pip install --force-reinstall --no-deps zai-sdk`（还原真 SDK 文件）。
- **修复根因**：`pyproject.toml` 依赖 `zai` → **`zai-sdk`**（`--dry-run` 验证：现在只装 `zai-sdk`）。
- **修复代码**：`tools/web_search.py` 把 `zai` 导入改为**懒加载**（在工具函数体内导入，缺失时返回友好错误）—— 避免可选 SDK 缺失/损坏拖垮整个后端启动。
- **验证**：`from zai import ZhipuAiClient` 恢复；后端启动成功（`Application startup complete`）。

#### 前端流式渲染修复（token 逐个显示）

- **现象**：回复不是逐个 token 显示，而是**结束后一次性出现**。
- **定位**（实测各层 SSE 到达时序）：直连后端 **逐 token**（~50ms/块）；经 Vite 代理 **也逐 token** → 传输层无问题；**问题在前端**。
- **根因**：`App.vue` 的 `send()` 把 assistant 消息存成**局部普通对象**再 push 进数组，后续 `reply.content += ...` 改的是**原始对象**而非 Vue 的响应式代理 → **不触发重渲染**，直到 `loading=false` 才一次性刷新。
- **修复**：改为按**索引**改数组元素（`messages.value[idx].content += ...`），走响应式代理触发更新。
- **验证**：短信流式采样（每 0.8s）文本长度 `1 → 23 → 145 → 152`，确认增量渲染；`vite build` 通过。

#### 意图分类：支持词 + 排除词

- **改动**：`server/intent.py` 的 `classify` 规则改为 `IntentRule(intent, keywords, excludes)` —— 命中任一**排除词**则该规则不成立，否则命中任一**支持词**判为该意图，全不命中默认 `qa`。
- **加的排除词**（针对易混场景）：
  - sync：排除「同步机制 / 同步锁 / 同步原理 / 数据同步」（并发话题属问答）。
  - plan：排除「设计模式」（知识话题）。
  - coding：排除「怎么实现 / 如何实现 / 实现原理 / 功能介绍 / 功能是什么 / 功能有哪些」（是在问，不是让动手写）。
- **验证**：16/16 用例通过，含排除词场景（"讲讲 Python 的同步机制"→qa、"怎么实现一个登录功能？"→qa、"介绍下设计模式"→qa、"这个功能有哪些参数"→qa）。

#### 启动脚本（一键起前后端）

- **改动**：新增 `start.bat`（Windows，双击可运行：分别在新窗口起后端/前端并打开浏览器）与 `start.sh`（Git Bash / Linux：后台起后端、等就绪后前台起前端，退出时 trap 停掉后端）。
- **配置**：venv 路径 / 端口 / 前端地址在脚本顶部可改，也支持环境变量覆盖。
- **验证**：`start.sh` 实测 —— 后端就绪（health 200）→ 前端 Vite 起在 5173（HTTP 200）。

#### 前端：DeepSeek 风格聊天页（Vite + Vue3）

- **后端**：新增 `GET /sessions`（会话列表，带仓库）、`GET /sessions/{id}/records`（时间线，记录+详情）；DAO 加 `list_sessions_with_repo` / `list_records_with_detail`；schemas 加 `SessionListItem` / `RecordItem`。
- **前端**：`frontend/`（Vite + Vue3 + TS）。
  - 布局仿 DeepSeek：左侧栏（品牌 + 新对话 + 历史会话列表）、主区（欢迎页 / 消息 / 底部输入）。
  - 新建对话需输入 `repo_url`；选中会话加载历史；发送用 **POST + 手写 SSE 解析**（EventSource 不支持 POST）。
  - `vite.config.ts` 把 `/sessions` `/chat` `/health` `/auth` 代理到 FastAPI（免 CORS）。
- **踩到两个坑**：① uvicorn 0.36+ 在 Windows 默认用 ProactorEventLoop（忽略事件循环策略），psycopg 异步报错 → `main.py` 用 `loop="ai_coding_harness.main:selector_loop_factory"` 显式指定 SelectorEventLoop；② sse-starlette 用 `\r\n` 换行 → 前端按 `\n\n` 切块解析不到事件 → 改用 `/\r?\n\r?\n/` 兼容。
- **验证**：`vue-tsc && vite build` 通过；浏览器端到端跑通（会话列表、新建对话、选中加载历史、发消息流式渲染回复）。

#### 智能体架构：一主三从

- **结构**：主智能体（唯一入口，协调 + 直接处理）+ 三个子智能体 `planner` / `coder` / `reviewer`（经 `task` 工具派发）。
- **改动**：
  - 新增 `agent/subagents.py`（`build_subagents(planner_tools, coder_tools, reviewer_tools)`）。
  - `agent/graph.py`：`build_agent` 支持 `subagents`；注册 harness profile 关闭 deepagents 默认自动添加的 `general-purpose` 子智能体（固定一主三从）。
  - `tools/register.py`：改为 `build_main_tools` / `build_subagent_tools` 按智能体分配工具。
  - `session/manager.py`：每会话建**一个主**智能体（去掉 multiton 按类型缓存）；`thread_id` = `public_id`。
  - `api/routes/chat.py` + `api/schemas.py`：**去掉 `task_type`**，chat 直接交给主智能体。
  - `prompts.py`：移除旧的 `CODING_PROMPT` / `READ_ONLY_PROMPTS` / `TASK_TYPES` / `get_task_prompt`，新增 `build_main_prompt(repo_url)`。
- **工具分配**：主 = web_search/fetch_url/get_pr_info；planner = web_search/fetch_url/storePlan；coder = get_unexecuted_plan/updatePlanStatus/create_pr；reviewer = comment_pr/get_pr_info。
- **验证**：编译通过、无旧符号残留；子智能体与工具装配正确；端到端建会话 + chat 走主智能体（自报「主控智能体」）；主智能体列出的子智能体恰为 planner/coder/reviewer。

#### get_pr_info 增强（支持仓库 / PR 链接）并归主智能体

- **输入**：`get_pr_info(source, state="all")` —— `source` 可以是仓库（`https://gitee.com/<owner>/<repo>` 或裸 `<owner>/<repo>`）或 PR 链接（`.../pulls/<n>`）；前者列 PR（`state` 过滤），后者返回单个 PR 详情。新增 `_normalize_source`（裸路径补全 URL）、`GiteeClient.list_pulls`、`_format_pr_list`。
- **归属**：该工具**归主智能体**；因主/子智能体架构尚未接入代码，**暂不注册**（从 `review` / `qa` 移除），待主智能体接入时挂载。
- **验证**：仓库 URL / 裸 owner-repo / PR 链接 / 非法输入四种路径均正确；`register` 已不含 `get_pr_info`。

#### git 工具：get_pr_info（按 PR 地址拉取 PR 信息）

- **改动**：`tools/git.py` 新增 `parse_gitee_pr_url(url) -> (owner, repo, number)`（支持 `/pulls/<n>` 与 `/pull/<n>`）；`GiteeClient.get_pull(owner, repo, number)`（owner/repo 可来自 PR 地址，不限于本会话仓库）；工具 `get_pr_info(pr_url)` —— 拉取并格式化为可读文本（标题/状态/作者/分支/链接/时间/可合并/评论数/描述）。
- **装配**：`review` → comment_pr + get_pr_info；`qa` → web_search + fetch_url + get_pr_info。
- **验证**：PR 地址解析（含非法地址拒绝）；工具装配正确；非法地址返回友好错误；**真实 token 拉取真实 PR 成功**。

#### 方案工具：storePlan / get_unexecuted_plan

- **改动**：新增 `tools/plan.py` —— `PlanContext`（会话上下文：session_db_id/owner/repo）+ `build_plan_tools(database, ctx)`：
  - `storePlan(content, steps?, title?)`：把方案写入 `plans` 表（供 coder 取用）。
  - `get_unexecuted_plan()`：取本会话最新 `pending` 方案并格式化为可读文本（含分步骤，结果带 `plan_id`）。
  - `updatePlanStatus(plan_id, status)`：更新方案状态（`executing` / `done` / `failed`），补上「取计划 → 执行完标记」闭环。
  - **按会话闭包**：模型无需传 owner/repo/session_id（工具内部捕获）。
- **装配**：`build_task_tools` 按类型挂载 —— coding → `create_pr` + `get_unexecuted_plan` + `updatePlanStatus`；planning → web_search + fetch_url + `storePlan`；`SessionManager.get_agent` 传入 `Database` + `PlanContext`。
- **coder 提示词**：取计划改为「工具已绑定会话、无需传参」；新增第 5 步「回写方案状态」（完成后 done / 失败 failed）。
- **验证**：store → get(带 plan_id) → updatePlanStatus(done) → 再 get 为空；存新方案后 get 取到新的；非法状态被拒；各任务类型装配正确。

#### 会话流程接入业务库 DAO

- **改动**：
  - `SessionManager.create_session` 改为「生成 `public_id` → 落库（sessions + repositories，拿内部 id）→ 用 `public_id` 建卷/沙箱 → 回填 `sandbox_id`/`workspace_volume`」；建沙箱/后端失败时**回滚**（删库记录 + 销毁沙箱）。
  - `Session` 数据类：`session_id` → `public_id`（对外）+ `db_id`（内部主键）；`thread_id` 用 `public_id`。
  - `destroy_session`：销毁沙箱后在库中标记 `status='destroyed'`。
  - `volumes.py` 加 `workspace_volume_name()`（卷名统一，供回填）。
  - `api/app.py`：lifespan 建 `Database` 并注入 `SessionManager`，退出时 `dispose()`。
- **验证**：HTTP 建会话 → 库中 `sessions`（public_id / sandbox_id / `ai-coding-ws-<public_id>` / status=active）+ `repositories`（owner/name）均正确落库；销毁后 status=`destroyed`。

#### 业务库 DAO 层

- **改动**：
  - `db/engine.py`：`Database` —— 异步引擎 + `async_sessionmaker`，`session()` 事务性上下文（正常提交 / 异常回滚）、`dispose()`。
  - `db/dao.py`：各表 CRUD —— sessions（create / get / get_by_public_id / list / set_sandbox / set_status / delete）、repositories（create / get）、plans（store_plan / get_unexecuted_plan / set_plan_status）、session records（add_record / list_records / get_record_detail）。
  - `db/__init__.py` 导出 `Database`。
  - 约定：DAO 函数接收外部传入的 `AsyncSession`、不自行提交，事务由 `Database.session()` 统一处理。
- **验证**：端到端跑通 —— 建会话（public_id）→ 建仓库 → 回填 sandbox/卷 → store_plan → get_unexecuted_plan → set_plan_status(done) → 再取未执行方案为 None → add_record → list_records / get_record_detail → **delete_session 级联清空 repo/plan/records**。

#### 业务库：会话记录表（垂直分表，页面展示用）

- **目的**：按会话记录「主要聊天 / 生成的方案 / review 结果」，供页面时间线展示；方案与审查结果**冗余存一份**（引用业务对象 id），便于查询。
- **垂直分表**（未来数据量大）：
  - `session_records`（基本信息/轻）：id / session_id（FK → sessions.id CASCADE）/ type（user·assistant·plan·review·pr·system）/ summary（摘要）/ ref_id（关联 plans.id 等）/ created_at；索引 `(session_id, created_at)`。
  - `session_record_details`（详情/重，1:1）：record_id（PK，FK → session_records.id CASCADE）/ content（全文）/ extra（JSONB）。
- **迁移**：`8cc9adc2c4ba_add_session_records_tables.py`。
- **验证**：`upgrade head` 成功；列/主键/FK(CASCADE)/索引确认；两表注释已写入。

#### session 主键改用方案 D（内部 BIGINT + 对外 UUID）

- **决策**：`sessions.id` 用 `BIGINT` 自增内部主键（索引最小最快、FK 轻量）；新增 `public_id`（`UUID`，UNIQUE，应用生成）作为**对外标识**（API / 卷名 / 线程名），避免自增 id 可枚举。
- **改动**：`db/models.py` —— `AgentSession.id` → BigInteger PK，新增 `public_id`（Uuid, unique）；`repositories.session_id` / `plans.session_id` → `BIGINT` FK。
- **迁移**：表为空，故 `alembic downgrade base` → 删旧迁移 → 重新 `--autogenerate` → 单一初始迁移 `6b0fc93c3dcd_init_business_tables.py`（仅本项目表）。
- **验证**：`upgrade head` 成功；`sessions.id=bigint(PK)` + `public_id=uuid(UNIQUE)`；两处 FK 均为 bigint → sessions.id CASCADE；漂移检查空 diff；注释 sessions 12/12、repositories 6/6、plans 12/12。
- **流程影响**：会话创建流程调整为「应用生成 `public_id` → INSERT 拿 `id` → 用 `public_id` 建卷名/线程名 → 建沙箱 → UPDATE 回填 sandbox_id」。当前 app 尚未接业务库，待 DAO 落地时生效。

#### 业务库：plans 表

- **模型**：`db/models.py` 加 `Plan` → `plans` 表。
  - 列：id / session_id（FK → sessions.id，`ON DELETE CASCADE`）/ owner / repo（冗余）/ title / content（Markdown 全文）/ steps（JSONB 结构化步骤 `[{index, goal, files, done_criteria}]`）/ status（`pending`/`executing`/`done`/`failed`）/ created_at / updated_at / started_at / finished_at。
  - 索引：`plans_session_status_idx (session_id, status)` —— 支撑 `get_unexecuted_plan`（找该会话 `status='pending'` 的最新方案）。
- **迁移**：`c3d0376fddd9_add_plans_table.py`（autogenerate，仅 plans 表——LangGraph 表已被过滤，未误判）。
- **验证**：`upgrade head` 建表成功；查 `information_schema` 确认列/类型/FK/索引；12/12 列 + 表注释已写入。

#### 业务表加列/表注释

- **改动**：`db/models.py` 给所有列加 `comment`，并加表注释（`__table_args__={"comment": ...}`）；`alembic revision --autogenerate` 生成迁移 `17e442b44e60_add_column_comments.py`（含 `alter_column(comment=...)` + `create_table_comment`）。
- **验证**：`alembic upgrade head` 应用成功；查 `col_description` / `obj_description` 确认所有列与表的注释已写入。

#### 业务库：sessions / repositories 表（SQLAlchemy + Alembic）

- **依赖**：`sqlalchemy[asyncio]` + `alembic`（加入 pyproject）。
- **模型**（`src/ai_coding_harness/db/`）：`base.py`（`Base`）、`models.py`（`AgentSession` → `sessions`、`Repository` → `repositories`）。
  - sessions：id / status / gitee_token（落库）/ gitee_username / sandbox_id / workspace_volume / title / created_at / updated_at / last_active_at / expires_at。
  - repositories：id / session_id（FK → sessions.id，`ON DELETE CASCADE`，`UNIQUE` 保证 1:1）/ owner / name / url / created_at。
- **Alembic**：async env（`alembic/env.py`），URL 取自 `settings.postgres_async_url`；`alembic/versions/0001_init_sessions_and_repositories.py`。
- **关键坑**：① Windows 下 psycopg 异步需 SelectorEventLoop（env.py 已处理）；② autogenerate 会把 **LangGraph 的表**（checkpoints/store 等）误判为「应删除」——已用 `include_object` 过滤，只管理本项目的表，避免误删。
- **验证**：`alembic upgrade head` 建表成功；查 `information_schema` 确认两表结构与 FK/UNIQUE 正确；`--autogenerate` 复检为空 diff（模型与库一致）。

#### 工作区卷改为「可配置后端」（支持集群）

- **背景**：集群部署已定，本地命名卷（节点本地）在集群下失效，需按环境切换共享存储。
- **改动**：
  - 新增 `sandbox/volumes.py` 的 `build_workspace_volume(settings, session_id)`，支持四种后端：`pvc`（本地命名卷 / K8s PVC，可配 `storage_class`/`storage`/`access_modes` RWX）、`host`（NFS 等共享路径，每会话子目录）、`ossfs`（对象存储）、`none`（不挂）；未知后端抛 `ValueError`。
  - `config.py` 新增 `sandbox_volume_backend` 及各后端配置（`sandbox_volume_host_path` / `sandbox_volume_storage_class` / `sandbox_volume_storage` / `sandbox_volume_access_modes` / `sandbox_oss_*`）。
  - `session/manager.py` 的 `_build_workspace_volume` 改为调用该函数（去掉内联构造）。
  - `.env.example` 补配置（默认 `pvc`，行为不变）。
- **验证**：各后端构造正确（pvc/host/ossfs/none + 未知后端报错）；默认 `pvc` 端到端回归通过（会话创建、`/ai_coding/project` 可写）。
- **范围外**：卷 GC、集群实机联调。

#### 重写 ai-coding-implementation skill（对齐 coder 角色）

- **定位**：从「需求理解 + 方案设计 + 实现」收敛为「**按 planner 的方案实施**」；明确不做方案设计、不与用户对话。
- **流程**：改为 `取方案(get_unexecuted_plan) → 建分支 → 按步骤实施(强要求) → 测试 → 更新仓库记忆 → 自检 → commit/push → create_pr → 回报主智能体`。
- **新增**：「按方案步骤逐条执行（强要求，不跳步/不合并/不改序）」「更新仓库记忆（按 repo-memory-template）」。
- **移除**：`comment_pr`（PR 评论归 reviewer）、「收尾回复用户」（改为回报主智能体）、「编码分层规则」（移到 planner 提示词，由方案管控）。
- **对齐**：前置条件改为「仓库已就绪（由 coder 准备）+ 已取到方案」，不重复 clone/pull。
- **planner 提示词**：新增「编码分层规则（方案须遵循）」段。
- **验证**：py_compile 通过；skill 无 `comment_pr`、含取方案/更新记忆/回报主智能体；planner 含分层规则。

#### 强要求：plan 分步骤、coder 按步骤逐条执行

- **planner**：方案**必须拆分为有序步骤**（每步含目标 / 涉及文件 / 完成标准），另附接口变化、影响面、整体验收标准；约束里明确「方案必须是有序步骤，每步可独立执行与验证」。
- **coder**：**必须按方案步骤逐条执行**——不跳步、不合并、不擅自改序，每步完成确认「完成标准」再进下一步；工作流程与原则两处都标为强要求。
- **验证**：py_compile 通过，关键字命中。

#### 四个智能体补「工作目录」段

- **主智能体**：project 只读（写码归 coder）、policies 只读、`output/` 可写、仓库记忆只读。
- **planner**：project 只读、policies 只读、仓库记忆（首次可生成）、产出写入 DB（不落文件）。
- **coder**：project 读写、policies/skills 只读、仓库记忆读写、`tmp/` 可写。
- **reviewer**：project 只读、policies 只读、`review/` 可写。
- **planner 首次处理**：非空且无记忆文件时，先用 `repo-bootstrap-analysis` skill 分析并生成仓库记忆文件。
- **验证**：py_compile 通过，各提示词均含「## 工作目录」且边界（只读/可写）明确。

#### 提示词明确「工具 + 具体命令」（不让模型猜）

- **原则**：提示词中所有操作都写明用哪个工具、执行哪条命令，给出确定执行路径。
- **改动**：
  - `BASE_PROMPT`：沙箱规则注明「写明具体命令，不要凭猜测」；git 工作方式给出逐操作命令（clone/pull/status/diff/checkout/add/commit/push）。
  - 主智能体：仓库分析用 `execute` 跑 `ls`/`find`/`cat`；代码问答用 `read_file`/`grep`；通用问答用 `web_search_tool`/`fetch_url`。
  - planner：`read_file` 读记忆 + `execute` 跑 `ls`/`find`/`cat` 浏览代码。
  - coder：`execute` 跑 `ls`/`git clone`/`git pull`；`read_file` 读记忆；实施走 skill（git/测试命令用 `execute`）。
  - reviewer：`execute` 跑 `git diff --stat` / `git diff -- <path>`；`read_file` 看代码；`write_file` 写报告。
- **验证**：py_compile 通过；各提示词均含明确的工具/命令。

#### coder / planner 提示词细化（计划落库 + 取计划 + 仓库区分）

- **coder**：① 区分项目存在与否（不存在 `git clone` / 已存在 `git pull` 更新）；② 新增「调用 `get_unexecuted_plan`（入参 `owner`/`repo`/`session_id`）从 DB 取上一次未执行的方案」；③ 代码编写与 git 提交通过 `ai-coding-implementation` skill 完成；④ 保留「按审查结论修复至无严重问题」。
- **planner**：新增「调用 `storePlan` 把方案写入 DB（供 coder 取用）」。
- **待办**：`get_unexecuted_plan` / `storePlan` 两个工具待写；DB 表设计待定；`ai-coding-implementation` skill 待修正。
- **验证**：py_compile 通过，关键字均命中。

#### 新架构提示词：主 + planner + coder + reviewer

- **改动**：`prompts.py` 新增 4 个角色提示词（均 = `BASE_PROMPT` + 角色）：
  - `MAIN_AGENT_PROMPT`：主智能体 —— ① 协调子智能体；② 直接处理仓库分析 / 仓库代码问答 / 通用问答。
  - `PLANNER_PROMPT`：制定实施方案（不写代码）。
  - `CODER_PROMPT`：按计划写码 / 测试 / 更新记忆 / 提 PR。
  - `REVIEWER_PROMPT`：只读审查 PR / diff，报告写 `/ai_coding/review/` 并 `comment_pr`。
- **说明**：旧的任务类型提示词（`CODING_PROMPT` / `READ_ONLY_PROMPTS` / `get_task_prompt`）暂时保留，待架构代码接入后移除。
- **验证**：py_compile 通过；4 个提示词均以 `BASE_PROMPT` 开头。

#### 仓库记忆：模版独立成文件 + 规则进基本提示词

- **改动**：
  - 基本提示词新增「仓库记忆文件规则」模块（用途、StoreBackend 位置、何时读写、模版路径）。
  - `CODING_PROMPT` 去掉内嵌模版，执行流程改为引用「仓库记忆文件规则」。
  - 模版独立成文件 `docs/repo-memory-template.md`，随镜像放入沙箱 `/ai_coding/policies/repo-memory-template.md`。
- **记忆存储**：StoreBackend，namespace `("memories",)`，key `${owner}_${repo_name}.md`。
- **验证**：py_compile 通过；BASE 8 模块（含新规则）、CODING_PROMPT 无内嵌模版且引用规则。

#### CODING_PROMPT 重写（方案先行 + 用户确认）

#### 移除 config 中的 repo_url 设计（统一走会话级 GiteeRepo）

- **结论**：`repo_url` 的值来自前端请求，PR 工具通过**会话创建时捕获的 `GiteeRepo` 闭包**获取 owner/repo，已足够；不需要把 `repo_url` 放进 agent 运行时 config。
- **改动**：`api/routes/chat.py` 的 `configurable` 去掉 `repo_url`（仅留 `thread_id`）；`tools/context.py` 删除未使用的 `get_repo_url()` 并更新模块说明。
- **保留**：`session.repo_url` 仍用于向 agent system prompt 注入「目标仓库」（`get_task_prompt`）。
- **验证**：py_compile 通过、无 `get_repo_url`/configurable repo_url 残留；app health 200。

#### 真实 PAT 端到端验证通过

- **验证**（真实私人令牌 + `zzpfordevelopment`，仓库 `zzpfordevelopment/test-abc`）：`.secrets/gitee_token` == 真实 PAT；askpass Username = `zzpfordevelopment`；沙箱内 `git clone` 成功；**`git push --dry-run` exit 0**（完整认证 + push 协商，dry-run 不改远端）。
- **说明**：本测试走「无请求 token → .env PAT 兜底」路径。发现并修正 `.env` 的 `SANDBOX_IMAGE` 误为原版镜像（导致 `setup.sh` 找不到）。

#### PAT / OAuth 双模式 git 认证用户名

- **方案**：git 认证用户名按 token 来源自动判定 —— 请求 token（线上 OAuth）用 `oauth2`；`.env` 兜底 token（本地 PAT）用配置的 Gitee 用户名。
- **改动**：`config.py` 加 `gitee_username` + `sandbox_git_username_env`；`GiteeRepo` 加 `git_username`；`SessionManager` 判定并注入 `GITEE_GIT_USERNAME`；镜像 askpass 改为 `${GITEE_GIT_USERNAME:-oauth2}`；`.env.example` 加 `GITEE_USERNAME`。
- **验证**：无请求 token → `alice`、有请求 token → `oauth2`，且沙箱内 askpass 返回值与之一致。
- **本地配置**：`.env` 用 `GITEE_ACCESS_TOKEN`（PAT）+ `GITEE_USERNAME`（Gitee 用户名）。

#### git 身份：静态写入镜像

- **结论**：push/clone/fetch 只需 token；git 身份（`user.name`/`email`）与认证无关，仅 `commit` 需要 → 采用**静态写入镜像**（`/etc/gitconfig`）。
- **改动**：镜像指南恢复 `files/gitconfig` + `COPY` 到 `/etc/gitconfig`；回退先前尝试的「按会话动态注入身份」代码（`fetch_gitee_user` / `GIT_AUTHOR_*` 注入）。
- **验证**：镜像内 `/etc/gitconfig` 就位，`git var GIT_AUTHOR_IDENT` = `ai-coding-agent <agent@example.com>`，沙箱内 `git commit`（不传身份、不配 config）成功、author 正确。

#### 镜像 git 能力验证

- **结果**：git 2.43.0，clone / checkout -b / add / commit / log / status / fetch / push 全部可用；**push 会调用 askpass**（`GIT_TRACE` 捕获 `run_command: /usr/local/sbin/gitee-askpass`）。
- **缺口**：镜像**未配置 git 身份**（无 `/etc/gitconfig`，`git var GIT_AUTHOR_IDENT` 报 Author identity unknown）→ `git commit` 会失败。
- **修复指引**：镜像指南的 `setup.sh` 增加 `git config --system user.name/email`（原「可选 gitconfig」改为 setup.sh 内必需项）。

#### 定制镜像验证通过（ai-coding/code-interpreter:v1）

- **验证**：走真实流程（注入 `GITEE_TOKEN` → 创建后执行 `setup.sh`）—— `.secrets/gitee_token`（0400）写入正确内容；`askpass` 返回 `oauth2`/令牌；`GIT_ASKPASS` 已配置；三个 skill、policies、git 2.43.0 就位；公开仓库 `git ls-remote` 成功。
- **配置**：`.env` 的 `SANDBOX_IMAGE` 切到 `ai-coding/code-interpreter:v1`。
- **待验证**：真实令牌的认证 push（需有效 OAuth 令牌 + 有写权限的仓库）。

#### 按方案 A 简化：去掉 sudo / gitwrite / agent 用户

- **决策**：沙箱 `execute` 默认以 **root** 运行 → 「令牌 root-only + sudo gitwrite 白名单」这套防护在 root 下形同虚设。按方案 A（已接受令牌对模型可见）**简化掉** sudo / gitwrite / sudoers / agent 用户。
- **改动**：
  - 镜像指南精简：仅保留 `gitee-askpass` + `setup.sh`；Dockerfile 不装 sudo、删 ENTRYPOINT、加 `ENV GIT_ASKPASS`/`GIT_TERMINAL_PROMPT`。
  - skill（`ai-coding-implementation`）与 `BASE_PROMPT` 的 push 改回普通 `git push`（askpass 自动供令牌）；删去 gitwrite/sudo 相关规则与排障项。
  - `workspace-directories.md` 的凭据说明对齐。

#### 沙箱初始化改走「创建后 execute」（Docker provider 限制）

- **验证定制镜像** `ai-coding/code-interpreter:v1`：✅ skills（3 个）、policies（java-style.md）、askpass/gitwrite 脚本、sudoers 文件、运行环境（Java21/Go1.25.5/Node22）都在；❌ `.secrets` 未生成、`sudo` 未安装、`agent` 用户不存在。
- **根因（两条 Docker provider 限制）**：① OpenSandbox 用默认 entrypoint `tail -f /dev/null` **覆盖镜像 ENTRYPOINT** → 镜像的初始化脚本从不执行；② **Docker provider 不支持 lifecycle hooks**（`lifecycle hooks are not supported by the Docker provider`）。
- **改动**：`SandboxProvider.create` 改为**创建后经 `execute` 执行 `sandbox_setup_command`**（默认 `/usr/local/sbin/setup.sh`）完成初始化，不依赖 ENTRYPOINT / hooks。
- **验证**：机制跑通 —— 创建后执行 setup，`.secrets/gitee_token`（0400 root）写入正确内容，且令牌不出现在命令文本里。
- **镜像待补**：装 `sudo`、提供不阻塞的 `setup.sh`（勿含 `exec tail`）、建 `agent` 用户。

#### 秘密处理决策：方案 A（令牌落沙箱 + 最小权限）

- **决策**：接受「令牌进沙箱 env」这一事实，靠「不池化（每沙箱单租户）+ OAuth 短时效/`projects` scope + prompt·skill 明令禁止读取」来控制风险。
- **改动**：`BASE_PROMPT` 的 git 章节改为如实描述（凭据由平台注入、gitwrite/askpass 取用），并加「严禁读取环境变量/`.secrets`、严禁打印令牌」；`ai-coding-implementation` skill 红线与动因描述同步；镜像指南补「已知取舍」。
- **排除**：OpenSandbox 凭据保险库（需服务端 `dns+nft`）。

#### 沙箱令牌注入（env 方式）

- **改动**：`SandboxProvider.create(volumes, env)` 支持注入环境变量；`SessionManager` 把会话 token 以 `GITEE_TOKEN`（`sandbox_gitee_token_env`）注入沙箱。
- **验证**：沙箱内 `env | grep GITEE_TOKEN` 能取到注入值。
- **发现问题（重要）**：env 注入**无法隐藏令牌** —— 环境变量不受权限保护，沙箱内任意命令（含 agent 的 `execute`）都能读到，且沙箱默认 root，entrypoint 也无法 unset（容器级 env，execd 独立继承）。即令牌会进入模型上下文，违背「秘密不进模型」的目标。要真正隐藏，需改用「root-only 文件 + 非 root 运行 agent」或 OpenSandbox 凭据保险库（需服务端 dns+nft）。

#### 工作区 Volume（容器可换、坏能重挂）

- **改动**：`SandboxProvider.create(volumes=...)` 支持卷挂载；`SessionManager` 为每个会话构建会话级工作区卷（Docker 命名卷 `ai-coding-ws-<session_id>` 挂到 `/ai_coding/project`）；config 加 `sandbox_volume_enabled/prefix/delete_on_termination`。
- **关键设计**：`session_id` 在创建沙箱前生成，卷名确定；`delete_on_sandbox_termination=False`（默认）使容器终止后卷仍在，新容器可挂同一卷续接。
- **验证**：真实故障转移测试 —— 容器 A 写 `/ai_coding/project/probe.txt` → 销毁 → 容器 B 挂同卷 → 读回成功。
- **遗留**：SDK 无删除卷 API，会话结束的卷清理需 Docker 侧 GC（否则命名卷堆积）。

#### 持久化 SQLite → PostgreSQL

- **改动**：checkpointer/store 从 SQLite 切到 PostgreSQL（`AsyncPostgresSaver` / `AsyncPostgresStore`，psycopg 异步连接池）；config 加 `postgres_host/port/db/user/password` + `postgres_url` 属性；依赖加 `langgraph-checkpoint-postgres`、`psycopg[binary]`。
- **动机**：多租户/多 worker 下状态需外置共享，SQLite 本地文件无法跨 worker。这也是「worker 无状态、可伸缩」的先决条件。
- **关键坑**：① langgraph `setup()` 建表迁移含 `CREATE INDEX CONCURRENTLY`，首次建表需 `autocommit=True`；表建好后连接池改为 `autocommit=False`（常规读写走事务），仅将来新增含 CONCURRENTLY 的迁移时需临时切回；② Windows 下 psycopg 异步不支持默认 ProactorEventLoop，`main.py` 需切 SelectorEventLoop（Linux 无此问题）。
- **验证**：checkpointer/store 构建成功，store 读写往返成功；完整端到端跑通（health 200 → create session 200 → chat SSE 236 行 → destroy 200），持久化走 Postgres。经 SSH 隧道连 `127.0.0.1:5432`。

#### 沙箱运行环境版本验证

- **验证**：沙箱（`opensandbox/code-interpreter:v1.1.0`）实测运行环境 —— Java 21.0.11、Go go1.25.5、Node v22.2.0（npm 10.7.0）均符合；Python 3.14.5 位于 `/root/.local/bin/python3.14`（uv 多版本，`/opt/python/versions/` 下有 3.10.20 / 3.11.15 / 3.12.13 / 3.13.13 / 3.14.5）。
- **说明**：默认 `python3` 指向系统 3.12.3；镜像中已按 3.14.5 使用。沙箱规则增加「仅支持 Java/Python/JavaScript/Go」与「支持运行环境版本」。

#### Gitee OAuth 登录（方案 A）

- **改动**：新增 `api/routes/auth.py`（`GET /auth/gitee/authorize` 跳授权页、`GET /auth/gitee/callback` 用 code 换 token）；config 加 `gitee_oauth_client_id` / `gitee_oauth_client_secret` / `gitee_oauth_redirect_uri`。
- **关键设计**：OAuth 授权码流程，scope=projects；callback 换 token 后返回 access_token 给前端，前端随 `create_session` 的 `gitee_token` 字段带上。
- **验证**：路由注册正常，未配置时返回友好 500。

#### 运行时上下文辅助函数 + repo_url 注入

- **改动**：新增 `tools/context.py`（`get_config` / `get_configurable` / `get_thread_id` / `get_repo_url` / `get_state`）；chat 路由把 `repo_url` 注入 `configurable`。
- **关键设计**：`thread_id` / `repo_url` 存于 LangGraph `configurable`；辅助函数无 runnable 上下文时安全返回默认值（不抛错）。

#### fetch_url 工具

- **改动**：新增 `tools/fetch_url.py`（httpx 拉取 + BeautifulSoup 剥离 script/style/nav 等 + markdownify 转 markdown），并接入 `build_task_tools`（qa / planning 增加 fetch_url）。
- **关键设计**：处理超时 / 连接失败 / HTTP 错误 / 限流（429 + Retry-After）/ 非 HTML / 空内容；内容超 20000 字符截断。
- **验证**：抓 PEP 8 页面转 markdown 成功；qa / planning 工具集含 web_search_tool + fetch_url。

#### 工作区目录调整（去 run、tmp 归入 /ai_coding/tmp）

- **改动**：沙箱工作区去掉 `run/`，`tmp` 改为 `/ai_coding/tmp`；`BASE_PROMPT` 工作区规则覆盖为新目录结构（含读写权限说明）。
- **同步**：`config.py`（去 `sandbox_run_dir`、加 `sandbox_review_dir`/`sandbox_tmp_dir`）、`provider.py`（mkdir 目录）、两个只读 skill（`/tmp`→`/ai_coding/tmp`）、`verify_opensandbox.py`、README、`docs/workspace-directories.md`。

#### skill 接入 deepagents（SkillsMiddleware）

- **改动**：`build_agent` 加 `skills` 参数透传给 `create_deep_agent`；`SessionManager.get_agent` 传 `skills=[sandbox_skills_dir]`（`/ai_coding/skills`）。
- **说明**：三个 skill（`ai-coding-implementation` / `repo-bootstrap-analysis` / `code-review`）写在 `sandbox/skills/` 目录，需经镜像 COPY 到沙箱 `/ai_coding/skills/` 后才被加载。
- **验证**：py_compile 通过；端到端 create + chat + destroy 跑通，SkillsMiddleware 初始化正常（沙箱 `/ai_coding/skills/` 为空加载为空、不报错）。

#### observability 层（日志 + trace 预留）

- **改动**：新增 `observability/logging.py`（`setup_logging`）+ `observability/tracing.py`（`setup_tracing` 预留）；在 `manager.py` 关键点加日志，`app.py` lifespan 启用。
- **关键设计**：日志格式含时间戳/级别/模块名；记录沙箱创建、会话创建/销毁、智能体构建等关键事件；trace 预留（LangSmith/OTel 后续接入）。
- **验证**：端到端跑通，日志输出关键事件。

#### safety 层：命令安全

- **改动**：新增 `safety/permissions.py`（`CommandSafety` 危险命令 denylist），接入 `OpenSandboxBackend.aexecute` 执行前检查。
- **关键设计**：denylist 覆盖 `rm -rf /`、`mkfs`、`dd` 写盘、`shutdown/reboot`、fork bomb、覆盖磁盘、全盘 777 等；拒绝时返回友好错误（exit_code=1）。
- **验证**：危险命令拒绝、安全命令（echo/git clone/python/rm -rf 具体子目录）放行。
- **说明**：人审（approval）预留，单用户本地暂不实现。

#### 多例智能体架构（按任务类型）

- **改动**：从「主智能体 + code_writer/code_reviewer 子智能体」改为「多例智能体」——每种任务类型一个智能体实例，按需构建并缓存，prompt = 基本提示词 + 任务类型提示词。
- **涉及**：
  - `prompts.py`：`CODING_PROMPT` + `READ_ONLY_PROMPTS`（qa/planning/analysis/inspect/sync/review）+ `get_task_prompt()`；移除旧角色 prompt。
  - `graph.py`：`build_agent` 改为接收任务级 tools + prompt（无子智能体）。
  - `tools/register.py`：`build_task_tools()` 按类型返回工具（coding→create_pr、review→comment_pr、qa/planning→web_search，其余无）。
  - `session/manager.py`：Session 持有多例 `agents` 字典，`get_agent()` 按需构建缓存，checkpoint 按 `(session, task_type)` 隔离。
  - api 层：`ChatRequest` 加 `task_type`，chat 路由按类型路由；`session_id` 替换 `thread_id`。
  - 删除 `subagents.py`。
- **验证**：7 种类型 prompt/工具正确；端到端 create + chat(task_type=inspect) 按需构建缓存 + destroy 通过。

#### repo_url 注入 agent prompt

- **改动**：`prompts.py` 加 `build_main_prompt(repo_url)`，`graph.py` 的 `build_agent` 加 `system_prompt` 参数，`session/manager.py` 创建会话时把仓库地址注入主智能体 prompt（主智能体调度 code_writer 时把仓库地址传下去 clone）。
- **验证**：prompt 含 repo_url 与 clone 说明；端到端 create_session 成功。

#### Gitee 配置动态化（仓库地址 + token）

- **改动**：Gitee 的 owner/repo 改为由 API 请求的仓库地址动态解析，token 请求优先、平台配置兜底。
- **涉及**：`tools/git.py`（新增 `GiteeRepo` + `parse_gitee_repo_url`）、`tools/register.py`、`config.py`（移除 gitee_owner/repo）、`api/schemas.py`（`CreateSessionRequest`）、`api/routes/sessions.py`、`session/manager.py`（每会话构建 PR 工具）、`api/app.py`（web_search 共享）。
- **验证**：仓库地址解析（`.git`/无后缀/尾随斜杠/非法均正确）；会话创建绑定 owner/repo/token，端到端通过。

#### api 层（FastAPI）

- **改动**：新增 `api/`（`app.py` / `schemas.py` / `routes/{health,sessions,chat}.py`），`main.py` 改用 `create_app` + uvicorn 启动。
- **关键设计**：
  - lifespan 构建共享依赖（model/tools/store/checkpointer/SessionManager），关闭时销毁会话并关闭持久化连接。
  - `POST /sessions` 创建会话（真实沙箱）、`DELETE /sessions/{thread_id}` 销毁；`POST /chat/{thread_id}` 用 `astream(stream_mode="messages")` 做 SSE 流式。
- **验证**：端到端跑通 —— `/health` 200；创建会话（真实沙箱）；`/chat` SSE 流式返回 AI token + done；销毁会话 200。

#### session 层：manager.py（会话管理）

- **改动**：新增 `session/manager.py`，`SessionManager` 管理每会话生命周期：`create_session()` 建沙箱 → 构建混合后端 → 编译绑定沙箱的 agent；`destroy_session()` 销毁沙箱。
- **关键修正**：移除 code_reviewer 的 permissions —— deepagents 的 `FilesystemMiddleware` 不支持「沙箱 backend + permissions」组合（抛 `NotImplementedError`），只读改由 prompt 约束。
- **验证**：端到端创建会话（真实沙箱）+ 编译 agent（`CompiledStateGraph`）+ 销毁，均成功。

#### agent 层：graph.py（装配）

- **改动**：新增 `agent/graph.py`，`build_agent(model, backend, tools, store, checkpointer)` 用 `create_deep_agent` 装配主智能体 + 两个子智能体。
- **关键设计**：backend 传实例（factory 已弃用），即每会话编译一个 graph（backend 绑定会话沙箱）；model/tools/store/checkpointer 跨会话共享。
- **验证**：用 `StateBackend` 临时后端 + `:memory:` store/checkpointer 装配，返回 `CompiledStateGraph`。

#### session 层：state.py（checkpointer + store）

- **改动**：新增 `session/state.py`，`build_checkpointer()` / `build_store()` 构建 async SQLite 的 LangGraph checkpointer 与 store。
- **关键设计**：
  - checkpointer = `AsyncSqliteSaver`，store = `AsyncSqliteStore`。
  - 连接必须用 `aiosqlite.connect(path, isolation_level=None)`（autocommit），否则 store 写操作报「嵌套事务」错误。
  - 配置新增 `checkpoint_db_path` / `store_db_path`（默认 `data/checkpoints.db`、`data/store.db`）。
- **验证**：构建 + setup 成功；store `aput`/`aget` 读写往返成功。

#### tools 层：register.py（汇总）

- **改动**：新增 `tools/register.py`，`build_tools(settings)` 返回 `HarnessTools`（`main_agent_tools` / `create_pr` / `comment_pr`）。
- **验证**：工具归属正确（web_search 归主智能体，create_pr/comment_pr 归子智能体），可直接喂给 `build_subagents`。

#### tools 层：web_search.py 优化

- **改动**：优化 `tools/web_search.py`（智谱 web 搜索工具）。
- **关键设计**：
  - 配置化：从 Settings 读 `zhipu_api_key` / `zhipu_base_url`（不再硬编码 YOUR_API_KEY），移除无用 `runtime` 参数。
  - 失败场景：超时 / 连接 / API 状态 / 响应错误 / 通用异常逐级捕获，返回友好错误信息；未配置 key 时返回提示。
  - 修正返回结构：`search_result` 实际是 `list`，逐条格式化为可读文本；空结果返回「搜索无结果」。
- **验证**：未配置 key 返回友好错误；真实调用返回 9 条格式化结果。

#### tools 层：git.py（Gitee PR 工具）

- **改动**：新增 `tools/git.py`，实现 `GiteeClient` + `build_pr_tools()`，提供 `create_pr` / `comment_pr` 两个工具，用 httpx 直接调 Gitee API v5（宿主进程，不经沙箱）。
- **关键设计**：
  - 端点：创建 PR `POST /repos/{owner}/{repo}/pulls`，评论 `POST /repos/{owner}/{repo}/pulls/{number}/comments`，`access_token` 放请求 body。
  - 配置新增 `gitee_access_token` / `gitee_owner` / `gitee_repo` / `gitee_base_url` / `gitee_base_branch`。
  - 未配置返回友好错误；HTTP 错误返回状态码 + 详情。
- **验证**：两工具名/参数 schema 正确；未配置时调用返回友好错误。

#### agent 层：subagents.py

- **改动**：新增 `agent/subagents.py`，提供 `build_subagents(create_pr_tool, comment_pr_tool)`，返回 `code_writer` / `code_reviewer` 两个 `SubAgent`。
- **关键设计**：
  - `code_writer`：`tools=[create_pr]`；`code_reviewer`：`tools=[comment_pr]`。
  - `model` 不指定、继承主智能体（DeepSeek）；内置文件工具/execute 由 deepagents 中间件注入。
- **验证**：两子智能体构造正确。

#### agent 层：prompts.py（三个 system prompt）

- **改动**：新增 `agent/prompts.py`，定义 `MAIN_AGENT_PROMPT`（主控/orchestrator）、`CODE_WRITER_PROMPT`、`CODE_REVIEWER_PROMPT`。
- **关键设计**：
  - 主智能体只做调度，先判断问题类型（一般性问题走 `web_search_tool`，写代码类走编码流程）。
  - 编码流程：`code_writer` 写码 → `create_pr` 工具创建 PR → `code_reviewer` 针对 PR 只读审查 → 按需回环。
  - PR 的创建/评论由专用工具（`create_pr` / `comment_pr`）实现，不直接跑 gh 命令；git add/commit/push 仍走命令。
  - `code_writer` 负责写码 + 自测 + 提交；`code_reviewer` 只读、报告写到 `/ai_coding/output/` 并发到 PR。
- **说明**：`web_search_tool` 由用户自行实现；git 代码平台采用 **Gitee**，`create_pr` / `comment_pr` 对接 Gitee API。

#### agent 层：模型参数（禁用思考 + 温度）

- **改动**：
  - `llm.py`：`init_chat_model` 增加 `extra_body={"thinking": {"type": "disabled"}}`（禁用思考/推理）与 `temperature=1.1`。
  - `config.py`：改用 `load_dotenv(verbose=True)`，字段默认值用 `os.getenv(...)` 读取；移除 `write_model_env`/`model_post_init`（不再写环境变量），并修正 `kimi_model` 环境变量名为大写 `KIMI_MODEL`。
- **验证**：主/备模型 `temperature=1.1`、`extra_body` 正确传入，model_name 正确。

#### agent 层：llm.py（DeepSeek 主 + Kimi 备）

- **改动**：`agent/llm.py` 改用 `init_chat_model(model_provider="openai")` 初始化主/备模型；`config.py` 新增 `ModelConfig`、`deepseek_*` / `kimi_*` 字段、`primary_model` / `backup_model` 属性、`write_model_env()`（把主模型写入 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，在 `model_post_init` 自动执行）。
- **关键决策**：DeepSeek 为主模型、Kimi 为备用，均走 OpenAI 兼容协议；显式 `model_provider="openai"`，避免 `deepseek-v4-pro` 被前缀推断成 `deepseek` provider（需额外包）。
- **验证**：`build_model` 主/备返回 `ChatOpenAI`、model_name 正确；`write_model_env` 生效；空 model 抛 `ValueError`。

#### 验证：OpenSandbox 端到端跑通

- **改动**：新增 `scripts/verify_opensandbox.py` 端到端验证脚本；`config.py` 增加 `opensandbox_request_timeout_seconds = 600`，`build_connection_config` 设置 `request_timeout`。
- **关键发现**：首次创建沙箱拉镜像耗时超过 SDK 默认 30s，触发 `ReadTimeout`；加大请求超时后解决。镜像内 Python 3.12.3。
- **验证结果**：创建沙箱 → 五个目录初始化 → 命令执行 → 文件写读 → 搜索 → 销毁，全部通过。

#### backends 层（智能体文件后端）

- **改动**：实现 deepagents 的混合文件后端。
  - `backends/opensandbox_backend.py`：`OpenSandboxBackend(BaseSandbox)`，子类化 deepagents 的 `BaseSandbox`，只实现异步原语 `aexecute` / `aupload_files` / `adownload_files` / `id`；`ls`/`read`/`write`/`edit`/`grep`/`glob` 由基类派生。
  - `backends/composite.py`：`build_composite_backend()` → `CompositeBackend(default=OpenSandboxBackend, routes={"/memories/": StoreBackend})`。
- **关键决策**：
  - OpenSandbox SDK 是异步的，故实现异步原语，同步版本抛 `NotImplementedError`（deepagents 工具层走异步路径）。
  - `StoreBackend` 用 `namespace=("memories",)`；`store`（LangGraph `BaseStore`）由外部传入，供 memory/session 层做持久化。
- **验证**：`/memories/` 路由的写入/读取/编辑端到端通过（`InMemoryStore`）；沙箱默认路由待真实沙箱验证。

#### 沙箱层（sandbox/）

- **改动**：
  - `sandbox/client.py`：`build_connection_config()` + `SandboxClient`（`run`/`write_file`/`read_file`/`list_dir`/`search`/`destroy`）+ `CommandResult`。
  - `sandbox/provider.py`：`SandboxProvider`（每会话新建沙箱 → `mkdir -p` 初始化五个目录 → 返回 `SandboxClient`；`destroy` 销毁）。
  - `config.py` 增加 `sandbox_timeout_minutes = 60`。
- **关键决策**：
  - 沙箱镜像 `opensandbox/code-interpreter:v1.1.0`，服务地址 `192.168.72.128:8080`，沙箱根目录 `/ai_coding`。
  - 五个工作目录：`/ai_coding/{project,policies,run,skills,output}`。
  - OpenSandbox API key 配置在 `.env`（不进代码、不入库）。

#### 脚手架 + 配置

- **改动**：建立 src 布局、`pyproject.toml`（hatchling 打包）、`.env.example`、`README.md`、`.gitignore`、`config.py`、`main.py` 占位。
- **关键决策**：
  - 发行包名 `ai-coding-harness`，导入包 `ai_coding_harness`。
  - venv 用 `D:\python-venv\ai_coding\Scripts\python.exe`（Python 3.14.7）。
  - 环境变量无前缀；`OPENAI_API_KEY` / `OPENAI_MODEL` 由用户自填。
