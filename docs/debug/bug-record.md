# Bug 原始记录

> 按**倒序**追加（最新在最顶部）。编号 `Bug-XXX` 全局唯一、三位递增。
> 检索入口见 [`bug-index.md`](bug-index.md)；分类与关系口径见 [`bug-taxonomy.md`](bug-taxonomy.md)。

---

## [Bug-007] 硬件能力页「当前使用保守参数」横幅永不消失：标定结果**读不到** + 快照**不作废** + 按钮**无反馈**

- **ID**：Bug-007
- **日期**：2026-10-11
- **发生阶段/切片**：M7 步骤 8/9 期间（用户实测反馈；属 T-805 阶段 C 的**消费侧接线缺口**）
- **严重程度**：Medium（功能可见且**误导**：本机已标定却宣称"用的是保守参数"，并让人反复点无效按钮）
- **关联任务/变更**：无（Bug 修复）；承接 T-805（阶段 C 自标定）/ T-804（阶段 D 决策消费）
- **影响范围**：
  - `server/app/services/system_info.py` —— 能力快照的标定取数（`_snapshot` 注入的 provider）
  - `server/app/services/calibration_service.py` —— 标定完成后的快照作废
  - `server/app/services/settings_store.py` —— 只读派生键 `calibration_state` 的两个读口径
  - `web/src/views/HardwareView.vue` / `stores/system.ts` / `views/WorkbenchView.vue` —— 按钮反馈
- **现象**（用户原话）：「为什么一直显示"当前使用保守参数（性能与画质偏保守），完成本机标定后
  将自动切换为实测推荐值"，我点击"重新绑定"后没反应」。
  实测确认两件事：① 本机**已经标定成功**（`CALIBRATION` 表有指纹一致的有效记录，tile=256/T1），
  硬件页却一直显示保守档；② **手动触发标定完全成功**（10 s 后 `completed` + `stored=True`），
  但界面**毫无变化**。
- **复现步骤**：
  1. 确认存在指纹一致的有效标定记录（`GET /api/system/calibration`）；
  2. `GET /api/system/capabilities` → `using_fallback` 仍为 **true**、参数来源「保守取值（未标定）」；
  3. `POST /api/system/calibrate` → 等作业 `completed / stored=true`；
  4. 再取 `/api/system/capabilities` → **仍然 true**（点多少次都一样）。
- **根因**：一条链上**四处**接线问题（同一目标："让界面反映本机标定已生效"）：
  1. 🔴 **快照的标定取数丢了 `model_id`（核心）**：`engine/capabilities.py` 调
     `calibration_provider(result.hardware_fingerprint)` —— **只传指纹**。而
     `read_calibration_view(fp, None)` 的语义是「精确匹配 `model_id == None`，
     再退到通用记录（`model_id IS NULL`）」= **只认通用记录**；标定实际写入的记录
     **永远带探针模型 id**（`calibration_service._resolve_target` 查得到行）→
     该查询**恒为 None** → `decide_profile` 落保底档 → `using_fallback=True` **永久**。
     实测反证：`read_calibration_view(fp)` → `None`；`read_calibration_view(fp, 1)` → 命中；
     `decide_profile(calibration=None)` → `using_fallback=True / tile=64`，
     传 `model_id=1` → `False / tile=256`。
  2. 🔴 **能力快照是进程内缓存，标定入库后无人作废**：`engine/capabilities.py` 的注释
     早已写明「标定落定后需以 `refresh=True` 重建（T-805 接线时在标定完成回调中触发）」
     —— **这根线当时没接上**。于是即便修好第 1 处，运行中的进程仍会继续返回
     **启动时**构建的那份结论（实测：往缓存里塞污染值后仍被原样返回）。
  3. 🔴 **前端按钮只投递、不跟结果**：`HardwareView.runCalibration()` 发完
     `POST /api/system/calibrate` 就结束 —— 不轮询作业状态、**不刷新能力快照**。
     后端异步返回，用户除了一个 toast 之外看不到任何变化 = 「点了没反应」。
     附带：模板里的 `<Refresh />` **从未 import**（按钮无图标）。
  4. 🟠 **同一件事两个相反结论**：设置页「标定状态」读
     `settings_store.effective("calibration_state")` —— 而 `effective()` **不走只读派生**，
     恒返回声明默认值 `"pending"`；`list_settings()` 走 `_readonly_value` 返回真实值。
     且 `_calibration_state()` 只看「**有没有** valid 记录」，**不看是否匹配当前指纹/模型** ——
     换驱动 / 换模型后旧记录仍在（`valid=True`）→ 设置页说「已完成」、硬件页说「未标定」。
     `build_diagnostics()` 也吃这个坑：诊断 JSON 的 `calibration.state` 永远说"未完成"。
