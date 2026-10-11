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
  created_at: string
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

export interface ChatEvent {
  type: 'ai' | 'tool' | 'todos' | 'plan' | 'done' | 'error'
  content?: string
  name?: string
  /** `tool` 帧：只有委派子智能体的 `task` 带，值是角色名（`coder` / `reviewer` / `planner`） */
  agent?: string
  /** `todos` 帧：**整棵**待办树的全量内容（每次都是整体替换，不是增量） */
  items?: TodoItem[]
  error?: string
  plan?: PlanInfo
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

export async function getRecords(id: string): Promise<RecordItem[]> {
  const r = await fetch(`/sessions/${id}/records`)
  if (!r.ok) throw new Error(`记录加载失败：${r.status}`)
  return r.json()
}

/** 该会话最新方案的快照；没有方案返回 null。 */
export async function getPlan(id: string): Promise<PlanInfo | null> {
  const r = await fetch(`/sessions/${id}/plan`)
  if (!r.ok) throw new Error(`方案加载失败：${r.status}`)
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

/** 取错误响应里的 `detail`；不是 JSON 或没有该字段就退回兜底文案。 */
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
): Promise<Response> {
  const resp = await fetch(`/chat/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
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
): Promise<void> {
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
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
        else onEvent(obj as ChatEvent)
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** 读一条流；传输中断就带着游标重连一次（本轮跑完了也能接，服务端会把尾巴补完）。 */
export async function consumeStream(
  id: string,
  first: Response,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const cursor = { last: 0 }
  let resp = first
  for (let attempt = 0; ; attempt++) {
    try {
      await readStream(resp, onEvent, cursor)
      return
    } catch (e) {
      // 用户自己点的终止：本地主动断的，不是传输故障 —— 再重连一次就白跑一个请求
      if (signal?.aborted) throw e
      if (attempt >= 1) throw e
      resp = await resumeChat(id, cursor.last, signal)
    }
  }
}
