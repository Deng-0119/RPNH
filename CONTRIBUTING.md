---
name: rpnh-contributing
description: "Contributing to RPNH"
metadata:
  document-kind: project-policy
  audience: user-and-developer
  language: en
  counterpart: CONTRIBUTING_ZH.md
  revision: "2026-11-02.1"
  status: maintained
---

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING_ZH.md)

# Contributing to RPNH

Issues and pull requests are welcome against `main`. Read the relevant
[architecture](docs/architecture/design.md), [API reference](docs/reference/api-index.md)
and [development guide](docs/guides/development.md) before changing a component.

## Development environment

Use Linux or WSL2 with Python 3.11 or newer:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pip install -r docs/requirements.txt
```

## Changes and tests

- Keep changes focused and preserve compatibility with versioned declarations.
- Keep Registry, workspace, permission and recovery authority in the shared
  runtime. Frontends must use the existing application services.
- Preserve provider neutrality; users select their provider and exact model.
- Update English and Chinese documentation together.
- Run deterministic checks for the changed boundary and describe the result in
  the pull request. Broader runtime changes may require the complete suite.
- Provider calls are separate opt-in checks; automated tests must not use real
  credentials or paid services. Do not add automatic GitHub Actions push triggers.

Pull requests should explain the problem, resulting behavior, checks performed
and relevant limitations. Bug reports should include the installed revision,
platform and a minimal redacted reproduction.

## Repository contents

Commit product source, tests, reusable fixtures, user documentation and licenses.
Keep coding-agent instruction files, session handoffs, internal review reports,
local environment notes, raw logs, provider profiles, credentials, Registries,
workspaces and build output outside the public repository. Public benchmark
summaries should identify their inputs, conditions and failures without exposing
raw transcripts. The ignore and packaging rules help enforce this separation.

Security-sensitive findings should follow [SECURITY.md](SECURITY.md).
