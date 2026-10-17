# Run and inspect the original task

[中文](RUNNING_ZH.md) · [Example](README.md) · [Model configuration](../../docs/guides/models.md)

## Source and environment

Use Linux or WSL2, Python 3.11+, Git and a source checkout containing this example **and its matching runtime dependencies**. The local bridge uses AF_UNIX and multiprocessing. Install the checkout using the repository's [installation guide](../../docs/guides/installation.md). Run commands below from that checkout root; keep outputs and configuration outside it.

The historical run02 runtime was `7e27f27c3474ceb5a1ce4b82e4df624c28529f30`. Public base `798b4260e33cfb7f6476eee17e9f48de9ba998f2` alone does not contain all inherited runtime dependencies. The example requires aggregate-call-cap and explicit context-selection support, native head-query support and the retained Petri input correction in addition to the public recovery/HOST JSON fixes. Do not combine this example with an older wheel and assume compatibility. This source curation has not performed a fresh paid run.

The actual example constructs `AgentTaskSpec` and uses the legacy `local_process` adapter execution route. Aggregate-cap worker transport is scoped to the cap-bearing legacy v14/v15 formats; the registered-host v13 format is not covered. Do not infer arbitrary frontend/provider compatibility. Keep the module invocation below unchanged so registered plugin function identity resolves correctly.

## Obtain the original upstream task

