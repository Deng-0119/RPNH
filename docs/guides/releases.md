---
name: rpnh-releases
description: "Releases and installation choices"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: en
  counterpart: releases_ZH.md
  revision: "2026-11-02.1"
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

The latest GitHub prerelease is [v0.1.0rc6](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc6), scoped to Basic Linux x86_64 with CPython 3.12/3.13 candidate materials. The npm-format attachment does not indicate npm registry publication. Public installation and activation are pending verification. The `main` checkout still identifies its source as `0.1.0rc5`; use the release tag for rc6.

The October 30 [v0.1.0rc3 release](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3) contains its historical Python wheel and stock DSH operator plugin; that pairing is not part of rc6.

Use the [installation guide](installation.md) for source or wheel installation,
the [stock DSH operator guide](../../integrations/dsh/operator/README.md) for the
matching plugin pair, and [compatibility](compatibility.md) for client versions
and known limitations. Manual installation does not migrate task data or restart
workers. Keep existing task directories when replacing an environment.

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
