<script setup lang="ts">
/**
 * EmptyState —— 空态（图标 + 主文案 + 副文案 + 可选操作），复用于 P1 左栏 / P2 / P3（03 §2-11）。
 *
 * **空态不是"什么都不放"**（04 §2.1）：每个空态都要回答一个具体问题 ——
 * "结果会出现在哪""能做什么""为什么现在不能用"。
 */
withDefaults(
  defineProps<{
    title: string
    description?: string
    /** 图标尺寸档位：32（列表）/ 48（拖拽区）（02 §7）。
     *  收 string 是为了兼容在模板里写 icon-size="48" 的写法。 */
    iconSize?: 32 | 48 | '32' | '48'
  }>(),
  { iconSize: 32 },
)
</script>

<template>
  <div class="empty-state">
    <div class="empty-state__icon" :style="{ width: `${iconSize}px`, height: `${iconSize}px` }">
      <slot name="icon">
        <svg viewBox="0 0 24 24" fill="none" :stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <path d="M21 15l-5-5L5 21" />
        </svg>
      </slot>
    </div>
    <p class="empty-state__title" :class="{ 'is-lg': Number(iconSize) === 48 }">{{ title }}</p>
    <p v-if="description || $slots.description" class="empty-state__desc">
      <slot name="description">{{ description }}</slot>
    </p>
    <div v-if="$slots.action" class="empty-state__action">
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: var(--Scale-space-2);
  padding: var(--Scale-space-5) var(--Scale-space-4);
  min-height: 120px;
}

.empty-state__icon {
  color: var(--icon-color);
  margin-bottom: var(--Scale-space-1);
}
.empty-state__icon :deep(svg) {
  width: 100%;
  height: 100%;
  stroke: currentColor;
}

.empty-state__title {
  margin: 0;
  font-size: var(--font-size-13);
  color: var(--Theme-text-secondary);
  line-height: var(--line-height-body);
}
.empty-state__title.is-lg {
  font-size: var(--font-size-16);
  font-weight: 500;
  color: var(--Theme-text-primary);
}

.empty-state__desc {
  margin: 0;
  font-size: var(--font-size-12);
  color: var(--Theme-text-tertiary);
  line-height: var(--line-height-relaxed);
  max-width: 320px;
}

.empty-state__action {
  margin-top: var(--Scale-space-2);
}
</style>
