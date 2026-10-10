/**
 * API 类型定义 —— `docs/tech/api-contract.md` **v1.0（已冻结）** 的 TypeScript 表达。
 *
 * ⚠️ 本文件是契约的**代码侧固化**（STEP-5C 决策 D-1 / T-700 冻结）：
 *    字段名、类型、可空性**必须与契约 v1.0 逐字对齐**，Mock 与真实实现共用同一套类型。
 *    三处事实源（本文件 / `api-contract.md` / `api/openapi.json`）由
 *    `scripts/test-script/verify_t700_contract.py` **静态断言**钉住，漂移即测试失败。
 *
 * 命名风格：统一 snake_case（与 Python 侧一致，避免双层转换）。
 * 时间格式：ISO 8601 带时区偏移，服务端存 UTC，前端按 Asia/Shanghai 展示。
 */

/**
 * 任务状态（api-contract §3.1，**服务端枚举为准**）。
 *
 * `queued`      = 已落库并入队，等待调度
 * `running`     = 执行中
 * `canceling`   = 协作式取消的中间态，不是终态
 * `completed`   = 成功完成
 * `canceled`    = 已取消
 * `failed`      = 推理失败（含降档耗尽后的失败）
 * `interrupted` = 进程退出导致的中断，**不是失败**（成因在外部）
 *
 * ⚠️ v1 无 `pending`（落库即 `queued`）；⚠️ 终态用 `completed` **不是** `done`。
 */
export type TaskStatus =
  | 'queued'
  | 'running'
  | 'canceling'
  | 'completed'
  | 'canceled'
  | 'failed'
  | 'interrupted'

/** 推理阶段（api-contract §5） */
export type TaskStage = 'queued' | 'preprocessing' | 'inferencing' | 'stitching' | 'saving'

/** 产物类型（api-contract §3.3：**服务端超集**） */
export type ArtifactKind = 'input' | 'output' | 'thumb' | 'log' | 'model'

/** 模型格式（PRD §7.5 四格式路由） */
export type ModelFormat = 'onnx' | 'openvino_ir' | 'ncnn' | 'pth' | 'safetensors'

/** 后端标识（tech-arch §6.2）
 *  注：`ncnn` 属**条件支持**（Windows/Python 可用性未验证，T-210），
 *      在验证通过前只作为能力声明出现，不进入 v1 承诺。 */
export type BackendId = 'cpu' | 'cuda' | 'openvino' | 'tensorrt' | 'ncnn'

/** 统一错误体（api-contract §2.2）—— 三要素缺一不可 */
export interface ApiError {
  code: string
  /** 用户可读 */
  message: string
  /** 下一步该做什么 */
  suggestion: string
  /** 机器细节：收进「查看日志」，不在界面直接展示 */
  detail?: Record<string, unknown>
}

/** 分页响应外壳（api-contract §1 / §4.0）。
 *
 * ⚠️ **v1 未使用**：v1 列表端点一律返回**裸数组**（见 §4.0 分页裁决）。
 *    本类型是**预留**——若将来列表规模增长，改用 `Paged<T>` 并新增 `?page&page_size`。 */
export interface Paged<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface Progress {
  /** 以 item 为准（api-contract §5） */
  percent: number
  current_item: number
  total_items: number
  current_chunk: number
  total_chunks: number
}

/**
 * 请求参数快照。
 * "自动"档时 tile / precision / backend 为 null —— 由引擎决定（api-contract §3.1）。
 */
export interface TaskParams {
  scale: number
  model_id: string
  /** 自动档：tile 为 null */
  tile: number | null
  precision: 'fp32' | 'fp16' | null
  backend: BackendId | null
  /** 自动档开关 */
  auto: boolean
  /** 源文件 id 快照（序列化时提升为 Task.file_id） */
  file_id?: string
}

/**
 * 引擎实际生效值 —— "自动档"的唯一可验证输出。
 * ⚠️ 只记这里，不记 params（M2 评审定案）。
 *
 * **分层（T-804 / T-806 定稿）**：
 * - 顶层 = **决策事实**（引擎决定用什么）；
 * - `execution` = **执行事实**（实际发生了什么），刻意不拍平到顶层——
 *   "决策与事实混在一层会让人误以为决策即事实"。
 */
