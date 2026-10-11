/**
 * 任务视图模型（ViewModel）—— 把契约的 snake_case 原始实体转成界面可直接消费的形状。
 *
 * 设计动机（STEP-5C 决策 D-1）：
 *   - `types/api.ts` 与后端契约**逐字对齐**，不做任何格式妥协（它是联调的唯一锚点）。
 *   - 界面需要的「展示态」（格式化时间、解析后参数行、缩略图路径、状态色调…）
 *     集中在这里换算，组件只读结果，不再各自 `formatXxx(...)` 拼字符串。
 *
 * ⚠️ 禁止在组件里直接读 `task.xxx` 原始字段做展示 —— 必须经 `toTaskVM()`。
 *    否则一旦契约微调，改动会散落到所有页面。
 */
import type { Artifact, Model, Task, TaskResolved } from '@/types/api'
import { STATUS_LABEL, STATUS_TONE, backendLabel } from '@/constants'
import { formatTime, formatDuration, formatParams, formatBytes, formatResolution, formatPercent } from '@/utils/format'

export type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'error'

export interface ParamRowVM {
  label: string
  /** 请求值；null 表示「自动」 */
  requested: string | number | null
  resolved: string | number | null
  reason?: string
  usingFallback?: boolean
  mono?: boolean
}

export interface ArtifactVM {
  id: string
  kind: Artifact['kind']
  kindLabel: string
  filename: string
  sizeText: string
  resolutionText: string
  isOutput: boolean
}

export interface TaskVM {
  id: string
  filename: string
  modelName: string
  status: Task['status']
  statusLabel: string
  tone: Tone
  /** 是否处于「进行中」语义（含排队的 queued 与取消中的 canceling） */
  isRunning: boolean
  isQueued: boolean
  isDone: boolean
  isFailed: boolean
  isInterrupted: boolean
  /** 进度（0–1，已 clamp） */
  percent: number
  percentText: string
  stageText: string
  /** 降级完成 */
  degraded: boolean
  degradeReasons: string[]
  errorMessage: string
  errorSuggestion: string
  errorCode: string
  paramsText: string
  createdText: string
  finishedText: string
  durationText: string
  /** 源图信息 */
  sourceWidth: number
  sourceHeight: number
  sourceResolution: string
  /** 源图缩略图 URL（前端派生，`client.ts::normalizeTask`；缺失/加载失败时回退占位） */
  sourceThumb?: string
  outputResolution: string
  /** 产出与中间图 */
  outputs: ArtifactVM[]
  intermediates: ArtifactVM[]
  hasOutput: boolean
  /** 参数与解析差异行 */
  paramRows: ParamRowVM[]
  /** EP 证据 */
  epEvidence: Task['ep_evidence']
  resolved: TaskResolved | null
}

function toneOf(status: Task['status']): Tone {
  return (STATUS_TONE as Record<string, Tone>)[status] ?? 'neutral'
}

function artifactVM(a: Artifact): ArtifactVM {
  return {
    id: a.id,
    kind: a.kind,
    kindLabel: a.kind === 'output' ? '产出' : '中间',
    filename: a.filename,
    sizeText: formatBytes(a.size_bytes),
    resolutionText: a.width && a.height ? formatResolution(a.width, a.height) : '',
    isOutput: a.kind === 'output',
  }
}

/** 由请求值与解析值构造差异行；触发原因从 `resolved.reasons` 里按关键词摘取 */
function buildParamRows(task: Task): ParamRowVM[] {
  const p = task.params
  const r = task.resolved
  const reasons = r?.reasons ?? []
  const pick = (...keys: string[]) => reasons.find((x) => keys.some((k) => x.toLowerCase().includes(k.toLowerCase())))

  const rows: ParamRowVM[] = []

  rows.push({
    label: '加速方式',
    requested: p.auto || !p.backend ? null : backendLabel(p.backend),
    resolved: r?.backend ? backendLabel(r.backend) : null,
    usingFallback: r?.using_fallback,
    reason: pick('后端', 'EP', 'backend'),
  })

  rows.push({
    label: '精度',
    requested: p.auto || !p.precision ? null : p.precision,
    resolved: r?.precision ?? null,
    usingFallback: r?.using_fallback,
    reason: pick('精度', 'fp16', 'precision'),
  })

  rows.push({
    label: '分块大小',
    requested: p.auto || p.tile === null ? null : p.tile,
    resolved: r?.tile ?? null,
    usingFallback: r?.using_fallback,
    reason: pick('tile', '分块', 'OOM', '显存'),
    mono: true,
  })

  rows.push({
    label: '放大倍数',
    requested: p.scale,
    resolved: r?.backend ? p.scale : p.scale,
    mono: true,
  })

  return rows
}

