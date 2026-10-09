# OpenCode 1.18.35 显式候选入口独立审查

## 结论

在本次有限 scope 内通过代码与纯 G1 审查，无未解决的阻塞项。默认生产入口保持 OpenCode 1.18.32；1.18.35 仅为显式测试候选。没有 promotion、第二 backend、第二 Registry 或 codec 迁移。

独立最终复跑：

- 提供的受限 G1 runner：274 passed，见 `g1-final.txt`、`g1-final/g1.xml`。
- 额外独立 profile/生产拒绝/manifest/AST 边界检查：33 passed，见 `profile-tests-final.txt`。
- 原生失败证据状态另以完全模拟 prepare 的 pytest 失败用例实际复现并确认修复；这是测试控制逻辑的预期失败，不是 native 执行。
- G2、G3：NOT_RUN。socket、PTY、stock executable、真实 Registry fixture execution、真实 provider/model/API、安装、登录、Actions、push 均未运行。

这不是完整 release、wheel、官方 SDK 编译或 stock TUI 认证。G2/G3 代码只准备了明确列出的有限 smoke；其脚本正确性和未来实际通过是两类证据。

## 来源与冻结范围

基线 commit：`8dd360e4848912a998dbd83220c3f0ce0a1caa86`，tree：`bd545364f73c571468c8d8aafa29d6fee386bded`。独立重新计算 baseline 全部 972 份文件的 Git blob，与完整 main tree 一致；这是本任务的 runtime/tests/scripts source subset，不是完整发行 checkout。

产品修改严格限于：

1. `cpn/frontend/opencode_protocol.py`
2. `cpn/frontend/opencode_launcher.py`
3. `cpn/frontend/opencode_compatibility.v1.json`

其余 719 份 `cpn/` 产品文件逐字不变，包括原 HTTP transport、FrontendGateway/RegistryFrontendApplication、MainSession、TaskControl、Registry、inspection/PN 和生产 CLI。原五份 OpenCode frontend/CLI/application/Registry/PTY 测试逐字不变。其余差异为两份中英 guide 与五份 test-only 文件；无删除。

所有复核文件的 SHA-256 和 Git blob 值在 `frozen-scope.json`。复核脚本为 `freeze_scope.py`。

## 产品边界

- manifest 是 version/commit 的唯一产品来源。default 是 1.18.32 / `545f51d26cc39a907d2867492d498d9607ea5fa4`；candidate 是 1.18.35 / `53d1eabb61e21162157817bf677da0a4ad3332e3`。
- profile frozen，公开 protocol.profile 不可重新赋值；resolver 只接收登记的精确候选。probe/protocol 在访问 subprocess 或 gateway 前拒绝未知、mixed commit/version、错误 certification_only 和伪类型。
- 生产 launcher 显式使用 DEFAULT_PROFILE，并将同一对象传给 probe/protocol；candidate 环境变量、未知配置或新版 binary 不改变生产选择。新版在 owner 创建前被拒绝。
- health/session 元数据读取实例 profile；两个 profile 实例不污染全局常量或彼此状态。既有 model/request/event/route 方法无新增执行语义。
- probe 拒绝 suffix、额外行、非零和 timeout。`opencode` 前缀只允许水平空白，不能用换行把多行输出包装成精确版本。
- Linux/WSL2 的既有入口边界不放宽；win32/darwin 在 owner/probe 前拒绝。

## 共享 schema 的明确例外

manifest 原有内容完整保留，包括 source-extracted subset 标签、$defs、responses、events 和旧 `/global/health.version` 的 `const: 1.18.32`。candidate metadata 是唯一新增 manifest 项。

旧 health const 不可能直接验证 candidate 的 1.18.35；测试显式证明该旧 schema 拒绝 candidate，再从同一 schema 仅替换 version const 进行候选验证。完整 health 对象另有精确 dict equality 断言，其他结构/字段继续校验。没有第二套 schema 或协议实现，也没有声称 candidate 通过未变的旧 health const。

