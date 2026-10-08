<script setup lang="ts">
/**
 * FallbackNotice —— 保底档提示条，复用于 P1 右栏 / P6 空态 / P5 配置（03 §2-9）。
 *
 * ⚠️ 该提示**不可关闭**（`el-alert` 的 closable 必须关闭）：保底档是**持续状态**，
 *    关掉后用户就不知道自己跑的是保守参数了 —— 违背"降级必须显式"（project-rules §2.2-5）。
 */
withDefaults(
  defineProps<{
    visible: boolean
    /** 自定义文案；默认用标准保底档文案 */
    text?: string
  }>(),
  { text: '当前为保底档 · 标定完成后自动切换实测参数' },
)
</script>

<template>
  <Transition name="fade">
    <div v-if="visible" class="fallback-notice" role="status">
      <svg class="fallback-notice__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
        <path d="M12 9v4M12 17h.01" />
        <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
      <span class="fallback-notice__text">{{ text }}</span>
      <slot />
    </div>
  </Transition>
</template>

<style scoped>
.fallback-notice {
  display: flex;
  align-items: flex-start;
  gap: var(--Scale-space-2);
  padding: var(--Scale-space-2) var(--Scale-space-3);
  border-radius: var(--Scale-radius-inner);
  background: color-mix(in srgb, var(--Theme-warning) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--Theme-warning) 32%, transparent);
  color: var(--Theme-warning);
  font-size: var(--font-size-12);
  line-height: var(--line-height-body);
}

.fallback-notice__icon {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
  margin-top: 1px;
}

.fallback-notice__text {
  flex: 1 1 auto;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 200ms ease-out;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
