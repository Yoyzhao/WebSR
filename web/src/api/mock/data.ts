/**
 * Mock 数据源 —— 步骤 5C 专用，**步骤 6 联调时整体替换**。
 *
 * ⚠️ 退出边界（6-frontend-rules.md "联调切换约束"）：
 *    1. Mock 只在 `client.ts` 一处被绑定，切换真实接口改一行；
 *    2. **禁止**在运行时按请求失败回退 Mock；
 *    3. 数据结构逐字段对齐 `docs/tech/api-contract.md`，不自行增删字段。
 *
 * 数据设计意图（不是随便填的假数据）：
 *   - 任务列表覆盖**全部 7 种状态**，使六态界面表现可在一次浏览中全部看到；
 *   - 其中 1 条带**降档记录**（`resolved.downgrades` 非空）—— 这是"降级必须显式"的证据位；
 *   - 1 条带**错误详情**（`error` 非空）—— 验证错误码收进抽屉区段；
 *   - 1 条 `interrupted` —— 验证"已中断"是与失败不同的独立状态（04 §2.6）；
 *   - 模型库含**1 个不可用模型**（min_vram_mb 超当前档位）与 **1 个 ncnn 条件支持模型**。
 */
import type { Artifact, Capabilities, EpEvidence, LogEntry, Model, ModelCapabilities, Setting, Task, TaskResolved } from '@/types/api'

const now = Date.now()
const iso = (offsetMs: number): string => new Date(now + offsetMs).toISOString()

/** 通用能力声明模板 —— 四个内置模型只有少量字段不同 */
function caps(patch: Partial<ModelCapabilities> = {}): ModelCapabilities {
  return {
    supports_fp16: true,
    supports_batch: false,
    supports_tile0: false,
    has_tensorrt: false,
    is_generative: false,
    num_inference_steps: null,
    requires_prompt: false,
    ...patch,
  }
}

// ---------------------------------------------------------------------------
// 硬件能力
// ---------------------------------------------------------------------------

/** 系统级 EP 证据（硬件能力页「执行证据」表） */
const CAP_EP_EVIDENCE: EpEvidence[] = [
  {
    provider: 'CUDAExecutionProvider',
    node_ownership: '1024 节点全在 CUDA',
    node_count: 1024,
    cpu_node_count: 0,
    verified: true,
    note: '判据：目标 EP 节点数 > 0 且 CPU 节点数 = 0。节点数为 1 属整图融合的正常现象。',
  },
  {
    provider: 'OpenVINOExecutionProvider',
    node_ownership: '未注册（本项目走 OpenVINO 原生 API）',
    node_count: 0,
    cpu_node_count: 0,
    verified: false,
    note: 'ORT + OpenVINO EP 实测比 ORT CPU EP 慢约 2.5 倍，故不采用该组合。',
  },
  {
    provider: 'CPUExecutionProvider',
    node_ownership: '1024 节点全在 CPU',
    node_count: 1024,
    cpu_node_count: 1024,
    verified: true,
    note: 'CPU 路径强制 fp32，禁用 fp16。',
  },
]

/** 当前档位事实（T1 消费级 8G；显存为**实读**值而非标称值，PRD §2.3 原则 4） */
export const MOCK_CAPABILITIES: Capabilities = {
  tier: 'T1',
  tier_label: 'T1 · 消费级 8G',
  tier_reason: '检测到 1 个已验证的 GPU 后端（CUDA EP 节点数 1024 / CPU 节点数 0）',
  device_facts: {
    cpu: '12th Gen Intel Core i5-12400F · 6 核 12 线程',
    gpu: 'NVIDIA GeForce RTX 3050 OEM',
    driver: '610.88',
    system_ram_gb: 32,
    available_vram_gb: 7.4,
    nominal_vram_gb: 8,
  },
  verified_backends: [
    { id: 'cuda', label: 'CUDAExecutionProvider', verified: true, precision: 'fp16', node_count: 1024, cpu_node_count: 0 },
    { id: 'openvino', label: 'OpenVINO (原生)', verified: false, precision: 'fp32', node_count: 0, cpu_node_count: 0 },
    { id: 'cpu', label: 'CPUExecutionProvider', verified: true, precision: 'fp32', node_count: 1024, cpu_node_count: 1024 },
  ],
  ep_evidence: CAP_EP_EVIDENCE,
  using_fallback: true,
  active_backend: 'CUDAExecutionProvider',
  active_precision: 'fp16',
  simulation: { enabled: false, force_tier: null },
}

