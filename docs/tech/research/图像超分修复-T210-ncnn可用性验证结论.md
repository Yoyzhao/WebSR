# 图像超分修复应用 · T-210 ncnn 在 Windows/Python 下的可用性验证结论

> 任务：**T-210**（`docs/plan/tasks/M3/STEP-4.md` §T-305，编号保留）
> 时间：2026-10-09 ｜ 环境：本机 Windows 11 / i5-12400F（6C12T）/ RTX 3050 OEM 8 GB（驱动 610.88）
> 隔离环境：`.venvs/sr-ncnn`（**项目内新建**，未污染 `sr-app` / `sr-gpu` / `sr-ov` / `sr-ovep`）
> 验证脚本：`tools/verify_ncnn_windows.py` ｜ 原始证据：`.workbuddy/verify/t210/*.json`

---

## 0. 一页结论

### 结论：✅ **可用**（不再是"未验证"）

`ncnn` 在 **Windows + Python 下有官方 PyPI wheel**，且该 wheel **编译进了 Vulkan 后端**，可在真机上加载 `.param` + `.bin` 并完成真实推理，输出与 ONNX 主路径**数值一致**。

| # | 判定项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Windows/Python 获取方式 | ✅ **官方 pip wheel 存在** | `ncnn 1.0.20260526`，含 `cp38`~`cp314` × `win_amd64` / `win32` / `win_arm64` |
| 2 | 能否 import | ✅ | `ncnn.__version__ == 1.0.20260526` |
| 3 | 能否**真实推理**（非"仅 import"） | ✅ | realesrgan-x4plus（999 层）64×64 → 256×256 |
| 4 | 输出是否**全黑**（ncnn 已知 bug） | ✅ **未触发** | mean 0.4834 / std 0.1748 / ptp 0.855 |
| 5 | 与 ONNX 主路径是否一致 | ✅ **近乎完全一致** | **corr = 1.0000，PSNR = 63.39 dB** |
| 6 | Vulkan 后端 | ✅ wheel 内含，真机枚举 3050 | CPU↔Vulkan 最大差 0.0067（fp16 容差内） |
| 7 | 降级路径（外部可执行文件） | ✅ 可用 | `realesrgan-ncnn-vulkan.exe` rc=0，220→880 px，非全黑 |
| 8 | 依赖代价 | ⚠️ wheel 声明**强制依赖 opencv-python（113 MB）**，但**运行期并不需要**（实测推理后 `cv2` 未加载） | 见 §4 |

**14/14 断言通过**，脚本判定 `verdict = "usable"`，进程干净退出（exit 0）。

### 对产品承诺的影响

- PRD §7.5 中 ncnn 的 **"条件支持 / 验证通过前不计入 v1 承诺"** 可以**解除**：ncnn 具备成为**正式后端**的条件。
- 但"条件"的含义要**重新定义**（原定义已失效，见 §5）：不再是"后端能不能跑"，而是"**某个具体模型的层是否被 ncnn 覆盖**"——ncnn 的层覆盖弱于 ONNX 运行时，`.param` 加载失败必须**可降级、不影响其它格式**。

---

## 1. 验证方法（为什么"能 import"不算通过）

判据来自任务卡：**不只看"能否 import"，必须跑出一次真实推理并检查输出非全黑**——ncnn 有输出全黑的已知 bug。

脚本 `tools/verify_ncnn_windows.py` 因此把"通过"定义在三层递进上：

1. **非全黑**：`mean > 0.01` 且 `std > 0.01` 且 `ptp > 0.05`（单一阈值会被"接近黑但非黑"骗过，三个一起看）。
2. **CPU 与 Vulkan 互证**：两条独立代码路径给出同一结果（最大绝对差 < 0.05，fp16 容差），既证明 GPU 路径真的执行了，也证明没触发黑图 bug。
3. **与 ONNX 主路径互证**：这是最关键的一条——**只有输出与已验证的主路径一致，ncnn 才能被当作可互换后端**，而不是"跑起来了但结果是垃圾"。

输入统一使用 `tools/_bench_common.py::make_test_image`（确定性合成图），保证可复现、与既有基准可比。

---

## 2. 实测数据

### 2.1 推理正确性（输入 64×64 → 输出 256×256）

| 项 | ncnn CPU | ncnn Vulkan | ONNX / ORT CPU |
|---|---|---|---|
| 输出形状 | (3, 256, 256) | (3, 256, 256) | (3, 256, 256) |
| mean | 0.4834 | 0.4832 | 0.4840 |
| std | 0.1748 | 0.1747 | — |
| 耗时 | 769.8 ms | 1040.2 ms | — |

