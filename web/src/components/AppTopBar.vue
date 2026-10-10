<script setup lang="ts">
/**
 * AppTopBar —— 全局顶栏（5A 定稿 · 15 组件之一）
 *
 * 职责：品牌标识 + 主导航 + 档位徽标 + 主题切换入口。
 * 约束（01 §2 全局骨架 / 02 §6 主题机制 / 01 §7.1 档位模拟）：
 *   - 高度固定 56px，底部 1px 描边，**不加阴影**（禁止事项第 6 条）。
 *   - 主题切换按钮是「三态循环」：深色 → 浅色 → 跟随系统 → 深色……
 *     按钮上不显示当前态文字，只用图标 + tooltip，避免顶栏拥挤。
 *   - **档位徽标必须常驻**：档位模拟开启时显示「T1 · 模拟 T3」——
 *     模拟值绝不能冒充真实档位（01 §7.1，闭环验证 T-510-13）。
 *   - compact（<1024）下导航收进抽屉，顶栏只留品牌 + 汉堡 + 徽标 + 主题。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Moon, Sunny, Monitor, Menu } from '@element-plus/icons-vue'
import { useTheme } from '@/composables/useTheme'
import { useSystemStore } from '@/stores/system'

const route = useRoute()
const router = useRouter()
const { mode, cycle } = useTheme()
const system = useSystemStore()

const emit = defineEmits<{ (e: 'toggle-nav'): void }>()

const NAV = [
  { path: '/', label: '工作台' },
  { path: '/tasks', label: '任务中心' },
  { path: '/models', label: '模型库' },
  { path: '/hardware', label: '硬件能力' },
  { path: '/settings', label: '系统配置' },
]

const activePath = computed(() => {
  const p = route.path
  // 详情/子路径归属到一级导航
  if (p.startsWith('/tasks')) return '/tasks'
  if (p.startsWith('/models')) return '/models'
  if (p.startsWith('/hardware')) return '/hardware'
  if (p.startsWith('/settings')) return '/settings'
  return '/'
})

const themeIcon = computed(() => (mode.value === 'dark' ? Moon : mode.value === 'light' ? Sunny : Monitor))
const themeTip = computed(() =>
  mode.value === 'dark' ? '深色模式（点击切换为浅色）' : mode.value === 'light' ? '浅色模式（点击跟随系统）' : '跟随系统（点击切换为深色）',
)

/** 档位徽标文案：模拟态下必须与真实档位可区分（01 §7.1） */
const tierBadge = computed(() => system.tierBadgeText)
const tierTip = computed(() =>
  system.simulating
    ? `档位模拟已开启（服务端生效）：档位判定、模型门控与任务决策都按声明的档位执行。执行结果不代表真实性能。`
    : `当前硬件档位：${system.tierLabel}`,
)

function go(path: string) {
  if (path !== activePath.value) router.push(path)
}
</script>

<template>
  <header class="topbar">
    <div class="tb-left">
      <button class="tb-burger" type="button" aria-label="打开导航" @click="emit('toggle-nav')">
        <el-icon :size="18"><Menu /></el-icon>
      </button>
      <div class="tb-brand" @click="go('/')">
        <span class="tb-logo">SR</span>
        <span class="tb-name">WebSR</span>
      </div>
    </div>

    <nav class="tb-nav" aria-label="主导航">
      <button
        v-for="n in NAV"
        :key="n.path"
        type="button"
        class="tb-link"
        :class="{ 'is-active': activePath === n.path }"
        :aria-current="activePath === n.path ? 'page' : undefined"
        @click="go(n.path)"
      >
        {{ n.label }}
      </button>
    </nav>

    <div class="tb-right">
      <!-- 档位徽标：模拟态加 warning 色与虚线描边，与真实档位明确区分 -->
      <span
        class="tb-tier"
        :class="{ 'is-sim': system.simulating }"
        :title="tierTip"
      >
        {{ tierBadge }}
      </span>
      <button class="tb-icon-btn" type="button" :title="themeTip" :aria-label="themeTip" @click="cycle()">
        <el-icon :size="17"><component :is="themeIcon" /></el-icon>
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  height: var(--layout-topbar-height);
  flex: 0 0 var(--layout-topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 20px;
  background: var(--Theme-bg-panel);
  border-bottom: 1px solid var(--Theme-border-subtle);
  /* 禁止事项第 6 条：常规卡片/容器不加阴影 */
}

.tb-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.tb-burger {
  display: none;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-button);
  background: transparent;
  color: var(--Theme-text-secondary);
  cursor: pointer;
}

.tb-burger:hover {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.tb-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
  padding: 4px 4px 4px 0;
}

.tb-logo {
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
  letter-spacing: 0.02em;
}

.tb-name {
  font-size: var(--font-size-16);
  font-weight: 600;
  color: var(--Theme-text-primary);
  letter-spacing: -0.01em;
}

.tb-nav {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.tb-link {
  appearance: none;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  color: var(--Theme-text-secondary);
  padding: 6px 12px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
  white-space: nowrap;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;
}

.tb-link:hover {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.tb-link.is-active {
  color: var(--Theme-primary);
  font-weight: 500;
  background: color-mix(in srgb, var(--Theme-primary) 10%, transparent);
}

.tb-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

/* 档位徽标 —— 常驻，模拟态用 warning 色 + 虚线描边（01 §7.1） */
.tb-tier {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-pill);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-secondary);
  font-family: var(--font-mono);
  font-size: var(--font-size-12);
  line-height: 16px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.tb-tier.is-sim {
  border-style: dashed;
  border-color: var(--Theme-warning);
  color: var(--Theme-warning);
}

.tb-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-button);
  background: transparent;
  color: var(--Theme-text-secondary);
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;
}

.tb-icon-btn:hover {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.tb-icon-btn:focus-visible,
.tb-burger:focus-visible,
.tb-link:focus-visible {
  outline: 2px solid var(--Theme-primary);
  outline-offset: 1px;
}

/* compact：导航收进抽屉 */
@media (max-width: 1023px) {
  .tb-burger {
    display: inline-flex;
  }
  .tb-nav {
    display: none;
  }
}

/* 极窄屏：徽标让位给品牌，仅保留状态点，避免顶栏换行 */
@media (max-width: 480px) {
  .tb-tier {
    max-width: 108px;
    overflow: hidden;
    text-overflow: ellipsis;
    display: block;
    line-height: 22px;
  }
}
</style>
