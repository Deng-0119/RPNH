# Linux, Viewer and npm source integration

## Current distribution status

The [rc5 prerelease](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc5) now includes core `0.1.0rc5`, optional OAuth HOST `0.0.8`, Linux x86_64 CPython 3.12/3.13 Basic dependency wheels, both v3 sidecars and an npm-format GitHub attachment. This is not an npm registry publication. Local candidate installation passed its scoped checks; actual public application installation is still awaiting local acceptance. The dot attempt exited before application-root creation with a metadata-read error; a separate diagnostic timed out during HTTPS proxy CONNECT. Neither result proves the original failure cause or public installation success. Previously published wheel binaries are not overwritten.

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

`packaging/npm-bootstrap` remains `private: true`, version `0.0.0-private`. This repository directory is the source transport template, not the generated rc5 release attachment. Historical template instructions describe a local controller wheel, persistent Python and uv; the generated attachment embeds the matching rc5 controller and selects Python automatically. No new supported public npm install command is claimed.

rc5 supplies the fixed version, matching controller/application wheels and real release metadata/assets. The remaining gate is actual public acquisition and installed application startup, followed by npm registry publication and registry-path verification. Existing local tarball activation is not public-registry transport or cross-release upgrade evidence. Do not replace existing rc3 assets or fabricate asset identities.

This publication scope is Linux/WSL2. macOS is not a prerequisite for this source update and is not claimed supported by these results. Production account-provider wiring, full frontend/API certification and old-release migration remain separately scoped.

[中文](linux-npm-candidate_ZH.md)
