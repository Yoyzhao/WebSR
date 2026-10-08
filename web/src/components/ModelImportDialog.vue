<script setup lang="ts">
/**
 * ModelImportDialog —— 模型导入弹窗（模型库）（5A 定稿 · 15 组件之一）
 *
 * 两级结构（01 §P3）：
 *   第一级：选择格式 → `.onnx` / `.openvino_ir` / `.ncnn` / `.pth` / `.safetensors`
 *           按格式给出不同的「需要哪些配套文件」提示。
 *   第二级：填写元信息（名称、倍数、最低显存、支持后端）+ 上传文件。
 *
 * 约束（ADR-003 格式路由）：
 *   - 输入格式只允许「四类入口」：`.onnx` 单文件 / `.xml + .bin` / `.param + .bin` / `.pth | .safetensors`
 *   - `.bin` 必须配套：`.xml`（OpenVINO）或 `.param`（ncnn），**按配套文件分派**，不靠扩展名猜
 *   - `.pth` / `.safetensors` 走**离线转换**（不捆绑 PyTorch）—— 界面必须明确告知
 *   - ncnn 标注「条件支持」
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElDialog, ElMessage } from 'element-plus'
import { UploadFilled, Warning } from '@element-plus/icons-vue'
import type { ModelFormat } from '@/types/api'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'submit', payload: ImportPayload): void
}>()

export interface ImportPayload {
  name: string
  format: ModelFormat
  scale: number
  minVramMb: number
  backends: string[]
  files: File[]
}

const ENTRY_FORMATS: Array<{
  value: ModelFormat
  label: string
  hint: string
  accept: string
  /** 该格式要求的文件组合，用于校验 */
  pattern: RegExp[]
  multi: boolean
  note?: string
  conditional?: boolean
}> = [
  {
    value: 'onnx',
    label: 'ONNX',
    hint: '单文件 .onnx，由本机 ORT / OpenVINO 直接加载',
    accept: '.onnx',
    pattern: [/\.onnx$/i],
    multi: false,
  },
  {
    value: 'openvino_ir',
    label: 'OpenVINO IR',
    hint: '需要 .xml 与 .bin 两个文件（.bin 权重必须同选）',
    accept: '.xml,.bin',
    pattern: [/\.xml$/i, /\.bin$/i],
    multi: true,
  },
  {
    value: 'ncnn',
    label: 'ncnn',
    hint: '需要 .param 与 .bin 两个文件',
    accept: '.param,.bin',
    pattern: [/\.param$/i, /\.bin$/i],
    multi: true,
    note: '条件支持：Windows 与 Python 环境下的可用性尚未验证，验证通过前不纳入正式支持范围',
    conditional: true,
  },
  {
    value: 'pth',
    label: 'PyTorch (.pth)',
    hint: '应用不捆绑 PyTorch，导入后需在离线环境完成一次转换',
    accept: '.pth',
    pattern: [/\.pth$/i],
    multi: false,
    note: '需离线转换：请在你自备的 Python 环境中转出 .onnx，再以 ONNX 格式导入',
  },
  {
    value: 'safetensors',
    label: 'Safetensors',
    hint: '与 .pth 同属训练态权重，需离线转换',
    accept: '.safetensors',
    pattern: [/\.safetensors$/i],
    multi: false,
    note: '需离线转换：请在你自备的 Python 环境中转出 .onnx，再以 ONNX 格式导入',
  },
]

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const step = ref<1 | 2>(1)
const picked = ref<ModelFormat | null>(null)
const files = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)

const form = reactive({
  name: '',
  scale: 2,
  minVramMb: 2048,
  backends: ['cpu'] as string[],
})

const activeFormat = computed(() => ENTRY_FORMATS.find((f) => f.value === picked.value) ?? null)

const BACKEND_OPTIONS = [
  { value: 'cpu', label: 'CPU' },
  { value: 'cuda', label: 'CUDA' },
  { value: 'openvino', label: 'OpenVINO' },
  { value: 'tensorrt', label: 'TensorRT' },
  { value: 'ncnn', label: 'ncnn（条件支持）' },
]

const fileError = ref('')

