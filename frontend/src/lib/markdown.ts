import DOMPurify from 'dompurify'
import { marked } from 'marked'
import 'highlight.js/styles/github.css'
import { highlightCode } from './highlight'
import { fenceClosed } from './streamblocks'

marked.setOptions({ gfm: true, breaks: true })

// 外链一律新窗口打开；rel 防止新标签页拿到 window.opener
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** marked 的 code token 若已转过义，hljs 拿到的会是实体，先还原回真字符（&amp; 必须放最后）。 */
function unescapeHtml(s: string): string {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
}

marked.use({
  renderer: {
    // 代码块加一层外壳：顶部一行放语言名与复制按钮，下面才是 pre
    code({ text, lang, escaped, raw }) {
      const code = escaped ? unescapeHtml(text) : text
      const name = (lang || '').trim().split(/\s+/)[0]
      // 围栏还没闭合 = 这段代码正在流式增长：每帧都会重新高亮（缓存必然未命中，实测
      // 4.6ms/块/帧），先按纯转义文本渲染，等闭合那一帧再上色 —— 同一条正文的最后一次
      // 渲染必定是闭合的，所以最终看到的高亮结果与之前完全一致。
      const painted = name && fenceClosed(raw) ? highlightCode(code, name) : null
      // 未知语言回退成纯转义文本；高亮结果本身也照样经 DOMPurify 消毒
      const body = painted ?? escapeHtml(code)
      const label = name ? `<span class="code-lang">${escapeHtml(name)}</span>` : '<span></span>'
      return (
        '<div class="code-block">' +
        `<div class="code-head">${label}` +
        '<button class="code-copy" type="button">复制</button></div>' +
        `<pre><code class="hljs${name ? ` language-${escapeHtml(name)}` : ''}">${body}</code></pre>` +
        '</div>\n'
      )
    },
  },
})

/**
 * 把 AI 回复的 Markdown 渲染成安全 HTML。
 *
 * 内容来自 LLM —— 它读过仓库文件、工具输出和网页，这些都是不可信输入，
 * 可能夹带 `<img src=x onerror=...>` 这类载荷，所以必须经 DOMPurify 消毒后再交给 v-html。
 * 消毒放在管线最后一步，高亮产生的 span 也一并过一遍。
 */
export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }))
}

/**
 * 一段代码 → 可直接 `v-html` 的安全 HTML（代码面板用）。
 *
 * 与正文同一条底线：高亮结果再经 DOMPurify 消毒一遍；语言不认识就退回纯转义文本
 * （不显示成「没颜色」，更不能把原文当 HTML 插进去）。
 */
export function renderCode(text: string, lang: string): string {
  return DOMPurify.sanitize(highlightCode(text, lang) ?? escapeHtml(text))
}
