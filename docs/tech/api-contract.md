# API 契约（草案 · 待步骤 5B 冻结）

> 状态：**草案** ｜ 冻结阶段：**步骤 5B（任务 `T-405`）** ｜ 最后更新：2026-09-30
>
> ⚠️ **本文件尚未冻结。** 步骤 5C 的 Mock 可以据此生成，但 5D 的后端实现**必须以冻结版本为准**。
> 冻结时需补齐：字段级类型与必填性、分页参数、全部错误码、SSE 事件的精确定义。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 基础路径 | `/api`（前端开发期由 Vite `server.proxy` 转发到 `http://127.0.0.1:8000`） |
| 绑定地址 | 后端绑 `127.0.0.1`（本地单机形态，**禁止** `0.0.0.0`） |
| 认证 | **无**（v1 单机自用，无账号体系）。若改为局域网可访问，**必须先补鉴权**（PRD §3.2） |
| 请求/响应格式 | JSON（文件上传与下载除外） |
| 命名风格 | 字段统一 `snake_case`（与 Python 侧一致，避免双层转换） |
| 时间格式 | **ISO 8601 带时区偏移**，服务端一律存 UTC，前端按 `Asia/Shanghai`（UTC+8）展示 |
| 分页 | `?page=1&page_size=20`；响应含 `{ items, total, page, page_size }` |
| 筛选 | 直接以字段名作为查询参数，如 `?status=completed&type=upscale` |
| CORS | 开发期由 Vite 代理规避；生产若需直连，**白名单限定前端来源，禁止 `*`** |
| 单 worker 约束 | uvicorn **必须单 worker**（SSE 依赖进程内广播，见 ADR-005） |

---

## 2. 统一响应体

### 2.1 成功

直接返回资源对象或 `{ items, total, page, page_size }`。**不额外包一层 `data`**。

### 2.2 失败（统一错误体，PRD §5 要求）

```json
{
  "error": {
    "code": "VRAM_INSUFFICIENT",
    "message": "显存不足：已自动降档 2 次仍无法完成",
    "suggestion": "降低放大倍数，或改用 fp16 量化模型",
    "detail": { "required_mb": 9216, "available_mb": 7577, "downgrade_attempts": 2 }
  }
}
```

**三要素缺一不可**：`code`（机器可读）+ `message`（用户可读）+ `suggestion`（**下一步该做什么**）。
界面上只展示 `message` 与 `suggestion`；`code` 与 `detail` 收进「查看日志」详情。

### 2.3 错误码草案

| code | HTTP | 触发场景 | 界面落点 |
|---|---|---|---|
| `VALIDATION_ERROR` | 400 | 请求字段缺失或非法 | 表单内联错误 |
| `UNSUPPORTED_FORMAT` | 400 | 上传扩展名不在白名单 | 上传区内联错误 |
| `FORMAT_MISMATCH` | 400 | 扩展名与实际格式不符（服务端二次校验） | 上传区内联错误 |
| `FILE_TOO_LARGE` | 413 | 超过 `APP_MAX_UPLOAD_MB` | 上传区内联错误 |
| `MODEL_NOT_FOUND` | 404 | 指定的 `model_id` 不存在 | 参数面板错误 |
| `MODEL_MISSING_COMPANION` | 400 | `.bin` 导入时缺少 `.xml` / `.param` 配套文件 | 导入弹窗内联错误 |
| `MODEL_INCOMPATIBLE` | 400 | 模型与所选后端不兼容 | 参数面板错误 |
| `MODEL_INSUFFICIENT_VRAM` | 409 | 该模型的最低显存需求高于当前可用量 | 模型置灰原因（**应前置到列表页，不该等到提交才报**） |
| `VRAM_INSUFFICIENT` | 409 | 推理中显存不足且降档链已用尽 | 任务行「失败」+ 原因 |
| `RAM_INSUFFICIENT` | 409 | CPU 档物理内存不足 | 同上 |
| `TASK_NOT_FOUND` | 404 | 任务不存在 | 列表刷新 |
| `TASK_CANCELED` | 409 | 对已取消/已完成任务再次取消 | 静默忽略并刷新状态 |
| `TASK_ALREADY_RUNNING` | 409 | 并发上限为 1 时重复提交 | 提交按钮禁用 + 提示排队 |
| `EP_FALLBACK_DETECTED` | 200（事件） | EP 未生效、发生静默回退 | 顶栏 EP 徽标变化 + 硬件面板标红 + 落盘 |
| `ENGINE_BUSY` | 503 | 引擎被占用且不接受新任务 | 提交按钮禁用 |
| `INTERNAL_ERROR` | 500 | 未预期异常 | 全局错误提示 + 诊断导出入口 |

---

## 3. 资源模型（草案）

### 3.1 Task

