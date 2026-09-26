# Two independent tasks in the basic frontend

English | [中文](README_ZH.md)

Create a repository-external scripted profile, then start one fresh main session:

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-tasks.XXXXXX")"
python examples/task_workspace/prepare_profile.py \
  --output-dir "$DEMO_ROOT/profile"
rpnh --frontend basic --execution "$DEMO_ROOT/profile/execution.json" \
  --session-dir "$DEMO_ROOT/session"
```

Inside the RPNH prompt, use `/agent`, `/tasks`, `/switch`, and
`/task ID status|result|net`. The exact interactive transcript is in the
[examples guide](../../docs/guides/examples.md). Each child has its own Registry;
switching focus does not create or replace a task.
