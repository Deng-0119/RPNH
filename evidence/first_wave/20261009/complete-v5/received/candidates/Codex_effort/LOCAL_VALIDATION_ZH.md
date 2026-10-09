# 给本地执行者的任务书

## 目标与授权边界

只验证本包固定 codec 修复。保留原 model、physical profile、provider、adapter、权限、Registry authority 和 Codex 0.155.0 pin；wire 字符串 `none` 只在 transport codec 解释。不得把 canonical None 全局变成字符串 none，不注入 medium，不给无 effort 模型补 supported choices。

可使用已经授权且已预置的官方 Codex 0.155.0 binary、现有 Python/pytest 及本地测试环境。不要为本任务安装、登录、降级/更换系统客户端，或改全局模型/用户配置。禁止真实 provider/model/付费请求、probe 请求、GitHub Actions、push、发布。此任务书也不授权提交或合并。

## 1. 来源和适用性

1. 记录 RPNH 实际 revision、工作区状态、OS/Python 版本。本包基线 ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4，H1 715468dab0b1bea07d7e94a7aa0606eaf194365c 的 12 目标 old blobs 已核对一致。
2. 运行 `verify_package.py`；再用 `verify_patch.py` 只读检查目标树。不得覆盖未提交工作。如果本地已应用 H2a，记录它的 patch/hash/修改路径；本包与其 5 路径不重叠，但组合行为没有云端重测。
3. 只在授权隔离 checkout 应用固定 patch，再核 12 个最终 SHA-256。新 commit 或本地同名文件有不同 bytes 时停止并回报，不自动改 patch，也不 reset/clean 用户工作。
4. `source/` 只是分发白名单。不得当作整仓运行、不得覆盖其他目录。

## 2. 零模型调用的原生前置条件

1. 读取实际 `cpn/frontend/codex_app_server.py` 中 resolve_codex_binary、codex_frontend_argv、run_codex_frontend 和仓库已支持的 launcher 路径；只读版本检查应返回 `codex-cli 0.155.0`。记录 binary SHA-256、真实版本输出与退出码。
2. 使用一次性目录存放测试 catalog/profile/config/session；RPNH_CONFIG 必须显式指向该目录。不得复用真实会话或更改真实配置默认值。存在 Codex HOME/config 写入时，也必须使用已支持、已核实的隔离机制；没有安全入口就 NOTRUN。
3. 参考 `tests/test_codex_compat.py::_wire_effort_profiles` 与 `review/test_review_codec_boundaries.py::profiles` 的合成 catalog。保留同一 exact model、不同 selection/physical profile 的冲突情形；没有真实 endpoint、credential 或可执行模型。至少三类：canonical None；真正 none 为默认；真正 none 为非默认（default=low）。
4. 只通过已支持且可审计的 fail-closed application double/本地验证入口阻断 provider、worker、模型探测及补偿执行。允许原生 Codex TUI 本身和本地 socket 的必要子进程，不可因统一 Popen guard 把客户端也替换成假客户端。应有请求/worker/网络边界计数及 guard 证据，不能仅声称“没输入 prompt 所以零调用”。
5. 先证明阻断可达，再启动 native。若本地没有安全支持入口、binary 版本不符、依赖缺失、AF_UNIX 不受支持或必须登录，记录精确阻塞并标记相应项 NOTRUN/BLOCKED_ENV。禁止放宽 pin 试跑 0.161，禁止去掉 guard、换 provider 或发一个真实请求来“验证”。本包没有新造 native CLI flags，也不要求临时开发未审阅的产品功能。

## 3. 真正的 0.155.0 gate

使用 stock 0.155.0 TUI 与实际 AF_UNIX transport；RPC Python double 结果只能单列 offline，不能替代本节。记录实际命令、guard/harness 文件和 hash、退出码、RPC 证据及终端截图（无秘密）。

- Bootstrap / model list：原生 typed client 接收，picker 展示正确 selection；无 effort selection 的 supportedReasoningEfforts 仍为空，defaultReasoningEffort wire 为 none。
- Picker / start：在三类 selection 间互切。分别核对 selection_id、physical execution profile、exact provider/model、adapter path 和 profile/adapter bytes。canonical None 必须仍为 JSON null/Python None；真 none 必须仍为字符串 `none`，不串选同一 exact model 的另一 profile。
- Config roundtrip：验证 model 与 effort 写入和 config/read；只写隔离 RPNH_CONFIG。记录两种 `none` wire 映射回正确 canonical 值，保存值与 physical profile 一致。
- 冷重开：关闭原生客户端和 server，重新开空的已建立 MainSession Registry；通过受支持路径恢复同一 profile，核原生 thread/start/resume response 被接收。记录读操作前后 canonical ordinal、writer epoch、lease，区分正常重启获得新 lease 与“同一 server 只读 resume 不改 lease”。不能把重启 lease 正常更新误报为异常，也不能忽略同一连接 resume 的写入。
- 真实 `none` 非默认：default=low 的 selection 选择 none 后配置保存/重开，仍定位非默认 none 的确切 physical variant。
- 无真实 turn 执行：不要求完成 turn。若检查 turn/start 解码，只允许停在受控 prepare_turn spy，并明确这不是 worker/model 成功执行；使用未受控 native prompt 触发真实 turn 禁止。
- 全程累计 provider/model/probe/worker/补偿调用为 0，并给出可审计的 guard/counter/readback。若计数无法建立，结果 INCONCLUSIVE，不得写 PASS。

## 4. AF_UNIX 与离线回归分开记账

按 LOCAL_COMMANDS.md 在已授权本地重跑原来 deselected 的 AF_UNIX 单项，保留实际 exit/stdout/stderr/JUnit。该现有单项用 /bin/true 代替前端进程，只证明 socket transport 启动路径；即便通过，也不能单独证明 stock TUI 接收。原生 gate 与此测试需要两份证据。

同一最终本地组合可运行既有 44-case 受影响回归和 4 个独立 probes。不得把 deselect/skip 算通过。若 H2a 已合入/应用，在结果 identity 明示，不沿用云端 43-pass 当本地组合结果。

## 5. 保留缺口、返回与停止

history 分页不属于本包，至少 2 committed turns 的 hydration 标记 DEFERRED_KNOWN_GAP；不用私有 DB 或合成 green 消掉它。0.161 schema/source 结果独立列为 STATIC_ONLY，native 支持仍被 pin 拒绝。

按 RETURN_SPEC_ZH.md 返回证据。所有要求执行完毕、或遇到所列安全/入口阻塞后即停止；不要自行安装、登录、发付费请求、变更支持版本或推送。任务最终可为 PARTIAL/NOTRUN，只有有证据的项目才能 PASS。
