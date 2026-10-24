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
import { getUsage, type UsageActor, type UsageSnapshot } from '../../lib/api'

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

/**
 * 面板的行结构（2026-10-20 用户口径）：
 *
 * - 主控**按形态**拆开：qa / plan 各一行；
 * - `coding` 是一组，子项是 coder / reviewer / planner；
 * - **rework 不单列**：它与 coding 共用 `orchestrate` 形态，主控开销并进 coding 那一组；
 * - `intent`（判定）与 `internal`（摘要）是别处的开销，各自平铺。
 */
const SUB_AGENT_KEYS = ['coder', 'reviewer', 'planner'] as const
/** 独立成行的形态（主控按 ``extra.intent`` 聚合出来的） */
const STANDALONE_FORMS = ['qa', 'plan'] as const
/** 并进 coding 组的两个形态：rework 与 coding 共用同一套「主控 + 三个子智能体」 */
const CODING_FORMS = ['coding', 'rework'] as const

/** 形态的两个名字：拉丁名（与后端 key 对齐）+ 中文。 */
const FORM_LABELS: Record<string, [string, string]> = {
  qa: ['qa', '问答'],
  plan: ['plan', '方案'],
  coding: ['coding', '编码'],
  rework: ['rework', 'PR 跟进'],
}

/** 子智能体的两个名字（与 /ai_coding/skills 里的角色名一致）。 */
const SUB_LABELS: Record<string, string> = { coder: '编码', reviewer: '审查', planner: '方案' }

/** 展开状态。**默认展开** —— 折叠起来会把最大的一笔开销（通常是 coder）藏起来。 */
const expanded = ref<Record<string, boolean>>({ coding: true, main: true })

interface UsageRow {
  /** 行键（v-for 与折叠状态都用它） */
  key: string
  actor: UsageActor
  depth: 0 | 1
  children: UsageActor[]
  /** 有值时按「拉丁名 + 中文」显示（形态行）；否则走 ``actorLabel`` */
  formKey?: string
}

/** 把若干项加起来装成一个「组」的伪 actor（只用于显示合计）。 */
function sumActors(parts: UsageActor[], key: string): UsageActor {
  const sum = (pick: (actor: UsageActor) => number) => parts.reduce((acc, a) => acc + pick(a), 0)
  return {
    key,
    input: sum((a) => a.input),
    output: sum((a) => a.output),
    calls: sum((a) => a.calls),
    total: sum((a) => a.total),
  }
}

const groupedRows = computed<UsageRow[]>(() => {
  const actors = data.value?.actors ?? []
  const forms = data.value?.by_form ?? {}
  // 老后端不返回 ``by_form``（还没重启）：退回改版前的样子 —— 一个「主控」父行 +
  // 三个子智能体。不能让它显示成「主控（未标注形态）」，那不是数据缺形态，是接口老了。
  const hasForms = Object.keys(forms).length > 0
  const byKey = new Map(actors.map((actor) => [actor.key, actor]))
  const rows: UsageRow[] = []

  // ① 独立形态：主控在那一轮的全部开销，按花得多排前面
  const standalone = STANDALONE_FORMS.map((key) => forms[key])
    .filter((actor): actor is UsageActor => actor !== undefined && actor.total > 0)
    .sort((a, b) => b.total - a.total)
  for (const form of standalone) {
    rows.push({ key: form.key, actor: form, depth: 0, children: [], formKey: form.key })
  }

  // ② coding 组：主控（coding + rework）+ 三个子智能体
  const kids = SUB_AGENT_KEYS.map((key) => byKey.get(key))
    .filter((actor): actor is UsageActor => actor !== undefined)
    .sort((a, b) => b.total - a.total)
  const codingParts = CODING_FORMS.map((key) => forms[key]).filter(
    (actor): actor is UsageActor => actor !== undefined,
  )
  if (hasForms && (kids.length || codingParts.length)) {
    rows.push({
      key: 'coding',
      actor: sumActors([...codingParts, ...kids], 'coding'),
      depth: 0,
      children: kids,
      formKey: 'coding',
    })
  } else if (!hasForms) {
    // 老接口：主控 + 子智能体，不按形态拆
    const mainActor = byKey.get('main')
    if (mainActor) rows.push({ key: 'main', actor: mainActor, depth: 0, children: kids })
    else for (const child of kids) rows.push({ key: child.key, actor: child, depth: 0, children: [] })
  }

  // ③ 其余执行主角：意图判定、上下文摘要、以及将来新增/认不出的角色
  for (const actor of actors) {
    if (actor.key === 'main') continue
    if ((SUB_AGENT_KEYS as readonly string[]).includes(actor.key)) continue
    rows.push({ key: actor.key, actor, depth: 0, children: [] })
  }

  // ④ 主控里没有标注形态的余量（老记录 / intent 为空）：单独兜住，否则账对不上
  const main = byKey.get('main')
  const forms_ = Object.values(forms)
  const lostInput = hasForms ? (main?.input ?? 0) - forms_.reduce((acc, f) => acc + f.input, 0) : 0
  const lostOutput = hasForms
    ? (main?.output ?? 0) - forms_.reduce((acc, f) => acc + f.output, 0)
    : 0
  if (hasForms && (lostInput > 0 || lostOutput > 0)) {
    rows.push({
      key: '__unattributed__',
      actor: {
        key: '__unattributed__',
        input: Math.max(0, lostInput),
        output: Math.max(0, lostOutput),
        calls: 0,
        total: Math.max(0, lostInput) + Math.max(0, lostOutput),
      },
      depth: 0,
      children: [],
    })
  }
  return rows
})

