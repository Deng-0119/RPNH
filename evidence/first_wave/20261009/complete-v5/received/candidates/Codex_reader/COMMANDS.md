# Executed checks

No installation was performed. The default `python -m pytest` had no pytest;
all executed tests used the existing interpreter
`../rpnh-recovery-20261003/source/.venv/bin/python` relative to this package.
Commands below run from `source/` unless stated otherwise.

1. Existing regression: `PYTHON -m pytest -q tests/test_main_thread_registry.py --junitxml=../existing-tests.xml` → 12 passed.
2. New history tests exposed a cached-current-row issue in the initial view read;
   the view path was changed to construct PreparedObject from the admitted
   canonical row. The initial failure is retained in `initial-history-tests.*`.
3. A byte-level read-only test initially counted SQLite `-shm` read marks as
   authoritative writes. It now excludes that volatile WAL index while checking
   database/WAL/object/profile/owner-lock bytes, writer entry points, SQL mutation
   denial, event/object counts, physical head and writer epoch. Initial output is
   retained in `initial-readonly-wal-index-test.log`.
4. Final focused combination:

```sh
PYTHON -m pytest -q \
  tests/test_main_thread_history.py \
  tests/test_main_thread_registry.py \
  tests/test_main_session_registry.py \
  -k 'not fresh_session_holds_owner_lease_before_registry_becomes_visible and not long_main_session_keeps_owner_socket_relative_to_turn_registry and not real_terminal_child_registry and not main_turn_accepts_direct_text_write and not task_call_cap_handoff and not two_registry_queries and not reconcile_real_terminal_child and not real_stopped_child and not owner_stop_cancels' \
  --junitxml=../final-tests.xml
```

Result: 57 passed, 9 deselected. The 9 excluded native execution/socket cases
are listed in `deselected-tests.txt`; they were not run and are not passes.
The new history file has 14 cases. Existing Registry coverage contributes 12
cases and selected MainSession compatibility contributes 31 parameterized cases.

5. `PYTHON -m py_compile cpn/rpnh/registry/main_thread.py cpn/rpnh/registry/main_thread_history.py tests/test_main_thread_history.py` → passed.
6. Full-site `scripts/docs.py check` could not start because this isolated
   baseline omits the script and other site files. The exact main checker blob
   `9de34e8187c718d21b5a5f8efcac878a42139b48` was fetched into `audit-tools/`.
   From the package root: `PYTHON audit-tools/check_history_docs.py` → all four
   changed pages parsed; bilingual identities/revisions and new links passed.
   No complete site check/build is claimed.
7. `python freeze.py` from the package root verifies seven-file scope, baseline
   Git blobs, SHA-256 hashes, and fresh `git apply --check`/`git apply` exact bytes.
8. Main advanced to `715468dab0b1bea07d7e94a7aa0606eaf194365c` during this task.
   All patch target blobs are unchanged. A separate `latest-main-source/`
   validation copy overlays only that merged commit's two relevant production
   changes (`runner.py`, `agent_tasks.py`), both Git-blob verified. Repeating
   command 4 there gives 57 passed, 9 deselected in `latest-main-tests.*`.
   This is the same 57-case matrix, not 114 unique tests.

All executions are deterministic offline fixtures/native Registry reads with
fake child observations and existing TaskControl doubles. Zero real model or
provider calls, stock Codex launches, Actions, remote writes, login changes or
software installations occurred. No native frontend/cold-resume certification
is implied by these Python tests.
