# Linux installation and npm source candidate

This branch is a source-integration candidate, not a new stable release. The default branch and existing release assets are unchanged. Do not replace an existing installation with the locally built `0.1.0rc3` wheel: its retained version identifies the development baseline, not a newly published release.

## Included changes

The npm transport delegates to the Python bootstrap and original managed installation lifecycle. Linux invocation supervision keeps child work in its original process group and owns descendant cleanup. Reopening a session observes pending work rather than silently restarting it; execution remains explicit. Installation, frontend selection, runtime/account bindings and their shared Registry/PN dependencies accompany the entry points. This is not merely an npm launcher patch: the necessary runtime compatibility closure also touches tool execution and Viewer management modules.

The source package is in `packaging/npm-bootstrap`; its bilingual README describes its exact prerequisites and local tarball commands. It remains `private: true`, version `0.0.0-private`. A local controller wheel, persistent Python and installed uv are required. There is no supported public `npm install` command yet. Updating the transport alone is not an application upgrade.

## Verification on 2026-11-16

The integrated candidate passed 67 focused Python bootstrap, installation-contract and platform tests, seven npm transport tests, Python compilation, wheel and source-archive builds. The wheel includes the bootstrap console entry and candidate frontend manifests. These are offline/source checks, not a new real-model acceptance run.

This newly assembled public candidate requires its own native validation. Offline checks do not establish real-model acceptance. Native process-supervision tests could not pass in the current validation environment because `/proc/self/task/<pid>/children` is unavailable. No guard was removed to make them pass.

## Before promotion

- Review the complete candidate against the public baseline, including shared runtime and Viewer changes.
- Validate this exact candidate in native Linux/WSL, including installation, subprocess cleanup and retained-data reopening.
- Choose a new release version and package identity; prepare matching public controller/application wheels, dependency assets and release metadata.
- Validate public npm acquisition and a genuinely supported application-version update. Same-payload frontend reconfiguration is not cross-release upgrade evidence.
- Preserve the existing release artifacts. macOS, OpenCode and old-release data migration are not established by this candidate.

[Chinese](linux-npm-candidate_ZH.md)

## Dynamic entrypoint closure follow-up

This candidate also aligns AgentLoop versioned tool identities, DSH result delivery, plugin exports, optional MCP/account-provider adapters and skill inputs with the shared runtime. Historical identity fields remain readable without adding new legacy-format validation; this is not a claim that every older identity format has been removed.

Focused installation tests (67) and component tests (36) pass. Native worker/AgentLoop integration did not complete successfully in the current validation environment: process startup failed before a ready handshake, and the required procfs child-list interface is unavailable there. A DSH parallel-tool diagnostic ended without a normal test result; its cause is unresolved. These outcomes are not waived or counted as passing. Exact-candidate native validation remains necessary before main promotion. This source candidate is not an npm registry release or a cross-release upgrade certification.
