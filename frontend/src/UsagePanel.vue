<script setup lang="ts">
/**
 * 右侧栏「Token」标签：本会话的 token 消耗（按执行主角分类）。
 *
 * 四条口径：
 * - **只读聚合**：数据源是各轮记录里的 `extra.usage`（后端在轮末写入），这里不改任何数据，
 *   「重新读取」只是重新拉一次接口；
 * - **缺数据要说出来**：本功能上线前跑的轮次没有用量，`rounds > roundsWithUsage` 时必须
 *   写明「其中 N 轮没有用量数据」—— 把缺的轮次当成 0 消耗是最容易看错的一种错；
 * - **缺口同样显示**：`missing` 是「平台没回用量的调用次数」，不是 0；
 * - **中断的轮次也在账上**（钱真花了），单独标出来让人知道这段数字不完整；
 * - **不算钱**：只显示 token。各家计价口径与缓存折扣不同，显示一个错的金额比不显示更糟。
 */
import { computed, ref, watch } from 'vue'
import { getUsage, type UsageActor, type UsageSnapshot } from './api'

const props = defineProps<{ sessionId: string }>()

/**
 * 「消息上显示消耗」开关，由父组件持有（它管的是聊天流的渲染，不是这个面板的私事）。
 *
 * 用 `defineModel` 而不是 emit 一个事件：这是双向绑定，父组件那边写一句
 * `v-model:show-usage="showUsage"` 就够，选择持久化在父组件（与侧栏宽度同一套）。
 */
const showUsage = defineModel<boolean>('showUsage', { default: true })

/** 归类名 → 中文标签。固定键由后端约定（见 `UsageActor.key`），认不出的原样显示。 */
const LABEL: Record<string, string> = {
  main: '主控',
  planner: 'planner',
  coder: 'coder',
  reviewer: 'reviewer',
  internal: '上下文摘要',
  intent: '意图分类',
}

const data = ref<UsageSnapshot | null>(null)
const error = ref('')
const loading = ref(false)

/** 是否有任何账可看：一轮都没统计到时就别再画 0 —— 那看着像「确实没花钱」。 */
const hasUsage = computed(() => (data.value?.rounds_with_usage ?? 0) > 0)

/** 某一项在最大项面前的占比（画横条用）；最大项为 0 时返回 0，避免除零。 */
const peak = computed(() =>
  Math.max(1, ...(data.value?.actors ?? []).map((a) => a.total)),
)

/** 上下文压力百分比：服务端已算好 ratio，这里只转整数（阈值口径只有服务端一处）。 */
const contextPercent = computed(() => {
  const ctx = data.value?.context
  if (!ctx || ctx.window <= 0) return 0
  return Math.min(100, Math.round(ctx.ratio * 100))
})

/** 压力档位：与输入框上方那条同一个口径（80% 警示 / 60% 提醒）。 */
const contextLevel = computed(() => {
  const ctx = data.value?.context
  if (!ctx || ctx.window <= 0) return 'none'
  if (ctx.ratio >= 0.8) return 'alert'
  if (ctx.ratio >= 0.6) return 'warn'
  return 'ok'
})

function actorLabel(key: string): string {
  return LABEL[key] ?? key
}

/** 千分位：账目数字要能一眼读出来位数。 */
function num(value: number): string {
  return value.toLocaleString('en-US')
}

/** 紧凑写法（横条右边那一列）：过万折成 `12.3k`，免得把面板撑歪。 */
function short(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value)
}

function barWidth(actor: UsageActor): string {
  return `${Math.max(2, Math.round((actor.total / peak.value) * 100))}%`
}

async function load() {
  const id = props.sessionId
  if (!id || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await getUsage(id)
    // 用户可能已经切走：旧会话的账不许盖到新会话上
    if (props.sessionId === id) data.value = result
  } catch (e: any) {
    if (props.sessionId === id) {
      data.value = null
      error.value = e?.message ?? '用量加载失败'
    }
  } finally {
    loading.value = false
  }
}

// 立刻加载；换会话时重新拉（账是按会话算的）
watch(() => props.sessionId, load, { immediate: true })
</script>

