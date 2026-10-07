# Reuse one real v2 package in three environments

English | [中文](README_ZH.md)

This example sends the **same `native-add-v2.zip`, package lock and `main` entry**
through existing-environment, new-venv and setup-document routes. Its genuine
Module comes from the public `build_plugin_module` API and calls the existing
`demo/add` plugin. It is not a handwritten substitute workflow.

Expected business result: `{"value":5}`, a registered terminal/result reference,
and `actual_model_call_counts: [0,0]`. No provider, API key, account, numerical
extra or external service is needed. Preparation success alone is not this result.

## 1. Start outside the source checkout

Use Bash on Linux/WSL2 and CPython 3.11+. You need an installed RPNH **0.1.0rc2
candidate or later** and its exact local wheel. Older published rc1 binaries do
not contain this workflow. Obtain the wheel from the selected release/candidate;
if building from a source checkout, first run `python -m pip wheel --no-deps
--wheel-dir dist .` there and retain that wheel. No rc2 publication is implied.

Run these commands in a fresh working directory, with the installed RPNH Python
active. Enter the actual path to the wheel you installed when prompted:

```bash
set -e
read -r -p 'Absolute path to your installed RPNH wheel: ' RPNH_WHEEL
test -f "$RPNH_WHEEL" || exit 1
WORK="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-package.XXXXXX")"
rpnh examples export --example package_reuse --output "$WORK/tutorial"
cd "$WORK/tutorial"
SAMPLE="$PWD/examples/package_reuse"
CONTROL_PYTHON="$(python -c 'import sys; print(sys.executable)')"
mkdir "$WORK/wheelhouse"
python -m pip download --only-binary=:all: --dest "$WORK/wheelhouse" "$RPNH_WHEEL"
python -m pip wheel --no-deps --wheel-dir "$WORK/wheelhouse" ./examples/native_plugin
WHEELS=()
for wheel in "$WORK"/wheelhouse/*.whl; do WHEELS+=(--wheel "$wheel"); done
MATERIAL=(--archive "$SAMPLE/native-add-v2.zip" --lock "$SAMPLE/native-add-v2.lock.json" --entry main)
rpnh package preview "$SAMPLE/native-add-v2.zip"
rpnh package resolve "$SAMPLE/native-add-v2.zip" > "$WORK/recomputed-package-lock.json"
cmp "$SAMPLE/native-add-v2.lock.json" "$WORK/recomputed-package-lock.json"
```

Only these `pip` acquisition/build steps use the package index. The package
resolver and preparation installer use the explicitly supplied local wheel
bytes; they never download or silently fill missing dependencies. If offline,
provide the same wheel closure from your approved wheel cache instead.

The exported `native_plugin` source is the existing trusted demo, included as
this example's dependency. Its standard wheel build adds the inert
`.dist-info/rpnh_environment_plugins.json` declaration needed to resolve a plugin
before installation. That declaration never executes the plugin factory.

### Files and dependency closure

- `native-add-v2.zip`: inert v2 Module, exact schemas, environment requirements and MIT license
- `native-add-v2.lock.json`: exact root package lock; no other sharepackages are required
- `plugins.json`: explicit receiver-trusted `demo` plugin selection, version `0.3.0`, empty configuration
- `owner-request.json`: complete owner request, both input roles, budgets and no-model condition
- `selection-existing.template.json` / `selection-new-venv.template.json`: full reviewable templates; `select_environment.py` fills actual paths and exact package target without manual digest copying
- `expected-output.json`: expected stock output; it is an assertion fixture, never execution evidence
- `prepared_binding.py` and `verify_result.py`: public-data readers; neither launches a workflow
- `build_package.py`: optional author tool using installed public native-plugin/package APIs; receivers do not need to run it

Declared roots are `rpnh-harness>=0.1.0rc2,<1` and
`rpnh-native-demo==0.3.0`; HOST is built-in `rpnh-native/v1`, plugin API is
`rpnh/plugin/v1`. Harness dependencies are `jsonschema>=4.20,<5`,
`packaging>=24,<27`, and `websockets>=12,<16`; the concrete resolver also includes
active transitive dependencies such as `attrs`, `jsonschema-specifications`,
`referencing`, `rpds-py` and, when its selected metadata requires it,
`typing-extensions`. Exact versions, wheel hashes and the actual active closure
are in each generated `resolution.json`, not guessed from this list. Build-only
`setuptools>=77` is used by pip's isolated build backend, not the business Module.

## 2A. Use an existing environment

