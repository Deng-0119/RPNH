# ERP-Bench with RPNH

[中文](README_ZH.md)

This example connects RPNH-owned model execution and managed tools to two
unchanged ERP-Bench task sources. `2000_easy_01_buy_only_baseline` is the smoke
case; `2299_hard_repair_plan_hard` is the repair showcase. The outage is present
at the start of the repair task. These two cases are not an aggregate benchmark
or evidence of measured RPNH superiority.

RPNH owns the actor, tool admission, TaskControl lifecycle and Registry facts.
Harbor owns the official Docker environment and original verifier. The example
does not launch the upstream Pi/Claude solver, regenerate tasks, or modify core.

## Source and installation

The fixed RPNH base is `ae09445fe1d9b973502bc5d2c961976c1d2c0163` from
`https://github.com/Deng-0119/RPNH`. ERP-Bench is pinned to
`ceba3880af555129b5278e056a0c20f2fb5a0ba9` from
`https://github.com/agentic-labs/erp-bench`. Task validation checks the exact
upstream commit, origin, tracked task sources and dependency digests. It passes
the original instruction bytes unchanged to the actor.

Use Python 3.12 or newer and an example-specific environment. From the RPNH
checkout root, install the local core and this example together:

```sh
python3.12 -m venv .erp-venv
.erp-venv/bin/python -m pip install . './examples/erp_bench[test]'
.erp-venv/bin/rpnh-erp --help
```

Harbor is pinned to `0.24.0` in the example's dependency metadata. This install
does not install or configure Docker or a model provider. Real trials also need
a usable Linux/WSL runtime with normal AF_UNIX/process support, Docker, and the
dependencies checked by the environment adapter. Run one ERP world at a time.
Use a short, private, Linux-native run directory; keep environments, caches and
run data out of the source overlay. Do not use a production ERP database.

The upstream container requests 3 CPUs, 4096 MB RAM, 2048 MB storage and no GPU.
That storage request does not cover host image/build caches. Official timeouts
remain 3600 seconds for the actor, 300 for verification and 600 for the build.
Actual image digests and solver/verifier dependency versions must be recorded:
the original image tags are mutable, and the image/verifier specify different
`odoo-client-lib` versions (`2.0.0` versus an attempted `2.0.2`).

Source reporting accepts the exact RPNH base or its first-wave integration
descendant: committed changes may be in `examples/erp_bench/`,
`examples/slopcodebench/`, `examples/example_validation/` and
`evidence/first_wave/`. These are the owner's assigned implementation and failure
evidence lanes. ERP ownership and patch exports remain confined to
`examples/erp_bench/`; uncommitted tracked edits in another lane, core/global
changes and unrelated examples are rejected. `base_commit` remains fixed;
`tested_commit` records the actual full HEAD tree plus ERP working-file hashes
and dirty state. Previously measured conditions retain their original commit
and results; integration does not establish a live rerun on the newer source.

## Offline commands

The following paths are portable placeholders for your own checkout and files.
The shared validator and adjacent schema are a frozen, read-only input supplied
by the integrator. They are not shipped as a competing framework in this package,
and are absent from the fixed core base. Supply their explicit path.

```sh
rpnh-erp inspect-task --upstream upstream/erp-bench \
  --task 2000_easy_01_buy_only_baseline
rpnh-erp check-plan --input observations/plan.json
rpnh-erp validate-report --manifest reports/condition/result-manifest.json \
  --shared-validator contracts/shared_contract/validate.py \
  --artifacts-root reports/condition --source-root source/RPNH
rpnh-erp export-source --source-root source/RPNH --output returns/erp-source
```

`inspect-task` prints public source/task identities without local checkout paths.
`check-plan` calls `planning.validate_plan`: it checks supplied observations and
allocations, not current Odoo state or the official score. Its input is an object
with `orders`, `routes`, `allocations` and `minimum_margin`; the exact schema is
`PLAN_INPUT_SCHEMA` in `src/rpnh_erp_bench/planning.py`. References must identify
the supplied observations. An infeasible plan returns its violations and exit 1.

