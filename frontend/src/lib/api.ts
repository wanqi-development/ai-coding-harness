// 后端 API 客户端。聊天用 POST + 手写 SSE 解析（EventSource 不支持 POST）。

export interface SessionItem {
  session_id: string
  title: string | null
  owner: string | null
  repo: string | null
  repo_url: string | null
  status: string
  created_at: string
}

/** 代码工作区状态：pending / provisioning 都算「准备中」，是前端要等的两个态。 */
export type WorkspaceStatus = 'pending' | 'provisioning' | 'ready' | 'failed'

export interface SessionDetail extends SessionItem {
  workspace_status: WorkspaceStatus
  /** 本会话有一轮对话正在跑：刷新后据它决定要不要接回直播流。 */
  running: boolean
}

export interface RecordItem {
  id: number
  type: string
  summary: string | null
  content: string | null
  /**
   * 业务附加字段：`intent`，以及 `parts`（正文/待办的顺序块，只有出现过待办的回合才写）。
   * 有 `parts` 就按它回放，否则退回 `content`。
   */
  extra: any
  /**
   * 这一轮的 token 账（只有 assistant 记录有；本功能上线前的记录是 null）。
   *
   * 后端已从 `extra.usage` 提出来单列，所以消息角标读它即可，不必知道内层形状。
   */
  usage: UsageDetail | null
  created_at: string
}

/** 历史记录的一页：默认最近 20 条，`next_before_id` 继续向前翻。 */
export interface RecordPage {
  items: RecordItem[]
  has_more: boolean
  next_before_id: number | null
}

/** 一组输入 / 输出计数（一轮的合计，或某个执行主角的累计）。 */
export interface UsageTokens {
  input: number
  output: number
  calls: number
}

/**
 * 一轮的消耗明细（每条 assistant 记录一份，也是 SSE `usage` 帧的载荷）。
 *
 * 与会话级的 `UsageSnapshot` 是两个东西：这个是原始账、那个是把它加起来。
 */
export interface UsageDetail {
  totals: UsageTokens
  /** 归类名 → 该主角在这一轮的用量（`main` / `planner` / `coder` / `reviewer` / `internal` / `intent`） */
  by_actor: Record<string, UsageTokens>
  /** 这一轮**主控**解析出来的模型名（标在这条回答的最下边）；空 ＝ 解析不到，不显示那一格 */
  model: string
  /** 平台没回用量的调用次数 —— 账上的缺口，如实显示而不是当 0 */
  missing: number
  /** 这一轮没跑完（用户终止 / 守卫中止 / 故障）：钱照样算，但标出来 */
  interrupted: boolean
  context?: UsageContext | null
}

/**
 * 上下文压力：**最近一次主控调用**的输入离主控模型的窗口还有多远。
 *
 * `window` 是**该轮主控模型**的窗口（orchestrate / plan 是 GLM 128k、review 是 Kimi 256k），
 * 所以同一份历史在不同形态下的 `ratio` 本来就不同。`input` 是最近一次主控 LLM 调用的
 * 输入 tokens（2026-10-20 用户口径：环图看「现在多长」，不看历史峰值）；`max_input`
 * 是旧记录里的字段名，只为读改版之前的历史。
 *
 * 字段名与接口一致（snake_case），不做转换：后端每个模型都直接序列化，多一层改名就多
 * 一处会漂的地方。
 */
/** 一个文件的改动：路径 + 增删行数 + 状态码（`A` 新增 / `M` 改 / `D` 删 / `R` 改名）。 */
export interface FileChange {
  path: string
  added: number
  deleted: number
  status: string
  /** 点开之后才有：这个文件的 diff 正文（服务端已脱敏，可能被截断） */
  diff?: string
  /** 加载状态或失败原因（没点开过就是空） */
  diffState?: string
  diffTruncated?: boolean
}

/**
 * 这一轮改了什么（**建成 PR 才有**）。
 *
 * 数据源是宿主机上那份代码缓存（`repo_cache`），`base...head` 两支来自 `create_pr` 的入参 ——
 * 与沙箱是否还在无关，所以沙箱被回收之后这些 diff 照样点得开。
 */
export interface ChangesInfo {
  base: string
  head: string
  files: FileChange[]
  /** 文件数超过服务端上限时才有：真实总数 */
  total?: number
}

