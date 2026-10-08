# Commands and local completion

All pytest commands ran from the reconstructed source checkout using the
existing Python environment at `../../rpnh-recovery-20261003/source/.venv/bin/python`.
No package installation or real provider invocation was required.

## Recorded commands

1. Native attempt, exit 1 (`native-attempt.log`):

   `python -m pytest -q examples/tool_pipeline/tests --maxfail=1`

2. Main focused batch (`focused-tests.log`, `focused-tests.xml`):

   `python -m pytest -q tests/test_orchestrator_boundary.py tests/test_harness_quiescence.py examples/tool_pipeline/tests --tool-pipeline-transport=pipe --junitxml=../focused-tests.xml`

   This batch was collected before the final early-stop/factory tests were
   appended. It covers the normal launcher path, which was unchanged afterward.
   The final narrow test additions and fixture cleanup were checked below.

3. Existing AgentTask interruption/recovery, exit 0 (`agent-task-tests.*`):

   `PYTHONPATH="$PWD:$PWD/.." python -m pytest -q -p agent_task_pipe_checks tests/test_task_frontend.py::test_interrupted_firing_does_not_publish_workspace_files tests/test_task_frontend.py::test_resume_uses_persisted_transition_profiles_when_graph_swaps_them tests/test_task_frontend.py::test_interruption_checkpoints_prior_workspace_action_and_discards_current --junitxml=../agent-task-tests.xml`

4. Final early-stop and sibling-drain regressions, exit 0 (`stop-final-tests.*`):

   `python -m pytest -q examples/tool_pipeline/tests/test_pipeline.py::test_agent_task_preconstruction_stop_keeps_host_ownership examples/tool_pipeline/tests/test_pipeline.py::test_entry_stop_drains_admitted_siblings_without_new_work --tool-pipeline-transport=pipe --junitxml=../stop-final-tests.xml`

5. Final boundary and compatibility checks, exit 0 (`boundary-final-tests.*`):

   `python -m pytest -q tests/test_orchestrator_boundary.py tests/test_harness_resource_continuation.py examples/tool_pipeline/tests/test_pipeline.py::test_legacy_factory_returns_shared_entry_harness --tool-pipeline-transport=pipe --junitxml=../boundary-final-tests.xml`

6. All five changed Python files passed `python -m py_compile`.

7. `python freeze.py` ran `git apply --check` and `git apply` against the exact
   changed-file baseline, then compared every final byte (`patch-apply.json`).
   `python verify_patch.py baseline` independently verified the portable
   non-mutating apply-check (`portable-apply-check.log`).

## Local native completion

Verify the current checkout and `file-manifest.json` before applying. The
non-mutating check is `python verify_patch.py /path/to/RPNH`. Apply the patch
only to the authorized checkout after that check succeeds.

In the checkout's installed test environment run:

- `python -m pytest -q tests/test_orchestrator_boundary.py tests/test_harness_quiescence.py tests/test_harness_resource_continuation.py examples/tool_pipeline/tests`
- The three AgentTask test node IDs in command 3, without the validation pipe plugin
- `python -m examples.tool_pipeline.run --run-dir <fresh-run-dir> --output-dir <fresh-export-dir>`

The default example tests and CLI exercise real AF_UNIX. Verify the final report
is 2.000 kWh / 1.70 CNY, zero model calls, one complete terminal; retain the logs
and readback outputs outside the repository. Do not replace native acceptance
with another pipe run. Do not perform model/API/Docker/Actions/push/merge steps
as part of this patch's local validation.
