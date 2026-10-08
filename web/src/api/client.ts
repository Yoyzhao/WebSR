/**
 * API 客户端 —— **Mock 与真实实现的唯一切换点**。
 *
 * 切换到真实后端（步骤 6）：把下方 `USE_MOCK` 改为 false 即可。
 * ⚠️ 禁止加入"请求失败时回退 Mock"的运行时逻辑（会造成真实与模拟数据混用）。
 */
import { describeError } from './errorMessages'
import { MOCK_CAPABILITIES, MOCK_MODELS, MOCK_SETTINGS, MOCK_TASKS } from './mock/data'
import type {
  Capabilities,
  LogEntry,
  Model,
  Setting,
  Task,
  TaskParams,
  UploadResult,
} from '@/types/api'

export const USE_MOCK = true

// ---------------------------------------------------------------------------
// 真实实现骨架（步骤 5D 完成后启用）
// ---------------------------------------------------------------------------

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    // 统一错误体（api-contract §2.2）：解析失败时构造兜底错误，绝不抛裸错误
    let payload: { error?: { code: string; message: string; suggestion: string } } = {}
    try {
      payload = await res.json()
    } catch {
      // 忽略：非 JSON 响应
    }
    const err = payload.error ?? {
      code: 'INTERNAL_ERROR',
      message: `请求失败（HTTP ${res.status}）`,
      suggestion: '请稍后重试，或查看日志定位原因',
    }
    throw Object.assign(new Error(err.message), { code: err.code, suggestion: err.suggestion, raw: err })
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

// ---------------------------------------------------------------------------
// 对外接口（Mock 与真实两套实现，签名完全一致）
// ---------------------------------------------------------------------------

const delay = (ms = 240): Promise<void> => new Promise((r) => window.setTimeout(r, ms))

/** 当前内存中的任务表（Mock 态可增删改，模拟服务端持久化） */
let taskStore: Task[] = [...MOCK_TASKS]

export async function fetchTasks(): Promise<Task[]> {
  if (!USE_MOCK) return request<Task[]>('/tasks')
  await delay()
  return [...taskStore]
}

export async function fetchTask(id: string): Promise<Task> {
  if (!USE_MOCK) return request<Task>(`/tasks/${id}`)
  await delay(120)
  const found = taskStore.find((t) => t.id === id)
  if (!found) throw Object.assign(new Error('任务不存在'), { code: 'TASK_NOT_FOUND' })
  return found
}

export async function createTask(
  fileId: string,
  filename: string,
  params: TaskParams,
  source: { width: number; height: number },
  modelName = 'Real-ESRGAN x4plus',
): Promise<Task> {
  if (!USE_MOCK) {
    return request<Task>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ type: 'upscale', file_id: fileId, params }),
    })
  }
  await delay(320)
  const now = new Date().toISOString()
  const task: Task = {
    id: `tsk_${Math.random().toString(36).slice(2, 10)}`,
    type: 'upscale',
    status: 'queued' as const,
    progress: { percent: 0, current_item: 0, total_items: 1, current_chunk: 0, total_chunks: 12 },
    params,
    resolved: {
      tile: 256,
      precision: 'fp32',
      backend: 'CUDAExecutionProvider',
      using_fallback: true,
      degraded: false,
      reasons: ['尚未完成首启自标定，取保守下界参数', '标定完成后将自动切换到实测参数'],
      downgrades: [],
    },
    error: null,
    model_id: params.model_id,
    model_name: modelName,
    file_id: fileId,
    filename,
    source_width: source.width,
    source_height: source.height,
    output_width: null,
    output_height: null,
    duration_ms: null,
    created_at: now,
    started_at: null,
    finished_at: null,
    artifacts: [],
    ep_evidence: [],
  }
  taskStore = [task, ...taskStore]
  return task
}

export async function updateTask(id: string, patch: Partial<Task>): Promise<Task> {
  const idx = taskStore.findIndex((t) => t.id === id)
  if (idx >= 0) taskStore[idx] = { ...taskStore[idx], ...patch }
  return taskStore[idx]
}

export async function cancelTask(id: string): Promise<Task> {
  if (!USE_MOCK) return request<Task>(`/tasks/${id}/cancel`, { method: 'POST' })
  await delay(200)
  return updateTask(id, { status: 'canceled', finished_at: new Date().toISOString() })
}

export async function retryTask(id: string): Promise<Task> {
  await delay(200)
  return updateTask(id, {
    status: 'running',
    progress: { percent: 0.02, current_item: 1, total_items: 1, current_chunk: 1, total_chunks: 12 },
    error: null,
    finished_at: null,
  })
}

export async function removeTasks(ids: string[]): Promise<void> {
  await delay(200)
  taskStore = taskStore.filter((t) => !ids.includes(t.id))
}

