# OpenCode 1.18.32 宿主

[English](README.md) | 中文

安装精确的 OpenCode 1.18.32，并选择一个已授权 profile。启动前端本身不会调用模型；
提交 `task.txt` 会启动一个逻辑主会话 turn：

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-opencode-example.XXXXXX")/session"
rpnh --frontend opencode --execution "$EXECUTION_CONFIG" --session-dir "$RUN_ROOT"
```

只粘贴一次 `task.txt` 的完整内容，等待 Registry 支撑的答案显示后正常退出 TUI。不要使用
OpenCode 原生 provider 登录或工具；该前端只是 RPNH 的展示客户端。
直接 JSON 答案后可能附带“未接受 child-task decision”的提示；对本任务而言，这表示没有
启动子任务，不代表登记答案失败。Registry 尚在结算时也可能显示保守的 reconciliation
提示；不要重新提交，应等待 Registry 终态。物理 generation 次数须以 provider-private
audit 为准。
