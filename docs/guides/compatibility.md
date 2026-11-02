---
name: rpnh-compatibility
description: "Compatibility and known limitations"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: en
  counterpart: compatibility_ZH.md
  revision: "2026-11-02.1"
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
| OpenCode | 1.19.4 by default | Attach client over RPNH's HTTP/SSE adapter; see [OpenCode setup](opencode.md) |
| OpenCode candidate | 2.2.5, explicit protocol selection | REST/authentication and non-durable connection/status events only; no complete native frontend or durable message stream |
| Integrated DSH Web | Pinned 0.2.1-rc.2 source | `rpnh --frontend dsh`; one bound MainSession, text, model/effort selection and RPNH controls |
| Stock DSH operator plugin | DSH 0.2.2-alpha.2 | Separately installed operator plugin and matching Python companion; manual installation |

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

The current source metadata is `0.1.0rc3`. The published October 30
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
- Codex 0.166.0 and OpenCode 2.2.5 remain opt-in candidates, not complete client
  compatibility guarantees. The [OpenCode candidate reference](../reference/opencode-v2-candidate.md)
  lists the supported subset.
- Provider availability depends on user configuration. Offline tests and example
  results do not establish that another provider, model, OS or client version works.

See [troubleshooting](troubleshooting.md) for configuration and recovery errors.
