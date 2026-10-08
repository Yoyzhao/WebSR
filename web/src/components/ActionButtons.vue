<script setup lang="ts">
/**
 * ActionButtons —— 任务/资源行内操作按钮组（5A 定稿 · 15 组件之一）
 *
 * 用途：任务卡与详情抽屉底部的操作区（取消 / 重试 / 对比 / 下载 / 删除）。
 * 约束（04 §5 危险操作保护）：
 *   - 危险操作（删除）必须二次确认 —— 由父级传入 `danger: true` 并用 ElMessageBox 包裹，
 *     组件本身只负责按钮外观与 disabled 门控。
 *   - 「对比」在无 `output` 产出时禁用；「下载」同理。
 *   - 禁用态 transparency 不得低于 0.55（01 §5）。
 */
export interface ActionItem {
  key: string
  label: string
  type?: 'primary' | 'default' | 'danger'
  disabled?: boolean
  disabledReason?: string
}

defineProps<{
  actions: ActionItem[]
  size?: 'sm' | 'md'
}>()

const emit = defineEmits<{ (e: 'action', key: string): void }>()
</script>

<template>
  <div class="action-buttons" :class="`is-${size ?? 'md'}`">
    <button
      v-for="a in actions"
      :key="a.key"
      type="button"
      class="act"
      :class="[`act-${a.type ?? 'default'}`, { 'is-disabled': a.disabled }]"
      :disabled="a.disabled"
      :title="a.disabled ? a.disabledReason : undefined"
      @click="emit('action', a.key)"
    >
      <slot :name="`icon-${a.key}`" />
      <span>{{ a.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.action-buttons {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.act {
  appearance: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  padding: 4px 12px;
  border-radius: var(--Scale-radius-button);
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    color 0.15s ease;
  white-space: nowrap;
}

.is-sm .act {
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 2px 10px;
}

.act:hover:not(.is-disabled) {
  background: var(--Theme-bg-hover);
  color: var(--Theme-text-primary);
}

.act-primary {
  border-color: var(--Theme-primary);
  color: var(--Theme-primary);
}

.act-primary:hover:not(.is-disabled) {
  background: color-mix(in srgb, var(--Theme-primary) 12%, transparent);
  color: var(--Theme-primary);
}

.act-danger {
  color: var(--Theme-error);
}

.act-danger:hover:not(.is-disabled) {
  border-color: var(--Theme-error);
  background: color-mix(in srgb, var(--Theme-error) 10%, transparent);
  color: var(--Theme-error);
}

.act.is-disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.act:focus-visible {
  outline: 2px solid var(--Theme-primary);
  outline-offset: 1px;
}
</style>
