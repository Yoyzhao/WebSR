# 开发环境信息

> 最后更新：2026-10-08（步骤 3 技术栈已确认；5C 前端已落地并回填实际版本）

---

## 1. 技术栈说明

| 项 | 值 |
|---|---|
| 项目拓扑 | **前后端分离** |
| 前端框架 | **Vue 3 + Vite + TypeScript** |
| UI 组件库 | **Element Plus 2.14.6**（T-302 定案 2026-09-30） |
| 后端框架 | **FastAPI**（Python）—— 见 ADR-001 |
| 后端与推理关系 | **同进程内嵌**（FastAPI 进程内直接调用 ORT/OpenVINO），见 ADR-005 |
| 数据库 | **SQLite**（存元信息；图片本体走文件系统）—— 见 ADR-002 |
| ORM | ✅ **定案**：**SQLAlchemy 2.x 同步引擎 + Alembic**（`T-303`，见 [ADR-006](arch/ADR-006-ORM与迁移方案.md)） |
| 异步任务 | 进程内任务队列 + **SSE** 推送进度（见 ADR-005）；**uvicorn 必须单 worker**（SSE 依赖进程内广播） |
| 训练依赖 | **不引入**：应用内**不含 PyTorch 训练栈**（F-13 为离线形态，见 PRD §7.6）。`.venvs/sr-app` **不含 torch** |
| 前端包管理器 | **npm**（随 nvm4w 全局 Node） |
| 后端包管理器 | **uv** |
| 部署形态 | **本机直接运行**（前端 dev server + 后端进程）；**不做 Docker**（v2 再考虑） |
| 时区策略 | `Asia/Shanghai`（UTC+8） |

---

## 2. 版本信息

| 组件 | 版本 | 说明 |
|---|---|---|
| 操作系统 | Windows 11（Build 10.0.26300.9457），中文版 | 满足 Windows ML 新 EP 要求（24H2+） |
| 终端 | Git Bash（POSIX sh）；另有 PowerShell 5.1 可用 | bash 为默认终端 |
| Node.js | **v24.15.0**（**nvm4w 全局**：`C:/nvm4w/nodejs/node.exe`） | ✅ **项目统一使用 nvm4w 全局版本**（用户指定）；**不使用** Agent 受管的 22.22.2 |
| 包管理器（JS） | **npm 11.12.1**（随 nvm 全局 Node） | `pnpm` / `yarn` / `bun` 未安装 |
| Python | **3.13.14**（受管：`C:/Users/Yoy/.workbuddy/binaries/python/versions/3.13.12/python.exe`） | ⚠️ **目录名 `3.13.12` 与实际解释器版本 `3.13.14` 不一致**（2026-10-08 实测纠正，原记录把两者写反了）。系统版 `C:/Program Files/Python313/python.exe` 为 **3.13.12** |
| 包管理器（Python） | **uv 0.12.21**（2026-10-08 实测） | 可用 |
| SQLite（运行时，随 Python 内置） | **lib 3.53.1** · `sqlite3.threadsafety = 3`（serialized） | 2026-10-08 实测能力：**json1 / RETURNING / STRICT 表 / UPSERT / 生成列** 全部可用 |
| 前端框架 | **Vue 3.5.42 + Vite 8.3.0**（✅ **2026-09-30 `web/` 实际安装版本**） | 已落地，见 `web/package.json` |
| 前端 UI 组件库 | **Element Plus 2.14.6** + **@element-plus/icons-vue 2.3.2** | T-302 定案（2026-09-30） |
| 前端字体 | **自托管拉丁子集可变字体**：`@fontsource-variable/inter` / `@fontsource-variable/jetbrains-mono` **5.3.0** | 10-08 引入。**只取 latin 子集**（`unicode-range` 限定），中文回退系统 Noto Sans SC —— 中文**不打包** webfont。落地：`web/src/assets/fonts/`（Inter 47 KB + JetBrains Mono 39 KB）+ `web/src/styles/fonts.css`。详见 `docs/prototype/README.md` §8 |
| 前端状态 / 路由 | **pinia 4.0.3** + **vue-router 4.6.4** | `web/package.json`（10-08 实际安装版本） |
| 前端日期库 | **dayjs 1.11.23**（`utc` + `timezone` 插件） | 时区固定 `Asia/Shanghai`，不依赖浏览器时区 |
| UI 主题策略 | **深色默认 → 可切浅色 → 可跟随系统**（三态并存） | T-401 定案（2026-09-30）；机制见 §10 |
| 后端框架 | FastAPI（版本待装并回填） | — |
| ORM / 迁移 | **SQLAlchemy 2.1.4** + **Alembic 1.20.0** | ✅ 2026-10-08 装入 `.venvs/sr-app` 并实测（建表 / batch 改列 / 降级 / 幂等）。见 [ADR-006](arch/ADR-006-ORM与迁移方案.md) |
| 数据库 | SQLite（Python 内置 `sqlite3`） | 文件路径待定，建议 `data/app.db` |
| 推理运行时 | `onnxruntime-gpu 1.22.0` / `openvino 2026.4.0` / `onnxruntime-openvino 1.24.1` | **沿用 `.venvs/` 三个已验证环境的设计**，但应用需新建独立环境 `sr-app` |


