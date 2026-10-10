<script setup lang="ts">
/**
 * 工作台 —— 主任务页（P1，docs/prototype/01-页面结构与布局.md §3）。
 *
 * 三栏结构（≥1440）：
 *   左栏 320px  上传区 + 参数面板（自动档开关 / 倍率 / 模型选择 / tile / 精度 / 后端）
 *   中栏 自适应  预览区（原图 / 结果 / 对比滑块）+ 主操作按钮
 *   右栏 340px  最近任务列表（TaskCard dense）
 *
 * 中屏（1024–1439）：右栏折叠进抽屉，中栏保留
 * 窄屏（<1024）：单栏堆叠，参数面板折叠进抽屉
 *
 * ⚠️ 不设宽度上限 —— 中栏是图像预览区，宽度直接转化为可用预览面积（05 §2.2）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElDrawer, ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled, Setting, Tickets, Picture, RefreshLeft } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import SettingsRow from '@/components/SettingsRow.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import EmptyState from '@/components/EmptyState.vue'
import TaskCard from '@/components/TaskCard.vue'
import TaskDetailDrawer from '@/components/TaskDetailDrawer.vue'
import ActionButtons from '@/components/ActionButtons.vue'
import StatusText from '@/components/StatusText.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useTaskStore } from '@/stores/tasks'
import { useSystemStore } from '@/stores/system'
import { fetchModels, triggerDownload, uploadFile } from '@/api/client'
import type { Model, TaskParams, UploadResult } from '@/types/api'
import { UPLOAD } from '@/constants'
import { formatBytes, formatResolution } from '@/utils/format'

const router = useRouter()
const { isWide, isCompact } = useBreakpoint()
const taskStore = useTaskStore()
const system = useSystemStore()

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
const models = ref<Model[]>([])
const modelsLoading = ref(true)

const uploading = ref(false)
const uploadProgress = ref(0)
const uploaded = ref<UploadResult | null>(null)
const sourcePreviewUrl = ref('')

const submitting = ref(false)

const detailOpen = ref(false)
const detailTaskId = ref<string | null>(null)

const paramsDrawer = ref(false)
const tasksDrawer = ref(false)

/** 自动档：tile / 精度 / 后端 三项由引擎决定（模型始终由用户选择，PRD v1.8） */
const autoMode = ref(true)
const form = ref({
  scale: 4,
  modelId: 'mdl_realesrgan_x4',
  tile: 512,
  precision: 'fp16' as 'fp32' | 'fp16',
  backend: 'cuda' as 'cpu' | 'cuda' | 'openvino' | 'tensorrt',
})

// ---------------------------------------------------------------------------
// 派生
// ---------------------------------------------------------------------------
const selectedModel = computed(() => models.value.find((m) => m.id === form.value.modelId) ?? null)

const detailTask = computed(() => taskStore.tasks.find((t) => t.id === detailTaskId.value) ?? null)

const previewTask = computed(() => taskStore.previewTask)

const canSubmit = computed(() => !!uploaded.value && !submitting.value)

const backendOptions = computed(() => {
  const m = selectedModel.value
  const all = [
    { value: 'cuda', label: 'CUDA', disabled: false },
    { value: 'openvino', label: 'OpenVINO', disabled: false },
    { value: 'cpu', label: 'CPU', disabled: false },
    { value: 'tensorrt', label: 'TensorRT', disabled: false },
  ]
  if (!m) return all
  return all.map((b) => ({
    ...b,
    // 能力声明驱动置灰（禁止按设备型号硬编码，ADR-004）
    disabled: !m.supported_backends.includes(b.value as never),
  }))
})

const modelOptions = computed(() =>
  models.value.map((m) => ({
    label: m.available ? m.name : `${m.name}（不可用）`,
    value: m.id,
    disabled: !m.available,
  })),
)

/** 参数面板：自动档时三项只读（引擎决定），手动档才可编辑 */
const paramsLocked = computed(() => autoMode.value)

