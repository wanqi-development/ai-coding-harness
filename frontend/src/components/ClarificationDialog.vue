<script setup lang="ts">
import type { ClarificationInfo } from '../lib/api'

defineProps<{
  info: ClarificationInfo
  busy?: boolean
}>()

const emit = defineEmits<{
  (e: 'choose', index: number): void
  (e: 'other'): void
  (e: 'close'): void
}>()
</script>

<template>
  <div class="clarify-mask" @click.self="emit('close')">
    <section class="clarify-card" role="dialog" aria-modal="true" aria-label="确认意图">
      <button class="clarify-close" type="button" title="关掉后在输入框自由输入" @click="emit('close')">
        ×
      </button>
      <div class="clarify-kicker">先确认一下</div>
      <h3 class="clarify-question">{{ info.question }}</h3>
      <div class="clarify-options">
        <button
          v-for="(option, index) in info.options"
          :key="`${option.intent}-${index}`"
          type="button"
          class="clarify-option"
          :disabled="busy"
          @click="emit('choose', index)"
        >
          <span class="clarify-option-label">{{ option.label }}</span>
          <span v-if="option.description" class="clarify-option-desc">{{ option.description }}</span>
        </button>
      </div>
      <button class="clarify-other" type="button" :disabled="busy" @click="emit('other')">
        其他 · 我在输入框里自己说
      </button>
      <p class="clarify-foot">选择后直接按这个方向继续；关掉弹窗也能自由输入。</p>
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
  width: min(440px, 100%);
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

.clarify-kicker {
  color: var(--text-muted);
  font-size: 12px;
  letter-spacing: 0.06em;
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
.clarify-other {
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
.clarify-other:hover:not(:disabled) {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.clarify-option:disabled,
.clarify-other:disabled {
  opacity: 0.55;
  cursor: wait;
}

.clarify-option-label {
  font-size: 14px;
  font-weight: 600;
}

.clarify-option-desc {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.45;
}

.clarify-other {
  margin-top: 10px;
  padding: 10px 13px;
  color: var(--text-muted);
  font-size: 13px;
}

.clarify-foot {
  margin: 12px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}
</style>