<template>
  <div class="usage-panel">
    <p v-if="!data && !error" class="repo-hint">
      <span class="thinking-dots"><i></i><i></i><i></i></span>
      正在统计用量…
    </p>

    <p v-else-if="error" class="repo-error">{{ error }}</p>

    <template v-else-if="data">
      <div class="usage-bar">
        <span class="usage-title">本会话累计</span>
        <button class="memory-reload" :disabled="loading" @click="load">
          {{ loading ? '读取中…' : '重新读取' }}
        </button>
      </div>

      <!-- 消息上要不要显示消耗（角标本身是全局偏好，放这里是因为它就是「用量」这件事） -->
      <label class="usage-switch">
        <input v-model="showUsage" type="checkbox" />
        <span>在每条回复下显示这轮的消耗</span>
      </label>

      <div class="usage-totals">
        <span class="usage-cell">
          <em>输入</em><b>{{ num(data.totals.input) }}</b>
        </span>
        <span class="usage-cell">
          <em>输出</em><b>{{ num(data.totals.output) }}</b>
        </span>
        <span class="usage-cell usage-cell-total">
          <em>合计</em><b>{{ num(data.totals.total) }}</b>
        </span>
      </div>

      <!-- 空态：一轮都没统计到（本功能上线前跑的会话就长这样），不是「消耗为 0」 -->
      <p v-if="!hasUsage" class="repo-hint">
        这个会话还没有用量数据 —— 从本轮起，
        每一轮的消耗会按执行主角逐轮记下来。
      </p>

      <template v-else>
        <p class="usage-note">
          统计到 <b>{{ data.rounds_with_usage }}</b> 轮<template
            v-if="data.rounds > data.rounds_with_usage"
          >
            （另有 {{ data.rounds - data.rounds_with_usage }} 轮没有用量数据）</template
          ><template v-if="data.rounds_interrupted">
            ，其中 {{ data.rounds_interrupted }} 轮是中断的</template
          ><template v-if="data.missing">
            ；{{ data.missing }} 次调用平台没回用量</template
          >。
        </p>

        <!-- 上下文压力：这是「为什么某轮回复变短了」的答案，放在明细之前 -->
        <template v-if="data.context && data.context.window > 0">
          <p class="usage-section">上下文</p>
          <div class="usage-context" :class="contextLevel">
            <div class="usage-track usage-context-track">
              <i class="usage-fill" :style="{ width: `${contextPercent}%` }"></i>
            </div>
            <p class="usage-context-note">
              峰值输入 {{ num(data.context.max_input) }} / 窗口
              {{ num(data.context.window) }}（{{ contextPercent }}%）<template
                v-if="data.context.compressed"
              >
                ；已压缩 {{ data.context.compressed }} 次</template
              >。
              <span v-if="contextLevel === 'alert'">
                已接近压缩线（85%）：下一条消息可能触发压缩，这一轮的回复会变短。
              </span>
            </p>
          </div>
        </template>

        <p class="usage-section">按角色</p>
        <ul class="usage-list">
          <li v-for="actor in data.actors" :key="actor.key" class="usage-item">
            <span class="usage-name" :title="actorLabel(actor.key)">{{
              actorLabel(actor.key)
            }}</span>
            <span class="usage-track">
              <i class="usage-fill" :style="{ width: barWidth(actor) }"></i>
            </span>
            <span class="usage-value" :title="`输入 ${num(actor.input)} / 输出 ${num(actor.output)}`">
              {{ short(actor.total) }}
            </span>
          </li>
        </ul>

        <p class="usage-section">明细</p>
        <table class="usage-table">
          <thead>
            <tr>
              <th>角色</th>
              <th>输入</th>
              <th>输出</th>
              <th>调用</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="actor in data.actors" :key="actor.key">
              <td>{{ actorLabel(actor.key) }}</td>
              <td>{{ num(actor.input) }}</td>
              <td>{{ num(actor.output) }}</td>
              <td>{{ actor.calls }}</td>
            </tr>
            <tr class="usage-sum">
              <td>合计</td>
              <td>{{ num(data.totals.input) }}</td>
              <td>{{ num(data.totals.output) }}</td>
              <td>{{ data.actors.reduce((sum, a) => sum + a.calls, 0) }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </template>
  </div>
</template>
