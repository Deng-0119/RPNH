---
name: rpnh-deferred-engineering-work
description: "记录已确认的非阻塞工程后续项和重新处理条件。"
metadata:
  document-kind: engineering-notes
  audience: developer
  language: zh-CN
  counterpart: DEFERRED_ENGINEERING_WORK.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# 延后处理的工程事项

[English](DEFERRED_ENGINEERING_WORK.md)

本文档记录已经确认、但目前不值得改变既有运行边界的非阻塞工程事项。列入本文档不表示
相关行为在正确性上有问题；只有重新评估条件在正常使用中实际出现时，才应重新处理。

## DOPT-001：主会话投影重复物化事件历史

- **状态：** 延后处理
- **类别：** 性能
- **优先级：** 低

### 当前行为

`MainThreadRegistry.project_current_thread()` 会重建当前 thread、turn lineages 和 child
Registry link lineages。这三个私有读取路径都会各自调用一次 `EventStore.list_events()`，
建立相同的 publication-event ordinal map。因此，在 Registry 内容不变时，一次投影仍会
完整物化并解析三遍事件历史。

该行为不改变 Registry 权威、顺序、损坏检测或投影结果，但会随着主 Registry 历史增长，
增加线性的读取和临时对象分配开销。

### 离线基线

以下合成基线于 2026-09-24 使用已提交的 main turn 测得，没有调用 provider。这些耗时只
是本地诊断证据，不是可移植的性能保证。

| 已提交 turn | Registry 事件数 | 每次恢复物化的 event envelope 数 | 热恢复中位数 |
| ---: | ---: | ---: | ---: |
| 0 | 4 | 12 | 13 ms |
| 1 | 15 | 45 | 12 ms |
| 8 | 92 | 276 | 37 ms |
| 32 | 356 | 1,068 | 126 ms |
| 128 | 1,412 | 4,236 | 466 ms |

在 128 个 turn 的样本中，一次完整事件扫描中位数为 27.2 ms；从当前最高 ordinal 之后
执行一次无新增事件的增量扫描为 0.277 ms。若把第二、第三次完整扫描换成增量扫描，该
样本约可减少 54 ms，即完整投影耗时的约 12%。对象读取和 lineage 校验仍占恢复的大部分
时间，因此该改动不会让整体恢复提速三倍。

短会话中没有明显的用户影响。本次没有观察到数据丢失、resume、workflow、provider 或
PetriNet 正确性故障；主会话 Registry 的 10 项定向基线测试全部通过。

### 建议的有界优化

后续若重新处理该事项：

1. 在单次 `project_current_thread()` 调用内部创建私有、request-local 的
   publication-event ordinal index；
2. 第一个子投影完整读取事件并记录已观察到的最高 ordinal，后续子投影使用
   `list_events(after_ordinal=...)`；
3. 每次为后续对象排序前，合并新近可见的事件。不同 SQLite 读取可能看到后续 commit，
   因此不能固定使用第一次读取的 map；
4. 投影结束即丢弃 index，不增加进程级或跨请求 cache；
5. 写入前置校验继续独立读取，并保留 publication event 缺失或权威数据损坏时现有的
   fail-loud 行为。

预计实现范围只涉及主会话私有投影调用链及定向测试，不应修改 EventStore、schema、持久化
格式、provider、workflow 或 PetriNet。

### 验收边界

未来的修改应证明：

- 投影输出完全等价；
- Registry 稳定时，一次投影执行一次完整事件读取和有界的增量读取，而不是三次完整读取；
- 子投影之间新提交并变得可见的事件能够被纳入；
- publication event 缺失和权威数据损坏仍然明确失败；
- index 不会进入下一次投影或写入前置校验；
- 在具有现实长度的主会话 Registry 上，定向基准显示有实际意义的改善。

### 重新处理条件

如果正常的主会话打开、resume 或历史投影出现用户可感知的延迟；现实 profile 显示重复
事件物化已成为主要成本；或者另一个已批准的主会话修改恰好涉及同一私有投影调用链，
则重新评估该事项。在此之前，当前测得的改善幅度不足以承担并发敏感代码改动的成本。
