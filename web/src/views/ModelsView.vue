<script setup lang="ts">
/**
 * 模型库 —— 模型列表 + 导入（P3，docs/prototype/01-页面结构与布局.md §5）。
 *
 * 约束（PRD v1.8）：
 *   - **模型始终由用户选择，不做自动推荐**；跑不动的按当前档位置灰并说明原因。
 *   - `min_vram_mb` 是可用性门槛，不是排序依据 —— 列表**不按显存排序**、不打"推荐"标签。
 *   - 导入走两级弹窗（ModelImportDialog）：先选格式，再填信息。
 *   - max-width 1344（2026-10-08 起与其余独立页统一；3 列各约 419px）
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Box, Download } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import ModelCard from '@/components/ModelCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import ModelImportDialog, { type ImportPayload } from '@/components/ModelImportDialog.vue'
import ActionButtons from '@/components/ActionButtons.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { useSystemStore } from '@/stores/system'
import {
  deleteModel, fetchModels, importModel,
  fetchDownloadCatalog, startModelDownload, fetchDownloadStatus,
} from '@/api/client'
import { PAGE_MAX_WIDTH } from '@/constants'
import { toModelVM } from '@/utils/viewModel'
import type { Model } from '@/types/api'
import type { DownloadCatalog, DownloadCatalogEntry, DownloadState } from '@/types/api'

const router = useRouter()
const system = useSystemStore()

const models = ref<Model[]>([])
const loading = ref(true)
const filter = ref<'all' | 'builtin' | 'imported'>('all')
const importOpen = ref(false)

// ---- 可下载目录（T-713）----
const catalog = ref<DownloadCatalog | null>(null)
const dlState = ref<DownloadState | null>(null)
let dlTimer: number | null = null
let convTimer: number | null = null

const filtered = computed(() => {
  if (filter.value === 'all') return models.value
  return models.value.filter((m) => m.source === filter.value)
})

const availableCount = computed(() => models.value.filter((m) => m.available).length)
const disabledCount = computed(() => models.value.filter((m) => !m.available).length)

const vms = computed(() =>
  // 模型可用性**只**由服务端裁定（契约 §3.2 `available`），前端不再用本地显存二次判断——
  // 否则档位模拟声明更高显存时，模型仍会被错误置灰（T-901）。
  filtered.value.map((m) => toModelVM(m)),
)

async function load() {
  loading.value = true
  try {
    models.value = await fetchModels()
  } finally {
    loading.value = false
  }
}

async function loadCatalog() {
  try {
    catalog.value = await fetchDownloadCatalog()
  } catch {
    // 目录拉取失败不阻塞模型库主列表；按钮态保持默认
    catalog.value = null
  }
}

const downloadingEntry = computed(() =>
  dlState.value?.status === 'running' ? dlState.value.entry_id : null,
)

function entryProgress(entry: DownloadCatalogEntry): number | null {
  if (downloadingEntry.value !== entry.id || !dlState.value) return null
  const total = dlState.value.total_bytes ?? entry.size_bytes
  if (!total) return null
  return Math.min(100, Math.round((dlState.value.received_bytes / total) * 100))
}

function stopPolling() {
  if (dlTimer !== null) { window.clearTimeout(dlTimer); dlTimer = null }
  if (convTimer !== null) { window.clearTimeout(convTimer); convTimer = null }
}

function fmtBytes(n: number): string {
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

/** 正在转换的源模型 id（用于卡片按钮的「转换中…」态） */
const convertingIds = ref(new Set<string>())

/**
 * 手动触发转换（T-719）：此前只有**下载**路径会自动转换，手动导入的 .pth 会永远停在
 * 「待转换」且没有可做的动作。转换是异步作业（导出实测数十秒），这里只负责发起，
 * 进度交给 `pollConversion`。
 */
async function onConvertModel(id: string) {
  if (convertingIds.value.has(id)) return
  try {
    const res = await fetch(`/api/models/${encodeURIComponent(id)}/convert`, { method: 'POST' })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) {
      ElMessage.error(body?.error?.message ?? `转换未能启动（HTTP ${res.status}）`)
      return
    }
    convertingIds.value.add(id)
    ElMessage.info('转换已开始（导出实测数十秒），完成后会自动刷新模型库')
    pollConversion(id)
  } catch (e) {
    ElMessage.error((e as Error).message || '转换请求失败')
  }
}

