# WebSR · 图像超分与修复 Web 应用

> 把已验证的推理能力，变成一个可点选的产品。

本机运行的前后端分离 Web 应用：上传低清图 / 截图 / 动漫图，选择模型与参数，在**自己的硬件上**完成超分与修复——并且**看得见**当前用的是哪个执行提供器、什么精度、多大的分块，以及**为什么**是这些值。

---

## 当前状态

| 项 | 值 |
|---|---|
| **进度** | **M0–M5 全部完成**（M1/M2/M3 已验收；M4 前端原型已收口；M5 步骤 5C **已验收**、5D 已判定收口）｜**M6 进行中** |
| 当前阶段 | **M6 步骤 6（前后端整合）** —— 🔵 **进行中**；**S1 主链路已真机打通**（`T-700` 契约 v1.0 ✅ → `T-701` Mock 退场 ✅ → `T-702` 全链路 ✅ + **Playwright 浏览器级补测 ✅ 81/81**） |
| 下一步 | **`T-703`**（主链路系统联调：单图超分端到端，P0） |
| 门控 | ✅ **已通过（2026-10-10）**：5D 收口 → `T-700`（契约 v1.0 定稿）→ 真实接口联调（`T-701`/`T-702`）**均已完成** |
| 代码目录 | `web/` ✅（15 业务组件 / 6 视图 / **真实 API + SSE 驱动，无 Mock**）｜ `server/` ✅（FastAPI，`T-601`~`T-609` + 引擎核心 `T-802`~`T-807`） |
| 端到端验证 | `scripts/test-script/verify_t702_e2e.py` —— 唯一覆盖**真实推理路径 + 真实 SSE + 真实产物**的脚本：直连 `:8000` **41/41**、经 Vite 代理 `:5173` **43/43**；全量后端回归 **776 项断言全绿** |
| 契约状态 | `docs/tech/api-contract.md` = **`v1.0 · 已冻结`**（8 个冻结点全 ✅、错误码 **20** 条、新增 `/api/tasks/{task_id}/logs`）；一致性校验 `verify_t700_contract.py` **33/33** |

> 里程碑、任务池与验收状态的**唯一事实源**是 [`docs/plan/project-progress.md`](docs/plan/project-progress.md)。

---

## 这个应用解决什么

三条设计来自真实实测（见 [`docs/tech/research/`](docs/tech/research/)），不是设想：

1. **机制硬编码，数值运行时求。**
   开发机的实测数值**不得**进入产品参数表；tile / 精度 / EP / 并发度全部由**运行时探测 + 首启自标定**得出。写死在代码里的只有**因果性防御**：EP 真实性校验（读 ORT profile 的节点归属）、`arena_extend_strategy=kSameAsRequested`、CPU 路径禁 fp16、Intel GPU 判定看 `FULL_DEVICE_NAME`、OOM 降档链。

2. **档位是一等概念，能力声明驱动。**
   `T0` 纯 CPU ｜ `T1` 消费级独显 ｜ `T2` 高端 16–24G ｜ `T3` 专业卡 48G+。**禁止按设备型号硬编码分支**（不写 `if "4090" in gpu_name`），一律读能力声明。不可验证的高端路径通过**档位模拟开关**在 T1 上真实执行。

3. **降级必须显式。**
   OOM 自动降档、EP 回退、标定未完成时的「保底档」，都以**警告事件**返回并在界面标注原因——**绝不静默**。

---

## 技术栈

| 层 | 选择 |
|---|---|
| 拓扑 | 前后端分离，本机直接运行（**不做 Docker**，v2 再议） |
| 前端 | Vue 3 + Vite + TypeScript（包管理器 **npm**） |
| 后端 | FastAPI（Python，包管理器 **uv**）—— 推理栈是 Python，这是硬约束 |
| 数据库 | SQLite（WAL）；只存元信息与索引，**图片本体走文件系统** |
| 推理 | ONNX Runtime（CUDA EP / CPU EP）、OpenVINO 原生、ncnn（**条件支持**） |
| 任务 | **进程内**任务队列 + **SSE** 进度推送（因此 uvicorn **必须单 worker**） |
| 端口 | 前端 `5173`、后端 `8000`（均绑 `127.0.0.1`） |

版本与环境细节见 [`docs/tech/dev-info.md`](docs/tech/dev-info.md)。

---

## 目录结构

```
WebSR/
├── data/                  # 【应用数据】可读写、可打包、可迁移
│   ├── models/            #   模型文件（.onnx + OpenVINO IR 子目录 ir/）—— 随仓库提供，只读
│   ├── uploads/           #   用户上传原图      ┐
│   ├── outputs/           #   超分 / 修复结果   │ 运行期创建，
│   ├── thumbs/            #   预览缩略图        │ 不进版本库
│   ├── calibration/       #   自标定记录与硬件指纹缓存 ┘
│   └── app.db             #   SQLite 元信息库（运行时创建）
├── docs/                  # 文档（见下方「文档地图」）
├── tools/                 # 基准与探测脚本（开发期工具，不参与应用运行时）
├── web/                   # 前端（Vue 3 + Vite + TS）—— ✅ 已创建（步骤 5C）
├── server/                # 后端（FastAPI）—— ✅ 已创建（步骤 5D：骨架 T-601~T-609 + 引擎核心 T-802~T-807）
├── .venvs/                # 隔离的 Python 推理环境（不进版本库）
└── .workbuddy/            # 工具链数据：项目记忆 + 基准结果
```

