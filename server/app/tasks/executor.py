"""任务执行器：真实推理（`EngineExecutor`）与占位（`StubExecutor`）。

## 两个执行器为什么都在

- **`EngineExecutor`（T-806 起为默认）**：按阶段 D 的决策加载模型 → 分块推理 →
  羽化拼接 → 落盘 **真实产物**。S1 闭环（上传 → 任务 → 推理 → 对比 → 下载）靠它打通。
- **`StubExecutor`**：只做控制面动作（分块循环 / 取消检查 / 进度上报），**不产出图像**。
  它的存在理由是**控制面验证必须能离线、快速地跑**——状态机、协作式取消、SSE 节流、
  并发拒绝这些机制与推理无关，用真实模型去验证只会让测试慢 100 倍且依赖模型文件。

### 测试注入点（显式，不走环境变量）

`set_executor_factory()` 让验证脚本把执行器换成 Stub。相比 `APP_*` 环境变量，好处是
**不可能泄漏到生产**：没有环境变量能改变线上行为，只有同进程内显式调用才生效。

## 职责分工（T-804 之后）

阶段 D/E 的**决策**在管理器侧完成（`services/engine_decision.py`），随 `TaskContext.profile`
传入——执行器**不自己决定** tile / 精度 / 后端。执行器只负责：

1. 按决策把模型加载起来（并把"执行时才发现的事实"如实记回 `downgrades` / `reasons`）；
2. 跑完编排，把**产物清单与输出尺寸**交给控制面（`RunResult`）；
3. 把引擎层的 `InferCancelled` 映射为控制面的 `TaskCancelled`。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..engine import model_loader, pipeline
from ..engine.fallback import DEFAULT_ALIGN, fallback_params, overlap_for
from ..engine.image_ops import PreprocessSpec
from ..engine.model_loader import ModelLoadError
from ..engine.runtime_profile import RuntimeProfile
from ..models.entities import Model
from ..services import media_store, settings_store

logger = logging.getLogger("websr.tasks.executor")

#: GPU 类 EP（判定"是否真的用上了加速后端"用；与引擎层同一集合口径）
_GPU_EPS = frozenset({"CUDAExecutionProvider", "TensorrtExecutionProvider"})


class TaskCancelled(Exception):
    """执行器在每块之间检查取消标志后抛出（协作式取消，ADR-005 约束 3）。"""


# ---------------------------------------------------------------------------
# 执行结果（执行器 → 控制面的交接结构）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArtifactSpec:
    """一个待落库的产物。`path` 相对 `data/`（与 ARTIFACT.path 同口径）。"""

    kind: str
    path: str
    sha256: str | None = None
    size_bytes: int | None = None


@dataclass
class RunResult:
    """执行器交给控制面的**执行事实**（不是决策）。"""

    artifacts: list[ArtifactSpec] = field(default_factory=list)
    output_width: int | None = None
    output_height: int | None = None
    #: 真实执行事实（后端 / 实际精度 / 块数 / 耗时 / 产物哈希），并入 `resolved["execution"]`
    execution: dict | None = None


@dataclass
class TaskContext:
    task_id: int
    params: dict  # 用户请求值快照（含 file_id）
    model: Model
    source_path: Path
    should_cancel: Callable[[], bool]
    # (chunk_done, chunk_total, stage, message)；节流与落库由管理器负责
    report_progress: Callable[[int, int, str, str], None]
    #: 阶段 D/E 决策结果（管理器注入）。None 时执行器回落到最保守的占位输出。
    profile: RuntimeProfile | None = None
    #: 执行器写入的执行事实（产物清单 / 输出尺寸）；控制面据此落 ARTIFACT 与回填尺寸。
    result: RunResult | None = None


class Executor(Protocol):
    def run(self, ctx: TaskContext) -> dict:
        """执行并返回 resolved（引擎决策结果）。取消抛 TaskCancelled，失败抛异常。"""
        ...


# ---------------------------------------------------------------------------
# 真实推理执行器（T-806）
# ---------------------------------------------------------------------------

def _downgrade(field_name: str, old, new, reason: str) -> dict:
    """与阶段 D 同形状的降档记录（`resolved.downgrades` 的消费方不区分来源）。"""
    return {"field": field_name, "from": old, "to": new, "reason": reason}


def _safe_stem(name: str | None) -> str:
    if not name:
        return "result"
    stem = Path(name).stem
    return re.sub(r"[^\w.\-一-鿿]", "_", stem) or "result"


class EngineExecutor:
    """真实推理执行器：决策 → 加载模型 → 分块推理 → 落盘产物。"""

    def run(self, ctx: TaskContext) -> dict:
        profile = ctx.profile
        if profile is None:
            # 独立调用（无决策注入）：按保底档跑，守住"至少要能出图"这条底线
            base = fallback_params([])
            tile = base["tile"]
            ov = overlap_for(tile, DEFAULT_ALIGN)
            profile = RuntimeProfile(
                tile=tile, precision=base["precision"], backend=base["backend"],
                overlap=ov, feather_px=ov, concurrency=base["concurrency"],
                using_fallback=True, source="fallback",
                reasons=["未注入引擎决策（独立调用路径），按保底档执行"],
            )

        resolved = profile.to_resolved()
        reasons: list[str] = list(resolved["reasons"])
        downgrades: list[dict] = list(resolved["downgrades"])
        tile = int(profile.tile)
        overlap = int(profile.overlap)
        feather = int(profile.feather_px)

        model = ctx.model
        source = Path(ctx.source_path)
        if not source.is_file():
            raise ModelLoadError(
                model_loader.FILE_MISSING, "源图不存在或已被清理",
                reason="请重新上传图片后再提交任务",
                detail={"path": str(source)},
            )

        scale = self._resolve_scale(ctx)
        model_dir = settings_store.effective_model_dir()
        primary = model_dir / model.path
        companion = (model_dir / model.companion_path) if model.companion_path else None

        spec = model_loader.LoadSpec(
            path=primary,
            fmt=model.format,
            companion=companion,
            scale=scale,
            precision=profile.precision,
            backend=profile.backend,
            num_threads=None,  # 线程数由运行时探测/标定决定（不写死）；当前交给 ORT 默认
        )

        if ctx.should_cancel():
            raise TaskCancelled()

        # 模型加载同属「非分块阶段」→ 块计数上报 (0, 0)（无块语义），避免污染 percent
        ctx.report_progress(0, 0, "preprocessing", f"正在加载模型（{model.format}）")
        backend = model_loader.load_backend(spec)

        try:
            desc = backend.describe()
            # ---- 执行时才发现的事实：逐条如实登记（降级必须显式）------------------
            tile, overlap, feather = self._reconcile_geometry(
                desc, tile=tile, overlap=overlap, reasons=reasons, downgrades=downgrades,
            )
            self._reconcile_backend(desc, profile=profile, reasons=reasons, downgrades=downgrades)
            self._reconcile_precision(
                desc, profile=profile, reasons=reasons, downgrades=downgrades,
            )

            out_dir = media_store.task_outputs_dir(ctx.task_id)
            stem = _safe_stem(self._source_filename(ctx))
            out_path = out_dir / f"{stem}_{scale}x.png"

            outcome = pipeline.run_upscale(
                backend,
                pipeline.UpscaleRequest(
                    source_path=source,
                    output_path=out_path,
                    tile=tile,
                    overlap=overlap,
                    feather_px=feather,
                    scale=scale,
                    spec=PreprocessSpec(),  # 内置 ONNX 统一 RGB / /255 / float32
                    should_cancel=ctx.should_cancel,
                    report=ctx.report_progress,
                ),
            )
        except pipeline.InferCancelled as exc:
            raise TaskCancelled() from exc
        finally:
            backend.close()

        # ---- 产物交接 ------------------------------------------------------------
        rel = out_path.relative_to(settings_store.effective_data_root()).as_posix()
        ctx.result = RunResult(
            artifacts=[ArtifactSpec(
                kind="output", path=rel, sha256=outcome.sha256, size_bytes=outcome.size_bytes,
            )],
            output_width=outcome.width,
            output_height=outcome.height,
            execution={
                "tiles": outcome.tiles,
                "elapsed_ms": outcome.elapsed_ms,
                "source_width": outcome.source_width,
                "source_height": outcome.source_height,
                "output_width": outcome.width,
                "output_height": outcome.height,
                "tile": tile,
                "overlap": overlap,
                "feather_px": feather,
                "backend": outcome.backend,
                "artifact_path": rel,
                "sha256": outcome.sha256,
            },
        )

        resolved["tile"] = tile
        resolved["overlap"] = overlap
        resolved["feather_px"] = feather
        resolved["reasons"] = reasons
        resolved["downgrades"] = downgrades
        resolved["degraded"] = bool(downgrades)
        resolved["execution"] = ctx.result.execution
        return resolved

    # ---- 辅助 ------------------------------------------------------------------

    @staticmethod
    def _resolve_scale(ctx: TaskContext) -> int:
        """放大倍数：用户参数优先，其次模型自带，最后按 4（RealESRGAN 系默认）。"""
        for raw in (ctx.params.get("scale"), getattr(ctx.model, "scale", None)):
            try:
                if raw is not None and int(raw) >= 1:
                    return int(raw)
            except (TypeError, ValueError):
                continue
        return 4

    @staticmethod
    def _source_filename(ctx: TaskContext) -> str | None:
        file_id = (ctx.params or {}).get("file_id")
        meta = media_store.read_meta(file_id) if file_id else None
        return (meta or {}).get("filename")

    @staticmethod
    def _reconcile_geometry(
        desc: dict, *, tile: int, overlap: int, reasons: list[str], downgrades: list[dict],
    ) -> tuple[int, int, int]:
        """模型输入为静态尺寸时，tile 必须正好等于它（决策阶段可能还没读到该约束）。"""
        fixed = (desc.get("input_spec") or {}).get("fixed_tile")
        if fixed and int(fixed) != tile:
            new_tile = int(fixed)
            downgrades.append(_downgrade(
                "tile", tile, new_tile,
                f"该模型的输入尺寸是固定的 {new_tile}×{new_tile}，分块边长必须恰好相等",
            ))
            tile = new_tile
            overlap = overlap_for(tile, DEFAULT_ALIGN)
        return tile, overlap, overlap

    @staticmethod
    def _reconcile_backend(
        desc: dict, *, profile: RuntimeProfile, reasons: list[str], downgrades: list[dict],
    ) -> None:
        """请求的 EP 是否真的进了 session（`get_providers` 只证明"对象被创建"，

        但**结合阶段 B 的 profile 级验证**，此处能发现"配置漂移"：环境换了 ORT 或驱动后，
        决策缓存里的 EP 可能已不可用）。不静默——降级必须显式。
        """
        providers = desc.get("providers")
        if not providers:
            return  # OpenVINO / ncnn 不走 ORT provider 概念
        requested = profile.backend
        if requested and requested not in providers and requested in _GPU_EPS:
            downgrades.append(_downgrade(
                "backend", requested, providers[0],
                "该后端未进入推理会话（运行时环境可能已变化），本次实际以可用后端执行",
            ))
            reasons.append(f"请求后端 {requested} 未生效，实际使用 {providers[0]}")

    @staticmethod
    def _reconcile_precision(
        desc: dict, *, profile: RuntimeProfile, reasons: list[str], downgrades: list[dict],
    ) -> None:
        """决策精度 vs 模型实际输入 dtype。

        **不在运行时做精度转换**（那要改写整张图）。模型是 fp32 而决策要 fp16 时，
        本次就是 fp32——如实记一条降档，而不是假装跑的是 fp16。
        """
        effective = desc.get("precision_effective")
        if not effective or effective == profile.precision:
            return
        if profile.precision == "fp16" and effective == "fp32":
            downgrades.append(_downgrade(
                "precision", "fp16", "fp32",
                "所选模型文件是 fp32 导出，运行时不做精度转换；本次实际以 fp32 执行",
            ))
        else:
            reasons.append(
                f"模型文件的输入精度为 {effective}（决策为 {profile.precision}）——"
                "以模型声明为准，不做运行时精度转换"
            )


class StubExecutor:
    """占位执行器：12 块 × 150ms 模拟推理循环，只验证控制面（不产出图像）。"""

    TOTAL_CHUNKS = 12
    CHUNK_SECONDS = 0.15

    def run(self, ctx: TaskContext) -> dict:
        stages = ["preprocessing", "inferencing", "stitching", "saving"]
        for i in range(1, self.TOTAL_CHUNKS + 1):
            if ctx.should_cancel():
                raise TaskCancelled()
            time.sleep(self.CHUNK_SECONDS)
            stage = stages[min((i - 1) * len(stages) // self.TOTAL_CHUNKS, len(stages) - 1)]
            ctx.report_progress(i, self.TOTAL_CHUNKS, stage, f"正在推理 {i} / {self.TOTAL_CHUNKS} 块")

        if ctx.profile is not None:
            resolved = ctx.profile.to_resolved()
            resolved["reasons"] = list(resolved["reasons"]) + [
                "占位执行器：不产生真实推理结果——以上参数是引擎的决策，尚未真正驱动计算"
            ]
            return resolved

        # 无决策注入（独立调用/测试）：给出与保底档同口径的最保守输出。
        # 取值仍走 fallback 单一事实源，避免这里再写一份 64 / fp32 / cpu。
        base = fallback_params([])
        align = DEFAULT_ALIGN
        ov = overlap_for(base["tile"], align)
        return {
            "tile": base["tile"],
            "precision": base["precision"],
            "backend": base["backend"],
            "overlap": ov,
            "feather_px": ov,
            "concurrency": base["concurrency"],
            "using_fallback": True,
            "degraded": False,
            "source": "fallback",
            "reasons": ["未注入引擎决策（占位路径），按最保守参数输出"],
            "downgrades": [],
        }


# ---------------------------------------------------------------------------
# 出口与测试注入点
# ---------------------------------------------------------------------------

#: 显式替换点（仅同进程生效）。**不提供**环境变量开关：线上行为不可被环境改变。
_factory: Callable[[], Executor] | None = None


def set_executor_factory(factory: Callable[[], Executor] | None) -> None:
    """替换执行器工厂（控制面验证脚本用；传 `None` 恢复默认）。

    典型用法：`set_executor_factory(StubExecutor)`——控制面测试不需要真实推理。
    """
    global _factory
    _factory = factory


def reset_executor_factory() -> None:
    set_executor_factory(None)


def get_executor() -> Executor:
    """执行器出口（唯一替换点）。生产路径返回真实推理执行器。"""
    if _factory is not None:
        return _factory()
    return EngineExecutor()
