"""M7 系统配置（F-07 / api-contract §4.4）—— 声明式键表 + 覆盖式存储 + 保留策略清理。

## 存储策略

`SETTING` 表**只存被显式改过的键**；未改动的键由声明默认值（config / .env）提供。
保存时若某键的值与默认一致，则**删除该覆盖行** —— 于是「恢复默认」就是普通的保存动作，
并且新增配置项不会因为旧覆盖行而丢默认。

## 生效边界（**必须由 UI 如实告知，勿夸大为"全部立即生效"**）

| 键 | 生效时机 |
|---|---|
| `data_root` | 立即对**后续新建任务**生效（上传 / 缩略图 / 产物按新根解析）；**不迁移既有文件** |
| `model_dir` | **只读派生** = `<data_root>/models`（PRD §4.2：数据根含 models 子目录），不独立配置 |
| `max_upload_mb` | 立即（上传校验读时求值） |
| `task_retention_days` | 下次启动清理时生效（启动序列第 1 步末执行 `cleanup_expired_tasks`） |
| `max_concurrency` | **已保存但尚未生效**：执行器并发恒为 1（保底档），开放并发属 G-06（T-907） |
| `log_level` | 立即（重设进程内 root logger）；重启后由启动序列按落库值重设 |
| `simulation_enabled` | **立即生效**（T-901）：开/关档位模拟总闸 |
| `force_tier` | **立即生效**（T-901）：强制档位（`T0`~`T3`，空 = 不强制），作用于档位判定 |
| `force_vram_mb` | **立即生效**（T-901）：强制"可用显存"，作用于档位推导**与**模型可用性门控 |
| `force_has_tensorrt` | **立即生效**（T-901）：把 TensorRT 加入 EP 候选链（消费方为 G-04 / T-909） |
| `calibration_state` | **只读派生**（阶段 C / T-805 已实现）：无有效标定记录时为 `pending` |

⚠️ 档位模拟四个键**必须一起改**：保存后本模块会作废设置缓存 + 能力快照 + 硬件快照
   （`services/simulation.invalidate()`），否则会出现"设置改了、能力面板不变"的假象。
   模拟**只覆盖判定输入、不伪造硬件事实**——诊断 JSON 里仍能看到真实硬件（见 `engine/simulation.py`）。

⚠️ 有意不做的两件事：路径类改动**不迁移既有文件**、**不重开已有文件句柄** ——
迁移 / 重开会让运行中的任务与已落库记录失去一致性（风险远大于收益）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from ..core.config import PROJECT_ROOT, get_settings
from ..core.errors import AppError
from ..db import get_session
from ..engine.simulation import VALID_TIERS as _SIM_TIERS
from ..engine.simulation import parse_tier as _parse_tier
from ..engine.simulation import parse_vram_mb as _parse_vram_mb
from ..models.entities import Artifact, Calibration, Setting, Task

logger = logging.getLogger("websr.services.settings_store")

# 数据根下的子目录（PRD §4.2）：保存 data_root 时尽量建齐，失败不致命
_DATA_SUBDIRS = ("models", "uploads", "outputs", "thumbs", "calibration")

# 任务的终态（只有终态记录会被保留策略清理；运行/排队中永不清理）
_TERMINAL_STATUSES = ("completed", "canceled", "failed", "interrupted")

_LOG_LEVELS = ("debug", "info", "warning", "error")


@dataclass(frozen=True)
class SettingSpec:
    """一个配置项的声明：类型 + 取值约束 + 是否可写。"""
    key: str
    type: str  # string | number | boolean
    writable: bool = True
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] | None = None
    #: 允许空串（T-901 档位模拟的"不强制"语义）：默认**不允许**，避免静默清空既有配置
    allow_empty: bool = False


_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("data_root", "string"),
    SettingSpec("model_dir", "string", writable=False),  # 由 data_root 派生
    SettingSpec("task_retention_days", "number", minimum=1, maximum=3650),
    SettingSpec("max_upload_mb", "number", minimum=1, maximum=4096),
    SettingSpec("max_concurrency", "number", minimum=1, maximum=8),
    SettingSpec("log_level", "string", choices=_LOG_LEVELS),
    # ---- T-901 档位模拟（P3-首）：四键一组，空串 = 不强制。校验走 engine/simulation 的解析器，
    #      保证"设置层接受的写法"与"引擎层认得的写法"是同一套（不会出现两边口径漂移）。
    SettingSpec("simulation_enabled", "boolean"),
    SettingSpec("force_tier", "string", choices=("",) + tuple(_SIM_TIERS), allow_empty=True),
    SettingSpec("force_vram_mb", "string", allow_empty=True),
    SettingSpec("force_has_tensorrt", "string", choices=("", "true", "false"), allow_empty=True),
    SettingSpec("calibration_state", "string", writable=False),  # 由标定记录派生
)
_SPEC_BY_KEY = {s.key: s for s in _SPECS}

# 键的展示顺序 = 声明顺序（前端按此渲染，不依赖 dict 顺序隐式约定）
KEY_ORDER = tuple(s.key for s in _SPECS)


# ---------------------------------------------------------------------------
# 覆盖行读取（进程内缓存；保存时失效）
# ---------------------------------------------------------------------------

_overrides: dict[str, str] | None = None


def invalidate_cache() -> None:
    global _overrides
    _overrides = None


def _load_overrides() -> dict[str, str]:
    """SETTING 表 → {key: value}。

    best-effort：迁移完成前（启动早期）或库不可用时返回空 dict，退回 config 默认值。
    """
    global _overrides
    if _overrides is not None:
        return _overrides
    try:
        s = get_session()
        try:
            _overrides = {r.key: r.value for r in s.scalars(select(Setting)).all()}
        finally:
            s.close()
    except Exception as exc:  # 库未就绪 / 表不存在 → 用默认值，不阻断调用方
        logger.debug("配置覆盖行读取失败，使用默认值: %s", exc)
        _overrides = {}
    return _overrides


def effective(key: str) -> str:
    """生效值：覆盖行优先，其次声明默认值。未知键抛 AppError。"""
    if key not in _SPEC_BY_KEY:
        raise AppError("VALIDATION_ERROR", f"未知配置项: {key}", "请刷新页面后重试", 400)
    ov = _load_overrides()
    if key in ov:
        return ov[key]
    return _default(key)


def effective_int(key: str) -> int:
    raw = effective(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("配置项 %s 的值 %r 非法，回退默认", key, raw)
        return int(_default(key))


def effective_str(key: str) -> str:
    return effective(key)


def effective_bool(key: str) -> bool:
    return str(effective(key)).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# 路径派生
# ---------------------------------------------------------------------------

def resolve_path(raw: str) -> Path:
    """相对路径一律相对**项目根**解析（与 config.APP_DATA_DIR 同口径）。"""
    p = Path(raw)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def effective_data_root() -> Path:
    return resolve_path(effective("data_root"))


def effective_model_dir() -> Path:
    """模型目录由数据根派生（PRD §4.2）—— 不独立配置。"""
    return effective_data_root() / "models"


# ---------------------------------------------------------------------------
# 默认值 / 只读派生值
# ---------------------------------------------------------------------------

def _default(key: str) -> str:
    cfg = get_settings()
    if key == "data_root":
        return cfg.data_dir
    if key == "model_dir":
        # 跟随 data_root 的**生效值**派生，保证展示值与真实解析路径一致。
        # 用字符串拼接而非 pathlib：pathlib 会把 "./data" 归一成 "data"，
        # 与 data_root 的展示风格（"./data"）不一致。
        raw = str(_load_overrides().get("data_root", cfg.data_dir)).replace("\\", "/")
        return f"{raw.rstrip('/')}/models"
    if key == "task_retention_days":
        return "30"
    if key == "max_upload_mb":
        return str(cfg.max_upload_mb)
    if key == "max_concurrency":
        return "1"  # 保底档：并发恒 1（开放并发属 G-06 / T-907）
    if key == "log_level":
        return cfg.log_level
    if key == "simulation_enabled":
        return "false"
    # T-901：三个"强制项"的默认值都是**空串 = 不强制**（不是"强制为 T0 / 0 显存"）
    if key in ("force_tier", "force_vram_mb", "force_has_tensorrt"):
        return ""
    if key == "calibration_state":
        return "pending"
    raise AppError("INTERNAL_ERROR", f"配置项 {key} 缺少默认值定义", "请导出诊断 JSON 并查看日志", 500)


def _calibration_state() -> str:
    """有有效标定记录才算 done（阶段 C / T-805 已接入，此逻辑已在线上生效）。"""
    try:
        s = get_session()
        try:
            has = s.scalar(
                select(Calibration.id).where(Calibration.valid.is_(True)).limit(1)
            )
            return "done" if has is not None else "pending"
        finally:
            s.close()
    except Exception as exc:
        logger.debug("标定状态读取失败，回退 pending: %s", exc)
        return "pending"


def _readonly_value(key: str) -> str:
    if key == "calibration_state":
        return _calibration_state()
    return _default(key)


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def list_settings() -> list[dict]:
    """完整配置清单（默认值 + 覆盖行合并），顺序由 KEY_ORDER 固定。"""
    ov = _load_overrides()
    out: list[dict] = []
    for key in KEY_ORDER:
        spec = _SPEC_BY_KEY[key]
        if not spec.writable:
            value = _readonly_value(key)
        else:
            value = ov.get(key, _default(key))
        out.append({"key": key, "value": str(value), "type": spec.type})
    return out


def _validate(key: str, value: str) -> str:
    """按声明校验并规范化。返回规范化的字符串值；非法抛 VALIDATION_ERROR。"""
    spec = _SPEC_BY_KEY[key]
    raw = str(value).strip()
    if spec.type == "number":
        try:
            num = int(raw)
        except (TypeError, ValueError):
            raise AppError(
                "VALIDATION_ERROR", f"「{key}」需要整数", "请填写整数后重试",
                400, detail={"key": key, "value": value},
            )
        if spec.minimum is not None and num < spec.minimum:
            raise AppError(
                "VALIDATION_ERROR", f"「{key}」不能小于 {spec.minimum}",
                f"请填写 {spec.minimum} ~ {spec.maximum} 之间的整数", 400,
                detail={"key": key, "value": value},
            )
        if spec.maximum is not None and num > spec.maximum:
            raise AppError(
                "VALIDATION_ERROR", f"「{key}」不能大于 {spec.maximum}",
                f"请填写 {spec.minimum} ~ {spec.maximum} 之间的整数", 400,
                detail={"key": key, "value": value},
            )
        return str(num)
    if spec.type == "boolean":
        low = raw.lower()
        if low not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise AppError(
                "VALIDATION_ERROR", f"「{key}」需要布尔值", "请填写 true / false", 400,
                detail={"key": key, "value": value},
            )
        return "true" if low in ("true", "1", "yes", "on") else "false"
    # string
    if not raw:
        # T-901：档位模拟的三个"强制项"用**空串表达"不强制"**（不是缺省填 T0 / 0 显存），
        # 所以这几个键必须放行空串；其余字符串键仍拒绝空值。
        if spec.allow_empty:
            return ""
        raise AppError(
            "VALIDATION_ERROR", f"「{key}」不能为空", "请填写内容后重试", 400,
            detail={"key": key, "value": value},
        )
    # T-901：模拟键的取值用**引擎层同一套解析器**校验——设置层接受的写法与引擎层
    # 认得的写法必须一致，否则会出现"保存成功但实际没生效"的静默失败。
    if key == "force_tier":
        if _parse_tier(raw) is None:
            raise AppError(
                "VALIDATION_ERROR", f"「{key}」取值非法: {raw}",
                f"可选值为 {' / '.join(_SIM_TIERS)}，留空表示不强制", 400,
                detail={"key": key, "value": value},
            )
        return str(_parse_tier(raw))
    if key == "force_vram_mb":
        if _parse_vram_mb(raw) is None:
            raise AppError(
                "VALIDATION_ERROR", f"「{key}」取值非法: {raw}",
                "请填 MB（如 24576）或带 G 后缀（如 24G），留空表示不强制", 400,
                detail={"key": key, "value": value},
            )
        return str(_parse_vram_mb(raw))
    if spec.choices and raw not in spec.choices:
        raise AppError(
            "VALIDATION_ERROR", f"「{key}」取值非法: {raw}",
            f"可选值为 {' / '.join(spec.choices)}", 400,
            detail={"key": key, "value": value},
        )
    return raw


def _prepare_data_root(raw: str) -> None:
    """把数据根及其 5 个子目录建出来（best-effort）。

    失败即报错：否则用户以为改成功、实际后续上传全写在默认目录，属"静默失败"。
    只创建目录，**不迁移、不删除**任何既有文件。
    """
    root = resolve_path(raw)
    if root.exists() and not root.is_dir():
        raise AppError(
            "VALIDATION_ERROR", "数据根路径指向的是一个文件而非目录",
            "请改成一个目录路径后重试", 400, detail={"key": "data_root", "value": raw},
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
        for sub in _DATA_SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AppError(
            "VALIDATION_ERROR", f"数据根目录不可用：{exc}",
            "请检查路径是否存在、是否有写权限后重试", 400,
            detail={"key": "data_root", "value": raw},
        )


def update_settings(items: list[dict]) -> list[dict]:
    """保存配置。只落**可写键**；只读键静默忽略（前端整表提交不因此报错）。

    与默认值一致的键 → 删除覆盖行（等价于「恢复默认」）。
    """
    accepted: dict[str, str] = {}
    for item in items:
        key = str(item.get("key", ""))
        if key not in _SPEC_BY_KEY:
            raise AppError(
                "VALIDATION_ERROR", f"未知配置项: {key}", "请刷新页面后重试", 400,
                detail={"key": key},
            )
        spec = _SPEC_BY_KEY[key]
        if not spec.writable:
            # 只读派生键（model_dir / calibration_state）：忽略，不报错
            logger.debug("忽略只读配置项写入: %s", key)
            continue
        accepted[key] = _validate(key, item.get("value", ""))

    if "data_root" in accepted:
        _prepare_data_root(accepted["data_root"])

    # T-901：记下模拟键的**改动前**取值，保存后比对——只有真的变了才作废下游缓存
    # （能力快照 / 硬件快照）。否则前端"整表提交"会让每次保存都重建快照。
    from . import simulation as simulation_service  # 局部 import：避免模块级环

    sim_before = {k: effective(k) for k in simulation_service.SIMULATION_KEYS}

    try:
        s = get_session()
        try:
            existing = {r.key: r for r in s.scalars(select(Setting)).all()}
            for key, value in accepted.items():
                if value == _default(key):
                    row = existing.get(key)
                    if row is not None:
                        s.delete(row)  # 回到默认 → 删覆盖行
                    continue
                row = existing.get(key)
                if row is None:
                    s.add(Setting(key=key, value=value,
                                  value_type=_SPEC_BY_KEY[key].type,
                                  updated_at=datetime.now(timezone.utc)))
                else:
                    row.value = value
                    row.value_type = _SPEC_BY_KEY[key].type
                    row.updated_at = datetime.now(timezone.utc)
            s.commit()
        finally:
            s.close()
    finally:
        invalidate_cache()

    if "log_level" in accepted:
        apply_log_level(accepted["log_level"])

    if any(sim_before[k] != effective(k) for k in simulation_service.SIMULATION_KEYS):
        # 模拟态变了 → 快照里的 tier / 门控显存已过时，必须作废（否则界面显示不变）
        simulation_service.invalidate()
        logger.info("档位模拟设置已变更，能力快照与硬件快照已作废")

    return list_settings()


def apply_log_level(level: str) -> None:
    """立即重设进程内 root logger 级别（与 core.logging 同口径；启动序列也会调用）。"""
    logging.getLogger().setLevel(level.upper())
    logger.info("日志级别已切换为 %s", level.upper())


# ---------------------------------------------------------------------------
# 保留策略清理（F-07：超期**记录**被清理，产出文件不受影响）
# ---------------------------------------------------------------------------

def cleanup_expired_tasks(days: int | None = None) -> int:
    """删除超过保留期的**终态任务记录**（含其产物记录行）。

    明确边界：
    - 只删 `status ∈ 终态` 且 `created_at < now - days` 的任务；运行 / 排队中永不清理；
    - `ARTIFACT` 记录行随外键连带删除，但**磁盘文件一律不动**（PRD F-07 原文：
      「超期记录会被清理，产出文件不受影响」）—— 清文件属 F-12（S2，按时间/大小清理）。
    """
    if days is None:
        days = effective_int("task_retention_days")
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    s = get_session()
    try:
        ids = list(
            s.scalars(
                select(Task.id).where(
                    Task.status.in_(_TERMINAL_STATUSES), Task.created_at < cutoff
                )
            ).all()
        )
        if not ids:
            return 0
        s.execute(delete(Artifact).where(Artifact.task_id.in_(ids)))
        s.execute(delete(Task).where(Task.id.in_(ids)))
        s.commit()
        return len(ids)
    finally:
        s.close()