**命令行工具可用性**

| 工具 | 版本 | 备注 |
|---|---|---|
| `git` | 2.55.0.windows.3 | ✅ |
| `rg`（ripgrep） | 15.2.0 | ✅ |
| `fd` | 10.5.0 | ✅ |
| `jq` | — | ❌ **未安装**，需要解析 JSON 时用 Python 替代 |
| `nvidia-smi` | 610.88 | ✅ |
| `nvcc` | — | ❌ 未安装（**不影响**：ORT 走 CUDA Runtime，无需 Toolkit） |

---

## 3. 目录与入口

```
E:/Desktop/Workspace2/WorkBuddySpace/WebSR/
├── web/                        # 前端（Vue 3 + Vite + TS）—— ✅ 已创建（2026-09-30，步骤 5C）
│   ├── src/                    #   源码（views / components / api / stores / types / styles）
│   ├── package.json
│   └── vite.config.ts
├── server/                     # 后端（FastAPI）—— 待创建（步骤 5D）
│   ├── app/
│   │   ├── main.py             #   FastAPI 入口
│   │   ├── api/                #   路由（按 PRD §5 的接口分组）
│   │   ├── core/               #   配置、日志、错误处理
│   │   ├── engine/             #   M2 推理引擎（探测/验证/标定/决策/降级）
│   │   ├── models/             #   SQLAlchemy 实体
│   │   ├── schemas/            #   Pydantic DTO
│   │   └── tasks/              #   异步任务队列与进度广播
│   └── pyproject.toml
├── data/                       # 【应用数据】运行期读写
│   ├── models/                 #   模型文件（onnx + OpenVINO IR 子目录 ir/ + ncnn）
│   ├── uploads/                #   用户上传原图（运行时创建）
│   ├── outputs/                #   超分/修复结果（运行时创建）
│   ├── thumbs/                 #   预览缩略图（运行时创建）
│   ├── calibration/            #   自标定记录与硬件指纹缓存（运行时创建）
│   └── app.db                  #   SQLite 元信息库（运行时创建）
├── docs/                       # 文档（fullstack-general 规范结构）
│   ├── rules/project-rules.md
│   ├── prd/prd.md
│   ├── tech/
│   │   ├── dev-info.md         #   本文件
│   │   ├── tech-arch.md        #   技术架构
│   │   ├── api-contract.md     #   API 契约（草案；最终冻结在 T-700 / M6）
│   │   ├── research/           #   前置调研与实测证据（6 份，见其 README.md）
│   │   └── arch/               #   ADR 架构决策记录
│   ├── prototype/              #   ★ 前端原型（5A 产出）：9 张页面图 + 6 份文档 + tokens.css + assets/
│   ├── plan/
│   │   ├── project-progress.md
│   │   ├── project-dev-plan.md
│   │   └── tasks/              #   阶段任务文档（M3/STEP-4.md、M4/STEP-5A.md、M5/STEP-5C.md …）
│   ├── debug/  deploy/  test/  experience/
├── tools/                      # 基准与探测脚本（11 个 .py，既有约定，保留）
├── .venvs/                     # 项目内隔离环境
│   ├── sr-gpu / sr-ov / sr-ovep   # 基准与验证环境（不得污染）
│   └── sr-app                  #   ★ 应用自身依赖环境 —— ✅ 已创建（2026-10-08；**不含 torch**：应用内无训练执行）
├── .workbuddy/                 # 【工具链数据】非应用数据
│   ├── results/                #   基准结果（原始 profile 在 ort_profiles/）
│   ├── verify/                 #   5C 闭环验证证据（shots/ 截图 + scripts/ 脚本）
│   └── memory/                 #   项目记忆
└── (顶层无 todo.txt —— 需求备忘已成稿转入 `docs/prd/prd.md`)
```

