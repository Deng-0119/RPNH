# Existing owner entry convergence

## Scope and source

Base: `Deng-0119/RPNH@674252feb836f631c162979f177d1fe91f22559f`.
All 948 Git blobs under `cpn/`, `tests/` and `examples/tool_pipeline/` were
matched to the connector-fetched immutable Git tree before implementation.
The later `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4` commit changes five evidence
files only. It is not represented as another code test run.

AgentTask already used `cpn.orchestrator.runner.Orchestrator`; the tool example
constructed `Harness` directly. Both already shared the underlying Harness
admission and completion algorithms. This patch converges the HOST entry only.
It does not claim to introduce or deduplicate a scheduler that was not present.

## Production changes

- Existing Orchestrator gets one `request_owner_stop()` forwarding method and
  explicit borrowed-resource documentation. No second owner, state, queue,
  runner, scheduler, context manager or close method is created.
- AgentTask uses the entry's stop method for both an early remembered SIGINT and
  an active-run SIGINT. Provider construction/closing, workspace and recovery,
  managed capacity, signal installation/restoration, semantic stop policy and
  ThreadPoolExecutor lifetime are unchanged.
- tool_pipeline uses `make_orchestrator(...).run()` for normal execution.
  Its old `make_harness(...)` helper preserves the Harness return shape by
  returning the shared entry's executor. It has no separate construction path.
- Step-level tests explicitly inspect the existing executor. No new generic
  step API or tool async API is introduced.

The application Module, registered tools, tool HOST ABI, dispatcher services,
Registry, Harness, OwnerEventLoop and pipe adapter remain byte-identical to base.
English and Chinese example READMEs describe the same entry and compatibility.

## Validation boundaries

The native AF_UNIX run was actually attempted and failed at socket creation
with `PermissionError: [Errno 1] Operation not permitted`. This is a separate
environment blocker, not a passing native transport test.

Semantic tests explicitly select the already-existing test-only pipe adapter.
It changes the wake transport only. Production has no fallback. AgentTask's
three selected existing interruption/recovery tests use the included validation
plugin, which also hides `.executor` from AgentTask, counts loop cleanup and
verifies restoration of the original SIGINT handler.

Added regressions cover exact constructor forwarding and failure identity;
1/2-worker direct Harness versus Orchestrator resource values, causal roles,
per-firing ordered event types and terminal outcomes; two admitted reads draining
on owner stop without successor admission; unchanged HOST ownership/one close;
early pre-construction SIGINT with zero provider dispatch; and compatibility of
the old factory. Existing tests still cover parallel read progress, join guards,
lineage, replay-free terminal readback, rejection and unresolved worker failure.

No real provider/model API, Docker, Actions, repository push or merge was used.
The package is a candidate pending local native transport validation and review.

## Preserved intermediate errors

`agent-task-plugin-setup-error.log` and `stop-test-collection-error.*` are shell
path/collection mistakes before the selected tests ran. `stop-fixture-error.*`
is the first early-stop test's unhashable fake-port fixture; the fixture was
corrected to a normal hashable class and the same two tests rerun successfully.
These files are retained and not counted as product failures or final passes.

Final pass counts, de-duplicated test identities and raw logs belong in
`test-results.json` and the XML/log files. Repeated checks are not new coverage.

## Final result

All 39 distinct final collected test identities passed. Six successful batches
contain 50 testcase executions because several targeted and independent checks
repeat the same identity. The main batch passed 32/32 in 908.82 seconds; final
supplements and independent checks cover the final source additions. This is
focused semantic acceptance with an explicit pipe transport, not full-repository
or native IPC acceptance. The seven-file source hash set exactly matches the
independent review. Native AF_UNIX and native CLI validation remain required
before merging this candidate.
