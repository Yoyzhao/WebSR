/**
 * 格式化工具。
 *
 * ⚠️ 时区：项目声明 Asia/Shanghai（project-rules §1），
 *    必须显式配置，**禁止依赖浏览器本地时区推断**（6-frontend-rules.md 步骤 5C）。
 *
 * ⚠️ 等宽数字：显存/内存容量、tile、耗时、节点数、参数量、体积、分辨率、
 *    放大倍数、进度「3 / 12 块」必须用等宽字体渲染，否则刷新时会左右跳动（02 §3）。
 */
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import timezone from 'dayjs/plugin/timezone'
import { TIMEZONE } from '@/constants'

dayjs.extend(utc)
dayjs.extend(timezone)

/** ISO 8601 → 本地时区展示 */
export function formatTime(iso: string | null, withSeconds = false): string {
  if (!iso) return '—'
  return dayjs(iso).tz(TIMEZONE).format(withSeconds ? 'MM-DD HH:mm:ss' : 'MM-DD HH:mm')
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** 参数量（模型）格式化：1234567 → "1.23 M" */
export function formatParamCount(count: number | null | undefined): string {
  if (count === null || count === undefined) return '—'
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(2)} M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)} K`
  return String(count)
}

/**
 * 任务参数摘要（列表行内展示）。
 * 形如 `×2 · tile 512 · fp16 · cuda`；「自动」档以 `自动` 代替具体值。
 * ⚠️ 不得用于参数量展示 —— 参数量请用 `formatParamCount`。
 */
export function formatParams(params: {
  scale?: number
  tile?: number | null
  precision?: string | null
  backend?: string | null
  auto?: boolean
} | null | undefined): string {
  if (!params) return '—'
  const parts: string[] = []
  if (params.scale) parts.push(`×${params.scale}`)
  if (params.auto) {
    parts.push('tile 自动', '精度自动', '后端自动')
  } else {
    if (params.tile !== null && params.tile !== undefined) parts.push(`tile ${params.tile}`)
    if (params.precision) parts.push(params.precision)
    if (params.backend) parts.push(params.backend)
  }
  return parts.join(' · ')
}

export function formatResolution(width: number | null, height: number | null): string {
  if (width === null || height === null) return '—'
  return `${width} × ${height}`
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** 补齐到 2 位，用于「3 / 12 块」这类进度（等宽字体下不会跳） */
export function pad2(value: number): string {
  return String(value).padStart(2, '0')
}
