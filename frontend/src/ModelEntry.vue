<script setup lang="ts">
/**
 * 模型配置列表里的一行（2026-10-15 口径：**一行两段**）。

 * - 主行：名字 +（可选的）复用标记 + 右上角「配置」按钮；
 * - 次行：**生效值** —— 现在真的用哪个模型、多大窗口；来源有三态：
 *   ``row``（这一行配的）/ ``unified``（被「统一模型」顶掉）/ ``unset``（没配，用到它的角色会报错）。

 * 「生效值」取代了以前那枚「已配置 / 出厂默认」chip：没有出厂默认这回事了（2026-10-15 用户口径），
 * 用户要的是「现在到底在用什么」，而不是「我填过没有」。
 */
import { computed } from 'vue'

const props = defineProps<{
  label: string
  /** 现在真的用什么（后端解析的结果；没配时 source=unset） */
  effectiveModel: string
  effectiveWindow: number
  effectiveSource: string
  /** 那个窗口哪来的（`row` / `model` / `fallback` / `none`）—— 只回显前两种 */
  windowSource?: string
  /** 这一行自己填的模型名（为空＝库里没配） */
  configuredModel?: string
  /** 复用别的行（「编码」那一组的规划 / 评审） */
  reused?: boolean
  /** 复用的是哪一行 */
  reusedFrom?: string
  /** 直接给一句次行文案（JEV 那一行不是聊天模型，说不了「生效值」） */
  detailText?: string
}>()

const emit = defineEmits<{ configure: [] }>()

/** 窗口：按 K / M 紧凑显示（与弹窗那一格同一个函数口径）。 */
import { formatWindow } from './modelWindow'

/**
 * 次行：**本行配了什么** + **实际生效是什么**。

 * 两个都要说（2026-10-15 用户反馈）：只显示生效值时，本行值看不出来，像是「我的配置没保存」。
 * 口径（同日）：**本行配了就是本行**，「统一模型」只兜底没配的行。
 */
const detail = computed(() => {
  if (props.detailText) return props.detailText
  const own = (props.configuredModel ?? '').trim()
  if (props.effectiveSource === 'unset') {
    return own
      ? `本行 ${own} · 没生效（这一行缺模型名，或设置页还没认它）`
      : '未配置：用到这个角色的那一轮会报错'
  }
  // 窗口只在**真值**时显示（行内手填 / 按模型查到）；兜底值是我们自己保守取的 128k，
  // 显示出来只会让人以为查到了（2026-10-15 用户报的「这个 125K 从哪来」）
  const real = props.windowSource === 'row' || props.windowSource === 'model'
  const win = real && props.effectiveWindow > 0 ? ` · 窗口 ${formatWindow(props.effectiveWindow)}` : ''
  if (props.effectiveSource === 'unified') {
    return `${props.effectiveModel}${win} · 本行没配，走「统一模型」`
  }
  return `${props.effectiveModel}${win}`
})
</script>

<template>
  <div class="entry">
    <div class="entry-main">
      <b class="entry-name">{{ label }}</b>
      <span v-if="reused" class="entry-hint" :title="`编辑的是「${reusedFrom}」那一行`">
        复用「{{ reusedFrom }}」
      </span>
      <button class="btn entry-btn" type="button" @click="emit('configure')">配置</button>
    </div>
    <p class="entry-detail" :class="{ 'entry-detail-warn': effectiveSource === 'unset' }">
      {{ detail }}
    </p>
  </div>
</template>
