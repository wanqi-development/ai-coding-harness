<script setup lang="ts">
/**
 * 右侧栏「记忆」标签：把仓库记忆显示出来。
 *
 * 记忆原先只活在系统提示词里 —— 模型看得到、用户看不到（想知道里面写了什么，只能去翻
 * store）。这一版把它摆到界面上，内容与中间件读的**是同一份**（StoreBackend 上那把 key）。
 *
 * 三条口径：
 * - **只读**：正文只在 coding / review 的收尾由主控写，文件头由平台每轮按 PR 实况重算；
 *   要更新就走对话（「刷新仓库记忆」）或跑一轮编码/审查，这里不提供编辑；
 * - 「重新读取」按钮只是重新拉一次接口（记忆可能刚被别的轮次改过），不会去动记忆本身；
 * - 没写过记忆的仓库是**空态**（`content === null`），不是错误。
 */
import { ref, watch } from 'vue'
import { getMemory, type MemorySnapshot } from '../../lib/api'
import MarkdownContent from '../MarkdownContent.vue'

const props = defineProps<{ sessionId: string }>()

const snapshot = ref<MemorySnapshot | null>(null)
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
    if (props.sessionId === id) snapshot.value = result
  } catch (e: any) {
    if (props.sessionId === id) {
      snapshot.value = null
      error.value = e?.message ?? '记忆加载失败'
    }
  } finally {
    loading.value = false
  }
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
        这个仓库还没有仓库记忆 —— 它会在一轮**编码或审查收尾**时由主控写出来
        （也可以在对话里说「刷新仓库记忆」按 PR 实况补一次）。
      </p>
      <MarkdownContent v-else class="memory-body" :source="snapshot.content" />
    </template>
  </div>
</template>
