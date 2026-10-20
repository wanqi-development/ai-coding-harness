<script setup lang="ts">
/**
 * 右侧栏「代码」标签：浏览宿主机的仓库缓存（不是沙箱里的那份 —— 见后端 repo_cache.py）。
 *
 * 三条口径：
 * - **打开才下载**：第一次切到这个标签时才 clone（大仓库要等一会儿），之后打开是秒开，
 *   点「同步」才去 fetch；
 * - **切分支只是换个参数**：服务端没有「当前分支」这种状态，所以两个标签页可以各看各的
 *   分支，互不影响；
 * - 太大 / 二进制的文件**不下发正文**，这里只说明情况 + 给出大小（不假装能显示）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  getRepoFile,
  getRepoStatus,
  getRepoTree,
  syncRepo,
  type RepoFile,
  type RepoStatus,
  type RepoTree,
  type RepoTreeEntry,
} from '../../lib/api'
import { renderCode } from '../../lib/markdown'

const props = defineProps<{
  sessionId: string
  /** 外部要求打开某个文件（计划步骤里的「涉及文件」点了就会传进来）；`at` 用来区分同一路径的重复点击 */
  target?: { ref: string; path: string; at: number } | null
}>()

const status = ref<RepoStatus | null>(null)
const branch = ref('')
const tree = ref<RepoTree | null>(null)
const file = ref<RepoFile | null>(null)
const error = ref('')
const notice = ref('')
const syncing = ref(false)
const loading = ref(false)

/** 扩展名 → 高亮语言（只映射 highlight.ts 里注册过的那些；其余走纯文本） */
const LANG_BY_EXT: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript', mts: 'typescript', cts: 'typescript',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  vue: 'xml', html: 'xml', htm: 'xml', xml: 'xml', svg: 'xml',
  py: 'python', java: 'java', go: 'go', rs: 'rust',
  json: 'json', md: 'markdown', markdown: 'markdown',
  css: 'css', scss: 'css', less: 'css',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  yml: 'yaml', yaml: 'yaml', sql: 'sql',
  toml: 'ini', ini: 'ini', cfg: 'ini', conf: 'ini',
  dockerfile: 'dockerfile',
}

function langOf(path: string): string {
  const name = path.split('/').pop() ?? ''
  if (name.toLowerCase() === 'dockerfile') return 'dockerfile'
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''
  return LANG_BY_EXT[ext] ?? ''
}

function sizeText(size: number | null): string {
  if (size === null || size === undefined) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

const currentRef = computed(() => branch.value || status.value?.default_branch || '')
const crumbs = computed(() => {
  const path = tree.value?.path ?? ''
  if (!path) return []
  const parts = path.split('/')
  return parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join('/') }))
})
const fileHtml = computed(() => {
  const text = file.value?.text
  if (text === null || text === undefined) return ''
  return renderCode(text, langOf(file.value?.path ?? ''))
})

async function loadTree(path: string): Promise<void> {
  loading.value = true
  try {
    tree.value = await getRepoTree(props.sessionId, currentRef.value, path)
  } finally {
    loading.value = false
  }
}

async function openFile(path: string): Promise<void> {
  loading.value = true
  try {
    file.value = await getRepoFile(props.sessionId, currentRef.value, path)
  } finally {
    loading.value = false
  }
}

/** 首次进入：没缓存就下（这一次会慢），有缓存就直接列根目录。 */
async function bootstrap(): Promise<void> {
  error.value = ''
  notice.value = ''
  file.value = null
  tree.value = null
  let loaded: RepoStatus
  try {
    loaded = await getRepoStatus(props.sessionId)
  } catch (e) {
    status.value = null
    error.value = (e as Error).message
    return
  }
  status.value = loaded
  if (!loaded.cloned) {
    await runSync()
    return
  }
  branch.value = loaded.default_branch || loaded.branches[0]?.name || ''
  await loadTree('')
}

async function runSync(): Promise<void> {
  if (syncing.value) return
  syncing.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await syncRepo(props.sessionId)
    status.value = result
    branch.value = result.default_branch || result.branches[0]?.name || ''
    notice.value = result.cloned_now ? '仓库已下载到本机' : '已更新到最新'
    await loadTree('')
    if (file.value) await openFile(file.value.path)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    syncing.value = false
  }
}

