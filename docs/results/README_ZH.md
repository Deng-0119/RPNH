---
name: rpnh-curated-results
description: "精选历史结果"
metadata:
  document-kind: index
  audience: operator-and-developer
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

[English](README.md) | 中文

# 精选历史结果


本目录保留历史应用评分与有限零模型 runtime 结果。每项明确实际源码、overlay、输入、scorer、命令/条件和全部评分/状态投影；旧窗口不认证后续组合产品。本次文档改写没有运行实验、模型或新的产品测试。原始诊断、对话、数据库和私有 profile 不在公开摘要内。

| 记录 | 原范围 |
|---|---|
| [ERP-Bench 历史首波结果](erp-first-wave-20261008/README_ZH.md) | ERP A01–A04 + H01：阻断/失败与 0/100、21/100、100/100 分别保留 |
| [SlopCodeBench 适配前 3 点结果](scb-prefix3-20261008/README_ZH.md) | SCB 五点中前三点：13/13、25/25、40/47；第四/第五点未运行 |
| [ERP 合成原生 runtime 验证](erp-runtime-20261008/README_ZH.md) | ERP 合成验证：65 + 4 subtest；B 生命周期 / C 六场景 |
| [原子工具 pipeline 原生验证](tool-pipeline-20261008/README_ZH.md) | 原子工具：32 unique / 33 executions；2.000 kWh / 1.70 CNY |
| [历史 H1 与 H1+H2a 原生验证](entry-reader-20261009/README_ZH.md) | H1 39 与后续 H1+H2a 166；旧 AF_UNIX 阻断保留 |

其他已整理结果：[AutomationBench first18 + repair4](../../examples/automationbench/PUBLIC_RESULTS_20261006_ZH.md)、[HarnessAudit Office](../../examples/harnessaudit_office/README_ZH.md)。它们各自保留旧 cohort/条件与失败/阻断分母，不与本目录求和。

- [产品组合有限验证（2026-10-09）](product-validation-20261009/README_ZH.md)
