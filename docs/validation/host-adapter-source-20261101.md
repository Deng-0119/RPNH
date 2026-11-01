---
name: rpnh-host-adapter-source-validation
description: "Bounded validation and release boundaries for source-only adapter changes."
metadata:
  document-kind: reference
  language: en
  counterpart: host-adapter-source-20261101_ZH.md
  revision: "2026-11-01.1"
  status: source-candidate
---

[English](host-adapter-source-20261101.md) | [中文](host-adapter-source-20261101_ZH.md)

# Adapter source changes, not replacement release artifacts

**These changes advance main source beyond `bc7755d0a0111a0045c86ed4508b11e1cb4a38bc`. The already uploaded v0.1.0rc3 Python wheel and 0.1.0-rc.3 DSH plugin tarball do not contain the later fixes merely because main has changed.** No replacement artifacts, new release, automatic installation, managed upgrade qualification or default client change are part of this update. Version labels alone are insufficient to identify these source changes.

## Included, narrowly

- Ordinary admitted workspace subprocesses receive existing invocation/firing version IDs and tool-call ID as correlation only. Host environment cannot override them; no context means no IDs. Registry/PN admission, confinement and permissions remain authoritative. Tests cover old-environment isolation, parallel contexts and writer changes without replaying unknown effects. This is not full owner checkpoint/reentry proof.
- DSH disposable-bridge cancellation handles pre-dispatch classification and ignored SIGTERM; escalation only targets that child, is cleared on exit, and never kills the worker group. A stop already accepted by TaskControl may legitimately settle its worker. UNKNOWN is not success, rollback or a retry instruction.
- Codex has an explicit `candidate-0.166.0` profile, exact binary/initialize gates and upstream schema fixtures. The launcher selects the exact bound thread with `resume` when reopening rather than asking the native client to create a second thread. Default 0.163.0 and the independent exec JSONL backend/tool restrictions remain unchanged. See [explicit invocation and limits](../guides/codex-candidate-0166.md).
- OpenCode default 1.19.4 rejects colliding message projections without rewriting noncolliding IDs; its admission mutex does not hold the event condition across gateway callbacks. Same-key owner dedup remains. This is protocol-instance protection, not global identity enforcement across all frontends/commands.
- OpenCode 2.2.5 remains an explicitly selected **REST/authentication/non-durable event subset**, not a production-launcher option or complete native frontend. Full catalog, native bootstrap and live durable message events are **not implemented**. Unsupported effects fail closed; no parallel event ledger or artificial sequence was added. See [supported subset](../reference/opencode-v2-candidate.md).

## Evidence, separated rather than added together

Prior bounded workspace validation covered 59 distinct tests, including real workspace subprocesses, local IPC, parallel context separation and a corrected atomicity fixture. Model input was deterministic, not a provider call. Prior adapter source checks covered 475 distinct tests, including 59 newly added tests; that second 59 is not the first set. Two IPC cases were excluded there and subsequently entered their actual bodies locally. These historical windows are not new public-overlay test totals.

Later selected native validation covered 34 distinct tests (22 Python + 12 Node), with native interaction scenarios recorded separately. DSH used official alpha.2 Loader/ApprovalService/Web, candidate source and copied operator package files, plus real TaskControl/Registry and deterministic local model/return-timing fixtures. Reject/approval cancellation did not dispatch; an allowed stop dispatched once, late cancellation returned UNKNOWN, and reconciliation reached `stopped_by_owner`, **not business completion**. Disconnect/component-disable windows had no in-flight bridge. Guarded test process factories do not certify the default detached-worker lifecycle. This was not a fresh validation of the published rc3 wheel/tarball pair.

Official Codex 0.166.0 ran twice in real PTYs against idle committed history: initialize, discovery, history, cold reconnect and unsubscribe preserved one thread/two turns/four items with no new turn or model call. This does not prove business checkpoint recovery, active-task survival, explicit object-item cursor use, or native default 0.163 recertification. Official OpenCode 1.19.4 exercised production-launcher input/history/cold reconnect with two deterministic backend responses; manual collision tests used separate HTTP/gateway fixtures, not invented UI controls. No complete 2.2.5 TUI was run.

Preserved first failures include the Codex second-thread rejection before the resume fix; a DSH invalid synthetic plan before UI control (that failed Registry was not rewritten into success); browser UID/socket-length setup failures; and OpenCode test-driver identity/proxy/SSE assumptions. Some initial HTTP response bytes/timings were not recorded and remain unavailable. Earlier independent review also caught v2 ID collisions, the runtime msg_ constraint, lost model-evidence metadata, and a legacy gateway callback deadlock; corrected tests do not retroactively erase those defects.

## Public-baseline overlay checks

The isolated source overlay retains the public rc3 metadata, updater, host pins and existing release documentation; unrelated development files and historical/private enhancements are not included. Final targeted command results are recorded below after execution. Existing compliant dependencies were reused; no installation, model/provider call, external service experiment or Actions run was initiated.

Targeted public-overlay checks: **97 Python passed, 4 failed** in 32.77s; **12 Node passed** in 2.75s. All four failures were the new launcher combinations at AF_UNIX socket construction (`PermissionError`), before the patched spawn path; no sandbox bypass or skip was added. The same launcher bytes and four combinations had passed in the separate local native-validation environment described above; that does not turn these four overlay failures into passes. The 97+12 are 109 distinct passing cases, not a full suite. Python3.12, pytest8.4.2, websockets15.0.1 and Node24.22.1 were used.

From the repository root, using an existing compliant environment:

```sh
python -m pytest -q tests/test_workspace_call_context.py tests/test_managed_interruption_writer_fencing.py tests/test_workspace_write_atomicity.py tests/test_opencode_v225_candidate.py tests/test_codex_0166_candidate.py tests/test_codex_0166_native_local.py \
 'tests/test_codex_0161_candidate.py::test_launcher_binds_verified_profile_before_transport[None-0.163.0]' \
 'tests/test_codex_0161_candidate.py::test_resolver_checks_only_selected_binary_exact_version[None-codex-cli 0.163.0-True]'
node --test integrations/dsh/operator/transport.test.js
```

All windows overlap; do not sum them into a full-product score. Managed cold installation/cross-version rollback remains unqualified, and real-provider/full-product qualification is outside these results. Registry and both PN layers retain execution/recovery authority; frontend reconnect does not resume a business task or replay external effects.