watch(picked, () => {
  files.value = []
  fileError.value = ''
  if (fileInput.value) fileInput.value.value = ''
})

watch(visible, (v) => {
  if (!v) {
    step.value = 1
    picked.value = null
    files.value = []
    fileError.value = ''
    form.name = ''
    form.scale = 2
    form.minVramMb = 2048
    form.backends = ['cpu']
  }
})

function addFiles(list: FileList | null) {
  if (!list || !activeFormat.value) return
  const fmt = activeFormat.value
  const incoming = Array.from(list)
  const merged = fmt.multi ? [...files.value, ...incoming] : incoming
  // 去重（同名同大小）
  const seen = new Set<string>()
  const dedup = merged.filter((f) => {
    const k = `${f.name}:${f.size}`
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
  files.value = dedup
  validateFiles()
}

function onDrop(e: DragEvent) {
  dragOver.value = false
  addFiles(e.dataTransfer?.files ?? null)
}

function removeFile(idx: number) {
  files.value = files.value.filter((_, i) => i !== idx)
  validateFiles()
}

function validateFiles() {
  const fmt = activeFormat.value
  if (!fmt) return
  const names = files.value.map((f) => f.name)
  const unmatched = names.filter((n) => !fmt.pattern.some((re) => re.test(n)))
  if (unmatched.length) {
    fileError.value = `文件类型不符：「${unmatched.join('、')}」不是该格式所需的文件`
    return
  }
  if (fmt.multi && files.value.length < 2) {
    fileError.value = `该格式需要 ${fmt.pattern.length} 个文件（${fmt.value === 'openvino_ir' ? '.xml + .bin' : '.param + .bin'}）`
    return
  }
  fileError.value = ''
}

const namePlaceholder = computed(() => {
  const first = files.value[0]?.name
  return first ? first.replace(/\.[^.]+$/, '') : '例如 Real-ESRGAN x4'
})

function next() {
  if (!picked.value) return
  step.value = 2
  if (!form.name && files.value[0]) {
    form.name = files.value[0].name.replace(/\.[^.]+$/, '')
  }
}

function back() {
  if (step.value === 2) step.value = 1
  else visible.value = false
}

function submit() {
  if (!picked.value) {
    ElMessage.warning('请先选择模型格式')
    step.value = 1
    return
  }
  if (files.value.length === 0) {
    ElMessage.warning('请先选择模型文件')
    return
  }
  validateFiles()
  if (fileError.value) {
    ElMessage.warning(fileError.value)
    return
  }
  if (!form.name.trim()) {
    ElMessage.warning('请填写模型名称')
    return
  }
  if (form.backends.length === 0) {
    ElMessage.warning('请至少选择一个支持后端')
    return
  }
  emit('submit', {
    name: form.name.trim(),
    format: picked.value,
    scale: Number(form.scale),
    minVramMb: Number(form.minVramMb),
    backends: [...form.backends],
    files: [...files.value],
  })
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="step === 1 ? '导入模型 · 选择格式' : '导入模型 · 填写信息'"
    width="560px"
    class="model-import-dialog"
    :close-on-click-modal="false"
  >
    <!-- 第一级：格式选择 -->
    <div v-if="step === 1" class="mi">
      <p class="mi-lead">
        输入格式决定需要哪些配套文件。应用不做自动识别 —— 请按你手上实际拥有的文件选择。
      </p>
      <ul class="mi-formats">
        <li
          v-for="f in ENTRY_FORMATS"
          :key="f.value"
          class="mi-format"
          :class="{ 'is-picked': picked === f.value }"
          role="radio"
          :aria-checked="picked === f.value"
          tabindex="0"
          @click="picked = f.value"
          @keydown.enter.prevent="picked = f.value"
          @keydown.space.prevent="picked = f.value"
        >
          <div class="mi-format-head">
            <span class="mi-format-label">{{ f.label }}</span>
            <span v-if="f.conditional" class="mi-format-flag">条件支持</span>
          </div>
          <p class="mi-format-hint">{{ f.hint }}</p>
          <p v-if="f.note" class="mi-format-note">
            <el-icon :size="12"><Warning /></el-icon>
            <span>{{ f.note }}</span>
          </p>
        </li>
      </ul>
    </div>

    <!-- 第二级：元信息 + 文件 -->
    <div v-else class="mi">
      <div class="mi-chosen">
        <span class="mi-chosen-k">已选格式</span>
        <span class="mi-chosen-v">{{ activeFormat?.label }}</span>
        <button type="button" class="mi-change" @click="step = 1">更改</button>
      </div>

      <p v-if="activeFormat?.note" class="mi-note">
        <el-icon :size="12"><Warning /></el-icon>
        <span>{{ activeFormat.note }}</span>
      </p>

      <!-- 文件区 -->
      <div
        class="mi-drop"
        :class="{ 'is-over': dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDrop"
        @click="fileInput?.click()"
      >
        <el-icon :size="24" class="mi-drop-icon"><UploadFilled /></el-icon>
        <p class="mi-drop-main">
          拖入或点击选择
          <span class="mi-mono">{{ activeFormat?.accept }}</span>
        </p>
        <p class="mi-drop-sub">
          {{ activeFormat?.multi ? '该格式需要多个文件，可一次多选' : '单文件格式' }}
        </p>
        <input
          ref="fileInput"
          type="file"
          class="mi-input"
          :accept="activeFormat?.accept"
          :multiple="activeFormat?.multi"
          @change="addFiles(($event.target as HTMLInputElement).files)"
        />
      </div>

      <ul v-if="files.length" class="mi-files">
        <li v-for="(f, i) in files" :key="`${f.name}-${i}`" class="mi-file">
          <span class="mi-file-name mi-mono" :title="f.name">{{ f.name }}</span>
          <span class="mi-file-size mi-mono">{{ (f.size / 1024 / 1024).toFixed(1) }} MB</span>
          <button type="button" class="mi-file-del" title="移除" @click="removeFile(i)">×</button>
        </li>
      </ul>

      <p v-if="fileError" class="mi-err">{{ fileError }}</p>

      <!-- 元信息 -->
      <div class="mi-grid">
        <label class="mi-field mi-field-wide">
          <span class="mi-field-k">模型名称</span>
          <input v-model="form.name" class="mi-text" type="text" :placeholder="namePlaceholder" />
        </label>

        <label class="mi-field">
          <span class="mi-field-k">放大倍数</span>
          <select v-model.number="form.scale" class="mi-select">
            <option :value="1">×1</option>
            <option :value="2">×2</option>
            <option :value="3">×3</option>
            <option :value="4">×4</option>
          </select>
        </label>

        <label class="mi-field">
          <span class="mi-field-k">最低显存 (MB)</span>
          <input v-model.number="form.minVramMb" class="mi-text mi-mono" type="number" min="0" step="512" />
        </label>
      </div>

      <div class="mi-field">
        <span class="mi-field-k">支持后端</span>
        <div class="mi-checks">
          <label v-for="b in BACKEND_OPTIONS" :key="b.value" class="mi-check">
            <input v-model="form.backends" type="checkbox" :value="b.value" />
            <span>{{ b.label }}</span>
          </label>
        </div>
        <p class="mi-field-hint">
          最低显存是<strong>可用性门槛</strong>，不是排序依据 —— 引擎不会按它推荐模型，只在你选中且跑不动时置灰说明。
        </p>
      </div>
    </div>

    <template #footer>
      <div class="mi-footer">
        <button type="button" class="mi-btn" @click="back">{{ step === 1 ? '取消' : '上一步' }}</button>
        <button v-if="step === 1" type="button" class="mi-btn mi-btn-primary" :disabled="!picked" @click="next">
          下一步
        </button>
        <button v-else type="button" class="mi-btn mi-btn-primary" @click="submit">导入</button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.mi {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.mi-lead {
  margin: 0;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-secondary);
}

.mi-formats {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mi-format {
  padding: 11px 13px;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-inner);
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mi-format:hover {
  border-color: var(--Theme-border-strong);
  background: var(--Theme-bg-hover);
}

.mi-format.is-picked {
  border-color: var(--Theme-primary);
  background: color-mix(in srgb, var(--Theme-primary) 9%, transparent);
}

.mi-format:focus-visible {
  outline: 2px solid var(--Theme-primary);
  outline-offset: 1px;
}

.mi-format-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mi-format-label {
  font-size: var(--font-size-14);
  line-height: 20px;
  font-weight: 600;
  color: var(--Theme-text-primary);
  font-family: var(--font-mono);
}

.mi-format-flag {
  font-size: var(--font-size-11);
  line-height: 15px;
  padding: 0 5px;
  border-radius: var(--Scale-radius-button);
  color: var(--Theme-warning);
  background: color-mix(in srgb, var(--Theme-warning) 14%, transparent);
}

.mi-format-hint {
  margin: 0;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-secondary);
}

.mi-format-note,
.mi-note {
  margin: 0;
  display: flex;
  align-items: flex-start;
  gap: 5px;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-warning);
}

.mi-chosen {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-13);
  line-height: 18px;
}

.mi-chosen-k {
  color: var(--Theme-text-tertiary);
}

.mi-chosen-v {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--Theme-text-primary);
}

