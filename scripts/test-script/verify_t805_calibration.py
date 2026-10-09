"""T-805 首启自标定（阶段 C / F-10）验证。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t805_calibration.py

覆盖：
  1. 候选集与对齐（动态 / 静态 / 下界）
  2. 单档真实测量（真实 ONNX + CPU EP，产出结构完整）
  3. 推荐规则（安全档取吞吐最高 / 全不安全 / 全失败）
  4. 预算与停止条件（超预算截断 / 超水位停止上探）
  5. 异常隔离（模型缺失 / 单档失败不毁整轮）
  6. 模拟保护（simulated 结论不得入库）
  7. 落库与失效（写入 / 旧记录作废 / 查询形状）
  8. **消费链路打通**：写入后阶段 D 不再走保底档（这是本任务真正要兑现的事）
  9. HTTP 端点形状
 10. 引擎纯度与源码级检查

隔离：数据根建在**系统临时目录**（不在仓库内），避免临时产物污染工作区。
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t805_"))
DATA = TMP / "data"
DATA.mkdir(parents=True, exist_ok=True)

# 必须在 import app 之前设置（config 会缓存数据根）
os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"

sys.path.insert(0, str(ROOT / "server"))

from app.engine.runtime_env import prepare_dll_paths  # noqa: E402

prepare_dll_paths()

import numpy as np  # noqa: E402

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


from app.core import lifecycle  # noqa: E402
from app.db import get_session  # noqa: E402
from app.engine import calibration as calib  # noqa: E402
from app.engine import device_probe  # noqa: E402
from app.engine.runtime_profile import CalibrationView, RuntimeRequest, decide_profile  # noqa: E402
from app.models.entities import Calibration, Model  # noqa: E402
from app.services import calibration_service, system_info  # noqa: E402

REAL_MODEL = ROOT / "data" / "models" / "RealESRGAN_x4.onnx"

lifecycle._run_migrations()


# ---------------------------------------------------------------------------
section("1. 候选集与对齐")
# ---------------------------------------------------------------------------

dyn = calib.candidate_tiles(align=8)
check("动态模型候选集非空且按 8 对齐", bool(dyn) and all(t % 8 == 0 for t in dyn), str(dyn))
check("候选集不含低于保底下界的档", all(t >= 64 for t in dyn), str(dyn))

static = calib.candidate_tiles(align=8, fixed_tile=512)
check("静态模型只有一个合法档（不是'最小档'）", static == [512], str(static))

a16 = calib.candidate_tiles(align=16)
check("align=16 时候选均按 16 对齐", all(t % 16 == 0 for t in a16), str(a16))


# ---------------------------------------------------------------------------
section("2. 单档真实测量（真实 ONNX + CPU EP）")
# ---------------------------------------------------------------------------

if not REAL_MODEL.is_file():
    check("真实基准模型存在", False, f"missing {REAL_MODEL}")
else:
    t0 = time.perf_counter()
    res = calib.run_calibration(
        model_path=REAL_MODEL,
        fingerprint="verify-fp-1",
        tier="T1",
        backend="CPUExecutionProvider",
        force_tiles=(64,),
        budget_s=60.0,
        runs=1,
        warmup=1,
    )
    dt = time.perf_counter() - t0
    check("标定跑完不抛异常", isinstance(res, calib.CalibrationOutcome))
    check("产出至少一条样本", len(res.samples) >= 1, f"{len(res.samples)}")
    s0 = res.samples[0]
    check("样本 ok=True", s0.ok, str(s0.error))
    check("样本记录了真实延迟", isinstance(s0.latency_ms, (int, float)) and s0.latency_ms > 0,
          f"{s0.latency_ms}")
    check("样本记录了吞吐（跨 tile 唯一可比口径）",
          isinstance(s0.throughput_pps, (int, float)) and s0.throughput_pps > 0,
          f"{s0.throughput_pps}")
    check("推荐出了块尺寸", res.recommended_tile == 64, f"{res.recommended_tile}")
    check("结论可入库（真实测量）", res.is_storable())
    check("理由非空且可读", bool(res.reason), res.reason[:80])
    print(f"     真实测量耗时 {dt:.1f}s · 延迟 {s0.latency_ms}ms · 吞吐 {s0.throughput_pps/1000:.0f}k px/s")

    curve = res.to_curve()
    check("tile_curve 带 recommended_tile（阶段 D 唯一读取的字段）",
          curve.get("recommended_tile") == 64, str(curve.get("recommended_tile")))

    # 序列化必须可 JSON 化（要进 JSON 列与 API）
    try:
        json.dumps(res.to_dict(), ensure_ascii=False)
        ok_json = True
    except Exception as exc:
        ok_json = False
        print("     ", exc)
    check("结论可 JSON 序列化", ok_json)


# ---------------------------------------------------------------------------
section("3. 推荐规则")
# ---------------------------------------------------------------------------


def _sample(tile: int, tput: float, *, safe: bool = True, ok: bool = True) -> calib.TileSample:
    return calib.TileSample(
        tile=tile, precision="fp32", ok=ok, latency_ms=1000.0,
        throughput_pps=tput, safe=safe, peak_ratio=0.5 if safe else 0.95,
    )


best, why = calib._recommend([_sample(64, 50_000), _sample(128, 90_000), _sample(256, 70_000)])
check("取吞吐最高者而非最大/最小档", best == 128, f"{best}")
check("理由里带上实测数据", "90k" in why or "90000" in why or "90" in why, why[:80])

unsafe_best, unsafe_why = calib._recommend([
    _sample(64, 50_000, safe=False), _sample(128, 90_000, safe=False),
])
check("全部超水位 → 不给推荐（宁可回保底档）", unsafe_best is None, str(unsafe_best))
check("超水位时理由说明原因", "水位" in unsafe_why, unsafe_why[:80])

failed_best, failed_why = calib._recommend([_sample(64, 0, ok=False)])
check("全部失败 → 不给推荐", failed_best is None, str(failed_best))
check("全失败时理由说明原因", "没有成功" in failed_why, failed_why[:80])


# ---------------------------------------------------------------------------
section("4. 预算与停止条件")
# ---------------------------------------------------------------------------

if REAL_MODEL.is_file():
    tiny = calib.run_calibration(
        model_path=REAL_MODEL, fingerprint="verify-fp-budget", tier="T1",
        backend="CPUExecutionProvider", force_tiles=(64, 128, 256, 384, 512),
        budget_s=0.5, runs=1, warmup=1,
    )
    check("预算极小 → 如实标 budget_exceeded", tiny.budget_exceeded, str(tiny.budget_exceeded))
    check("预算耗尽仍给出基于已测档位的结论（不空手而归）",
          tiny.recommended_tile is not None or len(tiny.samples) == 0,
          f"tile={tiny.recommended_tile} samples={len(tiny.samples)}")
    check("预算耗尽时理由里有交代", "预算" in tiny.reason, tiny.reason[:100])

# 超水位停止上探：用桩替换测量函数
_orig_measure = calib._measure_one
try:
    calls: list[int] = []

    def _fake_measure(**kw):
        calls.append(kw["tile"])
        ratio = 0.95 if kw["tile"] >= 256 else 0.40
        return calib.TileSample(
            tile=kw["tile"], precision=kw["precision"], ok=True, latency_ms=100.0,
            throughput_pps=10_000.0, safe=ratio <= calib.WATER_LEVEL_LIMIT, peak_ratio=ratio,
        )

    calib._measure_one = _fake_measure  # type: ignore[assignment]
    stopped = calib.run_calibration(
        model_path=REAL_MODEL if REAL_MODEL.is_file() else Path("/nonexistent.onnx"),
        fingerprint="verify-fp-water", tier="T1", backend="CPUExecutionProvider",
        force_tiles=(64, 128, 256, 512), budget_s=60.0, runs=1, warmup=0,
    )
    check("超过水位阈值即停止上探（不再测更大的档）", calls == [64, 128, 256], str(calls))
    check("停止后仍给出安全档中的最优", stopped.recommended_tile is not None,
          str(stopped.recommended_tile))
finally:
    calib._measure_one = _orig_measure  # type: ignore[assignment]


# ---------------------------------------------------------------------------
section("5. 异常隔离")
# ---------------------------------------------------------------------------

missing = calib.run_calibration(
    model_path=TMP / "not-exist.onnx", fingerprint="fp", tier="T0",
    force_tiles=(64,), budget_s=5.0,
)
check("模型缺失 → 不抛异常，返回带理由的结论", isinstance(missing, calib.CalibrationOutcome))
check("模型缺失 → 不入库", not missing.is_storable())
check("模型缺失 → 理由说明", "不存在" in missing.reason, missing.reason[:80])

# 单档失败不毁整轮：第一档失败，第二档成功
_orig_measure2 = calib._measure_one
try:
    seq = {"n": 0}

    def _flaky(**kw):
        seq["n"] += 1
        if seq["n"] == 1:
            return calib.TileSample(tile=kw["tile"], precision=kw["precision"],
                                    ok=False, error="OOM: simulated")
        return calib.TileSample(tile=kw["tile"], precision=kw["precision"], ok=True,
                                latency_ms=50.0, throughput_pps=20_000.0, safe=True, peak_ratio=0.3)

    calib._measure_one = _flaky  # type: ignore[assignment]
    flaky = calib.run_calibration(
        # 必须用**真实存在**的模型路径：run_calibration 对不存在的路径会提前返回，
        # 那样就绕过了 mock 的测量函数，断言会落在"空样本"上（2026-10-09 踩到）。
        model_path=REAL_MODEL, fingerprint="fp", tier="T1",
        backend="CPUExecutionProvider", force_tiles=(64, 128), budget_s=60.0, runs=1, warmup=0,
    )
    check("OOM 档被记为失败而不是抛异常", any(not s.ok for s in flaky.samples), str(flaky.samples))
    check("OOM 后停止上探（更大的档只会更糟）", len(flaky.samples) == 1, f"{len(flaky.samples)}")
finally:
    calib._measure_one = _orig_measure2  # type: ignore[assignment]


# ---------------------------------------------------------------------------
section("6. 模拟保护")
# ---------------------------------------------------------------------------

sim = calib.run_calibration(
    model_path=REAL_MODEL if REAL_MODEL.is_file() else Path("/nonexistent.onnx"),
    fingerprint="fp-sim", tier="T1", simulate=True,
)
check("模拟产出标 simulated=True", sim.simulated)
check("模拟结论**不可**入库（is_storable=False）", not sim.is_storable())
check("模拟理由明确说明不得入库", "不得写入" in sim.reason or "非实测" in sim.reason, sim.reason[:90])


# ---------------------------------------------------------------------------
section("7. 落库与失效")
# ---------------------------------------------------------------------------

good = calib.CalibrationOutcome(
    fingerprint="fp-store", model_path=str(REAL_MODEL), model_id=None, tier="T1",
    backend="CPUExecutionProvider", scale=4, samples=(_sample(256, 80_000),),
    recommended_tile=256, precision_decision="fp32", recommended_tier="T1",
    reason="测试用结论", elapsed_ms=1234.0,
)
check("可入库结论 is_storable", good.is_storable())
check("落库成功", calibration_service._store(good))

with get_session() as s:
    rows = s.query(Calibration).filter(Calibration.hardware_fingerprint == "fp-store").all()
check("CALIBRATION 表写入 1 行", len(rows) == 1, f"{len(rows)}")
check("落库的 tile_curve 含 recommended_tile",
      isinstance(rows[0].tile_curve, dict) and rows[0].tile_curve.get("recommended_tile") == 256)
check("落库的 precision_decision 正确", rows[0].precision_decision == "fp32")

# 再写一条同 (model, fingerprint) 的记录 → 旧的应作废
good2 = calib.CalibrationOutcome(
    fingerprint="fp-store", model_path=str(REAL_MODEL), model_id=None, tier="T1",
    backend="CPUExecutionProvider", scale=4, samples=(_sample(128, 90_000),),
    recommended_tile=128, precision_decision="fp32", recommended_tier="T1",
    reason="第二次标定", elapsed_ms=1000.0,
)
calibration_service._store(good2)
with get_session() as s:
    allrows = s.query(Calibration).filter(Calibration.hardware_fingerprint == "fp-store").all()
    validrows = [r for r in allrows if r.valid]
check("重标定后旧记录被置为失效", len(allrows) == 2 and len(validrows) == 1,
      f"total={len(allrows)} valid={len(validrows)}")
check("保留的是最新那条", validrows[0].tile_curve.get("recommended_tile") == 128)

desc = calibration_service.describe()
check("describe 形状含 records / recommended_tier / reasons / state",
      all(k in desc for k in ("records", "recommended_tier", "reasons", "state")), str(list(desc)))
check("describe 汇报了推荐档位", desc["recommended_tier"] == "T1", str(desc["recommended_tier"]))
check("state 含 status", "status" in desc["state"])


# ---------------------------------------------------------------------------
section("8. 消费链路打通（阶段 D 不再走保底档）")
# ---------------------------------------------------------------------------

view = system_info.read_calibration_view("fp-store", None)
check("按 (指纹, 模型) 能读回标定结论", view is not None)
check("读回的是通用记录（model_id=None）", view is not None and view.tile == 128,
      str(view.tile if view else None))
check("指纹一致才可用（usable_with）", view is not None and view.usable_with("fp-store"))
check("指纹不符即不可用（失效判据是指纹不是时间）",
      view is not None and not view.usable_with("other-fp"))

facts = device_probe.probe_device_facts()
prof = decide_profile(
    facts=facts, adopted=["CPUExecutionProvider"], fingerprint="fp-store",
    calibration=view, request=RuntimeRequest.from_params({}),
)
check("标定命中时 using_fallback=False（'自动'档真正生效）", not prof.using_fallback,
      f"using_fallback={prof.using_fallback}")
check("决策采纳了标定推荐的 tile", prof.tile == 128, f"tile={prof.tile}")
check("决策理由里指明来源是标定", any("标定" in r for r in prof.reasons), str(prof.reasons)[:120])

prof_fallback = decide_profile(
    facts=facts, adopted=["CPUExecutionProvider"], fingerprint="other-fp",
    calibration=view, request=RuntimeRequest.from_params({}),
)
check("指纹不符时退回保底档（using_fallback=True）", prof_fallback.using_fallback)
check("保底档 tile 不等于标定推荐值", prof_fallback.tile != 128, f"tile={prof_fallback.tile}")


# ---------------------------------------------------------------------------
section("9. HTTP 端点形状")
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as client:
    # 进 with 后 lifespan 已跑（会尝试投递首启标定），先清干净再看端点行为
    calibration_service.reset_state()

    r = client.get("/api/system/calibration")
    check("GET /api/system/calibration 返回 200", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("响应含 records / recommended_tier / reasons / state",
          all(k in body for k in ("records", "recommended_tier", "reasons", "state")),
          str(list(body)))

    r2 = client.post("/api/system/calibrate")
    check("POST /api/system/calibrate 返回 200", r2.status_code == 200, str(r2.status_code))
    b2 = r2.json()
    check("触发响应含 started 与 task_id", "started" in b2 and "task_id" in b2, str(b2)[:120])

    r3 = client.post("/api/system/calibrate")
    b3 = r3.json()
    check("重复触发不叠加新作业（返回当前状态）",
          b3.get("started") is False or b3.get("task_id") == b2.get("task_id"), str(b3)[:120])

    # 等后台作业收尾，避免测试进程带着活线程退出
    deadline = time.time() + 30
    while time.time() < deadline:
        if calibration_service.describe()["state"]["status"] != "running":
            break
        time.sleep(0.2)
    final_status = calibration_service.describe()["state"]["status"]
    check("标定作业最终离开 running 态", final_status != "running", final_status)

    # 临时数据根里没有模型 → 标定应如实跳过而不是报错
    check("无可用探针模型时标定如实跳过（不报错、不硬失败）",
          final_status in ("skipped", "completed"), final_status)


# ---------------------------------------------------------------------------
section("10. 引擎纯度与源码级检查")
# ---------------------------------------------------------------------------

import re  # noqa: E402

_SRC = ROOT / "server" / "app" / "engine"
_APP_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:fastapi|sqlalchemy|pydantic)\b", re.M)

for name in ("calibration.py",):
    src = (_SRC / name).read_text(encoding="utf-8")
    bad = sorted(set(_APP_IMPORT.findall(src)))
    check(f"{name} 不 import 应用层（引擎纯度）", not bad, str(bad))
    models = [w for w in ("3050", "4090", "RTX ", "GTX ", "GeForce") if w in src]
    check(f"{name} 不含设备型号字面量（ADR-004 原则 2）", not models, str(models))

cal_src = (_SRC / "calibration.py").read_text(encoding="utf-8")
check("标定不硬编码本机实测最优点（数值一律来自实测）",
      "recommended_tile = 256" not in cal_src and "recommended_tile = 512" not in cal_src)

# 服务层：模拟结论必须被拒绝入库
svc_src = (ROOT / "server" / "app" / "services" / "calibration_service.py").read_text(encoding="utf-8")
check("服务层以 is_storable() 作为入库闸门", "is_storable()" in svc_src)


# ---------------------------------------------------------------------------
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
raise SystemExit(1 if FAIL else 0)
