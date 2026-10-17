---
name: rpnh-repository-layout
description: "定位安装入口、共用权威、前端源码路径及开发边界。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: repository-layout.md
  revision: "2026-10-13.3"
  status: source-reviewed
  basis: "public source b16ecce83ba603b3c15f1c770bce172d4f44f88e; not a new runtime validation"
---

[English](repository-layout.md) | [中文](repository-layout_ZH.md)

# 仓库目录图

[文档首页](../index_ZH.md) · [API 地图](../reference/api-index_ZH.md) · [开发](development_ZH.md)

从用户操作沿入口找到实际负责的源码。下列链接固定到 `b16ecce8`；后续变化见 [Changelog](../../CHANGELOG_ZH.md)。源码路径说明职责，不是额外安装步骤。

## 从操作开始定位

| 操作 | 先读源码 | 下一层 / 契约 |
|---|---|---|
| 启动 `rpnh`、配置、选择前端 | [cpn/rpnh_cli.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py) | [user_config.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/user_config.py)、[onboarding.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/onboarding.py)、[配置](configuration_ZH.md) |
| 提交 main 输入或控制子任务 | [main_session.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/main_session.py)、[task_control.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/task_control.py) | [frontend_application.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/frontend_application.py)、[会话](usage_ZH.md) |
| 声明与 lowering agent 工作 | [module.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/module.py)、[registration.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registration.py)、[compiler.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/compiler.py) | [agent_tasks.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_tasks.py)、[agent_workflows.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_workflows.py)、[声明](../reference/declarations_ZH.md) |
| 准入、dispatch 和结算 run | [run.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/run.py)、[harness.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/harness.py) | Registry owner/transaction service；[运行契约](../reference/runtime-registry_ZH.md) |
| 新增原生工具 | [plugins/api.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/api.py)、[catalog.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/catalog.py)、[runtime.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/runtime.py) | [定制](customization_ZH.md)、[插件契约](../reference/extensions-observation_ZH.md) |
| 查询授权来源或交换定义 | [registry_read_session.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_session.py)、[subnet_exchange.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/subnet_exchange.py) | [独立 reader](independent-registry-reader_ZH.md)、[类型化交换](typed-registry-exchange_ZH.md) |
| 共创、adopt 或下载项目 | [project_local.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_local.py)、[project_entry.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_entry.py) | [项目操作](project-entry_ZH.md)、[版本/API 地图](../reference/api-index_ZH.md) |
| 导出随包示例 | [examples/cli.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/cli.py)、[gallery.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/gallery.py)、[catalog.json](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/catalog.json) | [已安装导出](examples_ZH.md) |

## 核心与包职责

| 路径 | 职责 |
|---|---|
| `cpn/rpnh/` | 共用声明、PN 结构、owner、task、session 及前端中立应用服务 |
| `cpn/rpnh/registry/` | Append-only 对象/事件权威、结构验证、精确读取、结算、下级执行网与恢复 |
| `cpn/rpnh/collaboration/` | 按需启用的作者/版本/Branch/merge、类型化读取、子网/包及受控项目契约 |
| `cpn/rpnh/net_operations/` | 定义变换及显式 owner adoption/reentry 计划 |
| `cpn/rpnh/pn_validation/` | 受支持有限分析、witness 及报告验证 |
| `cpn/components/` | 可复用 lowering/执行组件，包括可选 AgentLoop 和注册服务 |
| `cpn/plugins/` | 原生插件 SDK/catalog/runtime，显式选择的托管工具/程序 |
| `cpn/llm_adapters/` | 供可信适配器使用的共用 provider/local-process transport 组合 |
| `cpn/schemas/`、`cpn/config/` | 随包 schema 与配置材料；用户自有 profile 保留在版本控制之外 |

Registry 和 PN 是共用核心。业务 PN 表达 agent 操作及交接。[file_execution_net.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/file_execution_net.py)、[workspace_settlement.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/workspace_settlement.py) 及 Registry 执行服务，在同一 Registry 中隶属精确业务 firing，承载下级文件/workspace 机制；它们不会把领域算法或 AgentLoop transcript 变成执行 PN。

修改权威或结算前，先读[架构](../ARCHITECTURE_ZH.md)与[封闭执行设计](../architecture/design_ZH.md)。Registry `_event_store`、`_invocation` 等下划线实现包不是扩展入口；[API 参考](../reference/api-index_ZH.md)区分导出 facade 与可信 HOST 接口。

## 前端源码路径

