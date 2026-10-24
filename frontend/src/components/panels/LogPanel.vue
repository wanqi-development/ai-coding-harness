<script setup lang="ts">
/**
 * 右侧栏「日志」标签：这个会话自己的日志。
 *
 * 四条口径：
 * - **只看这个会话的**：后端按会话 id 筛（格式里那一格 `[<会话id>]`、请求路径、
 *   老的 `public_id=` 都算），不是把整份日志倒出来；
 * - **默认滤掉两类噪声**：① HTTP 请求行（`api.middleware` + `uvicorn.access`，一个请求三行、
 *   前端每 1.5 秒轮询一次 —— 实测占某会话 76%）② 「日志接口自己」的请求（永远滤掉，否则
 *   看日志这个动作就在污染日志）。第一类可以用勾选框打开，第二类不给开关；
 * - **关键字搜索**：大小写不敏感的子串，在后端**先筛再截尾**，所以「命中 0 行」是真的没有，
 *   不是尾部那 300 行里没有；
 * - **尾部 N 行 + 命中总数**：面板顶上说清「最后 300 行 / 共 1240 行」，上面还有多少一目了然；
 * - **下载给全**：面板里的按钮是指向接口的普通链接（服务端 `Content-Disposition` 管附件名），
 *   要看更早的那些行就从这儿拿走 —— 面板不追日志的 `.1`/`.2` 备份文件。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { getLogs, logsDownloadUrl, type LogPage } from '../../lib/api'

const props = defineProps<{ sessionId: string }>()

const LIMIT = 300
/** 级别是**阈值**：选了 WARNING 就是 WARNING 及以上（排障时的常见诉求） */
const LEVELS = [
  { value: '', label: '全部级别' },
  { value: 'INFO', label: 'INFO 及以上' },
  { value: 'WARNING', label: 'WARNING 及以上' },
  { value: 'ERROR', label: 'ERROR 及以上' },
]

const data = ref<LogPage | null>(null)
const level = ref('')
/** 关键字（回车或点「搜索」才发请求 —— 边打字边查会把日志文件反复读一遍） */
const keyword = ref('')
/** 把 HTTP 请求行也算进来（默认不算：那是浏览器的轮询噪声，不是这个会话干的事） */
const withHttp = ref(false)
const treeMode = ref(true)
const error = ref('')
const loading = ref(false)
const box = ref<HTMLElement | null>(null)

type LogGroup = {
  id: string
  parent: string
  lines: LogPage['lines']
  children: LogGroup[]
}

const groupedLines = computed(() => {
  const groups = new Map<string, LogGroup>()
  const roots: LogGroup[] = []
  for (const line of data.value?.lines ?? []) {
    const id = line.run_id || ''
    if (!id) {
      roots.push({ id: '', parent: '', lines: [line], children: [] })
      continue
    }
    let group = groups.get(id)
    if (!group) {
      group = { id, parent: line.parent_run_id || '', lines: [], children: [] }
      groups.set(id, group)
    }
    group.lines.push(line)
  }
  for (const group of groups.values()) {
    const parent = group.parent ? groups.get(group.parent) : undefined
    if (parent) parent.children.push(group)
    else roots.push(group)
  }
  const flat: Array<{ group: LogGroup; depth: number }> = []
  const walk = (group: LogGroup, depth: number) => {
    flat.push({ group, depth })
    group.children.forEach((child) => walk(child, depth + 1))
  }
  roots.forEach((root) => walk(root, 0))
  return flat
})

function rowClass(line: { level: string }): string {
  return line.level ? line.level.toLowerCase() : 'plain'
}

async function load() {
  const id = props.sessionId
  if (!id || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await getLogs(id, {
      limit: LIMIT,
      level: level.value || undefined,
      q: keyword.value.trim() || undefined,
      withHttp: withHttp.value,
    })
    // 用户可能已经切走：旧会话的日志不许盖到新会话上
    if (props.sessionId !== id) return
    data.value = result
    // 最新的在最后：读出来就贴到底，省得每次手动拖
    await nextTick()
    if (box.value) box.value.scrollTop = box.value.scrollHeight
  } catch (e: any) {
    if (props.sessionId === id) {
      data.value = null
      error.value = e?.message ?? '日志加载失败'
    }
  } finally {
    loading.value = false
  }
}