/** 下载完成后轮询转换状态（T-807 端点），终态后刷新模型库 */
function pollConversion(modelId: string) {
  convTimer = window.setTimeout(async () => {
    try {
      const res = await fetch(`/api/models/${encodeURIComponent(modelId)}/convert`)
      const body = await res.json()
      if (body.status === 'completed' || body.status === 'failed') {
        convertingIds.value.delete(modelId)
        await Promise.all([load(), loadCatalog()])
        if (body.status === 'completed') {
          ElMessage.success(`模型「${body.result?.model_name ?? modelId}」转换完成，已加入模型库`)
        } else {
          ElMessage.error(`转换失败：${body.error?.message ?? '未知错误'}`)
        }
        return
      }
    } catch { /* 网络抖动继续轮询 */ }
    pollConversion(modelId)
  }, 1500)
}

function pollDownload(entry: DownloadCatalogEntry) {
  dlTimer = window.setTimeout(async () => {
    try {
      dlState.value = await fetchDownloadStatus()
    } catch {
      dlTimer = window.setTimeout(() => pollDownload(entry), 1500)
      return
    }
    const st = dlState.value
    if (st.status === 'running') { pollDownload(entry); return }
    if (st.status === 'failed') {
      ElMessage.error(`下载失败：${st.error?.message ?? '未知错误'}`)
      return
    }
    if (st.status === 'completed' && st.model_id) {
      if (st.conversion_triggered) {
        ElMessage.info('下载完成，正在应用内转换（导出实测数十秒）…')
        pollConversion(st.model_id)
      } else {
        await Promise.all([load(), loadCatalog()])
        ElMessage.success(`模型「${entry.name}」已登记（转换环境未安装，保持待转换状态）`)
      }
    }
  }, 800)
}

async function onDownload(entry: DownloadCatalogEntry) {
  if (entry.downloaded) {
    ElMessage.info(`「${entry.name}」已在模型库中（${entry.downloaded_model_id}）`)
    return
  }
  const mb = Math.round(entry.size_bytes / 1024 / 1024)
  try {
    await ElMessageBox.confirm(
      `将从源地址下载「${entry.name}」（约 ${mb} MB），下载后自动转换并登记。确认下载？`,
      '下载模型',
      { confirmButtonText: '下载', cancelButtonText: '取消', type: 'info' },
    )
  } catch { return }
  try {
    await startModelDownload(entry.id)
    pollDownload(entry)
  } catch (e) {
    const err = e as Error & { suggestion?: string }
    ElMessage.error(`${err.message}${err.suggestion ? '：' + err.suggestion : ''}`)
  }
}

onBeforeUnmount(stopPolling)

function useModel(id: string) {
  const m = models.value.find((x) => x.id === id)
  if (!m) return
  ElMessage.success(`已选择「${m.name}」，前往工作台即可提交任务`)
  router.push({ path: '/', query: { model: id } })
}

