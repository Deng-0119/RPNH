---
name: rpnh-technical-report
description: "持续更新的 RPNH 工程技术报告：可执行流程资产、Registry/PetriNet runtime、实现与评估。"
metadata:
  document-kind: technical-report
  audience: operator-developer-and-researcher
  language: zh
  counterpart: technical-report.md
  revision: "2026-10-08.1"
  status: living-report
  basis: "Deng-0119/RPNH at 74fad32d369876841686d10d33361c016e3d3648"
---

[English](technical-report.md) | 中文 | [文档导航](index_ZH.md)

# RPNH 技术报告
## 面向 Agent–程序系统的可执行、可组合、可版本化流程

**更新日期：**2026-10-08。**实现快照：**
[`74fad32`](https://github.com/Deng-0119/RPNH/tree/74fad32d369876841686d10d33361c016e3d3648)。
各次实验实际使用的源码版本另列于[第 9 节](#9-证据与当前结果)。
本文介绍公开实现、报告已保留的观察，并约定后续优化实验如何持续补充同一份记录。

## 摘要

RPNH 是一个用于组合语言模型 Agent、原生程序与可复用工作流的 provider-neutral harness。
其核心对象是**流程本身**：一种可以被构造、编译、组合、修订，并关联准确执行证据的类型化声明。
持久化 Registry 记录身份、版本和已提交事实；类型化 PetriNet 决定执行资格、占用、路由与完成条件。
二者结合，使图成为实际执行结构，而非从对话中还原的展示图片。

实现连接了三类有用能力：异构工作共用执行契约；准确的结果/workspace 血缘与受控继续执行；
具有 author revision 和接收端本地绑定的可复用流程定义。这使应用开发者有机会把成功的方法沉淀成
可检查、可修改、可再次使用的资产。模型可以提出流程或修订，可信 HOST 绑定与既有 owner
则决定什么能够执行。

当前证据支持具体工程机制和有边界的案例结果，尚未建立任务质量、成本或速度方面的对照收益。
因此，本文区分源码能力、实际运行、业务验收与比较证据。Registry 中已经完成的运行与正确的业务结果，
有意采用不同的评价口径。

## 1. 问题与设计目标

有用的 Agent 系统不止要生成下一条模型回复。它还需要协调工具与人、保留产物、处理中断，并在任务或环境
变化时调整。对于重复任务，形成的方法可能与最终答案同样重要：哪些步骤相互依赖，哪里适合使用程序而非模型，
什么算合格产物，以及下一次可以复用什么。

RPNH 将这些关系显式化。以“分别完成两份分析、执行确定性汇总、综合报告”的流程为例：join 需要等到
两份分析产物齐备；计算需要准确的输入；最终结果需要声明的完成条件。后续修订应能识别哪项职责和哪些输入
发生了变化，而不能把同名文件或同名节点直接视作同一对象。

| 设计目标 | 当前机制 | 需要评估的用户收益 |
|---|---|---|
| 复用方法与产物 | 数据化 `ModuleDeclaration`、组合、author revision 与受支持的 package 格式 | 下一次修改或复用能节省多少编写和验证工作？ |
| 协调模型与既有程序 | 已登记组件/executor 契约、类型化端口、PetriNet 准入与结算 | 混合工作能否得到正确业务结果，并保持可理解的职责边界？ |
| 保持准确执行含义 | 准确引用、唯一 owner 路径、登记产物与终态证据 | 操作者能否区分尝试过的动作、已结算产物和已完成任务？ |
| 有控制地继续与修订 | Checkpoint、执行代次、workspace revision 与显式采用映射 | 保留了多少有效工作，哪些失败仍需要人工介入？ |
| 共享结构、本地绑定 | 仅含数据的 package、准确本地 lock、环境计划与独立运行批准 | 接收者能否复用流程，而不复制发送者的机器或私有配置？ |
| 观察与执行权威分离 | Read session、checkpoint 投影与有界比较 | 审查者能否更可靠地定位实质变化和缺失证据？ |

这些是**组合设计的长处**与实际价值假设。并行工具、多 Agent、流式输出、渐进披露和 provider 选择都是
通用配套能力，本身并非独特性主张。对于简单的一次性任务，声明、持久化状态与运行管理的额外成本可能并不划算。
评估计划因此包含此类对照任务，而不只选择有利于该架构的情形。

## 2. 架构：四类流程资产

流程定义、其可执行绑定，以及执行过它的记录彼此有关，但属于不同对象。明确区分它们，是实现复用的关键。

| 资产层 | 具体对象 | 所支持的工作 |
|---|---|---|
| 定义与创作历史 | `ModuleDeclaration`、Agent graph source、`NetRevision`、branch head、assembly recipe 与元素映射 | 以显式来源构造、比较、组合和修订方法。 |
| 可信接收端绑定 | `Registration`、准确 plugin/model 选择、package/environment lock 与本地 HOST binding | 将声明键解析为接收端选择的实现。 |
| 运行证据 | 已采用的网、token 出现项、execution lease、登记资源、workspace revision、checkpoint 与终态证据 | 执行已绑定方法，并保留这次运行的准确事实。 |
| 观察 | Read session、来源限定引用、net/checkpoint 投影与比较结果 | 解释选定事实，而不获得写入权威。 |

`Registration` 是可信组件 lowerer、executor、tool、analyzer 与 schema 的清单。声明选择已登记的键，
不能自行引入可执行 Python locator。编译器保留 lowering 时实际使用的片段与契约。
读取编译结果不会登记其中的 callable。参阅 [`module.py`][module-source]、
[`registration.py`][registration-source] 和 [`compiler.py`][compiler-source]。

运行栈进一步区分展示层、主会话/任务控制、`RunOwner` 及其 event loop、`Harness`、operation 实现与只读观察。
Basic、Codex、OpenCode 是同一直接主会话根的顺序展示入口，共享 lease 防止竞争性可写展示。
DSH 是具有自身 session surface 的 registered host 集成。
Provider、Registry、workspace 与恢复权威仍由共享 runtime 持有。

三类运行层次承担不同职责：

- **独立子任务**拥有各自的 Registry、net 与 owner。父级保留准确链接，改变主会话焦点不会停止子任务。
- **Delegated leaf** 是归属于准确父 action 的有界工作。
- **下级执行网**在同一 Registry 内表达业务 firing 下的文件物化、workspace finalization 等机制，
  其证据必须映射到业务结算中。

Authoring assembly 的成员关系则属于第四种、定义层的关系，并不自动构成分布式运行任务树。
这使集成代码能够复用既有 owner，而不是再发展出一套执行内核。
详细契约见[架构](architecture/design_ZH.md)与[运行参考](reference/runtime-registry_ZH.md)。

## 3. Registry 与 PetriNet 的联合执行契约

### 3.1 表示与执行资格

为便于解释，将已提交状态写为 `S = (G, M, R)`，分别表示已采用的图、marking 与 Registry 历史。
这只是说明记号，不是新增 SDK 对象。Registry 引用确定准确版本和上下文；marking 则提供这些记录上的
token 出现项与占用。

通用网支持类型化 place、容量、加权弧、consume/read/borrow/guard/produce/return 模式、依 outcome
而定的产物、可复用资源及显式资源占用。组合会限定符号并融合兼容的 place。融合意味着共享 place，
不意味着复制产物以进行广播。Fan-out 必须提供所需出现项，join 必须要求它实际需要的输入。
上层 Agent graph 使用依赖 DAG，并另行声明有界反馈。

`Harness.schedule_ready` 恢复当前网与 marking，装入活动占用，查找已使能 transition，并受在途容量限制。
注入的调度策略只能返回其中不重复的子集。对于每项选择，owner 先准入 firing、登记 Start，
之后才提交物理 operation。调度偏好不能让本不合法的 firing 获得执行资格。
参阅 [`harness.py`][harness-source] 与 [`petri_contracts.py`][petri-source]。

### 3.2 完成路径

| 边界 | 建立或核对的证据 |
|---|---|
| 编译 | 类型化组件、端口、operation、outcome、连接、预算和终态声明匹配可信 Registration。 |
| 采用与准入 | Owner 选择准确图，并将 firing 绑定到当前输入、占用和执行上下文。 |
| Start 与分发 | 外部计算开始前，`OperationDispatch` 携带准确的 `OperationExecutionAuthority`。 |
| 产物登记 | `OperationProducts` 携带该次 execution 的 `RegisteredOperationOutputsAuthority`。 |
| 结算 | Owner 闭合结果、后继 marking/checkpoint、适用的 workspace 发布及下级执行映射。 |
| 建立终态 | 已声明终态规则选出登记的最终结果与终态证据。 |

因此 Registry 不只是事后日志，PetriNet 也不只是就绪状态图。登记绑定与版本约束什么可以 firing；
接受的产物与后继状态必须一致，才能支持后续工作。提交协调器验证类型化身份、准确引用、顺序和发布闭合。
当前批次契约在一个事务中最多允许一次 firing 的结算/发布，辅助模块参与这一边界而非取得独立发布权威。
参阅 [`publish_batch`][commit-source]。

Worker future 完成不会直接推进 token。完成处理回到 owner event loop，核对 firing、Start 与 lease 身份。
物理 operation 可以并发，权威状态通过每个 run 的唯一 owner 路径推进。已经持久登记的产物优先于同时到来的
stop，以避免重新执行已经完成的语义动作。出现 completion error 后停止新准入，同时让已启动的兄弟 operation
通过 owner 完成收束。

`OperationDisposition` 将资源等待、执行阻塞与 terminal handoff 同产物分开表示。
`HarnessResult` 分别暴露 terminal evidence 与 completion error。即使方法名叫 `succeed`，其含义也是
结算某个已声明 outcome，不是证明应用目标已经满足。

### 3.3 契约确立什么

这些契约使身份、依赖、资源使用和结果发布可以被检查。业务正确性仍需要领域 schema、checker、约束或人工验收。
当前状态/占用检查不证明全局 PetriNet 活性、所有死锁都不存在，或模型内容为真。
因此[第 9 节](#9-证据与当前结果)将原任务评分与 runtime 闭合分别报告。

## 4. 产物、workspace 历史与恢复

### 4.1 产物成为资源，而不只是对话文本

产物属于某个已准入 operation 与已声明 outcome。它先成为登记资源，后继工作才能依赖它。
`TaskControl.result` 读取当前终态权威、准确的 `run_terminal_evidence` 及其选定资源，
返回 outcome、generation、output 与模型调用计数。进程退出、合理的文件名或最后一条 assistant 消息
都不能替代这条证据链。参阅 [`task_control.py`][task-control-source]。

普通 Agent graph 使用**文本产物**端口上的符号化 artifact label。标签匹配提供路由结构，
并不证明文本确实是一份合法采购计划或数值模型。需要更强业务数据契约的 author 应使用登记 schema
与通用 module 接口。原生 plugin 节点解析 JSON 文本，并按 plugin schema 验证。

### 4.2 Workspace 发布属于结算的一部分

Firing 在登记 workspace revision 派生的私有视图中工作。Finalization 冻结候选 archive，
普通结算发布后继 workspace 并将其关联到业务 marking。逐路径 create、update、delete delta
保留准确的前后资源引用。并发 revision 针对当前 head 协调并保留冲突，而非以后写目录覆盖先写目录。

下级执行网使文件物化和 finalization 机制可检查，而无需在每个 Designer 业务图中添加实现步骤。
其 terminal mapping 将机械执行证据关联到业务结果和后继 checkpoint。Workspace 版本化管理的是登记的本地产物，
不会撤销已经发出的消息、购买或其他外部效果。

### 4.3 继续执行时区分已知与未知

Owner stop 为未完成工作保存 checkpoint；`resume` 续接最近一次 owner-stopped cut。
用户选定的 `reopen` 在同一 Registry 内，从已提交 checkpoint 追加新的执行代次，保留后续历史与文件作为证据。
Reopen 是显式的新执行，不是删除中间经历。主会话回退不会抹除独立子任务。

在文档明确支持的窗口内，恢复可以使用准确的持久完成证据结算，而不重复 operation。
远端结果不明的 provider 提交保留为 `submission_unknown`，超时不是“没有远端效果”的证明。
显式 owner-selected reopen 可按受支持协议闭合未决尝试，以新身份继续。
当 operation 可能产生费用或修改外部系统时，这种区分尤其重要。
参阅[checkpoint 恢复](guides/checkpoint-recovery_ZH.md)。

## 5. 流程构造、组合与演进

### 5.1 两个 authoring 层级

`AgentWorkflowGraph` 是便捷的模型/程序工作流语言，包含职责、命名输入输出 artifact、显式弧、
execution selector 和唯一入口/出口。主 Designer 可以提出这种结构；验证与 lowering 将其转化为可执行 place、
transition、fan-out 出现项及有界返工许可。数组顺序不是依赖权威。
通用 `ModuleDeclaration` 则用于更丰富的类型化组件契约、资源行为、终态规则与可信应用扩展。

当前 graph 边界要求 native-plugin 节点各有一个输入和输出，且存在此类节点时不允许 graph feedback。
选择混合 optimize–validate 设计时需要考虑这一实际约束，但它并不描述所有通用 module。
参阅 [`agent_workflows.py`][graph-source] 与[声明接口](reference/declarations_ZH.md)。

### 5.2 流程也可以是流程的产物

Net-definition 组件接受并产生 `rpnh/module_declaration/v1` 资源。
Extract 选择受支持的完整 module/组件；Compose 连接显式兼容的公共边界；Instantiate 建立独立命名的符号；
Branch 提取并可进一步实例化。这些操作可以成为更大工作流中的登记 firing。
因此，“设计 → 评估 → 选择 → 修订”是应用可以构造的模式，而不是写死在 harness 内核中的策略。
生成定义与采用定义仍然是两步。参阅[网操作](guides/net-operations_ZH.md)。

### 5.3 Author revision 保留可复用结构

显式启用的 collaboration API 增加不可变 author revision、稳定元素身份、显式边界映射，以及父 revision/
selected-change 来源。Branch 推进使用准确 expected head；普通 module 与 graph-source 的 merge 分析
和 resolved revision 发布分开。Selected-change transplant、显式 split/fusion 历史、open-region
义务与有边界的 assembly 协议，表达更细致的复用和演进。

实际公开类包括 `ClosedModuleAuthor`、`GraphModuleAuthor`、`PlainModuleMergeAnalyzer`、
`PlainModuleMergeAuthor`、`OpenRegionAuthor` 与 `AssemblyAuthorV9`，它们支持的契约并不相同。
Assembly 固定成员 revision 和 lowering 映射，使审查者能够追问准确的源对象，而不是仅按标签识别元素。
参阅[公开导出][collaboration-source]、[graph authoring](reference/graph-authoring_ZH.md)、
[身份变换](reference/author-identity-transform-contract_ZH.md)与
[assembly 历史/合并](reference/assembly-full-history-merge_ZH.md)。

Coding Agent 和人都可以作为 author 使用这些接口。重要研究问题是下一次编辑、修复或组合能否更简单、
更少出错。这种价值不依赖图形编辑器；只读 dashboard 也不是图形编辑器。

### 5.4 将 revision 接入执行

RPNH 有两条显式桥梁：

1. **Owner 驱动的替换。** 基于准确当前网准备完整候选，暂停新准入，等待活动 firing 收束，
   应用显式出现项映射/退役，再用原有预算采用后继网。同 owner 的替换保留 workspace 血缘和执行环境。
2. **已声明 operation 驱动的修订。** 登记 effect 提供准确 Module 产物与 `DeclaredModuleRevision`。
   Core 验证绑定资源、编译候选、推导并核对结构 delta、映射保留的出现项，只允许显式有限激活。
   Revision witness、后继 checkpoint 与采用在结算时一起闭合。
   Whole-net switch 要求当前 firing 是唯一未闭合的 provisional firing。

这些机制可以在受控变更中保留有意义的工作，不会克隆在途模型调用，也不会对 live execution 进行无条件语义合并。
参阅 [`module_revision.py`][revision-source] 与原生网操作[测试][net-tests]。

## 6. 跨环境复用与协作边界

### 6.1 共享方法，由接收端绑定执行

Portable package 路径将声明材料与私有本地配置分开。Package 包含受支持的声明、schema、资源、要求及来源；
接收端解释器路径、plugin 配置、credential reference、environment lock、receipt 与私有运行证据具有不同归属。

已安装命令的路径是具体的：

1. `rpnh package preview` 验证有界本地 ZIP 材料，不解压或执行。
2. `package resolve` 选择准确的本地依赖，为受支持的 closed-module entry 生成 lock。
3. `check-environment`、`resolve-environment`、`plan-environment` 检查选定 HOST 和显式本地 wheel，
   形成具体计划。
4. `setup-instructions` 将同一计划输出给操作者；`prepare-environment` 在准确计划获批后执行受支持动作。
5. `package run` 单独批准运行，复核 binding/receipt/当前 HOST，并进入正常 owner/harness 执行。
   环境准备成功本身不是业务结果。

Native-plugin receiver 使用提供的带 hash wheel，以 isolated、no-index/no-deps 方式安装。
这是有界接收端流程，不是通用包管理器。Package v1/v2 支持一个 closed-module entry；
更丰富的 author/assembly 能力不会自动变成可移植 package 格式。Public 元数据也不等于自动脱敏，
author 仍需在共享前审查内容。参阅[可移植 package](guides/portable-packages_ZH.md)、
[环境准备](guides/package-environments_ZH.md)与[可运行教程](guides/package-reuse-example_ZH.md)。

### 6.2 共享定义、读取证据与接受工作

普通 closed-author subnet import 在目标 HOST 检查和 expected-head 保护下创建本地身份，
保留 `copied_from` 来源，而不启动执行。有范围的 Registry read session 使用选定来源和既有读取权威。
Workset 记录贡献、交付、接受的准确身份；首次 `WorksetOwner.accept_delivery` 需要真实登记 operation outputs，
重复的同一交付则可读取先前接受结果。

这些是流程复用与准确贡献登记的有用构件。应用应保留其当前本地/显式绑定范围，而不是把它们描述为完整远程协作服务。
接口见[normal-child/Workset 契约](reference/normal-child-root-contract_ZH.md)与
[read session](reference/registry-read-sessions_ZH.md)。

### 6.3 观察支持审查

`rpnh net --run RUN` 与本地 dashboard 投影真实 Registry 状态，包括资源、checkpoint 与选定活动。
独立 read host 可以在定义、配置、材料和运行维度上比较选定来源。来源限定引用和显式元素映射避免把不同来源中的
同名对象混为一谈；缺失映射或未披露材料保持 partial/unknown。

Viewer 保持只读，跨来源比较也不是全局原子快照或自动性能比较。它的价值是为诊断和审查提供可追溯依据。
参阅[比较上下文](guides/comparison-context_ZH.md)。

## 7. 嵌入、扩展与操作接口

受支持的已安装入口是 `rpnh`。用户持有的 provider/准确模型 catalog 初始为空；前端不会另建一套 provider owner。
Python authoring 接口提供的能力多于便捷 CLI。

| 目标 | 当前接口 | 实现入口 |
|---|---|---|
| 对话与独立工作 | `rpnh --frontend basic`；`/agent`、`/workflow`、`/tasks`、`/task ID result` | `main_session.py`、`task_control.py` |
| 定义类型化流程 | `ModuleDeclaration` + `Registration` + compiler | `module.py`、`registration.py`、`compiler.py` |
| 定义 Agent/程序图 | `AgentWorkflowGraph`、节点 execution selector | `agent_workflows.py`、`agent_tasks.py` |
| 接入现有业务代码 | 显式安装的 `rpnh.plugins` entry point；`PluginDefinition`、`PluginOperation` | `cpn/plugins/api.py`、`catalog.py` |
| 修订或组合创作材料 | 显式启用的 Python author/branch/merge/assembly API | `cpn/rpnh/collaboration/` |
| 导出可修改示例 | `rpnh examples list` / `rpnh examples export` | `cpn/examples/` |
| 绑定并运行收到的 package | `rpnh package …` | `collaboration/package_cli.py`、`environment_cli.py` |
| 检查与比较 | `rpnh net`；显式 independent read host | `cpn/frontend/`、Registry read-session API |

Plugin 声明输入输出 JSON schema、operation 身份、effect、资源与限制。确定性 plugin 可以不调用模型，
作为正式工作流节点运行；也可以显式绑定为某个 Agent 的 managed tool。
`PluginContext` 提供 execution/invocation 身份与协作式取消，不把 Registry writer 交给 worker。

有界并行调用、准确结果分页、可选隔离 tool program、资源查询与上下文管理为该结构提供配套。
它们的策略保持显式，不能因添加这些能力而悄悄改变 benchmark 工具权限或预算。
可信 native-plugin 进程与可选 Linux isolated-program substrate 有不同安全契约。
Registry 检查不能替代对可信 HOST 代码的审查，也不能替代所需的操作系统隔离。
参阅[自定义](guides/customization_ZH.md)与[受控工具](controlled-managed-tools.zh.md)。

## 8. 复现执行模型

源码案例库的确定性 parallel 示例适合端到端检查，其拓扑为 `prepare -> (facts || risks) -> join`。
预设 local-process 响应经过真实执行路径，但不评价模型推理能力。
在 Linux/WSL2、Python 3.11 及以上环境中，选定已审阅源码 commit 后执行：

```bash
git rev-parse HEAD
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check

DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-report.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

检查两个分支是否都形成登记结果后才进入 join、最终资源是否由 terminal evidence 选定，以及 viewer 是否对应
准确 run/checkpoint。成功时示例输出 `status: PASS`；最后一条命令启动本地只读服务。
依赖安装可能联网，默认 fixture 不调用真实模型。这是复现方法，不表示本次报告修订重新执行了示例。

随后可用[原生 plugin](../examples/native_plugin/README_ZH.md)修改真实程序契约，
用[混合汇总](../examples/hybrid_summary/README_ZH.md)检查 Agent–程序–Agent 数据流，
或用[package 复用](../examples/package_reuse/README_ZH.md)在接收环境中绑定同一 closed process。
已安装 export catalog 支持 `adapter_task`、`native_plugin`、`hybrid_summary`、`compose_serial` 和 `package_reuse`。
其中 `compose_serial` 演示定义组合，执行仍需匹配的可信 Registration。

真实 provider 检查是单独步骤，需要准确模型条件与获准预算。当前源码是 rc2 开发候选；
较早公开的 rc1 二进制不包含后续源码能力。[安装说明](guides/installation_ZH.md)记录了这种区别。

## 9. 证据与当前结果

### 9.1 快照与证据层次

实现快照是 `74fad32`；最终 ERP smoke/showcase 实际测试于
`6f8ee2e406f3c70edb73206f861e56a0202b9f15`，记录中的 tracked source 为 clean。
后续集成提交补充证据与示例，不会将旧记录变成新源码版本的测试。
两次运行都使用 `gpt-5.6-terra` / `local_process`，ERP-Bench task/scorer 固定为
`ceba3880af555129b5278e056a0c20f2fb5a0ba9`。未记录的 reasoning/profile 字段保持未知。
参阅[源码身份][erp-source]与[模型条件][erp-model]。

| 记录 | 保留观察 | 含义 |
|---|---|---|
| ERP `2000_easy_01_buy_only_baseline`，A04 / `s04` | 100/100，业务 `passed=true`，37 条适用检查全部通过，9 次真实 provider 调用 | 记录中的修复条件下，一次 smoke 任务通过。可读公开评分/生命周期投影与 raw-file hash；本证据集未包含 A04 raw grader 字节。 |
| ERP `2299_hard_repair_plan_hard`，A01 / `h01` | 21/100，业务 `passed=false`，95 条适用检查中 86 条通过，13 次真实 provider 调用 | Native/provider/evaluator 路径完成，业务任务失败。本次同时检查了原始 reward、逐条规则、checker log 与投影。 |
| 历史 ERP smoke A03 | 0/100，11 次真实调用，源码 `2ca5fbc` | 较早的输入能力绑定失败，按原条件保留，不是当前源码的重跑结果。 |
| ERP 定向测试窗口 | 190 passed，另计 35 subtests | 已记录命令覆盖 `examples/erp_bench/tests`，不是 225 个独立测试或全仓通过；本次报告没有重跑。 |
| SlopCodeBench `code_search` | Adapter、合成检查与 native/Docker fixture 记录已集成 | 尚无完整 benchmark 求解或原 evaluator 验收结果；fixture 通过不是任务分数。 |
| 历史 AutomationBench | Freeze04 first18：5 PASS / 9 FAIL / 4 BLOCKED；另一次 repair4：1 PASS / 3 FAIL | 分别属于有日期的条件，较早的 14 项 scored tasks 未重跑，见[公开记录](../examples/automationbench/PUBLIC_RESULTS_20261006.md)。 |

来源：[A04 评分投影][erp-smoke]、[H01 原始 reward][erp-reward]、[逐条规则][erp-rules]、
[A03 诊断][erp-a03]、[定向测试命令][erp-test-command]与[日志][erp-test-log]。
[发布验证历史](guides/release-validation_ZH.md)保留其他版本的离线结果；重叠窗口的测试数不能累加。

ERP 环境明确属于 adapted runtime，记录 Harbor 0.24.0、Odoo 19.0.20260926、Python 3.12.3 与
PostgreSQL 18.6。Solver 禁止外网，只访问本机 Odoo，不等同于原封不动的上游网络条件。
受管 action 粒度是完整 Python script，而非每个 Odoo transaction。参阅[world 投影][erp-world]。
这两个单次运行既不是任务集统计，也不是受控 harness 比较。

### 9.2 复杂任务失败提供了什么信息

H01 的价值在于同时呈现执行完成与业务拒绝。21 分**不是** `86/95` 换算的百分比。
原 scorer 在约束未满时阻止其他维度进入总分：constraint 为 63/75，最终 `25 × 63/75 = 21`。
Hygiene 为 15/20，optimality 子分数为 91.66，但不能补回门控后的总分。
[原始计分规则][erp-score-rule]保持不变。

九条失败包括四条约束与五条组件采购来源检查，需要进一步分层解读：

- **容量口径不一致有明确证据。** Actor 报告 5,130 分钟，与回读工单字段中 114 件、每件 45 分钟一致。
  任务固定 WC02 路线按每件 55 分钟计量，`114 × 55 = 6,270`，超过 5,555 分钟上限。
  实际存在回读，但回读计量不符合该任务路线语义。见[实际回读][erp-readback]与[scorer 路线规则][erp-route]。
- **三项失败包含 checker 异常。** 供给时序、MO 排程、组件库存容量均记录 `bool` 与 `datetime.date` 的比较错误。
  缺失/false 截止日期与上游日期处理交互，是有依据的候选解释，并非已经证实的唯一根因：
  保留的回读未给出缺失的最终字段值。保留原 FAIL 与 21 分，同时区分计算异常和成功计算后的约束违约。
  见[checker log][erp-checks]。
- **局部计划算术窄于世界状态验证。** `validate_plan` 对提供的计划返回无违规、支出 111,938.90；
  原 grader 按其全局范围得到 181,383.92，expected 为 133,394.07。
  该 helper 明确只校验提交的 observations，不查询 Odoo、不证明全局世界正确，差额尚未完整归因。
  五项采购来源失败也仍未解决。见[validator 输入输出][erp-plan]与[实现][erp-planning]。

H01 在 ERP 动作前已成功读取登记任务输入，不能沿用 A03 的缺失 reader 解释。
它还保留了针对不存在 workcenter 字段的 schema 试探失败，之后脚本继续。
这些与 core 调度失败或 provider outage 是不同的问题。

A03 的诊断定位于示例 Actor 能力绑定遗漏。后续修复增加登记输入 reader，并经有限输入交付/生命周期 fixture 检查；
A04 随后在 fresh world 中通过 smoke。这是可行动的修复证据，但不同源码与不同运行不足以证明
harness 性能的普遍因果改善。参阅[输入交付检查][erp-input-checks]。

### 9.3 证据覆盖本身也是结果的一部分

ERP [收集清单][erp-manifest]区分原文件、登记 payload 字节、既有投影与序列化元数据。
它保留了 40 个历史 fresh-reader provenance 失败，涉及缺失 `agent_loop_ref`；
核对 canonical object 字节并不修复这些 provenance。
该 `s03`/`h01` 收集范围未找到 vendor HTTP wire 与完整 Codex CLI events；
`s04` 则保留公开投影和 hash，而非 raw control transcript。

[发布审查][erp-publication]记录了 595 个 payload 的 digest/size 检查。
这属于发布完整性结果，不证明所有执行或 provenance 检查通过。
本报告交叉检查了选定记录，并未独立重算整个集合的 hash。
保留这些区别，才能让失败服务于工程改进，而不补造缺失证据。

## 10. 评估与优化方法

### 10.1 测量完整用户任务

有用的评估至少区分六个维度：

1. **业务结果：**原始 grader、领域约束、验收产物与独立检查。
2. **执行一致性：**已准入动作、准确产物/终态血缘、过时/重复贡献处理，以及对未知状态的如实记录。
3. **演进与复用：**改变的定义/成员身份、保留工作、后继 revision 有效性，以及接收端成功情况。
4. **Author/操作者工作量：**修改量、准备负担、修复时间、介入次数与诊断准确率。
5. **性能与资源：**总耗时、可获得的模型 attempt/usage、原生工具耗时、owner/Registry 耗时、存储与 workspace 复制开销。
6. **披露与可移植性：**实际披露、可信依赖、平台要求与明确不支持的情形。

原任务结果使用原 benchmark 任务和 scorer；续接或修改任务单独报告。
冻结模型/准确配置、指令、工具权限、反馈策略、任务集、seed/reset、checkpoint 披露顺序、环境与预算。
统计所有尝试，包括失败及未知提交。Usage 或费用无法可靠获得时标为 unavailable，不按调用次数猜测。

比较对象应是具有普通工具调用、并行、持久化及适用恢复能力的合理基线。
可做的消融包括固定/修订流程定义、复用流程/重新创作、观察辅助/普通诊断，以及保留 workspace/checkpoint/全新启动。
每个变体保持相关权限与预算，明确究竟改变了哪个机制。Registry 一致性检查不能代替业务质量指标。

### 10.2 选择真正检验价值的任务

第一批案例提供互补问题：ERP plan repair 检查真实业务状态、硬约束和来源；演进式 code-search 任务检查
重复需求、功能正确性和 workspace 连续性。后续混合仿真/优化案例可检验原生程序复用与独立数值验证。
如果主张针对流程资产，每个案例就应包含复用或变更阶段。仅仅执行预先提供的图，不能证明 Agent
创作或改进了该图。

还应加入低结构的一次性对照任务，暴露额外开销。中断或未知效果测试应事先定义故障窗口，同时测量
物理调用、真实外部效果与 Registry 记录。Authoring 实验应测量第二次成功修改及保留的组件身份，
而不只是统计可用 API 数量。

### 10.3 当前改进优先级

下一步应先让已有机制更易使用、更易诊断，再考虑新增调度器或扩大协作服务。
以下是**提案和验收条件**，不是已实现功能公告：

| 优先方向 | 具体干预 | 验收证据 |
|---|---|---|
| 有效任务预检 | 逐 operation 只读展示输入交付、selected/bound/declared/provider-visible 工具、profile、effect 和结果 reader；复用 compiler/catalog 逻辑，不发放新权威。 | 在 dispatch 前区分必要 reader 遗漏、selector 错误或结果 reader 缺失，同时不拒绝合法替代输入路径；与实际登记 request/catalog 对照。 |
| 分层诊断导出 | 在固定 cut 关联准确 Registry、adapter、业务世界与 grader 记录，分别保留 missing、redacted、provenance-rejected。 | Fresh reader 能识别已完成工作、未知项和失败层次；缺失阶段不变成成功；对相同失败测量诊断正确率/耗时。 |
| 公共 exact-Module runner | 抽出已有高层 Agent runner 的生命周期/绑定装配，让验证后的组合 Module 直接使用，并提供 author-only 初始化 helper。 | 两个真实 closed member 组合、重开并按所选准确编译闭包执行，profile/tool 身份及停止恢复保持一致，减少重复私有装配。 |
| 带覆盖率的 usage | 只读投影分别记录 logical returned call、physical attempt、provider token usage 与基于版本化价格的估算。 | 重复引用不重复计数，部分/未知用量保持未知，估价标明价格来源、币种和覆盖；既有调用预算含义不变。 |
| 类型化 ERP observation | 为实际回读实体、日期、单位、route/workcenter 规则和支出范围建立版本，分别记录计划、已写入与观察状态。 | 离线案例捕获单位/范围/日期差异；fixture 回读对应真实状态；原评分保持独立，原失败不被覆盖。 |

Exact-Module 提案针对具体 SDK 接缝：`start_run` 已支持通用 module，而 `AgentTaskSpec` 接受 stage 或 graph，
便捷 runner 在装配 managed service 时重新构造 Module。提案复用现有 owner/runtime，不是在补造缺失的核心语义。
参阅 [`agent_tasks.py`][agent-task-source] 与 [`start_run`][owner-source]。

同样，较早 ERP input-reader 遗漏已经修复；预检旨在防止其他应用再次遗漏。
类型化 ERP 验证属于领域 adapter，应使用任务合法可得事实，不将隐藏 grader 规则/参考解暴露给求解 Agent。
独立诊断 fixture 可以调查日期异常，而不修改原结果。
当前 SCB adapter 通过上游 Session 路径保持 source workspace，明确报告 `native_workspace_reuse=false`，
因此不主张已经验证 RPNH 原生 workspace 复用。参阅 [SCB 案例](../examples/slopcodebench/README_ZH.md)。

应先建立确定性契约和受支持用户入口的行为，再通过单独获授权的模型实验，检验业务验收或工作量是否改善。
测量之前，它们是具体工程问题，不是预计收益。

## 11. 持续更新约定

本文是一份持续演进的报告，链接逐次实验证据。每次优化应更新受影响的机制说明，追加具有准确条件的结果行，
并说明由此形成的工程决定。只有条件可比时，新结果才能取代旧主张；原始失败始终保留。

简洁的实验条目应包含：

| 字段 | 应记录的内容 |
|---|---|
| 身份 | 稳定实验 ID、日期、准确代码 SHA 与工作树是否有改动；准确 upstream/task/scorer revision。 |
| 问题 | 目标机制、预期收益、竞争性解释与验收条件。 |
| 条件 | 不含秘密的 model/profile 身份、输入 hash、工具、权限、预算、环境与 reset/reveal 规则。 |
| 干预 | 准确改动的代码/配置/流程 revision，以及保持不变的控制条件。 |
| 结果 | 尝试数；runtime terminal；原始业务分数与失败检查；产物/血缘验收；耗时及可获得 usage。 |
| 证据 | 已审查公开 manifest、报告与允许披露投影的链接/hash；覆盖范围或缺失数据。 |
| 决定 | 接受、拒绝或尚无结论；代价、回归与下一次实验。 |

这是一项报告约定，不是新增 runtime schema。已有案例证据契约时继续沿用。
只发布经过审查、许可证允许、非敏感的证据；私有原始 run、凭据、本地 profile 与未经审查的 transcript
不进入报告。

### 报告演进

| 报告版本 | 变化 |
|---|---|
| 2026-10-01.1 | 以 `40f1be8` 为基线的源码介绍，解释 runtime、workspace 与网操作。 |
| 2026-10-08.1 | 在同一报告中扩充流程资产主线，补充当前 authoring/receiver/read 接口，并在 `74fad32` 基线上加入第一批证据、优化问题及逐实验更新约定。 |

本次文档修订检查了源码与保留记录，没有启动模型实验、执行 runtime 套件或修改实现。

## 12. 相关工程工作与源码地图

本文借鉴一手工程材料的表达方法：先说明任务，沿一次完整执行展开，明确状态归属，
再将机制与证据及代价相连。

- DeepSeek 的 [Harness 架构](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/docs/architecture.md)
  适合参照插件职责和 turn 生命周期。其链接的 [Cordis 论文](https://arxiv.org/abs/2608.25512)
  *A Programming Paradigm for Spatiotemporal Composability* 讨论可组合性及成立条件，
  并非 DSH 全系统任务性能报告。
- OpenAI [Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
  （2026-01-23）解释完整 request/tool/context 路径。
- [Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/)
  （2026-02-04）解释共享内核与客户端协议边界。
- [Harness engineering](https://openai.com/index/harness-engineering/)
  （2026-02-11）将环境、可观察性、约束与反馈相连。
- [Codex as a platform](https://developers.openai.com/blog/codex-as-a-platform)
  （2026-08-19）区分应用接入与可复用执行层。

这些材料用于组织报告与交代相关工作，不支持 RPNH 性能结论。
持久任务、类型化接口、事件历史、checkpoint 和 plugin 系统都有先例。
比较主张需要同条件实现核查与实验，不能靠功能清单得出。

| 实现问题 | 固定版本源码 |
|---|---|
| 什么数据描述流程？ | [`module.py`][module-source]、[`petri_contracts.py`][petri-source] |
| 如何选择可信实现？ | [`registration.py`][registration-source]、[`compiler.py`][compiler-source] |
| Agent graph 如何成为执行结构？ | [`agent_workflows.py`][graph-source] |
| 谁准入和结算 operation？ | [`harness.py`][harness-source]、[`RunOwner`][owner-source] |
| 发布如何闭合？ | [`registry/_event_store/commit.py`][commit-source] |
| 返回的定义如何改变执行？ | [`registry/module_revision.py`][revision-source] |
| 流程 revision 和 assembly 如何公开？ | [`collaboration/__init__.py`][collaboration-source] |
| 收到的 package 如何运行？ | [`collaboration/environment_host.py`][receiver-source] |
| 如何读取任务的真实结果？ | [`task_control.py`][task-control-source] |
| 哪里检查具体执行示例？ | [`test_native_net_operations.py`][net-tests]、[发布验证](guides/release-validation_ZH.md) |

源码链接固定实现快照，不表示所有私有类都是稳定 SDK。核心命题始终可以检验：
具有显式可执行含义的流程，可以成为可复用、可检查、可版本化的资产。
下一阶段实验应说明这种结构在何种实际工作中带来足够收益，值得付出其成本。

[module-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/module.py
[petri-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/petri_contracts.py
[registration-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/registration.py
[compiler-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/compiler.py
[harness-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/harness.py
[owner-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/run.py
[commit-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/registry/_event_store/commit.py
[graph-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/agent_workflows.py
[revision-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/registry/module_revision.py
[collaboration-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/collaboration/__init__.py
[receiver-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/collaboration/environment_host.py
[task-control-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/task_control.py
[net-tests]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/tests/test_native_net_operations.py

[agent-task-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/agent_tasks.py
[erp-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/public/source-identity.json
[erp-model]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/public/model-configuration.json
[erp-smoke]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/s04/public/original-score.json
[erp-reward]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/reward.json
[erp-rules]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/rule_results.tsv
[erp-a03]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/records/input-delivery-a03-v2/public/diagnosis.json
[erp-test-command]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/scb-local/checks/erp-integrated-tests-01.json
[erp-test-log]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/scb-local/checks/erp-integrated-tests-01.log
[erp-world]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/public/world-projection.json
[erp-score-rule]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/test.sh#L351-L362
[erp-readback]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/registry/h01/agent_action_v3/a33b2d441be95df9b29a5ee5336489fc.json
[erp-route]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/checks.py#L305-L313
[erp-checks]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/logs/r/h01/w/harbor/verifier/checks.log
[erp-plan]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/registry/h01/agent_action_v3/b72a1257ec485ed3a8d0c7900d59af2f.json
[erp-planning]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/examples/erp_bench/src/rpnh_erp_bench/planning.py#L40-L89
[erp-input-checks]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/work/final-independent-review/input-delivery-closure.json
[erp-manifest]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/MANIFEST.json
[erp-publication]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/ERP_PUBLICATION_REVIEW.json
