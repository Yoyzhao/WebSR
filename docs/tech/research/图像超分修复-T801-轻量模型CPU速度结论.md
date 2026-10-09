# T-801 结论：轻量超分模型在纯 CPU 上的真实速度

> **任务**：`T-801`（原 M2 组，编号保留；在 M3 时间窗内以 `T-306` 名义执行）
> **性质**：**结论任务**，不是代码任务。产出回答一个问题——**`T0`（纯 CPU）档的产品承诺是否成立**。
> **日期**：2026-10-09 ｜ **硬件**：`i5-12400F`（12th Gen Intel，6P+0E，无核显）+ 32 GB RAM

---

## 1. 结论（先给答案）

**`T0` 档可用，且余量充足——比 PRD §2.3 写的"能用 / 预览"更好。**

| 场景 | 轻量模型实测 | 对照：`RealESRGAN_x4plus` | 加速 |
|---|---|---|---|
| **1080p 全图 ×4** | **5.13 s** | **272.6 s**（4.5 分钟） | **53×** |
| 720p 全图 ×4 | 1.87 s | —（未测，按吞吐外推约 74 s） | — |
| 单块 `tile=256` | **129 ms** | 4 894 ms | 38× |
| 1080p 峰值内存 | **687 MB** | 3 894 MB | 5.7× 更省 |

**三条推论**：

1. **`T0` 档必须默认走轻量模型**——`RealESRGAN_x4plus` 在纯 CPU 上处理一张 1080p 要 **4.5 分钟**，
   不构成可接受的交互体验；轻量模型 5 秒，两者不是同一个量级的选项。这与 PRD §2.3
   已写明的"`T0` 主模型 = 轻量（SPAN/SAFMN）"一致，**本任务为它补上了量化依据**。
2. **不需要新增 `min_ram_mb` 字段**（`T-306` 提出的待决问题）：轻量模型 1080p 峰值仅 **687 MB**，
   对任何能跑起 Windows + 浏览器的 `T0` 机器都不是门槛。`MODEL.min_vram_mb` 之外的 CPU 档元信息
   **不需要扩字段**。
3. **`T0` 的"输入长边上限（按内存）"不是紧约束**：按 687 MB / 2.07 MP 的比例，
   16 GB 内存的机器在内存维度上可以处理到 20 MP 以上；真正的限制是**耗时**（预览优先）而非内存。

---

## 2. 测量方法与口径（与既有 P0 基线可比）

工具：`tools/bench_t801_light_models.py`（开发期资产，**产品不得 import**）。
原始数据：`.workbuddy/results/t801_light_cpu.json`（720p）、`t801_light_cpu_1080p.json`（1080p）。

| 口径项 | 取值 | 理由 |
|---|---|---|
| 推理后端 | **OpenVINO 原生 API**（`openvino 2026.4.0`） | `project-rules.md` §3；ORT + OpenVINO EP 实测**慢 2.5 倍** |
| 精度 | **fp32** | 桌面 CPU 无 fp16 加速单元；P0 实测 fp16 反而更慢（4 894 → 5 936 ms） |
| 输入图 | **确定性合成图**（`tools/_bench_common.py::make_test_image`） | 与 P0 基线同源，可复现 |
| 内存 | **进程峰值 RSS**（`RssSampler`） | `T0` 档瓶颈是**物理内存**不是显存 |
| 分块 | `tile=256`（SAFMN 静态 256）／`256±512`（SPAN 动态） | 见 §4 的静态输入约束 |
| 运行环境 | `.venvs/sr-ov`（openvino 2026.4.0） | 基准环境，**不得污染** |

---

## 3. 被测模型（含来源与哈希）

