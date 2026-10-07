# Two versions and explicit rebinding

[中文](AUTHOR_VERSIONS_ZH.md)

The exported `selfcheck_declaration.py` is the declaration entry. It loads the
explicitly selected **trusted installed factories**, validates public
PluginDefinition/PluginResource schemas and dependencies, and reports complete
descriptors, resource hashes and actual import paths. It does not bind a HOST.
Use a wheel install and `python -I` so the exported source cannot shadow it.
The separate `selfcheck_terminal.py` entry executes one authorized pure operation
using the same runtime as `rpnh plugins run`, saves its genuine JSON result, and
checks output, terminal evidence and zero model calls. IPC failures are blocked,
not passes. These checks need no repository test helpers or full test suite.

| Fact | Version A | Version B comparison |
|---|---|---|
| Identity | distribution name/version, plugin name/version, entry point | Update pyproject, inert metadata, factory and selection together |
| Full descriptor | `declaration-a.json` → `descriptors` | `--previous declaration-a.json` emits exact before/after changes |
| Schemas | config, operation input/output | Review required fields, types and bounds |
| Effects | per-operation `effect` | Review authority needed; this terminal check only accepts pure |
| Dependencies | exact `requires` plugin versions | Select and install exact dependencies explicitly |
| Resources | media type, byte length, SHA-256 | Changed bytes require a new binding even with the same display name |
| Handler identity | module/function and module SHA-256 | Renaming or editing source changes identity; transitive dependencies stay environment-owned |

After exporting, make a separate copy for B. For example change distribution
`rpnh-native-demo` to `my-native-tools`, Python module `rpnh_demo` to
`my_native_tools`, plugin/entry point `demo` to `my_tools`, and version `0.3.0`
to `0.4.0`. Update `pyproject.toml` (including py-modules and entry-point value),
`rpnh_environment_plugins.json`, `PluginDefinition` and `plugins.json`. The sample
checks distribution/plugin versions together as its release policy; the generic
RPNH API does not require distribution and plugin versions to be equal.

From the exported root, explicitly build and install using your approved local
build dependencies and wheelhouse. Neither selfcheck installs anything:

```bash
python -m build --wheel --no-isolation --outdir ./wheels ./examples/native_plugin
python -m pip install --no-index --no-deps ./wheels/my_native_tools-0.4.0-py3-none-any.whl
python -I examples/native_plugin/selfcheck_declaration.py --output declaration-b.json --previous declaration-a.json
python -I examples/native_plugin/selfcheck_terminal.py --operation my_tools/add --run-dir /absolute/absent/b-run --result terminal-b.json
```

Create A's report before editing/building B. A renamed distribution can coexist,
but entry points must remain unique. The source check identifies pyproject,
inert metadata, selection and loaded PluginDefinition mismatches separately.
An installed distribution or inert wheel declaration is not a selected plugin,
a factory verification, or a HOST binding. Missing inert data remains unknown;
`inspect_plugins` and `inspect_plugin_wheel` from `cpn.plugins.catalog` never load
factories. Installed implementation fingerprints are not the original wheel SHA.

After approving differences, re-run `rpnh plugins ... check`. Rebuild derived
package declarations/archives and resolve a **new exact lock**, reselect the
environment, prepare and inspect the new binding, then start a fresh run.
For the supplied package workflow follow [package reuse guide](https://github.com/Deng-0119/RPNH/blob/main/examples/package_reuse/README.md); its stock
lock still refers to the stock plugin and must not be reused for renamed B.
Keep A's reports, wheel, locks and run unchanged. A version change is a fact,
not an automatic backward-compatibility conclusion. Registry metadata and the
plugin catalog remain separate authorities.
