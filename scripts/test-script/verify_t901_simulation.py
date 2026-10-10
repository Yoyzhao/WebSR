"""T-901 档位模拟开关验证：解析 / 档位覆盖 / 门控 / EP 候选链 / 设置 / HTTP 端到端。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t901_simulation.py

## 验证策略

档位模拟是**控制面**能力（PRD §2.3 原则 4）——它的价值是让一台 T1 机器**真实执行**
T2/T3 的判定代码路径。所以本脚本**不依赖 T2/T3 硬件**，而是逐层验证"声明被真实采纳"：

1. **解析层**：纯函数，非法值一律当作"未声明"（P3 不得阻塞 T1 主链路）。
2. **档位推导**：`force_tier` / `force_vram` 真的改变 `derive_tier` 的结论，
   且 `tier_reason` 必然带「档位模拟」字样与**本机真实档位**（可辨性）。
3. **门控**：模型可用性按**声明的显存**裁定（低显存置灰、高显存解禁）。
4. **EP 候选链**：`force_has_tensorrt` 真的把 TensorRT 加进链（否则该分支永不执行）。
5. **设置层**：三键可写、非法值在保存时即被拒（可照做的报错）、保存后作废下游缓存。
6. **HTTP 端到端**：`PUT /api/settings` → `GET /api/system/capabilities` 真的变成模拟档位；
   **真实硬件事实不被改写**（诚信判据）；契约字段集不变。
7. **不阻塞**：非法设置在 HTTP 层被拒、拒绝后 T1 主链路（capabilities / models）照常可用。

关键诚信判据（本脚本的"灵魂"）：**模拟只覆盖判定输入，不伪造硬件事实** ——
`device_facts.available_vram_gb` 在模拟前后必须**完全相等**。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t901_"))
DATA = TMP / "data"
DATA.mkdir(parents=True, exist_ok=True)

# 必须在 import app 之前设置（config 会缓存）
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


SKIPPED = 0


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def skip(name: str, why: str) -> None:
    """**有界跳过**：只有在无法归因于产品缺陷时才用（环境缺条件）。"""
    global SKIPPED
    SKIPPED += 1
    print(f"  [SKIP] {name} —— {why}")


# ---------------------------------------------------------------------------
# 1. 解析层（纯函数，不依赖应用栈）
# ---------------------------------------------------------------------------
section("1. 解析层（engine/simulation.py）")

from app.engine.simulation import (  # noqa: E402
    SimulationOverride,
    disabled,
    from_values,
    parse_bool,
    parse_tier,
    parse_vram_mb,
)

check("parse_tier 大小写与空白容错", parse_tier(" t2 ") == "T2" and parse_tier("T3") == "T3")
check("parse_tier 非法值 → None（不猜测）",
      parse_tier("T9") is None and parse_tier("") is None and parse_tier(None) is None)
check("parse_vram_mb：裸数字按 MB", parse_vram_mb("24576") == 24576)
check("parse_vram_mb：带 G 后缀按 GB", parse_vram_mb("24G") == 24576 and parse_vram_mb("24gb") == 24576)
check("parse_vram_mb：0 是**有效值**（模拟无显存），不是 falsy",
      parse_vram_mb("0") == 0 and parse_vram_mb("0G") == 0)
check("parse_vram_mb：负数 / 非数字 / 超界 → None",
      parse_vram_mb("-1") is None and parse_vram_mb("abc") is None and parse_vram_mb("9999999") is None)
check("parse_bool 三态", (parse_bool("true"), parse_bool("0"), parse_bool("")) == (True, False, None))

off = disabled()
check("未开启时 active 为假", off.active is False)
check("总闸开但未声明任何覆盖项 → **不算生效**",
      SimulationOverride(enabled=True).active is False)
check("声明覆盖项才算生效",
      SimulationOverride(enabled=True, force_tier="T2").active is True)
check("from_values 全空 → 未开启", from_values().active is False)
check("from_values 任一环节脏数据 → 整体降级为未开启（永不抛异常）",
      from_values(enabled="true", force_tier="T9").active is False)

sim_t2 = SimulationOverride(enabled=True, force_tier="T2", force_vram_mb=24576)
block = sim_t2.to_block()
check("契约 simulation 块**只有两个键**（字段集不变）", set(block) == {"enabled", "force_tier"},
      f"actual={sorted(block)}")
check("to_block 的 enabled 反映真实生效", block["enabled"] is True and block["force_tier"] == "T2")
line = sim_t2.reason_line()
check("reasons 文案可辨（含「档位模拟」「不代表真实性能」）",
      "档位模拟" in line and "不代表真实性能" in line, line)
check("未生效时不产生 reasons 文案", off.reason_line() == "")

# apply_to_facts：只覆盖判定输入
from app.engine.device_probe import CpuFacts, DeviceFacts, NvidiaGpuFacts  # noqa: E402


def make_facts(*, vram_free_mb: int | None, compute_cap: str | None = "8.6") -> DeviceFacts:
    return DeviceFacts(
        os_name="Test 1.0",
        is_wddm=True,
        cpu=CpuFacts("测试 CPU", "x86_64", 8, 4, 16384, 8192),
        nvidia=(
            [NvidiaGpuFacts("测试 GPU", "555.0", compute_cap, 8192, vram_free_mb)]
            if vram_free_mb is not None else []
        ),
    )


real_facts = make_facts(vram_free_mb=7168)
view = sim_t2.apply_to_facts(real_facts)
check("模拟视图的可用显存 = 声明值", view is not None and view.available_vram_mb == 24576)
check("**原对象未被改写**（真实事实不动）", real_facts.available_vram_mb == 7168)
check("模拟视图保留真实能力事实（compute_cap 不被伪造）",
      view is not None and view.primary_nvidia is not None
      and view.primary_nvidia.compute_cap == "8.6" and view.primary_nvidia.name == "测试 GPU")
no_gpu_view = sim_t2.apply_to_facts(make_facts(vram_free_mb=None))
check("无独显却声明显存 → 合成一条**标注为模拟**的记录",
      no_gpu_view is not None and no_gpu_view.available_vram_mb == 24576
      and "模拟" in (no_gpu_view.primary_nvidia.name if no_gpu_view.primary_nvidia else ""))
check("未开启时 apply_to_facts 恒等（同一对象）", off.apply_to_facts(real_facts) is real_facts)

# ---------------------------------------------------------------------------
# 2. 档位推导
# ---------------------------------------------------------------------------
section("2. 档位判定（derive_tier + 模拟覆盖）")

from app.engine.capabilities import derive_tier  # noqa: E402

CUDA = ["CUDAExecutionProvider"]
real_tier, real_label, real_reason = derive_tier(real_facts, CUDA)
check("基线：8G + 已验证 GPU → T1", real_tier == "T1", f"actual={real_tier}")

tier, label, reason = derive_tier(real_facts, CUDA, SimulationOverride(enabled=True, force_tier="T2"))
check("force_tier=T2 → 档位判定为 T2", tier == "T2")
check("模拟档位的 label 标注「模拟档位」", "模拟" in label, label)
check("模拟档位的理由含「档位模拟」+ 本机真实档位 + 真实判定依据",
      "档位模拟" in reason and "T1" in reason and real_reason[:12] in reason, reason)

tier, _, reason = derive_tier(real_facts, CUDA, SimulationOverride(enabled=True, force_vram_mb=24576))
check("force_vram=24G → **重新推导**为 T2", tier == "T2", f"actual={tier}")
tier, _, _ = derive_tier(real_facts, CUDA, SimulationOverride(enabled=True, force_vram_mb=65536))
check("force_vram=64G → 推导为 T3", tier == "T3")
tier, _, _ = derive_tier(real_facts, [], SimulationOverride(enabled=True, force_vram_mb=0))
check("force_vram=0 且无已验证后端 → T0（0 是有效值，未被当作未声明）", tier == "T0")
tier, _, _ = derive_tier(real_facts, [], SimulationOverride(enabled=True, force_tier="T2"))
check("纯 CPU 机器也可强制 T2（T0→T2 的控制面覆盖）", tier == "T2")
check("未开启时与真实判定**逐字一致**（模拟未开启不得改变任何结论）",
      derive_tier(real_facts, CUDA, off) == (real_tier, real_label, real_reason))

# ---------------------------------------------------------------------------
# 3. 可用性门控
# ---------------------------------------------------------------------------
section("3. 模型可用性门控（按**声明显存**裁定）")

from app.engine.availability import HardwareSnapshot, gate_availability  # noqa: E402

REQ_16G = 16 * 1024
real_snap = HardwareSnapshot(available_vram_mb=7168)
sim_snap = HardwareSnapshot(available_vram_mb=24576, simulated=True)

ok_real, why_real = gate_availability(status="ready", min_vram_mb=REQ_16G, snapshot=real_snap, fmt="onnx")
ok_sim, _ = gate_availability(status="ready", min_vram_mb=REQ_16G, snapshot=sim_snap, fmt="onnx")
check("真实 7G 可用显存 → 16G 门槛的模型置灰", ok_real is False, f"reason={why_real}")
check("模拟 24G 可用显存 → 同一模型解禁", ok_sim is True)
ok_no_threshold, why_no_threshold = gate_availability(
    status="ready", min_vram_mb=None,
    snapshot=HardwareSnapshot(available_vram_mb=7168, simulated=False), fmt="onnx",
)
check("无显存门槛的模型不受模拟影响（只覆盖声明项，不误伤）",
      ok_no_threshold is True and why_no_threshold is None, str(why_no_threshold))

# ---------------------------------------------------------------------------
# 4. EP 候选链
# ---------------------------------------------------------------------------
section("4. EP 候选链（force_has_tensorrt）")

from app.engine import availability as avail_mod  # noqa: E402
from app.engine import capabilities as caps_mod  # noqa: E402
from app.engine.ep_verify import build_candidate_chain  # noqa: E402

base_chain = build_candidate_chain(real_facts)
forced_chain = build_candidate_chain(real_facts, force_tensorrt=True)
check("默认不入链 TensorRT（G-04/P3 需能力声明）",
      "TensorrtExecutionProvider" not in base_chain, str(base_chain))
check("force_has_tensorrt → TensorRT 入链且 CUDA / CPU 仍在",
      forced_chain[:3] == ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"],
      str(forced_chain))
check("无 NVIDIA 时即使强制也不入链（不造出无候选资格的硬件组合）",
      "TensorrtExecutionProvider" not in build_candidate_chain(
          make_facts(vram_free_mb=None), force_tensorrt=True))
check("unknown 组合仍以 CPU 收尾", forced_chain[-1] == "CPUExecutionProvider")

# ---- 缓存纪律：模拟态不读写 EP 验证缓存（否则会污染真实硬件指纹下的结论）----
_, details_off = caps_mod.build_snapshot(DATA, models_dir=DATA / "models")
_, details_sim = caps_mod.build_snapshot(
    DATA, models_dir=DATA / "models", simulation=SimulationOverride(enabled=True, force_tier="T2")
)
check("常态走普通缓存路径",
      details_off.get("cache_state") == "skipped", str(details_off.get("cache_state")))
check("模拟态**绕过** EP 验证缓存（避免把带模拟成分的结论写进真实指纹的缓存）",
      details_sim.get("cache_state") == "bypassed_simulation",
      str(details_sim.get("cache_state")))
check("模拟态诊断里给出「真实档位」对照",
      details_sim.get("simulation", {}).get("real_tier") == details_off.get("simulation", {}).get("real_tier")
      and details_sim.get("simulation", {}).get("active") is True,
      json.dumps(details_sim.get("simulation"), ensure_ascii=False))

# ---------------------------------------------------------------------------
# 5. 设置层（需要建表；不起 HTTP）
# ---------------------------------------------------------------------------
section("5. 设置层（settings_store）")

from app.core import lifecycle  # noqa: E402

lifecycle._run_migrations()

from app.core.errors import AppError  # noqa: E402
from app.services import settings_store  # noqa: E402
from app.services import simulation as simulation_service  # noqa: E402

items = {s["key"]: s for s in settings_store.list_settings()}
for k in ("force_tier", "force_vram_mb", "force_has_tensorrt"):
    check(f"设置表暴露 {k}", k in items)
check("三个强制项默认都是空串（= 不强制，而非「强制 T0 / 0 显存」）",
      all(items[k]["value"] == "" for k in ("force_tier", "force_vram_mb", "force_has_tensorrt")))
check("总闸默认关闭", items["simulation_enabled"]["value"] == "false")

# 非法值必须在保存时被拒（可照做的报错），而不是静默接受后"看起来没生效"
for key, bad in (("force_tier", "T9"), ("force_vram_mb", "abc"), ("force_vram_mb", "-3")):
    try:
        settings_store.update_settings([{"key": key, "value": bad}])
        check(f"非法 {key}={bad} 被拒", False, "居然保存成功")
    except AppError as exc:
        check(f"非法 {key}={bad} 被拒且给出可照做的说明",
              exc.code == "VALIDATION_ERROR" and bool(exc.suggestion), f"{exc.code} / {exc.suggestion}")

# 合法值：规范化写法与引擎层同源
settings_store.update_settings([
    {"key": "simulation_enabled", "value": "true"},
    {"key": "force_tier", "value": "t2"},
    {"key": "force_vram_mb", "value": "24G"},
    {"key": "force_has_tensorrt", "value": "true"},
])
saved = {s["key"]: s["value"] for s in settings_store.list_settings()}
check("force_tier 规范化小写输入", saved["force_tier"] == "T2", saved["force_tier"])
check("force_vram_mb 规范化 G 后缀为 MB", saved["force_vram_mb"] == "24576", saved["force_vram_mb"])
cur = simulation_service.current()
check("services.simulation.current() 读到四项并生效",
      cur.active and cur.force_tier == "T2" and cur.force_vram_mb == 24576
      and cur.force_has_tensorrt is True, cur.summary())

# 保存触发下游缓存作废
caps_mod.reset_snapshot()
avail_mod.reset_snapshot()
settings_store.update_settings([{"key": "force_vram_mb", "value": ""}])
check("空串 = 撤销该强制项（回到不声明）", simulation_service.current().force_vram_mb is None)

# 读不到设置也必须安全（P3 不得阻塞）
check("settings 读取异常时 current() 仍返回未开启对象",
      isinstance(simulation_service.current(), SimulationOverride))

# 收尾复原：第 6 段要取**真实**基线，不能被本段残留的模拟设置污染
# （否则 real_tier_http 会拿到模拟档位，导致"关闭后复原"等断言假失败）
settings_store.update_settings([
    {"key": "simulation_enabled", "value": "false"},
    {"key": "force_tier", "value": ""},
    {"key": "force_has_tensorrt", "value": ""},
])
check("第 5 段收尾复原：模拟已关闭（第 6 段基线为真实判定）",
      simulation_service.current().active is False, simulation_service.current().summary())

# ---------------------------------------------------------------------------
# 6. HTTP 端到端
# ---------------------------------------------------------------------------
section("6. HTTP 端到端（TestClient）")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.entities import Model  # noqa: E402

# 注入一个"需要 16G 显存"的模型行——这样门控结论与本机真实显存无关（跨机可复现）
s = get_session()
try:
    s.add(Model(
        name="T901 虚拟 16G 模型", format="onnx", path="t901/never-exists.onnx",
        sha256="0" * 64, supported_backends=["cuda"], param_count=1, scale=4,
        min_vram_mb=REQ_16G, license="test", source="imported", status="ready",
    ))
    s.commit()
finally:
    s.close()

with TestClient(app) as client:
    base = client.get("/api/system/capabilities").json()
    real_vram = base["device_facts"]["available_vram_gb"]
    real_tier_http = base["tier"]
    check("基线：simulation.enabled 为假", base["simulation"]["enabled"] is False)
    check("契约字段集不变（simulation 只有两个键）",
          set(base["simulation"]) == {"enabled", "force_tier"}, str(base["simulation"]))

    def _find(model_id_prefix: str = "T901 虚拟"):
        rows = client.get("/api/models").json()
        return next((m for m in rows if m["name"].startswith(model_id_prefix)), None)

    m = _find()
    check("虚拟 16G 模型在列表中存在", m is not None)
    if real_vram is None:
        skip("基线：16G 门槛模型置灰", "本机探测不到可用显存（显存门槛不启用，跨机不可复现）")
        skip("置灰原因可照做", "同上")
    else:
        check("基线：16G 门槛模型被**置灰**（可用显存 < 16G）",
              m is not None and m["available"] is False, str(m and m.get("unavailable_reason")))
        check("置灰原因可照做（含需求与实际显存）",
              m is not None and "显存" in (m.get("unavailable_reason") or ""),
              str(m and m.get("unavailable_reason")))

    # —— 开启模拟：声明 T2 + 24G 显存 ——
    r = client.put("/api/settings", json={"items": [
        {"key": "simulation_enabled", "value": "true"},
        {"key": "force_tier", "value": "T2"},
        {"key": "force_vram_mb", "value": "24G"},
    ]})
    check("PUT /api/settings 200", r.status_code == 200, str(r.status_code))
    saved_http = {i["key"]: i["value"] for i in r.json()}
    check("响应即规范化值（t2→T2 / 24G→24576）",
          saved_http.get("force_tier") == "T2" and saved_http.get("force_vram_mb") == "24576",
          str(saved_http))

    caps2 = client.get("/api/system/capabilities").json()
    check("① 档位判定被真实覆盖 → T2", caps2["tier"] == "T2", caps2["tier"])
    check("① tier_label 标注模拟", "模拟" in caps2["tier_label"], caps2["tier_label"])
    check("① tier_reason 含「档位模拟」与本机真实档位",
          "档位模拟" in caps2["tier_reason"] and real_tier_http in caps2["tier_reason"],
          caps2["tier_reason"])
    check("① simulation.enabled 为真且 force_tier=T2",
          caps2["simulation"] == {"enabled": True, "force_tier": "T2"}, str(caps2["simulation"]))
    check("② **诚信判据**：真实硬件事实未被改写（可用显存前后相等）",
          caps2["device_facts"]["available_vram_gb"] == real_vram,
          f"{real_vram} → {caps2['device_facts']['available_vram_gb']}")

    m2 = _find()
    check("③ 能力门控按模拟显存解禁（16G 模型变可用）",
          m2 is not None and m2["available"] is True,
          str(m2 and m2.get("unavailable_reason")))

    diag = client.get("/api/system/diagnostics").json()
    sim_diag = diag.get("simulation", {})
    check("④ 诊断导出给出「真实 vs 模拟」对照",
          sim_diag.get("details", {}).get("real_tier") == real_tier_http
          and sim_diag.get("details", {}).get("active") is True, json.dumps(sim_diag, ensure_ascii=False)[:160])

    # —— 非法设置在 HTTP 层被拒，且不影响主链路（P3 不得阻塞）——
    r_bad = client.put("/api/settings", json={"items": [{"key": "force_tier", "value": "T9"}]})
    check("⑤ 非法档位 → 400 VALIDATION_ERROR",
          r_bad.status_code == 400
          and (r_bad.json().get("error") or {}).get("code") == "VALIDATION_ERROR",
          str(r_bad.status_code) + " " + r_bad.text[:120])
    check("⑤ 拒绝后主链路照常（capabilities / models 均为 200）",
          client.get("/api/system/capabilities").status_code == 200
          and client.get("/api/models").status_code == 200)
    check("⑤ 拒绝的非法值**未落库**",
          {i["key"]: i["value"] for i in client.get("/api/settings").json()}.get("force_tier") == "T2")

    # —— 关闭模拟 → 全面复原 ——
    client.put("/api/settings", json={"items": [
        {"key": "simulation_enabled", "value": "false"},
        {"key": "force_tier", "value": ""},
        {"key": "force_vram_mb", "value": ""},
    ]})
    caps3 = client.get("/api/system/capabilities").json()
    check("⑥ 关闭后档位复原", caps3["tier"] == real_tier_http, caps3["tier"])
    check("⑥ 关闭后 simulation.enabled 为假且无 force_tier",
          caps3["simulation"] == {"enabled": False, "force_tier": None}, str(caps3["simulation"]))
    m3 = _find()
    if real_vram is None:
        skip("⑥ 关闭后重新置灰", "本机探测不到可用显存")
    else:
        check("⑥ 关闭后 16G 模型重新置灰（无残留）",
              m3 is not None and m3["available"] is False, str(m3 and m3.get("unavailable_reason")))

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 / {SKIPPED} 跳过 =====")
sys.exit(1 if FAIL else 0)
