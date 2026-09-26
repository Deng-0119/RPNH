# Native tool and registered-resource example

English | [中文](README_ZH.md)

Install this package into the same environment as RPNH, from the repository
root. Each `RUN_DIR` below must not exist before the command starts.

```bash
python -m pip install ./examples/native_plugin
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
[new-user examples guide](../../docs/guides/examples.md). The common plugin
contract and trust boundary are in the
[customization guide](../../docs/guides/customization.md).