/** 展开的行 + 它的子项；折叠时子项不占行。 */
const visibleRows = computed<UsageRow[]>(() =>
  groupedRows.value.flatMap((row) => {
    if (!row.children.length || !expanded.value[row.key]) return [row]
    return [
      row,
      ...row.children.map((child) => ({
        key: `${row.key}:${child.key}`,
        actor: child,
        depth: 1 as const,
        children: [] as UsageActor[],
      })),
    ]
  }),
)

function toggleRow(key: string) {
  expanded.value[key] = !expanded.value[key]
}

/**
 * 横条基准：**分组后的行**里的最大项。
 *
 * 不能用原始 ``actors`` —— coding 那一组是「主控 + 三个子智能体」的合计，可能比任何单个
 * 角色都大；拿原始值当分母会让那一行的条超过 100%（撑出容器）。
 */
const peak = computed(() => Math.max(1, ...groupedRows.value.map((row) => row.actor.total)))

/** 行名：形态行走「拉丁名 + 中文」，其余走固定标签表。 */
function rowLabel(row: UsageRow): string {
  if (row.formKey) return `${FORM_LABELS[row.formKey]?.[0] ?? row.formKey} ${FORM_LABELS[row.formKey]?.[1] ?? ''}`.trim()
  const sub = SUB_LABELS[row.actor.key]
  if (row.depth === 1 && sub) return `${row.actor.key} ${sub}`
  if (row.actor.key === '__unattributed__') return '主控（未标注形态）'
  return actorLabel(row.actor.key)
}

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

        <p class="usage-section">按角色</p>
        <ul class="usage-list">
          <li
            v-for="row in visibleRows"
            :key="row.key"
            class="usage-item"
            :class="{ 'usage-item-child': row.depth === 1 }"
          >
            <button
              v-if="row.children.length"
              class="usage-fold"
              type="button"
              :aria-expanded="!!expanded[row.key]"
              :title="expanded[row.key] ? '折叠子智能体' : '展开子智能体'"
              @click="toggleRow(row.key)"
            >
              {{ expanded[row.key] ? '▾' : '▸' }}
            </button>
            <span v-else class="usage-fold usage-fold-empty" aria-hidden="true"></span>
            <span class="usage-name" :title="rowLabel(row)">{{ rowLabel(row) }}</span>
            <span class="usage-track">
              <i class="usage-fill" :style="{ width: barWidth(row.actor) }"></i>
            </span>
            <span
              class="usage-value"
              :title="`输入 ${num(row.actor.input)} / 输出 ${num(row.actor.output)}`"
            >
              {{ short(row.actor.total) }}
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
            <tr
              v-for="row in visibleRows"
              :key="row.key"
              :class="{ 'usage-child': row.depth === 1 }"
            >
              <td>
                <button
                  v-if="row.children.length"
                  class="usage-fold"
                  type="button"
                  :aria-expanded="!!expanded[row.key]"
                  @click="toggleRow(row.key)"
                >
                  {{ expanded[row.key] ? '▾' : '▸' }}
                </button>
                {{ rowLabel(row) }}
              </td>
              <td>{{ num(row.actor.input) }}</td>
              <td>{{ num(row.actor.output) }}</td>
              <td>{{ row.actor.calls }}</td>
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
