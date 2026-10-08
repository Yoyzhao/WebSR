/**
 * 任务 store —— 跨页共享（工作台左栏 / 任务中心 / 详情抽屉）。
 *
 * 状态提升的理由：任务列表在两个页面同时可见，且 SSE 进度需要单一订阅源
 * （若各页面自行订阅，切换页面会产生重复连接）。见 6-frontend-rules.md「状态管理降级原则」。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { cancelTask, createTask, fetchTasks, retryTask, updateTask } from '@/api/client'
import { subscribeTaskProgress } from '@/api/mock/sse'
import type { SseProgressEvent, Task, TaskParams } from '@/types/api'

export const useTaskStore = defineStore('tasks', () => {
  const tasks = ref<Task[]>([])
  const loading = ref(false)
  const activeTaskId = ref<string | null>(null)

  /** taskId → 取消订阅函数 */
  const subscriptions = new Map<string, () => void>()

  /** 进行中的任务（含 canceling） */
  const runningTasks = computed(() =>
    tasks.value.filter((t) => t.status === 'running' || t.status === 'queued' || t.status === 'canceling'),
  )

  const latestTask = computed(() => tasks.value[0] ?? null)

  /** 当前预览的任务：优先进行中，否则取最近一条 */
  const previewTask = computed(() => runningTasks.value[0] ?? latestTask.value)

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
    const unsubscribe = subscribeTaskProgress(task.id, task, {
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

  async function submit(
    fileId: string,
    filename: string,
    params: TaskParams,
    source: { width: number; height: number },
    modelName?: string,
  ): Promise<Task> {
    const task = await createTask(fileId, filename, params, source, modelName)
    tasks.value = [task, ...tasks.value]
    activeTaskId.value = task.id
    subscribe(task)
    return task
  }

  async function cancel(id: string): Promise<void> {
    const next = await cancelTask(id)
    patch(next)
    subscriptions.get(id)?.()
    subscriptions.delete(id)
  }

  async function retry(id: string): Promise<void> {
    const next = await retryTask(id)
    patch(next)
    subscribe(next)
  }

  /** 用于"清理已完成" */
  async function removeByIds(ids: string[]): Promise<void> {
    const { removeTasks } = await import('@/api/client')
    await removeTasks(ids)
    ids.forEach((id) => {
      subscriptions.get(id)?.()
      subscriptions.delete(id)
    })
    tasks.value = tasks.value.filter((t) => !ids.includes(t.id))
  }

  async function clearFinished(): Promise<number> {
    const finished = tasks.value.filter((t) => t.status === 'done' || t.status === 'canceled' || t.status === 'interrupted')
    await removeByIds(finished.map((t) => t.id))
    return finished.length
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
    retry,
    clearFinished,
    removeByIds,
    patch,
    updateTask,
    unsubscribeAll,
  }
})