export interface UsageContext {
  /** 服务端认的告警线 / 危险线（占窗口比例）：环图的变色口径由它给，界面不写死阈值 */
  warn_ratio?: number
  alert_ratio?: number
  /** 最近一次主控 LLM 调用的输入 tokens（2026-10-20 起） */
  input?: number
  /** 旧记录里的口径（历史峰值）；新记录不再写它 */
  max_input?: number
  window: number
  ratio: number
  compressed: number
  compressed_now: boolean
}

export interface PlanStep {
  index: number
  goal: string
  status: 'pending' | 'running' | 'done' | 'failed'
  files?: string[] | null
  done_criteria?: string | null
}

export interface PlanInfo {
  plan_id: number
  title: string | null
  status: string
  steps: PlanStep[]
  /**
   * 方案全文（Markdown）：聊天里那条「方案」链接点开就地展开的就是它。
   *
   * 后端把它同时放进 SSE 的 `plan` 帧与 `type='plan'` 的记录里，所以实时与刷新后
   * 拿到的是同一份数据，前端不必再补一次请求。
   */
  content?: string | null
  updated_at: string
}

/**
 * 仓库记忆（StoreBackend 上那份 Markdown）。
 *
 * `content` 为 null ＝ 这个仓库**还没写过**记忆 —— 与「写过一个空文件」是两件事，
 * 面板据此显示空态而不是一片空白。
 */
export interface MemorySnapshot {
  path: string
  content: string | null
}

/** 一条 PR（平台差异后端已归一）。`state` 是状态码，中文标签由前端映射。 */
export interface PullItem {
  number: string
  title: string
  /** `open` / `merged` / `closed` / `unknown`（认不出的状态，**不当成没开**） */
  state: string
  url: string
  branch: string
  author: string
  created_at: string
  updated_at: string
}

export interface PullList {
  /** 未合并的条数（面板顶部的「未合并 N」用它） */
  open_count: number
  pulls: PullItem[]
}

/** 一行日志：`text` 是原始整行，`level` 供着色（认不出就是空串）。 */
export interface LogLineItem {
  ts: string
  level: string
  /** 打这行的 logger 名（认不出就是空串） */
  logger: string
  text: string
  /** handoff 内层日志的 run 父子关系；根层日志为空串 */
  run_id?: string
  parent_run_id?: string
}

export interface LogPage {
  /** 命中的**总行数**（可能大于 `lines` 的长度 —— 面板只说最后 N 行） */
  total: number
  lines: LogLineItem[]
}

/** 一条待办：文本 + 状态（pending | in_progress | completed）。 */
export interface TodoItem {
  content: string
  status: string
  /** 子任务（子智能体自己那份清单），只有主项才有 */
  children?: TodoItem[]
}

/** handoff 内层通过 stream_writer 发上来的一个 run 事件。 */
export interface RunTrace {
  kind: string
  node_kind: string
  phase: 'start' | 'delta' | 'finish'
  run_id: string
  parent_run_id: string
  root_run_id: string
  agent?: string
  name?: string
  content?: string
  status?: string
  data?: any
}

/** 意图澄清弹窗里的一个候选；`intent` 由后端固定，label/description 是 qa 自由写的。 */
export interface ClarificationOption {
  intent?: string
  label: string
  description?: string
  value?: string
}

/** 多步澄清中的一页。第一步负责确定 intent，后续步骤补充业务口径。 */
export interface ClarificationStep {
  id: string
  question: string
  options: ClarificationOption[]
}

export interface ClarificationAnswer {
  step_id: string
  choice: number
}

/** SSE `clarification` 帧 / GET 待澄清接口的同一份载荷。 */
export interface ClarificationInfo {
  id: string
  /** 兼容旧后端的单题字段。 */
  question?: string
  options?: ClarificationOption[]
  steps?: ClarificationStep[]
  allow_other?: boolean
}

export interface ChatEvent {
  type:
    | 'ai'
    | 'reasoning'
    | 'tool'
    | 'todos'
    | 'plan'
    | 'usage'
    | 'changes'
    | 'clarification'
    | 'run'
    | 'done'
    | 'error'
  content?: string
  name?: string
  /**
   * `reasoning` 帧：产出这段思考的角色（`main` / `planner` / `coder` / `reviewer`）。
   * `tool` 帧：只有委派子智能体的 `task` 带，值是角色名。
   */
  agent?: string
  /** `todos` 帧：**整棵**待办树的全量内容（每次都是整体替换，不是增量） */
  items?: TodoItem[]
  error?: string
  plan?: PlanInfo
  clarification?: ClarificationInfo
  /**
   * `usage` 帧：本轮收尾时下发的那份账。
   *
   * 放在正文之后发，所以它天然贴在回复末尾；前端把它挂到**当前这条** AI 消息上，
   * 用户不必刷新页面就能看到本轮的消耗与上下文压力。
   */
  usage?: UsageDetail
  /**
   * `changes` 帧：这一轮**建成 PR** 时下发的「改了哪些文件」（在本地那份代码缓存里算的）。
   * 与 `usage` 一样贴在回复末尾：一条消息的**属性**，不是一段内容。
   */
  changes?: ChangesInfo
  /** `run` 帧：handoff 内层事件，用 run_id / parent_run_id 挂在 run 树上。 */
  run?: RunTrace
}

