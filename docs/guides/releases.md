---
name: rpnh-releases
description: "Releases and installation choices"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: en
  counterpart: releases_ZH.md
  revision: "2026-11-22.1"
  status: maintained
---

[English](releases.md) | [中文](releases_ZH.md)

# Releases and installation choices

RPNH is currently a prerelease. Release tags identify fixed source snapshots;
`main` and a release tag can point to different source versions.

## Versioning for future releases

Starting with releases after RC9, use [Semantic Versioning](https://semver.org/) for formal releases. The Git tags in both the private development and public repositories are `vX.Y.Z`; the matching Python wheel and npm package use `X.Y.Z` without the `v`. A release record ties the two approved source revisions to the exact artifacts and their verification; the repositories need not have identical Git history. On the same compatibility line, increment the patch for fixes, the minor for compatible new features, and use a major version for a breaking public release. During `0.y.z` development, disclose compatibility breaks explicitly instead of silently shipping them as fixes.

Keep candidates and archive references separate from formal releases. A preview must use the matching coordinates accepted by the chosen Python and npm tooling, and must not be tagged as a final release. Existing RC9 names and package channels are not changed by this policy. Publish or move a distribution tag only after approval and verification of the matching artifacts. The first final release on the current series would be `v0.1.0`; it is not published by this documentation update.

## Choose an installation

| Choice | Use |
|---|---|
| [Published release](https://github.com/Deng-0119/RPNH/releases) | Install the files attached to one release and use its matching instructions |
| Selected source revision | Use fixes after a release; record the chosen commit and build in an isolated environment |
| Current `main` | Follow current development; it may differ from the latest uploaded wheel |

[RPNH v0.1.0rc9](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc9) is the current GitHub prerelease. Its published npm package is [`rpnh-bootstrap@0.1.0-rc.9`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.9); `latest` and `next` both pointed to RC9 after release. Anonymous fresh installation with `npm exec --package=rpnh-bootstrap -- rpnh` passed on WSL2 Linux x86_64 with persistent CPython 3.12.13 (Basic), including activation, first setup, and offline doctor. This does not qualify upgrades, rollback, other platforms, or optional frontends. See the [RC9 npm installation guide](linux-npm-candidate.md) for prerequisites and installation options.

For historical comparison, [RPNH v0.1.0rc8](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc8) is a GitHub prerelease. [`rpnh-bootstrap@0.1.0-rc.8`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.8) is published on npm. Anonymous GitHub and npm installation, activation and version readback passed on WSL2 x86_64 / CPython 3.13.12 (Basic). Use Node.js 20+ with npm, `uv` on PATH, and a persistent CPython with `venv`. Pin `npm install -g rpnh-bootstrap@0.1.0-rc.8`, then run `rpnh-bootstrap`. At first publication, both `next` and `latest` pointed to RC8; this is not a stable release.

Historical RC6 remains a GitHub-only verification baseline; its A4 receipt does not certify RC8 or cross-release upgrades.

The October 30 [v0.1.0rc3 release](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3) contains its historical Python wheel and stock DSH operator plugin; that pairing is not part of rc6.

Use the [installation guide](installation.md) for source or wheel installation,
the [stock DSH operator guide](../../integrations/dsh/operator/README.md) for the
matching plugin pair, and [compatibility](compatibility.md) for client versions
and known limitations. Manual installation does not migrate task data or restart
workers. Keep existing task directories when replacing an environment.

## RC8 prerelease scope

RC8 packages Python `0.1.0rc8` and npm `0.1.0-rc.8`, with Codex `0.171.0` and DSH Web `0.2.2-rc.5` as
explicit opt-ins. Defaults remain Codex `0.163.0`, OpenCode `1.19.4` and DSH Web
`0.2.2-alpha.2`. Existing opt-ins remain available. No new OpenCode candidate
support is included in RC8.

RC8 also carries the shared Owner reconciliation fix and bounded
documentation navigation, metadata and Viewer-asset validation updates. Earlier
frontend task results retain their exact source, model, inputs and reopening
scope in [compatibility](compatibility.md). The separate RC8 anonymous installation checks do not qualify in-place upgrade, rollback, other operating systems or every optional frontend.

## What the repository contains

The public repository contains product source, schemas, static assets, tests,
runnable examples, user/developer documentation and licenses. `CHANGELOG.md`
records user-visible changes. Published benchmark summaries identify their
own inputs and source versions; they are not certification of current `main`.

Development-agent instructions, session handoffs, private environment notes,
raw run logs, Registry databases and generated build output are excluded from
version control and source distribution. See [contributing](../../CONTRIBUTING.md)
for the development workflow and [development](development.md) for package checks.

## Licensing

RPNH uses the [MIT license](../../LICENSE). Keep the
[third-party notices](../../THIRD_PARTY_NOTICES.md), DSH upstream license and
Viewer dependency licenses with distributions.