export interface TaskResolved {
  // ---- 决策事实 ----
  tile: number
  precision: 'fp32' | 'fp16'
  backend: string
  /** 分块重叠像素（= feather_px） */
  overlap: number
  /** 羽化宽度（= overlap） */
  feather_px: number
  /** 决策出的并发上限（调度生效属 G-06） */
  concurrency: number
  /** 是否处于保底档（未标定时的保守下界参数） */
  using_fallback: boolean
  /** 是否发生降档 */
  degraded: boolean
  /** 决策来源：标定 / 用户 / 保底档 */
  source: 'calibration' | 'user' | 'fallback'
  /** 决策理由文案 —— 会直接展示给用户，措辞规范见契约 §8 */
  reasons: string[]
  /** 降档次数与每次原因（"降级必须显式"的证据位） */
  downgrades?: Array<{ field: string; from: string | number; to: string | number; reason: string }>
  // ---- 执行事实（仅 completed 态有值）----
  execution?: TaskExecution | null
}

/** 执行事实（T-806）：任务真正跑出来什么。 */
export interface TaskExecution {
  output_width: number
  output_height: number
  source_width?: number
  source_height?: number
  tiles: number
  scale: number
  elapsed_ms: number
  sha256?: string
  size_bytes?: number
  backend?: string
}

export interface Task {
  id: string
  type: 'upscale' | 'batch_upscale' | 'face_restore' | 'video'
  status: TaskStatus
  progress: Progress
  params: TaskParams
  resolved: TaskResolved | null
  error: ApiError | null
  /** 使用中的模型（列表展示用，详情以 params.model_id 为准） */
  model_id: string
  model_name: string
  /**
   * 以下两个字段**不在服务端契约内**，是前端为渲染阶段文案附加的瞬时状态
   * （由 SSE `progress` 事件的 stage / message 驱动，见 api-contract §5）。
   * 刷新后由下一个 progress 事件重新填入，不做持久化。
   */
  stage?: string
  stage_message?: string
  /** 任务来源文件（用于列表与详情展示） */
  file_id: string
  filename: string
  /**
   * 源图缩略图 URL —— 前端为列表渲染**派生**，服务端不返回（api-contract §3.1）。
   * 由 `GET /api/files/{file_id}/content?variant=thumb` 派生（见 `client.ts::normalizeTask`），
   * 缺失时列表回退为占位图标。
   */
  source_thumb?: string
  source_width: number
  source_height: number
  output_width: number | null
  output_height: number | null
  duration_ms: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  /** 产出与中间图 */
  artifacts?: Artifact[]
  /** EP 真实性证据（详情页「执行证据」区段的唯一来源） */
  ep_evidence?: EpEvidence[]
}

export interface Artifact {
  id: string
  task_id: string
  kind: ArtifactKind
  path: string
  /** 文件名（展示用） */
  filename: string
  /** 像素尺寸（中间图可能缺失） */
  width?: number | null
  height?: number | null
  size_bytes: number
  sha256: string
  created_at: string
}

export interface ModelCapabilities {
  supports_fp16: boolean
  supports_batch: boolean
  supports_tile0: boolean
  has_tensorrt: boolean
  is_generative: boolean
  /** PRD §7.1 接缝字段 */
  num_inference_steps: number | null
  requires_prompt: boolean
}

export interface Model {
  id: string
  name: string
  /** 网络结构名（如 SPAN / SAFMN），用于卡片副标题 */
  architecture: string
  /** 简介文案 */
  description: string
  format: ModelFormat
  path: string
  sha256: string
  size_bytes: number
  params_count: number
  scale: number
  license: string
  source: 'builtin' | 'imported'
  /** 可用性门槛，**不是排序依据**（PRD v1.8：引擎不做模型推荐） */
  min_vram_mb: number
  supported_backends: BackendId[]
  capabilities: ModelCapabilities
  /** `.bin` 必须带配套文件：['.xml'] 或 ['.param'] */
  companion: string[] | null
  /** 服务端按当前档位算好的可用性 */
  available: boolean
  unavailable_reason: string | null
  /**
   * 登记态（api-contract §3.2）：`ready` | `needs_convert` | `invalid`。
   * 与 `available` **正交**：前者说"制品能不能加载"，后者说"这台机器能不能跑"。
   * 未知值显示原值 + 中性色，不抛错。
   */
  status: string
  /**
   * 离线转换可用性（T-807）：**仅** `.pth` / `.safetensors` 有值，其余为 null。
   * `available=false` 时 `reason` 给出可照做的说明。
   */
  conversion: ModelConversion | null
}

