---
name: rpnh-examples-validation
description: "Deterministic and authorized live acceptance record for the public newcomer examples."
metadata:
  document-kind: validation-record
  audience: user-and-maintainer
  language: en
  counterpart: examples-validation_ZH.md
  revision: "2026-09-26.3"
  status: focused-live-validation-complete
  basis: "focused local acceptance across basic, Codex, DSH and OpenCode hosts"
---

[English](examples-validation.md) | [中文](examples-validation_ZH.md)

# User-example validation record

Baseline was `main` at `6c675cb`; the candidate is the commit containing this
record. Validation used Linux, Python 3.13 and repository-external temporary run
directories. Deterministic checks used `local_process`. Authorized live checks
explicitly selected user profile `codex-terra`, catalog provider `codex` and
exact model `gpt-5.6-terra`; the saved default profile was not changed.

| Mode | Status | Observed result |
|---|---|---|
| Native add | PASS | `2 + 3` returned `5`, terminal evidence present, zero model calls; declared 1 KiB result bound covers the complete legal output domain |
| Instruction resource | PASS | Text and live resource/version identities returned |
| Hybrid deterministic | PASS | Three transitions; total `36`, mean `12`, min `9`, max `15` |
| Hybrid variant | PASS | Total changed to `39`, mean to `13` |
| Basic main session | PASS | Exact `BASIC_TERRA_READY`; terminal evidence and final result present |
| Codex 0.155.0 host | PASS | Exact `CODEX_TERRA_READY`; Registry terminal evidence and final result present |
| DSH pinned host | PASS | Same adapter route and exact model under an explicit 1 MiB DSH response budget; one managed summary tool call returned count `3`, total `36`, mean `12`, min `9`, max `15` |
| OpenCode 1.18.32 host | PASS with content warning | One replacement turn reached Registry terminal evidence and final result through the pinned TUI. The exact model returned prose `Completed.` instead of the requested marker; RPNH correctly accepted it only as a protocol-invalid prose answer. |
| Hybrid live graph | PASS | Read/correct/write/complete flow reached terminal state and produced count `3`, total `36`, mean `12`, min `9`, max `15` |
| Independent tasks | PASS | Two task/run identities, separate terminal results and one-node nets |

Focused commands:

```bash
python -m pytest -p no:cacheprovider -q tests/test_user_examples.py
python -m pytest -p no:cacheprovider -q tests/test_opencode_application_boundary.py tests/test_opencode_cli.py tests/test_opencode_frontend.py tests/test_opencode_registry_integration.py tests/test_opencode_pty.py
python scripts/docs.py check
python -m pytest -p no:cacheprovider -q tests/test_docs_site.py
```

The hybrid fixture performs two model-boundary calls per model node: one exact
`read_file` call for the Located input and one `write_file` plus
`complete_interaction` response. Those calls are local deterministic subprocess
traffic, not real model calls. The registered physical-call counters remain
visible so the fixture is not presented as zero execution.

Across completed live checks, 27 physical model responses succeeded and no
health probe was used. Failed first attempts and corrected replacement runs
were preserved outside the repository. The first OpenCode run records zero
successful responses and remains `submission_unknown`; it was neither counted
as success nor replayed. A separately authorized replacement used one new
physical call. During that replacement, a transient child-Registry read exposed
and validated the frontend reconciliation fix; reopening the same frontend root
committed the already-terminal result without another model call.

This record is focused candidate evidence, not a full release suite. Host-path
PASS means transport, Registry settlement and frontend projection completed; it
does not convert the OpenCode model-content warning into exact prompt compliance.
Generated Registries, raw adapter audits, credentials and private route details
were not committed.
