# OpenCode 显式 native smoke gate

本轮仅准备测试代码。G2、G3 均为 **NOT_RUN**；没有安装或执行 OpenCode binary，没有 native/socket/PTY/provider/model 测试。生产入口与默认 pin 仍为 1.18.32。候选 1.18.35 是 certification-only，运行本文件的测试也不会自动 promotion。

## 执行前提与命令

仅在获准执行 native 后，在完整 RPNH Git checkout、Linux 或 WSL2 Linux 内运行。依赖必须预置；不自动安装、登录、访问 provider、改 PATH 或网络安全设置。必须提供已获取并核对来源的官方 stock Linux ELF binary 的绝对路径。测试拒绝脚本 wrapper、相对路径、缺失 binary、未知版本、错误 version probe 与缺失依赖。

```sh
python -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 \
  --opencode-certify-binary=/absolute/path/to/opencode \
  --opencode-certify-lane=contract

python -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 \
  --opencode-certify-binary=/absolute/path/to/opencode \
  --opencode-certify-lane=registry-read
```

本入口只支持 1.18.35 候选与 1.18.32 对照两个精确版本；产品 resolver 仍只把 1.18.35 接受为 candidate。1.18.32 可用同一显式入口作独立对照，仍须给出对应实际 binary。version/hash 只能建立本次执行身份，不能单独证明官方发行来源；另保存发布资产名称、归档来源与官方 digest 核对记录，测试不会猜测这些信息。只给部分选项、选择不到请求 lane、`--collect-only` 或请求 lane 被 skip，均不能得到绿色认证结果。

未给任何认证选项时，新增两个用例明确 skip，原 `tests/test_opencode_pty.py` 的默认行为不变。显式认证时只保留请求 lane 的一个测试，deselect 所有其它测试（包括另一 lane 及 PATH 选取 binary 的旧 PTY 测试），防止混跑。推荐始终使用上面精确测试文件，不带 `-k/-m` 排除条件。

## 已实现但未运行的有限场景

### contract / G2 subset

使用实际未修改 stock TUI、原 OpenCodeHTTPServer / OpenCodeProtocol、既有 ApplicationDouble / LocalGateway：

- 必需 bootstrap routes；stock `/rpnh-help` command 与 observation 输出
- 一个 stock 普通 prompt，对应恰好一次 double submit
- double fixture commit，经原 SSE/投影观察到独特 answer 输出
- 零 double physical call、HTTP thread/socket 与所启动 TUI 的有界清理

ApplicationDouble 的 committed 只属于协议 fixture，不能称 Registry committed。终端证据是有界、去控制码的文字输出，不是截图或完整终端模拟器；测试仍要求可见内容片段，不能只靠 route 200。

### registry-read / G3 subset

准备阶段在独立临时根，用原 MainSession、既有 `_TerminalPort`、原 run_agent_task/reconciliation 生成两个真实 committed turn，并释放准备 owner。仅此准备阶段预计两次 fake-port 调用，真实 provider transport 不参与。没有调用方可指定的真实 Registry 路径。

测量阶段使用原 FrontendGateway(lambda: RegistryFrontendApplication(..., resume=True))、原 Protocol/HTTP、实际 stock TUI。原 tick 经仅计数的 wrapper 原样调用；snapshot、history reader、Registry authority 和 PN projection 不替换。守卫仅装在 provider、submit、launch、compensation、profile mutation 等 effect 边界。

- `--session SID` 与关闭后 `--continue` 两次冷 attach，观察两个 turn 的问题/答案
- 原历史 role/内容顺序与跨 attach 稳定 message/part IDs
- stock `/rpnh-net`、`--show-resources`、`--resources-only` 三种 observation，逐项与原 project_registry_net + _filter_projection 完全相等
- 每个阶段比对 main/child event ordinal、完整原 thread projection 摘要、committed refs、execution/adapter identity、原 snapshot
- 测量新增 fake-port、provider factory、worker starts、submit/compensation 全为零
- 原 owner 的排他锁被验证，两个测量 owner 关闭后分别验证 lease 释放

`/rpnh-tasks` 会 reconcile child links，故不属于严格只读场景并被 guard 拒绝。正常 owner lease / writer epoch 获取与释放单独记录，不拿整个目录字节哈希误判 SQLite WAL 或锁文件。

## 明确未覆盖

两个 smoke 用例不等于原设计完整 G2/G3：尚未实现 picker 两 selection/effort 矩阵、主动断开 SSE/SDK retry、failure/abort 显示、两个并行 server 的隔离、超过 100 条合法 committed history 的分页、真实模型/provider、active worker recovery 或 upstream plugins/tools。`--continue` 单 session case 也不能证明多 session 最近项排序。

客户端使用原 isolated_environment，空 plugin/MCP 配置并禁用自动更新、models-fetch 和 project config；G3 的 Python fixture owner 也隔离 HOME/XDG 与环境，避免发现操作者的真实 profiles/plugins。当前脚手架不安装或改动 OS 防火墙/网络 namespace，不能证明 stock binary 在 OS 层仅能访问 loopback。该限制必须保留在报告中。

## 证据与结果解释

每次测试在 pytest 私有 tmp_path 写 `opencode-{lane}-evidence.json`，文件权限 0600，并在 pytest user_properties 暴露路径。保留首个失败类型/阶段、当前运行 source tree digest/Git HEAD/dirty 状态、选择 profile、实际 binary SHA-256、原严格 probe 结果与另一次真实 probe 的有界 stdout/stderr、OS/kernel/WSL/arch/libc/TERM/PTY 尺寸、backend、已做场景、未覆盖项目、有限 routes、去敏终端输出/退出码、fixture setup 与 measured counters、Registry 前后摘要和 owner 结果。password 及 HTTP Basic token 不保存。

失败或 BLOCKED 的 lane 立即停止正常流程，保留第一份证据，只做既定的测试资源清理；不自动放宽 routes 或 effect guard。运行结果不写回 manifest 的 `native_g2/native_g3` 状态。

只有全部本用例断言成立时，证据 status 才是 `passed-limited-smoke`。允许表述“该 OS/arch/binary 在所列有限 smoke 场景通过”；不允许说完整 G2/G3、新版全部兼容或默认支持已升级。完整矩阵、发行来源验证和 promotion 均另行审核。