- **解决方式**：
  1. 新增 `system_info.read_snapshot_calibration(fingerprint)` = 按
     **「探针模型 × 指纹」**取数（`read_calibration_view(fingerprint, probe_model_id())`），
     `_snapshot()` 改用它；新增 `probe_model_id()`，与 `calibration_service._resolve_target`
     **同一条查法**（`Model.path == path.name`），保证"写哪条"与"读哪条"是同一个模型。
  2. 新增 `system_info.invalidate_capability_snapshot()`；
     `calibration_service._run` 在 `stored=True` 后调用它（失败只告警，不影响标定本身）。
  3. 前端：`system.calibrate()` 返回作业号；新增 `waitForCalibration(jobId)` 轮询到终态
     （用 `job_id` 对齐，避免把上一轮的终态当成这一次的结果）；`HardwareView` 等终态后
     `system.load()` 刷新快照，并按 `completed+stored / skipped / failed` 给差异化提示；
     补 `Refresh` 图标 import；`WorkbenchView` 的 toast 去掉「可去硬件页查看进度与结果」
     （本页并没有该区块，属**不实承诺**）。
  4. `settings_store.effective()` 对**只读派生键**走 `_readonly_value`；
     `_calibration_state()` 改为**复用能力快照的 `using_fallback`**（同一个判据，一处理清）。
- **修复产物/版本**：`server/app/services/system_info.py`、`calibration_service.py`、
  `settings_store.py`、`web/src/{stores/system.ts,views/HardwareView.vue,views/WorkbenchView.vue,
  api/client.ts,types/api.ts}`、`scripts/test-script/verify_t804_engine.py`、`verify_t609_settings.py`
- **验证证据**：
  - **真实环境（运行中的开发后端，HTTP 实测）**：修复前 `using_fallback=true`
    （而标定记录明明存在）→ 修复后 `using_fallback=false`、`active_backend=CUDAExecutionProvider`；
    设置页 `calibration_state` 与硬件页**同为**「已完成」。
  - **快照作废链路三级验证**（同一进程，可判定）：
    `① 冷构建 False → ② 缓存命中把新结论盖住 = True → ③ 作废后重建 = False`。
    第 ② 步正是"标定成功却看不到变化"的直接证据。
  - `verify_t804_engine.py` 新增 **+3 断言**（纯指纹口径取不到该模型记录 = 缺陷原形 /
    快照口径能取回 / 作废后快照确实**重建**）→ **138 通过 / 0 失败**（改前 135）。
  - `verify_t609_settings.py` 新增 **+1 断言**（只读键两个读口径必须同值）→ **48/0**（改前 47）。
  - 回归：`t604 59/0`、`t608 15/0`、`t805 63/0`、`t803 78/0`；前端 `vue-tsc -b` + `vite build` 通过。
- **受影响阶段重验**：M7 步骤 8/9 相关回归 —— 通过
- **关联关系**：caused-by: T-805（阶段 C 落地时只补了"写"，消费侧接线留了缺口）；
  similar-to: Bug-004（同属「实现与消费口径漂移」）；**同族教训**见 Bug-006
  （写入侧变了、读取侧仍按原样比对 → 静默失配）
- **状态**：已解决

---

## [Bug-006] 下载目录「已下载」判据用文件名 stem 精确比对，被防覆盖后缀 `_n` 打断 → 已下载的条目仍显示「下载」并可重复下载

