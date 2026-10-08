/**
 * SSE 模拟器 —— 对外暴露与真实 `EventSource` 同形的订阅接口。
 *
 * 关键契约（api-contract.md §5）：
 *   1. **percent 单调不减**（NFR §3.1）；
 *   2. `percent` 以 **item** 为准，chunk 只用于阶段文案 `message`；
 *   3. 四类事件：snapshot / progress / done / ping；
 *   4. 断线重连后由 **snapshot** 恢复，不依赖本地缓存推算。
 *
 * 切换到真实实现的成本：页面代码零改动，只替换本文件的实现为 EventSource 封装。
 */
import type { SseHandlers, Task, TaskStage } from '@/types/api'

/** 阶段推进表：chunk 进度 → 阶段文案 */
function stageFor(chunk: number, total: number): { stage: TaskStage; message: string } {
  if (chunk <= 1) return { stage: 'preprocessing', message: '正在预处理与分块' }
  if (chunk < total) return { stage: 'inferencing', message: `正在推理 ${chunk} / ${total} 块 · tile 512` }
  if (chunk === total) return { stage: 'stitching', message: '正在拼接羽化' }
  return { stage: 'saving', message: '正在保存结果' }
}

/**
 * 订阅一个任务的进度。
 *
 * @param taskId  任务 ID
 * @param task    当前任务快照（用于首个 snapshot 事件）
 * @param handlers 事件回调
 * @returns 取消订阅函数
 */
export function subscribeTaskProgress(
  taskId: string,
  task: Task,
  handlers: SseHandlers,
): () => void {
  let disposed = false
  let chunk = task.progress.current_chunk
  const totalChunks = task.progress.total_chunks || 12

  // 1) 连接建立后立即推送一次当前状态（真实实现亦如此）
  handlers.onSnapshot?.(task)

  let percent = task.progress.percent
  const tickMs = 900 // 节流 ≤ 1 s

  const timer = window.setInterval(() => {
    if (disposed) return

    // 单调不减：只增不减（NFR §3.1）
    percent = Math.min(1, percent + (1 / totalChunks) * (0.85 + Math.random() * 0.3))
    chunk = Math.min(totalChunks, Math.ceil(percent * totalChunks))

    const { stage, message } = stageFor(chunk, totalChunks)

    handlers.onProgress?.({
      task_id: taskId,
      percent,
      current_item: 1,
      total_items: 1,
      current_chunk: chunk,
      total_chunks: totalChunks,
      stage,
      message,
    })

    if (percent >= 1) {
      window.clearInterval(timer)
      if (disposed) return
      const finishedAt = new Date().toISOString()
      const outW = task.source_width * task.params.scale
      const outH = task.source_height * task.params.scale
      handlers.onDone?.({
        ...task,
        status: 'done',
        progress: { percent: 1, current_item: 1, total_items: 1, current_chunk: totalChunks, total_chunks: totalChunks },
        output_width: outW,
        output_height: outH,
        duration_ms: Math.round(8_000 + Math.random() * 12_000),
        finished_at: finishedAt,
        // 完成后补齐产出与证据，使详情抽屉与下载按钮立即可用
        artifacts: [
          {
            id: `art_${Math.random().toString(36).slice(2, 10)}`,
            task_id: task.id,
            kind: 'output',
            path: `data/outputs/${task.id}/${task.filename.replace(/\.[^.]+$/, '')}_${task.params.scale}x.png`,
            filename: `${task.filename.replace(/\.[^.]+$/, '')}_${task.params.scale}x.png`,
            width: outW,
            height: outH,
            size_bytes: Math.round(outW * outH * 1.2),
            sha256: '—',
            created_at: finishedAt,
          },
        ],
        ep_evidence: task.ep_evidence?.length
          ? task.ep_evidence
          : [
              {
                provider: task.resolved?.backend ?? 'CUDAExecutionProvider',
                node_ownership: '1024 节点全在该提供器',
                node_count: 1024,
                cpu_node_count: 0,
                verified: true,
                note: '本次执行未回退到 CPU。',
              },
            ],
      })
    }
  }, tickMs)

  return () => {
    disposed = true
    window.clearInterval(timer)
  }
}
