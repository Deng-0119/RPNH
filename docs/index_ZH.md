---
name: rpnh-documentation-index
description: "按首次运行、操作、API 契约或源码入口选择阅读路线。"
metadata:
  document-kind: index
  audience: operator-and-developer
  language: zh-CN
  counterpart: index.md
  revision: "2026-10-13.3"
  status: source-reviewed
  basis: "public source b16ecce83ba603b3c15f1c770bce172d4f44f88e; not a new runtime validation"
---

[English](index.md) | [中文](index_ZH.md)

# 文档导航

[仓库首页](../README_ZH.md) · [示例](../examples/README_ZH.md) · [CLI](reference/cli_ZH.md) · [API](reference/api-index_ZH.md) · [源码地图](guides/repository-layout_ZH.md)

RPNH 以 Registry 和 PetriNet 共同声明、执行、检查和复用 agent 工作。先完成一个小运行，再按下一项任务选指南。文档描述所选公共源码；有日期的结果记录明确其实际测试过的旧版本。

## 第一次使用 RPNH？

1. [第一次运行](guides/getting-started_ZH.md)：安装一份来源，运行无模型原生操作、检查实际证据，再按需打开模型会话。
2. [选择示例](../examples/README_ZH.md)：按任务和前提找到可修改工作流、完整协作项目或有界 benchmark。
3. [操作与恢复](guides/usage_ZH.md)：保留会话/任务身份、查看结果、安全停止，再从精确 checkpoint 继续。

运行前想先理解概念，阅读[技术报告](technical-report_ZH.md)。接入 Python 则从 [API 地图](reference/api-index_ZH.md)及[声明教程](reference/declarations_ZH.md)开始。

## 安装与配置

| 需要 | 指南 |
|---|---|
| 安装认可的源码或 wheel；选择平台与产物 | [安装及发行范围](guides/installation_ZH.md) |
| 设置 provider、精确模型及凭据环境变量 | [模型设置](guides/models_ZH.md)、[provider catalog 字段](PROVIDER_MODEL_CONFIGURATION_ZH.md) |
| 理解 profile 优先级、限制和 runtime policy | [配置参考](guides/configuration_ZH.md) |
| 选择 Basic、Codex 或其他可选 host | [适配器及共存](guides/adapters_ZH.md)、[OpenCode](guides/opencode_ZH.md)、[DSH](guides/dsh_ZH.md) |
| 为依赖完整的包准备接收端 | [包环境](guides/package-environments_ZH.md)、[检查/计划/准备流程](environment-preparation_ZH.md) |

## 运行、检查与恢复

| 需要 | 指南 |
|---|---|
| 启动会话、创建任务、向 workflow 目标发送消息 | [使用](guides/usage_ZH.md)、[CLI 参考](reference/cli_ZH.md) |
| 继续同一任务或选择旧 checkpoint | [Checkpoint 恢复](guides/checkpoint-recovery_ZH.md) |
| 排查配置缺失、owner 冲突或未知副作用 | [排障与安全恢复](guides/troubleshooting_ZH.md) |
| 查看完整 PN、agent Overview、firing、资源或 checkpoint | [Viewer](guides/viewer_ZH.md)、[展示/证据语义](DISPLAY_OBSERVATION_ZH.md) |
| 比较同一 Registry 的 checkpoint 或跨来源精确网 | [Checkpoint 比较](guides/checkpoint-comparison_ZH.md)、[跨网比较](guides/comparison-context_ZH.md) |
| 查看实际受控用户贡献 | [Viewer 贡献](guides/viewer-user-contributions_ZH.md) |
| 对已准备 HOST 检查声明兼容性 | [HOST readiness](guides/host-readiness_ZH.md) |

## 构建、协作与复用

| 需要 | 指南 |
|---|---|
| 修改随附输入、图或插件 | [示例及复制修改流程](guides/examples_ZH.md)、[定制](guides/customization_ZH.md) |
| 复制、共创、合并、adopt 并独立运行完整 agent 项目 | [完整项目示例](../examples/project_collaboration/README_ZH.md)、[项目操作/API 指南](guides/project-entry_ZH.md) |
| 理解受控参与者、范围权限和受保护接受 | [用户协作](guides/user-collaboration_ZH.md) |
| 提取、组合、实例化、branch 或替换定义 | [原生网操作](guides/net-operations_ZH.md) |
| 读取公共投影并交换完整封闭定义 | [类型化 Registry 交换](guides/typed-registry-exchange_ZH.md) |
| 预览/解析可移植包，在准备好的接收端运行 | [可移植包](guides/portable-packages_ZH.md)、[native-add 包教程](guides/package-reuse-example_ZH.md) |
| 接入所选工具、并行托管调用或隔离程序 | [受控托管工具](controlled-managed-tools.zh.md)、[插件契约](reference/extensions-observation_ZH.md) |

当前完整项目入口让 implementer/reviewer agent 经历受控本地协作、显式冲突解决、接受、owner adoption 及独立接收端执行。示例页保留精确证据、限制和原任务验证。历史[agent-network 共建](../examples/user_collaboration/agent_cobuild/README_ZH.md)是单独的确定性协议示例。