`validate-report` checks the manifest and referenced public bytes using the
supplied contract. Optional `--source-root` also verifies exact current source
identity. Success does not establish execution, safe effects or business success.
`export-source` uses the complete current owned-file identity as its positive
allowlist and writes a source overlay, original-base patch, changed-file hashes
and checksums into a new directory. It does not export runs or private state.
Review the result and use `git apply --check` before integration.

## Authorized trial

First obtain explicit user authorization covering the selected task, existing
provider/exact model and official time window. Credential presence or a previous
run is insufficient. Keep the existing RPNH execution-selection file private;
do not copy keys, profiles or endpoint values into this example. This CLI neither
selects a substitute model nor exposes arbitrary model, spend or call-budget flags.

```sh
rpnh-erp run --upstream upstream/erp-bench \
  --task 2000_easy_01_buy_only_baseline \
  --execution-selection private/execution-selection.json \
  --run-root private/erp-runs/smoke-01 --condition-id smoke-01 \
  --source-root source/RPNH \
  --shared-validator contracts/shared_contract/validate.py \
  --authorize-existing-model
```

`--run-root` is the new directory for this single condition, not a shared parent;
it must not already exist. Public artifacts are under its `public/` subdirectory.
The authorization flag confirms existing user authority; it does not create that
authority. It is mandatory. For the separately authorized showcase, change the
task to `2299_hard_repair_plan_hard` and use a new condition ID and fresh world.
For a revised condition, retain the first record and pass
`--previous-record reports/previous-condition/result-manifest.json`. Do not
overwrite failures, reuse a mutated database, or feed original grader feedback
back into the same solver and call it the unchanged original run.

Runtime repairs are opt-in. `--firewall-package packages/firewall.deb` supplies a
task-local package path; repeat the flag for each package. `--build-compatibility`
explicitly requests the driver's declared build compatibility repair. Without
these flags the CLI forwards an empty package tuple and `False`; it does not
silently repair a failing build. In particular, the floating upstream uv image
can encounter PEP 668 installation rejection. Preserve that original failure,
then use a new condition ID and `--previous-record` for an explicitly repaired
runtime condition. Keep the upstream checkout unchanged and record the actual
repair/dependency identities. These flags do not install host-wide dependencies
or change the model, official time limits, task instruction or scorer.

For the Codex subscription profile, also supply `--codex-binary OFFICIAL_CODEX`
with the existing official executable, rather than the global wrapper. The
private endpoint disables native web search and automatic project documents,
preserves RPNH's existing tool-disable arguments, and keeps the model, service,
request budgets and credential reference unchanged. This runtime adaptation is
recorded explicitly. A Codex trial without this endpoint is rejected before
world/model launch. The selection and adapter are frozen privately before build.
The historical `codex_subscription_bridge_outer_sandbox.py` entry, when present,
is bound to the installed RPNH bridge module in that snapshot, preserving its
model arguments and original adapter bytes. The historical script is not run.
The private version-probe executable is bound too, so Codex need not be on PATH.
The bridge keeps the active installed Python pathname, preserving its venv and
the pinned RPNH module instead of resolving to a base interpreter.
Adapted runs retain original grader values with `grader_compatibility` claims;
they do not claim an unchanged original benchmark condition.

The driver receives these arguments through `asyncio.run(run_trial(...))`. It
owns provisioning, execution, cleanup and report construction. CLI stdout is the
driver's safe JSON result. CLI errors return 1 with a safe error type, argument
errors return 2, and interruption returns 130. A normal `run` exit 0 means a
result was returned; read its stages and original report to determine outcome.

## Action boundary and lifecycle

The actor first reads the original task through native `read_file`, using the
exact registered input locator supplied by framework initialization. This reader
is limited to the firing's registered inputs; unregistered host paths are
rejected. The ERP container has its own workspace for business scripts. The
original instruction bytes remain unchanged.

