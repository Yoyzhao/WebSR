/**
 * API 客户端 —— **全部走真实后端**（步骤 6 · T-701）。
 *
 * ⚠️ 本文件**不再有任何 Mock 分支**：`USE_MOCK` 开关与 `api/mock/` 已在 T-701 移除
 *    （`fullstack-general` §前后端整合：Mock 必须删除或彻底停用，禁止保留为运行期回退）。
 *    请求失败时**只**上抛统一错误体，绝不回退到样例数据 —— 否则"真实与模拟混用"
 *    会让排障时看到的每一个结论都不可信。
 *
 * 契约依据：`docs/tech/api-contract.md` **v1.0（已冻结）**。
 * 接口路径一律相对 `/api`（开发期由 Vite `server.proxy` 转发到 127.0.0.1:8000）。
 */
import { describeError } from './errorMessages'
import type {
  Capabilities,
  LogEntry,
  Model,
  Setting,
  Task,
  TaskParams,
} from '@/types/api'

const BASE = '/api'

/** 后端统一错误体（api-contract §2.2）—— 三要素缺一不可 */
interface ApiErrorBody {
  code: string
  message: string
  suggestion: string
  detail?: Record<string, unknown>
}

/** 服务端返回的形状（`TaskOut`）与前端类型同形，但 `file_id` / `filename` 等可为 null */
type RawTask = Task

function toApiError(payload: unknown, status: number): Error {
  const err = (payload as { error?: ApiErrorBody } | null)?.error
  if (err?.code) {
    return Object.assign(new Error(err.message), {
      code: err.code,
      suggestion: err.suggestion,
      detail: err.detail,
    })
  }
  return Object.assign(new Error(`请求失败（HTTP ${status}）`), {
    code: 'INTERNAL_ERROR',
    suggestion: '请稍后重试，或到「系统配置 → 导出诊断」附带诊断文件反馈',
  })
}

async function parseError(res: Response): Promise<Error> {
  // 统一错误体解析失败时构造兜底错误，绝不抛裸错误
  let payload: unknown = null
  try {
    payload = await res.json()
  } catch {
    // 忽略：非 JSON 响应
  }
  return toApiError(payload, res.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** multipart 提交（上传 / 模型导入）—— 不设置 Content-Type，交给浏览器带 boundary */
async function requestForm<T>(path: string, form: FormData, method = 'POST'): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method, body: form })
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** 取二进制内容（下载 / 预览）—— 失败时同样解析统一错误体 */
async function requestBlob(path: string): Promise<Blob> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw await parseError(res)
  return await res.blob()
}

// ---------------------------------------------------------------------------
// 二进制内容 URL（供 <img>/<a> 直接引用，不走 fetch）
// ---------------------------------------------------------------------------

export type FileVariant = 'original' | 'thumb' | 'result'

/**
 * 文件内容 URL（api-contract §4.2）。
 *
 * - `original` / `thumb`：**按上传文件 id** 取原图与缩略图；
 * - `result`：同一张原图**最近一次成功任务**的超分结果。
 */
export function fileContentUrl(fileId: string, variant: FileVariant = 'original'): string {
  return `${BASE}/files/${encodeURIComponent(fileId)}/content?variant=${variant}`
}

// ---------------------------------------------------------------------------
// 任务（api-contract §4.1）
// ---------------------------------------------------------------------------

/** `TaskOut` 的 `file_id`/`filename` 等可为 null，补齐为前端类型要求的形状 */
export function normalizeTask(raw: RawTask): Task {
  const fileId = raw.file_id ?? ''
  return {
    ...raw,
    file_id: fileId,
    filename: raw.filename ?? '',
    source_width: raw.source_width ?? 0,
    source_height: raw.source_height ?? 0,
    // 缩略图不由服务端返回，按上传文件 id 派生（契约 §3.1「前端附加字段」）
    source_thumb: fileId ? fileContentUrl(fileId, 'thumb') : undefined,
  }
}

export async function fetchTasks(): Promise<Task[]> {
  const list = await request<RawTask[]>('/tasks')
  return list.map(normalizeTask)
}

export async function fetchTask(id: string): Promise<Task> {
  return normalizeTask(await request<RawTask>(`/tasks/${encodeURIComponent(id)}`))
}

/**
 * 提交超分任务。
 *
 * ⚠️ 不传 `params.file_id`：后端 `CreateTaskIn` 从顶层的 `file_id` 取值并**自行**
 *    写进 `params` 快照（`tasks/manager.submit`），前端重复传是多余且易漂移的。
 */
