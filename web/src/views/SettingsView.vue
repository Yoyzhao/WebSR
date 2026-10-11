<script setup lang="ts">
/**
 * 系统配置 —— 路径 / 保留策略 / 并发 / 日志 / 主题 / 诊断导出（P5，01 §7）。
 *
 * 约束：
 *   - 主题切换是**三态**（深色 / 浅色 / 跟随系统），与顶栏按钮共用 useTheme 单一入口（02 §6）。
 *   - 配置项形式为 key-value 行（SettingsRow），不引入独立表单组件。
 *   - 危险操作（恢复默认）走二次确认。
 *   - 布局为**显式双列**（左：外观 / 数据与网络 / 参数标定；右：任务策略 / 日志与诊断 / 档位模拟），
 *     列内纵向堆叠、列间顶部对齐；2026-10-11 由 grid 自动排布改来，
 *     原因是各卡高度悬殊导致左右不对齐、末行右列留大片空白（详见 <style> 内注释）。
 *   - max-width 1560（与任务中心 / 模型库 / 硬件能力页统一，见 constants.ts）。
 *     控件本身带 max-width 兜底，不随容器拉长，多余宽度体现为右侧留白。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import SettingsRow from '@/components/SettingsRow.vue'
import ActionButtons from '@/components/ActionButtons.vue'
import SegmentedControl from '@/components/SegmentedControl.vue'
import { useTheme, type ThemeMode } from '@/composables/useTheme'
import { exportDiagnostics, fetchLogs, fetchSettings, fetchTasks, saveSettings } from '@/api/client'
import { useSystemStore } from '@/stores/system'
import { PAGE_MAX_WIDTH, UPLOAD } from '@/constants'
import type { LogEntry, Setting } from '@/types/api'

const { mode, setMode } = useTheme()
const system = useSystemStore()

const settings = ref<Setting[]>([])
const loading = ref(true)
const saving = ref(false)
const logs = ref<LogEntry[]>([])
const logsOpen = ref(false)

/**
 * 档位模拟（T-901）—— **开发者设置**（PRD §2.3 原则 4 要求"必须放在开发者设置中"）。
 *
 * 它是**服务端**能力：提交后档位判定、模型可用性门控、EP 候选链与任务决策输入都会按声明值走。
 * 因此这里不做任何本地推断——提交完就重新拉服务端结果。
 */
const simTier = ref('T2')
const simVram = ref('')
const simTensorrt = ref('')

function syncSimDraft() {
  simTier.value = system.simDraft.forceTier || 'T2'
  simVram.value = system.simDraft.forceVramMb
  simTensorrt.value = system.simDraft.forceTensorrt
}

async function applySimulation(enabled: boolean) {
  try {
    await system.applySimulation({
      enabled,
      forceTier: simTier.value,
      forceVramMb: simVram.value.trim(),
      forceTensorrt: simTensorrt.value,
    })
    await load() // 设置表已变（模拟四项），刷新本页展示值
    ElMessage.warning(enabled ? '档位模拟已开启（服务端生效）' : '已关闭档位模拟，恢复真实探测值')
  } catch (e) {
    ElMessage.error((e as Error).message || '档位模拟设置失败')
    syncSimDraft()
  }
}

/** 开启状态下改动任一强制项 → 立即重新提交（关闭状态下只改草稿，不动服务端） */
function changeSimField() {
  if (system.simDraft.enabled) void applySimulation(true)
}

function changeSimTier(v: string) {
  simTier.value = v
  changeSimField()
}

function changeSimTensorrt(v: string) {
  simTensorrt.value = v
  changeSimField()
}

/** 本地草稿：避免每次输入都打接口，统一由「保存」提交 */
const draft = reactive<Record<string, string>>({})

const themeOptions: Array<{ label: string; value: ThemeMode }> = [
  { label: '深色', value: 'dark' },
  { label: '浅色', value: 'light' },
  { label: '跟随系统', value: 'system' },
]

function get(key: string, fallback = ''): string {
  return draft[key] ?? settings.value.find((s) => s.key === key)?.value ?? fallback
}

function set(key: string, value: string) {
  draft[key] = value
}

