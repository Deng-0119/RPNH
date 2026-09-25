---
name: rpnh-architecture-overview
description: "说明公开 RPNH 的执行与权威架构。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: ARCHITECTURE.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# RPNH Harness 架构

[English](ARCHITECTURE.md)

RPNH 是一个可复用的执行 harness。公开入口是 `rpnh`；stock Codex TUI、basic CLI 和
PetriNet viewer 都是 RPNH-owned 状态之上的界面，而不是彼此独立的执行后端。

```text
stock Codex TUI 0.155.0 ─┐
basic CLI ───────────────┼─> MainSession Registry ─> TaskControl
                         │                            │
provider profile ────────┘                            ├─> independent agent task
                                                      └─> graph workflow task
                                                               │
                                                               v
                                           Harness + typed PetriNet + Registry
                                                               ^
read-only PetriNet CLI/viewer ──────────────────────────────────┘
```

## 权威层次

前端只负责终端交互。每个 Codex conversation thread 一一对应一个 RPNH 主会话
Registry。`MainSession` 负责该持久 thread、turn 顺序、已提交会话历史、选中的
execution profile，以及指向 child task 的 Registry-native 链接。一个前端容器可以
保存多个 thread，但不会合并它们的 Registry。

每个 main turn 的模型执行都运行在自己的受监督 Registry root 中。主 Registry 保存
turn lineage，以及提交 assistant answer 所需的精确 terminal receipt。同样，每个独立
`/agent` 或 workflow 都有单独 Registry root。对这些独立 child，主 Registry 只保存
session-relative path、task kind、可选的来源 committed turn，以及可读取后得到的精确
child task/run 引用；不复制 child events、tokens、workspace revisions 或 results。TaskControl
manifest 仍然只是 process-lifecycle 投影，不是这层关系的权威。

Task owner socket 会在 task control root 及其向上两级祖先中，选择路径最深、实际可用且
满足 Linux `AF_UNIX` 长度限制的位置。通常它仍位于同一 session 树内，不要求源码树
可写，也绝不回退到全局 `/tmp`。

主 Agent 可以直接回复、创建一个独立 single-agent task，或作为 Designer 声明 workflow
图。每个独立 child 都拥有单独进程、Registry root、PetriNet、execution authority 和
owner control channel。关闭或中断主前端并不表示停止 child。

Basic CLI 只保存进程内的 task 焦点。`/switch TASK_ID` 改变后续控制或消息的发送目标；
它不转移 Registry ownership，也不会暂停、重启或停止 child。

## Workflow graph 与 PetriNet lowering

Workflow 声明 nodes、typed input/output ports、一个或多个 ingress/egress ports 和 arcs。
Dependency 子图必须是从入口到出口的 DAG，负责首次激活，包括 fan-out、并行分支和
fan-in/join。用户任务会实体化为 ingress task-resource token。

环不会编码为普通 dependency edge。Feedback arc 是显式的替代 rework route，具有独立
transition 和正数 `max_rework_cycles` 预算。这样首次 dependency 输入与 rework 输入不会
混合，并且每个 loop 都有 Registry 强制执行的停止上界。

通过验证的 graph 会 lowering 为 typed places、transitions、arcs、token claim、firing
admission、execution 和 settlement。进程退出或前端可见消息不能替代 PetriNet 与 Registry
terminal evidence。

## Agent 执行、workspace、资源与委派

已 admission 的 agent firing 使用 Registry-defined tool catalog 运行 agent loop。语义
输出通过声明的 output ports 写入。Tool action、provider attempt、response acceptance 和
settlement 都绑定到精确 firing 与 invocation identity。

每个 firing 获得由当前 Registry workspace revision 派生的私有 workspace view。有界、
禁网的 workspace action 可以创建或修改普通文件。成功 settlement 会把这些文件登记为
版本化资源并推进 workspace lineage，因此后续 firing 可以 materialize 并修改精确的已
登记状态。来自中断且未 settlement action 的文件不会提升为共享 revision。

Workspace shell 是**写入隔离**边界：它只能修改本 firing 的 workspace（以及
`/dev/null`），也不能使用网络 socket。它不是主机文件读取保密边界，因为命令仍需读取
Linux runtime、可执行程序和动态库。Agent 的语义读取仍受 Registry `read_file`／resource
authority 限制。若部署要求文件系统读取保密，须在 harness 外另加 container 或 VM；
harness 不宣称提供该性质。