> **三个边界**：① `data/`（应用数据，可迁移）↔ `.workbuddy/`（工具链产物，不交付）；② `web/`+`server/`（应用代码）↔ `tools/`（开发工具）；③ `.venvs/sr-app`（应用依赖）↔ `sr-gpu`/`sr-ov`/`sr-ovep`（基准验证环境，**应用不得污染**）。

| 入口 | 路径 | 状态 |
|---|---|---|
| 前端代码目录 | `web/` | ✅ 已创建（步骤 5C，2026-09-30） |
| 后端代码目录 | `server/` | 待创建（步骤 5D） |
| 前端启动 | `npm run dev`（在 `web/`） | ✅ 可用（`http://127.0.0.1:5173`，`strictPort` 固定端口） |
| 后端启动 | `uv run uvicorn app.main:app --reload --port 8000`（在 `server/`） | 待创建 |
| 前端构建 | `npm run build` | ✅ 可用（`vue-tsc -b && vite build`，实测通过） |
| 测试命令 | 后端 `pytest`；前端 `vitest`（待步骤 8 细化） | 待创建 |

---

## 4. 端口与地址

| 服务 | 本地地址 | 端口 | 备注 |
|---|---|---|---|
| 前端 dev server | `http://127.0.0.1:5173` | **5173** | Vite 默认。**端口占用时不得自动递增，须先处理占用** |
| 后端 API | `http://127.0.0.1:8000` | **8000** | 本地单机形态**必须绑 `127.0.0.1`**，禁止 `0.0.0.0` |
| 前端代理 | `/api` → `http://127.0.0.1:8000` | — | 由 Vite `server.proxy` 配置，避免开发期 CORS |


---

## 5. 推理环境与模型资产（本项目的核心既有资产）

### 5.1 项目内隔离环境（`.venvs/`，**严禁全局安装**）

| 环境 | 体积 | 关键包 | 用途 |
|---|---|---|---|
| `.venvs/sr-gpu` | 3.1 GB | `onnxruntime-gpu 1.22.0`、`nvidia-ml-py 13.610.43`、`numpy 2.5.3`、`psutil 7.2.2` | NVIDIA 路径（**真正验证过 CUDA EP 生效**，见 P0 报告 §2） |
| `.venvs/sr-ov` | 594 MB | `onnxruntime-openvino 1.24.1`、`openvino 2026.4.0`、`numpy 2.5.3` | OpenVINO **原生**路径 |
| `.venvs/sr-ovep` | 384 MB | `onnxruntime-openvino 1.24.1`、`openvino 2025.4.1`、`numpy 2.3.5` | ORT + OpenVINO EP **配对环境**（仅用于验证 EP 行为） |
| **`.venvs/sr-app`** | — | **SQLAlchemy 2.1.4**、**Alembic 1.20.0**、mako、markupsafe、typing-extensions | ★ **应用自身依赖环境**（**不含 torch**）。2026-10-08 建立；随 5D 逐步补齐 FastAPI / uvicorn / onnxruntime 等 |

> ⚠️ `sr-ov` 与 `sr-ovep` 的 openvino 版本**必须不同**：`onnxruntime-openvino 1.24.1` 是针对 **2025.4.1** 编译的，装 2026.4.0 会 ABI 不兼容并**静默回退 CPU**。详见 P0 报告 §5.3。