- **ID**：Bug-006
- **日期**：2026-10-11
- **发生阶段/切片**：M7 步骤 8/9 期间（用户实测反馈；属 T-713 / T-716 / T-719 的收口缺口）
- **严重程度**：Medium（对用户可见，且**可自我叠加**：按钮回到「下载」后每次点击都会再下一份权重）
- **关联任务/变更**：无（Bug 修复，按规范不计入功能变更）；承接 T-713 / T-716 / T-719
- **影响范围**：
  - `server/app/services/download_service.py::_existing_model` —— 目录 `downloaded` 判据
    **兼** `POST /api/models/download` 的幂等闸门（同一函数两用）
  - `server/app/services/model_registry.py::_download_spec_for` —— 下载链模型的适配简介 / 架构名反查
  - `server/app/services/model_registry.py::save_import` —— 撞名改名逻辑
  - `server/app/services/conversion_service.py::_find_existing` —— 转换产物幂等复用
  - 前端**零改动**（`ModelsView.vue` 的按钮态本就消费服务端 `downloaded` 字段）
- **现象**（用户原话）：「模型没有已下载这个状态，即使下载了，待转化时还是显示可以下载。」
  即：模型**已下载并登记**（`.pth`，`needs_convert` 待转换态），但「获取更多模型」区块
  里该条目**仍显示「下载」**按钮，点击后**会再下一份**。
- **复现步骤**：
  1. 从「获取更多模型」下载任一模型 → 登记成功（`.pth`，`needs_convert`）；
  2. 删除该模型的**登记**（`DELETE /api/models/{id}` 的既定语义：只删登记、**不删磁盘文件**）；
  3. 再次下载同一条目 → 因同名文件仍在，落盘被**改名**为 `X_1.pth`（防覆盖）；
  4. 回到模型库：该目录条目 `downloaded=false`、按钮回到「下载」；此后**每次点击都会再下一份**。
  - 现场证据（本机真实库）：命中条目 `dl_2x_ani4k_compact` —— 磁盘上
    `2x_Ani4K_Compact_35000.pth`（00:05，孤儿文件）与 `2x_Ani4K_Compact_35000_1.pth`
    （14:18，登记为 `mdl_26`）并存，后者是第 3 步的产物。
- **根因**：**同一个 stem 的两种语义被当成一种**。
  `save_import` 遇到同名文件会**改名**（`X.pth` → `X_1.pth`，只防覆盖、绝不覆盖），
  于是 `Path(model.path).stem`（`X_1`）**不再等于**来源文件名 stem（`X`）。
  而两条反查原先都按 stem **精确比对** → 一旦撞过名就**永久静默失配**（不抛错，只是判成「没下过」）：
  - `GET /api/models/download-catalog` → `downloaded=false`（实际 `mdl_26` 就在库里）；
  - 模型库卡片 `architecture` 从 `SRVGGNetCompact` 退化成格式名 `pth`、`description`
    退化成「用户导入模型」—— 正是 T-716 刚修好的那条反查被撞名打断。
  🔑 **同一模式全仓共三处**（`_existing_model` / `_download_spec_for` / `_find_existing`），
  只修第一处即为**补丁式修复**。
  ⚠️ **另有一处独立缺陷**（同段代码）：撞名循环 `dest = f"{dest.stem}_{n}…"` 对**已改过的名字再改**，
  第二次撞名产出 `X_1_2`（而非 `X_2`）—— 后缀逐次复合，名字越滚越长、越难反查。
- **解决方式**：
  1. **新增 `model_registry.normalized_stems()`**：把 stem 展开为**全部等价形态**
     （穿透 `__from<n>` 转换尾缀与 `_<n>` 防覆盖后缀）。
     🔑 **关键设计**：**不做**无条件 `re.sub(r"_\d+$", "", stem)` —— 目录里合法名
     `2x_Ani4K_Compact_35000` 本来就以 `_35000` 结尾，盲剥会把**合法名削成**`2x_Ani4K_Compact`。
     故只**枚举候选形态**，由调用方拿**已知来源 stem** 去命中，剥错也不会误判。
  2. `_existing_model` / `_download_spec_for` / `_find_existing` 三处反查**统一**改用它（一次收口）。
  3. `save_import` 撞名改为**内容感知**（回答「重复下载要不要覆盖」）：
     - 主（+配套）文件 sha256 与待落盘内容**一致** → **复用**已有文件
       （命中的就是"登记被删、文件残留"的孤儿文件）：重复下载同一权重不再占第二份盘，
       且**保住规范文件名** —— 从源头不再产生 `_n`，反查失配不再复发；
     - 内容**不一致** → 改名让路（`X_1`），**绝不覆盖**别人的文件。
  4. 抽出 `_free_dest()` 修掉后缀复合缺陷（`X_1` → `X_2`）。
