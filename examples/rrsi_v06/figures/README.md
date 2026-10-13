# RRSI declarations in the official Viewer

English | [中文](README_ZH.md) · [Example and original images](../README.md)

## Open a child declaration

From the repository root, use an existing Python environment with RPNH installed. Viewing these bundled JSON files does not require installing the separate RRSI example package.

```bash
python examples/view_declaration.py rrsi-policy --no-open
python examples/view_declaration.py rrsi-analyst --no-open
python examples/view_declaration.py rrsi-proposer --no-open
python examples/view_declaration.py rrsi-critic --no-open
python examples/view_declaration.py rrsi-digester --no-open
```

Choose one command. Open its printed `http://127.0.0.1:PORT/` URL, keep the terminal running, and press Ctrl+C when finished. `--no-open` only disables automatic browser launch: the Viewer still needs permission to bind a loopback HTTP socket. It is not a socket-free or `file://` HTML viewer.

Select **PetriNet**, **Detailed flow**, or **Agent overview**. Use zoom, pan and **Fit to view**, then select nodes and arcs to inspect them.

Bundled frames: [Policy](policy/declaration-frame.json), [Analyst](analyst/declaration-frame.json), [Proposer](proposer/declaration-frame.json), [Critic](critic/declaration-frame.json), [Digester](digester/declaration-frame.json). [Policy panorama guide](policy/README.md).

## What the views mean

Each frame describes one real, independent child Module in `initial_configured` mode. The four roles have seven Petri nodes and seven arcs. Detailed flow folds only `role.response`; `role.state` stays visible because initialization and the action loop both feed it. Agent overview shows the declared `role.model` agent and its structural self-loop. The loop has a path witness through response, act and state; it is not an observed execution order or proof of successful iteration.

`role.init` and `role.act` retain their deterministic executor transport. The model identity is `declared_llm_executor`; `semantic_group`, `agent_ref` and `executable_binding_ref` remain null. Frames contain no runtime, token marking, firings or terminal evidence, and history is unsupported. Unknown observation counts remain unknown. These independent declarations are not combined into a campaign PN or a lower-level execution PN.

For an actual run, use its exact Registry directory with `rpnh net --run "$RUN_DIR" --view --no-open` and inspect that run's evidence.

## Rebuild and verify without sockets

From the source root, with the existing RPNH Python dependencies available:

```bash
python -m examples.rrsi_v06.declarations --check
python -m unittest discover -s tests -p 'test_rrsi_declaration_export.py' -v
node tests/rrsi_declaration_read_model.mjs
```

The Python test command uses the standard-library runner, not pytest. The JavaScript check requires Node.js. These commands use no provider, model, Registry or Viewer server. To regenerate the four frames and their provenance, run `python -m examples.rrsi_v06.declarations` without `--check`.

The [generator](../declarations.py) uses real role declarations, registrations, compiler and shared identity export. [Current frame provenance](role-declaration-provenance.json) records the source baseline, relevant source hashes, generator hash, compiled/projection/frame hashes and serialization. The four compiled hashes match the [historical role image provenance](../results/figure-provenance.json). Current projection/frame hashes are recorded separately; original images and historical capture provenance are unchanged, and no new screenshot is claimed.
