---
name: rpnh-release-validation
description: "记录统一公开候选的脱敏离线验收边界。"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: release-validation.md
  revision: "2026-09-28.1"
  status: offline-candidate-validated
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# 公开候选验收记录

本记录描述 2026-09-28 验证的统一候选，只提供脱敏摘要，不复制本地日志或私有 Registry
数据。

## 纳入的源码边界

通过验证的运行时源码提交为
`073a4516013443fadfcd05fa81d29c4aa1b5391b`。后续只修改文档的提交可以纳入本记录，
但不会改变该运行时边界。

候选包含 core Registry/PetriNet 执行、basic、Codex 与 OpenCode 前端、原生插件、共享
provider/profile 层、可选 DSH 宿主和只读 PetriNet viewer。不包含历史分支证据、真实 API
campaign、项目 workflow 和本地 profile。

软件包附带的 provider/model catalog 为空，不预选 provider、endpoint、credential 或 exact
model。

## 已完成检查

| 检查 | 结果 |
|---|---|
| Python 3.13 完整离线套件 | 744 项通过，1 项因环境条件跳过，用时 2279.34 秒 |
| Viewer HTTP、打包资源与不可变对象定向集合 | 41 项通过，用时 0.75 秒 |
| 直接受影响的 Registry 集成集合 | 84 项通过，用时 241.80 秒 |
| 文档链接、语法与语言配对 | 70 页、366 个链接和 35 组语言配对通过 |
| 文档命令示例 | 通过 |
| 源码 wheel 的 Viewer allowlist 与第三方资源 | 通过，包含 `overview.mjs` 和 `wire-geometry.mjs` |
| 源码分发包往返验证 | sdist 构建成功；从 sdist 重建的 wheel 通过同一 Viewer 资源检查 |
| 源码目录外的 wheel 安装 smoke | Python 3.13 从隔离环境加载 `cpn`；7 条零模型命令通过 |
| 安装后的原生示例 | 导出与验证通过，且运行副作用受到 guard 限制 |

定向集合与完整套件有重叠，它们是变更相关证据，不会叠加到完整测试总数。打包检查使用从
上述已验证提交实际构建的 wheel 和 sdist。验收判断不依赖 checksum。

Viewer 检查覆盖 Viewer 提供的全部顶层静态文件、随包提供的 JointJS/ELK 资源及其许可
记录。安装后 smoke 在源码树外执行，并在运行 help、空 catalog 配置与示例命令时禁止网络、
子进程和 Registry 副作用。

## 调用与限制

本轮没有真实模型或 provider API 调用。离线通过不能证明用户自有 route 可达。完整套件中
唯一跳过项是验收环境未安装固定版本 OpenCode 可执行文件所对应的 PTY 测试；OpenCode
协议测试仍使用无 provider 的应用替身执行。

验收环境为 Linux/WSL2 x86-64 和 Python 3.13.12。环境未安装 Python 3.11，可用的 Python
3.12 解释器也不具备完整测试依赖，因此本轮没有重新完成 3.11/3.12 全套兼容性验证。本轮
也没有执行浏览器自动化、固定的真实 OpenCode 可执行文件或真实 provider route。本候选
没有修改 Viewer JavaScript，因此没有重复运行独立的 Node/浏览器套件。

## HTTP 边界

Viewer HTTP server 只是本地只读展示面。它仅接受字面 loopback 监听地址，校验请求目标、
`Host` 和可选的同源 `Origin`，并正确格式化 IPv6 authority。这不会取代其他 adapter 的协议
专属校验：OpenCode 继续使用带认证的 HTTP/SSE 合约，Codex 继续使用 Unix socket 合约，
provider adapter 继续遵守其配置的远程 transport 策略。

## 仓库策略

公开候选使用 `main`，不包含 GitHub Actions workflow，也没有自动 `push` trigger。真实
provider 证据和生成的 run 数据保存在仓库之外。
