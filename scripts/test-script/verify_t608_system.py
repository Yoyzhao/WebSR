"""T-608 系统端点 + 诊断导出 + OpenAPI schema 验证。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t608_system.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t608_"))
DATA = TMP / "data"

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


from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import get_session  # noqa: E402
from app.models.entities import Calibration  # noqa: E402

with TestClient(app) as client:
    print("== 1. capabilities（保底占位形状）==")
    r = client.get("/api/system/capabilities")
    check("200", r.status_code == 200)
    caps = r.json()
    check("顶层字段齐全", set(caps) >= {
        "tier", "tier_label", "tier_reason", "device_facts", "verified_backends",
        "ep_evidence", "using_fallback", "active_backend", "active_precision", "simulation"})
    check("EP 未验证 → T0 + using_fallback", caps["tier"] == "T0" and caps["using_fallback"] is True)
    check("verified_backends 为空（不冒充已验证）", caps["verified_backends"] == [])
    df = caps["device_facts"]
    check("device_facts 字段齐全", set(df) >= {
        "cpu", "gpu", "driver", "system_ram_gb", "available_vram_gb", "nominal_vram_gb"})

    print("== 2. diagnostics 导出 ==")
    s = get_session()
    s.add(Calibration(model_id=None, hardware_fingerprint="t608-fp",
                      tile_curve={"256": {"ms": 10}}, precision_decision="fp32",
                      recommended_tier="T0", reason="测试记录", valid=True))
    s.commit()
    s.close()

    r = client.get("/api/system/diagnostics")
    check("200 且为附件下载", r.status_code == 200 and "attachment" in r.headers.get("content-disposition", ""))
    diag = r.json()
    check("诊断字段齐全（PRD §3.4）", set(diag) >= {
        "exported_at", "tier", "device_facts", "ep_evidence", "calibration"})
    check("标定记录出库", len(diag["calibration"]["records"]) == 1
          and diag["calibration"]["records"][0]["hardware_fingerprint"] == "t608-fp")
    check("calibration.state=pending（S2 前）", diag["calibration"]["state"] == "pending")
    check("summary 统计存在", "model_count" in diag.get("summary", {}))

    print("== 3. 统一错误体回归 ==")
    r = client.get("/api/nonexistent")
    check("未知路由 404 NOT_FOUND 三要素", r.status_code == 404
          and set(r.json()["error"]) >= {"code", "message", "suggestion"})

print("== 4. OpenAPI schema 导出（供 T-700）==")
env = dict(os.environ)
r = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "export_openapi.py")],
    capture_output=True, text=True, env=env, cwd=ROOT,
)
check("导出脚本执行成功", r.returncode == 0, f"{r.stdout} {r.stderr[:300]}")
schema_path = ROOT / "docs" / "tech" / "api" / "openapi.json"
check("schema 文件存在", schema_path.is_file())
if schema_path.is_file():
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    paths = set(schema.get("paths", {}))
    expected = {
        "/api/health", "/api/models", "/api/models/import", "/api/models/{model_id}/export",
        "/api/files/upload", "/api/files/{file_id}/content",
        "/api/tasks", "/api/tasks/{task_id}", "/api/tasks/{task_id}/cancel",
        "/api/tasks/{task_id}/artifacts", "/api/tasks/{task_id}/events",
        "/api/system/capabilities", "/api/system/diagnostics",
    }
    check("全部端点进入 schema", expected <= paths, f"missing={expected - paths}")
    check("openapi 版本字段存在", "openapi" in schema and "info" in schema)

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
