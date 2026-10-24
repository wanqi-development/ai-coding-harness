import type { RunTrace, TodoItem } from './api'

/** 前端 run 树节点：一个 run_id 只出现一次，子节点按到达顺序追加。 */
export interface RunNode {
  run_id: string
  parent_run_id: string
  root_run_id: string
  node_kind: string
  name: string
  agent: string
  status: string
  content: string
  reasoning: string
  toolInput?: unknown
  toolOutput: string
  todos: TodoItem[]
  events: Array<{ phase: string; text: string }>
  children: RunNode[]
}

export interface RunTree {
  root_id: string
  root: RunNode
  nodes: Record<string, RunNode>
}

function nodeOf(trace: RunTrace): RunNode {
  return {
    run_id: trace.run_id,
    parent_run_id: trace.parent_run_id,
    root_run_id: trace.root_run_id,
    node_kind: trace.node_kind,
    name: trace.name || trace.agent || '',
    agent: trace.agent || '',
    status: trace.status || '',
    content: '',
    reasoning: '',
    toolInput:
      trace.node_kind === 'tool'
        ? trace.data?.input
        : trace.node_kind === 'handoff'
          ? trace.data
          : undefined,
    toolOutput: '',
    todos: [],
    events: [],
    children: [],
  }
}

export function createRunTree(trace: RunTrace): RunTree {
  const root = nodeOf(trace)
  return {
    root_id: trace.root_run_id || trace.run_id,
    root,
    nodes: { [root.run_id]: root },
  }
}

function ensureNode(tree: RunTree, trace: RunTrace): RunNode {
  let node = tree.nodes[trace.run_id]
  if (!node) {
    node = nodeOf(trace)
    tree.nodes[node.run_id] = node
    const parent = tree.nodes[trace.parent_run_id]
    if (parent && !parent.children.some((child) => child.run_id === node!.run_id)) {
      parent.children.push(node)
    }
  }
  return node
}

function eventText(trace: RunTrace): string {
  const who = trace.agent || trace.name || trace.node_kind
  if (trace.phase === 'start') return `${who} · start`
  if (trace.phase === 'finish') return `${who} · ${trace.status || 'finish'}`
  if (trace.node_kind === 'todos') return `${who} · todos`
  return `${who} · delta`
}

/** 把一个后端 run 帧合并进树；调用方只在 root_run_id 对得上时调用。 */
export function applyRunTrace(tree: RunTree, trace: RunTrace): void {
  const node = ensureNode(tree, trace)
  const parent = tree.nodes[node.parent_run_id]
  if (parent && !parent.children.some((child) => child.run_id === node.run_id)) {
    parent.children.push(node)
  }
  node.name = trace.name || node.name
  node.agent = trace.agent || node.agent
  node.status = trace.status || node.status
  if (trace.content) {
    if (trace.node_kind === 'model' && trace.data?.part === 'reasoning') {
      node.reasoning += trace.content
    } else if (trace.phase === 'finish' && trace.node_kind === 'tool') {
      node.toolOutput += trace.content
    } else {
      node.content += trace.content
    }
  }
  if (trace.node_kind === 'tool') {
    if (trace.phase === 'start') node.toolInput = trace.data?.input
    if (trace.phase === 'finish') node.toolOutput = trace.content || node.toolOutput
  }
  if (trace.node_kind === 'handoff' && trace.phase === 'start') {
    node.toolInput = trace.data
  }
  if (trace.node_kind === 'todos' && Array.isArray(trace.data?.items)) {
    node.todos = trace.data.items
  }
  node.events.push({ phase: trace.phase, text: eventText(trace) })
}