export function toTaskVM(task: Task): TaskVM {
  const status = task.status
  const percentRaw = task.progress?.percent
  const percent = typeof percentRaw === 'number' ? Math.max(0, Math.min(1, percentRaw)) : 0
  const isRunning = status === 'running' || status === 'queued' || status === 'canceling'
  const isDone = status === 'completed'
  const isFailed = status === 'failed'
  const isInterrupted = status === 'interrupted'
  const degraded = isDone && !!task.resolved?.degraded

  const artifacts = task.artifacts ?? []
  const outputs = artifacts.filter((a) => a.kind === 'output').map(artifactVM)
  // "其余产物"：契约 v1.0 的 ArtifactKind 是服务端超集（input/output/thumb/log/model），
  // 前端不再自造 `intermediate` 值——非最终产出的部分统称"其它产物"。
  const intermediates = artifacts.filter((a) => a.kind !== 'output').map(artifactVM)

  const degradeReasons = (task.resolved?.downgrades ?? []).map(
    (d) => `${d.field}：${d.from} → ${d.to}（${d.reason}）`,
  )

  return {
    id: task.id,
    filename: task.filename,
    modelName: task.model_name || task.model_id || '—',
    status,
    statusLabel: (STATUS_LABEL as Record<string, string>)[status] ?? String(status),
    tone: toneOf(status),
    isRunning,
    isQueued: status === 'queued',
    isDone,
    isFailed,
    isInterrupted,
    percent,
    percentText: formatPercent(percent),
    stageText: task.stage_message || '',
    degraded,
    degradeReasons: degradeReasons.length ? degradeReasons : (task.resolved?.reasons ?? []),
    errorMessage: task.error?.message ?? '',
    errorSuggestion: task.error?.suggestion ?? '',
    errorCode: task.error?.code ?? '',
    paramsText: formatParams(task.params),
    createdText: formatTime(task.created_at),
    finishedText: task.finished_at ? formatTime(task.finished_at) : '',
    durationText: task.duration_ms ? formatDuration(task.duration_ms) : '',
    sourceWidth: task.source_width,
    sourceHeight: task.source_height,
    sourceResolution: formatResolution(task.source_width, task.source_height),
    sourceThumb: task.source_thumb,
    outputResolution:
      task.output_width && task.output_height ? formatResolution(task.output_width, task.output_height) : '',
    outputs,
    intermediates,
    hasOutput: outputs.length > 0,
    paramRows: buildParamRows(task),
    epEvidence: task.ep_evidence ?? [],
    resolved: task.resolved,
  }
}

export interface ModelVM {
  id: string
  name: string
  architecture: string
  format: string
  scaleText: string
  /** 适配简介原文（拼回 `descLead` + `descDetail` 即得），供 title 悬浮全文用 */
  description: string
  /** 简介的**首句**——「这是什么」的定位，是卡片上最该被看见的一句 */
  descLead: string
  /** 简介的其余部分——「适合/不适合」的补充，低优先级 */
  descDetail: string
  minVramText: string
  fileSizeText: string
  stepsText: string
  backends: string[]
  ncnnConditional: boolean
  disabled: boolean
  disabledReason: string
  removable: boolean
}

/**
 * 把适配简介拆成「定位 + 补充」两级（2026-10-11）。
 *
 * **为什么拆**：T-716 的简介统一按「定位 + 适合 + 不适合」三段撰写，但此前整段
 * 以同一字号、同色渲染成 3 行文字块 —— 与模型名称争夺注意力，用户无法一眼看出
 * 「这个模型是干什么的」。拆开后卡片层级变成 **名称 > 定位 > 补充**，
 * 名称与定位都能被扫读命中，补充信息退居次要且可折叠。
 *
 * **拆分口径**：以**首个句末标点**（。！？）为界。这与项目既有撰稿约定一致 ——
 * `builtin_catalog.py` / `download_catalog.py` 的 description 一律「定位句。」开头。
 * 分号、逗号**不**作为切分点（定位句内常有「（如 SRVGGNetCompact，1.21M 参数）」这类括注）。
 *
 * **安全回落**：无句末标点、或首句长到不像一句（超过 64 字）时，**整段作为 lead**、
 * detail 留空 —— 宁可少一级层级，也绝不截断或丢失原文。
 */
function splitDescription(desc: string): { lead: string; detail: string } {
  const text = (desc ?? '').trim()
  if (!text) return { lead: '', detail: '' }
  const m = text.match(/^([^。！？\n]{2,64})([。！？])\s*([\s\S]*)$/)
  if (!m) return { lead: text, detail: '' }
  return { lead: m[1] + m[2], detail: m[3].trim() }
}

export function toModelVM(model: Model): ModelVM {
  const requiredGb = model.min_vram_mb ? model.min_vram_mb / 1024 : 0
  let disabledReason = ''
  // **可用性只由服务端裁定**（契约 §3.2：`available` 已按「登记态 → 运行时 → 显存门槛」三级算好）。
  //
  // ⚠️ T-901 起刻意**删掉**了这里原先的"本地再兜一层显存门槛"：它用**真实**可用显存
  //    二次否定服务端结论，于是档位模拟（声明更高显存）时模型仍会被错误置灰——
  //    同一个判断写两遍，迟早会漂移。前端只渲染服务端结论与原因。
  if (model.available === false) {
    disabledReason = model.unavailable_reason || '当前硬件档位不支持该模型'
  }

  const ncnnConditional = model.supported_backends?.includes('ncnn') ?? false
  const { lead, detail } = splitDescription(model.description ?? '')

  return {
    id: model.id,
    name: model.name,
    architecture: model.architecture || '',
    format: model.format ?? '',
    // ×1 是修复类（不改变尺寸），单写「×1」会被误读成放大一档 → 标签带语义
    scaleText: model.scale === 1 ? '×1 修复' : model.scale ? `×${model.scale}` : '',
    description: model.description ?? '',
    descLead: lead,
    descDetail: detail,
    minVramText: model.min_vram_mb ? `${requiredGb.toFixed(1)} GB` : '不适用',
    fileSizeText: formatBytes(model.size_bytes),
    stepsText: String(model.capabilities?.num_inference_steps ?? 1),
    backends: model.supported_backends ?? [],
    ncnnConditional,
    disabled: !!disabledReason,
    disabledReason,
    removable: model.source === 'imported',
  }
}