## 查询与理解系统

| 需要 | 指南 |
|---|---|
| 打开独立授权的类型化 reader | [独立 Registry reader](guides/independent-registry-reader_ZH.md) |
| 查询固定来源范围及精确引用 | [SourceSet 查询](guides/source-queries_ZH.md)、[Registry 读取会话](reference/registry-read-sessions_ZH.md) |
| 从精确 product 追踪已核验 producer | [有界 product-origin 查询](reference/registry-read-sessions_ZH.md) |
| 理解 Registry 与双层 PN | [Harness 架构](ARCHITECTURE_ZH.md)、[封闭执行设计](architecture/design_ZH.md) |
| 定位声明、runtime、AgentLoop 及扩展契约 | [API 参考地图](reference/api-index_ZH.md)、[源码地图](guides/repository-layout_ZH.md) |
| 分析受支持有限 PN，解读 HOLDS / VIOLATED / UNKNOWN | [有限 PN 验证](reference/pn-validation_ZH.md) |
| 比较 RPNH 权威与展示集成 | [RPNH 与 Codex](RPNH_VS_CODEX_ZH.md)、[源码来源](PROVENANCE_ZH.md) |

## 维护、贡献与版本

- [开发](guides/development_ZH.md)：确定性检查、包验证和贡献流程
- [Changelog](../CHANGELOG_ZH.md)：按版本/来源查看变化；[安装](guides/installation_ZH.md)解释源码候选与旧发布的区别
- [贡献](../CONTRIBUTING_ZH.md)、[安全](../SECURITY_ZH.md)、[许可证](../LICENSE)及[第三方声明](../THIRD_PARTY_NOTICES.md)
- [发布验证](guides/release-validation_ZH.md)、[示例验证](guides/examples-validation_ZH.md)、[精选结果](results/README_ZH.md)：有日期的证据、测试输入及未运行范围

## 如何阅读参考

[CLI 参考](reference/cli_ZH.md)定位操作命令，[API 参考](reference/api-index_ZH.md)定位导出符号、显式模块接口、协议入口和源码定义。[声明](reference/declarations_ZH.md)、[运行/Registry](reference/runtime-registry_ZH.md)、[agent/模型](reference/agents_ZH.md)、[扩展/观察](reference/extensions-observation_ZH.md)详述参数、身份、副作用及失败边界。

公共 facade、可信 HOST 接口和私有实现模块应区分。已记录的导入路径或 schema 不产生执行权限，也不承诺兼容所有后续版本。请使用与已安装产物匹配的源码版本。

## 已集成的显式合同

高级作者操作汇总在 [API 版本操作地图](reference/api-index_ZH.md#作者与版本契约)：

- [Graph 编写](reference/graph-authoring_ZH.md)、[graph-source 合并](reference/graph-source-merge_ZH.md)、[身份变换](reference/author-identity-transform-contract_ZH.md)
- [Plain merge 分析](reference/plain-merge-analysis_ZH.md)、[解决结果](reference/plain-merge-result_ZH.md)、[组合](reference/plain-merge-composition_ZH.md)、[嵌套 Assembly](reference/plain-merge-nested-assembly_ZH.md)、[完整历史 Assembly 合并](reference/assembly-full-history-merge_ZH.md)
- [Open-region 编写](reference/open-region-authoring_ZH.md)、[open-region 后代](reference/open-region-descendants_ZH.md)、[选择性 transplant](reference/plain-transplant_ZH.md)、[transplant 后代](reference/plain-transplant-descendants_ZH.md)
- [Worksets](reference/worksets_ZH.md)、[normal-child root 闭合](reference/normal-child-root-contract_ZH.md)

## 精确读取与前端协议

- [静态 resource-lease 读取](guides/static-lease-reads_ZH.md)
- [固定切面的主线程历史](reference/main-thread-history_ZH.md)
- [Codex 历史投影](reference/codex-history_ZH.md)
- [显式 Codex 0.161 协议候选](CODEX_0161_CANDIDATE_ZH.md)

## 验证状态

文档/源码核对证明所声明内容与链接。离线 runtime 检查、已安装产物检查、浏览器检查和真实 provider 运行分别证明不同事情。首次运行命令是操作说明，不代表此次文档修订重新执行过它们。

复用结果前，先核对精确 commit、输入、环境、scorer、失败及未运行范围。图可读不证明终态成功，历史通过不证明后续源码树已通过。原始运行数据和凭据保持私有。

## 当前有限验收与公开结果

[发布验证](guides/release-validation_ZH.md) · [示例验证](guides/examples-validation_ZH.md) · [结果归档](results/README_ZH.md) · [AutomationBench first18 + repair4](../examples/automationbench/PUBLIC_RESULTS_20261006.md)

Native-input 应用示例见[受控模拟退款](../examples/approval_workflow/README_ZH.md)和[合成外部结果报价](../examples/a2a_quote/README_ZH.md)，各自保留明确的证据范围与外部协议状态。
