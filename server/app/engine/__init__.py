"""M2 推理引擎 + M4 图像处理。

- `runtime_env.py`   —— Windows DLL 路径注册（启动序列第 1 步，T-603）
- `device_probe.py`  —— **阶段 A** 能力探测：`DeviceFacts`（T-803，永不抛异常）
- `ep_verify.py`     —— **阶段 B** EP 真实性验证：profile 节点归属（T-803）
- `backend_cache.py` —— EP 验证结果缓存（`data/calibration/`，硬件指纹失效，T-803）
- `capabilities.py`  —— 能力快照编排 + 档位判定（§6.2，T-803）
- `availability.py`  —— 模型可用性门控（T-604，数据源已换为阶段 A 的 facts）
- `fallback.py`      —— **保底档策略**（§6.8）与决策参数基线（T-804，常量单一事实源）
- `runtime_profile.py` —— **阶段 D** 运行时决策：`RuntimeProfile` + 因果性防御（T-804）
- `watermark.py`     —— **阶段 E** 水位反馈 / OOM 降档链 / 水位追踪器（T-804）
- `image_ops.py`     —— **M4** 预处理 / 后处理（T-802）
- `tiling.py`        —— **M4** 分块计划 + overlap/feather 拼接（T-802，质量红线）
- `runtimes.py`      —— 可选运行时可用性探测（不 import 重库；T-806）
- `model_introspect.py` —— 读 ONNX 输入约束（`align` / `fixed_tile`；T-806）
- `model_loader.py`  —— **加载器与格式路由**：ORT / OpenVINO / ncnn 三后端（T-806）
- `pipeline.py`      —— **M2 推理编排**：预处理 → 分块 → 逐块推理 → 拼接 → 落盘（T-806）

依赖方向（tech-arch §2.2）：**M2 → M4 单向**。M4 是无状态纯算法库：不持有会话、
不编排推理循环、不感知 EP / 档位 / 进度，一切参数由 M2 传入。
阶段 A/B/D/E 与加载器、编排同样不依赖应用层（无 fastapi / sqlalchemy / pydantic），
因此可在任意 Python 环境（如 `.venvs/sr-gpu`、`.venvs/sr-ov`、`.venvs/sr-ncnn`）里
独立复核真机结论；"标定记录从哪来、水位历史存哪、产物落哪个目录"这类问题由
`services/engine_decision.py` 与 `tasks/executor.py` 回答。
"""