.mi-change {
  margin-left: auto;
  appearance: none;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-12);
  line-height: 16px;
  padding: 1px 8px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
}

.mi-change:hover {
  color: var(--Theme-primary);
  border-color: var(--Theme-primary);
}

.mi-drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 22px 16px;
  border: 1px dashed var(--Theme-border-strong);
  border-radius: var(--Scale-radius-inner);
  background: var(--Theme-bg-elevated);
  cursor: pointer;
  text-align: center;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.mi-drop:hover,
.mi-drop.is-over {
  border-color: var(--Theme-primary);
  background: color-mix(in srgb, var(--Theme-primary) 7%, transparent);
}

.mi-drop-icon {
  color: var(--Theme-text-tertiary);
}

.mi-drop-main {
  margin: 0;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-secondary);
}

.mi-drop-sub {
  margin: 0;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.mi-input {
  display: none;
}

.mi-files {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mi-file {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 5px 9px;
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-bg-elevated);
}

.mi-file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--Theme-text-primary);
}

.mi-file-size {
  flex: 0 0 auto;
  color: var(--Theme-text-tertiary);
}

.mi-file-del {
  flex: 0 0 auto;
  appearance: none;
  border: none;
  background: transparent;
  color: var(--Theme-text-tertiary);
  font-size: var(--font-size-16);
  line-height: 1;
  cursor: pointer;
  padding: 0 3px;
}

