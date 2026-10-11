<script setup lang="ts">
/**
 * ModelCard —— 模型卡片（模型库）（5A 定稿 · 15 组件之一）
 *
 * 约束（PRD §7.2 / v1.8 模型不做自动推荐）：
 *   - 模型**始终由用户选择**，卡片不展示"推荐"字样，不做排序干预。
 *   - 跑不动的模型按「当前档位」置灰并**说明原因**——判据**只**取服务端 `ModelOut.available`
 *     与 `unavailable_reason`（契约 §3.2：服务端已按「登记态 → 运行时 → 显存门槛」三级裁定）。
 *     前端**不得**再用真实可用显存二次判断，否则档位模拟（T-901）时会把模型错误置灰。
 *   - 置灰 = `disabled`，且必须给出 `disabledReason`；禁用态透明度 0.55（01 §5）。
 *   - `supported_backends` 含 ncnn 时按「条件支持」表述（Windows/Python 可用性未验证，T-210）。
 */
import { computed } from 'vue'
import { ElIcon, ElTooltip } from 'element-plus'
import { Box, Delete } from '@element-plus/icons-vue'
import type { Model } from '@/types/api'
import { toModelVM } from '@/utils/viewModel'
import { BACKEND_EXPERIMENTAL, backendLabel } from '@/constants'

const props = defineProps<{
  model: Model
  /** 该模型的转换作业是否正在进行（由父级轮询维护） */
  converting?: boolean
}>()

const emit = defineEmits<{
  (e: 'use', id: string): void
  (e: 'delete', id: string): void
  (e: 'convert', id: string): void
}>()

const vm = computed(() => toModelVM(props.model))

/**
 * 「转换为 ONNX」入口（T-719）：`.pth` / `.safetensors` 是训练权重，推理引擎用不了，
 * 必须转一次。此前只有**下载**路径会自动触发转换，手动导入的权重会永远停在
 * 「待转换」且没有任何可做的动作 —— 卡片上直接给出按钮，转换环境未安装时只说明原因。
 */
const needsConvert = computed(() => props.model.status === 'needs_convert')
const canConvert = computed(() => needsConvert.value && props.model.conversion?.available === true)

function onUse() {
  if (vm.value.disabled) return
  emit('use', vm.value.id)
}
</script>

<template>
  <article class="model-card" :class="{ 'is-disabled': vm.disabled }">
    <header class="mc-head">
      <div class="mc-icon">
        <el-icon :size="18"><Box /></el-icon>
      </div>
      <div class="mc-title-wrap">
        <div class="mc-title-row">
          <span class="mc-title" :title="vm.name">{{ vm.name }}</span>
          <span v-if="vm.scaleText" class="mc-scale mc-mono">{{ vm.scaleText }}</span>
        </div>
        <div class="mc-sub">
          <span>{{ vm.architecture }}</span>
          <template v-if="vm.format">
            <span class="mc-dot">·</span>
            <span class="mc-mono">{{ vm.format }}</span>
          </template>
        </div>
      </div>
      <button v-if="vm.removable" type="button" class="mc-del" title="删除模型" @click="emit('delete', vm.id)">
        <el-icon :size="14"><Delete /></el-icon>
      </button>
    </header>

    <!-- 适配简介拆两级呈现（2026-10-11）：
         **定位句**回答「这是什么」，**补充**回答「什么时候用」。二者字重/字号/颜色
         明显不同，用户扫一眼卡片顶部即可完成选型，不必读完整段文字。 -->
    <p v-if="vm.descLead" class="mc-lead">{{ vm.descLead }}</p>
    <p v-if="vm.descDetail" class="mc-detail" :title="vm.description">
      {{ vm.descDetail }}
    </p>

    <!-- 资源事实压成**一行**内联元信息：此前 3 行「标签 … 值」占了近 1/3 卡高，
         却只有「最低显存」真正影响选型（其余是体积/步数，低频信息）。
         保留三个数值但降为单行次要层，腾出的高度让位给名称与定位。 -->
    <div class="mc-facts">
      <span class="mc-fact">
        <span class="mc-fact-k">最低显存</span>
        <span class="mc-fact-v mc-mono">{{ vm.minVramText }}</span>
      </span>
      <span class="mc-fact">
        <span class="mc-fact-k">体积</span>
        <span class="mc-fact-v mc-mono">{{ vm.fileSizeText }}</span>
      </span>
      <span class="mc-fact">
        <span class="mc-fact-k">步数</span>
        <span class="mc-fact-v mc-mono">{{ vm.stepsText }}</span>
      </span>
    </div>

    <div class="mc-backends">
      <span
        v-for="b in vm.backends"
        :key="b"
        class="mc-be"
        :class="{ 'is-conditional': BACKEND_EXPERIMENTAL.has(b) }"
      >
        {{ backendLabel(b) }}
        <el-tooltip
          v-if="BACKEND_EXPERIMENTAL.has(b)"
          content="实验性支持：部分运行环境下的兼容性仍在验证，个别机器上可能无法运行"
          placement="top"
        >
          <span class="mc-be-flag">?</span>
        </el-tooltip>
      </span>
    </div>

    <footer class="mc-foot">
      <template v-if="canConvert">
        <button
          type="button"
          class="mc-convert"
          :disabled="converting"
          :title="converting ? '转换进行中，完成后会自动刷新' : '把该模型转换为通用格式，转换完成后即可使用'"
          @click="emit('convert', vm.id)"
        >
          {{ converting ? '转换中…' : '转换格式' }}
        </button>
      </template>
      <template v-else>
        <span v-if="vm.disabled" class="mc-reason">{{ vm.disabledReason }}</span>
        <button v-else type="button" class="mc-use" @click="onUse">使用此模型</button>
      </template>
    </footer>
  </article>
