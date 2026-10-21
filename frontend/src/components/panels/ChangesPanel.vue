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

const props = defineProps<{ sessionId: string; changes: ChangesInfo }>()
const emit = defineEmits<{ close: [] }>()

const selected = ref('')
const cache = ref<Record<string, { text: string; truncated: boolean }>>({})
const loading = ref(false)
const error = ref('')

const files = computed(() => props.changes.files)
const added = computed(() => files.value.reduce((sum, f) => sum + f.added, 0))
const deleted = computed(() => files.value.reduce((sum, f) => sum + f.deleted, 0))
const total = computed(() => props.changes.total ?? files.value.length)

const current = computed(() => cache.value[selected.value] ?? null)
const rows = computed(() => (current.value ? sideBySide(current.value.text) : []))

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

    <ul class="changes-file-list">
      <li v-for="file in files" :key="file.path">
        <button
          class="changes-file"
          type="button"
          :class="{ active: file.path === selected }"
          @click="pick(file)"
        >
          <span class="changes-file-mark" :data-status="mark(file.status)">{{ mark(file.status) }}</span>
          <span class="changes-file-path" :title="file.path">{{ file.path }}</span>
          <span class="changes-file-count">
            <b class="chg-add">+{{ file.added }}</b> <b class="chg-del">−{{ file.deleted }}</b>
          </span>
        </button>
      </li>
    </ul>

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
</template>
