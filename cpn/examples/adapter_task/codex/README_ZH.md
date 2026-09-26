# Codex 0.155.0 宿主

安装精确的 `codex-cli 0.155.0`，并选择一个已授权 profile。启动前端本身不会调用模型；
提交 `task.txt` 会启动一个逻辑主会话 turn：

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-codex-example.XXXXXX")/session"
rpnh --frontend codex --execution "$EXECUTION_CONFIG" --session-dir "$RUN_ROOT"
```

只粘贴一次 `task.txt` 的完整内容，等待 turn 完成后正常退出 TUI。provider/model 选择与
Registry 结算由 RPNH 管理，不由 Codex 管理。物理 generation 次数须以 provider-private
audit 为准；一个逻辑 turn 不一定只有一次物理提交。
