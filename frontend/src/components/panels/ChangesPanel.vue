<script setup lang="ts">
/**
 * 右栏内嵌页：本轮改动。
 *
 * 从聊天流里的「改动 N 个文件」角标点进来 —— **不是新标签**，而是盖在当前标签页上的一页；
 * 左上角「返回」回到原来的标签。文件列表在上，点一个文件在下面看**左右两排**的 diff。
 *
 * diff 数据仍是后端那份 unified 文本（`GET /sessions/{id}/repo/diff`），左右两列由前端拆：
 * 连续的 `-` / `+` 行按行配对，context 两边都显示。没有新增后端接口。
 */
import { computed, ref, watch } from 'vue'
import { getRepoDiff, type ChangesInfo, type FileChange } from '../../lib/api'
import { usePanelWidth } from '../../lib/splitter'

const props = defineProps<{ sessionId: string; changes: ChangesInfo }>()
const emit = defineEmits<{ close: [] }>()

const selected = ref('')
const cache = ref<Record<string, { text: string; truncated: boolean }>>({})
const loading = ref(false)
const error = ref('')
const expandedDirs = ref<Set<string>>(new Set())
const splitRef = ref<HTMLElement | null>(null)

const {
  width: treeWidth,
  collapsed: treeCollapsed,
  min: treeMin,
  max: treeMax,
  onGripDown: treeGripDown,
  onGripMove: treeGripMove,
  onGripUp: treeGripUp,
  onGripKey: treeGripKey,
  reset: resetTreeWidth,
} = usePanelWidth({
  key: 'ui.changesTree',
  defaultWidth: 220,
  min: 160,
  max: 520,
  container: splitRef,
  // diff 至少要留出一列代码的宽度，再扣掉中间 8px 拖拽热区。
  reserve: 320,
  // 文件树是可收起的内容区，但每次进入改动页先展开；宽度仍然记住。
  persistCollapsed: false,
})

const files = computed(() => props.changes.files)
const added = computed(() => files.value.reduce((sum, f) => sum + f.added, 0))
const deleted = computed(() => files.value.reduce((sum, f) => sum + f.deleted, 0))
const total = computed(() => props.changes.total ?? files.value.length)

const current = computed(() => cache.value[selected.value] ?? null)
const rows = computed(() => (current.value ? sideBySide(current.value.text) : []))

interface TreeFileNode {
  kind: 'file'
  name: string
  path: string
  file: FileChange
  added: number
  deleted: number
  fileCount: number
}

interface TreeDirectoryNode {
  kind: 'dir'
  name: string
  path: string
  children: ChangeTreeNode[]
  added: number
  deleted: number
  fileCount: number
}

type ChangeTreeNode = TreeFileNode | TreeDirectoryNode

interface VisibleTreeNode {
  node: ChangeTreeNode
  depth: number
  expanded: boolean
}

function aggregateTree(node: ChangeTreeNode): { added: number; deleted: number; count: number } {
  if (node.kind === 'file') {
    return { added: node.added, deleted: node.deleted, count: 1 }
  }
  let added = 0
  let deleted = 0
  let count = 0
  for (const child of node.children) {
    const totals = aggregateTree(child)
    added += totals.added
    deleted += totals.deleted
    count += totals.count
  }
  node.added = added
  node.deleted = deleted
  node.fileCount = count
  return { added, deleted, count }
}

function sortTree(nodes: ChangeTreeNode[]): void {
  nodes.sort((left, right) => {
    if (left.kind !== right.kind) return left.kind === 'file' ? -1 : 1
    return left.name.localeCompare(right.name)
  })
  for (const node of nodes) {
    if (node.kind === 'dir') sortTree(node.children)
  }
}

/**
 * 把没有文件、且只有一个目录子节点的中间路径压成一行，例如：
 * `a -> b -> c + file2, d/file1` 显示为 `a/b/c`，下面是 `file2` 和 `d/file1`。
 */
function compactDirectoryChains(nodes: ChangeTreeNode[]): ChangeTreeNode[] {
  return nodes.map((node) => {
    if (node.kind === 'file') return node

    const children = compactDirectoryChains(node.children)
    const onlyChild = children.length === 1 ? children[0] : null
    if (onlyChild?.kind === 'dir') {
      return {
        ...node,
        name: `${node.name}/${onlyChild.name}`,
        path: onlyChild.path,
        children: onlyChild.children,
      }
    }
    return { ...node, children }
  })
}