async function onDelete(id: string) {
  const m = models.value.find((x) => x.id === id)
  if (!m) return
  await ElMessageBox.confirm(
    `将删除模型「${m.name}」的登记记录（数据目录中的模型文件不会被删除）。确认删除？`,
    '删除模型',
    { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' },
  )
  await deleteModel(id)
  models.value = models.value.filter((x) => x.id !== id)
  ElMessage.success('已删除该模型登记')
}

async function onSubmitImport(payload: ImportPayload) {
  importOpen.value = false
  // 主文件 = 非 .bin 的那个（`.onnx` / `.xml` / `.param` / `.pth` / `.safetensors`）；
  // 配套权重 = `.bin`（仅 OpenVINO IR 与 ncnn 需要，契约 §4.3 `companion_file`）。
  const primary = payload.files.find((f) => !/\.bin$/i.test(f.name)) ?? payload.files[0]
  const companion = payload.files.find((f) => /\.bin$/i.test(f.name)) ?? null
  await importModel({
    name: payload.name,
    format: payload.format,
    scale: payload.scale,
    min_vram_mb: payload.minVramMb,
    backends: payload.backends,
    file: primary,
    companionFile: companion,
  })
  await load()
  ElMessage.success(`模型「${payload.name}」已导入`)
}

onMounted(() => {
  load()
  loadCatalog()
})
</script>

<template>
  <div class="models-page" :style="{ maxWidth: PAGE_MAX_WIDTH.models + 'px' }">
    <PageHeader title="模型库" subtitle="选择要使用的超分模型；跑不动的模型会按当前硬件档位置灰并说明原因">
      <template #actions>
        <ActionButtons
          :actions="[{ key: 'import', label: '导入模型', type: 'primary' }]"
          size="sm"
          @action="importOpen = true"
        >
          <template #icon-import>
            <el-icon :size="13"><Plus /></el-icon>
          </template>
        </ActionButtons>
      </template>
    </PageHeader>

    <!-- 档位摘要 -->
    <div class="mp-summary">
      <div class="mp-sum-item">
        <span class="mp-sum-k">当前档位</span>
        <span class="mp-sum-v">{{ system.tierBadgeText }}</span>
      </div>
      <div class="mp-sum-item">
        <span class="mp-sum-k">可用显存</span>
        <span class="mp-sum-v mp-mono">{{ system.availableVramGb ? system.availableVramGb.toFixed(1) + ' GB' : '—' }}</span>
      </div>
      <div class="mp-sum-item">
        <span class="mp-sum-k">模型</span>
        <span class="mp-sum-v mp-mono">{{ availableCount }} 可用 / {{ disabledCount }} 不可用</span>
      </div>
      <div class="mp-sum-item mp-sum-item-grow" />
      <SegmentedControl
        v-model="filter"
        :options="[
          { label: '全部', value: 'all' },
          { label: '内置', value: 'builtin' },
          { label: '导入', value: 'imported' },
        ]"
        size="sm"
      />
    </div>

    <p class="mp-note">
      模型不做自动推荐：引擎不会替你挑选，也不会按显存排序。置灰只表示「当前档位跑不动」，
      不是模型本身有问题 —— 换到更高档位的机器上即可正常使用。
    </p>

    <!-- 加载态 -->
    <div v-if="loading" class="mp-skeleton">
      <div v-for="i in 6" :key="i" class="mp-skeleton-card" />
    </div>

    <!-- 空态 -->
    <EmptyState
      v-else-if="filtered.length === 0"
      icon-size="48"
      :title="models.length === 0 ? '模型库为空' : '当前筛选下没有模型'"
      :description="models.length === 0 ? '把 .onnx / OpenVINO IR / ncnn 模型放入 data/models 目录，或点击右上角导入' : '换一个筛选条件试试'"
    >
      <template #action>
        <button v-if="models.length === 0" type="button" class="mp-empty-act" @click="importOpen = true">
          <el-icon :size="13"><Box /></el-icon>导入模型
        </button>
      </template>
    </EmptyState>

    <!-- 网格 -->
    <div v-else class="mp-grid">
      <ModelCard
        v-for="vm in vms"
        :key="vm.id"
        :model="models.find((m) => m.id === vm.id)!"
        :converting="convertingIds.has(vm.id)"
        @use="useModel"
        @delete="onDelete"
        @convert="onConvertModel"
      />
    </div>

    <!-- 可下载目录（T-713）：效果优先策展，全部经实测（识别/转换/自检）才进白名单 -->
    <section v-if="catalog && catalog.items.length" class="mp-dl">
      <header class="mp-dl-head">
        <h3 class="mp-dl-title">获取更多模型</h3>
        <p class="mp-dl-sub">
          按效果优先整理的社区模型（{{ catalog.items.length }} 项，均经实测可转换）。下载后自动应用内转换并登记，
          转换{{ catalog.conversion.available ? '环境就绪' : '环境未安装：将保持待转换状态' }}。
        </p>
      </header>
      <div class="mp-dl-grid">
        <article v-for="e in catalog.items" :key="e.id" class="mp-dl-card">
          <div class="mp-dl-top">
            <span class="mp-dl-name">{{ e.name }}</span>
            <span class="mp-dl-scale">{{ e.scale === 1 ? '×1 修复' : `×${e.scale}` }}</span>
          </div>
          <div class="mp-dl-meta">
            <span>{{ e.style }}</span><span>{{ e.architecture }}</span>
            <span>{{ Math.round(e.size_bytes / 1024 / 1024) }} MB</span>
            <span>{{ e.license }}</span>
          </div>
          <p class="mp-dl-desc">{{ e.description }}</p>
          <p class="mp-dl-note">{{ e.quality_note }}</p>
          <div class="mp-dl-foot">
            <button
              type="button"
              class="mp-dl-btn"
              :disabled="downloadingEntry !== null"
              :class="{ 'is-done': e.downloaded }"
              @click="onDownload(e)"
            >
              <el-icon :size="13"><Download /></el-icon>
              {{ e.downloaded ? '已在模型库' : downloadingEntry === e.id ? `下载中 ${entryProgress(e) ?? 0}%` : '下载' }}
            </button>
            <span v-if="downloadingEntry === e.id" class="mp-dl-progress">
              {{ fmtBytes(dlState?.received_bytes ?? 0) }} / {{ fmtBytes(e.size_bytes) }}
            </span>
          </div>
          <div v-if="downloadingEntry === e.id" class="mp-dl-bar">
            <div class="mp-dl-bar-inner" :style="{ width: (entryProgress(e) ?? 0) + '%' }" />
          </div>
        </article>
      </div>
    </section>

    <ModelImportDialog v-model="importOpen" @submit="onSubmitImport" />
  </div>
