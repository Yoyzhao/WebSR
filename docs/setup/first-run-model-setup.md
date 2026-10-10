# 首次运行 · 模型补齐指南

> 2026-10-11 起，`data/` 目录（模型权重与运行期数据）**不再随 git 仓库分发**。
> 本文说明克隆后如何把模型补齐，让应用进入可用状态。

## 为什么模型不入库

| 理由 | 说明 |
|---|---|
| 体积 | 内置 ONNX + OpenVINO IR 合计约 **342 MB**，而项目源码仅约 2 MB（历史中混入的工具链权重另有 387 MB） |
| 可重建 | 全部模型均可通过应用内「模型库 → 下载」按需获取，无需克隆即下载 |
| 与硬件相关 | 不同用户可选的模型不同（轻量机型不会用 64 MB 的重型模型），不必全量分发 |

代价：**克隆后应用没有内置模型，需要先补齐至少一个模型才能执行任务。**

## 方式一：应用内下载（推荐）

1. 启动前后端（见 `README.md` 或项目根目录的 `start-dev.bat`）；
2. 打开 http://127.0.0.1:5173/ → 「模型库」页；
3. 下载目录共 10 个条目，**均已实测可转换可运行**，按倍率与用途选择：

| 倍率 | 模型 | 适用场景 |
|---|---|---|
| ×4 | `4xNomosWebPhoto_esrgan` | 通用照片放大（画质均衡） |
| ×4 | `4xNomos2_hq_mosr` | 通用照片放大（重型高质量，约 17 MB） |
| ×2 | `2xNomosUni_esrgan_multijpg` | 通用放大（重型抗压缩） |
| ×2 | `2xNomosUni_compact_otf_medium` | 轻量三合一（约 2.3 MB，低配推荐） |
| ×2 | `2x-AnimeSharpV3` | 动漫/插画 |
| ×2 | `2xHFA2kAVCCompact` | 动漫视频 h264 压缩痕 |
| ×2 | `2x_Ani4K_Compact` | 动漫通用 |
| **×1** | `1xDeNoise_realplksr_otf` | **修复**：去噪点（夜景高感光度、老照片颗粒） |
| **×1** | `1xDeJPG_realplksr_otf` | **修复**：去静态 JPEG 压缩痕（反复保存/转发的图） |
| **×1** | `1xDeH264_realplksr` | **修复**：去视频编码痕（录屏、视频/直播截图） |

> 下载 `.pth` 后应用会自动转成 ONNX；转换需独立的 `sr-convert` 环境（带 PyTorch），
> 未安装时该模型会停在「待转换」态，模型卡上会出现「转换为 ONNX」按钮 ——
> 该按钮只在装了转换环境时显示。

### ×1 = 修复，不是放大

三��� ×1 模型**输出尺寸与输入完全相同**，只修复画质。UI 中该倍率标注为「×1 修复」。

## 方式二：手工拷贝

若你已有本地模型目录，直接放到 `data/models/`（或 `data/models/imported/`）：

```bash
# 内置模型位置
cp /path/to/RealESRGAN_x4.onnx data/models/

# 用户导入模型位置（应用内「导入」按钮登记的位置）
cp /path/to/my_model.onnx data/models/imported/
```

支持格式：**`.onnx`**（推荐，推理引擎直接加载）。
`.pth` / `.safetensors` 为训练权重格式，需先转换为 ONNX（见 `.workbuddy/memory/2026-10-11.md` 中 T-712 记录）。

## 附：历史内置模型（不再入库）

以下模型曾在 `data/models/` 随仓库分发，现已移除。若需要可从
[Real-ESRGAN release](https://github.com/xinntao/Real-ESRGAN/releases) 获取源权重后自行转换：

- `RealESRGAN_x4.onnx`（63.9 MB）/ `_s512` / `_fp16` / `_fp16all`
- `RealESRGAN_x2plus.onnx`（64 MB）
- `RealESRGAN_x4plus_anime_6B.onnx`（17.1 MB）
- `realesr-general-x4v3.onnx`（4.6 MB，SRVGGNetCompact，轻量）
- `RealCUGAN_up3x.onnx`（4.9 MB，动漫 ×3）
