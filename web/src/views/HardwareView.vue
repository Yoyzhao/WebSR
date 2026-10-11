<script setup lang="ts">
/**
 * 硬件能力 —— 档位事实、设备信息、EP 证据、档位模拟（P4，01 §6）。
 *
 * 核心约束：
 *   1. **EP 真实性判据是「目标 EP 节点数 > 0 且 CPU 节点数 = 0」**，
 *      只看节点数 > 0 无法识别静默回退（project-rules §2.2-2）。
 *   2. 档位模拟（P3 能力）必须在界面上**显式标注为「模拟」**，
 *      且不能让模拟值冒充真实档位（01 §7.1）—— 顶栏显示「T1 · 模拟 T3」。
 *   3. 保底档必须可见：标定未完成时给出明确横幅与入口。
 *   4. max-width 1344（2026-10-08 与其余独立页统一）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Warning, InfoFilled, Refresh } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import SettingsRow from '@/components/SettingsRow.vue'
import FallbackNotice from '@/components/FallbackNotice.vue'
import ActionButtons from '@/components/ActionButtons.vue'
import EmptyState from '@/components/EmptyState.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { useSystemStore } from '@/stores/system'
import { PAGE_MAX_WIDTH, backendLabel } from '@/constants'
import type { Capabilities } from '@/types/api'

const system = useSystemStore()

const simForceTier = ref<'T0' | 'T1' | 'T2' | 'T3'>('T2')
const simVram = ref('')
const simTensorrt = ref('')
const simBusy = ref(false)
const calibrating = ref(false)

const caps = computed<Capabilities | null>(() => system.capabilities)

const facts = computed(() => caps.value?.device_facts ?? null)
const backends = computed(() => caps.value?.verified_backends ?? [])
const evidence = computed(() => caps.value?.ep_evidence ?? [])

/** 是否真正存在一个「已验证且 CPU 节点为 0」的 GPU 后端 —— 这是 T1 以上的硬条件 */
const hasVerifiedGpu = computed(() =>
  evidence.value.some((e) => e.verified && e.cpu_node_count === 0 && !e.provider.includes('CPU')),
)

/** 当前生效路径的判据说明（用真实证据算，不写死文案） */
const activeReason = computed(() => {
  const gpu = evidence.value.find((e) => e.verified && e.cpu_node_count === 0 && !e.provider.includes('CPU'))
  if (gpu) return `${backendLabel(gpu.provider)} 已生效（运算节点 ${gpu.node_count} 个，无运算落在 CPU 上）`
  return '未检测到可用的 GPU 加速方式 —— 当前使用 CPU 处理'
})

async function runCalibration() {
  calibrating.value = true
  try {
    const { jobId } = await system.calibrate()
    // 🔴 标定是**异步作业**（吃满 GPU 数秒～数十秒）：`POST` 只投递就返回。
    //    此前这里发完 POST 就结束 —— 界面既不显示进度、也不刷新能力快照，
    //    用户看到的就是「点了重新标定没反应」（实测反馈）。现在等到终态再刷新。
    const st = await system.waitForCalibration(jobId)
    // 标定结论按「探针模型 + 硬件指纹」被引擎消费；刷新能力快照后，
    // 横幅与「参数来源」才会即时反映结果（不必整页刷新）。
    await system.load()
    if (st === null) {
      ElMessage.warning('标定仍在进行，完成后本页「参数来源」会自动切换为实测取值')
    } else if (st.status === 'completed' && st.stored) {
      const tile = st.outcome?.recommended_tile
      ElMessage.success(`标定完成：已按本机实测结果更新参数${tile ? `（推荐块尺寸 ${tile}）` : ''}`)
    } else if (st.status === 'skipped') {
      ElMessage.warning(`标定结论未采纳：${st.skipped_reason || '未产生可入库的结果'}，仍按保守取值运行`)
    } else {
      ElMessage.error(`标定失败：${st.error || '未知错误'}，仍按保守取值运行`)
    }
  } catch (e) {
    ElMessage.error((e as Error).message || '标定触发失败')
  } finally {
    calibrating.value = false
  }
}

