<script setup lang="ts">
/**
 * CompareSlider —— 原图/修复图对比（5A 定稿 · 15 组件之一）
 *
 * ⚠️ 硬约束（03 §3 禁止事项第 4 条 + 04 §7 无障碍）：
 *   1. **禁止使用 el-slider 冒充对比滑块** —— 语义错误、无法承载双图裁切。
 *   2. 必须自行实现 `role="slider"` + `aria-valuenow/valuemin/valuemax`。
 *   3. 必须支持键盘 ← → （可选 Home/End 跳到两端）调整分割位置。
 *   4. 必须支持指针拖拽（pointerdown/move/up）+ 触摸。
 *
 * 三种模式（F-03 / 01-页面结构与布局 §93 · 03-组件映射与复用 §26 的 `mode` 属性）：
 *   - `side`   **并排**：两图各占一半，同时可见，适合整体观感对比；
 *   - `slider` **滑块**：上层图按分割位置 `clip-path` 裁切（**默认模式**，04 §31 定稿）；
 *   - `zoom`   **局部放大**：底层为原图全图，指针处出现放大镜，镜内是**同一位置**的修复结果
 *              按 `ZOOM` 倍放大 —— 用于确认"细节到底修没修出来"。
 *
 * 🔑 局部放大为什么不新增 `<img>`（`04-状态与交互定义 §164`：切换模式**不重新加载图片**）：
 *   放大镜由**同一个 `.cmp-after` 节点**实现 —— `transform: scale()` + `transform-origin`
 *   + `clip-path: circle()` 三者协同：
 *     · `clip-path` 作用于元素的**本地坐标系**，之后才施加 `transform`；
 *       因此圆心会随 `scale` 一起被放大 —— 把半径取 `LENS_D / 2 / ZOOM`，最终镜面直径恰为 `LENS_D`；
 *     · 令圆心与 `transform-origin` **重合于指针位置** → 指针下的那个点在放大前后**保持不动**。
 *   这既是放大镜的正确判据，也复用了已解码的同一个节点（无新请求、无闪烁）。
 *
 * 实现要点：
 *   - 滑块模式用 `clip-path: inset(...)` 裁切上层图，避免重排；位置以百分比存储，天然响应式。
 *   - 指针坐标转元素本地坐标时必须减去容器的 border（`clientLeft/clientTop`），
 *     否则放大镜与指针会差 1px（容器有 1px 描边）。
 */
import { computed, ref, watch, onBeforeUnmount } from 'vue'

type CompareMode = 'side' | 'slider' | 'zoom'

const props = withDefaults(
  defineProps<{
    beforeSrc: string
    afterSrc: string
    beforeLabel?: string
    afterLabel?: string
    /** 初始分割位置（0–100），仅滑块模式有效 */
    initial?: number
    /** 图片自然宽高比，用于占位防抖（如 "16 / 9"） */
    aspect?: string
    /** 对比模式（默认滑块，04 §31 定稿） */
    mode?: CompareMode
  }>(),
  {
    beforeLabel: '原图',
    afterLabel: '修复后',
    initial: 50,
    aspect: '16 / 10',
    mode: 'slider',
  },
)

/** 局部放大模式的放大幅率与镜面直径（像素） */
const ZOOM = 4
const LENS_D = 120

const pos = ref(clamp(props.initial))
const containerRef = ref<HTMLElement | null>(null)
const dragging = ref(false)
/** 放大镜位置（容器本地像素坐标；null = 尚未进入画布） */
const lens = ref<{ x: number; y: number } | null>(null)

function clamp(v: number): number {
  if (Number.isNaN(v)) return 50
  return Math.max(0, Math.min(100, v))
}

const beforeClip = computed(() => `inset(0 ${100 - pos.value}% 0 0)`)

const beforeStyle = computed(() => (props.mode === 'slider' ? { clipPath: beforeClip.value } : {}))

/**
 * 放大镜：`clip-path` 的圆心与 `transform-origin` 都是**同一个点**（指针）。
 * 半径先除以 ZOOM，抵消 `clip-path` 在本地坐标系被 `transform` 一起放大的效应。
 */
const afterZoomStyle = computed(() => {
  if (props.mode !== 'zoom') return {}
  const L = lens.value
  if (!L) return { display: 'none' }
  return {
    transformOrigin: `${L.x}px ${L.y}px`,
    transform: `scale(${ZOOM})`,
    clipPath: `circle(${LENS_D / 2 / ZOOM}px at ${L.x}px ${L.y}px)`,
  }
})

const lensRingStyle = computed(() => {
  const L = lens.value
  if (!L) return { display: 'none' }
  return { left: `${L.x}px`, top: `${L.y}px`, width: `${LENS_D}px`, height: `${LENS_D}px` }
})

/** 指针事件 → 容器本地像素坐标（减掉 1px 描边偏移，见文件头注释） */
function localPoint(e: PointerEvent): { x: number; y: number } | null {
  const el = containerRef.value
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (r.width <= 0 || r.height <= 0) return null
  return {
    x: Math.max(0, Math.min(r.width, e.clientX - r.left - el.clientLeft)),
    y: Math.max(0, Math.min(r.height, e.clientY - r.top - el.clientTop)),
  }
}

