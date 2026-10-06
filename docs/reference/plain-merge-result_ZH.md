---
name: rpnh-plain-merge-result
description: "显式调用者决策及不可变 closed Module 合并证明。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: plain-merge-result.md
  revision: "2026-10-05.1"
  status: stage-b1-result-candidate
---

[English](plain-merge-result.md) | [中文](plain-merge-result_ZH.md)

# Plain Module 合并结果（Stage B1）

显式组合 `plain_merge_result_schema_data()`，配置
`PlainModuleMergeAuthor(gateway, registration, producer_principal_ref)`，再以
`publish(analysis_ref=..., choices=..., command_id=...)` 消费已保存的精确
[analysis](plain-merge-analysis_ZH.md)。此候选实现验证调用者决策、重建作者 Module，
然后产生一个 closed `NetRevision/v1`。它不推进 Branch，也不调用运行或业务操作。
Branch CAS、Assembly 消费以及合并后的普通单父编辑仍分别属于 B2/B3 验收。

## 显式决策

每个已保存 conflict ID 必须对应唯一 choice，包含 `conflict_id`、`choice`、
`reason`、`delete_element_ids`。`choice` 只能是 `local`、`incoming`、`base` 或
`delete`；`reason` 是调用者给出的非空说明。前三种选择的删除列表必须为空；
delete 必须明确提供非空作者稳定元素 ID 列表。没有冲突时必须传 `choices=[]`。

每项决策选择分析中完整冲突范围的 atoms。重叠决策在共同 atom 上必须选择类型敏感、
presence 与 value 均相同的状态；不同标签在实际状态相同时可以兼容。
矛盾、重复、缺失、未知或过期的选择在写入结果 command 前拒绝。
不会从 reason、编译成功或父版本偏好推断业务答案。无冲突 atoms 保留精确三方变化。

delete 仅移除明确命名的完整作者元素及被命名 component 的结构自有子项。
子项所有权发生移动时需要明确选择子项。module root 和 HOST selection 不能删除。
删除 operation 不暗中删除 component、边界、消费者、link 或反馈；保留的业务引用必须有效。
容器顺序可以机械去掉已删除 ID；若仍存活的成员缺失，或现有顺序无法表达两侧保留的新增项，
则报 `UnresolvedPlainMerge`，不会自行排序、拼接或删除来修补。自定义顺序属于后续能力。

稳定引用按选中的 parent 和名称重建。operation 的 port 引用，以及 terminal 的 source
与 operation 必须仍处于同一选中 component。另一个 component 的同名 port 不能静默重绑定。
除明确删除的结构记录和未使用 HOST 声明裁剪外，重建后归一化 atoms 必须完全往返一致。
继承元素保留稳定 ID；merge 继承不记录成从某一 parent 普通复制。

## 首份 command 与不可变结果

只有具备唯一共同 base 的 `analyzed` 输入可以产生结果。L/R 为同一 exact head 时，
在任何结果写入前拒绝。结果 parents 固定为有序 `[L,R]`，`selected_change_refs` 为空，
base 不额外成为 parent。结果沿用共同谱系 logical ID，并在独立
`collaboration-plain-merge-result:` command 域中确定 version ID。

首个 durable 资源是 `plain_merge_author_command/v1`，固定 source、owner、producer、
原始 choices 与理由、exact analysis/analysis-command refs、算法、结果身份和 parents。
它同时固定五个未来资源：resolution、definition、element map、boundary map 和 HOST
requirements。每项包含确定性 ref、schema ID、完整文档、含选中 schema authority 的完整
metadata，以及 canonical payload digest；command 还记录实际 schema authority 字节 digest。
字节相同的另一个 authority 也不能替换首份已选中的 exact ref。

后续依次写入 `plain_merge_resolution/v1`、四份作者材料、最终结果 revision。
durable 的部分 command 是恢复证据，不代表结果已成功。相同请求可继续未完成写入；
更改请求、决策、选中 authority、metadata 或未来字节则与首份 command 冲突。
最终 revision commit 是结果发布点，重放返回同一证明。

## 强 full reader 与兼容性

`validate_closed_revision(core, result_ref, registration)` 按 definition 中显式的
`plain_merge_author_command_v1` marker 分派，并在同一读事务内重新计算 analysis、决策、
稳定 ID 重建与实际作者编译。source binding、task、native run、bootstrap 和配置的 producer
使用局部 exact authority 核验。嵌套 revision/proof 验证共享循环跟踪，不使用全局可变证明缓存。

reader 比较 exact command 和未来资源 refs、原始请求、结果 descriptor、完整 metadata、
选中 schema authority 以及实际 canonical payload 字节。仅解析 JSON 后相等不够：
即使同长度的键顺序改写也不是固定 payload。HOST 输出只包含重建编译实际消费的精确选中声明。
schema 检查、descriptor 读取或编译成功本身均不构成结果证明。

旧 `closed_author_command/v1` wire 及 root/单父语义保持原样，仍允许无关的 scalar
descriptor metadata。未知或双 author-command 协议 marker 明确拒绝，不按 parent 数量猜协议。
后续 analysis 遍历完整验证的 merge 输入时使用独立的 merge 身份合同；原始未证明多父
descriptor 仍不合法。

此片要求一个可信 Registration 能够重建全部选中输入。同 key 的不兼容历史 HOST 版本
目前在输入验证阶段拒绝；持久化并显式解决此类 HOST 冲突仍是后续能力。
graph 作者、Assembly 生成或带 opaque constraints 的输入不属于此 plain 小片。
保留有限精确输入与循环拒绝，不新增任意产品大小默认值，也不宣称硬内存上限。