| 界面 | Python / host 实现 | 展示资产与协议 | 操作指南 |
|---|---|---|---|
| Basic | [rpnh_cli.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py)、[main_session.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/main_session.py) | CLI 内的终端输入与任务控制 | [使用](usage_ZH.md) |
| Codex/OpenCode 共用应用 | [frontend_application.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/frontend_application.py)、[session_access.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/session_access.py) | 规范会话 ownership 及前端中立命令服务 | [Agents](../reference/agents_ZH.md) |
| Codex | [codex_app_server.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_app_server.py)、[codex_history.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_history.py) | [codex_compatibility.v1.json](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_compatibility.v1.json)；固定外部客户端 | [适配器](adapters_ZH.md)、[历史](../reference/codex-history_ZH.md) |
| OpenCode | [opencode_launcher.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_launcher.py)、[opencode_http.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_http.py)、[opencode_protocol.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_protocol.py) | [opencode_compatibility.v1.json](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_compatibility.v1.json)；固定外部客户端 | [OpenCode](opencode_ZH.md) |
| DSH | [cpn/dsh/launcher.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/launcher.py)、[backend.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/backend.py) | [integrations/dsh/src/app.ts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/integrations/dsh/src/app.ts) 及随包 bridge | [DSH](dsh_ZH.md) |
| 只读 Viewer | [frontend/server.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/server.py)、[dashboard.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/dashboard.py)、[comparison_context.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/comparison_context.py) | [cpn/frontend/static/app.js](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/static/app.js)、[index.html](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/static/index.html)、[style.css](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/static/style.css) 及相邻 `.mjs` 模块 | [Viewer](viewer_ZH.md)、[比较](comparison-context_ZH.md) |

`cpn/frontend/static/` 保存 wheel 随附浏览器资产。`frontend/net-viewer/` 保存现有资产构建脚本、lockfile 与 JavaScript 检查，不是另一套 runtime UI 源码。其[构建脚本](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/frontend/net-viewer/build.mjs)准备 vendor 资产。

Basic、Codex、OpenCode 顺序打开同一规范 MainSession 根目录，共用 owner lease 拒绝并发可写前端。DSH 有独立的注册 HOST adapter，使用共用 provider 层。Viewer 是只读展示。历史分支用于来源追溯，不是叠加到 `main` 的安装部件。

## 示例、文档与维护

| 位置 | 用途 / 入口 |
|---|---|
| `examples/` | 源码 checkout 示例、fixture 和实际 Viewer 图；[完整目录](../../examples/README_ZH.md) |
| `cpn/examples/` | 随包 provider-neutral 材料和命名导出机制；[导出指南](examples_ZH.md) |
| `docs/guides/` | 按任务组织的安装、配置、操作与扩展说明 |
| `docs/reference/` | 精确声明、运行、API、查询和版本操作契约 |
| `docs/architecture/`、`docs/ARCHITECTURE.md` | 执行/ownership 设计说明 |
| `docs/results/`、`docs/validation/` | 绑定精确来源与范围的有日期审核证据；[结果目录](../results/README_ZH.md) |
| `scripts/`、`tools/`、`tests/` | 已有文档、打包及确定性验证辅助工具；[开发指南](development_ZH.md) |
| 根目录规范 | [贡献](../../CONTRIBUTING_ZH.md)、[安全](../../SECURITY_ZH.md)、[Changelog](../../CHANGELOG_ZH.md)、[许可证](../../LICENSE)、[第三方声明](../../THIRD_PARTY_NOTICES.md) |

生成的 profile、凭据、原始 provider transcript、Registry 数据库、运行目录、环境及构建依赖均留在公共仓库之外。运行输出写入调用方选定的私有目录。公开结果摘要保留其日期和验证范围。文档链接检查不是 runtime 或真实模型测试。

## 多用户发布交接：独立智能体与共享资源

[完整示例](../../examples/multiuser_release_handoff/README_ZH.md)展示四个受控参与者的独立 Task/Registry/私有智能体 PN，以及独立第五个 Source Registry 的资源控制 PN。原 episode 经原生检查点恢复完成 v2、SQL、QA 检查表、事件和最终交接；最终只读审计 98/98 必需检查通过。Source 不是智能体，原始故障和两项可选历史覆盖 UNKNOWN 保留。

[实际角色 PN 与 Source 详情](../../examples/multiuser_release_handoff/figures/README_ZH.md) · [原任务及运行依赖](../../examples/multiuser_release_handoff/RUNNING_ZH.md) · [精确历史结果与边界](../../examples/multiuser_release_handoff/RESULTS_ZH.md)。新模型运行需授权；这不是官方基准分数、部署或实际回滚验证。
