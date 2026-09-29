---
name: rpnh-release-validation
description: "区分历史全量离线基线与当前定向验证。"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: release-validation.md
  revision: "2026-09-29.4"
  status: historical-full-baseline-current-focused-delta
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# 发布验证边界

本页把历史完整套件基线与后续定向验证分开，只提供脱敏摘要，不复制本地日志或私有
Registry 数据。历史总数不能表述成当前 `main` 的完整套件结果。

## 纳入的源码边界

本记录中最后一次完整离线套件于 2026-09-28 针对运行时提交
`073a4516013443fadfcd05fa81d29c4aa1b5391b` 执行。当前运行时文档还覆盖截至
`87e98356a1ba78b6afd7bae93d26704e030467a3` 的定向变更：跨前端 canonical session
ownership、下级执行网、workspace 版本历史、任意 checkpoint reopen、compaction／恢复
闭环、Registry 校验与 runtime 重试策略分离，以及 workspace 目标准入前拒绝、特殊文件
安全的 snapshot 恢复、Viewer 独立连接处理与有限 socket I/O 超时，以及严格 wheel vendor
asset 校验。该 SHA 之后
仅修改文档的提交不改变运行时边界。

统一代码树包含 core Registry/PetriNet 执行、Basic、Codex 与 OpenCode 前端、原生插件、
共享 provider/profile 层、可选 DSH 宿主和只读 PetriNet viewer。不包含历史分支证据、
真实 API campaign、项目 workflow 和本地 profile。

软件包附带的 provider/model catalog 为空，不预选 provider、endpoint、credential 或
exact model。

## 历史完整套件基线

| 检查 | `073a451` 的结果 |
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

定向集合与完整套件有重叠，是变更相关证据，不叠加到完整测试总数。打包检查使用该提交
实际构建的 wheel 和 sdist；验收判断不依赖 checksum。Viewer 检查覆盖全部顶层静态文件、
随包 JointJS/ELK 资源及其许可记录。安装 smoke 在源码树外执行，并在检查 help、空 catalog
配置和案例命令时阻止 provider-capable 网络、子进程与 Registry 副作用。

## 当前定向增量

完整套件基线之后的运行时变更使用直接覆盖受影响边界的最小集合验证，没有把无关全量
重跑伪装成必要证据：

| 边界 | 定向证据 |
|---|---|
| AgentLoop、registered-host、前端与 checkpoint 闭环 | `de53768` 时 14 项通过 |
| 已持久 v1 兼容与当前 checkpoint reopen | `de53768` 时 2 项通过；旧的可选 `next_attempt_allowed: false` 可读且不阻止 reopen，当前 writer 不再写入该字段 |
| registered-operation 与 resource-service 恢复 | `de53768` 时 16 项通过 |
| Workspace 拒绝／恢复、execution-child 闭合及 Viewer HTTP／资源 | `87e9835` 时 63 项聚焦测试通过，覆盖目录/FIFO/NUL 拒绝、目录错误后纠正、owner-stop FIFO 恢复和不完整 client 隔离 |
| 实际 wheel Viewer 门禁 | 从 `87e9835` 构建的 wheel 通过固定顶层文件和 vendor 文件／许可集合；空 manifest 会被拒绝 |
| 当前文档与配置参考 | 20 项通过；70 页、388 个内部链接、35 组语言配对及构建后的 70 页站点均通过 |
| 历史 Registry 投影 | 一个保留的大型 3-DOF run 在 verified event head 16185 通过 `rpnh net` 打开，且未获取 writer authority |

这些集合验证精确 checkpoint reentry、workspace candidate 结算、不可变失败证据与索引化
Registry 读取等变更边界。它们不是新的完整套件总数，也不是真实 provider campaign。
文档检查验证 metadata、链接、语法、语言配对和 schema 示例，不执行模型案例。

## 调用与限制

历史完整离线运行与后续定向运行时验证均未产生真实模型或 provider API 调用。离线通过
不能证明用户自有 route 可达。历史完整套件中唯一跳过项是未安装固定版本 OpenCode
可执行文件对应的 PTY 测试；OpenCode 协议测试仍使用无 provider 的应用替身。

完整套件环境为 Linux/WSL2 x86-64 和 Python 3.13.12。环境未安装 Python 3.11，可用的
Python 3.12 解释器也没有完整测试依赖。本记录没有执行浏览器自动化、固定的真实 OpenCode
客户端或真实 provider route。带日期且经过授权的真实案例另见
[案例验证](examples-validation_ZH.md)。

## HTTP 边界

Viewer HTTP server 只是本地只读展示面。它仅接受字面 loopback 监听地址，校验请求目标、
`Host` 与可选同源 `Origin`，正确格式化 IPv6 authority，并独立处理连接；已接受的 socket
具有有限 I/O 超时。这既不是总请求 deadline，也不是连接数或线程数上限。它不会取代其他
adapter 的协议专属校验：OpenCode 保留带认证的 HTTP/SSE 合约，Codex 保留 Unix socket 合约，provider
adapter 保留已配置的远程 transport 策略。

## 仓库策略

Canonical 项目使用 `main`，不包含 GitHub Actions workflow，也没有自动 `push` trigger。
真实 provider 证据和生成的 run 数据保存在仓库之外；仓库只提交审查过的脱敏摘要。
