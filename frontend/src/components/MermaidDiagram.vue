<script setup lang="ts">
import DOMPurify from 'dompurify'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  mermaidDiagramKind,
  mermaidDiagramLabel,
  renderMermaidDiagram,
} from '../lib/mermaid'

const props = defineProps<{ source: string }>()
const diagramLabel = computed(() => mermaidDiagramLabel(mermaidDiagramKind(props.source)))

const svg = ref('')
const error = ref('')
const loading = ref(true)
const sourceOpen = ref(false)
// 默认按原始尺寸显示。超大流程图缩到聊天宽度后字号只剩 4~5px，阅读优先于“一屏塞下”。
const fit = ref(false)
const expanded = ref(false)
const copied = ref(false)
const theme = ref(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
let renderToken = 0
let themeObserver: MutationObserver | null = null
let copyTimer = 0

function cssValue(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function themeVariables() {
  return {
    darkMode: theme.value === 'dark',
    background: cssValue('--surface'),
    mainBkg: cssValue('--surface'),
    primaryColor: cssValue('--surface'),
    primaryTextColor: cssValue('--text'),
    primaryBorderColor: cssValue('--accent'),
    secondaryColor: cssValue('--panel-bg'),
    secondaryTextColor: cssValue('--text'),
    secondaryBorderColor: cssValue('--border'),
    tertiaryColor: cssValue('--page-bg'),
    tertiaryTextColor: cssValue('--text'),
    tertiaryBorderColor: cssValue('--border'),
    lineColor: cssValue('--text-muted'),
    nodeBorder: cssValue('--accent'),
    clusterBkg: cssValue('--panel-bg'),
    clusterBorder: cssValue('--border'),
    edgeLabelBackground: cssValue('--surface'),
    fontFamily: getComputedStyle(document.body).fontFamily,
    fontSize: '14px',
  }
}

async function renderDiagram(): Promise<void> {
  const token = ++renderToken
  loading.value = true
  error.value = ''
  try {
    const rawSvg = await renderMermaidDiagram(props.source, themeVariables())
    if (token !== renderToken) return
    svg.value = DOMPurify.sanitize(rawSvg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      // Mermaid 的 HTML 标签放在 SVG foreignObject 里；DOMPurify 的 SVG profile 默认
      // 会把它整块删掉，表现为图还在但所有节点文字消失。
      ADD_TAGS: ['foreignObject'],
      FORBID_TAGS: ['script'],
      FORBID_ATTR: ['onload', 'onclick', 'onerror'],
    })
  } catch (exc: unknown) {
    if (token !== renderToken) return
    svg.value = ''
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    if (token === renderToken) loading.value = false
  }
}

async function copySource(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.source)
    copied.value = true
    window.clearTimeout(copyTimer)
    copyTimer = window.setTimeout(() => (copied.value = false), 1200)
  } catch {
    sourceOpen.value = true
  }
}

function downloadSvg(): void {
  if (!svg.value) return
  const blob = new Blob(
    [`<?xml version="1.0" encoding="UTF-8"?>\n${svg.value}`],
    { type: 'image/svg+xml;charset=utf-8' },
  )
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'mermaid-diagram.svg'
  anchor.click()
  URL.revokeObjectURL(url)
}

function closeOnEscape(event: KeyboardEvent): void {
  if (event.key === 'Escape') expanded.value = false
}

onMounted(() => {
  themeObserver = new MutationObserver(() => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
    if (next !== theme.value) theme.value = next
  })
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
  window.addEventListener('keydown', closeOnEscape)
  void renderDiagram()
})

onBeforeUnmount(() => {
  themeObserver?.disconnect()
  window.removeEventListener('keydown', closeOnEscape)
  window.clearTimeout(copyTimer)
  renderToken += 1
})

watch(() => props.source, () => void renderDiagram())
watch(theme, () => void renderDiagram())
</script>

<template>
  <figure class="mermaid-card" :class="{ expanded }">
    <figcaption class="mermaid-head">
      <span class="mermaid-title">
        <span class="mermaid-mark">◇</span>
        {{ diagramLabel }}
        <span class="mermaid-engine">Mermaid</span>
        <span v-if="svg && !fit" class="mermaid-hint">可滚动 · 可放大</span>
      </span>
      <span class="mermaid-actions">
        <button v-if="svg" type="button" @click="fit = !fit">
          {{ fit ? '原始大小' : '适应宽度' }}
        </button>
        <button v-if="svg" type="button" @click="expanded = true">全屏</button>
        <button type="button" @click="sourceOpen = !sourceOpen">
          {{ sourceOpen ? '收起源码' : '源码' }}
        </button>
        <button type="button" @click="copySource">{{ copied ? '已复制' : '复制' }}</button>
        <button v-if="svg" type="button" @click="downloadSvg">下载</button>
      </span>
    </figcaption>

    <div v-if="loading" class="mermaid-state">
      <span class="thinking-dots"><i></i><i></i><i></i></span>
      正在渲染{{ diagramLabel }}…
    </div>
    <div v-else-if="error" class="mermaid-state error">
      <strong>{{ diagramLabel }}语法无法渲染</strong>
      <span>{{ error }}</span>
    </div>
    <div
      v-if="svg"
      class="mermaid-canvas"
      :class="{ fit, original: !fit }"
      v-html="svg"
    ></div>

    <pre v-if="sourceOpen || error" class="mermaid-source"><code>{{ source }}</code></pre>
  </figure>

  <Teleport to="body">
    <div
      v-if="expanded"
      class="mermaid-modal"
      role="dialog"
      aria-modal="true"
      aria-label="流程图查看"
      @click.self="expanded = false"
    >
      <section class="mermaid-modal-card">
        <header class="mermaid-modal-head">
          <strong>{{ diagramLabel }} · Mermaid</strong>
          <span class="mermaid-actions">
            <button type="button" @click="downloadSvg">下载 SVG</button>
            <button type="button" @click="expanded = false">关闭</button>
          </span>
        </header>
        <div class="mermaid-modal-canvas" v-html="svg"></div>
      </section>
    </div>
  </Teleport>
</template>
