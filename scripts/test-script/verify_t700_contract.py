"""T-700 API 契约 v1.0 一致性校验（**纯静态解析，不启服务**）。

冻结后"防漂移"的落地方式（契约 §9.3）——替代 OpenAPI → TS 代码生成。
三处事实源：`docs/tech/api-contract.md` ／ `web/src/types/api.ts` ／ `docs/tech/api/openapi.json`。

断言：
1. 错误码：契约 §2.3 表 == `errorMessages.ts` 的 key 集合（20 条）
2. 状态取值：后端 `TASK_STATUSES` == 前端 `constants.ts::TASK_STATUS` == 契约 §3.1 表
3. 端点：契约 §4 中标 ✅ 的路径**必须在** openapi.json；标 ⬜ 的**必须不在**
4. 字段：后端 schema（openapi）字段 ⊆ 前端 TS 接口字段（前端可有额外派生字段）
5. `reasons` 文案：§8 允许集的字面前缀出现在实现中
6. 冻结点 §7 八项全部 ✅

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t700_contract.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "tech" / "api-contract.md"
OPENAPI = ROOT / "docs" / "tech" / "api" / "openapi.json"
API_TS = ROOT / "web" / "src" / "types" / "api.ts"
CONST_TS = ROOT / "web" / "src" / "constants.ts"
ERR_TS = ROOT / "web" / "src" / "api" / "errorMessages.ts"
ENTITIES = ROOT / "server" / "app" / "models" / "entities.py"
PROFILE = ROOT / "server" / "app" / "engine" / "runtime_profile.py"
DECISION = ROOT / "server" / "app" / "services" / "engine_decision.py"

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def norm_path(p: str) -> str:
    """归一化路径参数占位符，消除"文档短占位 vs 代码实名"的口径差。

    契约 §4 表面向人阅读用 `{id}`；FastAPI 路由用参数实名 `{task_id}`/`{model_id}`/`{file_id}`。
    两者**指同一端点**，对账时统一折叠为 `{}`。
    ⚠️ 只归一化**占位符本身**（`{...}`），不触碰字面前缀（`/api/tasks`）。
    """
    return re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "{}", p)


contract = _read(CONTRACT)
api_ts = _read(API_TS)
const_ts = _read(CONST_TS)
err_ts = _read(ERR_TS)
entities = _read(ENTITIES)
impl_text = _read(PROFILE) + "\n" + _read(DECISION)
openapi = json.loads(_read(OPENAPI))

# ---------------------------------------------------------------------------
# 1. 错误码：契约 §2.3 == errorMessages.ts
# ---------------------------------------------------------------------------
print("== 1. 错误码全量清单（契约 §2.3 ↔ errorMessages.ts）==")

sec_23 = contract.split("### 2.3", 1)[1].split("\n## 3.", 1)[0]
contract_codes = set(re.findall(r"^\|\s*\d+\s*\|\s*`([A-Z_]+)`", sec_23, re.M))

err_obj = err_ts.split("ERROR_MESSAGES", 1)[1]
err_codes = set(re.findall(r"^\s{2}([A-Z_][A-Z0-9_]*):", err_obj, re.M))

check("契约 §2.3 解析到 20 条错误码", len(contract_codes) == 20, f"actual={len(contract_codes)}")
check("errorMessages.ts 解析到 20 条", len(err_codes) == 20, f"actual={len(err_codes)}")
check("两侧集合完全一致", contract_codes == err_codes,
      f"仅契约有={sorted(contract_codes - err_codes)} 仅前端有={sorted(err_codes - contract_codes)}")
for must in ("NOT_FOUND", "METHOD_NOT_ALLOWED", "MODEL_NOT_CONVERTIBLE", "CONVERT_ENV_MISSING"):
    check(f"新码 {must} 已入册", must in contract_codes and must in err_codes)

# ---------------------------------------------------------------------------
# 2. 任务状态取值：后端 == 前端 == 契约 §3.1
# ---------------------------------------------------------------------------
print("\n== 2. 任务状态取值（后端 ↔ 前端 ↔ 契约 §3.1）==")

m = re.search(r"TASK_STATUSES\s*=\s*\(([^)]*)\)", entities)
backend_statuses = set(re.findall(r"\"([a-z_]+)\"", m.group(1))) if m else set()

m = re.search(r"export const TASK_STATUS\s*=\s*\[(.*?)\]\s*as const", const_ts, re.S)
front_statuses = set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()

sec_status = contract.split("**`status` 取值", 1)[1].split("**两个关键设计", 1)[0]
doc_statuses = set(re.findall(r"^\|\s*`([a-z_]+)`", sec_status, re.M))

check("后端 TASK_STATUSES 解析到 7 项", len(backend_statuses) == 7, f"actual={sorted(backend_statuses)}")
check("前端 TASK_STATUS == 后端", front_statuses == backend_statuses,
      f"仅后端={sorted(backend_statuses - front_statuses)} 仅前端={sorted(front_statuses - backend_statuses)}")
check("契约 §3.1 表 == 后端", doc_statuses == backend_statuses,
      f"仅契约={sorted(doc_statuses - backend_statuses)} 仅后端={sorted(backend_statuses - doc_statuses)}")
check("已无 `done`", "done" not in front_statuses and "done" not in backend_statuses)
check("已无 `pending`", "pending" not in front_statuses and "pending" not in backend_statuses)

# ---------------------------------------------------------------------------
# 3. 端点：契约 §4 的 ✅/⬜ 与 openapi.json 对账
# ---------------------------------------------------------------------------
print("\n== 3. 端点对账（契约 §4 ↔ openapi.json）==")

assert_method = re.compile(r"^(?:GET|POST|PUT|DELETE|PATCH)(?:\s*/\s*(?:GET|POST|PUT|DELETE|PATCH))*$")
sec_4 = contract.split("\n## 4. 端点", 1)[1].split("\n## 5.", 1)[0]
impl_paths: set[str] = set()
plan_paths: set[str] = set()
for line in sec_4.splitlines():
    if not line.startswith("|"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 3:
        continue
    if not assert_method.match(cells[0].replace("*", "").replace("`", "").strip()):
        continue
    pm = re.search(r"/api/[^\s`*]+", cells[1])
    if not pm:
        continue
    path = norm_path(pm.group(0))
    last = cells[-1]
    if "✅" in last:
        impl_paths.add(path)
    elif "⬜" in last:
        plan_paths.add(path)

openapi_paths = {norm_path(p) for p in openapi.get("paths", {})}

check("契约 §4 解析到足够多的已实现端点", len(impl_paths) >= 15, f"actual={len(impl_paths)}")
check("契约标记 ✅ 的端点都在 openapi 中", impl_paths <= openapi_paths,
      f"缺失={sorted(impl_paths - openapi_paths)}")
check("契约标记 ⬜ 的端点**不在** openapi 中（S2 未实现）", not (plan_paths & openapi_paths),
      f"不应存在={sorted(plan_paths & openapi_paths)}")
check("openapi 无契约未登记的端点", openapi_paths <= (impl_paths | plan_paths),
      f"契约未登记={sorted(openapi_paths - impl_paths - plan_paths)}")
check("日志端点已实现", norm_path("/api/tasks/{task_id}/logs") in openapi_paths)

# ---------------------------------------------------------------------------
# 4. 字段：后端 schema（openapi）⊆ 前端 TS 接口
# ---------------------------------------------------------------------------
print("\n== 4. 字段一致性（openapi schema ⊆ api.ts 接口）==")


def ts_fields(iface: str) -> set[str]:
    """提取 `export interface <iface> { ... }` 的顶层字段名（2 空格缩进）。"""
    m = re.search(rf"export interface {iface}\s*\{{(.*?)\n\}}", api_ts, re.S)
    if not m:
        return set()
    return set(re.findall(r"^\s{2}([a-zA-Z_][a-zA-Z0-9_]*)\??:", m.group(1), re.M))


schemas = openapi.get("components", {}).get("schemas", {})
pairs = [
    ("TaskOut", "Task"),
    ("ModelOut", "Model"),
    ("ArtifactOut", "Artifact"),
    ("ProgressOut", "Progress"),
    ("LogEntryOut", "LogEntry"),
    ("ModelCapabilities", "ModelCapabilities"),
]
for schema_name, iface in pairs:
    be = set(schemas.get(schema_name, {}).get("properties", {}))
    fe = ts_fields(iface)
    if not be:
        check(f"{schema_name} 在 openapi 中存在", False, "schema 缺失")
        continue
    check(f"{schema_name}({len(be)}) ⊆ {iface}({len(fe)})", be <= fe,
          f"前端缺字段={sorted(be - fe)}")

# ---------------------------------------------------------------------------
# 5. reasons 文案规范：§8 允许集出现在实现里
# ---------------------------------------------------------------------------
print("\n== 5. reasons 文案规范（契约 §8 ↔ 实现）==")

sec_8 = contract.split("## 8. `resolved.reasons`", 1)[1].split("\n## 9.", 1)[0]
patterns = re.findall(r"^\|\s*[①②③④⑤⑥][^|]*\|\s*`([^`]+)`", sec_8, re.M)
check("§8 解析到文案模式", len(patterns) >= 8, f"actual={len(patterns)}")

missing: list[str] = []
for pat in patterns:
    literal = pat.split("{")[0].strip()
    if literal and literal not in impl_text:
        missing.append(literal)
check("§8 全部文案模式的字面前缀均出现在实现中", not missing, f"缺失={missing}")

for must in ("未标定（阶段 C 属 S2）", "存在标定记录但与当前硬件指纹不符",
             "用户指定参数（非自动档）", "自动档：tile / 精度 / 后端由引擎决定",
             "没有任何后端通过 EP 真实性验证"):
    check(f"关键文案存在：{must[:16]}…", must in impl_text)

# ---------------------------------------------------------------------------
# 6. 冻结点 §7 八项闭合
# ---------------------------------------------------------------------------
print("\n== 6. 冻结点 §7 八项闭合 ==")

sec_7 = contract.split("## 7. 冻结点", 1)[1].split("\n## 8.", 1)[0]
rows = re.findall(r"^\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|", sec_7, re.M)
check("§7 解析到 8 行冻结点", len(rows) == 8, f"actual={len(rows)}")
not_closed = [n for n, _name, st in rows if "✅" not in st]
check("8 项全部 ✅", not not_closed, f"未闭合#{not_closed}")
check("契约标注 v1.0 已冻结", "**v1.0 · 已冻结**" in contract)

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
