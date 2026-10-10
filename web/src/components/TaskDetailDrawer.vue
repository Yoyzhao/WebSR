<script setup lang="ts">
/**
 * TaskDetailDrawer —— 任务详情抽屉（5A 定稿 · 15 组件之一）
 *
 * 五区段结构（04 §3）：
 *   1) 概览：文件名、状态、模型、时间
 *   2) 参数与解析：用户请求值 → 引擎解析值（ParamDiffRow）
 *   3) 产出：产出图 / 中间图列表
 *   4) 执行证据：EP 证据（判据「目标 EP 节点 > 0 且 CPU 节点 = 0」）
 *   5) 操作：取消 / 重试 / 对比 / 下载 / 删除
 *
 * 约束：
 *   - 抽屉是"证据唯一落点"：`resolved`、`ep_evidence`、`downgrades` 只在这里完整展示。
 *   - 证据表不加纵向边框线、不加斑马纹（禁止事项第 7 条）。
 */
import { computed } from 'vue'
import { ElDrawer, ElIcon, ElTooltip } from 'element-plus'
import { Warning, InfoFilled } from '@element-plus/icons-vue'
import StatusText from './StatusText.vue'
import ParamDiffRow from './ParamDiffRow.vue'
import ActionButtons, { type ActionItem } from './ActionButtons.vue'
import type { Task } from '@/types/api'
import { toTaskVM } from '@/utils/viewModel'

const props = defineProps<{
  modelValue: boolean
  task: Task | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'compare', id: string): void
  (e: 'download', id: string, artifactId?: string): void
  (e: 'cancel', id: string): void
  (e: 'retry', id: string): void
  (e: 'remove', id: string): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const vm = computed(() => (props.task ? toTaskVM(props.task) : null))

/** EP 证据：判据是「目标 EP 节点数 > 0 且 CPU 节点数 = 0」 */
const epRows = computed(() => {
  const list = vm.value?.epEvidence ?? []
  return list.map((e) => ({
    ...e,
    verified: e.verified || e.node_count > 0,
  }))
})

const actions = computed<ActionItem[]>(() => {
  const v = vm.value
  if (!v) return []
  const list: ActionItem[] = []
  if (v.isRunning) list.push({ key: 'cancel', label: '取消任务' })
  if (v.isFailed || v.isInterrupted || v.status === 'canceled') {
    // 重试属 S2；契约 v1.0 无 POST /api/tasks/{id}/retry —— 入口禁用并标注
    list.push({ key: 'retry', label: '重试', disabled: true, disabledReason: '重试属 S2 阶段，尚未实现' })
  }
  list.push({
    key: 'compare',
    label: '对比',
    disabled: !v.isDone,
    disabledReason: '任务完成后才能对比',
  })
  list.push({
    key: 'download',
    label: '下载产出',
    disabled: !v.hasOutput,
    disabledReason: '暂无产出文件',
  })
  // 删除属 F-12（S2）；契约 v1.0 无 DELETE /api/tasks/{id} —— 入口禁用并标注
  list.push({
    key: 'remove',
    label: '删除任务',
    type: 'danger',
    disabled: true,
    disabledReason: '任务删除属 F-12（S2 阶段），尚未实现',
  })
  return list
})

