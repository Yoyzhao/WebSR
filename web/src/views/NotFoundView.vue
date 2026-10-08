<script setup lang="ts">
/**
 * 兜底页（404）—— 路由未命中时的空态。
 *
 * 约束：**绝不白屏**（04 §2.3）。任何未知路径都要给出可回到主流程的出口。
 */
import { useRouter } from 'vue-router'
import EmptyState from '@/components/EmptyState.vue'

const router = useRouter()
</script>

<template>
  <div class="nf-page">
    <EmptyState
      icon-size="48"
      title="页面不存在"
      description="你访问的地址没有对应的页面，可能是链接已失效或输入有误"
    >
      <template #action>
        <button type="button" class="nf-btn" @click="router.push('/')">返回工作台</button>
      </template>
    </EmptyState>
  </div>
</template>

<style scoped>
.nf-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: 40px 24px;
}

.nf-btn {
  appearance: none;
  border: 1px solid var(--Theme-primary);
  background: transparent;
  color: var(--Theme-primary);
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  padding: 6px 18px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.nf-btn:hover {
  background: color-mix(in srgb, var(--Theme-primary) 12%, transparent);
}
</style>
