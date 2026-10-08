<script setup lang="ts">
/**
 * ParamDiffRow —— 参数差异行（任务详情抽屉「参数与解析」区段）（5A 定稿 · 15 组件之一）
 *
 * 用途：并排展示「用户请求值」与「引擎实际解析值」，差异项高亮。
 * 约束（产品核心铁律）：
 *   - 「自动档」的唯一可验证输出是 `resolved` —— 因此该组件是"自动档是否生效"的唯一可视化证据。
 *   - 差异不等于错误：tile/精度/EP 被引擎改写是设计行为，用 warning 色标注并附原因，
 *     不能用 error 色（避免误判为失败）。
 *   - 保底档时 `usingFallback` 为真，该行标注「保底档」而非「自动」。
 */
import { computed } from 'vue'

const props = defineProps<{
  label: string
  requested: string | number | null | undefined
  resolved: string | number | null | undefined
  /** 该项是否来自保底档 */
  usingFallback?: boolean
  /** 差异原因（如 "OOM 降档" / "CPU 路径禁 fp16"） */
  reason?: string
  mono?: boolean
}>()

const reqText = computed(() => (props.requested === null || props.requested === undefined || props.requested === '' ? '自动' : String(props.requested)))
const resText = computed(() => (props.resolved === null || props.resolved === undefined || props.resolved === '' ? '—' : String(props.resolved)))

const isAuto = computed(() => props.requested === null || props.requested === undefined || props.requested === '')

/** 差异判定：请求为「自动」且有解析值 → 不算异常差异，用中性标记 */
const isDiff = computed(() => !isAuto.value && reqText.value !== resText.value)
</script>

<template>
  <div class="pd-row" :class="{ 'is-diff': isDiff, 'is-auto': isAuto }">
    <span class="pd-label">{{ label }}</span>

    <div class="pd-values">
      <span class="pd-value pd-req" :class="{ 'is-auto-tag': isAuto, 'pd-mono': mono }">
        {{ reqText }}
      </span>
      <span class="pd-arrow" aria-hidden="true">→</span>
      <span class="pd-value pd-res" :class="{ 'pd-mono': mono }">{{ resText }}</span>
      <span v-if="usingFallback" class="pd-badge pd-badge-fallback">保底档</span>
      <span v-else-if="isAuto && resolved !== undefined && resolved !== null" class="pd-badge pd-badge-auto">引擎解析</span>
    </div>

    <span v-if="reason" class="pd-reason">{{ reason }}</span>
  </div>
</template>

<style scoped>
.pd-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  align-items: baseline;
  gap: 4px 12px;
  padding: 6px 0;
}

.pd-row + .pd-row {
  border-top: 1px solid var(--Theme-border-subtle);
}

.pd-label {
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-tertiary);
}

.pd-values {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.pd-value {
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-primary);
}

.pd-req.is-auto-tag {
  color: var(--Theme-text-tertiary);
  font-style: italic;
}

.pd-res {
  font-weight: 500;
}

.is-diff .pd-res {
  color: var(--Theme-warning);
}

.pd-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.pd-arrow {
  font-size: var(--font-size-12);
  color: var(--Theme-text-tertiary);
  opacity: 0.7;
}

.pd-badge {
  font-size: var(--font-size-11);
  line-height: 15px;
  padding: 0 5px;
  border-radius: var(--Scale-radius-button);
  white-space: nowrap;
}

.pd-badge-auto {
  color: var(--Theme-link);
  background: color-mix(in srgb, var(--Theme-link) 12%, transparent);
}

.pd-badge-fallback {
  color: var(--Theme-warning);
  background: color-mix(in srgb, var(--Theme-warning) 14%, transparent);
}

.pd-reason {
  grid-column: 2;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

@media (max-width: 1023px) {
  .pd-row {
    grid-template-columns: 72px minmax(0, 1fr);
  }
}
</style>
