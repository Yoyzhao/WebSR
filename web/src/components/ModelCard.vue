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

const props = defineProps<{
  model: Model
}>()

const emit = defineEmits<{
  (e: 'use', id: string): void
  (e: 'delete', id: string): void
}>()

const vm = computed(() => toModelVM(props.model))

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

    <p v-if="vm.description" class="mc-desc">{{ vm.description }}</p>

    <div class="mc-facts">
      <div class="mc-fact">
        <span class="mc-fact-k">最低显存</span>
        <span class="mc-fact-v mc-mono">{{ vm.minVramText }}</span>
      </div>
      <div class="mc-fact">
        <span class="mc-fact-k">文件大小</span>
        <span class="mc-fact-v mc-mono">{{ vm.fileSizeText }}</span>
      </div>
      <div class="mc-fact">
        <span class="mc-fact-k">内置步数</span>
        <span class="mc-fact-v mc-mono">{{ vm.stepsText }}</span>
      </div>
    </div>

    <div class="mc-backends">
      <span v-for="b in vm.backends" :key="b" class="mc-be" :class="{ 'is-conditional': b === 'ncnn' }">
        {{ b }}
        <el-tooltip
          v-if="b === 'ncnn'"
          content="条件支持：Windows 与 Python 环境下的可用性尚未验证"
          placement="top"
        >
          <span class="mc-be-flag">?</span>
        </el-tooltip>
      </span>
    </div>

    <footer class="mc-foot">
      <span v-if="vm.disabled" class="mc-reason">{{ vm.disabledReason }}</span>
      <button v-else type="button" class="mc-use" @click="onUse">使用此模型</button>
    </footer>
  </article>
</template>

<style scoped>
.model-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: var(--Theme-bg-panel);
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-card);
  transition: border-color 0.15s ease;
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

.mc-title {
  font-size: var(--font-size-14);
  line-height: 20px;
  font-weight: 600;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mc-scale {
  flex: 0 0 auto;
  font-size: var(--font-size-12);
  line-height: 16px;
  padding: 1px 6px;
  border-radius: var(--Scale-radius-button);
  background: color-mix(in srgb, var(--Theme-primary) 12%, transparent);
  color: var(--Theme-primary);
}

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

.mc-desc {
  margin: 0;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.mc-facts {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mc-fact {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--font-size-13);
  line-height: 18px;
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

.mc-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
