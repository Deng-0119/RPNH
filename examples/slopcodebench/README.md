# SlopCodeBench `code_search`: native Session-command pilot

English | [中文](README_ZH.md)

This optional development pilot connects a real RPNH owner/AgentLoop to normal
coding and testing commands in the original SlopCodeBench Docker Session. It is
not an untouched official `AgentRunner` run. Local acceptance includes 59
synthetic tests, an installed owner/native-plugin/AF_UNIX command path, and
complete/stop fixtures using the pinned original Docker Session and Snapshot.
Each fixture uses a scripted local provider (3/2 submissions), with no real model
calls or benchmark solution. The Docker image is a minimal Python fixture, not
the general-purpose SCB base. Those fixtures retain their original finite scope.

An authorized real development prefix on 2026-10-08 used the existing
`codex/gpt-5.6-terra` route. Original evaluator results were **13/13, 25/25,
40/47**, with 5, 5 and 14 real model calls. Checkpoint 3 has seven business test
failures; infrastructure completed for all three. CLI exit 0 under ANY_CASE does
not mean all tests passed. Each checkpoint had a 48-call cap and 7200-second
owner wait. There was no replay or grader feedback to the solver. Solver network
was none; build/evaluation used host networking and a same-version download
adaptation. This remains an adapted development condition, not an official
AgentRunner run or a speedup/harness-advantage measurement.
[Results and original evidence](../../evidence/first_wave/20261008/scb-real/README_ZH.md)
retain grader failure diagnostics, model/tool records and submitted source.
Retained original failures and local acceptance evidence are under
`../../evidence/first_wave/20261008/`; earlier records keep their original source
identities and are not relabeled as runs on the integrated commit.

The default scope is the original **1 → 2 → 3 partial prefix**. The pinned task
has five checkpoints. These public tasks were inspected during development and
are not a held-out evaluation. Source revisions and licenses are in
[sources.json](sources.json) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## What actually runs

`CheckpointPilot.run(current_checkpoint, rendered_prompt)` constructs an
`AgentTaskSpec` with one explicit graph node and calls the public `TaskControl`
owner boundary. Its `session_command` managed native plugin is admitted as
`external_write`. Each admitted tool call reaches the current official
`StreamingRuntime.stream(command, env={}, timeout=60)` once through a local
broker. The agent can inspect/edit files and execute its own tests with normal
shell commands in that container. It can retrieve retained large command output
with `read_managed_output`. The agent's terminal `write_file` product is a report;
source files live in the upstream Session workspace.

The model is not given the native host shell, arbitrary host file access,
delegation, evaluator files, future prompts, credentials or the broker socket.
For this selected task, nonempty static assets and extra mounts are rejected.
The solver has `network=none`, preventing public future-checkpoint/oracle fetches.
Required packages must already be in the owner-prepared image. The exact local
Docker image ID is resolved and passed to the runtime; there is no automatic
image pull/build or package install.
`disable_setup=True` is used only after rejecting any configured solver setup
commands; original evaluator setup remains separate and unchanged.

Codex execution selections also require `--codex-binary` naming the explicit
official executable. Before any owner starts, a private profile snapshot binds
the supported RPNH bridge to a private endpoint with `web_search="disabled"` and
`project_doc_max_bytes=0`, preserving the core's native-tool disabling flags.
This bypasses ambient wrappers and inherited project instructions. Model, route,
request budgets and environment bindings are preserved; credential files are
not opened or copied by this preparation. `endpoint-condition.json` records the
controls without publishing private profile values. The SCB package implements
this boundary locally and does not require the optional ERP package.