// ---------------------------------------------------------------------------
// 交互
// ---------------------------------------------------------------------------
async function onPickFile(file: File) {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!(UPLOAD.extensions as string[]).includes(ext)) {
    ElMessage.error(`不支持的格式 .${ext}，请上传 ${UPLOAD.extensions.join(' / ')}`)
    return
  }
  if (file.size > UPLOAD.maxSizeMB * 1024 * 1024) {
    ElMessage.error(`文件超过 ${UPLOAD.maxSizeMB} MB 上限`)
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  try {
    // 进度仅作上传体验，不参与推理进度（推理进度只由 SSE 驱动）
    const timer = window.setInterval(() => {
      uploadProgress.value = Math.min(90, uploadProgress.value + 12)
    }, 90)
    const res = await uploadFile(file)
    window.clearInterval(timer)
    uploadProgress.value = 100
    uploaded.value = res
    if (sourcePreviewUrl.value) URL.revokeObjectURL(sourcePreviewUrl.value)
    sourcePreviewUrl.value = URL.createObjectURL(file)
    ElMessage.success(`已读取：${formatResolution(res.width, res.height)} · ${formatBytes(res.size)}`)
  } catch (e) {
    ElMessage.error((e as Error).message || '上传失败')
  } finally {
    uploading.value = false
  }
}

function onDrop(e: DragEvent) {
  const f = e.dataTransfer?.files?.[0]
  if (f) onPickFile(f)
}

function onFileInput(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) onPickFile(f)
}

function resetUpload() {
  if (sourcePreviewUrl.value) URL.revokeObjectURL(sourcePreviewUrl.value)
  sourcePreviewUrl.value = ''
  uploaded.value = null
  uploadProgress.value = 0
}

async function submit() {
  if (!uploaded.value || !selectedModel.value) return
  submitting.value = true
  try {
    const params: TaskParams = {
      scale: form.value.scale,
      model_id: form.value.modelId,
      tile: autoMode.value ? null : form.value.tile,
      precision: autoMode.value ? null : form.value.precision,
      backend: autoMode.value ? null : form.value.backend,
      auto: autoMode.value,
    }
    const task = await taskStore.submit(
      uploaded.value.file_id,
      uploaded.value.filename,
      params,
      { width: uploaded.value.width, height: uploaded.value.height },
      selectedModel.value.name,
    )
    ElMessage.success('任务已提交，进度将在下方实时更新')
    resetUpload()
    if (!isWide.value) tasksDrawer.value = true
    detailTaskId.value = task.id
  } catch (e) {
    ElMessage.error((e as Error).message || '提交失败')
  } finally {
    submitting.value = false
  }
}

function openDetail(id: string) {
  detailTaskId.value = id
  detailOpen.value = true
}

async function onTaskAction({ key, id }: { key: string; id: string }) {
  if (key === 'compare') {
    router.push({ path: '/tasks', query: { compare: id } })
    return
  }
  if (key === 'download') {
    const t = taskStore.tasks.find((x) => x.id === id)
    const out = t?.artifacts?.find((a) => a.kind === 'output')
    if (!out) {
      ElMessage.warning('该任务暂无产出文件')
      return
    }
    triggerDownload(new Blob([`mock content of ${out.filename}`]), out.filename)
    ElMessage.success(`已开始下载 ${out.filename}`)
    return
  }
  if (key === 'cancel') {
    await ElMessageBox.confirm('取消后本次推理进度将丢失，需要重新提交。确认取消？', '取消任务', {
      confirmButtonText: '确认取消',
      cancelButtonText: '继续执行',
      type: 'warning',
    })
    await taskStore.cancel(id)
    ElMessage.info('任务已取消')
    return
  }
  if (key === 'retry') {
    await taskStore.retry(id)
    ElMessage.success('已重新提交')
  }
}

function runCalibration() {
  system.calibrate().then(() => ElMessage.success('标定任务已触发（S2 阶段实现）'))
}

onMounted(async () => {
  try {
    models.value = await fetchModels()
    if (!models.value.some((m) => m.id === form.value.modelId && m.available)) {
      const firstAvailable = models.value.find((m) => m.available)
      if (firstAvailable) form.value.modelId = firstAvailable.id
    }
  } finally {
    modelsLoading.value = false
  }
})
</script>

