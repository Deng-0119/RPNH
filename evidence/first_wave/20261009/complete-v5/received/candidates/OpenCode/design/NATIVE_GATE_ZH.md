# OpenCode 分层认证与本地任务要求

本文件是下一任务的验收设计。当前只读研究没有实施 candidate profile，没有执行本文件中的产品测试或 native gate。不能将 `source-checks.json` 的通过替代以下 lanes。

## 三个层次分别报告

| Lane | 测什么 | 能证明什么 | 不能证明什么 | 当前状态 |
|---|---|---|---|---|
| G1 | exact source / profile / DTO fixtures / fail-closed 单元测试 | 已覆盖契约和有限参数化的离线正确性 | native、真实 TUI 显示、binary 包装或完整 Registry 流程 | 只完成本包 12 项源码审计；拟新增产品测试未实现 |
| G2 | 实际未修改 stock TUI + 原 HTTP/SSE/Protocol + ApplicationDouble | 实际 client attach、picker、request/response/event 契约 | 真实 Registry/harness、provider、工作流执行 | NOT_RUN |
| G3 | 实际未修改 stock TUI + 原 Protocol/HTTP/Gateway/RegistryFrontendApplication + 规范合成 Registry | 已提交历史冷恢复、只读页/PN 投影、零新执行及 owner 边界 | live model/provider、active worker 恢复、整个 harness 的执行正确性 | NOT_RUN |

G2 的 app.physical_calls==0 不足以证明真实 Registry。G3 必须使用真实原 Application/MainSession/Registry 读取路径，不把 ApplicationDouble 更名后当作通过。

## G1 最小离线测试

先实施获准的有限 profile 参数，再运行现有 focused tests，并增加这些断言：

1. 默认 resolver、版本常量别名、launcher、manifest upstream 均保持 1.18.32。生产入口收到 1.18.35 仍在 owner 创建前拒绝；没有读取 candidate 环境变量或配置的后门。
2. 显式 certification profile 仅接受 1.18.35 / exact commit。未知版本、latest、范围、suffix、stdout 额外文本、nonzero 和 timeout 拒绝。candidate/version 混配失败，不能跳过 probe。
3. 同一个 immutable profile 传入 probe/Protocol；health/session version 与选定值一致。两个同时存在的 protocol instance 不互相污染；关闭一个不改变另一个 profile。测试不修改全局常量。
4. 同一 source-extracted schema 对两 profile 投影跑现有 fixture。记录这是 RPNH subset，不是运行官方生成 SDK validator。若需 typed 编译，使用已预置可信 SDK/compiler，缺失记 BLOCKED，不能现装或偷偷降格。
5. create model.id、prompt model.modelID、command model string 分别正负向；provider/exact model/variant 往返仍回相同 RPNH profile。禁止 model fallback、effort 注入、跨 model variant。
6. envelope directory/payload/id、message/part 稳定 ID、SSE framing、重连去重、messageID/Idempotency-Key 冲突及重复提交。不能把 event identity 当 Registry terminal proof。
7. 未支持的 shell/fork/revert/share/auth/config write、附件、MCP、未知 agent/location 继续拒绝，零 effect。GET/history/reconnect 不触发 submit 或 compensation。
8. 默认 Linux/WSL gate 不放宽；模拟 win32/darwin 时在 owner 创建前拒绝。不能把 source path.sep 修复当平台启用。

建议 focused command（在完整已验证 current-main checkout，依赖已预置时）：

`python -m pytest -q tests/test_opencode_frontend.py tests/test_opencode_cli.py tests/test_opencode_application_boundary.py tests/test_opencode_registry_integration.py`

先检查测试依赖和收集结果。此命令不含 `test_opencode_pty.py`，避免未获准的 native 执行。registry integration 使用 repo 的 deterministic fake LLM port，虽无外部模型/API，仍要把 fake-port setup/execution 与“零模型的只读测量”区分。当前审计没有运行这条命令。

