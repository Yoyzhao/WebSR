# ADR-006 · ORM 与数据库迁移方案

- **编号**：ADR-006
- **状态**：✅ **已接受**（2026-10-08）
- **日期**：2026-10-08
- **决策任务**：`T-303`（原挂 M3，已于 M3 移入 **5D 入口**执行）
- **相关**：[ADR-002](ADR-002-元信息库选SQLite.md) · [`tech-arch.md`](../tech-arch.md) §3 / §4 / §6.6 · [`dev-info.md`](../dev-info.md) §1
- **验证证据**：`.workbuddy/verify/orm-smoke/`（`report-connect.txt` 16 项 + `report-migration-fkoff.txt` 12 项，全部通过）

## 背景

[ADR-002](ADR-002-元信息库选SQLite.md) 已定"元信息库用 SQLite、通过 ORM 访问（建议 SQLAlchemy 2.x）、表结构变更用 Alembic 管理"。但该文件只给了方向与 4 条实施约束，**未定案"同步还是异步""时间怎么存""枚举怎么落"等会渗透到每一行业务代码的细节**，也未识别 SQLite + Alembic 组合的具体陷阱。

本 ADR 补齐这些细节，并把"可行"变成"已验证"。

## 环境实测（2026-10-08）

| 项 | 值 |
|---|---|
| Python | **3.13.14**（受管；目录名 `3.13.12` 与解释器版本不一致） |
| SQLite 运行时 | **lib 3.53.1**（随 Python 内置），`threadsafety = 3` |
| 包管理器 | uv **0.12.21** |
| **SQLAlchemy** | **2.1.4**（`requires_python >= 3.11`） |
| **Alembic** | **1.20.0**（`>=3.10`） |
| 环境落点 | `.venvs/sr-app`（项目内隔离，**严禁全局安装**） |

**兼容性**：SQLAlchemy 2.1.4 + Alembic 1.20.0 组合已实测跑通（建表 / 改列 / 降级 / 幂等）✅

## 决策

1. **ORM 用 SQLAlchemy 2.x**，**同步引擎**（`create_engine` + `Session`，**不用** async / aiosqlite）。
2. **迁移用 Alembic**，`render_as_batch=True` + `compare_type=True`。
3. 引擎与 PRAGMA 统一由**单一工厂**（`engine/db.py` 的 `make_engine()`）产出，**迁移与运行时复用同一工厂**。
4. 时间字段用自定义 **`UTCDateTime` TypeDecorator**；枚举用 `String`（DB 层不设 CHECK）；结构字段用 `JSON` 列。

### 为什么是同步引擎（关键取舍）

| 维度 | 同步（采纳） | async + aiosqlite（未采纳） |
|---|---|---|
| 写并发 | 单 worker、写极少；ADR-002 约束 2 明确"推理过程不得持有事务"，事务均为毫秒级 | 无改善（SQLite 是本地文件 IO） |
| 复杂度 | 单栈 | 双栈：`AsyncSession` 生命周期 + `greenlet` 依赖 + 依赖注入改造 |
| 事件循环 | FastAPI 对同步 `def` 端点**自动丢线程池**，不阻塞 | 需全程 async 化 |
| SSE 端点 | 只读**内存**进度广播，不查库 | 同样不查库 |

**唯一需要 async 的 SSE 长连接不触及数据库**，因此 async 带来的收益不足以覆盖复杂度成本。

## 实施约束（11 条，全部经实测）

### A. 迁移机制

**1. `render_as_batch=True` 是硬性要求**

SQLite **不支持 `ALTER COLUMN`**（改列类型/宽度/可空性/默认值），必须由 Alembic 以"建新表 → 拷数据 → 换名"模拟。

实测（SQLite 3.53.1）：
```
ALTER TABLE task ALTER COLUMN status VARCHAR(32)  →  OperationalError: near "VARCHAR": syntax error
ALTER TABLE t2 MODIFY COLUMN a1 VARCHAR(64)       →  OperationalError: near "MODIFY": syntax error
```

> ⚠️ **认知修正**：旧结论"SQLite 不支持大多数 ALTER TABLE"**过于笼统**。实测能力矩阵见 §附录。**只有"改列定义"这一类必须重建表**，其余（含约束增删）在 3.53 上均可原地完成。

