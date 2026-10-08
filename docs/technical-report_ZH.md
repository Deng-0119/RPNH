---
name: rpnh-technical-report
description: "RPNH 的架构、流程创作、运行机制、接入方式与已公开运行结果。"
metadata:
  document-kind: technical-report
  audience: application-developer-and-researcher
  language: zh
  counterpart: technical-report.md
  revision: "2026-10-08.3"
  status: technical-report
  basis: "Deng-0119/RPNH at dbad00458e9b356fcaf0bb97ceb90258ed9b1de0"
  erp-runtime-supplement: "integrated at e92b05c9afe324ebb675f2d67b73c02efe7b9536"
---

[English](technical-report.md) | 中文 | [文档导航](index_ZH.md)

# RPNH 技术报告
## 面向 Agent 与程序系统的可执行流程

**更新日期：**2026-10-08。**源码快照：**
[`dbad004`](https://github.com/Deng-0119/RPNH/tree/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0)。
各次实验的实际测试版本与结果发布版本分别列于[第 9 节](#9-示例验证结果)。
ERP runtime 补充见集成版本 [`e92b05c`](https://github.com/Deng-0119/RPNH/tree/e92b05c9afe324ebb675f2d67b73c02efe7b9536)。

## 摘要

RPNH 是一个用于组合语言模型 Agent、原生程序与可复用工作流的 provider-neutral harness。
它将流程视为类型化的可执行资产：流程的依赖、输入、输出和完成条件可以被构造、组合与修订，
实际执行则关联持久化记录。Registry 保存准确身份、资源版本和已提交事实；类型化 PetriNet
基于这些记录决定哪些工作可以执行，以及产物如何使后续步骤获得执行资格。

同一 runtime 支持对话式主会话、独立任务和多 Agent 工作流。它将流程定义连接到可信 HOST 实现，
记录 workspace 血缘与 checkpoint，并提供执行的只读视图。Authoring 与 package 接口让开发者
能够在不同修订和接收环境之间复用流程定义，而不必携带原机器的私有配置。

本文介绍当前架构、operation 生命周期、流程创作与复用，以及受支持的使用入口，随后呈现已公开的
ERP-Bench 与 SlopCodeBench 运行结果，并分别报告原任务验收和 runtime 完成情况。
这些是有明确范围的应用结果；现有实验尚未建立相对其他 harness 的质量、速度或成本优势。

## 1. 从 Agent 对话到可复用流程

Agent 应用常常需要组合执行方式不同的工作：模型理解请求，程序执行计算，多个分析独立进行，
后续步骤再消费它们的产物。应用还需要知道某项输出来自哪些输入、中断后保留了什么，以及接下来执行哪个流程版本。

RPNH 将这些关系显式化。以 `prepare -> (facts || risks) -> join` 为例，两条分支分别产生输出；
只有两项必需产物齐备，join 才能获得执行资格。节点换成既有业务程序时也遵循同一原则。
流程定义描述工作，可信注册提供实现，runtime 记录准确执行。

| 能力 | 当前机制 | 应用中的用途 |
|---|---|---|
| Agent 与程序混合执行 | 已登记组件/executor、类型化端口与声明的 outcome | 在同一流程中连接推理、确定性计算与验证。 |
| 显式协调 | PetriNet place、arc、token 与资源占用 | 表达依赖、并行分支、join 与资源使用。 |
| 可追溯产物 | 准确 Registry 引用、登记产物与终态证据 | 读取选定结果及其执行上下文。 |
| 继续执行与受控变更 | Checkpoint、执行代次、workspace revision 与采用映射 | 继续已停止工作，或带着明确血缘采用修订后的流程。 |
| 可复用流程定义 | `ModuleDeclaration`、组合、author revision 与 portable package | 保留并调整生成结果的方法。 |
| 独立观察 | Read session、图/checkpoint 投影与比较 | 检查执行并比较选定来源，而不成为 writer。 |

这套架构主要适用于需要显式依赖、持久化产物、重复复用流程或受控修订的应用，也会带来声明、验证与状态管理工作。
短暂的一次性交互可以只使用对话入口，或采用更简单的工具循环。

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
因此[第 9 节](#9-示例验证结果)将原任务评分与 runtime 闭合分别报告。

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

ERP adapter 对显式 bridge `unknown` 或派发后丢失/无效的回复，生成 managed `outcome_unknown`
回执。Registry 准入阻断该 operation 中的同一调用及新调用，service 重建后仍有效。
已知的 `completed`、`failed`、`domain_infeasible` 保持为 returned 结果。
既有 `interrupted` 分类不变，bridge 的物理安全闸保留；发送前连接失败仍可能保守地归为 unknown。
这些是 script 粒度的防重放控制，不构成逐笔 ERP 事务的 exactly-once 保证。
参阅 [ERP managed-operation 契约][erp-unknown-contract]。

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

人和 Coding Agent 都可以使用这些 Python 接口创作流程修订。稳定身份与显式映射将每次修订连接到
其来源结构，也让选定改动能够用于后续组合。

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

这些接口通过本地、显式绑定的 author/read host，支持流程复用与准确的贡献登记。
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
HOST 策略决定任务获得哪些工具、effect 与预算。Native plugin 作为可信 HOST 代码运行；
可选的 Linux isolated-program substrate 提供另一层执行边界。应用根据工具选择合适的信任与隔离策略。
参阅[自定义](guides/customization_ZH.md)与[受控工具](controlled-managed-tools.zh.md)。

## 8. 开始使用与示例工作流

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
依赖安装可能联网，默认 fixture 不调用真实模型。

[混合汇总](../examples/hybrid_summary/README_ZH.md)示例展示具体的混合执行：
`normalize -> demo/summarize -> explain`。两个 Agent 节点之间是执行汇总计算的原生程序。
确定性模式使用脚本化 Agent 响应，选定 execution profile 后可进入真实模型路径。
每次交接都使用登记输入与输出，因此同一张图既可从业务节点层面观察，也可从 PetriNet 层面检查。

随后可用[原生 plugin](../examples/native_plugin/README_ZH.md)修改真实程序契约，
用[混合汇总](../examples/hybrid_summary/README_ZH.md)检查 Agent–程序–Agent 数据流，
或用[package 复用](../examples/package_reuse/README_ZH.md)在接收环境中绑定同一 closed process。
已安装 export catalog 支持 `adapter_task`、`native_plugin`、`hybrid_summary`、`compose_serial` 和 `package_reuse`。
其中 `compose_serial` 演示定义组合，执行仍需匹配的可信 Registration。

使用真实模型时，先按[模型指南](guides/models_ZH.md)配置 provider、准确模型和 execution profile，
再在任务或示例中选择该 profile。当前源码为 `0.1.0rc2` 开发候选；较早公开的 `v0.1.0rc1`
二进制对应更早的功能集合。源码与发布包选项见[安装说明](guides/installation_ZH.md)。

## 9. 示例验证结果

这些示例将 RPNH 接入两类不同应用：在持久化业务系统上执行 ERP 操作，以及在逐步披露需求下修改代码。
以下分别列出原始 evaluator 的结果与原生 runtime 的终态。

### 9.1 ERP-Bench

ERP adapter 通过 managed Python script，将 Agent 连接到本地 Odoo world，并提供确定性计划校验工具。
Agent 可以检查业务实体、准备计划、应用变更并回读状态；原 ERP-Bench grader 独立评价该状态。
一个 managed operation 对应一段 script，其中可能包含多个 Odoo 动作。
参阅 [ERP 示例](../examples/erp_bench/README_ZH.md)。

下列运行使用 tracked source 为 clean 的 RPNH
`6f8ee2e406f3c70edb73206f861e56a0202b9f15`，模型为经 `local_process` 接入的
`gpt-5.6-terra`，ERP-Bench task/scorer revision 为
`ceba3880af555129b5278e056a0c20f2fb5a0ba9`。证据于 `74fad32` 集成，发布版本与实际测试版本分别记录。
适配环境为 Harbor 0.24.0、Odoo 19.0.20260926、Python 3.12.3、PostgreSQL 18.6；
solver 只能访问本地 Odoo，没有外部网络。
[源码身份][erp-source] · [模型条件][erp-model] · [业务环境][erp-world]。

| 任务与运行 | 原始业务结果 | 适用检查 | 真实模型调用 |
|---|---|---|---|
| `2000_easy_01_buy_only_baseline`，A04 / `s04` | 100/100，通过 | 37/37 | 9 |
| `2299_hard_repair_plan_hard`，A01 / `h01` | 21/100，失败 | 86/95 | 13 |

来源：[A04 评分投影][erp-smoke]、[H01 原始 reward][erp-reward] 与 [H01 规则结果][erp-rules]。
A04 保留公开评分/生命周期投影及原文件 hash；H01 还包含原始 reward、规则结果与 checker log。
[收集清单][erp-manifest]说明可用材料及历史 provenance 校验范围。

复杂任务完成了执行路径，但未达到业务验收。九项失败包括四项约束与五项组件采购来源检查。
原计分规则在约束不完整时阻止其他维度计入总分，由 63/75 constraint 分得到最终 21/100；
86/95 是检查计数。一项明确差异是工位容量：Agent 回读按每件 45 分钟计量，任务路线规则则使用
55 分钟，得到 6,270 分钟，超过 5,555 分钟上限。另外三项约束检查记录了 `bool`/`datetime.date`
异常，仍计入原始结果。
[计分规则][erp-score-rule] · [实际回读][erp-readback] · [路线规则][erp-route] · [Checker log][erp-checks]。

此例呈现了职责分工：RPNH 记录执行、产物与血缘；应用提供领域校验，独立 grader 决定业务验收。
只针对提交 observations 进行校验的计划工具可以通过，而最终业务世界仍未通过原始检查。
这里的两个不同任务是各自独立的运行，不构成配对比较或任务集成功率估计。

### 9.2 SlopCodeBench

`code_search` 示例随着新 checkpoint 需求的披露，逐步修改同一代码库。当前接入使用外层 Python controller
选择 checkpoint 顺序、应用继续执行策略，并通过上游 Session 路径交接源码快照。
每个 checkpoint 内部由 RPNH 原生任务 runtime 执行 Agent 及 managed command。
跨 checkpoint 的源码连续性属于 adapter/controller 路径，记录为 `native_workspace_reuse=false`。
参阅 [SCB 示例](../examples/slopcodebench/README_ZH.md)。

已公开运行覆盖**五个 checkpoint 中的前三个**，模式为 adapted development prefix。
Runner revision 为 `31ceea3add480edb33431e70475c4c70597e6b31`，
problem revision 为 `9cd9ca3a51c3d3e2a99d2488a25baf73a2204451`，
模型为 `codex/gpt-5.6-terra`。实际安装的 RPNH 字节核对至
`74fad32d369876841686d10d33361c016e3d3648`；结果与执行证据发布于
`dbad00458e9b356fcaf0bb97ceb90258ed9b1de0`。
[运行条件][scb-summary] · [安装源码身份][scb-identity]。

| Checkpoint | 原始 evaluator 用例通过 | Evaluator 退出码 | Runtime outcome | 真实模型调用 |
|---|---|---|---|---|
| 1 | 13/13 | 0 | `complete` | 5 |
| 2 | 25/25 | 0 | `complete` | 5 |
| 3 | 40/47 | 1 | `complete` | 14 |

原始报告：[checkpoint 1][scb-cp1]、[checkpoint 2][scb-cp2]、[checkpoint 3][scb-cp3]。
第三点有七项业务测试失败，`infrastructure_failure=false`，其中 Core 失败两项、Functionality
失败五项，25 项 regression 全部通过。各点包含回归用例，不能把计数相加当作独立 benchmark 任务总数。

每个 checkpoint 的模型调用上限为 48 次，owner 等待上限为 7,200 秒。运行共使用 24 次真实调用，
没有超过调用上限，记录总耗时为 998.82 秒。上游 cost、net-cost、step cap 均为关闭状态（设为零）；
本次实际使用的有界控制是模型调用上限。任务级规范化 token 总量与 USD 费用未提供；
逐调用 adapter return 保留了 token usage 字段，这与规范化任务级汇总是不同层次。
[运行汇总][scb-summary] · [公开证据][scb-collection]。

Solver 无网络，每个 checkpoint 使用 fresh container 并继承源码快照。镜像构建与评测使用 host 网络，
镜像准备包含同版本下载兼容适配。原始 evaluator 已运行，官方 `AgentRunner` 与完整五点 benchmark 未运行。
Grader 反馈未用于修复 solver，也没有自动 retry 或 resume。所选 `any-case` 继续策略使外层命令
可以在第三点有失败的情况下正常退出；表中列出的仍是原始测试结果。
具体条件见 [development summary][scb-development] 与[环境适配][scb-adaptation]。

这次运行展示了三个连续需求阶段中的源码连续性与原生任务执行：前两点通过全部原始用例，第三点部分通过。
其范围不构成完整 benchmark 验收，也不支持相对其他 harness 的优势结论。

### 9.3 其他保留结果

仓库同时保留早期 ERP smoke A03：源码为 `2ca5fbc`，结果 0/100，11 次真实调用。
其[诊断][erp-a03]定位了输入 reader 的绑定遗漏；这一历史条件与后续 A04 分别记录。
一份已记录的 [ERP 离线测试窗口][erp-test-command]在 `examples/erp_bench/tests` 范围内报告
190 项测试通过，并另外报告 35 项 subtest。

另一次 ERP runtime 验证使用 `dbad004` 加 ERP adapter 变更和独立 native fixture，随后集成于
`e92b05c`。[源码记录][erp-unknown-source]分别保留实际测试 overlay 与发布 commit 的身份。
两组 native 验证均使用合成 backend，installed-owner 路径使用脚本化 provider；
没有真实 provider 调用、Odoo world 执行或原始 grader 运行。

| 验证范围 | 观察结果 |
|---|---|
| [离线回归][erp-unknown-offline] | 65 个唯一 pytest 用例全部通过，另有 4 个 subtest。此前因 AF_UNIX `EPERM` 阻断的 15 项均在本地通过。 |
| [已安装 TaskControl 生命周期][erp-unknown-owner] | `complete`、公共 stop、wall timeout 三个场景通过真实 worker 与 AF_UNIX 路径。完成场景有 terminal evidence；stop/timeout 静止退出，没有业务 terminal。 |
| [原生 managed receipt][erp-unknown-native] | 六个直接 owner API 场景通过：显式 unknown、backend 异常、completed 回复丢失、非零退出失败、领域不可行和完成。 |

六场景 fixture 共记录 9 次 worker 派发、9 次 bridge 请求和 9 次合成 backend 调用。
三个 unknown 场景均产生 `started -> outcome_unknown`；原 service 与重建 service 上的
18 个探针未增加回执或派发。已知结果保留原输出：缓存 replay 与变参冲突不再执行，合法新 ID 可以执行。
Service 重建沿用同一 owner 和 Registry，不是 OS owner 崩溃恢复。
[回执与传输证据][erp-unknown-native] · [原生 fixture][erp-unknown-fixture]。

[2026-10-06 AutomationBench 结果](../examples/automationbench/PUBLIC_RESULTS_20261006.md)
中，freeze04 first18 为 5 PASS / 9 FAIL / 4 BLOCKED，独立 repair4 条件为 1 PASS / 3 FAIL；
较早的 14 个已计分任务没有重跑。[发布验证历史](guides/release-validation_ZH.md)还记录了其他版本的检查。
这些不同任务、版本与测试窗口，均与上面的 ERP、SCB 结果分别呈现。

## 10. 工程背景与源码导航

RPNH 属于持久任务、类型化接口、事件历史、checkpoint 与插件式 Agent runtime 的工程实践。
[DeepSeek Harness 架构](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/docs/architecture.md)
介绍插件职责与 turn 生命周期。OpenAI 的
[Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/) 与
[Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/)
分别解释 request/tool/context 处理，以及共享内核与客户端展示之间的边界。
这些资料提供相关架构背景；已公开的 RPNH 运行并非与这些系统的对比实验。

RPNH 围绕版本化流程定义与准确 Registry/PetriNet 执行之间的连接组织 runtime，进而连接创作、执行、
继续运行与观察：方法可以作为结构化产物保留，由 HOST 绑定具体实现，其输出则可追溯到特定执行与流程修订。

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
| 如何读取任务选定的结果？ | [`task_control.py`][task-control-source] |

应用接入可从[已安装入口](#7-嵌入扩展与操作接口)和[示例](#8-开始使用与示例工作流)开始，再根据需要阅读对应
的 authoring 或 HOST 接口参考。源码导航提供实现细节；公开契约和当前平台要求见各节链接的指南。

[module-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/module.py
[petri-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/petri_contracts.py
[registration-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/registration.py
[compiler-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/compiler.py
[harness-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/harness.py
[owner-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/run.py
[commit-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/registry/_event_store/commit.py
[graph-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/agent_workflows.py
[revision-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/registry/module_revision.py
[collaboration-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/collaboration/__init__.py
[receiver-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/collaboration/environment_host.py
[task-control-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/task_control.py
[net-tests]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/tests/test_native_net_operations.py
[erp-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/public/source-identity.json
[erp-model]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/public/model-configuration.json
[erp-smoke]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/s04/public/original-score.json
[erp-reward]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/reward.json
[erp-rules]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/rule_results.tsv
[erp-a03]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/records/input-delivery-a03-v2/public/diagnosis.json
[erp-test-command]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-local/checks/erp-integrated-tests-01.json
[erp-world]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/public/world-projection.json
[erp-score-rule]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/test.sh#L351-L362
[erp-readback]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/registry/h01/agent_action_v3/a33b2d441be95df9b29a5ee5336489fc.json
[erp-route]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/checks.py#L305-L313
[erp-checks]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/logs/r/h01/w/harbor/verifier/checks.log
[erp-manifest]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/MANIFEST.json
[scb-summary]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/preparation/evidence/scb-real-summary.json
[scb-identity]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/preparation/evidence/scb-real-install-byte-identity.json
[scb-cp1]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/checkpoint_1/evaluation/report.json
[scb-cp2]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/checkpoint_2/evaluation/report.json
[scb-cp3]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/checkpoint_3/evaluation/report.json
[scb-collection]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/README_ZH.md
[scb-development]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/development-summary.json
[scb-adaptation]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/preparation/work/scb-real-plan01/adaptation.json
[erp-unknown-contract]: https://github.com/Deng-0119/RPNH/blob/e92b05c9afe324ebb675f2d67b73c02efe7b9536/examples/erp_bench/README_ZH.md#动作边界与生命周期
[erp-unknown-source]: https://github.com/Deng-0119/RPNH/blob/e92b05c9afe324ebb675f2d67b73c02efe7b9536/evidence/first_wave/20261008/erp-unknown/INTEGRATION_SOURCE_MATCH.json
[erp-unknown-offline]: https://github.com/Deng-0119/RPNH/blob/e92b05c9afe324ebb675f2d67b73c02efe7b9536/evidence/first_wave/20261008/erp-unknown/local/offline-deduplicated-results.json
[erp-unknown-owner]: https://github.com/Deng-0119/RPNH/blob/e92b05c9afe324ebb675f2d67b73c02efe7b9536/evidence/first_wave/20261008/erp-unknown/B/export_safe.json
[erp-unknown-native]: https://github.com/Deng-0119/RPNH/blob/e92b05c9afe324ebb675f2d67b73c02efe7b9536/evidence/first_wave/20261008/erp-unknown/C/export_safe.json
[erp-unknown-fixture]: https://github.com/Deng-0119/RPNH/blob/e92b05c9afe324ebb675f2d67b73c02efe7b9536/examples/erp_bench/scripts/unknown_native_acceptance.py
