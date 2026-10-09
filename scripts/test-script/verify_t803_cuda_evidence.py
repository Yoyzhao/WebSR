"""T-803 真实 CUDA EP 取证（需要 GPU 版 ORT：`.venvs/sr-gpu`）。

运行：
    ./.venvs/sr-gpu/Scripts/python.exe scripts/test-script/verify_t803_cuda_evidence.py

为什么单独一个脚本：主验证脚本 `verify_t803_engine.py` 跑在应用环境（`.venvs/sr-app`，
CPU 版 ORT），它覆盖**判定分支**与**真实 CPU EP**；而"这台机器的 CUDA 后端到底有没有
真的接管计算"必须在**装着 GPU 版 ORT 的环境**里实测——这正是 tech-arch §6.1 阶段 B
存在的意义。产物存 `.workbuddy/verify/t803/`（不入库为产品代码）。

判据（不可简化）：目标 EP 节点数 > 0 **且** CPU 节点数 = 0。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

OUT_DIR = ROOT / ".workbuddy" / "verify" / "t803"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 必须在 import onnxruntime **之前**注册 DLL 路径（tech-arch §6.6 第 1 步）
from app.engine.runtime_env import prepare_dll_paths  # noqa: E402

dll = prepare_dll_paths()
print(f"DLL 候选目录：{len(dll['added'])} 个，失败 {len(dll['failed'])} 个")

from app.engine.device_probe import probe_device_facts  # noqa: E402
from app.engine.ep_verify import verify_candidates  # noqa: E402
from app.engine.capabilities import build_snapshot  # noqa: E402

MODEL = ROOT / "data" / "models" / "RealESRGAN_x4.onnx"
PROBE_SIZE = 256

facts = probe_device_facts()
print(f"阶段 A：CPU={facts.cpu.name[:48]} | ORT={facts.ort_version} | 可用显存={facts.available_vram_mb} MB")
print(f"        可用提供器：{facts.ort_available_providers}")

res = verify_candidates(facts, str(MODEL), probe_size=PROBE_SIZE, measure_latency=True)

print("\n--- 阶段 B 逐后端判定 ---")
for v in res.verdicts:
    print(f"  {v.provider:26s} usable={str(v.usable):5s} adopted={str(v.adopted):5s} "
          f"nodes={v.node_count:5d} cpu_nodes={v.cpu_node_count:5d} "
          f"latency={v.latency_ms} reason={v.reason_code}")
print(f"采用：{res.adopted}")
if res.exception_events:
    print(f"异常事件：{res.exception_events}")

# 完整能力快照（temp 数据根：不污染应用自己的校准缓存）
import tempfile  # noqa: E402

tmp_root = Path(tempfile.mkdtemp(prefix="websr_t803_cuda_"))
caps, details = build_snapshot(tmp_root, probe_model=MODEL, refresh=True, probe_size=PROBE_SIZE)
print(f"\n档位判定：{caps['tier']} —— {caps['tier_reason']}")
print(f"生效后端：{caps['active_backend']} / 精度 {caps['active_precision']} / 保底档 {caps['using_fallback']}")

evidence = {
    "env": "sr-gpu",
    "model": str(MODEL.relative_to(ROOT)),
    "probe_size": PROBE_SIZE,
    "dll_added": dll["added"],
    "device_facts": facts.to_dict(),
    "verdicts": [
        {"provider": v.provider, "usable": v.usable, "adopted": v.adopted,
         "reason_code": v.reason_code, "reason": v.reason,
         "node_assignment": v.node_assignment,
         "node_count": v.node_count, "cpu_node_count": v.cpu_node_count,
         "latency_ms": v.latency_ms, "note": v.note}
        for v in res.verdicts
    ],
    "adopted": res.adopted,
    "exception_events": res.exception_events,
    "tier": caps["tier"],
    "tier_reason": caps["tier_reason"],
    "capabilities": caps,
}
out = OUT_DIR / "cuda_evidence.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n证据已写入：{out}")

cuda = next((v for v in res.verdicts if v.provider == "CUDAExecutionProvider"), None)
if cuda and cuda.adopted and cuda.cpu_node_count == 0:
    print("\n结论：✅ CUDA EP 在真实 GPU 上被证实接管计算（CPU 节点 0 个）")
    raise SystemExit(0)
print("\n结论：❌ CUDA EP 未通过验证（详见上方判定与证据文件）")
raise SystemExit(1)
