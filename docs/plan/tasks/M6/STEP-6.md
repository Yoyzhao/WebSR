# STEP-6 前后端整合（M6 · 步骤 6）

## 任务索引

- **里程碑**：M6（整合与联调）
- **阶段**：步骤 6（前后端整合）
- **进度入口**：`docs/plan/project-progress.md`（**调度状态唯一事实源**）
- **拓扑**：分离式前后端（前端 `web/` 已于 5C 验收；后端 `server/` 已于 5D 完成；本阶段把前端的 Mock 换成真实接口）
- **门控**：本阶段第一项 **`T-700`（契约 v1.0 定稿，P0）** —— 已完成（2026-10-10），是 `T-701` 的硬前置。

## 阶段任务清单

| 任务 ID | 一句话范围 | 详情 / 状态 |
|---|---|---|
| **T-700** | **API 契约 v1.0 定稿** + 前端类型生成方式定案 | ✅ **完成（2026-10-10，`verify_t700_contract.py` 33/33）** → [§T-700](#t-700-api-契约-v10-定稿) |
| **T-701** | Mock → 真实接口替换与字段对齐 | ✅ **完成（2026-10-10）** → [§T-701](#t-701-mock--真实接口替换与字段对齐) |
| **T-702** | **S1 全链路真实数据打通**（上传 → 任务 → 推理 → 结果 → 对比） | ✅ **完成（2026-10-10，`verify_t702_e2e.py` 直连 41/41 · 经代理 43/43）** → [§T-702](#t-702-s1-全链路真实数据打通) |

> 本表只用于阶段内任务定位。状态、优先级、调度依赖、阻塞和下一步以 `project-progress.md` 为准，不在此复制。

---

## T-700 API 契约 v1.0 定稿

### 目标与边界

#### 目标

把承接 M4 `T-405` 移交的四项「取值只能由后端与引擎产出」的冻结点（②分页与筛选全量取值 / ④SSE 心跳与重连退避 / ⑥前端类型生成方案 / ⑦`resolved.reasons` 文案规范），连同 `04` §8-6 暴露的**任务日志拉取端点缺口**，一并定值并冻结；`docs/tech/api-contract.md` 由「草案」升为 **`v1.0 · 已冻结`**，作为 `T-701` 及后续前端对齐的唯一契约依据。

#### 范围内

- `docs/tech/api-contract.md` 全量定稿（§0 冻结结论 → §8 `reasons` 文案规范 → §9 前端类型方案 + 变更记录）
- 三处事实源同步：契约文档 / `web/src/types/api.ts`（代码侧固化）/ `docs/tech/api/openapi.json`（机器可读）
- 后端**实现缺口补齐**：任务日志端点、405 错误码归位、OpenAPI 重新导出
- 前端对齐：类型 / 常量 / 错误文案 / client 端点 / 视图与组件
- **防漂移机制**：一致性校验脚本（替代代码生成）

#### 范围外

- Mock → 真实接口替换（`T-701`）；S1 端到端打通（`T-702`）
- 任务池中 `T-808`（P3 视频接口预留）/ `T-809`（P2 微调离线形态）—— 属 5D 后置项

### 关键裁决（8 项冻结点全部 ✅）

| # | 冻结点 | 定值 |
|---|---|---|
| ① | 统一错误体 | `error.code / message / suggestion / detail` 四段式，**错误码全量 20 条** |
| ② | 分页与筛选全量取值 | **v1 裸数组**（无分页信封）；筛选值域走**白名单**（§4.0） |
| ③ | 任务状态取值 | 服务端 **7 值**：`queued / running / canceling / completed / canceled / failed / interrupted`；**v1 无 `pending`**；**前端不得出现 `done`** |
| ④ | SSE 精确定义 | 心跳间隔 / 重连退避 / `Last-Event-ID` 三项定值（§5） |
| ⑤ | 任务日志拉取端点 | **`GET /api/tasks/{task_id}/logs`**（由任务状态**派生**，非落库日志） |
| ⑥ | 前端类型生成方案 | **手写 + 一致性校验脚本**（否决 OpenAPI 代码生成） |
| ⑦ | `resolved.reasons` 文案规范 | §8 允许集（字面前缀 + 变量位） |
| ⑧ | `resolved` 分层 | 决策事实（顶层）与执行事实（`resolved.execution`）**刻意分层** |

### 关键设计理由

#### ① 前端类型：为什么**不用** OpenAPI 代码生成

- 手写类型含**服务端不返回的派生字段**（前端视图层需要，如 `TaskResolved.overlap`/`feather_px`）；
- 后端大量使用 `dict`（如 `params`、`downgrades`），导致 OpenAPI 输出 `object`，**无类型价值**；
- 因此「防漂移」用**一致性校验脚本** `scripts/test-script/verify_t700_contract.py` 落地：断言「后端 schema 字段 ⊆ 前端接口字段」，而非生成。

#### ② 日志端点为什么**派生**而非落库

- 日志可由**任务已有状态**（`status` / `downgrades` / `error` / 时间戳）确定性派生 → 无需新增日志表；
- 无 warning/error 的正常任务**返回一条 info**（"任务完成"），**不返回空数组** —— 让前端始终有内容可渲染。

### 技术方案（落地摘要）

#### 后端

| 文件 | 变更 |
|---|---|
| `server/app/services/task_logs.py` | **新增** —— `derive_logs(task, model_name)` 由任务状态派生日志（info/warning/error 三级）；`model_name_for(session, task)` |
| `server/app/schemas/task.py` | **新增** `LogEntryOut`（`level` / `timestamp` / `message` / `code`） |
| `server/app/api/tasks.py` | **新增** `GET /{task_id}/logs` → `list_logs` |
| `server/app/core/errors.py` | **补 405 归位**（`METHOD_NOT_ALLOWED`） |
| `scripts/export_openapi.py` | 重新导出 → **19 条路径** |

> ⚠️ `task_logs.py` 内的 `_iso` **刻意本地复制**而非 `from ..tasks.manager import _iso`：`tasks/manager` 反向依赖 `services/*`，import 会形成模块环。

#### 前端

| 文件 | 变更 |
|---|---|
| `web/src/types/api.ts` | `TaskResolved` 补 `overlap`/`feather_px`/`concurrency`/`source`；`Model` 补 `status`/`conversion`；新增 `TaskExecution`/`ModelConversion`/`LogEntry`；`TaskParams` 补 `file_id?` |
| `web/src/constants.ts` | `TASK_STATUS` 去 `pending`/`done`；`STATUS_TONE`/`STATUS_LABEL` 同步 |
| `web/src/api/errorMessages.ts` | 16 → **20** 条（补 `NOT_FOUND`/`METHOD_NOT_ALLOWED`/`MODEL_NOT_CONVERTIBLE`/`CONVERT_ENV_MISSING`） |
| `web/src/api/client.ts` | 新增 `fetchLogs` 真实端点；Mock 与真实两侧同形 |
| `StatusText.vue` / `utils/viewModel.ts` / `stores/tasks.ts` / `TasksView.vue` / `WorkbenchView.vue` | `done` → `completed`；`intermediate` → `!= 'output'` |
| `web/src/api/mock/data.ts` / `mock/sse.ts` | Mock 字面量随之上对齐 |

### 验证证据

- `scripts/test-script/verify_t700_contract.py` —— **33 通过 / 0 失败**（6 组断言）：
  1. 错误码：契约 §2.3 == `errorMessages.ts` == **20 条**；
  2. 状态取值：后端 `TASK_STATUSES` == 前端 `TASK_STATUS` == 契约 §3.1（7 值，无 `done`/`pending`）；
  3. **端点对账**：契约 §4 标 ✅ 的路径 ⊆ `openapi.json`，标 ⬜ 的**不在**；
  4. 字段：`openapi` schema ⊆ `api.ts` 接口（`TaskOut`(21)⊆`Task`(24) 等 6 对）；
  5. `reasons` 文案：§8 允许集字面前缀均出现在实现中；
  6. 冻结点 §7 八项全部 ✅ 且契约标注 `v1.0 · 已冻结`。
- 回归：`verify_t606`（27/27）、`verify_t607`（14/14）、`verify_t608`（15/15）全绿。
- 前端：`npm run build`（含 `vue-tsc`）**通过**。

> ⚠️ **踩坑**：契约 §4 表面向人阅读用**短占位** `{id}`，FastAPI 路由用**参数实名** `{task_id}`/`{model_id}`/`{file_id}` → 端点对账脚本内做**占位归一化**（`\{[a-zA-Z_]\w*\}` → `{}`）后才一致。

### 诚实边界

- 契约冻结时点 = **步骤 6 之前**（保持既定口径，非 5D 之前）；
- 「契约冻结」≠「前端已接真实接口」 —— 那是 `T-701`/`T-702` 的范围；
- 校验脚本是**静态解析**（不启服务），只证明**三处事实源彼此一致**，不证明运行时行为。

---

## T-701 Mock → 真实接口替换与字段对齐

### 目标与边界

#### 目标

把前端自 5C 起一直依赖的 `USE_MOCK` 与 `web/src/api/mock/` **整体删除**，改用真实 HTTP 客户端
与真实 SSE；并把 5 个视图 / 3 个组件的调用点、字段口径对齐到契约 v1.0。

#### 范围内

- 删除 `web/src/api/mock/`（`data.ts` / `sse.ts`）
- `api/client.ts` 全量重写（真实 `fetch` + 统一错误体解析 + multipart + blob）
- **新增** `api/sse.ts`（真实 `EventSource`，四类事件）
- `stores/tasks.ts` 重写（进度订阅改真实 SSE；`submit` 签名简化）
- 视图/组件调用点对齐：`WorkbenchView` / `TasksView` / `ModelsView` / `SettingsView` /
  `TaskCard` / `TaskDetailDrawer` / `ModelImportDialog`
- 后端补齐契约声明但未实现的 `variant=result`

#### 范围外

- 任何契约变更（v1.0 冻结，本轮**不动**）
- 重试 / 删除任务的端点（属 S2 F-12）→ 见下方范围决策

### 关键裁决

| # | 议题 | 裁决 |
|---|---|---|
| ① | Mock 层如何处置 | **整体删除**，不留运行期回退开关。理由：真实与模拟混用会让排障时的每个结论都不可信 |
| ② | 前端有入口但契约无端点的两个功能（重试 / 删除清理） | **界面入口置为不可用态并注明「S2 待实现」** —— 不改冻结契约、不扩范围、**不静默无效** |
| ③ | `variant=result` | 契约 §4.2 已声明支持 → **补后端实现**（不是改契约、不是前端降级） |

### 技术方案（落地摘要）

#### 后端

| 文件 | 变更 |
|---|---|
| `server/app/services/media_store.py` | 新增 `result_path_for_file(file_id)`：按 `file_id` 找**最近一次 `completed` 任务**的 `output` 产物；`get_content()` 补 `variant=result` 分支（命中回图，未命中 404 `NOT_FOUND`「该图片还没有成功的超分结果」） |

> ⚠️ `result_path_for_file` 内**函数级 import** `db`/`models.entities`：模块顶层 import 会与 `tasks/manager` 形成环。

#### 前端

| 文件 | 变更 |
|---|---|
| `api/client.ts` | 移除 `USE_MOCK`；新增 `requestForm` / `requestBlob` / `parseError`；导出 `fileContentUrl` / `normalizeTask` / `downloadFile` / `fetchCalibration`；`createTask(fileId, params)` 提交体只传 `{type, file_id, params}`（`file_id` 由后端自填进 `params` 快照） |
| `api/sse.ts` | **新增**：`subscribeTaskProgress()` 监听 `snapshot`/`progress`/`done`/`ping`，`done` 后自动 `close()` |
| `stores/tasks.ts` | 移除 `retryTask` / `removeTasks` / `clearFinished` / `updateTask`；新增 `refresh(id)`（打开抽屉兜底同步一次） |
| `views/WorkbenchView.vue` | `previewImageUrl` 回落真实原图 URL；提交走真实 `submit`；下载走真实 `downloadFile`；**默认模型 id 去假值 + 认 `?model=` 深链**；重试入口禁用标注 S2 |
| `views/TasksView.vue` | 删除内联 SVG 对比示意图源 → 改真实 `original`/`result` 两个 URL；「清理已完成」「重试」禁用标注 S2 |
| `views/SettingsView.vue` | 「打开日志」取最近一条真实任务（不再写死 `tsk_01J8X004`） |
| `views/ModelsView.vue` | 导入时按扩展名分派主文件（非 `.bin`）与配套文件（`.bin`）→ `companion_file` |
| `components/TaskCard.vue` | 重试按钮禁用 + `title` 标注；中断提示文案改为「重试能力属 S2 阶段，暂不可用」 |
| `components/TaskDetailDrawer.vue` | 重试 / 删除按钮禁用标注 S2；中断提示同步 |
| `types/api.ts` | `source_thumb` 注释修正为 `?variant=thumb`（此前写 `/thumb`，与实际不符） |
| `api/mock/` | **删除** |

### 验证证据

- `npm run build`（含 `vue-tsc -b`）**通过**（构建产物正常，无类型错误）。
- 全量后端回归 **776 项断言全绿**（含 `verify_t605_media` 一条**旧断言与契约不符**的修正）。
- `verify_t702_e2e.py` 覆盖 `variant=result` 正向路径（200 + `image/png`）。

> ⚠️ **踩坑**：`verify_t603_startup.py` 会**自起 8000 端口**服务；若开发后端正在运行，
> 子进程绑定失败 → 迁移没跑 → 「数据库文件已创建」等断言误报。跑全量回归前先停开发后端。

### 诚实边界

- 重试 / 删除清理两个入口**只是禁用 + 标注**，无后端能力（S2）。
- 未做浏览器级 UI 验证（见 `T-702` 诚实边界）。

---

## T-702 S1 全链路真实数据打通

### 目标与边界

#### 目标

证明 S1 主链路（**上传 → 建任务 → SSE 进度 → 推理 → 产物 → 对比 → 下载**）在真实后端上端到端可用，
并覆盖异常链路（受控 4xx 后服务仍可用）。

#### 范围内

- 真实推理路径的契约行为（进度语义 / 终态字段 / 产物 / 日志 / 文件内容三变体）
- 异常链路：统一错误体三要素、缺参 400、不存在资源 404
- 标定消费口径（命中记录 → 取值等于记录显式给出的推荐值）
- 浏览器侧链路：经 Vite 代理的 SSE 必须为**流式**

#### 范围外

- 真实照片的人眼质量复核（「无可见接缝」）—— 长期挂账项
- 浏览器级 UI 渲染 / 交互验证（未安装 Chromium，见诚实边界）

### 关键裁决

| # | 议题 | 裁决 |
|---|---|---|
| ① | 端到端脚本放哪 | **`scripts/test-script/verify_t702_e2e.py`**（长期回归资产），而非一次性临时脚本 |
| ② | 是否硬编码本机实测值（如 tile=256） | **不硬编码**：期望值一律**从接口推导**（读 `/api/system/calibration` 记录的 `tile_curve.recommended_tile`）；找不到记录就 `SKIP` 而非失败 |
| ③ | 是否需要浏览器运行时 | **不安装 Chromium**（项目纪律「绝不全局安装」）→ 用等价方式实测浏览器侧最高风险项（代理转发 + SSE 流式） |

### 关键发现：真实推理路径的 `percent` 非单调（🔴 高）

| 项 | 内容 |
|---|---|
| **现象** | 帧序 `0.0 → 1.0 → 0.0286 → …`：进度条在**预处理阶段即冲到 100%**，随后回落到 1/35 |
| **判据** | 契约 §5 / NFR §3.1 明确「**percent 必须单调不减**」→ **真实违约** |
| **根因** | `engine/pipeline.py` 把预处理经**分块计数通道**上报为 `(1, 1)`；服务端 `percent = (progress_done + current_chunk/total_chunks) / total_items`，`total_items=1` → `(0 + 1/1)/1 = 1.0` |
| **为何两轮都没发现** | `verify_t607_sse.py` **注入 `StubExecutor`**（控制面验证与真实推理解耦）→ 只验证了假执行器自造的进度序列，**从未跑过真实 `pipeline._emit`** |
| **修复** | 非分块阶段上报 `(0, 0)`（= 「无块语义」）：`pipeline._emit(req, 0, 0, "preprocessing", …)` 与 `executor.ctx.report_progress(0, 0, "preprocessing", …)` |
| **为何 `(0,0)` 而非其他** | `stitching` / `saving` 上报 `(total, total)` 是**正确**的（块确实已全部完成，percent 保持 1.0 不回落）；只有「尚未开始分块」的预处理不该有块计数 |
| **回归防线** | ① `verify_t806_loader.py` 追加**机制级断言**（预处理必须 `total=0`、不得出现 `done>=total>0`）；② `verify_t702_e2e.py` 断言**可观测契约**（`percent` 单调不减 + 预处理不得假 100%） |

#### 修复前后（同一脚本、同一环境）

```
修复前： progress percent = [1.0, 0.0286, 1.0, 1.0, 1.0]      ← 首帧即 100%，违约
修复后： progress percent = [0.0, 0.0286, 0.6, 1.0, 1.0, 1.0]  ← 单调不减 ✅
```

### 验证证据

| 脚本 / 手段 | 结果 |
|---|---|
| `verify_t702_e2e.py`（直连 `:8000`） | **41 通过 / 0 失败 / 0 跳过** |
| `verify_t702_e2e.py --base http://127.0.0.1:5173`（经 Vite 代理） | **43 通过 / 0 失败 / 0 跳过** |
| 经代理 SSE 流式性 | 帧分散到达，跨度 **3.533 s**（非一次性缓冲） |
| 全量后端回归 | **776 项断言全绿** |

#### 实测关键数据（RTX 3050 8G · T1 · 320×240 → 1280×960）

| 场景 | 结果 |
|---|---|
| 未标定模型（`mdl_2` 等） | `source=fallback` · `using_fallback=true` · `tile=64` · 35 块 |
| 已标定模型（`mdl_1`，记录 `recommended_tile=256`） | `source=calibration` · `using_fallback=false` · `tile=256` ✅ 等于记录推荐值 |
| 执行事实 | `resolved.execution.backend.providers = [CUDAExecutionProvider, CPUExecutionProvider]` |
| 文件内容三变体 | `original` 3 796 B(png) / `thumb` 20 948 B(jpeg) / `result` 1 297 995 B(png) |

### 诚实边界

- **`Task.ep_evidence` 在 S1 恒为空**（契约 §3.1 明示"回填属后续增强"）：任务详情抽屉的
  「EP 执行证据」区段在 S1 只会显示「无证据记录」；真实 EP 证据在
  `GET /api/system/capabilities.ep_evidence`（实测非空，含 `node_count`/`cpu_node_count`）。
- **未做浏览器级 UI 验证**：未安装 Chromium。已用等价方式实测浏览器侧最高风险两项
  （Vite 代理转发、SSE 穿越代理为流式）；组件渲染/交互正确性由 5C 闭环验证覆盖。
- **标定按模型生效**：未标定模型走保底档是**设计**（匹配判据 = 硬件指纹 + 模型）；
  换机器/换驱动/换 ORT 组合都会使记录失效。
- **`resolved.reasons` 的「阶段 C 属 S2」措辞过时但不可改**：被契约 §8.2 允许文案集冻结
  + `verify_t700_contract.py` 钉住 → 记为**契约债**，v1.1 修订项。
- **画质未验收**：本轮只答「能出图 / 多快 / 链路通」，**未做人眼画质复核**。