**2. 迁移必须 `PRAGMA foreign_keys=OFF` —— 否则静默丢数据（最高危）**

batch 重建表会 `DROP TABLE`。若 `foreign_keys=ON` 且子表有 `ON DELETE CASCADE`，**`DROP TABLE` 会触发级联删除，静默清空子表**。迁移不报错、返回成功，数据已经没了。

实测对照（重建 `task`，`artifact` 以 `ON DELETE CASCADE` 引用它）：

| `foreign_keys` | `artifact` 数据 |
|---|---|
| `ON`（默认） | ❌ **被清空**（`artifact=[]`） |
| `OFF`（迁移期） | ✅ 完整保留（`artifact=[(1, 1, 'data/outputs/a.png')]`） |

**落点**：`migrations/env.py` 通过 `make_engine(url, foreign_keys="OFF")` 注入；`PRAGMA` 必须在连接建立时（connect 事件）设置，**不能**在事务内设置（SQLite 的该 PRAGMA 在事务中无效）。

**3. 禁止在 `context.configure()` 之前用 `exec_driver_sql` 执行 PRAGMA**

这会**隐式开启外层事务**；Alembic 随后 `begin_transaction()` 加入该事务而不再独立提交，连接关闭时回滚 → `alembic_version` 的 INSERT 丢失。

症状极具迷惑性：**表建好了，但版本号没记**；再次 `alembic upgrade head` **报 `table task already exists`**。

**正确做法**：PRAGMA 全部由 `make_engine()` 的 connect 事件注入 —— 顺带保证"迁移连接"与"运行时连接"的 PRAGMA **强一致**，消除配置漂移。

**4. `alembic.ini` 必须纯 ASCII；优先改用 `pyproject.toml`**

Alembic 以 `encoding="locale"` 读取 ini（`alembic/util/compat.py::read_config_parser`）。中文 Windows 的 locale 是 **GBK**，UTF-8 中文注释直接崩：

```
UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position 107
```

实测三种处理：

| 方案 | 结果 |
|---|---|
| `alembic.ini` 保持**纯 ASCII**（注释用英文） | ✅ 可用 |
| `PYTHONUTF8=1` 环境变量 | ❌ **无效**（UTF-8 模式不改变 `locale.getencoding()`，而 Alembic 用的正是它） |
| **配置改放 `pyproject.toml` 的 `[tool.alembic]`** | ✅ **可用且可写中文**（TOML 由 `tomllib` 解析，**强制 UTF-8**） |

**采纳第三种**；若因故必须用 ini，则第一条。

**5. `autogenerate` 不会为自定义 TypeDecorator 生成 import**

生成的迁移体引用 `models.UTCDateTime()`，但文件头只有 `import sqlalchemy as sa` → 运行期 `NameError: name 'models' is not defined`。

**约定**：凡模型使用自定义类型，**必须手工审阅并补 import**；`env.py` 需配 `prepend_sys_path` 使该模块可导入。

**6. `alembic.ini` / 配置中不得硬编码数据库路径**

URL 从 `APP_DB_URL` 环境变量注入（`env.py` 里 `config.set_main_option`）。见 `project-rules` §2.3 安全红线。

**7. 迁移并入启动序列，且排在状态回收之前**

`alembic upgrade head` 属 `T-603` 启动序列，**必须先于**"回收 `running` → `interrupted`"执行（否则表尚不存在）。

### B. 连接与 PRAGMA

**8. PRAGMA 的作用域不同，注入方式也不同**

| PRAGMA | 作用域 | 实测 |
|---|---|---|
| `journal_mode=WAL` | **库文件持久**（写入文件头） | 裸连接读回 `wal` ✅ |
| `foreign_keys=ON` | **每连接**，SQLite 默认 **OFF** | 裸连接读回 `0` → 不设即**静默忽略外键**（实测：违反外键的写入不报错） |
| `busy_timeout` | **每连接**，但实际由 **DBAPI `timeout` 参数**决定 | `sqlite3.connect()` 默认 `timeout=5.0` → `5000`；`timeout=0` → **`0`** |