// ---------------------------------------------------------------------------
// 任务 resolved 样例
// ---------------------------------------------------------------------------

/** 标定完成后的 resolved 样例 */
const RESOLVED_NORMAL: TaskResolved = {
  tile: 512,
  precision: 'fp16',
  backend: 'CUDAExecutionProvider',
  using_fallback: false,
  degraded: false,
  reasons: [
    '已标定：tile 512 在 7.4 GB 可用显存下峰值占用 5.9 GB，位于安全水位内',
    'fp16 在 CUDA 上约为 fp32 的 2.1 倍吞吐，且精度损失不可见',
  ],
  downgrades: [],
}

/** 带降档记录的 resolved —— 抽屉区段「参数与解析」高亮用 */
const RESOLVED_DEGRADED: TaskResolved = {
  tile: 256,
  precision: 'fp16',
  backend: 'CUDAExecutionProvider',
  using_fallback: false,
  degraded: true,
  reasons: ['原定 tile 512 触发显存水位告警，已自动降档至 256'],
  downgrades: [{ field: 'tile', from: 512, to: 256, reason: '显存不足：峰值占用 7.9 GB 超过安全水位 7.1 GB' }],
}

/** 保底档样例（未标定） */
const RESOLVED_FALLBACK: TaskResolved = {
  tile: 256,
  precision: 'fp32',
  backend: 'CUDAExecutionProvider',
  using_fallback: true,
  degraded: false,
  reasons: ['尚未完成首启自标定，取保守下界参数', '标定完成后将自动切换到实测参数'],
  downgrades: [],
}

/** 任务级 EP 证据（详情抽屉「执行证据」区段） */
const TASK_EP_CUDA: EpEvidence[] = [
  {
    provider: 'CUDAExecutionProvider',
    node_ownership: '1024 节点全在 CUDA',
    node_count: 1024,
    cpu_node_count: 0,
    verified: true,
    note: '本次执行未回退到 CPU。',
  },
]

const TASK_EP_CPU: EpEvidence[] = [
  {
    provider: 'CPUExecutionProvider',
    node_ownership: '1024 节点全在 CPU',
    node_count: 1024,
    cpu_node_count: 1024,
    verified: true,
    note: '用户显式选择 CPU 后端，未做回退。',
  },
]

// ---------------------------------------------------------------------------
// 任务
// ---------------------------------------------------------------------------