```jsonc
{
  "id": "tsk_01J8X...",
  "type": "upscale",                   // upscale | batch_upscale | (预留 face_restore / video)
  "status": "queued",                  // queued | running | canceling | completed | canceled | failed
  "progress": { "percent": 0.25, "current_item": 1, "total_items": 1,
                "current_chunk": 3, "total_chunks": 12 },
  "params": { /* 参数快照：倍数、model_id、tile、precision、backend、auto */ },
  "resolved": {                        // 实际生效值（"自动"档的落点）—— 只记这里，不记 params
    "tile": 512, "precision": "fp16",
    "backend": "CUDAExecutionProvider",
    "using_fallback": true,            // 是否处于保底档
    "degraded": false,                 // 是否发生降档
    "reasons": ["未标定，取保守下界参数"]
  },
  "error": null,                       // 失败时填入统一错误体中的 error 对象
  "created_at": "2026-09-30T11:22:33+00:00",
  "started_at": "2026-09-30T11:22:34+00:00",
  "finished_at": null,
  "artifacts": []                      // 或由单独端点获取
}
```

**两个关键设计（来自 M2 评审定案）**

1. **`params` 与 `resolved` 分离**：`params` 是用户的**请求值**（"自动"档时 `tile` 为 `null`）；`resolved` 是引擎的**决策结果**。界面上的「高级参数」显示 `resolved`，「本次决策」显示 `resolved.reasons`。
2. **`status` 含 `canceling`**：协作式取消的中间态。前端据此显示「正在取消」而非「已取消」（详见 `docs/prototype/04-状态与交互定义.md` §2.5）。

### 3.2 Model

```jsonc
{
  "id": "mdl_01J8X...",
  "name": "RealESRGAN_x4plus",
  "format": "onnx",                    // onnx | openvino_ir | ncnn | pth | safetensors
  "path": "data/models/RealESRGAN_x4.onnx",
  "sha256": "5c586662...b89c033",
  "size_bytes": 67051616,
  "params_count": 16697987,
  "scale": 4,
  "license": "BSD-3-Clause",
  "source": "builtin",                 // builtin | imported
  "min_vram_mb": 4096,                 // 可用性门槛（不是排序依据）
  "supported_backends": ["cuda", "cpu", "openvino"],
  "capabilities": {                    // 能力声明（PRD §7.1 接缝字段）
    "supports_fp16": true, "supports_batch": false, "supports_tile0": false,
    "has_tensorrt": false, "is_generative": false,
    "num_inference_steps": null, "requires_prompt": false
  },
  "companion": null,                   // .bin 必须带配套文件：[".xml"] 或 [".param"]
  "available": true,                   // 服务端按当前档位算好的可用性
  "unavailable_reason": null           // 不可用时的原因文案
}
```

> **`min_vram_mb` 只用于可用性门控**：跑不动的置灰，**引擎不做模型推荐**（用户 2026-09-30 决定，PRD v1.8）。

### 3.3 其他

- **Artifact**：`{ id, task_id, kind, path, size_bytes, sha256, created_at }`
- **Preset**（S2）：`{ id, name, params, tier, is_builtin }`
- **Setting**：`{ key, value, type }`
- **Capabilities / DeviceFacts**：见 §4 的 `/api/system/capabilities`

---

## 4. 端点（草案）

> 端点清单源自 PRD §5，此处补上关键字段。**冻结时需逐条确认必填性与分页参数。**

### 4.1 任务

| 方法 | 路径 | 用途 | 关键请求 | 关键响应 |
|---|---|---|---|---|
| POST | `/api/tasks` | 提交任务 | `{ type, file_id, params: {...} }` | `{ id, status: "queued" }`（**≤ 1 s 返回**，不含推理） |
| GET | `/api/tasks` | 任务列表 | `?page&page_size&status&type` | `{ items: Task[], total, page, page_size }` |
| GET | `/api/tasks/{id}` | 任务详情 | — | `Task`（含 `params` 与 `resolved`） |
| GET | `/api/tasks/{id}/events` | **SSE 实时进度** | — | 事件流（见 §5） |
| POST | `/api/tasks/{id}/cancel` | 取消任务 | — | `{ id, status: "canceling" }` |
| GET | `/api/tasks/{id}/artifacts` | 产物列表 | — | `Artifact[]` |

### 4.2 文件

| 方法 | 路径 | 用途 | 关键请求 | 关键响应 |
|---|---|---|---|---|
| POST | `/api/files/upload` | 上传图片 / 模型 | `multipart/form-data` | `{ file_id, filename, size, width, height, real_format }` |
| GET | `/api/files/{id}/content` | 取文件内容 | `?variant=original\|result\|thumb` | 二进制（`Content-Type` 按实际格式） |

