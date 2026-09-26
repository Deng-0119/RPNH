# 在 basic 前端管理两个独立任务

[English](README.md) | 中文

每个子任务都拥有独立 Registry。下图是其中一个已完成子任务的实际 PetriNet；另一个子任务
具有不同的 run 和身份，但两者都由同一个主会话控制。

![已完成的独立子任务](assets/independent-task-petrinet.png)

先在仓库外生成脚本化 profile，再启动一个新的主会话：

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-tasks.XXXXXX")"
python examples/task_workspace/prepare_profile.py \
  --output-dir "$DEMO_ROOT/profile"
rpnh --frontend basic --execution "$DEMO_ROOT/profile/execution.json" \
  --session-dir "$DEMO_ROOT/session"
```

在 RPNH 提示符内使用 `/agent`、`/tasks`、`/switch` 和
`/task ID status|result|net`。完整交互步骤见[案例指南](../../docs/guides/examples_ZH.md)。
每个子任务拥有自己的 Registry；切换焦点不会新建或替换任务。
