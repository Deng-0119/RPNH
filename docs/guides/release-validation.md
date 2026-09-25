---
name: rpnh-release-validation
description: "Record the sanitized offline validation boundary for the unified public candidate."
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: release-validation_ZH.md
  revision: "2026-09-25.1"
  status: offline-candidate-validated
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# Public candidate validation

This record describes the unified candidate prepared on 2026-09-25. It is a
sanitized summary, not a copy of local logs or private Registry data.

## Included source boundary

The candidate contains core Registry/PetriNet execution, basic and Codex
frontends, native plugins, the shared provider/profile layer, the optional DSH
host and the read-only PetriNet viewer. Historical branch evidence, live API
campaigns, project workflows, local profiles and OpenCode are not included.

The provider/model catalog shipped by the package is empty. No provider,
endpoint, credential or exact model is preselected.

## Checks completed

| Check | Result |
|---|---|
| Complete Python 3.13 offline suite | 581 passed |
| Integrated Codex/DSH/viewer/registered-host focused set | 164 passed |
| Config, onboarding, resume, workspace and net CLI focused set | 52 passed |
| Viewer Node/JointJS/ELK tests | 72 passed |
| Pinned upstream DSH offline integration | 2 files, 16 tests passed |
| Documentation links, pairs and fenced syntax | 46 pages, 23 language pairs passed |
| Provider catalog examples | Empty catalog accepted; three invalid mutations rejected |
| Wheel viewer assets and third-party licenses | Present and nonempty |
| Installed wheel smoke outside source | Python 3.12 and 3.13 entry points passed |
| Installed zero-model configuration flow | 6 commands passed; runtime effects guarded |

The fixed DSH test used its declared upstream revision and pnpm 11.7.0 in a
temporary checkout. It exercised local deterministic transport only.

## Calls and limits

No real model or provider API call was made. Offline passing results do not
establish that a user-owned route is reachable. Codex TUI interaction, live
provider tests and all OpenCode tests remain for a separately authorized phase.

Playwright/Chromium was not installed in the validation environment, so the
optional browser automation scripts were not run. Viewer model/layout behavior,
real JointJS/ELK parsing, static packaging and read-only HTTP boundaries were
covered by the Python and Node suites above.

## Repository policy

The public candidate has one `main` branch, no GitHub Actions workflows and no
automatic `push` trigger. Live-provider evidence and generated run data remain
outside the repository.
