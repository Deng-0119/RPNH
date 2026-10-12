---
name: rpnh-versus-codex
description: "说明 RPNH 权威与 Codex 前端之间的边界。"
metadata:
  document-kind: concept
  audience: operator-and-developer
  language: zh-CN
  counterpart: RPNH_VS_CODEX.md
  revision: "2026-10-12.4"
  status: source-reviewed-pre-release
---

# RPNH 与 Codex 的区别

[English](RPNH_VS_CODEX.md)

RPNH 不是 Codex 的换皮客户端，也不是另一个 OpenAI 模型客户端。当前实现复用固定版
`codex-cli 0.155.0` 的终端界面，但会把它连接到 RPNH-owned 后端。Codex 提供一种前端；
RPNH 提供 harness、执行模型与证据权威。

| 维度 | 直接运行 `codex` | 运行 `rpnh` |
|---|---|---|
| 终端界面 | Codex TUI | 同一固定版本 TUI、内置 Basic 或固定版 OpenCode 展示 |
| 后端状态 | Codex/OpenAI thread 与执行状态 | 每个 conversation thread 一个 RPNH main-session Registry；独立 child Registry 与 typed PetriNet 状态 |
| Provider/model | Codex 账户与配置 | 用户所有的 RPNH profile，固定 provider、route 和 exact model |
| 普通输入 | Codex thread/turn | 受监督的 single-agent RPNH main turn |
| 独立工作 | Codex threads、tools 或 agent facilities | 进程隔离的 single-agent 或 graph-workflow task 对象 |
| 任务切换 | Codex 前端语义 | `/switch` 改变 basic CLI 焦点，不停止后台任务 |
| Workflow | Codex 后端行为 | Designer-authored typed graph lowering 为 PetriNet |
| Subagent | 由 Codex 实现决定 | 父级所有的有界 `delegate_leaf`，结果返回一个父 action |
| Workspace | Codex workspace/tool 语义 | 私有 firing view；settlement 时发布 Registry 版本化文件和逐路径 delta |
| 调度 | Codex 后端内部机制 | Petri enablement、admission、firing、resource 与 settlement |
| 完成判定 | 可见 model/session 输出 | Registry terminal evidence 加 final-result index |
| 中断／恢复 | Codex session 行为 | Registry checkpoint、owner stop、精确 lineage resume 与 owner 选择的 checkpoint reopen |
| 查看 | Codex logs/session | task/net/token/firing/provider/resource 的只读投影 |

## 四种执行身份

RPNH 明确区分下列身份，因为它们的 ownership 与恢复行为不同：

| 身份 | 创建方式 | Ownership | 能否独立切换 |
|---|---|---|---|
| Main turn | 主会话普通输入 | Main-session thread 与 turn Registry | 用 `/switch main` 返回 |
| Single-agent task | `/agent PROMPT` 或通过验证的 main decision | 独立进程、Registry、PetriNet 和 owner channel | 可以，按 task ID |
| Graph workflow | `/workflow PROMPT` 或通过验证的 Designer decision | 独立进程与 graph/PetriNet task authority | 可以，按 task ID |
| Delegated leaf | 父 Agent 的 `delegate_leaf` tool | 一个父 action 及其 Registry invocation lineage | 不可以；结果返回父级 |

Workflow 不是预装的项目 pipeline。Designer 为当前任务声明一个新 graph，harness 对其验证
并 lowering。Single-agent task 也是真实的单 transition PetriNet task，不是 main thread
的别名。

Registry 边界跟随执行 ownership。一个 Codex conversation thread 对应一个主会话
Registry 的直接 root。Codex 退出后，Basic 或 OpenCode 可重开完全相同的 root，不复制
Registry；共享 owner lease 会阻止并发可写前端。每个可独立切换的 agent 或 workflow
都有自己的 Registry。主 Registry 只包含
相对链接／索引、可选的来源 turn，以及 child Registry 可读取后的精确 task/run 引用；
不吸收 child 的 event history、tokens、workspace 或 final-result authority。

## 用户可见的行为差异

1. `rpnh` 不把 Codex model selection 当作第二份真相。Codex `/model` 列出并更新从 RPNH
   provider catalog 生成的 profiles；同一选择也可通过 `rpnh config use PROFILE` 或
   `rpnh config use PROVIDER MODEL` 完成。运行中的 turn 不能更换 route。
2. Codex TUI 只暴露 RPNH 已实现的 protocol methods。Permission 与 sandbox value 是固定
   RPNH 执行边界上的 compatibility projection。RPNH 专用 `/agent`、`/workflow`、
   `/switch`、`/task` 和 `/net` 属于 `rpnh --frontend basic`；不支持的 Codex-only 命令
   不会被宣传为 harness 能力。
3. 主会话在逻辑上是一个 Agent。执行 graph 工作时，该 Agent 承担 Designer，声明
   ingress、egress、typed ports 和 arcs。Dependency arcs 构成首次入口到出口 DAG；显式
   feedback arcs lowering 为独立 rework transitions，并消耗共享 `max_rework_cycles` 预算。
4. workflow 的初始 ingress resource token 先逐字保留原始用户任务，再附加带明确标签的
   Designer 补充说明。补充说明不能替换或摘要掉原始任务；执行职责由 node instruction
   承担。普通 untyped cycle 会被拒绝，因为它会在没有显式输出 route 与停止上界的情况下
   混合首次激活与 feedback 激活。
