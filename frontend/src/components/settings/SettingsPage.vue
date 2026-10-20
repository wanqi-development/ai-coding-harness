<script setup lang="ts">
/**
 * 设置页（整页工作台）：左菜单三栏 —— **基本设置 / 外观 / 模型配置**。
 *
 * 分工与「存在哪」：
 *
 * - **基本设置 / 外观** 是本机显示偏好，改动**立刻生效**，存在 localStorage（见 `prefs.ts`）；
 * - **模型配置** 存在后端库里（按智能体，见 `api/routes/model_config.py`），改完在**弹窗里保存**，
 *   **下一轮**就用新的连接（主智能体每轮现建，不用重启）。
 *
 * 2026-10-15 的口径：**没有「出厂默认」**（一行没配就是没配，用到它的角色会报错）、
 * 每一行都显示**生效值**（现在真的用哪个模型）、行分栏与「编码一主三从」的角色归属都由
 * 后端下发（前端不再硬编码行名）、报错**就地显示在弹窗里**。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  getAppSettings,
  getProviderModels,
  deleteModelConfig,
  getModelConfig,
  probeProvider,
  putAppSettings,
  putModelConfig,
  refreshProviderModels,
  type CodingGroupRole,
  type ModelConfigRow,
  type ModelConfigSnapshot,
  type ProviderModelItem,
  type ProviderOption,
  type RuntimeSettingItem,
  type RuntimeSettingsSnapshot,
} from '../../lib/api'
import ModelConfigDialog from './ModelConfigDialog.vue'
import ModelEntry from './ModelEntry.vue'
import ProvidersPanel from './ProvidersPanel.vue'
import { formatWindow, parseWindow } from '../../lib/modelWindow'
import {
  CODE_SIZE,
  CHAT_WIDTH_LABEL,
  chatWidth,
  codeSize,
  defaultPanelTab,
  setChatWidth,
  setCodeSize,
  setDefaultPanelTab,
  setTheme,
  themeMode,
  type ChatWidth,
  type PanelTab,
  type ThemeMode,
} from '../../lib/prefs'
import type { SettingsPanel } from '../../lib/viewState'

const props = defineProps<{ panel: SettingsPanel }>()
const emit = defineEmits<{ close: []; 'reset-layout': []; panel: [SettingsPanel] }>()
/** 左菜单的三栏。模型配置放最后 —— 它是「高级」那一栏，前两栏是日常会动的。 */
const PANELS: { key: SettingsPanel; label: string }[] = [
  { key: 'basic', label: '基本设置' },
  { key: 'look', label: '外观' },
  { key: 'models', label: '模型配置' },
  { key: 'providers', label: '提供商' },
]
/** 当前栏由 App 持有（它同时负责把它写进 URL hash，刷新才不会跳回聊天页）。 */
const panel = computed(() => props.panel)

const THEME_OPTIONS: { key: ThemeMode; label: string }[] = [
  { key: 'light', label: '浅色' },
  { key: 'dark', label: '深色' },
  { key: 'system', label: '跟随系统' },
]

const WIDTH_OPTIONS: { key: ChatWidth; label: string }[] = (
  ['compact', 'standard', 'wide'] as ChatWidth[]
).map((key) => ({ key, label: CHAT_WIDTH_LABEL[key] }))

const TAB_OPTIONS: { key: PanelTab; label: string }[] = [
  { key: 'code', label: '代码' },
  { key: 'memory', label: '记忆' },
  { key: 'pulls', label: 'PR' },
  { key: 'logs', label: '日志' },
  { key: 'usage', label: 'Token' },
]

function resetLayout() {
  if (!window.confirm('把左右两栏的宽度与收起状态都清回默认？')) return
  emit('reset-layout')
  notice.value = '布局已恢复默认。'
}

// -- 基本设置：运行期配置（改完**立刻生效**，不用重启）------------------------

const appSettings = ref<RuntimeSettingsSnapshot | null>(null)
/** 编辑中的草稿（键 → 字符串；与后端值分开，编辑中不该被刷新覆盖）。 */
const runtimeDrafts = ref<Record<string, string>>({})