| 模型 | 架构 | 参数量 | 来源 | 许可 |
|---|---|---|---|---|
| `SAFMN_DF2K_x4.pth` → `safmn_x4_s256.onnx` | `SAFMN` ×4 | **239,520** | 作者官方发布 `huggingface.co/Meloo/SAFMN` | Apache-2.0 |
| `2xHFA2k_LUDVAE_SPAN.safetensors` → `span_x2.onnx` | `SPAN` ×2 | **410,700**（**推理态**，见 §4.2） | `huggingface.co/Phips/2xHFA2k_LUDVAE_SPAN`（社区权重） | CC-BY-4.0 |

`SAFMN_DF2K_x4.pth` sha256 `fe4f38fd…`（1 004 953 B）
`2xHFA2k_LUDVAE_SPAN.safetensors` sha256 `457bca87…`（4 461 056 B）

⚠️ **诚实边界（SPAN 一侧）**：**官方 411K 版 SPAN 权重未能取到**——`hongyuanyu/SPAN`
仓库不含权重文件、无 GitHub Release，作者把权重放在 Google Drive（不可稳定脚本化）。
因此 SPAN 一侧用的是**社区权重**；但见 §4.2，其**被执行的子图**正是官方规模的 SPAN
（410,700 参数 ≈ 官方 411K，48 通道 / 6 blocks），**故本节结论对官方轻量版 SPAN 成立**。

---

## 4. 三个实测发现（都是"不实测就不会知道"的）

### 4.1 SAFMN 在 `dynamo=False`（TorchScript）下**无法**导出动态输入

```
Unsupported: ONNX export of operator adaptive pooling, since output_size is not constant.
```

原因：SAFMN 的**空间自适应调制**要对 `H/multiple_of` 做全局池化，池化窗口尺寸依赖输入
尺寸 → TorchScript 导出器无法把它变成常量。

**可行路径（已验证）**：`--static 256` 导出**静态输入**模型，自检 `max|diff| = 3.78e-05`。
→ 产品侧正好有对应通道：`decide_profile(fixed_tile=N)`（`T-806` 建立，语义是
"输入必须**正好等于** N"，不是倍数关系）。**SAFMN 类模型走 `fixed_tile`，不走 `align`。**

实测 `size_requirements = { minimum: 0, multiple_of: 8, square: false }`
——⚠️ **调研笔记里写的"必须是 16 的倍数"在 `SAFMN`（非 `SAFMN BCIE`）上实测是 `8`**。
该值由 spandrel 从权重本身读出（不是猜的），可直接作为 `DEFAULT_ALIGN` 的收紧输入。

### 4.2 SPAN 是**结构重参数化**网络（RepVGG 式），checkpoint 参数量不能当计算量

| 参数用途 | 参数量 |
|---|---|
| 训练态多分支（`block_N.cN_r.conv.{1..n}`） | 1,765,560 |
| **推理态融合单路**（`block_N.cN_r.eval_conv`） | **396,240** |
| 其它（`conv_1` / `conv_cat` / `conv_2` / `upsampler` 等） | 59,340 |
| **checkpoint 合计** | **2,221,140** |
| **导出 ONNX 图中真正使用的** | **410,700** |

- spandrel 报的 `param_count = 2,221,140` 是**checkpoint 的超集**；
  实测导出图的 44 个 initializer 合计 **410,700**——**这才是被执行的网络**。
- ⚠️ **推论（产品侧）**：任何"按 checkpoint 参数量估算速度"的做法在本项目里**不可靠**
  （SPAN 上会高估 5.4 倍）。**速度只能实测**，与 `T-805` 标定得出的"最优 tile 必须实测"同源。
- ⚠️ **必须显式 `eval()`**：重参数化网络在 train 模式下会走多分支路径（慢 5×），
  含 BN 时还会用 batch 统计导致**输出错误**。`tools/convert_to_onnx.py` 在导出前有
  显式 `net.eval()`（第 227 行），本项已防住；**但这是一条必须保持的不变量**。

### 4.3 自检机制在**第一个第三方模型上就拦下了一次偏差**

`4xNomosUni_span_multijpg.safetensors`（SPAN ×4）导出后自检
`max|torch − onnx| = 4.193e-03 > 1.0e-03` → **工具拒绝落盘**（退出码 5，产物已删除）。