<template>
  <div class="wb">
    <PageHeader title="工作台" subtitle="上传图像 → 选择模型 → 提交超分修复">
      <template #actions>
        <div class="wb-head-actions">
          <button v-if="isCompact" type="button" class="wb-chip" @click="paramsDrawer = true">
            <el-icon :size="14"><Setting /></el-icon>参数
          </button>
          <button v-if="!isWide" type="button" class="wb-chip" @click="tasksDrawer = true">
            <el-icon :size="14"><Tickets /></el-icon>任务
            <span v-if="taskStore.runningTasks.length" class="wb-chip-badge">{{ taskStore.runningTasks.length }}</span>
          </button>
        </div>
      </template>
    </PageHeader>

    <div class="wb-grid" :class="{ 'is-wide': isWide }">
      <!-- ============ 左栏：上传 + 参数 ============ -->
      <aside v-if="!isCompact" class="wb-col wb-col-left">
        <SectionCard title="源图像">
          <div
            v-if="!uploaded"
            class="up-drop"
            :class="{ 'is-busy': uploading }"
            @dragover.prevent
            @drop.prevent="onDrop"
          >
            <el-icon :size="24" class="up-icon"><UploadFilled /></el-icon>
            <p class="up-main">拖入图像，或<label class="up-link">点击选择<input type="file" class="up-input" :accept="UPLOAD.extensions.map((e) => '.' + e).join(',')" @change="onFileInput" /></label></p>
            <p class="up-sub">{{ UPLOAD.extensions.join(' / ') }} · 单张上限 {{ UPLOAD.maxSizeMB }} MB</p>
            <div v-if="uploading" class="up-bar"><div class="up-bar-fill" :style="{ width: uploadProgress + '%' }" /></div>
          </div>

          <div v-else class="up-done">
            <img v-if="sourcePreviewUrl" :src="sourcePreviewUrl" class="up-thumb" alt="源图预览" />
            <div class="up-done-info">
              <span class="up-done-name" :title="uploaded.filename">{{ uploaded.filename }}</span>
              <span class="up-done-meta">
                {{ formatResolution(uploaded.width, uploaded.height) }} · {{ formatBytes(uploaded.size) }} ·
                <span class="up-mono">{{ uploaded.real_format }}</span>
              </span>
            </div>
            <button type="button" class="up-reset" title="重新选择" @click="resetUpload">
              <el-icon :size="14"><RefreshLeft /></el-icon>
            </button>
          </div>
        </SectionCard>

        <SectionCard title="参数" class="wb-params">
          <SettingsRow label="自动档" hint="tile / 精度 / 后端由引擎按实测标定决定">
            <SegmentedControl
              :model-value="autoMode ? 'auto' : 'manual'"
              :options="[
                { label: '自动', value: 'auto' },
                { label: '手动', value: 'manual' },
              ]"
              size="sm"
              @update:model-value="autoMode = $event === 'auto'"
            />
          </SettingsRow>

          <SettingsRow label="模型" hint="模型始终由你选择，引擎不做推荐">
            <select v-model="form.modelId" class="wb-select" :disabled="modelsLoading">
              <option v-for="o in modelOptions" :key="o.value" :value="o.value" :disabled="o.disabled">
                {{ o.label }}
              </option>
            </select>
          </SettingsRow>

          <SettingsRow label="放大倍数">
            <SegmentedControl
              :model-value="String(form.scale)"
              :options="[
                { label: '×2', value: '2' },
                { label: '×3', value: '3' },
                { label: '×4', value: '4' },
              ]"
              size="sm"
              @update:model-value="form.scale = Number($event)"
            />
          </SettingsRow>

          <SettingsRow label="分块 tile" :hint="paramsLocked ? '自动档下由引擎决定' : '越小的 tile 越省显存，但更慢'">
            <SegmentedControl
              :model-value="String(form.tile)"
              :options="[
                { label: '256', value: '256', disabled: paramsLocked },
                { label: '384', value: '384', disabled: paramsLocked },
                { label: '512', value: '512', disabled: paramsLocked },
              ]"
              size="sm"
              @update:model-value="form.tile = Number($event)"
            />
          </SettingsRow>

          <SettingsRow label="精度" :hint="paramsLocked ? '自动档下由引擎决定' : 'CPU 路径强制 fp32'">
            <SegmentedControl
              v-model="form.precision"
              :options="[
                { label: 'fp32', value: 'fp32', disabled: paramsLocked },
                { label: 'fp16', value: 'fp16', disabled: paramsLocked },
              ]"
              size="sm"
              @update:model-value="form.precision = $event as 'fp32' | 'fp16'"
            />
          </SettingsRow>

          <SettingsRow label="后端" :hint="paramsLocked ? '自动档下由引擎决定' : '置灰项为当前模型不支持'">
            <div class="wb-backends">
              <button
                v-for="b in backendOptions"
                :key="b.value"
                type="button"
                class="wb-be"
                :class="{ 'is-active': form.backend === b.value }"
                :disabled="b.disabled || paramsLocked"
                @click="form.backend = b.value as 'cpu' | 'cuda' | 'openvino' | 'tensorrt'"
              >
                {{ b.label }}
              </button>
            </div>
          </SettingsRow>

          <template #footer>
            <div class="wb-params-foot">
              <span class="wb-foot-key">当前档位</span>
              <span class="wb-foot-val">{{ system.tierBadgeText }}</span>
              <button type="button" class="wb-foot-act" title="重新标定" @click="runCalibration">重新标定</button>
            </div>
          </template>
        </SectionCard>
      </aside>

      <!-- ============ 中栏：预览 + 主操作 ============ -->
      <section class="wb-col wb-col-main">
        <SectionCard class="wb-preview-card" no-padding>
          <div class="wb-preview">
            <template v-if="previewTask">
              <div class="wb-preview-head">
                <div class="wb-preview-title">
                  <el-icon :size="14"><Picture /></el-icon>
                  <span :title="previewTask.filename">{{ previewTask.filename }}</span>
                </div>
                <StatusText
                  :status="previewTask.status"
                  :degraded="!!previewTask.resolved?.degraded && previewTask.status === 'completed'"
                />
              </div>

              <div class="wb-preview-stage">
                <img
                  v-if="sourcePreviewUrl"
                  :src="sourcePreviewUrl"
                  class="wb-preview-img"
                  alt="源图预览"
                />
                <div v-else class="wb-preview-ph">
                  <el-icon :size="32"><Picture /></el-icon>
                  <p>当前任务无内置预览图</p>
                  <p class="wb-preview-ph-sub">提交本页上传的图像后将在此处显示预览</p>
                </div>
              </div>

              <div v-if="previewTask.status === 'running' || previewTask.status === 'queued' || previewTask.status === 'canceling'" class="wb-preview-prog">
                <div class="wb-prog-bar">
                  <div class="wb-prog-fill" :style="{ width: `${Math.round((previewTask.progress.percent ?? 0) * 100)}%` }" />
                </div>
                <div class="wb-prog-meta">
                  <span>{{ previewTask.stage_message || '处理中' }}</span>
                  <span class="wb-mono">{{ Math.round((previewTask.progress.percent ?? 0) * 100) }}%</span>
                </div>
              </div>

              <div class="wb-preview-actions">
                <ActionButtons
                  :actions="[
                    { key: 'detail', label: '查看详情', type: 'primary' },
                    { key: 'compare', label: '进入对比', disabled: previewTask.status !== 'completed', disabledReason: '任务完成后才能对比' },
                  ]"
                  @action="(k: string) => (k === 'detail' ? openDetail(previewTask!.id) : router.push({ path: '/tasks', query: { compare: previewTask!.id } }))"
                />
              </div>
            </template>

            <EmptyState
              v-else
              icon-size="48"
              title="还没有任务"
              description="上传一张图像并提交超分任务，这里会显示实时预览与进度"
            />
          </div>
        </SectionCard>

        <SectionCard v-if="uploaded" title="待提交任务">
          <div class="wb-ready">
            <div class="wb-ready-info">
              <span class="wb-ready-name">{{ uploaded.filename }}</span>
              <span class="wb-ready-meta">
                {{ formatResolution(uploaded.width, uploaded.height) }} → ×{{ form.scale }} =
                {{ formatResolution(uploaded.width * form.scale, uploaded.height * form.scale) }}
              </span>
            </div>
            <button type="button" class="wb-submit" :disabled="!canSubmit" @click="submit">
              {{ submitting ? '提交中…' : '提交超分任务' }}
            </button>
          </div>
          <p class="wb-ready-hint">
            {{ autoMode ? '当前为自动档：tile / 精度 / 后端将由引擎按实测标定结果决定，实际生效值可在任务详情中核对。' : '当前为手动档：你所填参数将被直接使用，若资源不足引擎会降档并在任务详情中显式说明。' }}
          </p>
        </SectionCard>
      </section>

      <!-- ============ 右栏：最近任务 ============ -->
      <aside v-if="isWide" class="wb-col wb-col-right">
        <SectionCard title="最近任务" :subtitle="`共 ${taskStore.tasks.length} 条 · 进行中 ${taskStore.runningTasks.length}`">
          <div v-if="taskStore.tasks.length === 0" class="wb-none">暂无任务</div>
          <div v-else class="wb-task-list">
            <TaskCard
              v-for="t in taskStore.tasks.slice(0, 6)"
              :key="t.id"
              :task="t"
              dense
              @open="openDetail"
            />
          </div>
          <template #footer>
            <button type="button" class="wb-more" @click="router.push('/tasks')">查看全部任务 →</button>
          </template>
        </SectionCard>
      </aside>
    </div>

    <!-- 窄屏抽屉：参数 -->
    <ElDrawer v-model="paramsDrawer" direction="ltr" size="320px" title="参数">
      <div class="wb-drawer-body">
        <SettingsRow label="自动档" hint="tile / 精度 / 后端由引擎决定">
          <SegmentedControl
            :model-value="autoMode ? 'auto' : 'manual'"
            :options="[
              { label: '自动', value: 'auto' },
              { label: '手动', value: 'manual' },
            ]"
            size="sm"
            @update:model-value="autoMode = $event === 'auto'"
          />
        </SettingsRow>
        <SettingsRow label="模型">
          <select v-model="form.modelId" class="wb-select">
            <option v-for="o in modelOptions" :key="o.value" :value="o.value" :disabled="o.disabled">
              {{ o.label }}
            </option>
          </select>
        </SettingsRow>
        <SettingsRow label="放大倍数">
          <SegmentedControl
            :model-value="String(form.scale)"
            :options="[
              { label: '×2', value: '2' },
              { label: '×3', value: '3' },
              { label: '×4', value: '4' },
            ]"
            size="sm"
            @update:model-value="form.scale = Number($event)"
          />
        </SettingsRow>
        <SettingsRow label="分块 tile">
          <SegmentedControl
            :model-value="String(form.tile)"
            :options="[
              { label: '256', value: '256', disabled: paramsLocked },
              { label: '384', value: '384', disabled: paramsLocked },
              { label: '512', value: '512', disabled: paramsLocked },
            ]"
            size="sm"
            @update:model-value="form.tile = Number($event)"
          />
        </SettingsRow>
        <SettingsRow label="精度">
          <SegmentedControl
            v-model="form.precision"
            :options="[
              { label: 'fp32', value: 'fp32', disabled: paramsLocked },
              { label: 'fp16', value: 'fp16', disabled: paramsLocked },
            ]"
            size="sm"
            @update:model-value="form.precision = $event as 'fp32' | 'fp16'"
          />
        </SettingsRow>
        <SettingsRow label="后端">
          <div class="wb-backends">
            <button
              v-for="b in backendOptions"
              :key="b.value"
              type="button"
              class="wb-be"
              :class="{ 'is-active': form.backend === b.value }"
              :disabled="b.disabled || paramsLocked"
              @click="form.backend = b.value as 'cpu' | 'cuda' | 'openvino' | 'tensorrt'"
            >
              {{ b.label }}
            </button>
          </div>
        </SettingsRow>
      </div>
    </ElDrawer>

    <!-- 窄/中屏抽屉：任务列表 -->
    <ElDrawer v-model="tasksDrawer" direction="rtl" size="360px" title="最近任务">
      <div class="wb-drawer-body">
        <div v-if="taskStore.tasks.length === 0" class="wb-none">暂无任务</div>
        <TaskCard v-for="t in taskStore.tasks" :key="t.id" :task="t" dense @open="(id: string) => { tasksDrawer = false; openDetail(id) }" />
      </div>
    </ElDrawer>

    <!-- 任务详情 -->
    <TaskDetailDrawer
      v-model="detailOpen"
      :task="detailTask"
      @compare="(id: string) => { detailOpen = false; router.push({ path: '/tasks', query: { compare: id } }) }"
      @cancel="(id: string) => onTaskAction({ key: 'cancel', id })"
      @retry="(id: string) => onTaskAction({ key: 'retry', id })"
      @download="(id: string) => onTaskAction({ key: 'download', id })"
      @remove="(id: string) => taskStore.removeByIds([id]).then(() => { detailOpen = false; ElMessage.success('任务已删除') })"
    />
  </div>
