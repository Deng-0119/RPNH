---
name: rpnh-result-entry-reader-20261009
description: "历史 H1 与 H1+H2a 原生验证"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

[English](README.md) | 中文

# 历史 H1 与 H1+H2a 原生验证

这里保留两个不同历史窗口：独立 H1 与后续 H1+H2a reader overlay；它们不是 V6 组合产品结果。[完整源码/用例/状态投影](results.json)分别保留两个窗口、全部用例 ID、路径勘误与原生回读计数。

## 源码窗口

H1 实测 `674252feb836f631c162979f177d1fe91f22559f` 加七文件，冻结补丁 SHA256 为 `71261d059d205b375334450aef281702a1c573562c6e905a08d411b907749a6c`，发布为 `715468dab0b1bea07d7e94a7aa0606eaf194365c`。本地 AF_UNIX focused36 + classic3 通过：**39 个唯一用例 / 39 次执行**，fail/error/skip 为 0。早期云端 pipe 语义为 39 unique / 50 executions，云端 native 为 BLOCKED_ENV；分别计数。

H1+H2a 实测 `715468dab0b1bea07d7e94a7aa0606eaf194365c` 加五个 reader 文件，补丁 SHA256 为 `62f914d41d953e9badd4eb06eec43273f9d52bd5bd9b948ad6e10137d3ed2b24`，发布于 `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。3493 项实际工作树 manifest SHA256 为 `488ce0b6ffac52b468db3d3d5cdc87a263fcb95aadbcc48d4df1f81d26cb5323`，前后不变；它不是 Git tree。投影标识全部变更文件。此窗口 H1 为 39、H2a 为 130，共享 classic3 仅执行一次，总计 **166 unique / 166 executions**，其中仓库 154、包附独立 12，不是 169。

| 历史组合窗口 | 通过 | JUnit 秒数 |
|---|---|---|
| h2-focused | 47 | 232.559 |
| h2-compatibility | 44 | 1.393 |
| h2-shared | 15 | 91.731 |
| h2-independent | 12 | 72.818 |
| h2-native-status | 7 | 2.635 |
| h2-native-callbacks | 2 | 0.606 |
| h1-classic | 3 | 162.904 |
| h1-focused | 36 | 745.261 |

h2-shared 的完整路径为 `examples/harnessaudit_office/tests/test_registry_reader_consumers.py`，仅纠正历史缩写路径，用例名和结果不变。早期 H2 status 仍为 6 PASS / 1 AF_UNIX BLOCKED_ENV，resume 在 provider dispatch/断言前阻断。20 个预期旧基线失败保留历史，不变成新 PASS。早期 shared-harness A 保持 PARTIAL_ENV，H1/H2a 没有重测。

## 原生结果与复现边界

Python 3.13.12 / pytest 8.4.2 使用真实 AF_UNIX、实际 OS SIGINT 与显式 resume；classic 脚本逻辑账本分别 `[2,0]`、`[3,0]`、`[3,0]`，真实 provider 请求为 0。最终 OS handler identity 未独立探针。套件含单元检查，不是 166 项 socket 集成或全仓验收。

两个原生 pipeline/回读记录均得到 complete、**2.000 kWh / 1.70 CNY**、10 firing、12 业务产物及 2 source。新进程回读的 11 个稳定字段全部一致，live-only `transport`/`stop_reason` 缺省；ordinal/event count 1001、dispatch/execution start 10、模型 `[0,0]` 前后不变。输入为合成 [pipeline fixture](../tool-pipeline-20261008/results.json)，不是业务题库；验收核对 Registry 状态与字段相等，没有外部 scorer。

在精确历史 overlay 和既有依赖下，H1 focused 及一个 H2 reader 窗口可参数化为以下命令；TMP 为短的原生目录：

```sh
python -B -m pytest -q -p no:cacheprovider   tests/test_orchestrator_boundary.py tests/test_harness_quiescence.py   tests/test_harness_resource_continuation.py examples/tool_pipeline/tests   --basetemp="$TMP/h1"
python -m pytest -q -p no:cacheprovider tests/test_task_control_registry_reads.py   --basetemp="$TMP/h2"
```

投影保留八个历史命令的可移植参数版本。包附独立测试需要另行提供历史 fixture，本页不分发；命令不是本次新执行，也不承诺当前 checkout 能重建旧窗口。H1/H2a 没有真实 provider、Docker 业务世界或 benchmark。


[分发字节清单](MANIFEST.json)记录本摘要与投影的新字节身份，不能当作原始材料字节。
