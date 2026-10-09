# API 契约（草案 · 最终冻结移交 M6 / `T-700`）

> 状态：**草案**（M4 可离线判定部分已定） ｜ 最终冻结点：**步骤 6（任务 `T-700`）** ｜ 最后更新：2026-10-08
>
> ⚠️ **本文件尚未全量冻结，且已确认其 7 个冻结点中有 4 项不可能在 5D 之前完成** —— 那些取值由后端与引擎产出（心跳间隔、`reasons` 文案、分页取值、OpenAPI schema），提前写死只会与实现漂移。
>
> **因此口径已修正（2026-10-08）**：契约的**最终冻结时点**由「5D 之前」改为「**步骤 6 之前**」。5C 的 Mock 与本文件同步生成；**5D 按本草案实现并导出 OpenAPI schema**；`T-700` 完成全量冻结。当前已定与待定逐项见 §7。

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
    "reasons": ["未标定，取保守下界参数"],
    // ↓ T-804（阶段 D/E）新增，**待 T-700 冻结时补入类型**（见 §3.1 差异登记）
    "overlap": 128, "feather_px": 128, // M4 分块/羽化的实际取值
    "concurrency": 1,                  // 决策出的并发上限（调度生效属 G-06）
    "source": "fallback",              // calibration | user | fallback
    "downgrades": [                    // 逐条降档证据（"降级必须显式"）
      { "field": "tile", "from": 512, "to": 256, "reason": "连续 2 次资源水位超过 85%" }
    ],
    // ↓ T-806（真实推理）新增：**执行事实**（与上面的"决策事实"刻意分层），
    //   仅 completed 态有值。**待 T-700 冻结时补入类型**（见 §3.1 差异登记）
    "execution": {
      "output_width": 400, "output_height": 320,
      "tiles": 4, "scale": 4, "elapsed_ms": 1823,
      "sha256": "9f3c…", "size_bytes": 21491, "backend": "onnxruntime-cpu"
    }
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

**差异登记（T-804 实施产生，T-700 冻结时裁决）**

- 5C 冻结的 `TaskResolved` 只有 `tile / precision / backend / using_fallback / degraded / reasons /
  downgrades`。T-804 追加 **`overlap` / `feather_px` / `concurrency` / `source`** 四项，原因是
  M2 编排必须拿到 `overlap` / `feather_px` 才能驱动 M4 的 `plan_tiles` / `TileAccumulator`，
  而 `source` 是界面"本次决策"解释"为什么用这个参数"的依据。前端类型为非严格结构，
  多字段不破坏既有解析。**建议冻结时正式补入 `TaskResolved`。**
- `downgrades[].reason` 内的数值是**运行期真实值**，前端应按字符串直出，不要解析成结构化数字。
- 诊断导出（`/api/system/diagnostics`）新增 `decision` 与 `watermark` 区段；
  契约未定义诊断体的字段集（与 T-803 的 `probe` / `ep_verification` 同类）。

**差异登记（T-806 实施产生，T-700 冻结时裁决）**

- **`resolved` 新增 `execution` 子对象**——记录**执行事实**而非决策事实：

  ```jsonc
  "resolved": {
    "tile": 64, "precision": "fp32", "backend": "CPUExecutionProvider",   // ← 决策
    "execution": {                                                        // ← 实际发生
      "output_width": 400, "output_height": 320,
      "source_width": 100, "source_height": 80,
      "tiles": 4, "scale": 4, "elapsed_ms": 1823,
      "size_bytes": 21491, "sha256": "9f3c…", "backend": "onnxruntime-cpu"
    }
  }
  ```

  刻意**不把执行结果拍平到 `resolved` 顶层**：T-804 的教训正是"决策与事实混在一层，
  会让人误以为决策即事实"。建议冻结时为 `TaskResolved` 增加 `execution` 字段（可空，
  仅 `completed` 态有值）。
- **`TaskOut.output_width` / `output_height` 由"恒为 null 的占位"变为"真实回填"**：
  字段 5C 就已存在，T-806 起才有值，**类型无需改动**，但应在契约中写明其**来源**
  （取自 `resolved.execution`）与**时机**（任务进入终态后）。
- **`ArtifactOut.path` 带 `data/` 前缀**：DB 存相对数据根的 `outputs/tsk_<id>/…`，
  对外统一输出 `data/outputs/…`（与 5C 前端已定稿的 Mock 约定一致）。
- **`ArtifactOut.width` / `height` 仅对 `kind=output` 有值**，取自 `resolved.execution`；
  **产物表不加宽高列**（避免为一个派生值做迁移）。缩略图 / 对比图等其它 kind 为 `null`。
- **`ep_evidence` 仍为空数组**：真实 EP 证据表回填属 **T-700 展示增强**，本任务未做。
- **失败语义补充**（模型侧）：`.pth` / `.safetensors` 会被拒绝并给出 `needs_convert`
  提示"需离线转换为 ONNX"；可选后端（openvino / ncnn）未安装时给出 `runtime_missing` +
  可照做的安装建议（`detail.optional = true`）。前端文案应能区分
  **"制品残缺"（硬错误）**与**"可选后端未安装"（软提示）**。

