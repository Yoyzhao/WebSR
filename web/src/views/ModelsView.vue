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
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Box } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import ModelCard from '@/components/ModelCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import ModelImportDialog, { type ImportPayload } from '@/components/ModelImportDialog.vue'
import ActionButtons from '@/components/ActionButtons.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { useSystemStore } from '@/stores/system'
import { deleteModel, fetchModels, importModel } from '@/api/client'
import { PAGE_MAX_WIDTH } from '@/constants'
import { toModelVM } from '@/utils/viewModel'
import type { Model } from '@/types/api'

const router = useRouter()
const system = useSystemStore()

const models = ref<Model[]>([])
const loading = ref(true)
const filter = ref<'all' | 'builtin' | 'imported'>('all')
const importOpen = ref(false)

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

onMounted(load)
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
        @use="useModel"
        @delete="onDelete"
      />
    </div>

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
</style>
