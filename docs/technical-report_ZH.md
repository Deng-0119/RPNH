---
name: rpnh-technical-report
description: "介绍 RPNH 的设计、执行模型、组合边界与评估路径。"
metadata:
  document-kind: explanation
  audience: operator-and-developer
  language: zh-CN
  counterpart: technical-report.md
  revision: "2026-10-01.1"
  status: source-reviewed-introduction
  basis: "Deng-0119/RPNH at 40f1be804b00abab587636783a5f0e9d3e1f83a9"
---

[English](technical-report.md) | 中文 | [文档导航](index_ZH.md)

# RPNH 技术报告
## 基于 Registry 与类型化 Petri Net 的 Agent 执行框架

**文档日期：**2026-09-30。**实现基线：**
[`40f1be8`](https://github.com/Deng-0119/RPNH/tree/40f1be804b00abab587636783a5f0e9d3e1f83a9)。
本文面向首次了解项目的使用者、集成开发者和外部测试者，介绍公开的通用 harness，不涉及私有研究工作流。
它提供整体技术说明，与操作指南、接口参考互补，不替代这些专题文档。

## 1. 概述与适用场景

RPNH 是一个 provider-neutral 的 Agent 执行框架，用于组合语言模型 Agent、原生程序与可复用工作流。
它的核心是一个闭合的执行路径：类型化 Petri Net 决定已声明的任务何时可以开始；持久化 Registry
提供这一判定所依赖的权威身份、资源版本和已提交证据。执行器返回的产物只有完成登记与结算，
才能成为后续任务的执行依据。

这不只是把 Agent 的对话画成流程图，而是让执行依赖、结果及继续执行的边界可以被检查。
例如，一项报告编写任务可以要求两份独立分析都完成后才能开始综合，将综合结果关联到准确的输入版本，
并保留检查或继续该次执行所需的证据。模型负责选择语义内容，harness 管理内容产生和使用的执行结构。

当任务需要混合模型推理与现有代码、执行并行工作与同步、持续修改文件，或需要在停止后继续，
而不能把聊天记录视为唯一运行状态时，这种设计更有意义。对于简单、一次性、单次调用任务，
额外的声明和持久化状态未必划算。这是设计上的取舍，不是 RPNH 优于某个替代系统的实测结论。

公开发行内容包括核心 runtime、Basic 终端、可选的 Codex/OpenCode 展示与 DSH 宿主接入、
原生插件接口、示例和只读 PetriNet 看板。本文基线属于预发布阶段，采用仓库中的
[MIT 许可证](../LICENSE)。当前支持 Linux，包括 WSL2，要求 Python 3.11 及以上；
支持范围不等于每一种解释器和平台组合都已完成测试。参阅
[安装指南](guides/installation_ZH.md)和[架构说明](architecture/design_ZH.md)。

## 2. 执行模型

### Registry 与 PetriNet 回答不同的问题

| 要素 | 回答的问题 | 在 RPNH 中的含义 |
|---|---|---|
| Registry | 已提交的准确事实是什么？ | 身份、版本、已准入的执行、访问、结果及其关系。 |
| Place 与 token | 哪些输入或条件已经具备？ | 对应已声明数据、控制或资源语义的类型化出现项。 |
| Transition 与弧 | 哪项工作可以使用什么开始？ | 输入要求、占用、输出产生与同步关系。 |
| Marking | 当前执行处于什么状态？ | 从登记权威恢复出的 token 状态与占用情况。 |
| Owner | 谁可以推进该状态？ | 某个 run 被授权的写入路径，而不是任意 worker 或看板。 |
| Terminal binding | 什么可以证明该次运行的最终结果？ | 已声明终态规则及其登记证据，而不是进程退出或可见文本。 |

为便于解释，可以将一次运行的已提交状态记为 `S = (G, M, R)`：`G` 是已采用的图，`M` 是其 marking，
`R` 是 Registry 历史。这个记号不是新增公共 API。Workspace 版本与结果身份记录在 `R` 中，
前端不会维护第二份具有相同权威的状态。

Transition 不会因为前序节点“发了一条消息”就自动获得执行资格。准入需要满足准确的 token 出现项、
资源版本、已登记绑定、占用、预算，以及适用的已声明条件。外部调度策略只能从**已经使能**的
transition 中选择不重复的子集，不能把尚未使能的 transition 变成可执行状态。当前实现的这一边界可见
[`Harness.schedule_ready`][harness-source]。

共享 place 也不等于广播。同一结果需要进入两条独立分支时，工作流必须声明具有相应输出的分发节点；
join 必须声明它真正需要的输入。Agent 工作流层的反馈使用显式且受预算限制的返工路径，
而不是不受约束的依赖环。这些区别使分支、同步与返工具有实际执行语义，而不只取决于图的外观。
参阅[整体架构](ARCHITECTURE_ZH.md)与[原生网操作规则](guides/net-operations_ZH.md)。

### 结构有效性与业务正确性

RPNH 管理结构性质：工作是否经过登记边界准入、使用了什么身份与资源、结果如何发布。
结构有效的运行仍可能给出错误答案，或以业务拒绝的结果结束。访问策略、保密规则、重试策略与答案验收
仍由应用决定，可以在适当边界通过登记 operation、guard 或 Inspector 条件表达；
通用 harness 不代替具体组织制定业务政策。

## 3. 架构与状态归属

执行路径区分展示、会话与任务控制、run 所有权、operation 实现和观察。不能仅为了集成方便，
就让某个模型、原生工具或适配器成为竞争性的第二状态拥有者。

| 层级 | 主要职责 | 应保持的边界 |
|---|---|---|
| 展示层 | 终端交互与协议翻译。 | Basic、Codex、OpenCode 复用同一直接会话权威，不复制其状态。 |
| 主会话与任务控制 | 持久对话、任务链接和显式控制。 | 独立子任务拥有自己的运行状态；主界面焦点不是子任务生命周期。 |
| RunOwner 与 owner event loop | 图采用、准入、Start、结算与控制。 | 完成处理回到该 run 的 owner 路径。 |
| Harness 与 marking | 使能任务选择、有界调度和结果协调。 | 并行 operation 不成为各自独立的 Registry writer。 |
| 登记组件与插件 | 模型调用、原生计算、已声明应用行为。 | 实现必须遵循已准入执行与选定契约。 |
| Registry 与 workspace 服务 | 版本化证据和提交发布。 | 辅助模块共用原有事务与权威边界。 |
| 看板与观察接口 | 投影当前或历史登记状态。 | 查看不能创建 token、结算任务或恢复执行。 |

`Harness` 接收已有 `RunOwner`、属于该 owner 的 `OwnerEventLoop`、dispatcher 与提交回调，
允许受限数量的物理 operation 同时在执行。Future 完成后，处理回到 owner loop，先核对产物是否属于
准确的 firing 与 execution lease，再结算。这是每个 run 的单写入路径设计，不是把全部计算串行化。
参阅[`harness.py`][harness-source]与[运行参考](reference/runtime-registry_ZH.md)。

三类层次关系需要区分。**独立子任务**有自己的 Registry、PetriNet 和 owner，父级保留准确链接，
不复制子任务事件。**Delegated leaf** 是归属于准确父 action 的有界委派。
**下级执行网**则在同一 Registry 内归属于某个业务 firing，用于表达文件物化、workspace finalization
等 harness 机制。下级网的进度不能直接推进业务 token；业务结算必须把这些执行证据通过 terminal mapping
关联到结果与后继 checkpoint。因此，主会话回退不会抹除独立子任务历史，下级机制 checkpoint
也不等于业务任务结果。

## 4. 从声明到任务完成

理解生命周期时，关键是每个边界需要什么证据，以及它尚未证明什么。

| 阶段 | 发生的事情 | 尚不能证明的事情 |
|---|---|---|
| 声明与编译 | 根据可信 Registration 检查类型化组件、端口、operation、outcome、连接、预算和终态绑定。 | 编译候选尚不是已经运行并采用的网。 |
| 采用与准入 | Owner 采用结构，将使能 firing 绑定到准确输入、占用和执行上下文。 | 准入本身不是物理工具或模型调用。 |
| Start 与调度 | 先登记 Start authority，再提交 operation 回调。 | 已提交请求不证明远端已经完成。 |
| 产物登记 | 按准确执行与 outcome 验证并登记返回数据。 | 执行器完成不等于 Petri 结算。 |
| 结算 | 经 owner 闭合结果、后继 marking、相关 workspace 发布及下级映射。 | 一个 firing 结算不等于整个工作流完成。 |
| 建立终态证据 | 已声明终态规则选出登记的最终结果。 | 终端中出现文字不能替代该证据。 |

代码中也显式区分这些状态：`OperationProducts` 必须携带 `RegisteredOperationOutputsAuthority`；
资源等待、执行阻塞和 terminal handoff 则使用 `OperationDisposition`，不会被静默改成 Success。
`HarnessResult` 分别暴露终态证据与完成错误。即使方法名为 `succeed`，其含义也是结算某个已声明 outcome，
不能直接等同于业务目标成功。

EventStore 的提交协调器检查类型化 task/transaction 身份、准确引用、预期状态和发布结构。
当前批次契约在一个事务中最多允许一次 firing 的结算与发布；辅助模块不会取得独立发布权威。
这样，结果与后继状态之间的关系是显式的。参阅
[`publish_batch`][event-store-commit-source]与
[Registry 运行参考](reference/runtime-registry_ZH.md)。

## 5. 版本化 workspace 与恢复

Firing 在登记 workspace revision 派生的私有视图中工作。系统不会因为目录中出现一个名称合理的文件
就推定它已经发布。Workspace finalization 先冻结候选 archive，普通 Success 再发布已结算后继状态，
并建立它与业务 marking 的关系。逐路径 delta 记录 create、update、delete 及准确的前后资源引用。
并发发布针对当前 workspace head 处理，冲突不会简单地被最后写入的目录覆盖。
这提供的是版本化执行证据，不是对外部系统的自动撤销。

恢复也有几个不同含义：

| 机制 | 当前实现的含义 | 重要边界 |
|---|---|---|
| Owner stop 与 resume | 在授权边界停止，保存 checkpoint，再续接停止的 run。 | 停止请求不证明远端请求从未生效。 |
| 准确 completion 恢复 | 在文档规定的狭窄条件下，根据持久登记完成证据结算 stale firing，不再次调用。 | 缺失或冲突证据、被排除的 HOST effect、未支持切面都不授权重放。 |
| Owner 选择 checkpoint reopen | 在同一 run/Registry 中选定已提交 checkpoint，追加新的执行代次、新出现项与恢复后的登记 workspace 状态。 | 后续历史保留；不会撤销过去的外部效果。 |
| 看板时间轴 | 读取已保存 checkpoint 及其投影。 | 不触发 resume、reopen 或重新执行。 |

尚未明确结果的 provider 提交可以记录为 `submission_unknown`。本地已经写出请求字节，或者发生超时，
都不能证明远端结果。后续显式 owner-selected reopen 可以在支持的协议下，把未决尝试闭合为 interrupted，
再使用新身份继续；这不是对旧物理尝试的自动重试。当 operation 可能扣费、发消息或修改外部系统时，
这一点尤其重要。

Workspace 绑定完成证据、并行 firing 收束、reopen 命令幂等等准确条件应以
[运行参考](reference/runtime-registry_ZH.md)为准，不能概括成“任意崩溃都能恢复”或
“外部效果天然 exactly-once”。

## 6. 工作流组合与 Git 类比的边界

RPNH 提供应用无关的**网定义提取、组合、实例化、分支与替换**能力，使应用可以复用结构化流程，
而不必把优化策略放进内核。定义变换、执行续接与 workspace 版本化彼此相关，但不是同一种操作。

Extract 作用于完整 module，或在支持的公共端口边界上选择完整组件。Compose 连接显式兼容的出入口。
Instantiate 创建名称独立的符号实例，不生成额外预算。Branch 提取定义，并可进一步实例化；
它本身**不创建**另一份 Registry 或 worker。确定性测试包含串行/并行编译，以及对隐式广播的拒绝：
[`test_native_net_operations.py`][native-net-tests]。

Replace 使用已有 owner edit 路径。在支持的 `whole_net_quiescent` 模式下，先暂停新准入，
等待活动 firing 收束，再应用显式状态映射或退役规则，并使用原有预算清单采用后继网。
同 owner 的 Agent 网替换保留登记 workspace lineage 与执行环境，不会隐式重建一个空 workspace。

这形成了有实际意义的版本与组合机制，但还不能等同于完整的 Git 式分布执行系统。
单体组件内部任意切分、工作流改动的自动语义合并、跨 Registry 迁移、workspace fork/import、
克隆尚在执行的模型调用，都不由这些基础操作自动提供。同样，已支持的 checkpoint reopen
不等于任意导入历史 token 的权限。准确接口及未支持分支见
[原生 PetriNet 操作](guides/net-operations_ZH.md)。

## 7. 模型、工具与前端接入

Provider-neutral 的含义是：provider/model 选择属于共享、由用户持有的配置边界。默认 catalog 为空，
使用模型任务之前需要显式配置受支持 route 和准确模型，前端不会另行提供一套 provider 权威。
这不意味着所有 provider 协议都能自动兼容。

原生插件需要显式安装和选择，声明输入输出 schema、operation 身份、effect、资源及限制，
其计算随后进入同样的准入与结算路径。Skill 可以表现为登记的指令资源；MCP 能力需要显式、受支持的绑定，
暴露 operation、资源与 effect 语义。只添加一个 Markdown 文件或任意服务器地址，
并不会自动产生受管 skill 或普遍兼容的工具。

Basic、Codex、OpenCode 是共享 runtime 的展示入口，受支持 Codex/OpenCode 客户端与具体版本绑定。
这三个前端可以顺序打开同一个直接会话目录；owner lease 防止并发可写展示。
DSH 属于有自身 session surface 的 registered host 接入。它们不是四套分别实现 provider、workspace
状态与恢复机制的执行内核。

Registry/Petri 管理也不是操作系统沙箱。可信 host/plugin 代码和传递依赖仍需要审查。
一个不透明程序不会因为外层调用进入 Registry，其内部就自动全部受管；需要治理的内部动作应通过适当声明边界暴露。
参阅[自定义指南](guides/customization_ZH.md)与[安装入口说明](guides/installation_ZH.md)。

## 8. 可复现的首次测试

仓库的并行案例适合作为入口，因为不需要 API 凭据即可检查结构。它的拓扑是
`prepare -> (facts || risks) -> join`。默认的确定性本地进程 fixture 使用预设协议响应，
测试的是执行路径，而不是语言模型推理水平。

在 Linux/WSL2 的**新源码检出目录**中执行：

```bash
git clone https://github.com/Deng-0119/RPNH.git
cd RPNH
git rev-parse HEAD
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check

DEMO_ROOT="$(mktemp -d /tmp/rpnh-report.XXXXXX)"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

成功时，示例会输出 `status: PASS`、终态证据信息及结果。最后一条命令启动本地只读看板并打印地址。
安装依赖可能联网，但默认 fixture 不调用模型。上述命令针对实际 checkout，反馈时应保留输出的 commit。
要复现本文实现基线，应在安装前选定文首完整 commit，而不是假定后续 `main` 与本文完全一致。

重点检查两个分支是否都形成登记结果后才进入 join、输出引用是否对应最终证据，以及看板是否展示预期的
run/checkpoint。截图本身不是验收条件。[工作流案例库](../examples/workflow_patterns/README_ZH.md)
提供确定性运行的真实看板图片，以及串行、文档和长流程命令。本文不把这些 fixture 描述为新增真实模型实验。

使用真实模型是单独的步骤：按照[文档导航](index_ZH.md)配置已授权 route，再选定 execution profile
运行示例，或启动 `rpnh --frontend basic`。这可能产生测试者自己的 provider 费用。
应先完成小任务，再测试并行与恢复；不要把首次恢复实验用在不可逆外部动作的重复执行上。

## 9. 当前证据可以说明什么

本文是基于源码的技术介绍，不是新增 benchmark 成绩，也不是一次独立全量运行审计。
基线仓库已有带日期的验证记录，其源码版本和环境限制是证据的一部分。

| 证据 | 记录中的范围 | 合理解释 |
|---|---|---|
| 历史完整离线套件 | `073a451`，2026-09-28：744 项通过，1 项环境相关跳过。 | 该版本的确定性基线，不是所有后续代码的全量结果。 |
| 发布候选集合 | `1e85b4f`：790 项通过、1 项 OpenCode PTY 跳过，23 项因临时 Unix socket 路径过长失败；之后两个受影响文件在短目录下共 30 项全部通过，合并覆盖 813 个独立通过项。 | 合并的候选证据，不能写成首次调用全量干净通过。 |
| 后续定向、打包和安装版检查 | 发布验证页中的带日期、变更相关记录。 | 证明已测试的边界或产物；重叠数量不能累加。 |
| 确定性工作流案例 | 带终态与结果检查的本地 fixture。 | 检查协议、结构与观察，不代表模型质量。 |
| 真实 provider 使用 | 必须关联具体授权 route、任务和带日期记录。 | 不自动推广到另一 provider、模型或负载。 |

[发布验证记录](guides/release-validation_ZH.md)给出了详细来源与排除项，其中列出的离线验证没有真实
模型/provider 调用。这些测试数量及设计本身都不能证明更低成本、更高任务准确率、全局活性，
或对恶意模型的普遍防护。

外部评估宜分别记录功能完成、结构证据、恢复行为与运行成本。有效反馈包括准确源码与环境、
适用时的模型 route、任务输入、预期结果、实际终态、复现步骤及脱敏诊断。进行比较时，
应控制模型、任务集、工具权限和预算，并分别测量模型调用数、总耗时及执行/存储开销。
本文不主张任何 benchmark 分数。不要将凭据、机密输入或原始私有 run 上传到公开 issue。

## 10. 阅读与源码地图

| 要了解的问题 | 建议入口 |
|---|---|
| Owner/worker 边界具体约束什么？ | [`cpn/rpnh/harness.py`][harness-source]，重点看 `schedule_ready`、`_complete`、`result`。 |
| 发布如何闭合？ | [`registry/_event_store/commit.py`][event-store-commit-source]与[运行参考](reference/runtime-registry_ZH.md)。 |
| 会话与下级执行网如何分层？ | [整体架构](ARCHITECTURE_ZH.md)与[执行原则](architecture/design_ZH.md)。 |
| 哪些组合情况确实可以执行？ | [原生网操作](guides/net-operations_ZH.md)及其[确定性测试][native-net-tests]。 |
| 新能力应如何进入 harness？ | [自定义指南](guides/customization_ZH.md)。 |
| 测试者如何检查真实案例？ | [工作流案例库](../examples/workflow_patterns/README_ZH.md)与[安装指南](guides/installation_ZH.md)。 |
| 哪些观察实际经过验证？ | [发布验证记录](guides/release-validation_ZH.md)。 |

源码链接用于定位实现，不表示其中私有类都是稳定 SDK。本文依据标明版本的公开源码与文档整理，
直接检查了调度、完成、发布以及组合测试等边界。整理本文没有重新运行 runtime 套件，也没有调用模型。

RPNH 在执行设计上的核心，是把声明结构、登记证据与受控继续执行联系起来。
对于某个具体应用，其价值取决于这些性质能否解决实际的协调与检查问题，并应最终在该应用的真实任务上验证。

[harness-source]: https://github.com/Deng-0119/RPNH/blob/40f1be804b00abab587636783a5f0e9d3e1f83a9/cpn/rpnh/harness.py
[event-store-commit-source]: https://github.com/Deng-0119/RPNH/blob/40f1be804b00abab587636783a5f0e9d3e1f83a9/cpn/rpnh/registry/_event_store/commit.py
[native-net-tests]: https://github.com/Deng-0119/RPNH/blob/40f1be804b00abab587636783a5f0e9d3e1f83a9/tests/test_native_net_operations.py
