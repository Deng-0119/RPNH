---
name: rpnh-user-collaboration
description: "通过可信本地 HOST 接入用户，完成精确提案审核、受保护 Branch 接纳与 owner adoption。"
metadata:
  document-kind: guide
  audience: trusted-host-developer
  language: zh-CN
  counterpart: user-collaboration.md
  revision: "2026-10-12.4"
  status: maintained
  basis: "trusted local HOST; plain author revisions; original Registry authority"
---

[English](user-collaboration.md) | [中文](user-collaboration_ZH.md)

# 受控本地用户协作

`cpn.rpnh.collaboration` 中的 `UserCollaborationHost` 将本地参与者接入受保护的
plain-author Branch。目标 owner 邀请贡献者、审核精确提案、接纳修订，再单独决定是否
将其采用到运行中的任务。发布、执行与恢复仍由原 Registry 和 owner 负责。

## Agent 合同共建入口

当前协作入口是[Agent 任务网共建](../../examples/user_collaboration/agent_cobuild/README_ZH.md)。`curator.run` 与 `auditor.run` 两个原生任务 agent 使用 stock `AgentStage`／default `AgentLoop`，由两份 stock 单 agent 声明组成同一普通 Module，连接为 `curator.result → auditor.request`。两个受控原 principal 的确定性 source 程序分别加强 curator 输入来源合同和 auditor 对账合同。原 HOST 审阅精确 revision、连续推进受保护 Branch，再由 owner 在 target 执行前采用。

source 程序、网外控制动作与 target agent 分层记录；单 parent 链不表示同 base 自动合并。`local_process` 脚本驱动从实际输入与工具返回计算，以已登记任务 agent 展示结构／协议，不声称真实模型调用或认知能力；运行结果、产物与 Viewer 证据以示例页记录为准。

source 程序不是作者模型 agent，HOST review／advance／adopt 是网外控制，不冒充 target PN firing。历史路线和电费 fixture 修改业务策略／阈值，不能用其业务节点数或 principal 数量代替任务 agent 数量。显示层不授予工具权限或采用权。

示例使用两份 stock 单 agent 声明组装普通 Module，保留既有 plain-proposal 约束、Registry 门、adapter 与调度器。

## 建立可信 HOST 边界

使用目标现有的 owner Core 及其原始 `RegistryRegistrationGateway` 构造
`UserCollaborationHost(core, gateway)`。将显式的 `user_collaboration_schema_data()`
清单组合到 HOST 所选 catalog；这不会改变默认 catalog，也不授予权限。

每个参与者 session 都从其拥有的原始 Registry、source binding、task 和精确
`principal/v1` 派生。参与者应各自通过自己的原始 Registry 与 principal 接入。显示名或 JSON 中的
actor 字段不能建立该身份。Session 与 action handle 绑定目标 EventStore 实例；
`resume_session` 重建同一个原始身份绑定。Handle 与 Core 必须留在可信 HOST 内，
不暴露给模型工具或插件。这是本地接入边界，不提供外部账号认证，也不隔离同一 OS
用户运行的其他代码。

以下是部分方法签名，所需 typed reference 由 HOST 从已有对象取得：

```text
create_session(*, origin_core, principal_ref, command_id)
protect(session, *, branch_ref, runtime_net_ref=None, command_id)
join(session, invitation_ref, *, command_id)
review(session, proposal_ref, *, disposition, reason='', command_id)
```

这些签名用于说明接口，不是独立 bootstrap 或可运行案例。

## 邀请与提交

1. 目标 owner 调用 `protect`，传入精确的 plain `collaboration_branch/v1`。
   对已有 run，将其当前 typed `net_instance/v1` 作为 `runtime_net_ref` 传入。
   保护记录是不可变的 task 级 project；错误目标类型会在 project 发布前被拒绝。
2. Owner 针对贡献者的精确 session 调用 `invite`，指定 `propose` 角色；贡献者用该
   精确 invitation 调用 `join`。
3. `submit_proposal` 绑定来源 TEXT、provenance、完整 Module payload、目标本地的
   candidate revision、base、预期 Branch 与预期 head。Candidate 必须派生自该精确
   base。修订提案通过 `previous_proposal_ref` 创建新 fact，不改写旧提案。

