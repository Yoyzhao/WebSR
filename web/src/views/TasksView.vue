<script setup lang="ts">
/**
 * 任务中心 —— 全量任务列表 + 对比视图（P2，docs/prototype/01-页面结构与布局.md §4）。
 *
 * 列定义（7 列，固定列合计 974 + 20px 列沟）：
 *   文件名(自适应) ｜ 模型/后端(300) ｜ 倍率(88,左对齐) ｜ 状态(自适应) ｜ 耗时(120,右对齐) ｜ 产出(180,右对齐) ｜ 操作(176)
 *
 * 约束：
 *   - 表格**不加纵向边框线、不加斑马纹**（禁止事项第 7 条）。
 *   - 窄屏（<1024）降级为卡片列表（05 §5）。
 *   - 状态筛选走 SegmentedControl，不引入额外组件。
 *   - 六态覆盖：空 / 加载 / 异常 / 降级 / 取消 / 中断。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import EmptyState from '@/components/EmptyState.vue'
import CompareSlider from '@/components/CompareSlider.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import TaskCard from '@/components/TaskCard.vue'
import TaskDetailDrawer from '@/components/TaskDetailDrawer.vue'
import ActionButtons from '@/components/ActionButtons.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useTaskStore } from '@/stores/tasks'
import { triggerDownload } from '@/api/client'
import { PAGE_MAX_WIDTH } from '@/constants'
import { toTaskVM, type TaskVM } from '@/utils/viewModel'
import type { Task } from '@/types/api'

const route = useRoute()
const { isCompact } = useBreakpoint()
const taskStore = useTaskStore()

const filter = ref<'all' | 'running' | 'completed' | 'failed'>('all')
const detailOpen = ref(false)
const detailTaskId = ref<string | null>(null)

// 对比视图
const compareTaskId = ref<string | null>(null)

const compareTask = computed(() => taskStore.tasks.find((t) => t.id === compareTaskId.value) ?? null)
const compareVM = computed(() => (compareTask.value ? toTaskVM(compareTask.value) : null))

/**
 * Mock 阶段的对比图源。
 * 用内联 SVG data URI 生成一对「低清 vs 修复」示意图像 —— 目的是让滑块的
 * 裁切、拖拽、键盘交互真实可验证，而不是用两张纯色块糊弄。
 * 步骤 6 接入后端后，改为 `output` 产出的真实文件 URL。
 */