/**
 * 立刻加载；换会话时**先把筛选条件复位再拉**。
 *
 * 复位必须和加载在同一个 watcher 里：拆成两个的话，先注册的那个（加载）会带着**上一个
 * 会话的关键字/级别**去查新会话 —— 界面上看不见的筛选条件最坑人。
 */
watch(
  () => props.sessionId,
  () => {
    keyword.value = ''
    level.value = ''
    withHttp.value = false
    treeMode.value = true
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <div class="log-panel">
    <div class="log-bar">
      <select v-model="level" class="log-level" :disabled="loading" @change="load">
        <option v-for="item in LEVELS" :key="item.value" :value="item.value">
          {{ item.label }}
        </option>
      </select>
      <button class="log-reload" :disabled="loading" @click="load">
        {{ loading ? '读取中…' : '重新读取' }}
      </button>
      <a class="log-download" :href="logsDownloadUrl(sessionId)">下载</a>
    </div>

    <!-- 关键字在后端「先筛再截尾」，所以命中数是真的（不是尾部 300 行里碰巧没有） -->
    <div class="log-search">
      <input
        v-model="keyword"
        class="log-input"
        type="search"
        placeholder="搜关键字（如 tool、PR #7、error）"
        :disabled="loading"
        @keydown.enter.prevent="load"
      />
      <button class="log-reload" :disabled="loading" @click="load">搜索</button>
    </div>
    <label class="log-toggle">
      <input v-model="withHttp" type="checkbox" :disabled="loading" @change="load" />
      含 HTTP 请求行
      <span class="log-toggle-hint">（默认不显示：那是浏览器轮询的噪声）</span>
    </label>
    <label class="log-toggle">
      <input v-model="treeMode" type="checkbox" />
      按 run 父子层级展示
      <span class="log-toggle-hint">（旧日志没有 run_id 时作为根节点）</span>
    </label>

    <p v-if="error" class="repo-error">{{ error }}</p>

    <template v-else-if="data">
      <p class="log-meta">
        命中 {{ data.total }} 行<template v-if="data.total > data.lines.length">，显示最后 {{ data.lines.length }} 行</template>
        <span v-if="data.total > data.lines.length">（更早的请下载）</span>
      </p>
      <p v-if="!data.total" class="repo-hint">
        <template v-if="keyword || level">当前条件下没有命中的行 —— 换个关键字、放宽级别，或勾上「含 HTTP 请求行」。</template>
        <template v-else>这个会话还没有日志。</template>
      </p>
      <!-- 只读展示：不选中、不换行截断，一行就是一行 -->
      <div v-else ref="box" class="log-body">
        <template v-if="treeMode">
          <div
            v-for="(item, i) in groupedLines"
            :key="`${item.group.id}:${i}`"
            class="log-run-group"
            :style="{ paddingLeft: `${item.depth * 16}px` }"
          >
            <div v-if="item.group.id" class="log-run-head">
              run {{ item.group.id }}
              <span v-if="item.group.parent"> ← {{ item.group.parent }}</span>
            </div>
            <div
              v-for="(line, j) in item.group.lines"
              :key="j"
              class="log-row"
              :class="rowClass(line)"
            >
              {{ line.text }}
            </div>
          </div>
        </template>
        <template v-else>
          <div v-for="(line, i) in data.lines" :key="i" class="log-row" :class="rowClass(line)">
            {{ line.text }}
          </div>
        </template>
      </div>
    </template>

    <p v-else class="repo-hint">
      <span class="thinking-dots"><i></i><i></i><i></i></span>
      正在读取日志…
    </p>
  </div>
</template>

<style scoped>
.log-run-group + .log-run-group { margin-top: 8px; }
.log-run-head {
  margin: 6px 0 3px;
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
}
</style>
