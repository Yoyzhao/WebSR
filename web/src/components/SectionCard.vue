<script setup lang="ts">
/**
 * SectionCard —— 分组卡容器（8 圆角 + 1px 描边 + 20 内边距 + 标题），复用于 P4 / P5（03 §2-5）。
 *
 * ⚠️ **常规卡片不加阴影**：靠 bg 层级 + 1px 描边区分。深色界面上阴影几乎不可见，
 *    滥用只增加渲染成本（02 §5、03 §5-6）。
 */
defineProps<{
  title?: string
  /** 标题右侧的一句话说明 */
  subtitle?: string
  /** 卡内是否使用 elevated 底色（次级容器，无描边） */
  elevated?: boolean
  /** 无内边距（用于预览区等需要内容贴边的场景） */
  noPadding?: boolean
}>()
</script>

<template>
  <section class="section-card" :class="{ 'is-elevated': elevated, 'is-flush': noPadding }">
    <header v-if="title" class="section-card__head">
      <h3 class="section-card__title">{{ title }}</h3>
      <span v-if="subtitle" class="section-card__subtitle">{{ subtitle }}</span>
      <div class="section-card__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="section-card__body">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="section-card__foot">
      <slot name="footer" />
    </footer>
  </section>
</template>

<style scoped>
.section-card {
  background: var(--Theme-bg-panel);
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-card);
  padding: var(--Scale-space-5);
}

/* 内容贴边：预览图需要占满卡片 */
.section-card.is-flush {
  padding: 0;
  overflow: hidden;
}

.section-card.is-flush .section-card__head {
  padding: var(--Scale-space-5) var(--Scale-space-5) 0;
}

.section-card.is-flush .section-card__body {
  padding: var(--Scale-space-5);
}

.section-card.is-flush .section-card__foot {
  padding: 0 var(--Scale-space-5) var(--Scale-space-5);
}

/* 卡片内的次级容器：抬升面，无描边 */
.section-card.is-elevated {
  background: var(--Theme-bg-elevated);
  border-color: transparent;
}

.section-card__head {
  display: flex;
  align-items: center;
  gap: var(--Scale-space-3);
  margin-bottom: var(--Scale-space-4);
}

.section-card__title {
  margin: 0;
  font-size: var(--font-size-14);
  font-weight: 600;
  color: var(--Theme-text-primary);
  line-height: var(--line-height-tight);
}

/* 卡体作为容器查询单元：SettingsRow 依据卡体实际宽度决定行内对齐方式
 * （宽卡片 > 900px 时改为左对齐网格，避免 label 与值相隔过远）。
 * 仅声明 inline-size，宽度仍由卡体父级决定，对布局无副作用。 */
.section-card__body {
  container: sc-body / inline-size;
}

.section-card__subtitle {
  font-size: var(--font-size-12);
  color: var(--Theme-text-tertiary);
}

.section-card__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--Scale-space-2);
}

.section-card__foot {
  margin-top: var(--Scale-space-3);
  padding-top: var(--Scale-space-3);
  border-top: 1px solid var(--Theme-border-subtle);
}
</style>