export async function listSessions(): Promise<SessionItem[]> {
  const r = await fetch('/sessions')
  if (!r.ok) throw new Error(`列表失败：${r.status}`)
  return r.json()
}

/** 建会话。`reused` 为真表示该仓库已有活跃会话，返回的是既有的那个（没新建）。 */
export async function createSession(
  repoUrl: string,
): Promise<{ sessionId: string; reused: boolean }> {
  const body: Record<string, unknown> = { repo_url: repoUrl }
  const r = await fetch('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`创建会话失败：${r.status} ${await r.text()}`)
  const data = await r.json()
  return { sessionId: data.session_id, reused: Boolean(data.reused) }
}

export async function destroySession(id: string): Promise<void> {
  await fetch(`/sessions/${id}`, { method: 'DELETE' })
}

/** 会话详情（含代码工作区状态）。只读状态，不会触发建工作区。 */
export async function getSession(id: string): Promise<SessionDetail> {
  const r = await fetch(`/sessions/${id}`)
  if (!r.ok) throw new Error(`会话详情失败：${r.status}`)
  return r.json()
}

/** 准备代码工作区并**等它就绪**；幂等，已就绪立刻返回。失败抛错 —— 重试就是再调一次。 */
export async function ensureWorkspace(id: string): Promise<void> {
  const r = await fetch(`/sessions/${id}/workspace`, { method: 'POST' })
  if (!r.ok) throw new Error(`代码工作区准备失败：${r.status}`)
}

export async function getRecords(
  id: string,
  beforeId?: number | null,
  limit = 20,
): Promise<RecordPage> {
  const q = new URLSearchParams({ limit: String(limit) })
  if (beforeId) q.set('before_id', String(beforeId))
  const r = await fetch(`/sessions/${id}/records?${q}`)
  if (!r.ok) throw new Error(`记录加载失败：${r.status}`)
  return r.json()
}

/** 该会话最新方案的快照；没有方案返回 null。 */
export async function getPlan(id: string): Promise<PlanInfo | null> {
  const r = await fetch(`/sessions/${id}/plan`)
  if (!r.ok) throw new Error(`方案加载失败：${r.status}`)
  return r.json()
}

/** 当前待回答的意图澄清（刷新后恢复弹窗用）；没有就是 null。 */
export async function getClarification(id: string): Promise<ClarificationInfo | null> {
  const r = await fetch(`/sessions/${id}/clarification`)
  if (!r.ok) throw new Error(`澄清状态加载失败：${r.status}`)
  return r.json()
}

/**
 * 该会话的仓库记忆全文（**只读**）。
 *
 * 记忆正文只在 coding / review 的收尾由主控写，文件头由平台每轮开工前按 PR 实况重算 ——
 * 所以这里只有读，没有写。
 */
export async function getMemory(id: string): Promise<MemorySnapshot> {
  const r = await fetch(`/sessions/${id}/memory`)
  if (!r.ok) throw new Error(await errorDetail(r, `记忆加载失败：${r.status}`))
  return r.json()
}

/**
 * 本仓库的 PR 列表（右栏「PR」标签）。

 * **查不到会抛错**（后端 502 + 原因），不是返回空列表 —— 「一个 PR 都没有」和「这次没查成」
 * 在界面上必须是两句话，所以调用方要把错误单独显示，别当成空态。
 */
export async function getPulls(id: string): Promise<PullList> {
  const r = await fetch(`/sessions/${id}/pulls`)
  if (!r.ok) throw new Error(await errorDetail(r, `PR 查询失败：${r.status}`))
  return r.json()
}

/**
 * 该会话的日志尾部（按时间合并了接口日志与运行日志）。

 * `level` 是**阈值**（给 `WARNING` 就是 WARNING 及以上），不是精确匹配。
 */
export async function getLogs(
  id: string,
  options: { limit?: number; level?: string; q?: string; withHttp?: boolean } = {},
): Promise<LogPage> {
  const q = new URLSearchParams()
  if (options.limit) q.set('limit', String(options.limit))
  if (options.level) q.set('level', options.level)
  if (options.q) q.set('q', options.q)
  if (options.withHttp) q.set('with_http', 'true')
  const query = q.toString()
  const r = await fetch(`/sessions/${id}/logs${query ? `?${query}` : ''}`)
  if (!r.ok) throw new Error(await errorDetail(r, `日志加载失败：${r.status}`))
  return r.json()
}

/** 下载这个会话的日志（整份 .txt 附件）。交给浏览器处理 `Content-Disposition`。 */
export function logsDownloadUrl(id: string): string {
  return `/sessions/${id}/logs/download`
}

/**
 * 一个执行主角在本会话的累计用量。
 *
 * `key` 是固定归类名（`main` / `planner` / `coder` / `reviewer` / `internal` / `intent`），
 * 中文标签由面板自己映射 —— 接口不掺展示文案（与 PR 的 `state` 码同一口径）。
 */
export interface UsageActor {
  key: string
  input: number
  output: number
  calls: number
  total: number
}

/**
 * 本会话的 token 消耗（右栏「Token」标签）。
 *
 * `rounds > rounds_with_usage` 时**必须**在界面上说明「其中 N 轮没有用量数据」：
 * 这些轮次是本功能上线前跑的，把缺数据当 0 消耗是最容易看错的一种错。
 *
 * 字段名与接口一致（snake_case）—— 后端直接序列化，改名的活在前端做只会多一处会漂的
 * 地方，而且**漂了不报错**（读一个不存在的键得到 undefined，界面静默地少显示一句）。
 */
export interface UsageSnapshot {
  totals: UsageActor
  actors: UsageActor[]
  /**
   * **主控按形态**拆出来的用量（qa / analysis / plan / review / coding / rework）。
   * 子智能体不在这里 —— 它们本来就是独立的执行主角（见 `actors`）。
   */
  by_form: Record<string, UsageActor>
  rounds: number
  rounds_with_usage: number
  rounds_interrupted: number
  /** 平台没回用量的调用次数 —— 账上的缺口，如实显示而不是抹掉 */
  missing: number
}

/**
 * 该会话的 token 消耗（**聚合视图，只读**）。
 *
 * 数据源是各轮记录里的 `extra.usage`，由后端在轮末写入、按执行主角分类；换会话时
 * 重新拉一次即可（本轮的账在这一轮结束时落库，面板不需要跟着流实时变）。
 */
export async function getUsage(id: string): Promise<UsageSnapshot> {
  const r = await fetch(`/sessions/${id}/usage`)
  if (!r.ok) throw new Error(await errorDetail(r, `用量加载失败：${r.status}`))
  return r.json()
}

/* -- 模型配置（按智能体，2026-10-13）--------------------------------------- */

/**
 * 「模型配置」里的一行：按**智能体**配，键是 `qa` / `analysis` / `plan` / `review` /
 * `coding`，外加 `unified`（统一模型）/ `fallback`（兜底）/ `classifier`（意图识别的大模型那一关）。
 *
 * - `api_key_masked` 是**打码值**（`sk-…e2b7`）：后端永远不回明文；写回时留空＝不改；
 * - `source`：`db`（库里这一行填过）/ `unset`（没配）。**没有出厂默认**（2026-10-15）——
 *   没配就是空的，要回答「现在真的用什么」看 `effective`；
 * - `context_window`：0 ＝ 没填（留空＝按模型查 → 查不到用保守值）；界面「测试连接」读到
 *   就帮你填上（那一格按 K / M 显示）。
 */
export interface ModelConfigRow {
  key: string
  label: string
  /** 属于设置页的哪一栏：`agents` / `global` / `intent` */
  section: string
  /** 这一行服务哪些角色（`plan` 行 = `plan` + `planner`）—— 「编码」那一组靠它按角色画 */
  roles: string[]
  /** 界面「提供商」那一栏选中的是谁（后端按 base_url 反推；不存库，纯界面提示） */
  provider: string
  /** 供应商被锁死（界面上不让改）：JEV 那一行锁 `typesafe` */
  provider_locked: boolean
  base_url: string
  model: string
  api_key_masked: string
  has_key: boolean
  context_window: number
  source: string
  /** 现在真的用谁（本行 → 统一模型兜底 → 没配）—— 列表上那句「生效值」 */
  effective: ModelConfigEffective | null
}

/** 一个角色现在真的用什么（设置页的「生效值」）。 */
export interface ModelConfigEffective {
  role: string
  /** 真正取值的那一行（被「统一模型」顶掉时就是 `unified`） */
  row: string
  model: string
  window: number
  /** 那个窗口哪来的：`row`（行内手填）/ `model`（按模型查到的）/ `fallback`（我们的保守值）/
   *  `none`（没配）—— 界面只回显 row / model（兜底值不是厂商给的数，回显出来会误导） */
  window_source: string
  base_url: string
  has_key: boolean
  /** `row`（这一行配的）/ `unified`（**这一行没配**，走统一模型兜底）/ `unset`（没配，用不了） */
  source: string
}

/** 「编码」那一组的一个角色：落在哪一行、是不是复用别人的行。 */
export interface CodingGroupRole {
  role: string
  label: string
  row: string
  reused: boolean
}

/** 设置页左菜单里的一小节（后端下发，前端不硬编码行名）。 */
export interface SectionOption {
  key: string
  label: string
}

/**
 * 厂商目录里的一项（后端存在库里的表 `model_providers`，新库按 `config/model_providers.toml` 灌）。
 *
 * - `base_url`：选中它时带出的地址（可改）——这是「少打字」，不是「改默认值」；
 * - `ui_group` / `sort_order`：下拉的分组与顺序，**后端已排好**，界面照画即可；
 * - `default_models`：拉不到实时清单时的**内置候选**（可能过时，界面要标出来）。
 *
 * 目录是后端**现读库**的：改库 + 刷新界面就生效，不用重启后端。
 */
export interface ProviderOption {
  key: string
  label: string
  base_url: string
  ui_group: string
  sort_order: number
  /** 拉清单的路径：相对路径拼在 base_url 后面，**也可以写绝对 URL**（qwen 的清单在另一个根下） */
  models_path: string
  /** 鉴权头怎么写（多数是 `Authorization` + `Bearer `；有的网关是 `x-api-key`） */
  auth_header: string
  auth_prefix: string
  /** 1 启用 / 0 停用（停用的不进下拉，但在「提供商」栏里看得到、能改回来） */
  status: number
  /** 这家官方有没有 `/models`（只是标注；「测试连接」不看它） */
  is_dynamic_model_list: boolean
}

/** 写一行提供商目录（整行覆盖；键在 URL 里、不可改）。 */
export interface ProviderUpsert {
  label: string
  base_url: string
  ui_group: string
  sort_order: number
  models_path: string
  auth_header: string
  auth_prefix: string
  status: number
  is_dynamic_model_list: boolean
}

export interface ModelConfigSnapshot {
  rows: ModelConfigRow[]
  providers: ProviderOption[]
  /** 左菜单这一栏里的小节（key + 中文名） */
  sections: SectionOption[]
  /** 「编码」那一组按角色展示：角色 → 落在哪一行、是不是复用 */
  coding_group: CodingGroupRole[]
  /** 意图识别走哪一关：`jev` / `llm`（**严格二选一**，不互相兜底） */
  intent_mode: string
  /** JEV 这一侧配齐了没有（地址 + key）：false 时选了 JEV 会落关键字口径 */
  jev_configured: boolean
}

export interface ProviderProbeResult {
  ok: boolean
  models: string[]
  /** 上面那份清单哪来的：`live`＝刚从厂商拉的；`builtin`＝目录里的内置候选（可能过时） */
  source: string
  context_window: number
  note: string
  error: string
}

export async function getModelConfig(): Promise<ModelConfigSnapshot> {
  const r = await fetch('/model-config')
  if (!r.ok) throw new Error(await errorDetail(r, `读取模型配置失败：${r.status}`))
  return r.json()
}

/** 保存：只提交**改过**的字段（不传＝不改；`api_key` 传空串＝清掉）。 */
export async function putModelConfig(
  rows: Record<string, { base_url?: string; model?: string; api_key?: string; context_window?: number }>,
  intentMode?: string,
): Promise<ModelConfigSnapshot> {
  const r = await fetch('/model-config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(intentMode ? { rows, intent_mode: intentMode } : { rows }),
  })
  if (!r.ok) throw new Error(await errorDetail(r, `保存模型配置失败：${r.status}`))
  return r.json()
}

