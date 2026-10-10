# API 契约 v1.0（已冻结）

> **状态**：**v1.0 · 已冻结**（2026-10-10，任务 `T-700`） ｜ **最后更新**：2026-10-10
>
> **冻结依据**：5D 已收口（`T-601`~`T-609` + `T-802`~`T-807` + 前置验证 `T-801`），
> `T-608` 已导出机器可读形态 `docs/tech/api/openapi.json`（21 条路径）。
>
> **三处事实源必须同步**：本文件（规范）｜ `web/src/types/api.ts`（前端代码侧固化）｜
> `docs/tech/api/openapi.json`（后端机器可读形态）。三者由
> `scripts/test-script/verify_t700_contract.py` **静态断言**钉住，漂移即测试失败。
>
> **变更规则**：冻结后任何字段增删改，**必须同步改三处 + 更新本文件「变更记录」**。
> 冻结前各方按本文件「草案」实现的差异，已在 §7 逐项裁决并落到实现。

---

## 0. 冻结结论（T-700 逐项裁决）

| # | 冻结点 | 裁决 | 落点 |
|---|---|---|---|
| 1 | 每个字段的类型、必填性、取值范围 | ✅ **定稿**（本次**修正 `status` 取值**：以服务端枚举为准，前端 `done` → `completed`，删除不存在的 `pending`） | §3 |
| 2 | 分页与筛选参数的**全部取值** | ✅ **裁决：v1 不引入分页信封**，列表端点一律返回**裸数组**；筛选取值见 §4.0 | §1 / §4.0 |
| 3 | 错误码全量清单 + 界面文案对照表 | ✅ **定稿 20 条**（16 → 20：补 `NOT_FOUND` / `METHOD_NOT_ALLOWED` / `MODEL_NOT_CONVERTIBLE` / `CONVERT_ENV_MISSING`） | §2.3 |
| 4 | SSE 精确定义（心跳 / 重连退避 / `Last-Event-ID`） | ✅ **定稿**：心跳 **15 s**；**不发 `retry:`**（用浏览器默认）；**不支持 `Last-Event-ID`**（恢复靠 `snapshot`，见 §5.3） | §5 |
| 5 | 上传大小上限与 MIME 白名单 | ✅ **定稿**（沿用 5C 结论，无变更） | §4.2 |
| 6 | 前端类型生成方案（OpenAPI 生成 vs 手写） | ✅ **裁决：保持手写 `api.ts` 为唯一事实源 + 一致性校验脚本**（不引入代码生成，理由见 §9） | §9 |
| 7 | `resolved.reasons` 文案规范 | ✅ **定稿**：新增 §8，给出**允许文案集 + 书写规范**（从 `engine/runtime_profile.py` 现状提取） | §8 |
| 8 | **任务日志拉取端点**（`04` §8-6 缺口） | ✅ **定稿并实现** `GET /api/tasks/{id}/logs` → `LogEntry[]`（派生自任务事实，见 §4.1） | §4.1 |

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
| **分页** | **v1 不实现**：列表端点返回**裸数组** `T[]`（见 §4.0）。**不接收 `page` / `page_size`**，传了也忽略 |
| 筛选 | 直接以字段名作为查询参数（如 `?status=completed&type=upscale`），**取值白名单**见 §4.0 |
| 排序 | 服务端固定：任务列表 `created_at DESC, id DESC`；模型列表按登记顺序（内置在前）。**不支持自定义排序参数** |
| CORS | 开发期由 Vite 代理规避；生产若需直连，**白名单限定前端来源，禁止 `*`** |
| 单 worker 约束 | uvicorn **必须单 worker**（SSE 依赖进程内广播，见 ADR-005） |
| 列表规模假设 | v1 单机自用，任务/模型数量在**数百级**；这是"不引入分页"的前提（§4.0） |

---

## 2. 统一响应体

### 2.1 成功

直接返回资源对象或数组。**不额外包一层 `data`**。

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

> `detail` 是**开放字典**：契约不逐字段冻结（各错误按需填充）。前端**不得**依赖 `detail` 的具体形状，
> 只应原样透传给「查看日志」区段。

### 2.3 错误码全量清单（**定稿 20 条**）

| # | code | HTTP | 触发场景 | 界面落点 |
|---|---|---|---|---|
| 1 | `VALIDATION_ERROR` | 400 | 请求字段缺失或非法（含 `RequestValidationError`） | 表单内联错误 |
| 2 | `UNSUPPORTED_FORMAT` | 400 | 上传扩展名不在白名单 | 上传区内联错误 |
| 3 | `FORMAT_MISMATCH` | 400 | 扩展名与实际格式不符（服务端二次校验） | 上传区内联错误 |
| 4 | `FILE_TOO_LARGE` | 413 | 超过 `APP_MAX_UPLOAD_MB` | 上传区内联错误 |
| 5 | `NOT_FOUND` | 404 | **框架级**：请求了不存在的路由 | 兜底提示 + 刷新 |
| 6 | `MODEL_NOT_FOUND` | 404 | 指定的 `model_id` 不存在 **或模型文件在数据目录中缺失** | 参数面板错误 / 模型库 |
| 7 | `MODEL_MISSING_COMPANION` | 400 | `.bin` 导入时缺少 `.xml` / `.param` 配套文件 | 导入弹窗内联错误 |
| 8 | `MODEL_INCOMPATIBLE` | 400 | 模型与所选后端不兼容（含 `needs_convert` / `file_missing` / `companion_missing` / `runtime_missing` 四类制品/后端问题，**具体原因在 `detail`**） | 参数面板错误 |
| 9 | `MODEL_NOT_CONVERTIBLE` | 400 | **对非 `.pth`/`.safetensors` 调用转换** | 模型库内联提示 |
| 10 | `MODEL_INSUFFICIENT_VRAM` | 409 | 该模型的最低显存需求高于当前可用量 | 模型置灰原因（**前置到列表页**，不该等到提交才报） |
| 11 | `CONVERT_ENV_MISSING` | 409 | **离线转换环境未安装**（附可照做的安装命令，`detail.optional=true`） | 模型库转换按钮旁提示 |
| 12 | `VRAM_INSUFFICIENT` | 409 | 推理中显存不足且降档链已用尽 | 任务行「失败」+ 原因 |
| 13 | `RAM_INSUFFICIENT` | 409 | CPU 档物理内存不足 | 同上 |
| 14 | `TASK_NOT_FOUND` | 404 | 任务不存在 | 列表刷新 |
| 15 | `TASK_CANCELED` | 409 | 对已取消/已完成任务再次取消 | 静默忽略并刷新状态 |
| 16 | `TASK_ALREADY_RUNNING` | 409 | 并发上限为 1 时重复提交 | 提交按钮禁用 + 提示排队 |
| 17 | `METHOD_NOT_ALLOWED` | 405 | **框架级**：方法不允许 | 兜底提示 |
| 18 | `EP_FALLBACK_DETECTED` | 200（事件） | EP 未生效、发生静默回退 | 顶栏 EP 徽标变化 + 硬件面板标红 + 落盘 |
| 19 | `ENGINE_BUSY` | 503 | 引擎被占用且不接受新任务 | 提交按钮禁用 |
| 20 | `INTERNAL_ERROR` | 500 | 未预期异常 | 全局错误提示 + 诊断导出入口 |

