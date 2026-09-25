# External native plugin example

Install this package into the same environment as RPNH:

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json check
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir /tmp/rpnh-new-demo-run
```

The folder uses only the public `cpn.plugins` author API. It contributes a
numeric tool and an instruction-resource loader. It does not implement a foreign
skill parser or an MCP client. See `docs/NATIVE_PLUGINS.md` for the common native
adapter contract and the execution/trust boundaries.
