---
name: rpnh-release-validation
description: "区分历史全量离线基线与当前定向验证。"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: release-validation.md
  revision: "2026-10-06.1"
  status: historical-full-baseline-current-focused-delta
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# 发布验证边界

本页把历史完整套件基线与后续定向验证分开，只提供脱敏摘要，不复制本地日志或私有
Registry 数据。历史总数不能表述成当前 `main` 的完整套件结果。

## 2026-10-06 有限集成与bridge修复

benchmark实际执行源条件称为 **freeze04** 与 **repair**。其旧manifest不描述本次最终发布文件集合；
本次另有文档、包装检查及测试修改。集成包括typed-v9、P4完整历史Assembly merge、
P6 graph source merge、有界I02/shared-graph gate，以及Workset/normal-root读取器、token分配、
ordinary revision和native HTTP/Node消费者。云端ownership交接已经完成，旧等待文字属历史。
这些是有限验收，不是完整HOST、全I00–I10或全部advanced25验收。

| 既有证据 | 接受边界 |
|---|---|
| 先行typed / P4 / I02 | writer节点23 / 38 / 10；独立节点11 / 10 / 7 |
| 先行P6 | writer在freeze03上32、04上6；独立在03上5、04上1；不重标为全部04 |
| 动态normal-root writer | 64 case / 192 phases；另一次R02 Success-wins窗口1 case / 3 phases |
| 集成独立执行 | 8个exit0窗口、31次执行 / 93 phases / 30不同节点；02上8、03上22、04上1 |
| 有限reader/root场景 | 跨Success保留refs/bytes、独立T01 run terminal、ordinary revision、旧catalog拒绝、精确replay/twochild、7损坏副本只读拒绝、attach-wins与独立CAS；R02仅一个明确Success-wins次序 |
| 原生观察 | HTTP、Node与mock DOM通过；不是实际浏览器DOM |
| 实际browser | Chromium启动exit1，root/sandbox限制，BLOCKED；pytest skip不是PASS；无页面/真实DOM/截图接受 |
| Bridge修复 | writer52项、独立六个exit0窗口50个不同用例；重叠，不是102 unique |
| Installed native-host准备 | 两个独立离线窗口，各7 PASS |

有限并发检查不保证任意调度或child数量。真实browser未重试或关闭sandbox。源码恢复和收集数量
不是测试通过。业务结果单列于[AutomationBench first18 + repair4](../../examples/automationbench/PUBLIC_RESULTS_20261006_ZH.md)。
人工审阅未运行，污染未知，强worker OS隔离未证明。上述历史证据与下方本次发布检查分开，
历史计数不累加到本次结果。

## 2026-10-06 发布验证

独立执行者在Linux/WSL、Python 3.13与Node 22.22.1上对集成发布源完成定向检查。
这是有限边界验证，不是完整套件或全HOST/I00–I10/advanced25接受。

| 本次发布检查 | 实际结果 |
|---|---|
| 定向Python测试 | 198个不同节点通过，已含全部30个bridge用例；重复执行只计一次 |
| 文档unittest套件 | 26 tests OK，与198个定向Python节点分开记录 |
| Node测试 | 224个不同test title通过；mock DOM/Node证据不是实际浏览器接受 |
| 可选Node边界 | 无Node时HTTP 1 PASS、JavaScript 1 SKIP；重复HTTP节点不增加198节点计数 |
| Schema与包资源 | 268个schema验证通过，另检查1个catalog JSON；wheel和sdist均核验全部674个预期打包文件 |
| 构建与安装 | wheel/sdist构建、wheel安装、依赖检查与wheel资产审计exit0；源码树外7条installed零模型命令通过 |
| Viewer重建 | 6个资产重建后与入库文件逐字节相等 |
| 文档check/build | 122个维护页面 / 61组语言对，另有30个支持文档；构建152个HTML页面 |
| 构建文档链接 | 检查10,983个本地链接，failures为空；64个外链未联网读取 |
| 文档示例 | 2个双语schema示例与空catalog有效，3种非法变更被拒绝；语法/schema检查不执行示例或验证live transport |

文档结果结合最终check/build/link验证与已记录的unittest和example检查。构建器还对链接的
5个JSON、4个CSV、3个Python文档做语法检查。结果支持页可构建，但不成为维护主题，
也不放宽维护页面的metadata与语言对要求。

初始失败单独保留：bridge runner对相对路径`dir_fd`的guard解释、Node依赖解析问题，
均在任务内runner/依赖设置中修正，并重跑受影响检查。此前docs checker失败通过支持链接的
结果文档及下载文件解决，仍保留链接与metadata校验。原失败不删除、不重标为PASS。
可选Node测试拆分与wheel审计修复属于发布测试/打包改动，不是新运行时行为，也不改变
既有benchmark实际执行源身份。

本次发布检查没有真实provider、model、judge或browser调用。此前实际browser的root/sandbox
启动阻断仍为BLOCKED；没有重试、绕过sandbox或页面/真实DOM/截图通过声明。


## 纳入的源码边界

本节旧提交与套件仅描述历史基线；本次集成边界以上方2026-10-06有限记录为准。

本记录中最后一次完整离线套件于 2026-09-28 针对运行时提交
`073a4516013443fadfcd05fa81d29c4aa1b5391b` 执行。该历史记录还覆盖截至
`87e98356a1ba78b6afd7bae93d26704e030467a3` 的定向变更：跨前端 canonical session
ownership、下级执行网、workspace 版本历史、任意 checkpoint reopen、compaction／恢复
闭环、Registry 校验与 runtime 重试策略分离，以及 workspace 目标准入前拒绝、特殊文件
安全的 snapshot 恢复、Viewer 独立连接处理与有限 socket I/O 超时，以及严格 wheel vendor
asset 校验。后续运行时集成有上方单列的有限证据；历史基线不认证本次集成发布树。

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
| 当前文档与配置参考 | 20 项通过；76 页、420 个内部链接、38 组语言配对及构建后的 76 页站点均通过 |
| 历史 Registry 投影 | 一个保留的大型 3-DOF run 在 verified event head 16185 通过 `rpnh net` 打开，且未获取 writer authority |
| `v0.1.0rc1` 完整 collection | 在 `1e85b4f` 上有 790 项通过；固定版本 OpenCode PTY 因未安装可执行文件而跳过。操作者提供的临时根过长，使 23 项 Unix socket 测试在协议处理前因平台路径上限失败；随后两个受影响文件在短临时根下 30 项全部通过。无需修改源码，因此组合候选证据覆盖 813 个唯一通过项和 1 个已记录环境 skip，同时不把第一次运行写成单次全绿。 |

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