- **修复产物/版本**：`server/app/services/model_registry.py`、`download_service.py`、
  `conversion_service.py`、`scripts/test-script/verify_t604_models.py`
- **验证证据**：
  - **真实库直测**（只读，不改数据）：修复前 `dl_2x_ani4k_compact.downloaded=false`；
    修复后 `downloaded=true / model_id=mdl_26`；`GET /api/models` 中 `mdl_26` 的
    `architecture` 由 `pth` 恢复为 `SRVGGNetCompact`、简介恢复为目录文案
    （**运行中的开发后端已热载入新代码**，为 HTTP 实测而非仅单元调用）。
  - `verify_t604_models.py` 新增第 **6c** 段 **+15 断言**：归一化**不误剥**合法
    `_<数字>` 结尾名 / 展开出 `_1` 与 `__fromN` 形态 / 用户导入模型**不被误命中** /
    撞名后的源 `.pth` 与转换产物**仍命中文档简介** / 同内容撞名**复用同一路径**（不再堆 `_1`）/
    内容不同撞名**改名让路且原文件未被覆盖** / 后缀**不复合**（`_2` 而非 `_1_2`）。
    → **59 通过 / 0 失败**（改动前 44/0）。
  - 回归：`t602 17/0`、`t605 24/0`、`t606 27/0`、`t607 14/0`、`t608 15/0`、`t609 47/0`、
    `t700 33/0`、`t802 87/0`、`t803 78/0`、`t807 25/0/4 跳过`（跳过 = 前置权重资产缺失，
    非本轮引入）；`t806 119/2/1` —— `git stash push -- server/ scripts/` 回基线复跑结果
    **完全相同**（同为 119/2/1，失败项为 ncnn 前置资产缺失）→ **既有问题，非本轮回归**。
- **受影响阶段重验**：M7 步骤 8/9 相关回归 —— 通过
- **关联关系**：caused-by: T-713（下载链）/ T-716（简介反查）/ T-719（撞名改名约束）；
  similar-to: Bug-004（同属「实现口径与反查口径漂移」）；
  与 T-719 已留痕的「不要把产物预放到 `imported/<name>`，否则 `_find_existing` 的定名约定失配」
  **同属一个根因族**（文件名被改名后反查失效）
- **状态**：已解决

---

## [Bug-005] E2E「非法格式未产生任何后端请求」用裸计数，被环境残留 404 误伤

- **ID**：Bug-005
- **日期**：2026-10-11
- **发生阶段/切片**：8-测试（回归执行）
- **严重程度**：Low
- **关联任务/变更**：CHG-20261011-05
- **影响范围**：`.workbuddy/verify/ui/run_ui_e2e.cjs` 第 7 节；不影响产品代码
- **现象**：UI E2E 由 88/0 变为 **87/1**，失败项为「非法格式未产生任何后端请求」，
  且**连续两次复跑均稳定失败**（非偶发）。
- **复现步骤**：`node .workbuddy/verify/ui/run_ui_e2e.cjs`（`data/` 内历史任务较多时）。
- **根因**：该断言用**全局计数裸比较** `apiErrors.length === apiBefore`。
  `apiErrors` 收集整个运行期**所有** `/api/` 的 4xx/5xx；本节只等待 600 ms，
  期间工作台「最近任务」面板渲染历史任务的缩略图，其源文件可能已被保留策略清理
  （任务记录无删除端点，行仍渲染）→ `files/content` 404 落进窗口即误红。
  ⚠️ **同一文件末尾的健康度检查与 `run_t706_acceptance.cjs` 早已采用
  「`files/content` 404 = 环境残留」的豁免口径**，唯独此处漏改，三处口径不一致。
  **取证**：单独写探针复现该场景，窗口内新增 API 错误为 **0** —— 证明噪声来自
  E2E 前六段累积的历史任务，而非本次上传动作。