Upstream is [Kirito-Lab/EntCollabBench](https://huggingface.co/datasets/Kirito-Lab/EntCollabBench/tree/main), dataset revision `df95c87317863d507f229df190b2c0a29c4e4afc`, root file `mcp_multi_tasks_40.json`, task `mcp_multi_26`. The corresponding [code snapshot](https://github.com/yutao1024/EntCollabBench/tree/9d085fcb86adaf20254c09e2ca35123e535a9643) identifies the downloaded dataset path as `scripts/dataset/mcp_multi_tasks_40.json`.

Dataset license metadata is unspecified and the inspected root has no LICENSE file. This package does not grant reuse rights or redistribute the dataset. Obtain and use the original data only if you are entitled to do so. With the official Hugging Face CLI installed, download the fixed source yourself:

```bash
WORKSPACE="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-mup.XXXXXX")"
export WORKSPACE
hf download Kirito-Lab/EntCollabBench mcp_multi_tasks_40.json \
  --repo-type dataset --revision df95c87317863d507f229df190b2c0a29c4e4afc \
  --local-dir "$WORKSPACE/upstream"
```

Select exactly the original `task_id=mcp_multi_26` object from that JSON into `$WORKSPACE/original-task.json`, preserving the whole object. `design.actor_contracts` reads its three `sub_task_list` entries and required `ground_truth` arguments as task requirements; these task requirements/ground-truth fields are visible to the roles, so this is not blind benchmark evaluation. It does not execute the upstream judge. Operations receives the file fields, developer branch and SQL fields, QA checklist fields, and support incident fields. Do not substitute a different synthetic task or interpret this adapted fixture as the official benchmark environment.

Create the exact task selection and a public-only scripted configuration:

```bash
python - <<'PY'
import json, os, sys
from pathlib import Path
w = Path(os.environ['WORKSPACE']).resolve()
source = json.loads((w / 'upstream/mcp_multi_tasks_40.json').read_text())
if not isinstance(source, list):
    raise SystemExit('Expected the pinned upstream JSON array; inspect its actual format before proceeding.')
rows = [row for row in source if row.get('task_id') == 'mcp_multi_26']
if len(rows) != 1:
    raise SystemExit('Expected exactly one original mcp_multi_26 task.')
(w / 'original-task.json').write_text(json.dumps(rows[0], ensure_ascii=False, indent=2) + '\n')
fixture = (Path.cwd() / 'examples/multiuser_release_handoff/tests/reader_fixture.py').resolve()
if not fixture.is_file():
    raise SystemExit('Run this from the matching source checkout root.')
condition = 'mup-native-reader-fixture-v1'
adapter = {
    'schema_version': 'local_process_adapter_config/v1',
    'adapter_kind': 'local_process', 'model_condition': condition,
    'argv': [sys.executable, str(fixture)],
    'probe_argv': [sys.executable, '--version'],
    'env': {}, 'inherit_env': ['PATH', 'HOME', 'TMPDIR', 'PYTHONDONTWRITEBYTECODE'],
}
(w / 'fixture-adapter.json').write_text(json.dumps(adapter, indent=2) + '\n')
selection = {
    'schema_version': 'llm_execution_selection/v1',
    'adapter_kind': 'local_process', 'model_condition': condition,
    'adapter_config_path': str(w / 'fixture-adapter.json'),
    'timeout_seconds': 120, 'max_output_tokens': 1024, 'max_response_bytes': 65536,
}
(w / 'execution.json').write_text(json.dumps(selection, indent=2) + '\n')
PY
```

## No-provider preflight

Prepare an execution selection with `schema_version=llm_execution_selection/v1`, `adapter_kind=local_process`, and a matching local-process adapter config. For this no-provider path only, the adapter can point at the shipped `tests/reader_fixture.py`; model condition is `mup-native-reader-fixture-v1`. Use absolute paths resolved on your own machine, an output allowance of 1024 tokens, response allowance of 65536 bytes and timeout of 120 seconds. The reader preflight creates its own adapter/selection inside the new output directory and exercises native input allow/deny and completion; it does not call a provider.

```bash
python -m examples.multiuser_release_handoff.episode \
  --output "$WORKSPACE/preflight" --workspace-root "$WORKSPACE" \
  --execution "$WORKSPACE/execution.json" \
  --task "$WORKSPACE/original-task.json" --preflight
```

The complete preflight exercises actual native registered tool calls, overlap/write exclusion, stale-version refusal, exact message delivery and a scripted reader. Expected output is `PREFLIGHT.json` with `status=PASS` and zero model/provider calls. Fixture owners stop honestly; this is not four completed model agents or a replacement for the historical business episode. `--reader-preflight` selects only the reader check.

## A separately authorized fresh model run

Prepare your own working **local_process** adapter and matching execution selection using the [adapter configuration reference](../../docs/guides/configuration.md) and [model guide](../../docs/guides/models.md). It must implement the RPNH request/response protocol; an arbitrary model CLI is not automatically a compatible adapter. The historical exact profile is not distributed and its account/credential setup is not copied. Use the exact model, reasoning/context limits and spending authorization you intend. Do not reuse the scripted reader selection for a live business run.

After model/tool execution is authorized, run the same module with your live selection, a new absent output directory and no preflight flag:

```bash
python -m examples.multiuser_release_handoff.episode \
  --output "$WORKSPACE/fresh" --workspace-root "$WORKSPACE" \
  --execution "$WORKSPACE/live-execution.json" \
  --task "$WORKSPACE/original-task.json"
```

This calls a designer, creates four independent participants, and writes a fresh local Git/Drive/incident world. It does not contact real Drive/Gitea/ITSM services. Inspect `RESULT.json`, per-user outcomes, actual tool receipts and the world before accepting success; process exit alone is insufficient. Open a participant's real Registry with `rpnh net --run "$WORKSPACE/fresh/users/operations" --view --no-open`.

A fresh run does not restore the historical world. `--continue-business` only adopts an existing terminal design before business users/world exist; `--resume-design` is design-Task recovery after separately authorized repair. Neither is a shortcut for recovering initialized business users. The original-world continuation scripts, private checkpoints and profiles are deliberately not distributed as portable commands.

## Local code checks

From the source root, the example's deterministic tests are under `examples/multiuser_release_handoff/tests`. They also use the checkout's native test helpers, including `tests/test_static_lease_reads.py`, so they are not a standalone wheel test bundle. Run `PYTHONPATH=.:tests python -m pytest examples/multiuser_release_handoff/tests`; test execution is separate from model/business execution. Preserve failures and distinguish a syntax/import pass, native offline pass and a fresh live run.

## Validated release entry

The fixed upstream download and the extraction/configuration commands above were executed successfully. The complete native preflight passed 13 registered tool calls plus the scripted reader's allowed-input/denied-path and workspace completion checks, with zero real model/provider calls. Native scripted request records are separate from real-provider usage. Eight retained runtime dependency boundaries passed 80 selected checks with the original task supplied; the fresh-driver's three control-flow tests also pass. A wheel was built and installed into an isolated target using existing dependencies, with its schema catalog, v14/v15 cap roundtrips and CLI entry checked. See [release validation](release-validation.json) for the exact scope.

In a live fresh run, any nonterminal role result stops the waiting peers. `RESULT.json` reports native Source firing/request/lease-claim counts and HOST reads/pending edit; completion requires all of them empty and records Source owner-stop before the final Viewer capture. Unresolved state remains partial for supported recovery and effect reconciliation.
