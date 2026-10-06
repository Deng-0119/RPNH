---
name: rpnh-plain-merge-composition
description: "在作者 Branch 和 Assembly/v2 中显式使用 plain merge 结果。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: plain-merge-composition.md
  revision: "2026-10-05.2"
  status: stage-b3-author-chain-candidate
---

[English](plain-merge-composition.md) | [中文](plain-merge-composition_ZH.md)

# Plain merge、作者 Branch 与 Assembly/v2

使用 `plain_merge_assembly_schema_data()` 显式组合既有 merge-result 和 Assembly/v2
catalog。Branch/v1 已由 author-material catalog 继承。重复 schema 与对象类型必须完全
相同；原 `plain_merge_result_schema_data()`、`graph_assembly_schema_data()` 的返回内容
保持原样。该 opt-in 不改变默认 HOST、schema authority 或运行配置。

先按明确的调用者决策生成并完整读取 [plain merge 结果](plain-merge-result_ZH.md) M，
其 exact parents 固定为 `[L,R]`。既有 gateway `advance_author_branch` 可从 head=L
推进到该直接后代，调用者必须固定 expected Branch version、head L、stream sequence。
过期预期会拒绝 Branch publication；M 仍是不可变作者结果，不会 reset 或隐式选择当前 head。

`current_branch` 和 `read_branch_version` 返回 canonical Branch descriptor。把所选
descriptor 的 exact `head_revision_ref` 交给 `validate_closed_revision`，才会完整重建
merge proof。保存的历史 Branch version 仍指向其保存的 head。这些是分别明确的调用，
不宣称 joint snapshot，也不宣称 current head 在调用之间不会变化。重放原 result command
返回 M，不会移动 Branch。

## Assembly 消费精确 merge proof

以 M 的 exact revision ref 创建 `AssemblyMemberV2` 并交给 `AssemblyAuthorV2`。
明确选择 completion member 和其 primary terminal 元素 ID，使用既有 `shared_exact`
budget policy 与 `same_run_candidate` deployment intent。这只是作者编译与发布；
deployment intent 不代表运行 adoption。

既有 `plain_closed_v1` member resolution 固定 M 的 definition、element map、boundary
map、HOST requirements 四 refs。full consumer 遍历 M 的显式 merge marker、exact analysis、
调用者决策、完整 command 与实际材料字节。`validate_assembly_revision` 再重建 member
Module、generated revision、compiled inventory 和 lowering origins。仅有可读 M descriptor
不能充当 Assembly member proof。

Assembly 生成的 paired revision 仍使用原 closed-author 协议与严格的
`ValidatedClosedRevision` 结果类型。成员是 merge proof，不意味着可以用其它 subclass
替换 paired generated revision，也不放宽既有 pair、carrier、constraints 或来源检查。

## 每个读切面独立的私有验证上下文

Assembly/v1、Assembly/v2 的内部 parent、member、generated-proof reader 在同一 Registry
读事务中传递外层 active 路径；当前 Assembly 身份一直 active 到其完整证明结束。
sibling members 共享外层路径，但不把已完成 sibling 放进全局 seen，因此同一 exact member
出现两次仍可合法验证。

producer preflight 和 final validation 各自在自己的事务中创建上下文。最终检查重新读取
选定 parent 与 member proof，不把早先事务的验证结果当作最终 authority。
递归 member 路径内部不重开 public reader，也不引入全局可变 proof cache。

## merge 之后的普通单父编辑

既有 `ClosedModuleAuthor.publish` 可把完整 merge proof M 作为 exact `parent_ref`。
调用者提供新的 closed Module、完整稳定元素 ID，以及明确的 `copy_sources`。
普通编辑 E 保留原 `closed_author_command/v1` marker 与确定性身份规则，只有单父 `[M]`，
不会变成 merge-result marker 或双父结果。

保留元素沿用 ID。复制元素必须有新 ID，并指定 M 中的同 kind 元素；其
`copied_from.revision_ref` 指向 M，不跳回早先 merge 输入。把保留 ID 标成复制仍属非法。
完整读取 E 时，会经既有递归 parent 路径完整验证 M。

作者可继续用同样的三项明确预期推进 Branch M→E。保存的历史 Branch version 仍指向 M；
current 与历史 head 都可按各自 exact ref 完整读取。Assembly/v2 可把 E 作为
`plain_closed_v1` member，重建 generated pair、inventory 和 origins。重放 M 的原 merge
command 仍返回 M，不回退当前 Branch head，也不新增事实。

该 B3 候选以既有 producer/reader 验证这一后续路径，不增加作者协议或策略。
Assembly/v3 集成及共享调用点另行处理；此 catalog 不会让其它 opt-in 隐式获得 merge 支持。
原 plain 小片的排除项、明确未解决的 order，以及不兼容历史 HOST 输入限制继续适用。