**差异登记（T-805 实施产生，T-700 冻结时裁决）**

- **两个标定端点的响应体比 5D 草案更宽**（草案只写了"关键响应"）：

  ```jsonc
  // POST /api/system/calibrate  → 2026-10-09 T-805
  { "started": true, "task_id": "calib_1760012345", "status": "running" }
  // 已有作业在跑时（幂等，不叠加）：
  { "started": false, "reason": "已有标定在进行中",
    "job_id": "…", "status": "running", "progress": [...], "stored": false }

  // GET /api/system/calibration
  { "records": [ { "model_id": …, "recommended_tier": "T1", "tile_curve": {...},
                   "precision_decision": "fp32", "reason": "…", "valid": true,
                   "created_at": "2026-10-09T…" } ],
    "recommended_tier": "T1",          // 取**首条有效**记录，无则 null
    "reasons": ["…"],                  // 无有效记录时是"后续走保底档"的说明，而非空数组
    "state": { "status": "idle|running|skipped|done|error", "job_id", "started_at",
               "finished_at", "progress": [...], "error", "stored", "skipped_reason",
               "outcome" } }
  ```

  **`records[].valid`** 不是存储字段，而是**按当前硬件指纹现算**的判定（指纹不符即为 `false`）；
  前端据此区分"有记录"与"记录还有效"。**`reasons` 无记录时非空**（说明为何走保底档），
  前端不要把它当成"错误列表"。**建议冻结时把 `state` 正式写入 `GET /calibration` 契约**
  （否则前端无法显示"标定进行中"）。
- **标定结论是"控制面数据"而非"用户输入"**：`tile_curve` 的数值是**本机实测**，随硬件指纹失效，
  **不得**被前端当常量缓存或写回 `params`；前端只应展示与透传。
