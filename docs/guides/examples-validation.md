---
name: rpnh-examples-validation
description: "Deterministic and authorized live acceptance record for the public newcomer examples."
metadata:
  document-kind: validation-record
  audience: user-and-maintainer
  language: en
  counterpart: examples-validation_ZH.md
  revision: "2026-09-26.7"
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
and validated the then-current frontend reconciliation fix. That historical
revision committed an already-terminal result while reopening its frontend root,
without another model call. Current main deliberately changed this boundary:
reopening the canonical MainSession is observational, and the user must issue
the explicit frontend resume command to commit terminal evidence.

This record is focused candidate evidence, not a full release suite. Host-path
PASS means transport, Registry settlement and frontend projection completed; it
does not convert the OpenCode model-content warning into exact prompt compliance.
Generated Registries, raw adapter audits, credentials and private route details
were not committed.

## Installed cross-host semantic task addition

The installable `batch-summary-v1` bundle was then run once through each host
from an isolated wheel candidate. Unlike the earlier readiness markers, every
host received the same semantic task and every committed answer passed the
strict answer-only JSON verifier.

| Host | Logical turns | Successful physical responses | Probes | Route/model switches | Authority | Content |
|---|---:|---:|---:|---:|---|---|
| Basic | 1 | 2 | 0 | 0 | PASS | PASS |
| Codex 0.155.0 | 1 | 2 | 0 | 0 | PASS | PASS |
| pinned DSH | 1 | 1 | 0 | 0 | PASS | PASS |
| OpenCode 1.18.32 | 1 | 2 | 0 | 0 | PASS | PASS |

This addition produced seven successful formal responses on the explicitly
selected `local_process` / `gpt-5.6-terra` route. It is a separate count from
the earlier 27-response campaign above. OpenCode displayed a conservative
reconciliation notice while settlement was pending and later displayed the
exact committed JSON plus its normal no-child-decision annotation; no task was
resubmitted. The per-host sanitized summaries are shipped in the installed
example bundle. They omit raw Registries, identifiers, local paths, endpoints,
credentials and transcripts.

Additional focused checks:

```bash
python -m pytest -q tests/test_adapter_task_examples.py tests/test_docs_site.py
python -m pytest -q tests/test_config_and_net_cli.py tests/test_dsh_distribution.py
python scripts/docs.py check
python scripts/check_doc_examples.py
python scripts/check_installed_docs.py --python "$WHEEL_VENV/bin/python" --source-root "$SOURCE_ROOT"
```

## Native net-operation live task

The executable native net-operation scope was also exercised in one authorized
real-provider task. Extract and Branch selected two Agent definitions;
Instantiate and Compose built the initial graph; after the first Agent settled
`outputs/seed.txt`, whole-net Replace adopted the successor at a quiescent
checkpoint. The successor read the inherited file through both the workspace
and its exact registered-resource path, then registered `outputs/result.txt`
and reached terminal outcome `complete`.

The selected route was `volcano` / `deepseek-v4-pro`. Five formal physical
responses succeeded, with zero health probes, zero route/model switches and
zero post-limit calls. The run also exposed and fixed a replacement binding
defect: a successor must reuse the run's exact execution environment/profile
instead of registering a duplicate pair. The deterministic end-to-end
regression reproduces that boundary without network access. The reusable task
and sanitized live record are under `examples/net_operations/`; raw Registry,
provider audit, identifiers, local paths and transcripts were not committed.

## Workflow-pattern and viewer gallery

The source gallery then completed four fresh deterministic Registry runs. This
was local-process fixture traffic, not a provider campaign:

| Scenario | Fixture calls | Transitions | Places | Edges | Registry terminal |
|---|---:|---:|---:|---:|---|
| Serial | 3 | 3 | 6 | 15 | PASS |
| Parallel fan-out/join | 4 | 4 | 9 | 26 | PASS |
| Document pipeline | 4 | 4 | 8 | 21 | PASS |
| Six-stage long process | 6 | 6 | 12 | 33 | PASS |

Each Agent read its exact Located inputs through the managed tool boundary and
published one registered output. The parallel run required both branch products
before its join settled. The long process provides canonical checkpoints for
viewer timeline instruction. The sanitized record is
`examples/workflow_patterns/validation.json`; raw runs remain outside the
repository.

## Public dashboard images

The documentation now carries 13 actual 1440×980 dashboard captures. Eight
come from deterministic source examples: native plugin, hybrid calculation,
serial, parallel Overview, parallel PetriNet, document, long process and an
independent child task. Five are read-only captures of previously authorized
accepted runs: native net replacement plus Basic, Codex, DSH and OpenCode.

`scripts/capture_example_dashboards.py` reproduces the deterministic images and
can accept explicit existing run directories for the authorized examples. The
capture path verifies expected graph nodes before writing each PNG. It preserves
the real graph, aggregate state, controls and timeline, while replacing the
run-specific checkpoint label and hiding the footer containing exact checkpoint
identity and local observation time. No new provider/model call was made for
this documentation capture. Images remain observation aids, not substitutes
for terminal evidence or registered final results.
