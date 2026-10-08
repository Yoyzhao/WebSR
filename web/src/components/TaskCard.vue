<script setup lang="ts">
/**
 * TaskCard —— 任务卡片（工作台「最近任务」+ 任务中心列表）（5A 定稿 · 15 组件之一）
 *
 * 约束：
 *   - 进度条**只渲染服务端下发的 percent**（不得本地插值，03 §5-6）。
 *   - 降级完成必须显式：`degraded` 为真时展示降级原因条（02 §2.2 铁律）。
 *   - 状态文案统一走 StatusText，禁止各页自行映射。
 *   - 数据一律经 `toTaskVM()` 换算，组件内不直接读契约原始字段。
 */
import { computed } from 'vue'
import { ElIcon } from 'element-plus'
import { Picture, Download } from '@element-plus/icons-vue'
import StatusText from './StatusText.vue'
import type { Task } from '@/types/api'
import { toTaskVM } from '@/utils/viewModel'

const props = defineProps<{
  task: Task
  /** 紧凑模式：用于工作台侧栏 */
  dense?: boolean
}>()

const emit = defineEmits<{
  (e: 'open', id: string): void
  (e: 'compare', id: string): void
  (e: 'download', id: string): void
  (e: 'cancel', id: string): void
  (e: 'retry', id: string): void
}>()

const vm = computed(() => toTaskVM(props.task))

function onClickCard() {
  emit('open', vm.value.id)
}
</script>

<template>
  <article
    class="task-card"
    :class="[`is-${vm.tone}`, { 'is-dense': dense }]"
    role="button"
    tabindex="0"
    @click="onClickCard"
    @keydown.enter.prevent="onClickCard"
    @keydown.space.prevent="onClickCard"
  >
    <!-- 区段一：缩略图 + 主信息 -->
    <div class="tc-body">
      <div class="tc-thumb">
        <img v-if="task.source_thumb" :src="task.source_thumb" alt="" loading="lazy" />
        <el-icon v-else :size="20" class="tc-thumb-ph"><Picture /></el-icon>
      </div>

      <div class="tc-main">
        <div class="tc-line1">
          <span class="tc-name" :title="vm.filename">{{ vm.filename }}</span>
          <StatusText :status="vm.status" :degraded="vm.degraded" />
        </div>

        <div class="tc-meta">
          <span class="tc-meta-item">{{ vm.modelName }}</span>
          <span class="tc-dot">·</span>
          <span class="tc-meta-item">{{ vm.paramsText }}</span>
          <span class="tc-dot">·</span>
          <span class="tc-meta-item tc-mono">{{ vm.createdText }}</span>
          <template v-if="vm.sourceResolution">
            <span class="tc-dot">·</span>
            <span class="tc-meta-item tc-mono">{{ vm.sourceResolution }}</span>
          </template>
        </div>

        <!-- 区段二：进度（仅运行中/取消中展示） -->
        <div v-if="vm.isRunning" class="tc-progress">
          <div class="tc-bar">
            <div class="tc-bar-fill" :style="{ width: vm.percentText }" />
          </div>
          <div class="tc-progress-meta">
            <span class="tc-stage">{{ vm.stageText || vm.statusLabel }}</span>
            <span class="tc-pct tc-mono">{{ vm.percentText }}</span>
          </div>
        </div>

        <!-- 区段三：降级原因（显式，不可静默） -->
        <div v-if="vm.degraded" class="tc-note tc-note-warn">
          <span class="tc-note-tag">降级</span>
          <span class="tc-note-text">{{ vm.degradeReasons.join('；') || '画质或速度已降级' }}</span>
        </div>

        <!-- 区段三之二：失败原因（显式） -->
        <div v-if="vm.isFailed" class="tc-note tc-note-error">
          <span class="tc-note-tag">失败</span>
          <span class="tc-note-text">{{ vm.errorMessage || '任务执行失败' }}</span>
        </div>

        <!-- 区段三之三：中断原因（显式） -->
        <div v-if="vm.isInterrupted" class="tc-note tc-note-warn">
          <span class="tc-note-tag">中断</span>
          <span class="tc-note-text">{{ vm.errorSuggestion || '进程重启导致任务中断，可重试' }}</span>
        </div>
      </div>
    </div>

    <!-- 区段四：操作（阻止冒泡，避免误触发打开详情） -->
    <div v-if="!dense" class="tc-actions" @click.stop>
      <button v-if="vm.isRunning" type="button" class="tc-act" @click="emit('cancel', vm.id)">取消</button>
      <button
        v-if="vm.isFailed || vm.isInterrupted || vm.status === 'canceled'"
        type="button"
        class="tc-act"
        @click="emit('retry', vm.id)"
      >
        重试
      </button>
      <button v-if="vm.isDone" type="button" class="tc-act" @click="emit('compare', vm.id)">对比</button>
      <button
        v-if="vm.isDone && vm.hasOutput"
        type="button"
        class="tc-act tc-act-icon"
        title="下载产出"
        @click="emit('download', vm.id)"
      >
        <el-icon :size="14"><Download /></el-icon>
      </button>
    </div>
  </article>
</template>

<style scoped>
.task-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  background: var(--Theme-bg-panel);
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-card);
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.task-card:hover {
  border-color: var(--Theme-border-strong);
  background: var(--Theme-bg-hover);
}

.task-card:focus-visible {
  outline: 2px solid var(--Theme-primary);
  outline-offset: 1px;
}

.task-card.is-dense {
  padding: 12px 14px;
  gap: 10px;
}

.tc-body {
  display: flex;
  gap: 12px;
  min-width: 0;
}

.tc-thumb {
  flex: 0 0 48px;
  width: 48px;
  height: 48px;
  border-radius: var(--Scale-radius-inner);
  overflow: hidden;
  background: var(--Theme-bg-elevated);
  border: 1px solid var(--Theme-border-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
}

.is-dense .tc-thumb {
  flex-basis: 40px;
  width: 40px;
  height: 40px;
}

.tc-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.tc-thumb-ph {
  color: var(--Theme-text-tertiary);
}

.tc-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tc-line1 {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.tc-name {
  font-size: var(--font-size-14);
  line-height: 20px;
  font-weight: 500;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex: 0 1 auto;
}

.tc-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-tertiary);
}

.tc-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.tc-dot {
  color: var(--Theme-text-tertiary);
  opacity: 0.6;
}

.tc-progress {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tc-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--Theme-bg-elevated);
  overflow: hidden;
}

.tc-bar-fill {
  height: 100%;
  background: var(--Theme-primary);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.tc-progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.tc-stage {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tc-pct {
  flex: 0 0 auto;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.tc-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: var(--font-size-12);
  line-height: 16px;
  padding: 4px 8px;
  border-radius: var(--Scale-radius-button);
}

.tc-note-warn {
  background: color-mix(in srgb, var(--Theme-warning) 12%, transparent);
  color: var(--Theme-warning);
}

.tc-note-error {
  background: color-mix(in srgb, var(--Theme-error) 10%, transparent);
  color: var(--Theme-error);
}

.tc-note-tag {
  flex: 0 0 auto;
  font-weight: 600;
}

.tc-note-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tc-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--Theme-border-subtle);
}

.tc-act {
  appearance: none;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 3px 10px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.tc-act:hover {
  background: var(--Theme-bg-hover);
  color: var(--Theme-text-primary);
}

.tc-act-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 3px 7px;
}

@media (max-width: 1023px) {
  .tc-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
