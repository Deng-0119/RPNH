---
name: rpnh-github-oauth-account
description: Use an explicitly selected existing GitHub OAuth account with the optional interactive HOST.
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: github-oauth-account_ZH.md
  revision: "2026-11-18.1"
  status: source-candidate-not-published
---

This private development candidate preserves existing Linux/Viewer fixes: core `0.1.0rc4.dev2026111806`, optional HOST `0.0.6`, unchanged implementation revision `oauth-candidate-2`. Build the HOST separately from `packaging/github-oauth-host`. Historical 0.0.1/0.0.2 evidence below is not a new real-account acceptance of 0.0.6. Release assets are not yet published.

[English](github-oauth-account.md) | [中文](github-oauth-account_ZH.md)

# Existing GitHub OAuth account

This source candidate adds the explicit `github_oauth/v1` slot. It does not require replacing an existing GitHub CLI OAuth login with a PAT. The optional interactive HOST is not installed by default and does not create, refresh or change a login. Core Registry/PN account binding, call admission, settlement and replay remain authoritative.

## Install the selected pair

For this candidate, obtain these two reviewed artifacts from your project handoff, not an unverified package-index search:

- `rpnh_harness-0.1.0rc4.dev2026111806-py3-none-any.whl`
- `rpnh_gh_interactive_oauth_host-0.0.6-py3-none-any.whl`

The HOST strictly depends on core `0.1.0rc4.dev2026111806`. The ordinary `dev2026111802` core has no OAuth slot and is not interchangeable. Use an isolated installation rather than replacing your daily environment. Given absolute paths to the two supplied files:

```bash
python3 -m venv .venv-oauth
.venv-oauth/bin/python -m pip install "$CORE_WHEEL" "$HOST_WHEEL"
.venv-oauth/bin/python -m pip check
.venv-oauth/bin/python -I -c 'from importlib.metadata import version; print(version("rpnh-harness")); print(version("rpnh-gh-interactive-oauth-host"))'
```

`CORE_WHEEL` and `HOST_WHEEL` are operator-supplied paths, not published download addresses. Dependency installation may use a package index; these commands do not access a GitHub account or call a model. No public release or npm inclusion is claimed here.

## Select the installed HOST

Use the original `AccountProviderSelection`/account-binding configuration. This is the provider-selection object, not a complete task configuration:

```json
{
  "schema_version": "rpnh/installed_account_provider/v1",
  "entry_point": "gh_interactive_oauth",
  "implementation": {
    "schema_version": "rpnh/plugin_implementation/v2",
    "implementation_id": "rpnh.local.gh_interactive_oauth",
    "revision": "oauth-candidate-2",
    "handler": "rpnh_gh_interactive_oauth_host.factory"
  }
}
```

Select `github_oauth/v1` consistently in the original operation account requirement, account selection and binding request. Keep the actual registered principal/task/resource references and binding revision; do not copy synthetic test identifiers. The selected service is `github`, with the user's explicit GitHub login. `oauth-candidate-2` is the HOST implementation revision, not the account binding revision. PAT configurations retain `github_pat/v1` and their own provider.

Set `RPNH_GH_EXECUTABLE` to the absolute path of the approved `gh` executable. The HOST asks gh for the explicit `github.com` account, not whichever account happens to be globally active. It ignores ambient token overrides. Do not print, copy or put credentials into the configuration, prompt, Registry or report.

## What the person sees

Use a foreground terminal with a controlling `/dev/tty`. Before a current call accesses credentials, the HOST displays the selected account, tool and principal/task/call scope, then asks:

```text
Allow this call? [yes/no] (default: no):
```

`yes` accepts (case and surrounding whitespace do not matter). `no`, Enter, EOF and other text deny. It no longer requires typing `ALLOW` plus a call ID. This is only the current call; it is not a standing grant. The prompt includes the identity `GET /user` and selected read-only MCP operation. The user answers directly, not an agent or model-controlled stdin. Multiple HOST processes require separate terminals; the in-process lock is not a cross-process terminal coordinator.

After confirmation, the existing credential source checks that the returned GitHub identity matches the selected login and supplies the original scoped FD. `external_read` admission does not enlarge the user's actual GitHub permissions. The current optional HOST supports GitHub.com OAuth App credentials and declared read-only operations, not Enterprise, refresh flows, GitHub App credentials or general write access.

## Interpreting results

- `account_provider_identity_mismatch`: check the selected implementation revision against the installed optional HOST; loading a factory is not account access.
- `account_use_denied` or no controlling terminal: no automatic stdin fallback or unattended confirmation.
- `account_credential_unavailable`: do not infer the exact failed internal step or HTTP count from this generic result. Check selected account/type and sanitized diagnostics without exposing credentials.
- Original managed replay reads settled evidence; do not rerun an already completed fixed-call launcher to retrieve a result.

The exact HOST0.0.1 candidate has a scoped local real selected-account `get_me` success, identity match, settlement and persisted replay. HOST0.0.2 changes the confirmation parser/UI and has installed offline regression and real-PTY offline evidence, **not a new live-account acceptance**. Identity/MCP HTTP totals in the historical live result remain unknown. Do not treat these results as approval of every account permission, tool, frontend or public release. A genuine new live integration check belongs to a new local call with the user's foreground consent; a prompt-only change is not itself a reason to repeat a successful external call.

[Managed tools](../controlled-managed-tools.md) · [Installation](installation.md)