function buildChangeTree(changes: FileChange[]): ChangeTreeNode[] {
  const roots: ChangeTreeNode[] = []
  const folders = new Map<string, TreeDirectoryNode>()
  for (const file of changes) {
    const parts = file.path.split('/').filter(Boolean)
    let children = roots
    let currentPath = ''
    for (const part of parts.slice(0, -1)) {
      currentPath = currentPath ? `${currentPath}/${part}` : part
      let folder = folders.get(currentPath)
      if (!folder) {
        folder = {
          kind: 'dir',
          name: part,
          path: currentPath,
          children: [],
          added: 0,
          deleted: 0,
          fileCount: 0,
        }
        folders.set(currentPath, folder)
        children.push(folder)
      }
      children = folder.children
    }
    children.push({
      kind: 'file',
      name: parts.at(-1) ?? file.path,
      path: file.path,
      file,
      added: file.added,
      deleted: file.deleted,
      fileCount: 1,
    })
  }
  const compacted = compactDirectoryChains(roots)
  sortTree(compacted)
  for (const root of compacted) aggregateTree(root)
  return compacted
}

function collectDirectoryPaths(nodes: ChangeTreeNode[]): string[] {
  const paths: string[] = []
  for (const node of nodes) {
    if (node.kind !== 'dir') continue
    paths.push(node.path, ...collectDirectoryPaths(node.children))
  }
  return paths
}

const treeNodes = ref<ChangeTreeNode[]>(buildChangeTree(files.value))

const visibleTreeNodes = computed<VisibleTreeNode[]>(() => {
  const out: VisibleTreeNode[] = []
  const visit = (nodes: ChangeTreeNode[], depth: number) => {
    for (const node of nodes) {
      const expanded = node.kind === 'dir' && expandedDirs.value.has(node.path)
      out.push({ node, depth, expanded })
      if (node.kind === 'dir' && expanded) visit(node.children, depth + 1)
    }
  }
  visit(treeNodes.value, 0)
  return out
})

function toggleDirectory(node: TreeDirectoryNode): void {
  const next = new Set(expandedDirs.value)
  if (next.has(node.path)) next.delete(node.path)
  else next.add(node.path)
  expandedDirs.value = next
}

function expandAllDirectories(): void {
  expandedDirs.value = new Set(collectDirectoryPaths(treeNodes.value))
}

function collapseAllDirectories(): void {
  expandedDirs.value = new Set()
}

function mark(status: string): string {
  const head = status.charAt(0).toUpperCase()
  return head === 'A' || head === 'M' || head === 'D' || head === 'R' ? head : head || '?'
}

async function pick(file: FileChange): Promise<void> {
  selected.value = file.path
  error.value = ''
  if (cache.value[file.path]) return
  loading.value = true
  try {
    const result = await getRepoDiff(
      props.sessionId,
      props.changes.base,
      props.changes.head,
      file.path,
    )
    cache.value = {
      ...cache.value,
      [file.path]: { text: result.text, truncated: result.truncated },
    }
  } catch (e: any) {
    error.value = e?.message ?? '读取改动失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.changes,
  (info) => {
    cache.value = {}
    error.value = ''
    treeNodes.value = buildChangeTree(info.files)
    expandedDirs.value = new Set(collectDirectoryPaths(treeNodes.value))
    selected.value = info.files[0]?.path ?? ''
    if (selected.value) void pick(info.files[0])
  },
  { immediate: true },
)

interface DiffRow {
  left: string
  right: string
  /** 左边 / 右边的着色；`meta` 是 hunk 头（通栏） */
  leftKind: 'ctx' | 'del' | 'add' | 'empty' | 'meta'
  rightKind: 'ctx' | 'del' | 'add' | 'empty' | 'meta'
  meta?: string
}

/**
 * unified diff → 左右两排。
 *
 * - `@@` 头 → 通栏 meta；
 * - 连续 `-` 行与随后连续 `+` 行按行配对（短的一侧补空）；
 * - context 行两边都显示；
 * - `\ No newline at end of file` 之类归 meta。
 */
function sideBySide(text: string): DiffRow[] {
  const out: DiffRow[] = []
  const lines = text.split('\n')
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    if (
      line.startsWith('diff --git') ||
      line.startsWith('index ') ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ') ||
      line.startsWith('\\ ')
    ) {
      index += 1
      continue
    }
    if (line.startsWith('@@')) {
      out.push({ left: '', right: '', leftKind: 'meta', rightKind: 'meta', meta: line })
      index += 1
      continue
    }
    if (line.startsWith('-')) {
      const dels: string[] = []
      while (index < lines.length && lines[index].startsWith('-') && !lines[index].startsWith('--- ')) {
        dels.push(lines[index].slice(1))
        index += 1
      }
      const adds: string[] = []
      while (index < lines.length && lines[index].startsWith('+') && !lines[index].startsWith('+++ ')) {
        adds.push(lines[index].slice(1))
        index += 1
      }
      const count = Math.max(dels.length, adds.length)
      for (let i = 0; i < count; i += 1) {
        out.push({
          left: dels[i] ?? '',
          right: adds[i] ?? '',
          leftKind: i < dels.length ? 'del' : 'empty',
          rightKind: i < adds.length ? 'add' : 'empty',
        })
      }
      continue
    }
    if (line.startsWith('+')) {
      out.push({ left: '', right: line.slice(1), leftKind: 'empty', rightKind: 'add' })
      index += 1
      continue
    }
    const body = line.startsWith(' ') ? line.slice(1) : line
    out.push({ left: body, right: body, leftKind: 'ctx', rightKind: 'ctx' })
    index += 1
  }
  return out
}
</script>