/** 按分组摆好（后端已排好顺序，这里只做分组，不动顺序）。 */
const runtimeGroups = computed(() => {
  const out: { name: string; items: RuntimeSettingItem[] }[] = []
  for (const item of appSettings.value?.items ?? []) {
    let bucket = out.find((group) => group.name === item.group)
    if (!bucket) {
      bucket = { name: item.group, items: [] }
      out.push(bucket)
    }
    bucket.items.push(item)
  }
  return out
})

function runtimeValue(item: RuntimeSettingItem): string {
  return runtimeDrafts.value[item.key] ?? ''
}

async function loadRuntime() {
  try {
    const fresh = await getAppSettings()
    appSettings.value = fresh
    // 草稿在加载时就填好（不在渲染里写状态：那是渲染期的副作用）
    runtimeDrafts.value = Object.fromEntries(
      fresh.items.map((item) => [item.key, item.value === null ? '' : String(item.value)]),
    )
  } catch (e: any) {
    error.value = e?.message ?? '读取失败'
  }
}

/** 只提交**改过**的键（与模型配置那栏同一口径：不传＝不改）。 */
async function saveRuntime() {
  const changed: Record<string, string | number> = {}
  for (const item of appSettings.value?.items ?? []) {
    const draft = runtimeDrafts.value[item.key]
    if (draft === undefined) continue
    const before = item.value === null ? '' : String(item.value)
    if (draft === before) continue
    changed[item.key] = item.kind === 'str' ? draft : Number(draft)
  }
  if (!Object.keys(changed).length) {
    notice.value = '没有改动。'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    appSettings.value = await putAppSettings(changed)
    await loadRuntime()
    notice.value = '已保存，立刻生效。'
  } catch (e: any) {
    error.value = e?.message ?? '保存失败'
  } finally {
    busy.value = false
  }
}

// -- 模型配置（只有切到那一栏才去拉：省一次请求，也免得别栏弹红条）--------------

