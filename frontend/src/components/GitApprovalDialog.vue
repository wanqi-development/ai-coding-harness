<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { GitApprovalInfo } from '../lib/api'

const props = defineProps<{
  info: GitApprovalInfo
  busy?: boolean
}>()

const emit = defineEmits<{
  (e: 'resolve', choice: string): void
  (e: 'close'): void
}>()

const selected = ref('deny')
const showDetails = ref(false)

watch(
  () => props.info.approval_id,
  () => {
    selected.value = props.info.options.find((item) => item.id === 'deny')?.id
      ?? props.info.options[0]?.id
      ?? 'deny'
    showDetails.value = false
  },
  { immediate: true },
)

const details = computed(() => props.info.details || {})
const statusLines = computed(() => details.value.status ?? [])
const cleanLines = computed(() => details.value.clean_preview ?? [])

function submit() {
  if (!props.busy) emit('resolve', selected.value)
}
</script>

<template>
  <div class="approval-mask" @click.self="emit('close')">
    <section class="approval-card" role="dialog" aria-modal="true" aria-label="Git 命令审批">
      <button class="approval-close" type="button" title="关闭，审批状态会保留" @click="emit('close')">
        ×
      </button>
      <p class="approval-kicker">需要确认 Git 操作</p>
      <h3>当前运行已暂停，不会开启新一轮</h3>
      <pre class="approval-command">{{ info.command }}</pre>
      <p class="approval-reason">{{ details.reason || '该命令可能丢失本地改动或修改历史。' }}</p>

      <div class="approval-meta">
        <span>分支：{{ details.branch || '-' }}</span>
        <span>HEAD：{{ (details.head_sha || '-').slice(0, 12) }}</span>
        <span v-if="details.recovery_path">恢复快照：{{ details.recovery_path }}</span>
      </div>

      <button class="approval-details-toggle" type="button" @click="showDetails = !showDetails">
        {{ showDetails ? '收起影响详情' : '查看影响详情' }}
      </button>
      <div v-if="showDetails" class="approval-details">
        <p v-if="details.targets?.length"><b>目标：</b>{{ details.targets.join('、') }}</p>
        <p v-if="details.preview_commands?.length"><b>预览命令：</b>{{ details.preview_commands.join('；') }}</p>
        <template v-if="cleanLines.length">
          <b>将删除的未跟踪文件</b>
          <pre>{{ cleanLines.join('\n') }}</pre>
        </template>
        <template v-if="statusLines.length">
          <b>当前工作区</b>
          <pre>{{ statusLines.join('\n') }}</pre>
        </template>
      </div>

      <div class="approval-options">
        <label
          v-for="option in info.options"
          :key="option.id"
          class="approval-option"
          :class="[option.tone, { selected: selected === option.id }]"
        >
          <input v-model="selected" type="radio" :value="option.id" :disabled="busy" />
          <span>
            <b>{{ option.label }}</b>
            <small v-if="option.description">{{ option.description }}</small>
          </span>
        </label>
      </div>

      <div class="approval-actions">
        <button type="button" class="approval-cancel" :disabled="busy" @click="emit('close')">
          稍后决定
        </button>
        <button type="button" class="approval-submit" :disabled="busy" @click="submit">
          {{ busy ? '提交中…' : '继续' }}
        </button>
      </div>
      <p class="approval-foot">审批只对当前命令生效；命令、HEAD 或工具调用变化后会重新确认。</p>
    </section>
  </div>
</template>

<style scoped>
.approval-mask {
  position: fixed;
  inset: 0;
  z-index: 41;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(12, 18, 30, 0.48);
}
.approval-card {
  position: relative;
  width: min(620px, 100%);
  max-height: min(780px, calc(100vh - 48px));
  overflow: auto;
  padding: 22px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 24px 80px rgba(10, 18, 35, 0.3);
}
.approval-close {
  position: absolute;
  top: 10px;
  right: 12px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 22px;
  cursor: pointer;
}
.approval-kicker {
  margin: 0 30px 6px 0;
  color: #c2410c;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
}
.approval-card h3 {
  margin: 0 30px 16px 0;
  font-size: 18px;
}
.approval-command,
.approval-details pre {
  overflow: auto;
  padding: 10px 12px;
  border-radius: 9px;
  background: var(--panel-bg);
  white-space: pre-wrap;
  word-break: break-word;
}
.approval-reason {
  margin: 10px 0;
  color: var(--text-muted);
}
.approval-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: var(--text-muted);
  font-size: 12px;
}
.approval-details-toggle {
  margin: 12px 0 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
}
.approval-details {
  margin: 8px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 12px;
}
.approval-details p {
  margin: 4px 0 8px;
}
.approval-options {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}
.approval-option {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 11px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  cursor: pointer;
}
.approval-option.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.approval-option.danger.selected {
  border-color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}
.approval-option span {
  display: grid;
  gap: 3px;
}
.approval-option small {
  color: var(--text-muted);
}
.approval-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
.approval-cancel,
.approval-submit {
  padding: 8px 14px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: transparent;
  color: var(--text);
  cursor: pointer;
}
.approval-submit {
  border-color: var(--accent);
  background: var(--accent);
  color: white;
}
.approval-foot {
  margin: 12px 0 0;
  color: var(--text-muted);
  font-size: 11px;
}
</style>
