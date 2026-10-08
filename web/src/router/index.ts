/**
 * 路由表（docs/prototype/01-页面结构与布局.md §1）。
 *
 * 6 条路由：
 *   /            → 工作台（主任务页，不设宽度上限）
 *   /tasks       → 任务中心（max-width 1344，全站统一）
 *   /models      → 模型库（max-width 1344，全站统一）
 *   /hardware    → 硬件能力（max-width 1344，全站统一）
 *   /settings    → 系统配置（max-width 1344，全站统一）
 *   /:pathMatch  → 兜底（404 空态，不是白屏）
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'workbench',
    component: () => import('@/views/WorkbenchView.vue'),
    meta: { title: '工作台' },
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('@/views/TasksView.vue'),
    meta: { title: '任务中心' },
  },
  {
    path: '/models',
    name: 'models',
    component: () => import('@/views/ModelsView.vue'),
    meta: { title: '模型库' },
  },
  {
    path: '/hardware',
    name: 'hardware',
    component: () => import('@/views/HardwareView.vue'),
    meta: { title: '硬件能力' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: '系统配置' },
  },
  {
    // 兜底：绝不白屏（04 §2.3）
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const t = to.meta?.title as string | undefined
  document.title = t ? `${t} · WebSR` : 'WebSR · 图像超分修复'
})

export default router