- **`simulation_enabled` 打开时的产出不入正式表**：`simulated: true` 的记录
  `is_storable()` 为假，不会出现在 `records` 里——这是有意为之，不是遗漏。

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
  "unavailable_reason": null,          // 不可用时的原因文案
  "conversion": null                   // ★ T-807 新增：仅 pth / safetensors 有值
                                       //   { "available": bool, "reason": str|null }
}
```

> **`min_vram_mb` 只用于可用性门控**：跑不动的置灰，**引擎不做模型推荐**（用户 2026-09-30 决定，PRD v1.8）。

**差异登记（T-807 实施产生，T-700 冻结时裁决）**

- **`ModelOut` 新增 `conversion` 子对象**（`{ available: bool, reason: str | null }`），
  **仅 `format ∈ {pth, safetensors}` 时有值**，其余格式为 `null`。理由：前端要能在**列表页**直接把
  "需转换"与"转换环境缺失"区分开，`available=false` + `reason` 给出可照做的说明。
  **建议冻结时正式补入 `ModelOut`（可空字段，非严格结构不破坏既有解析）。**
- **`POST /api/models/{id}/convert` 的响应比 5D 草案更宽**（草案只写了 `{ task_id }`）：

  ```jsonc
  // 202 Accepted
  { "started": true,  "task_id": "mdl_3", "status": "running" }
  // 已有转换在跑（幂等，不叠加）：
  { "started": false, "task_id": "mdl_3", "status": "running" }
  ```

  另补两个**失败码**（5D 草案未定义）：**`MODEL_NOT_CONVERTIBLE`（400）**——模型不是
  `.pth`/`.safetensors`；**`CONVERT_ENV_MISSING`（409）**——独立转换环境不存在（附三条安装命令）。
  口径与 T-806 一致：**"环境缺失"是状态不是异常**，用 `409` + 可照做的指引，而不是 `500`。
- **新增 `GET /api/models/{id}/convert`**（草案没有此端点）：

  ```jsonc
  { "job_id": "mdl_1", "status": "idle|running|done|error", "model_id": "mdl_1",
    "started_at": "…", "finished_at": "…",
    "result": { /* 转换产物的模型摘要，含新的 model_id */ } | null,
    "error": null,
    "source_model_id": "mdl_1",                        // 触发转换的源模型
    "availability": { "available": true, "reason": null } }
  ```

  ⚠️ 状态字段是**平铺**的（不套 `state` 子对象），字段语义与
  `GET /api/system/calibration` 的 `state` 区段同构，只是未嵌套。
  理由：转换是**数十秒级**离线作业（RRDBNet 实测），前端需要**轮询入口**；
  `availability` 与 `ModelOut.conversion` 共用同一份 `describe_availability()`。
- **转换产物是"新的 `.onnx` 模型"而非原模型的属性**：产物落
  `data/models/imported/<stem>__from<id>.onnx`，并**登记为独立的 `ModelOut`**
  （`format=onnx`、`source=imported`）；**原 `.pth` 模型保留不动**。重复触发**复用已有产物**（幂等定名约定）。
  前端应把二者**关联展示**（"来自 mdl_x 的转换产物"），**不要**假设 `id` 相同或原模型被替换。
- **转换的 `task_id` 与任务中心的 `task_id` 不在同一值空间**：转换作业**不落 `TASK` 表**
  （走文件系统 + 进程内状态），故**不能**拿去调 `/api/tasks/{id}`。前端只应把它当**不透明句柄**。

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
| POST | `/api/models/{id}/convert` | 触发离线转换（`.pth`/`.safetensors` → `.onnx`，**T-807 已实现**） | — | `202` `{ started, task_id, status }`（非可转换格式 `400`；转换环境缺失 `409`） |
| GET | `/api/models/{id}/convert` | 查询转换状态与结果（**T-807 已实现**） | — | `{ job_id, status, result, error, source_model_id, availability }`（**平铺**，不套 `state`） |
| GET | `/api/models/{id}/export` | 导出模型 | — | 二进制 |
| DELETE | `/api/models/{id}` | 删除模型 | — | `204` |

### 4.4 系统

| 方法 | 路径 | 用途 | 关键响应 |
|---|---|---|---|
| GET | `/api/system/capabilities` | 硬件探测结果 | `{ tier, device_facts: {...}, verified_backends: [...], ep_evidence: {...} }` |
| POST | `/api/system/calibrate` | 触发自标定（**S2**，**T-805 已实现**） | `{ started, reason?, task_id, status }`（已有作业时 `started=false` 并回现状） |
| GET | `/api/system/calibration` | 标定结果与理由（**S2**，**T-805 已实现**） | `{ records: [...], recommended_tier, reasons, state }` |
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

// 4) 心跳：保持连接（间隔与退避策略由 `T-700` 定值 —— 属后端实现参数）
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
| PRD §5 未定义错误码 | 本文件 §2.3 首次给出草案，**已定稿 16 条**（见 §7-3） |
| PRD §5 未定义 SSE 事件格式 | 本文件 §5 给出四类事件（快照 / 进度 / 终止 / 心跳）；事件与字段已定，心跳参数待 `T-700` |
| PRD §5 未定义任务日志端点 | **本文件原亦未定义**（`04` §8-6 指出的缺口）→ 由 `T-700` 补齐（见 §7-8） |
| 端点路径保持一致 | 未新增或删减端点，仅补充字段 |
| `POST /api/models/{id}/convert` | 来自 PRD v1.7 补充，路径一致 |

---

## 7. 冻结点（逐项状态 · 2026-10-08 核查）

**承接任务**：M4 `T-405`（可离线判定部分，已完成）→ **M6 `T-700`**（依赖后端产出的部分，待 5D 完成）

| # | 冻结点 | 状态 | 落点 / 阻塞原因 |
|---|---|---|---|
| 1 | 每个字段的类型、必填性、取值范围 | ✅ **已完成** | `web/src/types/api.ts`（302 行 / 26 个类型，逐字段落地） |
| 2 | 分页与筛选参数的全部取值 | ⏭ **待 `T-700`** | 常量形态已定（§1：`page/page_size`，筛选直接用字段名）；**全量取值**须与后端查询实现对齐，提前写死会造成规范与实现漂移 |
| 3 | 错误码全量清单 + 每种错误的界面文案对照表 | ✅ **已完成** | §2.3 **16 条** ↔ `web/src/api/errorMessages.ts` **16 条**（含未知错误码兜底），一一对应 |
| 4 | SSE 事件的精确定义（心跳间隔、重连退避、`Last-Event-ID` 支持与否） | ⏭ **待 `T-700`** | 四类事件与字段已定（§5）；**心跳间隔与重连退避是后端实现参数**，无后端无法定值 |
| 5 | 文件上传的大小上限与允许的 MIME 白名单 | ✅ **已完成** | `web/src/constants.ts` 的 `UPLOAD`（jpg/jpeg/png/webp/bmp/tif/tiff + 50 MB）+ `dev-info.md` 的 `APP_MAX_UPLOAD_MB` |
| 6 | 前端类型生成方案（由 OpenAPI 生成 TS 类型 vs 手写） | ⏭ **待 `T-700`** | 需先有 FastAPI 产出的 OpenAPI schema 才有可评估对象；当前手写类型已可用，引入代码生成需评估收益 |
| 7 | `resolved.reasons` 的文案规范（这些文案会直接展示给用户） | ⏭ **待 `T-700`** | `reasons` 由引擎**阶段 D** 生成（tech-arch §6.1）；引擎未实现则无法穷举文案 |
| **8** | **任务日志拉取端点**（`04` §8-6 暴露的缺口） | ⏭ **待 `T-700`** | `GET /api/tasks/{id}/logs` 尚未在本契约中定义；前端已备 `LogEntry` 类型但无端点可接。**本项为追加发现**，与冻结同时补齐 |

**结论**：3 项已闭合（1 / 3 / 5），5 项移交 `T-700`（2 / 4 / 6 / 7 / 8）。**5D 不再等待本文件的冻结版本**，按草案实现即可；`T-700` 须在 `T-701`（Mock → 真实接口）之前完成。