### 5.2 模型资产（`data/models/`，共 294 MB）

> 📌 **模型统一存放于 `data/models/`**（用户指定的应用数据目录，2026-09-30 从 `.workbuddy/models/` 迁入）。
> 迁移后已用 `tools/p0_1_cuda_smoke.py` 做端到端验证：**1024 节点全在 CUDA，输出 mean 0.534278 与迁移前一致**。

| 文件 | 大小 | 说明 |
|---|---|---|
| `RealESRGAN_x4.onnx` | 67,051,616 B | 主模型（RRDBNet，16,697,987 参数，opset 17，动态 H/W）。sha256 `5c586662…b89c033` |
| `RealESRGAN_x4_fp16.onnx` | 33,748,503 B | fp16（Resize 保持 fp32） |
| `RealESRGAN_x4_fp16all.onnx` | 33,748,503 B | 全量 fp16 |
| `RealESRGAN_x4_s512.onnx` | 67,129,351 B | H/W 冻结为 512 的静态 shape 版 |
| `ir/` | — | OpenVINO IR（fp32 63.7 MB / fp16 31.85 MB） |
| `inspect.json` | 1,070 B | 模型结构检查结果 |

### 5.3 基准结果与脚本

- 结果：`.workbuddy/results/*.json`（P0 矩阵、整图实测、后端对比）；原始 profile 在 `ort_profiles/`
- 脚本：`tools/` 下 11 个 `.py`，其中 `_runtime_env.py`（Windows DLL 路径注册）、`p0_1_cuda_smoke.py`（EP 真实性验证）、`p0_2_vram_matrix.py`（可信显存矩阵）是**产品应当吸收的启动自检逻辑**

---

## 6. 环境变量清单

| 变量名 | 作用 | 所在文件 | 是否敏感 |
|---|---|---|---|
| `APP_DATA_DIR` | 数据根目录，默认 `./data` | `.env.dev` | 否 |
| `APP_DB_URL` | SQLite 连接串，默认 `sqlite:///./data/app.db` | `.env.dev` | 否 |
| `APP_HOST` / `APP_PORT` | 后端绑定地址与端口（默认 `127.0.0.1` / `8000`） | `.env.dev` | 否 |
| `APP_LOG_LEVEL` | 日志级别（默认 `info`） | `.env.dev` | 否 |
| `APP_MAX_UPLOAD_MB` | 上传单文件大小上限 | `.env.dev` | 否 |
| `APP_CORS_ORIGINS` | 允许的前端来源（本地为 `http://127.0.0.1:5173`） | `.env.dev` | 否 |
| `HF_ENDPOINT` | 模型下载镜像源（可选） | `.env.dev` | 否 |
| `APP_*`（生产覆盖值） | 生产环境同名覆盖 | `.env.prd` | **是（不跟踪）** |

> 安全约束：敏感信息只写入环境变量文件；`.env.prd` **必须**加入 `.gitignore` —— **已落实**：仓库已初始化并推送至 `https://github.com/Yoyzhao/WebSR.git`（`main`），`.gitignore` 已排除 `.env.prd`、`.venvs/`、运行期数据与前端验证产物。
> 变量名以 `APP_` 前缀统一，避免与系统环境变量冲突。

---

## 7. 测试账号与测试数据

| 项 | 值 |
|---|---|
| 测试账号 | 不适用（本地单机应用，无用户体系） |
| 测试图片 | 待准备（建议放 `testdata/`，包含：低清照片、动漫图、带人脸图、大分辨率图） |
| 其他说明 | 基准脚本用确定性合成图（`tools/_bench_common.py::make_test_image`），可用于回归对照 |
| 测试数据入口 | `data/uploads/`（可手工放入样图） |

---

## 8. AI Coding 工具链环境

