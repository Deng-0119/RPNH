---
name: rpnh-documentation-index
description: "Navigate the bilingual operator and developer documentation."
metadata:
  document-kind: index
  audience: operator-and-developer
  language: zh-CN
  counterpart: index.md
  revision: "2026-09-26.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](index.md) | [中文](index_ZH.md)

# 文档导航

先读安装和精确模型配置。这套文档按实际源码核对，不代表最终共同候选或每个可选宿主均已测试。每个主题都有对应英文页。

| 需求 | 阅读 |
|---|---|
| 安装 core/basic、源码或 wheel | [安装](guides/installation_ZH.md) |
| 运行原生、混合、任务与安装版跨宿主案例 | [案例](guides/examples_ZH.md) |
| 配置 route、模型与全部受支持运行上限 | [配置总表](guides/configuration_ZH.md)、[模型配置](guides/models_ZH.md) |
| 控制会话/任务并恢复 | [使用](guides/usage_ZH.md) |
| 不破坏证据地定位故障 | [排障](guides/troubleshooting_ZH.md) |
| 扩展 workflow/tool/skill/MCP | [自定义](guides/customization_ZH.md) |
| 提取、组合、实例化或替换 PetriNet 定义 | [原生 PetriNet 操作](guides/net-operations_ZH.md) |
| 选用 Codex/DSH、理解共存 | [适配](guides/adapters_ZH.md) |
| 使用固定版本 OpenCode 展示前端 | [OpenCode 前端](guides/opencode_ZH.md) |
| 使用固定版本 DSH 宿主 | [DSH 宿主适配器](guides/dsh_ZH.md) |
| 在 PetriNet 看板中查看 run | [PetriNet 看板](guides/viewer_ZH.md) |
| 理解执行闭环 | [架构](architecture/design_ZH.md) |
| 阅读完整 harness 架构概览 | [Harness 架构](ARCHITECTURE_ZH.md) |
| 比较 RPNH 权威与 Codex 展示 | [RPNH 与 Codex](RPNH_VS_CODEX_ZH.md) |
| 查看 provider catalog 详细格式 | [Provider/model 配置](PROVIDER_MODEL_CONFIGURATION_ZH.md) |
| 核对 viewer 证据边界 | [展示观察](DISPLAY_OBSERVATION_ZH.md) |
| 审计源码整合与排除项 | [源码来源](PROVENANCE_ZH.md) |
| 查看非阻塞后续事项 | [延后工程事项](DEFERRED_ENGINEERING_WORK_ZH.md) |
| 查询声明与编译契约 | [声明参考](reference/declarations_ZH.md) |
| 理解 runtime/owner/原子记录 | [运行与 Registry](reference/runtime-registry_ZH.md) |
| 定位会话/AgentLoop/模型边界 | [Agent 与模型](reference/agents_ZH.md) |
| 查询插件/适配/观察接口 | [扩展与观察](reference/extensions-observation_ZH.md) |
| 测试、贡献及发布准备 | [维护与许可状态](guides/development_ZH.md) |
| 查看当前离线验收边界 | [发布验收](guides/release-validation_ZH.md) |
| 查看案例 focused 验收 | [案例验证](guides/examples-validation_ZH.md) |

## 如何阅读参考
明确区分声明契约、高级可信宿主接口与私有模块。各页源码路径为仓库内定位信息，不要求公开读者访问私有仓库。可选 API 按能力标注，必须与所选源码/产物核对；自动生成页面不授予 SDK 稳定性承诺。

## 验证状态

当前代码树统一包含 core、原生插件、Codex/OpenCode 展示、DSH 和只读 viewer。离线检查只能证明
确定性行为和打包完整性，不能证明用户自有 provider route 可用。原始真实 API 证据保存在
公开源码树之外并需要单独授权；不含秘密的脱敏验收摘要可以随对应案例发布。
