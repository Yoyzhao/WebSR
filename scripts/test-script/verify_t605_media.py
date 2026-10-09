"""T-605 媒体管理（M5）验证：上传二次校验 / 落盘 / 缩略图 / 内容获取。

隔离策略同 T-604：临时 APP_DATA_DIR / APP_DB_URL；上传上限调小（1MB）便于测 413。
运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t605_media.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t605_"))
DATA = TMP / "data"

os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"
os.environ["APP_MAX_UPLOAD_MB"] = "1"

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
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402


def make_image(fmt: str, size=(640, 480), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    img.save(buf, fmt)
    return buf.getvalue()


def upload(client, filename: str, payload: bytes):
    return client.post("/api/files/upload",
                       files={"file": (filename, io.BytesIO(payload), "application/octet-stream")})


PNG = make_image("PNG")
JPG = make_image("JPEG")

with TestClient(app) as client:
    print("== 1. 正常上传（PNG）==")
    r = upload(client, "photo.png", PNG)
    check("上传 → 201", r.status_code == 201, f"actual={r.status_code} {r.text[:200]}")
    body = r.json()
    check("字段齐全", set(body) == {"file_id", "filename", "size", "width", "height", "real_format"})
    check("尺寸真实", (body["width"], body["height"]) == (640, 480))
    check("real_format=png", body["real_format"] == "png")
    fid = body["file_id"]
    check("原图落盘 uploads/", (DATA / "uploads" / f"{fid}.png").is_file())
    check("缩略图落盘 thumbs/", (DATA / "thumbs" / f"{fid}.jpg").is_file())
    with Image.open(DATA / "thumbs" / f"{fid}.jpg") as t:
        check("缩略图最长边 ≤512", max(t.size) <= 512, f"actual={t.size}")

    print("== 2. 二次校验（不信任扩展名）==")
    r = upload(client, "fake.png", JPG)
    check("jpg 伪装 .png → 400 FORMAT_MISMATCH",
          r.status_code == 400 and r.json()["error"]["code"] == "FORMAT_MISMATCH")
    r = upload(client, "broken.png", b"not an image at all")
    check("非图片内容 → 400 FORMAT_MISMATCH",
          r.status_code == 400 and r.json()["error"]["code"] == "FORMAT_MISMATCH")
    r = upload(client, "anim.gif", make_image("GIF"))
    check("白名单外扩展名 → 400 UNSUPPORTED_FORMAT",
          r.status_code == 400 and r.json()["error"]["code"] == "UNSUPPORTED_FORMAT")

    print("== 3. 格式归一化 ==")
    for filename, payload, expect in [
        ("a.jpg", JPG, "jpg"), ("b.jpeg", JPG, "jpg"),
        ("c.webp", make_image("WEBP"), "webp"), ("d.bmp", make_image("BMP"), "bmp"),
        ("e.tif", make_image("TIFF"), "tif"),
    ]:
        r = upload(client, filename, payload)
        check(f"{filename} → real_format={expect}",
              r.status_code == 201 and r.json()["real_format"] == expect,
              f"actual={r.status_code} {r.text[:120]}")

    print("== 4. 大小上限 ==")
    big = make_image("PNG", size=(2000, 2000), color=(1, 2, 3))
    r = upload(client, "big.png", big + b"\x00" * (1024 * 1024))
    check("超限 → 413 FILE_TOO_LARGE",
          r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE")

    print("== 5. 内容获取 ==")
    r = client.get(f"/api/files/{fid}/content")
    check("original 字节一致", r.status_code == 200 and r.content == PNG)
    check("original Content-Type=image/png", r.headers.get("content-type") == "image/png")
    r = client.get(f"/api/files/{fid}/content", params={"variant": "thumb"})
    check("thumb → 200 image/jpeg", r.status_code == 200 and r.headers.get("content-type") == "image/jpeg")
    r = client.get(f"/api/files/{fid}/content", params={"variant": "result"})
    check("variant=result 暂不支持 → 400 VALIDATION_ERROR",
          r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR")
    r = client.get("/api/files/file_deadbeef99/content")
    check("不存在的 id → 404 NOT_FOUND",
          r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND")
    r = client.get("/api/files/..%2F..%2Fapp/content")
    check("目录逃逸 id → 404（不触发 500）", r.status_code == 404)

    print("== 6. 缩略图缺失时即时补生成 ==")
    (DATA / "thumbs" / f"{fid}.jpg").unlink()
    r = client.get(f"/api/files/{fid}/content", params={"variant": "thumb"})
    check("缺失后仍可 200（即时重建）", r.status_code == 200)
    check("缩略图已重建", (DATA / "thumbs" / f"{fid}.jpg").is_file())

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
