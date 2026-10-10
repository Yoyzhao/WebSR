# WebSR · 图像超分与修复 Web 应用

> 把已验证的推理能力，变成一个可点选的产品。

本机运行的前后端分离 Web 应用：上传低清图 / 截图 / 动漫图，选择模型与参数，在**自己的硬件上**完成超分与修复——并且**看得见**当前用的是哪个执行提供器、什么精度、多大的分块，以及**为什么**是这些值。

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
├── server/                # 后端（FastAPI）：API + SSE + 推理引擎
├── web/                   # 前端（Vue 3 + Vite + TypeScript）
├── data/                  # 【应用数据】本地资产，不入版本库
│   ├── models/            #   模型文件（.onnx + OpenVINO IR 子目录 ir/）
│   ├── uploads/           #   用户上传原图      ┐
│   ├── outputs/           #   超分 / 修复结果   │ 运行期创建
│   ├── thumbs/            #   预览缩略图        │
│   ├── calibration/       #   自标定记录与硬件指纹缓存 ┘
│   └── app.db             #   SQLite 元信息库
├── docs/                  # 文档（见下方「文档地图」）
├── scripts/               # 验证脚本（端到端 / 画质 / 异常 / 验收）
├── tools/                 # 基准与探测脚本（开发期工具，不参与应用运行时）
├── .venvs/                # 隔离的 Python 推理环境（约 7 GB，不入版本库）
├── start-dev.bat          # 一键启动前后端（附端口占用自检）
├── start-backend.bat      # 仅启动后端
└── stop-dev.bat           # 按端口停止开发服务
```

**三条边界**：① `data/`（应用数据）↔ `.workbuddy/`（工具链数据，不交付）；② `web/` + `server/`（应用代码）↔ `tools/` + `scripts/`（开发工具）；③ `.venvs/sr-app`（应用依赖）↔ `sr-gpu` / `sr-ov` / `sr-ovep`（基准验证环境，**应用不得污染**）。

> `data/` 与 `.workbuddy/` **均不在版本库内**（前者是本地资产，后者是工具链产物），克隆后需自行补齐，见下方说明。

---

## 快速开始

> 前端**已无 Mock 层**，开发期由 Vite `server.proxy` 把 `/api` 转发到 `127.0.0.1:8000`，故**前后端都要起**。

**前置条件**

- Windows 11（开发与验证环境；架构上不引入 Windows 专属依赖，Linux 可跑）
- Node.js + `npm`
- Python 3.13 + [`uv`](https://docs.astral.sh/uv/)
- 显卡可选：有 NVIDIA 走 CUDA；有 Intel GPU 走 OpenVINO 原生；都没有则降级 CPU 并**明确告知**

**① 准备 Python 环境**（项目内隔离环境，严禁全局安装）

```bash
# 在 server/ 目录下执行，环境落在项目根的 .venvs/sr-app
set UV_PROJECT_ENVIRONMENT=<项目根>/.venvs/sr-app
uv sync
```

**② 启动后端**（`http://127.0.0.1:8000`）

```bash
cd server
uv run --active uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

> ⚠️ **必须单 worker，禁止 `--workers`**：任务队列是进程内的，多 worker 会导致任务状态不一致。

**③ 启动前端**（`http://127.0.0.1:5173`）

```bash
cd web
npm install
npm run dev
```

Windows 下也可直接双击项目根目录的 `start-dev.bat`（会检查端口占用并分别开窗口）。

> 约定端口被占用时**不要**自动换端口：先识别占用进程归属并处理（见 [`docs/rules/project-rules.md`](docs/rules/project-rules.md) §2.4）。

---

## 模型资产

**内置 7 项**（`data/models/`，本地资产、不随仓库分发）：

| 倍率 | 模型 | 说明 |
|---|---|---|
| ×4 | `RealESRGAN_x4plus` | 通用写实（RRDBNet） |
| ×4 | `RealESRGAN_x4plus_fp16` | 通用写实 fp16 |
| ×4 | `RealESRGAN_x4`（OpenVINO IR） | 通用写实，需 `uv pip install openvino` |
| ×4 | `RealESRGAN_x4plus_anime_6B` | 动漫风格 |
| ×4 | `realesr-general-x4v3` | 轻量通用（SRVGGNetCompact） |
| ×2 | `RealESRGAN_x2plus` | 通用写实 ×2 |
| ×3 | `RealCUGAN_up3x` | 动漫 ×3 |

