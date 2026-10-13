---
name: rpnh-repository-layout
description: "Find the installed entry points, shared authority, frontend source paths and development boundaries."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: repository-layout_ZH.md
  revision: "2026-10-13.3"
  status: source-reviewed
  basis: "public source b16ecce83ba603b3c15f1c770bce172d4f44f88e; not a new runtime validation"
---

[English](repository-layout.md) | [中文](repository-layout_ZH.md)

# Repository map

[Documentation home](../index.md) · [API map](../reference/api-index.md) · [Development](development.md)

Follow a user action from its entry point to the code that owns it. The links
below select source `b16ecce8`; the [changelog](../../CHANGELOG.md) records later
changes. Source paths describe responsibility, not extra installation steps.

## Start from an action

| Action | First source to read | Next layer / contract |
|---|---|---|
| Launch `rpnh`, configure, choose a frontend | [cpn/rpnh_cli.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py) | [user_config.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/user_config.py), [onboarding.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/onboarding.py), [configuration](configuration.md) |
| Submit main input or control a child | [main_session.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/main_session.py), [task_control.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/task_control.py) | [frontend_application.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/frontend_application.py), [sessions](usage.md) |
| Declare and lower agent work | [module.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/module.py), [registration.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registration.py), [compiler.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/compiler.py) | [agent_tasks.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_tasks.py), [agent_workflows.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_workflows.py), [declarations](../reference/declarations.md) |
| Admit, dispatch and settle a run | [run.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/run.py), [harness.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/harness.py) | Registry owner/transaction services; [runtime contract](../reference/runtime-registry.md) |
| Add a native tool | [plugins/api.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/api.py), [catalog.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/catalog.py), [runtime.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/runtime.py) | [customization](customization.md), [plugin contracts](../reference/extensions-observation.md) |
| Query authorized sources or exchange definitions | [registry_read_session.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_session.py), [subnet_exchange.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/subnet_exchange.py) | [independent reader](independent-registry-reader.md), [typed exchange](typed-registry-exchange.md) |
| Co-create/adopt/download a project | [project_local.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_local.py), [project_entry.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_entry.py) | [project operations](project-entry.md), [version/API map](../reference/api-index.md) |
| Export a shipped example | [examples/cli.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/cli.py), [gallery.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/gallery.py), [catalog.json](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/catalog.json) | [installed exports](examples.md#export-an-example-and-make-it-yours) |

## Core and package ownership

| Path | Responsibility |
|---|---|
| `cpn/rpnh/` | Shared declarations, PN structures, owners, tasks, sessions and frontend-neutral application services |
| `cpn/rpnh/registry/` | Append-only object/event authority, structural validation, exact reads, settlement, child execution nets and recovery |
| `cpn/rpnh/collaboration/` | Opt-in authoring/version/Branch/merge, typed reads, subnet/package and controlled project contracts |
| `cpn/rpnh/net_operations/` | Definition transforms and explicit owner adoption/reentry plans |
| `cpn/rpnh/pn_validation/` | Supported finite analysis, witnesses and report verification |
| `cpn/components/` | Reusable lowering/execution components, including the optional AgentLoop and registered services |
| `cpn/plugins/` | Native plugin SDK/catalog/runtime and explicitly selected managed tools/programs |
| `cpn/llm_adapters/` | Shared configured provider/local-process transport composition for trusted adapters |
| `cpn/schemas/`, `cpn/config/` | Packaged schema and configuration materials; user-owned profiles remain outside source control |

The Registry and PN are the shared core. The business PN expresses agent
operations and handoffs. [file_execution_net.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/file_execution_net.py),
[workspace_settlement.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/workspace_settlement.py) and Registry
execution services implement subordinate file/workspace mechanisms under exact
business firings, in the same Registry. They do not turn a domain algorithm or
an AgentLoop transcript into an execution PN.

Start with [architecture](../ARCHITECTURE.md) and the [closed execution design](../architecture/design.md)
before changing authority or settlement. Underscore-prefixed implementation
packages such as Registry `_event_store` and `_invocation` are not extension
entry points; [API reference](../reference/api-index.md) distinguishes exported
facades from trusted HOST interfaces.

## Frontend source paths

| Surface | Python / host implementation | Presentation assets and protocol | Operator guide |
|---|---|---|---|
| Basic | [rpnh_cli.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py), [main_session.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/main_session.py) | Terminal input and task controls in the CLI | [Usage](usage.md) |
| Shared Codex/OpenCode application | [frontend_application.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/frontend_application.py), [session_access.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/session_access.py) | Canonical session ownership and frontend-neutral command services | [Agents](../reference/agents.md) |
| Codex | [codex_app_server.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_app_server.py), [codex_history.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_history.py) | [codex_compatibility.v1.json](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_compatibility.v1.json); pinned external client | [Adapters](adapters.md), [history](../reference/codex-history.md) |
| OpenCode | [opencode_launcher.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_launcher.py), [opencode_http.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_http.py), [opencode_protocol.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_protocol.py) | [opencode_compatibility.v1.json](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_compatibility.v1.json); pinned external client | [OpenCode](opencode.md) |
| DSH | [cpn/dsh/launcher.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/launcher.py), [backend.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/backend.py) | [integrations/dsh/src/app.ts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/integrations/dsh/src/app.ts) and its bundled bridge | [DSH](dsh.md) |
| Read-only Viewer | [frontend/server.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/server.py), [dashboard.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/dashboard.py), [comparison_context.py](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/comparison_context.py) | [cpn/frontend/static/app.js](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/static/app.js), [index.html](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/static/index.html), [style.css](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/static/style.css) and adjacent `.mjs` modules | [Viewer](viewer.md), [comparison](comparison-context.md) |

`cpn/frontend/static/` contains the browser assets shipped in the wheel.
`frontend/net-viewer/` contains the existing asset build script, lockfile and
JavaScript checks, not a second runtime UI tree. Its
[build script](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/frontend/net-viewer/build.mjs) prepares the vendor assets.

Basic, Codex and OpenCode open the same canonical MainSession root sequentially;
one shared owner lease prevents concurrent writable frontends. DSH has its
own registered HOST adapter and uses the shared provider layer. The Viewer is
read-only presentation. Historical branches are provenance, not installation
overlays to combine with `main`.

## Examples, documentation and maintenance

| Location | Purpose / entry |
|---|---|
| `examples/` | Source-checkout examples, fixtures and real Viewer figures; [catalog](../../examples/README.md) |
| `cpn/examples/` | Packaged provider-neutral material and named export machinery; [export guide](examples.md) |
| `docs/guides/` | Task-oriented installation, configuration, operation and extension instructions |
| `docs/reference/` | Exact declaration, runtime, API, query and version-operation contracts |
| `docs/architecture/`, `docs/ARCHITECTURE.md` | Explanations of the execution/ownership design |
| `docs/results/`, `docs/validation/` | Dated reviewed evidence with exact source/scope; [result index](../results/README.md) |
| `scripts/`, `tools/`, `tests/` | Existing documentation, package and deterministic validation helpers; [development guide](development.md) |
| Root policies | [Contributing](../../CONTRIBUTING.md), [security](../../SECURITY.md), [changelog](../../CHANGELOG.md), [license](../../LICENSE), [third-party notices](../../THIRD_PARTY_NOTICES.md) |

Generated profiles, credentials, raw provider transcripts, Registry databases,
run directories, environments and build dependencies stay out of the public
repository. Keep operational outputs in caller-selected private directories.
Public result summaries retain their dated validation boundary. A documentation
link check is not a runtime or live-model test.
