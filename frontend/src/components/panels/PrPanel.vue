<script setup lang="ts">
/**
 * 右侧栏「PR」标签：本仓库的 PR 列表。
 *
 * 三条口径：
 * - **查不到 ≠ 没有**：接口在查询失败时返回 502 + 原因，这里把它显示成一条错误（带原因），
 *   **不是**空列表。把「这次没查成」显示成「一个 PR 都没有」，会让人以为改动都合了。
 * - 未合并的排最前（后端已排序）：打开面板第一眼要看到「还开着的」。
 * - 状态标签用 `open` / `merged` / `closed` / `unknown` 四个码，`unknown` 显示成「状态未知」
 *   并保持中性色 —— 认不出来就说认不出来，不猜。
 */
import { computed, ref, watch } from 'vue'
import { getPulls, type PullList } from '../../lib/api'

const props = defineProps<{ sessionId: string }>()

const data = ref<PullList | null>(null)
const error = ref('')
const loading = ref(false)

const LABEL: Record<string, string> = {
  open: '待合并',
  merged: '已合并',
  closed: '已关闭',
  unknown: '状态未知',
}

const total = computed(() => data.value?.pulls.length ?? 0)

function stateLabel(state: string): string {
  return LABEL[state] ?? state
}

function timeText(iso: string): string {
  return iso ? iso.replace('T', ' ').slice(0, 16) : ''
}

async function load() {
  const id = props.sessionId
  if (!id || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await getPulls(id)
    // 用户可能已经切走：旧会话的 PR 不许盖到新会话上
    if (props.sessionId === id) data.value = result
  } catch (e: any) {
    if (props.sessionId === id) {
      data.value = null
      error.value = e?.message ?? 'PR 查询失败'
    }
  } finally {
    loading.value = false
  }
}

watch(() => props.sessionId, load, { immediate: true })
</script>

<template>
  <div class="pr-panel">
    <p v-if="!data && !error" class="repo-hint">
      <span class="thinking-dots"><i></i><i></i><i></i></span>
      正在查询 PR…
    </p>

    <!-- 查不到：把原因原样说出来，别装成空列表 -->
    <template v-else-if="error">
      <p class="repo-error">{{ error }}</p>
      <p class="repo-hint">「查不到」不等于「没有 PR」—— 先确认凭据 / 网络，再重新读取。</p>
      <button class="pr-reload" :disabled="loading" @click="load">
        {{ loading ? '读取中…' : '重新读取' }}
      </button>
    </template>

    <template v-else-if="data">
      <div class="pr-bar">
        <span class="pr-counts">
          未合并 <b>{{ data.open_count }}</b> / 共 {{ total }}
        </span>
        <button class="pr-reload" :disabled="loading" @click="load">
          {{ loading ? '读取中…' : '重新读取' }}
        </button>
      </div>

      <p v-if="!total" class="repo-hint">这个仓库还没有 PR。</p>
      <ul v-else class="pr-list">
        <li v-for="p in data.pulls" :key="p.number" class="pr-item" :class="p.state">
          <div class="pr-head">
            <span class="pr-badge" :class="p.state">{{ stateLabel(p.state) }}</span>
            <a
              class="pr-title"
              :href="p.url || undefined"
              :title="p.title"
              target="_blank"
              rel="noopener"
            >
              #{{ p.number }} {{ p.title }}
            </a>
          </div>
          <div class="pr-meta">
            <span v-if="p.branch" class="pr-branch" :title="p.branch">{{ p.branch }}</span>
            <span v-if="p.author">{{ p.author }}</span>
            <span v-if="timeText(p.updated_at)">{{ timeText(p.updated_at) }}</span>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>
