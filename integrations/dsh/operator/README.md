---
metadata:
  document-kind: how-to
  language: en
  counterpart: README_ZH.md
  status: prerelease
---

[English](README.md) | [中文](README_ZH.md)

# RPNH for DSH: existing-task operator

**RPNH operator 0.1.0-rc.3 + RPNH Python 0.1.0rc3** is an independent,
third-party adaptation for **DeepSeek Harness 0.2.2-alpha.2**, not an official
DeepSeek product or endorsement. Upstream branding and licenses remain intact.
The plugin identifies itself as **RPNH 适配版 / for DeepSeek Harness** and shows
its operator version separately from DSH. Both components are prereleases.

This is a manually installed existing-task plugin, not a replacement for the
DSH runtime, a task-creation UI, or a managed automatic-upgrade release.

## Exact prerequisites and downloads

- Linux or WSL2, Python >=3.11; Node >=24.9.0.
- An owner-controlled **DSH 0.2.2-alpha.2** host. Do not silently replace another
  host version. Source tag: `dsh-v0.2.2-alpha.2`; source commit:
  `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`. The published npm host's embedded
  build identity is separate from that source pin.
- Exact host peers: Cordis `4.0.6-alpha.1`, dsh-tools `0.2.2-alpha.2`,
  Schemastery `3.18.6-alpha.1`. DSH supplies the client React/services; this
  tarball does not vendor them or Python.

Use the **matching pair** from [RPNH v0.1.0rc3](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3):

1. [Python wheel](https://github.com/Deng-0119/RPNH/releases/download/v0.1.0rc3/rpnh_harness-0.1.0rc3-py3-none-any.whl)
2. [Operator tarball](https://github.com/Deng-0119/RPNH/releases/download/v0.1.0rc3/dsh-rpnh-operator-0.1.0-rc.3.tgz)

If either asset is absent, stop rather than substituting an older or similarly
named package. Keep old environments, profiles and Registry directories.

## Install into a separate environment

Download both assets to an owner-controlled directory. From that directory:

```sh
python3 -m venv .venv-rpnh-stock
.venv-rpnh-stock/bin/python -m pip install ./rpnh_harness-0.1.0rc3-py3-none-any.whl
export RPNH_PYTHON="$(pwd)/.venv-rpnh-stock/bin/python"
export RPNH_CONTROL_ROOT=/absolute/path/to/your/existing/task-control
# In the exact alpha.2 DSH environment:
dsh plugin --profile rpnh-operator add "$(pwd)/dsh-rpnh-operator-0.1.0-rc.3.tgz"
dsh --profile rpnh-operator --dump-config
```

The official profile workflow loads the packaged `cordis.patch.yml`. The whole
RPNH repository root is not the bundle. `RPNH_PYTHON` must be the absolute
interpreter of the matching, non-editable installation; `RPNH_CONTROL_ROOT`
must be an existing absolute directory owned by the operator. The bridge uses
`python -I -m cpn.dsh.operator`, not cwd/PYTHONPATH module selection.

The patch reads those two environment variables. When overriding its config,
retain the `rpnh-operator` row id and package name. This is only a config fragment:

```yaml
controlRoot: /absolute/path/to/your/existing/task-control
python: /absolute/path/to/.venv-rpnh-stock/bin/python
enableControl: false
```

Start with read-only configuration. A new empty directory can verify an empty
list, but is not a populated task store. Create tasks first through the existing
RPNH CLI/Python TaskControl workflow; the plugin neither creates tasks nor
recovers pending launches while loading/reading. See the
[checkpoint and recovery guide](https://github.com/Deng-0119/RPNH/blob/v0.1.0rc3/docs/guides/checkpoint-recovery.md).

## Authority, controls and uncertain outcomes

The five read tools are `rpnh_task_list`, `status`, `result`, `net`, and
`checkpoints` (all use the `rpnh_task_` prefix). Owner configuration
`enableControl: true` additionally exposes `stop`, `resume`, and `reopen` through
DSH's original approval pipeline. Enable them only under existing authorization:
resume/reopen can continue authorized model and external-tool work. Existing
host deny remains deny. No new Registry, PN, scheduler or permission store exists.

Results and graph references come from original TaskControl/Registry facts.
Normal resume selects the latest valid committed checkpoint; explicit reopen
selects an earlier committed cut while retaining later history. Neither undoes
external effects. `STOP_REQUESTED` is not proof of completed drain.

Pre-dispatch cancellation is `NOT_DISPATCHED`. Cancellation or a lost response
after dispatch is `OUTCOME_UNKNOWN`: inspect original task state/checkpoints and
do not automatically replay writes. Cancelling the bridge wait does not stop the
independent worker. Unloading the plugin likewise does not stop existing tasks.

## Manual updates and removal

The native RPNH version panel distinguishes upstream and adapter. When native
Connection is present, the shared Python checker may inspect qualified offers;
`RPNH_UPDATES=off` disables automatic checks. There is no supported automatic
update offer for this release and no managed installation claim.

Any qualified future offer exposes Update / Not now / Skip this version through
owner-only native routes. For this stock host, Update only gives manual matching
package instructions. It never hot-installs, restarts workers, migrates data or
resumes tasks. Exit the affected DSH host before manually replacing the pair.
Use the official profile package workflow for removal; preserve task data.

## Validation boundary

The [release validation summary](https://github.com/Deng-0119/RPNH/blob/v0.1.0rc3/docs/validation/dsh-stock-0.1.0rc3.md)
describes only checks of the final release pair. An empty-list Loader check does
not validate real task completion, UI, approval, worker recovery or model quality.
This release does not qualify managed cold installation, upgrade or rollback.
Tests and private execution records are not included in the operator tarball.
