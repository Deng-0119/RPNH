# Executed commands and limits

From `source/`, using the existing environment; no installation:

1. `python -m pytest ...` initially found no pytest in the default runtime.
   Continued using `[PYTHON_ENV]/bin/python`.
2. `PYTHON -m pytest -q tests/test_codex_compat.py tests/test_frontend_boundary.py --junitxml=../existing-tests.xml`
   Result: 36 passed, 1 failed, exit 1. AF_UNIX socket creation denied, errno 1.
3. `PYTHON -m pytest -q tests/test_codex_compat.py -k 'effort_wire or none_marker' --junitxml=../codec-tests.xml`
   Initial fixture assertion: 4 passed, 2 failed. Preserved as `initial-codec-fixture-failure.*`.
   Corrected TS expectation from enum to its real string alias; no product fallback added.
4. Final: `PYTHON -m pytest -q tests/test_codex_compat.py tests/test_frontend_boundary.py -k 'not test_codex_frontend_uses_external_socket_for_absent_long_root' --junitxml=../final-tests.xml`
   Result: 43 passed, 1 deselected, exit 0. This does not pass the native transport gate.
5. `PYTHON -m py_compile cpn/frontend/codex_app_server.py tests/test_codex_compat.py`
6. `python ../freeze.py` creates the portable patch, verifies hashes and performs a fresh-baseline git apply/check.

Only step 4 describes the final code and test inventory. The initial two official
schema checks were rerun as part of step 4. RPCs use an in-memory socket double;
turn/start stops at a preparation spy. No Codex binary, Rust/TS compilation,
real provider, login, installation, GitHub Actions, push or deployment occurred.