</template>

<style scoped>
.models-page {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 24px 28px 40px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

.mp-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 24px;
  padding: 12px 16px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-card);
  background: var(--Theme-bg-panel);
}

.mp-sum-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.mp-sum-item-grow {
  flex: 1;
}

.mp-sum-k {
  color: var(--Theme-text-tertiary);
}

.mp-sum-v {
  color: var(--Theme-text-primary);
  font-weight: 500;
}

.mp-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.mp-note {
  margin: -4px 0 0;
  font-size: var(--font-size-12);
  line-height: 17px;
  color: var(--Theme-text-tertiary);
}

.mp-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

@media (max-width: 1439px) {
  .mp-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1023px) {
  .models-page {
    padding: 18px 16px 32px;
  }
  .mp-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .mp-summary {
    gap: 8px 16px;
  }
}

.mp-skeleton {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

@media (max-width: 1439px) {
  .mp-skeleton {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1023px) {
  .mp-skeleton {
    grid-template-columns: minmax(0, 1fr);
  }
}

.mp-skeleton-card {
  height: 250px;
  border-radius: var(--Scale-radius-card);
  background: linear-gradient(
    90deg,
    var(--Theme-bg-elevated) 25%,
    var(--Theme-bg-hover) 50%,
    var(--Theme-bg-elevated) 75%
  );
  background-size: 200% 100%;
  animation: mp-shimmer 1.4s ease-in-out infinite;
}

@keyframes mp-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.mp-empty-act {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  appearance: none;
  border: 1px solid var(--Theme-primary);
  background: transparent;
  color: var(--Theme-primary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 12px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.mp-empty-act:hover {
  background: color-mix(in srgb, var(--Theme-primary) 12%, transparent);
}

/* ---- 可下载目录（T-713）---- */
.mp-dl {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
  padding: 16px;
  border: 1px dashed var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-card);
  background: var(--Theme-bg-panel);
}

.mp-dl-title {
  margin: 0;
  font-size: var(--font-size-14);
  line-height: 20px;
  color: var(--Theme-text-primary);
  font-weight: 600;
}

.mp-dl-sub {
  margin: 2px 0 0;
  font-size: var(--font-size-12);
  line-height: 17px;
  color: var(--Theme-text-tertiary);
}

.mp-dl-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 1023px) {
  .mp-dl-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.mp-dl-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-card);
  background: var(--Theme-bg-elevated);
}

.mp-dl-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.mp-dl-name {
  font-size: var(--font-size-13);
  line-height: 18px;
  font-weight: 600;
  color: var(--Theme-text-primary);
  font-family: var(--font-mono);
}

.mp-dl-scale {
  flex: none;
  font-size: var(--font-size-12);
  line-height: 16px;
  padding: 1px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--Theme-primary) 14%, transparent);
  color: var(--Theme-primary);
}

.mp-dl-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.mp-dl-desc {
  margin: 0;
  font-size: var(--font-size-12);
  line-height: 17px;
  color: var(--Theme-text-secondary);
}

.mp-dl-note {
  margin: 0;
  font-size: var(--font-size-12);
  line-height: 17px;
  color: var(--Theme-text-tertiary);
}

.mp-dl-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 2px;
}

.mp-dl-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  appearance: none;
  border: 1px solid var(--Theme-primary);
  background: transparent;
  color: var(--Theme-primary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 12px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.mp-dl-btn:hover:not(:disabled):not(.is-done) {
  background: color-mix(in srgb, var(--Theme-primary) 12%, transparent);
}

.mp-dl-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.mp-dl-btn.is-done {
  border-color: var(--Theme-border-subtle);
  color: var(--Theme-text-tertiary);
  cursor: default;
}

.mp-dl-progress {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.mp-dl-bar {
  height: 3px;
  border-radius: 999px;
  background: var(--Theme-bg-hover);
  overflow: hidden;
}

.mp-dl-bar-inner {
  height: 100%;
  border-radius: 999px;
  background: var(--Theme-primary);
  transition: width 0.4s ease;
}
</style>