- **解决方式**：抽出 `isStaleFile404(e)` 判据（`404` + `/api/files/*/content`），
  断言改为只计**非残留**的新增错误；健康度检查改为复用同一函数，三处口径统一。
  残留数量以 `info` 记为观察项（不隐藏，如实登记）。
- **修复产物/版本**：`.workbuddy/verify/ui/run_ui_e2e.cjs`
- **验证证据**：修复后 E2E **88 通过 / 0 失败**；日志如实打出
  「环境残留（非本脚本造成）：2 个历史文件 content 404」——印证噪声来源。
- **受影响阶段重验**：8-测试 —— 通过
- **关联关系**：similar-to: Bug-003（同属「测试测的不是它以为的东西」）；教训与
  `run_t706_acceptance.cjs` 既有豁免口径同源
- **状态**：已解决

---

## [Bug-004] 验证脚本 `verify_t604` / `verify_t609` 断言未随功能增补更新，长期红灯

- **ID**：Bug-004
- **日期**：2026-10-11
- **发生阶段/切片**：8-测试（回归执行）
- **严重程度**：Medium
- **关联任务/变更**：CHG-20261011-03
- **影响范围**：验证脚本 `scripts/test-script/verify_t604_models.py`、`verify_t609_settings.py`；不影响产品运行时
- **现象**：两脚本各长期稳定失败 1 项：
  - `t604`「原因文案指向离线转换」——`unavailable_reason` 实际为「这是训练权重格式（.pth/.safetensors），需转换为 ONNX 后才能使用」；
  - `t609`「键集合完整（11 项）」——实际返回 **12** 项，多出 `download_proxy`。
- **复现步骤**：`cd server && uv run --active python ../scripts/test-script/verify_t604_models.py` → 43 通过 / 1 失败；`verify_t609_settings.py` → 46 通过 / 1 失败。
- **根因**：**断言与实现漂移**——两处都是「功能已增补、断言未同步」：
  1. `t604`：T-719 把该文案由内部术语「离线转换」改说人话，断言仍匹配旧字面；
  2. `t609`：T-714 新增 `download_proxy` 设置项（`settings_store._SPECS`），但 `EXPECTED_KEYS` 清单漏登记。
- **解决方式**：
  1. `t604` 判据改为匹配**产品约束本身**（「说清训练权重 + 需转 ONNX」），不再匹配具体措辞；
  2. `t609` 补齐 `download_proxy`，并把失败信息改为输出**对称差**（`缺=… 多=…`），使下次漂移可自诊断。
- **修复产物/版本**：`scripts/test-script/verify_t604_models.py`、`scripts/test-script/verify_t609_settings.py`
- **验证证据**：`t604` **44/0**、`t609` **47/0**；离线批次合计 **386 通过 / 0 失败**
- **受影响阶段重验**：8-测试 —— 通过
- **关联关系**：similar-to: Bug-003
- **状态**：已解决

---

## [Bug-003] `verify_t603_startup.py` 端口写死 8000，与开发后端冲突时**「测的东西没跑，断言却绿了」**

- **ID**：Bug-003
- **日期**：2026-10-11
- **发生阶段/切片**：8-测试（回归执行）
- **严重程度**：**High**（测试可信度缺陷，非产品缺陷）
- **关联任务/变更**：CHG-20261011-03
- **影响范围**：验证脚本 `scripts/test-script/verify_t603_startup.py`；**产品代码无缺陷**
- **现象**：开发后端运行在 8000 时执行该脚本，出现自相矛盾的结果：
  - `[PASS] A1 首次启动成功（空库自动迁移）`
  - `[FAIL] A2 迁移日志出现`、`[FAIL] A3 数据库文件已创建`
  - 随后 `sqlite3.OperationalError: no such table: task`，脚本带**裸 Traceback 中断**，
    场景 B / C **根本没跑到**，报出的失败数因而失真。
