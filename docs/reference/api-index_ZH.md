---
name: rpnh-api-index
description: "公开 Python facade、显式模块 API、协议及源码的分类参考。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: api-index.md
  revision: "2026-10-13.3"
  status: source-reviewed
  basis: "public source b16ecce83ba603b3c15f1c770bce172d4f44f88e; not a new runtime validation"
---

[English](api-index.md) | [中文](api-index_ZH.md)

# API 参考地图

[文档首页](../index_ZH.md) · [CLI 参考](cli_ZH.md) · [源码地图](../guides/repository-layout_ZH.md)

先在下表选择任务，再进入契约和源码。本页索引公开源码 `b16ecce8` 中导出的 Python facade，以及已有文档明确介绍的模块接口；它是导航参考，不新增 SDK 稳定性承诺。精确 schema、必需参数、错误、权限和副作用以对应契约及实现为准。

## 选择集成接口

| 构建或检查 | 接口与契约 | 源码 |
|---|---|---|
| 类型化工作流声明 | `ModuleDeclaration`、`Registration`、`lower_module`；[声明](declarations_ZH.md) | [公共 facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/__init__.py)、[编译器](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/compiler.py) |
| 定义提取、组合或替换 | `extract_module`、`compose_modules`、`instantiate_module`、`branch_module`、替换/reentry 计划；[网操作](../guides/net-operations_ZH.md) | [定义变换](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/definitions.py)、[owner 操作](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/runtime.py) |
| Owner 执行与恢复 | `start_run`、`RunOwner`、`resume_run`、`OwnerInput`；[运行与 Registry](runtime-registry_ZH.md) | [Run owner](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/run.py)、[运行协议](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/petri_runtime_interfaces.py) |
| Agent 会话与 workflow 节点 | `MainSession`、`TaskControl`、`AgentStage`、`AgentTaskSpec`、`AgentWorkflowGraph`；[Agents](agents_ZH.md) | [会话](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/main_session.py)、[任务控制](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/task_control.py)、[任务定义](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_tasks.py)、[图](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_workflows.py) |
| 已安装的原生工具或指令资源 | `PluginDefinition`、`PluginOperation`、`PluginResource`、`PluginCatalog`；[插件 SDK](extensions-observation_ZH.md)、[教程](../guides/customization_ZH.md) | [插件 facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/__init__.py)、[声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/api.py)、[catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/catalog.py) |
| 按节点限定的托管工具、批次或程序 | Managed plugin catalog 与 invocation service；[托管工具](../controlled-managed-tools.zh.md) | [托管工具](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/managed_tools.py)、[调度器](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/managed_scheduler.py)、[程序执行](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/components/agent_loop/program_execution.py) |
| 已授权的独立 Registry 读取 | `ReadSessionRequest`、`RegistryReadHostBinding`、`open_registry_session`、`query_index`、`read_exact`、`read_material`；[读取契约](registry-read-sessions_ZH.md)、[reader 指南](../guides/independent-registry-reader_ZH.md) | [请求类型](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_contracts.py)、[会话](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_session.py) |
| 精确来源查询与生产者链路 | `SourceSetQuery`、限定引用、`query_product_origin_v1`；[SourceSets](../guides/source-queries_ZH.md)、[origin 查询](registry-read-sessions_ZH.md) | [来源查询](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/source_query.py)、[类型化投影](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/public_projections.py) |
| 作者版本、Branch、合并与 Assembly | `cpn.rpnh.collaboration` author 系列；[版本操作地图](#作者与版本契约) | [协作 facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/__init__.py) |
| 受控参与者与完整项目复用 | `UserCollaborationHost`、本地项目请求；[协作](../guides/user-collaboration_ZH.md)、[项目操作](../guides/project-entry_ZH.md) | [参与者 HOST](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/user_collaboration.py)、[项目端点](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_entry.py) |
| 定义交换与包准备 | Subnet plan/export/import；package preview/resolve；environment check/plan/prepare/launch | [交换](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/subnet_exchange.py)、[包指南](../guides/portable-packages_ZH.md)、[类型化交换](../guides/typed-registry-exchange_ZH.md)、[环境指南](../environment-preparation_ZH.md) |
| 有限 PN 分析与 witness | `build_analysis_input`、`analyze`、`explore`、报告验证；[PN 验证](pn-validation_ZH.md) | [分析 facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/__init__.py) |
| Viewer、checkpoint 和跨网比较 | `project_compiled_net`、只读投影 provider；[观察](extensions-observation_ZH.md)、[比较](../guides/comparison-context_ZH.md) | [投影](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/inspection.py)、[checkpoint 投影](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/checkpoint_view.py)、[比较上下文](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/comparison_context.py) |
| 前端或 LLM host adapter | 共用 session/application service 和注册 port；[适配器](../guides/adapters_ZH.md)、[历史](main-thread-history_ZH.md)、[Codex 历史](codex-history_ZH.md) | [应用服务](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/frontend_application.py)、[AgentLoop port](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/components/agent_loop/ports.py)、[注册 LLM 契约](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/llm_contracts.py) |

## 核心调用边界

| 入口 | 输入与结果 | 副作用 / 后续 |
|---|---|---|
| `ModuleDeclaration.from_dict(document)` / `.from_json(document)` | 严格声明数据 → `ModuleDeclaration` | 仅解析校验；`DeclarationError` 与字段约定见[声明](declarations_ZH.md) |
| `lower_module(module, registration)` | 实际声明 + 可信绑定 → `SymbolicNet` | 执行已注册可信 lowering callback，不发布 run |
| `compile_module(module, registration)` | 从 `cpn.rpnh.compiler` 显式导入 → `CompiledPetriNet` | 检查 lowering 候选；编译不等于 owner adoption 或执行 |
| `start_run(module, registration, **owner_arguments)` | 公共 facade 转发到显式类型化 [run 签名](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/run.py#L510) → `RunOwner` | 创建新的 Registry/owner lineage；需要 owner 输入、预算、身份、run 目录及 command ID；默认不执行业务 |
| `snapshot(owner_or_client)` | 调用现有 owner/client snapshot | 从该通道读取精确状态，不创建第二 writer |
| `apply_replacement(owner, plan, *, command_id)` | 精确 prepared replacement → 原有 owner edit 路径 | 可能 drain/adopt 后继；按[静止替换规则](../guides/net-operations_ZH.md)操作 |

使用[声明](declarations_ZH.md)中的完整纯解析示例，或可运行的[原生插件教程](../guides/customization_ZH.md)。猜测的空 Registration 不能绑定任意工作流。Registry 记录与 PN 结构共同管理身份、准入及结算，前端展示状态不是另一套执行权威。

业务 PN 建模 agent 工作和交接。下级执行 PN 在同一 Registry 中，隶属精确业务 invocation/firing，实现 harness 机制，并有独立 marking/checkpoint 证据。见[运行机制](runtime-registry_ZH.md)与[架构](../ARCHITECTURE_ZH.md)。

## 作者与版本契约

按已有材料选择契约；作者发布、受保护 Branch 接受、runtime adoption 是不同操作。

| 材料 / 操作 | 契约 |
|---|---|
| 限定来源引用、不可变 revision、closed Module、Branch、Assembly v1/v2 | [声明](declarations_ZH.md) |
| Graph source 编写与合并 | [Graph 编写](graph-authoring_ZH.md)、[graph-source 合并](graph-source-merge_ZH.md) |
| 普通 Module 冲突分析与解决结果 | [Plain merge 分析](plain-merge-analysis_ZH.md)、[合并结果](plain-merge-result_ZH.md) |
| 组合及嵌套 Assembly | [合并组合](plain-merge-composition_ZH.md)、[嵌套合并](plain-merge-nested-assembly_ZH.md)、[完整历史 Assembly 合并](assembly-full-history-merge_ZH.md) |
| Open-region 适配及普通后代 | [Open-region 编写](open-region-authoring_ZH.md)、[后代](open-region-descendants_ZH.md) |
| 选择性 transplant 与后代 | [Plain transplant](plain-transplant_ZH.md)、[transplant 后代](plain-transplant-descendants_ZH.md) |
| 显式 split/fusion 身份变换 | [身份变换](author-identity-transform-contract_ZH.md) |
| 有限类型化 Assembly v9 | [声明](declarations_ZH.md) |
| Workset 交付与 normal-child 闭合 | [Worksets](worksets_ZH.md)、[normal-child root 契约](normal-child-root-contract_ZH.md) |
| 固定切面查询、lease 与材料读取 | [读取会话](registry-read-sessions_ZH.md)、[静态 lease](../guides/static-lease-reads_ZH.md)、[类型化交换](../guides/typed-registry-exchange_ZH.md) |

`restore-content` 创建内容取自选定历史版本的后继；任务 checkpoint `reopen` 创建执行代际；包导入生成保留来源关系的本地身份。它们不同于 Git checkout 或改写 Registry 历史；各自指南规定了支持范围与所需权限。

## 前端与 HOST 协议

- Basic、Codex、OpenCode 使用规范 main-session/application 路径；[源码职责](../guides/repository-layout_ZH.md#前端源码路径)列出各实现。
- [Codex compatibility](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_compatibility.v1.json) 与 [OpenCode compatibility](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_compatibility.v1.json) 描述固定版本展示契约。[OpenCode HTTP](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_http.py) 与 [protocol](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_protocol.py) 实现该适配器，不是通用已认证公共服务器 API。
- DSH 使用 [Python backend](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/backend.py) 与[随包上层 app](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/integrations/dsh/src/app.ts)，显式选择 configured 或 offline 模式。见 [DSH 契约](extensions-observation_ZH.md)。
- 受控项目端点使用严格有界 JSON 请求字段、可信本地 route 身份及逐操作权限；其 API 契约是 [project-entry](../guides/project-entry_ZH.md)。
- [Codex 0.161 候选](../CODEX_0161_CANDIDATE_ZH.md)与默认固定版本 Codex 展示入口分开。适配模块存在不证明兼容所有上游版本。

## Python 导出符号

下列清单按 facade 及定义模块分组。名称静态读取自各 facade 的显式 `__all__` 与 lazy-export 声明，没有为生成清单导入 runtime 代码。模块链接打开本页基准源码中的精确定义；语义、必需字段和副作用见上文任务/契约表。未由 facade 导出的符号仍可能有上文已列出的显式模块导入路径。

### cpn.rpnh

[导出声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/__init__.py) · 49 个导出名称。

| 定义模块 | 导出符号 |
|---|---|
| [cpn.rpnh.registration](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registration.py) | `Registration`, `RegistrationError` |
| [cpn.rpnh.module](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/module.py) | `ModuleDeclaration`, `SymbolicNet` |
| [cpn.rpnh.control_ir](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/control_ir.py) | `ControlIR`, `ControlIRError`, `ControlIRCapabilityError` |
| [cpn.rpnh.control_eval](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/control_eval.py) | `EvalError`, `EvalResult`, `ReadSet`, `evaluate_expression` |
| [cpn.rpnh.petri_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/petri_contracts.py) | `DeclarationError`, `PNFragment` |
| [cpn.rpnh](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/__init__.py) | `lower_module`, `start_run`, `snapshot` |
| [cpn.rpnh.petri_primitives](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/petri_primitives.py) | `PetriStructureDelta`, `PetriStructureDeltaError`, `PetriStructureEdit`, `PetriStructureSnapshot`, `apply_petri_structure_delta`, `derive_petri_structure_delta`, `verify_petri_structure_delta` |
| [cpn.rpnh.marking](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/marking.py) | `PetriFiringResourceAccess`, `PetriMarkingDelta`, `PetriTokenEdit`, `apply_petri_marking_delta`, `derive_petri_marking_delta`, `verify_petri_marking_delta` |
| [cpn.rpnh.firing_resource_access](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/firing_resource_access.py) | `RegisteredPetriFiringResourceAccess`, `derive_registered_firing_resource_access`, `verify_registered_firing_resource_access_conflicts` |
| [cpn.rpnh.net_operations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/__init__.py) | `BranchResult`, `ComposeConnection`, `ComposePlan`, `ExtractPlan`, `ExtractResult`, `ReplacementPlan`, `ReentryPlan`, `WorkspaceBindingPlan`, `WorkspaceImportPlan`, `apply_replacement`, `branch_module`, `compose_modules`, `extract_module`, `instantiate_module`, `prepare_reentry`, `prepare_replacement`, `register_net_components` |

### cpn.rpnh.net_operations

[导出声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/__init__.py) · 17 个导出名称。

| 定义模块 | 导出符号 |
|---|---|
| [cpn.rpnh.net_operations.definitions](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/definitions.py) | `BranchResult`, `ComposeConnection`, `ComposePlan`, `ExtractPlan`, `ExtractResult`, `branch_module`, `compose_modules`, `extract_module`, `instantiate_module` |
| [cpn.rpnh.net_operations.runtime](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/runtime.py) | `ReplacementPlan`, `ReentryPlan`, `WorkspaceBindingPlan`, `WorkspaceImportPlan`, `apply_replacement`, `prepare_reentry`, `prepare_replacement` |
| [cpn.rpnh.net_operations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/__init__.py) | `register_net_components` |

### cpn.plugins

[导出声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/__init__.py) · 19 个导出名称。

| 定义模块 | 导出符号 |
|---|---|
| [cpn.plugins.api](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/api.py) | `API_VERSION`, `PluginContext`, `PluginDefinition`, `PluginError`, `PluginOperation`, `PluginResource`, `ResourceView` |
| [cpn.plugins.catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/catalog.py) | `BoundPlugin`, `PluginCatalog`, `load_catalog` |
| [cpn.plugins.managed_tools](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/managed_tools.py) | `ManagedPluginToolAdapter`, `ManagedPluginToolCatalog`, `ManagedPluginInvocationService`, `ManagedPluginInvocationConflict`, `ManagedPluginInvocationFailed`, `ManagedPluginInvocationReconciliationRequired`, `ManagedToolDeclaration`, `ManagedToolSelector`, `build_managed_plugin_tool_catalog` |

### cpn.rpnh.registry

[导出声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/__init__.py) · 7 个导出名称。

| 定义模块 | 导出符号 |
|---|---|
| [cpn.rpnh.registry.identities](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/identities.py) | `TypedId` |
| [cpn.rpnh.registry.models](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/models.py) | `VersionRef` |
| [cpn.rpnh.registry.observer_access](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/observer_access.py) | `ObserverReadScope`, `RegistryReadObserverContext`, `issue_observer_access`, `revoke_observer_access`, `observer_access_schema_data` |

### cpn.rpnh.collaboration

[导出声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/__init__.py) · 223 个导出名称。

| 定义模块 | 导出符号 |
|---|---|
| [cpn.rpnh.collaboration.assembly_v2](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v2.py) | `AssemblyAuthorV2`, `ValidatedAssemblyRevisionV2`, `AssemblyMemberV2`, `AssemblyRevisionV2` |
| [cpn.rpnh.collaboration.schema_catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/schema_catalog.py) | `graph_assembly_schema_data`, `collaboration_schema_data`, `authoring_schema_data`, `source_identity_schema_data`, `branch_schema_data`, `author_material_schema_data`, `assembly_schema_data`, `graph_author_schema_data`, `graph_branch_schema_data`, `plain_merge_schema_data`, `plain_merge_result_schema_data`, `plain_merge_assembly_schema_data`, `nested_assembly_schema_data`, `plain_merge_nested_assembly_schema_data`, `open_region_schema_data`, `open_region_derived_schema_data`, `open_region_derived_assembly_schema_data`, `open_region_closure_schema_data`, `open_region_assembly_schema_data`, `source_set_schema_data`, `source_observation_schema_data`, `workset_schema_data`, `normal_child_root_schema_data`, `plain_transplant_schema_data`, `plain_transplant_assembly_schema_data`, `plain_transform_schema_data`, `plain_transform_assembly_schema_data`, `plain_transplant_derived_schema_data`, `plain_transplant_derived_assembly_schema_data`, `assembly_merge_schema_data`, `graph_merge_schema_data`, `graph_merge_assembly_schema_data`, `typed_assembly_schema_data`, `candidate_schema_data`, `candidate_v2_schema_data`, `registry_read_schema_data` |
| [cpn.rpnh.collaboration.references](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/references.py) | `SourceQualifiedResourceRef`, `SourceQualifiedVersionRef` |
| [cpn.rpnh.collaboration.authoring](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/authoring.py) | `NetRevision`, `AuthorRevisionReadError`, `read_net_revision` |
| [cpn.rpnh.collaboration.sources](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/sources.py) | `LocalSourceIdentity`, `get_local_source_identity` |
| [cpn.rpnh.collaboration.branches](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/branches.py) | `BranchVersion`, `GraphBranchVersion`, `current_branch`, `read_branch_version`, `GraphMergeBranchVersion` |
| [cpn.rpnh.collaboration.materials](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/materials.py) | `ClosedModuleAuthor`, `ValidatedClosedRevision`, `validate_closed_revision` |
| [cpn.rpnh.collaboration.assemblies](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assemblies.py) | `AssemblyMember`, `AssemblyConnection`, `AssemblyCompletion`, `AssemblyRevision`, `AssemblyAuthor`, `ValidatedAssemblyRevision`, `read_assembly_revision`, `validate_assembly_revision` |
| [cpn.rpnh.collaboration.graph_authoring](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_authoring.py) | `GraphModuleAuthor`, `GraphNetRevision`, `ValidatedGraphRevision` |
| [cpn.rpnh.collaboration.graph_source](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_source.py) | `make_graph_source`, `make_graph_recipe`, `graph_source_elements`, `rebuild_graph_module` |
| [cpn.rpnh.collaboration.plain_merge](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_merge.py) | `PlainModuleMergeAnalyzer`, `ValidatedPlainMergeAnalysis`, `read_plain_merge_analysis` |
| [cpn.rpnh.collaboration.plain_merge_result](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_merge_result.py) | `PlainModuleMergeAuthor`, `ValidatedPlainMergeRevision` |
| [cpn.rpnh.collaboration._plain_merge_resolution](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/_plain_merge_resolution.py) | `UnresolvedPlainMerge` |
| [cpn.rpnh.collaboration.assembly_v3](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v3.py) | `AssemblyMemberV3`, `AssemblyRevisionV3`, `AssemblyAuthorV3`, `ValidatedAssemblyRevisionV3` |
| [cpn.rpnh.collaboration.open_region](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/open_region.py) | `OpenRegionAuthor`, `ValidatedOpenRegion`, `validate_open_revision` |
| [cpn.rpnh.collaboration.open_region_derived](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/open_region_derived.py) | `OpenRegionDerivedAuthor`, `ValidatedOpenDerivedRevision` |
| [cpn.rpnh.collaboration.assembly_v5](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v5.py) | `AssemblyMemberV5`, `AssemblyRevisionV5`, `AssemblyAuthorV5`, `ValidatedAssemblyRevisionV5` |
| [cpn.rpnh.collaboration.open_region_closure](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/open_region_closure.py) | `OpenRegionClosureAuthor`, `ValidatedAdaptedRevision` |
| [cpn.rpnh.collaboration.assembly_v4](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v4.py) | `AssemblyMemberV4`, `AssemblyRevisionV4`, `AssemblyAuthorV4`, `ValidatedAssemblyRevisionV4` |
| [cpn.rpnh.collaboration.source_sets](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/source_sets.py) | `SourceMember`, `SourceSetVersion`, `read_source_set`, `current_source_set` |
| [cpn.rpnh.collaboration.source_query](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/source_query.py) | `SourceSetQuery`, `RegistrySourceReader`, `SourceUnavailable`, `SourceNotEstablished`, `SourceObservationDraft` |
| [cpn.rpnh.collaboration.worksets](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/worksets.py) | `WorksetOwner`, `WorksetExpectation`, `ExportResult`, `AcceptDelivery`, `Contribute`, `CompleteWorkset`, `bind_local_workset_source`, `prepare_local_delivery`, `finish_local_delivery`, `read_record`, `CompleteWorksetNormalChildren` |
| [cpn.rpnh.collaboration.root_terminals](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/root_terminals.py) | `read_root_terminal` |
| [cpn.rpnh.collaboration.plain_transplant](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transplant.py) | `PlainModuleTransplantAnalyzer`, `ValidatedPlainTransplantAnalysis`, `read_plain_transplant_analysis` |
| [cpn.rpnh.collaboration.plain_transplant_result](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transplant_result.py) | `PlainModuleTransplantAuthor`, `ValidatedPlainTransplantRevision` |
| [cpn.rpnh.collaboration._plain_transplant_resolution](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/_plain_transplant_resolution.py) | `UnresolvedPlainTransplant` |
| [cpn.rpnh.collaboration.plain_transform](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transform.py) | `PlainModuleTransformAuthor`, `ValidatedPlainTransformRevision` |
| [cpn.rpnh.collaboration.plain_transplant_derived](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transplant_derived.py) | `PlainTransplantDerivedAuthor`, `ValidatedPlainTransplantDerivedRevision` |
| [cpn.rpnh.collaboration.assembly_v7](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v7.py) | `AssemblyMemberV7`, `AssemblyRevisionV7`, `AssemblyAuthorV7`, `ValidatedAssemblyRevisionV7` |
| [cpn.rpnh.collaboration.assembly_merge](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_merge.py) | `AssemblyMergeAnalyzer`, `ValidatedAssemblyMergeAnalysis`, `UnresolvedAssemblyMerge`, `read_assembly_merge_analysis` |
| [cpn.rpnh.collaboration.assembly_v6](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v6.py) | `AssemblyRevisionV6`, `AssemblyAuthorV6`, `AssemblyMergeAuthor`, `ValidatedAssemblyRevisionV6` |
| [cpn.rpnh.collaboration.graph_merge](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_merge.py) | `GraphMergeNetRevision`, `ValidatedGraphMergeRevision`, `GraphMergeAnalyzer`, `ValidatedGraphMergeAnalysis`, `read_graph_merge_analysis` |
| [cpn.rpnh.collaboration.graph_merge_author](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_merge_author.py) | `GraphMergeAuthor` |
| [cpn.rpnh.collaboration._graph_merge_model](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/_graph_merge_model.py) | `UnresolvedGraphMerge` |
| [cpn.rpnh.collaboration.assembly_v8](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v8.py) | `AssemblyAuthorV8`, `AssemblyMemberV8`, `AssemblyRevisionV8`, `ValidatedAssemblyRevisionV8` |
| [cpn.rpnh.collaboration.assembly_v9](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v9.py) | `AssemblyMemberV9`, `AssemblyRevisionV9`, `AssemblyAuthorV9`, `ValidatedAssemblyRevisionV9`, `validate_generated_assembly_v9` |
| [cpn.rpnh.collaboration.environment_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_contracts.py) | `EnvironmentContractError`, `PackageTarget` |
| [cpn.rpnh.collaboration.environment_requirements](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_requirements.py) | `EnvironmentRequirements`, `ScopedEnvironmentRequirements`, `PackageEnvironment`, `EnvironmentRequirementsSet`, `read_environment_requirements`, `read_package_environment` |
| [cpn.rpnh.collaboration.environment_local_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_local_contracts.py) | `EnvironmentSelection`, `LocalEnvironmentBinding`, `EnvironmentResolutionLock`, `EnvironmentCheckReport`, `EnvironmentPreparationPlan`, `PreparationReceipt` |
| [cpn.rpnh.collaboration.environment_check](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_check.py) | `ProbePolicy`, `check_environment` |
| [cpn.rpnh.collaboration.environment_plan](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_plan.py) | `ConcreteSelections`, `resolve_local_wheels`, `plan_environment` |
| [cpn.rpnh.collaboration.environment_prepare](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_prepare.py) | `PreparationExecutionContext`, `PreparedEnvironmentResult`, `LaunchExecutionContext`, `prepare_environment` |
| [cpn.rpnh.collaboration.environment_host](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_host.py) | `HostProfile`, `ExistingRunHandle`, `launch_package` |
| [cpn.rpnh.collaboration.environment_setup](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_setup.py) | `render_environment_setup` |
| [cpn.rpnh.collaboration.registry_read_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_contracts.py) | `RegistryReadSessionError`, `ReadLimits`, `ReadLimitOverrides`, `SourceSelection`, `ExplicitSources`, `SelectedSourceSet`, `ReadSessionRequest`, `PublicRegistryHead`, `SourceCut`, `HistoricalCutRequest`, `TypedPredicate`, `TypedIndexClause`, `IndexQuery`, `PRODUCT_ORIGIN_PROFILE`, `PRODUCT_ORIGIN_PAGE_SCHEMA`, `PRODUCT_ORIGIN_CONTRACT_REVISION` |
| [cpn.rpnh.collaboration.registry_read_session](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_session.py) | `RegistryReadHostBinding`, `RegistryReadSession`, `ExistingReadAuthorityProvider`, `ResolvedReadSource`, `open_readonly_source`, `open_registry_session`, `query_index`, `query_product_origin_v1`, `read_exact`, `read_material` |
| [cpn.rpnh.collaboration.registry_typed_readers](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_typed_readers.py) | `TypedReaderCatalog`, `TypedReadError`, `DEFAULT_TYPED_READER_CATALOG` |
| [cpn.rpnh.collaboration.subnet_exchange](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/subnet_exchange.py) | `SubnetExchangeError`, `SubnetExportPlan`, `SubnetExportResult`, `SubnetImportPlan`, `SubnetImportResult`, `TargetRegistrySelection`, `plan_subnet_export`, `export_subnet`, `plan_subnet_import`, `import_subnet` |
| [cpn.rpnh.collaboration.read_host_config](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/read_host_config.py) | `ReadHostConfiguration`, `load_read_host_config`, `open_read_host_session` |

### cpn.rpnh.pn_validation

[导出声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/__init__.py) · 35 个导出名称。

| 定义模块 | 导出符号 |
|---|---|
| [cpn.rpnh.pn_validation.contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/contracts.py) | `ActionBinding`, `ActionEvidence`, `ActiveOccurrence`, `AnalysisInput`, `AnalysisPolicy`, `AnalysisState`, `BindingEnumeration`, `EnvironmentContract`, `OperationCase`, `OperationModel`, `ProducedSpec`, `PropertyResult`, `SchedulerContract`, `SupportResult`, `TerminalContract`, `canonical_data`, `canonical_json`, `state_key` |
| [cpn.rpnh.pn_validation.evidence](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/evidence.py) | `AnalysisReport`, `ReplayResult`, `analyze`, `input_digest`, `replay_witness`, `verify_graph`, `verify_report_binding`, `verify_report` |
| [cpn.rpnh.pn_validation.explorer](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/explorer.py) | `ExplorationGraph`, `Frontier`, `GraphEdge`, `GraphNode`, `explore` |
| [cpn.rpnh.pn_validation.properties](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/properties.py) | `TerminalClassification`, `classify_terminal`, `evaluate_properties` |
| [cpn.rpnh.pn_validation.projection](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/projection.py) | `build_analysis_input` |

## 显式模块接口与实现内部

`UserCollaborationHost` 的明确实现位置是 `cpn.rpnh.collaboration.user_collaboration`；它也由协作 facade 导入，但未列入该 facade 的 `__all__`。完整项目、分享包、HOST readiness、AgentLoop port、runtime owner 与 Viewer API 应按上文明确链接的模块/契约使用。不要把所有非下划线文件都当作稳定 SDK。

[LLM adapter facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/llm_adapters/__init__.py) 明确属于 adapter-private 组合层；执行选择和 transport 工厂供可信 adapter 集成，不能绕过注册请求路径。Registry 的 `_event_store`、`_invocation`、`_provider_calls` 等私有子模块负责验证和事务，不是插件直接写入入口。参见[扩展契约](extensions-observation_ZH.md)及[源码地图](../guides/repository-layout_ZH.md)。

Schema 版本属于数据契约，不等于 Python 包版本。精确引用和 schema 不匹配时按[排障](../guides/troubleshooting_ZH.md)保留证据；不要通过修改版本字符串强行兼容。变更记录见 [Changelog](../../CHANGELOG_ZH.md)，贡献和测试见[开发](../guides/development_ZH.md)。