export const MOCK_TASKS: Task[] = [
  {
    id: 'tsk_01J8X001',
    type: 'upscale',
    status: 'running',
    progress: { percent: 0.25, current_item: 1, total_items: 1, current_chunk: 3, total_chunks: 12 },
    params: { scale: 4, model_id: 'mdl_realesrgan_x4', tile: null, precision: null, backend: null, auto: true },
    resolved: RESOLVED_FALLBACK,
    error: null,
    model_id: 'mdl_realesrgan_x4',
    model_name: 'RealESRGAN_x4plus',
    file_id: 'file_001',
    filename: 'landscape_3840x2160.png',
    source_width: 3840,
    source_height: 2160,
    output_width: null,
    output_height: null,
    duration_ms: null,
    created_at: iso(-120_000),
    started_at: iso(-115_000),
    finished_at: null,
    stage: 'inferencing',
    stage_message: '正在推理 3 / 12 块',
    artifacts: [],
    ep_evidence: [],
  },
  {
    id: 'tsk_01J8X002',
    type: 'upscale',
    status: 'done',
    progress: { percent: 1, current_item: 1, total_items: 1, current_chunk: 12, total_chunks: 12 },
    params: { scale: 4, model_id: 'mdl_realesrgan_x4', tile: 512, precision: 'fp16', backend: 'cuda', auto: false },
    resolved: RESOLVED_NORMAL,
    error: null,
    model_id: 'mdl_realesrgan_x4',
    model_name: 'RealESRGAN_x4plus',
    file_id: 'file_002',
    filename: 'portrait_1920x1080.jpg',
    source_width: 1920,
    source_height: 1080,
    output_width: 7680,
    output_height: 4320,
    duration_ms: 12_400,
    created_at: iso(-1_800_000),
    started_at: iso(-1_795_000),
    finished_at: iso(-1_782_600),
    artifacts: [
      {
        id: 'art_001',
        task_id: 'tsk_01J8X002',
        kind: 'output',
        path: 'data/outputs/tsk_01J8X002/portrait_4x.png',
        filename: 'portrait_4x.png',
        width: 7680,
        height: 4320,
        size_bytes: 24_812_544,
        sha256: '3f9a1c7e2b4d8a06f5e3c9b1d7a2f4e6',
        created_at: iso(-1_782_600),
      },
      {
        id: 'art_002',
        task_id: 'tsk_01J8X002',
        kind: 'intermediate',
        path: 'data/outputs/tsk_01J8X002/tiles/',
        filename: 'tiles_preview.png',
        width: 1024,
        height: 576,
        size_bytes: 6_184_960,
        sha256: '8b2e4f6a1c3d5e7f9a0b2c4d6e8f0a1b',
        created_at: iso(-1_790_000),
      },
    ],
    ep_evidence: TASK_EP_CUDA,
  },
  {
    id: 'tsk_01J8X003',
    type: 'upscale',
    status: 'done',
    progress: { percent: 1, current_item: 1, total_items: 1, current_chunk: 12, total_chunks: 12 },
    params: { scale: 4, model_id: 'mdl_realesrgan_x4', tile: null, precision: null, backend: null, auto: true },
    resolved: RESOLVED_DEGRADED,
    error: null,
    model_id: 'mdl_realesrgan_x4',
    model_name: 'RealESRGAN_x4plus',
    file_id: 'file_003',
    filename: 'architecture_exterior.jpg',
    source_width: 2560,
    source_height: 1440,
    output_width: 10240,
    output_height: 5760,
    duration_ms: 21_700,
    created_at: iso(-3_600_000),
    started_at: iso(-3_595_000),
    finished_at: iso(-3_573_300),
    artifacts: [
      {
        id: 'art_003',
        task_id: 'tsk_01J8X003',
        kind: 'output',
        path: 'data/outputs/tsk_01J8X003/architecture_4x.png',
        filename: 'architecture_4x.png',
        width: 10240,
        height: 5760,
        size_bytes: 41_238_528,
        sha256: 'c4d6e8f0a2b4c6d8e0f2a4b6c8d0e2f4',
        created_at: iso(-3_573_300),
      },
    ],
    ep_evidence: TASK_EP_CUDA,
  },
  {
    id: 'tsk_01J8X004',
    type: 'upscale',
    status: 'failed',
    progress: { percent: 0.42, current_item: 1, total_items: 1, current_chunk: 5, total_chunks: 12 },
    params: { scale: 4, model_id: 'mdl_span_x4', tile: 512, precision: 'fp32', backend: 'cpu', auto: false },
    resolved: {
      tile: 256,
      precision: 'fp32',
      backend: 'CPUExecutionProvider',
      using_fallback: false,
      degraded: true,
      reasons: ['物理内存不足，自动降档 2 次后仍未完成'],
      downgrades: [
        { field: 'tile', from: 512, to: 384, reason: 'RSS 超过水位' },
        { field: 'tile', from: 384, to: 256, reason: 'RSS 仍超过水位' },
      ],
    },
    error: {
      code: 'RAM_INSUFFICIENT',
      message: '物理内存不足：已自动降档 2 次仍无法完成',
      suggestion: '关闭占用内存的其他程序，或降低并发度与分块尺寸',
      detail: { required_mb: 14_336, available_mb: 9_216, downgrade_attempts: 2 },
    },
    model_id: 'mdl_span_x4',
    model_name: 'SPAN_x4',
    file_id: 'file_004',
    filename: 'panorama_8000x2000.tif',
    source_width: 8000,
    source_height: 2000,
    output_width: null,
    output_height: null,
    duration_ms: null,
    created_at: iso(-5_400_000),
    started_at: iso(-5_395_000),
    finished_at: iso(-5_380_000),
    artifacts: [],
    ep_evidence: TASK_EP_CPU,
  },
  {
    id: 'tsk_01J8X005',
    type: 'upscale',
    status: 'interrupted',
    progress: { percent: 0.25, current_item: 1, total_items: 1, current_chunk: 3, total_chunks: 12 },
    params: { scale: 2, model_id: 'mdl_realesrgan_x4', tile: 512, precision: 'fp16', backend: 'cuda', auto: false },
    resolved: RESOLVED_NORMAL,
    error: {
      code: 'TASK_INTERRUPTED',
      message: '任务被中断：应用进程在推理过程中退出',
      suggestion: '点击重试可从头重新执行该任务',
    },
    model_id: 'mdl_realesrgan_x4',
    model_name: 'RealESRGAN_x4plus',
    file_id: 'file_005',
    filename: 'night_city_3000x2000.jpg',
    source_width: 3000,
    source_height: 2000,
    output_width: null,
    output_height: null,
    duration_ms: null,
    created_at: iso(-7_200_000),
    started_at: iso(-7_195_000),
    finished_at: null,
    artifacts: [],
    ep_evidence: TASK_EP_CUDA,
  },
  {
    id: 'tsk_01J8X006',
    type: 'upscale',
    status: 'canceled',
    progress: { percent: 0.58, current_item: 1, total_items: 1, current_chunk: 7, total_chunks: 12 },
    params: { scale: 4, model_id: 'mdl_realesrgan_x4', tile: 512, precision: 'fp16', backend: 'cuda', auto: false },
    resolved: RESOLVED_NORMAL,
    error: null,
    model_id: 'mdl_realesrgan_x4',
    model_name: 'RealESRGAN_x4plus',
    file_id: 'file_006',
    filename: 'sketch_scan_2400x1600.png',
    source_width: 2400,
    source_height: 1600,
    output_width: null,
    output_height: null,
    duration_ms: null,
    created_at: iso(-9_000_000),
    started_at: iso(-8_995_000),
    finished_at: iso(-8_970_000),
    artifacts: [],
    ep_evidence: TASK_EP_CUDA,
  },
  {
    id: 'tsk_01J8X007',
    type: 'upscale',
    status: 'canceling',
    progress: { percent: 0.71, current_item: 1, total_items: 1, current_chunk: 8, total_chunks: 12 },
    params: { scale: 2, model_id: 'mdl_span_x4', tile: 512, precision: 'fp16', backend: 'cuda', auto: false },
    resolved: RESOLVED_NORMAL,
    error: null,
    model_id: 'mdl_span_x4',
    model_name: 'SPAN_x4',
    file_id: 'file_007',
    filename: 'mural_detail_4000x3000.jpg',
    source_width: 4000,
    source_height: 3000,
    output_width: null,
    output_height: null,
    duration_ms: null,
    created_at: iso(-600_000),
    started_at: iso(-590_000),
    finished_at: null,
    stage: 'stitching',
    stage_message: '正在拼接羽化 8 / 12 块',
    artifacts: [],
    ep_evidence: TASK_EP_CUDA,
  },
]

