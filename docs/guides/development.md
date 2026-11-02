---
name: rpnh-develop-and-validate
description: "Development and packaging"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: en
  counterpart: development_ZH.md
  revision: "2026-11-02.1"
  status: maintained
---

[English](development.md) | [中文](development_ZH.md)

# Development and packaging

## Set up a checkout

Use Linux or WSL2 with Python 3.11 or newer. From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pip install -r docs/requirements.txt
python -m pip install build
```

The `test` extra includes pytest, NumPy and SciPy. Credentials and generated
profiles belong outside the repository. Tests use deterministic inputs;
live-provider checks require a separately configured opt-in environment.

## Run checks

Run tests covering the changed component, or the complete suite for broad changes:

```bash
python -m pytest -q
```

Documentation checks validate language pairs, local links and code-block syntax:

```bash
python scripts/docs.py check
python scripts/check_doc_examples.py
python -m unittest discover -s tests -p 'test_docs_site.py' -v
python scripts/docs.py build --output /tmp/rpnh-docs-site
```

The site output directory must not exist. The renderer produces a local static
site with no runtime imports, model calls or remote assets. Syntax checks do not
execute the documented examples; external links are not fetched.

## Build and inspect distributions

```bash
python -m build
python -m pytest -q tests/test_dsh_distribution.py::test_distribution_contains_runtime_and_public_source_assets
```

The build creates a wheel and source distribution. The packaging check verifies
runtime resources, public documentation, installer and exclusion of development
instructions. Source archives include the tests and reusable examples needed for
development. Keep generated packages in `dist/`, which is ignored by Git.

Install the built wheel in a separate environment, then run the installed smoke
from [installation](installation.md). To use the bundled checker, set the source
root and the installed environment's Python:

```bash
: "${RPNH_SOURCE:?Set the source root}"
: "${INSTALLED_PYTHON:?Set the wheel environment Python}"
python "$RPNH_SOURCE/scripts/check_installed_docs.py"   --python "$INSTALLED_PYTHON" --source-root "$RPNH_SOURCE"
```

This checks package resources and zero-model help/configuration/example commands.
It does not run a business workflow or certify an optional native frontend.

## Viewer assets

The repository includes built Viewer dependencies and their licenses, so normal
wheel builds do not require Node. When updating those dependencies:

```bash
npm ci --ignore-scripts --prefix frontend/net-viewer
npm test --prefix frontend/net-viewer
npm run build --prefix frontend/net-viewer
```

Commit the matching lockfile, static assets and third-party license texts.

## Documentation and contributions

Guides explain user tasks; references describe interfaces; architecture pages
explain the design. Maintain the English/Chinese counterparts together, including
their topic metadata and links. The Markdown files are ordinary documentation.

See [contributing](../../CONTRIBUTING.md) for change scope and issue reports,
[releases](releases.md) for distribution choices, and
[compatibility](compatibility.md) for supported client versions. Preserve the
root MIT license, third-party notices, DSH upstream license and Viewer licenses.
