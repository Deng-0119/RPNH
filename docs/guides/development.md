---
name: rpnh-develop-and-validate
description: "Maintain documentation and verify actual artifacts without implicit CI or model calls."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: development_ZH.md
  revision: "2026-09-29.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](development.md) | [中文](development_ZH.md)

# Development, validation and release maintenance

## Work from an exact version
Record the source revision, relevant adapter revisions and commands actually run. A feature branch containing main is not proof that both adapters work together. Preserve other contributors' changes; re-read the target HEAD before a fast-forward commit. Documentation changes do not authorize runtime redesign, real provider calls, GitHub Actions, public releases or website deployment.

## Deterministic tests and installed checks
Prepare an isolated Linux environment from the selected source and install its declared test requirements:

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

This is the repository's general test entry, not a command executed by every
change. Use focused affected-boundary tests for documentation and local fixes;
run the complete suite when a broad runtime/schema refactor can affect unrelated
components or when preparing an explicitly scoped full candidate. Real-provider
checks require separate authorization and are appropriate only when offline
tests cannot establish the changed transport or end-to-end behavior. Test scope
is evidence for the change, not a progress counter. The single `.[test]` extra
declares pytest, numpy and scipy. Record missing dependencies rather than
attributing collection failure to runtime semantics. Do not relax version bounds
merely to use preinstalled libraries.

The repository includes the lockfile-pinned viewer bundles and their license
texts so normal source and sdist wheel builds do not require Node. When those
dependencies are deliberately updated, regenerate and test the committed
assets explicitly:

```bash
npm ci --ignore-scripts --prefix frontend/net-viewer
npm test --prefix frontend/net-viewer
npm run build --prefix frontend/net-viewer
```

For an actual wheel smoke, build/install the wheel using [installation](installation.md). Then set `RPNH_SOURCE` to that source tree and `INSTALLED_PYTHON` to the separate environment's Python:

```bash
: "${RPNH_SOURCE:?Set the approved source root}"
: "${INSTALLED_PYTHON:?Set the wheel environment Python}"
python "$RPNH_SOURCE/scripts/check_installed_docs.py" \
  --python "$INSTALLED_PYTHON" --source-root "$RPNH_SOURCE"
```

The checker verifies installed provenance/package resources, uses a fresh temporary HOME/config directory, and runs only help plus empty-catalog init/build/check/list. A child-process audit guard rejects provider-capable socket connects, subprocess launches and SQLite connections. It neither opens a Registry writer nor supplies model credentials. Passing this smoke does not test interactive basic/Codex, plugin execution, DSH fixed-host behavior, resume or a live model. It does not install dependencies for you.

## Documentation checks and local site
The maintained sources are the Markdown pages under `docs/guides`, `docs/architecture`, `docs/reference`, both docs indexes and the README language pair. `_ZH.md` is the Chinese counterpart, with the same topic name/revision. Protocol fields and commands remain unchanged. These pages are ordinary documentation, not executable plugin skills.

```bash
python -m pip install -r docs/requirements.txt
python scripts/docs.py check
python scripts/check_doc_examples.py
python -m unittest discover -s tests -p 'test_docs_site.py' -v
python scripts/docs.py build --output /tmp/rpnh-docs-site
```

The build output directory must not already exist. The local site uses the installed `markdown-it-py` parser; no JavaScript, remote assets, runtime imports, model calls or hosting service are required. It renders the same files, rewrites internal Markdown links, provides topic navigation, heading anchors and per-page language switching. Open its `index.html` locally. Source checks validate metadata, counterparts, headings, links and fenced-code syntax. JSON/Python/Bash syntax checks **do not execute the examples**. Build success and link validation are reported separately; external URLs are not probed by the offline checker.

Documentation tests skip explicitly when the optional Markdown/YAML libraries are absent, so installing only core test dependencies does not cause documentation-test collection to fail. Install `docs/requirements.txt` to run rather than skip them. The schema-example checker uses the runtime's declared `jsonschema` dependency.

Sphinx/MyST/PyData were evaluated against their official configuration documentation, but dependency installation was unavailable in the documentation environment. They are not the accepted builder and no Sphinx success is claimed. The small local renderer is documentation-only and can later be replaced without changing the Markdown body or runtime.

## Contributions, compatibility and issue reports
Read the repository working agreement, keep a focused change, include a deterministic regression for changed behavior and update the relevant guide/reference/example. Separate code movement from semantic changes. Classify interfaces as declaration contracts, advanced trusted-host interfaces or private internals; do not advertise every generated symbol as stable. Keep versioned schema compatibility and prior evidence provenance explicit.

Reports need revision, platform, minimal redacted reproduction, expected/actual behavior and affected boundary. Consult [troubleshooting](troubleshooting.md) before sharing logs. Do not commit raw Registry databases, user workspaces, model transcripts, secrets or private endpoints. Workflows or PR operations require a separate trigger check; a skip string in a commit message is not sufficient evidence of safety.

## License, attribution and release status

RPNH is licensed under the root MIT License. Third-party components retain
their own terms; preserve `THIRD_PARTY_NOTICES.md`, the pinned DSH upstream
license and the viewer library licenses in source and built distributions.

This source tree is a release candidate, not evidence that every optional host
or user-owned provider route works. Validate the actual wheel and installed
entry points before publication. Keep private Registry data and live-provider
evidence outside the public repository.

Official tool references: [Markdown parser](https://markdown-it-py.readthedocs.io/en/latest/using.html), [Sphinx Markdown configuration](https://www.sphinx-doc.org/en/master/usage/markdown.html), [PyData installation](https://pydata-sphinx-theme.readthedocs.io/en/stable/user_guide/install.html), [Python packaging](https://packaging.python.org/en/latest/tutorials/packaging-projects/). The runtime support facts above come from the inspected repository, not these external guides.