</template>

<style scoped>
.wb {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px 28px 40px;
}

.wb-head-actions {
  display: flex;
  gap: 8px;
}

.wb-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  appearance: none;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 11px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.wb-chip:hover {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.wb-chip-badge {
  min-width: 15px;
  height: 15px;
  padding: 0 4px;
  border-radius: var(--Scale-radius-pill);
  background: var(--Theme-primary);
  color: var(--Theme-on-primary);
  font-size: var(--font-size-11);
  line-height: 15px;
  text-align: center;
}

.wb-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.wb-grid.is-wide {
  grid-template-columns: var(--layout-left-panel-width) minmax(0, 1fr) var(--layout-right-panel-width);
}

@media (min-width: 1024px) and (max-width: 1439px) {
  .wb-grid {
    grid-template-columns: var(--layout-left-panel-width) minmax(0, 1fr);
  }
}

.wb-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

/* ---------- 上传 ---------- */
.up-drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 22px 16px;
  border: 1px dashed var(--Theme-border-strong);
  border-radius: var(--Scale-radius-inner);
  background: var(--Theme-bg-elevated);
  text-align: center;
}

.up-icon {
  color: var(--Theme-text-tertiary);
}

.up-main {
  margin: 0;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-secondary);
}

.up-link {
  color: var(--Theme-primary);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.up-input {
  display: none;
}

.up-sub {
  margin: 0;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.up-bar {
  width: 100%;
  height: 3px;
  margin-top: 6px;
  border-radius: 2px;
  background: var(--Theme-bg-hover);
  overflow: hidden;
}

.up-bar-fill {
  height: 100%;
  background: var(--Theme-primary);
  transition: width 0.2s ease;
}

.up-done {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.up-thumb {
  flex: 0 0 52px;
  width: 52px;
  height: 52px;
  object-fit: cover;
  border-radius: var(--Scale-radius-inner);
  border: 1px solid var(--Theme-border-subtle);
}

.up-done-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.up-done-name {
  font-size: var(--font-size-13);
  line-height: 18px;
  font-weight: 500;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.up-done-meta {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.up-reset {
  flex: 0 0 auto;
  appearance: none;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-tertiary);
  width: 26px;
  height: 26px;
  border-radius: var(--Scale-radius-button);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.up-reset:hover {
  color: var(--Theme-primary);
  border-color: var(--Theme-primary);
}

.wb-mono,
.up-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* ---------- 参数 ---------- */
.wb-select {
  width: 100%;
  max-width: 200px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-primary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 8px;
  outline: none;
}

.wb-select:focus {
  border-color: var(--Theme-primary);
}

/* ---- 窄栏参数行（左栏参数卡片与窄屏参数抽屉共用） ----
 * 卡体仅约 280px，SettingsRow 的「两端对齐」在此不成立：
 *   ① 各控件宽度本就不同（自动/手动 104px、256/384/512 155px、fp32/fp16 95px），
 *      贴右对齐会让控件**左边界逐行错位**，看上去是一列锯齿；
 *   ② 最宽的一行（后端 4 项合计 247px）超出可用宽度 228px，折成 3 + 1 两行，
 *      第二行只剩一个按钮，更显零散。
 * 改为「固定 label 列 + 控件左对齐」：所有控件自同一 x 起排，形成规整的纵向
 * 基线；hint 随控件左对齐，不再与下一行 label 形成对角关系。 */
.wb-params :deep(.settings-row),
.wb-drawer-body :deep(.settings-row) {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  align-items: center;
  column-gap: 10px;
}

.wb-params :deep(.settings-row__value),
.wb-drawer-body :deep(.settings-row__value) {
  align-items: flex-start;
  text-align: left;
}

/* hint 折行时按「长度均衡」断行，避免末行只剩一个字
 * （如「…由引擎按实测标定决 / 定」）。
 * 不支持该属性的浏览器退回普通折行，不影响可用性。 */
.wb-params :deep(.settings-row__hint),
.wb-drawer-body :deep(.settings-row__hint) {
  text-wrap: balance;
}

/* ---- 参数分组：只加 1 条分割线 ----
 * 6 个参数行按语义分两组：
 *   组 1（第 1–3 行）自动档 / 模型 / 放大倍数  —— 由用户直接决定
 *   组 2（第 4–6 行）分块 tile / 精度 / 后端   —— 「自动档」打开时由引擎接管
 * 线只画在组间，用来标记**语义边界**；组内仍靠留白建立层次，因此不与
 * 「用留白而非分割线制造层次」的基线（02 §32、禁止事项第 7 条）相悖。
 *
 * 为什么**不逐行**加线：6 条线会让卡片高度增加约 40%、逼出滚动；更关键的是
 * 6 条等权重的线会稀释「这 6 项里哪 3 项会被自动档接管」这个真正要传达的信息。
 * 为什么**不做卡片式**：卡体内填 padding 会压缩内容宽度（272 → 248px），
 * 使 hint 折行变多；6 个嵌套卡片也让层级过重。两者均经实测排除。 */
.wb-params :deep(.settings-row:nth-child(4)),
.wb-drawer-body :deep(.settings-row:nth-child(4)) {
  border-top: 1px solid var(--Theme-border-subtle);
  margin-top: 10px;
  padding-top: 18px;
}

/* 后端选项：固定 2×2 网格。
 * 原为 flex-wrap，4 项放不下时自然折成 3 + 1，孤行很难看；网格让四个按钮
 * 等宽、两行对齐，也把长标签（OpenVINO / TensorRT，实测 78px）稳在单元格内。 */
.wb-backends {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 5px;
  width: 100%;
}

.wb-be {
  appearance: none;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: var(--font-mono);
  font-size: var(--font-size-12);
  line-height: 16px;
  padding: 4px 6px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wb-be:hover:not(:disabled) {
  color: var(--Theme-text-primary);
  background: var(--Theme-bg-hover);
}

.wb-be.is-active {
  border-color: var(--Theme-primary);
  color: var(--Theme-primary);
  background: color-mix(in srgb, var(--Theme-primary) 12%, transparent);
}

.wb-be:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.wb-params-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-12);
  line-height: 16px;
}

.wb-foot-key {
  color: var(--Theme-text-tertiary);
}

.wb-foot-val {
  color: var(--Theme-text-primary);
  font-weight: 500;
}

.wb-foot-act {
  margin-left: auto;
  appearance: none;
  border: none;
  background: transparent;
  color: var(--Theme-link);
  font-family: inherit;
  font-size: var(--font-size-12);
  cursor: pointer;
  padding: 0;
}

.wb-foot-act:hover {
  text-decoration: underline;
}

/* ---------- 预览 ---------- */
.wb-preview {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.wb-preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.wb-preview-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-14);
  line-height: 20px;
  font-weight: 500;
  color: var(--Theme-text-primary);
  min-width: 0;
}

.wb-preview-title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wb-preview-stage {
  min-height: 300px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--Scale-radius-inner);
  background: var(--Theme-bg-elevated);
  border: 1px solid var(--Theme-border-subtle);
  overflow: hidden;
}

.wb-preview-img {
  max-width: 100%;
  max-height: 460px;
  display: block;
  object-fit: contain;
}

.wb-preview-ph {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--Theme-text-tertiary);
  text-align: center;
  padding: 40px 20px;
}

.wb-preview-ph p {
  margin: 0;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.wb-preview-ph-sub {
  font-size: var(--font-size-12) !important;
  opacity: 0.85;
}

.wb-preview-prog {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.wb-prog-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--Theme-bg-elevated);
  overflow: hidden;
}

.wb-prog-fill {
  height: 100%;
  background: var(--Theme-primary);
  transition: width 0.3s ease;
}

.wb-prog-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

/* ---------- 待提交 ---------- */
.wb-ready {
  display: flex;
  align-items: center;
  gap: 12px;
}

.wb-ready-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.wb-ready-name {
  font-size: var(--font-size-14);
  line-height: 20px;
  font-weight: 500;
  color: var(--Theme-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wb-ready-meta {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.wb-submit {
  flex: 0 0 auto;
  appearance: none;
  border: 1px solid var(--Theme-primary);
  background: var(--Theme-primary);
  color: var(--Theme-on-primary);
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  padding: 6px 18px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.wb-submit:hover:not(:disabled) {
  background: var(--Theme-primary-hover);
  border-color: var(--Theme-primary-hover);
}

.wb-submit:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.wb-ready-hint {
  margin: 10px 0 0;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

/* ---------- 最近任务 ---------- */
.wb-task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wb-none {
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-tertiary);
}

.wb-more {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--Theme-link);
  font-family: inherit;
  font-size: var(--font-size-13);
  cursor: pointer;
  padding: 0;
}

.wb-more:hover {
  text-decoration: underline;
}

.wb-drawer-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

@media (max-width: 1023px) {
  .wb {
    padding: 18px 16px 32px;
  }
}
</style>