const data = ref<ModelConfigSnapshot | null>(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const intentMode = ref('jev')

/** 一行的可编辑草稿（与后端快照分开：编辑中不该被刷新覆盖）。 */
type Draft = {
  provider: string
  base_url: string
  model: string
  api_key: string
  /** 窗口那一格的**文本**（`1M` / `128K` / 空＝按模型查） */
  context_window: string
  /** 这一格显示的原值（tokens）：文本没被改过时按它存，避免 `195.3K` 这类来回换算失真 */
  window_exact: number
  /** true ＝ 这一格是**回显**（后端解析出来的值），保存时不写库；手一改就变成 false */
  window_auto: boolean
  /** 点过「清除 key」：保存时把库里那一把清掉（送空串） */
  clear_key: boolean
  /** 该行的探测反馈（一行文字） */
  probe: string
  /** 探测到的**实时**模型名（做 datalist 候选；内置候选走提供商那一格） */
  models: string[]
  /** 动过这一行没有（动过就不被 load() 覆盖） */
  dirty: boolean
}

const drafts = ref<Record<string, Draft>>({})
const providers = computed<ProviderOption[]>(() => data.value?.providers ?? [])
/** 正在配置的那一行（null ＝ 弹窗关着） */
const editing = ref<ModelConfigRow | null>(null)
/** 弹窗里的报错（就地显示：页面顶部那条被遮罩挡着，看不见） */
const dialogError = ref('')

function openDialog(row: ModelConfigRow) {
  dialogError.value = ''
  editing.value = row
}

function closeDialog() {
  dialogError.value = ''
  editing.value = null
}

/**
 * 行按**后端给的 section** 分栏（前端不再自己列行名：后端加一行不会再静默消失）。
 * 「编码」那一组的两个行（coding / coder）不在这里平铺 —— 它们按角色显示在下面的块里。
 */
const rowsBySection = computed<Record<string, ModelConfigRow[]>>(() => {
  const out: Record<string, ModelConfigRow[]> = {}
  for (const section of data.value?.sections ?? []) out[section.key] = []
  for (const row of data.value?.rows ?? []) {
    ;(out[row.section] ??= []).push(row)
  }
  return out
})

/** 「编码」那一组按角色展示（预置里的 4 个角色 → 落在哪一行、是不是复用） */
const codingGroup = computed<CodingGroupRole[]>(() => data.value?.coding_group ?? [])

/** 平铺在「各智能体」里的行：编码组里的那两个不计（它们在块里按角色出现） */
const codingKeys = computed(() => new Set(codingGroup.value.map((item) => item.row)))
const flatAgentRows = computed(() =>
  (rowsBySection.value.agents ?? []).filter((row) => !codingKeys.value.has(row.key)),
)

function rowOf(key: string): ModelConfigRow | undefined {
  return (data.value?.rows ?? []).find((row) => row.key === key)
}

const jevRow = computed(() => rowOf('jev'))
const classifierRows = computed(() =>
  (rowsBySection.value.intent ?? []).filter((row) => row.key !== 'jev'),
)
const globalRows = computed(() => rowsBySection.value.global ?? [])

/** 后端没给 effective（例如 JEV / 兜底这两行不是聊天模型）时的空值 */
const NO_EFFECTIVE = {
  role: '',
  row: '',
  model: '',
  window: 0,
  window_source: 'none',
  base_url: '',
  has_key: false,
  source: 'unset',
}

function eff(row: ModelConfigRow) {
  return row.effective ?? NO_EFFECTIVE
}

function sectionLabel(key: string): string {
  return (data.value?.sections ?? []).find((item) => item.key === key)?.label ?? key
}

function rowLabel(key: string): string {
  return rowOf(key)?.label ?? key
}

/** 「编码」那一组的角色行：点进去编辑的就是它落在的那一行（规划 / 评审编辑的是别人那行）。 */
function openRoleRow(key: string) {
  const row = rowOf(key)
  if (row) openDialog(row)
}

/** JEV 那一行的次行文案：它不是聊天模型，没有「生效值」这一说。 */
function jevDetail(row: ModelConfigRow): string {
  if (!row.model && !row.base_url && !row.has_key) {
    return '未配置：选了 JEV 会落关键字口径'
  }
  const parts = [row.model || 'SDK 默认模型']
  if (row.base_url) parts.push(row.base_url)
  parts.push(row.has_key ? '已配 key' : '缺 key')
  return parts.join(' · ')
}

/**
 * 次行文案：正常情况下由 `ModelEntry` 按**生效值**算；「兜底模型」不是聊天模型
 * （后端不给它 effective），得自己说 —— 不然会被当成「未配置」。
 */
function rowDetail(row: ModelConfigRow): string {
  // 「统一模型」那一行看它自己：模型名 + 窗口（真值才写）—— 这一行不是"被兜底"的角色
  if (row.key === 'unified') {
    if (!row.model) return '未配置：没填就不兜底'
    const eff = row.effective
    const real = eff?.window_source === 'row' || eff?.window_source === 'model'
    const win = real && eff && eff.window > 0 ? ` · 窗口 ${formatWindow(eff.window)}` : ''
    return `${row.model}${win} · 只兜底没配的行`
  }
  if (row.effective) return ''
  if (row.key === 'fallback') {
    if (!row.model) return '未配置：留空＝没有兜底'
    return `${row.model}${row.base_url ? ` · ${row.base_url}` : ''}`
  }
  return row.model || '未配置'
}

const INTENT_MODES = [
  { key: 'jev', label: 'JEV' },
  { key: 'llm', label: '大模型' },
]

/** 「编码」块里某个角色对应的行还没加载到时的占位（正常不会走到）。 */
const NO_ROW: ModelConfigRow = {
  key: '',
  label: '',
  section: '',
  roles: [],
  provider: '',
  provider_locked: false,
  base_url: '',
  model: '',
  api_key_masked: '',
  has_key: false,
  context_window: 0,
  source: 'unset',
  effective: null,
}

function pickIntentMode(mode: string) {
  if (intentMode.value === mode) return
  intentMode.value = mode
  void saveIntentMode()
}

// -- 供应商的模型清单（**点了刷新才拉**；窗口按模型查就查这份）------------------

/**
 * 各家的模型清单（弹窗里的候选与**窗口回显**用它）。
 *
 * 拉取时机：打开弹窗时按行里那一家的需要拉一次、换供应商时拉一次、弹窗里点「刷新清单」时拉。
 * **不主动轮询**（用户口径：界面上提供刷新按钮，不主动刷新）。
 */
const modelsByProvider = ref<Record<string, ProviderModelItem[]>>({})
/** 正在刷新的那一家（弹窗里那个按钮显示「刷新中…」） */
const refreshing = ref('')

/**
 * 拉一家的清单（有缓存就不重复拉；`force` 用于刷新之后）。

 * **缓存命中也要刷一次窗口回显**（2026-10-15 用户报的「选了模型窗口没跟着刷新」）：
 * 以前这里命中就 `return`，于是"选完模型去查后台"这一步只在**第一次**打开弹窗时才发生。
 */
async function ensureModels(provider: string, force = false) {
  if (!force && modelsByProvider.value[provider]) {
    fillAutoWindow(provider)
    return
  }
  try {
    modelsByProvider.value[provider] = (await getProviderModels(provider)).items
    fillAutoWindow(provider)
  } catch (e: any) {
    error.value = e?.message ?? '读取模型清单失败'
  }
}

/**
 * 清单到手后，把**回显着的**窗口那一格按新模型刷一次（用户手改过的格子不动）。

 * 只有清单里有非 0 窗口时才填：厂商不回窗口的那几家（实测 glm / kimi / qwen / openai 都不回）
 * 就维持原样，等后端按兜底值解析。
 */
function fillAutoWindow(provider: string) {
  const row = editing.value
  const draft = row ? drafts.value[row.key] : undefined
  if (!draft || !draft.window_auto || draft.provider !== provider) return
  const model = draft.model.trim()
  if (!model) return
  const window = (modelsByProvider.value[provider] ?? []).find(
    (item) => item.model === model,
  )?.context_window
  if (window && window > 0) {
    draft.context_window = formatWindow(window)
    draft.window_exact = window
  }
}

/**
 * 点「刷新」才去厂商那儿拉清单并落表（后端同样只在被调用时刷）。
 * 地址用**这一行手填的那个**：代理 / 自建网关跟目录里的默认地址不是一回事。
 */
async function refreshModels(provider: string) {
  refreshing.value = provider
  dialogError.value = ''
  notice.value = ''
  try {
    const result = await refreshProviderModels(
      provider,
      editing.value ? draftOf(editing.value).base_url : undefined,
    )
    modelsByProvider.value[provider] = result.items
    fillAutoWindow(provider)  // 刚拉到的窗口立刻回显（不用关了再开一次弹窗）
    notice.value = `已刷新：${provider} 读到 ${result.refreshed ?? 0} 个模型${
      result.note ? `（${result.note}）` : ''
    }`
  } catch (e: any) {
    dialogError.value = e?.message ?? '刷新失败'
  } finally {
    refreshing.value = ''
  }
}

function draftOf(row: ModelConfigRow): Draft {
  drafts.value[row.key] ??= draftFor(row)
  return drafts.value[row.key]
}

/**
 * 一行的初始草稿。

 * 窗口那一格只回显**真值**（`window_source` 是 ``row`` 手填的、或 ``model`` 按模型查到的）：
 * 兜底值（128k）是我们自己保守取的不是厂商给的数，填进去只会让人以为查到了
 * （2026-10-15 用户问「这个 125K 从哪来」）。`window_auto` 说明「这是回显」，保存时**不写库**。
 */
function draftFor(row: ModelConfigRow): Draft {
  const eff = row.effective
  const real = eff?.window_source === 'row' || eff?.window_source === 'model'
  const shown = row.context_window > 0 ? row.context_window : (real ? (eff?.window ?? 0) : 0)
  return {
    provider: row.provider,
    base_url: row.base_url,
    model: row.model,
    api_key: '',
    context_window: formatWindow(shown),
    window_exact: shown,
    window_auto: row.context_window === 0,
    clear_key: false,
    probe: '',
    models: [],
    dirty: false,
  }
}

/**
 * 换提供商：把它的默认地址带出来（用户仍可改）。
 *
 * 换了一家，**上一家的 key 也要清掉**（用户口径 2026-10-15）：界面清空只是草稿，
 * 库里那一把得靠保存时送空串 —— 不送的话「留空＝不改」会把旧家的 key 留在新地址上。
 */
function pickProvider(row: ModelConfigRow, key: string) {
  const draft = draftOf(row)
  const hadKey = row.has_key
  draft.provider = key
  draft.dirty = true
  if (key !== 'custom' && providers.value.some((item) => item.key === key)) void ensureModels(key)
  // 换了一家：**配置项全部清空**（用户口径）—— 上一家的模型名 / key / 窗口都属于上一家，
  // 留着只会是错的数据；地址换成新那家的默认地址（选中供应商的意义就在这儿），
  // 那家没配地址就留空。实时清单与探测反馈同样清掉。
  draft.model = ''
  draft.api_key = ''
  draft.context_window = ''
  draft.window_exact = 0
  draft.window_auto = true
  draft.clear_key = hadKey
  draft.models = []
  draft.probe = ''
  draft.base_url = ''
  const option = providers.value.find((item) => item.key === key)
  if (option?.base_url) draft.base_url = option.base_url
}

async function load() {
  error.value = ''
  try {
    const fresh = await getModelConfig()
    data.value = fresh
    intentMode.value = fresh.intent_mode
    // 给每行备好「这家已登记的模型」（够填下拉就行；点「查看/刷新」再拉全量）
    for (const provider of new Set(fresh.rows.map((row) => row.provider))) {
      // 只拉目录里真有的那几家（JEV 那一行的供应商是锁死的，可能还没进目录）
      if (provider && provider !== 'custom' && fresh.providers.some((p) => p.key === provider)) {
        void ensureModels(provider)
      }
    }
    for (const row of fresh.rows) {
      const existing = drafts.value[row.key]
      if (existing?.dirty) continue
      drafts.value[row.key] = {
        ...draftFor(row),
        probe: existing?.probe ?? '',
        models: existing?.models ?? [],
      }
    }
  } catch (e: any) {
    error.value = e?.message ?? '读取失败'
  }
}

async function probe(row: ModelConfigRow) {
  const draft = draftOf(row)
  draft.probe = '连接中…'
  draft.models = []
  draft.dirty = true
  try {
    const result = await probeProvider(
      draft.base_url,
      draft.api_key || null,
      row.key,
      draft.provider,
    )
    // 只有**实时**清单才进候选框（2026-10-15 起没有"内置候选"这回事了）
    draft.models = result.source === 'live' ? result.models : []
    if (!result.ok) {
      draft.probe = `${result.note ? `${result.note} · ` : ''}${result.error}`
      return
    }
    if (result.context_window && !draft.context_window) {
      // 探测到的窗口只用来**显示**（window_auto）：它是「清单里第一个带窗口的模型」的值、
      // 未必就是你选的那一个 —— 写进库就成了错的覆盖值，所以保存时**不写**它。
      draft.context_window = formatWindow(result.context_window)
      draft.window_exact = result.context_window
      draft.window_auto = true
      draft.probe = `已连接 · ${result.models.length} 个模型 · 窗口 ${draft.context_window} 已回显`
    } else {
      draft.probe = `已连接 · ${result.models.length} 个模型`
    }
  } catch (e: any) {
    draft.probe = `连不上 · ${e?.message ?? e}`
  }
}

/**
 * 保存**这一行**（弹窗里那个按钮，用户口径：保存按钮在弹窗内部）。

 * 只提交改过的字段（与「留空＝不改」同一口径）；成功就关掉弹窗 —— 弹窗开着而没保存
 * 是两处状态，容易让人以为已经生效。
 */
async function saveRow(row: ModelConfigRow) {
  const draft = drafts.value[row.key]
  if (!draft) return
  const item: Record<string, unknown> = {}
  if (draft.base_url !== row.base_url) item.base_url = draft.base_url
  if (draft.model !== row.model) item.model = draft.model
  if (draft.api_key) item.api_key = draft.api_key
  else if (draft.clear_key && row.has_key) item.api_key = ''  // 显式清掉（留空＝不改）
  // 窗口那一格：**回显**（`window_auto`）时一个字都不写 —— 那一格显示的是后端解析出来的值
  // （行内 → 按模型查 → 兜底值），把它存下来等于给这一行加一个覆盖值，之后换模型就成了脏数据。
  // 用户手改过才写：文本没变按原值送（`195.3K` 这类写法来回换算会失真），清空则送 0（撤掉覆盖）。
  if (!draft.window_auto) {
    const text = draft.context_window.trim()
    const window = !text
      ? 0
      : text === formatWindow(draft.window_exact) && draft.window_exact > 0
        ? draft.window_exact
        : (parseWindow(text) ?? -1)
    if (window < 0) {
      dialogError.value = '窗口那一格认不出：填 128K / 1M 这样的值'
      return
    }
    if (window !== row.context_window) item.context_window = window
  }
  busy.value = true
  dialogError.value = ''
  notice.value = ''
  try {
    if (Object.keys(item).length) {
      data.value = await putModelConfig({ [row.key]: item })
      notice.value = `「${row.label}」已保存，下一轮生效。`
      await load()
    } else {
      notice.value = '没有改动。'
    }
    closeDialog()
  } catch (e: any) {
    dialogError.value = e?.message ?? '保存失败'
  } finally {
    busy.value = false
  }
}

/** 意图识别那一关（JEV / 大模型）：一个下拉，改完当场存 —— 没有「弹窗」可放保存按钮。 */
async function saveIntentMode() {
  busy.value = true
  error.value = ''
  try {
    data.value = await putModelConfig({}, intentMode.value)
    notice.value = '意图识别口径已保存，下一轮生效。'
  } catch (e: any) {
    error.value = e?.message ?? '保存失败'
  } finally {
    busy.value = false
  }
}

async function restore(row: ModelConfigRow) {
  if (!window.confirm(`清空「${row.label}」这一行？填过的地址、模型与 key 都会被删掉。`)) {
    return
  }
  busy.value = true
  error.value = ''
  try {
    data.value = await deleteModelConfig(row.key)
    delete drafts.value[row.key]
    await load()
    notice.value = `「${row.label}」已清空。`
  } catch (e: any) {
    dialogError.value = e?.message ?? '清空失败'
  } finally {
    busy.value = false
  }
}

// 切到模型配置那一栏才拉数据（immediate：从那一栏进来也算）
watch(
  panel,
  (key) => {
    if (key === 'models' && data.value === null) void load()
    if (key === 'basic' && appSettings.value === null) void loadRuntime()
  },
  { immediate: true },
)

/** Esc：弹窗开着就只关弹窗（别把整页和草稿一起关掉），没弹窗才返回。 */
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  if (editing.value) {
    closeDialog()
    return
  }
  emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="page">
    <header class="page-head">
      <button class="icon-btn" type="button" aria-label="返回" title="返回（Esc）" @click="emit('close')">
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <path
            d="M9.5 3.5L5 8l4.5 4.5"
            fill="none"
            stroke="currentColor"
            stroke-width="1.4"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <h2>设置</h2>
      <!-- 模型那一栏的保存按钮在**每个配置弹窗内部**（用户口径）；这里只剩基本设置那一个 -->
      <button
        v-if="panel === 'basic'"
        class="btn btn-primary"
        type="button"
        :disabled="busy"
        @click="saveRuntime()"
      >
        {{ busy ? '保存中…' : '保存' }}
      </button>
    </header>

    <p v-if="error" class="page-banner page-banner-error">{{ error }}</p>
    <p v-else-if="notice" class="page-banner">{{ notice }}</p>

    <div class="page-body">
      <div class="page-inner">
        <nav class="set-menu" aria-label="设置分类">
          <button
            v-for="item in PANELS"
            :key="item.key"
            class="set-menu-item"
            :class="{ 'is-on': panel === item.key }"
            type="button"
            :aria-current="panel === item.key ? 'true' : undefined"
            @click="emit('panel', item.key)"
          >
            {{ item.label }}
          </button>
        </nav>

        <div class="set-panel">
          <!-- 基本设置：本机显示偏好，改动立刻生效 -->
          <template v-if="panel === 'basic'">
            <section class="section">
              <h3>界面</h3>
              <div class="set-row">
                <span class="set-label">默认打开的右栏标签</span>
                <select
                  class="input set-control"
                  :value="defaultPanelTab"
                  @change="setDefaultPanelTab(($event.target as HTMLSelectElement).value as PanelTab)"
                >
                  <option v-for="item in TAB_OPTIONS" :key="item.key" :value="item.key">
                    {{ item.label }}
                  </option>
                </select>
              </div>
              <div class="set-row">
                <span class="set-label">窗口布局</span>
                <div class="set-control">
                  <button class="btn" type="button" @click="resetLayout">恢复默认布局</button>
                </div>
              </div>
            </section>

            <!-- 运行期配置：存后端库、**改完立刻生效**（主智能体每轮现建，不缓存） -->
            <section v-for="group in runtimeGroups" :key="group.name" class="section">
              <h3>{{ group.name }}</h3>
              <div v-for="item in group.items" :key="item.key" class="set-row">
                <span class="set-label">
                  {{ item.label }}
                  <span v-if="item.source === 'db'" class="set-tag">已改过</span>
                </span>
                <input
                  class="input set-control"
                  :class="item.kind === 'str' ? 'input-wide' : 'input-num'"
                  :value="runtimeValue(item)"
                  @input="runtimeDrafts[item.key] = ($event.target as HTMLInputElement).value"
                />
              </div>
            </section>
          </template>

          <!-- 外观：主题 / 代码字号 / 对话列宽，都是立刻生效 -->
          <template v-else-if="panel === 'look'">
            <section class="section">
              <h3>外观</h3>
              <div class="set-row">
                <span class="set-label">主题</span>
                <div class="set-control seg" role="group" aria-label="主题">
                  <button
                    v-for="item in THEME_OPTIONS"
                    :key="item.key"
                    class="seg-btn"
                    :class="{ 'is-on': themeMode === item.key }"
                    type="button"
                    :aria-pressed="themeMode === item.key"
                    @click="setTheme(item.key)"
                  >
                    {{ item.label }}
                  </button>
                </div>
              </div>
              <div class="set-row">
                <span class="set-label">代码字号</span>
                <div class="set-control set-inline">
                  <input
                    class="range"
                    type="range"
                    :min="CODE_SIZE.min"
                    :max="CODE_SIZE.max"
                    step="1"
                    :value="codeSize"
                    aria-label="代码字号"
                    @input="setCodeSize(Number(($event.target as HTMLInputElement).value))"
                  />
                  <output class="set-value">{{ codeSize }}px</output>
                </div>
              </div>
              <div class="set-row">
                <span class="set-label">对话列宽</span>
                <div class="set-control seg" role="group" aria-label="对话列宽">
                  <button
                    v-for="item in WIDTH_OPTIONS"
                    :key="item.key"
                    class="seg-btn"
                    :class="{ 'is-on': chatWidth === item.key }"
                    type="button"
                    :aria-pressed="chatWidth === item.key"
                    @click="setChatWidth(item.key)"
                  >
                    {{ item.label }}
                  </button>
                </div>
              </div>
            </section>
          </template>

          <!-- 模型配置：按智能体配，存后端库；改完要保存 + 重启后端 -->
          <template v-else-if="panel === 'models'">
            <!-- 各智能体：平铺那几行 + 「编码」按角色显示的那一块 -->
            <section class="section">
              <h3>{{ sectionLabel('agents') }}</h3>
              <ModelEntry
                v-for="row in flatAgentRows"
                :key="row.key"
                :label="row.label"
                :effective-model="eff(row).model"
                :effective-window="eff(row).window"
                :effective-source="eff(row).source"
                :window-source="eff(row).window_source"
                :configured-model="row.model"
                @configure="openDialog(row)"
              />

              <!-- 「编码」是一主三从：按**角色**显示，规划 / 评审点进去编辑的就是「方案」「审查」 -->
              <div v-if="codingGroup.length" class="subgroup">
                <h4>编码（一主三从）</h4>
                <ModelEntry
                  v-for="role in codingGroup"
                  :key="role.role"
                  :label="role.label"
                  :effective-model="eff(rowOf(role.row) ?? NO_ROW).model"
                  :effective-window="eff(rowOf(role.row) ?? NO_ROW).window"
                  :effective-source="eff(rowOf(role.row) ?? NO_ROW).source"
                  :window-source="eff(rowOf(role.row) ?? NO_ROW).window_source"
                  :configured-model="rowOf(role.row)?.model ?? ''"
                  :reused="role.reused"
                  :reused-from="rowLabel(role.row)"
                  @configure="openRoleRow(role.row)"
                />
              </div>
            </section>

            <section class="section">
              <h3>{{ sectionLabel('global') }}</h3>
              <ModelEntry
                v-for="row in globalRows"
                :key="row.key"
                :label="row.label"
                :effective-model="eff(row).model"
                :effective-window="eff(row).window"
                :effective-source="eff(row).source"
                :window-source="eff(row).window_source"
                :configured-model="row.model"
                :detail-text="rowDetail(row)"
                @configure="openDialog(row)"
              />
            </section>

            <section class="section">
              <h3>{{ sectionLabel('intent') }}</h3>
              <!-- **严格二选一**（2026-10-15 用户口径）：选哪一侧就只走哪一侧，不互相兜底 -->
              <div class="set-row">
                <span class="set-label">走哪一关</span>
                <div class="set-control seg" role="group" aria-label="意图识别走哪一关">
                  <button
                    v-for="mode in INTENT_MODES"
                    :key="mode.key"
                    class="seg-btn"
                    :class="{ 'is-on': intentMode === mode.key }"
                    type="button"
                    :aria-pressed="intentMode === mode.key"
                    @click="pickIntentMode(mode.key)"
                  >
                    {{ mode.label }}
                  </button>
                </div>
              </div>
              <p v-if="intentMode === 'jev' && !data?.jev_configured" class="field-note field-note-warn">
                JEV 这一侧还没配齐（base_url + api_key）：意图判定会落关键字口径
              </p>
              <ModelEntry
                v-if="jevRow"
                :label="jevRow.label"
                :effective-model="''"
                :effective-window="0"
                :effective-source="data?.jev_configured ? 'row' : 'unset'"
                :detail-text="jevDetail(jevRow)"
                @configure="openDialog(jevRow)"
              />
              <ModelEntry
                v-for="row in classifierRows"
                :key="row.key"
                :label="row.label"
                :effective-model="eff(row).model"
                :effective-window="eff(row).window"
                :effective-source="eff(row).source"
                :window-source="eff(row).window_source"
                :configured-model="row.model"
                @configure="openDialog(row)"
              />
            </section>

          </template>

          <!-- 提供商目录：改完刷新界面即生效（目录是每条请求现读库的） -->
          <template v-else>
            <ProvidersPanel />
          </template>
        </div>
      </div>
    </div>

    <!-- 一行模型配置的弹窗：点列表里的「配置」打开（三类：供应商 / base_url+api_key / 模型+窗口） -->
    <ModelConfigDialog
      v-if="editing"
      :label="editing.label"
      :draft="draftOf(editing)"
      :providers="providers"
      :known-models="modelsByProvider[draftOf(editing).provider] ?? []"
      :has-key="editing.has_key"
      :masked-key="editing.api_key_masked"
      :refreshing="refreshing === draftOf(editing).provider"
      :busy="busy"
      :error="dialogError"
      :locked="editing.provider_locked"
      :lock-note="editing.provider_locked ? `这一行固定用「${editing.provider}」这一家` : ''"
      @provider="pickProvider(editing, $event)"
      @probe="probe(editing)"
      @refresh-models="refreshModels($event)"
      @restore="restore(editing); closeDialog()"
      @save="saveRow(editing)"
      @need-models="ensureModels($event)"
      @close="closeDialog"
    />
  </div>
</template>