> **前端对照表必须与上表 key 集合一致**：`web/src/api/errorMessages.ts`（未知码走通用兜底文案）。
> 后端在 `AppError` 抛出的码 + 框架级映射（404→`NOT_FOUND`、405→`METHOD_NOT_ALLOWED`、413→`FILE_TOO_LARGE`）
> 的并集，必须 ⊆ 上表。

---

## 3. 资源模型（v1.0）

### 3.1 Task

```jsonc
{
  "id": "tsk_01J8X...",
  "type": "upscale",                   // upscale | batch_upscale | face_restore | video
  "status": "queued",                  // ★ 取值见下方枚举（**服务端为准**）
  "progress": { "percent": 0.25, "current_item": 1, "total_items": 1,
                "current_chunk": 3, "total_chunks": 12 },
  "params": {                          // 请求值快照（"自动"档 tile/precision/backend 为 null）
    "scale": 4, "model_id": "mdl_3", "tile": null, "precision": null,
    "backend": null, "auto": true, "file_id": "f_..."   // ← file_id 快照，序列化提升为顶层
  },
  "resolved": {                        // ★ 实际生效值（"自动"档的落点）—— 只记这里，不记 params
    // ---- 决策事实（阶段 D）----
    "tile": 512, "precision": "fp16", "backend": "CUDAExecutionProvider",
    "overlap": 128, "feather_px": 128, // M4 分块/羽化的实际取值
    "concurrency": 1,                  // 决策出的并发上限（调度生效属 G-06）
    "using_fallback": true,            // 是否处于保底档
    "degraded": false,                 // 是否发生降档
    "source": "fallback",              // calibration | user | fallback
    "reasons": ["未标定，取保守下界参数"],   // 文案规范见 §8
    "downgrades": [                    // 逐条降档证据（"降级必须显式"）
      { "field": "tile", "from": 512, "to": 256, "reason": "连续 2 次资源水位超过 85%" }
    ],
    // ---- 执行事实（阶段 E，只有 completed 态有值）----
    "execution": {
      "output_width": 400, "output_height": 320,
      "source_width": 100, "source_height": 80,
      "tiles": 4, "scale": 4, "elapsed_ms": 1823,
      "size_bytes": 21491, "sha256": "9f3c…", "backend": "onnxruntime-cpu"
    }
  },
  "error": null,                       // 失败时填入统一错误体中的 error 对象
  "model_id": "mdl_3",                 // 列表展示用（详情以 params.model_id 为准）
  "model_name": "RealESRGAN_x4plus",
  "file_id": "f_...",                  // 由 params.file_id 提升（见下）
  "filename": "photo.jpg",
  "source_width": 100, "source_height": 80,
  "output_width": 400, "output_height": 320,   // ★ 取自 resolved.execution（终态后才有值）
  "duration_ms": 1823,
  "created_at": "2026-09-30T11:22:33+00:00",
  "started_at": "2026-09-30T11:22:34+00:00",
  "finished_at": null,
  "artifacts": [],                     // 或由 GET /api/tasks/{id}/artifacts 单独获取
  "ep_evidence": []                    // EP 证据（当前恒空，回填属后续增强）
}
```

**`status` 取值（**服务端枚举为准**，`models/entities.py::TASK_STATUSES`）**

| 取值 | 含义 | 语义色槽位 | 界面文案 |
|---|---|---|---|
| `queued` | 已落库并入队，等待调度 | primary | 排队中 |
| `running` | 执行中 | primary | 进行中 |
| `canceling` | 协作式取消中间态（**非终态**） | primary | 正在取消 |
| `completed` | 成功完成 | success | 已完成 |
| `canceled` | 已取消 | neutral | 已取消 |
| `failed` | 推理失败（含降档耗尽） | error | 失败 |
| `interrupted` | 进程退出导致的中断（**不是失败**，成因在外部） | warning | 已中断 |

> ⚠️ **v1 无 `pending`**：任务落库即 `queued`（`manager.submit`），不存在"未入队"的独立状态。
> ⚠️ **终态 = `completed` / `canceled` / `failed` / `interrupted`**（启动回收只扫 `running` / `canceling`）。
> ⚠️ **前端不得出现 `done`**：`done` 是 5C Mock 期的临时命名，v1.0 已统一为 `completed`。

