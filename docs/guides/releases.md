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
`main` contains development since the latest tagged release.

## Choose an installation

| Choice | Use |
|---|---|
| [Published release](https://github.com/Deng-0119/RPNH/releases) | Install the files attached to one release and use its matching instructions |
| Selected source revision | Use fixes after a release; record the chosen commit and build in an isolated environment |
| Current `main` | Follow current development; it may differ from the latest uploaded wheel |

The latest uploaded software prerelease is
[v0.1.0rc3](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3), with
`rpnh_harness-0.1.0rc3-py3-none-any.whl` and
`dsh-rpnh-operator-0.1.0-rc.3.tgz`. Both belong to the October 30 release.
Current source retains the same version metadata but includes later changes;
the version string alone does not distinguish those source revisions.

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
