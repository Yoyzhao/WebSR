"""T-804 引擎阶段 D/E 验证：决策 Profile + 水位反馈与降级 + 保底档。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t804_engine.py

验证策略（为什么不是"跑一遍看着对"）：

- **阶段 D 的决策分支**（标定命中 / 指纹失效 / 用户指定与后端冲突 / CPU+fp16 /
  GPU 无 fp16 单元 / tile 低于下界或未对齐）互相耦合，真机上只会碰到其中一种。
  故用**纯函数 + 构造输入**把每个分支真实走一遍；
- **阶段 E 的水位与降档链**依赖"资源跑满"这种无法按需复现的条件，
  故用**构造水位值**驱动追踪器，用**可控假执行器**走通 OOM 重试；
- 端到端只做两件事：真实任务链路里 `resolved` 是真实决策；OOM 降档重试确实发生。

关键判据（tech-arch §6.8 / §6.1）：
保底档的每个取值必须是**下界**（不取开发机实测值）；降级必须**显式**
（`degraded` + `downgrades` 逐条可查）；`fp16` **不在**降档链上。
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t804_"))
DATA = TMP / "data"
DATA.mkdir(parents=True, exist_ok=True)

os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"

sys.path.insert(0, str(ROOT / "server"))

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


from app.engine import fallback, runtime_profile as rp, watermark  # noqa: E402
from app.engine.device_probe import (  # noqa: E402
    CpuFacts,
    DeviceFacts,
    NvidiaGpuFacts,
)
from app.engine.ep_verify import GPU_PROVIDERS  # noqa: E402

ENGINE_DIR = ROOT / "server" / "app" / "engine"


def facts(*, cc: str | None = "8.6", vram_free: int | None = 7112) -> DeviceFacts:
    """构造硬件事实（**不触发真实探测**，判定是纯函数）。"""
    return DeviceFacts(
        os_name="Windows 11",
        is_wddm=True,
        cpu=CpuFacts("Intel64 Family 6 Model 151", "AMD64", 12, 6, 16384, 8000, []),
        nvidia=[NvidiaGpuFacts("RTX 3050", "566.03", cc, 8192, vram_free)],
        ort_available_providers=["CPUExecutionProvider"],
        ort_version="1.30.0",
    )


CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"


# ---------------------------------------------------------------------------
section("1. 保底档策略（§6.8：每个取值必须是下界，且只有一处定义）")
# ---------------------------------------------------------------------------

pol = fallback.describe_policy()
check("保底精度恒 fp32（不论 GPU/CPU）", pol["precision"] == "fp32", pol["precision"])
check("保底并发恒 1（动态并发属 T-907）", pol["concurrency"] == 1, str(pol["concurrency"]))
check("保底 tile 是**下界**而非开发机实测最优点", 0 < pol["min_tile"] <= 128,
      f"min_tile={pol['min_tile']}")
check("过渡区比例是回归验证过的 1/4（T-802 用的是 256/64、64/16）",
      pol["overlap_ratio"] == "1/4", pol["overlap_ratio"])

fb = fallback.fallback_params([CPU])
check("保底后端取'已验证列表'首项", fb["backend"] == CPU)
fb_empty = fallback.fallback_params([])
check("无已验证后端时回落 cpu 并给出原因",
      fb_empty["backend"] == fallback.FALLBACK_BACKEND and bool(fb_empty["backend_reason"]))

# 源码级：保底档在结构上不可能写入标定表 / 模型元信息（§6.8 规则 2）
src_fb = (ENGINE_DIR / "fallback.py").read_text(encoding="utf-8")
check("fallback.py 不 import 模型层 / 数据库（结构上无法写标定）",
      re.search(r"^\s*(?:import|from)\s+(?:sqlalchemy|\.\.models|\.db|\.services)\b",
                src_fb, re.M) is None and ".commit(" not in src_fb)
check("fallback.py 不含设备型号字面量（ADR-004 原则 2）",
      not re.search(r"4090|3050|RTX\s|GTX\s|GeForce|Tesla", src_fb))

# 对齐与过渡区：tile / overlap 必须落在模型对齐倍数上
check("align_up(70, 8) == 72", fallback.align_up(70, 8) == 72)
check("align_up 对 align<=1 是恒等", fallback.align_up(70, 1) == 70)
check("overlap_for(512, 8) == 128（= tile/4）", fallback.overlap_for(512, 8) == 128,
      str(fallback.overlap_for(512, 8)))
check("overlap_for 不小于一个对齐单位", fallback.overlap_for(64, 8) == 16,
      str(fallback.overlap_for(64, 8)))
check("overlap 恒 < tile（否则 stride 退化为 0，T-802 已断言拒绝）",
      all(fallback.overlap_for(t, 8) < t for t in (64, 128, 256, 512, 1024)))


# ---------------------------------------------------------------------------
section("2. 阶段 D 基线：未标定 → 保底档")
# ---------------------------------------------------------------------------

p = rp.decide_profile(facts=facts(), adopted=[CPU])
r = p.to_resolved()
check("using_fallback=True（阶段 C 未落地）", r["using_fallback"] is True)
check("source=fallback", r["source"] == "fallback", r["source"])
check("precision=fp32", r["precision"] == "fp32")
check("tile 已被抬到下界并对齐", r["tile"] == fallback.align_up(fallback.FALLBACK_MIN_TILE, 8))
check("backend 取已 adopted 首项", r["backend"] == CPU)
check("overlap == feather_px（T-802 交接契约推荐值）",
      r["overlap"] == r["feather_px"] == fallback.overlap_for(r["tile"], 8))
check("concurrency=1", r["concurrency"] == 1)
check("reasons 非空且可解释", bool(r["reasons"]) and all(isinstance(x, str) for x in r["reasons"]))
check("契约字段齐全（含 downgrades）",
      {"tile", "precision", "backend", "using_fallback", "degraded", "reasons", "downgrades"}
      <= set(r))
check("新增字段齐全（M2 编排需要 overlap/feather）",
      {"overlap", "feather_px", "concurrency", "source"} <= set(r))
check("无降档 → degraded=False", r["degraded"] is False and r["downgrades"] == [])

check("自动档理由写明'模型始终由用户选择'",
      any("模型始终由用户选择" in x for x in r["reasons"]), str(r["reasons"]))

# 决策永不抛异常
try:
    weird = [
        dict(facts=None, adopted=[]),
        dict(facts=None, adopted=[], align=0),
        dict(facts=facts(), adopted=[], request=rp.RuntimeRequest(auto=False, tile="abc")),
        dict(facts=facts(), adopted=[CPU], request=rp.RuntimeRequest(auto=True, tile=-5)),
    ]
    ok = True
    for kw in weird:
        rp.decide_profile(**kw)
except Exception as exc:  # noqa: BLE001
    ok = False
    print(f"    {type(exc).__name__}: {exc}")
check("异常/荒唐输入下决策永不抛异常（退化为最保守）", ok)


# ---------------------------------------------------------------------------
section("3. 因果性防御：与'来源'无关，一律执行并记录 downgrade")
# ---------------------------------------------------------------------------

# 3.1 用户指定 fp16 + CPU 后端 → 必须降 fp32
p = rp.decide_profile(
    facts=facts(), adopted=[CPU],
    request=rp.RuntimeRequest(auto=False, precision="fp16", backend=CPU, tile=256),
)
r = p.to_resolved()
check("CPU + fp16 → 降为 fp32", r["precision"] == "fp32", r["precision"])
check("CPU + fp16 记入 downgrades 且 degraded=True",
      r["degraded"] is True and any(d["field"] == "precision" for d in r["downgrades"]),
      str(r["downgrades"]))
check("该降档理由写明'CPU 路径禁止 fp16'",
      any("CPU 路径禁止 fp16" in d["reason"] for d in r["downgrades"]))
check("用户指定的合法 tile 被尊重（256 保留）", r["tile"] == 256, str(r["tile"]))

# 3.2 用户指定未通过验证的后端 → 回落到 adopted 首项
p = rp.decide_profile(
    facts=facts(), adopted=[CPU],
    request=rp.RuntimeRequest(auto=False, backend=CUDA),
)
r = p.to_resolved()
check("未验证后端 → 回落到 verified 首项", r["backend"] == CPU, r["backend"])
check("后端回落记入 downgrades",
      any(d["field"] == "backend" and d["from"] == CUDA for d in r["downgrades"]),
      str(r["downgrades"]))
check("后端回落理由写明'未通过 EP 真实性验证'",
      any("未通过 EP 真实性验证" in d["reason"] for d in r["downgrades"]))

# 3.3 tile 下界与对齐
p = rp.decide_profile(facts=facts(), adopted=[CPU], request=rp.RuntimeRequest(auto=False, tile=100))
check("tile=100 → 抬到 104（下界 64 之上但需对齐 8）", p.tile == 104, str(p.tile))
p = rp.decide_profile(facts=facts(), adopted=[CPU], request=rp.RuntimeRequest(auto=False, tile=8))
check("tile=8（低于下界）→ 抬到 64", p.tile == fallback.FALLBACK_MIN_TILE, str(p.tile))
check("tile 被抬升时记入 downgrades",
      any(d["field"] == "tile" for d in p.downgrades), str(p.downgrades))

# 3.4 GPU fp16：按 compute_cap 判定，不按型号
p = rp.decide_profile(
    facts=facts(cc="8.6"), adopted=[CUDA, CPU],
    request=rp.RuntimeRequest(auto=False, precision="fp16", backend=CUDA),
)
check("GPU 且 compute_cap 8.6（有 fp16 单元）→ 保留 fp16", p.precision == "fp16", p.precision)
check("保留 fp16 时不产生该字段的降档",
      not any(d["field"] == "precision" for d in p.downgrades), str(p.downgrades))
p = rp.decide_profile(
    facts=facts(cc="5.0"), adopted=[CUDA, CPU],
    request=rp.RuntimeRequest(auto=False, precision="fp16", backend=CUDA),
)
check("GPU 但 compute_cap 5.0（无 fp16 单元）→ 降 fp32",
      p.precision == "fp32" and any("5.3" in d["reason"] for d in p.downgrades),
      f"{p.precision} {p.downgrades}")
p = rp.decide_profile(
    facts=facts(cc=None), adopted=[CUDA, CPU],
    request=rp.RuntimeRequest(auto=False, precision="fp16", backend=CUDA),
)
check("compute_cap 未知 → 保守降 fp32",
      p.precision == "fp32" and any("无法确认" in d["reason"] for d in p.downgrades),
      f"{p.precision} {p.downgrades}")

# 3.5 未知精度字符串不得静默透传
p = rp.decide_profile(facts=facts(), adopted=[CPU],
                      request=rp.RuntimeRequest(auto=False, precision="bf16"))
check("未知精度 → 落到 fp32 并记录",
      p.precision == "fp32" and any(d["field"] == "precision" for d in p.downgrades))


# ---------------------------------------------------------------------------
section("4. 标定命中 / 失效（消费侧；T-805 只负责产出）")
# ---------------------------------------------------------------------------

fp_ok = "fp-match"
cal = rp.CalibrationView(
    hardware_fingerprint=fp_ok, tile=512, precision="fp32",
    backend=CUDA, concurrency=1, reason="在 CUDA 上 512 块峰值 6.1 GB，留 1 GB 余量",
)
p = rp.decide_profile(facts=facts(), adopted=[CUDA, CPU], fingerprint=fp_ok, calibration=cal)
r = p.to_resolved()
check("标定命中 → source=calibration", r["source"] == "calibration", r["source"])
check("标定命中 → using_fallback=False", r["using_fallback"] is False)
check("采用标定的 tile/backend", (r["tile"], r["backend"]) == (512, CUDA), f"{r['tile']} {r['backend']}")
check("标定理由被带出", any("512 块峰值" in x for x in r["reasons"]), str(r["reasons"]))
check("标定命中不产生降档", r["degraded"] is False, str(r["downgrades"]))

p = rp.decide_profile(facts=facts(), adopted=[CPU], fingerprint="fp-other", calibration=cal)
r = p.to_resolved()
check("指纹不符 → 回落保底档（失效判据是硬件指纹，不是时间）",
      r["source"] == "fallback" and r["using_fallback"] is True, r["source"])
check("指纹不符的理由被写出", any("硬件指纹不符" in x for x in r["reasons"]), str(r["reasons"]))

cal_invalid = rp.CalibrationView(hardware_fingerprint=fp_ok, tile=512, valid=False)
p = rp.decide_profile(facts=facts(), adopted=[CPU], fingerprint=fp_ok, calibration=cal_invalid)
check("标定记录被标为无效 → 回落保底档",
      p.source == "fallback" and p.using_fallback is True, p.source)

# 标定给出的 tile 同样要过因果性防御（防御不信任来源）
cal_bad = rp.CalibrationView(hardware_fingerprint=fp_ok, tile=512, precision="fp16", backend=CPU)
p = rp.decide_profile(facts=facts(), adopted=[CPU], fingerprint=fp_ok, calibration=cal_bad)
check("标定给的 CPU+fp16 同样被拦下（防御不信任来源）",
      p.precision == "fp32" and p.degraded is True, f"{p.precision} {p.downgrades}")
cal_untiled = rp.CalibrationView(hardware_fingerprint=fp_ok, tile=None, precision="fp32", backend=CPU)
p = rp.decide_profile(facts=facts(), adopted=[CPU], fingerprint=fp_ok, calibration=cal_untiled)
check("标定缺 tile → 退回保底下界（保守方向，不猜测）",
      p.tile == fallback.FALLBACK_MIN_TILE and any(d["field"] == "tile" for d in p.downgrades),
      str(p.tile))


# ---------------------------------------------------------------------------
section("5. 无已验证后端：不冒领加速")
# ---------------------------------------------------------------------------

p = rp.decide_profile(facts=facts(), adopted=[])
r = p.to_resolved()
check("adopted=[] → 强制 using_fallback=True", r["using_fallback"] is True)
check("理由写明'未通过 EP 真实性验证'",
      any("EP 真实性验证" in x for x in r["reasons"]), str(r["reasons"]))
check("backend 不回落到某个具体后端（无可信后端）", r["backend"] == "", repr(r["backend"]))
check("GPU 存在但仍 fp32（不拿'检测到显卡'冒充'已优化'）", r["precision"] == "fp32")

# 标定有效但后端全无：档位由标定裁决，缺后端由 degraded 表达（不重复改档位语义）
p = rp.decide_profile(facts=facts(), adopted=[], fingerprint=fp_ok, calibration=cal)
check("标定有效 + 无已验证后端 → 档位仍由标定裁决（using_fallback=False）",
      p.source == "calibration" and p.using_fallback is False, f"{p.source} {p.using_fallback}")
check("但 backend 被置空且 degraded=True（缺后端如实表达，不静默）",
      p.backend == "" and p.degraded is True, f"{p.backend} {p.downgrades}")


# ---------------------------------------------------------------------------
section("6. 阶段 E：水位采样与'连续高位'判定")
# ---------------------------------------------------------------------------

lv = watermark.WaterLevel(vram_used_mb=7200, vram_total_mb=8000, ram_used_mb=4000, ram_total_mb=16000)
check("口径 = max(显存比, 内存比)", abs(lv.ratio - 0.9) < 1e-9, str(lv.ratio))
lv_ram = watermark.WaterLevel(ram_used_mb=15000, ram_total_mb=16000)
check("显存读不到时只按内存算（T0 的瓶颈是内存）",
      abs(lv_ram.ratio - 0.9375) < 1e-9, str(lv_ram.ratio))
check("全部读不到 → None（不是 0）", watermark.WaterLevel().ratio is None)
check("vram_total=0 时不产生除零异常", watermark.WaterLevel(vram_used_mb=0, vram_total_mb=0).ratio is None)

real = watermark.sample_water_level()
check("真实采样不抛异常且口径正确（ratio=None 或 0<=ratio<=1）",
      real.ratio is None or 0.0 <= real.ratio <= 1.0, str(real.to_dict()))

orig_run = watermark.subprocess.run


def _boom(*_a, **_k):
    raise OSError("nvidia-smi 不存在")


watermark.subprocess.run = _boom
try:
    u, t = watermark.read_nvidia_memory_mb()
    check("显存读取失败 → (None, None)，绝不抛异常", u is None and t is None)
finally:
    watermark.subprocess.run = orig_run

tr = watermark.WaterLevelTracker()
high = watermark.WaterLevel(vram_used_mb=9000, vram_total_mb=10000)
low = watermark.WaterLevel(vram_used_mb=1000, vram_total_mb=10000)
e1 = tr.record(high)
check("第 1 次高位：不触发降档（连续 2 次才降）",
      e1["high"] is True and e1["suggest_downgrade"] is False and tr.should_downgrade() is False)
e2 = tr.record(high)
check("第 2 次连续高位：触发降档建议",
      e2["consecutive_high"] == 2 and tr.should_downgrade() is True)
tr.note_downgrade()
check("执行降档后计数复位（不再重复降档）", tr.should_downgrade() is False)
check("一次低水位会把连续计数清零", tr.record(low)["consecutive_high"] == 0)
tr.record(high)
tr.record(low)
tr.record(high)
check("非连续高位不触发（3 次里只有 2 次连续）", tr.should_downgrade() is False)
snap = tr.snapshot()
check("水位快照含阈值 / 计数 / 历史与 WDDM 口径说明",
      {"high_ratio", "consecutive_threshold", "consecutive_high", "history", "sampling_note"} <= set(snap))
check("历史条数受限（不会无限增长）", len(snap["history"]) <= watermark.HISTORY_LIMIT)
tr.reset()
check("reset 清空历史与计数", tr.snapshot()["consecutive_high"] == 0 and tr.snapshot()["history"] == [])


# ---------------------------------------------------------------------------
section("7. 降档链与 OOM 识别")
# ---------------------------------------------------------------------------

check("OOM 正例：CUDA out of memory", watermark.is_oom_error(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")))
check("OOM 正例：bad_alloc", watermark.is_oom_error(RuntimeError("std::bad_alloc")))
check("OOM 正例：WinError 1455（页面文件不足）",
      watermark.is_oom_error(RuntimeError("[WinError 1455] 页面文件太小，无法完成操作")))
check("OOM 正例：中文'显存不足'", watermark.is_oom_error(RuntimeError("显存不足，无法分配")))
check("OOM 负例：普通异常不误判", not watermark.is_oom_error(ValueError("shape mismatch")))

check("降档链耗尽后 GPU → 报显存不足",
      watermark.insufficient_error_code(CUDA) == "VRAM_INSUFFICIENT")
check("降档链耗尽后 CPU → 报物理内存不足（T0 的真瓶颈）",
      watermark.insufficient_error_code(CPU) == "RAM_INSUFFICIENT")

ch, rec = watermark.next_downgrade(
    {"tile": 512, "precision": "fp16", "backend": CUDA},
    adopted=[CUDA, CPU], cause="测试",
)
check("第 1 步：tile 折半 512 → 256", ch["tile"] == 256 and rec["field"] == "tile", str(ch))
ch, _ = watermark.next_downgrade({"tile": 128, "precision": "fp32", "backend": CUDA}, adopted=[CUDA, CPU])
check("继续折半 128 → 64", ch["tile"] == 64, str(ch))
ch, rec = watermark.next_downgrade({"tile": 64, "precision": "fp32", "backend": CUDA}, adopted=[CUDA, CPU])
check("tile 已到下界 → 切后端 GPU→CPU（换资源池，不换模型）",
      ch["backend"] == CPU and rec["field"] == "backend", str(ch))
ch, rec = watermark.next_downgrade({"tile": 64, "precision": "fp32", "backend": CPU}, adopted=[CUDA, CPU])
check("无步可退 → (None, None)，由调用方报错", ch is None and rec is None)
ch, _ = watermark.next_downgrade({"tile": 64, "precision": "fp32", "backend": CUDA}, adopted=[CUDA])
check("CPU 未通过验证时不得切到 CPU（避免'降级到不可用后端'）", ch is None)
check("降档链里没有 fp16 步骤（降档只做更保守的动作）",
      all("precision" not in (c or {}) for c, _ in [
          watermark.next_downgrade({"tile": 512, "precision": "fp16", "backend": CUDA}, adopted=[CUDA, CPU]),
          watermark.next_downgrade({"tile": 64, "precision": "fp16", "backend": CUDA}, adopted=[CUDA, CPU]),
          watermark.next_downgrade({"tile": 64, "precision": "fp16", "backend": CPU}, adopted=[CUDA, CPU]),
      ]))

merged = watermark.apply_downgrade_to_params({"tile": 512, "precision": "fp32"}, {"tile": 256, "align": 8})
check("降档参数合并后同步了过渡区宽度", merged["tile"] == 256 and merged["_overlap"] == 64, str(merged))


# ---------------------------------------------------------------------------
section("8. 端到端：任务链路里的决策 + 水位降档 + OOM 重试一次")
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402
from app.services import engine_decision  # noqa: E402
from app.tasks import manager as mgr  # noqa: E402
from app.tasks.executor import TaskCancelled  # noqa: E402


def make_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (96, 96), (12, 34, 56)).save(buf, "PNG")
    return buf.getvalue()


def wait_status(client, task_id: str, targets=("completed", "failed", "canceled"), timeout=25.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t["status"] in targets:
            return t
        time.sleep(0.15)
    raise TimeoutError(f"{task_id} 未在 {timeout}s 内进入 {targets}")


class _FastExecutor:
    """快进版占位执行器：只在控制面上跑很短一圈（真实推理仍属 T-806）。"""

    def __init__(self, *, oom_times: int = 0):
        self.oom_times = oom_times
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if self.calls <= self.oom_times:
            raise RuntimeError("CUDA out of memory. Tried to allocate 3.00 GiB")
        for i in (1, 2):
            if ctx.should_cancel():
                raise TaskCancelled()
            ctx.report_progress(i, 2, "inferencing", f"正在推理 {i} / 2 块")
        return ctx.profile.to_resolved()


with TestClient(app) as client:
    r = client.post("/api/files/upload", files={"file": ("a.png", io.BytesIO(make_png()), "image/png")})
    file_id = r.json()["file_id"]
    r = client.post("/api/models/import", data={
        "name": "SR_x4", "format": "onnx", "scale": "4", "min_vram_mb": "0",
        "backends": json.dumps(["cpu"]),
    }, files={"file": ("m.onnx", io.BytesIO(b"onnx-weights"), "application/octet-stream")})
    model_id = r.json()["id"]
    check("准备就绪：图片 + 模型", bool(file_id) and model_id.startswith("mdl_"), f"{file_id} {model_id}")

    params = {"scale": 4, "model_id": model_id, "tile": None, "precision": None, "backend": None, "auto": True}

    print("-- 8.1 正常任务：resolved 来自真实决策 --")
    orig_exec = mgr.get_executor
    mgr.get_executor = lambda: _FastExecutor()
    try:
        created = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
        check("提交返回 201", created.status_code == 201, str(created.status_code))
        t = wait_status(client, created.json()["id"])
    finally:
        mgr.get_executor = orig_exec
    check("任务完成", t["status"] == "completed", f"{t['status']} {t['error']}")
    res = t["resolved"] or {}
    check("resolved 含完整契约字段", {"tile", "precision", "backend", "using_fallback", "degraded", "reasons"} <= set(res))
    check("resolved 含 M2 编排所需的新增字段", {"overlap", "feather_px", "concurrency", "source"} <= set(res))
    check("阶段 C 未落地 → using_fallback=True 且 fp32",
          res.get("using_fallback") is True and res.get("precision") == "fp32", str(res)[:200])
    check("task.params 与 resolved 分离（params.tile 仍为 null）",
          t["params"]["tile"] is None and res.get("tile") is not None)
    check("任务完成后水位已记入历史",
          len(engine_decision.watermark_snapshot()["history"]) >= 1)

    print("-- 8.1b 占位执行器的诚实注记（快进单测，不跑 12×150ms）--")
    from app.tasks.executor import StubExecutor, TaskContext
    stub = StubExecutor()
    stub.TOTAL_CHUNKS, stub.CHUNK_SECONDS = 1, 0
    note = stub.run(TaskContext(
        task_id=0, params={}, model=None, source_path=Path("."),
        should_cancel=lambda: False, report_progress=lambda *a: None,
        profile=rp.decide_profile(facts=facts(), adopted=[CPU]),
    ))
    check("resolved 由注入的决策产出（tile 与决策一致）",
          note["tile"] == fallback.FALLBACK_MIN_TILE, str(note["tile"]))
    check("占位执行器在 resolved 里写明'尚未真正驱动计算'",
          any("尚未真正驱动计算" in x for x in note["reasons"]), str(note["reasons"]))
    no_profile = stub.run(TaskContext(
        task_id=0, params={}, model=None, source_path=Path("."),
        should_cancel=lambda: False, report_progress=lambda *a: None,
    ))
    check("未注入决策时回落到最保守输出（下界 tile / fp32 / cpu）",
          no_profile["using_fallback"] is True
          and no_profile["tile"] == fallback.FALLBACK_MIN_TILE
          and no_profile["precision"] == "fp32", str(no_profile))

    print("-- 8.2 标定命中：消费路径（构造记录，非端到端真实链路）--")
    from app.db import get_session
    from app.models.entities import Calibration, Model
    from app.services import system_info as sysinfo
    _, details = sysinfo.get_capability_snapshot()
    fp = details["hardware_fingerprint"]
    s = get_session()
    try:
        model_row = s.get(Model, int(model_id.split("_")[1]))
        s.add(Calibration(
            model_id=model_row.id, hardware_fingerprint=fp,
            tile_curve={"recommended_tile": 512, "recommended_backend": CPU},
            precision_decision="fp32", recommended_tier="T0", reason="构造的标定记录（消费路径验证）",
            valid=True,
        ))
        s.commit()
    finally:
        s.close()
    check("标定记录已落库并可按指纹取回",
          sysinfo.read_calibration_view(fp, model_row.id) is not None)
    check("指纹不符时取不到（失效判据是硬件指纹）",
          sysinfo.read_calibration_view("fp-other", model_row.id) is None)
    engine_decision.reset_last_decision()
    prof = engine_decision.decide_for_task(params, model_row)
    check("标定命中 → 任务决策采用标定结论",
          prof.source == "calibration" and prof.tile == 512 and prof.using_fallback is False,
          f"{prof.source} {prof.tile} {prof.using_fallback}")

    print("-- 8.3 阶段 E：连续 2 次高位 → 下一次任务降一档 --")
    engine_decision.watermark.get_tracker().reset()
    hw = watermark.WaterLevel(vram_used_mb=9500, vram_total_mb=10000)
    engine_decision.watermark.get_tracker().record(hw)
    check("第 1 次高位尚不降档", engine_decision.watermark.get_tracker().should_downgrade() is False)
    engine_decision.watermark.get_tracker().record(hw)
    prof2 = engine_decision.decide_for_task(params, model_row)
    check("连续 2 次高位 → 决策自动降一档（512 → 256）",
          prof2.tile == 256 and any(d["field"] == "tile" for d in prof2.downgrades),
          f"{prof2.tile} {prof2.downgrades}")
    check("降档理由写明阶段 E 反馈与降档原因",
          any("阶段 E 反馈" in d["reason"] for d in prof2.downgrades), str(prof2.downgrades))
    check("降档后计数复位（不会每个任务都降）",
          engine_decision.watermark.get_tracker().should_downgrade() is False)

    print("-- 8.4 OOM → 降一档后重试一次（成功）--")
    engine_decision.watermark.get_tracker().reset()
    s = get_session()
    try:  # 清掉标定，回到保底档，便于观察降档链
        for row in s.scalars(__import__("sqlalchemy").select(Calibration)).all():
            s.delete(row)
        s.commit()
    finally:
        s.close()
    # 保底档 tile 已在下界，故先用 M4 能力之外的路径验证：把 tile 抬到 128 再 OOM
    fast = _FastExecutor(oom_times=1)
    mgr.get_executor = lambda: fast
    try:
        created = client.post("/api/tasks", json={
            "type": "upscale", "file_id": file_id,
            "params": {**params, "auto": False, "tile": 256},
        })
        t2 = wait_status(client, created.json()["id"])
    finally:
        mgr.get_executor = orig_exec
    res2 = t2["resolved"] or {}
    check("OOM 后重试一次成功 → 任务 completed", t2["status"] == "completed", f"{t2['status']} {t2['error']}")
    check("执行器确实被调用了两次（重试）", fast.calls == 2, str(fast.calls))
    check("重试前发生了降档：tile 256 → 128",
          res2.get("tile") == 128 and any(d["field"] == "tile" for d in res2.get("downgrades", [])),
          str(res2)[:220])
    check("OOM 降档理由写明'推理时资源耗尽'",
          any("OOM" in d["reason"] for d in res2.get("downgrades", [])), str(res2.get("downgrades")))
    check("降档后 degraded=True（降级必须显式）", res2.get("degraded") is True)

    print("-- 8.5 OOM 连续两次 → 失败且报资源不足（降档链耗尽）--")
    engine_decision.watermark.get_tracker().reset()
    fast2 = _FastExecutor(oom_times=9)
    mgr.get_executor = lambda: fast2
    try:
        created = client.post("/api/tasks", json={
            "type": "upscale", "file_id": file_id,
            "params": {**params, "auto": False, "tile": 256},
        })
        t3 = wait_status(client, created.json()["id"])
    finally:
        mgr.get_executor = orig_exec
    check("只重试一次（共两次调用）", fast2.calls == 2, str(fast2.calls))
    check("任务 failed", t3["status"] == "failed", t3["status"])
    err = t3["error"] or {}
    check("错误码为 RAM_INSUFFICIENT（CPU 后端 → 物理内存不足）",
          err.get("code") == "RAM_INSUFFICIENT", str(err))
    check("错误体三要素齐全（code / message / suggestion）",
          {"code", "message", "suggestion"} <= set(err), str(err))
    check("错误详情含降档次数与后端信息",
          {"downgrade_attempts", "backend", "tile"} <= set(err.get("detail", {})), str(err.get("detail")))

    print("-- 8.6 诊断导出：decision / watermark 区段 --")
    diag = json.loads(client.get("/api/system/diagnostics").content.decode("utf-8"))
    check("诊断含 decision 与 watermark 区段", {"decision", "watermark"} <= set(diag), str(sorted(diag)))
    check("decision.policy 含保底档与水位阈值",
          {"fallback", "watermark", "sources"} <= set(diag["decision"]["policy"]))
    check("decision.last 记录了最近一次决策",
          diag["decision"]["last"] is None or "resolved" in diag["decision"]["last"],
          str(diag["decision"]["last"])[:120])
    check("watermark 区段含阈值 / 历史 / WDDM 口径说明",
          {"high_ratio", "consecutive_threshold", "history", "sampling_note"} <= set(diag["watermark"]))
    check("watermark.history 记录了任务边界采样",
          len(diag["watermark"]["history"]) >= 1, str(len(diag["watermark"]["history"])))

    print("-- 8.7 /api/system/capabilities 的保底语义与决策同源 --")
    caps = client.get("/api/system/capabilities").json()
    check("无有效标定 → using_fallback=True / fp32",
          caps["using_fallback"] is True and caps["active_precision"] == "fp32",
          f"{caps['using_fallback']} {caps['active_precision']}")
    check("保底语义来自阶段 D（active_precision 与 fallback 策略一致）",
          caps["active_precision"] == fallback.FALLBACK_PRECISION)


# ---------------------------------------------------------------------------
section("9. 引擎纯度与源码级检查")
# ---------------------------------------------------------------------------

for name in ("fallback.py", "runtime_profile.py", "watermark.py"):
    text = (ENGINE_DIR / name).read_text(encoding="utf-8")
    check(f"{name} 不含设备型号字面量",
          not re.search(r"4090|3050|RTX\s|GTX\s|GeForce|Tesla", text))
    check(f"{name} 不 import 应用层（fastapi/sqlalchemy/pydantic）",
          not re.search(r"^\s*(import|from)\s+(fastapi|sqlalchemy|pydantic)", text, re.M))

src_rp = (ENGINE_DIR / "runtime_profile.py").read_text(encoding="utf-8")
check("阶段 D 不按设备型号分支（无 gpu_name 字符串判断）",
      "gpu_name" not in src_rp and "device_name" not in src_rp)
check("阶段 D 判定顺序：先基线、再用户覆盖、再防御",
      src_rp.index("① 基线") < src_rp.index("② 用户覆盖") < src_rp.index("③ 因果性防御"))
check("GPU 后端集合与档位判定共用同一处定义（避免两处漂移）",
      set(GPU_PROVIDERS) == {"CUDAExecutionProvider", "TensorrtExecutionProvider"}
      and "GPU_PROVIDERS" in src_rp)
src_caps = (ENGINE_DIR / "capabilities.py").read_text(encoding="utf-8")
check("能力面板不再硬编码 using_fallback / active_precision（T-804 后同源）",
      '"using_fallback": True' not in src_caps and '"active_precision": "fp32"' not in src_caps)

src_mgr = (ROOT / "server/app/tasks/manager.py").read_text(encoding="utf-8")
check("管理器不再自己决定参数（决策来自 engine_decision）",
      "engine_decision.decide_for_task" in src_mgr)
check("OOM 只重试一次（有明确的一次性闸门）", "oom_retry_used" in src_mgr)


# ---------------------------------------------------------------------------
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
