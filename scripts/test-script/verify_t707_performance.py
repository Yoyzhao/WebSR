"""T-707 性能与资源基线验证（PRD §3.1 三项 · 真实推理路径）。

用法（需先启动后端）：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t707_performance.py

覆盖（严格对照 PRD §3.1 原文，不自拟阈值）：
  1  任务提交响应 P95 ≤ 300 ms（不含推理；提交即返回）
  2  进度事件间隔：节流 ≤ 1 s（不洪泛）且**不饥饿**（新鲜度）
     —— 事件只在块 report() 到来时才可能发出，故新鲜度上界 = 节流(1.0s) + 单块平均耗时（自指判据）
  3  峰值显存/内存 < 标定安全线（可用量的 80%）
     —— WDDM 拿不到按进程显存（P0 报告 §3.3）→ 显存用**设备级相对基线的增量**口径：
        任务自身消耗（峰值 used − 提交前基线 used）≤ 启动时可用量 × 80%
     —— 内存用**后端进程 RSS**（可按进程）：峰值 RSS ≤ 启动时系统可用量 × 80%

设计纪律（与 T-706 一致）：
  - 不硬编码任何本机实测数值；模型、tile、块数全部从接口推导；
  - 优先相对/自指判据；不可判定项如实 SKIP。
"""
import ctypes
import csv
import io
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8000/api"
FAIL, PASS, SKIP = [], 0, []

SUBMIT_DEADLINE_S = 0.300   # PRD §3.1：P95 ≤ 300 ms
PEAK_RATIO_LIMIT = 0.80     # PRD §3.1：峰值 < 可用量 80%（标定安全线）
N_SUBMITS = 12              # P95 样本数


def base_from_argv() -> None:
    global BASE
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--base" and i + 1 < len(args):
            BASE = args[i + 1].rstrip("/") + "/api"
        elif a.startswith("--base="):
            BASE = a.split("=", 1)[1].rstrip("/") + "/api"


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
    else:
        FAIL.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {extra}" if extra else ""))


def skip(label: str, why: str) -> None:
    SKIP.append(f"{label}（{why}）")
    print(f"  [SKIP] {label} — {why}")


def section(title: str) -> None:
    print(f"\n== {title} ==")


def http(method, path, data=None, headers=None, raw=False, timeout=180):
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (body if raw else json.loads(body.decode() or "null"))
    except urllib.error.HTTPError as e:
        raw_body = e.read()
        try:
            return e.code, json.loads(raw_body.decode() or "null")
        except Exception:
            return e.code, raw_body