/** 清掉一行的覆盖值 ＝ 恢复出厂默认。 */
export async function deleteModelConfig(key: string): Promise<ModelConfigSnapshot> {
  const r = await fetch(`/model-config/${encodeURIComponent(key)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(await errorDetail(r, `恢复默认失败：${r.status}`))
  return r.json()
}

/**
 * 「测试连接」：拿这套 base_url/key 拉那家的模型清单（一次验连通性 + 给模型下拉 + 读窗口）。
 * **只在点这个按钮时拉**：不轮询、保存时也不拉。`provider` 决定去哪拉、怎么鉴权。
 */
export async function probeProvider(
  baseUrl: string,
  apiKey: string | null,
  keyFrom?: string,
  provider?: string,
): Promise<ProviderProbeResult> {
  const r = await fetch('/model-config/probe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      base_url: baseUrl,
      api_key: apiKey,
      key_from: keyFrom ?? null,
      provider: provider ?? null,
    }),
  })
  if (!r.ok) throw new Error(await errorDetail(r, `探测失败：${r.status}`))
  return r.json()
}

/**
 * 一格**运行期配置**（设置页「基本设置」里的代码缓存配置 / 上下文窗口配置）。
 *
 * `source=db` ＝ 这一格在界面里改过；`default` ＝ 还在用 `.env` / 代码默认值。
 * 这些值**改完立刻生效**（不用重启）：后端把值赋回 `Settings`，而主智能体每轮现建。
 */
export interface RuntimeSettingItem {
  key: string
  label: string
  group: string
  /** 值的类型：界面据此选输入框与校验 */
  kind: 'str' | 'int' | 'float' | string
  value: string | number | null
  source: string
}

export interface RuntimeSettingsSnapshot {
  items: RuntimeSettingItem[]
}

export async function getAppSettings(): Promise<RuntimeSettingsSnapshot> {
  const r = await fetch('/app-settings')
  if (!r.ok) throw new Error(await errorDetail(r, `读取运行期配置失败：${r.status}`))
  return r.json()
}

/** 保存：**只提交改过的键**（不传＝不改）。值非法后端回 400，界面上原样显示那句话。 */
export async function putAppSettings(
  items: Record<string, string | number>,
): Promise<RuntimeSettingsSnapshot> {
  const r = await fetch('/app-settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
  if (!r.ok) throw new Error(await errorDetail(r, `保存运行期配置失败：${r.status}`))
  return r.json()
}

/** 取错误响应里的 `detail`；不是 JSON 或没有该字段就退回兜底文案。 */
/**
 * 供应商的**模型详情**（表 `provider_models` 的一行）：名字 / 类型 / 上下文长度 / 来源。
 *
 * 上下文长度挂**模型**而不是供应商：同一家 `glm-5.3` 是 1M、`glm-4.6` 是 128k。
 * `context_window` 为 0 ＝ 还不知道（界面照实显示「未知」）。
 */
export interface ProviderModelItem {
  provider: string
  model: string
  /** chat / embedding / rerank / image / video / audio / other */
  model_type: string
  context_window: number
  /** probe（刷新时读到的）/ manual / seed */
  source: string
  status: number
}

export interface ProviderModelList {
  provider: string
  items: ProviderModelItem[]
  /** 这次刷新从厂商那儿读回来几条 */
  refreshed?: number
  /** 刷新时的一句话（例如「这个入口没回窗口」） */
  note?: string
}

export async function getProviderModels(provider: string): Promise<ProviderModelList> {
  const r = await fetch(`/model-config/providers/${encodeURIComponent(provider)}/models`)
  if (!r.ok) throw new Error(await errorDetail(r, `读取模型清单失败：${r.status}`))
  return r.json()
}

/**
 * 提供商目录**全量**（含停用）—— 设置页「提供商」那一栏的列表用它。
 * （快照里的 `providers` 是「只含启用」的那份，给弹窗下拉。）
 */
export async function getProviders(): Promise<ProviderOption[]> {
  const r = await fetch('/model-config/providers')
  if (!r.ok) throw new Error(await errorDetail(r, `读取提供商目录失败：${r.status}`))
  return r.json()
}

/** 写一行提供商（**整行覆盖**；键在 URL 里、不可改）。回的是目录全量。 */
export async function putProvider(
  key: string,
  body: ProviderUpsert,
): Promise<ProviderOption[]> {
  const r = await fetch(`/model-config/providers/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(await errorDetail(r, `保存提供商失败：${r.status}`))
  return r.json()
}

/**
 * **点按钮才刷**：拿这家的地址去调它的清单接口并写进表（界面口径：不主动刷新）。
 *
 * `baseUrl` 传**这一行手填的地址**：代理 / 自建网关与目录里的默认地址不是一回事，
 * 不传才退回目录里的默认地址 —— 「测试连接」用的就是你填的那个地址，两处口径必须一致。
 */
export async function refreshProviderModels(
  provider: string,
  baseUrl?: string,
): Promise<ProviderModelList> {
  const query = baseUrl ? `?base_url=${encodeURIComponent(baseUrl)}` : ''
  const r = await fetch(
    `/model-config/providers/${encodeURIComponent(provider)}/models/refresh${query}`,
    { method: 'POST' },
  )
  if (!r.ok) throw new Error(await errorDetail(r, `刷新模型清单失败：${r.status}`))
  return r.json()
}

/** 手填 / 手改一个模型（窗口 / 类型 / 启停）。 */
export async function putProviderModel(
  provider: string,
  body: { model: string; model_type?: string; context_window?: number; status?: number },
): Promise<ProviderModelList> {
  const r = await fetch(`/model-config/providers/${encodeURIComponent(provider)}/models`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(await errorDetail(r, `保存模型失败：${r.status}`))
  return r.json()
}

/** 取一个文件在某次改动里的 diff（服务端在本地缓存里算，已脱敏 + 显式截断）。 */
export async function getRepoDiff(
  sessionId: string,
  base: string,
  head: string,
  path: string,
): Promise<{ path: string; text: string; truncated: boolean }> {
  const query = new URLSearchParams({ base, head, path })
  const r = await fetch(`/sessions/${encodeURIComponent(sessionId)}/repo/diff?${query}`)
  if (!r.ok) throw new Error(await errorDetail(r, `读取改动失败：${r.status}`))
  return r.json()
}

async function errorDetail(resp: Response, fallback: string): Promise<string> {
  try {
    const body = await resp.json()
    return typeof body?.detail === 'string' ? body.detail : fallback
  } catch {
    return fallback
  }
}

/**
 * 发一条消息，拿到本轮的事件流响应。本轮已在跑时后端返回 409。
 *
 * `signal` 用于用户点「终止」时本地断流：光断流是停不掉后端的（运行不挂在请求上），
 * 它是配合 `cancelChat` 一起用的 —— 后端负责真停，这里负责立刻松手。
 */
export async function postChat(
  id: string,
  message: string,
  signal?: AbortSignal,
  options?: {
    clarificationId?: string
    choice?: number | string
    answers?: ClarificationAnswer[]
  },
): Promise<Response> {
  const body: Record<string, unknown> = { message }
  if (options?.clarificationId) body.clarification_id = options.clarificationId
  if (options?.choice !== undefined) body.choice = options.choice
  if (options?.answers?.length) body.answers = options.answers
  const resp = await fetch(`/chat/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  // 409 有两种成因：本轮已在跑（接回去就行），或会话已失效 / 缺仓库信息（只能报错）。
  // 状态码一样，区别只在服务端那句 detail 里 —— 原样带出来，别拿一句固定的「本轮仍在进行中」盖掉。
  if (resp.status === 409) {
    throw new Error(await errorDetail(resp, '本轮仍在进行中，等它结束或刷新页面接回。'))
  }
  if (!resp.ok || !resp.body) throw new Error(`聊天失败：${resp.status}`)
  return resp
}

/** 只订阅、不发起：带着游标接回本轮的事件流（刷新 / 断线后重连用）。 */
export async function resumeChat(
  id: string,
  after: number,
  signal?: AbortSignal,
): Promise<Response> {
  const resp = await fetch(`/chat/${id}/stream?after=${after}`, { signal })
  if (!resp.ok || !resp.body) throw new Error(`接回本轮失败：${resp.status}`)
  return resp
}

/** 终止该会话正在跑的那一轮。返回后端是否真停了一轮（没在跑就是 false，幂等）。 */
export async function cancelChat(id: string): Promise<boolean> {
  const r = await fetch(`/sessions/${id}/cancel`, { method: 'POST' })
  if (!r.ok) throw new Error(`终止失败：${r.status}`)
  return Boolean((await r.json()).cancelled)
}

// ---------------------------------------------------------------- 代码面板
// 宿主机自己 clone 的那份仓库缓存（与沙箱无关）：分支、目录、文件。

export interface RepoBranch {
  name: string
  sha: string
  updated_at: string
  subject: string
  is_default: boolean
}

export interface RepoStatus {
  /** 本机有没有这份缓存；没有时 `branches` 一定是空的。 */
  cloned: boolean
  /** 只在同步回执里有：这次是把仓库下载下来（true）还是只做了更新（false）。 */
  cloned_now?: boolean
  default_branch: string
  branches: RepoBranch[]
  /** 上次成功同步的时间；从没同步过是 null。 */
  fetched_at: string | null
  /** 探测到的 git 可执行文件；为 null 表示本机没装 git（面板据此给提示）。 */
  git: string | null
}

export interface RepoTreeEntry {
  name: string
  /** 相对仓库根的完整路径，直接拿去请求文件内容。 */
  path: string
  type: string
  size: number | null
}

export interface RepoTree {
  ref: string
  path: string
  entries: RepoTreeEntry[]
}

export interface RepoFile {
  path: string
  ref: string
  /** 正文；`binary` / `too_big` 为真时是 null。 */
  text: string | null
  size: number
  binary: boolean
  too_big: boolean
}

/** 缓存状态 + 分支（不联网）。 */
export async function getRepoStatus(id: string): Promise<RepoStatus> {
  const r = await fetch(`/sessions/${id}/repo`)
  if (!r.ok) throw new Error(await errorDetail(r, `代码面板不可用：${r.status}`))
  return r.json()
}

/** 下载或更新这份缓存（幂等；首次会 clone，之后是 fetch）。 */
export async function syncRepo(id: string): Promise<RepoStatus> {
  const r = await fetch(`/sessions/${id}/repo/sync`, { method: 'POST' })
  if (!r.ok) throw new Error(await errorDetail(r, `同步失败：${r.status}`))
  return r.json()
}

/** 列一层目录（`path` 留空是根目录）。 */
export async function getRepoTree(id: string, ref: string, path: string): Promise<RepoTree> {
  const q = new URLSearchParams({ ref, path })
  const r = await fetch(`/sessions/${id}/repo/tree?${q}`)
  if (!r.ok) throw new Error(await errorDetail(r, `目录读取失败：${r.status}`))
  return r.json()
}

/** 读一个文件的正文（`ref` 留空用默认分支）。 */
export async function getRepoFile(id: string, ref: string, path: string): Promise<RepoFile> {
  const q = new URLSearchParams({ ref, path })
  const r = await fetch(`/sessions/${id}/repo/file?${q}`)
  if (!r.ok) throw new Error(await errorDetail(r, `文件读取失败：${r.status}`))
  return r.json()
}

/**
 * 消费一条 SSE 流到结束，把 `cursor.last` 推进到已收到的最后一帧。
 *
 * 帧号取服务端下发的 `id:`，不由本地计数 —— 本地数在某帧解析失败被跳过时会错位，
 * 重连之后就会重复渲染一段正文。游标由调用方持有，中途抛错也拿得到断点。
 */
async function readStream(
  resp: Response,
  onEvent: (ev: ChatEvent) => void,
  cursor: { last: number },
): Promise<boolean> {
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let sawDone = false
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      // SSE 换行可能是 \n 或 \r\n（sse-starlette 默认 \r\n），两者都兼容
      const blocks = buf.split(/\r?\n\r?\n/)
      buf = blocks.pop() ?? ''
      for (const block of blocks) {
        let eventName = 'message'
        let id = 0
        const dataLines: string[] = []
        for (const line of block.split(/\r?\n/)) {
          if (line.startsWith('id:')) id = Number(line.slice(3).trim()) || 0
          else if (line.startsWith('event:')) eventName = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (!dataLines.length) continue
        if (id) cursor.last = id
        const raw = dataLines.join('\n')
        if (!raw) continue
        let obj: any
        try {
          obj = JSON.parse(raw)
        } catch {
          continue
        }
        if (eventName === 'error') onEvent({ type: 'error', error: obj.error ?? 'unknown' })
        else {
          const ev = obj as ChatEvent
          if (ev.type === 'done') sawDone = true
          onEvent(ev)
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
  return sawDone
}

/**
 * 读一条流；只有收到 `done` 才算成功结束。
 *
 * 长任务可能被代理或连接层以**正常 EOF** 截断。此时 `reader.read()` 不会抛错，
 * 但本轮并没有完成；必须带着游标再接一次。两次都拿不到 `done` 时返回 false，
 * 由调用方用持久化记录兜底，不能把半截流当完成。
 */
export async function consumeStream(
  id: string,
  first: Response,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<boolean> {
  const cursor = { last: 0 }
  let resp = first
  for (let attempt = 0; ; attempt++) {
    try {
      if (await readStream(resp, onEvent, cursor)) return true
    } catch (e) {
      // 用户自己点的终止：本地主动断的，不是传输故障 —— 再重连一次就白跑一个请求
      if (signal?.aborted) throw e
      if (attempt >= 1) throw e
      resp = await resumeChat(id, cursor.last, signal)
      continue
    }
    if (attempt >= 1) return false
    resp = await resumeChat(id, cursor.last, signal)
  }
}
