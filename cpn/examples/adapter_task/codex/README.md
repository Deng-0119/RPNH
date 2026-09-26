# Codex 0.155.0 host

Install exact `codex-cli 0.155.0`, then select one authorized profile. Starting
the frontend sends no model request; submitting `task.txt` starts one logical
main-session turn:

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-codex-example.XXXXXX")/session"
rpnh --frontend codex --execution "$EXECUTION_CONFIG" --session-dir "$RUN_ROOT"
```

Paste the complete contents of `task.txt` once. Wait for the turn to finish,
then leave the TUI normally. RPNH, not Codex, owns provider/model selection and
Registry settlement. Use the provider-private audit for the physical generation
count; a logical turn is not necessarily one physical submission.
