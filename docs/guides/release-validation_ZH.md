---
name: rpnh-release-validation
description: "区分历史全量离线基线与版本相关定向验证。"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: release-validation.md
  revision: "2026-10-09.1"
  status: historical-version-specific-validation
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# 发布验证边界

本页只记录指定历史源码窗口，不能当作 V6 组合产品、当前 main 或新 rc2 二进制的全量认证。精选结果保留源码、overlay、输入、命令和完整状态；原始运行资料不在本页分发。离线/native 通过不能证明真实模型 route、stock 前端或未运行业务流程。

## 最近的历史原生窗口

| 源码与范围 | 结果 |
|---|---|
| `00f2d29c7deffed44e2ec635a24f390c6e0d9ace` + 16 示例文件 | [原子工具](../results/tool-pipeline-20261008/README_ZH.md)：32 unique / 33 executions PASS，真实 AF_UNIX，零模型 |
| `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0` + 5 ERP 文件及 C fixture | [ERP runtime](../results/erp-runtime-20261008/README_ZH.md)：65 PASS + 4 subtests；B 三生命周期、C 六场景 PASS，无 Odoo/grader |
| `674252feb836f631c162979f177d1fe91f22559f` + H1 七文件 | [H1](../results/entry-reader-20261009/README_ZH.md)：39 unique / 39 executions，PASS_NATIVE_FINITE |
| `715468dab0b1bea07d7e94a7aa0606eaf194365c` + H2a 五文件 | [H1+H2a](../results/entry-reader-20261009/README_ZH.md)：166 unique / 166 executions，PASS_NATIVE_FINITE；含纯单元项与 12 个包附独立项 |

旧 AF_UNIX EPERM/native-resume 阻断与此前 shared-harness A 的 PARTIAL_ENV 保持原状态，未由 H1/H2a 改写。最终 OS signal-handler identity 未独立探针。不同窗口的 PASS 不相加为同一产品覆盖。

## 2026-10-06 的历史有限集成

此前 freeze04 的真实 Chromium 启动因 root/sandbox 限制为 BLOCKED，没有页面/真实 DOM/截图接受；HTTP、Node 和 mock DOM 通过不改变该事实。这一历史窗口只有限接受选定合同，不是完整 HOST、全部 I00–I10 或 advanced25 接受。业务结果为 [AutomationBench first18 5 PASS / 9 FAIL / 4 BLOCKED 与独立 repair4 1 PASS / 3 FAIL](../../examples/automationbench/PUBLIC_RESULTS_20261006_ZH.md)，旧 14 个已评分任务未重跑。历史 publication source `f58a0f061d1daf4c09fc96c43237c613cc43f439` 的检查与 freeze04/repair 实验源码分别记录：

| 发布边界 | 历史结果 |
|---|---|
| Python / 文档 unittest | 198 个不同 Python 节点通过；另 26 文档 tests OK |
| Node | 224 个不同 title 通过；无 Node 时 HTTP 1 PASS、JavaScript 1 SKIP，重复 HTTP 不增加覆盖 |
| Schema/资源/安装 | 268 schema、1 catalog；wheel/sdist 各 674 文件；构建/安装/依赖/资产审计 exit 0；源码树外 7 条零模型命令通过 |
| Viewer | 6 资产重建逐字节一致；不是实际浏览器认证 |
| 文档 | 122 维护页/61 语言对 + 30 支持页；152 HTML；10,983 本地链接无失败，64 外链未 fetch |
| 示例合同 | 2 双语 schema 示例与空 catalog 有效；3 非法变体拒绝；仅语法/schema，不执行 live 示例 |

这些历史发布检查没有真实 provider/model/judge/browser 调用；不能转记到 V6。原始失败与后续修正窗口保持分别记录。

## 较早的完整离线基线

2026-09-28 完整套件源码为 `073a4516013443fadfcd05fa81d29c4aa1b5391b`，后续定向记录至 `87e98356a1ba78b6afd7bae93d26704e030467a3`。完整套件 host 为 Linux/WSL2 x86-64、Python 3.13.12；当时 Python 3.11 未安装、Python 3.12 无完整测试依赖。保留以下原始结果范围，不认证后续树。

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

## 历史定向增量

完整套件基线之后的运行时变更使用直接覆盖受影响边界的最小集合验证，没有把无关全量
重跑伪装成必要证据：

| 边界 | 定向证据 |
|---|---|
| AgentLoop、registered-host、前端与 checkpoint 闭环 | `de53768` 时 14 项通过 |
| 已持久 v1 兼容与当前 checkpoint reopen | `de53768` 时 2 项通过；旧的可选 `next_attempt_allowed: false` 可读且不阻止 reopen，当前 writer 不再写入该字段 |
| registered-operation 与 resource-service 恢复 | `de53768` 时 16 项通过 |
| Workspace 拒绝／恢复、execution-child 闭合及 Viewer HTTP／资源 | `87e9835` 时 63 项聚焦测试通过，覆盖目录/FIFO/NUL 拒绝、目录错误后纠正、owner-stop FIFO 恢复和不完整 client 隔离 |
| 实际 wheel Viewer 门禁 | 从 `87e9835` 构建的 wheel 通过固定顶层文件和 vendor 文件／许可集合；空 manifest 会被拒绝 |
| 历史文档与配置参考 | 20 项通过；76 页、420 个内部链接、38 组语言配对及构建后的 76 页站点均通过 |
| 历史 Registry 投影 | 一个保留的大型 3-DOF run 在 verified event head 16185 通过 `rpnh net` 打开，且未获取 writer authority |
| `v0.1.0rc1` 完整 collection | 在 `1e85b4f` 上有 790 项通过；固定版本 OpenCode PTY 因未安装可执行文件而跳过。操作者提供的临时根过长，使 23 项 Unix socket 测试在协议处理前因平台路径上限失败；随后两个受影响文件在短临时根下 30 项全部通过。无需修改源码，因此组合候选证据覆盖 813 个唯一通过项和 1 个已记录环境 skip，同时不把第一次运行写成单次全绿。 |

这些集合验证精确 checkpoint reentry、workspace candidate 结算、不可变失败证据与索引化
Registry 读取等变更边界。它们不是新的完整套件总数，也不是真实 provider campaign。
文档检查验证 metadata、链接、语法、语言配对和 schema 示例，不执行模型案例。


## 使用这些记录

历史离线/native 集合没有真实 provider API 调用，route 可达性须在明确授权的条件下单列。包附 provider/model catalog 为空，不预选 provider、endpoint、credential 或 exact model。当前版本的构建、安装和文档结果应由其自己的发布验收记录给出；本次文档投影没有执行这些门槛。参见[精选结果](../results/README_ZH.md)与[案例验证](examples-validation_ZH.md)。
