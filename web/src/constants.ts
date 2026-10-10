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
 * 取值 1344 的依据：
 *   - 模型库 3 列：(1344 − 28×2 padding − 16×2 gap) ÷ 3 ≈ 419px/列，舒适；
 *   - 任务中心沿用原值，数据表格宽度不变；
 *   - 硬件页 1200 → 1344，EP 证据表更宽裕；
 *   - 设置页输入控件自带 max-width 兜底（不随容器拉长），留白集中而非拉伸。
 * 主工作台不设上限：中栏是图像预览区，宽度直接转化为可用预览面积。 */
export const PAGE_MAX_WIDTH_VALUE = 1344

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