export async function createTask(fileId: string, params: TaskParams): Promise<Task> {
  const body = {
    type: 'upscale',
    file_id: fileId,
    params: {
      scale: params.scale,
      model_id: params.model_id,
      tile: params.tile,
      precision: params.precision,
      backend: params.backend,
      auto: params.auto,
    },
  }
  return normalizeTask(
    await request<RawTask>('/tasks', { method: 'POST', body: JSON.stringify(body) }),
  )
}

export async function cancelTask(id: string): Promise<Task> {
  return normalizeTask(
    await request<RawTask>(`/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  )
}

/** 任务日志（api-contract §4.1 `GET /api/tasks/{id}/logs`） */
export async function fetchLogs(taskId: string): Promise<LogEntry[]> {
  return request<LogEntry[]>(`/tasks/${encodeURIComponent(taskId)}/logs`)
}

// ---------------------------------------------------------------------------
// 文件（api-contract §4.2）
// ---------------------------------------------------------------------------

export async function uploadFile(file: File): Promise<import('@/types/api').UploadResult> {
  const form = new FormData()
  form.append('file', file)
  return requestForm('/files/upload', form)
}

/** 通过后端取真实文件内容再触发保存（结果下载走 `variant=result`） */
export async function downloadFile(fileId: string, variant: FileVariant, filename: string): Promise<void> {
  const blob = await requestBlob(`/files/${encodeURIComponent(fileId)}/content?variant=${variant}`)
  triggerDownload(blob, filename)
}

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// ---------------------------------------------------------------------------
// 模型（api-contract §4.3）
// ---------------------------------------------------------------------------

export async function fetchModels(): Promise<Model[]> {
  return request<Model[]>('/models')
}

export interface ImportModelPayload {
  name: string
  format: Model['format']
  scale: number
  min_vram_mb: number
  backends: string[]
  /** 主文件（`.onnx` / `.xml` / `.param` / `.pth` / `.safetensors`） */
  file: File
  /** 配套权重文件（`.bin`），仅 OpenVINO IR 与 ncnn 需要 */
  companionFile?: File | null
}

export async function importModel(payload: ImportModelPayload): Promise<Model> {
  const form = new FormData()
  form.append('name', payload.name)
  form.append('format', payload.format)
  form.append('scale', String(payload.scale))
  form.append('min_vram_mb', String(payload.min_vram_mb))
  // 后端按 JSON 数组字符串解析（`api/models.py::import_model`）
  form.append('backends', JSON.stringify(payload.backends))
  form.append('file', payload.file)
  if (payload.companionFile) form.append('companion_file', payload.companionFile)
  return requestForm('/models/import', form)
}

export async function deleteModel(id: string): Promise<void> {
  await request<void>(`/models/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// 系统（api-contract §4.4）
// ---------------------------------------------------------------------------

export async function fetchCapabilities(): Promise<Capabilities> {
  return request<Capabilities>('/system/capabilities')
}

/**
 * 触发标定（异步，立即返回作业号）。
 * 阶段 C / T-805 已落地：结果会按「硬件指纹 + 模型」被引擎决策层消费。
 */
export async function triggerCalibration(): Promise<{ started: boolean; task_id?: string; status?: string }> {
  return request('/system/calibrate', { method: 'POST' })
}

/** 标定记录与理由 */
export async function fetchCalibration(): Promise<{
  records: Array<Record<string, unknown>>
  recommended_tier: string | null
  reasons: string[]
  state: { status: string } & Record<string, unknown>
}> {
  return request('/system/calibration')
}

/** 导出诊断 JSON（PRD §3.4）—— 由后端生成，前端只负责保存 */
export async function exportDiagnostics(): Promise<void> {
  const blob = await requestBlob('/system/diagnostics')
  triggerDownload(blob, `websr-diagnostics-${Date.now()}.json`)
}

// ---------------------------------------------------------------------------
// 配置（api-contract §4.4）
// ---------------------------------------------------------------------------

export async function fetchSettings(): Promise<Setting[]> {
  return request<Setting[]>('/settings')
}

export async function saveSettings(items: Setting[]): Promise<Setting[]> {
  return request<Setting[]>('/settings', {
    method: 'PUT',
    body: JSON.stringify({ items }),
  })
}

export { describeError }
