<script setup lang="ts">
/**
 * 应用外壳 —— 顶栏 + 主内容区 + 窄屏导航抽屉（01 §2 全局骨架）。
 *
 * 约束：
 *   - 顶栏固定 56px；主内容区独立滚动，页面切换不重置顶栏。
 *   - compact（<1024）下导航收进左侧抽屉。
 *   - 全局只在这里初始化系统能力与任务列表（跨页共享数据），
 *     使「顶栏档位徽标」「全局保底档横幅」在任何页面都可见。
 */
import { onMounted, onBeforeUnmount, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDrawer } from 'element-plus'
import AppTopBar from '@/components/AppTopBar.vue'
import FallbackNotice from '@/components/FallbackNotice.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useSystemStore } from '@/stores/system'
import { useTaskStore } from '@/stores/tasks'

const route = useRoute()
const router = useRouter()
// 断点在本组件仅用于注册共享监听（响应式值由各页自行消费），
// 不直接参与渲染判断，因此不解构具体字段。
useBreakpoint()


const system = useSystemStore()
const tasks = useTaskStore()

const navOpen = ref(false)

const NAV = [
  { path: '/', label: '工作台' },
  { path: '/tasks', label: '任务中心' },
  { path: '/models', label: '模型库' },
  { path: '/hardware', label: '硬件能力' },
  { path: '/settings', label: '系统配置' },
]

const activePath = computed(() => {
  const p = route.path
  if (p.startsWith('/tasks')) return '/tasks'
  if (p.startsWith('/models')) return '/models'
  if (p.startsWith('/hardware')) return '/hardware'
  if (p.startsWith('/settings')) return '/settings'
  return '/'
})

/** 保底档横幅：只在「真·保底档」且不在硬件页时展示（硬件页有专门区块说明） */
const showFallbackBanner = computed(
  () => system.usingFallback && !system.simulating && route.name !== 'hardware',
)

/**
 * 档位模拟横幅：模拟开启时全局可见（01 §7.1）。
 *
 * 与保底档横幅**互斥** —— 模拟是更强的前置声明，若同时出现两条警示，
 * 用户无法判断哪一条才是当前生效参数的真实来源。
 * 硬件页不展示（该页有专门的模拟区块与二次确认弹窗）。
 */
const showSimulationBanner = computed(() => system.simulating && route.name !== 'hardware')

const simulationBannerText = computed(() => {
  const forced = system.capabilities?.simulation.force_tier ?? '—'
  return `档位模拟已开启 · 强制声明 ${forced}（本机真实档位 ${system.realTierLabel}）· 执行结果不代表真实性能`
})

function go(path: string) {
  navOpen.value = false
  if (path !== activePath.value) router.push(path)
}

onMounted(() => {
  // 全站共享数据的唯一初始化点
  system.load()
  tasks.load()
})

onBeforeUnmount(() => {
  tasks.unsubscribeAll()
})
</script>

<template>
  <div class="app-shell">
    <AppTopBar @toggle-nav="navOpen = true" />

    <FallbackNotice v-if="showFallbackBanner" :visible="true" class="app-fallback" />

    <FallbackNotice
      v-if="showSimulationBanner"
      :visible="true"
      :text="simulationBannerText"
      class="app-fallback"
    />

    <main class="app-main">
      <RouterView v-slot="{ Component }">
        <component :is="Component" />
      </RouterView>
    </main>

    <!-- 窄屏导航抽屉 -->
    <ElDrawer v-model="navOpen" direction="ltr" size="240px" :with-header="false" class="nav-drawer">
      <nav class="nd-nav">
        <div class="nd-brand">
          <span class="nd-logo">SR</span>
          <span class="nd-name">WebSR</span>
        </div>
        <button
          v-for="n in NAV"
          :key="n.path"
          type="button"
          class="nd-link"
          :class="{ 'is-active': activePath === n.path }"
          @click="go(n.path)"
        >
          {{ n.label }}
        </button>
      </nav>
    </ElDrawer>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: var(--Theme-bg-app);
}

.app-fallback {
  flex: 0 0 auto;
}

.app-main {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.nd-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
}

.nd-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 14px;
}

.nd-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-primary);
  color: var(--Theme-on-primary);
  font-size: var(--font-size-12);
  font-weight: 700;
}

.nd-name {
  font-size: var(--font-size-16);
  font-weight: 600;
  color: var(--Theme-text-primary);
}

.nd-link {
  appearance: none;
  border: none;
  background: transparent;
  text-align: left;
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  color: var(--Theme-text-secondary);
  padding: 8px 12px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.nd-link:hover {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.nd-link.is-active {
  color: var(--Theme-primary);
  font-weight: 500;
  background: color-mix(in srgb, var(--Theme-primary) 10%, transparent);
}
</style>
