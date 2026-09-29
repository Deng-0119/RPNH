---
name: rpnh-deferred-engineering-work
description: "记录已解决及延后的工程观察与重新开启条件。"
metadata:
  document-kind: engineering-notes
  audience: developer
  language: zh-CN
  counterpart: DEFERRED_ENGINEERING_WORK.md
  revision: "2026-09-29.2"
  status: source-reviewed-pre-release
---

# 工程后续记录

[English](DEFERRED_ENGINEERING_WORK.md)

本文保留不构成受支持运行功能的已确认工程观察。已解决事项继续保留，避免把旧基线误认为
当前行为。只有重新开启条件在正常使用中可观察时，才重新处理。

## DOPT-001：主会话投影事件物化

- **状态：** 当前 `main` 已解决
- **类别：** 性能
- **优先级：** 仅监控
- **解决边界：** `de537681c43a077a999089220558aca266196ead`

### 历史行为与基线

2026-09-24 的实现会在多个主会话投影路径中完整物化事件历史，重复建立 publication-event
ordinal map。它不改变权威或投影结果，但 Registry 历史增长时会重复产生线性读取与临时分配。

保留原 provider-free 合成基线作为来源记录：

| 已提交 turn | Registry 事件数 | 每次恢复物化的 event envelope 数 | 热恢复中位数 |
|---:|---:|---:|---:|
| 0 | 4 | 12 | 13 ms |
| 1 | 15 | 45 | 12 ms |
| 8 | 92 | 276 | 37 ms |
| 32 | 356 | 1,068 | 126 ms |
| 128 | 1,412 | 4,236 | 466 ms |

128 turn 样本中，一次完整扫描为 27.2 ms，从最高 ordinal 后执行空增量扫描为 0.277 ms。
这些是本地诊断数据，不是可移植性能承诺。

### 当前行为

主会话与 Registry 权威读取现在使用 `canonical_object_rows`、`event_by_id`、按条件事件查询
和直接 current-authority lookup 等索引化接口，不再通过反复解析完整事件流构造普通权威。
精确 publication event 存在性、object identity、schema 校验和损坏时 fail-loud 行为保持不变。

修改后使用一个保留的大型历史 3-DOF Registry 做现实只读检查。Authority inspection
benchmark 从约 1.54 秒、491 MiB 峰值 RSS 改善到约 0.57 秒、56 MiB；`rpnh net` 也在不获取
writer authority 的情况下打开 verified event head ordinal 16185。这些仍是本地证据，不是
整项 release 性能保证。

实现范围保持在 EventStore query／accounting 与普通 Registry 读取路径，没有增加进程级
cache、持久索引格式、provider 行为、workflow 规则或 PetriNet 语义变化。

### 重新开启条件

只有受支持的现实 Registry profile 再次显示完整事件流物化成为主要成本，或索引查询丢失
exact-head／publication 校验时，才重新开启。新优化必须保持投影结果等价、并发读取正确、
非法权威明确失败和 viewer 只读语义。使用聚焦的长 Registry benchmark；不能仅凭合成极端
情况推导必须增加 cache。
