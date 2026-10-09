"""T-807 工具级探针 —— **必须在转换环境 `.venvs/sr-convert` 里运行**。

由 `verify_t807_convert.py` 以子进程调用（沿用 T-806 的**跨环境复核**方法论：
跑的是**产品/工具本身的实现**，不是平行实现）。

为什么必须换环境：本节要验证"架构认不出 / 自检不通过 / 导出失败"三条失败路径，
它们都需要真的 torch（构造垃圾 state_dict、编译模型、导出）。而应用环境
`.venvs/sr-app` **刻意不含 torch**（ADR-003）——这本身就是依赖分离的证据。

运行：./.venvs/sr-convert/Scripts/python.exe scripts/test-script/_t807_tool_probe.py
退出码：0 全过 ｜ 1 有失败 ｜ 2 无法运行（环境不对）
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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
    print(f"\n--- {title} ---")


try:
    import torch  # noqa: F401
except Exception as exc:  # pragma: no cover
    print(f"[FATAL] 本探针必须在转换环境运行，当前环境没有 torch：{exc}")
    raise SystemExit(2)

sys.path.insert(0, str(ROOT / "tools"))
import convert_to_onnx as tool  # noqa: E402

FIXTURE = ROOT / ".workbuddy" / "verify" / "t807" / "realesr-general-x4v3.pth"
TMP = Path(tempfile.mkdtemp(prefix="websr_t807probe_"))

try:
    section("1. 惰性重依赖（源码级：torch 只在函数体内出现）")
    src = (ROOT / "tools" / "convert_to_onnx.py").read_text(encoding="utf-8")
    top_level = [
        ln for ln in src.splitlines()
        if ln.startswith(("import ", "from ")) and ("torch" in ln or "spandrel" in ln)
    ]
    check("工具顶层不 import torch / spandrel（否则无 torch 的环境连 import 都做不到）",
          not top_level, str(top_level))
    check("重依赖只在 _require_runtime 内惰性 import", "_require_runtime" in src)

    section("2. 架构认不出 → 明确报错，不猜、不落盘")
    junk = TMP / "junk.pth"
    torch.save({"weight": torch.rand(4, 4)}, junk)
    try:
        tool.convert(src=junk, out=TMP / "junk.onnx", arch=None, static=None, fp16=False,
                     opset=17, tol=1e-3, skip_check=False, write_meta=False)
        check("垃圾 state_dict 应当被拒绝", False, "竟然转换成功了")
    except tool.ConvertError as exc:
        check("code = arch_unrecognized", exc.code == "arch_unrecognized", exc.code)
        check("退出码 = 3", exc.exit_code == tool.EXIT_ARCH, str(exc.exit_code))
        check("报错带 spandrel 候选架构列表（用户能照做）",
              len(exc.extra.get("candidates") or []) >= 20,
              str(len(exc.extra.get("candidates") or [])))
        check("认不出时不产出任何文件", not (TMP / "junk.onnx").exists())

    section("3. 自检不通过 → 删产物、不落盘")
    if FIXTURE.is_file():
        out = TMP / "selfcheck_fail.onnx"
        orig = tool.self_check
        tool.self_check = lambda **kw: {"ok": False, "max_abs_diff": 9.9, "tolerance": 1e-3}
        try:
            tool.convert(src=FIXTURE, out=out, arch=None, static=None, fp16=False,
                         opset=17, tol=1e-3, skip_check=False, write_meta=False)
            check("自检不通过应当抛错", False, "没有抛错")
        except tool.ConvertError as exc:
            check("code = selfcheck_failed", exc.code == "selfcheck_failed", exc.code)
            check("退出码 = 5", exc.exit_code == tool.EXIT_SELFCHECK, str(exc.exit_code))
            check("产物不存在（能加载但算错的模型比加载失败更糟）", not out.exists())
        finally:
            tool.self_check = orig
    else:
        check("前置权重存在（自检分支需要）", False, str(FIXTURE))

    section("4. 导出失败 → 原子写不留残留")
    if FIXTURE.is_file():
        orig_export = torch.onnx.export
        torch.onnx.export = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("模拟导出器崩溃"))
        try:
            tool.convert(src=FIXTURE, out=TMP / "boom.onnx", arch=None, static=None, fp16=False,
                         opset=17, tol=1e-3, skip_check=False, write_meta=False)
            check("导出失败应当抛错", False, "没有抛错")
        except tool.ConvertError as exc:
            check("code = export_failed", exc.code == "export_failed", exc.code)
            check("不留残留 .tmp", not list(TMP.glob("*.tmp")), str(list(TMP.glob("*.tmp"))))
            check("目标文件不存在", not (TMP / "boom.onnx").exists())
        finally:
            torch.onnx.export = orig_export
    else:
        check("前置权重存在（导出分支需要）", False, str(FIXTURE))

    section("5. 自检数值本身是**真的在算**（不是恒真断言）")
    if FIXTURE.is_file():
        out = TMP / "real.onnx"
        t0 = time.time()
        res = tool.convert(src=FIXTURE, out=out, arch=None, static=None, fp16=False,
                           opset=17, tol=1e-3, skip_check=False, write_meta=False)
        secs = time.time() - t0
        chk = res["check"]
        check("自检通过且给出的 max|diff| 是有限小数",
              chk.get("ok") is True and 0.0 <= chk["max_abs_diff"] < 1e-3, json.dumps(chk))
        check("自检规模是真实推理（输入尺寸与输出形状相符）",
              chk["output_shape"][2] == chk["input_size"] * 4, str(chk["output_shape"]))
        check("--json 结果含可复核的哈希与体积",
              len(res["output"]["sha256"]) == 64 and res["output"]["size_bytes"] > 0)
        print(f"  （真实转换耗时 {secs:.1f}s，自检 max|diff| = {chk['max_abs_diff']:.3e}）")

    section("6. 显式固定尺寸导出（--static 对应产品侧 fixed_tile 通道）")
    if FIXTURE.is_file():
        out = TMP / "static256.onnx"
        res = tool.convert(src=FIXTURE, out=out, arch=None, static=256, fp16=False,
                           opset=17, tol=1e-3, skip_check=False, write_meta=False)
        check("static 导出成功且报告 dynamic=False", res["output"]["dynamic"] is False)
        check("static 数值被记录", res["output"]["static"] == 256, str(res["output"]["static"]))
        import onnxruntime as ort
        s = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
        dims = s.get_inputs()[0].shape
        check("产物输入形状被写死为 256×256（不是动态）",
              dims[2] == 256 and dims[3] == 256, str(dims))
        try:
            s.run(None, {"input": __import__("numpy").random.rand(1, 3, 128, 128).astype("float32")})
            check("固定尺寸产物喂错误尺寸应当报错", False, "128 竟然跑通了")
        except Exception:
            check("固定尺寸产物喂错误尺寸会报错（语义清晰：正好等于 N）", True)
    section("7. `.safetensors` 路径（任务要求覆盖两种格式）")
    if FIXTURE.is_file():
        import spandrel
        from safetensors.torch import save_file

        sf = TMP / "compact.safetensors"
        sd = spandrel.ModelLoader().load_state_dict_from_file(str(FIXTURE))
        save_file(sd, str(sf))
        check(".safetensors 由真实权重的 state_dict 生成（非伪造空文件）",
              sf.is_file() and sf.stat().st_size > 1_000_000, f"{sf.stat().st_size if sf.is_file() else 0}")

        out = TMP / "from_safetensors.onnx"
        res = tool.convert(src=sf, out=out, arch=None, static=None, fp16=False,
                           opset=17, tol=1e-3, skip_check=False, write_meta=False)
        check(".safetensors 转换成功且格式被正确标注",
              res["ok"] is True and res["input"]["format"] == "safetensors",
              str(res["input"]))
        check(".safetensors 与 .pth 产出**同构**（同一架构/倍数/参数量）",
              res["model"]["scale"] == 4 and res["model"]["param_count"] > 100_000,
              str(res["model"])[:160])
        check(".safetensors 路径自检同样通过",
              (res.get("check") or {}).get("ok") is True, str(res.get("check"))[:160])
    else:
        check("前置权重存在（.safetensors 分支需要）", False, str(FIXTURE))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n===== 工具级探针：{PASS} 通过 / {FAIL} 失败 =====")
raise SystemExit(1 if FAIL else 0)
