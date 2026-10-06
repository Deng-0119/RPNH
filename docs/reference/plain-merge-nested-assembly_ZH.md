---
name: rpnh-plain-merge-nested-assembly
description: "Explicit plain merge proofs in closed two-level Assembly/v3."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: plain-merge-nested-assembly.md
  revision: "2026-10-05.1"
  status: integrated-author-proof-candidate
---

[English](plain-merge-nested-assembly.md) | [中文](plain-merge-nested-assembly_ZH.md)

# 普通合并结果接入闭合嵌套 Assembly

修订：2026-10-05.1。范围：显式 catalog 与同一读取快照内的证明集成。

通过 `plain_merge_nested_assembly_schema_data()` 显式选择普通合并分析与结果、普通作者版本、Branch/v1，以及已有闭合 Assembly/v1–v3 合同。旧 catalog builder 保持原清单。组合 builder 合并重叠项前检查规范 schema 内容与完整 type definition 相等。

真实合并结果 M 仍为具有有序 exact 双父引用及独立完整合并命令的 `NetRevision/v1`。M 后普通编辑 E 保持原单父作者合同。M/E 都可作为 Assembly/v3 的显式叶成员，或平坦 Assembly/v2、Assembly/v3 子 Assembly 的成员。同一 exact 版本在不同 member ID 下保留独立实例。已有两层闭合限制以及显式连接、完成项语义不变。

一次公开完整读取只使用一个 Registry 快照和本次调用私有的 active-reference 路径。直接叶成员与配对 generated 验证继承外层 Assembly 路径；重读 child-v2 成员时再加入该子 Assembly exact 引用。producer 前置及最终发布 cut 的父版本验证均带上本次结果引用。每个 sibling 只扩展自己的路径，不把已完成 sibling 加入全局 visited 集合。

配对 generated 必须仍是严格普通 `ValidatedClosedRevision`，且原 command、材料引用、声明和实际编译全部匹配。允许合并叶成员并不允许把合并版本替代 Assembly 自身的 generated 版本。descriptor 读取与完整证明读取继续使用不同 API。完整消费者先重建合并分析与调用者显式选择，再组合声明；编译成功不能替代业务冲突选择。

聚焦集成测试覆盖真实 M/E、child-v2 与 flat-child-v3 路径、重复实例来源、重新打开的完整消费者、exact 零写 replay、合并证明损坏和循环，以及 parent/member/generated 最终 cut 失败。失败前已提交的前缀材料可以保留；恢复 exact 输入后，同一命令可继续完成一次。实际验证状态以对应执行证据为准，本合同文本不宣称测试已通过。

本集成不增加 runtime adoption、执行、joint-cut/latest 保证、任意嵌套深度、自定义合并顺序或同 key 不兼容 HOST 协调。这些独立能力保持其原计划状态。
