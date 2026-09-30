# WebSR · 图像超分与修复 Web 应用

> 把已验证的推理能力，变成一个可点选的产品。

本机运行的前后端分离 Web 应用：上传低清图 / 截图 / 动漫图，选择模型与参数，在**自己的硬件上**完成超分与修复——并且**看得见**当前用的是哪个执行提供器、什么精度、多大的分块，以及**为什么**是这些值。

---

## 当前状态

| 项 | 值 |
|---|---|
| **进度** | **M1 需求与 PRD ✅ 已验收** ｜ **M2 技术架构（步骤 3）产出已完成，待验收** |
| 下一里程碑 | M3 任务拆解与开发计划（步骤 4） |
| 代码目录 | `web/`（前端）与 `server/`（后端）**尚未创建**，将在步骤 5C / 5D 建立 |

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
├── web/                   # 前端（Vue 3 + Vite + TS）—— 待创建
├── server/                # 后端（FastAPI）—— 待创建
├── .venvs/                # 隔离的 Python 推理环境（不进版本库）
└── .workbuddy/            # 工具链数据：项目记忆 + 基准结果
```

**三条边界**：① `data/`（应用数据，可迁移）↔ `.workbuddy/`（工具链数据）；② `web/` + `server/`（应用代码）↔ `tools/`（开发工具）；③ `.venvs/sr-app`（应用依赖）↔ `sr-gpu` / `sr-ov` / `sr-ovep`（基准验证环境，**应用不得污染**）。

---

## 快速开始

> ⚠️ `web/` 与 `server/` 尚未创建（步骤 5C / 5D）。以下命令是**目标形态**，当前不可执行。

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

`data/models/`（约 294 MB）随仓库提供：

| 文件 | 说明 |
|---|---|
| `RealESRGAN_x4.onnx` | 主模型（RRDBNet，16,697,987 参数，opset 17，动态 H/W） |
| `RealESRGAN_x4_fp16.onnx` / `_fp16all.onnx` | fp16 变体（Resize 是否保持 fp32 之别） |
| `RealESRGAN_x4_s512.onnx` | H/W 冻结为 512 的静态 shape 版 |
| `ir/` | OpenVINO IR（fp32 / fp16，`.xml` + `.bin` 成对） |
| `inspect.json` | 模型结构检查结果 |

**许可证**：Real-ESRGAN 为 **BSD-3-Clause，可商用**。
⚠️ **Upscayl 内置的 Remacri / Ultramix / Ultrasharp 为非商用许可，禁止打包进本仓库。**

---

## 文档地图

| 文档 | 路径 | 作用 |
|---|---|---|
| 项目规则 | [`docs/rules/project-rules.md`](docs/rules/project-rules.md) | **硬性约束与领域铁律，动手前必读** |
| 需求文档 | [`docs/prd/prd.md`](docs/prd/prd.md) | 功能边界、优先级、档位矩阵、验收标准 |
| 技术架构 | [`docs/tech/tech-arch.md`](docs/tech/tech-arch.md) | 分层、模块边界、数据模型、API 与 SSE 规范、引擎五阶段 |
| 架构决策 | [`docs/tech/arch/`](docs/tech/arch/) | ADR-001~005 |
| 环境信息 | [`docs/tech/dev-info.md`](docs/tech/dev-info.md) | 技术栈、版本、端口、环境变量 |
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
