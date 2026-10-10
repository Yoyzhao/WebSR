/**
 * SSE 进度订阅 —— **真实 `EventSource`**（步骤 6 · T-701）。
 *
 * 契约依据：`docs/tech/api-contract.md` §5（**已冻结**）。
 *
 * 四类事件（服务端定义，前端只消费）：
 *   - `snapshot`：连接建立**或断线重连**后立即推送一次完整 Task —— 恢复不依赖本地推算；
 *   - `progress`：节流 ≤ 1 s 的进度帧（`percent` **以 item 为准**，单调不减）；
 *   - `done`     ：终态（completed / canceled / failed / interrupted）后服务端关闭连接；
 *   - `ping`     ：心跳（15 s），仅用于保活，无业务含义。
 *
 * ⚠️ 三条「不做」（契约 §5.1~5.3 已定值，改这里前先改契约）：
 *   1. **不发 `retry:`、不自行实现更激进的重连** —— 用浏览器默认退避（≈3 s）；
 *   2. **不支持 `Last-Event-ID`** —— SSE 是状态快照流而非事务流，重连即由 `snapshot` 全量恢复；
 *   3. **不本地推算进度** —— 只渲染服务端推来的 `percent`。
 */
import { normalizeTask } from './client'
import type { SseHandlers, SseProgressEvent, Task } from '@/types/api'

const BASE = '/api'

interface SnapshotPayload {
  task: Task
}

interface DonePayload {
  task: Task
}

export function subscribeTaskProgress(taskId: string, handlers: SseHandlers): () => void {
  const es = new EventSource(`${BASE}/tasks/${encodeURIComponent(taskId)}/events`)

  es.addEventListener('snapshot', (ev: MessageEvent) => {
    try {
      const payload = JSON.parse(ev.data) as SnapshotPayload
      if (payload?.task) handlers.onSnapshot?.(normalizeTask(payload.task))
    } catch {
      // 单帧解析失败不致命：下一帧 snapshot/progress 会覆盖，不打断连接
    }
  })

  es.addEventListener('progress', (ev: MessageEvent) => {
    try {
      handlers.onProgress?.(JSON.parse(ev.data) as SseProgressEvent)
    } catch {
      // 同上：忽略坏帧
    }
  })

  es.addEventListener('done', (ev: MessageEvent) => {
    try {
      const payload = JSON.parse(ev.data) as DonePayload
      if (payload?.task) handlers.onDone?.(normalizeTask(payload.task))
    } catch {
      // 解析失败也要收口：终态已到，直接关闭连接（状态由重新拉取兜底）
    }
    es.close()
  })

  // 心跳：仅保活，无业务处理
  es.addEventListener('ping', () => {})

  // 浏览器对 EventSource 的 error 会自动重连（服务端不发 retry: → 默认退避）
  es.onerror = (err) => handlers.onError?.(err)

  return () => es.close()
}
