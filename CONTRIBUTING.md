---
name: rpnh-contributing
description: "Explain how to propose and validate focused RPNH changes."
metadata:
  document-kind: project-policy
  audience: contributor
  language: en
  counterpart: CONTRIBUTING_ZH.md
  revision: "2026-09-29.1"
  status: v0.1.0rc1
---

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING_ZH.md)

# Contributing to RPNH

RPNH accepts focused issues and pull requests against `main`. Before changing
code, read [AGENTS.md](AGENTS.md), the
[development guide](docs/guides/development.md) and the relevant architecture or
interface page.

## Development environment

Use Linux or WSL2 with Python 3.11 or newer:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pip install -r docs/requirements.txt
```

Keep credentials, provider profiles, generated Registries, user workspaces and
raw model transcripts outside the repository.

## Change and test scope

- Keep runtime authority in RPNH. Frontend or host integrations must not create
  separate provider, Registry, workspace or recovery implementations.
- Preserve provider neutrality. Do not bundle endpoints, credentials or a
  preferred exact model.
- Update English and Chinese user documentation together.
- Add a deterministic regression for changed behavior and run the smallest test
  set that covers the affected boundary. A full suite is appropriate for a
  release-wide candidate or a change that can affect unrelated modules.
- Real-provider checks require explicit authorization and never belong in the
  automated suite.
- Do not add GitHub Actions `push` triggers.

Format the pull request around the observed boundary, the change, the exact
commands run and any remaining limitation. Do not claim broader validation than
the evidence supports.

Security-sensitive findings should follow [SECURITY.md](SECURITY.md), not a
public issue.