- **复现步骤**：`cd server && uv run --active python ../scripts/test-script/verify_t603_startup.py`（同时保持开发后端占用 8000）。
- **根因**：脚本把端口**写死 8000**（`start_server` 的 `--port`、`wait_health` 的 URL、`APP_PORT`），
  而它要验证的是「**起自己的后端进程**、在**临时数据目录**上完成迁移」。端口被占时：
  1. 子进程 bind 失败**静默退出**，stdout 为空（连 bind 错误都没打出来）；
  2. `wait_health` 轮询 8000 拿到的是**开发后端**的 200 → **A1 假通过**；
  3. 临时库从未迁移 → A2/A3 失败 → 播种 `Task` 时报表不存在。
  即 **「测的东西压根没跑，断言却绿了」——比直接失败更危险**（与 T-706「断言把缺陷固化成了期望」同族）。
- **解决方式**：
  1. 新增 `free_port()`（`socket.bind(("127.0.0.1", 0))` 由内核分配空闲端口），端口改为参数贯穿
     `start_server` / `wait_health` / `make_env`；
  2. A3 失败时**显式判停**并给出可读结论，不再带裸 Traceback 中断（保证 B/C 场景仍能跑到）；
  3. `wait_health` 失败时回带子进程日志尾部，便于定位。
- **修复产物/版本**：`scripts/test-script/verify_t603_startup.py`
- **验证证据**：**开发后端保持运行**（未停）的前提下重跑 → **13 通过 / 0 失败**（A/B/C 全跑通）。
  这同时构成根因的判定性证据：换端口后即通过，说明失败源于端口冲突而非产品逻辑。
- **受影响阶段重验**：8-测试 —— 通过
- **关联关系**：similar-to: Bug-004；教训与 T-706「断言把缺陷固化成了期望」同源
- **状态**：已解决

---

## [Bug-002] 模型库补齐多倍率后，`verify_t704` / `verify_t705` 选模型未按 scale 过滤，提交必被 400 拒绝

- **ID**：Bug-002
- **日期**：2026-10-11
- **发生阶段/切片**：8-测试（回归执行）
- **严重程度**：Medium
- **关联任务/变更**：无（**待处理**，未纳入本轮修复范围）
- **影响范围**：验证脚本 `scripts/test-script/verify_t704_degradation.py`、`verify_t705_cancel_concurrency.py`
- **现象**：对**运行中的开发后端**执行，两脚本大面积失败：
  - `t704` 27 通过 / 4 失败 / 1 跳过；
  - `t705` 3 通过 / 5 失败 / 4 跳过，报「提交返回 201 —— 400」。
  错误体：`VALIDATION_ERROR`「所选模型「2xHFA2kAVCCompact（转换）」的放大倍数为 ×2，与请求的 ×4 不一致」。
- **复现步骤**：开发后端运行中执行 `verify_t705_cancel_concurrency.py`。
- **根因**：**脚本的选模型逻辑与模型库的 scale 维度漂移**。
  两脚本的 `pick_model()` 都只按 `min_vram_mb` 排序取 `usable[0]`，**从不按 `scale` 过滤**；
  而 `create_task()` 把 `scale` 写死为 `4`。T-717 起模型库补齐 ×1/×2/×3，
  「显存门槛最低」的那个恰好是 **×2** 模型 → 提交必被后端前置校验拒绝。
  ⚠️ 值得肯定的是：**后端的拒绝行为是正确的**（T-706 引入的倍数前置校验在正常工作），
  错的是脚本假设了「库里总有一个 ×4 模型门槛最低」。
- **影响范围（修复后扩大）**：`verify_t704` / `verify_t705` / **`verify_t706_acceptance`** 三处。
  🔑 **修第一处时发现的连锁**：Bug-002 并非「两个脚本各犯一次」，而是**同一个模式散落三处**——
  每处都是「选模型只按 `min_vram_mb` 排序」+「`create_task` 把 `scale` 写死 4」。
  - `t704` 的 `pick_model()` + **`t704` 第 6 段另有一处选「无标定模型」**（同文件第二处）；
  - `t705` 的 `pick_model()`；
  - `t706_acceptance` 的 `available_models()`（该脚本 `f01` 用 `model.get("scale")` 推导、
    其余用例靠默认值 4，**两种口径并存**），且它选中的是 **×1** 模型 `1xDeH264_realplksr`。
  仅修 `pick_model()` 会留下两处继续红 —— 属**补丁式修复**，故按「全局影响评估」一并收口。
