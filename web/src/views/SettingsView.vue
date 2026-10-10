<script setup lang="ts">
/**
 * 系统配置 —— 路径 / 保留策略 / 并发 / 日志 / 主题 / 诊断导出（P5，01 §7）。
 *
 * 约束：
 *   - 主题切换是**三态**（深色 / 浅色 / 跟随系统），与顶栏按钮共用 useTheme 单一入口（02 §6）。
 *   - 配置项形式为 key-value 行（SettingsRow），不引入独立表单组件。
 *   - 危险操作（恢复默认）走二次确认。
 *   - max-width 1344（2026-10-08 起与其余独立页统一）。
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
import { PAGE_MAX_WIDTH, UPLOAD } from '@/constants'
import type { LogEntry, Setting } from '@/types/api'

const { mode, setMode } = useTheme()

const settings = ref<Setting[]>([])
const loading = ref(true)
const saving = ref(false)
const logs = ref<LogEntry[]>([])
const logsOpen = ref(false)

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

onMounted(load)
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

    <!-- 外观 -->
    <SectionCard title="外观" subtitle="主题设置会立即生效并记住选择">
      <SettingsRow label="主题模式" hint="跟随系统时会随操作系统的深浅色设置自动切换">
        <SegmentedControl
          :model-value="mode"
          :options="themeOptions"
          size="sm"
          @update:model-value="setMode($event as ThemeMode)"
        />
      </SettingsRow>
    </SectionCard>

    <!-- 路径 -->
    <SectionCard title="数据路径" subtitle="均相对于项目根目录">
      <SettingsRow label="数据根目录">
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
    </SectionCard>

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
      <SettingsRow label="最大并发任务数" hint="CPU 档建议保持 1；提高并发会成倍增加内存占用">
        <input
          class="st-input st-mono st-input-num"
          type="number"
          min="1"
          max="8"
          :value="get('max_concurrency')"
          @input="set('max_concurrency', ($event.target as HTMLInputElement).value)"
        />
      </SettingsRow>
      <SettingsRow label="日志级别">
        <SegmentedControl
          :model-value="get('log_level', 'info')"
          :options="[
            { label: 'debug', value: 'debug' },
            { label: 'info', value: 'info' },
            { label: 'warning', value: 'warning' },
            { label: 'error', value: 'error' },
          ]"
          size="sm"
          @update:model-value="set('log_level', $event)"
        />
      </SettingsRow>
      <template #footer>
        <div class="st-foot">
          <span class="st-foot-hint">
            {{ dirty ? '有未保存的改动' : '配置与已保存值一致' }}
          </span>
          <button type="button" class="st-textbtn" @click="onAction('reset')">恢复默认值</button>
        </div>
      </template>
    </SectionCard>

    <!-- 标定 -->
    <SectionCard title="参数标定" subtitle="标定结果决定「自动档」下 tile / 精度 / 后端的取值">
      <SettingsRow label="标定状态" :value="get('calibration_state', 'pending') === 'pending' ? '未完成（当前使用保底档）' : '已完成'" mono />
      <SettingsRow
        label="说明"
        value="标定按「硬件指纹 + 模型」匹配：命中的模型在自动档下使用本机实测推荐参数；未标定的模型退回保底档保守下界参数，并在任务详情中显式标注来源。"
      />
      <template #footer>
        <div class="st-foot">
          <span class="st-foot-hint">标定需要在本机实际跑一轮推理，可能耗时数十秒</span>
          <RouterLink to="/hardware" class="st-textbtn">前往硬件能力页标定 →</RouterLink>
        </div>
      </template>
    </SectionCard>

    <!-- 日志与诊断 -->
    <SectionCard title="日志与诊断">
      <SettingsRow label="查看运行日志" hint="错误码与机器细节收在此处，不在界面直接展示">
        <button type="button" class="st-textbtn" @click="onAction('logs')">打开日志</button>
      </SettingsRow>
      <SettingsRow label="导出诊断报告" hint="包含硬件事实、EP 证据与标定状态，用于问题排查">
        <button type="button" class="st-textbtn" @click="onAction('export')">导出 JSON</button>
      </SettingsRow>
    </SectionCard>

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
/* 2026-10-08 由 flex 单列改为 grid 双列。
 * 背景：页面宽度上限统一为 1344 后，若仍单列，SettingsRow 的
 *   「label 左 / 值右」两端对齐会让二者相隔近 900px，明显脱离。
 * 双列后每列约 630px，label 与值保持成组，同时不再有大片空区。 */
.st-page {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 18px;
  padding: 24px 28px 40px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

/* 页头横跨两列（子组件根元素带父级 scope id，可直接选取）；
 * el-dialog 为 fixed 定位，不参与网格。 */
.st-page > .page-header {
  grid-column: 1 / -1;
}

/* < 1280 退回单列：再窄下去每列不足 600px，行内会挤 */
@media (max-width: 1279px) {
  .st-page {
    grid-template-columns: minmax(0, 1fr);
  }
}

.st-input {
  width: 100%;
  max-width: 260px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-primary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 4px 9px;
  outline: none;
}

.st-input:focus {
  border-color: var(--Theme-primary);
}

.st-input-num {
  max-width: 110px;
}

.st-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.st-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.st-foot-hint {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.st-textbtn {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--Theme-link);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  cursor: pointer;
  padding: 0;
  text-decoration: none;
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
  font-size: var(--font-size-11);
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
    padding: 18px 16px 32px;
  }
  .st-input {
    max-width: 100%;
  }
}
</style>
