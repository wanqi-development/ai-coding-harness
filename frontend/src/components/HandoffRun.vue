<script setup lang="ts">
import { computed } from 'vue'
import type { RunNode, RunTree } from '../lib/runs'

defineOptions({ name: 'HandoffRun' })

const props = withDefaults(
  defineProps<{ node: RunNode; tree?: RunTree; depth?: number }>(),
  { depth: 0 },
)

const fromForm = computed(() => String(props.node.toolInput ?? ''))
const handoffMeta = computed(() => {
  const value = props.node.toolInput
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
})
const logLines = computed(() => {
  const tree = props.tree
  if (!tree || props.depth !== 0) return []
  const lines: string[] = []
  const walk = (node: RunNode) => {
    for (const event of node.events) lines.push(`${node.name || node.run_id} · ${event.text}`)
    node.children.forEach(walk)
  }
  walk(tree.root)
  return lines
})

function inputText(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value ?? '')
  }
}
</script>

<template>
  <div class="handoff-run" :class="[`depth-${depth}`, node.status || '']">
    <div class="handoff-head">
      <span class="handoff-title">{{ depth === 0 ? 'handoffTask' : node.name || node.node_kind }}</span>
      <span v-if="depth === 0" class="handoff-flow">
        {{ handoffMeta.from || fromForm || '当前形态' }}
        <b>→</b>
        {{ handoffMeta.to || '' }}
      </span>
      <span v-else class="handoff-node-kind">{{ node.node_kind }}</span>
      <span class="handoff-status">{{ node.status || 'running' }}</span>
    </div>

    <div v-if="node.reasoning" class="handoff-reasoning">{{ node.reasoning }}</div>
    <div v-if="node.content" class="handoff-content">{{ node.content }}</div>

    <details v-if="node.node_kind === 'tool'" class="handoff-tool">
      <summary>{{ node.name || 'tool' }} {{ node.status }}</summary>
      <pre v-if="node.toolInput !== undefined">{{ inputText(node.toolInput) }}</pre>
      <pre v-if="node.toolOutput">{{ node.toolOutput }}</pre>
    </details>

    <ul v-if="node.todos.length" class="handoff-todos">
      <li v-for="(todo, i) in node.todos" :key="i" :class="todo.status">
        {{ todo.content }}
      </li>
    </ul>

    <div v-if="node.children.length" class="handoff-children">
      <HandoffRun
        v-for="child in node.children"
        :key="child.run_id"
        :node="child"
        :tree="tree"
        :depth="(depth ?? 0) + 1"
      />
    </div>

    <details v-if="depth === 0 && logLines.length" class="handoff-logs">
      <summary>运行日志（{{ logLines.length }}）</summary>
      <div v-for="(line, i) in logLines" :key="i" class="handoff-log-line">{{ line }}</div>
    </details>
  </div>
</template>

<style scoped>
.handoff-run {
  margin: 10px 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 9px;
  background: color-mix(in srgb, var(--surface) 88%, var(--accent) 12%);
}
.handoff-run.depth-1,
.handoff-run.depth-2 {
  margin-left: 12px;
  background: var(--surface);
}
.handoff-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.handoff-title { font-weight: 650; }
.handoff-flow,
.handoff-node-kind {
  color: var(--text-dim);
  font-size: 12px;
}
.handoff-status {
  margin-left: auto;
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
}
.handoff-run.error .handoff-status { color: var(--danger); }
.handoff-content {
  margin-top: 8px;
  white-space: pre-wrap;
  line-height: 1.65;
}
.handoff-reasoning {
  margin-top: 8px;
  white-space: pre-wrap;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.55;
}
.handoff-tool,
.handoff-logs {
  margin-top: 8px;
  font-size: 12px;
}
.handoff-tool summary,
.handoff-logs summary {
  cursor: pointer;
  color: var(--text-dim);
}
.handoff-tool pre {
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.handoff-todos {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 12px;
}
.handoff-todos .completed { color: var(--text-dim); text-decoration: line-through; }
.handoff-children { margin-top: 8px; }
.handoff-log-line {
  padding: 2px 0;
  color: var(--text-dim);
  font-family: var(--mono);
  white-space: pre-wrap;
}
</style>
