---
name: rpnh-static-lease-reads
description: "资源 lease 的非消费精确读取。"
metadata:
  document-kind: guide
  audience: developer
  language: zh-CN
  counterpart: static-lease-reads.md
  revision: "2026-10-09.1"
  status: maintained
---

[English](static-lease-reads.md) | [中文](static-lease-reads_ZH.md)

# 静态 resource-lease read 弧

当 adopted PN 的输入弧声明 `mode="read"`，且 place 的 `token_kind` 为
`resource_lease` 时，该弧引用原 marking 中的准确 token，不消费它。
引用复用 variable lease read 已有的 claim/reference 字段；不增加第二份
marking、reader 表、scheduler 或锁策略。

## 引用的含义

- 准确 token 必须存在、新鲜、属于 adopted net，并满足声明的 read 权重。
  Registry 从不可变 PN 和 checkpoint 核验；调用方填写的 claim 或 consume
  索引不能替代这个契约。
- Firing 活跃期间不消费该 token。多个合法 firing 可以引用同一个 token。
  Firing 次数由独立的 consume 控制/输入 token、原预算或其他声明结构限制；
  read 前置本身不限制 firing 次数或并发。
- Success 保留原 token 的准确身份与状态，不 return、复制或 mint 引用 token。
  兄弟 firing 的 Success、active claim 重建和正常 completion recovery 后，
  这个引用仍然存在。
- 这是 PN read 前置和资源引用，不是 borrow，也不授予物理资源使用许可。
  既有 exact-resource 字节读取 provenance 不变；动态资源访问仍需要原
  variable resource arc 和访问检查。

## 兼容性

普通 data/control read 弧保持原本的局部消费归还行为：消费前驱 occurrence，
  后继 occurrence 携带同一资源。静态 lease read 则保留原 occurrence。

支持已有 `VariableResourceArc`。同一静态 read 与 variable read 可共用同一个
  token，也可在同池选取不同 token；准确 claim 是两者的并集，不重复计算同一
  引用。每个额外 token 都必须由声明弧解释，超量 claim 会拒绝。Variable 的
  消费 claim 和静态引用不能同时占有同一个 occurrence；合法 `produce`/`edit`
  的 consume-return 行为不变。

仅含静态引用的 transition，在引用可用时可结构启用。每轮 selection 有限地
  枚举一个候选；read 不引入无限本地枚举，也不是一次性开关。

不需要 schema 迁移。复用已有 `claimed_input_refs`、claim delta 的
  `consumed_refs`、marking reference 和 publication/index 结构。Module 准入
  的 consume 索引与局部 consume-return 投影不是同一个概念；普通 read 的
  既有索引语义保留。

## 准入与恢复边界

对声明静态 lease read 的 transition，原 Registry admission transaction 会
  从准确 adopted PN/checkpoint 重建所选 claim，核对 claimed refs 与 version
  index，保留 Module 原 consume-index 约定，拒绝缺失、跨 net、伪造、超量或
  被虚报为 consumed 的引用。Direct 低层准入和 transaction proposal 也经过
  这项检查。

新增检查仅守此静态 read 形状，不代表全部历史低层 invocation 形状已经完成
  全局 claim/index 审计。没有静态 lease read 的合法声明保持原行为。

准确 ref 验证对同一显式选择的 occurrence 检查 enabledness。默认首个 variable
  carrier 不可满足，不应否决另一个可满足的 exact claim；原 count/verdict/timed
  guard 仍保留。默认 owner 调度策略不变，不代表新增 binding 搜索或活性保证。

Selection、准确 claim 重建、capacity preflight、declared-effect 投影和
  Success 验证采用同一静态 lease-read 分类。Registry hydration 与 settled
  state 重建继续使用原准确身份和状态方程检查。

## 动态改网与范围

继续沿原规则：lease/reusable token 不能 reset 或普通 retirement；owner
  replacement 等待活跃 firing 收束；operation net replacement 保留原独占
  active firing 条件。满足这些规则后，本通用能力不会把某个应用前置变成
  永远不可删除。应用特定的 origin 保护属于后续工作。

本修改不实现 child launch、transport 目标占有、bootstrap acceptance、origin
  guard、H7 或物理资源权限策略。这些离线用例通过不等于 H7 或原生 transport
  验收通过。

## 定向验证

`tests/test_static_lease_reads.py`、`tests/test_static_lease_interactions.py`
  和 `tests/test_static_lease_exact_selection.py`
  使用真实临时 Registry，直接执行 admission、Start、products 与 Success。
  覆盖独立消费输入的并行 reader、准确引用负测、真正非空 variable claims、
  普通 read 回归、declared effects、独立 Python 进程冷重建、completion
  recovery 和既有动态改网约束。

在项目已有支持环境中运行：

```sh
python -m pytest -q tests/test_static_lease_reads.py tests/test_static_lease_interactions.py tests/test_static_lease_exact_selection.py
```

这些用例不需要 socket、外部 provider/model、native client 或网络。