const dirty = computed(() =>
  settings.value.some((s) => draft[s.key] !== undefined && draft[s.key] !== s.value),
)

async function load() {
  loading.value = true
  try {
    settings.value = await fetchSettings()
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!dirty.value) return ElMessage.info('没有需要保存的改动')
  saving.value = true
  try {
    const items: Setting[] = settings.value.map((s) => ({
      ...s,
      value: draft[s.key] ?? s.value,
    }))
    await saveSettings(items)
    settings.value = items
    ElMessage.success('配置已保存')
  } finally {
    saving.value = false
  }
}

async function resetDefaults() {
  await ElMessageBox.confirm(
    '将把所有配置项恢复为默认值（模型文件与任务记录不受影响）。确认恢复？',
    '恢复默认配置',
    { confirmButtonText: '确认恢复', cancelButtonText: '取消', type: 'warning' },
  )
  const defaults: Record<string, string> = {
    data_root: './data',
    model_dir: './data/models',
    task_retention_days: '30',
    max_upload_mb: String(UPLOAD.maxSizeMB),
    max_concurrency: '1',
    log_level: 'info',
    // 下载代理（T-714）：默认空 = 跟随环境变量
    download_proxy: '',
    // 档位模拟（T-901）：默认 = 总闸关、三项都不声明
    simulation_enabled: 'false',
    force_tier: '',
    force_vram_mb: '',
    force_has_tensorrt: '',
  }
  Object.entries(defaults).forEach(([k, v]) => set(k, v))
  ElMessage.info('已填入默认值，点击「保存配置」生效')
}

async function openLogs() {
  // 日志按任务派生（契约 §4.1 `GET /api/tasks/{id}/logs`）—— 取最近一条任务，
  // 而不是写死一个本地假 id（写死会在真实链路上永远 404）。
  const list = await fetchTasks()
  const latest = list[0]
  if (!latest) return ElMessage.info('还没有任何任务，暂无可查看的日志')
  logs.value = await fetchLogs(latest.id)
  logsOpen.value = true
}

function onAction(key: string) {
  if (key === 'save') save()
  else if (key === 'reset') resetDefaults()
  else if (key === 'logs') openLogs()
  else if (key === 'export') {
    exportDiagnostics().then(() => ElMessage.success('诊断文件已导出'))
  }
}

onMounted(async () => {
  await load()
  await system.loadSimulationDraft()
  syncSimDraft()
})
</script>

