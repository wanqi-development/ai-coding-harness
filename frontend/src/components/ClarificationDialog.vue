<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  ClarificationAnswer,
  ClarificationInfo,
} from '../lib/api'
import { clarificationSteps } from '../lib/clarification'

const props = defineProps<{
  info: ClarificationInfo
  busy?: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', answers: ClarificationAnswer[]): void
  (e: 'other'): void
  (e: 'close'): void
}>()

const steps = computed(() => clarificationSteps(props.info))
const current = ref(0)
const confirming = ref(false)
const selected = ref<Record<string, number>>({})

function reset() {
  current.value = 0
  confirming.value = false
  selected.value = {}
}

watch(() => props.info.id, reset, { immediate: true })

const step = computed(() => steps.value[current.value] ?? null)
const currentChoice = computed(() => {
  const id = step.value?.id
  return id ? selected.value[id] : undefined
})

const summary = computed(() =>
  steps.value.map((item) => {
    const choice = selected.value[item.id]
    const option = typeof choice === 'number' ? item.options[choice] : undefined
    return {
      id: item.id,
      question: item.question,
      label: option?.label ?? '未选择',
      description: option?.description ?? '',
    }
  }),
)

function choose(index: number) {
  const item = step.value
  if (!item || props.busy) return
  selected.value = { ...selected.value, [item.id]: index }
  if (current.value < steps.value.length - 1) current.value += 1
  else confirming.value = true
}

function back() {
  if (props.busy) return
  if (confirming.value) {
    confirming.value = false
    current.value = Math.max(0, steps.value.length - 1)
    return
  }
  current.value = Math.max(0, current.value - 1)
}

function submit() {
  if (props.busy) return
  const answers = steps.value.flatMap((item): ClarificationAnswer[] => {
    const choice = selected.value[item.id]
    return typeof choice === 'number'
      ? [{ step_id: item.id, choice }]
      : []
  })
  if (answers.length !== steps.value.length) return
  emit('submit', answers)
}
</script>

<template>
  <div class="clarify-mask" @click.self="emit('close')">
    <section class="clarify-card" role="dialog" aria-modal="true" aria-label="补充澄清信息">
      <button class="clarify-close" type="button" title="关掉后在输入框自由输入" @click="emit('close')">
        ×
      </button>

      <div class="clarify-head">
        <span class="clarify-kicker">先确认一下</span>
        <span v-if="steps.length" class="clarify-progress">
          {{ confirming ? '确认选择' : `第 ${current + 1} / ${steps.length} 步` }}
        </span>
      </div>
      <div v-if="steps.length" class="clarify-progress-track" aria-hidden="true">
        <span
          class="clarify-progress-bar"
          :style="{ width: `${confirming ? 100 : ((current + 1) / steps.length) * 100}%` }"
        ></span>
      </div>

      <template v-if="confirming">
        <h3 class="clarify-question">确认后按这些选择继续</h3>
        <div class="clarify-summary">
          <div v-for="item in summary" :key="item.id" class="clarify-summary-row">
            <span class="clarify-summary-q">{{ item.question }}</span>
            <strong class="clarify-summary-a">{{ item.label }}</strong>
            <span v-if="item.description" class="clarify-summary-desc">{{ item.description }}</span>
          </div>
        </div>
        <div class="clarify-actions">
          <button class="clarify-secondary" type="button" :disabled="busy" @click="back">返回修改</button>
          <button class="clarify-primary" type="button" :disabled="busy" @click="submit">
            确认并继续
          </button>
        </div>
      </template>

      <template v-else-if="step">
        <h3 class="clarify-question">{{ step.question }}</h3>
        <div class="clarify-options">
          <button
            v-for="(option, index) in step.options"
            :key="`${option.intent || option.value || 'option'}-${index}`"
            type="button"
            class="clarify-option"
            :class="{ selected: currentChoice === index }"
            :disabled="busy"
            @click="choose(index)"
          >
            <span class="clarify-option-label">{{ option.label }}</span>
            <span v-if="option.description" class="clarify-option-desc">{{ option.description }}</span>
          </button>
        </div>
        <div v-if="current > 0" class="clarify-actions">
          <button class="clarify-secondary" type="button" :disabled="busy" @click="back">上一步</button>
        </div>
      </template>

      <div v-else class="clarify-empty">这次澄清没有可用的结构化选项，请直接在输入框里说明。</div>

      <button class="clarify-other" type="button" :disabled="busy" @click="emit('other')">
        其他 · 我在输入框里自己说
      </button>
      <p class="clarify-foot">
        选项会分步收集，最后统一提交；关掉弹窗或自由输入会放弃本次全部选择。
      </p>
    </section>
  </div>
</template>

<style scoped>
.clarify-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(12, 18, 30, 0.42);
}

.clarify-card {
  position: relative;
  width: min(500px, 100%);
  max-height: min(720px, calc(100vh - 48px));
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 24px 80px rgba(10, 18, 35, 0.28);
  padding: 22px;
}

.clarify-close {
  position: absolute;
  top: 10px;
  right: 12px;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}

.clarify-close:hover {
  background: var(--accent-soft);
  color: var(--accent);
}

.clarify-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-right: 24px;
}

.clarify-kicker,
.clarify-progress {
  color: var(--text-muted);
  font-size: 12px;
  letter-spacing: 0.04em;
}

.clarify-progress-track {
  height: 3px;
  margin: 10px 0 16px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--border);
}

.clarify-progress-bar {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 180ms ease;
}

.clarify-question {
  margin: 6px 26px 16px 0;
  font-size: 17px;
  font-weight: 600;
  line-height: 1.45;
}

.clarify-options {
  display: grid;
  gap: 8px;
}

.clarify-option,
.clarify-other,
.clarify-secondary,
.clarify-primary {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.clarify-option {
  display: grid;
  gap: 3px;
  padding: 11px 13px;
}

.clarify-option:hover:not(:disabled),
.clarify-option.selected,
.clarify-other:hover:not(:disabled),
.clarify-secondary:hover:not(:disabled) {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.clarify-option:disabled,
.clarify-other:disabled,
.clarify-secondary:disabled,
.clarify-primary:disabled {
  opacity: 0.55;
  cursor: wait;
}

.clarify-option-label {
  font-size: 14px;
  font-weight: 600;
}

.clarify-option-desc,
.clarify-summary-desc {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.45;
}

.clarify-summary {
  display: grid;
  gap: 10px;
}

.clarify-summary-row {
  display: grid;
  gap: 3px;
  padding: 11px 13px;
  border: 1px solid var(--border);
  border-radius: 10px;
}

.clarify-summary-q {
  color: var(--text-muted);
  font-size: 12px;
}

.clarify-summary-a {
  font-size: 14px;
}

.clarify-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.clarify-secondary,
.clarify-primary {
  margin: 0;
  padding: 9px 13px;
  text-align: center;
}

.clarify-primary {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--surface);
  font-weight: 600;
}

.clarify-other {
  margin-top: 12px;
  padding: 10px 13px;
  color: var(--text-muted);
  font-size: 13px;
}

.clarify-empty {
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.55;
}

.clarify-foot {
  margin: 12px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}
</style>
