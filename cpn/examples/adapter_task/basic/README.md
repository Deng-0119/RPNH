# Basic host

Prerequisite: set `EXECUTION_CONFIG` to the absolute path of one authorized
RPNH execution selection. This command starts one real main-session logical
turn:

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-basic-example.XXXXXX")/session"
rpnh --frontend basic --execution "$EXECUTION_CONFIG" \
  --session-dir "$RUN_ROOT" --prompt "$(cat task.txt)"
```

Inspect the generated main-turn run with `rpnh net --run RUN_DIR`; obtain the
exact `RUN_DIR` from the session's registered attempt rather than guessing it.
One logical turn can contain multiple physical generation submissions; inspect
the provider-private audit instead of inferring that count from the prompt.