- 对照：`RRDBNet` 1.55e-06、`SRVGGNetCompact` 4.08e-06、`SAFMN` 3.78e-05、
  `SPAN ×2` 3.76e-04 —— SPAN 系明显偏大，**且随倍数放大**（×2 通过、×4 超出）。
- 推测原因与 §4.1 同源：SPAN/SAFMN 的**全局池化**在 `dynamo=False` 下被换成
  `Slice` 近似（报错信息里可见 `onnx::Slice` 替代 `adaptive_avg_pool2d`），
  近似误差随分辨率/倍数放大。
- **这是 `T-807` 那条纪律的实证收益**："**能加载但算错**的模型比加载失败更糟"——
  若没有自检，这个 4.2e-03 的偏差会**静默进入产品**并在 ×4 输出上被放大四倍。
- **待办（不阻塞本任务）**：要正式支持 SPAN ×4 族，需要评估
  ① `dynamo=True`（需 `onnxscript` 依赖）是否消除该偏差，或
  ② 按模型族给出分级容差。**本任务只记录，不改容差**（改容差=掩盖问题）。

---

## 5. 完整实测数据

### 5.1 单块延迟（fp32 / CPU / OpenVINO 原生）

| 模型 | `tile=256` mean | p95 | 输出 | 输出吞吐 | RSS 增量 |
|---|---|---|---|---|---|
| `SAFMN ×4` | **129.2 ms** | 138.0 ms | 1.05 MP | 8.12 MP/s | +12 MB |
| `SPAN ×2` | **115.9 ms** | 125.3 ms | 0.26 MP | 2.26 MP/s | +117 MB |
| `RealESRGAN_x4plus` | 4 893.9 ms | — | 1.05 MP | 0.21 MP/s | +836 MB |

> `SAFMN ×4` 与 `RealESRGAN_x4plus` 同为 ×4、同 tile，**38× 差距**，可直接对比。
> `SPAN ×2` 输出像素只有 1/4，按**输入吞吐**对比更公平（见 §5.3）。

### 5.2 全图端到端（`tile=256`）

| 模型 | 输入 | 输出 | 耗时 | 输入吞吐 | 峰值 RSS |
|---|---|---|---|---|---|
| `SAFMN ×4` | 1280×720 | 5120×2880 | **1.87 s** | 0.49 MP/s | 451 MB |
| `SPAN ×2` | 1280×720 | 2560×1440 | **1.79 s** | 0.52 MP/s | 727 MB |
| `SAFMN ×4` | 1920×1080 | 7680×4320 | **5.13 s** | 0.40 MP/s | 687 MB |
| `SPAN ×2` | 1920×1080 | 3840×2160 | **6.43 s** | 0.32 MP/s | 407 MB |
| `RealESRGAN_x4plus`（`tile=512`） | 1920×1080 | 7680×4320 | **272.6 s** | 0.0076 MP/s | 3 894 MB |

### 5.3 加速倍数（以 1080p 输入吞吐为口径）

| 模型 | 输入吞吐 | vs `RealESRGAN_x4plus` |
|---|---|---|
| `SAFMN ×4` | 0.404 MP/s | **53.1×** |
| `SPAN ×2` | 0.323 MP/s | **42.4×** |

**与前置调研的预测吻合**：`docs/tech/research/图像超分修复-实施前方案调研.md` 预测
"理论上比 x4plus 快 **40~70 倍**"——实测落在区间内。**预测被实测确认，不是反例。**

---

## 6. 对产品的影响（落到具体条款）