/** 把服务端已保存的模拟声明回填到控件（唯一来源是设置表，不是本地记忆） */
function syncDraftFromStore() {
  const d = system.simDraft
  simForceTier.value = (['T0', 'T1', 'T2', 'T3'].includes(d.forceTier) ? d.forceTier : 'T2') as
    | 'T0'
    | 'T1'
    | 'T2'
    | 'T3'
  simVram.value = d.forceVramMb
  simTensorrt.value = d.forceTensorrt
}

/**
 * 提交模拟设置 —— **服务端生效**（档位判定 / 模型门控 / EP 候选链 / 任务决策输入）。
 * 前端不做任何本地推断，提交后一律以服务端返回的能力快照为准。
 */
async function submitSimulation(enabled: boolean) {
  simBusy.value = true
  try {
    await system.applySimulation({
      enabled,
      forceTier: simForceTier.value,
      forceVramMb: simVram.value.trim(),
      forceTensorrt: simTensorrt.value,
    })
  } catch (e) {
    ElMessage.error((e as Error).message || '档位模拟设置失败')
    syncDraftFromStore()
  } finally {
    simBusy.value = false
  }
}

async function toggleSimulation(next: boolean) {
  if (next) {
    try {
      await ElMessageBox.confirm(
        '开启后，系统将按你声明的档位与可用显存来判断硬件能力：档位判定、模型可用性、' +
          '加速方式选择与任务参数都按声明值执行。模拟期间的执行结果不代表真实性能，' +
          '仅用于验证不同硬件条件下的表现。请确认开启？',
        '开启档位模拟',
        { confirmButtonText: '确认开启', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return // 用户取消：什么都不做（控件状态由服务端返回值决定，本就未变）
    }
    await submitSimulation(true)
    ElMessage.warning(`档位模拟已开启：目标 ${simForceTier.value}`)
  } else {
    await submitSimulation(false)
    ElMessage.success('已关闭档位模拟，恢复真实探测值')
  }
}

function changeForceTier(t: string) {
  simForceTier.value = t as 'T0' | 'T1' | 'T2' | 'T3'
  if (system.simulating) void submitSimulation(true)
}

function changeTensorrt(v: string) {
  simTensorrt.value = v
  if (system.simulating) void submitSimulation(true)
}

function commitVram() {
  if (system.simulating) void submitSimulation(true)
}

onMounted(async () => {
  if (!system.capabilities) await system.load()
  await system.loadSimulationDraft()
  syncDraftFromStore()
})
</script>

<template>
  <div class="hw-page" :style="{ maxWidth: PAGE_MAX_WIDTH.hardware + 'px' }">
    <PageHeader title="硬件能力" subtitle="本机硬件信息、加速方式验证结果与档位模拟">
      <template #actions>
        <ActionButtons
          :actions="[{ key: 'calibrate', label: calibrating ? '标定中…' : '重新标定', disabled: calibrating }]"
          size="sm"
          @action="runCalibration"
        >
          <template #icon-calibrate>
            <el-icon :size="13"><Refresh /></el-icon>
          </template>
        </ActionButtons>
      </template>
    </PageHeader>

    <FallbackNotice v-if="system.usingFallback" :visible="true" />

    <!-- 空 / 加载 -->
    <EmptyState v-if="!caps" icon-size="48" title="正在读取硬件信息" description="首次探测需要数秒，请稍候" />

    <template v-else>
      <!-- 档位 -->
      <SectionCard title="硬件档位" :subtitle="caps.tier_label">
        <SettingsRow label="当前档位" :value="caps.tier" mono />
        <SettingsRow label="判定依据" :value="caps.tier_reason" />
        <SettingsRow label="实际生效加速方式" :value="backendLabel(caps.active_backend)" mono tone="success" />
        <SettingsRow label="实际生效精度" :value="caps.active_precision" mono />
        <SettingsRow
          label="参数来源"
          :value="caps.using_fallback ? '保守取值（未标定）' : '本机标定实测值'"
          :tone="caps.using_fallback ? 'warning' : 'success'"
        />
        <SettingsRow label="当前路径判据" :value="activeReason" />
      </SectionCard>

      <!-- 设备事实 -->
      <SectionCard v-if="facts" title="设备信息" subtitle="可用显存为实时读取值，非标称容量">
        <SettingsRow label="CPU" :value="facts.cpu" />
        <SettingsRow label="GPU" :value="facts.gpu" />
        <SettingsRow label="驱动版本" :value="facts.driver" mono />
        <SettingsRow label="系统内存" :value="facts.system_ram_gb + ' GB'" mono />
        <SettingsRow
          label="可用显存（实读）"
          :value="facts.available_vram_gb.toFixed(1) + ' GB'"
          mono
          tone="success"
        />
        <SettingsRow
          label="标称显存"
          :value="facts.nominal_vram_gb + ' GB'"
          mono
          tone="tertiary"
        />
      </SectionCard>

      <!-- 加速方式验证 -->
      <SectionCard title="加速方式验证">
        <template #actions>
          <el-tooltip
            content="用于确认加速是否真正生效：需该方式的运算节点数大于 0，且没有运算落在 CPU 上。节点数为 1 通常表示整图合并计算，属正常现象。"
            placement="top"
          >
            <el-icon :size="14" class="hw-flag"><InfoFilled /></el-icon>
          </el-tooltip>
        </template>

        <table class="hw-table">
          <thead>
            <tr>
              <th>加速方式</th>
              <th class="hw-num">运算节点</th>
              <th class="hw-num">CPU 节点</th>
              <th>结论</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in evidence" :key="e.provider">
              <td>
                <div class="hw-prov">
                  <span class="hw-mono">{{ backendLabel(e.provider) }}</span>
                  <span class="hw-prov-note">{{ e.node_ownership }}</span>
                </div>
              </td>
              <td class="hw-num hw-mono">{{ e.node_count }}</td>
              <td class="hw-num hw-mono">{{ e.cpu_node_count }}</td>
              <td>
                <span class="hw-verdict" :class="e.verified ? 'is-ok' : 'is-idle'">
                  {{ e.verified ? '已生效' : '未使用' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!hasVerifiedGpu" class="hw-warn">
          <el-icon :size="12"><Warning /></el-icon>
          尚未检测到可用的 GPU 加速方式，当前使用 CPU 处理，处理大图会较慢。
        </p>
        <ul class="hw-notes">
          <li v-for="(e, i) in evidence" :key="i">{{ e.note }}</li>
        </ul>
      </SectionCard>

      <!-- 可用加速方式 -->
      <SectionCard title="可用加速方式" subtitle="依据实测能力给出，不按显卡型号预设">
        <div class="hw-be-list">
          <div v-for="b in backends" :key="b.id" class="hw-be" :class="{ 'is-idle': !b.verified }">
            <div class="hw-be-head">
              <span class="hw-be-id hw-mono">{{ backendLabel(b.label) }}</span>
              <span class="hw-verdict" :class="b.verified ? 'is-ok' : 'is-idle'">
                {{ b.verified ? '可用' : '不可用' }}
              </span>
            </div>
            <div class="hw-be-meta hw-mono">
              精度 {{ b.precision }} · 运算节点 {{ b.node_count }} · CPU 节点 {{ b.cpu_node_count }}
            </div>
          </div>
        </div>
      </SectionCard>

      <!-- 档位模拟 -->
      <SectionCard title="档位模拟" subtitle="用于在其他硬件条件下预览效果，不影响本机实测信息">
        <div class="hw-sim-warn">
          <el-icon :size="13"><Warning /></el-icon>
          <div>
            <p class="hw-sim-msg">
              开启后，系统将按你声明的档位与可用显存来判断硬件能力，并据此决定模型可用性、加速方式与处理参数。
            </p>
            <p class="hw-sim-sub">
              它只影响「能力判定」，不会修改本机探测到的设备信息；模拟期间的执行结果不代表真实性能，
              请勿用于评估硬件。相同的开关也可在「系统配置」页使用。
            </p>
          </div>
        </div>

        <SettingsRow label="模拟开关" :hint="system.simulating ? '当前处于模拟状态' : '当前使用本机实测值'">
          <SegmentedControl
            :model-value="system.simulating ? 'on' : 'off'"
            :options="[
              { label: '关闭', value: 'off' },
              { label: '开启', value: 'on' },
            ]"
            size="sm"
            @update:model-value="toggleSimulation($event === 'on')"
          />
        </SettingsRow>

        <SettingsRow label="目标档位" hint="T0 仅 CPU ｜ T1 入门独显 8G ｜ T2 高端独显 16–24G ｜ T3 专业显卡">
          <SegmentedControl
            :model-value="simForceTier"
            :options="[
              { label: 'T0', value: 'T0' },
              { label: 'T1', value: 'T1' },
              { label: 'T2', value: 'T2' },
              { label: 'T3', value: 'T3' },
            ]"
            size="sm"
            @update:model-value="changeForceTier($event)"
          />
        </SettingsRow>

        <SettingsRow
          label="声明可用显存"
          hint="用于档位推导与模型置灰；填 MB（如 24576）或带 G 后缀（如 24G），留空 = 不声明"
        >
          <input
            class="hw-input hw-mono"
            type="text"
            placeholder="例如 24G"
            :value="simVram"
            :disabled="simBusy"
            @input="simVram = ($event.target as HTMLInputElement).value"
            @change="commitVram"
          />
        </SettingsRow>

        <SettingsRow label="TensorRT 支持" hint="将 TensorRT 纳入加速方式候选列表">
          <SegmentedControl
            :model-value="simTensorrt"
            :options="[
              { label: '不声明', value: '' },
              { label: '声明可用', value: 'true' },
              { label: '声明不可用', value: 'false' },
            ]"
            size="sm"
            @update:model-value="changeTensorrt($event)"
          />
        </SettingsRow>

        <template v-if="system.simulating">
          <SettingsRow label="当前声明" :value="system.tierBadgeText" tone="warning" simulated />
          <SettingsRow label="判定依据" :value="system.tierReason" />
          <SettingsRow
            label="本机真实档位"
            :value="system.realTierLabel"
            hint="模拟值不会覆盖真实档位——本机真实档位恒由实测硬件信息推导"
          />
        </template>
      </SectionCard>
    </template>
  </div>
</template>

<style scoped>
.hw-page {
  display: flex;
  flex-direction: column;
  gap: var(--Scale-space-5);
  padding: var(--Scale-space-5) var(--Scale-space-6) 40px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

.hw-flag {
  color: var(--Theme-text-tertiary);
  cursor: help;
}

/* 证据表：无纵向边框线、无斑马纹（禁止事项第 7 条） */
.hw-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.hw-table th {
  text-align: left;
  font-weight: 500;
  color: var(--Theme-text-tertiary);
  padding: 7px 10px;
  border-bottom: 1px solid var(--Theme-border-subtle);
}

.hw-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--Theme-border-subtle);
  color: var(--Theme-text-secondary);
  vertical-align: middle;
}

