<script setup lang="ts">
/**
 * SettingsRow —— key-value 行（SPACE_BETWEEN），复用于 P4 / P5（03 §2-3）。
 *
 * 这是本项目的高频结构：硬件事实、后端信息、系统配置三处共用。
 * 各页各写一套会导致行高、字号、对齐漂移 —— 违反 03 §5-2。
 *
 * 窄屏（< 1024px）由父级控制堆叠：label 在上、控件在下（05 §4.2 P5）。
 */
defineProps<{
  label: string
  /** 纯展示值；与默认插槽二选一 */
  value?: string
  /** 值下方的一行辅助说明 */
  hint?: string
  /** 值用等宽字体（技术数值必须等宽，否则刷新时左右跳动，02 §3） */
  mono?: boolean
  /** 值的语义色 */
  tone?: 'default' | 'warning' | 'success' | 'error' | 'tertiary'
  /** 标记为模拟态（warning 底），用于档位模拟（01 §7.1） */
  simulated?: boolean
}>()
</script>

<template>
  <div class="settings-row">
    <div class="settings-row__label">
      {{ label }}
      <span v-if="simulated" class="settings-row__sim">模拟中</span>
    </div>
    <div class="settings-row__value" :class="[`tone-${tone ?? 'default'}`, { 'is-mono': mono }]">
      <slot>{{ value }}</slot>
      <div v-if="hint" class="settings-row__hint">{{ hint }}</div>
    </div>
  </div>
</template>

<style scoped>
.settings-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--Scale-space-4);
  min-height: var(--layout-field-height);
  padding: var(--Scale-space-2) 0;
}

.settings-row__label {
  flex: 0 0 auto;
  font-size: var(--font-size-13);
  color: var(--Theme-text-secondary);
  line-height: var(--line-height-body);
}

.settings-row__sim {
  margin-left: var(--Scale-space-2);
  padding: 1px 6px;
  border-radius: var(--Scale-radius-pill);
  background: color-mix(in srgb, var(--Theme-warning) 18%, transparent);
  color: var(--Theme-warning);
  font-size: var(--font-size-11);
}

.settings-row__value {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  text-align: right;
  font-size: var(--font-size-13);
  color: var(--Theme-text-primary);
  line-height: var(--line-height-body);
  min-width: 0;
}

/* 等宽 + tabular-nums：数字宽度恒定 */
.settings-row__value.is-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: var(--font-size-12);
}

.settings-row__hint {
  margin-top: 2px;
  font-family: var(--font-sans);
  font-size: var(--font-size-12);
  color: var(--Theme-text-tertiary);
}

.tone-warning {
  color: var(--Theme-warning);
}
.tone-success {
  color: var(--Theme-success);
}
.tone-error {
  color: var(--Theme-error);
}
.tone-tertiary {
  color: var(--Theme-text-tertiary);
}

/* 窄屏：两端对齐改为上下堆叠（05 §4.2） */
@media (max-width: 1023px) {
  .settings-row {
    flex-direction: column;
    align-items: stretch;
    gap: var(--Scale-space-1);
  }
  .settings-row__value {
    align-items: flex-start;
    text-align: left;
  }
}

/* ============================================================================
 * 宽卡体：由「两端对齐」改为「固定 label 列 + 值左对齐」
 *
 * ⚠️ 本块必须置于文件末尾。它要覆盖 .settings-row 与 .settings-row__value
 *    的基础声明，而两者特异性相同（0,1,0），**胜负由书写顺序决定**。
 *    若误置于基础声明之前：display:grid 仍会生效（因为 .settings-row 在前），
 *    但 align-items / text-align 会被后面的 .settings-row__value 覆盖 ——
 *    症状是「明明变成了网格，值却仍贴在右侧」。
 *
 * 触发条件：卡体 ≥ 1000px（如硬件能力页的单列全宽卡片）。
 * 「label 左 / 值右」在近千像素的跨度下会让视线横跨困难；固定 220px 的
 * label 列后，值与 label 始终成组。
 *
 * 不会触发的场景（保持原有两端对齐）：
 *   - 工作台右栏卡体约 300px
 *   - 设置页双列卡体约 630px
 *   - compact 档（<1024 视口）卡体不足 968px，故不与上方堆叠规则争抢
 * ========================================================================== */
@container sc-body (min-width: 1000px) {
  .settings-row {
    display: grid;
    grid-template-columns: 220px minmax(0, 1fr);
    align-items: start;
  }
  .settings-row__value {
    align-items: flex-start;
    text-align: left;
  }
}
</style>