## G2 真实 stock client 与 application double

### 预备

- 仅 Linux 或 WSL2 内 Linux；分别记录发行版、kernel、WSL、arch、libc、TERM、PTY 尺寸、RPNH SHA。
- 使用预置且获准执行的官方 1.18.32 与 1.18.35 binary，路径分别显式给出。记录 version stdout、文件 SHA-256、release asset 名称/官方 digest、归档与解包来源。源码 SHA 和 binary hash 是不同证据。
- 不能把 version string 当 binary 身份充分证明。不得把旧 binary rename 为新版本或给 binary 套伪造 stdout 的 wrapper。
- 若没有 binary，报告具体版本/平台缺失，等待安装或获取授权；不 curl|sh、不 npm install、不改 PATH/default pin。
- 使用原 isolated_environment；独立 HOME/XDG/display、plugin=[]、MCP={}、autoupdate/models-fetch/project-config 禁用，无 provider/API key/global config/proxy。OS 级网络控制只保留本地 loopback，若未有该能力，不擅自改变安全设置，记录隔离限制。

### 测量

1. 先按 profile probe 确认实际 binary；随后启动原 OpenCodeHTTPServer 与固定 ApplicationDouble/LocalGateway。不要构造真实生产 Application。
2. 真 PTY 观察 required bootstrap routes 与可见 prompt；使用 1.18.32 对照防止 test fixture 自身漂移。
3. picker 切换至少两种 selection 和有/无 effort profile，通过 double 记录观察到的 provider/model/variant；不得只断言 route 200。
4. `/rpnh-help` 正常回显；一次普通 prompt 恰好一次 double submit；messageID/header 重送同一结果且无第二 submit；有意重复由 `/rpnh-send NEW_ID TEXT` 单独覆盖。
5. server 模拟 committed/failure/abort 后核 client 看到的最终消息与状态；double 的 committed 只是协议 fixture，不称 Registry committed 证据。
6. 切断并重连 SSE，再退出/重新 attach；观察 history/stable IDs，没有重复显示或额外 submit。SDK 内部 retry 与 TUI 外部 reconnect 分开记录。
7. 两个隔离 session/server 的消息、profile、认证不可串扰；退出 TUI/关闭 server 后端口和线程释放。
8. 所有 real provider/model/API calls 为零；保存 bounded routes、计数、去敏终端截图/输出与退出码，不记录 session-local password。

可声称的结果用语：`1.18.35 stock TUI 在 Linux/具体架构通过 G2 HTTP/SSE presentation contract，backend=ApplicationDouble。`

不可声称：`新版 OpenCode 全部兼容`、`Registry/harness 完整通过`、`native plugins 已认证`。

## G3 原 Application 与合成 Registry 的冷恢复

### 复用既有 fixture 与权威边界

现有 `test_basic_direct_session_projects_and_continues_without_resume_effects` 和 `test_actual_registry_net_matches_existing_inspection` 是实现起点。不要新建 read backend、复制 MainSession 逻辑或构造假的 committed UI transcript。

把合成 fixture 的准备阶段与测量阶段拆开：

- 在临时目录，用原 MainSession、现有 deterministic `_TerminalPort`、原 `run_agent_task` / reconciliation 构造至少两个真正 committed turn；记录准备阶段的 fake-port 调用数，真实 provider/API 调用始终为零。
- 若用规范 Registry API 直接构造提交 fixture，必须由原 reader 校验 authority refs/committed history，不手写不完整 SQLite 表、不改生产数据。
- fixture 完全终结，无 active worker、pending launch、terminal-but-uncommitted 或待修复链接。release/close 前一个 owner 后再启动测量。
- 对消息分页另准备超过 initial `limit=100` 的合法 committed history fixture；能用既有规范 fixture factory 就复用，不能用空壳消息列表冒充 Registry。若只做两 turn case，报告“冷恢复通过，长历史页边界未测”。