另有若干**基准资产**（`_fp16all` / `_s512` 等技术变体）刻意不登记为内置项。

**克隆后如何补齐模型** —— `data/models/` 不随仓库分发，三条途径：

1. **应用内下载（推荐）**：启动后到「模型库」页，下载目录内置 **10 个已实测可用的模型**（×4:2 / ×2:4 / ×1:3），下载后自动转为 ONNX；
2. **手工拷贝**：把 `.onnx` 放进 `data/models/`（内置）或 `data/models/imported/`（用户导入）；
3. **自行转换**：`.pth` / `.safetensors` 是训练权重格式，需先转 ONNX 才能使用。

详见 [`docs/setup/first-run-model-setup.md`](docs/setup/first-run-model-setup.md)。

### ×1 = 修复，不是放大

下载目录中的三条 `1x*` 模型**输出尺寸与输入完全相同**，只修复画质：

| 模型 | 适用 |
|---|---|
| `1xDeNoise_realplksr_otf` | 去噪点（夜景高感光度、老照片颗粒） |
| `1xDeJPG_realplksr_otf` | 去静态 JPEG 压缩痕（反复保存/转发的图） |
| `1xDeH264_realplksr` | 去视频编码痕（录屏、视频/直播截图） |

UI 中该倍率标注为「**×1 修复**」，提示明确写「不改变尺寸，只修复画质」。

**许可证**：Real-ESRGAN 为 **BSD-3-Clause，可商用**。
⚠️ **Upscayl 内置的 Remacri / Ultramix / Ultrasharp 为非商用许可，禁止打包进本仓库。**

---

## 文档地图

| 文档 | 路径 | 作用 |
|---|---|---|
| 项目规则 | [`docs/rules/project-rules.md`](docs/rules/project-rules.md) | **硬性约束与领域铁律，动手前必读** |
| 需求文档 | [`docs/prd/prd.md`](docs/prd/prd.md) | 功能边界、优先级、档位矩阵、验收标准 |
| 技术架构 | [`docs/tech/tech-arch.md`](docs/tech/tech-arch.md) | 分层、模块边界、数据模型、API 与 SSE 规范、引擎结构 |
| 架构决策 | [`docs/tech/arch/`](docs/tech/arch/) | ADR-001~006 |
| 环境信息 | [`docs/tech/dev-info.md`](docs/tech/dev-info.md) | 技术栈、版本、端口、环境变量 |
| API 契约 | [`docs/tech/api-contract.md`](docs/tech/api-contract.md) | 端点、统一错误体（**20 码**）、SSE 事件定义（**v1.0.3 已冻结**） |
| 首次运行 | [`docs/setup/first-run-model-setup.md`](docs/setup/first-run-model-setup.md) | **克隆后补齐模型的完整步骤** |
| 前端原型 | [`docs/prototype/`](docs/prototype/) | **设计 token 唯一来源**（`tokens.css`）+ 规范文档 + 页面图 |
| 前置调研 | [`docs/tech/research/`](docs/tech/research/) | 调研与实测报告 |
| 阶段任务 | [`docs/plan/tasks/`](docs/plan/tasks/) | 各里程碑阶段的执行细节 |
| 项目进度 | [`docs/plan/project-progress.md`](docs/plan/project-progress.md) | 调度状态唯一事实源 |

---

## 开发约定（摘要）

完整约束见 [`docs/rules/project-rules.md`](docs/rules/project-rules.md)。

- **严禁全局安装**：Python 环境、包、模型一律留在项目内（`.venvs/`、`data/models/`）
- **推理出口唯一**：任何推理必须经引擎层，业务代码不得自行创建推理会话
- **模型目录只读**：`data/models/` 的任何运行期写入都是不允许的
- 环境变量三份：`.env.example`（示例）、`.env.dev`（开发）、`.env.prd`（**不跟踪**）
- 服务绑 `127.0.0.1`；CORS 禁止 `*`
- 中文单语交付（不做 i18n 框架，但文案集中管理）
- 时区 `Asia/Shanghai`（UTC+8）；数据库时间字段存 UTC
