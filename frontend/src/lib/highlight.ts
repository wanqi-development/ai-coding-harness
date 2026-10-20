import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import go from 'highlight.js/lib/languages/go'
import ini from 'highlight.js/lib/languages/ini'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

// 按需注册，不用 lib/common（那个整包约 300KB）。html 由 xml 覆盖，toml 由 ini 覆盖。
for (const [name, def] of Object.entries({
  bash, css, dockerfile, go, ini, java, javascript, json, markdown, python, rust, sql,
  typescript, xml, yaml,
})) {
  hljs.registerLanguage(name, def)
}

/** 代码围栏上常见的写法 → 实际注册的语言名。 */
const ALIASES: Record<string, string> = {
  sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python',
  yml: 'yaml',
  html: 'xml', htm: 'xml', vue: 'xml', svg: 'xml',
  toml: 'ini', cfg: 'ini', conf: 'ini',
  md: 'markdown',
  golang: 'go',
  rs: 'rust',
  docker: 'dockerfile',
}

/**
 * 把一段代码转成带 hljs token 的 HTML。
 *
 * 未知语言返回 ``null``（调用方回退成纯转义文本）—— 不跑 auto-detect：
 * 它既不便宜又常猜错，猜错的高亮比不高亮更难读。
 */
// 高亮结果记忆化：流式渲染时整篇会反复重来，而**已经闭合的代码块内容一个字都没变** ——
// 不缓存的话，前面每个代码块每帧都要重新高亮一遍（hljs 不便宜）。key 用「语言 + 正文」，
// 容量到顶按插入顺序丢最早的一条（Map 保持插入顺序），以免长会话把内存吃光。
const CACHE_LIMIT = 200
const cache = new Map<string, string | null>()

export function highlightCode(code: string, lang: string): string | null {
  const key = lang.trim().toLowerCase()
  if (!key) return null
  const name = ALIASES[key] ?? key
  if (!hljs.getLanguage(name)) return null
  const cacheKey = `${name}\u0000${code}`
  const cached = cache.get(cacheKey)
  if (cached !== undefined) return cached
  let html: string | null
  try {
    html = hljs.highlight(code, { language: name, ignoreIllegals: true }).value
  } catch {
    // 高亮失败不该让整条消息渲染不出来，退回纯文本
    html = null
  }
  if (cache.size >= CACHE_LIMIT) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  cache.set(cacheKey, html)
  return html
}
