"""T-609 M7 系统配置（F-07）验证：配置读写 / 校验 / 生效性 / 保留策略清理。

隔离策略（与 T-603~T-606 相同）：临时目录构造独立 APP_DATA_DIR / APP_DB_URL。
重点不是"接口能返回 200"，而是**配置是否真的生效**：
  - max_upload_mb 改了之后上传校验立刻按新值拦截；
  - data_root 改了之后新上传落到新根；
  - log_level 改了之后 root logger 级别立刻变；
  - 保留策略只删超期**终态记录**，磁盘产出文件与运行中任务不受影响。

运行方式：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t609_settings.py
"""
import io
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t609_"))
DATA = TMP / "data"
(DATA / "models").mkdir(parents=True)

os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"
os.environ["APP_CORS_ORIGINS"] = "http://127.0.0.1:5173"

sys.path.insert(0, str(ROOT / "server"))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def png_bytes(width: int, height: int, noise: bool = False) -> bytes:
    if noise:
        img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    else:
        img = Image.new("RGB", (width, height), (120, 140, 160))
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1 if noise else 6)
    return buf.getvalue()


EXPECTED_KEYS = {
    "data_root", "model_dir", "task_retention_days", "max_upload_mb",
    "max_concurrency", "log_level", "simulation_enabled", "calibration_state",
}

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import settings_store  # noqa: E402

DEFAULT_ROOT = get_settings().data_dir  # 默认值（形如 "./data"，被测试环境改成了绝对路径）