**两个关键设计（来自 M2 评审定案）**

1. **`params` 与 `resolved` 分离**：`params` 是用户的**请求值**（"自动"档时 `tile` 为 `null`）；`resolved` 是引擎的**决策结果**。界面上的「高级参数」显示 `resolved`，「本次决策」显示 `resolved.reasons`。
2. **`status` 含 `canceling`**：协作式取消的中间态。前端据此显示「正在取消」而非「已取消」（详见 `docs/prototype/04-状态与交互定义.md` §2.5）。

**`resolved` 分层（T-804 / T-806 定稿）**

- **决策事实**（顶层：`tile` / `precision` / `backend` / `overlap` / `feather_px` / `concurrency` /
  `using_fallback` / `degraded` / `source` / `reasons` / `downgrades`）——引擎**决定**用什么。
- **执行事实**（`execution` 子对象）——**实际发生**了什么。刻意**不拍平到顶层**：
  T-804 的教训正是"决策与事实混在一层，会让人误以为决策即事实"。
- `execution` **可空，仅 `completed` 态有值**；`TaskOut.output_width/output_height` 取自它。
- `downgrades[].reason` 内的数值是**运行期真实值**，前端应**按字符串直出**，不要解析成结构化数字。

**序列化提升字段（T-606 定稿）**

- `file_id` 快照存于 `params.file_id`，序列化时**提升为顶层字段**（列表与详情都要用）。
- `filename` / `source_width` / `source_height` 由上传旁车元信息解析（T-605），缺失时为 `null`。
- chunk 级进度（`current_chunk` / `total_chunks`）**只存在于运行期内存**，任务结束后不再持久化；
  刷新后由下一个 `progress` 事件重建（前端类型已按此注释）。

**前端**附加字段（**不在服务端契约内**，前端渲染用，刷新后由 SSE 重建）：
`stage` / `stage_message`（阶段文案）、`source_thumb`（缩略图 URL，服务端可能不返回）。

### 3.2 Model

```jsonc
{
  "id": "mdl_01J8X...",
  "name": "RealESRGAN_x4plus",
  "architecture": "ESRGAN",            // 网络结构名（卡片副标题）
  "description": "通用 / 写实 · 高保真超分",
  "format": "onnx",                    // onnx | openvino_ir | ncnn | pth | safetensors
  "path": "data/models/RealESRGAN_x4.onnx",
  "sha256": "5c586662...b89c033",
  "size_bytes": 67051616,
  "params_count": 16697987,            // ★ 可空（读不出时为 null）
  "scale": 4,                          // ★ 可空
  "license": "BSD-3-Clause",           // ★ 可空
  "source": "builtin",                 // builtin | imported
  "status": "ready",                   // ★ ready | needs_convert | invalid（登记态，见下）
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
  "conversion": null                   // ★ 仅 pth / safetensors 有值：{ "available": bool, "reason": str|null }
}
```

> **`min_vram_mb` 只用于可用性门控**：跑不动的置灰，**引擎不做模型推荐**（用户 2026-09-30 决定，PRD v1.8）。

**命名/合成差异（T-604 定稿，均有断言钉住）**

- DB `param_count` → API **`params_count`**（契约命名）。
- DB 无 `architecture` / `description` / `capabilities` 列 —— 由服务层**合成**（内置模型查 catalog，导入模型按格式给默认值）。
- DB `companion_path`（单一相对路径）→ API `companion`（**扩展名数组**，形如 `[".xml", ".bin"]`）。
- `size_bytes` 不落库，响应时由文件 `stat` 合成。
- **`status` 是"登记态"**（`models/entities.py::MODEL_STATUSES`）：`ready` | `needs_convert` | `invalid`。
  与 `available`（"**这台机器此刻能不能跑**这份模型"的**综合判定**）**正交**：前者说"这份制品能不能加载"，后者说"这台机器能不能跑"。
  ⚠️ **加载期失败码**（`file_missing` / `companion_missing` / `runtime_missing`）**不是** `status` 取值——
  它们是**加载时的判定结果**，出现在 `MODEL_INCOMPATIBLE` 的 `detail.code` 里（§2.3 第 8 条）。
  前端渲染 `status` 时对未知值显示原值 + 中性色，不抛错。

- **`available` 的判定顺序**（`engine/availability.py::gate_availability`，唯一计算点；T-703 澄清）：
  ① 登记态（`needs_convert` / `invalid` → 置灰）→ ② **运行时**（该格式所需的运行时，如 `openvino` / `ncnn`，
  未安装 → 置灰）→ ③ 显存门槛（`min_vram_mb` vs **实读**可用显存）。
  三者任一不满足即 `available=false`，原因文案写入 `unavailable_reason`（**可照做**：含安装命令/所需显存）。
  ①与②是**确定性事实**，必须门控；③在**探测不到**显存时不门控（宁可可用，也不误灰）。
  依据 §2.3 第 10 条"能力缺口**前置到列表页**，不该等到提交才报"——运行时可缺性同理。

### 3.3 Artifact / LogEntry / 其它

```jsonc
// Artifact —— ★ kind 取服务端超集
{ "id": "art_...", "task_id": "tsk_...",
  "kind": "output",                    // input | output | thumb | log | model
  "path": "data/outputs/tsk_<id>/out.png",  // 对外统一带 data/ 前缀
  "filename": "out.png",
  "width": 400, "height": 320,         // 仅 kind=output 有值（取自 resolved.execution），其余 null
  "size_bytes": 21491, "sha256": "9f3c…", "created_at": "..." }

// LogEntry —— ★ T-700 新增端点返回类型
{ "level": "warning",                  // debug | info | warning | error
  "timestamp": "2026-10-09T12:00:00+00:00",
  "message": "RSS 超过水位 9.0 GB，tile 512 → 384",
  "code": "RAM_INSUFFICIENT" }         // ★ 可空：仅错误类条目带 code

// Preset（S2，未实现）
{ "id": "...", "name": "...", "params": {...}, "tier": "T1", "is_builtin": false }

// Setting
{ "key": "log_level", "value": "info", "type": "string" }   // type: string | number | boolean
```

