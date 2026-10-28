/**
 * 从一段完整 Markdown 中切出 Mermaid 围栏。
 *
 * 正文只在任务结束后 Markdown 化，所以这里只处理已闭合的围栏；流式阶段仍按纯文本显示。
 * 只切开 Mermaid 不会破坏列表、表格或其他代码块 —— 普通 Markdown 仍交给 marked 一次解析。
 */
export type MarkdownSegment =
  | { kind: 'markdown'; source: string }
  | { kind: 'mermaid'; source: string }

export type MermaidDiagramKind =
  | 'flowchart'
  | 'sequence'
  | 'state'
  | 'class'
  | 'er'
  | 'gantt'
  | 'pie'
  | 'mindmap'
  | 'timeline'
  | 'journey'
  | 'other'

type MermaidApi = (typeof import('mermaid'))['default']

const OPEN_FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*([^\r\n]*)$/
const EDGE_LABEL = /\|([^|\n]+)\|/g
const DOTTED_EDGE_LABEL = /(\s-\.\s+)(?!["'])(.+?)(\s+\.->)/g
const NODE_LABEL_WITH_AT = /\[([^\[\]\n]*@[^\[\]\n]*)\]/g
const SEQUENCE_RESERVED = new Set([
  'end',
  'alt',
  'else',
  'opt',
  'par',
  'and',
  'loop',
  'rect',
  'note',
  'over',
  'activate',
  'deactivate',
  'autonumber',
  'participant',
  'actor',
  'as',
  'critical',
  'break',
  'box',
  'off',
  'on',
])

let mermaidLoader: Promise<MermaidApi> | null = null
let renderQueue: Promise<void> = Promise.resolve()
let diagramSeq = 0

function closingFence(fence: string): RegExp {
  const char = fence[0] === '`' ? '`' : '~'
  return new RegExp(`^ {0,3}${char}{${fence.length},}[ \\t]*$`)
}

export function splitMermaidBlocks(source: string): MarkdownSegment[] {
  const normalized = source.replace(/\r\n?/g, '\n')
  const lines = normalized.split('\n')
  const segments: MarkdownSegment[] = []
  let markdownStart = 0
  let index = 0

  while (index < lines.length) {
    const opener = OPEN_FENCE.exec(lines[index])
    const language = opener?.[2].trim().split(/\s+/, 1)[0]?.toLowerCase()
    if (!opener || language !== 'mermaid') {
      index += 1
      continue
    }

    const close = closingFence(opener[1])
    let end = index + 1
    while (end < lines.length && !close.test(lines[end])) end += 1
    if (end >= lines.length) {
      // 未闭合时不抢 marked 的工作；这种内容只会出现在异常历史里。
      index += 1
      continue
    }

    if (index > markdownStart) {
      segments.push({
        kind: 'markdown',
        source: lines.slice(markdownStart, index).join('\n'),
      })
    }
    segments.push({
      kind: 'mermaid',
      source: lines.slice(index + 1, end).join('\n').trimEnd(),
    })
    index = end + 1
    markdownStart = index
  }

  if (markdownStart < lines.length) {
    segments.push({
      kind: 'markdown',
      source: lines.slice(markdownStart).join('\n'),
    })
  }
  return segments.length ? segments : [{ kind: 'markdown', source: normalized }]
}

export function mermaidDiagramKind(source: string): MermaidDiagramKind {
  const first = source
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith('%%'))
    ?.toLowerCase()
  if (!first) return 'other'
  if (first.startsWith('flowchart') || first.startsWith('graph ')) return 'flowchart'
  if (first === 'sequencediagram') return 'sequence'
  if (first.startsWith('statediagram')) return 'state'
  if (first.startsWith('classdiagram')) return 'class'
  if (first === 'erdiagram') return 'er'
  if (first === 'gantt') return 'gantt'
  if (first.startsWith('pie')) return 'pie'
  if (first === 'mindmap') return 'mindmap'
  if (first === 'timeline') return 'timeline'
  if (first === 'journey') return 'journey'
  return 'other'
}

export function mermaidDiagramLabel(kind: MermaidDiagramKind): string {
  return {
    flowchart: '流程图',
    sequence: '时序图',
    state: '状态图',
    class: '类图',
    er: 'ER 图',
    gantt: '甘特图',
    pie: '饼图',
    mindmap: '思维导图',
    timeline: '时间线',
    journey: '用户旅程图',
    other: '图表',
  }[kind]
}

function serializeRender<T>(task: () => Promise<T>): Promise<T> {
  const next = renderQueue.then(task, task)
  renderQueue = next.then(
    () => undefined,
    () => undefined,
  )
  return next
}

export async function renderMermaidDiagram(
  source: string,
  themeVariables: Record<string, string | boolean>,
): Promise<string> {
  const mermaid = await (mermaidLoader ??= import('mermaid').then(
    (module) => module.default,
  ))
  const result = await serializeRender(async () => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      suppressErrorRendering: true,
      theme: 'base',
      themeVariables,
      flowchart: { htmlLabels: true, useMaxWidth: false, curve: 'linear' },
      sequence: { useMaxWidth: false, wrap: true },
      state: { useMaxWidth: false },
    })
    return mermaid.render(
      `harness-mermaid-${++diagramSeq}`,
      repairMermaidSource(source),
    )
  })
  return result.svg
}

