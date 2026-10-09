# Commands and evidence

All product tests used the existing interpreter at
`../rpnh-recovery-20261003/source/.venv/bin/python` relative to the work package.
The default Python had no pytest; no installation was attempted.

From the complete development source checkout:

PYTHON -m pytest -q tests/test_codex_history.py tests/test_codex_compat.py tests/test_main_thread_history.py tests/test_frontend_session_access.py tests/test_frontend_boundary.py -k 'not test_codex_frontend_uses_external_socket_for_absent_long_root' --tb=short --junitxml=../final-tests.xml

Final: 119 passed, 1 deselected. The omitted case needs native AF_UNIX transport;
it was not worked around or counted as passing. Additional 43 selected native
Registry/MainSession node IDs are recorded in native-regression-command.json.
These do not overlap the 119. Nine other native execution cases were not selected.

The same 119-case command ran separately after overlaying only Git-verified
d92ff370 task_control.py and registry/run_authority.py. Evidence lives under
latest-main-overlay/. This is not a full-HEAD checkout or a full repository run.

Independent reviewer: 128 passed, 21 additional adversarial probes, stable
before/after source hashes, and independent full-tree portable apply replay.
Its selected product tests overlap the author's results. Read its report for
the exact matrix and the retained failed writable-connection evidence.

Other checks:
- py_compile on five changed Python modules and both native-gate scripts
- `PYTHON audit-tools/check_docs.py` on the two new bilingual pages
- `python freeze.py` in the full development work package: composite-baseline
  hash checks, portable `git apply --check`/apply and exact resulting bytes
- `PYTHON native-gate/prepare_fixture.py --repo source --output NEW_TEMP_PATH --turns 2 --pending`
  prepared synthetic committed/pending Registry records without any model/child
- No run_native_gate.py, stock Codex, install/login/model/API/Actions/push/upload

The documentation parser found `language: zh`, corrected to `zh-CN` after the
runtime freeze. Only that documentation line changed. The pre-doc-fix manifest,
new manifest, parser failure/success and supplemental independent review retain
this distinction; no runtime test count is reassigned to the newer full hash.