<template>
  <div class="st-page" :style="{ maxWidth: PAGE_MAX_WIDTH.settings + 'px' }">
    <PageHeader title="系统配置" subtitle="数据路径、任务策略、日志与诊断">
      <template #actions>
        <ActionButtons
          :actions="[
            { key: 'export', label: '导出诊断' },
            { key: 'save', label: '保存配置', type: 'primary', disabled: saving || !dirty },
          ]"
          size="sm"
          @action="onAction"
        >
          <template #icon-export>
            <el-icon :size="13"><Download /></el-icon>
          </template>
        </ActionButtons>
      </template>
    </PageHeader>

    <!-- ===== 左列：外观 / 数据与网络 / 参数标定 ===== -->
    <div class="st-col">
      <!-- 外观 -->
      <SectionCard title="外观" subtitle="主题设置立即生效并会被记住">
        <SettingsRow label="主题模式" hint="选择「跟随系统」时，界面会随操作系统的深浅色设置自动切换">
          <SegmentedControl
            :model-value="mode"
            :options="themeOptions"
            size="sm"
            @update:model-value="setMode($event as ThemeMode)"
          />
        </SettingsRow>
      </SectionCard>

      <!-- 数据与网络（2026-10-11 合并：路径与代理同属「数据进出」语义，
           原先各自成卡导致左列出现两张只有一两行的窄卡，视觉上零散） -->
      <SectionCard title="数据与网络" subtitle="路径均相对于项目根目录">
        <SettingsRow label="数据根目录" hint="上传原图、产出文件与运行记录的存放位置">
          <input
            class="st-input st-mono"
            type="text"
            :value="get('data_root')"
            @input="set('data_root', ($event.target as HTMLInputElement).value)"
          />
        </SettingsRow>
        <SettingsRow label="模型目录" hint="该目录只读，运行期不会写入">
          <input
            class="st-input st-mono"
            type="text"
            :value="get('model_dir')"
            @input="set('model_dir', ($event.target as HTMLInputElement).value)"
          />
        </SettingsRow>
        <SettingsRow
          label="下载代理"
          hint="用于模型下载等外部网络请求；留空表示跟随系统代理设置"
        >
          <input
            class="st-input st-mono"
            type="text"
            placeholder="例如 socks5://127.0.0.1:7890"
            :value="get('download_proxy')"
            @input="set('download_proxy', ($event.target as HTMLInputElement).value)"
          />
        </SettingsRow>
      </SectionCard>

      <!-- 参数标定 -->
      <SectionCard title="参数标定" subtitle="标定结果决定自动模式下各参数的实际取值">
        <SettingsRow
          label="标定状态"
          :value="get('calibration_state', 'pending') === 'pending' ? '未完成（当前使用保守取值）' : '已完成'"
          :tone="get('calibration_state', 'pending') === 'pending' ? 'warning' : 'success'"
        />
        <SettingsRow
          label="说明"
          value="标定结果按「硬件配置 + 模型」匹配：已标定的模型在自动模式下使用本机实测推荐参数；未标定的模型使用保守取值，并在任务详情中标注来源。"
        />
        <template #footer>
          <div class="st-foot">
            <span class="st-foot-hint">标定需要在本机实际跑一轮推理，可能耗时数十秒</span>
            <RouterLink to="/hardware" class="st-textbtn">前往硬件能力页标定 →</RouterLink>
          </div>
        </template>
      </SectionCard>
    </div>

    <!-- ===== 右列：任务策略 / 日志与诊断 / 档位模拟 ===== -->
    <div class="st-col">
      <!-- 任务策略 -->
      <SectionCard title="任务策略">
        <SettingsRow label="任务记录保留天数" hint="超期记录会被清理，产出文件不受影响">
          <input
            class="st-input st-mono st-input-num"
            type="number"
            min="1"
            :value="get('task_retention_days')"
            @input="set('task_retention_days', ($event.target as HTMLInputElement).value)"
          />
        </SettingsRow>
        <SettingsRow label="单张上传上限" hint="单位 MB">
          <input
            class="st-input st-mono st-input-num"
            type="number"
            min="1"
            :value="get('max_upload_mb')"
            @input="set('max_upload_mb', ($event.target as HTMLInputElement).value)"
          />
        </SettingsRow>
        <SettingsRow label="最大并发任务数" hint="仅 CPU 处理时建议保持 1；提高并发会成倍增加内存占用">
          <input
            class="st-input st-mono st-input-num"
            type="number"
            min="1"
            max="8"
            :value="get('max_concurrency')"
            @input="set('max_concurrency', ($event.target as HTMLInputElement).value)"
          />
        </SettingsRow>
        <SettingsRow label="日志级别" hint="仅影响运行日志的详细程度，不影响处理结果">
          <SegmentedControl
            :model-value="get('log_level', 'info')"
            :options="[
              { label: '详细', value: 'debug' },
              { label: '标准', value: 'info' },
              { label: '警告', value: 'warning' },
              { label: '仅错误', value: 'error' },
            ]"
            size="sm"
            @update:model-value="set('log_level', $event)"
          />
        </SettingsRow>
        <template #footer>
          <div class="st-foot">
            <span class="st-foot-hint" :class="{ 'is-dirty': dirty }">
              {{ dirty ? '有未保存的改动' : '配置与已保存值一致' }}
            </span>
            <button type="button" class="st-textbtn" @click="onAction('reset')">恢复默认值</button>
          </div>
        </template>
      </SectionCard>

      <!-- 日志与诊断 -->
      <SectionCard title="日志与诊断">
        <SettingsRow label="查看运行日志" hint="运行日志会记录错误详情与机器信息">
          <button type="button" class="st-textbtn" @click="onAction('logs')">打开日志</button>
        </SettingsRow>
        <SettingsRow label="导出诊断报告" hint="包含硬件信息、加速验证与标定状态，用于问题排查">
          <button type="button" class="st-textbtn" @click="onAction('export')">导出 JSON</button>
        </SettingsRow>
      </SectionCard>

      <!-- 档位模拟：在其他硬件条件下预览效果，不影响本机实测信息 -->
      <SectionCard title="档位模拟" subtitle="用于在其他硬件条件下预览效果，模拟结果不代表真实性能">
        <SettingsRow
          label="模拟开关"
          :hint="
            system.simDraft.enabled
              ? '已开启：系统按声明的档位与可用显存判断硬件能力'
              : '关闭时一切按本机实测值执行'
          "
        >
          <SegmentedControl
            :model-value="system.simDraft.enabled ? 'on' : 'off'"
            :options="[
              { label: '关闭', value: 'off' },
              { label: '开启', value: 'on' },
            ]"
            size="sm"
            @update:model-value="applySimulation($event === 'on')"
          />
        </SettingsRow>

        <SettingsRow label="目标档位" hint="T0 仅 CPU ｜ T1 入门独显 8G ｜ T2 高端独显 16–24G ｜ T3 专业显卡">
          <SegmentedControl
            :model-value="simTier"
            :options="[
              { label: 'T0', value: 'T0' },
              { label: 'T1', value: 'T1' },
              { label: 'T2', value: 'T2' },
              { label: 'T3', value: 'T3' },
            ]"
            size="sm"
            @update:model-value="changeSimTier($event)"
          />
        </SettingsRow>

        <SettingsRow label="声明可用显存" hint="填 MB（如 24576）或带 G 后缀（如 24G）；留空表示不声明">
          <input
            class="st-input st-mono"
            type="text"
            placeholder="例如 24G"
            :value="simVram"
            @input="simVram = ($event.target as HTMLInputElement).value"
            @change="changeSimField"
          />
        </SettingsRow>

        <SettingsRow label="TensorRT 支持" hint="将 TensorRT 纳入加速方式候选列表">
          <SegmentedControl
            :model-value="simTensorrt"
            :options="[
              { label: '不声明', value: '' },
              { label: '声明可用', value: 'true' },
              { label: '声明不可用', value: 'false' },
            ]"
            size="sm"
            @update:model-value="changeSimTensorrt($event)"
          />
        </SettingsRow>

        <template #footer>
          <div class="st-foot">
            <span class="st-foot-hint">
              模拟只影响能力判定，不会修改本机探测到的设备信息
            </span>
            <RouterLink to="/hardware" class="st-textbtn">查看硬件能力页 →</RouterLink>
          </div>
        </template>
      </SectionCard>
    </div>

    <!-- 日志弹窗 -->
    <el-dialog v-model="logsOpen" title="运行日志" width="640px" class="st-logs-dialog">
      <ul class="st-logs">
        <li v-for="(l, i) in logs" :key="i" class="st-log" :class="`lv-${l.level}`">
          <span class="st-log-time st-mono">{{ l.timestamp.slice(11, 19) }}</span>
          <span class="st-log-level st-mono">{{ l.level }}</span>
          <span class="st-log-msg">{{ l.message }}</span>
          <span v-if="l.code" class="st-log-code st-mono">{{ l.code }}</span>
        </li>
      </ul>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ============================================================================
 * 2026-10-11 重构：grid 自动排布 → 显式双列。
 *
 * 改前的问题：7 个 SectionCard 全部作为 grid 子项自动落位（外观|数据路径 /
 * 网络|任务策略 / 参数标定|档位模拟 / 日志与诊断|—），而各卡高度差异极大
 * （外观仅 1 行、档位模拟 4 行），于是：
 *   ① 左右两列的卡片在纵向上**互不对齐**，视觉上像两组不相干的碎片；
 *   ② 末行只剩左列一张卡，右列留出整块空白 —— 即用户反馈的「容器框太分散、
 *      与右侧不协调」。
 *
 * 改后：每列是一个 `.st-col` 纵向 flex 栈，列内卡片顶部对齐、间距一致，
 * 两列各自内部节奏相同；并按语义合并了卡片（原「数据路径」+「网络」合并为
 * 「数据与网络」，消除只有一两行的窄卡）。这样左右列的**卡片数量与内容量
 * 天然接近**，无需依赖自动排布去凑对齐。
 * ========================================================================== */
