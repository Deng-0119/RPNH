# HarnessAudit Office: an RPNH application example

English | [中文](README_ZH.md)

**Published reference:** [Published scores and interpretation](COMPARISON.md) · [Implementation supplement](IMPLEMENTATION_COMPARISON.md). This comparison uses public results and requires no local reference implementation or execution.

This example connects **formal RPNH + a benchmark-configured agent team + the
original Office tools**. It contains the upper-layer adapter used by the completed
Office study, with the final evidence-export correction. It does not ship a second
harness, patch `cpn/`, or embed a benchmark answer in an agent instruction.

## Start without an API key

From the RPNH source root:

```bash
python examples/harnessaudit_office/example.py results
```

This reads the checked-in results using the Python standard library. It does not
install dependencies, contact a provider, execute a task, or claim to replay a
Registry. The study retained **20 execution attempts and 15 scored source slots**
across **five Office tasks and two execution conditions**. The original restricted
attempts are not erased. Read [results and limitations](RESULTS.md), then inspect
[per-slot scores](results/source_slot_scores.csv) and [all attempts](results/attempts.csv).

## What this example demonstrates

A public task view supplies the goal, role descriptions and public tools. A small
application graph generates one planning hub, the declared specialists, and a
final hub. Native RPNH owns operation admission, tool-call identity, Registry
records and settlement. The adapter runs the original stateful OfficeBank and
exports facts for the original HarnessAudit scorer.

```text
public task -> hub_plan -> specialist_1 --+
                       -> specialist_2 --+-> hub_finalize -> final output
                       -> specialist_3 --+   (third specialist only for off-t6)
```

This is a **declaration sketch**, not a recorded PetriNet screenshot. It does not
claim that a policy report gates an external write: that dependency is not in this
application graph. All 15 public Office tools remain visible to each business
role; hidden useful/forbidden tool labels are not converted into an answer-aware
allowlist. Role instructions and enforced runtime permissions are different facts.

The guide [DESIGN.md](DESIGN.md) maps this flow to actual functions and artifacts.
No optimization or additional experiments are required to use the example.

## Install the optional example

Use Linux/WSL2 and Python 3.11+. Start from a current formal RPNH checkout containing
`3492ba2fb50e40173afebae627138afea0bc2f24` (unmetered cumulative call support). Keep the
external benchmark checkout outside the RPNH repository:

```bash
python -m pip install -e .
python -m pip install -e './examples/harnessaudit_office[test]'

export AUDIT_ROOT="$HOME/rpnh-example-deps/HarnessAudit"
git clone https://github.com/UCSB-AI/HarnessAudit.git "$AUDIT_ROOT"
git -C "$AUDIT_ROOT" checkout --detach 6317162590aeeb1c8dde32b880ac199933343e4a
python -m pip install -e "$AUDIT_ROOT[oai]"

python examples/harnessaudit_office/example.py --help
```

The `[oai]` upstream extra supplies the SDK imported by its grading helpers. This
example still uses the RPNH driver, not the upstream OpenAI agent execution loop.
It is an optional source example; do not assume the previously published RPNH
wheel already contains it. No task/fixture source is vendored here. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Configure your own routes

The retained adapter currently supports **RPNH `local_process` execution profiles
and a compatible local-process judge adapter**. It does not claim to support every
RPNH provider route. Reuse your authorized profiles from the
[model configuration guide](../../docs/guides/models.md); do not copy the author's
account configuration or assume the historical model labels are available.

The execution profile uses `llm_execution_selection/v1` and references its local
adapter. The retained readiness check expects the adapter argv to declare
`--model` and `--reasoning-effort`; the judge also declares
`--model-max-output-tokens 512`. See [CONFIGURATION.md](CONFIGURATION.md).

After setting your local values (paths point outside Git):

```bash
: "${RPNH_EXECUTOR_PROFILE:?Set your execution profile path}"
: "${EXECUTOR_MODEL:?Set its exact model label}"
: "${EXECUTOR_EFFORT:?Set its configured effort}"
: "${RPNH_JUDGE_ADAPTER:?Set your judge adapter path}"
: "${JUDGE_MODEL:?Set the judge model label}"
: "${JUDGE_EFFORT:?Set its configured effort}"
export WORK="$(mktemp -d)"
python examples/harnessaudit_office/example.py configure \
  --task-id off-t1 \
  --executor-profile "$RPNH_EXECUTOR_PROFILE" \
  --executor-model "$EXECUTOR_MODEL" --executor-effort "$EXECUTOR_EFFORT" \
  --judge-adapter "$RPNH_JUDGE_ADAPTER" \
  --judge-model "$JUDGE_MODEL" --judge-effort "$JUDGE_EFFORT" \
  --authorize --output "$WORK/off-t1.json"
python examples/harnessaudit_office/example.py check-config --config "$WORK/off-t1.json"
```

`configure` only writes a configuration; `--authorize` records your explicit route
selection, not a paid call. Execution and judge readiness are reported separately.
New-run cumulative model calls, tool calls and task duration default to `null`;
there is **no required monetary cap**. Single-request timeouts, startup/failure
handling, native constraints and manual stopping remain. No unlimited hardware,
context window or fault tolerance is implied.

## Run one task, then score its saved evidence

These two commands can contact your selected models. All business writes affect
an isolated benchmark OfficeBank, not real employee records or external accounts.

```bash
python examples/harnessaudit_office/example.py run \
  --config "$WORK/off-t1.json" --audit-root "$AUDIT_ROOT" \
  --output "$WORK/off-t1-run"

python examples/harnessaudit_office/example.py score \
  --config "$WORK/off-t1.json" --audit-root "$AUDIT_ROOT" \
  --run-root "$WORK/off-t1-run/adapter-run" --output "$WORK/off-t1-score"
```

`run` does not invoke judges automatically. `score` does not rerun the executor.
Every output directory must be fresh. A low score or incomplete business outcome
is a result, not a reason to erase or retry the run. Other selected tasks are
`off-t3`, `off-t4`, `off-t5`, and `off-t6`; create a distinct task configuration and
output directory for each. Repetitions are optional; the example does not launch a
15-run campaign on import or installation.

## Inspect a run

```bash
rpnh net --run "$WORK/off-t1-run/adapter-run/rpnh-run" --view --no-open
```

This uses the existing read-only viewer; it does not render a fabricated result.
The generated run also retains `protocol.json`, `public_input.json`,
`actions.normalized.json`, `crosswalk.json`, `capture_diagnostics.json`, the native
Registry, and private Bank snapshots. `full_score_report.json` contains scoring.
An observed tool return is not a claim of later model consumption; communications
need their own delivery evidence. To re-export existing evidence without any model
call, use `example.py reproject --run-root ... --output NEW_DIRECTORY` and then
explicitly choose whether grading is required.

## Boundaries and publication

The result bundle has five tasks, mixed resource/source conditions, and per-row
projection revisions. It is not a leaderboard submission, the full benchmark,
a single-condition success probability, or a kernel-only safety certificate.
The legacy role envelope is retained, including its efficiency-oriented wording;
no new workflow/prompt optimization was run during packaging.

Raw Registry data, provider transcripts, account profiles, private paths, full Bank
snapshots and historical handoff archives are deliberately not checked in. The
public score CSV is a reported historical dataset, not enough to independently
re-grade the old runs. New runs generate complete local evidence for their owner.

Packaging tests: `python -m pytest examples/harnessaudit_office/tests -q` after
installing the example's test extra. These are offline example/unit tests, not
new live benchmark results. The publication package records exactly which tests
were actually run and which full-source checks remain for the local maintainer.
