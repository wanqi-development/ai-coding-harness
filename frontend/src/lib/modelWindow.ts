/**
 * 「窗口大小」那一格的显示口径（2026-10-15 用户口径）：
 * **按量级选单位、带 K / M 后缀** —— ``1048576`` → ``1M``、``131072`` → ``128K``、
 * ``8192`` → ``8K``、``512`` → ``512``。
 *
 * ``K`` 一律**取整**：dashscope 给 qwen3.8-max 报的是 ``991808``（＝968.5625K）→ 显示 ``969K``
 * —— 一位小数是假精度。``M`` 除不尽时保留一位（``1.5M`` 比 ``2M`` 诚实）。
 *
 * 存库永远是 **tokens**：这两个函数只管「显示」与「解析回去」。进制用 1024 —— 各家报的
 * 1048576 / 131072 都是 1024 的整数倍，按 1000 换算会显示成 ``1048.6K`` 这种读不出来的数。
 */

const K = 1024
const M = 1024 * 1024

/** tokens → 界面上的紧凑写法；0 / 非法值回空串（＝没填）。 */
export function formatWindow(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens <= 0) return ''
  if (tokens >= M) {
    const value = tokens / M
    return `${Number.isInteger(value) ? value : value.toFixed(1)}M`
  }
  if (tokens >= K) {
    return `${Math.round(tokens / K)}K`
  }
  return String(tokens)
}

/**
 * 界面上的写法 → tokens：认 ``1M`` / ``128K`` / ``131072``（大小写、空格、小数都容）。
 * 认不出回 ``null``（调用方据此拦下保存，别把 0 悄悄存进库）。
 */
export function parseWindow(text: string): number | null {
  const raw = text.trim().toUpperCase()
  if (!raw) return null
  const match = /^(\d+(?:\.\d+)?)\s*([KM]?)$/.exec(raw)
  if (!match) return null
  const value = Number(match[1])
  if (!Number.isFinite(value) || value <= 0) return null
  const unit = match[2]
  const tokens = unit === 'M' ? value * M : unit === 'K' ? value * K : value
  return Math.round(tokens)
}