- **`Artifact.kind`**：服务端取超集 `input | output | thumb | log | model`；前端 5C 类型为子集
  `output | intermediate`。**v1.0 前端对齐为服务端超集**（见 `api.ts`）。
- **`Artifact.path` 带 `data/` 前缀**：DB 存相对数据根的 `outputs/tsk_<id>/…`，对外统一输出 `data/outputs/…`
  （与 5C 前端已定稿的 Mock 约定一致）。
- **产物表不加宽高列**：`width`/`height` 取自 `resolved.execution`，避免为一个派生值做迁移。
- **Capabilities / DeviceFacts**：见 §4.4 的 `/api/system/capabilities`。

---

## 4. 端点（v1.0）

> 端点清单源自 PRD §5；下表为**冻结版**。状态列标注实现情况。

### 4.0 列表端点的筛选取值（冻结点 #2 定稿）

| 端点 | 筛选参数 | **取值白名单** | 排序（固定） |
|---|---|---|---|
| `GET /api/tasks` | `status` | `queued` / `running` / `canceling` / `completed` / `canceled` / `failed` / `interrupted` | `created_at DESC, id DESC` |
| | `type` | `upscale` / `batch_upscale` / `face_restore` / `video` | |
| `GET /api/models` | `format` | `onnx` / `openvino_ir` / `ncnn` / `pth` / `safetensors` | 登记顺序（内置在前） |
| `GET /api/tasks/{id}/artifacts` | — | 无筛选 | `created_at ASC` |
| `GET /api/tasks/{id}/logs` | — | 无筛选（前端按 `level` 本地过滤，默认只显示 `warning` 以上） | 时间升序 |

**分页裁决（冻结点 #2）**：**v1 不引入分页信封**。
- **理由**：单机自用，列表规模在数百级；一次拉取 + 前端本地筛选/渲染足够。
  引入分页会让 5C/5D 已冻结的 `Task[]` / `Model[]` 裸数组类型**全部返工**，收益不成比例。
- **不接收 `page` / `page_size`**：传了**静默忽略**（不报错），保证前端"多传参数"不炸。
- **演进路径**（若将来数据量增长）：改用 `Paged<T> = { items, total, page, page_size }`，
  **新增** `?page&page_size` 并保留**裸数组兼容期**；`web/src/types/api.ts` 已备 `Paged<T>` 类型。
- 因此 §1 的"分页"约定为**不实现**；`Paged<T>` 是**预留类型**，v1 未被任何端点使用。

### 4.1 任务

| 方法 | 路径 | 用途 | 关键请求 | 关键响应 | 状态 |
|---|---|---|---|---|---|
| POST | `/api/tasks` | 提交任务 | `{ type, file_id, params: {...} }` | `201` `Task`（**≤ 1 s 返回**，不含推理） | ✅ |
| GET | `/api/tasks` | 任务列表 | `?status&type` | `Task[]`（**裸数组**） | ✅ |
| GET | `/api/tasks/{id}` | 任务详情 | — | `Task`（含 `params` 与 `resolved`） | ✅ |
| GET | `/api/tasks/{id}/events` | **SSE 实时进度** | — | 事件流（见 §5） | ✅ |
| POST | `/api/tasks/{id}/cancel` | 取消任务 | — | `Task`（`status: "canceling"`） | ✅ |
| GET | `/api/tasks/{id}/artifacts` | 产物列表 | — | `Artifact[]` | ✅ |
| **GET** | **`/api/tasks/{id}/logs`** | **任务日志**（★ T-700 新增） | — | **`LogEntry[]`** | ✅ |

**`GET /api/tasks/{id}/logs` 语义（冻结点 #8）**

- 返回**由任务事实派生的结构化条目**（时间线 + 降档 + 错误），**不是**原始进程 stdout 抓取
  ——原始日志仍在服务端日志文件中，此处是"给用户看的排障摘要"。
- **派生规则**（`services/` 侧）：
  - `info`：任务创建 / 开始 / 完成（含模型、后端、耗时、产物）；
  - `warning`：每条 `resolved.downgrades[]`（降档必然显式）；保底档运行（`using_fallback`）；
    中断（`interrupted`）；
  - `error`：失败任务（`failed`）的 `error.code` + `error.message`，条目带 `code` 字段；
  - 无降档、无错误的正常任务返回**一条 `info`**（"本次任务无警告或错误记录"），**不返回空数组**
    （避免前端把"空"误读为"加载失败"）。
- **级别过滤由前端做**（`04` §2.3：默认只显示 `warning` 以上），服务端**不做** `?level=` 过滤。
- 任务不存在 → `TASK_NOT_FOUND`（404）。

### 4.2 文件

| 方法 | 路径 | 用途 | 关键请求 | 关键响应 | 状态 |
|---|---|---|---|---|---|
| POST | `/api/files/upload` | 上传图片 / 模型 | `multipart/form-data` | `{ file_id, filename, size, width, height, real_format }` | ✅ |
| GET | `/api/files/{id}/content` | 取文件内容 | `?variant=original\|result\|thumb` | 二进制（`Content-Type` 按实际格式） | ✅ |

> `real_format` 由服务端二次校验得出（**不信任扩展名**，PRD §3.2）。前端将其与扩展名比对，不符时给出明确提示。