## test-only native 入口静态审查

- 三个参数需一起显式提供。缺参数、未知版本、相对/缺失路径、脚本 wrapper、缺依赖、错误探测都阻止执行，不以 skip 产生绿色认证。
- 显式 lane 只保留对应一个测试，防止顺带运行旧 PATH-selected PTY、另一 lane 或无关测试。collect-only、被 skip、未建立 terminal evidence 均不能记为成功。
- 正常不提供参数时，新 native 测试 skip，既有 pinned PTY 选择行为不变。
- 原 `isolated_environment` 创建 HOME/XDG/display，清空 plugin/MCP，禁止自动更新、models-fetch、project config；stock 只走显式 binary 的 attach。G3 Python owner 也隔离环境/HOME，避免读取操作者真实 profiles/plugins。
- binary 路径、ELF 头、hash 与精确 version 只建立所测文件身份。官方 release 来源/digest 未由脚本独立认证；未来获准 native 任务必须先核官方已存在 binary，来源不明/缺失或需要安装、登录、模型调用时停止。
- 没有 OS 网络 namespace/firewall保证；当前不能声称 stock 实际零外部访问已经实证。

G2 使用既有 ApplicationDouble/LocalGateway 与原 HTTP/SSE/Protocol；fixture committed 不是 Registry committed。G3 使用原 MainSession 与 deterministic TerminalPort 准备两个真 committed turn，准备 owner 释放后再用原 FrontendGateway/RegistryFrontendApplication resume。snapshot、history reader、Registry authority、PN projection 不替换；effect guard 限 provider/submit/launch/compensation 等边界。

G3 逻辑状态/committed refs/execution identity 前后比较，原 tick 仅计数后原样调用，lease 获取/排他/释放单列；不用整个目录 byte hash。`/rpnh-tasks` 会 reconcile，被排除并拒绝；`/rpnh-net` 三种资源参数与原 projection/filter 相等。以上仍是静态核对，G3 fixture 与 native 实际执行均未运行。

## 审查中修复的问题

1. 独立复现 pytest call-phase 失败不抛回 yield fixture，导致 JSON 留在 running。现由 makereport 标记 failed 并落盘，finalizer 也终结未完成状态；独立 fake pytest 重跑确认。
2. 显式 lane 原先保留无关测试，改成只执行所选一个 lane。
3. 普通 pytest call PASS 但 run 未建立 passed-limited-smoke 的情况，现强制失败。
4. 作者进一步发现 teardown-after-pass / passed-then-skip 可残留旧磁盘 PASS；现在撤销 completed 并立即落盘 failed，两个纯回归通过。
5. tick 与 effect 混淆风险已通过单独原 tick 计数处理；G1 runner 自身禁用第三方 plugin autoload，建立 socket/process/thread/PTY guards。

未来仍须单独完成 native 授权、官方 binary provenance、精确平台实测。picker/effort、主动 SSE reconnect/retry、failure/abort、并发隔离、长历史分页等未实现完整矩阵，不据有限 smoke 声称全部兼容。

## 净补丁复核

`opencode-candidate.patch` SHA-256：`ee33ed95126989319e981683a308e3182d9b5b4e4c0f0a900e5bb8f111716bba`。

在独立全新 baseline 副本执行 `git apply --check` 和 `git apply` 成功；应用后全部 977 份 source 文件与最终实现字节相等。再对净应用版本运行同一 guarded G1：274 passed。见 `clean-apply-result.json`、`clean-apply-g1.txt` 和 `clean-apply-g1/g1.xml`。工作副本本身不需重复打包。

最终冻结 conftest 也重新跑过完整的纯 fake pytest 失败流程：退出码 1，落盘 failed，保留去敏首个失败类型。见 `fake-failure-result.json`、`fake-final-pytest.txt`；它不是 G2/G3 的失败或通过结果。