> ⚠️ **对 ADR-002 的措辞修正**：原文只说"设 `journal_mode=WAL` 与 `foreign_keys=ON`"。经实测，`busy_timeout` **并非"漏设"**（pysqlite 默认已给 5 s），真正的风险是**被设成 0**。因此约定为：**显式声明 `busy_timeout`，且禁止 `connect_args` 中出现 `timeout=0`**。

**9. 统一由 `make_engine()` 注入，运行时与迁移共用**

```python
PRAGMAS = {"journal_mode": "WAL", "foreign_keys": "ON",
           "busy_timeout": "5000", "synchronous": "NORMAL"}

@event.listens_for(engine, "connect")
def _apply_pragmas(dbapi_connection, _record):
    cur = dbapi_connection.cursor()
    for k, v in pragmas.items():
        cur.execute(f"PRAGMA {k}={v}")
    cur.close()
```

### C. 类型策略

**10. 时间字段用 `UTCDateTime`；`DateTime(timezone=True)` 在 SQLite 上是"假时区"**

实测：写入 `2026-10-08T10:33:41+00:00`

| 列类型 | 读回值 |
|---|---|
| `UTCDateTime` | `2026-10-08T10:33:41+00:00`（带 tzinfo，等值）✅ |
| `DateTime(timezone=True)` | `2026-10-08T10:33:41`（**tzinfo=None**）❌ |

两者落库文本**完全相同**（`2026-10-08 10:33:41.000000`，naive UTC）—— 差别只在读出时是否贴回 `tzinfo`。

**`UTCDateTime` 约定**：写入要求 aware（**naive 直接抛错**，把错误挡在写入侧，实测生效）；读出统一贴 `dateutil`-free 的 `datetime.timezone.utc`。

**11. 枚举用 `String`，**不**在 DB 层加 CHECK；结构字段用 `JSON`**

- 枚举字段（`TASK.status` / `MODEL.status` / `MODEL.format` / `ARTIFACT.kind`）取值**会增长** —— `canceling`、`interrupted` 都是后期补入的。取值约束放 **Python 层**（`Literal` / `Enum` + 校验），DB 存 `String(16|32)`。
- ⚠️ **SQLite 3.53 起 `ADD CONSTRAINT` / `DROP CONSTRAINT` 已可用**（旧版报语法错）。**能力变强反而更危险**：加 CHECK 变得很容易，而一旦加上，新增枚举值就必须走迁移。
- 结构字段（`TASK.params`/`resolved`、`MODEL.supported_backends`、`CALIBRATION.tile_curve`、`PRESET.params`）用 SQLAlchemy `JSON` 列。实测：嵌套 dict/list 与**中文**均原样往返。
- ⚠️ **`tech-arch` §4.1 ER 图把 `MODEL.supported_backends` 标注为 `string` 属类型简写**，落库必须是 **JSON 数组**，不得用逗号分隔字符串。

## 后果

**正面**
- 换库成本被限制在 `make_engine()` 的连接串 + 迁移脚本内；业务层零裸 SQL
- 11 条约束**全部有实测证据**，不是经验推测；其中第 2、3 条是**会静默损坏数据/阻塞启动**的缺陷，提前拦截
- `.venvs/sr-app` 已建立，5D 可直接开工

**负面 / 代价**
- **batch 重建表的成本随数据量线性增长**（拷全表）。本应用表都很小（任务元信息），可接受；但**若将来 `TASK` 行数上万，改列迁移需评估**
- 同步引擎意味着**同步端点占线程池**。当前并发为 1，无压力；若将来并发度调高需复核
- `UTCDateTime` 是自定义类型，**每次 `autogenerate` 后都要人工补 import**（第 5 条）

## 实施约束汇总（供 `T-602` / `T-603` 直接执行）

