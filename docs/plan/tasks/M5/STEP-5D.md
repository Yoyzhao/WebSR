# STEP-5D 后端骨架与业务 + 引擎核心

## 任务索引

- **里程碑**：M5（前后端实现）
- **阶段**：5D（后端开发）
- **进度入口**：`docs/plan/project-progress.md`
- **项目拓扑**：分离式（前端 `web/` 已于 5C 验收；后端 `server/` 本阶段新建）

## 阶段任务清单

| 任务 ID | 一句话范围 | 详情 |
|---|---|---|
| T-601 | 后端工程初始化（FastAPI + 配置 + 日志 + 统一错误体 + 单 worker 约束） | [§T-601](#t-601-后端工程初始化) |
| T-602 | 数据模型与 Alembic 迁移（6 张表） | [§T-602](#t-602-数据模型与-alembic-迁移) |
| T-603 | 运行时基线与启动序列（DLL 注册 / `running`→`interrupted` 回收 / 能力探测非阻塞） | [§T-603](#t-603-运行时基线与启动序列) |
| T-604 | M3 模型库：`.onnx` 主路径 + sha256 + 元信息 + 导入/导出/删除 | [§T-604](#t-604-m3-模型库) |
| T-605 | M5 媒体管理：上传二次校验真实格式 / 落盘 / 缩略图 | [§T-605](#t-605-m5-媒体管理) |
| T-606 | M1 任务中心：队列 / 状态机 / 协作式取消 / 历史 / 启动回收 | [§T-606](#t-606-m1-任务中心) |
| T-607 | SSE 进度推送（节流 ≤1s / item+chunk 两级 / 终止事件 / 断线恢复） | [§T-607](#t-607-sse-进度推送) |
| T-608 | API 路由层 + 统一错误体 + 诊断 JSON 导出 + **导出 OpenAPI schema（供 T-700）** | ✅ 完成（15/15，见文末） |
| T-609 | M7 系统配置（F-07，含保留策略清理）；**F-09 参数预设经核对属 S2，本轮不做**（见 §T-609） | ✅ 完成（47/47，见文末） |
| T-802 | M4 图像处理：预处理 / 分块 / overlap + feather 拼接（质量红线，须重写） | ✅ 完成（87/87，见文末） |
| T-803 | 引擎阶段 A/B：能力探测 + **EP 真实性验证**（profile 节点归属 + 缓存） | ✅ 完成（2026-10-09，76/76 + 真机 CUDA 取证） |
| T-804 | 引擎阶段 D/E：决策 Profile + 水位反馈与降级 + 保底档 | ✅ 完成（2026-10-09，见文末） |
| T-805 | 引擎阶段 C：首启自标定（S2） | ✅ 完成（2026-10-09，63/63；真实 GPU 标定落库） |
| T-806 | 模型加载器：`.onnx` / IR(`.xml`+`.bin`) / ncnn(`.param`+`.bin`) / 待转换格式 + **真实推理编排** | ✅ 完成（2026-10-09，121/121；含跨环境 IR/ncnn 真实推理复核） |
| T-807 | `.pth`/`.safetensors` → `.onnx` 离线转换工具（依赖分离） | ✅ 完成（2026-10-09，63/63 + 工具级探针 23/23；见 [§T-807](#t-807-pth--safetensors--onnx-离线转换adr-003-方案-a)） |
| T-808 | 视频超分后端接口预留（P3，不实现管线） | 进入时展开 |
| T-809 | 微调支持（离线形态）：数据集打包导出 + 训练脚本模板 + 回灌入口 | 进入时展开 |
| T-901~T-910 | 档位能力与高端/专业档（P3，排在 S1/S2 之后） | 进入时展开 |

> 本表只用于阶段内任务定位。状态、优先级、调度依赖、阻塞和下一步以 `project-progress.md` 为准，不在此复制。

## T-601 后端工程初始化

### 目标与边界

#### 目标

`server/` 可启动：FastAPI 工程骨架就位，配置（`APP_*` 环境变量）、日志、统一错误体三件套落地，uvicorn 单 worker 约束在启动方式与代码注释中显式化，为 T-602（建库迁移）与 T-603（启动序列）提供挂载点。

#### 范围内

- `server/pyproject.toml`（uv 项目，依赖声明；**环境落到既有 `.venvs/sr-app`**，不新建 venv）
- `server/app/` 目录骨架：`main.py` + `core/`（config / logging / errors）+ 空包占位 `api/` `engine/` `models/` `schemas/` `tasks/`
- 统一错误体（`error.code / message / suggestion / detail`）与全局异常处理（`AppError` / 请求校验错误 / HTTP 异常 / 未预期异常）
- CORS 白名单（`APP_CORS_ORIGINS`，禁止 `*`）；服务绑 `127.0.0.1`
- 骨架级 `/api/health` 探活端点（供最小启动验证；契约身份待 `T-700` 定稿时确认）
- 最小启动验证：服务起得来、探活返回、错误体形态正确

#### 范围外

- 建库 / Alembic 迁移（T-602）；DLL 路径注册、`running`→`interrupted` 回收（T-603）
- 任何业务路由（T-604~T-608）；推理运行时依赖（onnxruntime / openvino，随 T-803 进入）
- OpenAPI schema 导出文件（T-608 一并产出；FastAPI 自带的 `/openapi.json` 此时已可用）

### 技术方案

#### 影响位置

- **服务端路由/服务/API**：`server/app/main.py`、`server/app/core/`（新建）
- **数据模型/迁移/存储**：无（本任务不碰数据库）

#### 实现说明

1. **依赖管理**：`server/pyproject.toml` 声明依赖 + `[tool.uv] package = false`；用 `UV_PROJECT_ENVIRONMENT=<项目根>/.venvs/sr-app uv sync` 把依赖装进既有应用环境（遵守「严禁全局安装」与「`sr-app` 为应用唯一依赖环境」约定）。`sqlalchemy==2.1.4` / `alembic==1.20.0` 按 ADR-006 实测版本固定；fastapi / uvicorn / pydantic-settings 取解析版本并回填 `dev-info.md`。
2. **配置**：`pydantic-settings`，`env_prefix="APP_"`，env_file 指向项目根 `.env.dev`；`APP_DATA_DIR` / `APP_DB_URL` 的相对路径**一律相对项目根解析**（后端进程 cwd 是 `server/`，不解析会错写到 `server/data/`）。
3. **错误体**：严格按 `api-contract.md` §2.2 三要素（`code` + `message` + `suggestion`，`detail` 可选）。`RequestValidationError` → `VALIDATION_ERROR`(400)；未匹配路由 404 → `NOT_FOUND`（tech-arch §5.2 有此码，前端走未知码兜底，T-700 再核对是否入册）；未预期异常 → `INTERNAL_ERROR`(500) 并落日志。
4. **单 worker**：SSE 依赖进程内广播（ADR-005 / tech-arch §5.1），uvicorn 禁止多 worker。落点：启动命令不含 `--workers`（默认即 1），并在 `main.py` 顶部注释与启动日志中显式声明，防止后人误加。
5. **时区**：存储一律 UTC（T-602 起由 `UTCDateTime` 保证）；日志时间戳用本机时间（`Asia/Shanghai`）。

#### 权威文档引用

- **API 契约**：`docs/tech/api-contract.md` §1（通用约定）/ §2（统一响应体）
- **技术架构**：`docs/tech/tech-arch.md` §5.1（单 worker / CORS / 绑定）/ §5.2（错误体）/ §6.6（启动序列，T-603 前置）
- **环境事实**：`docs/tech/dev-info.md` §3（目录入口）/ §4（端口）/ §5.1（`sr-app`）/ §6（环境变量键名）
- **项目规则**：`docs/rules/project-rules.md` §2.1（环境隔离）/ §2.3（CORS / 绑定）/ §2.4（端口）

### 实施步骤

1. 建 `server/pyproject.toml` 与 `server/app/` 骨架（含空包占位）
2. 实现 `core/config.py`（APP_* 读取 + 相对路径归一到项目根）
3. 实现 `core/logging.py`、`core/errors.py`（`AppError` + 三类全局 handler）
4. 实现 `main.py`（lifespan 挂配置与日志；CORS；`/api/health`；单 worker 注释）
5. `uv sync` 安装依赖到 `.venvs/sr-app`，回填 `dev-info.md` §2 版本
6. 最小启动验证（见下）

### 技术输入与交付

- **输入产物**：ADR-001 / ADR-005 / ADR-006；`.env.dev`；`.venvs/sr-app`
- **输出产物**：`server/` 工程；`dev-info.md` 版本回填；本文件阶段结果
- **对接阶段**：T-602（数据模型挂到 `app/models/`）、T-603（启动序列挂到 lifespan）

### 验证与验收

- **验证步骤**：
  1. 启动：`cd server && uv run --active uvicorn app.main:app --reload --port 8000`（或等价 `.venvs/sr-app` 直调）
  2. `GET /api/health` → 200 且 JSON 含 `status`
  3. `GET /api/nonexistent` → 404 且响应体为统一错误体（含 `error.code/message/suggestion`）
  4. `POST /api/health` 触发 405、非法 JSON 触发错误体不崩
  5. 确认监听地址为 `127.0.0.1:8000`（非 `0.0.0.0`）
- **质量门控**：终端无启动报错与异常堆栈；`/openapi.json` 可访问（T-608 导出前置）
- **验收标准**：`server/` 可启动，探活与错误体形态验证全部通过
- **证据落点**：本文件「阶段完成结果」+ 终端输出摘录

### 风险与恢复

- **风险**：`uv sync` 默认会按 lock 清理环境外包——`sr-app` 现有包均为 sqlalchemy/alembic 及其传递依赖，声明后不会被清
- **兼容性**：无（全新目录）
- **回滚/恢复**：删除 `server/` 即可；`sr-app` 环境可用 `uv pip install` 单独恢复

## T-602 数据模型与 Alembic 迁移

### 目标与边界

#### 目标

6 张业务表（`task` / `artifact` / `model` / `preset` / `setting` / `calibration`）的 SQLAlchemy 实体 + Alembic 初始迁移落地，空库可建、可重复、可回滚；ADR-006 的 11 条 SQLite 适配约束全部落到产品代码。

#### 范围内

- `app/db.py`：`make_engine()` 单一工厂（PRAGMA 唯一注入点，运行时/迁移共用）+ 会话工厂
- `app/models/base.py`：`Base`（约束命名约定）+ `UTCDateTime`（aware 强制 / naive 拒写 / 读出贴 UTC）
- `app/models/entities.py`：6 表实体 + Python 层枚举常量（DB 不设 CHECK）
- `server/migrations/`：`env.py`（batch 模式 + 迁移期 FK=OFF + URL 从 `APP_DB_URL` 注入）+ `versions/0001_initial.py`
- `server/pyproject.toml` `[tool.alembic]`（不用 ini —— 中文 Windows GBK locale 必崩）
- 验证脚本 `scripts/test-script/verify_t602_db.py`

#### 范围外

- 迁移并入启动序列（`alembic upgrade head` 排在状态回收前 —— 属 T-603）
- 任何业务读写逻辑（T-604 起）；保留策略清理（T-609）

### 技术方案

#### 影响位置

- **数据模型/迁移/存储**：`server/app/db.py`、`server/app/models/`、`server/migrations/`、`data/app.db`

#### 实现说明

1. **ADR-006 约束落点对照**：1/2/3 → `migrations/env.py`；4 → `pyproject.toml [tool.alembic]`；5 → `0001_initial.py` 手工补 `UTCDateTime` import；6 → `env.py` 经 `get_settings()`；8/9 → `app/db.py`；10/11 → `app/models/`。
2. **与 ER 的两处实现级差异**：① `TASK.error` 存统一错误体 **JSON 对象**（ER 的 `error_message` 字符串是早期简写，契约 §3.1 `Task.error` 为对象）；② `ARTIFACT.kind` 取 ER 超集（input/output/thumb/log/model），前端类型当前为子集（output/intermediate），归并口径列入 T-700 核对。
3. **枚举取值与前端已定稿类型对齐**（`web/src/types/api.ts`）：`queued/running/canceling/completed/canceled/failed/interrupted`、`onnx/openvino_ir/ncnn/pth/safetensors` 等；取值约束在 Python 层，新增枚举值无需迁移。
4. **索引**按 ADR-006 附录 B：`task(status, created_at)`、`artifact(task_id)`、`model.sha256` 唯一、`calibration(model_id, hardware_fingerprint, valid)`；MetaData 带命名约定（batch 重建表可按名引用约束）。
5. **外键策略**：`artifact.task_id` → `ON DELETE CASCADE`；`task.model_id` / `task.calibration_id` / `calibration.model_id` → `ON DELETE SET NULL`（历史任务与标定记录不随模型删除而丢）。

#### 权威文档引用

- **技术架构**：`docs/tech/tech-arch.md` §4.1 / §4.2
- **ADR**：`docs/tech/arch/ADR-006-ORM与迁移方案.md`（11 条约束 + 附录 B 索引清单）
- **API 契约**：`docs/tech/api-contract.md` §3（资源模型字段口径）

### 实施步骤

1. `app/db.py`（`make_engine` + PRAGMA connect 事件 + 会话工厂）
2. `app/models/base.py`（Base + UTCDateTime）与 `entities.py`（6 表）
3. `migrations/env.py` + `versions/0001_initial.py` + `pyproject [tool.alembic]`
4. `alembic upgrade head`（建表）→ 二次 upgrade（幂等）→ `downgrade base` + upgrade（可逆）
5. 断言脚本验证 17 项

### 技术输入与交付

- **输入产物**：ADR-006 + orm-smoke 验证工程；tech-arch §4
- **输出产物**：`server/app/db.py`、`server/app/models/`、`server/migrations/`、`scripts/test-script/verify_t602_db.py`、`data/app.db`（空库可用）
- **对接阶段**：T-603（迁移并入启动序列）、T-604~T-609（业务读写）

### 验证与验收

- **验证步骤**：`cd server && ../.venvs/sr-app/Scripts/python.exe -m alembic upgrade head`；`./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t602_db.py`
- **验收标准**：迁移幂等 + 可逆；17 项断言全通过；脚本可重复执行
- **证据落点**：本文件「阶段完成结果」§T-602

### 风险与恢复

- **风险**：batch 重建表成本随数据量线性增长（ADR-006「后果」已评估，任务元信息表很小，可接受）
- **兼容性**：全新库，无旧数据
- **回滚/恢复**：`alembic downgrade base`（已实测）；或直接删除 `data/app.db*` 重建

## T-603 运行时基线与启动序列

### 目标与边界

#### 目标

tech-arch §6.6 启动序列落地：第 1 步阻塞（迁移 → DLL 注册 → `running`/`canceling` 回收为 `interrupted`），第 2 步非阻塞（阶段 A/B/C 调度挂载点）；数据库不可用拒绝启动，DLL 失败降级不阻断。

#### 范围内

- `app/engine/runtime_env.py`：`tools/_runtime_env.py` 逻辑的产品化重写（产品代码不 import `tools/`，tech-arch §6.7）
- `app/core/lifecycle.py`：`run_startup_blocking()`（迁移 + DLL + 回收，结果写 `app.state.startup_report`）+ `schedule_startup_nonblocking()`（占位，T-803~T-805 接入）
- `main.py` lifespan 接入
- 验证脚本 `scripts/test-script/verify_t603_startup.py`（临时数据目录，不碰真实库）

#### 范围外

- 阶段 A/B/C 的真实实现（T-803~T-805）；`onnxruntime.preload_dlls()`（随推理依赖进入）
- 任务队列本身（T-606）；诊断 JSON 导出消费 `startup_report`（T-608）

### 技术方案

#### 影响位置

- **服务端**：`server/app/core/lifecycle.py`、`server/app/engine/runtime_env.py`、`server/app/main.py`

#### 实现说明

1. **迁移以编程方式执行**（`alembic.config.Config` + `command.upgrade`），不走 CLI 子进程：避免依赖 CLI 对 pyproject 的探测行为，且迁移连接与运行时复用同一 `make_engine()`（ADR-006 约束 9）；迁移排在状态回收**之前**（约束 7）。
2. **DLL 注册必须在 import 推理库之前**；`runtime_env.py` 自身不 import onnxruntime/openvino（T-803 才引入），当前 `sr-app` 无推理依赖时候选目录为 0 属预期。
3. **回收幂等**：`UPDATE ... WHERE status IN ('running','canceling')`，二次执行更新 0 行；`finished_at` 一并落库（UTC）。
4. **失败分级**：DB 失败 → `RuntimeError("…拒绝启动…")` 让 uvicorn 退出；DLL 部分失败 → warning + 降级仅 CPU；第 2 步任何失败 → 只记日志（保底档兜底，§6.8）。

#### 权威文档引用

- **技术架构**：`docs/tech/tech-arch.md` §6.6（启动序列）/ §6.7（与 tools/ 的关系）/ §6.8（保底档）
- **ADR**：ADR-005（实施约束 2 状态回收）/ ADR-006（约束 7 迁移先于回收）

### 实施步骤

1. `engine/runtime_env.py`（候选目录枚举 + `add_dll_directory` + PATH 前置，非 Windows no-op）
2. `core/lifecycle.py`（迁移 / 回收 / 报告 / 非阻塞挂载点）
3. `main.py` lifespan 接入
4. 验证脚本三场景：A 空库自动迁移；B 回收 + 幂等；C DB 不可用拒绝启动

### 技术输入与交付

- **输入产物**：`tools/_runtime_env.py`；ADR-005 / ADR-006；tech-arch §6.6
- **输出产物**：上述 3 个产品文件 + `scripts/test-script/verify_t603_startup.py`
- **对接阶段**：T-803（第 2 步真实逻辑）、T-606（任务队列）、T-608（`startup_report` 入诊断导出）

### 验证与验收

- **验证步骤**：`./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t603_startup.py`
- **验收标准**：13 项断言全通过；真实配置启动冒烟通过
- **证据落点**：本文件「阶段完成结果」§T-603

### 风险与恢复

- **风险**：DLL 失败降级路径在本机无法真实触发（sr-app 无推理依赖，候选目录为空、无失败项）——该分支仅代码走查，随 T-803 引入推理依赖后实测
- **兼容性**：无（既有空库行为不变）
- **回滚/恢复**：恢复 Git 历史即可；验证全程使用临时目录

## 阶段完成结果

> 阶段完成时只记录实际产出与偏差；状态和验收结论回写 `project-progress.md`。

### T-601 后端工程初始化（2026-10-08 完成）

- **实际产出**：
  - `server/pyproject.toml`（uv 项目，`package = false`）+ `server/uv.lock`
  - `server/app/`：`main.py`（lifespan / CORS / `/api/health` / 单 worker 注释）、`core/config.py`（APP_* + 相对路径归一项目根）、`core/logging.py`、`core/errors.py`（`AppError` + 4 类全局 handler）、占位包 `api/ engine/ models/ schemas/ tasks/`
  - 依赖装进既有 `.venvs/sr-app`（19 个新包；既有 SQLAlchemy 2.1.4 / Alembic 1.20.0 未被清理）
- **验证证据**（实测通过 5 项）：
  1. 启动无报错，日志确认单 worker 与数据根解析正确（`E:\...\WebSR\data`，非 `server/data/`）
  2. `GET /api/health` → 200 `{"status":"ok","service":"websr-server","version":"0.1.0"}`
  3. `GET /api/nonexistent` → 404 + 统一错误体（`NOT_FOUND` + 三要素）
  4. `POST /api/health` → 405 + 统一错误体（占位码 `INTERNAL_ERROR`，归位动作已列入 T-700 核对，见 `errors.py` 注释）
  5. 监听地址 `127.0.0.1:8000`（非 `0.0.0.0`）；`/openapi.json` → 200（T-608 导出前置就绪）
- **方案偏差**：① 补充了骨架级 `/api/health` 探活端点（契约清单外，身份待 T-700 确认）；② 框架级 405 暂无契约码，暂映射 `INTERNAL_ERROR` 语义位并注释挂账 T-700
- **关联记录**：无 CHG 级需求变更；版本回填 `dev-info.md` §2/§3/§5.1

### T-602 数据模型与 Alembic 迁移（2026-10-08 完成）

- **实际产出**：`server/app/db.py`、`server/app/models/base.py` + `entities.py`、`server/migrations/`（env + 0001）、`pyproject [tool.alembic]`、`scripts/test-script/verify_t602_db.py`、`data/app.db`（空库，WAL）
- **验证证据**：
  1. `upgrade head` 建表成功；**二次 upgrade 幂等**（无重复建表）；`downgrade base` + 重建**可逆**
  2. 断言脚本 **17/17 通过且可重复执行**：6 表 + 3 索引 + sha256 唯一约束；PRAGMA（WAL / FK=ON / busy_timeout=5000 / synchronous=NORMAL）；`UTCDateTime`  aware 往返贴 UTC + **naive 写入被拒**；JSON 中文往返；sha256 重复写入被 IntegrityError 拒绝
  3. 验证中确认一处测试脚本口径：`TypeDecorator` 抛出的 `ValueError` 会被 SQLAlchemy 包装为 `StatementError`（产品行为正确，修正断言捕获类型）；脚本探针数据带 `__t602__` 标记精确清理，不误删真实任务
- **方案偏差**：`TASK.error` 改存 JSON 对象、`ARTIFACT.kind` 取 ER 超集（均已在 §T-602 实现说明登记，T-700 冻结核对）
- **关联记录**：无

### T-603 运行时基线与启动序列（2026-10-08 完成）

- **实际产出**：`server/app/engine/runtime_env.py`、`server/app/core/lifecycle.py`、`main.py` lifespan 接入、`scripts/test-script/verify_t603_startup.py`
- **验证证据**（13/13 通过 + 真实配置冒烟通过）：
  1. **场景 A**：临时空目录启动 → 自动迁移建库，6 表 + `alembic_version=0001`
  2. **场景 B**：预置 `running`/`canceling` 各 1 → 二次启动回收为 `interrupted`（`finished_at` 带 tzinfo 落库）；三次启动「无遗留运行中任务」**幂等**
  3. **场景 C**：DB 路径不可写 → 进程退出码 3，日志明确「数据库初始化/迁移失败，**拒绝启动**」
  4. 真实配置（`data/app.db`）启动冒烟：启动序列 1/2/3 日志齐全，`/api/health` 200
  5. 脚本加固：Windows 硬终止进程后立即读 WAL 库出现一次性 `disk I/O error`（瞬时锁释放时序），读库加短重试后复跑稳定
- **方案偏差**：迁移改以编程方式执行（`command.upgrade`）而非 CLI 子进程（理由见 §T-603 实现说明 1）；DLL「失败降级仅 CPU」分支本机无法真实触发，留待 T-803 实测
- **关联记录**：无

### T-604 M3 模型库

#### 目标

落地 tech-arch §3 的 M3 模块：内置模型登记、`.onnx` 主路径、sha256 校验、元信息管理、导入 / 导出 / 删除 API（api-contract §4.3）。

#### 范围内

- 内置模型目录（`app/models/builtin_catalog.py`）+ 启动时幂等同步（`sync_builtin_models`）
- 模型列表（含 `available` / `unavailable_reason` 门控）/ 导入（流式落盘 + ADR-003 格式路由校验）/ 导出 / 删除
- 可用性门控计算点（`app/engine/availability.py`，硬件快照来源是临时的 nvidia-smi best-effort，T-803 替换）
- `app/services/` 新包（M3/M5 业务服务层，与路由、实体分离）

#### 范围外

- `POST /api/models/{id}/convert`（.pth/.safetensors 离线转换）—— 依赖 T-806 加载器与转换工具链，S2（**已于 T-807 实现**，见下文 `### T-807`）
- 四格式**加载器**（T-806）；导入模型的真实元信息提取（参数量 / 动态 shape，T-806 回填）
- IR/ncnn 配套文件的打包导出形式（待 T-700 裁决）
- 完整硬件探测（T-803）

#### 技术方案

- **内置登记**：catalog 声明式描述 **5 个**产品内置模型（`RealESRGAN_x4.onnx` / `_fp16.onnx` / IR fp16 对，外加 2026-10-09 增补的两个**风格模型**：`RealESRGAN_x4plus_anime_6B.onnx`（动漫）与 `realesr-general-x4v3.onnx`（轻量通用））；启动时按 path upsert，文件缺失只告警。基准变体（`_fp16all.onnx` / `_s512.onnx` / IR fp32 对）**不登记**——它们是开发期基准产物。**风格由「模型」区分、不由参数区分**（PRD §7.3：模型不做自动推荐，选择权在用户）
- **sha256**：IR/ncnn 取「主文件 + 配套 .bin」**组合哈希**（权重体变化也要能触发去重）；`MODEL.sha256` 唯一约束做导入去重
- **导入**：multipart 流式落盘（不读内存）→ 格式路由校验（ADR-003：主文件为 `.bin` 或 IR/ncnn 缺配套 → `MODEL_MISSING_COMPANION`）→ 组合哈希去重 → 落 `data/models/imported/` → `.pth`/`.safetensors` 登记为 `needs_convert`
- **删除**：内置模型拒绝删除（409）；导入模型只删登记记录、不删磁盘文件（与前端确认弹窗文案一致）
- **可用性门控**：`gate_availability(status, min_vram_mb, snapshot)` 单点计算；探测不到显存时**不门控**（宁可可用也不误灰）

#### 契约差异（挂账 T-700 冻结核对）

1. `GET /api/models` 返回 `Model[]` 数组（对齐前端已定稿类型），契约草案的分页包装待裁决
2. 响应含 `status` 字段（前端类型暂无，忽略未知字段无破坏）
3. 新增错误码 `MODEL_ALREADY_EXISTS`（409）/ `MODEL_BUILTIN_READONLY`（409）
4. DB `param_count` → API `params_count`；`architecture`/`description`/`capabilities`/`size_bytes` 由服务层合成（DB 无列，是否加列待裁决）
5. 模型导入不套用 `APP_MAX_UPLOAD_MB`（图片上限 50MB 小于内置模型 67MB）

#### 验证与验收

- `scripts/test-script/verify_t604_models.py`：**37/37 通过**（临时数据根隔离，内置用小体积假文件）
  - 内置登记 3 项、字段完整、重启幂等；导入 .onnx/.pth/IR 全路径；重复导入 409；三类格式路由校验；导出内容一致；删内置 409 / 删导入 204 且不删文件；format 筛选；门控三场景（超限置灰 / 达标可用 / 探测不到不门控）
- 真实配置冒烟：`GET /api/models` 返回 3 个真实内置模型（67MB/33MB/36MB），3050 8G 实读显存下全部 available

### T-605 M5 媒体管理

#### 目标

落地 PRD §3.2 上传链路：扩展名白名单 + 服务端二次校验真实格式（不信任扩展名）、原图落盘、缩略图生成、内容获取（api-contract §4.2）。

#### 范围内

- `POST /api/files/upload`（multipart，→ `{file_id, filename, size, width, height, real_format}`）
- `GET /api/files/{id}/content?variant=original|thumb`
- Pillow 真实格式解码校验（jpg/png/webp/bmp/tif，归一化 JPEG→jpg / TIFF→tif）
- 缩略图统一 JPEG（最长边 512），缺失时即时补生成

#### 范围外

- `variant=result`（任务产物）—— 随 T-606 任务中心 / T-808 推理执行接入
- 上传文件落库（见下「设计决策」）；保留策略清理（T-609）

#### 技术方案

- **上传无状态存储**：`data/uploads/<file_id>.<real_ext>` + `data/thumbs/<file_id>.jpg`，不落库。
  原图只有被任务引用后才进 ARTIFACT（task_id 外键非空，上传时点无任务），
  为上传单建 FILE 表属过度设计；file_id → 路径靠目录扫描，id 形态校验天然防目录逃逸。
- 上传图片整体读入内存做完整解码校验（上限 50MB 可控）；`img.load()` 强制解码让截断文件暴露。
- 超限 413 `FILE_TOO_LARGE`；伪装扩展名 / 非图片内容 400 `FORMAT_MISMATCH`；白名单外 400 `UNSUPPORTED_FORMAT`。

#### 验证与验收

- `scripts/test-script/verify_t605_media.py`：**24/24 通过**（临时数据根隔离，上传上限调 1MB 测 413）
  - 正常上传字段/尺寸/落盘/缩略图；jpg 伪装 png、非图片内容、gif 白名单外三类拦截；
    5 种格式归一化；original 字节一致 + Content-Type；thumb 恒定 image/jpeg；
    result 暂拒；不存在 id 404；目录逃逸 id 404；缩略图缺失即时重建。
- 新增依赖：`pillow 12.3.0`（运行时）。

### T-606 M1 任务中心

#### 目标

落地 ADR-005 的异步任务控制面：进程内队列 + 状态机 + 协作式取消 + 历史查询 + 进度广播（启动回收已在 T-603 落地）。

#### 范围内

- `app/tasks/`：broadcaster（进程内发布订阅）/ executor（协议 + StubExecutor）/ manager（队列 + 工作线程 + 状态机）
- 路由：提交 / 列表 / 详情 / 取消 / 产物列表（api-contract §4.1，SSE 端点属 T-607）
- 上传旁车元信息（`uploads/<file_id>.json`：文件名 / 尺寸 / 真实格式，供任务快照反查）

#### 范围外

- SSE 端点（T-607）；真实推理执行器（T-808 以 EngineExecutor 替换 StubExecutor，唯一替换点 `get_executor()`）
- `output_width/height` / artifacts / ep_evidence 回填（T-808）；批量 / 视频类型（预留）

#### 技术方案

- **状态机**：`queued → running → completed / canceled / failed`，`canceling` 为协作式取消中间态（前端「正在取消」语义）；取消不强杀线程，置 `threading.Event`，执行器块间自检抛 `TaskCancelled`；排队中取消直接终态化，工作线程取到后跳过。
- **并发度 = 1**（保底档保守下界，ADR-005 §2）：已有任务在运行/排队时拒绝新提交（409 `TASK_ALREADY_RUNNING`，与前端文案一致）；排队策略演进挂账 T-700。
- **进度**：chunk 级只走广播不落库；item 级随状态翻转落库；事件节流 ≤ 1 s（首块/末块必发）；percent = item 级 + item 内 chunk 折算（单调不减）。
- **StubExecutor 是诚实占位**：12 块 × 150ms 模拟推理循环，只产生控制面动作（分块 / 取消检查 / 进度上报 / resolved 保底档），**不产出图像与 artifacts**——没有它状态机无可执行路径可验证。
- **提交门控兜底**：needs_convert → 400 `MODEL_INCOMPATIBLE`；显存不足 → 409 `MODEL_INSUFFICIENT_VRAM`；模型 / 文件不存在 → 404。
- 修复一处 T-605 回归：`find_upload` 的 glob 会匹配同名 `.json` 旁车文件 → 改为按图片扩展名白名单匹配。

#### 契约差异（挂账 T-700）

1. `GET /api/tasks` 返回 `Task[]` 数组（对齐前端已定稿类型），分页包装待裁决；
2. `file_id` 快照存于 `params.file_id`，序列化提升为顶层字段；
3. chunk 级进度（`current_chunk/total_chunks`）在任务结束后不再持久化（刷新后由下一个 progress 事件重建，符合前端类型注释）。

#### 验证与验收

- `scripts/test-script/verify_t606_tasks.py`：**27/27 通过**
  - 提交→完成全链路（resolved 保底档 / duration / 时间戳）；广播 progress + done 事件；
    协作式取消（canceling → canceled，重复取消 409）；并发 1 拒绝（409）；
    五类提交门控；列表倒序 + status 筛选；详情 404；产物端点。
- 回归：T-604（37/37）/ T-605（24/24）修复后全绿。

### T-607 SSE 进度推送

#### 目标

落地契约 §5 的事件流端点 `GET /api/tasks/{id}/events`：snapshot / progress / done / ping 四类帧，断线重连由 snapshot 恢复。

#### 范围内

- `app/api/events.py`：SSE 端点 + 帧序列化（`event:`/`data:` 两行帧）
- 终态任务的迟到订阅处理（snapshot + done 即关）

#### 范围外

- 广播层与节流（T-606 已落地，本任务只消费）；心跳间隔定值（占位 15s，契约明确由 T-700 定稿）

#### 技术方案

- 同步 `queue.Queue`（工作线程发布）经 `asyncio.to_thread` 消费，不阻塞事件循环；
- `finally` 无条件退订，防订阅队列泄漏；响应头 `Cache-Control: no-cache` + `X-Accel-Buffering: no`；
- 任务不存在走普通 JSON 错误体（404 TASK_NOT_FOUND），不开 SSE 流。

#### 验证与验收

- `scripts/test-script/verify_t607_sse.py`：**14/14 通过**
  - 帧序（snapshot 首帧 → progress → done 末帧）；progress 契约字段齐全；
    percent 单调不减；ping 心跳（测试调至 0.3s）；终态订阅 snapshot+done 即关（断线恢复）；
    不存在 404 JSON；取消路径 done 帧状态 canceled。

### T-608 API 路由层收尾 + 诊断导出 + OpenAPI schema

#### 目标

补齐路由层剩余端点（系统能力 / 诊断），并导出 OpenAPI schema 供 `T-700` 契约定稿消费。

#### 范围内

- `app/services/system_info.py`：`build_capabilities()`（契约 Capabilities 形状）+ `build_diagnostics()`（PRD §3.4 一键诊断）
- `app/api/system.py`：`GET /api/system/capabilities`、`GET /api/system/diagnostics`（附件下载）
- `scripts/export_openapi.py`：把运行时 `app.openapi()` 落盘为 `data/openapi.json`

#### 范围外

- 完整硬件探测（阶段 A）与 EP 真实性验证（阶段 B）→ T-803（本任务只定义输出形状与取值语义，探测来源到时原位替换）
- `POST /api/system/calibrate` / `GET /api/system/calibration` → S2（自标定，T-805）

#### 技术方案

- **能力数据是保底占位**：EP 未验证前不得声明 GPU 档位，一律返回 `tier=T0` + `using_fallback=true` + `verified_backends=[]`，符合「EP 未验证不得声明 T1」原则；探测来源（nvidia-smi best-effort + 系统内存）为数据面，T-803 替换。
- 诊断 JSON 以 `Content-Disposition: attachment` 下载，含硬件事实 / EP 证据 / 标定记录 / 规模统计（模型数 + 任务状态分布）。

#### 验证与验收

- `scripts/test-script/verify_t608_system.py`：**15/15 通过**
  - capabilities 形状齐全、EP 未验证 → T0 + using_fallback、verified_backends 为空（不冒充已验证）、device_facts 字段齐全；
  - diagnostics 200 且为附件下载、字段齐全（PRD §3.4）、标定记录出库、calibration.state=pending、summary 统计存在；
  - 统一错误体回归（404 NOT_FOUND 三要素）；
  - OpenAPI 导出脚本执行成功、schema 文件存在、**全部端点进入 schema**、openapi 版本字段存在。


### T-609 M7 系统配置（F-07）

#### 目标

落地 `GET / PUT /api/settings`（契约 §4.4）+ 保留策略清理（F-07 的"任务保留策略"），
使 S1 的"系统配置"从 mock 变为真实可读可写、**且改动真的生效**。

#### 范围定性（**本轮收窄，重要**）

任务卡原文为「M7 系统配置 + M6 参数预设（P1）」。核对切片定义后**拆开处理**：

- **F-07 系统配置 → 本轮交付**：PRD §2.7 明确 F-07 属 **S1**、优先级 **P0**，无争议；
- **F-09 参数预设 → 归 S2，本轮不做**，三条依据：
  1. PRD §2.7 的 S2 清单**显式列出** F-09；`docs/prototype/README.md` 亦写明预设界面"不在本阶段"；
  2. F-09 要求"预设**绑定档位**（同一场景在 T1 与 T3 上的推荐参数不同）"—— 产出**推荐参数**
     必须依赖引擎决策 Profile（T-804）与标定（T-805）。此刻实现只能**硬编码数值**，
     与项目核心原则「机制硬编码、数值运行时求」直接冲突（开发机实测值不得进入产品参数表）；
  3. 前端 5C 无预设 UI（原型明确排除），后端先行只能产出无人消费的端点。
- `preset` 表已在 T-602 建好（schema 无需改动），S2 启动时直接落地 CRUD 即可。

#### 范围内

- `app/services/settings_store.py`：**声明式键表**（类型 / 区间 / 枚举）+ **覆盖式存储**
  （`SETTING` 表只存被改过的键；值等于默认即删覆盖行）+ 生效值解析 + 保留策略清理
- `app/api/settings.py` + `app/schemas/setting.py`：`GET / PUT /api/settings`
- **配置真实生效接线**（这是本任务的重点，而非"接口返回 200"）：
  `media_store`（`max_upload_mb` / 数据根）、`model_registry`（模型目录）改为读**生效值**
- 启动序列（T-603 扩展）：迁移后按落库 `log_level` 重设日志级别；末位执行保留策略清理

#### 范围外

- **F-09 参数预设 CRUD** → S2（依据见上）
- `max_concurrency` 的真实并行：开放并发属 **G-06（T-907，P3）**；本任务只做持久化，
  执行器并发仍恒为 1（保底档）
- `simulation_enabled` 的生效：档位模拟开关属 **P3（T-901）**；本任务只做持久化
- 数据根变更后的**文件迁移**：有意不做（迁移会让运行中任务与已落库记录失去一致性）

#### 技术方案

- **覆盖式存储**：`SETTING` 只存显式改动过的键 → 「恢复默认」= 保存默认值（自动删行），
  且将来新增配置项不会因旧覆盖行丢默认。进程内缓存覆盖行，保存时失效。
- **只读派生键**（`model_dir` = `<data_root>/models`，依据 PRD §4.2；`calibration_state`）：
  PUT 时**静默忽略**而非报错 —— 前端保存会把整表回传，报错会直接打断"整表提交"。
- **生效边界如实标注**（写进模块 docstring，供 UI 与 T-700 参考）：
  `data_root` 立即对后续新建任务生效但不迁移既有文件；`max_upload_mb` / `log_level` 立即；
  `task_retention_days` 下次启动清理时生效；`max_concurrency` / `simulation_enabled`
  **已保存但尚未生效**（分别待 T-907 / T-901）。
- **保留策略清理**：只删 `status ∈ 终态` 且超期的任务记录；产物**记录行**随外键连带删除，
  **磁盘文件一律不动**（PRD F-07 原文"产出文件不受影响"；按时间/大小清文件属 F-12/S2）。
- 请求体接受契约的 `{ items: [...] }`，**同时兼容裸数组**；响应为裸数组（见差异登记）。

#### 验证与验收

- `scripts/test-script/verify_t609_settings.py`：**47/47 通过**（隔离临时数据根 + TestClient）
  - 清单 8 键 / 类型 / 顺序固定 / 默认值；
  - **6 类非法值全被拦**（越界 / 非整数 / 下界 / 枚举 / 空值 / 未知键），且**未污染已有配置**；
  - 只落显式改动的键；保存默认值 → 覆盖行删除（等价恢复默认）；
  - `log_level` 改后 **root logger 级别立即变**；只读派生键写入被忽略且值未被篡改；
  - **`max_upload_mb` 改 1MB 后，1.2MB 图上传立即被 413 拦截**（证明配置真生效，不只是落库）；
  - **`data_root` 改根后新上传落新根**、5 个子目录建齐、**旧根未被迁移**；
  - 保留策略：超期终态记录被清、**超期运行中记录保留**、未超期记录保留、
    产物记录行连带删除、**磁盘产出文件仍在**、二次清理幂等；
  - 诊断导出与能力端点回归（T-608 无破坏）。
- 真实配置冒烟：启动序列日志呈现 **1/4~4/4** 四步；`GET /api/settings` 返回 8 键真实默认值；
  PUT 往返成功且**恢复默认后真实库无残留覆盖行**。
- 全量回归：T-602(17) / T-603(13) / T-604(37) / T-605(24) / T-606(27) / T-607(14) / T-608(15) 全绿。
- `docs/tech/api/openapi.json` 重新导出（15 条路径，含 `/api/settings` get+put）。

#### 差异登记（T-700 冻结核对）

- 响应为**裸数组** `Setting[]`（与前端已定稿 `fetchSettings(): Setting[]` 一致），
  契约 §4.4 写作 `{ items }` —— 与 `GET /api/models` 同一处理；
- PUT 请求体宽松兼容（`{items}` 或裸数组）；
- 新增错误使用既有 `VALIDATION_ERROR`（无新增错误码）；
- `/api/settings` 挂在 **`/api` 根**（契约 §4.4 表内位置与系统域同表，但路径非 `/api/system/*`）。

### T-802 M4 图像处理：预处理 / 分块 / overlap + feather 拼接

#### 目标

M4 落地为**无状态纯算法库**：图片 → 模型输入张量（预处理）、分块计划、**overlap +
feather 加权拼接**（质量红线）、张量 → 图片（后处理 / 格式转换）。

tech-arch §6.5 对既有实现的判词是明确的：

> 现有 `tools/_bench_common.py::tiled_infer` **无羽化**，是基准用的形状验证器，
> **生产实现必须重写**（T-802）。

#### 范围内

- `app/engine/image_ops.py`：`PreprocessSpec` 契约、`open_image`（完整解码 + EXIF 转正）、
  `to_tensor`（RGB/BGR、归一化、mean/std、float16、alpha 分离）、`from_tensor`
  （反归一化、尺寸对齐、alpha 回填）、`encode_image` / `save_image`（PNG 无损 / JPEG 4:4:4）
- `app/engine/tiling.py`：`plan_tiles` / `TileBox` / `TilePlan`、`box_weight` /
  `feather_window`、`extract_tile`、`TileAccumulator`、`stitch`
- 依赖：`numpy`（新增运行时依赖）
- `scripts/test-script/verify_t802_image.py`（87 项断言，**纯算法、不启动后端**）

#### 范围外

- **推理本身**（会话创建 / 逐块 infer / 进度上报）→ M2 编排（T-804）+ 加载器（T-806）。
  M4 只提供**部件**，`TileAccumulator.add()` 由 M2 在循环里调用
- **tile / overlap / feather_px 的取值** → 阶段 C 标定（T-805）+ 阶段 D 决策（T-804）。
  本任务只定义参数与语义，**不写死任何数值**
- 模型输入尺寸倍数约束（如 `tile % 4 == 0`）→ 属模型能力，随 T-806 读取 ONNX 输入约束后校验
- 视频 / 批量 / 不分块整图（`tile=0`）→ T-808 / G-02 / G-01（T-904）

#### 技术方案

**1. 分块排布：全块落在图内 + 贴边收尾（不 pad 到网格）**

块起点从 0 起按 `stride = tile - overlap` 递增，末尾补一个**贴住尾边**的收尾块：

```
y:  0 ──────── tile
        ├── overlap ──┤
    stride                        倒数第二块
                                  └── 收尾块（与前块重叠 >= overlap）
```

于是**内部重叠量恒为 `overlap`，收尾处重叠量 ≥ `overlap`**——变大的重叠只会让权重和
更大，加权平均依旧成立。这样避免了"pad 到网格"在图四周造出一整圈虚假边缘。

唯一例外：**原图短边 < tile**（只有一个块），内容按**居中**放到 tile 中央、用
**edge 复制**补齐（镜像填充会在 pad 区造出假纹理边缘）。pad 区域不写回结果，
输出尺寸恒为 `原图 × scale`。

**2. 拼接：加权累加 + 逐像素归一化**

每块按位置生成权重窗（边缘渐隐），逐像素 `acc += w * result`、`wacc += w`，
最后 `out = acc / max(wacc, eps)`。因为做了**归一化**，只要每个像素至少被一块以正权重
覆盖，结果就是凸组合——**既平滑又不改变整体亮度**（常量图与全 1 块测试专门验证这点）。

**3. 权重窗的两个关键细节（都实测踩过）**

- **端点取整**：渐隐区在 `[0, fade-1]` 上从 0 线性升到 1（分母 `fade-1`），第 `fade` 个
  像素恰为 1，与窗外恒为 1 的部分**数值连续**。起初用 `(i + 0.5) / fade`，窗内首值是
  `1/fade` 而非 0/1，在窗边界留下一个小台阶——实测中它正是最大的残留"接缝"来源。
- **在输出分辨率上生成**：权重若先在 tile 分辨率生成再用 `repeat` 放大，会变成**阶梯函数**，
  混合结果仍带台阶。改为直接按输出像素数生成，权重才真正连续。

**4. 贴图边不渐隐**：`has_*` 为假的一侧权重恒为 1——那里没有邻居可融合，渐隐只会白降权重
（归一化后还得除回来）。

**5. 交接契约**：`feather_px == overlap` 时两块权重在重叠区**互补（和恒为 1）**，混合严格线性；
`feather_px > overlap` 仍是线性（比例恒定）；`feather_px < overlap` 会留下一阶连续的
**斜率折点**（无阶跃，但过渡略生硬）。推荐 `feather_px = overlap`，由 T-804 决定取值。

#### 验证与验收

`scripts/test-script/verify_t802_image.py`：**87/87 通过**（纯算法库，不启动 FastAPI，
顺带证明 M4 与推理栈/后端解耦）。

**"无可见接缝"必须可证伪**，所以验证不是直接断言"结果很平滑"，而是先构造**已知会产生
接缝的对照组**，确认指标能把它测出来，再在同一指标下比较本实现：

| 分组 | 边界峰比 | max_d1 | max_d2 |
|---|---|---|---|
| 硬切（无重叠，process-out 式拼贴） | **63.0** | 1.0000 | 1.0159 |
| **本实现（overlap + feather）** | **1.3** | 0.0349 | 0.0508 |
| 整图一次推理（无接缝基准） | — | 0.0020 | 0.0000 |

- **边界峰比** = 块边界列跨列差分 ÷ 邻域中位差分。接缝的本质不是"梯度大"而是**该处梯度
  相对周围突然变大**；羽化在整个重叠区均匀抬升梯度（平滑但略糊），比值仍 ≈ 1。
- **max_d2（二阶）** 用于区分"阶跃"与"线性过渡"：线性函数二阶差分为 0。硬切是台阶，
  二阶出现尖峰；羽化是线性混合，二阶只有内容曲率量级的残差（0.0508 相对硬切压低 20 倍，
  且边界峰比 1.3 证明它**不是孤立尖峰**）。
- 其余断言：分块计划（覆盖完整无空洞 / 收尾贴边 / 内部步长恒定 / 参数校验 / 小图居中补齐）；
  **逐像素推理器下分块拼接与整图推理逐像素精确相等（max|diff| = 0）**；常量图无亮度漂移；
  全 1 块 → 结果全 1；9 组极端尺寸（1×800、800×1、3×3、65×65…）；预处理（归一化 / 通道顺序 /
  mean-std / float16 / alpha / 灰度 L / 调色板 P / **EXIF 转正** / 损坏文件拒绝）；
  后处理（尺寸对齐 / RGBA 回填 / clamp / 编码往返）；累加器契约（非法块拒绝 / `added` 计数）。

真实图像验收（人眼复核"无可见接缝"）**属 S1 闭环验收**，需 T-806 加载器就位后跑真实
RealESRGAN 才能做——本任务交付的是**可验证的机制**（控制面）。

#### 差异登记（T-700 冻结核对）

- **归一化契约应由模型元信息声明**：通道顺序（RGB/BGR）、值域（`/255` vs `[-1,1]`）、
  mean/std 目前由调用方传参，内置 ONNX 模型统一按 **RGB / `/255` / float32**（与 RealESRGAN
  官方 ONNX 导出一致）。建议契约冻结时为 `MODEL` 增加预处理声明字段，避免"模型级契约"散落在代码默认值里。
- `ImageMeta`（原图尺寸 / 模式 / alpha）是 M4 内部的**交接结构**，不进 API 契约。

#### 顺带修正（非本任务范围，但属真实缺陷）

- **编号漂移**：多处代码与文档把"硬件探测 / EP 真实性验证"写成 `T-802 替换`，实为
  **T-803**（T-802 是 M4 图像处理）。已统一修正 `api/system.py`、`engine/availability.py`、
  `services/system_info.py` 与本文档。
- **上传缩略图缺 EXIF 转正**（T-605 遗留）：手机竖拍图会让"缩略图方向 ≠ 超分结果方向"，
  且旁车元信息里的宽高是转正前的值（前端按它排版）。已在 `media_store.verify_and_open`
  返回前补 `exif_transpose`，`get_content` 的即时补生成分支同步修正；T-605 回归 24/24。

### T-803 引擎阶段 A/B：能力探测 + EP 真实性验证

#### 目标

把 tech-arch §6.1 的**阶段 A（能力探测）**与**阶段 B（EP 真实性验证）**落成产品代码，
并解决两个"看起来能用、其实没用上 GPU"的静默失败：

- `session.get_providers()` **只证明 EP 对象被创建**，不证明它执行了计算——
  ORT 在 EP 创建失败时只打一条 warning 就静默回退 CPU（性能差一个数量级却毫无提示）；
- `get_available_providers()` 只说明"编译进去了"，不是可用性结论。

#### 范围内

- `app/engine/device_probe.py` —— **阶段 A**：`DeviceFacts`（CPU / 内存 / NVIDIA GPU /
  Intel GPU / OpenVINO 设备 / ORT 候选提供器），**永不抛异常**
- `app/engine/ep_verify.py` —— **阶段 B**：候选链展开 + 逐后端验证（**profile 节点归属**）+ 剔除规则
- `app/engine/backend_cache.py` —— 验证结果落 `data/calibration/`，**硬件指纹失效**
- `app/engine/capabilities.py` —— 编排 + **档位判定（§6.2）** + 契约 `Capabilities` 组装
- 接线：`services/system_info.py`（真实探测）、`core/lifecycle.py` 第 2 步（非阻塞）、
  `engine/availability.py`（门控数据源换为阶段 A 的 facts）
- 依赖：`onnxruntime`（CPU 基线 wheel，装入 `.venvs/sr-app`）
- 验证：`scripts/test-script/verify_t803_engine.py`（76 项）+ `verify_t803_cuda_evidence.py`（真机取证）

#### 范围外

- **能力声明字段**（`supports_fp16` / `supports_batch` / `supports_tile0` / `has_tensorrt` /
  `is_generative` / `num_inference_steps` / `requires_prompt`）→ **T-902（P3-首）**。
  本任务只产出判定依据所在的 `DeviceFacts` 与 `VerifiedBackend`；
  唯一例外是 `NvidiaGpuFacts.supports_fp16`——它由 `compute_cap` 推导（**按能力不按型号**），
  是档位/后端排序的输入，不是模型能力声明。
- **档位模拟开关**（`force_tier` / `force_vram` / …）→ **T-901**。`Capabilities.simulation`
  当前恒为 `{enabled: false, force_tier: null}`；`backend_cache.save(simulated=True)` 已**拒写**，
  为 T-901 预留了正确的落点。
- **Intel 路径的验证**（OpenVINO 原生 API 非 ORT EP，不走 profile 数节点）→ 未实现。
- **阶段 C 自标定（T-805）/ 阶段 D 决策（T-804）/ 阶段 E 水位**，以及**模型加载与格式路由**（T-806）。

#### 技术方案

**1. 阶段 A：绝不抛异常，且区分两种"没拿到值"**

| 归类 | 含义 | 例子 |
|---|---|---|
| `probes_failed` | 探测**抛了异常** | 驱动查询失败、WMI 被禁 |
| `probes_skipped` | **前置条件缺失**，不是故障 | 未安装 onnxruntime / openvino |

两者混记会让人把"没装某组件"误读成"这台机器有问题"。每项还记 `probe_ms`——
探测不能成为启动瓶颈（实测本机总耗时约 370 ms，其中 ORT import 占 294 ms）。

**2. 三条硬性事实纪律（都是踩过的坑）**

- **绝不把标称值当可用值**：`vram_total_mb` 是标称，`vram_free_mb` 才是可用。
  本机标称 8192 MB、实读可用 7112 MB（验证脚本断言 `free <= total` 且
  `available_vram_mb` 必须等于实读 `free`）。
- **Intel GPU 必须读 `FULL_DEVICE_NAME`**：OpenVINO 的 GPU 插件会把 NVIDIA 卡
  也枚举成 `(dGPU)`，用 `"GPU" in available_devices` 必然误判（故按全名过滤 `intel`）。
- **`is_wddm` 决定显存语义**：Windows WDDM 下拿不到按进程显存，只能读设备级 `used` 增量。

**3. 阶段 B：判据是"CPU 节点数"，不是"节点数够不够多"**

```
建 session（providers=[目标 EP, CPU]） → 失败 → session_create_failed
  ↓ 成功
EP 在 session.get_providers() 里吗 → 不在 → ep_not_in_session_providers
  ↓ 在
开 profiling 跑一次 → 数 cat=="Node" 事件的 args.provider
  ↓
目标节点数 = 0                → 🔴 silent_fallback（**必须记为异常事件**）
目标节点数 > 0 且 CPU 节点 = 0 → ✅ 完全接管（**哪怕是 1 个节点**）
目标节点数 > 0 且有 CPU 节点   → 记占比；> 30% → 剔除（partial_unprofitable）
  ↓ 通过
另开**不开启 profiling** 的 session 测干净延迟 → 比 CPU 慢则剔除
```

- **`节点数 == 1` 是正常的**：ORT 可以把整个 RRDBNet 融合成单个子图
  （实测 OpenVINO EP 就是 `{'OpenVINOExecutionProvider': 1}`）。按"节点数少 = 没接管"
  理解会**误判**，所以交叉判据必须是"CPU 节点数是否为 0"。
- **干净延迟必须另开 session**：profiling 会显著放大单次耗时，用它排序后端会得出错误结论。
- **剔除"能跑但更慢"**：候选链以 CPU 为基准，GPU 后端比 CPU 慢即剔除——
  静默使用会得到"看似加速实则更慢"的结果（实测 ORT + OpenVINO EP 就是这种）。

**4. 候选链：`[CUDA?, CPU]`，且把"刻意排除"与"验证失败"分开表达**

- NVIDIA 存在 → `CUDAExecutionProvider`（TensorRT 属 G-04，需 T-902 的能力声明才入链）；
- Intel GPU → **走 OpenVINO 原生 API**，不是 ORT EP（实测 ORT+OV EP 为负收益），
  故**刻意不入链**；该结论以 `excluded_negative_gain` 的 verdict 形式进入 `ep_evidence`，
  面板上能看见"为什么不用它"，而不是凭空消失；
- CPU EP 恒在链尾。

**5. 缓存：失效判据是硬件指纹，不是时间**

指纹 = CPU 名 + GPU 名/驱动/显存总量 + Intel 设备 + **ORT 版本** + OS。
驱动升级、换卡、ORT 版本变化都会改变验证结论，指纹不符即整份作废（宁可重跑一次）。
另外两条边界：**无任何后端通过验证时不写缓存**（避免把"暂时的失败"固化）；
**`simulated=True` 拒绝写入**（§6.3 约束 2，为 T-901 预留）。

**6. 档位判定严格照 §6.2 表，且不按设备型号**

```
可用显存 ≥ 48 GB → T3 ｜ ≥ 16 GB → T2 ｜ 存在已验证 GPU 后端 → T1 ｜ 其余 → T0
```

第 3 条要求后端**已验证**：本机有 3050，但 `.venvs/sr-app` 装的是 CPU 版 ORT，
CUDA EP 不可用 → **档位判为 T0**，理由如实写明"检测到 NVIDIA GPU，但其后端未通过
EP 真实性验证"——不静默降级、也不冒领 T1。
代码里**禁止**出现按型号分支（验证脚本做了**源码级检查**：
扫描引擎层四个文件，断言不含 `4090/3050/RTX /GTX /GeForce` 等型号字面量）。

**7. 启动第 2 步非阻塞，且放线程里跑**

EP 验证要跑一次 profile 采样（秒级），放事件循环里会卡住第一批请求。
用 `asyncio.to_thread` 起；任何失败只记异常事件——探测失败 ≠ 服务不可用，
只是拿不到加速（保底档兜底，§6.8）。

**8. 保底语义如实标注**

阶段 C 未落地 → `using_fallback` 恒 `true`、`active_precision` 恒 **`fp32`**
（§6.8：保底档不论 GPU/CPU 一律 fp32）。即便真机 CUDA 验证通过（档位 T1），
精度仍是 fp32，直到标定给出结论——**不拿"检测到 GPU"冒充"已优化"**。

#### 验证与验收

**`scripts/test-script/verify_t803_engine.py`：76/76 通过**（跑在 `.venvs/sr-app`）

- **阶段 A**：结构完整 / `probes_failed` 与 `probes_skipped` 分离 / 每项耗时 / 总耗时 < 5s /
  可用显存 ≤ 标称显存且取自实读 / `is_wddm` 与平台一致 /
  **注入会抛异常的探测 → 记入 `probes_failed` 且整体不崩、其余事实仍产出**。
- **profile 解析**：只统计 `cat == "Node"`（忽略 Session/Model 噪声事件）/ 兼容 `traceEvents` 包装。
- **阶段 B 判定分支（假 ORT 注入，11 组）**——这些分支真机上无法逐一复现，故用可控假 ORT 逐个走：
  `ort_not_installed` / `ep_not_available` / `session_create_failed`（含 `WinError 127` 场景）/
  `ep_not_in_session_providers` / **🔴 `silent_fallback`（EP 在 session 里但 0 节点）** /
  **🔴 整图融合单节点（node=1, cpu=0）→ 判为已接管** / `partial_unprofitable`（CPU 占比 90%）/
  部分接管但占比 1% → 采用 / `probe_run_failed`（CUDA OOM）/ `probe_model_missing` /
  **比 CPU 慢 → `slower_than_cpu` 剔除**。
- **候选链与刻意排除**：`[CUDA, CPU]` / 无 NVIDIA → `[CPU]` / OpenVINO EP 说明"负收益" /
  无 NVIDIA 时 TensorRT 说明"不适用"。
- **档位判定**：§6.2 表 **8 组全命中**（含"有卡但后端未验证 → T0"与"显存读不到但后端已验证 → T1"）；
  降档理由必须写明"未通过 EP 真实性验证"。
- **源码级检查**：引擎层不含设备型号字面量（ADR-004 原则 2）。
- **缓存**：路径正确 / 写读往返 / **指纹变化即作废** / `simulated=True` 拒写 /
  损坏 JSON 容错 / 二次构建 `cache_state=hit`（不重跑 profile）。
- **无探针模型 → 优雅降级**：`cache_state=skipped`、不写缓存、档位 T0、保底档、记入异常事件。
- **真实 ORT 端到端（CPU EP，真实 profile）**：节点数 > 0 且全在 CPU 名下 / 干净延迟已测 /
  总耗时 < 60s / `.venvs/sr-app` 无 CUDA 时**如实报 `ep_not_available`**（不冒充可用）。
- **API 形状**：`/api/system/capabilities` 契约字段齐全、`device_facts` 为实读值；
  诊断导出含 `probe`（失败项）与 `ep_verification`（缓存状态/指纹）；启动第 2 步已写入 `capability_report`。

**真机 CUDA 取证**：`scripts/test-script/verify_t803_cuda_evidence.py`（跑在 `.venvs/sr-gpu`，
ORT 1.22.0 + CUDA/TensorRT wheel），产物 `.workbuddy/verify/t803/cuda_evidence.json`：

| 后端 | 节点数 | CPU 节点数 | 干净延迟 | 判定 |
|---|---|---|---|---|
| **CUDAExecutionProvider** | **1024** | **0** | **677.8 ms** | ✅ 已接管，采用 |
| CPUExecutionProvider | 1409 | 1409 | 8215.8 ms | ✅ 保底，采用 |

同一台机器、同一模型：CUDA 比 CPU 快 **12.1 倍**；档位判定为 **T1**（存在已验证的 GPU 后端）。
DLL 路径注册 9 个候选目录、0 失败。这与 P0 报告（`tools/p0_1_cuda_smoke.py`：1024 节点全在 CUDA）
**结论一致**——产品代码的判据与原基准工具同源（tech-arch §6.7 要求的"逻辑重写而非调用"）。

**真实配置冒烟**（`.venvs/sr-app` + 真实数据根）：启动序列 1/4~4/4 + 第 2 步日志
`档位 T0，采用 ['CPUExecutionProvider']，缓存 hit`；`/api/system/capabilities` 返回真实
`verified_backends=[cpu 1409 节点]`，CUDA/OpenVINO 两条证据如实标注"未参与验证"。

**全量回归**：T-602(17) / T-603(13) / T-604(37) / T-605(24) / T-606(27) / T-607(14) /
T-608(15) / T-609(47) / T-802(87) / **T-803(76)** 全绿。

#### 差异登记（T-700 冻结核对）

1. `Capabilities.device_facts` 额外返回 `cpu_cores` / `available_ram_gb`——F-06 要求
   展示"CPU 核数与指令集""实读可用内存"，而 5C 冻结的 `DeviceFacts` 类型只有 6 个字段。
   建议契约补齐这两个字段（`isa` 指令集列表现阶段留空：无 `py-cpuinfo` 依赖，
   属可选增强，不阻塞任何判定）。
2. `device_facts.available_vram_gb` / `nominal_vram_gb` / `system_ram_gb` 在探测不到时为 `null`，
   前端类型当前标为 `number`。建议契约标为可空。
3. `EpEvidence.node_ownership` 新增一种非契约语义："未参与验证（<reason_code>）"，
   用于区分"跑过但 0 节点"与"根本没参与验证"（后者写"0 节点"会被误读）。
4. `/api/system/capabilities` 的 `simulation` 现为恒定量（`force_*` 属 T-901）。
5. 诊断导出新增 `probe` 与 `ep_verification` 两个区段（契约未定义诊断体的字段集）。

#### 有意不做 / 已知限制（如实登记）

- **Intel GPU 路径未验证**：OpenVINO 原生 API 不是 ORT EP，不走 profile 数节点，
  需要另一套验证机制。当前若检测到 Intel GPU 而 NVIDIA 后端不可用，**档位落 T0**，
  理由写明"Intel GPU 的 OpenVINO 原生路径尚未验证/标定，按 T0 保守处理"——
  宁可保守，也不冒领 T1（§6.2 约束 4 说"由首次标定裁决"，而标定属 S2）。
  ⚠️ **2026-10-10 用户裁决：核显与专业卡（T2/T3）暂无硬件环境，实机测试暂不做**
  → 本项连同 T2/T3 一并**转为"已知边界"**（不再列为待办）。产品形态不变（照实现、排 P3、
  交付标注"未验证"），控制面靠 **`T-901` 档位模拟**、真实硬件靠**用户侧首次自标定**兜住。
- **`.venvs/sr-app` 装的是 CPU 版 `onnxruntime`**：产品部署若要 GPU 加速，
  需在该环境换装 `onnxruntime-gpu`。真机 CUDA 取证走 `.venvs/sr-gpu`，
  **不**因此修改应用环境的依赖集——依赖选择是部署决定，而验证机制必须对两种环境给出一致判据。
- **多卡策略未定**：`primary_nvidia` 取第一张卡；多卡属后续任务。
- **`ram_available_mb` 是瞬时值**：仅作展示与诊断；T0 档的输入尺寸上限应随水位动态求
  （属决策/标定，T-804/T-805）。

### T-804 引擎阶段 D/E：决策 Profile + 水位反馈与降级 + 保底档

#### 目标

把 tech-arch §6.1 的**阶段 D（决策）**、**阶段 E（运行时反馈）**与 §6.8 的**保底档**落成产品代码，
回答一个问题：**这次任务到底用多大的块、什么精度、哪个后端，以及在当前机器上此刻是否安全**。

它不产生任何图像，也不做任何探测——输入全部来自已经就位的阶段 A（`DeviceFacts`）与
阶段 B（已验证后端），标定位（阶段 C / T-805）尚未落地时缺口由**保底档**补上。

#### 范围内

- `app/engine/fallback.py` —— **保底档策略**（§6.8）：保守下界参数 + 语义规则，单一事实源
- `app/engine/runtime_profile.py` —— **阶段 D**：`RuntimeProfile` 与 `decide_profile()`
  （纯函数，含**因果性防御**）、`tile / overlap / feather_px` 取值规则
- `app/engine/watermark.py` —— **阶段 E**：水位采样（VRAM/RAM）、连续高位触发降档、
  **OOM 识别与降档链**、进程内 `WaterLevelTracker`
- `app/services/engine_decision.py` —— 应用层组装件（读标定 / 能力快照 / 水位），
  **引擎层保持不依赖应用层**（无 fastapi / sqlalchemy / pydantic）
- 接线：`tasks/executor.py`（`resolved` 从硬编码换成真实决策）、`tasks/manager.py`
  （前置水位基线 + 事后峰值记录 + **OOM 降档重试一次**）、`engine/capabilities.py`
  （保底语义改由 `fallback.py` 单一来源）、`services/system_info.py`（诊断新增 `decision` 区段）
- 验证：`scripts/test-script/verify_t804_engine.py`

#### 范围外

- **真实推理编排**（会话创建 / 逐块 infer / 真实产物落盘）→ 需模型加载器（**T-806**）就位，
  本任务只交付**决策与降级机制**（控制面）。因此任务仍以 `StubExecutor` 的模拟块循环跑通，
  但 `resolved` 已换成真实决策结果——**不拿"能出决策"冒充"能出图"**。
- **阶段 C 自标定（T-805）**：本任务只定义"标定记录存在且有效时如何消费它"，
  **不产生**标定记录（`Calibration` 表现在仍为空）。
- **档位模拟**（`force_tier` / `force_vram`）→ **T-901**；保底档**不得**因模拟而改变语义。
- **并发度生效** → G-06 / **T-907**。`RuntimeProfile.concurrency` 只是决策产物，
  管理器仍按 §6.8 的保底值 1 排队，**不在此任务放开**。
- **Intel / OpenVINO 原生路径**、多卡、视频（T-808）。

#### 技术方案

**1. 保底档（`fallback.py`）：每个取值必须是"下界"，且只有一处定义**

| 参数 | 保底取值 | 依据 |
|---|---|---|
| `tile` | `FALLBACK_MIN_TILE = 64`，再按模型对齐倍数向上取整 | 见下方"为什么这个数可以硬编码" |
| `precision` | `fp32` | §6.8：不论 GPU/CPU 一律 fp32（CPU 无 fp16 单元时 fp16 反而更慢） |
| `backend` | "已验证列表"**首项**；列表为空时回落 `cpu` 并标记异常 | §6.8；空列表意味着 EP 验证整体未通过 |
| `concurrency` | `1` | §6.8；动态并发属 T-907 |

**为什么这个数可以硬编码**：§6.8 规则 1 允许保底档取常量，但只有两个条件同时成立才合规——
① 它是**任何环境都不会 OOM 的下界**，不是"这台机器的最优值"；② 它**不得来自开发机实测**
（开发机上 256/512 更快、更省时间，但那是个体最优）。因此 `64` 的定位是"结构下界"：
必须 ≥ 模型对齐倍数（4/8），且小到足以在任何 ≥1 GB 显存的设备上安全完成单块推理。
**开发机的实测最优点永远不会进这张表**——那是阶段 C 标定的产物（且标定属 S2）。

另两条纪律直接写成代码约束：保底档**不写** `Calibration`（本模块不 import 任何模型层），
**不写**模型元信息（`RuntimeProfile` 只作为任务快照落 `TASK.resolved`）。

**2. 阶段 D（`runtime_profile.py`）：先取基线，再过因果性防御，最后叠加水位降档**

```
输入：facts(阶段A) + adopted(阶段B) + calibration(阶段C，可空) + 用户 params + align(模型对齐倍数)

① 基线
   标定有效（valid 且硬件指纹一致）→ 取标定结论，source=calibration，using_fallback=false
   否则                            → 取保底档，  source=fallback，  using_fallback=true
② 用户覆盖（auto=false 且字段非空）→ 覆盖基线对应项，source=user
③ 因果性防御（**与来源无关，一律执行；每触发一条都记 downgrade**）
   backend ∉ adopted                    → 回落 adopted[0]
   precision == fp16 且后端非 GPU        → 降 fp32（CPU 路径铁律）
   precision == fp16 且 GPU 无 fp16 单元 → 降 fp32（按 compute_cap 判定，不按型号）
   tile < 下界                            → 抬到下界
   tile 非 align 倍数                     → 向上取整到 align 倍数
④ overlap / feather_px
   overlap = align_down(tile / 4, align)（下界 align），feather_px = overlap
⑤ 水位降档（阶段 E 传入的待执行降档）→ 逐条应用并记录
⑥ degraded = (downgrades 非空)；adopted 为空时强制 using_fallback = true
```

- **为什么 overlap 取 `tile/4`**：T-802 的质量对照实验正是按 `tile=256 / overlap=64`
  与 `tile=64 / overlap=16`（即 1:4）验证"边界峰比 63.0 → 1.3"的，**1:4 是回归验证过
  的比例**；`feather_px = overlap` 则是 T-802 交接契约的推荐值（两块权重在重叠区互补，
  混合严格线性）。两者都是**机制取值**，不是开发机的实测最优点。
- **为什么 `align` 是入参而不是常量**：tile 必须满足模型自己的窗口/步长约束（SwinIR 类为 8），
  这属**模型能力**，由 T-806 读 ONNX 输入约束后传入；本模块只给保守默认值 `8`
  （4/8 的公倍数），**不按型号硬编码**。
- **防御为什么不信任"来源"**：标定结论同样可能失效（换卡后指纹不符、标定在 CPU 档做的但
  现在换成了 GPU），用户手填的参数更可能自相矛盾（CPU + fp16）。防御是**因果性**的，
  所以无条件执行；触发即 `degraded=true`，并在界面"本次决策"里逐条可见——对应 PRD"降级必须显式"。
- **标定记录的消费口径**（本任务只定义"怎么用"，产出属 T-805）：`tile` 取
  `Calibration.tile_curve["recommended_tile"]`，精度取 `precision_decision`，后端取
  `tile_curve["recommended_backend"]`，**匹配判据是 `hardware_fingerprint` 精确相等**
  （先按 `model_id` 精确匹配，再退到通用记录 `model_id IS NULL`）。字段取不到就**留空**，
  让决策退回保底下界——**不做任何猜测**；曲线形状由 T-805 定义，本任务不预设。
- **"无已验证后端"与"标定有效"同时出现时谁说了算**：档位语义由**标定**裁决
  （§6.8 表格第 2 行），`using_fallback` 保持 false；而"缺后端"这件事由 ③ 的
  `backend` 回落（置空）+ `degraded=true` + 显式理由表达。**两件事分开说，不互相覆盖**——
  否则会出现"有标定却声称在保底档"的自相矛盾状态。

**3. 阶段 E（`watermark.py`）：水位是"这一次跑得多满"，降档是"下一次怎么办"**

| 环节 | 规则 | 依据 |
|---|---|---|
| 采样 | 任务**前**取基线、任务后取峰值；Windows 下显存只能读设备级 `used` 增量 | P0 报告 §3.3 / tech-arch §6.7 |
| 判定 | 连续 **2 次** > **85%** → 建议降档；单次高位不触发 | §6.1 阶段 E（机制阈值，可硬编码） |
| 降档链 | `tile` 折半（至下界）→ 后端 GPU→CPU（若 CPU 已在 adopted）→ 耗尽即报错 | §2.2 铁律 1 明列"OOM 降档链"可硬编码 |
| OOM 重试 | 识别到 OOM 类异常 → 降一档 → **重试一次**；再次 OOM 即 `VRAM_INSUFFICIENT` / `RAM_INSUFFICIENT` | §6.1 阶段 E |
| 比例口径 | `ratio = max(vram_used/vram_total, ram_used/ram_total)`；显存读不到时只算内存 | **T0 档的瓶颈是物理内存不是显存**（§6.2） |

- **采样"永不抛异常"**：水位是**观测**，不是判定前置。取不到值就记 `None`，
  不能让"读不到显存"把任务搞挂——与阶段 A 同一纪律。
- **`fp16` 不在降档链里**：降档只允许"更保守"的动作，而 fp16 在显存上是**变省**的；
  它属于阶段 C 的**精度性价比结论**（"这块卡上 fp16 值不值"），不是压力应对手段。误把它
  放进降档链会得到"OOM 时反而更省显存"的错误语义。
- **降档理由必须落进 `resolved.downgrades`**：用户要能在任务详情里看到
  "因为上一次跑满 91%，本次 tile 从 512 降到 256"。

#### 验证与验收

**`scripts/test-script/verify_t804_engine.py`**（跑在 `.venvs/sr-app`）

- **保底档策略**：tile 下界 ≤ 所有真实档位的合理取值；`precision` 恒 fp32；
  源码级检查其**不 import 模型层 / 不写标定**（"保底档不入标定与模型元信息"是代码约束，不是口号）。
- **决策基线**：未标定 → `using_fallback=true`、fp32、backend=adopted[0]、tile 已对齐、
  `overlap == feather_px == align_down(tile/4)`、reasons 非空。
- **因果性防御（逐个走通，与"来源"无关）**：用户 fp16 + CPU 后端 → 降 fp32；
  用户指定未验证后端 → 回落；tile < 下界 / 非对齐 → 抬升并对齐；
  GPU 无 fp16 单元（`compute_cap < 5.3`）→ 降 fp32；GPU 有 fp16 单元 → 保留 fp16。
  每条命中都必须产生 `degraded=true` 与一条 `downgrades` 记录。
- **标定命中 / 失效**：构造有效且指纹一致的标定记录 → `source=calibration`、`using_fallback=false`、
  采用标定 tile/precision；指纹不符 → 回落保底档（**失效判据是硬件指纹，不是时间**）。
- **无已验证后端**：`adopted=[]` → 强制 `using_fallback=true`，理由写明"未通过 EP 真实性验证"，
  不冒领任何加速。
- **水位**：采样永不抛异常；比例口径取显存/内存的**较大者**；1 次高位不降档、
  连续 2 次触发；降档执行后计数复位。
- **降档链**：512→256→…→下界；到不了下界就切后端；无步可退返回 None（由调用方报错）。
- **OOM 重试一次**：用可控假执行器让第一次抛 OOM 类异常、第二次成功 → 任务 `completed`
  且 `resolved.downgrades` 含一条；连续两次 OOM → 任务 `failed` 且错误码为
  `VRAM_INSUFFICIENT` / `RAM_INSUFFICIENT`。
- **接线**：提交→执行后的 `resolved` 含 `source / overlap / feather_px / concurrency`；
  `using_fallback=true`（阶段 C 未落地）；诊断导出新增 `decision` 与 `watermark` 区段。
- **源码级检查**：`engine/runtime_profile.py` / `fallback.py` / `watermark.py` 不 import
  `fastapi` / `sqlalchemy` / `pydantic`（引擎纯度），且不含设备型号字面量（ADR-004 原则 2）；
  能力面板不再硬编码 `using_fallback` / `active_precision`（与阶段 D 同源）；管理器不再自己决定参数。

**结果：`verify_t804_engine.py` 132/132 通过**，覆盖上述全部条目（含 1 处构造标定记录的
消费路径验证与 2 处可控假执行器驱动的 OOM 分支）。

**全量回归**：T-602(17) / T-603(13) / T-604(37) / T-605(24) / T-606(27) / T-607(14) /
T-608(15) / T-609(47) / T-802(87) / T-803(76) / **T-804(132)** —— 共 **489 项断言全绿**。

**真实配置冒烟**（`.venvs/sr-app` + 真实数据根 + 真实内置 ONNX 探针）：

```
启动序列 1/4~4/4 → 第 2 步：档位 T0，采用 ['CPUExecutionProvider']，缓存 hit
能力面板：保底档=True 后端=CPUExecutionProvider 精度=fp32（来自阶段 D，非硬编码）
真实任务：resolved = {tile:64, precision:fp32, backend:CPUExecutionProvider,
                     using_fallback:true, degraded:false, overlap:16, source:fallback,
                     reasons:[未标定 → 保底档；自动档说明；占位执行器未真正驱动计算]}
诊断导出：含 decision（last + policy）与 watermark（history 1 条，consecutive_high 0）
```

#### 顺带修正（实施中发现，非本任务范围）

- **并发槽被观测动作占住 → 下一个任务误收 409**：水位采样要起一次 `nvidia-smi` 子进程
  （百毫秒级），而它原先排在终态落库之后、`_release_active` 之前。于是"任务已显示完成、
  但提交新任务仍返回 `TASK_ALREADY_RUNNING`"——在 T-606 回归里表现为确定性失败。
  修正：终态落库后**立即释放并发槽**，观测与广播排在其后。
  教训是通用的：**观测动作不得占用调度位**。

#### 差异登记（T-700 冻结核对）

1. `TaskResolved` 新增 **`overlap` / `feather_px` / `concurrency` / `source`** 四个字段：
   M2 编排（需 `overlap`/`feather_px` 才能驱动 M4 的 `plan_tiles` / `TileAccumulator`）
   与"本次决策"展示（`source`）都需要它们，而 5C 冻结的 `TaskResolved` 只有
   `tile / precision / backend / using_fallback / degraded / reasons / downgrades`。
   建议契约补齐；前端类型为非严格结构，多字段不破坏既有解析。
2. **降档原因里的数值是运行期真实值**（如"9120 MB / 10240 MB"），前端展示时应按字符串直出，
   不要尝试解析成结构化数值。
3. 诊断导出新增 `decision`（保底档策略 + 最近一次决策）与 `watermark`（水位历史）两个区段；
   契约未定义诊断体的字段集（与 T-803 的 `probe` / `ep_verification` 同类）。

#### 有意不做 / 已知限制（如实登记）

- **`resolved` 是"决策结果"而不是"执行结果"**：真实推理未接入前，任务仍由 `StubExecutor`
  跑模拟块循环，因此 `resolved` 里的 tile/精度/后端**尚未真正驱动计算**——
  它们是引擎的决策，不是已发生的加速。**S1 闭环验收必须以真实产物为准**。
- **`watermark` 的峰值在模拟执行器下不含真实显存压力**：只有 T-806 加载真实模型后，
  水位数据才有"这台机器跑这个模型要多少显存"的含义。当前的验证只能证明**机制**成立。
- **标定消费路径已实现但无真实数据**：`Calibration` 表在 S2 前恒为空，
  因此线上永远走保底档；命中分支靠构造记录验证（脚本内），不是端到端真实链路。
- **`align` 默认 8**：真实取值需 T-806 读 ONNX 输入约束；在此之前按公倍数保守处理。
  ✅ **已由 T-806 兑现**（`model_introspect.py` + `model_constraints()`：只收紧不放宽；
  静态输入另走 `fixed_tile` 独立通道，见下文 §T-806）。
- **多卡 / Intel 原生路径 / 视频**仍未覆盖（同 T-803 边界）。

### T-806 模型加载器与真实推理编排

#### 目标

把"磁盘上的模型文件"变成"**可以 `infer()` 的后端对象**"，并把 M2 编排
（预处理 → 分块 → 逐块推理 → 羽化拼接 → 落盘）真正接上真实推理。

它兑现的是 T-804 明确挂账的那笔债：*"`resolved` 是决策结果而非执行结果……**S1 闭环验收
必须以真实产物为准**"*。本任务完成后，任务中心产出的不再是模拟块循环，而是**磁盘上真实的、
尺寸正确、可校验的图像文件**。

#### 范围内

- `app/engine/runtimes.py` —— **可选运行时探测**（不 import 重库）
- `app/engine/model_introspect.py` —— **ONNX 输入约束读取**（兑现 T-804 的 `align` 挂账）
- `app/engine/model_loader.py` —— **格式路由 + 三后端**（ORT / OpenVINO 原生 / ncnn）+ 失败码
- `app/engine/pipeline.py` —— **M2 真实推理编排**（纯引擎层，不认识任何后端实现）
- `app/engine/runtime_profile.py` —— `decide_profile()` 新增 `fixed_tile` 入参
- `app/services/engine_decision.py` —— 新增 `model_constraints()`（读模型约束，**只收紧不放宽**）
- `app/services/media_store.py` —— `outputs_dir()` / `task_outputs_dir(task_id)`
- 接线：`tasks/executor.py`（新增 `EngineExecutor`，`StubExecutor` 降为测试夹具）、
  `tasks/manager.py`（**产物落库 + 尺寸回填**）、`api/tasks.py`（产物端点带执行事实）
- 验证：`scripts/test-script/verify_t806_loader.py` + `_t806_backend_probe.py`（跨环境探针）

#### 范围外

- **`.pth`/`.safetensors` 的转换工具** → **T-807**。本任务只负责"识别出来并拒绝加载"，
  给出指向转换的 `needs_convert` 错误码——**不假装能加载**。
- **阶段 C 自标定（T-805）**：属 S2。S1 期间依旧走保底档。
- **批量推理 / `tile=0` 整图（G-01）**、**视频（T-808）**、**微调（T-809）**。
- **TensorRT EP**：候选链表已含，但本任务不新增验证逻辑（沿用阶段 B 结论）。

#### 技术方案

**1. 格式路由（ADR-003：确定性判定，不猜）**

| 扩展名 | 配套 | 后端 | 说明 |
|---|---|---|---|
| `.onnx` | — | ORT（CUDA / TensorRT / CPU EP） | **S1 主路径**，应用环境必备 |
| `.xml` | `.bin` | OpenVINO 原生 API | **不走 ORT + OV EP**（实测负收益） |
| `.param` | `.bin` | ncnn | 层覆盖失败即剔除，不影响其它格式 |
| `.pth` / `.safetensors` | — | 拒绝加载 | 指向离线转换（T-807） |
| 单独 `.bin` | — | 拒绝 | 导入层已拦，这里再兜一次 |

判定只看**扩展名 + 配套文件**，不看文件名内容、不看文件头。

**2. "未安装"必须是一种状态，而不是一个异常**

`runtimes.py` 的探测走 `importlib.util.find_spec`（只查 spec，**不 import 本体**）+
`importlib.metadata.version`（读分发元数据，**也不 import**）。理由有两条，缺一不可：

- **性能**：一次 `import openvino` 是秒级、数百 MB 内存，探测不能成为启动或列表请求的瓶颈；
- **状态语义**：openvino / ncnn 是**可选后端**，缺失不是故障。若用异常表达，调用方就得
  到处 `try/except ImportError`，最终必然有人写成 `except Exception: 认为不可用`——
  把真实加载失败也吞掉。返回 `RuntimeStatus(available=False, reason=..., optional=True)`
  让"缺什么、怎么装"成为**可展示的数据**（与阶段 A 的 `probes_skipped` / `probes_failed`
  纪律同源）。

**3. 校验顺序：先问「制品完不完整」，再问「本机跑不跑得动」**

```
needs_convert → file_missing → companion_missing → runtime_missing
```

即**结构性校验先于环境性校验**。这不是随手排的，理由是"同一份残缺模型在任何机器上
都应该得到**同一个错误码**"：

- 若先报 `runtime_missing`，一台没装 openvino 的机器上**永远看不到"缺 `.bin` 配套"**——
  而那才是用户真正要修的问题（装上运行时后依旧失败，白折腾一轮）；
- 更实际的是：应用环境只装 CPU 版 onnxruntime，运行时前置会让 companion 分支
  **在应用环境不可观测**，只能靠跨环境探针间接证明。

这条顺序有专门的回归断言（脚本 §3「残缺制品在任何机器上同码」）钉住。

**4. 输入约束：`align` 与 `fixed_tile` 是**两条通道**，不能混用**

`model_introspect.py` 读 ONNX 输入形状，产物是 `InputSpec`。关键裁决：

| 形态 | `source` | `align` | `fixed_tile` |
|---|---|---|---|
| 动态 H/W（如 SwinIR） | `dynamic` | `1`（**未检测到**约束） | `None` |
| 静态正方形（如 512×512） | `static` | `N` | `N` |
| 4 维以外 / 非正方形 | `unknown` | `1` | `None` |

- **`align = 1` 的含义是"未检测到约束"，不是"无约束"。** 动态导出的窗口注意力模型同样
  呈现 `[1,3,"h","w"]`，窗口倍数约束**读不出来**。因此 `model_constraints()` 的口径是
  **只能收紧、不能放宽**：默认 `DEFAULT_ALIGN = 8`，只有读出 > 8 的约束才覆盖它。
  若把 `align=1` 当成"无约束"直接采用，会把安全的 tile 变成非法 tile。
- **静态输入是"正好等于"，不是"倍数关系"**，不能用 `align` 表达。动手前实测：
  `overlap_for(512, 512)` → `raw = 128 → align_down(128, 512) = 0 < a → ov = 512`；
  `ov >= tile` → `ov = max(512, align_down(511,512)) = 512` → **`stride = tile - overlap = 0`**
  → `plan_tiles` 断言失败。所以 `decide_profile(fixed_tile=N)` 走**独立分支**：`tile = fixed_tile`，
  再按 `DEFAULT_ALIGN` 重算 overlap（实测 `tile=512 → overlap=128, stride=384`）。

**5. 决策精度 ≠ 模型精度：不做运行时精度转换**

`profile.precision` 是**决策**，模型文件的输入 dtype 是**事实**。`load_backend` **始终按模型
实际的输入 dtype 喂数据**（fp16 导出模型喂 fp32 会被 ORT 直接拒绝类型不匹配），并把实际
dtype 经 `describe()["input_dtype"]` 如实报出。

"决策 fp16、模型是 fp32"时**不做运行时转换**（那要改写整张图），而是由 `EngineExecutor`
记一条显式降档；反向（模型 fp16、决策 fp32）只记说明、**不算降级**（那不是保守动作）。
两种方向都有断言覆盖。

**6. ncnn 的两个陷阱（T-210 实测踩出，实现里逐条防住）**

1. `ncnn.Mat(numpy)` 把三维数组解释为 **(c,h,w)**（不是 HWC），且**借用**缓冲区不拷贝；
   临时数组若在 `extract()` 前被 GC → **无回溯段错误（exit 139）**。
   → `NcnnBackend.infer` 用局部变量持有 `(c,h,w)` 数据直到 `extract()` 返回，再 `np.array()`
   拷出结果。
2. `create_gpu_instance()` 必须与 `destroy_gpu_instance()` 配对，否则解释器退出阶段段错误
   → `close()` 负责配对，**失败路径也配对**。

**7. 控制面与真实推理解耦：显式注入点，不用环境变量**

`EngineExecutor` 成为**默认**执行器后，T-606 / T-607 / T-804 三个离线快跑脚本会开始真跑推理
（成本从 ~1.8s 变成分钟级，且依赖真实模型文件）。解决办法是源码级注入点：

```python
set_executor_factory(factory) / reset_executor_factory() / get_executor()
```

三个脚本各自显式注入 `StubExecutor`（只改脚本，不改产品默认行为）。

**刻意不提供环境变量开关**，并有源码级断言钉住（`executor.py` 中不出现 `os.environ` /
`getenv`）——线上行为不得被环境改变。

**8. 执行时才发现的偏差必须显式登记**

决策在任务开始前算，但有些事实只有执行时才知道。`EngineExecutor` 用三个 `_reconcile_*`
方法逐条比对并写进 `reasons` / `downgrades`：

| 方法 | 比对 | 触发时 |
|---|---|---|
| `_reconcile_geometry` | 决策 tile/overlap vs 模型 `input_spec.fixed_tile` | tile 被校正，记一条降档 |
| `_reconcile_backend` | 决策 backend vs 会话真实 providers | GPU EP 未生效，记一条降档 |
| `_reconcile_precision` | 决策 precision vs 模型实际 dtype | 见上文 §5 |

这对应 PRD「**降级必须显式**」——配置漂移不静默。

#### 验证与验收

`verify_t806_loader.py` —— **121 通过 / 0 失败 / 0 跳过**，共 10 段：

| 段 | 内容 | 要点 |
|---|---|---|
| 1 | 可选运行时探测 | 不 import 重库；缺 openvino/ncnn 给可照做的安装建议；pth 归因为"需转换" |
| 2 | 格式路由（ADR-003） | 五格式 primary/companion/runtime 判定；未知格式抛 `unsupported_format` |
| 3 | 加载失败分支 | 每条都要有**可判别的码**；含"残缺制品在任何机器上同码"的顺序回归 |
| 4 | ONNX 输入约束 | 动态 / 静态 512 / fp16 三种真实形态；进程内缓存；读不到不抛异常 |
| 5 | ORT 后端 | `describe` 报真实 provider 与 dtype；close 后 infer 抛 `INFERENCE_FAILED` |
| 6 | pipeline 全链路 | 多块 / 小图单块 / 协作式取消；sha256 与尺寸双重校验 |
| 7 | HTTP 端到端 | `EngineExecutor` 产出 **ARTIFACT**；尺寸回填；并发槽已释放 |
| 8 | 执行时偏差登记 | 5 个合成断言（几何 / 后端 / 精度双向） |
| 9 | 引擎纯度 | 行首锚定正则；无设备型号字面量；无环境变量开关 |
| 10 | **跨环境复核** | 子进程在 `sr-ov` / `sr-ncnn` 里跑**产品加载器本身** |

**真实产物证据**（应用环境，CPU EP）：

```
分块计划：100x80 → tile=64 overlap=16 stride=48，共 4 块（2 行 x 2 列）
→ 输出 400x320（原图 ×4），4 块，~1.8s，std=60.31（非全黑）
→ ARTIFACT path = data/outputs/tsk_1/...，sha256 与磁盘文件一致
```

**跨环境复核为什么必须做**：应用环境只有 CPU 版 onnxruntime，"四格式可加载"若只在
`sr-app` 验证，只证明了两种分支会**正确报 `runtime_missing`**。因为引擎层纯净
（不 import fastapi / sqlalchemy / pydantic），可以用**子进程**在 `sr-ov` / `sr-ncnn` 里
跑**产品加载器本身**——验证的是产品实现，而不是另写一份平行实现。结果：

- `【sr-ov】` IR 全链路 **4/4 PASS**（`kind=openvino`、尺寸 = 原图 ×4、非全黑、尺寸读回一致）
- `【sr-ncnn】` **3/3 PASS**（输出形状 = 输入 ×4、非全黑、无 NaN，T-210 结论复现）

**全量回归**：T-602(17) / T-603(13) / T-604(37) / T-605(24) / T-606(27) / T-607(14) /
T-608(15) / T-609(47) / T-802(87) / T-803(76) / T-804(132) / **T-806(121)** = **610 项断言全绿**。

#### 顺带修正（实施中发现，非本任务范围）

1. **三个既有验证脚本改为显式注入 `StubExecutor`**（T-606 / T-607 / T-804）：默认执行器
   换成真实推理后，它们会开始真跑模型。这是"控制面验证不该被数据面绑架"的落地。
2. **T-804 脚本的时序竞争（1/132 偶发失败）**：`[FAIL] 任务完成后水位已记入历史`。根因是
   任务状态先落 `completed`，水位采样（起 `nvidia-smi` 子进程，百毫秒级）在其后，脚本却
   立刻断言——与 T-804 修过的那处竞态**同源**。修正：脚本改为**轮询等待（≤5s）**。
   直连调用 `sample_water_level()` + `tracker.record()` 已先行证明机制本身正常。
3. **T-606 脚本的过时文案**：产物列表断言原写"当前为空，T-808 回填"，改为
   "产物端点可用（占位执行器不产生产物，故为空）"——执行器语义变了，注释要跟上。

#### 差异登记（T-700 冻结核对）

1. **`TaskOut.output_width` / `output_height` 由"恒为 null 的占位"变为"真实回填"**：
   字段在 5C 就已在 schema 中，本任务起才有值。**类型无需改动**，但契约应写明其来源
   （取自 `resolved.execution`）与时机（任务进入终态后）。
2. **`resolved` 新增 `execution` 子对象**：记录**执行事实**（`output_width` / `output_height` /
   `tiles` / `elapsed_ms` / `backend` / `sha256` / `size_bytes`）。与 `resolved` 顶层的
   **决策事实**（`tile` / `precision` / `backend` / `downgrades`）刻意分开——**决策与执行不同源，
   混在一层会让人误以为"决策即事实"**（这正是 T-804 那条挂账的教训）。
3. **`ArtifactOut.path` 带 `data/` 前缀**：DB 里存相对数据根的 `outputs/tsk_<id>/...`，
   对外统一输出 `data/outputs/...`，与前端已定稿的 Mock 约定一致。
   配套：产物宽高取自 `resolved.execution`，**产物表不加宽高列**（避免为一个派生值做迁移）。
4. **`ep_evidence` 仍为空数组**：真实 EP 证据表回填属 **T-700 展示增强**。

#### 有意不做 / 已知限制（如实登记）

- **`.venvs/sr-app` 装的是 CPU 版 onnxruntime**，因此应用环境**跑不出 GPU 加速**。
  这是**部署决定**而非代码问题（T-803 既定口径）：需要 CPU 基线可选装 `onnxruntime`，
  需要 CUDA 则换 `onnxruntime-gpu`。本任务的验证结论是"**加载器与编排正确**"，
  不是"**这台机器已加速**"。
- **`resolved.execution.backend` 在应用环境恒为 CPU**：如实反映环境，不冒领 GPU。
- **`.pth`/`.safetensors` 只是"被正确拒绝"**，不是"可加载"——任务池里"四格式可加载"的
  原始措辞按切片修正为：**三格式可加载（onnx / IR / ncnn）+ 两格式正确拒绝并指向转换**。
  依据 PRD §2.7：`.bin`/`.pth`/`.safetensors` 与离线转换属 **S2**，而 S1 只含 `.onnx` 主路径；
  且"S2 项的失败不得影响 S1 主链路"。
- **ncnn 属"条件支持"**：剩余条件是**具体模型的层覆盖需运行时校验**（加载失败即剔除），
  不是"后端能不能跑"（T-210 已关）。
- **推理仍在中并发 1 的工作线程内**：真实负载下的资源争抢（与 API 争抢 CPU/内存）
  待后续在真实长任务上观察；P3 与实验性后端的独立子进程形态（ADR-005）未实现。
- ⚠️ **本任务证明的是"能出图"，不是"出得好看"**：验证用的是**合成图与基准模型**（尺寸、非全黑、
  非常量、sha256 一致），**人眼质量复核（真实照片"无可见接缝"）尚未做**——它属 **S1 闭环验收**
  （T-802 交接时就写明"真实图像人眼验收属 S1 闭环"）。**不要把本任务的"121/121"读成"画质已验收"。**

---

### T-805 引擎阶段 C：首启自标定（F-10）

#### 目标

在**本机硬件**上实测 tile 曲线与精度性价比，写入 `CALIBRATION` 表，使阶段 D 的"自动"档
从**保底档**升级为**真实参数**。它兑现的是 PRD §2.2 写明的那条边界：

> **C 阶段（首启自标定，即 F-10）属 S2**：S1 期间"自动"档回落到保底档……**F-10 完成后"自动"档才真正生效**。

#### 范围内

- `app/engine/calibration.py` —— **阶段 C 标定引擎**（纯引擎层：候选集 / 单档测量 / 推荐 / 预算 / 异常隔离）
- `app/services/calibration_service.py` —— 应用层组装：触发、落库、失效、查询
- `app/api/system.py` —— `POST /api/system/calibrate`（**异步**）+ `GET /api/system/calibration`
- `app/core/lifecycle.py` —— 启动第 2 步在能力探测**之后**投递首启标定（非阻塞）
- 验证：`scripts/test-script/verify_t805_calibration.py`（63 断言 / 10 段）

#### 范围外

- 前端展示与"重新标定"按钮 → **T-701 / T-702**（本任务只保证数据与接口就位）
- 标定的**增量续测**（本轮超预算就停，不留断点续跑）→ 未排期
- `.pth`/`.safetensors` 转换（T-807）、视频（T-808）、微调（T-809）

#### 技术方案

**1. 四条纪律**（每条都有"不这么做会踩什么"的具体依据）

| 纪律 | 依据 |
|---|---|
| **一个 (tile × 精度) 组合一个 session，测完立即 `close()`** | ORT 的显存 arena 只增不减，同一 session 连跑多个 tile 会让后测的档位"看起来"不需要显存。2026-10-09 实测：每档新建并关闭后，同一 tile 重复测得偏差 < 2%（256 → 2070 / 2107 MB，512 → 6623 / 6670 MB）→ **不必为每档起子进程**（`tools/p0_2_vram_matrix.py` 的子进程隔离是为了隔离"设备级 used 增量"，这里靠 session 生命周期即可） |
| **预算是硬边界，不是目标** | 超预算即停在当前最优档并标 `budget_exceeded=true`。宁可给"只测了 3 档"的真实结论，也不给一条猜出来的完整曲线 |
| **模拟结果绝不入库** | `simulation_enabled` 打开时产出 `simulated=true`，`is_storable()` 拒绝落库——否则会被阶段 D 当成真实结论继承（§6.8 规则 2 同源） |
| **单档失败不毁整轮** | OOM / 加载失败只记该档 `ok=false`，绝不把整轮抛掉（`tools/bench_ort.py` 记着一次 tile-512 OOM 把进程带走、JSON 都没写出的教训） |

**2. 预算约束的是"档数"，不是"单档时长"**

第一版实现让单档测量也吃 deadline，结果预算 0.5s 时**首档被腰斩**，只换来一条
`ok=false` 的失败样本——标定在任意预算下都可能空手而归。已改为：
**首档无条件测完，预算只决定"还能不能再开下一档"**。"测一档"是标定的最小有意义单位。

**3. 推荐口径是「吞吐」而不是「延迟」**

跨 tile 可比的是**每秒像素**（`tile²·scale² / 延迟`），不是单块延迟——大 tile 单次更慢，
但每像素可能更划算。实测恰好证明了这一点（见下表）。

**4. 显存安全判据沿用阶段 E 的口径**（`used / total`，阈值 85%）

WDDM 拿不到按进程显存，只能读设备级用量。**刻意不另立一套**：否则"标定说这档安全、
水位说它危险"会互相打架，用户看到的提示会自相矛盾。

**5. 启动时的顺序依赖**：阶段 C **必须**排在阶段 A/B 之后

标定要用的 `hardware_fingerprint` 与 `adopted_backends` 都产自 A/B。若两者并发，
标定会拿到空指纹 → 结论无法做失效判定（指纹是唯一失效判据）。故两者串在**同一个**
协程里，而不是各起一个 task。

#### 实测（RTX 3050 8G + CUDA EP，`RealESRGAN_x4.onnx`，fp32）

| tile | 延迟 | 吞吐 | 显存峰值 | 判定 |
|---|---|---|---|---|
| 64 | 70 ms | 941k px/s | 11% | 安全 |
| 128 | 179 ms | 1466k px/s | 15% | 安全 |
| **256** | 670 ms | **1564k px/s** | 34% | **← 推荐** |
| 384 | 1534 ms | 1538k px/s | 66% | 安全 |
| 512 | 3485 ms | 1204k px/s | **95%** | **超水位 → 停止上探** |

**这张表就是"机制硬编码，数值运行时求"的最好论据**：直觉会选 512（"更大更快"），
实测却是 **256 最优、512 反而慢 23% 且吃掉 95% 显存**。开发机上这个结论**不得**写进
产品参数表——它只是这台机器的答案，换张卡可能完全不同。

#### 验证与验收

`scripts/test-script/verify_t805_calibration.py` —— **63 通过 / 0 失败 / 10 段**：

1. 候选集与对齐（动态 / 静态单一档 / 下界）
2. 单档真实测量（真实 ONNX + CPU EP，产出结构完整、可 JSON 化）
3. 推荐规则（取吞吐最高 / 全超水位不给推荐 / 全失败不给推荐）
4. 预算与停止（超预算如实标注 / 超水位停止上探）
5. 异常隔离（模型缺失不抛异常 / OOM 记失败并停止上探）
6. 模拟保护（`simulated=true` 不可入库）
7. 落库与失效（写入 / 旧记录作废 / `describe` 形状）
8. **消费链路打通**：标定命中后阶段 D `using_fallback=False` 且采纳推荐 tile；指纹不符则退回保底档
9. HTTP 端点形状（`POST /calibrate` 异步返回作业号 / 重复触发不叠加）
10. 引擎纯度（不 import 应用层 / 不含设备型号字面量 / 不硬编码本机最优点）

#### 诚实边界

- **预算会截断曲线**：默认 8s 预算下通常只测得 3~4 档（实测跑完 5 档用了 17.2s）。
  `budget_exceeded=true` 时结论**只基于已测档位**，不是"完整曲线的全局最优"。
- **标定会吃满 GPU 十几秒**：虽然在后台异步跑、不占并发槽，但会与正在执行的任务争显存/算力，
  故默认预算刻意保守。
- **标定对象是内置探针模型**：产出 per (模型 × 硬件) 记录；用户导入的其他模型若无对应记录，
  仍会退到"通用记录"或保底档。**这是设计而非缺陷**——标定必须跑在产品真正会加载的模型上。
- **`recommended_tier` 目前直接沿用当前档位**，未做"标准曲线是否值得升档"的判断；
  待有 T2/T3 硬件后再补（当前无对应硬件，属 P3）。
- ⚠️ **不要把这台机器的 256 当成"正确答案"**：它只说明"标定机制在真实 GPU 上跑通了"。




---

### T-807 `.pth` / `.safetensors` → `.onnx` 离线转换（ADR-003 方案 A）

**任务定位**：兑现 PRD §7.5 方案 A / ADR-003 的第 1 条架构影响——**应用内不捆绑 PyTorch**，
`.pth`/`.safetensors` 走**独立环境的离线转换**，应用只加载 `.onnx` 产物。
它是 T-806"两格式正确拒绝并指向转换"那句话的**兑现方**：拒绝有了去处。

#### 为什么不是"在应用里直接加载"

ADR-003 已定案，此处只复述理由以免后人翻案：捆绑 PyTorch（+ torchvision + spandrel）
会让依赖从 ~130 MB 级跳到 **2.5 GB 级**，启动与打包显著变重，而本应用对 `.pth` 的需求本质是
"把自己训练的权重用起来"——**转成 ONNX 后收益相同**。代价是"不能直接拖进去用"，
缓解手段就是本任务：**一条命令 / 一个按钮**。F-13 微调的"产物回灌"（T-809）与本任务**同构**，
不引入第二套依赖语义。

#### 交付物

| 交付 | 位置 | 说明 |
|---|---|---|
| **离线转换工具** | `tools/convert_to_onnx.py` | 产品**不得 import**（`tools/` 是开发期资产）。CLI + `--json` 机器可读结果 |
| **独立转换环境** | `.venvs/sr-convert` | torch **2.14.1+cpu** / torchvision 0.29.1+cpu / **spandrel 0.4.2** / onnx / onnxruntime / safetensors。**CPU 版 torch 足够**——转换不需要 GPU |
| **应用侧转换入口** | `services/conversion_service.py` + `api/models.py` | `POST /api/models/{id}/convert`（异步，202）+ `GET /api/models/{id}/convert`（取状态与结果） |
| **模型输出扩展** | `schemas/model.py` → `ModelOut.conversion` | 仅 `.pth`/`.safetensors` 有值；其它格式为 `null` |

#### 四个关键裁决

1. **架构识别交给 spandrel，认不出就报错、不猜**。ADR-003 明文："若无法识别则明确报错而非猜测"。
   工具报 `arch_unrecognized`（退出码 3）并**附上 spandrel 注册的 42 个架构名**——报错本身要能照做。
   另提供 `--arch` 强制指定（走同一注册表，**不自己重写网络实现**）。
   同时提供 `spandrel.size_requirements`（`minimum` / `multiple_of` / `square`）——这是**唯一**
   能"不靠猜"拿到窗口倍数约束的来源（`load_from_file` 返回的 descriptor 自带），与 T-806 的
   `align` 口径衔接：**报出来作为参考值，产品侧仍按 `DEFAULT_ALIGN` 只收紧不放宽**。

2. **默认导出动态 H/W**。产品侧 `model_introspect` 只看 ONNX 输入约束，**动态输入**引擎才能自由分块。
   实测：动态导出的产物在 `64 / 97 / 101 / 128 / 155 / 192 / 200` 等尺寸（**含非 8 倍数**）全部正确输出 4×
   → Real-ESRGAN 系（纯卷积）确实**无隐藏步长约束**。
   `--static N` 才导出固定边长（对应产品侧 `fixed_tile` 通道）：实测固定 256 的产物喂 128 **会报错**，
   语义清晰——产品侧要求的正是"**正好等于 N**"而非倍数关系。

3. **自检通过才落盘**。导出后用 ONNXRuntime 真跑一遍并与 torch 输出比对 `max|diff|`（默认阈值 1e-3）。
   理由写进了代码注释：**"能加载但算错"的模型比加载失败更糟**——产品侧无法识别，会静默出坏图。
   实测两例：RRDBNet `1.55e-06`、SRVGGNetCompact `4.08e-06`。
   配合**原子写**（`tempfile` + `os.replace`）：失败**不留半个 `.onnx`**、不留残留 `.tmp`
   （与 `engine/pipeline.py` 的"取消不留半个文件"同源）。

4. **"环境缺失"是状态不是异常**，且**登记复用 F-05 导入链**。`.venvs/sr-convert` 不存在时，
   `POST` 返回 409 + **可照做的三条安装命令**（不是一句 ImportError）；`ModelOut.conversion.available=false`
   带同一条说明——与 `engine/runtimes.py` 对可选后端的口径一致。
   产物登记直接调 `model_registry.save_import()`，不另写一套入库逻辑（校验 / 定名 / 去重 / 落哈希全复用）。

#### 实测事实（`scripts/test-script/verify_t807_convert.py` 与 `_t807_tool_probe.py`）

```
RealESRGAN_x4.pth        (RRDBNet / "ESRGAN"，16,697,987 参数) → 64.0 MB .onnx
  同源自检 max|torch-onnx| = 1.55e-06；size_requirements = {minimum:2, multiple_of:1, square:false}
realesr-general-x4v3.pth (SRVGGNetCompact，1,213,296 参数)     → 4.6 MB .onnx
  同源自检 max|torch-onnx| = 4.08e-06
```

**与仓内既有 `data/models/RealESRGAN_x4.onnx` 的关系（务必分清）**：
**图结构同构**——节点 1187 / 初始化器 702 / 算子直方图逐项一致（`Conv 351 / LeakyRelu 279 / Concat 276 /
Constant 94 / Add 93 / Mul 92 / Resize 2`）、opset 17、输入输出名 `input`/`output` 也一致；
但**权重不同源**（同输入下 `corr = 0.856`、`max|diff| = 0.23`）→ 属**同一架构的不同微调版本**，
**不能互相替代**。工具的正确性由"**同源自检**"保证，**不由"与既有产物的相似度"保证**。

**端到端闭环**：`.pth` 导入（status=`needs_convert`）→ 触发转换 → 产物落 `data/models/imported/`
并登记为新的 `.onnx` 模型（status=`ready`）→ **产品加载器（`model_loader.load_backend`）
真的加载它并推理出 4× 结果**，**CPU EP 与 CUDA EP 双路径均通过且结果一致**。
重复触发**复用已有产物**（不重复导出——RRDBNet 导出实测数十秒）。

#### 验证

| 项 | 结果 |
|---|---|
| `verify_t807_convert.py`（sr-app） | **63 通过 / 0 失败 / 0 跳过**（8 段） |
| `_t807_tool_probe.py`（**sr-convert**，跨环境） | **23 通过 / 0 失败** |
| 全量回归 | T-602~T-609 / T-802~T-807 全绿 |

**跨环境复核方法论（沿用 T-806）**：工具级失败分支（架构认不出 / 自检不通过 / 导出失败 /
`.safetensors` 路径 / 静态导出）**需要真 torch**，因此放在 `.venvs/sr-convert` 里跑
**工具本尊**，由主脚本以子进程调用——跑的是产品/工具实现，不是平行实现。
反过来，主脚本在 `sr-app` 里断言 **`find_spec('torch') is None`**、**产品源码不 import torch/spandrel**、
**产品不 import `tools/`**——依赖分离这件事**必须被断言钉住**，否则会在某次重构里悄悄失效。

#### 诚实边界

- **spandrel 决定支持面**：其注册表覆盖 42 个架构，超出范围的权重**一律拒绝**（这是设计，不是缺陷）；
- **转换不改变数值**：产物精度 = 源权重精度（fp32）。`--fp16` 可导出半精度，但**CPU EP 未必能跑**，
  自检会如实标 `skipped` 并记原因，不假装通过；
- **`multiple_of=1` 不能推广**：Real-ESRGAN 系是纯卷积网络，实测无步长约束；
  **窗口注意力模型（如 SwinIR）的约束读不出来**，产品侧仍按 `DEFAULT_ALIGN` 保守处理——
  与 T-806 的"`align=1` 是**未检测到**约束，不是无约束"完全同源，**别把这条结论放大**；
- **转换是 CPU 密集的离线动作**：RRDBNet 导出实测数十秒（构建整图 + 常量折叠），
  故接口做成**异步作业**；同一时刻只跑一个，重复触发返回现状或复用产物；
- **不做"自动挑架构/自动改倍数"**：模型声明的 `scale` 不在 2/3/4 之内时直接报错，
  **不替用户改成 4**——那是替用户做决定。

---

### 增补：内置风格模型（2026-10-09，非任务项）

> **触发**：用户指出「Real-ESRGAN 分写实 / 动漫 / 通用几个档位，现在没看到模型或者参数设置」。
> 核查确认：那几套是**独立权重文件，不是参数档位**；本项目把"换风格"设计成「**换模型**」（F-05），
> 而内置清单原先只有 `RealESRGAN_x4plus` **一个风格**的三个技术变体（fp32 / fp16 / IR），
> 因此"看不出风格可选"。经用户确认补入两个风格模型。

#### 交付

| 项 | 内容 |
|---|---|
| `data/models/RealESRGAN_x4plus_anime_6B.onnx` | **动漫**：RRDBNet 6 块 / 4,467,779 参数 / 17,961,298 B。sha256 `27c1f885…9b3527f` |
| `data/models/realesr-general-x4v3.onnx` | **轻量通用**：SRVGGNetCompact / 1,213,296 参数 / 4,866,394 B。sha256 `ba3e0db2…f8f6169` |
| `server/app/models/builtin_catalog.py` | 增补 2 条 `BuiltinModelSpec`（显存门槛 2048 / 1024 MB），内置项 3 → **5** |
| `web/src/api/mock/data.ts` | 同步 `MOCK_MODELS`——前端当前 `USE_MOCK = true`，不同步则界面上看不到 |

#### 关键点

- **权重来源可复现**：动漫取**官方 release**（`xinntao/Real-ESRGAN@v0.2.2.4`，
  sha256 `f872d837…e99da`）；轻量复用 T-807 已入库的夹具 `realesr-general-x4v3.pth`（v0.2.5.0）。
- **正确性沿用 T-807 的判据**（同源自检，不是"看起来对"）：anime_6B `max|torch−onnx| = 3.22e-06`、
  general-x4v3 `4.08e-06`，阈值 1e-3 → 通过才落盘。
- **"登记了"不等于"能用"**：另以**产品加载器本尊**真机复核
  （`.workbuddy/verify/builtin_models/check_builtin_models.py`）逐个加载并推理，**12/12 通过**。
  同口径（CPU EP，64×64 → 256×256）：`RealESRGAN_x4` 432 ms ｜ `anime_6B` 147 ms ｜
  `general-x4v3` **32 ms**。⚠️ 这三个数是**本机实测参照**，**不得**进产品参数表。
- **不扩大 PRD 承诺**：内置 5 个仍**全是 `scale=4`**——×2 / ×3 倍数无对应权重；
  人脸修复（F-14）仍属 S3。本次只补"风格模型"这一项缺口。
- **风格属于"模型固有属性"**：架构 / 许可证 / 最低显存 / 能力声明可声明（来源：前置调研与
  P0 实测），与"单机实测数值"（tile / 吞吐）不同——后者一律运行时求。

#### 验证

`verify_t604_models.py` **41/41**（新增 4 条断言：动漫架构与参数量、`source=builtin`、
轻量架构与参数量、显存门槛序）；`check_builtin_models.py` **12/12**；
前端 `npm run build`（含 `vue-tsc` 类型检查）通过。
