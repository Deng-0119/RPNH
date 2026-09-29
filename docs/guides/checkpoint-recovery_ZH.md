---
name: rpnh-checkpoint-recovery
description: "不替换 Registry，基于 checkpoint 继续已有主会话或子任务。"
metadata:
  document-kind: how-to
  audience: operator-and-agent
  language: zh-CN
  counterpart: checkpoint-recovery.md
  revision: "2026-09-29.1"
  status: current
  basis: "current MainSession and TaskControl commands"
---

[English](checkpoint-recovery.md) | [中文](checkpoint-recovery_ZH.md)

# 从 checkpoint 继续已有任务

这份说明不绑定任何 example。用户提出“继续已有任务”时，可以把它交给任意 operator
agent；目标是继续同一个 Registry，而不是重写 prompt 或创建替代任务。

## Agent 必须先确认的信息

- 所属主会话的 `SESSION_DIR`；
- 如果工作是独立任务／workflow，则需要 child `TASK_ID`；
- 用户要继续最近一次 owner-stopped 切面，还是明确选择较早的 checkpoint。

task ID 的作用域属于原主会话。无关的新会话不能只凭 task ID 安全发现或接管 child。
如果位置未知，只能在用户授权的目录中查找 session/task manifest；不能复制或编辑
Registry 来让它“可见”。

## 安全续接最近 checkpoint

可以从任意 shell 或 host 上下文重开精确 owner session；RPNH task-control 命令使用
Basic 前端：

```bash
: "${SESSION_DIR:?Set the existing owning session directory}"
rpnh --frontend basic --resume "$SESSION_DIR"
```

进入 RPNH 提示符后：

```text
/tasks
/task TASK_ID status
/task TASK_ID checkpoints
```

根据 Registry 状态而不是进程外观选择动作：

- `terminal`：使用 `/task TASK_ID result`，不要 resume；
- `RUNNING` 或 Registry `running`：继续监控，不启动第二个 writer；
- `stopped_by_owner`：`/task TASK_ID resume` 续接最近的已提交 owner-stopped 切面；
- `submission_unknown` 或其它未决外部效果：不能自动重发；保留原 attempt，并按
  provider／排障证据路径处理。

执行 `resume` 后再次查看 `status`，最终必须取得注册的 terminal evidence 与 `result`。
前端退出、worker 退出或界面出现答案都不等于任务完成。

## 用户选择较早 checkpoint

先列出精确 ID；运行中的 owner 必须先停止／协调，然后只能使用该任务实际返回的 ID：

```text
/task TASK_ID checkpoints
/task TASK_ID reopen CHECKPOINT :: 说明本次纠正或继续目标。
```

`reopen` 在同一 Registry 中追加新的 execution generation，并恢复所选切面的 Petri
marking 与已结算 workspace。它不会删除后续历史、复制 Registry 或创建替代任务；所选
切面之后已经发生的外部效果也不会自动撤销。

## 主 turn 中断

重开所属 `SESSION_DIR`。选中 main 时，`/resume` 继续暂停的主 turn；`/rollback` 返回
上一个已完成主 turn。rollback 不会删除或回退 child Registry。如果主 turn 只是启动了
child，应通过 `/tasks` 和 `/task` 单独检查 child。

完整命令语义见[使用指南](usage_ZH.md)；unknown outcome 与 owner conflict 见
[排障指南](troubleshooting_ZH.md)。
