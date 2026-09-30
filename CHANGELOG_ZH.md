---
name: rpnh-changelog
description: "记录 RPNH 发布中的用户可见变更。"
metadata:
  document-kind: project-record
  audience: user-and-developer
  language: zh-CN
  counterpart: CHANGELOG.md
  revision: "2026-10-01.1"
  status: v0.1.0rc1
---

[English](CHANGELOG.md) | [中文](CHANGELOG_ZH.md)

# 变更记录

## 未发布

- Agent task 与 workflow 现在可以按 node 或 stage，把显式登记的原生插件 operation
  绑定到模型可见工具名。绑定会明确准入 effect、保留逐调用回执与结构化结果；未配置
  此功能时，旧任务和普通 action 记录保持原有行为。
- native 与 managed plugin worker 现在会收到由 harness 所有的逐调用
  `PluginContext.call_id`，使外部效果桥能够区分对同一 operation 的两次合法调用与重放。
- 用户自有 provider catalog 现可为精确模型声明可选 reasoning effort。Basic、DSH、
  Codex 与 OpenCode 使用同一份固定的 model/effort selection；生成的 local/external
  adapter 会把所选值传到物理请求，同时不在 RPNH 内置供应商模型对照表。
- 新增可复现的 JB 临床 packet 与 3-DOF 动力下降案例。两者都使用用户选择的精确
  profile，由主 agent 自行设计图，提供独立业务验证和真实验收 PetriNet 图，同时不发布
  原始 Registry 或私有输入。
- 新增独立 checkpoint 恢复流程，用于从最近 owner-stopped 切面或用户选择的较早
  checkpoint 继续同一个 child Registry。
- dashboard 捕获现在验证当前 authority 引用的 terminal/final-result 对，也支持含有早期
  execution generation 的 append-only Registry。

## 0.1.0rc1 — 2026-09-29

统一 RPNH 仓库的首个公开预发布版本。

### 包含内容

- 由 Registry 管理的执行机制，以及类型化 PetriNet 声明、marking、checkpoint 与终态证据。
- 对话式主会话，以及相互独立的子 task/workflow Registry。
- 用户选择 checkpoint 的 reopen、owner-stop resume，以及不改写既有 Registry 证据的对话压缩。
- 版本化 workspace 发布、逐路径变更历史和下级文件执行网。
- 用户自有 provider/model catalog、共享 external/local provider adapter，以及有界同 route
  transport recovery。
- Basic、Codex、OpenCode 展示前端和可选 DSH 宿主。
- 原生插件、串行／并行／文档／长流程案例，以及支持历史 checkpoint 导航的只读 PetriNet 看板。

### 发布边界

- 支持 Linux 与 WSL2；不支持原生 Windows 和 macOS。
- 不内置 provider 或 exact-model route；真实访问取决于用户自己的配置和授权。
- Codex、OpenCode 与 DSH 集成绑定特定版本，安装前应阅读对应指南。
- 这是预发布版。公开声明契约有文档说明，但私有实现模块不是稳定 SDK。

验证范围与已知限制见[发布验证](docs/guides/release-validation_ZH.md)和
[案例验证](docs/guides/examples-validation_ZH.md)。
