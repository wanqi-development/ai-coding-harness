<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  cancelChat,
  consumeStream,
  createSession,
  destroySession,
  ensureWorkspace,
  getRecords,
  getClarification,
  getSession,
  listSessions,
  postChat,
  resumeChat,
  type ChatEvent,
  type ClarificationInfo,
  type PlanInfo,
  type PlanStep,
  type RecordItem,
  type SessionItem,
  type TodoItem,
  type UsageContext,
  type UsageDetail,
  type ChangesInfo,
  type WorkspaceStatus,
} from './lib/api'
import { renderMarkdown } from './lib/markdown'
import MemoryPanel from './components/panels/MemoryPanel.vue'
import LogPanel from './components/panels/LogPanel.vue'
import PrPanel from './components/panels/PrPanel.vue'
import UsagePanel from './components/panels/UsagePanel.vue'
import RepoPanel from './components/panels/RepoPanel.vue'
import ChangesPanel from './components/panels/ChangesPanel.vue'
import SettingsPage from './components/settings/SettingsPage.vue'
import ClarificationDialog from './components/ClarificationDialog.vue'
import { defaultPanelTab } from './lib/prefs'
import { usePanelWidth } from './lib/splitter'
import { splitLongText } from './lib/streamblocks'
import { shouldShowTodos } from './lib/todos'
import { readView, watchView, writeView, type AppView, type SettingsPanel } from './lib/viewState'

/** AI 消息里的三种块；用户消息恒为单个 text 块。待办树固定渲染在回复最下边。 */
type Part =
  /** `sealed` = 这块已经写完，不会再变（流式分块用，见 streamblocks.ts）。 */
  | { kind: 'text'; content: string; sealed?: boolean }
  /** 思考：只在前端存在、不落库；与正文同处 `parts`，按到达顺序交错。 */
  | { kind: 'reasoning'; agent: string; text: string }
  | { kind: 'todos'; items: TodoItem[] }
  /**
   * 方案：以**一条链接**出现在聊天流里，点开就地展开。
   *
   * 原来方案只活在右侧「计划」面板里 —— 面板取消之后，方案改由聊天流承载：实时那帧来自
   * SSE 的 `plan` 事件，刷新后那条来自落库的 `type='plan'` 记录，两边形状一样。
   */
  | { kind: 'plan'; plan: PlanInfo }

interface Message {
  role: 'user' | 'assistant'
  parts: Part[]
  /** 历史记录 id；实时消息没有（翻页游标只认历史记录）。 */
  recordId?: number
  /** 该条消息的时间，ISO 串（历史来自记录，实时取本地钟）；流没结束时是空串。 */
  at: string
  /**
   * 这一轮的 token 账（只有 AI 消息有）。历史来自记录里的 `usage` 字段，实时来自 SSE 的
   * `usage` 帧 —— **两者同一份形状**，所以一条渲染路径。
   *
   * 注意它是**这一段对话**的消耗，不是「这一条离窗口上限有多近」：子智能体用的是别的模型
   * （coder 是 1M 窗口），它们的量也算在这一条上。要看压力看输入框上方那条。
   */
  usage?: UsageDetail | null
  /**
   * 这一轮改了什么（**建成 PR 才有**）。历史来自记录里的 `changes`，实时来自 SSE 的
   * `changes` 帧 —— 同一份形状，一条渲染路径。数据是服务端在**本地代码缓存**里算的，
   * 所以沙箱被回收之后点开某个文件照样看得到 diff。
   */
  changes?: ChangesInfo | null
  /**
   * 这一轮从什么时候开始跑的（epoch 毫秒）—— 用来在回复最上面显示 `working… 12s`。
   *
   * 两个来源：**自己发的**那轮就是发送时刻；**刷新/重新打开时正跑着的那轮**取
   * **用户那条消息的时间**（用户口径：「未打开场景下突然打开，从现在开始计时 + 从用户发的
   * 消息算」）—— 于是数字是「这一轮已经跑了多久」，即使我们才刚开始看。
   */
  startedAt?: number
  /** 跑完之后冻结下来的总时长（毫秒）；没跑完就是 undefined */
  elapsedMs?: number
}

/** 账目里一组计数的可读写法：`输入 12.3k · 输出 0.9k · 8 次调用`。 */
function usageSummary(u: UsageDetail): string {
  const k = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))
  const calls = Object.values(u.by_actor ?? {}).reduce((sum, a) => sum + (a.calls ?? 0), 0)
  return `输入 ${k(u.totals.input)} · 输出 ${k(u.totals.output)} · ${calls} 次调用`
}

/** 角色名（账里的归类名 → 中文），与 Token 面板同一张表。 */
const USAGE_ACTOR_LABEL: Record<string, string> = {
  main: '主控',
  planner: 'planner',
  coder: 'coder',
  reviewer: 'reviewer',
  internal: '摘要',
  intent: '意图分类',
}

function usageActorLabel(key: string): string {
  return USAGE_ACTOR_LABEL[key] ?? key
}

/** 展开的角标（消息下标 → 是否展开明细）。与待办折叠、方案步骤同一套做法。 */
const usageOpen = ref<Record<number, boolean>>({})

function toggleUsage(index: number) {
  usageOpen.value[index] = !usageOpen.value[index]
}

/**
 * 右栏里的「本轮改动」内嵌页：`null` ＝ 没开；有值 ＝ 盖在当前标签页上。
 * 再点同一个角标会关掉它（返回原来的标签）。
 */
const changesPage = ref<ChangesInfo | null>(null)

function openChanges(info: ChangesInfo) {
  if (changesPage.value === info && !panelCollapsed.value) {
    changesPage.value = null
    return
  }
  changesPage.value = info
  panelCollapsed.value = false
}

const REASONING_AGENT_LABEL: Record<string, string> = {
  main: '主控',
  planner: 'planner',
  coder: 'coder',
  reviewer: 'reviewer',
}

function reasoningAgentLabel(agent: string): string {
  return REASONING_AGENT_LABEL[agent] ?? (agent || '子智能体')
}

/** 思考块：连续同一角色并进上一块，换了角色就新起一块（与正文同处 `parts`，保持到达顺序）。 */
function pushReasoning(parts: Part[], agent: string, text: string) {
  if (!text) return
  const last = parts[parts.length - 1]
  if (last?.kind === 'reasoning' && last.agent === agent) last.text += text
  else parts.push({ kind: 'reasoning', agent, text })
}