Workspace finalization 遇到一次已观测的 SQLite I/O 中断时，会立即使用同一个 per-firing
幂等键重试一次；这不会重放 AgentLoop 或 provider 请求。第二次仍失败时保持为
`framework_repair` block，不伪造 terminal 或 final-result evidence；provisional 文件不得
报告为已完成输出。

`request_resource` 使用 Registry resource lifecycle、queue、grant 和 lease authority，
而不是非正式共享路径。等待中的 firing 只有在其 resource grant 成为当前状态后，才会
通过原来精确保留的 dispatcher 继续。

`delegate_leaf` 创建归父 action 所有的有界 child LLM session。Leaf 可以使用允许的原子
工具，但不能递归委派。它的 terminal response 会登记为 resource，并返回同一个父
action。这与 `/agent` 不同：delegated leaf 没有独立进程、owner channel 或可切换的 CLI
task identity。

## 中断、checkpoint、compaction 与恢复

Registry checkpoint 标识持久执行边界。已完成的原子 action 和已 settlement workspace
revision 会保留可见；中断时仍在执行的工作会记录为 interrupted，不会发布为已完成语义
输出。

通过 owner channel 停止 child 会记录 `stopped_by_owner`；resume 使用同一 Registry
lineage，并验证每个持久 transition 仍解析到相同 provider route 和 exact model。如果该
child 是当前 main turn 的执行对象，主 Registry 会保持该 turn active 但 paused。重新打开
主会话只会从 Registry authority 重建 paused 状态，不会自动 dispatch。`/resume` 从最新
checkpoint 续接同一 child Registry。用户执行 `/rollback` 时，只把 main turn 提交为
interrupted，让主会话回到上一个已完成 turn；独立 child Registry 以及所有已索引的独立
task/workflow Registry 均保持不变。

Owner stop 也会传递到当前正在运行的 provider 或 workspace 边界。运行中的进程／连接会被
取消，竞态返回的 products 会被拒绝，当前 semantic action 不会提交。对于 LLM input，
Registry 会在同一事务中把 provider attempt、logical call 和 neutral invocation attempt
关闭为 owner-interrupted，同时保留实际观测到的 submission state。这既不形成 LLM success，
也不形成 LLM failure result；显式 resume 会在同一个 child Registry 中分配新的 physical
attempt identity。

当 agent 触及 response-length 或 context-pressure 边界时，context compaction 是一个已
登记执行步骤。Replacement history 保留 fact capsule 和模型生成的 continuation summary；
replay 仍绑定到被中断的 semantic slot，不会成为新的 user turn。

## Provider 与前端权威

用户生成的 execution profile 固定 adapter kind、provider identity、exact model、route、
limits 和 credential environment names。发送给 provider 的 model identifier 就是已配置的
`model_condition`；失败不会引发静默 provider、route 或 model fallback。

Provider 执行是共享 harness 基础设施，不属于某个前端。明确选择使用该能力的 host
adapter 在 firing 内获得 `registered_llm/v1` capability，并只在此边界转换宿主消息与结果；
它不自行连接 provider、解析凭据、选择其它模型、实现重试策略或建立第二条记账路径。
因此 Codex、托管 DSH 以及未来的 OpenCode 集成都复用同一 selected profile、input port、
物理 attempt 记录和结算规则。接入新宿主只需实现 host adapter/plugin 并声明 operation
binding，不需要重新实现 provider。

默认前端是精确固定的 stock `codex-cli 0.155.0`。RPNH 运行自己的 Unix-socket
compatibility server，只实现明确声明的 protocol methods。Codex `/model` 投影并更新 RPNH
profiles，不会形成第二份 model authority。RPNH 专用 task 命令由
`rpnh --frontend basic` 提供，因为 stock Codex client 拥有自己的 slash-command registry。

## 只读查看

`rpnh net --run RUN_DIR` 从 Registry 构建投影，不取得 writer authority。终端 viewer 和
浏览器页面区分 transition、普通 place 与 resource place。Resource node 和
resource-only edge 默认隐藏，只有在显式请求且 net 实际声明时才会出现。

## 仓库边界

本仓库只包含可复用 harness、schemas、frontend compatibility、provider 配置工具和确定性
离线测试，不包含项目专用 workflow、实验数据、历史 Registry、结果包、用户 profile 或
凭据。

当前运行时仅支持 Linux（包括 WSL2）。不支持原生 Windows 和 macOS，因为执行依赖
Unix domain socket、Linux `/proc`、`fcntl` lock 和 POSIX signal。