For a safe first trial, create a user-owned existing environment containing only
the same harness and its declared dependencies. To use your own environment,
replace `EXISTING_PYTHON` with its absolute Python path and skip its creation and
installation commands. Review any planned changes before approving them.

```bash
python3 -m venv "$WORK/existing-python"
EXISTING_PYTHON="$WORK/existing-python/bin/python"
"$EXISTING_PYTHON" -m pip install --no-index --find-links "$WORK/wheelhouse" "$RPNH_WHEEL"
ROUTE="$WORK/existing"
mkdir "$ROUTE"
python "$SAMPLE/select_environment.py" --python "$EXISTING_PYTHON" --output "$ROUTE/selection.json"
```

Continue with section 3. The first check normally exits `3` because the demo
plugin is not installed yet. The exact plan should add `rpnh-native-demo`, then
assemble the actual HOST. It should not replace your harness. If your chosen
existing environment needs replacements, stop to inspect the conflict; a new
resolution with explicit `--allow-existing-changes` is required for those changes.

## 2B. Create a new venv from the exact wheels

Keep the controller active. Do not pre-create `new-python`; preparation owns
that explicitly approved action. Run section 3 using these variables:

```bash
ROUTE="$WORK/new-venv"
mkdir "$ROUTE"
python "$SAMPLE/select_environment.py" --python "$CONTROL_PYTHON" \
  --prefix "$WORK/new-python" --output "$ROUTE/selection.json"
```

The initial check exits `3` for the absent target. Resolve/plan must become
unblocked using the complete exact local wheel closure. The approved plan
creates that venv, installs its declared closure and runs actual installed HOST
assembly there. It never treats the base Python as the prepared target.

## 2C. Give the same plan to a person or local agent

Use another absent target so this is a separate receiver route, not reuse of a
previous receipt:

```bash
ROUTE="$WORK/setup-document"
mkdir "$ROUTE"
python "$SAMPLE/select_environment.py" --python "$CONTROL_PYTHON" \
  --prefix "$WORK/setup-python" --output "$ROUTE/selection.json"
```

Run section 3 through `setup-instructions`, then give `setup.txt`, its exact plan,
selection, resolution, material and wheel paths to the authorized local operator.
The operator must review those files and perform the same interactive
`prepare-environment` command below. The document itself grants no authority;
a checked box or a text claim cannot replace the actual checker/HOST assembly.
If the machine or paths change, regenerate the selection/check/resolution/plan
there and approve that new plan. Reuse the same ZIP and package lock.

## 3. Check, resolve, review and prepare the selected route

Run this block once for each route selected above. A blocked initial check is
expected; any status other than `0` or `3` must stop the walkthrough.

```bash
if rpnh package check-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --output "$ROUTE/check.json"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 3 || exit "$CHECK_STATUS"
rpnh package resolve-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --check "$ROUTE/check.json" \
  "${WHEELS[@]}" --output "$ROUTE/resolution.json"
rpnh package plan-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --check "$ROUTE/check.json" \
  --resolved-selections "$ROUTE/resolution.json" "${WHEELS[@]}" --output "$ROUTE/plan.json"
rpnh package setup-instructions --plan "$ROUTE/plan.json" --format text --output "$ROUTE/setup.txt"
cat "$ROUTE/setup.txt"
rpnh package prepare-environment "${MATERIAL[@]}" \
  --selection "$ROUTE/selection.json" --check "$ROUTE/check.json" \
  --resolved-selections "$ROUTE/resolution.json" --plan "$ROUTE/plan.json" \
  --state-dir "$ROUTE/state" --output "$ROUTE/receipt.json"
BINDING="$(python "$SAMPLE/prepared_binding.py" --receipt "$ROUTE/receipt.json" --state-dir "$ROUTE/state")"
rpnh package check-environment "${MATERIAL[@]}" --binding "$BINDING" \
  --resolved-selections "$ROUTE/resolution.json" --output "$ROUTE/after-check.json"
```

Review the displayed exact actions/paths and type the plan digest only if you
approve them. This must run in an interactive terminal; piping `yes`, an
`approved` JSON field or a stored digest file is not authorization.

Acceptance: resolve/plan have `unresolved: []`; receipt has `failure: null`, a
`host_declarations_digest`, a bound interpreter and completed HOST assembly;
after-check is `passed_for_checked_scope` with `execution_permitted: false`.
The state directory contains immutable digest-named evidence. `prepared_binding.py`
selects only the binding actually named by that successful receipt.