.mi-file-del:hover {
  color: var(--Theme-error);
}

.mi-err {
  margin: 0;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-error);
}

.mi-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 12px;
}

.mi-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.mi-field-wide {
  grid-column: 1 / -1;
}

.mi-field-k {
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.mi-text,
.mi-select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--Theme-border-subtle);
  border-radius: var(--Scale-radius-button);
  background: var(--Theme-bg-elevated);
  color: var(--Theme-text-primary);
  font-family: inherit;
  font-size: var(--font-size-13);
  line-height: 18px;
  padding: 5px 9px;
  outline: none;
}

.mi-text:focus,
.mi-select:focus {
  border-color: var(--Theme-primary);
}

.mi-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
}

.mi-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-13);
  line-height: 18px;
  color: var(--Theme-text-secondary);
  cursor: pointer;
}

.mi-field-hint {
  margin: 2px 0 0;
  font-size: var(--font-size-12);
  line-height: 16px;
  color: var(--Theme-text-tertiary);
}

.mi-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.mi-btn {
  appearance: none;
  border: 1px solid var(--Theme-border-subtle);
  background: transparent;
  color: var(--Theme-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-14);
  line-height: 20px;
  padding: 5px 16px;
  border-radius: var(--Scale-radius-button);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.mi-btn:hover:not(:disabled) {
  background: var(--Theme-bg-hover);
  color: var(--Theme-text-primary);
}

.mi-btn-primary {
  border-color: var(--Theme-primary);
  color: var(--Theme-primary);
}

.mi-btn-primary:hover:not(:disabled) {
  background: color-mix(in srgb, var(--Theme-primary) 12%, transparent);
  color: var(--Theme-primary);
}

.mi-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.mi-mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 640px) {
  .mi-grid {
    grid-template-columns: 1fr;
  }
}
</style>