- **解决方式**：三脚本各自引入 `SCALE = 4` 常量，并让**选型与提交共用它**（结构上杜绝再漂移）：
  1. 选型处一律加 `m.get("scale") == SCALE` 过滤（`t704` 两处、`t705` 一处、`t706` 一处）；
  2. `create_task(..., scale: int = SCALE)` —— 默认值不再是字面量 `4`；
  3. `t706` 的 `f01` 统一为 `model.get("scale") or SCALE`，消除「两种倍率口径并存」；
  4. **准备阶段失败自诊断**：取不到模型时打印「库存按倍率（总数/可用）」并指明该改哪，
     避免只报「准备失败」无从下手。
- **修复产物/版本**：`scripts/test-script/verify_t704_degradation.py`、
  `verify_t705_cancel_concurrency.py`、`verify_t706_acceptance.py`
- **验证证据**：
  - `t705` **3/5/4 跳过 → 24 通过 / 0 失败 / 0 跳过**；
  - `t704` **27/4/1 跳过 → 31 通过 / 0 失败 / 1 跳过**；
  - `t706` **46/6/2 跳过 → 62 通过 / 0 失败 / 0 跳过**；浏览器级 `run_t706_acceptance.cjs` **46/0**；
  - 离线回归批次 **554 通过 / 0 失败**；浏览器级 E2E **88/0**。
  - 基线判定：`git stash push -- server/ scripts/` 回基线复跑，失败数完全相同
    （t704 27/4、t705 3/5）→ 确认为既有问题，与 CHG-20261011-01/02 的改动无关。
- **受影响阶段重验**：8-测试 —— 通过
- **关联关系**：similar-to: Bug-004（同属「测试假设与实现/数据漂移」）；caused-by: T-717（模型库补齐多倍率）
- **状态**：**已解决**（2026-10-11）

> **修复过程中附带发现并修复（Bug-005）**：`t704` 的「跑任务不改写模型元信息」在
> 本轮一度由 PASS 转 FAIL。取证结论：断言比对的是**整个模型库的 before/after 快照**，
> 而 `data/` 里历史任务增多时会有新模型登记 / 缩略图 404 落在该窗口内 → 属环境噪声。
> 该断言在模型库稳定后复跑即通过（见 Bug-005）。

---

## [Bug-001] 后端 `tier_reason` 直出 ORT provider 类名与内部术语

- **ID**：Bug-001
- **日期**：2026-10-11
- **发生阶段/切片**：5D-后端实现（文案收口）
- **严重程度**：Low
- **关联任务/变更**：CHG-20261011-02
- **影响范围**：硬件能力页「判定依据」展示；不影响判定逻辑与契约
- **现象**：`GET /api/system/capabilities` 的 `tier_reason` 返回
  `存在已验证的 GPU 后端（CUDAExecutionProvider）`，把 ORT 内部实现名泄漏到用户界面。
- **复现步骤**：调用 `GET /api/system/capabilities`，查看 `tier_reason` 字段。
- **根因**：`server/app/engine/capabilities.py::_derive_tier_hardware()` 第 98 行
  `ev = "、".join(gpu_verified)` 把 provider 名原样拼进文案。
  🔑 **`ep_verify.provider_label()` 早已定义规范名映射，但全项目零调用点**（典型的「工具写了、忘了接」）。
- **解决方式**：T1 分支改用 `provider_label()`；其余三分支顺带清掉「EP 真实性验证」「§6.2 约束 4」
  等内部术语。判定逻辑与契约零变更。
- **修复产物/版本**：`server/app/engine/capabilities.py`、`scripts/test-script/verify_t803_engine.py`
- **验证证据**：`t803` 76 → **78/0**（新增 2 条防回归断言：`tier_reason` 不得含 `ExecutionProvider`、
  须用规范名）；实测 API 返回「检测到已验证可用的 GPU 加速方式（CUDA）」；UI e2e **88/0**。
- **受影响阶段重验**：8-测试 —— 通过
- **关联关系**：无
- **状态**：已解决
