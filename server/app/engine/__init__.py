"""M2 推理引擎 + M4 图像处理。

- `runtime_env.py`   —— Windows DLL 路径注册（启动序列第 1 步，T-603）
- `device_probe.py`  —— **阶段 A** 能力探测：`DeviceFacts`（T-803，永不抛异常）
- `ep_verify.py`     —— **阶段 B** EP 真实性验证：profile 节点归属（T-803）
- `backend_cache.py` —— EP 验证结果缓存（`data/calibration/`，硬件指纹失效，T-803）
- `capabilities.py`  —— 能力快照编排 + 档位判定（§6.2，T-803）
- `availability.py`  —— 模型可用性门控（T-604，数据源已换为阶段 A 的 facts）
- `image_ops.py`     —— **M4** 预处理 / 后处理（T-802）
- `tiling.py`        —— **M4** 分块计划 + overlap/feather 拼接（T-802，质量红线）

依赖方向（tech-arch §2.2）：**M2 → M4 单向**。M4 是无状态纯算法库：不持有会话、
不编排推理循环、不感知 EP / 档位 / 进度，一切参数由 M2 传入。
阶段 A/B 同样不依赖应用层（无 fastapi / sqlalchemy / pydantic），
因此可在任意 Python 环境（如 `.venvs/sr-gpu`）里独立复核真机结论。
"""
