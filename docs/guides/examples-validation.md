---
name: rpnh-examples-validation
description: "Deterministic acceptance record for the public newcomer examples."
metadata:
  document-kind: validation-record
  audience: user-and-maintainer
  language: en
  counterpart: examples-validation_ZH.md
  revision: "2026-09-26.1"
  status: deterministic-offline-validated
  basis: "focused local acceptance; no real provider call"
---

[English](examples-validation.md) | [中文](examples-validation_ZH.md)

# User-example validation record

Baseline was `main` at `1c17366`; the candidate is the commit containing this
record. Validation used Linux, Python 3.13 and repository-external temporary run
directories. The scripted profile used `local_process` and made no external
provider request.

| Mode | Status | Observed result |
|---|---|---|
| Native add | PASS | `2 + 3` returned `5`, terminal evidence present, zero model calls |
| Instruction resource | PASS | Text and live resource/version identities returned |
| Hybrid default | PASS | Three transitions; total `36`, mean `12`, min `9`, max `15` |
| Hybrid variant | PASS | Total changed to `39`, mean to `13` |
| Independent tasks | PASS | Two task/run identities, separate terminal results and one-node nets |
| Real model | NOT RUN | No provider/model/route or call budget was authorized for this task |

Focused commands:

```bash
python -m pytest -p no:cacheprovider -q tests/test_user_examples.py
python scripts/docs.py check
python -m pytest -p no:cacheprovider -q tests/test_docs_site.py
```

The hybrid fixture performs two model-boundary calls per model node: one exact
`read_file` call for the Located input and one `write_file` plus
`complete_interaction` response. Those calls are local deterministic subprocess
traffic, not real model calls. The registered physical-call counters remain
visible so the fixture is not presented as zero execution.

This record is a focused example acceptance, not a full release suite or live
provider claim. Generated Registries and raw adapter audits were not committed.
