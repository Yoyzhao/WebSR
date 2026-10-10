"""T-604 模型库（M3）验证：内置登记 / 列表 / 导入 / 导出 / 删除 / 可用性门控。

隔离策略（与 T-603 相同）：临时目录构造独立 APP_DATA_DIR / APP_DB_URL，
内置模型用**小体积假文件**（登记只读哈希，不解析内容），避免依赖 200MB 真实模型。
运行方式：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t604_models.py
"""
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t604_"))
DATA = TMP / "data"
MODELS = DATA / "models"
(MODELS / "ir").mkdir(parents=True)

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


# ---- 构造小体积假内置模型（内容随意，登记只算哈希）----
def fake(path: Path, payload: bytes) -> bytes:
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


SHA_X4 = fake(MODELS / "RealESRGAN_x4.onnx", b"fake-x4-onnx" * 100)
fake(MODELS / "RealESRGAN_x4_fp16.onnx", b"fake-x4-fp16" * 100)
fake(MODELS / "ir" / "RealESRGAN_x4_fp16.xml", b"<xml/>" * 50)
fake(MODELS / "ir" / "RealESRGAN_x4_fp16.bin", b"ir-weights" * 100)
# 风格模型（2026-10-09 增补）：内容同样是假的——登记只算哈希，不解析网络结构
fake(MODELS / "RealESRGAN_x4plus_anime_6B.onnx", b"fake-anime-6b" * 100)
fake(MODELS / "realesr-general-x4v3.onnx", b"fake-general-x4v3" * 100)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