.hw-table tbody tr:last-child td {
  border-bottom: none;
}

.hw-num {
  text-align: right;
  width: 92px;
}

.hw-prov {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.hw-prov-note {
  font-size: var(--font-size-12);
  color: var(--Theme-text-tertiary);
}

.hw-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* 档位模拟的显存声明输入框（T-901）：与设置页输入框同口径的 token */
.hw-input {
  width: 100%;
  max-width: 200px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-primary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 9px;
  outline: none;
}

.hw-input:focus {
  border-color: var(--Theme-primary);
}

.hw-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.hw-verdict {
  font-size: var(--font-size-12);
}

.hw-verdict.is-ok {
  color: var(--Theme-success);
}

.hw-verdict.is-idle {
  color: var(--Theme-text-tertiary);
}

.hw-warn {
  margin: 10px 0 0;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-warning);
}

.hw-notes {
  margin: 10px 0 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

/* 后端列表 */
.hw-be-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.hw-be {
  padding: 10px 12px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-inner);
  background: var(--Theme-bg-elevated);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.hw-be.is-idle {
  opacity: 0.55;
}

.hw-be-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.hw-be-id {
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hw-be-meta {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

/* 模拟 */
.hw-sim-warn {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  border-radius: var(--Scale-radius-inner);
  background: color-mix(in srgb, var(--Theme-warning) 11%, transparent);
  color: var(--Theme-warning);
  font-size: var(--font-size-13);
  line-height: 18px;
}

.hw-sim-msg {
  margin: 0;
  font-weight: 500;
}

.hw-sim-sub {
  margin: 2px 0 0;
  opacity: 0.85;
  font-size: var(--font-size-12);
}

@media (max-width: 1023px) {
  .hw-page {
    padding: 18px 16px 32px;
  }
  .hw-table {
    font-size: var(--font-size-12);
  }
  .hw-num {
    width: 68px;
  }
}
</style>
