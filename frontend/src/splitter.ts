/**
 * 侧栏宽度：拖拽调整、双击复位、方向键微调，宽度与收起状态记在 localStorage。
 *
 * 左侧会话栏与右侧标签栏共用这一份 —— 两边各写一遍，夹取范围、指针捕获、持久化必然漂。
 *
 * 三个细节：
 * - **方向**：左栏是「往右拖变宽」，右栏是「往左拖变宽」，所以右栏传 ``invert``；
 * - **夹取上限跟窗口走**：大屏按各自上限，窄屏压到 45vw 以内，别把中间的对话区挤没；
 * - **拖动期间给 body 挂 resizing**：cursor / user-select 交给 CSS，免得拖着拖着选中一堆文字。
 */

import { onBeforeUnmount, ref, watch } from 'vue'

export interface PanelWidthOptions {
  /** localStorage 键前缀，如 `ui.sidebar`（实际写 `<key>.width` / `<key>.collapsed`） */
  key: string
  defaultWidth: number
  min: number
  max: number
  /** 面板在对话区的**右侧**时传 true（拖动方向相反） */
  invert?: boolean
  /**
   * 收起状态要不要记住。默认记住；**左栏传 false** —— 它是导航，每次加载都该是展开的
   * （宽度仍然记住）。右栏是内容面板，收起往往是刻意的，所以保持记住。
   */
  persistCollapsed?: boolean
}

/** localStorage 在隐私模式 / 被禁时是会抛异常的：记不住就算了，不能因此白屏。 */
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
    /* 存不下就这一次不存：宽度与收起状态只是体验，不影响功能 */
  }
}

export function usePanelWidth(options: PanelWidthOptions) {
  const { key, defaultWidth, min, max, invert = false, persistCollapsed = true } = options

  const stored = Number(read(`${key}.width`))
  const width = ref(Number.isFinite(stored) && stored > 0 ? stored : defaultWidth)
  const collapsed = ref(persistCollapsed && read(`${key}.collapsed`) === '1')
  const dragging = ref(false)

  function clamp(value: number): number {
    // 窄屏上限跟着窗口走：两个面板都按上限摊开时，中间的对话列还得有地方
    const cap = Math.max(min, Math.min(max, Math.round(window.innerWidth * 0.45)))
    return Math.round(Math.min(Math.max(value, min), cap))
  }

  function persist(): void {
    if (dragging.value) return // 拖动中每帧都写没必要，松手时写一次
    write(`${key}.width`, String(width.value))
    if (persistCollapsed) write(`${key}.collapsed`, collapsed.value ? '1' : '0')
  }

  let startX = 0
  let startWidth = 0

  function onGripDown(event: PointerEvent): void {
    const grip = event.currentTarget as HTMLElement
    dragging.value = true
    startX = event.clientX
    startWidth = width.value
    grip.setPointerCapture(event.pointerId)
    document.body.classList.add('resizing')
    event.preventDefault()
  }

  function onGripMove(event: PointerEvent): void {
    if (!dragging.value) return
    const delta = (event.clientX - startX) * (invert ? -1 : 1)
    width.value = clamp(startWidth + delta)
  }

  function onGripUp(event: PointerEvent): void {
    if (!dragging.value) return
    dragging.value = false
    document.body.classList.remove('resizing')
    const grip = event.currentTarget as HTMLElement
    if (grip.hasPointerCapture(event.pointerId)) grip.releasePointerCapture(event.pointerId)
    persist()
  }

  /** 方向键微调：与拖动同一套方向约定（左栏 → 变宽，右栏 ← 变宽）。Shift 走大步。 */
  function onGripKey(event: KeyboardEvent): void {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    const step = (event.shiftKey ? 48 : 16) * (event.key === 'ArrowRight' ? 1 : -1)
    width.value = clamp(width.value + step * (invert ? -1 : 1))
    persist()
    event.preventDefault()
  }

  /** 双击热区：回到默认宽度。 */
  function reset(): void {
    width.value = clamp(defaultWidth)
    persist()
  }

  function toggle(): void {
    collapsed.value = !collapsed.value
    persist()
  }

  // 窗口变小后原来的宽度可能超出上限（比如 45vw 变小了），跟着收一下
  function onWindowResize(): void {
    width.value = clamp(width.value)
  }
  window.addEventListener('resize', onWindowResize)

  watch([width, collapsed], persist)
  onBeforeUnmount(() => {
    window.removeEventListener('resize', onWindowResize)
    document.body.classList.remove('resizing')
  })

  return {
    width,
    collapsed,
    dragging,
    // 给 aria-valuemin/max 用：没有它们，浏览器的无障碍层会把 aria-valuenow 当百分比，
    // 260 直接被截成 100（实测拿无障碍树验证宽度时才发现）
    min,
    max,
    onGripDown,
    onGripMove,
    onGripUp,
    onGripKey,
    reset,
    toggle,
  }
}