/** 明细行：`主控 12.3k / coder 4.0k`（按用量从大到小，最多显示 4 项）。 */
function usageRows(u: UsageDetail): { label: string; value: number }[] {
  return Object.entries(u.by_actor ?? {})
    .map(([key, item]) => ({
      label: usageActorLabel(key),
      value: (item.input ?? 0) + (item.output ?? 0),
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 4)
}

/**
 * 「这一轮改了什么」：摘要那一行的文案 + 文件列表的展开状态。
 *
 * 折叠状态与「看过的 diff」都按**消息下标**记（与 token 明细同一个口径：窗口往前滑时
 * 下标不能跟着变，所以用绝对下标）。
 */
function changesSummary(info: ChangesInfo): string {
  const files = info.total ?? info.files.length
  const added = info.files.reduce((sum, f) => sum + f.added, 0)
  const deleted = info.files.reduce((sum, f) => sum + f.deleted, 0)
  return `改动 ${files} 个文件 · +${added} −${deleted}`
}


/**
 * 这一轮跑了多久：`working… 12s` 的依据。
 *
 * - 跑着的消息且我们知道起点 → 用**现在**减起点，所以数字会自己走；
 * - 跑完的消息 → 用冻结下来的 `elapsedMs`（刷新回放时也照实显示）；
 * - 没有起点（老的记录）→ 不显示。
 *
 * 「不需要很精确」（用户口径）：秒级、不补零、超过一分钟给 `1m03s`。
 */
/** 每秒走一下的「现在」——只在有活在跑的时候才开定时器（见下面的 watch）。 */
const nowTick = ref(Date.now())

function elapsedLabel(m: Message): string {
  const total = m.elapsedMs ?? (m.startedAt ? nowTick.value - m.startedAt : 0)
  if (!total || total < 0) return ''
  const seconds = Math.round(total / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, '0')}s`
}

/**
 * 上下文压力（输入框上方那条）。
 *
 * 取**最近一次拿到的账**：本轮刚开始还没账时留上一轮的，刷新页面后由记录回填 ——
 * 否则那条提示会一闪一闪。
 */
const latestContext = computed<UsageContext | null>(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const ctx = messages.value[i].usage?.context
    if (ctx && ctx.window > 0) return ctx
  }
  return null
})

/**
 * 压力档位：低于告警线＝中性，过了告警线＝警告色，过了危险线＝危险色。
 *
 * 两条线**由服务端随上下文那一格发下来**（设置页可改：上下文窗口配置里的那两格），
 * 界面不写死 —— 阈值只有一个出处。旧记录没这两格时按 0.6 / 0.8 兜（与服务端默认值一致）。
 */
const contextLevel = computed(() => {
  const ctx = latestContext.value
  if (!ctx) return 'none'
  const alert = ctx.alert_ratio ?? 0.8
  const warn = ctx.warn_ratio ?? 0.6
  if (alert > 0 && ctx.ratio >= alert) return 'alert'
  if (warn > 0 && ctx.ratio >= warn) return 'warn'
  return 'none'
})

const contextPercent = computed(() => {
  const ctx = latestContext.value
  if (!ctx || ctx.window <= 0) return 0
  return Math.min(100, Math.round(ctx.ratio * 100))
})

/**
 * 环图的悬浮说明（也是无障碍读屏那句话）。
 *
 * 原来这些字是**常驻**在输入框上方一条横条里的；2026-10-14 换成发送框右边的小环图
 * （用户口径），文字挪进 `title` —— 信息没丢，只是不再天天占一行。
 */
const contextTitle = computed(() => {
  const ctx = latestContext.value
  if (!ctx) return ''
  const num = (n: number) => n.toLocaleString('en-US')
  const used = ctx.input ?? ctx.max_input ?? 0
  const parts = [`上下文已用 ${contextPercent.value}%（${num(used)} / ${num(ctx.window)}）`]
  if (ctx.ratio >= (ctx.alert_ratio ?? 0.8)) {
    parts.push('接近压缩线：下一条消息可能触发压缩，回复会变短')
  }
  if (ctx.compressed) parts.push(`已压缩 ${ctx.compressed} 次`)
  return parts.join('；')
})

const pad2 = (n: number) => String(n).padStart(2, '0')

/**
 * 消息时间：今天只给 `HH:mm`，今年给 `MM-dd HH:mm`，跨年才补上年份。
 *
 * 「今天」按**本地日历日**判（不是 24 小时差）；一律 24 小时制。解析不出就返回
 * 空串，调用处据此不渲染 —— 正在流式、时间还没定下来的那条正好落这里。
 */
function formatTime(iso: string): string {
  if (!iso) return ''
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const hm = `${pad2(at.getHours())}:${pad2(at.getMinutes())}`
  const now = new Date()
  if (
    at.getFullYear() === now.getFullYear() &&
    at.getMonth() === now.getMonth() &&
    at.getDate() === now.getDate()
  ) {
    return hm
  }
  const md = `${pad2(at.getMonth() + 1)}-${pad2(at.getDate())}`
  return at.getFullYear() === now.getFullYear() ? `${md} ${hm}` : `${at.getFullYear()}-${md} ${hm}`
}

/**
 * 往末尾追加正文。
 *
 * 只续写在**还没封口**的那一块上：封口块的内容从此不再变，模板里的 `v-memo="[p.content]"`
 * 就会跳过它，流式那一帧只重渲染最后这一小块（切点规则见 streamblocks.ts）。
 */
function pushText(parts: Part[], text: string) {
  if (!text) return
  const last = parts[parts.length - 1]
  if (last?.kind === 'text' && !last.sealed) last.content += text
  else parts.push({ kind: 'text', content: text })
}

/**
 * 待办树：整棵只有一份，每帧整体替换。
 *
 * 模型每次 `write_todos` 给的都是全量清单，追加式的块序列只会留下一串过时快照。
 * 后端也是这么攒的（树排在正文之后），所以这里没有块号、也不需要块号。
 */
function pushTodos(parts: Part[], items: TodoItem[]) {
  for (const p of parts) {
    if (p.kind === 'todos') {
      p.items = items
      return
    }
  }
  parts.push({ kind: 'todos', items })
}

/** 取这条消息里的方案块（没有就是 null）。一条消息最多一块 —— 见 `pushPlan`。 */
function planPart(m: Message): PlanInfo | null {
  for (const p of m.parts) if (p.kind === 'plan') return p.plan
  return null
}

/**
 * 方案块：**同一份方案只占一块**。
 *
 * 方案在实施过程中会被反复改（`updatePlanSteps` 每完成一步就推一帧），追加式渲染会变成
 * 一屏「方案已更新」；所以按「这条消息里已有的那块」就地替换。传 `null` 表示方案被删了，
 * 把块摘掉。
 */
function pushPlan(parts: Part[], info: PlanInfo | null) {
  const at = parts.findIndex((p) => p.kind === 'plan')
  if (!info) {
    if (at >= 0) parts.splice(at, 1)
    return
  }
  if (at >= 0) (parts[at] as { kind: 'plan'; plan: PlanInfo }).plan = info
  else parts.push({ kind: 'plan', plan: info })
}

/** 落库那条方案记录 → 方案块。字段来自后端 `plan_payload`（extra 里带 plan_id/status/steps）。 */
function planFromRecord(r: {
  content: string | null
  summary: string | null
  extra: any
}): PlanInfo | null {
  const extra = r.extra ?? {}
  const planId = Number(extra.plan_id ?? 0)
  if (!planId) return null
  return {
    plan_id: planId,
    title: r.summary ?? null,
    status: String(extra.status ?? 'pending'),
    steps: Array.isArray(extra.steps) ? extra.steps : [],
    content: r.content ?? null,
    updated_at: '',
  }
}

// 方案链接的展开态（按 plan_id 记）。默认收起：它常常只是「这轮做了什么」的索引，
// 正文和步骤都点开才看，免得一屏被方案全文占满。
const planOpen = ref<Record<number, boolean>>({})

function isPlanOpen(id: number): boolean {
  return Boolean(planOpen.value[id])
}

function togglePlan(id: number) {
  planOpen.value = { ...planOpen.value, [id]: !planOpen.value[id] }
}

/* 模板里用的取值助手：都返回安全值，省得在模板里写非空断言。 */
function planTitle(m: Message): string {
  return planPart(m)?.title || '实施计划'
}

function planStatus(m: Message): string {
  return planPart(m)?.status || 'pending'
}

function planSteps(m: Message): PlanStep[] {
  return planPart(m)?.steps ?? []
}

function planMarkdown(m: Message): string {
  return planPart(m)?.content ?? ''
}

function planProgress(m: Message): string {
  const info = planPart(m)
  if (!info || !info.steps.length) return ''
  return `已完成 ${planDone(info)}/${info.steps.length}`
}

/** 取一条消息的待办树（没有就是空数组）。 */
function todosPart(m: Message): TodoItem[] {
  for (const p of m.parts) if (p.kind === 'todos') return p.items
  return []
}

/**
 * 这条消息的待办树要不要画出来。
 *
 * 整棵树全完成、且这一轮已经收尾（`at` 落上了）就不画 —— 跑完留在气泡下面的那份
 * 全绿清单只是噪音。判定规则见 todos.ts。
 */
function showsTodos(m: Message): boolean {
  return shouldShowTodos(todosPart(m), Boolean(m.at))
}

/** 正文块（`Part` 里 text 那一支）。 */
type TextPart = Extract<Part, { kind: 'text' }>

/**
 * 一条消息的「正文 / 思考」流：**连续**的 text 块合成一组（一组渲染成一个气泡），
 * 思考块在运行中原样插在它到达的位置 —— 于是渲染出来就是「思考 → 正文 → 思考 → 正文」；
 * 跑完之后只留正文（思考不占屏、不落库）。
 *
 * 为什么要分组：流式分块（见 streamblocks.ts）会把一段长回复切成多块，块与块之间只是
 * 「这一帧哪些内容要重渲染」的界限，视觉上必须还是一条连续正文 —— 每块各占一个气泡的话，
 * 每 1.5 KB 就会断一次、还多出 16px 间距。待办 / 方案不参与这条流，仍固定在底部渲染。
 */
type FlowEntry =
  | { kind: 'text'; parts: TextPart[] }
  | { kind: 'reasoning'; agent: string; text: string }

function flowEntries(m: Message): FlowEntry[] {
  const out: FlowEntry[] = []
  let current: TextPart[] | null = null
  for (const p of m.parts) {
    if (p.kind === 'reasoning') {
      // 跑完只留正文：思考只用于运行中观察，结束后不占屏、也不落库。
      // **别在这里把 current 置空** —— 跳过思考之后，它前后的正文仍应合成一个
      // Markdown 气泡，而不是被思考切成两段。
      if (m.at) continue
      current = null
      out.push({ kind: 'reasoning', agent: p.agent, text: p.text })
      continue
    }
    if (p.kind !== 'text') {
      current = null
      continue
    }
    if (!current) {
      current = []
      out.push({ kind: 'text', parts: current })
    }
    current.push(p)
  }
  return out
}

/**
 * 一组连续正文拼成一份完整的 Markdown。
 *
 * 流式期间这些块是为了「只重渲染最后一块」而切开的；任务结束时要把它们拼回去，
 * 一次 `renderMarkdown` 渲染整组 —— 否则列表 / 表格会在块边界处断掉。
 */
function groupText(group: TextPart[]): string {
  return group.map((p) => p.content ?? '').join('')
}

/** 历史里的待办归一化：新记录是 `{content, status, children?}`，老记录只有字符串。 */
function todoItems(raw: any): TodoItem[] {
  return (raw ?? []).map((t: any): TodoItem => {
    if (typeof t === 'string') return { content: t, status: 'pending' }
    const item: TodoItem = {
      content: String(t?.content ?? ''),
      status: String(t?.status ?? 'pending'),
    }
    const children = todoItems(t?.children)
    if (children.length) item.children = children
    return item
  })
}

/**
 * 子任务折叠态：key 认的是「哪条消息里的哪一项」，不是条目文本。
 *
 * 按文本记会把文本相同的项串成同一个开关：一轮里可能落下两个同名的兜底容器
 * （子智能体名取不到时都叫「子智能体任务」，见 `stream.py` 的 `_container`），
 * 不同消息里也会出现一模一样的条目 —— 点一个，它们全跟着开合。
 *
 * 点过的记在这里、一直算数；没点过的走默认规则 —— 父项或任一子项还在跑就展开
 * （子智能体的进度正是这时候要看的），跑完自己收起。
 */
const todoOpen = ref<Record<string, boolean>>({})

/** 树内一项的折叠 key：消息下标 + 文本 + 「同文本第几次出现」。 */
function todoKey(m: Message, k: number, msgIndex: number): string {
  const items = todosPart(m)
  const item = items[k]
  let nth = 0
  for (let i = 0; i < k; i++) if (items[i]?.content === item?.content) nth++
  return `${msgIndex}:${item?.content ?? ''}#${nth}`
}

function isTodoOpen(m: Message, k: number, msgIndex: number): boolean {
  const chosen = todoOpen.value[todoKey(m, k, msgIndex)]
  if (chosen !== undefined) return chosen
  const t = todosPart(m)[k]
  return t.status === 'in_progress' || (t.children ?? []).some((c) => c.status === 'in_progress')
}

function toggleTodo(m: Message, k: number, msgIndex: number) {
  const key = todoKey(m, k, msgIndex)
  todoOpen.value = { ...todoOpen.value, [key]: !isTodoOpen(m, k, msgIndex) }
}

/** 折叠按钮上的进度：已完成子任务数 / 子任务总数。 */
function doneCount(t: TodoItem): string {
  const kids = t.children ?? []
  return `${kids.filter((c) => c.status === 'completed').length}/${kids.length}`
}

/** 用户消息恒为单个正文块，取它的纯文本。 */
function userText(m: Message): string {
  const first = m.parts[0]
  return first?.kind === 'text' ? first.content : ''
}

/** 状态位只认三种样式；认不出来的一律按 pending 画（老数据、后端的意外取值）。 */
function statusClass(status: string): string {
  return status === 'completed' || status === 'in_progress' ? status : 'pending'
}

/** 历史记录 → 块序列：有落库的 parts 就按它回放，没有（纯正文回合）退回 content。 */
function partsOf(r: { content: string | null; summary: string | null; extra: any }): Part[] {
  const saved = r.extra?.parts
  if (Array.isArray(saved) && saved.length) {
    return saved.map((p: any): Part => {
      if (p?.kind === 'todos') return { kind: 'todos', items: todoItems(p.items) }
      return { kind: 'text', content: String(p?.content ?? '') }
    })
  }
  return [{ kind: 'text', content: r.content || r.summary || '' }]
}

const sessions = ref<SessionItem[]>([])
const currentId = ref<string>('')
const messages = ref<Message[]>([])
/**
 * 首屏只渲染最近这么多条，更早的点「显示更早」再往前放。
 *
 * 一条回复动辄几 KB，180 条的会话就是 270 KB 级的 DOM；它同时抬着两笔成本 —— 打开会话时
 * 一次性渲染（实测 120ms 阻塞）和流式每帧的强制重排版（实测 8~9ms，随消息数上涨）。
 * 历史本来就一次拉回内存了，这里只是先不挂进 DOM。
 */
// 历史已经由后端按 20 条分页；这里不再做本地截断（保留变量是为了模板 key 的兼容）。
const VISIBLE_TAIL = Number.MAX_SAFE_INTEGER
const visibleTail = ref(VISIBLE_TAIL)
/** 被挡在前面的条数；0 表示这个会话全都在 DOM 里。 */
const hiddenCount = computed(() => Math.max(0, messages.value.length - visibleTail.value))
const visibleMessages = computed(() =>
  hiddenCount.value ? messages.value.slice(-visibleTail.value) : messages.value,
)
function showEarlier() {
  void loadOlder()
}

function onMessagesScroll() {
  const box = msgBox.value
  if (!box) return
  // 用户翻上去之后浮出「回到最新」；回到底部（含程序化置底）它自己消失。
  atBottom.value = box.scrollHeight - box.scrollTop - box.clientHeight <= JUMP_LATEST_PX
  if (box.scrollTop <= 80) void loadOlder()
}

async function loadOlder() {
  const id = currentId.value
  if (!id || loadingOlder.value || !hasMoreHistory.value || !nextBeforeId.value) return
  const before = nextBeforeId.value
  const box = msgBox.value
  const previousHeight = box?.scrollHeight ?? 0
  const previousTop = box?.scrollTop ?? 0
  loadingOlder.value = true
  try {
    const page = await getRecords(id, before, 20)
    if (currentId.value !== id) return
    const older = page.items
      .map(recordToMessage)
      .filter((m): m is Message => m !== null && m.parts.length > 0)
    if (older.length) messages.value = [...older, ...messages.value]
    hasMoreHistory.value = page.has_more
    nextBeforeId.value = page.next_before_id
    visibleTail.value = VISIBLE_TAIL
    await nextTick()
    if (box) box.scrollTop = box.scrollHeight - previousHeight + previousTop
  } catch (e: any) {
    setNotice(`更早的历史加载失败：${e?.message ?? e}`)
  } finally {
    loadingOlder.value = false
  }
}
const input = ref('')
/** 输入框高度随内容长（1→6 行）；超过 6 行框内滚动。 */
const composerInput = ref<HTMLTextAreaElement | null>(null)
const COMPOSER_MAX_LINES = 6
function fitComposer() {
  const el = composerInput.value
  if (!el) return
  // 输入框变高会把消息区压矮：原本贴着底的要重新贴回去，否则最后一行被顶出视口、
  // 「回到最新」也会误弹出来。
  const stick = atBottom.value
  const style = getComputedStyle(el)
  const line = parseFloat(style.lineHeight) || 22
  const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)
  // border-box 下 offsetHeight - clientHeight 就是上下边框；不加回去会矮 2px，一直挂着滚动条。
  const border = el.offsetHeight - el.clientHeight
  const max = line * COMPOSER_MAX_LINES + padding
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight + border, max)}px`
  el.style.overflowY = el.scrollHeight + border > max ? 'auto' : 'hidden'
  if (stick) scrollToBottom()
}
// 发送清空 / 粘贴 / 快捷动作改值都会经过这里，不必在每个调用点各写一次。
watch(input, () => void nextTick(fitComposer))
const loading = ref(false)
const hasMoreHistory = ref(false)
const loadingOlder = ref(false)
const nextBeforeId = ref<number | null>(null)
/** 当前要展示的澄清弹窗；null = 没有（或用户已经关掉）。 */
const clarification = ref<ClarificationInfo | null>(null)
const clarificationBusy = ref(false)
// 输入框上方那条快捷动作：默认收起（见模板处的说明）
const quickOpen = ref(false)
// 这一轮流是「用户自己喊停」的吗：本地据此补终止标记，并抑制 abort 带出来的那行「错误」
const stopping = ref(false)
// 当前流的本地中断句柄：点终止时 abort 它，本地立刻松手，不等后端收尾
let streamAbort: AbortController | null = null

/** 与后端 `service.CANCELLED_BY_USER` 同一句 —— 本地补的标记要和落库的正文一致 */
const TERMINATED = '〔已由用户终止〕'

// 代码工作区状态：非 ready 时输入框禁用，顶部状态条显示进度（准备中… / 异常+重试）
const workspaceStatus = ref<WorkspaceStatus>('pending')
const workspaceBusy = ref(false)
const workspaceReady = computed(() => workspaceStatus.value === 'ready')
const workspacePreparing = computed(
  () => workspaceStatus.value === 'pending' || workspaceStatus.value === 'provisioning',
)

/** 轮询句柄：非响应式，切会话/结束时必须停，否则旧会话的轮询会覆盖新会话的状态。 */
let pollTimer = 0

function stopWorkspacePoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = 0
  }
}

// 左侧会话栏 / 右侧标签栏：宽度可拖、可收起，宽度与收起状态都记在 localStorage（见 splitter.ts）
const {
  width: sidebarWidth,
  collapsed: sidebarCollapsed,
  min: sidebarMin,
  max: sidebarMax,
  onGripDown: sidebarGripDown,
  onGripMove: sidebarGripMove,
  onGripUp: sidebarGripUp,
  onGripKey: sidebarGripKey,
  reset: resetSidebarWidth,
  toggle: toggleSidebar,
} = usePanelWidth({
  key: 'ui.sidebar',
  defaultWidth: 260,
  min: 180,
  max: 480,
  // 左栏是导航：宽度记住，但**收起状态不记** —— 每次打开界面都该看到会话列表
  persistCollapsed: false,
})
const {
  width: rightWidth,
  collapsed: panelCollapsed,
  min: rightMin,
  max: rightMax,
  onGripDown: rightGripDown,
  onGripMove: rightGripMove,
  onGripUp: rightGripUp,
  onGripKey: rightGripKey,
  reset: resetRightWidth,
  toggle: togglePanel,
} = usePanelWidth({
  key: 'ui.right',
  defaultWidth: 360,
  min: 260,
  max: 720,
  invert: true,
})

// 右侧栏：代码 / 记忆 / PR / 日志 / Token。初始标签来自设置页的「基本设置」
// （本机偏好，见 prefs.ts）；之后点标签照旧自由切，不再回写那个偏好。
const rightTab = ref<'code' | 'memory' | 'pulls' | 'logs' | 'usage'>(defaultPanelTab.value)
// 方案链接里展开的步骤（按 index 记）：随会话切换重置
const expandedSteps = ref<number[]>([])
// 右侧栏「全屏」（面板盖满整个窗口）：临时状态，不持久化
const panelFullscreen = ref(false)
// 左上角那颗齿轮：设置**整页工作台**（左菜单三栏）。
//
// 两处刻意的做法：
// - 切页时聊天区是 `v-show` 藏起来而不是 `v-if` 卸掉 —— 正在跑的流还在往 App 的状态里写，
//   卸掉 DOM 再挂回来没意义，反而多一堆空引用要防；
// - 「在哪一页」记在 URL hash 上（见 `viewState.ts`），刷新后回到原来那页 ——
//   用户口径：「刚才在设置页面，刷新了一下还在设置页面才是对的」。
const initialView = readView()
const view = ref<AppView>(initialView.view)
const settingsPanel = ref<SettingsPanel>(initialView.panel)
watch([view, settingsPanel], () => writeView(view.value, settingsPanel.value))
// 从计划步骤点「涉及文件」跳过来时用：at 只用来区分同一路径的重复点击（否则 watch 不触发）
const codeTarget = ref<{ ref: string; path: string; at: number } | null>(null)

// 流式进行中的活动提示：请求跑完前一直转，用户据此判断「还在跑 / 已经跑完」
const activity = ref('正在思考')

// 新建对话：先输入仓库地址
const pendingRepo = ref(false)
const repoInput = ref('')

// 一次性提示（如「该仓库已有会话，已打开它」）：几秒后自己消失，不用点
const notice = ref('')
let noticeTimer: ReturnType<typeof setTimeout> | undefined

function setNotice(text: string) {
  notice.value = text
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => (notice.value = ''), 6000)
}

// ---------------------------------------------------------------------------
// 初次加载恢复上次的页面
// ---------------------------------------------------------------------------

/** 上次打开的会话；空串表示没有（或已失效被清掉）。 */
const LAST_SESSION_KEY = 'ui.lastSessionId'
/** 上次停在「新建对话」页（用户点了「＋ 新对话」还没开始时）。 */
const NEW_CHAT_KEY = 'ui.newChatPending'
/** 消息上要不要显示 token 角标（默认显示：藏起来等于没做）。 */
const SHOW_USAGE_KEY = 'ui.showMessageUsage'

/** localStorage 在隐私模式 / 被禁时会抛异常：读不到、写不进都只当「没记住」。 */
function readPref(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writePref(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    /* 记不住就算了：恢复不了也照常用 */
  }
}

/** 消息上要不要显示 token 角标。默认显示 —— 藏起来等于没做；嫌吵可以在此关掉，
 * 选择记在本地（与侧栏宽度同一套 `readPref` / `writePref`）。 */
const showUsage = ref(readPref(SHOW_USAGE_KEY) !== '0')

watch(showUsage, (on) => {
  writePref(SHOW_USAGE_KEY, on ? '1' : '0')
})

/**
 * 初次加载恢复上次的页面：优先回到上次打开的会话（直接复用 ``openSession`` —— 历史、方案、
 * 「本轮还在跑就接回直播」、工作区准备都在那一条路径上）；上次停在「新建对话」页就回到那一页。
 *
 * 会话已被删 / 已失效时**清掉记录**、复位到欢迎页并给一条轻提示 —— 不能让用户停在一个
 * 打不开的页面上，也不能每次刷新都再试一遍同一个死会话。
 */
async function restoreLastView() {
  if (readPref(NEW_CHAT_KEY) === '1') {
    startNewChat()
    return
  }
  const id = readPref(LAST_SESSION_KEY)
  if (!id) return
  try {
    await openSession(id)
  } catch {
    writePref(LAST_SESSION_KEY, '')
    currentId.value = ''
    messages.value = []
    resetWorkspace()
    resetPanels()
    setNotice('上次打开的会话已失效，已回到列表')
  }
}

const msgBox = ref<HTMLElement | null>(null)

async function reloadSessions() {
  try {
    sessions.value = await listSessions()
  } catch (e) {
    console.error(e)
  }
}

/** 离底部多少像素以内算「还贴着底」：用户往上翻之后，流式内容就不要再把他拽回来。 */
const STICK_BOTTOM_PX = 48

/** 离底部超过这段距离算「用户翻上去了」：显示回到最新的圆钮。 */
const JUMP_LATEST_PX = 80
/** 视口是否还贴着底（与 ``STICK_BOTTOM_PX`` 分开：48 是「别把我拽回去」的容忍，80 是按钮的显隐线）。 */
const atBottom = ref(true)

/** 视口是不是还贴着消息区的底？拿不到元素时按「是」算（首次进来本来就要跟到底）。 */
function nearBottom(): boolean {
  const el = msgBox.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_BOTTOM_PX
}

/**
 * 把消息区滚到底。
 *
 * 同一个 tick 里的多次调用合并成一次（滚动要等 DOM 更新完，重复调度只是白跑）；
 * 另外调用方可以用 `stick=false` 主动放弃这一次 —— 用户正在往上翻历史时，
 * 每 100ms 被拽回底部比掉帧更难受。
 */
let scrollQueued = false
function scrollToBottom(stick = true) {
  if (!stick || scrollQueued) return
  scrollQueued = true
  nextTick(() => {
    scrollQueued = false
    const el = msgBox.value
    if (el) {
      el.scrollTop = el.scrollHeight
      atBottom.value = true
    }
  })
}

/** 点圆钮：平滑回到底部；按钮先收起，落底后由 scroll 事件重新校准。 */
function jumpToLatest() {
  const el = msgBox.value
  atBottom.value = true
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
}

/**
 * 准备代码工作区：轮询状态条 + 等后端建完。
 *
 * 两条通道缺一不可：POST 是**阻塞到就绪**的那条（成功/失败的最终结论由它给），
 * 轮询只负责过程中的动态展示 —— 后台建的进度本来就不在 POST 的响应里。
 */
async function prepareWorkspace(id: string) {
  workspaceBusy.value = true
  workspaceStatus.value = 'provisioning'
  stopWorkspacePoll()
  pollTimer = window.setInterval(async () => {
    try {
      const detail = await getSession(id)
      // 用户可能已经切走：旧会话的状态不许盖到新会话上
      if (currentId.value !== id) return
      workspaceStatus.value = detail.workspace_status
      if (detail.workspace_status !== 'pending' && detail.workspace_status !== 'provisioning') {
        stopWorkspacePoll()
      }
    } catch {
      // 单次轮询失败不打断：网络抖动不该让状态条卡在「准备中」
    }
  }, 1500)

  try {
    await ensureWorkspace(id)
    if (currentId.value === id) workspaceStatus.value = 'ready'
  } catch {
    if (currentId.value === id) workspaceStatus.value = 'failed'
  } finally {
    stopWorkspacePoll()
    if (currentId.value === id) workspaceBusy.value = false
  }
}

function retryWorkspace() {
  if (currentId.value) prepareWorkspace(currentId.value)
}

function startNewChat() {
  // 记住「停在新建对话页」；同时清掉上次会话，免得下次加载又把它打开
  writePref(NEW_CHAT_KEY, '1')
  writePref(LAST_SESSION_KEY, '')
  pendingRepo.value = true
  repoInput.value = ''
  messages.value = []
  currentId.value = ''
  // 会话号清空后旧流再也复位不了 loading，这里自己收：否则「开始」按钮会一直禁用
  loading.value = false
  resetWorkspace()
  resetPanels()
}

/** 切走会话：停掉轮询、状态归位，别让旧会话的进度残留到新会话上。 */
function resetWorkspace() {
  stopWorkspacePoll()
  workspaceBusy.value = false
  workspaceStatus.value = 'pending'
}

async function confirmNewChat() {
  const url = repoInput.value.trim()
  if (!url) return
  loading.value = true
  // 复用时落到既有会话上，等 loading 复位（finally）之后再打开，免得被它清掉
  let reusedId = ''
  try {
    const created = await createSession(url)
    if (created.reused) {
      reusedId = created.sessionId
    } else {
      writePref(LAST_SESSION_KEY, created.sessionId)
      writePref(NEW_CHAT_KEY, '')
      currentId.value = created.sessionId
      pendingRepo.value = false
      messages.value = []
      resetPanels()
      await reloadSessions()
      // 不 await：接口已返回，剩下的准备进度交给状态条（await 会白占住整屏 loading）
      void prepareWorkspace(created.sessionId)
    }
  } catch (e: any) {
    alert(e.message ?? '创建失败')
  } finally {
    loading.value = false
  }
  // 一个仓库只留一个会话：后端把「新建」并到了既有会话上，这里还原它的历史与工作区状态
  if (reusedId) {
    await openSession(reusedId)
    setNotice('该仓库已有进行中的会话，已为你打开它')
  }
}

function recordToMessage(r: RecordItem): Message | null {
  if (r.type === 'plan') {
    const plan = planFromRecord(r)
    return {
      role: 'assistant',
      parts: plan ? [{ kind: 'plan', plan }] : [],
      at: r.created_at,
      recordId: r.id,
    }
  }
  if (r.type !== 'user' && r.type !== 'assistant') return null
  return {
    role: r.type === 'user' ? 'user' : 'assistant',
    parts: partsOf(r),
    at: r.created_at,
    usage: r.usage ?? undefined,
    changes: (r.extra?.changes as ChangesInfo | undefined) ?? undefined,
    recordId: r.id,
  }
}

async function openSession(id: string) {
  // 记住这次打开的会话（初次加载就用它恢复）
  writePref(LAST_SESSION_KEY, id)
  writePref(NEW_CHAT_KEY, '')
  currentId.value = id
  pendingRepo.value = false
  // 上一条提示说的是上一个动作，切会话后就过期了（复用流程在 openSession 之后自己再设）
  notice.value = ''
  // 换会话就把「显示更早」的展开量收回默认值
  visibleTail.value = VISIBLE_TAIL
  clarification.value = null
  resetPanels()
  resetWorkspace()
  // 上一个会话的流若还在收尾，它的 finally 不会再碰 loading（会话号对不上了）；这里复位，
  // 下面若发现本轮真在跑会重新置回 true
  loading.value = false
  // 会话详情拉不到不该挡住历史消息，故单独兜住
  const [page, detail] = await Promise.all([
    getRecords(id),
    getSession(id).catch(() => null),
  ])
  const records = page.items
  hasMoreHistory.value = page.has_more
  nextBeforeId.value = page.next_before_id
  // 三类记录进聊天流：用户 / 助手 / **方案**（方案以链接形式出现，见 planFromRecord）
  messages.value = records
    .filter((r) => r.type === 'user' || r.type === 'assistant' || r.type === 'plan')
    .map((r) => {
      if (r.type === 'plan') {
        const plan = planFromRecord(r)
        return {
          role: 'assistant' as const,
          parts: plan ? [{ kind: 'plan' as const, plan }] : [],
          at: r.created_at,
        }
      }
      return {
        role: r.type === 'user' ? ('user' as const) : ('assistant' as const),
        parts: partsOf(r),
        at: r.created_at,
        // 这一轮的账（老记录没有 → undefined，角标不显示）
        usage: r.usage ?? undefined,
        // 这一轮改了哪些文件（只有建成 PR 的轮次才有；老记录同样没有）
        changes: (r.extra?.changes as ChangesInfo | undefined) ?? undefined,
      }
    })
    // 方案记录万一缺 plan_id（老数据）会映射成空块：丢掉它，别留一条空消息
    .filter((m) => m.parts.length)
  visibleTail.value = VISIBLE_TAIL
  scrollToBottom()
  try {
    clarification.value = await getClarification(id)
  } catch {
    clarification.value = null
  }
  // 刷新时这一轮还在跑：库里的记录还没有它（收尾才落库），得接回直播流
  if (detail?.running) void attachRunning(id)
  // 已就绪就别再碰工作区（免得白等一次重连）；异常态只显示重试，不自动重踢
  if (detail?.workspace_status === 'ready' || detail?.workspace_status === 'failed') {
    workspaceStatus.value = detail.workspace_status
  } else {
    void prepareWorkspace(id)
  }
}

async function removeSession(id: string) {
  await destroySession(id)
  if (currentId.value === id) {
    writePref(LAST_SESSION_KEY, '')
    currentId.value = ''
    messages.value = []
    clarification.value = null
    pendingRepo.value = false
    loading.value = false
    resetWorkspace()
    resetPanels()
  }
  await reloadSessions()
}

function sessionTitle(s: SessionItem): string {
  return s.title || (s.owner && s.repo ? `${s.owner}/${s.repo}` : '未命名会话')
}

/** 仓库地址压成 `host/owner/repo` 展示（去掉协议与 .git，两个平台通用）。 */
function repoLabel(s: SessionItem): string {
  const url = s.repo_url || ''
  return url.replace(/^https?:\/\//, '').replace(/\.git$/, '') || '无仓库地址'
}

// ---------------- 计划面板 ----------------

const STEP_LABEL: Record<string, string> = {
  pending: '待办',
  running: '进行中',
  done: '完成',
  failed: '失败',
}

const PLAN_LABEL: Record<string, string> = {
  pending: '待实施',
  executing: '实施中',
  done: '已完成',
  failed: '实施失败',
}

/** 工具名 → 活动描述。只说「在做什么」，不提是谁在做 —— 唯一的例外是 `task` 那一格，
 * 派出去的是谁直接决定它在做什么，所以走下面那张按角色的表。 */
const TOOL_ACTIVITY: Record<string, string> = {
  execute: '正在执行命令',
  read_file: '正在读取文件',
  write_file: '正在写入文件',
  edit_file: '正在修改代码',
  grep: '正在检索代码',
  glob: '正在查找文件',
  write_todos: '正在整理任务',
  storePlan: '正在落地方案',
  get_unexecuted_plan: '正在读取方案',
  updatePlanStep: '正在更新进度',
  updatePlanStatus: '正在更新方案状态',
  web_search_tool: '正在联网检索',
  fetch_url: '正在抓取网页',
  create_pr: '正在提交 PR',
  comment_pr: '正在评论 PR',
  get_pr_info: '正在查询 PR',
  task: '正在处理子任务',
}

/** 子智能体角色 → 活动描述（`task` 帧上带了角色才查得到）。表里没有的角色落回上面
 * 那格的泛化文案，将来加了新角色也不会空着。 */
const SUBAGENT_ACTIVITY: Record<string, string> = {
  planner: '正在规划',
  coder: '正在编码',
  reviewer: '正在评审',
}

/** 方案里已完成的步数（方案链接上的「3/7」用它）。 */
function planDone(info: PlanInfo): number {
  return info.steps.filter((s) => s.status === 'done').length
}

function stepLabel(status: string): string {
  return STEP_LABEL[status] ?? status
}

function planLabel(status: string): string {
  return PLAN_LABEL[status] ?? status
}

function toggleStep(index: number) {
  expandedSteps.value = expandedSteps.value.includes(index)
    ? expandedSteps.value.filter((i) => i !== index)
    : [...expandedSteps.value, index]
}

const activeTab = computed<'code' | 'memory' | 'pulls' | 'logs' | 'usage'>(
  () => rightTab.value,
)

/**
 * 右侧标签（竖着排）。
 *
 * **加标签只往这张表里加一项** —— 模板是 `v-for` 渲染的，不用动结构。
 *
 * 「计划」不再在这里：方案改成聊天流里的一条链接（面板取消，2026-10-06）。第二批加的
 * 是「仓库记忆 / PR / 日志」，最后加的是「Token」（本会话消耗，只读聚合）。
 */
const panelTabs = computed<
  { key: 'code' | 'memory' | 'pulls' | 'logs' | 'usage'; label: string; dot?: string }[]
>(() => [
  { key: 'code', label: '代码' },
  { key: 'memory', label: '记忆' },
  { key: 'pulls', label: 'PR' },
  { key: 'logs', label: '日志' },
  { key: 'usage', label: 'Token' },
])

/**
 * 点竖标签：切过去并展开；**再点当前标签＝收起**（2026-10-21 用户口径）。
 * 「本轮改动」内嵌页开着时，点任意标签先关掉那一页、再切过去。
 */
function selectTab(tab: 'code' | 'memory' | 'pulls' | 'logs' | 'usage') {
  if (changesPage.value) {
    changesPage.value = null
    rightTab.value = tab
    panelCollapsed.value = false
    return
  }
  if (tab === rightTab.value && !panelCollapsed.value) {
    panelCollapsed.value = true
    return
  }
  rightTab.value = tab
  panelCollapsed.value = false
}

/** 右侧栏全屏：面板用 `position: fixed; inset: 0` 盖满窗口。收起态点它会先展开 —— 否则
 * 「全屏」只剩一条 44px 的标签条，看不出全屏。Esc 也能退出（见下面的监听）。 */
function togglePanelFullscreen() {
  panelFullscreen.value = !panelFullscreen.value
  if (panelFullscreen.value) panelCollapsed.value = false
}

/**
 * 设置页「基本设置」里的「恢复默认布局」：两个栏宽回默认、两侧都展开、退出右栏全屏。
 *
 * 宽度与收起状态都由 `usePanelWidth` 的 watcher 落盘（见 splitter.ts），这里只改状态 ——
 * 不自己动 localStorage，免得和拖拽那条路径写出两套口径。
 */
function resetLayout() {
  resetSidebarWidth()
  resetRightWidth()
  sidebarCollapsed.value = false
  panelCollapsed.value = false
  panelFullscreen.value = false
}

function onFullscreenKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') panelFullscreen.value = false
}

// 只在全屏期间挂监听：平时不留一个全局 keydown
watch(panelFullscreen, (on) => {
  if (on) window.addEventListener('keydown', onFullscreenKeydown)
  else window.removeEventListener('keydown', onFullscreenKeydown)
})

// 方案里的文件路径 → 仓库内相对路径。模型写的路径三种形态都有（`src/a.py`、
// `/ai_coding/project/<repo>/src/a.py`、`./src/a.py`），把沙箱那层前缀剥掉就行。
function planPathToRepoPath(path: string): string {
  return path
    .trim()
    .replace(/^\/ai_coding\/project\/[^/]+\//, '')
    .replace(/^\/ai_coding\/project\//, '')
    .replace(/^\.?\/*/, '')
}

// 方案步骤里的「涉及文件」→ 切到代码标签并打开它（同一路径连点两次也重开）
function openCodeFile(path: string) {
  rightTab.value = 'code'
  panelCollapsed.value = false
  codeTarget.value = { ref: '', path: planPathToRepoPath(path), at: Date.now() }
}

/** 切会话时把面板与各种「展开态」复位：跳转目标、方案步骤、待办折叠。 */
function resetPanels() {
  codeTarget.value = null
  expandedSteps.value = []
  // 折叠态按「消息下标 + 文本」记，跨会话留着会让新会话的同位项莫名是展开的
  todoOpen.value = {}
}

/** 复制文本：优先 clipboard API，不可用时退回 execCommand（旧环境 / 非安全上下文）。 */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

// 复制会话的仓库地址（地址行短暂显示 ✓）
const copiedId = ref('')

async function copyRepo(s: SessionItem) {
  if (!s.repo_url || !(await copyText(s.repo_url))) return
  copiedId.value = s.session_id
  setTimeout(() => {
    if (copiedId.value === s.session_id) copiedId.value = ''
  }, 1200)
}

/**
 * 代码块复制按钮的点击委托。
 *
 * 按钮是 v-html 出来的，不归 Vue 管、挂不了 @click，只能在容器上统一收事件。
 */
async function onMessagesClick(e: MouseEvent) {
  const btn = (e.target as HTMLElement | null)?.closest<HTMLButtonElement>('.code-copy')
  if (!btn) return
  const code = btn.closest('.code-block')?.querySelector('code')?.textContent ?? ''
  const ok = await copyText(code)
  btn.textContent = ok ? '已复制 ✓' : '复制失败'
  btn.classList.toggle('done', ok)
  // 连点只留最后一个定时器，否则先前那个会把文案提前复原
  const prev = Number(btn.dataset.timer ?? 0)
  if (prev) clearTimeout(prev)
  btn.dataset.timer = String(
    window.setTimeout(() => {
      btn.textContent = '复制'
      btn.classList.remove('done')
      btn.dataset.timer = '0'
    }, 1200),
  )
}

/** 流式渲染节流间隔：分片先进缓冲，显示层最多这么频繁地整篇重渲染一次。 */
const RENDER_INTERVAL_MS = 100

/**
 * 把一条 assistant 消息接上流：正文攒着按间隔刷新，并处理工具 / 待办 / 方案事件。
 *
 * 「发一条新消息」与「接回正在跑的那一轮」走同一套 —— 两处各写一遍事件分支，
 * 迟早只改其中一处。
 */
function attachStream(msg: Message) {
  // 分片先攒着，按间隔统一落进消息里：每来一个分片就把整篇重渲染太费。
  // **必须是一条有序队列**：正文和思考各用一个缓冲的话，flush 时会把「先思考后正文」
  // 拼成「先正文后思考」——交错效果就没了。
  type PendingChunk =
    | { kind: 'text'; text: string }
    | { kind: 'reasoning'; agent: string; text: string }
  let pending: PendingChunk[] = []
  let timer = 0
  const queueText = (text: string) => {
    if (!text) return
    const last = pending[pending.length - 1]
    if (last?.kind === 'text') last.text += text
    else pending.push({ kind: 'text', text })
  }
  const queueReasoning = (agent: string, text: string) => {
    if (!text) return
    const last = pending[pending.length - 1]
    if (last?.kind === 'reasoning' && last.agent === agent) last.text += text
    else pending.push({ kind: 'reasoning', agent, text })
  }
  const flush = () => {
    if (timer) {
      clearTimeout(timer)
      timer = 0
    }
    // 先判断用户还在不在底部：下面一置底，scrollTop 就到底了，事后再读永远算「在底部」
    const stick = nearBottom()
    if (pending.length) {
      for (const chunk of pending) {
        if (chunk.kind === 'text') pushText(msg.parts, chunk.text)
        else pushReasoning(msg.parts, chunk.agent, chunk.text)
      }
      pending = []
    }
    // 这一块太长了就在安全点封口，下一帧只重渲染新块；不封口的话整条回复每 100ms 重渲染一次
    splitLongText(msg.parts)
    scrollToBottom(stick)
  }
  const schedule = () => {
    if (!timer) timer = window.setTimeout(flush, RENDER_INTERVAL_MS)
  }
  const append = (text: string) => {
    queueText(text)
    schedule()
  }
  const onEvent = (ev: ChatEvent) => {
    if (ev.type === 'ai' && ev.content) {
      activity.value = '正在生成回答'
      append(ev.content)
    } else if (ev.type === 'reasoning' && ev.content) {
      // 思考只在前端攒着，后端不落库；主控与子智能体都发，按角色分段
      const agent = ev.agent || 'main'
      activity.value =
        agent === 'main' ? '正在思考' : `正在思考（${reasoningAgentLabel(agent)}）`
      queueReasoning(agent, ev.content)
      schedule()
    } else if (ev.type === 'todos') {
      // 待办树永远画在正文下面，这里不用抢在正文前头落地
      pushTodos(msg.parts, ev.items ?? [])
    } else if (ev.type === 'tool') {
      // 委派帧带了角色就先按角色说（「正在编码」），其余照工具名说，不认识再退回泛化文案
      activity.value =
        SUBAGENT_ACTIVITY[ev.agent ?? ''] ?? TOOL_ACTIVITY[ev.name ?? ''] ?? '正在处理'
    } else if (ev.type === 'plan') {
      // 后端每次推的都是全量快照：同一份方案只占一块，就地替换（`plan: null` = 方案被删）
      pushPlan(msg.parts, ev.plan ?? null)
    } else if (ev.type === 'usage') {
      // 本轮的账：后端落在正文之后发，所以它天然贴在回复末尾。不参与「正文/待办」的块序列
      // —— 它是这条消息的**属性**，不是一段内容（渲染在时间那一行上面）。
      msg.usage = ev.usage ?? null
    } else if (ev.type === 'changes') {
      // 这一轮改了什么：同样贴在这条消息上（只有建成 PR 的轮次才发这一帧）
      msg.changes = ev.changes ?? null
    } else if (ev.type === 'clarification') {
      clarification.value = ev.clarification ?? null
      activity.value = '等待确认'
    } else if (ev.type === 'error') {
      append(`\n[错误] ${ev.error}`)
    }
  }
  return { onEvent, flush, append }
}

/**
 * 消费一条流并收尾；`msg` 是接帧的那条 assistant 消息。
 *
 * 帧的落点按会话号守着：中途切走会话就不再往这条消息里塞（它已经不在屏幕上了），
 * 也别去碰新会话的 loading —— 那是新会话自己的事。
 */
async function runStream(id: string, resp: Response, msg: Message, signal?: AbortSignal) {
  const { onEvent, flush, append } = attachStream(msg)
  try {
    await consumeStream(
      id,
      resp,
      (ev) => {
        if (currentId.value === id) onEvent(ev)
      },
      signal,
    )
  } catch (e: any) {
    // 用户点的终止：AbortError 不是故障，那行「错误」不发，收尾标记在下面补
    if (!stopping.value) append(`\n[错误] ${e.message ?? e}`)
  } finally {
    // 后台真被终止了，正文尾巴上补一句 —— 与落库的那条记录同一句，刷新前后看到的一样
    if (stopping.value && currentId.value === id) append(`\n\n${TERMINATED}`)
    // 收尾必须刷一次，否则最后一批会卡在定时器里
    flush()
    // AI 那条的时间＝流结束时刻（失败/中断也补上，否则这条会一直没有时间）
    msg.at = new Date().toISOString()
    // 计时收尾：从开跑那一刻算起，冻结下来（之后刷新回放也照实显示这个数）
    if (msg.startedAt) msg.elapsedMs = Date.now() - msg.startedAt
    if (currentId.value === id) {
      loading.value = false
      scrollToBottom()
    }
    // 只清**自己**那份句柄与标记：切走会话后这条旧流的收尾不该去动新会话的状态
    // （清了的话，新会话那边点终止会被当成传输故障，正文后面多一行「错误」）
    if (streamAbort?.signal === signal) {
      stopping.value = false
      streamAbort = null
    }
  }
}

/**
 * 这一次 409 是不是「本轮已在跑」？
 *
 * 409 有两种成因：本轮已在跑（把流接回来就行），以及会话已失效 / 缺仓库信息（接不回，
 * 只能报错）。两者状态码相同，唯一干净的判据是详情里那个 running 标志 ——
 * openSession 判断「刷新后要不要接回」用的也是它，这里不另立口径。
 */
async function isRunInProgress(id: string): Promise<boolean> {
  try {
    return (await getSession(id)).running === true
  } catch {
    // 详情都拿不到（会话没了 / 网络断），那也不是「接回」能解决的事
    return false
  }
}

/**
 * 快捷动作：把预置的那句话填进输入框并直接发出去。
 *
 * 只留「无参数、点了就该发生」的两条 —— 措辞都**命中关键字层的强口令**
 * （`刷新仓库记忆` → analysis；`同步仓库` → sync **直接执行**，不经智能体、不烧 token），
 * 所以判定是确定的，不取决于分类模型怎么想。
 */
function quickAct(text: string) {
  if (loading.value || !workspaceReady.value || !currentId.value) return
  input.value = text
  quickOpen.value = false
  void send()
}

async function sendTurn(choice?: { info: ClarificationInfo; index: number }) {
  const picked = choice ? choice.info.options[choice.index] : null
  const text = choice ? '' : input.value.trim()
  const displayText = choice ? `选择：${picked?.label ?? '澄清选项'}` : text
  if ((!choice && !text) || !currentId.value || loading.value) return
  // 工作区没好就不发（输入框此时也是禁用的，这里兜住其它触发路径）
  if (!workspaceReady.value) return
  const id = currentId.value

  // 先渲染、再去拿流：POST 的 await 要一直挂到后端把响应头发回来，等它回来才 push 的话，
  // 用户会以为没发出去而重发；而 loading 也还没置上、按钮尚未禁用，第二次点击真能发出去 ——
  // 后端那一轮已经在跑，于是收到 409。渲染与置 loading 都提前，这两个毛病一起消掉。
  input.value = ''
  // 用户那条的时间＝发送时刻；AI 那条等流结束再补，与后端「回合收尾才写记录」对齐
  messages.value.push({ role: 'user', parts: [{ kind: 'text', content: displayText }], at: new Date().toISOString() })
  // 计时从这一下开始（用户口径：自己发的那轮就直接计时）
  messages.value.push({ role: 'assistant', parts: [], at: '', startedAt: Date.now() })
  // 取**数组里的响应式代理**来改（改局部原始对象不会触发重渲染）
  const msg = messages.value[messages.value.length - 1]
  loading.value = true
  activity.value = '正在思考'
  scrollToBottom()

  let resp: Response
  // 本轮的本地中断句柄：终止按钮拿它 abort（后端那一份由 cancelChat 负责）
  const controller = new AbortController()
  streamAbort = controller
  try {
    resp = await postChat(
      id,
      text,
      controller.signal,
      choice ? { clarificationId: choice.info.id, choice: choice.index } : undefined,
    )
  } catch (e: any) {
    if (streamAbort === controller) streamAbort = null
    // 用户点的是终止（响应头还没回来就点了）：这不是发送失败 —— 别摘掉刚放上去的两条消息、
    // 也别弹窗。那一条用户消息后端大概率已经收下（刷新后历史上看得到），本地这轮就停在
    // 这里：AI 那条只留终止标记，跟库里那条记录对得上。
    if (stopping.value) {
      stopping.value = false
      loading.value = false
      msg.parts.push({ kind: 'text', content: TERMINATED })
      msg.at = new Date().toISOString()
      if (msg.startedAt) msg.elapsedMs = Date.now() - msg.startedAt
      return
    }
    // 没发出去：把刚放上去的两条摘掉、输入还原，别留下两条空消息（用户那条后端根本没收到）。
    // loading 期间输入框与按钮都是禁用的，末尾这两条不会被别人动过，按下标摘是安全的。
    messages.value.splice(messages.value.length - 2, 2)
    if (choice) clarification.value = choice.info
    else input.value = text
    loading.value = false
    // 409 的两种成因状态码相同，靠 running 区分（见 isRunInProgress）：本轮真在跑就接回它，
    // 用户那条后端没收到，所以内容留在输入框里，等这一轮完了他自己再发一次；
    // 会话已失效 / 缺仓库信息则接不回，只能把服务端那句报出来。
    if (await isRunInProgress(id)) {
      await attachRunning(id)
      return
    }
    alert(e.message ?? '发送失败')
    return
  }
  await runStream(id, resp, msg, controller.signal)
}

async function send() {
  await sendTurn()
}

function chooseClarification(index: number) {
  const info = clarification.value
  if (!info || clarificationBusy.value) return
  clarification.value = null
  clarificationBusy.value = true
  void sendTurn({ info, index }).finally(() => {
    clarificationBusy.value = false
  })
}

function otherClarification() {
  clarification.value = null
  setNotice('已关闭澄清弹窗，直接在输入框里说你想做什么即可。')
  nextTick(() => {
    const box = document.querySelector<HTMLTextAreaElement>('.composer-input')
    box?.focus()
  })
}

/**
 * 终止正在跑的这一轮。
 *
 * 两件事都得做，缺一不可：`cancelChat` 让后端把那一轮的 task 取消掉（只断本地连接是没用的
 * —— 按后端的约定，请求断了断的只是订阅，运行照跑、token 照烧）；`abort` 让本地立刻松手，
 * 不等后端收尾那一刻。后端没收到取消指令也照停本地，但得说清「那一轮可能还在跑」。
 */
async function stopRun() {
  const id = currentId.value
  if (!id || !loading.value || stopping.value) return
  stopping.value = true
  activity.value = '正在终止'
  try {
    await cancelChat(id)
  } catch {
    setNotice('终止请求没能送达后端，那一轮可能仍在运行')
  }
  streamAbort?.abort()
}

/**
 * 接回本会话正在跑的那一轮。
 *
 * 刷新页面断掉的是**订阅**，后端那一轮还在跑（帧也攒着），所以这里能从头发起重放。
 * 用户那条已经在 `/records` 里了，只补一条 assistant 消息接住重放的帧。
 */
async function attachRunning(id: string) {
  // 接回来的流同样可终止：刷新断的只是订阅，后端那一轮还在跑
  const controller = new AbortController()
  let resp: Response
  try {
    resp = await resumeChat(id, 0, controller.signal)
  } catch {
    // 接不上就当没有直播，读库那条路照样把历史显示出来，不打断「打开会话」
    return
  }
  if (currentId.value !== id) return
  streamAbort = controller
  // 重新打开时这一轮**已经在跑**：起点取**用户那条消息的时间** —— 于是计时器显示的是
  // 「这一轮已经跑了多久」，而不是「我看了多久」（用户口径：不需要很精确）
  const lastUser = [...messages.value].reverse().find((item) => item.role === 'user')
  const startedAt = lastUser ? Date.parse(lastUser.at) : NaN
  messages.value.push({
    role: 'assistant',
    parts: [],
    at: '',
    startedAt: Number.isFinite(startedAt) ? startedAt : Date.now(),
  })
  const msg = messages.value[messages.value.length - 1]
  loading.value = true
  activity.value = '正在接续本轮'
  scrollToBottom()
  await runStream(id, resp, msg, controller.signal)
}

// 初次加载：先拉会话列表，再恢复上次的页面（见 restoreLastView）
onMounted(async () => {
  await reloadSessions()
  await restoreLastView()
})
// 手改地址 / 前进后退改了 hash：跟着切页（页面自己写 hash 那几次不会触发这里）
const unwatchView = watchView((next) => {
  view.value = next.view
  settingsPanel.value = next.panel
})

// 「working… 12s」那个秒针：只在有活在跑时开，跑完就停（空转的定时器没必要留着）
let tickTimer = 0
watch(loading, (on) => {
  if (on && !tickTimer) {
    nowTick.value = Date.now()
    tickTimer = window.setInterval(() => {
      nowTick.value = Date.now()
    }, 1000)
  } else if (!on && tickTimer) {
    clearInterval(tickTimer)
    tickTimer = 0
  }
})

onUnmounted(() => {
  // 卸载时停掉轮询：定时器持有组件闭包，不停会在切页后继续打接口
  stopWorkspacePoll()
  if (tickTimer) clearInterval(tickTimer)
  unwatchView()
})
</script>

<template>
  <div class="app">
    <!-- 左侧会话栏：可收起（收成一条竖标签）、可拖宽 -->
    <aside
      class="sidebar"
      v-show="view === 'chat'"
      :class="{ collapsed: sidebarCollapsed }"
      :style="sidebarCollapsed ? null : { width: `${sidebarWidth}px` }"
    >
      <div v-if="sidebarCollapsed" class="rail">
        <!-- 收起态的展开按钮放在最上面：与右侧栏同款、同位置，一眼能找到 -->
        <button class="panel-toggle-btn" title="展开会话栏" @click="toggleSidebar">›</button>
        <button class="rail-tab" title="展开会话栏" @click="toggleSidebar">会话</button>
        <button class="rail-icon" title="新对话" @click="startNewChat">＋</button>
      </div>

      <div v-else class="sidebar-body">
        <div class="brand-row">
          <div class="brand">ai-coding-harness</div>
          <!-- 图标用内联 SVG 而不是 emoji（emoji 在不同系统上字形不一、也一眼廉价） -->
          <button
            class="panel-toggle-btn"
            title="设置"
            aria-label="设置"
            @click="view = 'settings'"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                d="M2.5 5h11M2.5 11h11"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
              />
              <circle cx="6" cy="5" r="1.9" fill="var(--surface)" stroke="currentColor" stroke-width="1.4" />
              <circle cx="10.5" cy="11" r="1.9" fill="var(--surface)" stroke="currentColor" stroke-width="1.4" />
            </svg>
          </button>
          <button class="panel-toggle-btn" title="收起会话栏" @click="toggleSidebar">‹</button>
        </div>
        <button class="new-chat" @click="startNewChat">＋ 新对话</button>
        <div class="session-list">
          <div
            v-for="s in sessions"
            :key="s.session_id"
            class="session-item"
            :class="{ active: s.session_id === currentId }"
            @click="openSession(s.session_id)"
          >
            <div class="session-main">
              <span class="session-title">{{ sessionTitle(s) }}</span>
              <span
                class="session-repo"
                :title="s.repo_url ? `点击复制：${s.repo_url}` : ''"
                @click.stop="copyRepo(s)"
              >
                {{ copiedId === s.session_id ? '已复制 ✓' : repoLabel(s) }}
              </span>
            </div>
            <button class="session-btn session-del" title="删除" @click.stop="removeSession(s.session_id)">
              ×
            </button>
          </div>
        </div>
      </div>
    </aside>

    <!-- 左侧拖拽热区：拖动改宽、双击回到默认、方向键微调 -->
    <div
      v-if="!sidebarCollapsed && view === 'chat'"
      class="grip"
      role="separator"
      aria-orientation="vertical"
      :aria-valuemin="sidebarMin"
      :aria-valuemax="sidebarMax"
      :aria-valuenow="sidebarWidth"
      aria-label="调整会话栏宽度"
      tabindex="0"
      @pointerdown="sidebarGripDown"
      @pointermove="sidebarGripMove"
      @pointerup="sidebarGripUp"
      @pointercancel="sidebarGripUp"
      @dblclick="resetSidebarWidth"
      @keydown="sidebarGripKey"
    ></div>

    <main v-show="view === 'chat'" class="main">
      <!-- 新建对话：输入仓库地址 -->
      <div v-if="pendingRepo" class="repo-setup">
        <h2>新建对话</h2>
        <p class="hint">请输入目标仓库地址（Gitee / GitHub）</p>
        <input
          v-model="repoInput"
          class="repo-input"
          placeholder="https://gitee.com 或 https://github.com/<owner>/<repo>.git"
          @keyup.enter="confirmNewChat"
        />
        <button class="primary" :disabled="loading || !repoInput.trim()" @click="confirmNewChat">
          开始
        </button>
      </div>

      <!-- 空状态 -->
      <div v-else-if="!messages.length" class="welcome">
        <h1>有什么可以帮你的？</h1>
        <p class="hint">选中或新建一个对话，然后输入消息</p>
      </div>

      <!-- 消息区：外面这层只做「回到最新」的定位锚点，不参与滚动 -->
      <div v-else class="messages-wrap">
      <div class="messages" ref="msgBox" @scroll.passive="onMessagesScroll" @click="onMessagesClick">
        <!-- 更早的历史先不挂进 DOM（见 visibleTail）：真要翻再点出来 -->
        <div v-if="loadingOlder" class="load-earlier">正在加载更早的历史…</div>
        <div v-else-if="hasMoreHistory" class="load-earlier">
          <button type="button" @click="showEarlier">加载更早的 20 条</button>
        </div>
        <!-- key 与待办折叠 key 一律用**绝对**下标（hiddenCount + i）：窗口往前滑时下标不能跟着变，
             否则同一个下标会落到另一条消息上，折叠状态会串 -->
        <div v-for="(m, i) in visibleMessages" :key="hiddenCount + i" class="msg" :class="m.role">
          <!-- 用户输入按纯文本显示；AI 回复按块渲染：正文 Markdown，待办是列表 -->
          <div v-if="m.role === 'user'" class="bubble">{{ userText(m) }}</div>
          <div v-else class="bubble">
            <!-- 执行时间（用户口径：放在 AI 回复的最上面，就叫 working…）。
                 跑着显示 `working… 12s`，跑完冻结成「用时 12s」。 -->
            <p v-if="m.startedAt || m.elapsedMs" class="working">
              <template v-if="m.elapsedMs">用时 {{ elapsedLabel(m) }}</template>
              <template v-else>working<span class="working-dots">…</span> {{ elapsedLabel(m) }}</template>
            </p>
            <!-- 运行中思考与正文按到达顺序交错：连续的正文块仍合成一个气泡（分组规则见
                 flowEntries），思考块浅色、始终展开；跑完隐藏思考、只留正文。
                 流式期间正文按纯文本显示（不解析 Markdown），任务结束后整组一次性渲染。 -->
            <template v-for="(entry, ei) in flowEntries(m)" :key="ei">
              <div v-if="entry.kind === 'reasoning'" class="ai-part reasoning-inline">
                <div class="reasoning-inline-head">
                  思考过程<span v-if="entry.agent !== 'main'">
                    · {{ reasoningAgentLabel(entry.agent) }}</span>
                </div>
                <div class="reasoning-inline-text">{{ entry.text }}</div>
              </div>
              <div
                v-else
                class="ai-part text"
                :class="m.at ? 'markdown' : 'streaming-text'"
              >
                <!-- v-memo：任务结束后正文不再变，整组只 parse 一次。 -->
                <div
                  v-if="m.at"
                  v-memo="[groupText(entry.parts)]"
                  class="md-block"
                  v-html="renderMarkdown(groupText(entry.parts) || '…')"
                ></div>
                <div v-else class="streaming-plain">{{ groupText(entry.parts) }}</div>
              </div>
            </template>
            <!-- 待办树固定画在正文下面：它表示「现在什么状态」，不是「说到哪儿了」。
                 整棵全完成、且这一轮已收尾就不画（见 showsTodos）—— 那份全绿清单只是噪音，
                 没做完才要留着；空列表是「参数还没流完」的中间态，也不占位 -->
            <!-- 方案：以一条链接出现，点开就地展开步骤与全文。原来它只活在右侧「计划」
                 面板里，面板取消后改由聊天流承载（落库记录 + 实时帧同一形状）。 -->
            <div v-if="planPart(m)" class="ai-part plan-link">
              <button class="plan-head" :aria-expanded="isPlanOpen(planPart(m)!.plan_id)" @click="togglePlan(planPart(m)!.plan_id)">
                <span class="plan-icon">📋</span>
                <span class="plan-anchor">方案：{{ planTitle(m) }}</span>
                <span class="plan-badge" :class="planStatus(m)">{{ planLabel(planStatus(m)) }}</span>
                <span v-if="planProgress(m)" class="plan-progress">{{ planProgress(m) }}</span>
                <span class="plan-caret" :class="{ open: isPlanOpen(planPart(m)!.plan_id) }"></span>
              </button>
              <div v-if="isPlanOpen(planPart(m)!.plan_id)" class="plan-body">
                <ol v-if="planSteps(m).length" class="plan-steps">
                  <li v-for="s in planSteps(m)" :key="s.index" class="plan-step" :class="s.status">
                    <div class="step-row" :title="s.goal || ''" @click="toggleStep(s.index)">
                      <span class="step-dot" :class="s.status"></span>
                      <span class="step-index">{{ s.index }}</span>
                      <span class="step-goal">{{ s.goal }}</span>
                      <span class="step-caret" :class="{ open: expandedSteps.includes(s.index) }">›</span>
                    </div>
                    <div v-if="expandedSteps.includes(s.index)" class="step-detail">
                      <!-- 列表里 goal 被截成一行，点开才给全 —— 旧格式（goal 写得长）也能读全 -->
                      <div class="step-field">
                        <span class="detail-label">目标</span>
                        <span class="step-criteria">{{ s.goal }}</span>
                      </div>
                      <div class="step-field">
                        <span class="detail-label">状态</span>
                        <span class="step-status" :class="s.status">{{ stepLabel(s.status) }}</span>
                      </div>
                      <div v-if="s.files && s.files.length" class="step-field">
                        <span class="detail-label">涉及文件</span>
                        <span class="step-files">
                          <!-- 点一下就跳到代码面板并打开这个文件（不切走当前对话） -->
                          <button
                            v-for="(f, i) in s.files"
                            :key="i"
                            class="step-file"
                            :title="`在代码面板打开 ${f}`"
                            @click="openCodeFile(f)"
                          >
                            {{ f }}
                          </button>
                        </span>
                      </div>
                      <div v-if="s.done_criteria" class="step-field">
                        <span class="detail-label">完成标准</span>
                        <span class="step-criteria">{{ s.done_criteria }}</span>
                      </div>
                      <div v-if="!s.files?.length && !s.done_criteria" class="step-field muted">
                        该步未填写涉及文件与完成标准
                      </div>
                    </div>
                  </li>
                </ol>
                <p v-else class="plan-empty">该方案没有结构化步骤</p>
                <!-- 方案全文（Markdown）：步骤是索引，正文才是细节 -->
                <div
                  v-if="planMarkdown(m)"
                  v-memo="[planMarkdown(m)]"
                  class="plan-content md-block markdown"
                  v-html="renderMarkdown(planMarkdown(m))"
                ></div>
              </div>
            </div>
            <ul v-if="showsTodos(m)" class="ai-part todos">
              <li v-for="(t, k) in todosPart(m)" :key="k" :class="statusClass(t.status)">
                <div class="todo-row">
                  <span class="todo-mark"></span>
                  <span class="todo-text">{{ t.content }}</span>
                  <button
                    v-if="t.children?.length"
                    class="todo-toggle"
                    :aria-expanded="isTodoOpen(m, k, hiddenCount + i)"
                    @click.stop="toggleTodo(m, k, hiddenCount + i)"
                  >
                    {{ doneCount(t) }}
                    <span class="todo-caret" :class="{ open: isTodoOpen(m, k, hiddenCount + i) }"></span>
                  </button>
                </div>
                <!-- 子任务：子智能体自己那份 write_todos -->
                <ul v-if="t.children?.length && isTodoOpen(m, k, hiddenCount + i)" class="todo-children">
                  <li v-for="(c, n) in t.children" :key="n" :class="statusClass(c.status)">
                    <span class="todo-mark"></span>
                    <span class="todo-text">{{ c.content }}</span>
                  </li>
                </ul>
              </li>
            </ul>
            <!-- 还没出任何块（正在思考）：留个占位，别让气泡空成一片 -->
            <!-- v-memo="[]"：正文是常量，渲染一次就够（等价 v-once），别让它跟着每一帧重跑 -->
            <div
              v-if="!m.parts.length"
              v-memo="[]"
              class="ai-part text markdown"
              v-html="renderMarkdown('…')"
            ></div>
          </div>
          <!-- 本轮消耗：贴在时间那一行上面。默认显示（藏起来等于没做），可在右栏关掉。
               展开态用**绝对**下标（hiddenCount + i）—— 窗口往前滑时下标不能跟着变 -->
          <div
            v-if="m.changes"
            class="msg-changes"
            :class="{ open: changesPage === m.changes }"
          >
            <button
              class="msg-changes-badge"
              :title="changesPage === m.changes ? '收起改动' : '在右侧查看本轮改动'"
              @click="openChanges(m.changes)"
            >
              <span class="msg-changes-dot"></span>
              {{ changesSummary(m.changes) }}
            </button>
          </div>
          <div
            v-if="showUsage && m.role === 'assistant' && m.usage"
            class="msg-usage"
            :class="{ open: usageOpen[hiddenCount + i] }"
          >
            <button
              class="msg-usage-badge"
              :title="usageOpen[hiddenCount + i] ? '收起明细' : '展开明细'"
              @click="toggleUsage(hiddenCount + i)"
            >
              <span class="msg-usage-dot"></span>
              {{ usageSummary(m.usage) }}
              <span v-if="m.usage.interrupted" class="msg-usage-flag">（中断）</span>
              <span v-if="m.usage.missing" class="msg-usage-flag">
                {{ m.usage.missing }} 次无用量
              </span>
            </button>
            <ul v-if="usageOpen[hiddenCount + i]" class="msg-usage-detail">
              <li v-for="row in usageRows(m.usage)" :key="row.label">
                <span>{{ row.label }}</span>
                <b>{{ row.value.toLocaleString('en-US') }}</b>
              </li>
            </ul>
          </div>
          <!-- 末行：**回答这条的模型** + 时间（两者都没有就整块不渲染，不占位）。
               模型不受「显示用量」开关影响 —— 它是「谁答的」，不是账。 -->
          <div v-if="m.usage?.model || formatTime(m.at)" class="msg-time">
            <template v-if="m.usage?.model">
              <span class="msg-model" :title="`本轮主控模型：${m.usage.model}`">
                {{ m.usage.model }}
              </span>
              <span v-if="formatTime(m.at)" class="msg-meta-sep">·</span>
            </template>
            {{ formatTime(m.at) }}
          </div>
        </div>
        <!-- 流式进行中的提示：请求没结束就一直转，结束时连同 loading 一起消失 -->
        <div v-if="loading" class="thinking">
          <span class="thinking-dots"><i></i><i></i><i></i></span>
          <span class="thinking-text">{{ activity }}</span>
        </div>
      </div>
      <!-- 翻上去之后浮出来：圆形钮，点击平滑回到底部 -->
      <button
        v-if="!atBottom"
        type="button"
        class="jump-latest"
        title="回到最新"
        aria-label="回到最新"
        @click="jumpToLatest"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path
            d="M12 4v14m0 0 6-6m-6 6-6-6"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      </div>

      <!-- 一次性提示（如复用既有会话）：几秒后自己消失，不用点 -->
      <div v-if="notice" class="notice-bar">{{ notice }}</div>

      <!-- 代码工作区状态条：非就绪时占位，就绪后整条消失 -->
      <div
        v-if="!pendingRepo && currentId && !workspaceReady"
        class="workspace-bar"
        :class="workspaceStatus"
      >
        <template v-if="workspacePreparing">
          <span class="thinking-dots"><i></i><i></i><i></i></span>
          <span>代码工作区准备中…</span>
        </template>
        <template v-else>
          <span>代码工作区创建异常，请重试</span>
          <button class="ws-retry" :disabled="workspaceBusy" @click="retryWorkspace">重试</button>
        </template>
      </div>

      <!-- 输入区 -->
      <!-- 快捷动作：只留两个**无参数、点了就该发生**的。默认收起 —— 常驻一行会占掉
           输入的注意力，需要时点一下展开。动作就是替你发一句话（命中关键字层那一关，
           判定确定，不依赖分类模型）。 -->
      <div v-if="!pendingRepo && currentId" class="quickbar">
        <button
          class="quick-toggle"
          :aria-expanded="quickOpen"
          :disabled="loading || !workspaceReady"
          @click="quickOpen = !quickOpen"
        >
          快捷 {{ quickOpen ? '▾' : '▸' }}
        </button>
        <template v-if="quickOpen">
          <button class="quick-act" :disabled="loading || !workspaceReady" @click="quickAct('刷新仓库记忆')">
            刷新仓库记忆
          </button>
          <button class="quick-act" :disabled="loading || !workspaceReady" @click="quickAct('同步仓库')">
            同步代码
          </button>
        </template>
      </div>
      <!-- 输入区 -->
      <div v-if="!pendingRepo" class="composer">
        <textarea
          v-model="input"
          ref="composerInput"
          class="composer-input"
          rows="1"
          placeholder="给 ai-coding-harness 发送消息…"
          :disabled="!currentId || loading || !workspaceReady"
          @keydown.enter.exact.prevent="send"
        />
        <!-- 正在跑时这一格换成「终止」：停的是本轮，会话与已产出的内容都留着 -->
        <button
          v-if="loading"
          class="primary send stop"
          :disabled="stopping"
          title="终止本轮（已产出的内容会留在历史里）"
          @click="stopRun"
        >
          终止
        </button>
        <button
          v-else
          class="primary send"
          :disabled="!input.trim() || !currentId || !workspaceReady"
          @click="send"
        >
          发送
        </button>
        <!-- 上下文压力环图：放在发送框右边（原来是输入框上方一条横条）。
             <60% 中性色、≥60% 警告色、≥80% 危险色 —— 阈值口径与服务端一致（它算 ratio）。
             详细数字与「会不会触发压缩」挪进 title，不再常驻一行文字。 -->
        <div
          v-if="currentId && latestContext"
          class="ctx-ring"
          :class="contextLevel"
          role="img"
          :title="contextTitle"
          :aria-label="contextTitle"
        >
          <svg viewBox="0 0 36 36" width="30" height="30" aria-hidden="true">
            <circle class="ctx-ring-track" cx="18" cy="18" r="15.5" />
            <circle
              class="ctx-ring-fill"
              cx="18"
              cy="18"
              r="15.5"
              pathLength="100"
              :stroke-dasharray="`${contextPercent} ${100 - contextPercent}`"
            />
          </svg>
          <span class="ctx-ring-num">{{ contextPercent }}</span>
        </div>
      </div>
    </main>

    <!-- 右侧拖拽热区（收起了就没有可拖的宽度，热区一并隐藏） -->
    <div
      v-if="currentId && !pendingRepo && !panelCollapsed && !panelFullscreen && view === 'chat'"
      class="grip"
      role="separator"
      aria-orientation="vertical"
      :aria-valuemin="rightMin"
      :aria-valuemax="rightMax"
      :aria-valuenow="rightWidth"
      aria-label="调整右侧栏宽度"
      tabindex="0"
      @pointerdown="rightGripDown"
      @pointermove="rightGripMove"
      @pointerup="rightGripUp"
      @pointercancel="rightGripUp"
      @dblclick="resetRightWidth"
      @keydown="rightGripKey"
    ></div>

    <!-- 右侧栏：**竖排标签条** + 内容。收起＝收内容，标签留着（点一下就展开到那个标签） -->
    <aside
      v-if="currentId && !pendingRepo"
      v-show="view === 'chat'"
      class="right-panel"
      :class="{ collapsed: panelCollapsed, fullscreen: panelFullscreen }"
      :style="panelCollapsed || panelFullscreen ? null : { width: `${rightWidth}px` }"
    >
      <nav class="panel-tabs">
        <!-- 收起/展开放在标签条**最上面**：原先在最下边（margin-top:auto 顶到底），不好找 -->
        <button
          class="panel-toggle-btn"
          :title="panelCollapsed ? '展开面板' : '收起面板'"
          @click="togglePanel"
        >
          {{ panelCollapsed ? '‹' : '›' }}
        </button>
        <!-- 全屏：图标用内联 SVG，不依赖字体里的符号（免得缺字变方块）。
             四角向外＝进入，四角向内＝退出；Esc 也能退。 -->
        <button
          class="panel-toggle-btn"
          :class="{ active: panelFullscreen }"
          :title="panelFullscreen ? '退出全屏（Esc）' : '全屏'"
          @click="togglePanelFullscreen"
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path
              v-if="!panelFullscreen"
              d="M6 2H2v4M10 2h4v4M6 14H2v-4M10 14h4v-4"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
            />
            <path
              v-else
              d="M2 6h4V2M14 6h-4V2M2 10h4v4M14 10h-4v4"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
            />
          </svg>
        </button>
        <button
          v-for="tab in panelTabs"
          :key="tab.key"
          class="panel-tab"
          :class="{ active: activeTab === tab.key }"
          :title="tab.label"
          @click="selectTab(tab.key)"
        >
          <span class="panel-tab-text">{{ tab.label }}</span>
          <i v-if="tab.dot" class="plan-dot" :class="tab.dot"></i>
        </button>
      </nav>

      <div v-if="!panelCollapsed" class="panel-body">

        <!-- 本轮改动：从聊天流的角标点进来的内嵌页（盖在当前标签上，不占竖标签） -->
        <ChangesPanel
          v-if="changesPage"
          :session-id="currentId"
          :changes="changesPage"
          @close="changesPage = null"
        />
        <!-- 代码：本机那份只读缓存（与沙箱无关）。方案不再是标签 —— 它进了聊天流 -->
        <RepoPanel
          v-else-if="activeTab === 'code'"
          :session-id="currentId"
          :target="codeTarget"
        />
        <!-- 记忆：StoreBackend 上那份仓库记忆（只读；正文由 coding / review 收尾写） -->
        <MemoryPanel v-else-if="activeTab === 'memory'" :session-id="currentId" />
        <!-- PR：本仓库的 PR 列表（查不到会显示成错误，不是空列表） -->
        <PrPanel v-else-if="activeTab === 'pulls'" :session-id="currentId" />
        <!-- 日志：这个会话自己的日志（面板只显示尾部；整份走「下载」） -->
        <LogPanel v-else-if="activeTab === 'logs'" :session-id="currentId" />
        <!-- Token：本会话消耗（只读聚合；缺数据的轮次会如实说出来，不显示成 0） -->
        <UsagePanel
          v-else
          v-model:show-usage="showUsage"
          :session-id="currentId"
        />
      </div>
    </aside>

    <!-- 设置页（整页工作台）：左上角齿轮打开；左菜单分基本设置 / 外观 / 模型配置。
         当前在哪一栏由 App 持有（它同时负责写 hash），设置页只发意图上来。 -->
    <SettingsPage
      v-if="view === 'settings'"
      :panel="settingsPanel"
      @close="view = 'chat'"
      @panel="settingsPanel = $event"
      @reset-layout="resetLayout"
    />

    <ClarificationDialog
      v-if="clarification"
      :info="clarification"
      :busy="clarificationBusy"
      @choose="chooseClarification"
      @other="otherClarification"
      @close="clarification = null"
    />

  </div>
</template>
