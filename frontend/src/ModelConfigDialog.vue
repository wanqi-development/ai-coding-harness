<script setup lang="ts">
/**
 * 一行模型配置的弹窗（2026-10-14 用户口径：列表里只留「生效值 + 配置按钮」，点配置再弹窗，
 * **分三类**：供应商 / base_url + api_key / 模型 + 窗口大小）。
 *
 * 2026-10-15 的几处调整：
 *
 * - **没有「出厂默认」这回事**了 —— 头部不再有「已配置 / 出厂默认」那枚 chip；
 * - 窗口那一格按 ``1M`` / ``128K`` 显示（见 ``modelWindow.ts``），存库仍是 tokens；
 * - 保存失败等的报错**就地显示在弹窗里**（以前写在页面顶部，被遮罩挡着看不见）；
 * - 「恢复默认」改名「清空这一行」（没有出厂默认可恢复，它就是把这行删掉）；
 * - 供应商被锁死的行（JEV）那一栏置灰不可改；
 * - **点弹窗外面不关**（用户口径：误点一下就得重配一遍）；要关就按 X 或 Esc；
 * - 必填加红星；key 那一格只显示缩略值（`sk-…e2b7`），不放解释；
 * - 窗口那一格不加任何提示文字，也不显示我们的保守兜底值（只回显查到的真值）。
 *
 * 自己不持有状态：草稿还是父组件那份，改完点「保存」由父组件提交这一行。
 */
import { computed, onMounted, ref } from 'vue'
import { formatWindow, parseWindow } from './modelWindow'
import type { ProviderModelItem, ProviderOption } from './api'

type Draft = {
  provider: string
  base_url: string
  model: string
  api_key: string
  /** 窗口那一格的**文本**（``1M`` / ``128K`` / 空） */
  context_window: string
  /** 这一格显示的原值（tokens）：文本没被改过时按它存，避免 ``195.3K`` 这类来回换算失真 */
  window_exact: number
  /** true ＝ 这一格是**回显**（后端解析出来的值），保存时不写库；手一改就变成 false */
  window_auto: boolean
  probe: string
  models: string[]
}

const props = defineProps<{
  label: string
  draft: Draft
  providers: ProviderOption[]
  /** 这一家已登记的模型（候选与**窗口回显**都用它：窗口是模型的属性） */
  knownModels?: ProviderModelItem[]
  hasKey: boolean
  maskedKey: string
  /** 正在刷这家的清单（按钮上显示「刷新中…」） */
  refreshing?: boolean
  busy?: boolean
  /** 保存 / 刷新的报错：就地显示在这一栏里，不写到页面顶部（那儿被遮罩挡着） */
  error?: string
  /** 供应商锁定（JEV：锁 ``typesafe``） */
  locked?: boolean
  /** 锁定时那句说明 */
  lockNote?: string
}>()

const emit = defineEmits<{
  provider: [string]
  probe: []
  restore: []
  save: []
  close: []
  /** 点「刷新清单」：去厂商那儿拉这家的模型清单并落表（只在点它时拉） */
  'refresh-models': [string]
  /** 模型名那一格动了：父组件按需去拉这家的清单（没拉过才拉） */
  'need-models': [string]
}>()

/** 无分组的那几项（现在只有「自定义」）：排在所有分组之后 */
const ungrouped = computed(() => props.providers.filter((item) => !item.ui_group))

const grouped = computed(() => {
  const out: { name: string; items: ProviderOption[] }[] = []
  for (const item of props.providers) {
    if (!item.ui_group) continue
    let bucket = out.find((group) => group.name === item.ui_group)
    if (!bucket) {
      bucket = { name: item.ui_group, items: [] }
      out.push(bucket)
    }
    bucket.items.push(item)
  }
  return out
})

/** 「测试连接」要地址 + key 两样都有；缺哪样说哪样（不让用户点了才撞 401）。 */
const probeHint = computed(() => {
  if (!props.draft.base_url.trim()) return '先填 base_url 再测'
  if (!props.hasKey && !props.draft.api_key.trim()) return '先填 api_key 再测'
  return ''
})

/**
 * 模型名候选：刚拉到的实时清单 → 表里已登记的文本模型（`provider_models`）。

 * 2026-10-15 起**没有第三档**了：那份种子内置候选清单已删 —— 模型名只认「刷新清单」
 * 拉回来落到表里的那些。
 */
const candidates = computed(() =>
  props.draft.models.length
    ? props.draft.models
    : chatNames.value,
)

/** 已登记的**文本**模型名（停用的不列） */
const chatNames = computed(() =>
  (props.knownModels ?? [])
    .filter((item) => item.model_type === 'chat' && item.status === 1)
    .map((item) => item.model),
)

/** 窗口那一格：填了就得能认出来（认不出拦下保存，别把 0 悄悄存进库）。 */
const windowBad = computed(
  () => !!props.draft.context_window.trim() && parseWindow(props.draft.context_window) === null,
)