5. Workspace 文件只有在 Registry 成功 settlement 后才成为共享状态。后续 Agent 能看到
   已登记 revision 并继续修改；中断且未 settlement 的写入不会被提升。这样既保留顺序
   node 间的 provenance，也隔离并发 firing view。
6. Worker 进程结束不等于成功。Task 完成必须同时有 Registry terminal evidence 和 final
   result。前端与 task-control 命令会分别报告 process status 和 Registry status。
7. 主会话历史只保存完整的 Registry-terminal answer。发生中断时，已 settlement action
   持久保留，未完成 semantic action 失效，turn 停在 checkpoint 的 paused 状态。
   `rpnh --resume SESSION_DIR --frontend basic` 会重建该 paused 状态，但不会自动执行。之后
   由用户选择 `/resume` 续接同一 child Registry lineage，或用 `/rollback` 让主会话回到
   上一个已完成 turn。Rollback 绝不删除或回退 child Registry。
8. 独立 task 的行为类似后台 Agent。退出或中断主会话不会停止它们。`/task ID stop` 请求
   checkpointed owner stop；`/task ID resume` 从最新满足条件的 Registry 切面继续。
   `/task ID reopen CHECKPOINT [:: REASON]` 是另一项操作：它从精确已提交切面追加新执行
   代次，同时保留全部后续历史和当前 attempt 高水位。
9. `rpnh net` 与浏览器 viewer 在不取得 writer authority 的情况下读取已有 run。普通 graph
   node 默认可见；实际 resource node 只有通过 `--show-resources`、`--resources-only` 或
   页面开关才会显示。
10. Provider credential 不会交给 Codex TUI，其值也不会存入 RPNH catalog。外部 route 只
    读取用户 profile 指定名称的环境变量。

## 当前边界

- 运行时仅支持 Linux（包括 WSL2），不支持原生 Windows 和 macOS；
- Codex 前端只支持精确的 `codex-cli 0.155.0`，不暗示兼容其他版本；
- RPNH 在当前 RPNH session 内持久化 thread state 并支持 `thread/resume`；不导入
  Codex-owned history，也不会自动恢复 paused firing。Main-turn `/resume` 与 `/rollback`
  是 basic 前端中的显式用户控制，并要求存在 clean Registry checkpoint；
- Codex TUI 主要提供 main-session 输入/输出；完整 task controls 和自定义 slash commands
  位于 `rpnh --frontend basic` 与 `rpnh net`；
- RPNH 不启动 Codex app-server 或 Codex backend；model call 只使用选中的 RPNH execution
  profile；
- RPNH 自带空 provider/model catalog，不包含项目专用 workflow。Provider 配置与任务专用
  graph 设计都属于用户；
- 独立仓库是通用 harness 的权威；历史实验 workspace 不是运行时依赖。

## 从示例检查权威边界

当前协作入口是[Agent 任务网共建](../examples/user_collaboration/agent_cobuild/README_ZH.md)。`curator.run` 与 `auditor.run` 两个原生任务 agent 使用 stock `AgentStage`／default `AgentLoop`，由两份 stock 单 agent 声明组成同一普通 Module，连接为 `curator.result → auditor.request`。两个受控原 principal 的确定性 source 程序分别加强 curator 输入来源合同和 auditor 对账合同。原 HOST 审阅精确 revision、连续推进受保护 Branch，再由 owner 在 target 执行前采用。

source 程序、网外控制动作与 target agent 分层记录；单 parent 链不表示同 base 自动合并。`local_process` 脚本驱动从实际输入与工具返回计算，以已登记任务 agent 展示结构／协议，不声称真实模型调用或认知能力；运行结果、产物与 Viewer 证据以示例页记录为准。

[模拟审批](../examples/approval_workflow/README_ZH.md)和[外部结果接入报价](../examples/a2a_quote/README_ZH.md)是原生输入／权限 fixture，不证明真实 A2A／OID4VP 互操作。[历史路线](../examples/user_collaboration/README_ZH.md#历史路线规划机制证据)与[原生电费](../examples/user_collaboration/native_billing/README_ZH.md)保留原业务工具网、版本、结果、图像与失败；它们的业务操作和受控作者不计为任务 agent，也不验证当前示例。

Overview 是只读 agent 投影，不是简化后可执行 PN。`registered_agent_binding` 可追到精确 executable binding 与 agent 引用；`declared_llm_executor` 仅确认声明，不证明执行；`explicit_host_annotation` 是未确认显示注释，不建立 agent 身份。角色按显式 scoped identity 分组，不能凭同名、模型名或用户账号合并。边保留原路径依据，表示通往下一 agent 的结构路径，不证明已发生顺序、guard 通过或全输入 join 满足。完整 PetriNet 与所属 Registry 保留权限、guard、join 以及实际 loop／turn／action、工具和产物引用。managed child／action 不计为新增 PN transition，独立 child Registry 保持独立。

贡献配色表示记录中的提议者，采用另行展示，不认证作者或赋予 baseline 归属。接口增加观察能力，执行权威仍在原系统。[技术报告](technical-report_ZH.md#agent-任务网与只读投影)说明分层与历史限制。