print("== 1. GET /api/settings（默认值合并）==")
with TestClient(app) as client:
    r = client.get("/api/settings")
    check("200", r.status_code == 200)
    items = r.json()
    check("响应为裸数组（与前端已定稿类型一致）", isinstance(items, list))
    by_key = {i["key"]: i for i in items}
    check("键集合完整（8 项）", set(by_key) == EXPECTED_KEYS, f"actual={sorted(by_key)}")
    check("顺序固定（声明顺序，非隐式）", [i["key"] for i in items][0] == "data_root")
    check("type 字段齐全", all(i["type"] in ("string", "number", "boolean") for i in items))
    check("max_concurrency 默认 1（保底档）", by_key["max_concurrency"]["value"] == "1")
    check("calibration_state 默认 pending（S2 前）", by_key["calibration_state"]["value"] == "pending")
    check("model_dir 由数据根派生", by_key["model_dir"]["value"].endswith("/models")
          or by_key["model_dir"]["value"].endswith("\\models"),
          f"actual={by_key['model_dir']['value']}")
    check("data_root 默认 = APP_DATA_DIR", by_key["data_root"]["value"] == DEFAULT_ROOT)
    # 默认值随 .env.dev 变化（如 APP_LOG_LEVEL=debug），断言一律「与初始值比对」而非硬编码
    defaults = {i["key"]: i["value"] for i in items}
    alt_level = next(l for l in ("warning", "info", "error") if l != defaults["log_level"])

    print("== 2. PUT 校验（非法值必须被拦，不静默落库）==")
    bad_cases = [
        ({"key": "max_concurrency", "value": "99"}, "并发越界"),
        ({"key": "max_concurrency", "value": "abc"}, "并发非整数"),
        ({"key": "task_retention_days", "value": "0"}, "保留天数下界"),
        ({"key": "log_level", "value": "trace"}, "日志级别枚举"),
        ({"key": "data_root", "value": ""}, "数据根为空"),
        ({"key": "not_a_key", "value": "x"}, "未知配置项"),
    ]
    for payload, label in bad_cases:
        rr = client.put("/api/settings", json={"items": [payload]})
        body = rr.json()
        ok = rr.status_code == 400 and body.get("error", {}).get("code") in (
            "VALIDATION_ERROR", "INTERNAL_ERROR") and body.get("error", {}).get("suggestion")
        check(f"拒绝非法配置（{label}）", bool(ok), f"status={rr.status_code} body={body}")

    after_bad = {i["key"]: i["value"] for i in client.get("/api/settings").json()}
    check("非法请求未污染已有配置（6 次拒绝后配置与初始值完全一致）",
          after_bad == defaults, f"diff={ {k: (defaults[k], v) for k, v in after_bad.items() if defaults[k] != v} }")

    print("== 3. PUT 落库 + 「保存默认值即删覆盖行」==")
    r = client.put("/api/settings", json={"items": [
        {"key": "task_retention_days", "value": "7"},
        {"key": "log_level", "value": alt_level},
    ]})
    check("200 且返回更新后清单", r.status_code == 200 and isinstance(r.json(), list))
    now = {i["key"]: i["value"] for i in r.json()}
    check("task_retention_days = 7", now["task_retention_days"] == "7")
    check(f"log_level = {alt_level}", now["log_level"] == alt_level)
    from app.db import get_session  # noqa: E402
    from app.models.entities import Setting  # noqa: E402
    s = get_session()
    keys_in_db = {x.key for x in s.query(Setting).all()}
    s.close()
    check("只落显式改动的键（无冗余覆盖行）", keys_in_db == {"task_retention_days", "log_level"},
          f"actual={sorted(keys_in_db)}")
    check("log_level 立即生效（root logger）",
          logging.getLogger().level == getattr(logging, alt_level.upper()),
          f"actual={logging.getLogger().level}")

    r = client.put("/api/settings", json={"items": [
        {"key": "task_retention_days", "value": defaults["task_retention_days"]},
        {"key": "log_level", "value": defaults["log_level"]},
    ]})
    s = get_session()
    keys_in_db = {x.key for x in s.query(Setting).all()}
    s.close()
    check("保存默认值 → 覆盖行被删除（等价恢复默认）", keys_in_db == set(),
          f"actual={sorted(keys_in_db)}")

    print("== 4. 裸数组请求体兼容 ==")
    r = client.put("/api/settings", json=[{"key": "simulation_enabled", "value": "true"}])
    check("裸数组被接受", r.status_code == 200, f"status={r.status_code}")
    check("simulation_enabled=true 已生效于清单",
          {i["key"]: i["value"] for i in r.json()}["simulation_enabled"] == "true")
    client.put("/api/settings", json={"items": [{"key": "simulation_enabled", "value": "false"}]})

    print("== 5. 只读派生键：写入被忽略而非报错 ==")
    r = client.put("/api/settings", json={"items": [
        {"key": "model_dir", "value": "D:/somewhere/else"},
        {"key": "calibration_state", "value": "done"},
    ]})
    check("只读键写入不报错（前端整表提交不被破坏）", r.status_code == 200, f"status={r.status_code}")
    check("model_dir 未被篡改（仍由数据根派生）",
          settings_store.effective_model_dir() == settings_store.effective_data_root() / "models")
    check("calibration_state 未被篡改", {i["key"]: i["value"] for i in r.json()}["calibration_state"] == "pending")

    print("== 6. max_upload_mb 真实生效（改 1MB → 1.2MB 上传被 413 拦截）==")
    big = png_bytes(760, 760, noise=True)
    check("构造的测试图 > 1MB", len(big) > 1024 * 1024, f"size={len(big)}")
    r = client.post("/api/files/upload", files={"file": ("big.png", big, "image/png")})
    check("默认 50MB 上限下可上传", r.status_code == 201, f"status={r.status_code}")

    client.put("/api/settings", json={"items": [{"key": "max_upload_mb", "value": "1"}]})
    r = client.post("/api/files/upload", files={"file": ("big.png", big, "image/png")})
    body = r.json()
    check("改小上限后立即被拦（配置真实生效）", r.status_code == 413, f"status={r.status_code}")
    check("错误体三要素 + 提示含新上限",
          body.get("error", {}).get("code") == "FILE_TOO_LARGE" and "1 MB" in body["error"]["message"],
          f"body={body}")
    small = png_bytes(64, 64)
    r = client.post("/api/files/upload", files={"file": ("small.png", small, "image/png")})
    check("小文件仍可上传", r.status_code == 201, f"status={r.status_code}")
    client.put("/api/settings", json={"items": [{"key": "max_upload_mb", "value": "50"}]})

    print("== 7. data_root 改根 → 新上传落新根（不迁移既有文件）==")
    new_root = TMP / "data2"
    r = client.put("/api/settings", json={"items": [{"key": "data_root", "value": str(new_root)}]})
    check("改数据根成功", r.status_code == 200, f"status={r.status_code} body={r.json()}")
    check("子目录已建齐（models/uploads/outputs/thumbs/calibration）",
          all((new_root / d).is_dir() for d in
              ("models", "uploads", "outputs", "thumbs", "calibration")),
          f"actual={sorted(p.name for p in new_root.iterdir())}")
    check("effective_data_root 跟随", settings_store.effective_data_root() == new_root)
    r = client.post("/api/files/upload", files={"file": ("s.png", small, "image/png")})
    new_id = r.json().get("file_id", "")
    check("新上传落在新根 uploads/",
          r.status_code == 201 and (new_root / "uploads" / f"{new_id}.png").is_file(),
          f"id={new_id}")
    check("旧根未被迁移（既有上传仍在原处）", (DATA / "uploads").is_dir())
    r = client.put("/api/settings", json={"items": [{"key": "data_root", "value": DEFAULT_ROOT}]})
    check("改回默认数据根", r.status_code == 200 and settings_store.effective_data_root() == DATA)

    print("== 8. 保留策略清理：只删超期终态记录，文件与运行中任务不动 ==")
    from app.models.entities import Artifact, Task  # noqa: E402
    out_file = DATA / "outputs" / "kept_output.png"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(b"fake-output")
    now_utc = datetime.now(timezone.utc)
    s = get_session()
    old_done = Task(type="upscale", status="completed", params={}, tier="T0",
                    created_at=now_utc - timedelta(days=40))
    old_running = Task(type="upscale", status="running", params={}, tier="T0",
                       created_at=now_utc - timedelta(days=40))
    recent_done = Task(type="upscale", status="completed", params={}, tier="T0",
                       created_at=now_utc - timedelta(days=1))
    s.add_all([old_done, old_running, recent_done])
    s.commit()
    s.add(Artifact(task_id=old_done.id, kind="output", path="outputs/kept_output.png",
                   size_bytes=11, sha256="x" * 64))
    s.commit()
    ids = {"old_done": old_done.id, "old_running": old_running.id, "recent_done": recent_done.id}
    s.close()

    cleaned = settings_store.cleanup_expired_tasks(int(defaults["task_retention_days"]))
    check("清理 1 条超期终态记录", cleaned == 1, f"actual={cleaned}")
    s = get_session()
    left = {t.id for t in s.query(Task).all()}
    art_left = {a.task_id for a in s.query(Artifact).all()}
    s.close()
    check("超期完成态记录已删", ids["old_done"] not in left)
    check("超期运行中记录保留（绝不清理非终态）", ids["old_running"] in left)
    check("未超期记录保留", ids["recent_done"] in left)
    check("产物记录行随任务连带删除", ids["old_done"] not in art_left)
    check("磁盘产出文件不受影响（PRD F-07）", out_file.is_file())
    check("再次清理幂等（0 条）", settings_store.cleanup_expired_tasks() == 0)

    print("== 9. 诊断导出含启动序列清理结果（T-608 回归）==")
    r = client.get("/api/system/diagnostics")
    check("诊断仍可导出", r.status_code == 200 and "attachment" in r.headers.get("content-disposition", ""))
    check("配置项未破坏能力端点", client.get("/api/system/capabilities").status_code == 200)

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
