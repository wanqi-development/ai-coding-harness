/**
 * 流式正文分块。
 *
 * 背景：流式期间每 100ms 会把「正在长的那条回复」整段重渲染一次 —— marked 解析 → hljs 高亮 →
 * DOMPurify 消毒 → 整段 innerHTML 替换 → 紧接着读 scrollHeight 强制一次重排版。实测一帧
 * 15~17ms（180 条消息的会话里，9ms 是强制重排版），按 10 帧/秒折算就是每秒 150ms 以上，
 * 用户在这一秒里滚动、点击都会撞上这些帧。
 *
 * 办法：把长回复切成「已封口」的小块。封口之后内容不再变，模板上的 `v-memo` 也就不再重渲染它，
 * 每帧只碰最后那一小块（实测 0.2ms，差两个数量级）。
 *
 * 切点必须落在**空行**上，而且不能切进代码围栏、列表、表格 —— 切错了会让有序列表重新编号、
 * 把一张表断成两张。所以宁可这一块再长一会儿（安全点找不到就不切），也不能切出错误渲染。
 */

/** 一块正文：与 `App.vue` 里的 `Part` 同形，这里只关心 `text` 那一支。 */
export interface BlockPart {
  kind: string
  content?: string
  sealed?: boolean
}

/**
 * 一块长到多少字符就找机会封口。
 *
 * 实测（180 条消息的会话、13 KB 回答、每帧 40 字符）：封口块越小，单帧越便宜 ——
 * 阈值 3000 时中位 1.40ms，1500 时 0.80ms，1000 时 0.70ms，600 时 0.60ms。再往下收益就平了，
 * 而块数会一直涨（13 KB 正文：3000→5 块、1500→12 块、600→15 块），所以取 1500 这个拐点。
 * 相邻块在同一个气泡里、段间距与没切开时一致，块数只影响 DOM 节点数，不影响观感。
 */
export const BLOCK_SOFT_LIMIT = 1500

/** 行首围栏：三个及以上的反引号或波浪号（CommonMark 的 fenced code）。 */
const FENCE = /^[ \t]*(?:`{3,}|~{3,})/

/** 空行之后以这些开头的，切开会改变渲染结果：列表项、表格行、引用段。 */
const UNSAFE_START = /^(?:[*+-]\s|\d+[.)]\s|\||>)/

/** 文本里还有没闭合的围栏吗（围栏行数为奇数就是没闭合）。 */
export function fenceOpen(text: string): boolean {
  let n = 0
  for (const line of text.split('\n')) if (FENCE.test(line)) n++
  return n % 2 === 1
}

/**
 * 一段 code token 的原文是不是「以闭合围栏收尾」。
 *
 * 流式中的围栏还没闭合，这段代码每帧都在变 —— 高亮必然缓存未命中（实测 4.6ms/块/帧），
 * 所以这种情况先按纯文本渲染，等闭合的那一帧再上色。
 */
export function fenceClosed(raw: string): boolean {
  return /(?:^|\n)[ \t]*(?:`{3,}|~{3,})[ \t]*\n?$/.test(raw)
}

/** 左半段可以独立成篇吗（围栏闭合、别把表格从中间断开）。 */
function tailSafe(left: string, right: string): boolean {
  if (fenceOpen(left)) return false
  // 右侧顶格缩进 = 上一段的续行（嵌套列表、段落换行），切开就断了
  if (/^[ \t]/.test(right)) return false
  if (UNSAFE_START.test(right)) return false
  const lastLine = left.replace(/[ \t]+$/, '').split('\n').pop() ?? ''
  return !lastLine.trimStart().startsWith('|')
}

/**
 * 找一个**安全切点**：优先最后一个空行，不行就往前退。
 *
 * 返回右半段的起始下标（切完之后的左半长度）；找不到安全点返回 -1。
 */
export function safeCut(text: string): number {
  let at = text.lastIndexOf('\n\n')
  while (at > 0) {
    const right = text.slice(at + 2)
    if (right.trim() && tailSafe(text.slice(0, at), right)) return at + 2
    at = text.lastIndexOf('\n\n', at - 1)
  }
  return -1
}

/**
 * 最后一块正文太长就在安全点封口：切出来的左半段标记 `sealed`，右半段继续长。
 *
 * 返回是否真的切了（没切就说明还没到阈值，或者找不到安全点）。
 */
export function splitLongText(parts: BlockPart[]): boolean {
  const last = parts[parts.length - 1]
  if (last?.kind !== 'text' || last.sealed) return false
  const content = last.content ?? ''
  if (content.length < BLOCK_SOFT_LIMIT) return false
  const cut = safeCut(content)
  if (cut <= 0 || cut >= content.length) return false
  // 先改尾部再插入：`last` 是响应式对象的引用，改 content 不会打断它自己的追踪
  last.content = content.slice(cut)
  parts.splice(parts.length - 1, 0, {
    kind: 'text',
    content: content.slice(0, cut),
    sealed: true,
  })
  return true
}
