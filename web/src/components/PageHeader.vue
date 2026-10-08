<script setup lang="ts">
/**
 * PageHeader —— 独立页页头：标题 + 副标题 + 右侧操作槽，复用于 P2 / P3 / P4 / P5（03 §2-2）。
 */
defineProps<{
  title: string
  subtitle?: string
  /** 副标题中的关键数值用等宽字体 */
  monoSubtitle?: string
}>()
</script>

<template>
  <header class="page-header">
    <div class="page-header__text">
      <h1 class="page-header__title">{{ title }}</h1>
      <p class="page-header__subtitle">
        <slot name="subtitle">{{ subtitle }}</slot>
        <span v-if="monoSubtitle" class="page-header__mono">{{ monoSubtitle }}</span>
      </p>
    </div>
    <div class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--Scale-space-5);
  /* 独立页左右 32（舒展），见 02 §4 */
  padding: var(--Scale-space-6) var(--Scale-space-6) var(--Scale-space-5);
}

.page-header__title {
  margin: 0;
  font-size: var(--font-size-20);
  font-weight: 600;
  color: var(--Theme-text-primary);
  line-height: var(--line-height-tight);
}

.page-header__subtitle {
  margin: var(--Scale-space-1) 0 0;
  font-size: var(--font-size-13);
  color: var(--Theme-text-tertiary);
  line-height: var(--line-height-body);
}

.page-header__mono {
  margin-left: var(--Scale-space-2);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: var(--font-size-12);
  color: var(--Theme-text-secondary);
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--Scale-space-2);
  flex: 0 0 auto;
}

@media (max-width: 1023px) {
  .page-header {
    padding: var(--Scale-space-5) var(--Scale-space-4) var(--Scale-space-4);
    flex-direction: column;
    gap: var(--Scale-space-3);
  }
}
</style>