// ---------------------------------------------------------------------------
// 模型库
// ---------------------------------------------------------------------------

export const MOCK_MODELS: Model[] = [
  {
    id: 'mdl_realesrgan_x4',
    name: 'RealESRGAN_x4plus',
    architecture: 'RRDBNet',
    description: '通用 4 倍超分，对人像、建筑、风景均有稳定表现，是内置的默认主模型。',
    format: 'onnx',
    path: 'data/models/RealESRGAN_x4.onnx',
    sha256: '5c586662…b89c033',
    size_bytes: 67_051_616,
    params_count: 16_697_987,
    scale: 4,
    license: 'BSD-3-Clause',
    source: 'builtin',
    min_vram_mb: 4096,
    supported_backends: ['cuda', 'cpu', 'openvino'],
    capabilities: caps(),
    companion: null,
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'mdl_realesrgan_x4_fp16',
    name: 'RealESRGAN_x4plus_fp16',
    architecture: 'RRDBNet',
    description: '上者的 fp16 导出，体积减半，在支持 fp16 的后端上吞吐约为 fp32 的 2 倍。',
    format: 'onnx',
    path: 'data/models/RealESRGAN_x4_fp16.onnx',
    sha256: '7d1b3f5a9c2e4d6f8a0b1c3d5e7f9a2b',
    size_bytes: 33_748_503,
    params_count: 16_697_987,
    scale: 4,
    license: 'BSD-3-Clause',
    source: 'builtin',
    min_vram_mb: 2048,
    supported_backends: ['cuda', 'cpu'],
    capabilities: caps(),
    companion: null,
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'mdl_realesrgan_ir',
    name: 'RealESRGAN_x4 (OpenVINO IR)',
    architecture: 'RRDBNet',
    description: '面向 Intel 集显 / 核显的 IR 格式，走 OpenVINO 原生 API，不经过 ORT。',
    format: 'openvino_ir',
    path: 'data/models/ir/RealESRGAN_x4_fp16.xml',
    sha256: 'a2c4e6f8b0d2c4e6f8a0b2d4c6e8f0a2',
    size_bytes: 31_850_496,
    params_count: 16_697_987,
    scale: 4,
    license: 'BSD-3-Clause',
    source: 'builtin',
    min_vram_mb: 0,
    supported_backends: ['openvino', 'cpu'],
    capabilities: caps(),
    companion: ['.xml', '.bin'],
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'mdl_realesrgan_anime_6b',
    name: 'RealESRGAN_x4plus_anime_6B',
    architecture: 'RRDBNet',
    description:
      '动漫特化 4 倍超分（RRDBNet 6 块，4.47M 参数）。针对线条与平涂色块优化，适用于动画截图与插画；处理写实照片请选通用模型。',
    format: 'onnx',
    path: 'data/models/RealESRGAN_x4plus_anime_6B.onnx',
    sha256: '27c1f885…9b3527f',
    size_bytes: 17_961_298,
    params_count: 4_467_779,
    scale: 4,
    license: 'BSD-3-Clause',
    source: 'builtin',
    min_vram_mb: 2048,
    supported_backends: ['cuda', 'cpu', 'openvino'],
    capabilities: caps(),
    companion: null,
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'mdl_realesrgan_general_x4v3',
    name: 'realesr-general-x4v3',
    architecture: 'SRVGGNetCompact',
    description:
      '轻量通用 4 倍超分（SRVGGNetCompact，1.21M 参数、4.6 MB）。体积与算力需求远低于 x4plus，面向低配或纯 CPU 场景；细节还原能力弱于 x4plus。',
    format: 'onnx',
    path: 'data/models/realesr-general-x4v3.onnx',
    sha256: 'ba3e0db2…f8f6169',
    size_bytes: 4_866_394,
    params_count: 1_213_296,
    scale: 4,
    license: 'BSD-3-Clause',
    source: 'builtin',
    min_vram_mb: 1024,
    supported_backends: ['cuda', 'cpu', 'openvino'],
    capabilities: caps(),
    companion: null,
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'mdl_span_x4',
    name: 'SPAN_x4',
    architecture: 'SPAN',
    description: '轻量高效结构，参数量仅 1.05 M，是 CPU 档上唯一可能实用的候选。',
    format: 'onnx',
    path: 'data/models/SPAN_x4.onnx',
    sha256: 'f0e2d4c6b8a0f2e4d6c8b0a2f4e6d8c0',
    size_bytes: 4_218_880,
    params_count: 1_054_720,
    scale: 4,
    license: 'Apache-2.0',
    source: 'imported',
    min_vram_mb: 1024,
    supported_backends: ['cuda', 'cpu'],
    capabilities: caps(),
    companion: null,
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'mdl_swinir_l',
    name: 'SwinIR_L_x4',
    architecture: 'SwinIR',
    description: 'Transformer 系大模型，画质上限高，但对显存要求远超当前档位。',
    format: 'onnx',
    path: 'data/models/SwinIR_L_x4.onnx',
    sha256: 'e0d2c4b6a8f0e2d4c6b8a0f2e4d6c8b0',
    size_bytes: 240_000_000,
    params_count: 60_310_000,
    scale: 4,
    license: 'Apache-2.0',
    source: 'imported',
    // 24 GB 需求 > 当前可用 7.4 GB → 置灰（PRD §2.3 原则 3：不能让用户点了才失败）
    min_vram_mb: 24_576,
    supported_backends: ['cuda'],
    capabilities: caps(),
    companion: null,
    available: false,
    unavailable_reason: '需 ≥ 24 GB 显存 · 当前可用 7.4 GB',
  },
  {
    id: 'mdl_custom_ncnn',
    name: 'CustomUpscaler_x4 (ncnn)',
    architecture: 'ncnn 自定义',
    description: '用户导入的 ncnn 模型，依赖 .param + .bin 两个配套文件。',
    format: 'ncnn',
    path: 'data/models/CustomUpscaler_x4.bin',
    sha256: 'b0a2c4e6f8d0b2a4c6e8f0a2b4d6c8e0',
    size_bytes: 12_582_912,
    params_count: 3_145_728,
    scale: 4,
    license: '未标注',
    source: 'imported',
    min_vram_mb: 1024,
    supported_backends: ['cpu', 'ncnn'],
    capabilities: caps({ supports_fp16: false }),
    companion: ['.param', '.bin'],
    available: true,
    unavailable_reason: null,
  },
]

