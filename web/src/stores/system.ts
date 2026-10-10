/**
 * 系统 store —— 硬件能力事实、档位、EP 验证证据与档位模拟状态。
 *
 * 全站共享理由：顶栏档位徽标、工作台参数面板、硬件页、配置页都要读同一份事实。
 *
 * ⚠️ **T-901 起：档位模拟是服务端能力。**
 * 此前本 store 在浏览器里把 `caps.tier` 改掉再写 localStorage —— 那是**纯前端的假象**：
 * 后端一无所知，真实决策 / 模型门控 / 降级链完全没走模拟档位（违反 PRD §2.3 原则 4）。
 * 现在：① 前端只负责**提交模拟设置**；② 档位与 `simulation` 状态一律以**服务端返回值**为准；
 * ③ 模拟值不得冒充真实档位——`realTierLabel` 恒由真实硬件事实推导。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchCapabilities, fetchSettings, saveSettings, triggerCalibration } from '@/api/client'
import type { Capabilities, Setting } from '@/types/api'

/**
 * 档位模拟的四个设置键。
 * 与后端 `services/simulation.py` 的 `KEY_*` 同源——改一处必须同步，
 * 否则会出现「界面改了、服务端没变」的静默失败。
 */
export const SIMULATION_KEYS = {
  enabled: 'simulation_enabled',
  tier: 'force_tier',
  vramMb: 'force_vram_mb',
  tensorrt: 'force_has_tensorrt',
} as const

/** 档位模拟的声明草稿（空串 = 不声明该项） */
export interface SimulationDraft {
  enabled: boolean
  forceTier: string
  forceVramMb: string
  forceTensorrt: string
}

const TIERS = ['T0', 'T1', 'T2', 'T3']

export const useSystemStore = defineStore('system', () => {
  const capabilities = ref<Capabilities | null>(null)
  const loading = ref(false)
  const calibrating = ref(false)

  /** 模拟设置的回填草稿（来自服务端设置表，不是本地记忆） */
  const simDraft = ref<SimulationDraft>({
    enabled: false,
    forceTier: 'T2',
    forceVramMb: '',
    forceTensorrt: '',
  })

  /** 档位模拟是否**确实改变了判定输入**（服务端口径：开了总闸但没声明任何项时为 false） */
  const simulating = computed(() => capabilities.value?.simulation.enabled ?? false)

  const tier = computed(() => capabilities.value?.tier ?? 'T1')
  const tierLabel = computed(() => capabilities.value?.tier_label ?? 'T1 · 消费级 8G')
  const tierReason = computed(() => capabilities.value?.tier_reason ?? '')
  const activeBackend = computed(() => capabilities.value?.active_backend ?? '—')
  const activePrecision = computed(() => capabilities.value?.active_precision ?? '—')
  const usingFallback = computed(() => capabilities.value?.using_fallback ?? false)

  /**
   * 可用显存（GB）—— **恒为真实探测值**。
   *
   * 为什么不用模拟值：`device_facts` 是"硬件事实"，模拟只覆盖**判定输入**。
   * 模型是否可用由服务端 `ModelOut.available` 裁定（它按模拟档位算），
   * 前端不得用这个数字二次否定服务端结论（否则模拟档位下会错误置灰）。
   */
  const availableVramGb = computed(() => capabilities.value?.device_facts.available_vram_gb ?? 0)

  /**
   * 本机**真实**档位 —— 不受模拟影响，用于"真实 vs 模拟"的对照展示。
   * 从真实 device_facts + 已验证后端推导，与后端 `derive_tier` 同规则。
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

  /** 顶栏文案：显示「T1 · 模拟 T2」而不是直接显示 T2（避免误导） */
  const tierBadgeText = computed(() => {
    const forced = capabilities.value?.simulation.force_tier
    if (simulating.value && forced) return `${realTierLabel.value} · 模拟 ${forced}`
    return tierLabel.value
  })

  async function load(): Promise<void> {
    loading.value = true
    try {
      capabilities.value = await fetchCapabilities()
    } finally {
      loading.value = false
    }
  }

  /** 从服务端读回模拟设置，用于把开关与输入框回填成"当前真实生效值" */
  async function loadSimulationDraft(): Promise<void> {
    try {
      const items: Setting[] = await fetchSettings()
      const val = (k: string): string => items.find((s) => s.key === k)?.value ?? ''
      const forced = val(SIMULATION_KEYS.tier)
      simDraft.value = {
        enabled: val(SIMULATION_KEYS.enabled) === 'true',
        forceTier: TIERS.includes(forced) ? forced : 'T2',
        forceVramMb: val(SIMULATION_KEYS.vramMb),
        forceTensorrt: val(SIMULATION_KEYS.tensorrt),
      }
    } catch {
      // 读不到设置就保持默认展示——模拟是开发期能力，不得阻断页面
    }
  }

  /**
   * 提交档位模拟设置（**服务端生效**）。
   *
   * 生效范围：档位判定 / 模型可用性门控（列表页置灰 + 提交兜底）/ EP 候选链 / 任务决策输入。
   * 提交后重新拉取能力快照与设置——前端**不做任何本地推断**，避免与服务端口径漂移。
   */
  async function applySimulation(next: SimulationDraft): Promise<void> {
    await saveSettings([
      { key: SIMULATION_KEYS.enabled, value: next.enabled ? 'true' : 'false', type: 'boolean' },
      { key: SIMULATION_KEYS.tier, value: next.forceTier, type: 'string' },
      { key: SIMULATION_KEYS.vramMb, value: next.forceVramMb, type: 'string' },
      { key: SIMULATION_KEYS.tensorrt, value: next.forceTensorrt, type: 'string' },
    ])
    await Promise.all([load(), loadSimulationDraft()])
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
    simDraft,
    simulating,
    tier,
    tierLabel,
    tierReason,
    realTierLabel,
    tierBadgeText,
    activeBackend,
    activePrecision,
    usingFallback,
    availableVramGb,
    load,
    loadSimulationDraft,
    applySimulation,
    calibrate,
  }
})
