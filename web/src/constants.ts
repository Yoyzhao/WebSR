/**
 * 全站常量唯一入口。
 *
 * ⚠️ 禁止在组件里散落魔法数字 —— 断点与页面宽度必须集中在单一入口
 *    （docs/prototype/05-响应式规范.md §7）。
 */

/** 三档断点（05 §1） */
export const BREAKPOINTS = {
  /** < 此值：单栏 + 组件降级 */
  compact: 1024,
  /** < 此值：两栏 + 参数面板折叠为抽屉；≥ 此值：三栏 */
  medium: 1440,
} as const

/** 统一内容宽度上限（05 §2.2）—— 超出上限时内容居中
 *
 * 2026-10-08 由「逐页差异化」统一为单一值。原本四页各不相同
 * （tasks 1344 / models 1408 / hardware 1200 / settings 880），
 * 切换页面时内容区左右边界跳变，观感割裂。
 *
 * 2026-10-11 由 1344 → 1560（用户反馈容器偏窄、需为放大后的字体留出余量）：
 *   - 模型库 3 列：(1560 − 28×2 padding − 16×2 gap) ÷ 3 ≈ 491px/列，宽松；
 *   - 任务中心沿用原口径，数据表格宽度不变（列宽为固定 px，容器变宽仅多出留白）；
 *   - 硬件页 EP 证据表更宽裕；
 *   - 设置页改双列后每列约 740px，label 与值不再分离过远。
 * ⚠️ 主工作台**不设上限**：中栏是图像预览区，宽度直接转化为可用预览面积。
 *
 * 上限的作用是「超宽屏上不让行长失控」，而非填满屏幕 —— 1560 在 2K（2560 逻辑宽）
 * 下约占 61%，仍保留可预期的左右留白。 */
export const PAGE_MAX_WIDTH_VALUE = 1560

export const PAGE_MAX_WIDTH = {
  tasks: PAGE_MAX_WIDTH_VALUE,
  models: PAGE_MAX_WIDTH_VALUE,
  hardware: PAGE_MAX_WIDTH_VALUE,
  settings: PAGE_MAX_WIDTH_VALUE,
} as const

/** 任务状态枚举（api-contract **v1.0 §3.1**，**服务端枚举为准**）
 *  ⚠️ v1 无 `pending`（落库即 `queued`）；⚠️ 终态是 `completed` **不是** `done`。 */
export const TASK_STATUS = [
  'queued',
  'running',
  'canceling',
  'completed',
  'canceled',
  'failed',
  'interrupted',
] as const

export type TaskStatus = (typeof TASK_STATUS)[number]

/** 状态 → 语义色（01 §4.2）
 *  取值收敛为 5 个语义槽位，与 tokens.css 的 --Theme-* 一一对应：
 *  neutral=text-tertiary / primary=link / success / warning / error */
export type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'error'

export const STATUS_TONE: Record<TaskStatus, Tone> = {
  queued: 'primary',
  running: 'primary',
  canceling: 'primary',
  completed: 'success',
  canceled: 'neutral',
  failed: 'error',
  // 已中断不是 error：成因是外部因素（进程退出），不是推理失败（04 §2.6）
  interrupted: 'warning',
}

/** 状态 → 界面文案（01 §4.2）
 *  ⚠️ 未知枚举的兜底由 StatusText 组件处理（显示原值 + text-tertiary），
 *     因此这里是 `Record<string, string>` 而非严格枚举映射。 */
export const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '进行中',
  canceling: '正在取消',
  completed: '已完成',
  canceled: '已取消',
  failed: '失败',
  interrupted: '已中断',
}

/** 执行后端 → 界面文案（契约 §3.2 `supported_backends` / `backend`）
 *
 *  ⚠️ 单一来源：模型库卡片、硬件页后端列表、任务详情证据表此前**各自**直出
 *     原始值，而工作台参数面板已用规范写法 —— 同一后端在两处显示不一致。
 *
 *  ⚠️ 必须同时登记**两种形态**的键（这是实测踩到的坑）：
 *     - 小写 id：`supported_backends` 用（`cuda` / `openvino` / `tensorrt` / `ncnn` / `cpu`），
 *       对应前端 `BackendId`；
 *     - ORT provider 类名：`resolved.backend` 与 `ep_evidence[].provider` 用
 *       （`CUDAExecutionProvider` 等）。
 *     只登记小写 id 时，任务中心表格会直接显示 `CUDAExecutionProvider`
 *     （E2E 实测截图确认），属内部实现名泄漏到界面。
 *     映射口径与后端 `server/app/engine/ep_verify.py::_PROVIDER_LABELS` 保持一致。 */
export const BACKEND_LABEL: Record<string, string> = {
  // 小写 id
  cuda: 'CUDA',
  openvino: 'OpenVINO',
  tensorrt: 'TensorRT',
  ncnn: 'ncnn',
  cpu: 'CPU',
  // ORT provider 类名
  CUDAExecutionProvider: 'CUDA',
  TensorrtExecutionProvider: 'TensorRT',
  OpenVINOExecutionProvider: 'OpenVINO',
  CPUExecutionProvider: 'CPU',
  // 后端 `verified_backends[].label` 直接给出的形态（ep_verify._PROVIDER_LABELS）
  'OpenVINO EP': 'OpenVINO',
}

/** 取后端显示名；未登记时回退为原值 */
export function backendLabel(id: string): string {
  return BACKEND_LABEL[id] ?? id
}

/** 未随版本发布的后端（当前仅 ncnn）—— 展示时须附「实验性」标注。
 *  同时登记 id 与 provider 类名两种形态，理由同 BACKEND_LABEL。 */
export const BACKEND_EXPERIMENTAL = new Set(['ncnn', 'NcnnExecutionProvider'])

/* ============================================================================
 * 尚未开放的能力 —— 界面统一文案（单一来源）
 *
 * ⚠️ 为什么集中在这里：重试 / 删除任务 / 清理任务三处入口分散在
 *    `TaskCard` / `TaskDetailDrawer` / `TasksView` / `WorkbenchView` 四个文件，
 *    此前各写一份「属 S2 阶段，尚未实现」，共 6 处。
 *    既重复，又把内部阶段代号（S2 / F-12）暴露给了用户 —— 用户只关心
 *    「现在能不能用、为什么不能」，不需要知道排期代号。
 *
 * 写法约定（04 §5 危险操作保护）：禁用态**必须写明原因**，
 *    但只说「当前版本未提供」，不承诺时间、不暴露内部排期。
 * ========================================================================== */
export const UNAVAILABLE_COPY = {
  /** 失败任务的重试入口 */
  retry: '当前版本未提供重试功能，可重新提交任务',
  /** 单个任务的删除入口 */
  removeTask: '当前版本未提供任务删除功能',
  /** 批量清理已完成任务 */
  clearTasks: '当前版本未提供任务清理功能',
} as const

/** 推理阶段 → 文案前缀（api-contract §5） */
export const STAGE_LABEL: Record<string, string> = {
  queued: '排队中',
  preprocessing: '正在预处理',
  inferencing: '正在推理',
  stitching: '正在拼接羽化',
  saving: '正在保存',
}

/** 上传白名单（04 §6） */
export const UPLOAD = {
  extensions: ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'tif', 'tiff'] as string[],
  /** 小于此值用 KB 展示等（保留扩展位） */
  maxSizeMB: 50,
  /** `<input accept>` 属性用的选择器串 */
  accept: '.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff',
} as const

/** 时长格式化基准：项目时区固定 Asia/Shanghai（project-rules §1） */
export const TIMEZONE = 'Asia/Shanghai'