/** 供详情抽屉按需取用的产出索引（保留兼容：新数据已内联到 task.artifacts） */
export const MOCK_ARTIFACTS: Record<string, Artifact[]> = Object.fromEntries(
  MOCK_TASKS.filter((t) => t.artifacts?.length).map((t) => [t.id, t.artifacts as Artifact[]]),
)

// ---------------------------------------------------------------------------
// 设置 / 日志 / 诊断
// ---------------------------------------------------------------------------

export const MOCK_SETTINGS: Setting[] = [
  { key: 'data_root', value: './data', type: 'string' },
  { key: 'model_dir', value: './data/models', type: 'string' },
  { key: 'task_retention_days', value: '30', type: 'number' },
  { key: 'max_upload_mb', value: '50', type: 'number' },
  { key: 'max_concurrency', value: '1', type: 'number' },
  { key: 'log_level', value: 'info', type: 'string' },
  { key: 'calibration_state', value: 'pending', type: 'string' },
  { key: 'simulation_enabled', value: 'false', type: 'boolean' },
]

export const MOCK_LOGS: Record<string, LogEntry[]> = {
  tsk_01J8X004: [
    { level: 'info', timestamp: iso(-5_395_000), message: '任务开始 · 模型 SPAN_x4 · 后端 CPUExecutionProvider' },
    { level: 'warning', timestamp: iso(-5_390_000), message: 'CPU 档瓶颈为物理内存：tile 512 时 RSS 达 3.55 GB' },
    { level: 'warning', timestamp: iso(-5_386_000), message: 'RSS 超过水位 9.0 GB，tile 512 → 384' },
    { level: 'warning', timestamp: iso(-5_383_000), message: 'RSS 仍超过水位，tile 384 → 256' },
    {
      level: 'error',
      timestamp: iso(-5_380_000),
      message: 'RAM_INSUFFICIENT 物理内存不足：已自动降档 2 次仍无法完成',
      code: 'RAM_INSUFFICIENT',
    },
  ],
  tsk_01J8X005: [
    { level: 'info', timestamp: iso(-7_195_000), message: '任务开始 · 模型 RealESRGAN_x4plus · 后端 CUDAExecutionProvider' },
    { level: 'info', timestamp: iso(-7_190_000), message: '正在推理 3 / 12 块' },
    { level: 'warning', timestamp: iso(-7_185_000), message: '进程异常退出，启动回收将任务标记为 interrupted' },
  ],
}

export const MOCK_DIAGNOSTICS = {
  exported_at: iso(0),
  tier: 'T1',
  device_facts: MOCK_CAPABILITIES.device_facts,
  ep_evidence: MOCK_CAPABILITIES.ep_evidence,
  calibration: { state: 'pending', records: [], note: '首启自标定属 S2，当前使用保底档' },
}
