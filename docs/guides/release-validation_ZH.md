---
name: rpnh-release-validation
description: "记录统一公开候选的脱敏离线验收边界。"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: release-validation.md
  revision: "2026-09-25.1"
  status: offline-candidate-validated
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# 公开候选验收记录

本记录描述 2026-09-25 整理的统一候选，只提供脱敏摘要，不复制本地日志或私有 Registry
数据。

## 纳入的源码边界

候选包含 core Registry/PetriNet 执行、basic 与 Codex 前端、原生插件、共享
provider/profile 层、可选 DSH 宿主和只读 PetriNet viewer。不包含历史分支证据、真实 API
campaign、项目 workflow、本地 profile 和 OpenCode。

软件包附带的 provider/model catalog 为空，不预选 provider、endpoint、credential 或 exact
model。

## 已完成检查

| 检查 | 结果 |
|---|---|
| Python 3.13 完整离线套件 | 581 项通过 |
| Codex/DSH/viewer/registered-host 整合定向集合 | 164 项通过 |
| config、onboarding、resume、workspace 与 net CLI 定向集合 | 52 项通过 |
| Viewer Node/JointJS/ELK 测试 | 72 项通过 |
| 固定上游 DSH 离线集成 | 2 个文件、16 项通过 |
| 文档链接、配对与代码块语法 | 46 页、23 组语言配对通过 |
| Provider catalog 示例 | 空 catalog 通过；3 种非法变更被拒绝 |
| Wheel viewer 资源与第三方许可 | 均存在且非空 |
| 源码目录外 wheel 安装 smoke | Python 3.12、3.13 命令入口通过 |
| 安装后零模型配置流程 | 6 条命令通过；运行副作用受 guard 限制 |

固定 DSH 测试在临时 checkout 中使用清单声明的精确上游 revision 和 pnpm 11.7.0，只使用
本地确定性 transport。

## 调用与限制

本轮没有真实模型或 provider API 调用。离线通过不能证明用户自有 route 可达。Codex TUI
交互、真实 provider 测试和全部 OpenCode 测试留待单独授权阶段。

验收环境未安装 Playwright/Chromium，因此没有运行可选浏览器自动化脚本。上述 Python 与
Node 套件已经覆盖 viewer model/layout、真实 JointJS/ELK 解析、静态打包和只读 HTTP
边界。

## 仓库策略

公开候选只有 `main` 分支，不包含 GitHub Actions workflow，也没有自动 `push` trigger。
真实 provider 证据和生成的 run 数据保存在仓库之外。