/** 失焦时把写法归一（``131072`` → ``128K``）；认不出就原样留着，由下面那行红字提示。 */
function normalizeWindow() {
  const parsed = parseWindow(props.draft.context_window)
  if (parsed !== null) props.draft.context_window = formatWindow(parsed)
}

/** 手改这一格 = 从「回显」变成「我要覆盖它」（保存时才写库）。 */
function markWindowTyped() {
  props.draft.window_auto = false
}

/** 模型名失焦：让父组件确保这家的清单已拉过（回显窗口要有数据）。 */
function modelBlurred() {
  if (props.draft.model.trim()) emit('need-models', props.draft.provider)
}

/** 打开时把焦点落在第一格（键盘用户不用先 Tab 一遍）。 */
const firstField = ref<HTMLSelectElement | null>(null)
onMounted(() => firstField.value?.focus())
</script>

<template>
  <!-- 点遮罩**不关**（用户口径：误点一下就得重配一遍）；要关按 X 或 Esc -->
  <div class="modal-mask">
    <section class="modal" role="dialog" aria-modal="true" :aria-label="`配置 ${label}`">
      <header class="modal-head">
        <h2>{{ label }}</h2>
        <button class="icon-btn" type="button" aria-label="关闭" @click="emit('close')">
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
            <path
              d="M3.5 3.5l9 9M12.5 3.5l-9 9"
              fill="none"
              stroke="currentColor"
              stroke-width="1.4"
              stroke-linecap="round"
            />
          </svg>
        </button>
      </header>

      <div class="modal-body">
        <p v-if="error" class="modal-error">{{ error }}</p>

        <div class="group">
          <h3>供应商</h3>
          <select
            ref="firstField"
            class="input"
            :value="draft.provider"
            :disabled="locked"
            @change="emit('provider', ($event.target as HTMLSelectElement).value)"
          >
            <optgroup v-for="group in grouped" :key="group.name" :label="group.name">
              <option v-for="item in group.items" :key="item.key" :value="item.key">
                {{ item.label }}
              </option>
            </optgroup>
            <option v-for="item in ungrouped" :key="item.key" :value="item.key">
              {{ item.label }}
            </option>
          </select>
          <span v-if="lockNote" class="field-note">{{ lockNote }}</span>
        </div>

        <div class="group">
          <h3>base_url / api_key</h3>
          <label class="field">
            <span class="field-label">
              base_url <span v-if="locked" class="req">*</span>
            </span>
            <input v-model="draft.base_url" class="input" placeholder="https://…" />
          </label>
          <label class="field">
            <span class="field-label">
              api_key <span v-if="locked" class="req">*</span>
            </span>
            <input
              v-model="draft.api_key"
              class="input"
              type="password"
              autocomplete="off"
              :placeholder="hasKey ? maskedKey || 'sk-…' : 'sk-…'"
            />
          </label>
          <div class="group-actions">
            <button
              class="btn"
              type="button"
              :disabled="busy || !!probeHint"
              :title="probeHint"
              @click="emit('probe')"
            >
              测试连接
            </button>
            <span v-if="probeHint" class="field-note">{{ probeHint }}</span>
            <span v-else-if="draft.probe" class="field-note">{{ draft.probe }}</span>
          </div>
        </div>

        <div class="group">
          <h3>模型 / 窗口大小</h3>
          <div class="field">
            <span class="field-label">
              模型 <span class="req">*</span>
            </span>
            <div class="field-inline">
              <input
                v-model="draft.model"
                class="input"
                :list="`models-${label}`"
                @change="modelBlurred"
                @blur="modelBlurred"
              />
              <button
                class="btn"
                type="button"
                :disabled="busy || refreshing"
                title="去这家的清单接口拉一次（不主动刷新，点它才拉）"
                @click="emit('refresh-models', draft.provider)"
              >
                {{ refreshing ? '刷新中…' : '刷新清单' }}
              </button>
            </div>
            <datalist :id="`models-${label}`">
              <option v-for="name in candidates" :key="name" :value="name" />
            </datalist>
          </div>
          <label class="field">
            <span class="field-label">窗口大小</span>
            <input
              v-model="draft.context_window"
              class="input input-num"
              @input="markWindowTyped"
              @blur="normalizeWindow"
            />
            <span v-if="windowBad" class="field-note field-note-warn">
              认不出这个写法：填 128K / 1M 这样的值
            </span>
          </label>
        </div>
      </div>

      <footer class="modal-foot">
        <button
          class="btn btn-quiet btn-danger"
          type="button"
          :disabled="busy"
          @click="emit('restore')"
        >
          清空这一行
        </button>
        <button class="btn" type="button" @click="emit('close')">取消</button>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="busy || windowBad"
          @click="emit('save')"
        >
          {{ busy ? '保存中…' : '保存' }}
        </button>
      </footer>
    </section>
  </div>
</template>
