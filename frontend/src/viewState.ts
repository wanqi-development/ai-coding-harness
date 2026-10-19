/**
 * 「现在在哪一页」记在 URL 的 hash 上：`#settings/<panel>`；hash 为空 ＝ 聊天页。
 *
 * 2026-10-14 用户口径：「刷新页面之后，回到原来的页面，不要跳回主页，比如刚才在设置页面，
 * 刷新了一下还在设置页面才是对的」。
 *
 * 三处刻意的做法：
 *
 * - **用 hash 而不是 localStorage**：hash 不会发到服务端（后端一行都不用改），刷新天然还原，
 *   以后想复制链接给别人也直接能用；
 * - **不引路由库**：总共两页，一个 hash 就够，路由库带来的构建与心智成本不值；
 * - **写用 `replaceState`**：切页与切面板不往历史里塞条目 —— 否则在设置里点三个面板，
 *   要按三次「返回」才回到聊天。
 */

export type AppView = 'chat' | 'settings'
export type SettingsPanel = 'basic' | 'look' | 'models' | 'providers'

const PANELS: SettingsPanel[] = ['basic', 'look', 'models', 'providers']

/** 认不出的 hash 一律当聊天页（手改错地址不该白屏）。 */
export function readView(): { view: AppView; panel: SettingsPanel } {
  const raw = location.hash.replace(/^#/, '').trim().toLowerCase()
  if (!raw.startsWith('settings')) return { view: 'chat', panel: 'basic' }
  const rest = raw.slice('settings'.length).replace(/^\//, '')
  const panel = PANELS.find((item) => item === rest)
  return { view: 'settings', panel: panel ?? 'basic' }
}

/** 把当前页写进 hash（聊天页就清掉它）。 */
export function writeView(view: AppView, panel: SettingsPanel): void {
  const target = view === 'settings' ? `#settings/${panel}` : ''
  const current = location.hash
  if (current === target) return // 同值就别写，免得白跑一次 replaceState
  const { pathname, search } = location
  history.replaceState(null, '', target || `${pathname}${search}`)
}

/** 订阅 hash 变化（手改地址、或用户用前进/后退走到别的 hash）。返回取消订阅的函数。 */
export function watchView(apply: (next: { view: AppView; panel: SettingsPanel }) => void): () => void {
  const onHash = () => apply(readView())
  window.addEventListener('hashchange', onHash)
  return () => window.removeEventListener('hashchange', onHash)
}