print("== 1. 启动 + 内置模型登记 ==")
with TestClient(app) as client:
    r = client.get("/api/models")
    check("GET /api/models → 200", r.status_code == 200)
    models = r.json()
    check("返回数组（非分页包装）", isinstance(models, list))
    check("内置 5 项已登记", len(models) == 5, f"actual={len(models)}")
    by_name = {m["name"]: m for m in models}
    x4 = by_name.get("RealESRGAN_x4plus", {})
    check("id 带 mdl_ 前缀", x4.get("id", "").startswith("mdl_"))
    check("sha256 与文件一致", x4.get("sha256") == SHA_X4)
    check("params_count 映射（DB param_count）", x4.get("params_count") == 16_697_987)
    check("capabilities 完整", set(x4.get("capabilities", {})) == {
        "supports_fp16", "supports_batch", "supports_tile0", "has_tensorrt",
        "is_generative", "num_inference_steps", "requires_prompt"})

    # 风格模型（2026-10-09 增补）：风格由「模型」区分，故元信息必须如实可辨
    anime = by_name.get("RealESRGAN_x4plus_anime_6B", {})
    check("动漫模型已登记（RRDBNet 6 块 / 4,467,779 参数）",
          anime.get("architecture") == "RRDBNet" and anime.get("params_count") == 4_467_779,
          f"actual={anime.get('architecture')}/{anime.get('params_count')}")
    check("动漫模型 scale=4 且为内置",
          anime.get("scale") == 4 and anime.get("source") == "builtin")
    general = by_name.get("realesr-general-x4v3", {})
    check("轻量通用模型已登记（SRVGGNetCompact / 1,213,296 参数）",
          general.get("architecture") == "SRVGGNetCompact"
          and general.get("params_count") == 1_213_296,
          f"actual={general.get('architecture')}/{general.get('params_count')}")
    check("轻量模型显存门槛低于主模型",
          (general.get("min_vram_mb") or 0) < (x4.get("min_vram_mb") or 0),
          f"general={general.get('min_vram_mb')} x4={x4.get('min_vram_mb')}")
    check("architecture 来自 catalog", x4.get("architecture") == "RRDBNet")
    ir = by_name.get("RealESRGAN_x4 (OpenVINO IR)", {})
    check("IR companion 为 ['.xml', '.bin']", ir.get("companion") == [".xml", ".bin"])
    # ⚠️ 可用性必须反映**本机能否跑**，而不是只看登记态：
    #    IR 需要 `openvino` 运行时，未装则必须置灰并给出安装指引
    #    （T-703 联调修复：此前只看 status/显存，缺运行时的模型仍被判"可用"）。
    import importlib.util as _ilu

    _ov = _ilu.find_spec("openvino") is not None
    check("IR status=ready", ir.get("status") == "ready")
    check("IR 可用性与本机 openvino 运行时一致",
          ir.get("available") is _ov,
          f"available={ir.get('available')} · openvino={'已装' if _ov else '未装'}")
    if not _ov:
        check("IR 置灰原因给出安装指引（含 openvino）",
              "openvino" in (ir.get("unavailable_reason") or ""),
              str(ir.get("unavailable_reason"))[:90])
    x4_id = x4.get("id")

    print("== 2. 导入 .onnx ==")
    onnx_bytes = b"user-onnx-model" * 200
    onnx_sha = hashlib.sha256(onnx_bytes).hexdigest()
    r = client.post("/api/models/import", data={
        "name": "MySR_x4", "format": "onnx", "scale": "4",
        "min_vram_mb": "1024", "backends": json.dumps(["cuda", "cpu"]),
    }, files={"file": ("MySR_x4.onnx", io.BytesIO(onnx_bytes), "application/octet-stream")})
    check("导入 .onnx → 201", r.status_code == 201, f"actual={r.status_code} {r.text[:200]}")
    imported = r.json()
    check("导入模型 sha256 正确", imported.get("sha256") == onnx_sha)
    check("source=imported / status=ready", imported.get("source") == "imported" and imported.get("status") == "ready")
    check("落盘到 imported/ 子目录", "data/models/imported/" in imported.get("path", ""))
    check("文件真实落盘", (MODELS / "imported" / "MySR_x4.onnx").is_file())
    imported_id = imported.get("id")

    print("== 3. 重复导入去重 ==")
    r = client.post("/api/models/import", data={
        "name": "Dup", "format": "onnx", "scale": "4",
        "min_vram_mb": "1024", "backends": json.dumps(["cpu"]),
    }, files={"file": ("copy.onnx", io.BytesIO(onnx_bytes), "application/octet-stream")})
    body = r.json().get("error", {})
    check("重复导入 → 409 MODEL_ALREADY_EXISTS", r.status_code == 409 and body.get("code") == "MODEL_ALREADY_EXISTS")
    check("错误体三要素齐全", all(k in body for k in ("code", "message", "suggestion")))

    print("== 4. .pth 登记为 needs_convert ==")
    r = client.post("/api/models/import", data={
        "name": "RawPTH", "format": "pth", "scale": "4",
        "min_vram_mb": "512", "backends": json.dumps(["cpu"]),
    }, files={"file": ("raw.pth", io.BytesIO(b"torch-weights"), "application/octet-stream")})
    check("导入 .pth → 201", r.status_code == 201)
    pth = r.json()
    check("status=needs_convert 且不可用", pth.get("status") == "needs_convert" and pth.get("available") is False)
    check("原因文案指向离线转换", "离线转换" in (pth.get("unavailable_reason") or ""))

    print("== 5. ADR-003 格式路由校验 ==")
    r = client.post("/api/models/import", data={
        "name": "LoneBin", "format": "ncnn", "scale": "4",
        "min_vram_mb": "0", "backends": json.dumps(["ncnn"]),
    }, files={"file": ("weights.bin", io.BytesIO(b"bin"), "application/octet-stream")})
    check("主文件为 .bin → 400 MODEL_MISSING_COMPANION",
          r.status_code == 400 and r.json()["error"]["code"] == "MODEL_MISSING_COMPANION")

    r = client.post("/api/models/import", data={
        "name": "IRnoBin", "format": "openvino_ir", "scale": "4",
        "min_vram_mb": "0", "backends": json.dumps(["openvino"]),
    }, files={"file": ("model.xml", io.BytesIO(b"<xml/>"), "application/octet-stream")})
    check("IR 缺配套 .bin → 400 MODEL_MISSING_COMPANION",
          r.status_code == 400 and r.json()["error"]["code"] == "MODEL_MISSING_COMPANION")

    r = client.post("/api/models/import", data={
        "name": "WrongExt", "format": "onnx", "scale": "4",
        "min_vram_mb": "0", "backends": json.dumps(["cpu"]),
    }, files={"file": ("m.pth", io.BytesIO(b"x"), "application/octet-stream")})
    check("扩展名与格式不符 → 400 UNSUPPORTED_FORMAT",
          r.status_code == 400 and r.json()["error"]["code"] == "UNSUPPORTED_FORMAT")

    r = client.post("/api/models/import", data={
        "name": "MyIR", "format": "openvino_ir", "scale": "4",
        "min_vram_mb": "0", "backends": json.dumps(["openvino", "cpu"]),
    }, files={
        "file": ("my_ir.xml", io.BytesIO(b"<myxml/>"), "application/octet-stream"),
        "companion_file": ("my_ir.bin", io.BytesIO(b"my-weights"), "application/octet-stream"),
    })
    check("IR (.xml+.bin) → 201 ready", r.status_code == 201 and r.json().get("status") == "ready")
    check("companion=['.xml','.bin']", r.json().get("companion") == [".xml", ".bin"])

    print("== 6. 导出 / 删除 ==")
    r = client.get(f"/api/models/{imported_id}/export")
    check("导出 → 200 且内容一致", r.status_code == 200 and r.content == onnx_bytes)
    r = client.get("/api/models/mdl_99999/export")
    check("导出不存在的模型 → 404 MODEL_NOT_FOUND",
          r.status_code == 404 and r.json()["error"]["code"] == "MODEL_NOT_FOUND")

    r = client.delete(f"/api/models/{x4_id}")
    check("删除内置模型 → 409 MODEL_BUILTIN_READONLY",
          r.status_code == 409 and r.json()["error"]["code"] == "MODEL_BUILTIN_READONLY")
    r = client.delete(f"/api/models/{imported_id}")
    check("删除导入模型 → 204", r.status_code == 204)
    names = [m["name"] for m in client.get("/api/models").json()]
    check("删除后列表不再包含", "MySR_x4" not in names)
    check("删除登记不删磁盘文件", (MODELS / "imported" / "MySR_x4.onnx").is_file())

    print("== 7. 筛选 ==")
    r = client.get("/api/models", params={"format": "onnx"})
    check("?format=onnx 只剩 onnx 项", all(m["format"] == "onnx" for m in r.json()))
    r = client.get("/api/models", params={"format": "bogus"})
    check("非法筛选值 → 400 VALIDATION_ERROR",
          r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR")

print("== 8. 重启幂等（再进一次 lifespan）==")
with TestClient(app) as client2:
    models2 = client2.get("/api/models").json()
    check("重启后内置不重复登记", len([m for m in models2 if m["source"] == "builtin"]) == 5)

print("== 9. 可用性门控单元断言 ==")
from app.engine.availability import HardwareSnapshot, gate_availability  # noqa: E402

ok, reason = gate_availability(status="ready", min_vram_mb=24_576,
                               snapshot=HardwareSnapshot(available_vram_mb=7_600))
check("需求超过可用显存 → 置灰", ok is False and "显存" in (reason or ""))
ok, _ = gate_availability(status="ready", min_vram_mb=4_096,
                          snapshot=HardwareSnapshot(available_vram_mb=7_600))
check("需求低于可用显存 → 可用", ok is True)
ok, _ = gate_availability(status="ready", min_vram_mb=999_999,
                          snapshot=HardwareSnapshot(available_vram_mb=None))
check("探测不到显存 → 不门控", ok is True)

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