> `real_format` 由服务端二次校验得出（**不信任扩展名**，PRD §3.2）。前端将其与扩展名比对，不符时给出明确提示。

### 4.3 模型

| 方法 | 路径 | 用途 | 关键请求 | 关键响应 |
|---|---|---|---|---|
| GET | `/api/models` | 模型列表 | `?page&page_size&format` | `{ items: Model[], total, ... }`（含 `available` 与 `unavailable_reason`） |
| POST | `/api/models/import` | 导入模型 | `multipart`（`.bin` 必须同时带 `.xml` 或 `.param`） | `Model` |
| POST | `/api/models/{id}/convert` | 触发离线转换（`.pth`/`.safetensors` → `.onnx`） | — | `{ task_id }`（转换本身也是长任务） |
| GET | `/api/models/{id}/export` | 导出模型 | — | 二进制 |
| DELETE | `/api/models/{id}` | 删除模型 | — | `204` |

### 4.4 系统

| 方法 | 路径 | 用途 | 关键响应 |
|---|---|---|---|
| GET | `/api/system/capabilities` | 硬件探测结果 | `{ tier, device_facts: {...}, verified_backends: [...], ep_evidence: {...} }` |
| POST | `/api/system/calibrate` | 触发自标定（**S2**） | `{ task_id }` |
| GET | `/api/system/calibration` | 标定结果与理由（**S2**） | `{ records: [...], recommended_tier, reasons }` |
| GET | `/api/system/diagnostics` | 一键导出诊断 JSON（PRD §3.4） | 单个 JSON 文件（含硬件事实 + EP 验证结果 + 标定记录） |
| GET / PUT | `/api/settings` | 读写系统配置 | `{ items: Setting[] }` / 更新后的同结构 |

### 4.5 预设（S2）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET / POST / DELETE | `/api/presets` | 参数预设 CRUD（预设含 `tier` 字段，可绑定档位） |

---

## 5. SSE 事件（草案）

**事件流**：`GET /api/tasks/{id}/events`（`text/event-stream`）

```jsonc
// 1) 快照：连接建立（或断线重连）后立即推送一次当前状态
event: snapshot
data: { "task": { /* 完整 Task 对象 */ } }

// 2) 进度：节流 ≤ 1 s（NFR §3.1）
event: progress
data: {
  "task_id": "tsk_...",
  "percent": 0.25,               // 以 item 为准
  "current_item": 1, "total_items": 1,
  "current_chunk": 3, "total_chunks": 12,
  "stage": "inferencing",        // queued | preprocessing | inferencing | stitching | saving
  "message": "正在推理 3 / 12 块"
}

// 3) 终止：completed / canceled / failed 三者之一，随后服务端关闭该连接
event: done
data: { "task": { /* 完整 Task 对象，含 resolved 与 error */ } }

// 4) 心跳：保持连接（间隔待 5B 定义）
event: ping
data: {}
```

### 前端映射规则（对应 `docs/prototype/04-状态与交互定义.md` §2.2）

| 界面元素 | 数据来源 |
|---|---|
| 进度条宽度 | `percent`（**以 item 为准**） |
| 阶段文案 | `message`（含 chunk 级细节，如「正在推理 3 / 12 块」） |
| 任务状态文字 | `stage` / `task.status` 的组合映射 |
| 断线恢复 | 重连后由 `snapshot` 事件恢复，**不依赖本地缓存推算进度** |
| 页面刷新 | 重新拉取 `GET /api/tasks` + 对运行中的任务重连 SSE |

> **UI 不得自行推算进度**——只能渲染服务端推送的 `percent`。进度必须单调不减（NFR §3.1）。

---

## 6. 与 PRD §5 的差异

| 项 | 说明 |
|---|---|
| PRD §5 未定义错误码 | 本文件 §2.3 首次给出草案，5B 冻结 |
| PRD §5 未定义 SSE 事件格式 | 本文件 §5 给出草案（快照 / 进度 / 终止 / 心跳四类），5B 冻结 |
| 端点路径保持一致 | 未新增或删减端点，仅补充字段 |
| `POST /api/models/{id}/convert` | 来自 PRD v1.7 补充，路径一致 |

---

## 7. 冻结点（5B 必须完成）

- [ ] 每个字段的类型、必填性、取值范围
- [ ] 分页与筛选参数的全部取值
- [ ] 错误码全量清单 + 每种错误的界面文案对照表
- [ ] SSE 事件的精确定义（心跳间隔、重连退避、`Last-Event-ID` 支持与否）
- [ ] 文件上传的大小上限与允许的 MIME 白名单
- [ ] 前端类型生成方案（由 OpenAPI 生成 TS 类型 vs 手写）
- [ ] `resolved.reasons` 的文案规范（这些文案会直接展示给用户）
