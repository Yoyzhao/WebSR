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
| T-701 | Mock → 真实接口替换与字段对齐 | ⬜ 待开发 |
| T-702 | **S1 全链路真实数据打通**（上传 → 任务 → 推理 → 结果 → 对比） | ⬜ 待开发 |

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