- **ncnn vs ONNX：corr = 1.0000，PSNR = 63.39 dB** → 同一模型、同一任务，差异仅来自 ncnn 权重的 fp16 存储。
- **CPU vs Vulkan：max abs diff = 0.00668** → 两条路径等价。

### 2.2 耗时随尺寸的变化（揭示 Vulkan 的真实收益）

| 输入 | 输出 | ncnn CPU | ncnn Vulkan | Vulkan 相对 |
|---|---|---|---|---|
| 64×64 | 256×256 | 769.8 ms | 1040.2 ms | **0.74×（更慢）** |
| 128×128 | 512×512 | 2681.1 ms | 1137.7 ms | **2.36×（更快）** |

**读法（重要，勿误读）**：Vulkan 有固定的初始化/传输开销，**小 tile 上是负收益**；输入从 64→128（面积 ×4）时 CPU 耗时 ×3.5，而 Vulkan 几乎不变（1040 → 1138 ms，说明该尺寸下它仍被固定开销主导）。

> ⚠️ **量级/倍数仅本机成立**（同 P0 报告口径）：换机器即变。可迁移的是**因果**——"Vulkan 的收益随 tile 面积增长、小 tile 被固定开销吃掉"。**不得把这两个数字写进产品参数表**。

### 2.3 降级路径（外部可执行文件）

```
realesrgan-ncnn-vulkan.exe -i input.jpg -o out.png -n realesrgan-x4plus -s 4
→ rc = 0，elapsed ≈ 4.1 s，220×220 → 880×880
→ 均值 126.65 → 127.36（亮度保持），max=255，非全黑
```

Vulkan 运行时 `C:\Windows\System32\vulkan-1.dll` **已就位**；exe 自带 `vcomp140.dll` / `vcomp140d.dll`，**开箱即可运行**。

---

## 3. 关键发现（4 条，直接影响 T-806 实现）

### 3.1 🔴 ncnn 的 `Mat(numpy)` 有两个致命陷阱（T-806 必读）

| 陷阱 | 现象 | 正确做法 |
|---|---|---|
| **按 (c, h, w) 解释，不是 (h, w, c)** | 传 HWC 数组时输入通道被当成图像高度，**extractor 越界读取**，输出形状错乱（实测出现 `c=64, w=12` 这类荒谬组合） | **传 CHW**：`ncnn.Mat(arr_chw)` |
| **借用 numpy 缓冲区，不拷贝** | 临时数组被 GC 后指针悬空 → **进程段错误（exit 139），没有任何 Python 回溯** | 显式 `data = np.ascontiguousarray(...)` 并保证它在整个前向传播期间存活 |

第二条尤其阴险：**第一次实现就是这么崩的，且崩溃点没有任何 Python 异常**（stdout 缓冲一并丢失，只看到 C++ 日志）。脚本里已加注释，防止后人"顺手改回 HWC"。

### 3.2 🔴 ncnn 的 Vulkan 实例必须显式销毁

`ncnn.create_gpu_instance()` 之后若不调用 `destroy_gpu_instance()`，**解释器在退出阶段段错误**（脚本断言全绿、JSON 已落盘，却在 `return` 后崩，exit 139）。这会污染 CI/验证脚本的退出码，**必须作为收尾步骤**。

### 3.3 🟡 ncnn 强制依赖 opencv-python，但**运行期不需要**

- wheel 的 `Requires-Dist` 里**硬依赖 `opencv-python`（113 MB）**，会把主应用体积推高一个量级。
- 实测：`ncnn/__init__.py` 中**没有任何 `cv2` 引用**；完成一次完整推理后 `'cv2' in sys.modules == False`。
- **结论**：主应用集成时可 `pip install ncnn --no-deps`（只保留 numpy），**依赖代价从 ~130 MB 降到 ~14 MB**（`ncnn.cp313-win_amd64.pyd` 单文件 14 MB）。
- ⚠️ 该结论只覆盖本脚本用到的 API（`Net` / `Mat` / `Extractor`）；若 T-806 要用 `ncnn` 的高层图像工具（如 `Mat.from_pixels` 的某些重载），需重新确认。

### 3.4 🟡 同一模型存在"双格式权重"，模型元信息需要能区分

ncnn 的 `realesrgan-x4plus.bin`（32 MB, fp16）与 `data/models/RealESRGAN_x4.onnx`（64 MB, fp32）是**同一网络的两份权重**（PSNR 63.4 dB 印证）。用户同时导入两者时，模型库需要能表达"同源不同格式、精度档不同"，否则会被误读为两个不同模型。