**上传限制（冻结点 #5，沿用无变更）**：扩展名白名单 `jpg / jpeg / png / webp / bmp / tif / tiff`；
大小上限由 `APP_MAX_UPLOAD_MB` 配置（默认 50 MB）。

### 4.3 模型

| 方法 | 路径 | 用途 | 关键请求 | 关键响应 | 状态 |
|---|---|---|---|---|---|
| GET | `/api/models` | 模型列表 | `?format` | `Model[]`（**裸数组**，含 `available` / `unavailable_reason` / `conversion`） | ✅ |
| POST | `/api/models/import` | 导入模型 | `multipart`（`.bin` 必须同时带 `.xml` 或 `.param`） | `Model` | ✅ |
| POST | `/api/models/{id}/convert` | 触发离线转换（`.pth`/`.safetensors` → `.onnx`） | — | `202` `{ started, task_id, status }`（非可转换格式 `400 MODEL_NOT_CONVERTIBLE`；转换环境缺失 `409 CONVERT_ENV_MISSING`） | ✅ |
| GET | `/api/models/{id}/convert` | 查询转换状态与结果 | — | `{ job_id, status, model_id, started_at, finished_at, result, error, source_model_id, availability }`（**平铺**，不套 `state`） | ✅ |
| GET | `/api/models/{id}/export` | 导出模型 | — | 二进制 | ✅ |
| DELETE | `/api/models/{id}` | 删除模型 | — | `204` | ✅ |
| GET | `/api/models/download-catalog` | 可下载模型目录（T-713） | — | `{ items: DownloadCatalogEntry[], conversion }`，`items` 按效果优先序；每条含 `downloaded` / `downloaded_model_id` | ✅ |
| POST | `/api/models/download` | 从目录下载模型（**异步**） | `{ entry_id }` | `202` `{ started, status, entry_id }`；已下载过 `{ started:false, code:"already_downloaded", model_id }`；目录外 id `400 VALIDATION_ERROR` | ✅ |
| GET | `/api/models/download` | 下载作业状态 | — | `{ status: idle\|running\|completed\|failed, entry_id, received_bytes, total_bytes, model_id, conversion_triggered, error }` | ✅ |

**下载端点补充口径（T-713 定稿）**

- **目录是白名单**：下载 URL 只来自服务端 `download_catalog.py`（每条均经开发机实测：
  spandrel 识别 → ONNX 转换 → 同源自检 → 多尺寸正确性），请求方**只能传条目 id，不能传 URL**。
- **流程是链式的**：下载 → 复用 F-05 导入链登记 `.pth`（`status=needs_convert`）→
  **自动触发** T-807 应用内转换 → 产物登记为新 `.onnx` 模型。前端在
  `status=completed && conversion_triggered` 后改轮询 `GET /api/models/{model_id}/convert`。
- **转换环境缺失不是失败**：`.venvs/sr-convert` 不存在时模型保持 `needs_convert` 态
  （`conversion_triggered=false`），目录响应的 `conversion` 字段给可照做说明。
- **下载代理**：子进程 curl 依次取 `ALL_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY` 环境变量，
  并**剥离**子进程继承的代理变量（只认 `--proxy` 一个来源）。
- **幂等**：同一权重只登记一次（按源文件名 stem 匹配已登记 `.pth`），重复触发返回
  `already_downloaded` + 已有模型 id。

**转换端点补充口径（T-807 定稿）**

- **`POST` 幂等**：已有转换在跑时返回 `{ started: false, task_id, status: "running" }`，**不叠加**。
- **`GET` 是轮询入口**：转换是**数十秒级**离线作业，前端需轮询；`status ∈ idle | running | done | error`。
  字段语义与 `GET /api/system/calibration` 的 `state` 区段同构，只是**未嵌套**。
- **转换产物是"新的 `.onnx` 模型"而非原模型的属性**：产物落
  `data/models/imported/<stem>__from<id>.onnx`，登记为**独立的 `ModelOut`**（`format=onnx`、`source=imported`）；
  **原 `.pth` 模型保留不动**。重复触发**复用已有产物**（幂等定名约定）。
  前端应**关联展示**（"来自 mdl_x 的转换产物"），**不要**假设 `id` 相同或原模型被替换。
- **转换的 `task_id` 与任务中心的 `task_id` 不在同一值空间**：转换作业**不落 `TASK` 表**
  （走文件系统 + 进程内状态），故**不能**拿去调 `/api/tasks/{id}`。前端只应把它当**不透明句柄**。

### 4.4 系统

| 方法 | 路径 | 用途 | 关键响应 | 状态 |
|---|---|---|---|---|
| GET | `/api/system/capabilities` | 硬件探测结果 | `{ tier, tier_label, tier_reason, device_facts, verified_backends, ep_evidence, using_fallback, active_backend, active_precision, simulation }` | ✅ |
| POST | `/api/system/calibrate` | 触发自标定（**S2**） | `{ started, task_id, status }`；已有作业时 `{ started:false, reason, job_id, status, progress, stored }` | ✅ |
| GET | `/api/system/calibration` | 标定结果与理由（**S2**） | `{ records, recommended_tier, reasons, state }` | ✅ |
| GET | `/api/system/diagnostics` | 一键导出诊断 JSON（PRD §3.4） | 单个 JSON 文件（硬件事实 + EP 验证 + 标定 + `decision` + `watermark`） | ✅ |
| GET / PUT | `/api/settings` | 读写系统配置 | `{ items: Setting[] }` / 更新后的同结构 | ✅ |

**标定端点补充口径（T-805 定稿）**

