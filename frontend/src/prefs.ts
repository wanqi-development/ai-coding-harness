/**
 * 本机界面偏好：外观（主题 / 代码字号 / 对话列宽）与基本设置（默认打开的右栏标签）。
 *
 * **为什么存 localStorage**：这是本机、单人的显示偏好，后端没有「用户设置」表 ——
 * 为它加表 + 迁移 + 接口不划算，和栏宽同一口径（见 `splitter.ts`）。读写都容错：
 * 隐私模式下 localStorage 会抛异常，记不住就算了，不能因此白屏。
 *
 * **主题怎么落地**：只往 `<html>` 上写 `data-theme="light|dark"`，具体颜色全在
 * `style.css` 的 `:root` / `:root[data-theme='dark']` 两组令牌里 —— 组件里不判断主题。
 */
import { ref } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ChatWidth = 'compact' | 'standard' | 'wide'
export type PanelTab = 'code' | 'memory' | 'pulls' | 'logs' | 'usage'

/** 代码字号的可调范围（`--code-size` 的取值，单位 px）。 */
export const CODE_SIZE = { min: 12, max: 16, default: 14 } as const

/**
 * 对话列宽三档。`standard` **必须**与 `style.css` 里 `--chat-max` 的原值逐字一致 ——
 * 它就是改版前一直在用的那档，切到它会写回同一个值。
 */
export const CHAT_WIDTH: Record<ChatWidth, string> = {
  compact: 'clamp(680px, 46vw, 1040px)',
  standard: 'clamp(760px, 60vw, 1400px)',
  wide: 'clamp(900px, 72vw, 1800px)',
}

export const CHAT_WIDTH_LABEL: Record<ChatWidth, string> = {
  compact: '紧凑',
  standard: '标准',
  wide: '宽',
}

const KEY = {
  theme: 'ui.theme',
  codeSize: 'ui.codeSize',
  chatWidth: 'ui.chatWidth',
  tab: 'ui.panelTab',
} as const

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* 存不下就这一次不存：偏好只是体验，不影响功能 */
  }
}

function asTheme(value: string | null): ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system'
}

function asChatWidth(value: string | null): ChatWidth {
  return value === 'compact' || value === 'standard' || value === 'wide' ? value : 'standard'
}

function asTab(value: string | null): PanelTab {
  return value === 'memory' || value === 'pulls' || value === 'logs' || value === 'usage'
    ? value
    : 'code'
}

function asCodeSize(value: string | null): number {
  const size = Number(value)
  if (!Number.isFinite(size)) return CODE_SIZE.default
  return Math.min(CODE_SIZE.max, Math.max(CODE_SIZE.min, Math.round(size)))
}

export const themeMode = ref<ThemeMode>(asTheme(read(KEY.theme)))
export const codeSize = ref<number>(asCodeSize(read(KEY.codeSize)))
export const chatWidth = ref<ChatWidth>(asChatWidth(read(KEY.chatWidth)))
/** 新建/刷新后默认打开哪个右栏标签（只影响进入界面的初始值，之后点标签照旧自由切）。 */
export const defaultPanelTab = ref<PanelTab>(asTab(read(KEY.tab)))

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')

/** 把主题落到 `<html data-theme>`：`system` 时看系统当前是不是深色。 */
function applyTheme(): void {
  const dark = themeMode.value === 'dark' || (themeMode.value === 'system' && darkQuery.matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

function applyCodeSize(): void {
  document.documentElement.style.setProperty('--code-size', `${codeSize.value}px`)
}

function applyChatWidth(): void {
  document.documentElement.style.setProperty('--chat-max', CHAT_WIDTH[chatWidth.value])
}

export function setTheme(mode: ThemeMode): void {
  themeMode.value = mode
  write(KEY.theme, mode)
  applyTheme()
}

export function setCodeSize(size: number): void {
  codeSize.value = Math.min(CODE_SIZE.max, Math.max(CODE_SIZE.min, Math.round(size)))
  write(KEY.codeSize, String(codeSize.value))
  applyCodeSize()
}

export function setChatWidth(width: ChatWidth): void {
  chatWidth.value = width
  write(KEY.chatWidth, width)
  applyChatWidth()
}

export function setDefaultPanelTab(tab: PanelTab): void {
  defaultPanelTab.value = tab
  write(KEY.tab, tab)
}

/**
 * 启动时调一次（`main.ts` 里、挂载之前）：把记住的偏好落到 `<html>` 上。
 * 只在这里挂系统主题变化的监听 —— 选「跟随系统」的人切换系统外观时要立刻跟着变。
 */
export function initPrefs(): void {
  applyTheme()
  applyCodeSize()
  applyChatWidth()
  darkQuery.addEventListener('change', () => {
    if (themeMode.value === 'system') applyTheme()
  })
}