### 原实现启动与守卫

以原 `FrontendGateway(lambda: RegistryFrontendApplication(root, execution, resume=True), ...)`、原 Protocol、原 HTTPServer 启动。候选 profile 只进入 probe/Protocol；MainSession/Registry/TaskControl/PN 不改。

测试可给 provider factory、TaskControl._spawn、submit/launch 等 effect 边界安装 fail-on-call 计数守卫，以证明不会执行。不得替换 snapshot、history、Registry reader、PN projection、check_version 或 stock TUI。`rpnh-net` 是允许的读投影命令，不能用简单“所有 POST 都假成功”的代理。

原 gateway 会定时 app.tick，原 resume 会取得唯一 owner lease。这些事实必须保留。严格测量记录开始时的逻辑 Registry state/revision、committed refs、selection/execution identity、spawn/provider/submit/compensation counters，结束后逐项比较。不要用整个目录全字节哈希来误判 SQLite WAL/lease 文件；正常 lease 获取/释放单列，断言无第二 owner和结束释放。

### 只读场景

1. stock attach `--session SID` 冷启动，看见原两个 committed turn、顺序与内容正确；再测 `--continue` 最近会话选择。
2. 读取 session list/history，反复导航、退出重开、SSE 断连/重连；不输入普通 prompt 或执行 resume/rollback/reopen。
3. 长历史 fixture 测 initial page/older page 边界：无丢失、重复、越界 cursor；未做长历史 case 不可宣称分页全部通过。
4. `/rpnh-net`、`--show-resources`、`--resources-only` 的 observation 与原 `project_registry_net` + `_filter_projection` 完全一致；没有可显示资源节点时，真实结果为空即可，不发明节点。
5. 禁止用 `/rpnh-tasks` 作为严格只读对照，它当前会 reconcile child links；不混入 command resume/rollback/agent/workflow/message 等有执行效果操作。
6. 每个阶段新增 provider calls、fake-port calls、worker starts、submit、resume/compensation 均为零；Registry logical revision/committed refs/exact profile 不变，关闭后正常释放 owner lease。
7. 保留已拒绝 operation 的 negative tests；若 stock unexpectedly 请求一个 effectful route，阻断并报告具体 method/path/status，不自动放开 allowlist。

可声称的结果用语：`1.18.35 stock TUI + 原 RPNH Application 在合成已提交 Registry 上通过 G3 冷恢复/指定只读页面，测量阶段零模型与零新执行。`

不可声称：active worker recovery、真实模型推理、所有 frontend/backend/插件的完整兼容。

## 独立本地任务交接要求

待设计批准后，新任务先从 `Deng-0119/RPNH main` 读取最新 SHA，核对 scoped blobs，保留当前默认 1.18.32。不要混入其他尚未合并的 Codex/DSH work。读取 repo AGENTS.md 和所需本地 skills。

第一阶段仅实施 `CANDIDATE_PROFILE_DESIGN_ZH.md` 的有限参数化与测试，跑 G1，交差异和报告，不发布。G1 完成后，只有已授权且可执行的 stock binary 才跑 G2/G3。安装、登录、真实模型/API、Actions、push、默认 pin 更改均不在当前交接范围。

每个 native 报告至少包含：RPNH exact SHA、candidate exact version/tag commit、profile status、OS/WSL/arch、binary hash/provenance、lane/backend type、fixture setup vs measured counters、实际场景、failed/skipped/not-run、Registry 前后逻辑摘要、有限 route diagnostics、去敏输出以及 default pin 未变证明。

显式要求的 lane 遇到 binary/依赖缺失不得以 skip 当通过；状态为 BLOCKED。出现真实 provider/spawn/Registry logical mutation，立即 FAIL 并保留第一份去敏证据，停止该 lane，不扩权限继续。候选认证结果不自动提升到生产支持；promotion 是另一个需要审核的最小默认/支持集变更。
