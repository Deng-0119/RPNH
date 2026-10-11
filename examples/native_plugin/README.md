# Native tool and registered-resource example

English | [中文](README_ZH.md)

Start from a source checkout root or obtain an editable installed copy:

```bash
rpnh examples export --example native_plugin --output /absolute/absent/my-native
cd /absolute/absent/my-native
```

Install this plugin into the same environment as RPNH. The editable install
below uses your exported `rpnh_demo.py`, so later handler edits take effect.
Each `RUN_DIR` must not exist before the command starts. Listing/exporting does
not install or execute the plugin. Runtime requires supported local process and
Unix-domain socket permissions, even though this example makes no model call.

The image below is the actual PetriNet view of the completed `demo/add` run.
The hidden resource counter shows that one declared plugin capability can be
revealed with **Show resources**; the visible graph is the ordinary
request–operation–result path.

![Completed native-plugin PetriNet](assets/native-plugin-petrinet.png)

```bash
python -m pip install -e ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json list
rpnh plugins --config examples/native_plugin/plugins.json check
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-native.XXXXXX")"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$DEMO_ROOT/add-run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/instruction \
  --input examples/native_plugin/instruction-input.json \
  --run-dir "$DEMO_ROOT/instruction-run"
rpnh net --run "$DEMO_ROOT/add-run" --show-resources
```

The addition returns `output.value` equal to `5`, terminal evidence and zero
model calls. The instruction operation returns the exact identity and version
of its registered instruction resource. `demo/summarize` is reused by the
hybrid example; its direct input is in `summary-input.json`.

For the complete walkthrough and cleanup notes, see the
[new-user examples guide](https://github.com/Deng-0119/RPNH/blob/main/docs/guides/examples.md). The common plugin
contract and trust boundary are in the
[customization guide](https://github.com/Deng-0119/RPNH/blob/main/docs/guides/customization.md).

## Two portable author checks

For wheel/source consistency use an explicit wheel installation (the editable
quickstart above remains supported for normal development):

```bash
python -m build --wheel --no-isolation --outdir ./wheels ./examples/native_plugin
python -m pip install --no-index --no-deps --force-reinstall ./wheels/rpnh_native_demo-0.3.0-py3-none-any.whl
python -I examples/native_plugin/selfcheck_declaration.py --output declaration.json
python -I examples/native_plugin/selfcheck_terminal.py --run-dir /absolute/absent/author-run --result terminal.json
```

Build dependencies must already be available from your approved local source.
The declaration entry executes trusted selected factories and checks complete
schemas/resources/install sources; it does not execute operations or bind a HOST.
The terminal entry runs a declared pure operation through the public runtime and
saves the genuine result with terminal reference and model counts. Exit 0 means
PASS, 1 means failed validation; terminal IPC unavailability reports BLOCKED/2.
Use new output paths for every check. [Two-version differences and rebinding](AUTHOR_VERSIONS.md)
explains explicit renaming, versioning, full descriptors and new locks.

## PetriNet declaration gallery

The image below shows this example’s initial PetriNet structure. The [full gallery](figures/README.md) shows the listed declaration variants and readable node details for large nets.

![Initial PetriNet structure](assets/petrinet-afe2f2df36c37247.png)