.st-page {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: var(--Scale-space-5);
  padding: var(--Scale-space-5) var(--Scale-space-6) 40px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

/* 列容器：纵向栈。卡片间距统一为 --Scale-space-4，
 * 比页面级的 24 小一档 —— 层级由「页 > 列 > 卡」递减，不跨级。 */
.st-col {
  display: flex;
  flex-direction: column;
  gap: var(--Scale-space-4);
  min-width: 0;
}

/* 页头横跨两列（子组件根元素带父级 scope id，可直接选取）；
 * el-dialog 为 fixed 定位，不参与网格。 */
.st-page > .page-header {
  grid-column: 1 / -1;
  margin: calc(var(--Scale-space-5) * -1) calc(var(--Scale-space-6) * -1) 0;
  padding-left: var(--Scale-space-6);
  padding-right: var(--Scale-space-6);
}

/* < 1280 退回单列：再窄下去每列不足 600px，行内会挤。
 * 退回时两列依次纵向排列，故列间距需额外加大，否则卡片之间会贴得过近。 */
@media (max-width: 1279px) {
  .st-page {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--Scale-space-4);
  }
  /* 单列下两列内容连续，卡片间距回到页级节奏 */
  .st-col {
    gap: var(--Scale-space-4);
  }
  .st-col + .st-col {
    margin-top: var(--Scale-space-5);
  }
}

