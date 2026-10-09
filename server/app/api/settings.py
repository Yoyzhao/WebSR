"""系统配置路由（api-contract §4.4 的 `GET / PUT /api/settings`）。

- GET：默认值与覆盖行合并后的完整清单（前端整表渲染）；
- PUT：只落可写键，只读派生键（`model_dir` / `calibration_state`）**静默忽略** ——
  前端保存时会把整表回传，忽略而非报错才能让「整表提交」成立。

差异登记（T-700 冻结核对）：响应为裸数组（前端已定稿类型），契约写作 `{ items }`。
"""
from fastapi import APIRouter, Body

from ..schemas.setting import SettingIn, SettingOut, SettingsUpdate
from ..services import settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=list[SettingOut])
def get_settings_all() -> list[dict]:
    return settings_store.list_settings()


@router.put("", response_model=list[SettingOut])
def put_settings(
    body: SettingsUpdate | list[SettingIn] = Body(...),
) -> list[dict]:
    """保存配置。接受 `{ items: [...] }`（契约）或裸数组（宽松兼容）。"""
    raw = body.items if isinstance(body, SettingsUpdate) else body
    items = [{"key": i.key, "value": i.value} for i in raw]
    return settings_store.update_settings(items)
