"""系统配置 DTO（对齐 web/src/types/api.ts 的 Setting / SettingsPayload）。

形态决策（登记于 STEP-5D §T-609，冻结核对归 T-700）：
- 请求体用契约 §4.4 的包装 `{ items: [...] }`，同时**兼容裸数组**（前端将来接真实
  接口时两种写法都能过，避免为一次猜测埋雷）；
- **响应为裸数组 `Setting[]`** —— 与前端已定稿的 `fetchSettings(): Setting[]` 一致；
  契约写作 `{ items }`，是否启用包装待 T-700 裁决（与 `GET /api/models` 同一处理）。
"""
from pydantic import BaseModel


class SettingOut(BaseModel):
    key: str
    value: str
    type: str  # string | number | boolean


class SettingIn(BaseModel):
    key: str
    value: str
    type: str | None = None  # 类型以服务端声明为准，客户端传值仅作参考


class SettingsUpdate(BaseModel):
    items: list[SettingIn]