</template>

<style scoped>
.model-card {
  display: flex;
  flex-direction: column;
  gap: var(--Scale-space-3);
  padding: var(--Scale-space-4);
  /* 顶部微光：与 SectionCard 同一套材质语言（渐变而非投影，见 03 §5-6） */
  background-color: var(--Theme-bg-panel);
  background-image: var(--Theme-sheen-panel);
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-card);
  transition:
    border-color var(--motion-base) var(--motion-ease),
    background-color var(--motion-base) var(--motion-ease);
}

.model-card:hover:not(.is-disabled) {
  border-color: var(--Theme-border-strong);
}

.model-card.is-disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.mc-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.mc-icon {
  flex: 0 0 32px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--Scale-radius-inner);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-secondary);
}

.mc-title-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mc-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

/* ⚠️ 类名 `.mc-title` 被 UI E2E 的「使用此模型深链」断言用作取名选择器
   （`.model-card .mc-title`），**改名会打断该断言** —— 勿动。 */
.mc-title {
  font-size: var(--font-size-17);
  line-height: 24px;
  font-weight: 600;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mc-scale {
  flex: 0 0 auto;
  font-size: var(--font-size-13);
  line-height: 18px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--Scale-radius-button);
  background: color-mix(in srgb, var(--Theme-primary) 14%, transparent);
  color: var(--Theme-primary);
}

/* 架构 / 格式：纯技术元信息，最次要层 */
.mc-sub {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.mc-dot {
  opacity: 0.6;
}

.mc-del {
  flex: 0 0 auto;
  appearance: none;
  border: 1px solid transparent;
  background: transparent;
  color: var(--Theme-text-tertiary);
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.mc-del:hover {
  color: var(--Theme-error);
  background: color-mix(in srgb, var(--Theme-error) 10%, transparent);
}

/* ---- 简介两级：定位（主） / 补充（次） ----
 * 2026-10-11 重构。此前整段简介是同字号同色的 3 行文字块，与名称争夺注意力。
 * 现在层级为 **名称 17px > 定位 14px 主色 > 补充 13px 三级色**：
 * 定位句是选型时唯一必须读到的一句，给它主色与更高行高；补充退居灰度且可截断。 */
.mc-lead {
  margin: 0;
  font-size: var(--font-size-14);
  line-height: 20px;
  font-weight: 500;
  color: var(--Theme-text-primary);
}

.mc-detail {
  margin: 0;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-tertiary);
  /* 补充段截 2 行（原 3 行）：全文可悬浮查看，卡面不承载长文 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 资源事实：单行内联，三项依次排列 */
.mc-facts {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px var(--Scale-space-2);
  padding-top: var(--Scale-space-1);
  border-top: 1px solid var(--Theme-border-subtle);
}

.mc-fact {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: var(--font-size-12);
  line-height: 17px;
}

/* 项与项之间加分隔点（用 ::before 挂在后两项上，首项无） */
.mc-fact + .mc-fact::before {
  content: '·';
  margin-right: var(--Scale-space-2);
  color: var(--Theme-border-strong);
}

.mc-fact-k {
  color: var(--Theme-text-tertiary);
}

.mc-fact-v {
  color: var(--Theme-text-secondary);
}

.mc-backends {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.mc-be {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: var(--font-size-12);
  line-height: 16px;
  font-family: var(--font-mono);
  padding: 1px 6px;
  border-radius: var(--Scale-radius-button);
  border: 1px solid var(--Theme-border-subtle);
  color: var(--Theme-text-secondary);
  background: var(--Theme-bg-elevated);
}

.mc-be.is-conditional {
  border-style: dashed;
  color: var(--Theme-warning);
  border-color: color-mix(in srgb, var(--Theme-warning) 50%, transparent);
}

.mc-be-flag {
  cursor: help;
  opacity: 0.8;
}

.mc-foot {
  display: flex;
  align-items: center;
  min-height: 28px;
}

.mc-reason {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.mc-use {
  appearance: none;
  width: 100%;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 12px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.mc-use:hover {
  border-color: var(--Theme-primary);
  color: var(--Theme-primary);
  background: color-mix(in srgb, var(--Theme-primary) 10%, transparent);
}

/* 「转换为 ONNX」是**当前唯一可做的动作**，用主色实心强调，区别于次要的「使用此模型」 */
.mc-convert {
  appearance: none;
  width: 100%;
  border: 1px solid var(--Theme-primary);
  background: var(--Theme-primary);
  color: #fff;
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 12px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
  transition: opacity 0.15s ease, background-color 0.15s ease;
}

.mc-convert:hover {
  opacity: 0.9;
}

.mc-convert:disabled {
  opacity: 0.55;
  cursor: progress;
}

.mc-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
