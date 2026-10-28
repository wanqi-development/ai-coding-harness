<script setup lang="ts">
import { computed } from 'vue'
import MermaidDiagram from './MermaidDiagram.vue'
import { renderMarkdown } from '../lib/markdown'
import { splitMermaidBlocks } from '../lib/mermaid'

const props = defineProps<{ source: string }>()
const segments = computed(() => splitMermaidBlocks(props.source || '…'))
</script>

<template>
  <div class="markdown markdown-content">
    <template v-for="(segment, index) in segments" :key="index">
      <div
        v-if="segment.kind === 'markdown' && segment.source.trim()"
        class="md-block"
        v-html="renderMarkdown(segment.source)"
      ></div>
      <MermaidDiagram v-else-if="segment.kind === 'mermaid'" :source="segment.source" />
    </template>
  </div>
</template>
