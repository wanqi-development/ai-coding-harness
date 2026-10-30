import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发时把后端路径代理到 FastAPI（避免 CORS），SSE 流式透传。
// 启动脚本可覆盖端口；不传时保持原有的本地默认值。
const BACKEND = process.env.HARNESS_BACKEND_URL ?? 'http://127.0.0.1:8000'
const FRONTEND_PORT = Number(process.env.HARNESS_FRONTEND_PORT ?? 5173)

// **后端路由前缀要逐个列这里**（vite 只转发列出来的前缀）：漏一个的现象是「前端一调就 404」，
// 而 curl 后端却是 200 —— 2026-10-13 的 `/model-config` 就这么踩过一次，别只加后端忘了这。
// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: FRONTEND_PORT,
    proxy: {
      '/auth': BACKEND,
      '/users': BACKEND,
      '/me': BACKEND,
      '/sessions': BACKEND,
      '/chat': BACKEND,
      '/health': BACKEND,
      '/model-config': BACKEND,
      '/app-settings': BACKEND,
    },
  },
})