| # | 约束 | 落点 |
|---|---|---|
| 1 | `render_as_batch=True` + `compare_type=True` | `migrations/env.py` |
| 2 | 迁移期 `foreign_keys=OFF` | `env.py` 调 `make_engine(..., foreign_keys="OFF")` |
| 3 | PRAGMA 只走 connect 事件，不在 `configure()` 前 `exec_driver_sql` | `engine/db.py` |
| 4 | 配置放 `pyproject.toml` 的 `[tool.alembic]`（或纯 ASCII ini） | 后端工程根 |
| 5 | 自定义类型迁移后人工补 import | 迁移脚本审阅清单 |
| 6 | DB URL 从 `APP_DB_URL` 注入 | `env.py` |
| 7 | `upgrade head` 排在状态回收之前 | `T-603` 启动序列 |
| 8 | 显式 `busy_timeout`；禁止 `connect_args={"timeout": 0}` | `make_engine()` |
| 9 | 运行时与迁移共用 `make_engine()` | `engine/db.py` |
| 10 | `UTCDateTime` 替换所有 `DateTime(timezone=True)` | `models/` |
| 11 | 枚举 `String` 无 CHECK；结构字段 `JSON` 数组 | `models/` |

## 未决 / 移交

- **`ER 图缺索引定义`**（`tech-arch` §4.1）：本 ADR 验证了索引可正常创建，**具体索引清单见 §附录**，需回写 `tech-arch` §4.1（属 `T-602`）。
- **`MODEL.sha256` 唯一约束**、**保留策略清理** 等业务约束在 `T-602` 任务内细化。

## 附录 A · SQLite 3.53.1 ALTER TABLE 能力矩阵（实测）

| 操作 | 支持 | 说明 |
|---|---|---|
| `RENAME TO` | ✅ | |
| `RENAME COLUMN` | ✅ | 3.25+ |
| `ADD COLUMN` | ✅ | |
| `DROP COLUMN` | ✅ | 3.35+ |
| `ADD CONSTRAINT` | ✅ | **3.53 起可用**（旧版语法错） |
| `DROP CONSTRAINT` | ✅ | 同上 |
| **`ALTER COLUMN`（改类型/宽度）** | ❌ | `near "VARCHAR": syntax error` → **必须 batch 重建** |
| `MODIFY COLUMN` | ❌ | `near "MODIFY": syntax error` |

## 附录 B · 本 ADR 建议的索引清单（ER 图缺失，需回写）

| 表 | 索引 | 依据 |
|---|---|---|
| `TASK` | `(status, created_at)` | 任务中心按状态筛选 + 按时间倒序分页 |
| `ARTIFACT` | `(task_id)` | 按任务取产物 |
| `MODEL` | `(sha256)` **唯一** | 导入去重 |
| `CALIBRATION` | `(model_id, hardware_fingerprint, valid)` | 标定失效判据查询 |

## 附录 C · 验证工程

`.workbuddy/verify/orm-smoke/`（**开发期验证产物，产品不得 import**，与 `tools/` 同性质）：

| 文件 | 作用 |
|---|---|
| `models.py` | 2 张代表性表 + `UTCDateTime` + `DateTime(timezone=True)` 对照组 |
| `db.py` | `make_engine()` —— PRAGMA 注入的唯一落点 |
| `pyproject.toml` | `[tool.alembic]` 配置（中文注释可用） |
| `migrations/env.py` | batch 模式 + URL 注入 + 复用 `make_engine()` |
| `migrations/versions/0001,0002` | 初始建表 + 改列/加列（含数据保留验证） |
| `smoke_db.py` | 连接层 16 项断言（`report-connect.txt`） |
| `verify_migration.py` | 迁移层 12 项断言（`report-migration-fkoff.txt`） |
| `report-migration-fkON-cascade-lost.txt` | ⚠️ **对照实验（错误配置）**：迁移期 `foreign_keys=ON` 时 `artifact` 被级联清空的原始证据 —— 保留作为第 2 条约束的反例 |
| `probe_alter.py` | ALTER TABLE 能力矩阵（附录 A） |

**复现**：
```bash
cd .workbuddy/verify/orm-smoke
../../../.venvs/sr-app/Scripts/python.exe -m alembic upgrade head     # 建表
../../../.venvs/sr-app/Scripts/python.exe smoke_db.py                 # 连接层
SMOKE_FK=OFF ../../../.venvs/sr-app/Scripts/python.exe verify_migration.py  # 迁移层
```
