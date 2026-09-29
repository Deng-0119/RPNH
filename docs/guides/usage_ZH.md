---
name: rpnh-use-and-resume
description: "Operate main sessions, independent tasks and read-only net projections."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: usage.md
  revision: "2026-09-28.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](usage.md) | [中文](usage_ZH.md)

# 会话、任务、工作流与观察

## 执行前
安装对应范围并选择获得授权的精确 profile。**对话、`/agent`、`/workflow`、`/resume`、`--prompt` 都可能执行模型或工具**，不是安装 smoke。新会话使用新目录，保留返回的任务 ID，运行数据保留在私有位置。

以 `rpnh --frontend basic` 显式启动文本前端。`--execution PATH` 指定执行配置，`--session-dir PATH` 指向尚不存在的新会话目录；后者与 `--resume SESSION_DIR` 互斥。`--prompt TEXT` 执行主会话一轮，不是无害的回显命令。
新会话省略 `--execution` 时，使用 `RPNH_EXECUTION_CONFIG` 或用户保存的默认值；
`--resume` 省略该参数时则使用该会话持久化的精确 profile。恢复时显式提供
`--execution` 只用于相等性断言，不能替换已持久化的 provider／exact-model 身份。

Basic、Codex 与 OpenCode 使用同一个直接 `SESSION_DIR` 契约。当前前端退出后，可用
三者中的任意一个重开完全相同的 root；后端只有一个 MainSession Registry，不会为每个
前端复制会话。共享 owner lease 会拒绝并发的可写前端。重开、列出与历史投影不会调用
模型，也不会补偿启动 child。

## 主会话与独立任务
以下是 **basic 前端**命令，不是给 stock Codex 新增的 slash 命令。

| 命令 | 含义 |
|---|---|
| `/tasks`、`/current` | 列出独立子任务、查看当前焦点 |
| `/agent PROMPT` | 创建并选中独立单 agent 任务 |
| `/workflow PROMPT` | 请求主 Designer 生成图工作流；设计无效不算已启动 |
| `/switch ID`、`/switch main` | 只切换焦点，不停止或重启子任务 |
| `/task ID status`、`/task ID result` | 读取进程/Registry 状态或已注册结果 |
| `/task ID message TEXT` | 向单 agent 任务排队投递输入 |
| `/task ID message TARGET :: TEXT` | 明确工作流接收目标 |
| `/task ID stop`、`/task ID resume` | 请求 checkpoint 停止、续接当前 owner-stopped 切面 |
| `/task ID checkpoints` | 列出同一 run 中可选择的精确已提交 checkpoint 版本 |
| `/task ID reopen CHECKPOINT [:: REASON]` | 从所选切面追加新执行代次，并让恢复后的 agent 看见可选 owner 指引；可能执行模型或工具 |
| `/quit` | 离开主前端；独立子任务可能继续运行 |

简写 `/status`、`/result`、`/net`、`/message`、`/stop` 作用于选中子任务。普通文本发给主会话或选中单 agent；选中工作流时必须写 `TARGET :: TEXT`。`delegate_leaf` 属于父 agent 内部 action，不是可独立切换的任务。

`resume` 与 `reopen` 的语义不同。`resume` 续接当前 `stopped_by_owner`
checkpoint；`reopen` 接受 `checkpoints` 返回的精确 ID，在同一 Registry／同一 run
中恢复该切面的 Petri marking 与 workspace，并追加新的不可变执行代次。它不会删除
后续历史，也不会创建替代任务。当前 child 仍在运行时应先停止，再选择旧切面。若所选
切面已经 terminal，新代次不会再次调用模型而是直接闭合。若 worker 已退出，且某次
物理调用被登记为 `submission_unknown`，`reopen` 同时表示 owner 明确放弃该未决 firing，
并从所选安全切面创建新的 attempt。原 unknown attempt 仍作为不可变证据保留，系统绝不
自动重发它。

需要纠正任务时可追加 ` :: REASON`。owner 原因会写入 reopen authorization，
并作为该执行代次内 agent 可见的 system 指令；省略时保留中性的默认原因。

## 主回合中断
使用 `rpnh --frontend basic --resume SESSION_DIR` 重开。未显式提供 `--execution`
时，该命令使用会话持久化的精确 profile，而不是当前环境变量或默认 profile。打开时只
观察权威状态，不自动提交 terminal child，也不补偿 committed launch。选中 main 后，
`/resume` 会显式提交已有 terminal evidence，或继续暂停回合；`/rollback` 回到主对话
上一个已完成回合。rollback 保留暂停子 Registry，不回退独立子任务，也不补偿外部写入。
选中子任务时 `/rollback` 会被拒绝。尚未解决的活跃回合不能直接接受新输入。

## 不执行模型地检查已有 run
传入真实 run 目录，而不是聊天记录或任意 session 索引：

```bash
: "${RUN_DIR:?Set an existing run directory}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --format json
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

最后一条会启动只读本地 viewer 服务，默认 `127.0.0.1` 和自动分配端口。它不是模型调用，但确实创建进程/网络监听；保持 loopback。`--view` 不能组合 `--resources-only`、`--node`、`--output` 或非默认 `--format`。`--output PATH` 写投影文件，不写 Registry。

资源 place 默认隐藏，只有真实声明才显示；没有资源节点的 run，其 resources-only 为空是有效结果。`/task ID net view` 或选中后的 `/net view` 使用同一观察者。图可读、`enabled_transitions` 或界面有答案，都不能证明全局活性或终态成功。

## 预期证据与恢复
成功依据是子任务权威中的 terminal evidence 与 registered result，不是进程退出、UI 缓存答案或队列为空。一致性备份应保留主/子目录关系，相对 Registry 链接不是子事件库副本。不要为了修复展示启动第二个 writer。owner 冲突、配置漂移和未知结果见[排障](troubleshooting_ZH.md)。

代码：`cpn/rpnh_cli.py:_task_command`、`_run_task_action`、`_net_command`，`cpn/rpnh/main_session.py`、`task_control.py`、`inspection.py`、`registry/main_thread.py`。
