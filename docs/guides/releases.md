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

## Choose an installation

| Choice | Use |
|---|---|
| [Published release](https://github.com/Deng-0119/RPNH/releases) | Install the files attached to one release and use its matching instructions |
| Selected source revision | Use fixes after a release; record the chosen commit and build in an isolated environment |
| Current `main` | Follow current development; it may differ from the latest uploaded wheel |

At the checked public baseline, the latest GitHub prerelease is [v0.1.0rc6](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc6), scoped to Basic Linux x86_64 with CPython 3.12/3.13 candidate materials. The npm-format attachment does not indicate npm registry publication. The formal rc6 npm-format tgz subsequently passed A4 installation and activation on WSL2 / CPython 3.13.12 Basic; npm registry publication remains pending. The checked public baseline still identifies its source as `0.1.0rc5`; use the release tag for rc6.

The October 30 [v0.1.0rc3 release](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3) contains its historical Python wheel and stock DSH operator plugin; that pairing is not part of rc6.

Use the [installation guide](installation.md) for source or wheel installation,
the [stock DSH operator guide](../../integrations/dsh/operator/README.md) for the
matching plugin pair, and [compatibility](compatibility.md) for client versions
and known limitations. Manual installation does not migrate task data or restart
workers. Keep existing task directories when replacing an environment.

## Local RC8 candidate (not yet published)

This local candidate targets Python `0.1.0rc8` and npm `0.1.0-rc.8`, assembled
from the public baseline with Codex `0.171.0` and DSH Web `0.2.2-rc.5` as
explicit opt-ins. Defaults remain Codex `0.163.0`, OpenCode `1.19.4` and DSH Web
`0.2.2-alpha.2`. Existing opt-ins remain available. No new OpenCode candidate
support is included in RC8.

The candidate also carries the shared Owner reconciliation fix and bounded
documentation navigation, metadata and Viewer-asset validation updates. Earlier
frontend task results retain their exact source, model, inputs and reopening
scope in [compatibility](compatibility.md); those results do not establish RC8
package installation, public acquisition, upgrade or rollback acceptance.
This source state is not a GitHub release or an npm registry publication.
Formal GitHub installation acceptance A for RC8 awaits separate authorization.

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