def upload(img: Path):
    boundary = "----WebSR" + uuid.uuid4().hex
    b = boundary.encode()
    body = b"".join([
        b"--", b, b"\r\n",
        f'Content-Disposition: form-data; name="file"; filename="{img.name}"\r\n'.encode(),
        b"Content-Type: image/png\r\n\r\n", img.read_bytes(), b"\r\n", b"--", b, b"--\r\n",
    ])
    return http("POST", "/files/upload", body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def make_image(path: Path, w: int, h: int) -> None:
    """固定内容的高频纹理图（内容稳定，便于复现）。"""
    if path.is_file():
        return
    import numpy as np
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    arr[..., 0] = ((xx // 8 + yy // 8) % 2 * 200).astype(np.uint8)
    arr[..., 1] = ((xx * 5 + yy * 3) % 256).astype(np.uint8)
    arr[..., 2] = ((xx * 7 + yy * 11) % 256).astype(np.uint8)
    Image.fromarray(arr).save(path)


def pick_model():
    """从接口推导：ready 且 available 且 scale=4 的模型（不硬编码）。"""
    _, models = http("GET", "/models")
    for m in models if isinstance(models, list) else []:
        if m.get("status") == "ready" and m.get("available") and m.get("scale") == 4:
            return m["id"], m.get("name", m["id"])
    return None, None


def create_task(file_id, model_id):
    return http("POST", "/tasks", json.dumps({
        "type": "upscale", "file_id": file_id,
        "params": {"scale": 4, "model_id": model_id, "tile": None,
                   "precision": None, "backend": None, "auto": True}}).encode(),
        {"Content-Type": "application/json"})


def wait_terminal(task_id, timeout_s=180):
    deadline = time.time() + timeout_s
    final = None
    while time.time() < deadline:
        st, t = http("GET", f"/tasks/{task_id}")
        if st == 200:
            final = t
            if t.get("status") in ("completed", "failed", "canceled", "interrupted"):
                return final
        time.sleep(0.2)
    return final


def sse_collect(task_id, sink, stop, t0):
    """后台线程读 SSE，收集 (event, payload, 相对到达秒)。与 t702 同口径。"""
    try:
        resp = urllib.request.urlopen(f"{BASE}/tasks/{task_id}/events", timeout=180)
    except Exception as e:  # noqa: BLE001
        sink.append(("__error__", {"message": str(e)}, round(time.monotonic() - t0, 3)))
        return
    event, buf = None, []
    try:
        for line in resp:
            if stop.is_set():
                break
            s = line.decode().rstrip("\r\n")
            if s == "":
                if event is not None:
                    try:
                        payload = json.loads("".join(buf)) if buf else {}
                    except Exception:
                        payload = {"__raw__": "".join(buf)}
                    sink.append((event, payload, round(time.monotonic() - t0, 3)))
                event, buf = None, []
            elif s.startswith("event:"):
                event = s[6:].strip()
            elif s.startswith("data:"):
                buf.append(s[5:].strip())
    finally:
        resp.close()


# ---- 资源采样（零第三方依赖；WDDM 口径） ------------------------------------

class _MEMSTAT(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def system_available_ram_mb() -> int | None:
    """与 engine/device_probe._system_memory_mb 同一实现（同一口径）。"""
    if sys.platform != "win32":
        return None
    try:
        stat = _MEMSTAT()
        stat.dwLength = ctypes.sizeof(_MEMSTAT)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return int(stat.ullAvailPhys / (1024 * 1024))
    except Exception:
        return None


def _nvidia_smi_used_mb():
    try:
        where = subprocess.run(["where", "nvidia-smi"], capture_output=True,
                               text=True, encoding="mbcs", errors="replace", timeout=15)
        exe = where.stdout.strip().splitlines()[0] if where.stdout.strip() else None
    except Exception:
        exe = None
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=True,
        ).stdout.strip().splitlines()[0]
        total, used = (float(x.strip()) for x in out.split(",")[:2])
        return total, used
    except Exception:
        return None


def _backend_pid():
    """通过 8000 端口监听反查后端 PID（netstat，零依赖）。"""
    try:
        # 中文 Windows 的 netstat 输出是 ANSI（GBK）码页 → 用 mbcs 解码
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, text=True, encoding="mbcs",
                             errors="replace", timeout=15).stdout
    except Exception:
        return None
    if not out:
        return None
    port = BASE.split(":")[-1].split("/")[0]
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts:
                try:
                    return int(parts[-1])
                except ValueError:
                    continue
    return None


def _rss_mb(pid: int) -> float | None:
    """后端进程工作集（MB）。tasklist CSV 的"内存使用"即 working set。"""
    try:
        out = subprocess.run(
            ["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
            timeout=15).stdout.strip()
        rows = list(csv.reader(io.StringIO(out)))
        if not rows or len(rows[0]) < 5:
            return None
        mem = rows[0][4].replace(",", "").replace(" K", "").replace("K", "").strip()
        return float(mem) / 1024.0
    except Exception:
        return None


class ResourceSampler:
    """后台采样线程：VRAM（设备级）+ 后端 RSS。100~200ms 粒度足以覆盖
    推理期显存平台期（arena 分配在推理期间持续持有）。"""

    def __init__(self, interval_s: float = 0.15):
        self.interval = interval_s
        self.samples: list[tuple[float, float | None, float | None]] = []  # (t, vram_used_mb, rss_mb)
        self.pid = _backend_pid()
        self._stop = threading.Event()
        self._th: threading.Thread | None = None

    def start(self):
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def _run(self):
        while not self._stop.is_set():
            t = time.monotonic()
            smi = _nvidia_smi_used_mb()
            used = smi[1] if smi else None
            rss = _rss_mb(self.pid) if self.pid else None
            self.samples.append((t, used, rss))
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._th:
            self._th.join(timeout=3)


# ---------------------------------------------------------------- 主流程 ----

def main() -> None:
    base_from_argv()
    print("== T-707 性能与资源基线（PRD §3.1 · 真实推理路径）==")

    # ---- [0] 可达性 + 前置 ----
    section("[0] 可达性与前置")
    st, _ = http("GET", "/models")
    check("后端可达", st == 200, f"GET /models -> {st}")
    model_id, model_name = pick_model()
    check("存在 ready+available+scale=4 的模型（从接口推导）", bool(model_id),
          f"{model_id} {model_name}")
    if not model_id:
        print("===== 无可用模型，终止 =====")
        return

    tmp = ROOT / ".workbuddy" / "tmp"
    img_small = tmp / "t707-small.png"    # P95 提交样本：单块、快
    img_sse = tmp / "t707-sse.png"        # 节流样本：多块
    img_peak = tmp / "t707-peak.png"      # 峰值样本：更大、更长
    make_image(img_small, 96, 96)
    make_image(img_sse, 800, 600)
    make_image(img_peak, 1280, 960)

    # 排空队列（并发 1：有 running 任务时提交会 409，污染 P95）
    _, tasks = http("GET", "/tasks")
    running = [t for t in (tasks or []) if t.get("status") in ("running", "queued", "canceling")]
    if running:
        skip("[0] 队列排空", f"存在 {len(running)} 个未终态任务，P95 样本将混入 409 —— 请先排空")
    else:
        check("[0] 队列空闲（P95 不被 409 污染）", True)

    # ---- [1] 提交 P95 ≤ 300 ms ----
    section(f"[1] 任务提交响应 P95 ≤ {SUBMIT_DEADLINE_S*1000:.0f} ms（{N_SUBMITS} 次串行真实提交）")
    st, up = upload(img_small)
    check("上传夹具", st in (200, 201) and isinstance(up, dict) and up.get("file_id"),
          f"{up.get('file_id') if isinstance(up, dict) else up}")
    file_id = up["file_id"] if isinstance(up, dict) else None

    latencies = []
    accepted = 0
    if file_id:
        for i in range(N_SUBMITS):
            t0 = time.monotonic()
            st, task = create_task(file_id, model_id)
            dt = time.monotonic() - t0
            latencies.append(dt)  # 拒绝也算一次提交响应（如实统计）
            if st in (200, 201):
                accepted += 1
                wait_terminal(task["id"])
            else:
                print(f"    第 {i+1} 次提交：HTTP {st}（{dt*1000:.0f} ms）")
    lat_sorted = sorted(latencies)
    # P95：取上 95 分位（样本数 12 → 第 12 个 = 最大值；样本内插不引入乐观偏差）
    idx95 = max(0, int(len(lat_sorted) * 0.95 + 0.999) - 1)
    p95 = lat_sorted[idx95] if lat_sorted else None
    check(f"提交响应 P95 ≤ {SUBMIT_DEADLINE_S*1000:.0f} ms",
          p95 is not None and p95 <= SUBMIT_DEADLINE_S,
          f"P95 {p95*1000:.1f} ms（中位 {lat_sorted[len(lat_sorted)//2]*1000:.1f} ms / "
          f"最大 {lat_sorted[-1]*1000:.1f} ms / n={len(lat_sorted)}）" if lat_sorted else "无样本")
    check(f"全部 {N_SUBMITS} 次提交都被受理（无 409/5xx 混入）", accepted == N_SUBMITS,
          f"受理 {accepted}/{N_SUBMITS}")

    # ---- [2] 进度事件：节流 ≤1s 且不饥饿 ----
    section("[2] 进度事件间隔（SSE 节流 ≤1s + 新鲜度，多块真实任务）")
    st, up = upload(img_sse)
    file_id2 = up["file_id"] if isinstance(up, dict) and st in (200, 201) else None
    frames = []
    if file_id2:
        st, task = create_task(file_id2, model_id)
        check("SSE 用例建任务", st in (200, 201), str(task.get("id") if isinstance(task, dict) else task))
        stop, t0 = threading.Event(), time.monotonic()
        th = threading.Thread(target=sse_collect, args=(task["id"], frames, stop, t0), daemon=True)
        th.start()
        final = wait_terminal(task["id"])
        time.sleep(0.8)
        stop.set()
        th.join(timeout=3)
    prog_ts = [f[2] for f in frames if f[0] == "progress"]
    done_ts = [f[2] for f in frames if f[0] == "done"]
    check("SSE 收到 progress 帧", len(prog_ts) >= 2, f"{len(prog_ts)} 帧")
    if len(prog_ts) >= 2 and final:
        active_s = (done_ts[0] if done_ts else prog_ts[-1]) - prog_ts[0]
        gaps = [b - a for a, b in zip(prog_ts, prog_ts[1:])]
        max_gap = max(gaps) if gaps else 0.0
        # 终态的 progress.total_chunks 会清零 → 从**最后一条 progress 帧**取块数
        prog_payloads = [f[1] for f in frames if f[0] == "progress" and isinstance(f[1], dict)]
        total_chunks = next(
            (p.get("total_chunks") for p in reversed(prog_payloads)
             if p.get("total_chunks")), 0)
        # 自指新鲜度上界：事件只在块 report() 到来时可发 → 间隔上界 = 节流 + 单块平均耗时
        duration_ms = final.get("duration_ms")
        chunk_avg = (duration_ms / 1000.0 / total_chunks) if (duration_ms and total_chunks) else None
        freshness_limit = 1.0 + (chunk_avg if chunk_avg else 0.5)
        check("进度新鲜度：活跃期相邻 progress 最大间隔 ≤ 节流(1.0s) + 单块平均耗时（自指）",
              max_gap <= freshness_limit,
              f"最大间隔 {max_gap:.3f} s ≤ {freshness_limit:.3f} s"
              f"（单块均 {chunk_avg*1000:.0f} ms × {total_chunks} 块，活跃 {active_s:.1f} s）"
              if chunk_avg else f"最大间隔 {max_gap:.3f} s（无块数信息，用 0.5s 余量）")
        # 节流上限（不洪泛）：除首末必发外每秒至多 1 条
        count_cap = max(active_s, float(total_chunks)) + 4
        check("节流确实生效：progress 帧数 ≤ max(活跃秒数, 块数) + 首末余量（不洪泛）",
              len(prog_ts) <= count_cap,
              f"{len(prog_ts)} 帧 ≤ 上限 {count_cap:.0f}（活跃 {active_s:.1f} s / {total_chunks} 块）")
        rate = len(prog_ts) / active_s if active_s > 0 else 0
        print(f"    参考：事件速率 {rate:.2f} 帧/s")
    elif not frames:
        skip("[2] SSE 帧收集", "未收到任何 SSE 帧（连接失败或任务过快）")

    # ---- [3] 峰值 < 可用量 80% ----
    section(f"[3] 峰值资源 < 可用量 {PEAK_RATIO_LIMIT:.0%}（WDDM 设备级增量 / 进程 RSS 口径）")
    st, up = upload(img_peak)
    file_id3 = up["file_id"] if isinstance(up, dict) and st in (200, 201) else None
    if not file_id3:
        skip("[3] 峰值资源", "上传失败")
        print(f"\n===== 结果：{PASS} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过 =====")
        return

    smi0 = _nvidia_smi_used_mb()
    ram0 = system_available_ram_mb()
    pid = _backend_pid()
    if smi0 is None:
        skip("[3] VRAM 峰值", "nvidia-smi 不可用")
    if ram0 is None:
        skip("[3] RAM 峰值", "系统内存读取不可用")
    if pid is None:
        skip("[3] 后端 RSS", "未找到 8000 端口监听进程")
    if smi0 is None or ram0 is None or pid is None:
        print(f"\n===== 结果：{PASS} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过 =====")
        return

    vram_total_mb, vram_base_mb = smi0
    ram_base_mb = ram0
    print(f"    基线：VRAM used {vram_base_mb:.0f}/{vram_total_mb:.0f} MB ｜ "
          f"系统可用 RAM {ram_base_mb:.0f} MB ｜ 后端 PID {pid}")

    sampler = ResourceSampler()
    sampler.start()
    t0 = time.monotonic()
    st, task = create_task(file_id3, model_id)
    final = wait_terminal(task["id"]) if st in (200, 201) else None
    task_s = time.monotonic() - t0
    time.sleep(0.5)   # 收尾峰值（落盘/清理期）
    sampler.stop()

    status = final.get("status") if final else "?"
    check("峰值样本任务真实完成", status == "completed", f"status={status}，耗时 {task_s:.1f} s")

    task_samples = [s for s in sampler.samples if s[0] >= t0]
    vram_used = [s[1] for s in task_samples if s[1] is not None]
    rss_vals = [s[2] for s in task_samples if s[2] is not None]
    check("采样点充足（覆盖整个任务窗口）", len(task_samples) >= 5,
          f"{len(task_samples)} 个采样点 / {task_s:.1f} s")

    if vram_used:
        peak_vram = max(vram_used)
        avail_at_start = vram_total_mb - vram_base_mb
        consumption = peak_vram - vram_base_mb
        limit = avail_at_start * PEAK_RATIO_LIMIT
        check(f"VRAM：任务自身消耗（峰值−基线）≤ 启动时可用量 {PEAK_RATIO_LIMIT:.0%}",
              consumption <= limit,
              f"消耗 {consumption:.0f} MB ≤ {limit:.0f} MB"
              f"（峰值 used {peak_vram:.0f} − 基线 {vram_base_mb:.0f}；"
              f"启动时可用 {avail_at_start:.0f} MB / 总 {vram_total_mb:.0f} MB）"
              f" —— WDDM 设备级口径：桌面占用计入基线，不冤枉任务")
    else:
        skip("[3] VRAM 峰值", "采样窗口内无有效 nvidia-smi 读数")

    if rss_vals:
        peak_rss = max(rss_vals)
        ram_limit = ram_base_mb * PEAK_RATIO_LIMIT
        check(f"RAM：后端进程峰值 RSS ≤ 启动时系统可用量 {PEAK_RATIO_LIMIT:.0%}",
              peak_rss <= ram_limit,
              f"峰值 RSS {peak_rss:.0f} MB ≤ {ram_limit:.0f} MB（启动时可用 {ram_base_mb:.0f} MB）")
    else:
        skip("[3] RAM 峰值", "采样窗口内无有效 RSS 读数")

    # ---- 汇总 ----
    print(f"\n===== 结果：{PASS} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过 =====")
    for f in FAIL:
        print(f"  FAIL: {f}")
    for s in SKIP:
        print(f"  SKIP: {s}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