.st-input {
  width: 100%;
  max-width: 280px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-primary);
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  padding: 5px 10px;
  outline: none;
  transition: border-color var(--motion-fast) var(--motion-ease);
}

.st-input:hover {
  border-color: var(--Theme-border-strong);
}

.st-input:focus {
  border-color: var(--Theme-primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--Theme-primary) 22%, transparent);
}

.st-input-num {
  max-width: 116px;
}

.st-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.st-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--Scale-space-3);
}

.st-foot-hint {
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-tertiary);
}

/* 有未保存改动时提示转为主色，避免用户漏看（防错优先） */
.st-foot-hint.is-dirty {
  color: var(--Theme-warning);
  font-weight: 500;
}

.st-textbtn {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--Theme-link);
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  cursor: pointer;
  padding: 0;
  text-decoration: none;
  white-space: nowrap;
}

.st-textbtn:hover {
  text-decoration: underline;
}

/* 日志 */
.st-logs {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 420px;
  overflow-y: auto;
}

.st-log {
  display: flex;
  align-items: baseline;
  gap: 9px;
  font-size: var(--font-size-12);
  line-height: 17px;
  padding: 4px 8px;
  border-radius: var(--Scale-radius-button);
}

.st-log-time {
  flex: 0 0 auto;
  color: var(--Theme-text-tertiary);
}

.st-log-level {
  flex: 0 0 auto;
  width: 52px;
  text-transform: uppercase;
  font-size: var(--font-size-12);
}

.lv-info .st-log-level {
  color: var(--Theme-link);
}
.lv-warning .st-log-level {
  color: var(--Theme-warning);
}
.lv-error .st-log-level {
  color: var(--Theme-error);
}
.lv-debug .st-log-level {
  color: var(--Theme-text-tertiary);
}

.lv-warning {
  background: color-mix(in srgb, var(--Theme-warning) 8%, transparent);
}

.lv-error {
  background: color-mix(in srgb, var(--Theme-error) 9%, transparent);
}

.st-log-msg {
  flex: 1;
  min-width: 0;
  color: var(--Theme-text-secondary);
  overflow-wrap: anywhere;
}

.st-log-code {
  flex: 0 0 auto;
  color: var(--Theme-error);
}

@media (max-width: 1023px) {
  .st-page {
    padding: var(--Scale-space-4) var(--Scale-space-4) 32px;
  }
  .st-page > .page-header {
    margin: calc(var(--Scale-space-4) * -1) calc(var(--Scale-space-4) * -1) 0;
    padding-left: var(--Scale-space-4);
    padding-right: var(--Scale-space-4);
  }
  .st-input {
    max-width: 100%;
  }
}
</style>
