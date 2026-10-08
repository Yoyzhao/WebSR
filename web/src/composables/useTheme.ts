/**
 * 主题唯一入口（docs/prototype/02-视觉与主题基线.md §6）。
 *
 * ⚠️ 禁止在组件里判断深浅色 —— 主题判断必须集中在这里，
 *    否则主题切换必然漏项，且无法支持"跟随系统"（03 §5-5）。
 *
 * 三态策略（T-401 定案）：
 *   默认深色 → 可手动切浅色 → 可跟随系统；手动切换后以手动值为准并持久化。
 */
import { computed, ref } from 'vue'

export type ThemeMode = 'dark' | 'light' | 'system'
type ResolvedTheme = 'dark' | 'light'

const STORAGE_KEY = 'websr.theme-mode'

const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)')

const mode = ref<ThemeMode>(readStoredMode())
const systemDark = ref(systemPrefersDark.matches)

function readStoredMode(): ThemeMode {
  const raw = localStorage.getItem(STORAGE_KEY)
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'dark'
}

/** 解析后的实际主题 */
const resolved = computed<ResolvedTheme>(() =>
  mode.value === 'system' ? (systemDark.value ? 'dark' : 'light') : mode.value,
)

/**
 * 落到 DOM：
 *   1. <html data-theme="Dark|Light"> —— 驱动业务 token（tokens.css）
 *   2. <html class="dark">         —— 驱动 Element Plus 深色变量
 *
 * 两者必须同步设置：业务色只用 tokens.css，EP 组件内部用其自身色板
 * （02 §6.2）。
 */
function apply(): void {
  const el = document.documentElement
  const isLight = resolved.value === 'light'
  el.dataset.theme = isLight ? 'Light' : 'Dark'
  el.classList.toggle('dark', !isLight)
  el.style.colorScheme = isLight ? 'light' : 'dark'
}

systemPrefersDark.addEventListener('change', (event) => {
  systemDark.value = event.matches
  // 仅在"跟随系统"模式下才需要重新落盘
  if (mode.value === 'system') apply()
})

export function useTheme() {
  function setMode(next: ThemeMode): void {
    mode.value = next
    localStorage.setItem(STORAGE_KEY, next)
    apply()
  }

  /** 顶栏按钮：在深色 / 浅色之间切换（离开"跟随系统"） */
  function toggle(): void {
    setMode(resolved.value === 'light' ? 'dark' : 'light')
  }

  /**
   * 顶栏按钮：三态循环 dark → light → system → dark。
   * 与 `toggle()` 的区别：`toggle` 只做二元切换（供设置页使用），
   * `cycle` 会把"跟随系统"也纳入循环（02 §6.1 三态要求）。
   */
  function cycle(): void {
    const order: ThemeMode[] = ['dark', 'light', 'system']
    const idx = order.indexOf(mode.value)
    setMode(order[(idx + 1) % order.length])
  }

  return { mode, resolved, setMode, toggle, cycle, apply }
}

/** 应用启动时调用一次（main.ts） */
export function initTheme(): void {
  apply()
}