Read-only commands write only explicit outputs, and existing output files are
refused. Use a new `WORK` directory for a fresh attempt. On failure, keep the
partial environment/evidence for diagnosis; do not delete or silently reuse it.

## 4. Authorize the business run and verify its real result

Only proceed after section 3 passed. `run` has a separate interactive approval:
review the complete owner request and absent run path, then type its displayed
identity. The preparation digest does not approve the business run.

```bash
RUN_DIR="$ROUTE/run"
rpnh package run "${MATERIAL[@]}" --binding "$BINDING" \
  --receipt "$ROUTE/receipt.json" --resolved-selections "$ROUTE/resolution.json" \
  --owner-request "$SAMPLE/owner-request.json" --run-dir "$RUN_DIR" \
  --include-terminal-result --output "$ROUTE/run-result.json"
python "$SAMPLE/verify_result.py" --result "$ROUTE/run-result.json"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --view --no-open
```

The verifier requires the exact package target, run/task/net/terminal references,
`stop_reason: terminal`, available registered JSON output `{"value":5}` with
its content identity, and zero model calls. It reads the explicit private result
emitted by the real owner command; it cannot authenticate a manually invented
JSON file. Normal stdout remains a reference/count projection; result bytes go
only to the explicitly chosen private `--output` file.

The viewer prints a local loopback URL. Open it in your own browser. Look for
request → operation → result, the completed operation and its checkpoint/token
references; **Show resources** reveals the registered plugin capability. This
viewer is read-only and does not grant execution or prove a result just by
showing a graph. Stop its server with Ctrl-C.

If your host denies AF_UNIX owner sockets, the real owner cannot run. Preserve
`ENVIRONMENT_OWNER_CONTROL_UNAVAILABLE` and report **BUSINESS_TERMINAL_NOT_VERIFIED**.
A compiled Module, prepared venv or created Registry is not business completion.
There is no fake worker or alternative transport fallback. Run the same commands
on a supported local Linux/WSL2 host with ordinary owner-socket support; preserve
the same package bytes, regenerate receiver paths/plans there and approve them.

## 5. Reuse with your own inputs

This changes only owner input, so all three routes retain the same selected
package target. Create separate request/expectation files and a new run directory:

```bash
python - "$SAMPLE/owner-request.json" "$ROUTE" <<'PY'
import json, pathlib, sys
request = json.loads(pathlib.Path(sys.argv[1]).read_text())
for item in (request['task_input'], request['entry_inputs']['request']):
    item['payload'] = {'left': 12, 'right': 8}
    item['summary'] = 'Add the supplied integers 12 and 8'
request['command_id'] = 'package-reuse-native-add-custom'
root = pathlib.Path(sys.argv[2])
with (root / 'custom-owner.json').open('x') as f: json.dump(request, f)
with (root / 'custom-expected.json').open('x') as f: json.dump({'value': 20}, f)
PY
rpnh package run "${MATERIAL[@]}" --binding "$BINDING" \
  --receipt "$ROUTE/receipt.json" --resolved-selections "$ROUTE/resolution.json" \
  --owner-request "$ROUTE/custom-owner.json" --run-dir "$ROUTE/custom-run" \
  --include-terminal-result --output "$ROUTE/custom-result.json"
python "$SAMPLE/verify_result.py" --result "$ROUTE/custom-result.json" --expected "$ROUTE/custom-expected.json"
```

Both integers must be between -1,000,000,000 and 1,000,000,000. The custom expected
answer is `20`; do not verify it with the stock `5` fixture.

Changes to graph structure, schemas, plugin configuration, resource bytes or
plugin implementation are material changes. Do not edit the ZIP in place while
keeping an old manifest/lock/binding. Work in your exported author copy, version
changed plugin code/resources, rebuild and install its wheel, update the explicit
configuration and requirements, and author a new package/lock. Then restart
selection/check/resolve/plan/prepare for that new target. `build_package.py`
shows the real public SDK authoring for this fixed `demo/add` example:

```bash
python -m pip install --no-index --find-links "$WORK/wheelhouse" rpnh-native-demo==0.3.0
python "$SAMPLE/build_package.py" --output "$WORK/rebuilt-stock-package"
cmp "$SAMPLE/native-add-v2.zip" "$WORK/rebuilt-stock-package/native-add-v2.zip"
```

This optional stock rebuild must be byte-identical. It is not a generic graph
editor; author changes belong in its actual Module/dependency declarations.
Keep ZIP/package identity separate from local environment locks and private run
evidence. Do not share local selections, credentials, receipts or Registry paths
as part of a public sharepackage.
