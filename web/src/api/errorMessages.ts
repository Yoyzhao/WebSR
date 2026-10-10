/**
 * 错误码 → 界面文案对照表。
 *
 * 对应 `docs/tech/api-contract.md` **v1.0 §2.3** 的 **20 个错误码**，以及其 §7
 * 冻结点第 3 项（"错误码全量清单 + 每种错误的界面文案对照表"，T-700 定稿 16 → 20）。
 *
 * 约定（PRD §5）：界面上只展示 message 与 suggestion；
 * **错误码与 detail 收进「查看日志」详情**，不在正文浮现。
 *
 * ⚠️ 本表的 key 集合必须与契约 §2.3 完全一致 —— 由
 *    `scripts/test-script/verify_t700_contract.py` 断言钉住。
 */
import type { ApiError } from '@/types/api'

interface ErrorCopy {
  message: string
  suggestion: string
}

export const ERROR_MESSAGES: Record<string, ErrorCopy> = {
  VALIDATION_ERROR: { message: '请求参数不完整或格式不正确', suggestion: '请检查表单中的必填项后重试' },
  UNSUPPORTED_FORMAT: {
    message: '不支持的文件格式',
    suggestion: '请改用 jpg / png / webp / bmp / tif 格式的图片',
  },
  FORMAT_MISMATCH: {
    message: '文件的实际格式与扩展名不符',
    suggestion: '请确认文件未被改名，或重新导出为正确格式',
  },
  FILE_TOO_LARGE: { message: '文件超过大小上限', suggestion: '请压缩图片或降低分辨率后重试' },
  NOT_FOUND: { message: '请求的资源不存在', suggestion: '请确认访问地址，或刷新列表后重试' },
  MODEL_NOT_FOUND: { message: '指定的模型不存在', suggestion: '请回到模型库重新选择模型' },
  MODEL_MISSING_COMPANION: {
    message: '缺少配套文件',
    suggestion: 'OpenVINO IR 需要同名 .xml，ncnn 需要同名 .param，请一并提供',
  },
  MODEL_INCOMPATIBLE: { message: '该模型与当前后端不兼容', suggestion: '请改用支持的模型，或切换目标后端' },
  MODEL_NOT_CONVERTIBLE: {
    message: '该模型不需要转换',
    suggestion: '离线转换只对 .pth / .safetensors 开放，其它格式直接加载或导入即可',
  },
  MODEL_INSUFFICIENT_VRAM: {
    message: '该模型的显存需求高于当前可用量',
    suggestion: '请改用更小的模型，或降低放大倍数',
  },
  CONVERT_ENV_MISSING: {
    message: '转换环境未安装，无法执行离线转换',
    suggestion: '请按提示安装独立的转换环境（含 PyTorch），再重试',
  },
  VRAM_INSUFFICIENT: {
    message: '显存不足：已自动降档仍无法完成',
    suggestion: '降低放大倍数，或改用 fp16 量化模型',
  },
  RAM_INSUFFICIENT: { message: '物理内存不足', suggestion: '关闭占用内存的其他程序，或降低并发度与分块尺寸' },
  TASK_NOT_FOUND: { message: '任务不存在或已被清理', suggestion: '刷新任务列表获取最新状态' },
  TASK_CANCELED: { message: '该任务已结束，无法取消', suggestion: '无需操作，列表状态已刷新' },
  TASK_ALREADY_RUNNING: { message: '已有任务正在执行', suggestion: '当前并发度为 1，请等待其完成后再提交' },
  METHOD_NOT_ALLOWED: { message: '该地址不支持此请求方法', suggestion: '请检查请求方法后重试' },
  EP_FALLBACK_DETECTED: {
    message: '执行提供器未生效，已回退到 CPU',
    suggestion: '请到硬件能力页查看 EP 验证证据，确认驱动与依赖是否正确安装',
  },
  ENGINE_BUSY: { message: '推理引擎正被占用', suggestion: '请稍后重试' },
  INTERNAL_ERROR: { message: '服务端发生未预期错误', suggestion: '请导出诊断 JSON 并查看日志定位原因' },
}

/** 未知错误码兜底 —— 绝不白屏或抛错（04 §2.6 同类原则） */
export function describeError(error: ApiError | null): ErrorCopy {
  if (!error) return { message: '', suggestion: '' }
  return (
    ERROR_MESSAGES[error.code] ?? {
      message: error.message || '发生未知错误',
      suggestion: error.suggestion || '请查看日志获取更多信息',
    }
  )
}
