---
name: rpnh-plain-merge-analysis
description: "显式、惰性的 plain Module 作者三方分析。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: plain-merge-analysis.md
  revision: "2026-10-04.1"
  status: stage-a-analysis-only
---

[English](plain-merge-analysis.md) | [中文](plain-merge-analysis_ZH.md)

# plain Module 作者合并分析（Stage A）

显式组合 `plain_merge_schema_data()`，配置
`PlainModuleMergeAnalyzer(gateway, registration, producer_principal_ref)`。
`analyze(local_ref=L, incoming_ref=R, command_id=..., base_ref=B)` 先发布
不可变完整命令，再发布 exact analysis 资源。可选 `base_ref` 只是精确祖先
断言，不允许调用者任意指定共同祖先。
`read_plain_merge_analysis(core, analysis_ref, registration)` 在同一个 Registry
只读事务切面内独立重建完整分析。

可信 HOST 仅编译已有 canonical 作者输入，不调用业务 operation、executor、
tool，不采用运行、不产生结果 revision、不推进 Branch。`status=analyzed`
只表示作者差异已分析，不表示合并成功、冲突已决或运行安全。

## 身份与不可变证据

输入必须是同本域 source、绑定 owner 的 exact `NetRevision/v1` 完整作者证明。
本 merge 入口局部核对 source-binding 实际 payload 与选中投影，并 exact read
task/native-run/bootstrap 实际 payload。constructor 先核验再登记 schema；分析与
读取各在其输入／证明的同一 snapshot 内核验。配置 producer principal 即使不同于
输入作者，也在写入任何 analysis 资源前检查。每个参与祖先都完整核验
definition、稳定映射、边界、实际 schema authority
字节、选中 HOST 声明和原始完整命令。本 Stage A 入口另按原 v1 command/source/task
UUID5 规则重算每个 revision 的 version 身份及 root／单父 logical 身份；这只是
producer 身份合同兼容性，不证明代码执行来源或授权。等价新副本必须使用新的
producer command；原全局 closed reader 合同不变。graph v1–v4 component key、graph revision、
Assembly generated constraints 和非空 opaque designer constraints 均排除；
祖先同样检查，不能通过后代擦掉约束洗成 plain。

谱系只接受唯一最近共同祖先；显式 B 不匹配即拒绝。无共同祖先可发布
`unrelated_histories`，其中 base 为空，不伪造三方规范化、diff 或冲突。
合同为多最近祖先保留同样未决的 `multiple_bases`。当前已接受 v1 完整证明只有
零或单父，因此受支持 canonical 输入尚不能产生多 base；无证明的多父
描述符仍被 full consumer 拒绝，不会虚构祖先。

首份独立 analysis command schema 锁定全部请求、算法、规范化版本、祖先／
材料 pins 和完整预期 analysis。第二份资源必须具有唯一的 exact command marker
与引用。同命令异输入冲突；相同重试可完成中断的准备发布。未知、额外或双重
metadata marker 均 fail closed。本阶段不改变 `closed_author_command/v1`，
不接纳多父结果材料。将来的 resolution/result 必须使用新的不可变命令域，
引用本 analysis 的 exact ref。

## 稳定 ID 比较与冲突

Module/component 容器 body 排除子列表；每个稳定身份分别保留 kind/parent、
name 和整体契约 value atom，列表顺序单独保留。端点、terminal operation、
operation input/output/request、outcome product 和 effect port binding 均改为
稳定 ID；各侧 locator 独立保留。机械 effect reference、opaque config、schema
及 exact HOST selection 保持不可拆分，不递归猜测 JSON 字段合并或选择答案。

每个差异保留类型敏感的 B/L/R 精确值及存在性。冲突有确定 ID、原因、subject
和三侧值，覆盖同 atom 分歧、同身份并发引入、delete/modify、被删依赖、同名
异 ID，以及契约耦合。两侧交叉修改时，对全局边界、terminal、schema/HOST、
预算和 component 内端口/config/effect/binding 采用保守分组；已有 link 连接两端
component，共享 budget bucket 或机械 effect reference 也连接依赖组。单独编译成功
不能证明组合作者意图。规范化值不是结果 Module。

已有完整校验的实际输入 fragments 另提供显式派生资源依赖提示，固定稳定 component
ID 与 carrier 类别。lease identity/pool、variable resource arc、logical slot/reset、
可复用／资源库所及 arc lease claim 会对两侧编辑形成保守全局耦合，表面断开的
作者区域也不能自动视为独立。`lowered_resource_coupling` 冲突仍保留作者 atoms，
不把 PN 产物当作者差异或合并／采用目标。strict reader 从相同完整输入独立重建提示。

仅处理有限精确输入，拒绝循环及非有限 JSON 数值；不增加任意大小默认值，也不
宣称硬内存上限。耦合冲突按依赖组记录一次，不展开为字段笛卡尔积。已有可选及默认 schema catalogs 不变。冲突决策、
引用重建、结果 full consumer 分派、Branch CAS、Assembly 消费及 merge 后普通
单父编辑属于后续验收阶段，不能由本阶段宣称完成。

单个显式 Registration 必须重建所有选中输入 HOST 版本。同一 HOST key 的
不兼容 pinned revision 会在分析发布前 fail closed；这不是已持久化的 HOST
冲突分析结果。

显式启用的 [Stage B1 结果候选](plain-merge-result_ZH.md) 新增独立 merge-command
证明分支。在该扩展下，analysis 可以遍历完整验证的 merge 输入；上文 Stage A
单独合同中的 parent 限制描述原已验收 baseline。结果验证及 B2/B3 集成仍分别验收。
