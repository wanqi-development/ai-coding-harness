<script setup lang="ts">
import { computed } from 'vue'
import type { ReviewFinding, ReviewInfo } from '../lib/api'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{ review: ReviewInfo }>()
const emit = defineEmits<{ 'create-pr': [ReviewInfo] }>()

const counts = computed(() => {
  const out: Record<ReviewFinding['severity'], number> = { P0: 0, P1: 0, P2: 0, P3: 0 }
  for (const finding of props.review.findings) out[finding.severity] += 1
  return out
})

const canCreatePr = computed(() => props.review.stage === 'local')

const canMerge = computed(() => props.review.stage === 'pr')

const reviewLabel = computed(() => (props.review.stage === 'pr' ? 'PR 审查' : '本地审查'))
</script>

<template>
  <details class="review-card" :open="counts.P0 + counts.P1 > 0">
    <summary>
      <span class="review-title">{{ reviewLabel }} #{{ review.review_id }}</span>
      <span class="review-counts">
        <b :class="{ bad: counts.P0 }">P0 {{ counts.P0 }}</b>
        <b :class="{ bad: counts.P1 }">P1 {{ counts.P1 }}</b>
        <span>P2 {{ counts.P2 }}</span>
        <span>P3 {{ counts.P3 }}</span>
      </span>
      <span class="review-status">{{ review.status }}</span>
    </summary>

    <div class="review-body">
      <p class="review-meta">
        版本 {{ review.code_revision.slice(0, 12) }} · {{ review.base_ref || '默认分支' }}
      </p>
      <p v-if="review.summary" class="review-summary">{{ review.summary }}</p>

      <ul v-if="review.findings.length" class="review-findings">
        <li v-for="item in review.findings" :key="item.finding_id">
          <b :class="`sev-${item.severity.toLowerCase()}`">{{ item.severity }}</b>
          <span>{{ item.title || item.description }}</span>
          <code v-if="item.file">{{ item.file }}<template v-if="item.line">:{{ item.line }}</template></code>
        </li>
      </ul>

      <MarkdownContent v-if="review.report_markdown" :source="review.report_markdown" />

      <p v-if="canCreatePr" class="review-next">
        审查仅供参考，不阻断交付。你可以继续提出修改，或明确选择交付 PR；合并后本阶段任务完成。
      </p>
      <p v-else-if="canMerge" class="review-next">
        PR 审查完成。下一步请检查结论并合并 PR；合并后本阶段任务完成。
      </p>

      <button
        v-if="canCreatePr"
        class="btn primary review-create"
        type="button"
        @click="emit('create-pr', review)"
      >
        创建 PR
      </button>
    </div>
  </details>
</template>

<style scoped>
.review-card {
  margin: 10px 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel-bg);
}

.review-card summary {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}

.review-title {
  font-weight: 600;
}

.review-counts {
  display: flex;
  gap: 8px;
  color: var(--text-muted);
  font-size: 12px;
}

.review-counts .bad,
.sev-p0,
.sev-p1 {
  color: #d33;
}

.review-status {
  margin-left: auto;
  color: var(--text-muted);
  font-size: 12px;
}

.review-body {
  margin-top: 10px;
}

.review-meta,
.review-summary {
  color: var(--text-muted);
  font-size: 12px;
}

.review-findings {
  margin: 8px 0;
  padding-left: 18px;
}

.review-findings li {
  margin: 6px 0;
}

.review-findings code {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 11px;
}

.review-next {
  margin-top: 10px;
  font-size: 13px;
}

.review-create {
  margin-top: 8px;
}
</style>
