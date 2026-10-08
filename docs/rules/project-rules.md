# 项目规则（WebSR · 图像超分修复应用）

> 本文件只在当前项目生效，不得与全局规则冲突；冲突时以全局规则为准。
> 最后更新：2026-09-30

## 1. 基础约定

| 项 | 值 |
|---|---|
| 项目名称 | WebSR（图像超分与修复 Web 应用） |
| 时区 | `Asia/Shanghai`（UTC+8） |
| 交流语言 | 简体中文 |
| 多 Agent | `multi_agent: false`（单 Agent 串行执行） |
| 项目拓扑 | 前后端分离 |

## 2. 硬性约束（违反即返工）

### 2.1 环境隔离与目录约定

- **严禁全局安装**。所有 Python 环境、包、模型必须位于项目工作区内。
- **目录约定**（`data/` = 应用数据；`.workbuddy/` = 工具链与基准数据）：

| 目录 | 归属 | 内容 |
|---|---|---|
| `data/models/` | **应用数据** | **模型文件（含 OpenVINO IR 子目录 `ir/`）—— 统一放这里。路径为项目内相对路径 `<项目根>/data/models`（已确认），不是盘符根目录** |
| `data/uploads/` | 应用数据 | 用户上传的原图（运行时创建） |
| `data/outputs/` | 应用数据 | 超分/修复结果（运行时创建） |
| `data/thumbs/` | 应用数据 | 预览缩略图（运行时创建） |
| `data/calibration/` | 应用数据 | 首启自标定记录与硬件指纹缓存（运行时创建） |
| `.venvs/` | 工具链 | 隔离的 Python 推理环境 |
| `.workbuddy/results/` | 工具链 | 基准结果（原始 profile 在 `ort_profiles/`） |
| `tools/` | 工具链 | 基准与探测脚本 |
| `docs/` | 文档 | 见 §4 |

- **Node 环境使用 nvm4w 的全局版本**（`C:/nvm4w/nodejs/`，当前 node v24.15.0 / npm 11.12.1），**不使用** Agent 自带的受管 Node。
- 已有三个推理环境（`sr-gpu` / `sr-ov` / `sr-ovep`），**不得混用**：
  - `sr-gpu`：ORT + CUDA（NVIDIA 路径）
  - `sr-ov`：OpenVINO 原生（openvino 2026.4.0）
  - `sr-ovep`：ORT + OpenVINO EP 配对（openvino **2025.4.1**，与 `onnxruntime-openvino 1.24.1` ABI 匹配）
  - ⚠️ `sr-ov` 若用于 ORT + OpenVINO EP 会**静默回退 CPU**，不要这么做。
- 应用自身的后端依赖使用独立环境 **`.venvs/sr-app`**（✅ 2026-10-08 已建立，**不含 torch** —— 应用内无训练执行），**不要**污染上述三个基准环境。
- **ORM 与迁移**（`T-303` 定案，见 ADR-006）：**SQLAlchemy 2.x 同步引擎 + Alembic**。SQLite 适配的 11 条约束（`render_as_batch`、迁移期 `foreign_keys=OFF`、PRAGMA 只走 connect 事件、`UTCDateTime`、枚举不设 CHECK 等）**违反即返工**。

### 2.2 推理引擎设计铁律

1. **机制硬编码，数值运行时求。**
   可写死代码的只有因果性防御：EP 真实性校验（读 ORT profile 节点归属）、`arena_extend_strategy=kSameAsRequested`、CPU 路径禁 fp16、Intel GPU 判定看 `FULL_DEVICE_NAME`、OOM 降档链。
   所有数量（tile / 精度 / EP / 线程数 / 并发度）必须由运行时探测 + 首启自标定得出。
2. **禁止用 `session.get_providers()` 当作 EP 生效的证据。**
   唯一可信证据是 ORT profiling JSON 的节点级归属；判据是**该 EP 节点数 > 0 且 CPU 节点数 = 0**（节点数 = 1 是整图融合的正常现象，不能据此判为未接管）。
3. **禁止用 `"GPU" in available_devices` 判断 Intel 集显可用。**
   OpenVINO 会把 NVIDIA 卡也枚举为 `(dGPU)` 并接受调用（但更慢）。必须读 `FULL_DEVICE_NAME`。
4. **禁止读标称显存当可用显存。**
   必须 `nvml` 实读；Windows 桌面进程常占数百 MB（本机标称 8 GB 实测可用 7.4 GB）。
5. **降级必须显式**，不得静默降画质；用户须能知道自己当前处于哪个档位。
6. **分块推理必须做 overlap + feather**，不得只加 pad 或平移窗口了事。

### 2.3 数据与安全

- 环境变量文件固定三份：`.env.example`（示例，入库）、`.env.dev`（开发，入库）、`.env.prd`（生产，**不跟踪**，必须进 `.gitignore`）。
- 敏感信息只允许写入环境变量文件，**禁止**写入代码、Dockerfile 或 `dev-info.md`。
- 生产环境 CORS 必须限定前端域名白名单，**禁止** `*`。
- 服务绑定地址：有反向代理绑 `127.0.0.1`；无反向代理绑 `0.0.0.0`。

### 2.4 端口

- 约定端口一旦确定即写入 `docs/tech/dev-info.md`，后续以该文件为准。
- **端口被占用时禁止自动 kill 非目标进程，禁止自动递增换端口**；须先识别占用进程归属，必要时询问用户。

## 3. 本项目特有的领域约束

| 约束 | 说明 |
|---|---|
| 色彩通道 | Real-ESRGAN 系期望 **BGR** 输入（OpenCV 约定）；PIL 读取后必须转换，否则色彩错乱 |
| 归一化 | 输入 `[0,1]`，输出截断到 `[0,1]`；Real-ESRGAN 是 `x/255 → 推理 → clamp(0,1) → ×255` |
| 窗口倍数 | SwinIR/HAT 类要求输入边长向上取整到窗口倍数（SwinIR 为 8），否则报尺寸不匹配 |
| 许可证 | 主模型 Real-ESRGAN 为 BSD-3-Clause，可商用；**Upscayl 内置的 Remacri / Ultramix / Ultrasharp 标注非商用，不得打包** |
| CPU 路径 | 必须 fp32；fp16 在无 fp16 加速单元的桌面 CPU 上更慢 |
| Intel 路径 | 走 OpenVINO 原生 API，**禁止**在 ORT 里注册 OpenVINO EP（实测慢 2.5 倍） |

## 4. 文档维护

- 文档结构遵循 `fullstack-general` 技能规范：`docs/{rules,prd,tech,plan,debug,deploy,test,experience}/`
- 前置调研产出统一放 `docs/tech/research/`，索引见该目录 `README.md`
- 阶段任务文档：`docs/plan/tasks/<milestone-id>/STEP-<stage>.md`
- `docs/plan/project-progress.md` 是**调度状态的唯一事实源**；不承载完整技术方案
- 编号规范：Bug `Bug-XXX`、任务 `T-XXX`、功能变更 `CHG-YYYYMMDD-序号`、测试用例 `TC-<模块>-XXX`（均全局唯一，不带日期/模块前缀）
- 编码：源码文件编码 UTF-8；在 Git Bash 下处理中文路径时注意避免写出会乱码的 `.ps1` / `.bat` 脚本