---

## 4. 对产品与文档的落点

| 落点 | 变更 |
|---|---|
| **PRD §7.5 格式路由表** | `.bin` 行的"ncnn 为条件支持 / 验证通过前不计入 v1 承诺" → 改为 **ncnn 后端已验证可用**；"条件"重新定义为**模型层覆盖需运行时校验** |
| **PRD §7.2 风险行** | "ncnn 后端可用性未验证" 风险 **关闭**，降级为"层覆盖按模型评估" |
| **ADR-003** | ncnn 从"条件支持待验证"升级为**已验证后端**；保留"加载失败即剔除、不影响其它格式"的降级原则 |
| **tech-arch §7 风险 1** | **关闭**（结论回填本文件）；风险 2（黑图/Vulkan 不可用）降级为**运行时探测项**——已证明本机不触发，但仍需在其它机器上按"加载失败即降级"处理 |
| **`T-806` 模型加载器** | ncnn 分支可正式实现：`.param` + `.bin` 成对加载；**不需要** pnnx 转换工具；须内建 §3.1 的两个防御 |
| **依赖清单** | 主应用若集成 ncnn：`ncnn --no-deps` + numpy（见 §3.3） |

---

## 5. 边界与遗留（诚实声明）

**已覆盖**

- ncnn 1.0.20260526 / Windows 11 / Python 3.13 / NVIDIA Vulkan
- RealESRGAN 系两个模型：`realesrgan-x4plus`（999 层，跑完整验证）、`realesr-animevideov3-x4`（41 层，跑通）

**未覆盖（不得据此下结论）**

- ❌ **非 RealESRGAN 的 ncnn 模型**：层覆盖未评估（这是"条件支持"保留的真实含义）
- ❌ **无 Vulkan 的 Windows**（老显卡/无驱动）：只验证了"有 Vulkan 时可用"，未验证"无 Vulkan 时是否干净回退到 CPU"——`net.opt.use_vulkan_compute = True` 在无 Vulkan 设备时的行为需另测
- ❌ **无 AVX2 的老 CPU**（ncnn CPU 路径的性能与正确性）
- ❌ **Intel 核显 / AMD 卡**（Vulkan 通用性；本机只有 NVIDIA）
- ❌ **tile 尺寸 × 显存的矩阵**（属 T-804/T-806 范畴，本任务只验"可用性"）
- ❌ **并发/多实例**（ncnn 的 GPU 实例是进程级单例，多任务并发行为未测）

**已知限制**

- `.venvs/sr-ncnn` 是**验证环境**，含 onnxruntime（仅作对照用），**不是产品环境**。
- ncnn 的 GPU 实例为进程级单例，对"单 worker + 任务队列"的架构天然吻合，但需在 T-806 明确"GPU 与 CPU 互斥使用"的串行化假设。

---

## 6. 复现步骤

```bash
# 1) 新建隔离环境（项目内，严禁全局）
uv venv .venvs/sr-ncnn --python 3.13
uv pip install --python .venvs/sr-ncnn/Scripts/python.exe ncnn numpy onnxruntime

# 2) 取 ncnn 格式模型（含降级路径用的 exe）
curl -L -o .workbuddy/verify/t210/ncnn_pkg.zip \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip
# 解压到 .workbuddy/verify/t210/ncnn_pkg/

# 3) 跑验证（务必 -u，否则 C++ 层崩溃时 Python 缓冲输出会丢失）
.venvs/sr-ncnn/Scripts/python.exe -u tools/verify_ncnn_windows.py \
  --json .workbuddy/verify/t210/ncnn_verify_report.json

# 4) 降级路径
cd .workbuddy/verify/t210/ncnn_pkg && ./realesrgan-ncnn-vulkan.exe \
  -i input.jpg -o ../exe_out_x4.png -n realesrgan-x4plus -s 4 -f png
```

**证据文件**

| 文件 | 内容 |
|---|---|
| `.workbuddy/verify/t210/ncnn_verify_report.json` | 64×64 完整报告（14 项断言 + 全部指标） |
| `.workbuddy/verify/t210/ncnn_verify_report_128.json` | 128×128 报告（CPU/Vulkan 收益对比） |
| `.workbuddy/verify/t210/exe_out_x4.png` | 降级路径输出（880×880） |

> 模型包（45 MB zip / 55 MB 解压产物）**不入库**，见 `.gitignore`；按 §6 步骤可完整重建。
