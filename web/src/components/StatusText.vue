<script setup lang="ts">
/**
 * StatusText —— 6 态 → 颜色 + 字重 的语义映射（03 §2-4）。
 *
 * ⚠️ 两条不可违反的约束：
 *   1. **未知枚举值的兜底策略是显示原值 + text-tertiary**，绝不抛错或留空（04 §2.6）；
 *   2. **降级完成 ≠ 成功**：有降档的 done 用 warning 而非 success（project-rules §2.2-5）。
 */
import { computed } from 'vue'
import { STATUS_LABEL, STATUS_TONE } from '@/constants'
import type { Task } from '@/types/api'

const props = withDefaults(
  defineProps<{
    task?: Pick<Task, 'status' | 'resolved'>
    status?: string
    /** 由父级传入的降级标记（TaskVM 已算好，优先采用） */
    degraded?: boolean
    /** 仅状态文字，不显示降级后缀 */
    plain?: boolean
  }>(),
  { plain: false, degraded: undefined },
)

const rawStatus = computed(() => props.status ?? props.task?.status ?? '')

const tone = computed(() => STATUS_TONE[rawStatus.value as keyof typeof STATUS_TONE] ?? 'neutral')

const label = computed(() => {
  const known = STATUS_LABEL[rawStatus.value]
  if (known) return known
  // 未知枚举兜底：显示原值，绝不白屏（04 §2.6）
  return rawStatus.value || '未知状态'
})

/** 有降档的"已完成"必须显示为「降级完成」（01 §4.2） */
const degraded = computed(() => {
  if (props.degraded !== undefined) return props.degraded && rawStatus.value === 'completed'
  return props.task?.resolved?.degraded === true && rawStatus.value === 'completed'
})

const text = computed(() => {
  if (!props.plain && degraded.value) return '降级完成'
  return label.value
})

const finalTone = computed(() => (degraded.value && !props.plain ? 'warning' : tone.value))
</script>

<template>
  <span class="status-text" :class="[`tone-${finalTone}`, { 'is-medium': finalTone === 'primary' }]">
    {{ text }}
  </span>
</template>

<style scoped>
.status-text {
  font-size: var(--font-size-13);
  line-height: 18px;
  white-space: nowrap;
}
/* 进行中与其他状态的字重区分：状态不只靠颜色传达 */
.status-text.is-medium {
  font-weight: 500;
}
.tone-primary {
  color: var(--Theme-link);
}
.tone-success {
  color: var(--Theme-success);
}
.tone-warning {
  color: var(--Theme-warning);
}
.tone-error {
  color: var(--Theme-error);
}
.tone-neutral {
  color: var(--Theme-text-tertiary);
}
</style>