/**
 * Mermaid 的 sequenceDiagram 使用 `participant ID as 显示名`，ID 不能撞控制关键字。
 * `OFF` 会被词法器识别成 `off` token，整张图报 lexical/parse error。这里只改参与者 ID，
 * 显示名不动。
 */
function repairSequenceIdentifiers(source: string): string {
  if (!source.split('\n').some((line) => line.trim() === 'sequenceDiagram')) {
    return source
  }
  const declaration = /^(\s*(?:participant|actor)\s+)([A-Za-z_][A-Za-z0-9_]*)(\s+as\s+[^\n]+)$/gm
  const renamed = new Map<string, string>()
  for (const match of source.matchAll(declaration)) {
    const identifier = match[2]
    if (SEQUENCE_RESERVED.has(identifier.toLowerCase())) {
      renamed.set(identifier, `${identifier}_diagram`)
    }
  }
  let repaired = source
  for (const [from, to] of renamed) {
    repaired = repaired.replace(new RegExp(`\\b${from}\\b`, 'g'), to)
  }
  return repaired
}

/**
 * 修复模型常见的 Mermaid 标签语法：
 * - `|POST /x/{id}|` 的花括号会被当成菱形节点起点；
 * - `-. 3. MQ .->` 的编号句点会打乱虚线箭头的词法解析；
 * - `[<br/>@Transactional...]` 的 `@` 会被当成 link id。
 *
 * 解决方式都是给对应文本补双引号；已经带引号的标签保持原样。
 */
export function repairMermaidSource(source: string): string {
  const sequence = repairSequenceIdentifiers(source)
  const atLabels = sequence.replace(
    NODE_LABEL_WITH_AT,
    (whole: string, label: string) => {
      const text = label.trim()
      if (text.startsWith('"') && text.endsWith('"')) return whole
      return `["${text.replace(/"/g, '&quot;')}"]`
    },
  )
  const dotted = atLabels.replace(
    DOTTED_EDGE_LABEL,
    (_whole: string, before: string, label: string, after: string) =>
      `${before}"${label.trim().replace(/"/g, '&quot;')}"${after}`,
  )
  return dotted.replace(EDGE_LABEL, (whole: string, label: string) => {
    const text = label.trim()
    if (!/[{}]/.test(text) || (text.startsWith('"') && text.endsWith('"'))) {
      return whole
    }
    return `|"${text.replace(/"/g, '&quot;')}"|`
  })
}