function setFromClientX(clientX: number) {
  const el = containerRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (rect.width <= 0) return
  pos.value = clamp(((clientX - rect.left) / rect.width) * 100)
}

function onPointerDown(e: PointerEvent) {
  if (props.mode !== 'slider') return
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  setFromClientX(e.clientX)
  e.preventDefault()
}

function onPointerMove(e: PointerEvent) {
  if (props.mode === 'zoom') {
    const p = localPoint(e)
    if (p) lens.value = p
    return
  }
  if (props.mode !== 'slider' || !dragging.value) return
  setFromClientX(e.clientX)
}

function onPointerUp(e: PointerEvent) {
  if (props.mode !== 'slider') return
  dragging.value = false
  ;(e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId)
}

function onPointerLeave() {
  if (props.mode === 'zoom') lens.value = null
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

/** 局部放大模式的键盘支持：方向键移动放大镜（首次按键定位到画布中心） */
function onZoomKeydown(e: KeyboardEvent) {
  if (props.mode !== 'zoom') return
  const el = containerRef.value
  if (!el) return
  const r = el.getBoundingClientRect()
  if (r.width <= 0 || r.height <= 0) return
  const cur = lens.value ?? { x: r.width / 2, y: r.height / 2 }
  const step = e.shiftKey ? 40 : 10
  let { x, y } = cur
  switch (e.key) {
    case 'ArrowLeft':
      x -= step
      break
    case 'ArrowRight':
      x += step
      break
    case 'ArrowUp':
      y -= step
      break
    case 'ArrowDown':
      y += step
      break
    default:
      return
  }
  e.preventDefault()
  lens.value = {
    x: Math.max(0, Math.min(r.width, x)),
    y: Math.max(0, Math.min(r.height, y)),
  }
}

/** 切换模式时丢弃旧的放大镜位置，避免换模式后残留一个错位的镜面 */
watch(
  () => props.mode,
  () => {
    lens.value = null
    dragging.value = false
  },
)

onBeforeUnmount(() => {
  dragging.value = false
})
</script>

<template>
  <div
    ref="containerRef"
    class="cmp"
    :class="[`mode-${mode}`, { 'is-dragging': dragging }]"
    :style="{ aspectRatio: aspect }"
    :tabindex="mode === 'zoom' ? 0 : undefined"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
    @pointerleave="onPointerLeave"
    @keydown="onZoomKeydown"
  >
    <!-- 底层：修复后（完整显示；局部放大模式下本节点同时充当放大镜） -->
    <img
      class="cmp-img cmp-after"
      :src="afterSrc"
      alt="修复后"
      draggable="false"
      :style="afterZoomStyle"
    />
    <!-- 上层：原图（滑块模式按 pos 裁切；并排模式占左半；局部放大模式为完整底图） -->
    <img
      class="cmp-img cmp-before"
      :src="beforeSrc"
      alt="原图"
      draggable="false"
      :style="beforeStyle"
    />

    <!-- 局部放大：镜面描边（纯视觉，不吃指针事件） -->
    <div v-if="mode === 'zoom'" class="cmp-lens" :style="lensRingStyle" aria-hidden="true" />

    <span class="cmp-tag cmp-tag-before" :class="{ 'is-dim': mode === 'slider' && pos < 14 }">
      {{ beforeLabel }}
    </span>
    <span class="cmp-tag cmp-tag-after" :class="{ 'is-dim': mode === 'slider' && pos > 86 }">
      {{ afterLabel }}
    </span>
    <span v-if="mode === 'zoom'" class="cmp-tag cmp-tag-zoom">镜头内为{{ afterLabel }} ×{{ ZOOM }}</span>

    <!-- 分割手柄：既是视觉分隔线，也是键盘焦点目标（仅滑块模式存在） -->
    <div
      v-if="mode === 'slider'"
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
 * 叠加在「图像画布」上的元素（标签、分割线、把手、镜面）
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

.cmp.mode-side {
  cursor: default;
}

.cmp.mode-zoom {
  cursor: crosshair;
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

/* ---- 并排模式：两个节点变成 flex 项，按 order 排成「原图 | 修复后」 -------------- */
.cmp.mode-side {
  display: flex;
  align-items: stretch;
}

.cmp.mode-side .cmp-img {
  position: relative;
  inset: auto;
  width: 50%;
  height: 100%;
}

.cmp.mode-side .cmp-before {
  order: 1;
}

.cmp.mode-side .cmp-after {
  order: 2;
  border-left: 1px solid var(--Theme-border-subtle);
}

/* ---- 局部放大模式：修复后图层压在原图之上，且只在镜面内可见 -------------------- */
.cmp.mode-zoom .cmp-after {
  z-index: 2;
}

.cmp.mode-zoom:focus-visible {
  outline: 2px solid var(--Theme-primary);
  outline-offset: 2px;
}

.cmp-lens {
  position: absolute;
  z-index: 3;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  border: 2px solid var(--img-handle-fg);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.45);
  pointer-events: none;
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

.cmp-tag-zoom {
  bottom: 10px;
  top: auto;
  left: 50%;
  transform: translateX(-50%);
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
