import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 约定端口 5173（docs/tech/dev-info.md §4）。
    // 端口被占用时必须报错停止，禁止自动切到 5174 —— 见 project-rules §2.4
    // 与 fullstack-general 强制执行原则第 8 条。
    strictPort: true,
    proxy: {
      // 开发期由 Vite 代理规避 CORS（dev-info.md §4）
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