```jsonc
// POST /api/system/calibrate → 202
{ "started": true, "task_id": "calib_1760012345", "status": "running" }
// 已有作业在跑时（幂等，不叠加）：
{ "started": false, "reason": "已有标定在进行中",
  "job_id": "…", "status": "running", "progress": [...], "stored": false }

// GET /api/system/calibration
{ "records": [ { "model_id": …, "recommended_tier": "T1", "tile_curve": {...},
                 "precision_decision": "fp32", "reason": "…", "valid": true,
                 "created_at": "2026-10-09T…" } ],
  "recommended_tier": "T1",            // 取首条有效记录，无则 null
  "reasons": ["…"],                    // 无有效记录时是"后续走保底档"的说明，而非空数组
  "state": { "status": "idle|running|skipped|done|error", "job_id", "started_at",
             "finished_at", "progress": [...], "error", "stored", "skipped_reason", "outcome" } }
```

- **`records[].valid` 不是存储字段**，而是**按当前硬件指纹现算**的判定（指纹不符即为 `false`）；
  前端据此区分"有记录"与"记录还有效"。
- **`reasons` 无记录时非空**（说明为何走保底档），前端**不要**把它当成"错误列表"。
- **`state` 已正式进契约**（否则前端无法显示"标定进行中"）。
- **标定结论是"控制面数据"而非"用户输入"**：`tile_curve` 数值是**本机实测**，随硬件指纹失效，
  **不得**被前端当常量缓存或写回 `params`；前端只应展示与透传。
- **`simulation_enabled` 打开时的产出不入正式表**：`simulated: true` 的记录 `is_storable()` 为假，
  不会出现在 `records` 里——有意为之，不是遗漏。

**档位模拟口径（T-901 定稿）**

```jsonc
// GET /api/system/capabilities → simulation 字段（**字段集未变**，取值改为真实计算）
{ "enabled": true, "force_tier": "T2" }
```

- `enabled` = **确实改变了判定输入**（总闸开但未声明任何强制项时为 `false`）。此时
  `tier` / `tier_label` / `tier_reason` 都是**模拟态**的取值，且 `tier_reason` 里必然出现
  「档位模拟」字样与**本机真实档位**——界面据此提示"模拟档位仅用于测试，不代表真实能力"。
- **`device_facts` 恒为真实探测值**（模拟只覆盖判定输入，不伪造硬件事实）；
  「真实 vs 模拟」的对照在 `GET /api/system/diagnostics` 的 `simulation` 区段。
- 强制项 `force_vram_mb` / `force_has_tensorrt` 与总闸 `simulation_enabled`、`force_tier`
  一样是**通用设置项**（`GET/PUT /api/settings` 的 `Setting` 列表），**不占用契约字段**；
  取值校验与引擎层解析器同源，非法值在保存时即被拒（`VALIDATION_ERROR`）。
- 生效范围：**档位推导 / 模型可用性门控（含列表页置灰与提交兜底）/ EP 候选链 / 任务决策输入**。
  模拟态下**不读写 EP 验证缓存**（避免把"带模拟成分"的结论写进真实硬件指纹的缓存）。

### 4.5 预设（S2，未实现）

| 方法 | 路径 | 用途 | 状态 |
|---|---|---|---|
| GET / POST / DELETE | `/api/presets` | 参数预设 CRUD（预设含 `tier` 字段，可绑定档位） | ⬜ S2 |

### 4.6 健康检查

| 方法 | 路径 | 用途 | 关键响应 | 状态 |
|---|---|---|---|---|
| GET | `/api/health` | 探活（骨架级） | `{ status: "ok", ... }` | ✅ |

> `/api/health` 是**骨架级探活端点**，不在 PRD §5 端点清单内。T-700 **确认其 v1 身份**：
> 供最小启动验证与部署探活使用（不参与业务），**不视为契约外泄漏**。

---

## 5. SSE 事件（**定稿**）

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

// 3) 终止：completed / canceled / failed / interrupted 之一，随后服务端关闭该连接
event: done
data: { "task": { /* 完整 Task 对象，含 resolved 与 error */ } }

