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
| **T-702** | **S1 全链路真实数据打通**（上传 → 任务 → 推理 → 结果 → 对比） | ✅ **完成（2026-10-10，`verify_t702_e2e.py` 直连 41/41 · 经代理 43/43；浏览器级补测 81/81）** → [§T-702](#t-702-s1-全链路真实数据打通) |
| **T-703** | **主链路系统联调（单图超分端到端）+ 画质与接缝验收** | ✅ **完成（2026-10-10，`verify_t703_quality.py` 38/38；全量 **857** 项断言 0 失败）** → [§T-703](#t-703-主链路系统联调--画质与接缝验收) |

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

---

## T-702 浏览器级补测（Playwright）

### 为什么补这一轮

`T-702` 主体验证跑在 HTTP 层（`verify_t702_e2e.py`）。**HTTP 层过了不等于浏览器里能用**：
DOM 是否真的渲染、`EventSource` 是否真的建立、`<img>` 是否真的解码成功、禁用态是否真的不可点、
SPA 路由参数是否真的被读取 —— 这些只有真浏览器能答。
本轮用**系统既有的 Playwright**（npx 缓存 `1.57.0`）+ **系统已装的 Chrome/Edge**（`channel`，
无需下载 Chromium、无需全局安装）补齐。

### 新增验证资产

| 路径 | 说明 |
|---|---|
| `.workbuddy/verify/ui/run_ui_e2e.cjs` | 浏览器级 E2E 主脚本（14 个段落、81 项断言），自带夹具与截图 |
| `.workbuddy/verify/ui/fixtures/` | 两张夹具图（320×240 快任务 / 1600×1200 用于取消） |
| `.workbuddy/verify/ui/shots/` | 逐步截图与人眼可核对证据（下载产物不入库） |

运行：`node .workbuddy/verify/ui/run_ui_e2e.cjs`（前后端需已启动；`--headed` 可肉眼观察）。

### 🔴 最有价值的发现：取消任务后界面**永久停在「正在取消」**

| 项 | 内容 |
|---|---|
| **现象** | 点「取消」并确认后，任务行的状态显示 `取消 0%` / `正在取消`，**再也不会变成「已取消」**，除非手动刷新页面 |
| **根因** | `web/src/stores/tasks.ts::cancel()` 在收到 `POST /cancel` 响应后**立即 `es.close()` 退订 SSE** |
| **为何这是缺陷** | 取消是**协作式**的：后端只把 `running` 任务置为中间态 `canceling`，真正的终态 `canceled` 由工作线程在块间自检后写入，**再过 `_publish_done` 广播 `done`**（`tasks/manager.py:376-389`）。提前退订等于**扔掉了唯一会带来终态的那条消息** |
| **修复** | `cancel()` 不再无条件退订：把收口交给既有的 `onDone`（它本就在终态帧后关闭连接）；仅当取消响应本身已是终态（`queued → canceled` 那条路径）才当场收口 |
| **为何 5C/5D/HTTP 层都没发现** | HTTP 层脚本自己轮询 `GET /api/tasks/{id}` 判终态，**根本不经过前端 store 的订阅生命周期**；而控制面测试注入 `StubExecutor`，也不覆盖这条路径 |

### 其他被浏览器级测试逮到的缺陷

| 缺陷 | 影响 | 修复 |
|---|---|---|
| `TasksView`/`WorkbenchView` 取消失败无反馈 | 任务若在确认弹窗这几秒内已结束，后端返回 `409 TASK_CANCELED` → 抛未处理的 Promise 拒绝，**界面毫无反应** | 两处加 `try/catch` 并给出 warning 提示 |
| 系统配置页文案「首启自标定属 S2 阶段；当前所有自动档任务均使用保守下界参数」 | **事实错误**（阶段 C 已落地且已在生效：已标定模型 `source=calibration`）→ 用户会误判自己的参数来源 | 改为真实口径：「按『硬件指纹 + 模型』匹配，命中用实测参数、未命中退回保底档」 |
| 硬件页标定成功提示「首启自标定属 S2 阶段，当前仍使用保底档」 | 同上 | 改为「标定已触发；完成后『自动档』将按本机实测结果取值」 |
| 诊断 JSON 的 `calibration.note` 仍称标定为 S2 占位能力 | 导出给外部排障时传递错误结论 | 改为描述真实匹配与回退口径 |
| 后端多处注释断言「阶段 C 属 S2，尚未落地 / 表恒为空 / `using_fallback` 恒 true」 | 会误导后续维护者（代码早已不是这个状态） | `capabilities.py` / `system_info.py` / `engine_decision.py` / `settings_store.py` / `fallback.py` / `api/system.py` / `client.ts` 逐处更正 |

> ⚠️ **改不了的那一条**：`resolved.reasons` 的「未标定（阶段 C 属 S2）」被契约 §8.2 允许文案集
> **冻结**且被 `verify_t700_contract.py` 钉住 → 仍记为**契约债**（v1.1 修订项）。本轮只改
> **未被契约冻结**的文案，不动冻结契约。

### 验证证据（`run_ui_e2e.cjs`，81 项断言）

| 段落 | 覆盖 | 结果 |
|---|---|---|
| 0 启动基线 | 外壳/路由标题/真实模型下拉/默认模型 id 非硬编码/顶栏档位徽标 | ✅ |
| 1 上传 | 真实 multipart、尺寸/大小/格式、×4 目标分辨率推导 | ✅ |
| 2 提交 + 真实 SSE | 提交返回 `tsk_`；**按 task_id 收口**；帧序 `snapshot→progress×5→done`；`percent=[0,0.5,1,1,1]` 单调不减；chunk 口径；UI 渲染百分比亦单调 | ✅ |
| 3 预览 | `<img>` 真正解码（`naturalWidth>0`）、指向 `variant=original` | ✅ |
| 4 表格门控 | 「清理已完成」禁用 + `title` 标注 S2；已完成行「对比/下载」可用、无「重试」 | ✅ |
| 5 对比滑块 | 双图真实解码，结果图恰为原图 **×4**；键盘 ←/→/Home 与指针拖拽均可调整 | ✅ |
| 6 下载 | 触发真实下载、PNG 魔数校验（1 317 035 B） | ✅ |
| 7 异常链路 | 非法格式被前端拦下、**不发任何请求**、不进待提交态 | ✅ |
| 8 取消 | 运行态出现「取消」→ 确认 → 服务端 `done(canceled)` 到达 → **界面自动落到「已取消」**；「重试」禁用且点击不产生请求 | ✅ |
| 9 详情抽屉 | 任务事实渲染、删除禁用 + 标注 S2、对比可用 | ✅ |
| 10 配置 | 日志弹窗取真实任务日志（非假 id）；诊断 JSON 含 `device_facts/ep_evidence/probe/ep_verification/calibration/summary`；过时文案检查 | ✅ |
| 11 模型库深链 | 「使用此模型」→ 工作台下拉**真的**变成该模型 | ✅ |
| 12 硬件页 | 档位事实 / EP 证据区 / 档位模拟（标注「模拟」） | ✅ |
| 13 兜底路由 | 未知路径显示空态而非白屏 | ✅ |
| 14 全局健康度 | 0 页面异常 / 0 `console.error` / 0 API 4xx-5xx / 0 真实请求失败 | ✅ |

**`net::ERR_ABORTED` 不是失败**：`done` 后客户端 `es.close()`、导航时中断在途 `<img>` 都会产生它。
把主动取消当失败会让「正确的关闭行为」看起来像缺陷 —— 判据只抓连不上 / 超时 / 协议错。

### 诚实边界（本轮新增）

- **未做人眼画质复核**：本轮新增的是「浏览器里点得动、看得见、数据对」，**不是**「画面好看」。
- **`queued` 即刻取消那条路径未被浏览器实测覆盖**：快任务几乎不会停在 `queued`，
  该分支由 `verify_t806_loader.py` 的机制级断言覆盖。
- **Playwright 复用系统环境**：脚本按候选路径探测 playwright 包（可用 `PW_ROOT` 覆盖）
  并走系统 Chrome/Edge；这是为遵守「绝不全局安装」而做的取舍，换来的是**本机可跑但非零依赖**。

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
- ~~浏览器级 UI 渲染 / 交互验证~~ → **已于「T-702 浏览器级补测（Playwright）」补齐**

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
- **浏览器级 UI 验证已在本轮补齐**（见「T-702 浏览器级补测（Playwright）」）：
  复用系统既有 Playwright + 系统 Chrome/Edge（不下载 Chromium、不全局安装），81 项断言全绿。
  注意其边界：**「能点、能看、数据对」≠「画质已验收」**。
- **标定按模型生效**：未标定模型走保底档是**设计**（匹配判据 = 硬件指纹 + 模型）；
  换机器/换驱动/换 ORT 组合都会使记录失效。
- **`resolved.reasons` 的「阶段 C 属 S2」措辞过时但不可改**：被契约 §8.2 允许文案集冻结
  + `verify_t700_contract.py` 钉住 → 记为**契约债**，v1.1 修订项。
- **画质未验收**：本轮只答「能出图 / 多快 / 链路通」，**未做人眼画质复核**。

---

## T-703 主链路系统联调 · 画质与接缝验收

### 为什么这一项不能省

`T-702` 打通了**通路**，但它（以及此前所有验证）回答的都是「**能出图 / 多快 / 链路通 / 数据对**」。
`T-703` 回答的是那个从头挂账到现在的另一个问题：**「画得好不好」**。
PRD §2.7 写明「`F-11` 按**无可见接缝**做质量检查」、§2.5 `F-01` 要求「尺寸 = 原图 × 倍数（误差 0）、
色彩与输入一致（无通道错乱）」——**本轮第一次真正去验这两条**。

### 目标与边界

**范围内**：内置模型矩阵端到端联调；`F-01` 尺寸/色彩客观验收；`F-11` 接缝验收（客观 + 人眼）；
与官方参考实现的交叉校验；缺运行时模型的可观测性。
**范围外**：异常与降级链（`T-704`）、取消与并发边界（`T-705`）、逐条测试用例（`T-706`/M7）。

### 新增验证资产

| 路径 | 说明 |
|---|---|
| `scripts/test-script/verify_t703_quality.py` | 主脚本（8 段 / 38 项断言），走**真实后端 + 真实推理 + 真实产物** |
| `.workbuddy/verify/t703/fixtures/` | 真实照片夹具（**不入库**，来源与许可见该目录 `README.md`，一条命令重建） |
| `.workbuddy/verify/t703/out/` | 人眼复核素材：并排对比图、1:1 放大裁剪、**接缝裁剪条**（仅接缝条入库） |

运行：`.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t703_quality.py`（需先启动后端；
`--fast` 跳过耗时的大图接缝段）。

#### 判据设计纪律

**优先用相对/自指判据，少用绝对阈值** —— 绝对阈值会随图像内容漂移，容易变成"调参调到绿"：

| 判据 | 形式 |
|---|---|
| 色彩无通道错乱 | **互换反证**：输出面积平均回源尺寸后，恒等映射误差必须**小于** R↔B 互换映射误差 |
| 无周期性接缝伪影 | **双跑一致性**：同图用不同 tile 各跑一次，两次差异**不得沿任一方块网格呈周期性结构**（ratio < 1.25） |
| 块边界无突变 | 边界带梯度 vs **同图块内基线**的比值（ratio < 1.30） |
| 管线正确性 | 与 **官方参考实现**产物比对（PSNR），而不是自证 |

### 内置模型矩阵（真实链路逐模型）

夹具 `photo-00003`（512×256 写实）等；`resolved` 取值**一律取自任务返回**，不硬编码本机实测值：

| 组合 | 产物 | tile | overlap/feather | source | 块数 | 耗时 |
|---|---|---|---|---|---|---|
| `photo-00003` × `mdl_1`（x4plus） | 2048×1024 | 256 | 64 / 64 | `calibration` | 3 | 2 602 ms |
| `photo-00003` × `mdl_2`（fp16） | 2048×1024 | 64 | 16 / 16 | `fallback` | 55 | 4 256 ms |
| `photo-00003` × `mdl_4`（anime_6B） | 2048×1024 | 64 | 16 / 16 | `fallback` | 55 | 1 437 ms |
| `photo-00003` × `mdl_5`（general-x4v3） | 2048×1024 | 64 | 16 / 16 | `fallback` | 55 | 496 ms |
| `anime-OST_009`(448×640) × `mdl_4` | 1792×2560 | 64 | 16 / 16 | `fallback` | 117 | 3 102 ms |
| `ncnn-input2`(256²) × `mdl_1` | 1024×1024 | 256 | 64 / 64 | `calibration` | 1 | 981 ms |
| `ncnn-input`(220²) × `mdl_1` | 880×880 | 256 | 64 / 64 | `calibration` | 1 | 816 ms |

**7/7 完成**；`F-01` 尺寸**逐条精确等于源 ×4（误差 0）**，色彩互换反证**全部为恒等**。

### 画质验收：三层判据

1. **客观（自动断言）**：尺寸精确 ×4；色彩恒等映射优于互换映射。
2. **官方参考交叉校验（最强的一条）**：把本项目 ONNX 管线的输出，与 **Real-ESRGAN 官方 ncnn
   可执行文件**对**同一张图**的 ×4 产物逐像素比对（`exe_out_x4.png` 是 T-210 留下的同源参考）——
   实测 **PSNR = 38.20 dB、平均绝对差 0.99、最大通道均值差 0.25**。
   → 说明我们的 ×4 结果与官方实现**结构一致**，而不是"自己看着还行"。
3. **人眼复核（1:1 裁剪，非缩放全图）**：
   - 写实照片：细枝结构**真实恢复**（不是把模糊放大），无伪影、无串色；
   - 动漫插画（`mdl_4`）：线条干净锐利、色块平整洁净；
   - 轻量模型（`mdl_5`）：细节不及主模型但显著优于输入，色彩保持。
   ⚠️ **方法论教训**：缩放的整幅并排图会因显示重采样产生**观感假象**（曾据此误判"沙变灰"，
   而两半的通道均值差 < 1）。**画质结论必须建立在 1:1 裁剪 + 客观指标上**。

### 接缝验收（`F-11` 无可见接缝）

夹具 `fronalpstock-1600`（**1920×863** 真实风景），强制多块：`tile=256` → **50 块**、`tile=128` → **180 块**。

| 判据 | 结果 |
|---|---|
| 两次运行输出尺寸一致 | 3452×7680 ✅ |
| **差异不沿 `tile=256` 网格呈周期性** | 网格列 1.18 / 其余 1.02 → **ratio 1.153**（阈值 1.25）✅ |
| **差异不沿 `tile=128` 网格呈周期性** | 网格列 1.17 / 其余 1.02 → **ratio 1.142**（阈值 1.25）✅ |
| `tile=256` 块边界梯度 vs 块内 | 4.074 / 4.297 → **ratio 0.948**（n=9，阈值 1.30）✅ |
| `tile=128` 块边界梯度 vs 块内 | 3.980 / 4.106 → **ratio 0.969**（n=19，阈值 1.30）✅ |
| **人眼**（边界裁剪条 ×3，100%~200%） | 岩壁与林木纹理**连续穿过 tile 边界**，无亮度台阶、无条带 ✅ |
| **人眼**（整幅 7680×3452） | 无条带、无网格、无可见接缝 ✅ |

> 边界梯度比 **< 1**（0.948 / 0.969）意味着**块边界处并不比块内部更"陡"** —— 这是"羽化确实生效"的
> 直接证据，比"看起来没缝"更硬。

### 🔴 本轮逮到的两个真实缺陷（同一根因：**运行时缺失没有被当成一等状态**）

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| 1 | 内置 `RealESRGAN_x4 (OpenVINO IR)` 在未装 `openvino` 的环境里被判为**可用**，用户选中后**必然失败** | `engine/availability.py::gate_availability` 只看「登记态」与「显存门槛」，**不含运行时维度** | 增加运行时判定：`status_for_format(fmt)` 不可用即置灰，`unavailable_reason` 直接给出**可照做的安装命令** |
| 2 | 上述失败的任务终态是通用 `INTERNAL_ERROR / 推理执行失败 / 请导出诊断 JSON`，**真正原因与修法全被丢掉** | `tasks/manager.py::_fail_task` 把所有非 OOM 异常一律压成内部错误，**丢弃了 `ModelLoadError` 自带的 `code/message/reason`** | 保留结构化错误，按**契约 §2.3 第 8 条**输出 `MODEL_INCOMPATIBLE` + `detail.code = runtime_missing` + 可照做的 `suggestion` |

**为什么这是"缺陷"而不是"设计"**：契约 §2.3 第 8 条**明文规定**
`needs_convert / file_missing / companion_missing / runtime_missing` 四类要出现在
`MODEL_INCOMPATIBLE` 的 `detail.code` 里；§2.3 第 10 条又要求能力缺口
「**前置到列表页**，不该等到提交才报」。**两处实现都与已冻结契约相悖**，属实现偏差修复。

修复后实测：

```json
{ "code": "MODEL_INCOMPATIBLE",
  "message": "openvino_ir 格式所需的运行时「openvino」未安装",
  "suggestion": "在当前环境执行 `uv pip install openvino` 后重启后端，即可加载 OpenVINO IR 模型",
  "detail": { "format": "openvino_ir", "runtime": "openvino", "optional": true,
              "code": "runtime_missing", "exception": "ModelLoadError: ..." } }
```

**为什么此前八轮全绿都没发现**：`verify_t604_models.py` 的断言
`IR status=ready 且可用` **把缺陷固化成了期望** —— 它假设"登记为 ready 就等于能用"。
本轮把该断言改为**环境感知**（`available` 必须与本机 `openvino` 是否安装一致），
断言数 41 → **43**。

**附带影响**：内置 5 个模型中，**应用环境内实际可用 4 个**；OpenVINO IR 需
`uv pip install openvino` 后重启后端。前端**无需改动** —— `ModelCard` 早已按
`available === false` 置灰并显示 `unavailable_reason`。

### 验证证据

| 项 | 结果 |
|---|---|
| `verify_t703_quality.py` | **38 通过 / 0 失败 / 0 跳过** |
| 官方参考交叉校验 | **PSNR 38.20 dB**，平均绝对差 0.99，最大通道均值差 0.25 |
| 离线全量回归（15 脚本） | **778 通过 / 0 失败**（`t604` 41 → 43） |
| 在线 E2E | `verify_t702_e2e.py` **41/41**（含标定消费口径）；`verify_t703_quality.py` **38/38** |
| **合计** | **857 项断言，0 失败** |

### 诚实边界

- **画质是"人眼 + 客观双判"，不是"已认证"**：本轮只覆盖 Reale-ESRGAN 三风格 + 轻量模型 ×
  5 张公开样图；**未做**有参考真值（GT）的 PSNR/SSIM 评测，也**未覆盖**低照度/压缩噪声/人脸等
  难例。人眼复核由**单一评审者（本 Agent）**完成，**非多人盲评**。
- **官方交叉校验只能证明"与官方一致"，不能证明"官方就是好的"**。
- **接缝验收只覆盖 `tile=256` / `tile=128` 两档**（本机标定的 `recommended_tile` 附近）；
  更小 tile（保底档 64）下的边数更多，未逐档人眼复核。
- **`T-704` / `T-705` 仍未做**：异常与降级链（OOM 降档 / EP 回退 / 保底档可见）、
  取消与并发边界（≤2 s 生效、资源释放）本轮**未覆盖**。
- **`T-704` 有隐性前置**：OOM 降档与显存水位在真机 T1 上**造不出触发条件**，
  需先有 `T-901` 档位模拟（`force_vram` 等）。
- **`resolved.reasons` 的「阶段 C 属 S2」措辞过时但不可改**（契约 §8.2 冻结 + 脚本钉住）→ **契约债**（v1.1）。