<template>
  <div class="changes-page">
    <header class="changes-page-head">
      <button class="changes-back" type="button" title="返回" @click="emit('close')">← 返回</button>
      <span class="changes-page-title">本轮改动</span>
      <span class="changes-page-sum">
        {{ total }} 个文件 · <b class="chg-add">+{{ added }}</b>
        <b class="chg-del">−{{ deleted }}</b>
      </span>
    </header>

    <p class="changes-base">{{ changes.base }} … {{ changes.head }}</p>

    <div
      ref="splitRef"
      class="changes-split"
      :class="{ 'tree-collapsed': treeCollapsed }"
      :style="treeCollapsed ? undefined : { '--changes-tree-width': `${treeWidth}px` }"
    >
      <aside v-if="!treeCollapsed" id="changes-file-tree" class="changes-tree">
        <div class="changes-tree-head">
          <span class="changes-tree-title">文件 · {{ files.length }}</span>
          <div class="changes-tree-actions">
            <button type="button" title="展开全部目录" @click="expandAllDirectories">展开</button>
            <button type="button" title="折叠全部目录" @click="collapseAllDirectories">折叠</button>
            <button
              type="button"
              class="changes-tree-pane-toggle"
              title="收起文件树"
              aria-label="收起文件树"
              @click="treeCollapsed = true"
            >
              ‹
            </button>
          </div>
        </div>
        <div class="changes-tree-scroll">
          <button
            v-for="row in visibleTreeNodes"
            :key="row.node.path"
            type="button"
            class="changes-tree-row"
            :class="{
              'is-dir': row.node.kind === 'dir',
              'is-file': row.node.kind === 'file',
              active: row.node.kind === 'file' && row.node.path === selected,
            }"
            :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
            :title="row.node.path"
            :aria-expanded="row.node.kind === 'dir' ? row.expanded : undefined"
            @click="row.node.kind === 'dir' ? toggleDirectory(row.node) : pick(row.node.file)"
          >
            <span v-if="row.node.kind === 'dir'" class="changes-tree-chevron">
              {{ row.expanded ? '▾' : '▸' }}
            </span>
            <span
              v-else
              class="changes-file-mark changes-tree-mark"
              :data-status="mark(row.node.file.status)"
            >
              {{ mark(row.node.file.status) }}
            </span>
            <span class="changes-tree-name">{{ row.node.name }}</span>
            <span class="changes-tree-count">
              <b class="chg-add">+{{ row.node.added }}</b>
              <b class="chg-del">−{{ row.node.deleted }}</b>
            </span>
          </button>
          <p v-if="!files.length" class="changes-note">这一轮没有文件改动。</p>
        </div>
      </aside>
      <button
        v-else
        type="button"
        class="changes-tree-rail"
        title="展开文件树"
        aria-label="展开文件树"
        @click="treeCollapsed = false"
      >
        文件 ›
      </button>

      <div
        v-if="!treeCollapsed"
        class="changes-tree-grip"
        role="separator"
        aria-orientation="vertical"
        aria-controls="changes-file-tree"
        :aria-valuemin="treeMin"
        :aria-valuemax="treeMax"
        :aria-valuenow="treeWidth"
        aria-label="调整文件树宽度"
        title="拖拽调整宽度，双击恢复默认"
        tabindex="0"
        @pointerdown="treeGripDown"
        @pointermove="treeGripMove"
        @pointerup="treeGripUp"
        @pointercancel="treeGripUp"
        @dblclick="resetTreeWidth"
        @keydown="treeGripKey"
      ></div>

      <div class="changes-diff-wrap">
        <p v-if="loading" class="changes-note">正在读取改动…</p>
        <p v-else-if="error" class="changes-note changes-error">{{ error }}</p>
        <p v-else-if="!files.length" class="changes-note">这一轮没有文件改动。</p>
        <template v-else-if="current">
          <div class="changes-diff">
            <div v-for="(row, i) in rows" :key="i" class="changes-diff-row">
              <div v-if="row.meta" class="changes-diff-meta">{{ row.meta }}</div>
              <template v-else>
                <code class="changes-diff-cell" :class="`is-${row.leftKind}`">{{ row.left }}</code>
                <code class="changes-diff-cell" :class="`is-${row.rightKind}`">{{ row.right }}</code>
              </template>
            </div>
          </div>
          <p v-if="current.truncated" class="changes-note">改动太长，只显示前一段。</p>
        </template>
      </div>
    </div>
  </div>
</template>
