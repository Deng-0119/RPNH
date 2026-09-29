# RPNH Harness

[English](README.md) | 中文

[查看实际图](#先看一次多-agent-运行) · [快速开始](#快速开始) ·
[案例](#案例目录) · [前端](#前端与宿主集成) · [文档](#文档)

RPNH 是一个用于组合语言模型、原生工具和可复用工作流的 provider-neutral Agent 执行框架。
持久化 **Registry** 记录执行与资源历史；类型化 **PetriNet** 决定任务何时具备执行条件，
以及已结算产物如何启用后续步骤。

同一 runtime 支持对话式主会话、隔离的子任务、多 Agent 工作流、版本化 workspace 资源和
只读查看。Basic、Codex、OpenCode 与 DSH 是围绕该 runtime 的展示或宿主入口，不是模型、
workspace 或恢复状态的独立所有者。

| 概览 | 当前边界 |
|---|---|
| 平台 | Linux 或 WSL2，Python 3.11+ |
| 发布 | `v0.1.0rc1` 公开预发布版 |
| 安装 | 源码 checkout 或 GitHub Release wheel/sdist |
| 模型 | 用户自有 provider 与 exact-model catalog；不预选 route |
| 执行 | 主会话，以及相互独立的 task/workflow Registry |
| 查看 | 终端投影和本地只读 PetriNet 看板 |
| 恢复 | owner-stop resume 与用户选择 checkpoint 的 reopen |

## 先看一次多 Agent 运行

下图来自仓库内 `parallel` 案例完成后的真实 Registry 看板。`prepare` 会同时启用两个独立
Agent；只有两个登记产物都可用后，`join` 才具备执行条件。

![RPNH 并行多 Agent 实际概览](examples/workflow_patterns/assets/parallel-overview.png)

完成[快速开始](#快速开始)后，无需 provider 或 API 凭据即可复现：

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-readme.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

Overview 强调 Agent 之间的结构；同一次运行的 PetriNet 视图会展示执行实际使用的
transition、交接 place、并行分叉弧和全输入 join：

![RPNH 并行流程的实际 PetriNet 投影](examples/workflow_patterns/assets/parallel-petrinet.png)

这些图片保留真实图投影、聚合执行状态和控件，只移除了 run 专属 checkpoint 身份及本地
时间。看板始终只读；完成判定依赖 Registry 终态证据和已登记最终结果，不能用图片或
进程退出替代。

## RPNH 提供什么

- **在同一工作流中组合 Agent 与已有代码。** 模型节点负责推理与沟通，原生插件针对
  已声明输入输出执行计算或已有业务逻辑。
- **明确表达并行与 join。** PetriNet 的 place、arc 和 token 表达 fan-out、同步、资源读取
  与完成条件，不把执行简化成串行聊天记录。
- **通过统一入口管理独立工作。** 主会话可以创建、查看、发送消息、停止和 reopen 子任务；
  每个子任务保留自己的 Registry、执行状态与结果。
- **保留 workspace 版本证据。** 资源版本把输入和产物关联到具体执行。结算记录逐路径
  create、update、delete 历史，并保留并发修订之间的冲突。
- **统一 provider 与前端边界。** 用户自行配置模型 route；受支持的前端共用 RPNH 执行
  语义，不重复实现 provider、Registry、workspace 或恢复逻辑。

## 快速开始

在 Linux 或 WSL2 中克隆仓库，然后从仓库根目录执行。对应 wheel 与 sdist 附在
[`v0.1.0rc1` prerelease](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc1)中。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check
```

这些命令不会调用模型。初始 provider/model catalog 为空。按照
[模型配置指南](docs/guides/models_ZH.md)添加已授权的 provider 与 exact model；所有受支持
配置项及其默认值汇总在[配置参考](docs/guides/configuration_ZH.md)。

### 1. 不配置模型，先运行本地工具

仓库自带的 `demo/add` 插件会经过准入、执行、产物登记和终态证据路径：

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json check
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-demo.XXXXXX")"
RUN_DIR="$DEMO_ROOT/run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$RUN_DIR"
```

输入是 `{"left": 2, "right": 3}`；返回 JSON 中的 `output.value` 为 `5`，并包含运行目录
与终态证据引用。

### 2. 查看这次运行

```bash
: "${RUN_DIR:?Set an existing run directory}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

默认终端投影隐藏 resource place；显式资源视图只显示实际 net 声明的资源。最后一条命令
启动本地只读看板并输出 loopback 地址。[看板教程](docs/guides/viewer_ZH.md)说明所有视图、
控件和图形符号。

### 3. 启动使用模型的主会话

构建已授权的 execution profile 后执行：

```bash
rpnh --frontend basic
```

使用 `/agent` 创建独立任务，使用 `/workflow` 请 Designer 创建工作流，使用 `/tasks` 列出
子任务。`/switch` 切换当前任务；`/task ID status` 与 `/task ID result` 查看状态和结果。
`/task ID checkpoints` 列出已提交切面，`/task ID reopen CHECKPOINT [:: REASON]` 在同一
Registry／run 中从用户所选切面追加新的执行代次。详见
[使用与恢复指南](docs/guides/usage_ZH.md)。

## 案例目录

| 案例 | 展示的能力 | 说明 |
|---|---|---|
| 原生计算 | 本地插件、登记输入输出、零模型调用 | [原生工具](examples/native_plugin/README_ZH.md) |
| 模型—程序—模型计算 | 原生汇总 operation 前后的 Agent 节点 | [混合汇总](examples/hybrid_summary/README_ZH.md) |
| 串行、并行、文档和长流程 | 多 Agent 拓扑、有界并行、join 与时间轴 checkpoint | [工作流案例库](examples/workflow_patterns/README_ZH.md) |
| 独立任务 | 一个主会话控制多个相互隔离的子 Registry | [任务工作区](examples/task_workspace/README_ZH.md) |
| 原生 PetriNet 操作 | 定义组合与真实 whole-net replacement | [网操作](examples/net_operations/README_ZH.md) |
| Basic、Codex、DSH 与 OpenCode | 同一个 provider-neutral 语义任务经过四种宿主入口 | [安装版适配任务](docs/guides/examples_ZH.md) |

每个案例页面都包含实际 dashboard 图，并注明图片来自确定性本地运行还是此前已授权的真实
运行。[完整案例指南](docs/guides/examples_ZH.md)记录计算、文档、串行、并行、长流程和
独立任务案例的命令、预期结果与证据边界；
[源码案例索引](examples/README_ZH.md)提供仓库层级的完整目录。

安装包还包含 provider-neutral 适配任务。导出操作不会配置 provider 或联系模型：

```bash
EXAMPLE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-example-parent.XXXXXX")/adapter-task"
rpnh examples list
rpnh examples export --output "$EXAMPLE_ROOT"
```

导出的指南在不同宿主中使用同一任务和预期语义结果。只有用户传入已授权 execution profile
并提交任务后才会产生模型调用；原始 run 保持私有。

## 执行与恢复模型

已声明 operation 先经过准入，再交给执行器运行。返回产物经过登记与结算后，后续工作才能
依赖它们；已声明的 terminal binding 确定最终结果。前端与看板读取同一套持久记录。

Harness 自有的文件物化与 workspace finalization 作为同一 Registry 内的下级执行 PetriNet
运行。它们把实现机制与 Designer 编写的业务图分开，同时保留 checkpoint 到结果的精确
证据。Registry 校验负责身份、顺序、引用与原子闭合；是否重试、如何补救，以及失败工具
动作能否被业务接受，仍是明确的 runtime 或应用策略。

Owner stop 会为当前 run 建立 checkpoint，但不会把未完成工作改写成成功结果。`resume`
续接最新 owner-stopped 切面；`reopen` 选择任意已提交 checkpoint，并追加新的执行代次，
同时把后续历史与文件保留为不可变证据。主对话回退到上一个已完成 turn 时，不会删除独立
child Registry。Provider 提交状态未知时会被记录，而不是静默重放。

参阅[架构说明](docs/architecture/design_ZH.md)和
[运行参考](docs/reference/runtime-registry_ZH.md)，了解 token 语义、workspace 结算、执行网与
工作流修订。

## 前端与宿主集成

| 入口 | 用途 |
|---|---|
| `rpnh --frontend basic` | 内置终端，提供主会话与独立任务控制 |
| `rpnh --frontend codex` | 固定版本的 Codex 展示前端；见[适配器指南](docs/guides/adapters_ZH.md) |
| `rpnh --frontend opencode` | OpenCode 1.18.32 展示前端；见[OpenCode 指南](docs/guides/opencode_ZH.md) |
| `rpnh-dsh` | 可选的固定版本 DSH 宿主集成；见[DSH 指南](docs/guides/dsh_ZH.md) |

Basic、Codex 与 OpenCode 是同一个直接 MainSession root 的顺序展示入口。先用
`--session-dir` 创建；当前前端退出后，再通过另一个前端对完全相同的路径执行 `--resume`。
状态不会复制成前端私有会话，共享 owner lease 会拒绝并发可写展示。DSH 是具有自身
session surface 的 registered host 集成。所有情况下，模型选择与受管执行 authority 仍
属于 RPNH。

## 文档

[English index](docs/index.md) · [中文文档](docs/index_ZH.md)

| 目标 | 从这里开始 |
|---|---|
| 安装与运行 | [安装](docs/guides/installation_ZH.md)、[案例](docs/guides/examples_ZH.md)、[使用](docs/guides/usage_ZH.md) |
| 配置 runtime | [配置](docs/guides/configuration_ZH.md)、[模型](docs/guides/models_ZH.md)、[使用与恢复](docs/guides/usage_ZH.md) |
| 构建应用 | [自定义与插件](docs/guides/customization_ZH.md)、[原生 PetriNet 操作](docs/guides/net-operations_ZH.md)、[声明](docs/reference/declarations_ZH.md) |
| 理解执行 | [看板](docs/guides/viewer_ZH.md)、[架构](docs/architecture/design_ZH.md)、[Registry/runtime](docs/reference/runtime-registry_ZH.md)、[会话与 Agent](docs/reference/agents_ZH.md) |
| 接入宿主 | [前端与宿主适配](docs/guides/adapters_ZH.md)、[扩展与观察接口](docs/reference/extensions-observation_ZH.md) |
| 使用维护或参与开发 | [排障](docs/guides/troubleshooting_ZH.md)、[开发](docs/guides/development_ZH.md)、[参与贡献](CONTRIBUTING_ZH.md)、[安全报告](SECURITY_ZH.md)、[变更记录](CHANGELOG_ZH.md)、[发布验证](docs/guides/release-validation_ZH.md) |

## 验证与开发

局部代码修改使用有针对性的确定性离线测试；只有可能影响额外模块的发布级变更才运行更广
套件。Provider-backed 检查需要单独授权，并记录 route 与调用预算；离线通过不能证明真实
route 可用。凭据、本地 profile 与生成的 run 数据不进入 Git。参阅
[开发指南](docs/guides/development_ZH.md)和带日期的
[验证记录](docs/guides/release-validation_ZH.md)。

## 许可证

RPNH 使用 [MIT License](LICENSE)。捆绑或固定的第三方组件继续使用各自许可证；参阅
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)以及可选集成附带的许可文件。
