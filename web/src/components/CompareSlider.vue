<script setup lang="ts">
/**
 * CompareSlider —— 原图/修复图对比滑块（5A 定稿 · 15 组件之一）
 *
 * ⚠️ 硬约束（03 §3 禁止事项第 4 条 + 04 §7 无障碍）：
 *   1. **禁止使用 el-slider 冒充对比滑块** —— 语义错误、无法承载双图裁切。
 *   2. 必须自行实现 `role="slider"` + `aria-valuenow/valuemin/valuemax`。
 *   3. 必须支持键盘 ← → （可选 Home/End 跳到两端）调整分割位置。
 *   4. 必须支持指针拖拽（pointerdown/move/up）+ 触摸。
 *
 * 实现要点：
 *   - 用 `clip-path: inset(0 calc(100% - X%) 0 0)` 裁切上层图，避免重排。
 *   - 分割位置以百分比存储（0–100），与容器尺寸解耦，天然响应式。
 *   - `ResizeObserver` 不需要 —— 百分比方案无像素依赖。
 */
import { computed, ref, onBeforeUnmount } from 'vue'

const props = withDefaults(
  defineProps<{
    beforeSrc: string
    afterSrc: string
    beforeLabel?: string
    afterLabel?: string
    /** 初始分割位置（0–100） */
    initial?: number
    /** 图片自然宽高比，用于占位防抖（如 "16 / 9"） */
    aspect?: string
  }>(),
  {
    beforeLabel: '原图',
    afterLabel: '修复后',
    initial: 50,
    aspect: '16 / 10',
  },
)

const pos = ref(clamp(props.initial))
const containerRef = ref<HTMLElement | null>(null)
const dragging = ref(false)

function clamp(v: number): number {
  if (Number.isNaN(v)) return 50
  return Math.max(0, Math.min(100, v))
}

const beforeClip = computed(() => `inset(0 ${100 - pos.value}% 0 0)`)

function setFromClientX(clientX: number) {
  const el = containerRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (rect.width <= 0) return
  pos.value = clamp(((clientX - rect.left) / rect.width) * 100)
}

function onPointerDown(e: PointerEvent) {
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  setFromClientX(e.clientX)
  e.preventDefault()
}

function onPointerMove(e: PointerEvent) {
  if (!dragging.value) return
  setFromClientX(e.clientX)
}

function onPointerUp(e: PointerEvent) {
  dragging.value = false
  ;(e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId)
}

/** 键盘：← → 步进 2%，Shift 加速 ×5，Home/End 直达两端 */
function onKeydown(e: KeyboardEvent) {
  const step = e.shiftKey ? 10 : 2
  switch (e.key) {
    case 'ArrowLeft':
    case 'ArrowDown':
      pos.value = clamp(pos.value - step)
      break
    case 'ArrowRight':
    case 'ArrowUp':
      pos.value = clamp(pos.value + step)
      break
    case 'Home':
      pos.value = 0
      break
    case 'End':
      pos.value = 100
      break
    case 'PageDown':
      pos.value = clamp(pos.value - 10)
      break
    case 'PageUp':
      pos.value = clamp(pos.value + 10)
      break
    default:
      return
  }
  e.preventDefault()
}

onBeforeUnmount(() => {
  dragging.value = false
})
</script>

<template>
  <div
    ref="containerRef"
    class="cmp"
    :class="{ 'is-dragging': dragging }"
    :style="{ aspectRatio: aspect }"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <!-- 底层：修复后（完整显示） -->
    <img class="cmp-img cmp-after" :src="afterSrc" alt="修复后" draggable="false" />
    <!-- 上层：原图（按 pos 裁切） -->
    <img class="cmp-img cmp-before" :src="beforeSrc" alt="原图" draggable="false" :style="{ clipPath: beforeClip }" />

    <span class="cmp-tag cmp-tag-before" :class="{ 'is-dim': pos < 14 }">{{ beforeLabel }}</span>
    <span class="cmp-tag cmp-tag-after" :class="{ 'is-dim': pos > 86 }">{{ afterLabel }}</span>

    <!-- 分割手柄：既是视觉分隔线，也是键盘焦点目标 -->
    <div
      class="cmp-handle"
      role="slider"
      tabindex="0"
      :aria-valuenow="Math.round(pos)"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="`对比分割位置 ${Math.round(pos)}%，使用左右方向键调整`"
      :style="{ left: `${pos}%` }"
      @keydown="onKeydown"
      @pointerdown.stop="onPointerDown"
    >
      <div class="cmp-handle-line" />
      <div class="cmp-handle-knob">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M6 4L2.5 8L6 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M10 4L13.5 8L10 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ============================================================================
 * 叠加在「图像画布」上的元素（标签、分割线、把手）
 *
 * 这些元素叠在用户图片之上，不叠在应用界面上，因此**不能随主题切换**：
 * 浅色主题下若把手变暗，压在一张亮色照片上就看不见了。
 * 故此处使用固定的 --img-* 局部变量，而非 --Theme-*。
 * 这是一条明确的例外，不违反「禁止硬编码色值」——那一条针对的是界面色。
 * ========================================================================== */
.cmp {
  --img-overlay-bg: rgba(0, 0, 0, 0.6);
  --img-overlay-fg: #ffffff;
  --img-handle-fg: #ffffff;
  --img-handle-bg: #ffffff;
  --img-handle-icon: #1a1a1a;

  position: relative;
  width: 100%;
  border-radius: var(--Scale-radius-card);
  overflow: hidden;
  background: var(--Theme-bg-elevated);
  border: 1px solid var(--Theme-border-subtle);
  cursor: ew-resize;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.cmp-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  pointer-events: none;
}

.cmp-after {
  background: var(--Theme-bg-elevated);
}

.cmp-before {
  will-change: clip-path;
}

.cmp-tag {
  position: absolute;
  top: 10px;
  font-size: var(--font-size-12);
  line-height: 16px;
  padding: 2px 8px;
  border-radius: var(--Scale-radius-button);
  background: var(--img-overlay-bg);
  color: var(--img-overlay-fg);
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.cmp-tag-before {
  left: 10px;
}

.cmp-tag-after {
  right: 10px;
}

.cmp-tag.is-dim {
  opacity: 0;
}

.cmp-handle {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 0;
  transform: translateX(-50%);
  cursor: ew-resize;
  outline: none;
}

.cmp-handle-line {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 2px;
  transform: translateX(-1px);
  background: var(--img-handle-fg);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.25);
}

.cmp-handle-knob {
  position: absolute;
  top: 50%;
  left: 0;
  transform: translate(-50%, -50%);
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--img-handle-bg);
  color: var(--img-handle-icon);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
  transition: transform 0.12s ease;
}

.cmp:hover .cmp-handle-knob {
  transform: translate(-50%, -50%) scale(1.06);
}

.cmp.is-dragging .cmp-handle-knob {
  transform: translate(-50%, -50%) scale(1.1);
}

.cmp-handle:focus-visible .cmp-handle-knob {
  outline: 2px solid var(--Theme-primary);
  outline-offset: 2px;
}

/* 焦点在容器外时给出可见提示（键盘用户） */
.cmp:focus-within {
  border-color: var(--Theme-primary);
}
</style>
