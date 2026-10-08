/**
 * 系统 store —— 硬件能力事实、档位、EP 验证证据与档位模拟状态。
 *
 * 全站共享理由：顶栏档位徽标、工作台参数面板、硬件页、配置页都要读同一份事实，
 * 且档位模拟开关会同时改变顶栏徽标与全局横幅（01 §7.1）。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchCapabilities, triggerCalibration } from '@/api/client'
import type { Capabilities } from '@/types/api'

export const useSystemStore = defineStore('system', () => {
  const capabilities = ref<Capabilities | null>(null)
  const loading = ref(false)
  const calibrating = ref(false)

  /**
   * 档位模拟的持久化 key。
   *
   * 为什么必须持久化：档位模拟是**跨页可见**的状态（顶栏徽标 + 全局横幅 + 参数面板
   * 都要反映它）。Pinia 是内存态，整页导航/刷新即丢失，会导致「在硬件页开启了模拟，
   * 回到工作台却变回真实档位」的不一致（闭环验证 T-510-13 实测暴露）。
   * 故与主题同法：写入 localStorage，并在 load() 后回放。
   */
  const SIM_STORAGE_KEY = 'websr.simulation'

  function readStoredSimulation(): { enabled: boolean; force_tier: string | null } {
    try {
      const raw = localStorage.getItem(SIM_STORAGE_KEY)
      if (!raw) return { enabled: false, force_tier: null }
      const parsed = JSON.parse(raw) as { enabled?: boolean; force_tier?: string | null }
      return { enabled: parsed.enabled === true, force_tier: parsed.force_tier ?? null }
    } catch {
      return { enabled: false, force_tier: null }
    }
  }

  function persistSimulation(next: { enabled: boolean; force_tier: string | null }): void {
    try {
      localStorage.setItem(SIM_STORAGE_KEY, JSON.stringify(next))
    } catch {
      // localStorage 不可用（隐私模式等）时不阻断流程，仅本次会话不持久化
    }
  }

  /** 档位模拟是否开启 —— 决定顶栏徽标文案与全局横幅（01 §7.1） */
  const simulating = computed(() => capabilities.value?.simulation.enabled ?? false)

  const tier = computed(() => capabilities.value?.tier ?? 'T1')
  const tierLabel = computed(() => capabilities.value?.tier_label ?? 'T1 · 消费级 8G')
  const activeBackend = computed(() => capabilities.value?.active_backend ?? '—')
  const activePrecision = computed(() => capabilities.value?.active_precision ?? '—')
  const usingFallback = computed(() => capabilities.value?.using_fallback ?? false)

  /** 可用显存（GB）—— 用于模型置灰判定与前端展示 */
  const availableVramGb = computed(() => capabilities.value?.device_facts.available_vram_gb ?? 0)

  /**
   * 本机**真实**档位 —— 不受模拟影响。
   *
   * 为什么需要它：模拟开启后 `tier` / `tierLabel` 会被改写为模拟值，
   * 于是"真实档位"就无处可读了。这个 computed 从固定基线推导，
   * 用于横幅与提示语，保证用户始终能看到「真实 vs 模拟」的对照。
   */
  const realTierLabel = computed(() => {
    const gpuVerified = (capabilities.value?.verified_backends ?? []).some(
      (b) => b.verified && b.cpu_node_count === 0 && !b.label.includes('CPU'),
    )
    const vram = capabilities.value?.device_facts.available_vram_gb ?? 0
    if (vram >= 48) return 'T3'
    if (vram >= 16) return 'T2'
    return gpuVerified ? 'T1 · 消费级 8G' : 'T0'
  })

  /** 模拟态的顶栏文案：显示「T2 · 模拟 T3」而不是直接显示 T3（避免误导） */
  const tierBadgeText = computed(() => {
    const forced = capabilities.value?.simulation.force_tier
    // 注意：tier 是 computed，必须取 .value —— 漏掉会渲染成 "[object Object]"
    if (simulating.value && forced) return `${realTierLabel.value} · 模拟 ${forced}`
    return tierLabel.value
  })

  async function load(): Promise<void> {
    loading.value = true
    try {
      const caps = await fetchCapabilities()
      // 回放持久化的模拟态：使模拟在整个 SPA 生命周期（含刷新）内保持可见
      const stored = readStoredSimulation()
      if (stored.enabled && stored.force_tier) {
        caps.simulation = { enabled: true, force_tier: stored.force_tier }
        caps.tier = stored.force_tier as Capabilities['tier']
        caps.tier_label = `${stored.force_tier} · 模拟档位`
      } else {
        caps.simulation = { enabled: false, force_tier: null }
      }
      capabilities.value = caps
    } finally {
      loading.value = false
    }
  }

  /** 切换档位模拟开关（P3，ADR-004） */
  function setSimulation(enabled: boolean, forceTier: string | null): void {
    if (!capabilities.value) return
    capabilities.value.simulation = { enabled, force_tier: forceTier }
    persistSimulation({ enabled, force_tier: forceTier })
    // 模拟档位同时改变生效后端与精度声明，使顶栏与硬件页能一致地反映"模拟"
    if (enabled && forceTier) {
      capabilities.value.tier = forceTier as Capabilities['tier']
      capabilities.value.tier_label = `${forceTier} · 模拟档位`
    } else {
      capabilities.value.tier = 'T1'
      capabilities.value.tier_label = 'T1 · 消费级 8G'
    }
  }

  async function calibrate(): Promise<void> {
    calibrating.value = true
    try {
      await triggerCalibration()
    } finally {
      calibrating.value = false
    }
  }

  return {
    capabilities,
    loading,
    calibrating,
    simulating,
    tier,
    tierLabel,
    realTierLabel,
    tierBadgeText,
    activeBackend,
    activePrecision,
    usingFallback,
    availableVramGb,
    load,
    setSimulation,
    calibrate,
  }
})
