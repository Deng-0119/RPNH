# RPNH Harness

[English](README.md) | 中文

[查看实际图](#先看一次多-agent-运行) · [快速开始](#快速开始) ·
[案例](#案例目录) · [前端](#前端与宿主集成) · [文档](#文档) · [技术报告](docs/technical-report_ZH.md)

RPNH 是一个用于组合语言模型、原生工具和可复用工作流的 provider-neutral Agent 执行框架。
持久化 **Registry** 记录执行与资源历史；类型化 **PetriNet** 决定任务何时具备执行条件，
以及已结算产物如何启用后续步骤。

同一 runtime 支持对话式主会话、隔离的子任务、多 Agent 工作流、版本化 workspace 资源和
只读查看。Basic、Codex、OpenCode 与 DSH 是围绕该 runtime 的展示或宿主入口，不是模型、
workspace 或恢复状态的独立所有者。

| 概览 | 当前边界 |
|---|---|
| 平台 | Linux 或 WSL2，Python 3.11+ |
| 源码候选版 | `0.1.0rc2` 开发候选版；旧公开二进制为 `v0.1.0rc1` |
| 安装 | 源码 checkout 或 GitHub Release wheel/sdist |
| 模型 | 用户自有 provider 与 exact-model catalog；不预选 route |
| 执行 | 主会话，以及相互独立的 task/workflow Registry |
| 查看 | 终端投影和本地只读 PetriNet 看板 |
| 恢复 | owner-stop resume 与用户选择 checkpoint 的 reopen |

当前协作入口是[复制、共创并复用同一 Agent 项目](examples/project_collaboration/README_ZH.md)。完整项目包含定义、输入、环境、原任务、验收器、来源与许可证；两个受控贡献者解决真实冲突并形成双 parent 修订 M。业务执行前依次采用 M → base 内容新后继 → M 内容新后继，再下载精确原 M，由新身份、独立授权的 receiver 导入。source 与 receiver 均由 implementer／reviewer 两个调用真实模型的 stock AgentLoop 职责执行同一原始多源合并任务；receiver 包不含 source 的业务答案。[操作指南](docs/guides/project-entry_ZH.md)给出导入、审核、接纳、采用、下载和运行步骤。

[![source 完成态业务 PN：implementer／reviewer，五节点六弧。](examples/project_collaboration/figures/source-petri.png)](examples/project_collaboration/figures/source-petri.png)

[原尺寸 PN](examples/project_collaboration/figures/source-petri.png) · [精确观察与来源](examples/project_collaboration/figures/source-petri.json) · [捕获记录](examples/project_collaboration/figures/provenance.json)

2026-10-13 的有界结果为 **43 次登记／42 次响应，上限 48**：source 23／22（保留 1 次 `NOT_SUBMITTED` 原失败、2 个完成业务 firing），receiver 20／20、2 个完成业务 firing。两端路径适配原断言均 **3/3 PASS**，独立完整 instruction 审阅为 **ACCEPT_BOTH**；原 `test.sh` **NOT_RUN**，grader **VISIBLE**，不称盲评官方容器成绩。全部四行／20 单元格和三项完整字段冲突经独立核对；可选 status 归一化歧义仍披露。

业务 PN 建模两个 agent 的职责与交接；两端 Overview 均为 2 节点／1 边，完整业务 PN 为 5 节点／6 弧。文件物化与 workspace finalization 的真实下级执行 PN 在同一 Registry 内归属于精确业务 invocation/firing，具有分开的 token、firing 和 checkpoint；AgentLoop 日志不替代执行 PN。受支持范围是单一可信 OS 用户下的受控主体、普通 Module 贡献与显式 scoped 读取／owner 采用。业务前内容恢复不复活已消费或终态 token，也不回滚外部效果；不证明真人身份认证、活跃 token 热迁移或 graph／open-region／selected-change／assembly 的广泛交换。

2026-10-06公开记录：freeze04首轮18题为5 PASS / 9 FAIL / 4 BLOCKED；独立repair四题为1 PASS / 3 FAIL。旧14道已评分题未重跑。 [结果与限制](examples/automationbench/PUBLIC_RESULTS_20261006_ZH.md).

显式opt-in的[作者/graph接口](docs/reference/declarations_ZH.md)、[Workset与normal-child root](docs/reference/normal-child-root-contract_ZH.md)及[只读source查询](docs/guides/source-queries_ZH.md)已集成；[有限验收](docs/guides/release-validation_ZH.md)不等于全HOST/I00–I10/advanced25通过，历史 freeze04 的真实浏览器窗口为 BLOCKED。

离线准备与查看入口包括[可移植包预览和精确本地锁](docs/guides/portable-packages_ZH.md)、[HOST 声明诊断](docs/guides/host-readiness_ZH.md)和[checkpoint 比较](docs/guides/checkpoint-comparison_ZH.md)。包命令使用安装后的 `rpnh`；HOST 诊断是显式 Python API。这些入口检查材料和已记录事实，不授予执行权。

[受控 managed 工具](docs/controlled-managed-tools.zh.md)介绍精确结果分页、有界并发、隔离 Python 程序、只读结果证据与来源预检。新能力通过显式 HOST 策略选择；既有默认工具目录与基准对照条件保持原样。

历史零 provider 协议入口是[Agent 任务网共建](examples/user_collaboration/agent_cobuild/README_ZH.md)。`curator.run` 与 `auditor.run` 两个原生任务 agent 使用 stock `AgentStage`／default `AgentLoop`，由两份 stock 单 agent 声明组成同一普通 Module，连接为 `curator.result → auditor.request`。两个受控原 principal 的确定性 source 程序分别加强 curator 输入来源合同和 auditor 对账合同。原 HOST 审阅精确 revision、连续推进受保护 Branch，再由 owner 在 target 执行前采用。

该历史窗口的 source 程序、网外控制动作与 target agent 分层记录；其单 parent 链不表示同 base 自动合并。`local_process` 脚本驱动从实际输入与工具返回计算，以已登记任务 agent 展示结构／协议，不声称真实模型调用或认知能力；运行结果、产物与 Viewer 证据以示例页记录为准。

历史应用评分与零模型 runtime 验证见[精选结果](docs/results/README_ZH.md)。每项保留实际源码与条件；这些旧窗口不认证后续组合产品，也不代表已发布新的 rc2 二进制。

两个受控本地应用把浏览器输入接入原 Registry 与 PN：
[双主体模拟退款审批](examples/approval_workflow/README_ZH.md)和
[外部结果接入报价](examples/a2a_quote/README_ZH.md)。每个案例开头都有实际 Viewer
截图，并提供同一运行的查看与重开命令。业务输入和外部结果均为合成，不调用模型或金融服务。

[历史路线](examples/user_collaboration/README_ZH.md#历史路线规划机制证据)与[原生电费](examples/user_collaboration/native_billing/README_ZH.md)保留原 74／10-operation 业务工具网、版本、结果、图像、贡献／采用回执与失败。其工具操作和作者不是任务 agent，历史结果不验证当前共建示例。

## 先看一次多 Agent 运行

下图来自仓库内 `parallel` 案例完成后的真实 Registry 看板。`prepare` 会同时启用两个独立
Agent；只有两个登记产物都可用后，`join` 才具备执行条件。

[![RPNH 并行流程的实际 PetriNet 投影](examples/workflow_patterns/assets/parallel-petrinet.png)](examples/workflow_patterns/assets/parallel-petrinet.png)

完成[快速开始](#快速开始)后，无需 provider 或 API 凭据即可复现：

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-readme.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

上图为同一次运行的 PetriNet 视图，展示执行实际使用的 transition、交接 place、并行分叉弧和全输入 join。Viewer 支持缩放、平移、节点／弧细节与 checkpoint 查看；简要视图 Overview 用于查看承担任务的 Agent 及交接，见 [Viewer 指南](docs/guides/viewer_ZH.md)及[同次运行原 Overview](examples/workflow_patterns/assets/parallel-overview.png)。

这些图片保留真实图投影、聚合执行状态和控件，只移除了 run 专属 checkpoint 身份及本地
时间。看板始终只读；完成判定依赖 Registry 终态证据和已登记最终结果，不能用图片或
进程退出替代。

Overview 身份来自精确登记绑定或明确标注的声明；外部显示注释不确认 agent 身份。概览边保留结构路径来源，guard 与全输入 join 仍由完整 PN 决定，路径本身不证明实际 firing 顺序。

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

请明确选择源码或发行产物：

- **当前源码：**本 checkout 是 `0.1.0rc2` 开发候选版，应从选定的源码提交构建；
  此处不声称已经发布新的 rc2 二进制产物。
- **旧二进制：**[`v0.1.0rc1` prerelease](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc1)
  中的 wheel/sdist 来自 2026-09-29，不包含之后 `main` 新增的功能。
- **历史 benchmark 快照：**[`benchmark-baseline-2026-10-06`](https://github.com/Deng-0119/RPNH/releases/tag/benchmark-baseline-2026-10-06)
  是提交 `f58a0f061d1daf4c09fc96c43237c613cc43f439` 的纯源码发行版；它不是新 runtime
  二进制发行版，也不证明所有 runtime 用户流程均已通过。

在 Linux 或 WSL2 中使用选定且已审查的源码 checkout，从根目录安装。普通的公开 `main`
clone 不会取得尚未发布的候选变更。记录所选提交，并使用全新环境；wheel 和升级检查见[安装指南](docs/guides/installation_ZH.md)。

```bash
git rev-parse HEAD
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
[使用指南](docs/guides/usage_ZH.md)与
[checkpoint 恢复流程](docs/guides/checkpoint-recovery_ZH.md)。

## 案例目录

按建模对象选择：agent 工作流描述任务职责与交接，领域算法和业务系统是工具或外部环境。ERP、3-DOF、JB、HarnessAudit、AutomationBench、SlopCodeBench 与 RRSI 保留为真实 agent／角色工作流。RRSI 自定义 registered-host role PN 也可具有真实 agent 绑定，不要求一律使用 stock AgentLoop。纯工具流水线、原生输入 gate、作者 principal 和显示注释不会仅因 Registry 来源链而变成 agent。

| 类别 | 案例 | 展示的能力 | 说明 |
| --- |---|---|---|
| Agent／角色工作流 | 项目协作 | 真实双 parent M、业务前 M/base/M 采用与 source／receiver 独立原任务执行 | [项目示例](examples/project_collaboration/README_ZH.md) |
| 历史协议工作流 | Agent 任务网共建 | Curator → auditor 原生 AgentLoop 角色、合同贡献与原 HOST 采用；脚本协议边界 | [共建示例](examples/user_collaboration/agent_cobuild/README_ZH.md) |
| 工具／SDK fixture | 原生计算 | 本地插件、登记输入输出、零模型调用 | [原生工具](examples/native_plugin/README_ZH.md) |
| Agent／角色工作流 | 模型—程序—模型计算 | 原生汇总 operation 前后的 Agent 节点 | [混合汇总](examples/hybrid_summary/README_ZH.md) |
| Agent／角色工作流 | 串行、并行、文档和长流程 | 多 Agent 拓扑、有界并行、join 与时间轴 checkpoint | [工作流案例库](examples/workflow_patterns/README_ZH.md) |
| Agent／角色工作流 | 独立任务 | 一个主会话控制多个相互隔离的子 Registry | [任务工作区](examples/task_workspace/README_ZH.md) |
| Agent／纯声明 | 原生 PetriNet 操作 | 定义组合与真实 whole-net replacement | [网操作](examples/net_operations/README_ZH.md) |
| 工具／SDK fixture | 可运行 v2 分享包 | 显式接收端准备、绑定与原生加法执行 | [分享包复用](examples/package_reuse/README_ZH.md) |
| 工具／SDK fixture | 十步工具流水线 | 十个确定性工具 transition、join 与独立账单验证器 | [工具流水线](examples/tool_pipeline/README_ZH.md) |
| 工具／SDK fixture | SQL 查询校验 | 固定 SQL 候选、只读执行与保留行序的精确结果比较 | [SQL 案例](examples/query_validation/README_ZH.md) |
| 工具／SDK fixture | 多源数据合并 | 原 JSON、CSV 和真实 Parquet 输入、冲突及两件终态产物 | [多源案例](examples/tool_pipeline/multi_source/README_ZH.md) |
| 原生输入 fixture | 双主体模拟退款审批 | 不同原生申请者与审批者、精确输入等待和登记模拟结果 | [退款审批](examples/approval_workflow/README_ZH.md) |
| 原生输入 fixture | 外部结果接入报价 | 自定义本地 HTTP fixture、精确关联与一次登记报价 | [受控报价](examples/a2a_quote/README_ZH.md) |
| 工具／SDK fixture | 有界合成迭代 | 同一 Registry 中的一至四轮流程，不宣称模型能力改进 | [合成迭代](examples/rsi_workflows/README_ZH.md) |
| Agent／角色工作流 | Basic、Codex、DSH 与 OpenCode | 同一个 provider-neutral 语义任务经过四种宿主入口 | [安装版适配任务](docs/guides/examples_ZH.md) |
| Agent／角色工作流 | JB 临床 packet | 公开临床来源、文档／数据分析和 Designer 自主设计图 | [复现 JB](examples/jb_steering_packet/README_ZH.md) |
| Agent／角色工作流 | 3-DOF 动力下降 | 数值实现、独立验证和 Designer 自主设计图 | [复现 3-DOF](examples/three_dof_powered_descent/README_ZH.md) |
| Agent／角色工作流 | HarnessAudit Office benchmark | 原生团队、登记业务工具、原始评分与完整结果边界 | [Office 示例](examples/harnessaudit_office/README_ZH.md) |
| Agent／角色工作流 | RRSI v0.6 Policy 演化 | 独立 child Registry 驱动的两轮 Policy 演化与分数/成本选择 | [RRSI 示例](examples/rrsi_v06/README_ZH.md) |
| Agent／角色工作流 | AutomationBench 公开业务任务 | 历史pilot、freeze04首轮18题与独立repair四题；按条件保留评分与限制 | [AutomationBench 示例](examples/automationbench/README_ZH.md) |
| Agent／角色工作流 | ERP-Bench | 受管 ERP 动作与单独保留的原始 verifier 成绩 | [ERP 案例](examples/erp_bench/README_ZH.md) |
| Agent／角色工作流 | SlopCodeBench pilot | 原生 Session 命令与有限编码任务前缀 | [编码试验](examples/slopcodebench/README_ZH.md) |
| 工具／SDK fixture | 案例证据 | 只读校验结果文件与源码身份 | [证据契约](examples/example_validation/README_ZH.md) |

执行案例中的实际 dashboard 图注明其运行来源。Benchmark examples 提供成绩记录与新运行的
viewer 命令，不宣称已归档运行截图。[完整案例指南](docs/guides/examples_ZH.md)记录计算、文档、串行、并行、长流程和
独立任务案例的命令、预期结果与证据边界；
[源码案例索引](examples/README_ZH.md)提供仓库层级的完整目录。

从本源码候选版构建的 wheel 可导出 `native_plugin`、`hybrid_summary`（含共享替身和插件）、
`compose_serial`、`package_reuse`，以及默认的 provider-neutral `adapter_task`。
详见[导出与修改](docs/guides/examples_ZH.md#导出案例并改成自己的应用)，或完整的
[可运行 v2 分享包教程](docs/guides/package-reuse-example_ZH.md)。旧 rc1 wheel 只包含 adapter
导出。导出操作不会配置 provider 或联系模型：

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

[全部文档主题](docs/index_ZH.md) · [全部源码案例](examples/README_ZH.md) ·
[代码与安装包目录](docs/guides/repository-layout_ZH.md) ·
[固定版本代码链接](docs/technical-report_ZH.md#源码索引) · [English index](docs/index.md)

| 目标 | 从这里开始 |
|---|---|
| 测试前先了解整体设计 | [技术报告](docs/technical-report_ZH.md) |
| 安装与运行 | [安装](docs/guides/installation_ZH.md)、[案例](docs/guides/examples_ZH.md)、[使用](docs/guides/usage_ZH.md) |
| 配置 runtime | [配置](docs/guides/configuration_ZH.md)、[模型](docs/guides/models_ZH.md)、[使用与恢复](docs/guides/usage_ZH.md) |
| 构建应用 | [自定义与插件](docs/guides/customization_ZH.md)、[原生 PetriNet 操作](docs/guides/net-operations_ZH.md)、[声明](docs/reference/declarations_ZH.md) |
| 理解执行 | [看板](docs/guides/viewer_ZH.md)、[架构](docs/architecture/design_ZH.md)、[Registry/runtime](docs/reference/runtime-registry_ZH.md)、[有限 PN 验证](docs/reference/pn-validation_ZH.md)、[会话与 Agent](docs/reference/agents_ZH.md) |
| 复用与检查定义 | [可移植包](docs/guides/portable-packages_ZH.md)、[接收端准备](docs/environment-preparation_ZH.md)、[图作者接口](docs/reference/graph-authoring_ZH.md)、[Workset](docs/reference/worksets_ZH.md) |
| 读取精确证据 | [独立 Registry 读取](docs/guides/independent-registry-reader_ZH.md)、[来源查询](docs/guides/source-queries_ZH.md)、[checkpoint 比较](docs/guides/checkpoint-comparison_ZH.md)、[精选结果](docs/results/README_ZH.md) |
| 接入宿主 | [前端与宿主适配](docs/guides/adapters_ZH.md)、[扩展与观察接口](docs/reference/extensions-observation_ZH.md)、[受控用户协作](docs/guides/user-collaboration_ZH.md) |
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
