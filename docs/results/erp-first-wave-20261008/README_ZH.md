---
name: rpnh-result-erp-first-wave-20261008
description: "ERP-Bench 历史首波结果"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

[English](README.md) | 中文

# ERP-Bench 历史首波结果

这些是两个不同 ERP-Bench 任务的独立历史运行，不构成配对比较、完整 benchmark 或 V6 组合产品测试。[完整评分与状态投影](results.json)保留全部已输出指标、规则状态与历史尝试；内容经过重排，不是原始 verifier 字节。

## 源码与输入

适配器基点为 `ae09445fe1d9b973502bc5d2c961976c1d2c0163`。A04/H01 使用干净的跟踪源码 `6f8ee2e406f3c70edb73206f861e56a0202b9f15`，35 个 ERP 自有文件的身份列于 `results.json`。首次发布为 `74fad32d369876841686d10d33361c016e3d3648`，发布不代表重跑。任务与 scorer 固定在 [ERP-Bench ceba3880](https://github.com/agentic-labs/erp-bench/tree/ceba3880af555129b5278e056a0c20f2fb5a0ba9)，投影包含任务 manifest、seed 与输入文件身份。

| 运行 | 任务 | 受测 commit | 业务评分 | 适用检查 | 真实调用 |
|---|---|---|---|---|---|
| A01 / s01 | `2000_easy_01_buy_only_baseline` | `8980e1f0552d30f490af08e60d4e8e3724ea638e` | 不可得；world/owner/model 启动前阻断 | 不可得 | 未知 |
| A02 / s02 | 同一 smoke 任务 | `5df13012e08b474cc0fe86d9531b4df5d5325c08` | 不可得；未证明静止世界可供评分 | 不可得 | 未知 |
| A03 / s03 | 同一 smoke 任务 | `2ca5fbceb058e4ffe3d5b761ca3a6834dac8f621` | **0/100，失败** | 4/32；6 NA | 11 |
| A04 / s04 | 同一 smoke 任务 | `6f8ee2e406f3c70edb73206f861e56a0202b9f15` | **100/100，通过** | 37/37；1 NA | 9 |
| A01 / h01 | `2299_hard_repair_plan_hard` | `6f8ee2e406f3c70edb73206f861e56a0202b9f15` | **21/100，失败** | 86/95；0 NA | 13 |

A03/A04/H01 的 native/provider/evaluation 阶段完成；各次 live 条件内未运行独立 offline/mock 阶段。A01 的 native/provider/evaluation 均阻断；A02 的 native/provider 失败、evaluation 阻断。未知计数不能写成零。H01 九项失败为四项约束与五项采购来源检查；constraint 为 63/75，hygiene 为 15/20，报告 optimality 为 91.66/100。原约束计分闸得到总分 21/100，不能替换为其他指标。A04/H01 保留全部规则行及 NA，省略规则参数和 checker log。A03 保留完整汇总指标及阶段状态，撤下依赖原始请求的诊断。

## 条件与命令

模型为 `local_process` 的 `gpt-5.6-terra`，每节点最多 12 turn、并发 4 节点、单请求 900 秒。官方 actor/verifier/build 时限分别为 3600/300/600 秒。适配环境记录 Harbor 0.24.0、Python 3.12.3、Odoo 19.0.20260926、PostgreSQL 18.6、`odoo-client-lib` 2.0.0。Solver 为非 root、network-none，保留任务内 Odoo loopback。2048 MB 存储是声明值，未证明宿主 writable-layer 配额。投影包含 H01 镜像、依赖、构建身份与完整模型限制。安装使用明确的 uv 兼容适配，结果属于 `grader_compatibility`。

历史命令类别为 TaskControl start/status/result/result_evidence/stop、既有精确模型 local-process，以及使用不变 `tests/test.sh` 的 Harbor `Verifier.verify`。以下可移植 CLI 配方需要历史源码、精确上游、自有 selection 及另行提供的共享 validator；它是执行说明，本次文档任务没有运行：

```sh
rpnh-erp run --upstream "$ERP_SOURCE"   --task 2000_easy_01_buy_only_baseline   --execution-selection "$SELECTION" --run-root "$NEW_RUN"   --condition-id reproduction-smoke --source-root "$SOURCE"   --shared-validator "$VALIDATOR" --authorize-existing-model
```

H01 改用 `2299_hard_repair_plan_hard`，另建 world/condition。历史 build/firewall 与 Codex endpoint 适配须按 [ERP 指南](../../../examples/erp_bench/README_ZH.md)显式提供；此命令本身不能重现已准备资产。不分发凭据或私有 selection。另一个 integration-only 离线窗口因命令记录缺少精确受测源码，已从本精选结果撤下，不能认证指定产品版本。

部分历史资源未通过 fresh-reader provenance 检查，精确字节回收不解除该边界。A04 control 文本及 vendor HTTP/完整 Codex event stream 在此证据边界没有收集。不由分数推断单一原因、runtime 优势或完整业务验收。


[分发字节清单](MANIFEST.json)记录本摘要与投影的新字节身份，不能当作原始材料字节。
