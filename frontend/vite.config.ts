import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发时把后端路径代理到 FastAPI（避免 CORS），SSE 流式透传
const BACKEND = 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/sessions': BACKEND,
      '/chat': BACKEND,
      '/health': BACKEND,
      '/auth': BACKEND,
    },
  },
})
