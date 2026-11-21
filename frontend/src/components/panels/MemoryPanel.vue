<script setup lang="ts">
/**
 * 右侧栏「记忆」标签：把仓库记忆显示出来。
 *
 * 记忆原先只活在系统提示词里 —— 模型看得到、用户看不到（想知道里面写了什么，只能去翻
 * store）。这一版把它摆到界面上，内容与中间件读的**是同一份**（StoreBackend 上那把 key）。
 *
 * 三条口径：
 * - **只读**：QA 可写分析文档，coding 主控可写缺口/约束；PR 头由平台同步，
 *   面板只提供查看，不提供编辑；
 * - 「重新读取」按钮只是重新拉一次接口（记忆可能刚被别的轮次改过），不会去动记忆本身；
 * - 没写过记忆的仓库是**空态**（`content === null`），不是错误。
 */
import { computed, ref, watch } from 'vue'
import {
  getMemory,
  getMemoryDocument,
  type MemorySnapshot,
} from '../../lib/api'
import {
  buildMemoryTree,
  flattenMemoryTree,
  memoryFolderKeys,
  type MemoryTreeRow,
} from '../../lib/memoryTree'
import MarkdownContent from '../MarkdownContent.vue'

const props = defineProps<{ sessionId: string }>()

const snapshot = ref<MemorySnapshot | null>(null)
const selectedPath = ref('')
const selectedContent = ref('')
const documentLoading = ref(false)
const expandedFolders = ref<Set<string>>(new Set())
const memoryTree = computed(() => buildMemoryTree(snapshot.value?.documents ?? []))
const visibleRows = computed(() => flattenMemoryTree(memoryTree.value, expandedFolders.value))
const error = ref('')
const loading = ref(false)

async function load() {
  const id = props.sessionId
  if (!id || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await getMemory(id)
    // 用户可能已经切走：旧会话的记忆不许盖到新会话上
    if (props.sessionId === id) {
      snapshot.value = result
      selectedPath.value = ''
      selectedContent.value = ''
      expandedFolders.value = new Set(
        memoryFolderKeys(buildMemoryTree(result.documents)),
      )
    }
  } catch (e: any) {
    if (props.sessionId === id) {
      snapshot.value = null
      error.value = e?.message ?? '记忆加载失败'
    }
  } finally {
    loading.value = false
  }
}

async function openDocument(path: string) {
  const id = props.sessionId
  if (!id || documentLoading.value) return
  if (selectedPath.value === path) {
    return
  }
  documentLoading.value = true
  error.value = ''
  try {
    const result = await getMemoryDocument(id, path)
    if (props.sessionId === id) {
      selectedPath.value = path
      selectedContent.value = result.content
    }
  } catch (e: any) {
    if (props.sessionId === id) error.value = e?.message ?? '记忆文档加载失败'
  } finally {
    documentLoading.value = false
  }
}

function openIndex() {
  selectedPath.value = ''
  selectedContent.value = ''
  error.value = ''
}

function toggleFolder(row: MemoryTreeRow<{ path: string }>) {
  const next = new Set(expandedFolders.value)
  if (next.has(row.key)) next.delete(row.key)
  else next.add(row.key)
  expandedFolders.value = next
}

function rowPadding(row: MemoryTreeRow<{ path: string }>): string {
  return `${8 + row.depth * 15}px`
}

// 立刻加载；换会话时重新拉（同一份数据不同仓库）
watch(() => props.sessionId, load, { immediate: true })
</script>

<template>
  <div class="memory-panel">
    <p v-if="!snapshot && !error" class="repo-hint">
      <span class="thinking-dots"><i></i><i></i><i></i></span>
      正在读取仓库记忆…
    </p>

    <p v-else-if="error" class="repo-error">{{ error }}</p>

    <template v-else-if="snapshot">
      <div class="memory-bar">
        <span class="memory-path" :title="snapshot.path">{{ snapshot.path }}</span>
        <button class="memory-reload" :disabled="loading" @click="load">
          {{ loading ? '读取中…' : '重新读取' }}
        </button>
      </div>

      <!-- 空态：这个仓库还没写过记忆（不是「写了个空文件」，也不是错误） -->
      <p v-if="snapshot.content === null" class="repo-hint">
        这个仓库还没有仓库记忆 —— QA 分析、coding 收尾或「刷新仓库记忆」都可以首建。
      </p>
      <div v-else class="memory-layout">
        <aside class="memory-tree" role="tree" aria-label="仓库记忆目录">
          <button
            class="memory-node memory-index"
            :class="{ active: !selectedPath }"
            type="button"
            role="treeitem"
            aria-level="1"
            :aria-selected="!selectedPath"
            @click="openIndex"
          >
            <span class="memory-node-icon">◆</span>
            index.md
          </button>
          <button
            v-for="row in visibleRows"
            :key="`${row.kind}:${row.key}`"
            class="memory-node"
            :class="{
              active: row.kind === 'file' && selectedPath === row.document?.path,
              folder: row.kind === 'folder',
            }"
            :style="{ paddingLeft: rowPadding(row) }"
            :title="row.document?.summary || row.label"
            :disabled="row.kind === 'file' && documentLoading"
            type="button"
            role="treeitem"
            :aria-level="row.depth + 1"
            :aria-expanded="row.kind === 'folder' ? row.expanded : undefined"
            :aria-selected="
              row.kind === 'file' ? selectedPath === row.document?.path : undefined
            "
            @click="
              row.kind === 'folder'
                ? toggleFolder(row)
                : openDocument(row.document?.path || '')
            "
          >
            <span class="memory-chevron" aria-hidden="true">
              {{ row.kind === 'folder' ? (row.expanded ? '▾' : '▸') : '·' }}
            </span>
            <span class="memory-node-label">{{ row.label }}</span>
          </button>
        </aside>
        <section class="memory-content">
          <MarkdownContent
            class="memory-body"
            :source="selectedPath ? selectedContent : snapshot.content"
          />
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.memory-layout {
  display: grid;
  grid-template-columns: minmax(145px, 0.8fr) minmax(0, 2.2fr);
  min-height: 0;
  height: calc(100% - 42px);
}

.memory-tree {
  overflow: auto;
  padding: 10px 8px 18px;
  border-right: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface) 82%, transparent);
}

.memory-node {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.memory-node:hover {
  background: color-mix(in srgb, var(--accent) 7%, transparent);
}

.memory-node.active {
  background: color-mix(in srgb, var(--accent) 13%, transparent);
  color: var(--accent);
}

.memory-index {
  padding-left: 8px;
  font-family: var(--font-mono);
  font-weight: 600;
}

.memory-chevron {
  display: inline-grid;
  place-items: center;
  width: 14px;
  flex: none;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.memory-node.folder {
  color: var(--text);
  font-weight: 550;
}

.memory-node-label {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.memory-content {
  min-width: 0;
  overflow: auto;
}

.memory-node:disabled {
  cursor: wait;
  opacity: 0.7;
}
</style>
