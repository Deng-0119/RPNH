---
name: rpnh-worksets
description: "显式本地 Workset 接纳与只读证据。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: worksets.md
  revision: "2026-10-09.2"
  status: accepted-finite-browser-blocked
  basis: "finite local ordinary PN; optional collaboration inventory"
---

[English](worksets.md) | [中文](worksets_ZH.md)

# 本地 Workset 与接纳

这是显式启用的本地适配器，由调用方选择来源 Registry。请求、export、逻辑交付保留 source-qualified 身份；物理 prepare、release、consume 与终态复用原 ResourceService。没有网络传输或 executor 派发。

WorksetOwner 明确登记要求、输入、generation 与预期贡献槽。growth 和 seal 在原 EventStore BEGIN IMMEDIATE 内比较期望前版、stream head 与 command。seal 冻结预期集合，仍允许其中结果到达；已完成集合不能被迟到结果重开。`grow` 仅支持仍为 open 且没有 acceptance 或 contribution 的集合，并且必须严格扩大预期槽集合；已有结果后的 growth 会被拒绝。

原 RunOwner.succeed 的可选 typed action 在同一原 Success 提交中登记 Acceptance 与真实本地 PN deposited occurrence。显式启用的强提交门重放原纯 PN 投影、durable Start 与真实已登记 products 发布（若已有 dispatcher return 记录也必须核对）。Contribution 必须消费已接纳 occurrence；root 完成必须消费已 seal 的全部贡献，并产出真实编译 terminal。RunOwner.terminal 随后给出独立真实 run-terminal 证据。Workset seal 不代替 required-child closure。

同 source-qualified logical delivery 加目标 Workset/collection/input/slot，同内容返回原 Acceptance/occurrence，异内容冲突。目标已接纳时 P1 仍可 unknown；显式新命令 P2 合法重传后可 ACK 同一 Acceptance。独立 reconciliation 保留各原物理终态，从不覆写 unknown。不能只按 bytes 合并业务身份。

物理命令使用稳定 attempt/release 身份。同会话重放命令返回已消费 handle，不重复 release。重开后 canonical 记录仍在，但丢失 opaque channel 不能重建或再次 consume，原 ResourceService 报 DeliveryOutcomeUnknown；调用方需核对旧尝试并显式选择新物理命令。

Viewer 的 Worksets 按钮与 GET /api/v2/worksets 共用只读 DTO，当前业务集合与不可变历史分开。未显式提供完整来源的只读交付历史时，物理总数为 null；逐源 cut 独立，不是全局快照。cancel、stop、required-child seal 缺实际 producer 时明确 not_provided。不返回原始结果 payload。

本地接纳路径要求真实已登记的 operation outputs，且所选 outcome 必须无 effects。
接纳 decision 只支持 `new` 或 `carry`；carry 由调用方选择，仍建立目标专属接纳。
该适配器不选择适用性、不调度 executor、不提供远程 transport，也不授予来源 Registry
访问权限。revalidate、recompute 与 retirement 不是支持的 Workset action。
不支持任意 child/effect 完成；另行实现的[normal-child 合同](normal-child-root-contract_ZH.md)
规定了显式 child-completion 路径。

## 创建并 seal 预期集合

使用已有 `RunOwner`，其 catalog 应包含 `workset_schema_data()`，并已通过
`schema_gateway.bind_source_identity(...)` 建立本地来源身份。传入精确的
source-qualified 已登记 requirements 与 input refs。下列函数创建并 seal 单槽集合，
不产生接纳、贡献或 run-terminal 证据；新集合使用新的 `command_prefix`。

```python
from cpn.rpnh.collaboration import WorksetOwner, WorksetExpectation


def create_sealed_workset(owner, requirements_ref, input_binding_ref,
                          command_prefix):
    worksets = WorksetOwner(owner)
    created = worksets.create(
        requirements_ref=requirements_ref,
        input_binding_ref=input_binding_ref,
        generation=1,
        expected_slots=["result"],
        command_id=f"{command_prefix}:create",
    )
    expected = WorksetExpectation.from_record(worksets.core, created)
    return worksets.change(
        expected, action="seal", command_id=f"{command_prefix}:seal",
    )
```

本地交付通过 `bind_local_workset_source(target_owner, source_owner, source_id=...)`
绑定精确 source owner，重开后重新绑定。首次 `accept_delivery(...)` 必须提供真实
已登记的 `outputs` 与 `output_port`；重放可读取既有接纳而不启动另一 firing。
seal 后仍可接收预期结果。

面板分别展示 A/O/C 精确关联。接纳 occurrence 总数保留历史；当前可用性在同一读 cut 内从当前 RunAuthority/checkpoint 得出。有活跃 firing claim 时明确 not_provided，不把 marking 成员关系当作使用许可。贡献及根完成后，原接纳 O 仍保留历史，但不在当前 marking 中。

历史 HTTP/Node 与 mock-DOM 检查通过；记录中的真实 browser 启动因 root/sandbox
BLOCKED。静态接纳 fixture 使用 mock product bytes 及拒绝执行的 callback，不能证明
任意 child/effect 支持。见[有限验证](../guides/release-validation_ZH.md)。
上例为语法检查的文档代码，不是已经执行的任务结果。
