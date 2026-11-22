---
name: rpnh-linux-npm-candidate
description: "Distribution scope, evidence and limits for the Linux, Viewer and npm source integration."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: linux-npm-candidate_ZH.md
  revision: "2026-11-22.1"
  status: source-reviewed
---

[English](linux-npm-candidate.md) | [中文](linux-npm-candidate_ZH.md)

# Linux, Viewer and npm source integration

## Current distribution status

The local RC8 candidate (Python `0.1.0rc8`, npm `0.1.0-rc.8`) is not yet published; see [scoped release status](releases.md). The rc6 status below preserves the earlier public-baseline record and does not establish RC8 release or installation acceptance.

The [rc6 GitHub prerelease](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc6) contains the matching Basic Linux x86_64 application wheel, CPython 3.12/3.13 dependency wheels, two v3 sidecars, and an npm-format GitHub attachment. The exact candidate passed scoped offline CLI checks. The first anonymous release metadata readback succeeded, but its download attempt timed out after the GitHub redirect; that original failure is retained. The formal rc6 npm-format tgz subsequently passed A4 installation and activation on WSL2 / CPython 3.13.12 Basic. The npm registry release is still pending. This does not qualify upgrade, rollback, optional frontends, or macOS.

The Linux/npm runtime integration is already on public main at baseline `3d61bcf17b22deee28c5db7a427cda0e81a7f6bd`. The 2026-11-17 Viewer/query source update adds 13 product files and 13 tests/fixtures from the reviewed integrated candidate. This is a source update, not a new stable wheel or an npm registry publication. Existing release assets remain unchanged; do not overwrite rc3 assets with a development build.

## Included behavior

The npm transport delegates to the Python bootstrap and original managed installation lifecycle. Linux process supervision retains process-group ownership and descendant cleanup. Reopening observes pending work rather than silently restarting it. Shared runtime bindings, AgentLoop versioned identities, DSH results, optional MCP/account-provider adapters and skill inputs continue to use the original Registry/PN.

Viewer changes cover Registry navigation, material availability/body policies, managed material locations and connection lifetime. Terminal-location access remains explicitly opt-in under the original contract; ordinary executors do not acquire terminal capabilities. Relation queries retain forward/reverse/either filtering, logical ObjectRef handling and same-cut relation-row reuse. This is not a new parallel authorization or fact store.

## Scoped verification

- Baseline offline evidence: 67 installation/platform checks, 36 component checks, 7 Node transport tests and package builds.
- Exact public baseline 3d61bcf: three native WSL2/Linux integrations passed on 2026-11-16 (Registry worker/replay 6.15s, AgentLoop identity/receipt 33.02s, DSH ordered parallel success/failure 72.61s). These use scripted model ports and the original fixture transport, not a new provider API or native DSH frontend acceptance. Earlier dot startup/diagnostic failures are historical, not outstanding repetitions of these checks.
- Integrated Viewer/query source: independent 20 scoped tests passed; installed private candidate relation tests 14 passed and version tests 7 passed. These sets overlap and must not be summed as unique coverage. A broader eight-file run was manually stopped and is not a full pass.
- Exact private wheel `0.1.0rc4.dev2026111702`: independently installed on WSL2/Linux 6.6.87.2, CPython 3.13.12; three original terminal-location native tests passed first attempt, 0 failures/errors/skips, 15.273s. Actual AF_UNIX OwnerEventLoop was used; model calls: zero.
- Browser evidence remains scoped: earlier ordinary six-stage/nine-image flow and later terminal HTTP/DOM flow belong to their respective source windows. The final installed wheel reopened an existing run and Registry pages successfully, but this is not a repeat of every browser flow. Human visual acceptance remains unconfirmed.

## npm and release work still separate

`packaging/npm-bootstrap` remains `private: true`, version `0.0.0-private`. This repository directory is the source transport template, not the generated rc6 release attachment. Historical template instructions describe a local controller wheel, persistent Python and uv; the generated attachment embeds the matching rc6 controller and selects Python automatically. No new supported public npm install command is claimed.

rc6 supplies the fixed version, matching controller/application wheels and real release metadata/assets. The formal rc6 tgz A4 installation and activation has passed; npm registry publication and registry-path verification remain pending. RC8 is a separate local candidate, with final formal GitHub installation acceptance A awaiting separate authorization. Existing local tarball activation is not public-registry transport or cross-release upgrade evidence. Do not replace existing rc3 assets or fabricate asset identities.

This publication scope is Linux/WSL2. macOS is not a prerequisite for this source update and is not claimed supported by these results. Production account-provider wiring, full frontend/API certification and old-release migration remain separately scoped.

[中文](linux-npm-candidate_ZH.md)