| # | 影响位置 | 结论 |
|---|---|---|
| 1 | **PRD §7.2 风险表** | "CPU 档能否实用未确定"→ **关闭**（已有量化结论） |
| 2 | **PRD §2.3 `T0` 行** | "主模型 = 轻量（SPAN/SAFMN）"得到量化支撑；**"用户可感知预期 = 能用 / 预览"的实际能力更强**——是否上调措辞属**产品承诺变更，留给用户裁决**（本任务只提供数据，不擅自改承诺） |
| 3 | **`MODEL` 表** | **不需要** `min_ram_mb`（687 MB 峰值不构成门槛） |
| 4 | **模型加载/决策（`T-806`）** | SAFMN 类必须走 `fixed_tile`（静态输入"正好等于 N"）；`multiple_of=8` 可作为 `DEFAULT_ALIGN` 的收紧输入 |
| 5 | **离线转换（`T-807`）** | SAFMN 需 `--static N`；SPAN 族注意 `eval()` 不变量的保持 |
| 6 | **`T0` 档模型清单** | 需准备**轻量模型**作为 `T0` 的默认/唯一实用选项（当前 `data/models/` 只有 `RealESRGAN` 系，无轻量模型） |

---

## 7. 未验证范围（诚实边界）

- **只有一台机器**：`i5-12400F`（2021 中端桌面，6P 核）。**低功耗笔记本 CPU 可能慢 2–4 倍**
  → 1080p 约 10–25 s，仍是"能用 / 预览"级别，但**未实测**。
- **只测了两个轻量模型**（`SAFMN ×4` / `SPAN ×2`）；同族的 `SAFMN BCIE`、`PLKSR`、
  `IMDN`、`ShuffleMixer` 未测。
- **只测了 fp32**（正确口径）；未测 OpenVINO 的 `bf16`/`INT8` 量化路径（`T0` 承诺不依赖它）。
- **未做人眼质量复核**：本任务只回答"**多快**"，**不回答"多好"**。轻量模型的画质
  （调研给出的 `PSNR ≈ 32.2 dB @Set5 ×4`，与 `IMDN` 同级）**未在本项目内复核**。
- **未测真实照片上的分块接缝**：`_bench_common.tiled_infer` 无羽化，本任务测的是**耗时**，
  不构成画质证据（质量红线由 `T-802`/`F-11` 覆盖）。
- **`SPAN ×4` 未纳入结论**：其 ONNX 被自检拦下（§4.3），故只有 ×2 的数据。

---

## 8. 复现方式

```bash
# 1) 取权重（走代理；仓库内不入库，见 .gitignore）
curl -sL --proxy socks5h://127.0.0.1:10088 \
  -o .workbuddy/verify/t801/SAFMN_DF2K_x4.pth \
  https://huggingface.co/Meloo/SAFMN/resolve/main/SAFMN_DF2K_x4.pth
curl -sL --proxy socks5h://127.0.0.1:10088 \
  -o .workbuddy/verify/t801/2xHFA2k_LUDVAE_SPAN.safetensors \
  https://huggingface.co/Phips/2xHFA2k_LUDVAE_SPAN/resolve/main/2xHFA2k_LUDVAE_SPAN.safetensors

# 2) 转 ONNX（SAFMN 必须静态导出，见 §4.1）
.venvs/sr-convert/Scripts/python.exe tools/convert_to_onnx.py \
  .workbuddy/verify/t801/SAFMN_DF2K_x4.pth -o .workbuddy/verify/t801/safmn_x4_s256.onnx --static 256
.venvs/sr-convert/Scripts/python.exe tools/convert_to_onnx.py \
  .workbuddy/verify/t801/2xHFA2k_LUDVAE_SPAN.safetensors -o .workbuddy/verify/t801/span_x2.onnx

# 3) 纯 CPU 实测（OpenVINO 原生 API，fp32）
.venvs/sr-ov/Scripts/python.exe tools/bench_t801_light_models.py \
  --model SAFMN_x4=.workbuddy/verify/t801/safmn_x4_s256.onnx \
  --model SPAN_x2=.workbuddy/verify/t801/span_x2.onnx \
  --ir-dir .workbuddy/verify/t801/ir \
  --image 1920x1080 --tiles 256 \
  --json .workbuddy/results/t801_light_cpu_1080p.json
```