At each boundary the broker stops the whole solver container, drains command
handlers with bounded waits, then cleans up again to close startup races. Unknown
writes, timeout, lost responses, output overflow or cleanup failure do not become
a successful submission. Duplicate delivery of one exact command identity is
cached, never re-executed; conflicting reuse is rejected. This cache is local to
one live broker, not crash recovery or exactly-once remote execution.
Command responses have a 16 MiB framed-JSON ceiling with 4 KiB reserved headroom.
Overflow aborts the checkpoint with unknown effects. Excess bytes are not
retained or recoverable; `read_managed_output` only reads successfully retained
results and cannot recover an overflowing response.

After confirmed quiescence, `Session.finish_checkpoint` creates the original
filtered snapshot. Hashes describe the exact native extracted file selection,
so excluded `.venv`, `node_modules` and caches are not mistaken for source.
Each immutable checkpoint record binds the before/after source digests, graph
identity, predecessor record hash and actual RPNH terminal result. The next
checkpoint verifies its before digest matches the settled predecessor. The same
source workspace continues, while each checkpoint gets a fresh RPNH Registry.

## Explicit development condition

Select `condition.example.json` deliberately; do not quietly substitute it for
another benchmark configuration:

- Upstream cost/net-cost/step caps are explicitly disabled (0). Positive caps
  are rejected because there is no implemented faithful usage/budget bridge.
- RPNH still has a declared model-call cap (example: 48) and checkpoint wall
  timeout (example: 7,200 seconds). Commands have a 60-second bound and native
  operation IPC has a 180-second deadline. These are ceilings, not measurements.
- A fresh network-isolated solver container is used per checkpoint. Source
  files persist; container-installed state does not. Dependencies must be baked
  into the image. This is an adaptation to make the snapshot boundary quiescent.
- `run.py` reveals only the current prompt using the original renderer, retains
  original order and tests, and invokes the original checkpoint evaluator in the
  separately configured evaluation environment. Evaluation results are not fed
  to the next model task. The chosen native PassPolicy is explicit; infrastructure
  failure stops the pilot. Optional code-quality judging is not run.

The original `AgentRunner` shim remains blocked: its `UsageTracker` stores numeric
usage defaults, while RPNH's public task result exports actual call counts but no
normalized token/cost summary or per-call USD admission hook. The pilot reports
unknown tokens/cost as `null`/`unavailable`; it does not publish zero as observed
billing. The original runner also snapshots in a `finally` block, so its integration
would need to ensure an unquiesced snapshot cannot become accepted evidence.

The actual harness mechanism is Registry-governed agent execution and managed
native tool admission, with upstream-owned workspace continuity. This does not
claim native workspace revision reuse, same-Registry resume, topology mutation,
or automatic process redesign. An optional future reuse showcase needs a separate
condition and result namespace. No core files were changed.

## Offline checks available now

From the RPNH checkout with its existing test dependencies:

```bash
python -m pytest examples/slopcodebench/tests -q
python -m examples.slopcodebench.preflight --prefix 3
```

Tests use synthetic runtimes, transport and owner substitutes at named seams.
They exercise real RPNH graph/plugin declarations but never start an owner,
Docker, a provider, or the official grader. Preflight prints its status and exits
**2**, indicating missing full integration acceptance. A detected Docker binary
is not daemon acceptance. It does not probe denied sockets or retry restrictions.

Optional pinned upstream API inspection without importing or executing it:

```bash
python -m examples.slopcodebench.preflight --runner-source /path/to/slop-code-bench
```

This verifies API shape only. `run.py` separately checks the exact Git revisions
and tracked cleanliness before importing the specified runner checkout.

## Authorized local integration

These commands are instructions, **not evidence they were executed**. First
prepare Python 3.12+, the pinned runner's documented dependencies/Docker base
image, an existing exact RPNH provider profile, and a working POSIX owner socket.
Use the RPNH source baseline `ae09445fe1d9b973502bc5d2c961976c1d2c0163`
with this example applied. An older published wheel (including rc1) is not the
tested API baseline; the package dependency alone does not verify the commit.
Follow the pinned upstream installation/build instructions. Already-authorized
ordinary setup and offline acceptance can proceed without per-step questions.
Obtain the required authorization for real provider calls, new paid services,
permission expansion, and other restricted actions under the applicable local
policy. This does not automatically authorize unrecognized software or
security-sensitive changes.

