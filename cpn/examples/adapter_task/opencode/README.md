# OpenCode 1.18.32 host

Install exact OpenCode 1.18.32 and select one authorized profile. Starting the
frontend sends no model request; submitting `task.txt` starts one logical
main-session turn:

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-opencode-example.XXXXXX")/session"
rpnh --frontend opencode --execution "$EXECUTION_CONFIG" --session-dir "$RUN_ROOT"
```

Paste the complete contents of `task.txt` once. Wait until the Registry-backed
answer is visible, then leave the TUI normally. Do not use OpenCode-native
provider login or tools; this frontend is an RPNH presentation client.
The direct JSON answer can be followed by a note that no child-task decision was
accepted; for this task that means no child was launched, not that the registered
answer failed. A conservative reconciliation notice can appear while settlement
is still pending. Do not resubmit: wait for the terminal Registry result. Use the
provider-private audit for the physical generation count.