async function switchBranch(): Promise<void> {
  file.value = null
  try {
    await loadTree('')
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function openEntry(entry: RepoTreeEntry): Promise<void> {
  error.value = ''
  try {
    if (entry.type === 'dir') {
      file.value = null
      await loadTree(entry.path)
    } else if (entry.type === 'file') {
      await openFile(entry.path)
    } else {
      error.value = `${entry.name} 不是普通文件（${entry.type}），这里看不了`
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function openFromOutside(): Promise<void> {
  const target = props.target
  if (!target) return
  if (target.ref && target.ref !== branch.value) {
    branch.value = target.ref
    await loadTree('')
  }
  error.value = ''
  try {
    await openFile(target.path)
  } catch (e) {
    error.value = (e as Error).message
  }
}

watch(
  () => props.target,
  (value) => {
    if (value) void openFromOutside()
  },
)
watch(() => props.sessionId, () => void bootstrap(), { immediate: true })

let timer: number | undefined
onBeforeUnmount(() => {
  if (timer) window.clearTimeout(timer)
})
watch(notice, (value) => {
  if (!value) return
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(() => (notice.value = ''), 3000)
})
</script>

<template>
  <div class="repo-panel">
    <!-- 还没下载：这一步会慢（要 clone），所以状态写清楚 -->
    <template v-if="!status && !error">
      <p class="repo-hint">
        <span class="thinking-dots"><i></i><i></i><i></i></span>
        正在准备代码…
      </p>
    </template>

    <template v-else>
      <div class="repo-bar">
        <select v-model="branch" class="repo-branch" :disabled="syncing" @change="switchBranch">
          <option v-for="b in status?.branches ?? []" :key="b.name" :value="b.name">
            {{ b.name }}{{ b.is_default ? '（默认）' : '' }}
          </option>
        </select>
        <button class="repo-sync" :disabled="syncing" @click="runSync">
          {{ syncing ? '同步中…' : '同步' }}
        </button>
      </div>

      <p v-if="status?.fetched_at" class="repo-meta">
        上次同步 {{ status.fetched_at.replace('T', ' ').slice(0, 16) }}
      </p>
      <p v-if="notice" class="repo-notice">{{ notice }}</p>
      <p v-if="error" class="repo-error">{{ error }}</p>
      <p v-if="!status?.git" class="repo-error">
        本机没找到 git：装一个 git（或在 .env 里用 REPO_VIEW_GIT 指定路径），代码面板才能用。
      </p>

      <!-- 文件视图：返回目录 + 完整路径 -->
      <template v-if="file">
        <div class="repo-filehead">
          <button class="repo-back" title="回到目录" @click="file = null">←</button>
          <span class="repo-filepath" :title="file.path">{{ file.path }}</span>
          <span class="repo-filesize">{{ sizeText(file.size) }}</span>
        </div>
        <!-- 正文区撑满剩下高度（面板越高、全屏时看得越多）；滚动发生在这一层，文件头始终留在上面 -->
        <div class="repo-file">
          <p v-if="file.too_big" class="repo-hint">
            文件太大（{{ sizeText(file.size) }}），这里不展示正文。完整内容可以在沙箱里用
            <code>read_file</code> 或直接在 Gitee 上看。
          </p>
          <p v-else-if="file.binary" class="repo-hint">二进制文件（{{ sizeText(file.size) }}），不展示正文。</p>
          <pre v-else class="repo-code"><code class="hljs" v-html="fileHtml"></code></pre>
        </div>
      </template>

      <!-- 目录视图：面包屑 + 条目 -->
      <template v-else>
        <div class="repo-crumbs">
          <button class="repo-crumb" @click="loadTree('')">根目录</button>
          <template v-for="c in crumbs" :key="c.path">
            <span class="repo-sep">/</span>
            <button class="repo-crumb" @click="loadTree(c.path)">{{ c.name }}</button>
          </template>
        </div>
        <ul v-if="tree?.entries.length" class="repo-entries">
          <li v-for="entry in tree.entries" :key="entry.path">
            <button class="repo-entry" :class="entry.type" @click="openEntry(entry)">
              <span class="repo-icon">{{ entry.type === 'dir' ? '▸' : '·' }}</span>
              <span class="repo-name" :title="entry.path">{{ entry.name }}</span>
              <span v-if="entry.size !== null" class="repo-size">{{ sizeText(entry.size) }}</span>
            </button>
          </li>
        </ul>
        <p v-else-if="!loading" class="repo-hint">这个目录是空的。</p>
      </template>
    </template>
  </div>
</template>
