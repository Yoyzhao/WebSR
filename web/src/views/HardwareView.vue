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
import { Warning, InfoFilled } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import SettingsRow from '@/components/SettingsRow.vue'
import FallbackNotice from '@/components/FallbackNotice.vue'
import ActionButtons from '@/components/ActionButtons.vue'
import EmptyState from '@/components/EmptyState.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { useSystemStore } from '@/stores/system'
import { PAGE_MAX_WIDTH } from '@/constants'
import type { Capabilities } from '@/types/api'

const system = useSystemStore()

const simForceTier = ref<'T1' | 'T2' | 'T3'>('T2')
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
  if (gpu) return `${gpu.provider} 已生效（该提供器节点数 ${gpu.node_count}，CPU 节点数 ${gpu.cpu_node_count}）`
  return '未检测到已验证的 GPU 后端 —— 当前走 CPU 路径'
})

async function runCalibration() {
  calibrating.value = true
  try {
    await system.calibrate()
    // 阶段 C（T-805）已落地：标定不再是 S2 占位能力，结果会被引擎决策层按
    // 「硬件指纹 + 模型」自动消费。
    // ⚠️ 本页目前**没有**标定结果区块（原型 P4 未定义该区块）；
    //    故文案不得宣称"可在此查看曲线"——那会是一句骗人的话。
    ElMessage.success('标定已触发；完成后「自动档」将按本机实测结果取值')
  } finally {
    calibrating.value = false
  }
}

async function toggleSimulation(next: boolean) {
  if (next) {
    await ElMessageBox.confirm(
      '档位模拟会强制声明一个高于/低于本机的硬件档位，用于验证高/低档位的界面与参数路径。' +
        '模拟期间**执行结果不代表真实性能**，仅用于验证代码分支。确认开启？',
      '开启档位模拟',
      { confirmButtonText: '确认开启', cancelButtonText: '取消', type: 'warning' },
    )
    system.setSimulation(true, simForceTier.value)
    ElMessage.warning(`已进入档位模拟：强制 ${simForceTier.value}（界面已标注「模拟」）`)
  } else {
    system.setSimulation(false, null)
    ElMessage.success('已退出档位模拟，恢复真实探测值')
  }
}

function changeForceTier(t: string) {
  simForceTier.value = t as 'T1' | 'T2' | 'T3'
  if (system.simulating) {
    system.setSimulation(true, t)
    ElMessage.info(`模拟档位已切换为 ${t}`)
  }
}

onMounted(() => {
  if (!system.capabilities) system.load()
})
</script>

<template>
  <div class="hw-page" :style="{ maxWidth: PAGE_MAX_WIDTH.hardware + 'px' }">
    <PageHeader title="硬件能力" subtitle="本机硬件事实、执行提供器验证证据与档位模拟">
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
        <SettingsRow label="实际生效后端" :value="caps.active_backend" mono tone="success" />
        <SettingsRow label="实际生效精度" :value="caps.active_precision" mono />
        <SettingsRow
          label="参数来源"
          :value="caps.using_fallback ? '保底档（保守下界参数）' : '首启自标定实测值'"
          :tone="caps.using_fallback ? 'warning' : 'success'"
        />
        <SettingsRow label="当前路径判据" :value="activeReason" />
      </SectionCard>

      <!-- 设备事实 -->
      <SectionCard v-if="facts" title="设备信息" subtitle="显存取实读可用值，非标称值">
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

      <!-- EP 证据 -->
      <SectionCard title="执行提供器验证证据">
        <template #actions>
          <el-tooltip
            content="判据：目标 EP 节点数 > 0 且 CPU 节点数 = 0。节点数为 1 属整图融合的正常现象，不能只凭节点数判断。"
            placement="top"
          >
            <el-icon :size="14" class="hw-flag"><InfoFilled /></el-icon>
          </el-tooltip>
        </template>

        <table class="hw-table">
          <thead>
            <tr>
              <th>执行提供器</th>
              <th class="hw-num">节点数</th>
              <th class="hw-num">CPU 节点</th>
              <th>判定</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in evidence" :key="e.provider">
              <td>
                <div class="hw-prov">
                  <span class="hw-mono">{{ e.provider }}</span>
                  <span class="hw-prov-note">{{ e.node_ownership }}</span>
                </div>
              </td>
              <td class="hw-num hw-mono">{{ e.node_count }}</td>
              <td class="hw-num hw-mono">{{ e.cpu_node_count }}</td>
              <td>
                <span class="hw-verdict" :class="e.verified ? 'is-ok' : 'is-idle'">
                  {{ e.verified ? '已生效' : '未参与' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!hasVerifiedGpu" class="hw-warn">
          <el-icon :size="12"><Warning /></el-icon>
          尚未检测到「CPU 节点数为 0」的 GPU 后端，当前实际走 CPU 路径。
        </p>
        <ul class="hw-notes">
          <li v-for="(e, i) in evidence" :key="i">{{ e.note }}</li>
        </ul>
      </SectionCard>

      <!-- 可用后端 -->
      <SectionCard title="可用后端" subtitle="按能力声明给出，不按设备型号硬编码">
        <div class="hw-be-list">
          <div v-for="b in backends" :key="b.id" class="hw-be" :class="{ 'is-idle': !b.verified }">
            <div class="hw-be-head">
              <span class="hw-be-id hw-mono">{{ b.label }}</span>
              <span class="hw-verdict" :class="b.verified ? 'is-ok' : 'is-idle'">
                {{ b.verified ? '可用' : '不可用' }}
              </span>
            </div>
            <div class="hw-be-meta hw-mono">
              精度 {{ b.precision }} · 节点 {{ b.node_count }} · CPU 节点 {{ b.cpu_node_count }}
            </div>
          </div>
        </div>
      </SectionCard>

      <!-- 档位模拟 -->
      <SectionCard title="档位模拟" subtitle="开发期能力 · P3">
        <div class="hw-sim-warn">
          <el-icon :size="13"><Warning /></el-icon>
          <div>
            <p class="hw-sim-msg">模拟档位不做真实硬件校验，用于在低档位机器上验证高/低档位的代码分支与界面表现。</p>
            <p class="hw-sim-sub">开启后所有界面会标注「模拟」，执行结果不代表真实性能，请勿用于评估硬件。</p>
          </div>
        </div>

        <SettingsRow label="模拟开关" :hint="system.simulating ? '当前处于模拟态' : '当前为真实探测值'">
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

        <SettingsRow label="目标档位" hint="T0 纯 CPU / T1 消费级 8G / T2 高端 16–24G / T3 专业卡">
          <SegmentedControl
            :model-value="simForceTier"
            :options="[
              { label: 'T1', value: 'T1' },
              { label: 'T2', value: 'T2' },
              { label: 'T3', value: 'T3' },
            ]"
            size="sm"
            @update:model-value="changeForceTier"
          />
        </SettingsRow>

        <SettingsRow
          v-if="system.simulating"
          label="当前声明"
          :value="system.tierBadgeText"
          tone="warning"
          simulated
        />
      </SectionCard>
    </template>
  </div>
</template>

<style scoped>
.hw-page {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 24px 28px 40px;
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