接收已完成执行的输出时，HOST 使用 `ingest_completed_text` 与
`delivered_proposal_inputs`。来源 owner 路径从原始 run、invocation 和 firing 记录
重新核对成功的 stock TEXT 产物。独立 execution 通过 `bind_execution` 在首次 invocation
前显式绑定。目标中的副本以目标为 publisher；delivery fact 保留原 producer。
TEXT 传递不会自动推导导入的 author revision，candidate 仍走目标本地的 authoring 路径。

## 审核与接纳精确修订

Owner 使用 `review` 作出 `approve` 或 `request_changes`；具备提案资格的贡献者可以
留下 `comment`。批准覆盖完整提案，包括 provenance、candidate 和 Branch 预期。

Owner 的 `decide(..., operation='advance', ...)` 指定精确的 proposal、approval、
result revision、Branch 和 head。`action` 创建可信 action handle，再用 `command`
将其绑定到原 gateway 的 `advance_author_branch` 及该操作的精确 idempotency key。
Decision 本身不会推进 Branch。

原始 commit 边界会再次检查当前 owner、每位必需贡献者的提案资格、当前精确批准、
Branch 预期与结果。单提案只能接纳其精确 candidate。双父 plain merge 结果 M 必须携带
原始且经验证的 merge proof，使用预期共同 base，并覆盖每个尚未接纳的直接输入及其批准。
名称或只有 descriptor 的 ancestry 不足以成立。

Branch successor 与协作 receipt 原子提交。`receipt_ref` 使用真实接纳事务的
idempotency key 定位 receipt。受保护 Branch 的别名、普通 adoption 路径和 owner edit
不能通过省略 HOST action 绕过检查。

## 经原 owner 采用到运行中

Runtime adoption 是单独的 owner decision：`operation='adopt'`，并指定已接纳的
Branch receipt、精确 accepted revision、当前 Branch/runtime 预期。将其 action 作为
`collaboration_action` 传给现有 `owner.control.edits.submit` 路径。原有 replacement
准备、PN/marking 检查和 owner 准入规则继续适用；Branch 接纳本身不改变运行中的网。
参见[原生网操作](net-operations_ZH.md)。

贡献者之后被撤销资格，不会撤回已接纳的 Branch，也不会阻止当前 owner 采用该精确的
已接纳修订。Adoption 仍检查当前 owner、accepted receipt 和 Branch/runtime 预期。
精确 replay 保留 actor 与 action 身份，不产生新的接纳效果，并须通过当前 read fence。

## 将读取权限单独管理

`revoke` 改变贡献资格。`issue_read_access` 与 `revoke_read_access` 沿用原始 observer
grant/profile 机制，管理另一类权限。Join 本身不签发读取 grant，`read` 角色也不例外。
非 owner 调用 `read_fact` 或 `read_view` 前，通过 `bind_read_authority` 绑定已授权
observer context。这些方法要求完整的协作 record scope，并返回协作 fact；PN material 仍由原 scoped reader 读取。
参见 [Registry 读会话](../reference/registry-read-sessions_ZH.md)。

检查 plain merge 时，在已授权的 fixed-cut reader 中选择真实的 M revision。
Reader 根据精确 proof material 验证已存投影；与 parent 比较使用 `full_pair`，
merge correspondence 和 runtime continuity 保持 `not_provided`。读取限额仍适用：
投影不可用或过大时，不会截断后宣称成功，也不会回退为只有拓扑的结果。
参见[独立精确网比较](comparison-context_ZH.md)。

## 源码入口

- `cpn/rpnh/collaboration/user_collaboration.py`：HOST 方法与精确 handle
- `cpn/rpnh/registry/_event_store/user_collaboration.py`：原始 commit guard
- `cpn/rpnh/registry/user_collaboration_source.py`：成功 TEXT 的 provenance
- `cpn/rpnh/registry/_event_store/user_collaboration_merge.py`：原始 merge proof

Authoring 与 merge 契约见[普通作者合并结果](../reference/plain-merge-result_ZH.md)。