/** `.pth` / `.safetensors` → `.onnx` 的离线转换可用性（api-contract §3.2，T-807）。 */
export interface ModelConversion {
  available: boolean
  reason: string | null
}

// ---------------------------------------------------------------------------
// 可下载模型目录（api-contract §4.3 增补，T-713）
// ---------------------------------------------------------------------------

/** 目录条目 —— 全部经过开发机实测（下载→识别→转换→自检）才进白名单 */
export interface DownloadCatalogEntry {
  id: string
  name: string
  scale: number
  architecture: string
  /** 场景标签：通用照片 / 动漫 */
  style: string
  license: string
  size_bytes: number
  /** 效果优先序（1 最强）；策展序，非运行时推荐 */
  effect_rank: number
  min_vram_mb: number
  description: string
  quality_note: string
  downloaded: boolean
  downloaded_model_id: string | null
}

export interface DownloadCatalog {
  items: DownloadCatalogEntry[]
  conversion: ModelConversion
}

/** 下载作业状态（单作业，与 conversion_service 同构） */
export interface DownloadState {
  status: 'idle' | 'running' | 'completed' | 'failed'
  entry_id: string | null
  received_bytes: number
  total_bytes: number | null
  /** 登记后的源模型（.pth）id；转换产物 id 看转换状态接口 */
  model_id: string | null
  conversion_triggered: boolean
  error: { code: string; message: string; reason: string } | null
}

/** EP 真实性证据（PRD §2.5 F-06：必须有 profile 节点归属作为证据） */
export interface EpEvidence {
  /** 执行提供器名称 */
  provider: string
  /** ORT profile 中的节点归属描述 */
  node_ownership: string
  /** 该 provider 名下的节点数 */
  node_count: number
  /** 判据：目标 EP 节点数 > 0 **且** CPU 节点数 = 0 */
  cpu_node_count: number
  verified: boolean
  note: string
}

/** 已具备该接口的兼容别名（详情区段按 backend 聚合展示时使用） */
export interface EpEvidenceByBackend {
  backend: string
  node_count: number
}

export interface DeviceFacts {
  cpu: string
  gpu: string
  driver: string
  system_ram_gb: number
  /** 实读可用显存（不是标称值） */
  available_vram_gb: number
  nominal_vram_gb: number
}

export interface VerifiedBackend {
  id: BackendId
  label: string
  verified: boolean
  precision: string
  node_count: number
  cpu_node_count: number
}

export interface Capabilities {
  /** T0 纯 CPU / T1 消费级 8G / T2 高端 16–24G / T3 专业卡 */
  tier: 'T0' | 'T1' | 'T2' | 'T3'
  tier_label: string
  tier_reason: string
  device_facts: DeviceFacts
  verified_backends: VerifiedBackend[]
  ep_evidence: EpEvidence[]
  /** 保底档：标定未完成时使用保守下界参数 */
  using_fallback: boolean
  /** 当前生效后端 */
  active_backend: string
  active_precision: string
  /** 档位模拟（P3，ADR-004） */
  simulation: {
    enabled: boolean
    force_tier: string | null
  }
}

export interface Setting {
  key: string
  value: string
  type: 'string' | 'number' | 'boolean'
}

export interface SettingsPayload {
  items: Setting[]
}

/** SSE 事件（api-contract §5） */
export interface SseProgressEvent {
  task_id: string
  percent: number
  current_item: number
  total_items: number
  current_chunk: number
  total_chunks: number
  stage: TaskStage
  message: string
}

export interface SseHandlers {
  onSnapshot?: (task: Task) => void
  onProgress?: (event: SseProgressEvent) => void
  onDone?: (task: Task) => void
  onError?: (err: unknown) => void
}

export interface LogEntry {
  level: 'debug' | 'info' | 'warning' | 'error'
  timestamp: string
  message: string
  code?: string
}

/** 上传响应（api-contract §4.2） */
export interface UploadResult {
  file_id: string
  filename: string
  size: number
  width: number
  height: number
  /** 服务端二次校验得出的真实格式 —— **不信任扩展名**（PRD §3.2） */
  real_format: string
}