| 维度 | 内容 | 备注 |
|---|---|---|
| 操作系统 | Windows 11 Build 10.0.26300.9457（中文版） | 无 WSL 环境 |
| 终端工具 | Git Bash（默认，POSIX sh）；PowerShell 5.1 | — |
| 命令行工具 | `git` 2.55.0、`rg` 15.2.0、`fd` 10.5.0、`nvidia-smi` 610.88 可用；**`jq` 缺失** | `jq` 缺失时用 Python 替代，未安装 |
| 可用插件 | Connector：`agent-mail`（已连接，未使用） | — |
| 可用 Skill | `fullstack-general`（本项目开发流程）、`ort-ep-verify-bench`（EP 验证与基准方法论）、`tencent-docx`/`tencent-pptx`/`xlsx`（文档产出）、`sites`（发布）、`cloud-service`（后端托管） | — |
| MCP 服务 | `agent-mail`、`genie-baas`（云服务）、`sheetagent`、`weixinpay` | 当前未使用 |
| 其他 Agent 能力 | 文件读写、Shell（bash/PowerShell）、Web 搜索与抓取、浏览器自动化（`agent-browser` Skill） | — |

---

## 9. 技术栈确认清单（步骤 3，已完成）

| # | 项 | 决定 | 依据 |
|---|---|---|---|
| 1 | 项目拓扑 | ✅ **前后端分离** | 用户要求 |
| 2 | 前端框架 | ✅ **Vue 3 + Vite + TypeScript** | 用户确认；ADR-001 |
| 3 | 后端实现方式 | ✅ **FastAPI（Python）** | **硬约束**：推理栈（ORT/OpenVINO）是 Python，换语言要额外搭跨语言通信层；ADR-001 |
| 4 | 数据库 | ✅ **SQLite** | 单机零配置，与"不做 Docker"契合；ORM 抽象便于后续迁 PostgreSQL；ADR-002 |
| 5 | 包管理器与启动方式 | ✅ 前端 **npm**（随 nvm4w 全局 Node）；后端 **uv**；前端 `npm run dev`、后端 `uvicorn --reload` | 本机可用性 |
| 6 | 部署形态 | ✅ **本机直接运行**；**不做 Docker**（v2 再议） | 用户指定 |

**由 PRD §8 继承、须在本阶段定案的两项**

| # | 项 | 决定 | 依据 |
|---|---|---|---|
| 7 | `.pth`/`.safetensors` 加载方式 | ✅ **离线转换工具**（不捆绑 PyTorch） | 用户确认；ADR-003 |
| 8 | `.bin` 来源 | ✅ **OpenVINO IR 与 ncnn 都支持**，按配套文件分派（`.xml` → OpenVINO；`.param` → ncnn） | 用户确认；ADR-003。⚠️ **ncnn 在 Windows/Python 下的安装方式需验证**，见 tech-arch §7 风险 |

**遗留待验证（不阻塞，但会影响实现）**：ncnn 运行时的获取方式、Vulkan 可用性、以及 ncnn 是否复现"黑图"已知 bug。

---

## 10. 前端主题机制（步骤 5A 定案）

| 项 | 约定 |
|---|---|
| 主题形态 | **深色默认 → 可手动切浅色 → 可跟随系统**（三态并存） |
| 默认值 | **深色**（`data-theme` 未设置或设为 `Dark`） |
| 切换入口 | 顶栏主题按钮（**唯一入口**，禁止各页面自行加切换控件） |
| 落地方式 | `<html>` 上设 `data-theme="Light"`；Element Plus 侧同时加 `dark` class 并引入其深色变量文件 |
| Token 唯一来源 | `docs/prototype/tokens.css`（含 `[data-theme="Light"]` 覆盖块）→ 5C 落地到 `web/src/styles/tokens.css` |
| 强调色对齐 | 必须把 `--el-color-primary` 覆盖为 `var(--Theme-primary)`，使组件库与设计稿一致 |
| 持久化 | `localStorage`；手动切换后以手动值为准，不再跟随系统 |
| 禁止 | ① 页面内硬编码色值；② 各组件自行判断深浅色（`if (isDark)`）；③ 把浅色做成深色的机械反相 |

**完整规范**：`docs/prototype/02-视觉与主题基线.md` §6。其中记录了浅色模式**三处必须单独验证**的差异——强调色需加深（`#0078D4` → `#0067C0`）、语义色需重取（深色版的浅绿/浅黄在白底上不可读）、图片容器不得加描边。
