---
name: rpnh-compatibility
description: "Compatibility and known limitations"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: en
  counterpart: compatibility_ZH.md
  revision: "2026-11-22.1"
  status: maintained
---

[English](compatibility.md) | [中文](compatibility_ZH.md)

# Compatibility and known limitations

RPNH supports Linux and WSL2 with Python 3.11 or newer. Native Windows and
macOS are not supported. Optional clients have exact version requirements.

## Frontend versions

| Integration | Selected version | Scope |
|---|---|---|
| Basic | Included with RPNH | Terminal conversation and task controls; no external UI client |
| Codex | 0.163.0 by default | Text, model selection and committed session history over the RPNH app-server adapter |
| Codex candidate | 0.166.0, explicit Python selection | Version-specific protocol candidate; see [selection and limits](codex-candidate-0166.md) |
| Codex candidate | 0.171.0, explicit `candidate-0.171.0` selection | 2026-11-22 WSL2 live batch-summary-v1 and nonempty same-root reopen passed; exact model and call scope below |
| OpenCode | 1.19.4 by default | Attach client over RPNH's HTTP/SSE adapter; see [OpenCode setup](opencode.md) |
| OpenCode candidate | 2.2.5, explicit protocol selection | REST/authentication and non-durable connection/status events only; no complete native frontend or durable message stream |
| OpenCode candidate | 2.2.6, existing explicit selection | One bound ordinary-text session, profile selection, history and checkpoint controls; no native tool execution or full-client certification |
| Integrated DSH Web | Default 0.2.2-alpha.2; opt-in 0.2.2-rc.1 / 0.2.2-rc.5 | `rpnh --frontend dsh`; one bound MainSession, text, model/effort selection and RPNH controls |
| Stock DSH operator plugin | DSH 0.2.2-alpha.2 | Separately installed operator plugin and matching Python companion; manual installation |

Select Codex 0.171.0 with `rpnh frontend setup codex --codex-bin /absolute/path/to/stock/codex --codex-compatibility-profile candidate-0.171.0`.
The following 2026-11-22 results belong to the earlier frontend acceptance
source; they are not a rerun or installation acceptance of the historical RC8 candidate.
The earlier no-model native fresh/reopen check passed, and an independent wheel
reported `available=true` through actual `frontend setup` and `frontend list`.
The separate 2026-11-22 live `batch-summary-v1` task passed on WSL2 with
`gpt-5.6-terra` / `high` via the RPNH official Codex subscription CLI route:
one native input, two Registry-settled calls, and correct count/total/mean/min/max
of `3/36/12/9/15`. Nonempty same-root reopening read the original answer without
new calls. This is evidence for that exact route and task.
DSH Web alpha.2 uses 32 official Web packages; each rc candidate uses 33.
The rc.1 profile is an existing opt-in. On 2026-11-22, rc.5 passed WSL2 frozen
installation, 17/17 native BFF/projection tests with zero skips (including three
Chrome browser cases with a scripted backend), and empty same-root cold reopening
with the actual Python Owner and Web. These scripted/no-model checks are separate
from the first authorized live submission: the driver misread `running` plus
`reconciliation_required` as terminal and actively closed the Owner. The result
was `stopped_by_owner`, zero settled calls, `submission_unknown` and no registered
answer. That first unknown submission remains separate historical evidence.
After repair, a separately approved independent task with a new input set passed
through the genuine browser on source `b65f6682`, with `gpt-5.6-terra` / `high`:
one input, two settled calls, observed completion in 81.464 seconds, correct
count/total/mean/min/max `3/45/15/10/21`, and semantic verifier exit 0. Same-root
read-only reopening passed in 4.486 seconds with zero native submissions; the
same browser session, Registry references and counters were retained, and the
original answer was readable in the actual DOM. This exact live outcome is
separate from the scripted-backend suite. Exact tools, selection and the
independent legacy 0.2.1-rc.2 host are described in the [DSH guide](dsh.md).

The two DSH integrations use different installation paths. Follow the
[integrated Web guide](dsh.md) or [stock operator guide](../../integrations/dsh/operator/README.md)
for the selected integration. Basic, Codex and OpenCode use RPNH-owned sessions,
providers, tools, Registries and recovery. A frontend version does not grant
additional execution permissions.

The Codex compatibility table also retains explicit `pinned-0.155.0` and
`candidate-0.161.0` profiles for legacy callers. Neither is the current default.
Client discovery responses do not imply installed plugins, MCP services, memory,
attachments or arbitrary process execution. Unsupported operations are rejected.

## Source and release packages

[RPNH v0.1.0rc9](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc9) is the current GitHub prerelease. Its published npm package is [`rpnh-bootstrap@0.1.0-rc.9`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.9); `latest` and `next` both pointed to RC9 after release. Anonymous fresh installation with `npm exec --package=rpnh-bootstrap -- rpnh` passed on WSL2 Linux x86_64 with persistent CPython 3.12.13 (Basic), including activation, first setup, and offline doctor. This does not qualify upgrades, rollback, other platforms, or optional frontends. Earlier frontend evidence in this guide retains its original version and scope.

For historical comparison, [RPNH v0.1.0rc8](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc8) is a GitHub prerelease. [`rpnh-bootstrap@0.1.0-rc.8`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.8) is published on npm. Anonymous GitHub and npm installation, activation and version readback passed on WSL2 x86_64 / CPython 3.13.12 (Basic). See [release scope](releases.md). The October 30
[v0.1.0rc3 release](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)
contains the Python wheel and stock DSH operator tarball built from
`bc7755d0a0111a0045c86ed4508b11e1cb4a38bc`. Later changes on `main` are not in
those uploaded files. Install a selected source revision to use later fixes;
changing `main` does not upgrade an existing installation.

Later source fixes include workspace-call correlation, DSH bridge cancellation,
Codex reopening of the bound thread, and OpenCode message-collision/deadlock
handling. They do not change the default client versions. The stock plugin has
no qualified automatic upgrade or cross-version rollback path. See
[releases and installation choices](releases.md).

## Operational limits

- Frontend reconnect restores the conversation view; it does not resume a
  stopped business task. Use the [checkpoint controls](checkpoint-recovery.md).
- A stopped task, an unknown external effect and completed business work are
  distinct states. Reopening a checkpoint does not undo external effects.
- Integrated DSH Web supports only its bound session. Native uploads, arbitrary
  sessions, workspace editing and stock DSH tool execution are unavailable.
- Codex 0.166.0 / 0.171.0 and OpenCode 2.2.5 / 2.2.6 remain opt-in
  candidates, not complete client
  compatibility guarantees. The [OpenCode candidate reference](../reference/opencode-v2-candidate.md)
  lists the supported subset.
- Provider availability depends on user configuration. Offline tests and example
  results do not establish that another provider, model, OS or client version works.

See [troubleshooting](troubleshooting.md) for configuration and recovery errors.
