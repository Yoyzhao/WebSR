/**
 * 断点判定单一入口（docs/prototype/05-响应式规范.md §7）。
 *
 * 禁止各组件自行判断窗口宽度 —— 断点逻辑集中在这里。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { BREAKPOINTS } from '@/constants'

export type LayoutTier = 'compact' | 'medium' | 'wide'

const width = ref(window.innerWidth)

function onResize(): void {
  width.value = window.innerWidth
}

let listenerCount = 0

export function useBreakpoint() {
  onMounted(() => {
    if (listenerCount === 0) window.addEventListener('resize', onResize)
    listenerCount += 1
    onResize()
  })

  onUnmounted(() => {
    listenerCount -= 1
    if (listenerCount === 0) window.removeEventListener('resize', onResize)
  })

  /** < 1024 单栏降级 ｜ < 1440 两栏 + 参数抽屉 ｜ ≥ 1440 三栏 */
  const tier = computed<LayoutTier>(() => {
    if (width.value < BREAKPOINTS.compact) return 'compact'
    if (width.value < BREAKPOINTS.medium) return 'medium'
    return 'wide'
  })

  return {
    width,
    tier,
    isCompact: computed(() => tier.value === 'compact'),
    isMedium: computed(() => tier.value === 'medium'),
    isWide: computed(() => tier.value === 'wide'),
  }
}