function mockCompareSvg(kind: 'before' | 'after'): string {
  const blur = kind === 'before' ? '<filter id="b"><feGaussianBlur stdDeviation="2.2"/></filter>' : ''
  const filterAttr = kind === 'before' ? ' filter="url(#b)"' : ''
  const gridOpacity = kind === 'before' ? '0.10' : '0.22'
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800" viewBox="0 0 1280 800">
<defs>
${blur}
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#1b3a5c"/><stop offset="55%" stop-color="#3d6f9e"/><stop offset="100%" stop-color="#c9a26b"/>
</linearGradient>
</defs>
<rect width="1280" height="800" fill="url(#sky)"/>
<circle cx="960" cy="250" r="70" fill="#f6d9a0" opacity="0.9"/>
<g${filterAttr}>
<path d="M0 620 L260 430 L430 560 L640 360 L880 600 L1080 470 L1280 620 L1280 800 L0 800 Z" fill="#20313f"/>
<path d="M0 700 L330 590 L620 690 L900 580 L1280 700 L1280 800 L0 800 Z" fill="#16232d"/>
<rect x="140" y="470" width="120" height="180" fill="#2b3d4c" opacity="0.9"/>
<rect x="300" y="520" width="90" height="130" fill="#243542" opacity="0.9"/>
<rect x="1050" y="500" width="130" height="150" fill="#2b3d4c" opacity="0.9"/>
</g>
<g stroke="#ffffff" stroke-opacity="${gridOpacity}" stroke-width="1">
${Array.from({ length: 7 }, (_, i) => `<line x1="${(i + 1) * 160}" y1="0" x2="${(i + 1) * 160}" y2="800"/>`).join('')}
${Array.from({ length: 4 }, (_, i) => `<line x1="0" y1="${(i + 1) * 160}" x2="1280" y2="${(i + 1) * 160}"/>`).join('')}
</g>
${kind === 'after' ? '<text x="40" y="60" font-family="monospace" font-size="26" fill="#ffffff" fill-opacity="0.75">4x upscaled · detail reconstructed</text>' : '<text x="40" y="60" font-family="monospace" font-size="26" fill="#ffffff" fill-opacity="0.55">source · low resolution</text>'}
</svg>`
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

const compareSources = computed(() => ({
  before: mockCompareSvg('before'),
  after: mockCompareSvg('after'),
}))

const filtered = computed(() => {
  const list = taskStore.tasks
  if (filter.value === 'all') return list
  if (filter.value === 'running') return list.filter((t) => t.status === 'running' || t.status === 'queued' || t.status === 'canceling')
  if (filter.value === 'completed') return list.filter((t) => t.status === 'completed')
  return list.filter((t) => t.status === 'failed' || t.status === 'interrupted' || t.status === 'canceled')
})

const hasAny = computed(() => taskStore.tasks.length > 0)
const isEmptyFiltered = computed(() => filtered.value.length === 0)

function vms(list: Task[]): TaskVM[] {
  return list.map(toTaskVM)
}

function openDetail(id: string) {
  detailTaskId.value = id
  detailOpen.value = true
}

function startCompare(id: string) {
  compareTaskId.value = id
}

function stopCompare() {
  compareTaskId.value = null
  if (route.query.compare) {
    // 清掉深链参数，避免刷新后又弹回对比
    window.history.replaceState({}, '', '/tasks')
  }
}

async function onAction({ key, id }: { key: string; id: string }) {
  if (key === 'compare') return startCompare(id)
  if (key === 'download') {
    const t = taskStore.tasks.find((x) => x.id === id)
    const out = t?.artifacts?.find((a) => a.kind === 'output')
    if (!out) return ElMessage.warning('该任务暂无产出文件')
    triggerDownload(new Blob([`mock content of ${out.filename}`]), out.filename)
    return ElMessage.success(`已开始下载 ${out.filename}`)
  }
  if (key === 'cancel') {
    await ElMessageBox.confirm('取消后本次推理进度将丢失。确认取消？', '取消任务', {
      confirmButtonText: '确认取消',
      cancelButtonText: '继续执行',
      type: 'warning',
    })
    await taskStore.cancel(id)
    return ElMessage.info('任务已取消')
  }
  if (key === 'retry') {
    await taskStore.retry(id)
    return ElMessage.success('已重新提交')
  }
}

async function clearFinished() {
  const count = taskStore.tasks.filter((t) => t.status === 'completed' || t.status === 'canceled' || t.status === 'interrupted').length
  if (!count) return ElMessage.info('没有可清理的已完成任务')
  await ElMessageBox.confirm(`将删除 ${count} 条已完成 / 已取消 / 已中断的任务记录（不影响产出文件）。确认清理？`, '清理已完成', {
    confirmButtonText: '确认清理',
    cancelButtonText: '取消',
    type: 'warning',
  })
  const n = await taskStore.clearFinished()
  ElMessage.success(`已清理 ${n} 条记录`)
}

onMounted(async () => {
  if (!taskStore.tasks.length) await taskStore.load()
  const q = route.query.compare
  if (typeof q === 'string' && taskStore.tasks.some((t) => t.id === q)) startCompare(q)
})

watch(
  () => route.query.compare,
  (q) => {
    if (typeof q === 'string' && taskStore.tasks.some((t) => t.id === q)) startCompare(q)
  },
)
</script>

<template>
  <div class="tasks-page" :style="{ maxWidth: PAGE_MAX_WIDTH.tasks + 'px' }">
    <PageHeader title="任务中心" subtitle="全部超分修复任务的执行记录与产出">
      <template #actions>
        <ActionButtons
          :actions="[
            { key: 'clear', label: '清理已完成', disabled: !hasAny },
          ]"
          size="sm"
          @action="clearFinished"
        />
      </template>
    </PageHeader>

    <!-- ===== 对比视图 ===== -->
    <SectionCard v-if="compareVM" title="结果对比" :subtitle="compareVM.filename">
      <template #actions>
        <button type="button" class="tp-close" @click="stopCompare">退出对比</button>
      </template>
      <CompareSlider
        :before-src="compareSources.before"
        :after-src="compareSources.after"
        before-label="原图"
        after-label="修复后"
        :initial="50"
      />
      <p class="tp-compare-note">
        拖动手柄或用 ← → 方向键调整分割位置（Shift + 方向键可加速）。左半为原图，右半为修复结果。
        <br />
        <span class="tp-compare-mock">
          注：当前为 Mock 阶段的示意图源，用于验证滑块交互与裁切行为；步骤 6 接入后端后替换为真实产出文件。
        </span>
      </p>
      <dl class="tp-compare-kv">
        <div><dt>源图</dt><dd class="tp-mono">{{ compareVM.sourceResolution }}</dd></div>
        <div><dt>产出</dt><dd class="tp-mono">{{ compareVM.outputResolution || '—' }}</dd></div>
        <div><dt>耗时</dt><dd class="tp-mono">{{ compareVM.durationText || '—' }}</dd></div>
        <div><dt>模型</dt><dd>{{ compareVM.modelName }}</dd></div>
      </dl>
    </SectionCard>

    <!-- ===== 列表 ===== -->
    <SectionCard :subtitle="`共 ${filtered.length} 条`">
      <template #actions>
        <SegmentedControl
          v-model="filter"
          :options="[
            { label: '全部', value: 'all' },
            { label: '进行中', value: 'running' },
            { label: '已完成', value: 'completed' },
            { label: '异常', value: 'failed' },
          ]"
          size="sm"
        />
      </template>

      <!-- 加载态 -->
      <div v-if="taskStore.loading" class="tp-skeleton">
        <div v-for="i in 4" :key="i" class="tp-skeleton-row" />
      </div>

      <!-- 空态（完全无数据） -->
      <EmptyState
        v-else-if="!hasAny"
        icon-size="48"
        title="还没有任何任务"
        description="前往工作台上传图像并提交超分任务，执行记录会汇总到这里"
      >
        <template #action>
          <RouterLink to="/" class="tp-link">去工作台 →</RouterLink>
        </template>
      </EmptyState>

      <!-- 筛选后为空 -->
      <EmptyState
        v-else-if="isEmptyFiltered"
        title="当前筛选下没有任务"
        description="换一个筛选条件，或选择「全部」查看"
      />

      <!-- 窄屏：卡片列表 -->
      <div v-else-if="isCompact" class="tp-cards">
        <TaskCard
          v-for="t in filtered"
          :key="t.id"
          :task="t"
          @open="openDetail"
          @compare="startCompare"
          @download="(id: string) => onAction({ key: 'download', id })"
          @cancel="(id: string) => onAction({ key: 'cancel', id })"
          @retry="(id: string) => onAction({ key: 'retry', id })"
        />
      </div>

      <!-- 宽屏：表格 -->
      <div v-else class="tp-table-wrap">
        <table class="tp-table">
          <colgroup>
            <col />
            <col style="width: 300px" />
            <col style="width: 88px" />
            <col />
            <col style="width: 120px" />
            <col style="width: 180px" />
            <col style="width: 176px" />
          </colgroup>
          <thead>
            <tr>
              <th>文件</th>
              <th>模型 / 后端</th>
              <th class="tp-left">放大</th>
              <th>状态</th>
              <th class="tp-right">耗时</th>
              <th class="tp-right">产出</th>
              <th class="tp-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="vm in vms(filtered)" :key="vm.id" class="tp-row" @click="openDetail(vm.id)">
              <td>
                <div class="tp-file">
                  <span class="tp-file-name" :title="vm.filename">{{ vm.filename }}</span>
                  <span class="tp-file-sub tp-mono">{{ vm.createdText }}</span>
                </div>
              </td>
              <td>
                <div class="tp-file">
                  <span class="tp-model">{{ vm.modelName }}</span>
                  <span class="tp-file-sub tp-mono" :title="vm.resolved?.backend">
                    {{ vm.resolved?.backend ?? '—' }}{{ vm.resolved?.precision ? ' · ' + vm.resolved.precision : '' }}
                  </span>
                </div>
              </td>
              <td class="tp-left tp-mono">×{{ vm.resolved ? vm.paramRows.find((r) => r.label === '放大倍数')?.requested : '—' }}</td>
              <td>
                <div class="tp-status">
                  <StatusText :status="vm.status" :degraded="vm.degraded" />
                  <span v-if="vm.isRunning" class="tp-status-pct tp-mono">{{ vm.percentText }}</span>
                </div>
              </td>
              <td class="tp-right tp-mono">{{ vm.durationText || '—' }}</td>
              <td class="tp-right tp-mono">{{ vm.outputResolution || '—' }}</td>
              <td class="tp-right" @click.stop>
                <ActionButtons
                  :actions="
                    [
                      { key: 'compare', label: '对比', disabled: !vm.isDone, disabledReason: '任务完成后才能对比' },
                      { key: 'download', label: '下载', disabled: !vm.hasOutput, disabledReason: '暂无产出文件' },
                      vm.isRunning ? { key: 'cancel', label: '取消' } : null,
                      vm.isFailed || vm.isInterrupted || vm.status === 'canceled' ? { key: 'retry', label: '重试' } : null,
                    ].filter(Boolean) as any
                  "
                  size="sm"
                  @action="(k: string) => onAction({ key: k, id: vm.id })"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </SectionCard>

    <TaskDetailDrawer
      v-model="detailOpen"
      :task="taskStore.tasks.find((t) => t.id === detailTaskId) ?? null"
      @compare="(id: string) => { detailOpen = false; startCompare(id) }"
      @cancel="(id: string) => onAction({ key: 'cancel', id })"
      @retry="(id: string) => onAction({ key: 'retry', id })"
      @download="(id: string) => onAction({ key: 'download', id })"
      @remove="(id: string) => taskStore.removeByIds([id]).then(() => { detailOpen = false; ElMessage.success('任务已删除') })"
    />
  </div>
</template>

<style scoped>
.tasks-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px 28px 40px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

/* ---------- 对比 ---------- */
.tp-close {
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
}

.tp-close:hover {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.tp-compare-note {
  margin: 10px 0 0;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.tp-compare-mock {
  opacity: 0.9;
}

.tp-compare-kv {
  margin: 12px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
}

.tp-compare-kv > div {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.tp-compare-kv dt {
  color: var(--Theme-text-tertiary);
}

.tp-compare-kv dd {
  margin: 0;
  color: var(--Theme-text-primary);
}

/* ---------- 表格 ---------- */
.tp-table-wrap {
  width: 100%;
  overflow-x: auto;
}

.tp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.tp-table th {
  text-align: left;
  font-weight: 500;
  color: var(--Theme-text-tertiary);
  padding: 8px 10px;
  border-bottom: 1px solid var(--Theme-border-subtle);
  white-space: nowrap;
}

.tp-table td {
  padding: 10px;
  border-bottom: 1px solid var(--Theme-border-subtle);
  color: var(--Theme-text-secondary);
  vertical-align: middle;
}

.tp-table tbody tr:last-child td {
  border-bottom: none;
}

/* 禁止斑马纹与纵向边框线（禁止事项第 7 条） */
.tp-row {
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.tp-row:hover td {
  background: var(--Theme-bg-hover);
}

.tp-left {
  text-align: left;
}

.tp-right {
  text-align: right;
}

.tp-file {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.tp-file-name {
  font-size: var(--font-size-13);
  line-height: 18px;
  font-weight: 500;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}

.tp-model {
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tp-file-sub {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tp-status {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.tp-status-pct {
  font-size: var(--font-size-12);
  color: var(--Theme-text-tertiary);
}

.tp-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* ---------- 卡片列表 / 骨架 ---------- */
.tp-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tp-skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tp-skeleton-row {
  height: 52px;
  border-radius: var(--Scale-radius-inner);
  background: linear-gradient(
    90deg,
    var(--Theme-bg-elevated) 25%,
    var(--Theme-bg-hover) 50%,
    var(--Theme-bg-elevated) 75%
  );
  background-size: 200% 100%;
  animation: tp-shimmer 1.4s ease-in-out infinite;
}

@keyframes tp-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.tp-link {
  color: var(--Theme-link);
  font-size: var(--font-size-13);
  text-decoration: none;
}

.tp-link:hover {
  text-decoration: underline;
}

@media (max-width: 1023px) {
  .tasks-page {
    padding: 18px 16px 32px;
  }
}
</style>
