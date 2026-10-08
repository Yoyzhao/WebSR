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
    /**
     * dev 模式下禁用浏览器缓存（no-store）。
     *
     * 背景：Vite 默认对源码模块用「协商缓存」（Cache-Control: no-cache + ETag）。
     * 浏览器收到 304 后需要读取本地缓存副本；一旦该副本损坏，Chromium 会抛出
     * `net::ERR_CACHE_READ_FAILURE`，模块加载中断并报
     * `Failed to fetch dynamically imported module` → 路由白屏。
     *
     * 实测触发路径：dev server 运行期间由外部工具**批量改写源文件**，HMR 密集
     * 更新使浏览器缓存条目与索引不一致（安全软件清理浏览器缓存也会造成同样结果）。
     *
     * 设为 no-store 后浏览器不再缓存这些响应，也就不会出现「304 却读不到缓存」
     * 的组合。代价是每次刷新重新请求模块 —— localhost 下为毫秒级，可忽略。
     * 该配置仅作用于 dev server，**不影响 `npm run build` 的产物**。
     */
    headers: {
      'Cache-Control': 'no-store',
    },
    proxy: {
      // 开发期由 Vite 代理规避 CORS（dev-info.md §4）
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
