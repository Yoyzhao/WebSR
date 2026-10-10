/**
 * 任务 store —— 跨页共享（工作台左栏 / 任务中心 / 详情抽屉）。
 *
 * 状态提升的理由：任务列表在两个页面同时可见，且 SSE 进度需要单一订阅源
 * （若各页面自行订阅，切换页面会产生重复连接）。见 6-frontend-rules.md「状态管理降级原则」。
 *
 * 步骤 6（T-701）：进度订阅已由模拟器切换为**真实 `EventSource`**（`api/sse.ts`），
 * 本文件对页面暴露的接口保持稳定 —— 页面代码无需知道进度从哪来。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { cancelTask, createTask, fetchTask, fetchTasks } from '@/api/client'
import { subscribeTaskProgress } from '@/api/sse'
import type { SseProgressEvent, Task, TaskParams } from '@/types/api'

export const useTaskStore = defineStore('tasks', () => {
  const tasks = ref<Task[]>([])
  const loading = ref(false)
  const activeTaskId = ref<string | null>(null)

  /** 终态集合（契约 §3.1：服务端 7 值，无 `pending`）—— 终态任务不再需要 SSE */
  const TERMINAL = new Set(['completed', 'canceled', 'failed', 'interrupted'])

  /** taskId → 取消订阅函数 */
  const subscriptions = new Map<string, () => void>()

  /** 进行中的任务（含 canceling） */
  const runningTasks = computed(() =>
    tasks.value.filter((t) => t.status === 'running' || t.status === 'queued' || t.status === 'canceling'),
  )

  const latestTask = computed(() => tasks.value[0] ?? null)

  /**
   * 会话内出现过的任务 id（= 由本页 `submit()` 提交的任务）。
   * 工作台预览**只跟随这些任务**：页面加载默认为空态，不回放历史任务的图
   * （T-711 用户反馈「工作台默认为空，不要显示最近的图片」）；
   * 提交后从进行中一路跟随到终态 —— 否则任务跑完离开 running 集合的瞬间
   * 预览会突然清空，观感是"进度看完就没了"。
   */
  const sessionTaskIds = ref(new Set<string>())

  /** 当前预览的任务：会话内优先进行中，否则取会话内最近一条；会话外一律空 */
  const previewTask = computed(() => {
    const seen = sessionTaskIds.value
    if (seen.size === 0) return null
    // tasks 按新→旧排序：find 命中的即会话内最近一条
    return (
      runningTasks.value.find((t) => seen.has(t.id)) ??
      tasks.value.find((t) => seen.has(t.id)) ??
      null
    )
  })

  async function load(): Promise<void> {
    loading.value = true
    try {
      tasks.value = await fetchTasks()
      // 页面刷新后：对运行中的任务重连 SSE（api-contract §5「页面刷新」规则）
      runningTasks.value.forEach((task) => subscribe(task))
    } finally {
      loading.value = false
    }
  }

  /** 对单个任务建立进度订阅（幂等：已订阅则不重复建立） */
  function subscribe(task: Task): void {
    if (subscriptions.has(task.id)) return
    const unsubscribe = subscribeTaskProgress(task.id, {
      onSnapshot: (snapshot) => patch(snapshot),
      onProgress: (event: SseProgressEvent) => applyProgress(event),
      onDone: (done) => {
        patch(done)
        subscriptions.get(task.id)?.()
        subscriptions.delete(task.id)
      },
    })
    subscriptions.set(task.id, unsubscribe)
  }

  function patch(next: Task): void {
    const idx = tasks.value.findIndex((t) => t.id === next.id)
    if (idx >= 0) tasks.value[idx] = { ...tasks.value[idx], ...next }
    else tasks.value = [next, ...tasks.value]
  }

  function applyProgress(event: SseProgressEvent): void {
    const task = tasks.value.find((t) => t.id === event.task_id)
    if (!task) return
    // UI 不得自行推算进度，只渲染服务端推送的 percent（api-contract §5）
    task.progress = {
      percent: event.percent,
      current_item: event.current_item,
      total_items: event.total_items,
      current_chunk: event.current_chunk,
      total_chunks: event.total_chunks,
    }
    task.stage = event.stage
    task.stage_message = event.message
  }

  /** 释放所有订阅（组件卸载/登出时调用） */
  function unsubscribeAll(): void {
    subscriptions.forEach((fn) => fn())
    subscriptions.clear()
  }

  /**
   * 提交任务。
   *
   * 提交后立即建立 SSE 订阅 —— 服务端「提交即返回、推理异步进行」，
   * 若等到下一次列表刷新才订阅，会漏掉前几帧进度。
   */
  async function submit(fileId: string, params: TaskParams): Promise<Task> {
    const task = await createTask(fileId, params)
    tasks.value = [task, ...tasks.value]
    activeTaskId.value = task.id
    // 纳入会话跟踪：工作台预览从这一刻开始跟随本任务（含终态后的结果展示）
    sessionTaskIds.value.add(task.id)
    subscribe(task)
    return task
  }

  async function cancel(id: string): Promise<void> {
    const next = await cancelTask(id)
    patch(next)
    // ⚠️ 这里**不能**无条件退订（T-702 浏览器补测实测出的真实缺陷）。
    //
    // 取消是**协作式**的（api-contract §4.1 + `tasks/manager.cancel`）：
    //   - `queued` 任务：后端直接终态化为 `canceled` 并广播 `done`；
    //   - `running` 任务：后端只置中间态 `canceling`，真正的终态 `canceled`
    //     由工作线程在块间自检后写入，再过 `_publish_done` 广播 `done`。
    // 若在此处立即 `es.close()`，第二条 `done` 就永远收不到 —— 界面会**永久停在
    //「正在取消」，且不再有任何自更新途径**（除非用户手动刷新页面）。
    // 正确做法：把退订交给既有的 `onDone` 收口（它本就在终态帧后关闭连接）。
    // 仅当取消响应本身已是终态（`queued` 那条路径）时，才由这里直接收口。
    if (TERMINAL.has(next.status)) {
      subscriptions.get(id)?.()
      subscriptions.delete(id)
    }
  }

  /** 主动重新拉取单个任务（详情抽屉打开时兜底同步一次） */
  async function refresh(id: string): Promise<void> {
    try {
      const fresh = await fetchTask(id)
      patch(fresh)
      if (fresh.status === 'running' || fresh.status === 'queued' || fresh.status === 'canceling') {
        subscribe(fresh)
      }
    } catch {
      // 刷新失败不打断界面：SSE 或下次列表加载会兜底
    }
  }

  return {
    tasks,
    loading,
    activeTaskId,
    runningTasks,
    latestTask,
    previewTask,
    load,
    submit,
    cancel,
    refresh,
    patch,
    unsubscribeAll,
  }
})
