<script setup lang="ts">
/**
 * SegmentedControl —— 分段控件（5A 定稿 · 15 组件之一）
 *
 * 用途：主题模式切换 / 任务状态筛选 / 对比模式等「少量互斥选项」场景。
 * 约束（03 §3）：
 *   - 不使用 el-radio-group 的按钮样式（边框与选中态无法完全对齐设计稿）
 *   - 选中态：surface-2 底 + primary 文字 + 1px primary 描边
 *   - 键盘：原生 button 天然支持 Tab / Enter / Space，role="tablist" 语义
 */
interface Option {
  label: string
  value: string
  disabled?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: Option[]
    size?: 'sm' | 'md'
  }>(),
  { size: 'md' },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

function select(opt: Option) {
  if (opt.disabled || opt.value === props.modelValue) return
  emit('update:modelValue', opt.value)
}
</script>

<template>
  <div class="segmented" :class="`is-${size}`" role="tablist">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      class="seg-item"
      :class="{ 'is-active': opt.value === modelValue, 'is-disabled': opt.disabled }"
      role="tab"
      :aria-selected="opt.value === modelValue"
      :disabled="opt.disabled"
      @click="select(opt)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped>
.segmented {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  gap: 2px;
  background: var(--Theme-bg-elevated);
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-inner);
}

.seg-item {
  appearance: none;
  border: 1px solid transparent;
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  padding: 4px 12px;
  border-radius: calc(var(--Scale-radius-inner) - 2px);
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    color 0.15s ease,
    border-color 0.15s ease;
  white-space: nowrap;
}

.is-sm .seg-item {
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 2px 10px;
}

.seg-item:hover:not(.is-active):not(.is-disabled) {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.seg-item.is-active {
  background: var(--Theme-bg-panel);
  border-color: var(--Theme-primary);
  color: var(--Theme-primary);
  font-weight: 500;
}

.seg-item.is-disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.seg-item:focus-visible {
  outline: 2px solid var(--Theme-primary);
  outline-offset: 1px;
}
</style>