function onAction(key: string) {
  const id = vm.value?.id
  if (!id) return
  if (key === 'compare') emit('compare', id)
  else if (key === 'download') emit('download', id)
  else if (key === 'cancel') emit('cancel', id)
  else if (key === 'retry') emit('retry', id)
  else if (key === 'remove') emit('remove', id)
}
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="vm ? vm.filename : '任务详情'"
    direction="rtl"
    size="520px"
    class="task-drawer"
  >
    <div v-if="!vm" class="td-empty">未选择任务</div>

    <div v-else class="td">
      <!-- 区段一 · 概览 -->
      <section class="td-sec">
        <div class="td-status-row">
          <StatusText :status="vm.status" :degraded="vm.degraded" />
          <span v-if="vm.isRunning" class="td-pct td-mono">{{ vm.percentText }}</span>
        </div>

        <div v-if="vm.isRunning" class="td-bar-wrap">
          <div class="td-bar"><div class="td-bar-fill" :style="{ width: vm.percentText }" /></div>
          <span v-if="vm.stageText" class="td-stage">{{ vm.stageText }}</span>
        </div>

        <dl class="td-kv">
          <div class="td-kv-row"><dt>模型</dt><dd>{{ vm.modelName }}</dd></div>
          <div class="td-kv-row"><dt>源图尺寸</dt><dd class="td-mono">{{ vm.sourceResolution || '—' }}</dd></div>
          <div v-if="vm.outputResolution" class="td-kv-row">
            <dt>产出尺寸</dt>
            <dd class="td-mono">{{ vm.outputResolution }}</dd>
          </div>
          <div class="td-kv-row"><dt>提交时间</dt><dd class="td-mono">{{ vm.createdText }}</dd></div>
          <div v-if="vm.finishedText" class="td-kv-row">
            <dt>完成时间</dt><dd class="td-mono">{{ vm.finishedText }}</dd>
          </div>
          <div v-if="vm.durationText" class="td-kv-row">
            <dt>耗时</dt><dd class="td-mono">{{ vm.durationText }}</dd>
          </div>
        </dl>
      </section>

      <!-- 失败提示（显式） -->
      <section v-if="vm.isFailed" class="td-alert td-alert-error">
        <el-icon :size="15"><Warning /></el-icon>
        <div class="td-alert-body">
          <p class="td-alert-msg">{{ vm.errorMessage || '任务执行失败' }}</p>
          <p v-if="vm.errorSuggestion" class="td-alert-sug">{{ vm.errorSuggestion }}</p>
          <p v-if="vm.errorCode" class="td-alert-code td-mono">{{ vm.errorCode }}</p>
        </div>
      </section>

      <!-- 中断提示（显式） -->
      <section v-if="vm.isInterrupted" class="td-alert td-alert-warn">
        <el-icon :size="15"><Warning /></el-icon>
        <div class="td-alert-body">
          <p class="td-alert-msg">{{ vm.errorMessage || '任务已被中断' }}</p>
          <p class="td-alert-sug">
            {{ vm.errorSuggestion || '应用重启或进程退出导致中断；重试能力属 S2 阶段，暂不可用' }}
          </p>
        </div>
      </section>

      <!-- 降级提示（显式，不可静默） -->
      <section v-if="vm.degraded" class="td-alert td-alert-warn">
        <el-icon :size="15"><Warning /></el-icon>
        <div class="td-alert-body">
          <p class="td-alert-msg">本次执行已降级</p>
          <ul class="td-alert-list">
            <li v-for="(d, i) in vm.degradeReasons" :key="i">{{ d }}</li>
          </ul>
        </div>
      </section>

      <!-- 区段二 · 参数与解析 -->
      <section class="td-sec">
        <h4 class="td-h">参数与解析</h4>
        <p class="td-hint">左为请求值，右为引擎实际解析值；「自动」表示交由引擎按当前档位决定。</p>
        <div class="td-params">
          <ParamDiffRow
            v-for="row in vm.paramRows"
            :key="row.label"
            :label="row.label"
            :requested="row.requested"
            :resolved="row.resolved"
            :reason="row.reason"
            :using-fallback="row.usingFallback"
            :mono="row.mono"
          />
        </div>
      </section>

      <!-- 区段三 · 产出 -->
      <section class="td-sec">
        <h4 class="td-h">产出</h4>
        <div v-if="vm.outputs.length === 0 && vm.intermediates.length === 0" class="td-none">暂无产出</div>
        <ul v-else class="td-artifacts">
          <li v-for="a in vm.outputs" :key="a.id" class="td-art">
            <span class="td-art-kind td-art-out">{{ a.kindLabel }}</span>
            <span class="td-art-name td-mono" :title="a.filename">{{ a.filename }}</span>
            <span v-if="a.resolutionText" class="td-art-dim td-mono">{{ a.resolutionText }}</span>
            <span class="td-art-size td-mono">{{ a.sizeText }}</span>
            <button type="button" class="td-art-dl" title="下载此文件" @click="emit('download', vm.id, a.id)">下载</button>
          </li>
          <li v-for="a in vm.intermediates" :key="a.id" class="td-art">
            <span class="td-art-kind">{{ a.kindLabel }}</span>
            <span class="td-art-name td-mono" :title="a.filename">{{ a.filename }}</span>
            <span v-if="a.resolutionText" class="td-art-dim td-mono">{{ a.resolutionText }}</span>
          </li>
        </ul>
      </section>

      <!-- 区段四 · 执行证据 -->
      <section class="td-sec">
        <h4 class="td-h">
          EP 执行证据
          <el-tooltip
            content="判据：目标 EP 节点数 > 0 且 CPU 节点数 = 0。节点数为 1 属整图融合的正常现象。"
            placement="top"
          >
            <el-icon :size="13" class="td-h-flag"><InfoFilled /></el-icon>
          </el-tooltip>
        </h4>
        <div v-if="epRows.length === 0" class="td-none">无证据记录</div>
        <table v-else class="td-table">
          <thead>
            <tr>
              <th>执行提供器</th>
              <th class="td-num">节点数</th>
              <th>CPU 节点</th>
              <th>判定</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in epRows" :key="e.provider">
              <td class="td-mono">{{ e.provider }}</td>
              <td class="td-num td-mono">{{ e.node_count }}</td>
              <td class="td-num td-mono">{{ e.cpu_node_count }}</td>
              <td>
                <span class="td-verdict" :class="e.verified ? 'is-ok' : 'is-idle'">
                  {{ e.verified ? '已生效' : '未参与' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="epRows[0]?.note" class="td-hint">{{ epRows[0].note }}</p>
        <p v-if="vm.resolved?.backend" class="td-hint">
          实际生效后端：<span class="td-mono">{{ vm.resolved.backend }}</span>
          <template v-if="vm.resolved.precision">
            · 精度 <span class="td-mono">{{ vm.resolved.precision }}</span>
          </template>
        </p>
      </section>

      <!-- 区段五 · 操作 -->
      <section class="td-sec td-sec-actions">
        <ActionButtons :actions="actions" @action="onAction" />
      </section>
    </div>
  </el-drawer>
</template>

<style scoped>
.td {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 0 4px 8px;
}

.td-sec {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.td-sec + .td-sec {
  padding-top: 18px;
  border-top: 1px solid var(--Theme-border-subtle);
}

.td-h {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-14);
  line-height: 20px;
  font-weight: 600;
  color: var(--Theme-text-primary);
}

.td-h-flag {
  color: var(--Theme-text-tertiary);
  cursor: help;
}

.td-hint {
  margin: 0;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.td-status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.td-pct {
  font-size: var(--font-size-14);
  color: var(--Theme-text-secondary);
}

.td-bar-wrap {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.td-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--Theme-bg-elevated);
  overflow: hidden;
}

.td-bar-fill {
  height: 100%;
  background: var(--Theme-primary);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.td-stage {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.td-kv {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.td-kv-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.td-kv-row dt {
  flex: 0 0 auto;
  color: var(--Theme-text-tertiary);
}

.td-kv-row dd {
  margin: 0;
  min-width: 0;
  text-align: right;
  color: var(--Theme-text-secondary);
  overflow-wrap: anywhere;
}

.td-alert {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--Scale-radius-inner);
  font-size: var(--font-size-13);
  line-height: 18px;
}

.td-alert-error {
  background: color-mix(in srgb, var(--Theme-error) 10%, transparent);
  color: var(--Theme-error);
}

.td-alert-warn {
  background: color-mix(in srgb, var(--Theme-warning) 11%, transparent);
  color: var(--Theme-warning);
}

.td-alert-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.td-alert-msg {
  margin: 0;
  font-weight: 500;
}

.td-alert-sug {
  margin: 0;
  opacity: 0.85;
}

.td-alert-code {
  margin: 0;
  opacity: 0.7;
  font-size: var(--font-size-12);
}

.td-alert-list {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.td-params {
  display: flex;
  flex-direction: column;
}

.td-none {
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-tertiary);
}

.td-artifacts {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.td-art {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-13);
  line-height: 18px;
  min-width: 0;
}

.td-art-kind {
  flex: 0 0 auto;
  font-size: var(--font-size-11);
  line-height: 15px;
  padding: 0 5px;
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-tertiary);
}

.td-art-out {
  background: color-mix(in srgb, var(--Theme-success) 14%, transparent);
  color: var(--Theme-success);
}

.td-art-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--Theme-text-primary);
}

.td-art-dim,
.td-art-size {
  flex: 0 0 auto;
  color: var(--Theme-text-tertiary);
}

.td-art-dl {
  flex: 0 0 auto;
  appearance: none;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-12);
  line-height: 16px;
  padding: 1px 7px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.td-art-dl:hover {
  border-color: var(--Theme-primary);
  color: var(--Theme-primary);
}

/* 证据表：无纵向边框线、无斑马纹（禁止事项第 7 条） */
.td-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.td-table th {
  text-align: left;
  font-weight: 500;
  color: var(--Theme-text-tertiary);
  padding: 6px 0;
  border-bottom: 1px solid var(--Theme-border-subtle);
}

.td-table td {
  padding: 6px 0;
  color: var(--Theme-text-secondary);
  border-bottom: 1px solid var(--Theme-border-subtle);
}

.td-table tbody tr:last-child td {
  border-bottom: none;
}

.td-num {
  text-align: right;
  width: 64px;
}

.td-verdict {
  font-size: var(--font-size-12);
}

.td-verdict.is-ok {
  color: var(--Theme-success);
}

.td-verdict.is-idle {
  color: var(--Theme-text-tertiary);
}

.td-sec-actions {
  border-top: 1px solid var(--Theme-border-subtle);
}

.td-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.td-empty {
  font-size: var(--font-size-14);
  color: var(--Theme-text-tertiary);
}
</style>

<style>
/* 抽屉尺寸在窄屏需要收敛 */
@media (max-width: 640px) {
  .task-drawer.el-drawer {
    width: 92vw !important;
  }
}
</style>