// 4) 心跳：保持连接
event: ping
data: {}
```

### 5.1 心跳（冻结点 #4 定稿）

- **间隔 = `15.0 s`**（`api/events.py::PING_INTERVAL_SECONDS`）。**固定值，不做自适应**。
- 理由：本机连接、无中间代理，15 s 足以在 Nginx/浏览器默认超时（≥60 s）前保活，又不会刷屏。

### 5.2 重连退避（冻结点 #4 定稿）

- **服务端不发 `retry:` 字段** → 使用浏览器默认退避（Chrome/Firefox 约 **3 s**）。
- 理由：单机本地连接，**不引入后端可调参数**就等于不引入新的漂移点；显式 `retry:` 只会在
  "服务重启"窗口制造固定轮询，反而更差。
- 前端**不得**自行实现更激进的重连（避免重连风暴）；如需，须先改本节。

### 5.3 `Last-Event-ID`（冻结点 #4 定稿）

- **不支持**。服务端**不发送 `id:` 字段**，**不解析 `Last-Event-ID` 请求头**。
- 理由：SSE 事件是**状态快照（snapshot/progress）而非必须逐条重放的事务流**。
  丢失中间 `progress` 帧不影响正确性——**进度单调不减**，且断线重连后**连接即推 `snapshot`**
  （完整 Task 对象）即可恢复全部状态；终态由 `done` 事件或重新拉取 `GET /api/tasks/{id}` 保证。
- **终态任务的迟到订阅**：立即发 `snapshot` + `done` 两帧后关闭（断线恢复路径）。

### 5.4 前端映射规则（对应 `04` §2.2）

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
| PRD §5 未定义错误码 | 本文件 §2.3 给出 **20 条定稿**清单 |
| PRD §5 未定义 SSE 事件格式 | 本文件 §5 定义**四类事件**（快照 / 进度 / 终止 / 心跳），心跳与重连已定值 |
| PRD §5 未定义任务日志端点 | **本文件原亦未定义**（`04` §8-6 缺口）→ T-700 **补齐并实现** `GET /api/tasks/{id}/logs`（§4.1） |
| PRD §5 未定义健康检查端点 | `/api/health` 为**骨架级探活**，T-700 确认其身份（§4.6） |
| 端点路径一致性 | 未新增或删减业务端点，仅补充字段与日志端点 |
| `POST /api/models/{id}/convert` | 来自 PRD v1.7 补充，路径一致；T-807 补齐幂等与失败码 |

---

## 7. 冻结点（**8 / 8 已闭合** · 2026-10-10 冻结）

**承接任务**：M4 `T-405`（可离线判定部分）→ **M6 `T-700`**（本次全量冻结）

| # | 冻结点 | 状态 | 定稿值 / 落点 |
|---|---|---|---|
| 1 | 每个字段的类型、必填性、取值范围 | ✅ | §3（`web/src/types/api.ts` 为代码侧固化；**本次修正 `status` 取值**） |
| 2 | 分页与筛选参数的全部取值 | ✅ | §4.0：**v1 不引入分页**，裸数组；筛选取值白名单见 §4.0 |
| 3 | 错误码全量清单 + 界面文案对照表 | ✅ | §2.3 **20 条** ↔ `web/src/api/errorMessages.ts` 20 条 |
| 4 | SSE 精确定义（心跳 / 重连退避 / `Last-Event-ID`） | ✅ | §5.1/5.2/5.3：心跳 15 s；不发 `retry:`；不支持 `Last-Event-ID` |
| 5 | 文件上传大小上限与 MIME 白名单 | ✅ | §4.2（沿用 5C 结论） |
| 6 | 前端类型生成方案 | ✅ | §9：**手写 + 一致性校验脚本**，不引入代码生成 |
| 7 | `resolved.reasons` 文案规范 | ✅ | §8 |
| 8 | 任务日志拉取端点 | ✅ | §4.1 `GET /api/tasks/{id}/logs`（**已定义并实现**） |

---

## 8. `resolved.reasons` 文案规范（冻结点 #7 定稿）

> `reasons` **会直接展示给用户**（详情抽屉「本次决策」区段）。文案质量 = 产品可信度。

### 8.1 书写规范

1. **中文**，完整句；不省略主语/宾语，不用缩写黑话。
2. **不含设备型号**（ADR-004 原则 2）——说"该卡/该后端"，不写"RTX 3050"。
3. **不含本机实测常量**（tile 最优点、吞吐数值）——这些是**运行期求**出来的，不是文案常量。
   允许含**本次任务的真实数值**（如 `tile 512 → 384`），因为那是这次发生的事实。
4. **不出现内部实现术语裸奔**（如 `kSameAsRequested` / `DEFAULT_ALIGN`）。
5. **有序**：顺序 = 决策六段顺序（基线 → 用户覆盖 → 因果性防御 → 过渡 → 兜底 → 降档汇总）。
   前端**按序直出**，不排序、不解析、不拼接。
6. **可追加、不可依赖具体字符串**：前端只渲染文本，**不按内容分支**。新增文案无需前端改动。

### 8.2 允许文案集（从 `engine/runtime_profile.py` 现状提取，冻结为允许集）

| 段 | 文案模式 | 触发 |
|---|---|---|
| ① 基线 | `标定记录命中（硬件指纹一致）：{标定结论说明}` | 标定有效 |
| ① 基线 | `存在标定记录但与当前硬件指纹不符（或已失效）→ 回落保底档` | 指纹不符 |
| ① 基线 | `标定记录不可用 → 回落保底档` | 记录不可用 |
| ① 基线 | `未标定（阶段 C 属 S2），采用保底档保守下界参数` | 无记录 |
| ① 基线 | `档位模拟生效（{本次声明的覆盖项}）——执行结果不代表真实性能` | **档位模拟开启**（T-901；追加在决策之后，`engine/simulation.py` 产出） |
| ② 覆盖 | `用户指定参数（非自动档）：{tile、precision、backend 中被指定者}` | 手动档 |
| ② 覆盖 | `用户关闭了自动档但未指定具体参数 → 仍按基线取值` | 手动档缺值 |
| ② 覆盖 | `自动档：tile / 精度 / 后端由引擎决定（模型始终由用户选择）` | 自动档 |
| ④ 过渡 | `模型输入为固定 {N}×{N}，分块边长据其确定（读自 ONNX 输入约束）` | 静态输入模型 |
| ⑤ 兜底 | `没有任何后端通过 EP 真实性验证 → 任务不获得加速` | 无已验证后端 |
| ⑤ 兜底 | `决策过程异常（{异常类名}），退化为最保守参数以保可用` | 决策异常（永不抛异常的兜底） |
| ⑥ 降档 | `{原因}，但已到下界（无可再降的档位）` | 降档到下限 |

**降档条目**（同时进 `reasons` 与 `downgrades[]`，两者文本同源）：
- `tile`：`块尺寸必须不小于下界 {N} 且为模型对齐倍数 {M} 的整数倍`
- `precision`：`CPU 路径禁止 fp16（无 fp16 加速单元的桌面 CPU 上 fp16 反而更慢）` /
  `该卡 compute_cap < 5.3，不具备 fp16 单元，改用 fp32` /
  `无法确认该卡具备 fp16 单元（compute_cap 未知），改用 fp32` /
  `未知精度取值 {v!r}，按最保守的 fp32 处理`
- `backend`：`该后端未通过 EP 真实性验证（节点级归属）——静默使用会得到「看似加速实则更慢」的结果`

> **不在允许集的文案**属新增，须**同时更新本节**（契约与实现同源）。

---

## 9. 前端类型生成方案（冻结点 #6 **定案**）

### 9.1 决策

**保持手写 `web/src/types/api.ts` 为唯一代码侧事实源；不引入 OpenAPI → TS 代码生成。**

### 9.2 理由

1. **前端类型含服务端不返回的派生字段**（`Task.stage` / `Task.stage_message` / `Task.source_thumb`），
   代码生成会**丢失或被覆盖**，需要额外补丁层——反而更复杂。
2. **后端大量用 `dict`**（`params` / `resolved` / `error` / `detail`）→ OpenAPI 输出 `object`，
   **生成不出有用类型**；要生成就得先大改 Pydantic schema（把 `resolved` 全量建模），
   收益有限、风险不小（会把"宽松的字典"变成"严格的类型"，与"字段可增长"的设计冲突）。
3. **手写类型已 302 行、被 5C/5D 双向验证过**，且注释承载了大量设计意图（代码生成会丢）。
4. 代码生成的真正价值是**防漂移**；而防漂移用**一致性校验脚本**即可达成，**成本远低**。

### 9.3 一致性校验（**已实现**）

`scripts/test-script/verify_t700_contract.py` —— **纯静态解析，不启服务**，断言：

| 断言 | 数据源 |
|---|---|
| 契约 §4 端点集合 == `openapi.json` 的 paths（S2 未实现端点除外） | §4 ↔ `openapi.json` |
| `TaskOut` / `ModelOut` / `ArtifactOut` / `ProgressOut` 字段名 ⊆ `api.ts` 对应接口（前端可有额外派生字段） | `openapi.json` ↔ `api.ts` |
| **状态取值**：后端 `TASK_STATUSES` == 前端 `constants.ts::TASK_STATUS` == 契约 §3.1 表 | 三处 |
| **错误码**：契约 §2.3 表 == `errorMessages.ts` 的 key 集合（含未知码兜底） | 两处 |
| **`reasons` 文案**：允许集的模式串全部出现在 `runtime_profile.py` | §8 ↔ 实现 |

> 冻结后**任何漂移在此脚本失败**——这是"防漂移"落地的方式，替代代码生成。

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-10 | **v1.0.3（增补端点，T-713 模型下载）** | §4.3 新增三个端点：`GET /api/models/download-catalog`（可下载模型目录，效果优先白名单，每条含 `downloaded` 状态）、`POST /api/models/download`（异步下载→复用 F-05 导入链登记 `.pth`→自动触发 T-807 应用内转换）、`GET /api/models/download`（下载作业状态）。**只增不改**：既有端点字段集与错误码零变更；URL 仅来自服务端白名单（请求方只传条目 id）；代理取 `ALL_PROXY`/`HTTPS_PROXY`/`HTTP_PROXY` 环境变量 |
| 2026-10-10 | **v1.0.2（澄清 + 语义落地，不改变字段集）** | `T-901` 档位模拟开关落地。**`Capabilities.simulation` 的字段集不变**（仍是 `{ enabled, force_tier }`），但语义从「契约占位」变为**真实生效**：`enabled` 表示"确实改变了判定输入"（开了总闸但没声明任何覆盖项时为 `false`），`force_tier` 取自设置项。新增的强制项（`force_vram_mb` / `force_has_tensorrt`）走**既有的通用设置表**（`Setting` 是 `{key,value,type}` 通用结构，**无需契约变更**）。§8.2 允许文案集补一行（档位模拟的 `reasons` 追加文案）。**无字段增删、无错误码变更。** 同时**实现偏差修复**：此前 `simulation` 是写死的 `{enabled:false, force_tier:null}`，而前端在浏览器里自行改写 `tier` 并持久化到 localStorage —— 那是**纯客户端的假象**，后端决策/门控/降级链完全没走模拟档位（违反 PRD §2.3 原则 4 的本意）；现已改为服务端真实生效、前端只消费 |
| 2026-10-10 | **v1.0.1（澄清，不改变字段集）** | `T-703` 联调澄清 **`ModelOut.available` 的判定顺序**（§3.2）：原表述"按当前档位算的可用性"易被读成"只看显存"，实际语义是"**这台机器此刻能不能跑**"。现明确为 ①登记态 → ②**运行时** → ③显存门槛 三级，任一不满足即置灰且原因写入 `unavailable_reason`。**无字段增删、无错误码变更**；同时**实现侧对齐**：任务终态对模型加载类失败改用契约 §2.3 第 8 条既有的 `MODEL_INCOMPATIBLE` + `detail.code`（原实现误给 `INTERNAL_ERROR`，属**实现偏差修复**而非契约变更） |
| 2026-10-10 | **v1.0（冻结）** | `T-700` 全量冻结：8 冻结点全部闭合。① **`status` 取值修正**（`done` → `completed`，删除不存在的 `pending`）；② **分页裁决**（v1 不引入分页信封，裸数组）；③ **错误码 16 → 20**；④ **SSE 定值**（心跳 15 s / 不发 `retry:` / 不支持 `Last-Event-ID`）；⑤ **`resolved` 分层定稿**（决策事实 + `execution` 执行事实）；⑥ **`ModelOut` 补 `architecture`/`description`/`status`/`conversion`**；⑦ **`Artifact.kind` 取超集**；⑧ **新增日志端点** `GET /api/tasks/{id}/logs`（定义并实现）；⑨ **前端类型方案定案**（手写 + 一致性校验脚本）；⑩ **新增 §8 `reasons` 文案规范** |
| 2026-10-09 | 草案 | T-807 / T-805 / T-806 / T-804 实施差异登记（各项 `resolved` / 端点响应补全），均留待 T-700 裁决 |
| 2026-10-08 | 草案 | `T-405` 拆分：契约最终冻结时点移至步骤 6 之前（`T-700`）；本文件标注"草案" |