export async function uploadFile(file: File): Promise<UploadResult> {
  if (!USE_MOCK) {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/files/upload`, { method: 'POST', body: form })
    return (await res.json()) as UploadResult
  }
  await delay(700)
  // 读取真实像素尺寸 —— 让"上传后立即显示原图与真实像素尺寸"这一步是真实行为
  const dims = await readImageSize(file)
  const ext = file.name.split('.').pop()?.toLowerCase() ?? 'png'
  return {
    file_id: `file_${Math.random().toString(36).slice(2, 10)}`,
    filename: file.name,
    size: file.size,
    width: dims.width,
    height: dims.height,
    real_format: ext === 'jpeg' ? 'jpg' : ext,
  }
}

function readImageSize(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve({ width: img.naturalWidth, height: img.naturalHeight })
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      resolve({ width: 0, height: 0 })
    }
    img.src = url
  })
}

export async function fetchModels(): Promise<Model[]> {
  if (!USE_MOCK) return request<Model[]>('/models')
  await delay(420)
  return [...MOCK_MODELS]
}

export async function importModel(payload: {
  name: string
  format: Model['format']
  scale: number
  min_vram_mb: number
  backends: string[]
  /** 主文件（.onnx / .xml / .param / .pth / .safetensors） */
  file: File
  /** 配套权重文件（.bin），仅 OpenVINO IR 与 ncnn 需要 */
  companionFile?: File | null
}): Promise<Model> {
  await delay(700)
  const isCompanionFormat = payload.format === 'openvino_ir' || payload.format === 'ncnn'
  const companion = isCompanionFormat ? ['.xml', '.bin'] : null
  const totalSize = payload.file.size + (payload.companionFile?.size ?? 0)
  const model: Model = {
    id: `mdl_${Math.random().toString(36).slice(2, 10)}`,
    name: payload.name,
    architecture: payload.format,
    description: '用户导入模型',
    format: payload.format,
    path: `data/models/${payload.file.name}`,
    sha256: '—',
    size_bytes: totalSize,
    params_count: 0,
    scale: payload.scale,
    license: '未标注',
    source: 'imported',
    min_vram_mb: payload.min_vram_mb,
    supported_backends: payload.backends as Model['supported_backends'],
    capabilities: {
      supports_fp16: true,
      supports_batch: false,
      supports_tile0: false,
      has_tensorrt: false,
      is_generative: false,
      num_inference_steps: null,
      requires_prompt: false,
    },
    companion,
    available: payload.min_vram_mb <= MOCK_CAPABILITIES.device_facts.available_vram_gb * 1024,
    unavailable_reason:
      payload.min_vram_mb <= MOCK_CAPABILITIES.device_facts.available_vram_gb * 1024
        ? null
        : `需 ≥ ${(payload.min_vram_mb / 1024).toFixed(0)} GB 显存 · 当前可用 ${MOCK_CAPABILITIES.device_facts.available_vram_gb} GB`,
  }
  MOCK_MODELS.push(model)
  return model
}

/** 用上传文件生成一个可用于「上传后即预览」的本地 URL（Mock 态） */
export function localObjectUrl(file: File): string {
  return URL.createObjectURL(file)
}

export async function deleteModel(id: string): Promise<void> {
  await delay(300)
  const idx = MOCK_MODELS.findIndex((m) => m.id === id)
  if (idx >= 0) MOCK_MODELS.splice(idx, 1)
}

export async function fetchCapabilities(): Promise<Capabilities> {
  if (!USE_MOCK) return request<Capabilities>('/system/capabilities')
  await delay(500)
  return { ...MOCK_CAPABILITIES }
}

export async function triggerCalibration(): Promise<void> {
  await delay(300)
}

export async function fetchLogs(taskId: string): Promise<LogEntry[]> {
  await delay(260)
  return (
    (await import('./mock/data')).MOCK_LOGS[taskId] ?? [
      { level: 'info', timestamp: new Date().toISOString(), message: '本次任务无警告或错误记录' },
    ]
  )
}

export async function fetchSettings(): Promise<Setting[]> {
  if (!USE_MOCK) return request<Setting[]>('/settings')
  await delay(340)
  return [...MOCK_SETTINGS]
}

export async function saveSettings(items: Setting[]): Promise<Setting[]> {
  await delay(420)
  items.forEach((item) => {
    const idx = MOCK_SETTINGS.findIndex((s) => s.key === item.key)
    if (idx >= 0) MOCK_SETTINGS[idx] = item
  })
  return [...MOCK_SETTINGS]
}

/** 导出诊断 JSON（PRD §3.4） */
export async function exportDiagnostics(): Promise<void> {
  const { MOCK_DIAGNOSTICS } = await import('./mock/data')
  const blob = new Blob([JSON.stringify(MOCK_DIAGNOSTICS, null, 2)], { type: 'application/json' })
  triggerDownload(blob, `websr-diagnostics-${Date.now()}.json`)
}

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export { describeError }