Install the optional plugin from this trusted checkout into the same Python
that runs RPNH, so its spawned worker can resolve the installed entry point:

```bash
python -m pip install -e . -e examples/slopcodebench
```

Let SCB and PROBLEMS be clean checkouts at the revisions in `sources.json`.
Prepare the runner's local base image with needed dependencies already present.
The source/evaluator checkouts must stay outside the solver workspace and mounts.
Use a short external OUT path to respect UNIX socket path limits.

```bash
python -m examples.slopcodebench.run \
  --runner-source "$SCB" --problems-source "$PROBLEMS" \
  --environment "$SCB/configs/environments/docker-python3.12-uv.yaml" \
  --template "$SCB/configs/prompts/just-solve.jinja" \
  --execution "$EXECUTION" \
  --codex-binary "$OFFICIAL_CODEX" \
  --condition examples/slopcodebench/condition.example.json \
  --output "$OUT" --prefix 3 --pass-policy any-case \
  --acknowledge-development-model-run
```

The optional installed CLI is `rpnh-scb-pilot` with the same arguments. The Python
API is `CheckpointPilot` in `rpnh_scb.pilot` after installing the example. Supply
an official inference Session, an exact resolved solver image ID and a selected
DevelopmentCondition. For Codex selections also supply `codex_binary`; omit the
Codex CLI option/argument for non-Codex profiles. `run()` accepts only the next checkpoint/current rendered
prompt. It does not load later instructions. Do not use the underscored test
injection seams in real experiments.

Expected outputs for an actual successful pilot:

- `condition.json`, `environment.json`, `definition.json`, `input-identities.json`
- Per checkpoint: `request.json`, `before.json`, `after.json`, `snapshot/`,
  local RPNH Registry/control files, `commands.jsonl`, `rpnh-result.json`, `result.json`
- Unchanged original evaluator files saved by its native `grade.save()` method
- `lineage/record-01.json`, subsequent immutable records, and `development-summary.json`

A failing attempt retains `failure.json` and a failed lineage record when the
failure reaches the coordinator; no later checkpoint or evaluation is attempted.
This includes Session spawn and broker-construction failure. Cleanup is attempted
only when a runtime handle exists, and the original exception is preserved.
`failed` describes the checkpoint attempt, not absence of effects: partial source
changes may remain and must not be replayed as though the command never ran.
Automatic resume/retry is unsupported. Stop and diagnose uncertain effects;
never replay a command just to recover output. Source changes after a grade
require a new grade under the new source identity.

## Publication and design feedback

Keep original evaluator results, supplementary checks, mock outcomes and missing
stages separate. `contracts.py`'s raw-workspace helper is only a bounded observation
utility; the pilot uses the native snapshot selection instead. No ledger is a
sandbox or an authority to advance the benchmark.

Publication uses `rpnh/example-evidence/v1` from `../example_validation/` with
checkpoint lineage as a hashed artifact. Copy actual Registry refs unchanged;
never fabricate them from hashes. Keep profiles, credentials, Registry databases
and unreviewed outputs private. The owner requested original failure content for
web analysis: publish reviewed nonsecret logs, retained request/response material
and grader details under `evidence/first_wave/`, preserving their bytes when safe.
If actual secrets occur, change only the necessary fields, document each change
and retain full originals locally. Distinguish request recipes/canonical adapter
returns from vendor wire that was never retained; do not infer a single cause
from a score alone.

Current design feedback: normal command integration is possible with existing
managed-plugin APIs; no workspace seed/export core change is needed for this
route. Portable settled-workspace export and public usage/budget observation
remain separate potential usability gaps, not demonstrated invariant failures.
