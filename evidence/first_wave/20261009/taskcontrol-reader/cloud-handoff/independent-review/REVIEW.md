# H2a independent review

Reviewed baseline: `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`, materialized from the authoritative RPNH main tree. No Git push, Actions, provider dispatch or full-repository test was run.

## Result

No remaining product blocker found in the consumer convergence. TaskControl.result and _read_terminal_status delegate identity/closure/provenance to the shared native reader, use its typed refs, and recheck the same cut after rendering/count accounting. There is no copied terminal matcher or fallback to a previous generation. The actual model-call accounting call and historical evidence/index counts are preserved. Owner socket/process observations remain independent.

The initially proposed 4 MiB product/descriptor caps were rejected during review and removed. Terminal body reads now use the registered result size; the native descriptor helper uses each registered size when the caller supplies no budget. Explicit caller budget behavior is preserved. This bounds unexpected backing-file growth without rejecting legal large objects.

## Independent evidence

- `new-green.log`: 11 passed in 35.50s. Exact legacy result/status JSON keys; string task_ref; readonly event/object fingerprints; no writer/resume/socket/grant on reads; current generation only; late head/epoch races after cumulative count read rejected; immutable descriptor corruption rejected; >4 MiB result accepted; implicit registered-size and explicit-budget descriptor reads.
- `old-red.log`: the same 4 late-cut race cases fail against ec9077 baseline (DID NOT RAISE), then pass after the change.
- `large-descriptor.log`: 1 passed in 4.87s. A real temporary Registry with a valid native_run_identity descriptor >4 MiB remains readable through both TaskControl consumers; an explicit 4 MiB descriptor budget rejects it.
- `shared-core-regressions.log`: 24 passed in 53.12s. Existing `tests/test_run_descriptor_reads.py` and `examples/harnessaudit_office/tests/test_registry_reader_consumers.py` verify shared-core behavior, both prior consumers, cross-run/cut/kernel binding, current generation and physical bounds.
- `native-taskcontrol-status.log` and `.xml`: 6 passed, 1 blocked, 33 deselected. `test_task_control_keeps_owner_socket_in_registry_and_shortens_only_transport` fails at native `socket.socket(AF_UNIX, SOCK_STREAM)` construction with `PermissionError: [Errno 1] Operation not permitted`. This is an environment gate, not a product assertion failure. Native socket/process/resume behavior is not claimed passed by mocked or static fixtures.

Independent checks use `<CLOUD_WORKSPACE>/rpnh-recovery-20261003/source/.venv/bin/python` with the reviewed source root first in PYTHONPATH. They use real temporary Registries with explicit static products, never models. Small adversarial monkeypatches inject late read-cut movement or verify consumer delegation; they are not substitutes for native process/socket validation.

- `native-resume.log` and `.xml`: the existing offline scripted test `test_resume_uses_persisted_transition_profiles_when_graph_swaps_them` was separately attempted. It is blocked at `OwnerEventLoop` construction (`socket.socket(AF_UNIX, SOCK_STREAM)`, EPERM), before provider dispatch, in 3.73s. The resume assertions were not reached. No socket workaround was used.

## Frozen artifact verification

Independent patch apply/check passed against the 4 existing baseline files; the newly added fifth file and all 5 final file bytes match `file-manifest.json`. All 1,335 entries in `tested-source.sha256` match the frozen source. Baseline SHA-256 and Git blob IDs match the verified ec9077 provenance.

- Patch SHA-256: `62f914d41d953e9badd4eb06eec43273f9d52bd5bd9b948ad6e10137d3ed2b24`
- Tested source-list SHA-256: `bf088faa3fe22f9f14fcfa0081484bc1bae9067ea7c20c1162125eab29bf806a`
- Machine-readable evidence: `final-artifact-verification.json`.

## Native validation still required

In an environment that supports native AF_UNIX, rerun the affected TaskControl frontend/status tests and the requested native owner-stop/resume end-to-end gate using the final source. The current cloud gate must remain recorded as blocked until that succeeds.