The managed `erp_python` tool executes Python through the original
`odoo-client-lib` interface inside the task world. It is an `external_write`
operation at **script granularity**: one admitted script and receipt may contain
multiple Odoo reads/writes. The example does not claim per-transaction RPNH
admission, rollback or exactly-once ERP effects. A lost response can mean an
unknown committed effect; read back known identities instead of replaying a write.
An explicit bridge `unknown` or a lost/invalid reply after dispatch raises through
the existing managed worker failure path. Registry records `outcome_unknown` and
blocks the same call and new calls in that operation, including after service
reconstruction. The bridge's trial-local physical safety gate remains in place;
it is not a second durable execution authority. Observed `completed`, `failed`
(including nonzero exit), and `domain_infeasible` results remain known returns.
The mixed `interrupted` status is unchanged; this boundary does not provide
precise pre-send failure or interruption classification.
`validate_plan` is a separate `pure` arithmetic tool.

The solver runs nonroot with network-none policy while retaining task-local Odoo
loopback access. This is the explicit `adapted_network_none_nonroot_script`
condition: the original `allow_internet=true` task manifest is unchanged, but
effective access is narrower. Report this adaptation; do not claim an unchanged
original-condition benchmark result. Actual isolation probes must pass before
launch. The developer checkout, setup data, hidden tests, reference solutions,
Docker socket and private host files are not solver inputs.

The lifecycle is: fresh seed/readiness → RPNH owner execution → close bridge
admission → stop and quiesce owner/script writers → freeze terminal evidence →
original verifier → teardown the owned world. The official actor deadline uses
supported stop; unproven quiescence blocks freezing/grading. Verifier material
is introduced only after solver writes settle; the solver must never resume
after that introduction. Freeze evidence binds the private database/filesystem
snapshot, auxiliary repair records, sentinel timestamp and timezone. Recreating
a database alone does not preserve that identity.

## Reports and limits

Each condition has a `rpnh/example-evidence/v1` `result-manifest.json` with five
distinct stages: **offline, mock, native, provider, evaluation**. Stages preserve
`passed`, `failed`, `blocked`, `not_run` or `unknown`, with command evidence and
reasons. Missing accounting is `null`, not zero; real and fake provider counts
are separate. A native claim needs actual owner/Registry projections, never
invented task or object IDs. A fixture is not a real model run.

The original verifier emits `reward.txt`, `reward.json`, `rule_results.tsv`,
`optimality.json` and `spend.json`. The parser preserves metric names,
earned/total denominators, NA states and the 0–100 reward; the emitted `passed`
threshold is 99. Evaluation completion and business acceptance are different.
Verifier exit 0 or a native terminal state alone does not mean ERP success.
An emitted zero is a measured failing score; unrun/blocked verification has no
score. Supplementary process, lineage and reuse observations stay separate from
the original reward. A changed business plan is not evidence of process-definition
revision, computational reuse, or measured RPNH superiority.

Raw provider/tool transcripts, profiles, Registry databases, ERP snapshots and
original verifier bytes remain private. Public records use reviewed allowlisted
projections and exact digests. A sanitized score projection declares changed bytes
and links to retained original digests; it is not labeled as original raw content.
Public paths are relative, confined and hash-checked. Source exports do not contain
generated runs, credentials, caches, environments or shared/core changes.

For offline checks, set `ERP_SHARED_VALIDATOR` to the frozen validator and
optionally `ERP_TEST_UPSTREAM` to the exact read-only upstream checkout, then run:

```sh
python -B -m pytest -p no:cacheprovider examples/erp_bench/tests/test_cli.py
```

These CLI tests use synthetic driver calls and offline arithmetic; they do not
provision Docker, call a model or establish native/provider/business acceptance.
See `THIRD_PARTY_NOTICES.md` and `licenses/` for MIT/CC0 provenance and the separate
licensing of runtime dependencies.