**三条边界**：① `data/`（应用数据，可迁移）↔ `.workbuddy/`（工具链数据）；② `web/` + `server/`（应用代码）↔ `tools/`（开发工具）；③ `.venvs/sr-app`（应用依赖）↔ `sr-gpu` / `sr-ov` / `sr-ovep`（基准验证环境，**应用不得污染**）。

---

## 快速开始

> ✅ `web/` 与 `server/` **均已可运行，且前端已接真实后端**（Mock 层已于 `T-701` 整体删除，**无运行期模拟回退**）。开发期由 Vite `server.proxy` 把 `/api` 转发到 `127.0.0.1:8000`，故**两个都要起**；后端沿用 `.venvs/sr-app`（须先按 [`docs/tech/dev-info.md`](docs/tech/dev-info.md) §3 准备环境）。下列命令均可执行。

**前置条件**

- Windows 11（开发与验证环境；架构上不引入 Windows 专属依赖，Linux 可跑）
- Node.js + `npm`
- Python 3.13 + [`uv`](https://docs.astral.sh/uv/)
- 显卡可选：有 NVIDIA 走 CUDA；有 Intel GPU 走 OpenVINO 原生；都没有则降级 CPU 并**明确告知**

**后端**

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

**前端**

```bash
cd web
npm install
npm run dev        # http://127.0.0.1:5173
```

> 约定端口被占用时**不要**自动换端口：先识别占用进程归属并处理（见 `docs/rules/project-rules.md` §2.4）。

---

## 模型资产

`data/models/`（约 316 MB）随仓库提供：

| 文件 | 说明 |
|---|---|
| `RealESRGAN_x4.onnx` | 主模型 / 通用写实（RRDBNet，16,697,987 参数，opset 17，动态 H/W） |
| `RealESRGAN_x4plus_anime_6B.onnx` | 动漫风格（RRDBNet 6 块，4,467,779 参数） |
| `realesr-general-x4v3.onnx` | 轻量通用（SRVGGNetCompact，1,213,296 参数） |
| `RealESRGAN_x4_fp16.onnx` / `_fp16all.onnx` / `_s512.onnx` | 精度与静态-shape 变体（**基准资产，刻意不登记为内置项**） |
| `ir/` | OpenVINO IR（fp32 / fp16，`.xml` + `.bin` 成对） |
| `inspect.json` | 模型结构检查结果 |

> **产品内置登记 5 项** = 3 风格 + 2 技术变体：通用/写实 `RealESRGAN_x4`、动漫 `anime_6B`、轻量通用 `realesr-general-x4v3`，外加 `_fp16` 与 IR fp16 对。**风格由「模型」区分，参数面板刻意不设风格开关**。
> ⚠️ 内置 5 个**全是 `scale=4`** → ×2 / ×3 倍数暂无对应权重；人脸修复（F-14）属 S3。

**许可证**：Real-ESRGAN 为 **BSD-3-Clause，可商用**。
⚠️ **Upscayl 内置的 Remacri / Ultramix / Ultrasharp 为非商用许可，禁止打包进本仓库。**

---

## 文档地图

| 文档 | 路径 | 作用 |
|---|---|---|
| 项目规则 | [`docs/rules/project-rules.md`](docs/rules/project-rules.md) | **硬性约束与领域铁律，动手前必读** |
| 需求文档 | [`docs/prd/prd.md`](docs/prd/prd.md) | 功能边界、优先级、档位矩阵、验收标准 |
| 技术架构 | [`docs/tech/tech-arch.md`](docs/tech/tech-arch.md) | 分层、模块边界、数据模型、API 与 SSE 规范、引擎五阶段 |
| 架构决策 | [`docs/tech/arch/`](docs/tech/arch/) | ADR-001~006 |
| 环境信息 | [`docs/tech/dev-info.md`](docs/tech/dev-info.md) | 技术栈、版本、端口、环境变量 |
| API 契约 | [`docs/tech/api-contract.md`](docs/tech/api-contract.md) | 端点、统一错误体（**20 码**）、SSE 事件定义（**`v1.0 · 已冻结`**，`T-700` / 2026-10-10 定稿） |
| 前端原型 | [`docs/prototype/`](docs/prototype/) | **设计 token 唯一来源**（`tokens.css`）+ 6 份规范文档 + 9 张页面图 |
| 阶段任务文档 | [`docs/plan/tasks/`](docs/plan/tasks/) | 各里程碑阶段的执行细节与验证结果（M3/M4/M5/M6） |
| 前置调研 | [`docs/tech/research/`](docs/tech/research/) | 6 份调研与实测报告（含 P0 实测证据） |
| 项目进度 | [`docs/plan/project-progress.md`](docs/plan/project-progress.md) | **调度状态唯一事实源** |

---

## 开发约定（摘要）

完整约束见 [`docs/rules/project-rules.md`](docs/rules/project-rules.md)。

- **严禁全局安装**：Python 环境、包、模型一律留在项目内（`.venvs/`、`data/models/`）
- **推理出口唯一**：任何推理必须经 M2 引擎，业务代码不得自行创建推理会话
- **模型目录只读**：`data/models/` 的任何运行期写入都是不允许的
- 环境变量三份：`.env.example`（示例）、`.env.dev`（开发）、`.env.prd`（**不跟踪**）
- 服务绑 `127.0.0.1`；CORS 禁止 `*`
- 中文单语交付（不做 i18n 框架，但文案集中管理）
- 时区 `Asia/Shanghai`（UTC+8）；数据库时间字段存 UTC
