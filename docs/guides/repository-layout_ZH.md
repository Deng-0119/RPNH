---
name: rpnh-repository-layout
description: "定位用户入口、案例、viewer 源码、宿主集成和验证代码。"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: zh-CN
  counterpart: repository-layout.md
  revision: "2026-09-29.2"
  status: source-reviewed-pre-release
---

[English](repository-layout.md) | [中文](repository-layout_ZH.md)

# 仓库目录图

仓库将执行权威、展示适配器、案例和文档分开。用户从已安装的 `rpnh` 命令开始；下面的源码
路径用于说明职责，不是额外运行入口。

| 路径 | 用途 | 适用场景 |
|---|---|---|
| `cpn/rpnh/` | PetriNet、owner、任务、canonical session 与前端中立应用服务 | 修改核心执行语义 |
| `cpn/rpnh/registry/` | Append-only event/object 权威、结构校验、索引读取、执行网与恢复 | 修改持久化、结算或 checkpoint 行为 |
| `cpn/rpnh/{workspace_settlement,file_execution_net}.py` | 版本化 workspace 发布与预定义文件执行机制 | 修改文件版本／证据行为，而非业务 workflow |
| `cpn/llm_adapters/` | 共用 local-process 与 external-provider input port | 添加不依赖宿主的 transport 行为 |
| `cpn/components/` | 可复用 AgentLoop 与执行服务组件 | 扩展受管 Agent 行为 |
| `cpn/frontend/static/` | Python wheel 实际携带的 viewer 资源 | 查看运行时看板实现 |
| `frontend/net-viewer/` | viewer 的 lockfile、构建脚本和 JavaScript 测试 | 重建或测试看板 |
| `examples/` | 源码安装案例、输入和任务模式案例库 | 通过本地运行学习 |
| `cpn/examples/` | 安装版 `rpnh` 导出的 provider-neutral 案例包 | 测试 Basic、Codex、DSH 或 OpenCode |
| `integrations/` | DSH 等可选宿主适配器 | 修改固定宿主边界 |
| `docs/` | 双语指南、架构和参考 | 查询支持行为 |
| `scripts/` | 聚焦构建、文档和验证工具 | 维护仓库 |
| `tests/` | 确定性离线验收与回归 | 验证修改 |

生成的 profile、provider transcript、Registry 数据库、run 目录、虚拟环境和 viewer 依赖不应
进入仓库。案例把 run 写入调用者选择的源码树外目录。可以提交脱敏验证摘要，不能提交原始
运行证据。

Basic、Codex 与 OpenCode 顺序打开同一个 canonical MainSession root，不是不同源码变体。
DSH 是同一发行包内的可选 registered host，并使用共享 provider 层。历史分支只用于来源
记录，不是需要覆盖安装到 `main` 的组件。

从[案例目录](examples_ZH.md)选择任务，按[看板教程](viewer_ZH.md)查看 PetriNet。修改所有权
或执行边界前先阅读[架构指南](../architecture/design_ZH.md)。
