---
name: rpnh-native-net-operations
description: "Extract, compose, instantiate, branch and replace Petri-net definitions without an RSI-specific runtime."
metadata:
  document-kind: guide
  audience: developer
  language: en
  counterpart: net-operations_ZH.md
  revision: "2026-09-26.2"
  status: implemented-basic-scope
---

[English](net-operations.md) | [中文](net-operations_ZH.md)

# Native Petri-net operations

RPNH provides opt-in, application-neutral operations for rearranging Petri-net
definitions. They do not implement an optimizer or an RSI policy. An
application may use them for ordinary workflow assembly, manual graph changes,
or as building blocks in its own improvement process.

## Supported basic scope

| Capability | Implemented boundary |
|---|---|
| Extract | A complete `ModuleDeclaration`, or complete declared components whose cut edges already meet public component ports. One source terminal must remain in the selection. |
| Compose | Flatten named definitions, preserve their registered contracts, and connect explicit output/entry pairs. `serial` is shorthand only when each boundary has exactly one exit and one entry. |
| Parallel | Preserve independent entry and exit lanes. RPNH does not interpret a fused place as broadcast. A shared-input distributor and an all-settled join must be real declared components. |
| Instantiate | Create one symbolically independent, prefixed instance of a definition. Budget buckets remain the same registered buckets; instantiation does not mint more budget. |
| Branch | Extract a definition and optionally instantiate that definition. It does not create another Registry or owner process. |
| Replace | Prepare and submit a complete successor through the existing owner edit path. The first supported boundary is `whole_net_quiescent` with explicit state mapping and the existing exact budget inventory. |

The definition functions are exported from `cpn.rpnh`:

```python
from cpn.rpnh import (
    ComposeConnection,
    ComposePlan,
    ExtractPlan,
    compose_modules,
    extract_module,
    instantiate_module,
    prepare_replacement,
    apply_replacement,
)
```

They are pure until `apply_replacement` is called. Always lower or compile the
result with the exact trusted `Registration` that supplies its selected keys.
The repository's `examples/net_operations/compose_serial.py` prints a complete
two-stage definition without a Registry write or model call. The companion
`live_agent_replacement.py` executes Extract, Branch, Instantiate and Compose
output as a real Agent graph, then applies Replace and executes its successor.

## Explicit registered operation

Call `register_net_components(registration)` at a trusted HOST composition root
to install:

- component `rpnh/net-definition-operation/v1`;
- executor `rpnh/net-definition-executor/v1`;
- config schema `rpnh/net_operation_config/v1`.

The application must then declare the operation, its
`rpnh/module_declaration/v1` input/output ports, the internal
`net_operation_config` capability input, its budget binding and its outcome.
Registration does not put the operation into every Agent's tool list. The
deterministic executor publishes an ordinary definition resource and settles an
ordinary firing; it does not adopt that result.

### Operation config reference

Every config uses an exact field set; fields for another operation kind are
rejected. `source_order` is the ordered list of Module input port names.

| `kind` | Required fields | Meaning |
|---|---|---|
| `extract` | `kind`, `source_order`, `selection` | Extract the sole source according to `selection`. |
| `compose` | `kind`, `source_order`, `instances`, `compose` | Map every source port to one unique instance name, then compose them. |
| `instantiate` | `kind`, `source_order`, `instance`, `output_name` | Prefix one source with `instance`; `output_name` may be null. |
| `branch` | `kind`, `source_order`, `selection`, `output_mode`, `instance` | Extract and either return the definition or one new instance. |

`selection` has exactly `kind` (`whole_module` or `components`), `components`
(unique component names), `boundary_policy` (currently only
`preserve_all_dependencies`) and nullable `output_name`. `compose` has exactly
`name`, `terminal_instance`, `mode` (`explicit`, `serial` or `parallel`) and
`connections`. Every connection names `source_instance`, `source_exit`,
`target_instance` and `target_entry`. For a branch, `definition_only` requires
`instance: null`; `new_instance` requires a nonempty instance name.

These options configure only the declared operation. They do not enable a
global mode, add budget, choose a model/provider, or expose the operation to an
Agent. Every declared public source and result port must be a data-channel
`rpnh/module_declaration/v1` port with exact cardinality `1..1`; unsupported
quantities fail during lowering, before firing admission.

## Composition rules

- A connection names a source instance's public exit and a target instance's
  public entry.
- Each target entry has at most one producer.
- Qualified component, public-entry and public-exit names must remain unique;
  ambiguous underscore-prefixed names are rejected instead of hiding a lane.
- Schema, channel, cardinality, place capacity, colour, initial-token and
  reusable properties are rechecked by the existing compiler.
- Identical budget declarations are shared. Conflicting bucket IDs, scopes or
  limits are rejected rather than renamed or enlarged.
- Parallel instances keep separate inputs. To copy immutable business data,
  declare a distributor transition that produces the exact branch occurrences.
- A parallel workflow needs a real downstream join definition if completion
  means all branches settled.

## Replacement boundary

`prepare_replacement(owner, candidate, ...)` binds a candidate to the owner's
current exact `net_ref` and validates it with the same Registration. It also
requires the candidate's budget buckets and operation bindings to match the
run's existing budget manifest. `apply_replacement` submits that immutable plan
through the existing owner edit queue:

1. register the candidate and owner command;
2. pause new firing admission;
3. return `DRAINING` while an old firing remains active;
4. apply explicit exact-token-version-to-candidate-place mappings or ordinary
   retirements after quiescence;
5. commit the successor checkpoint and `net_adopted/v1` authority.

A graceful owner stop is rejected while a replacement is pending. A fresh
Agent run creates one empty workspace lineage. During same-owner Agent graph
replacement, the candidate inherits the current checkpoint's sole settled
workspace revision and original lineage; replacement fails loudly if that
authority is absent or ambiguous. RPNH does not silently reset files.
The candidate also reuses the run's one exact execution environment and
workspace profile. Publishing a second pair would make tool execution
ambiguous and is rejected by the runtime.

## Reproducible live acceptance task

See `examples/net_operations/README.md` for the real-provider command and
acceptance boundary. The checked-in sanitized
record shows one authorized run on `volcano` / `deepseek-v4-pro`: all five
executable operation capabilities passed, five formal model responses
succeeded, no health probe or route switch occurred, the successor used both
`workspace` and `read_file`, and Registry terminal outcome was `complete`.

The structure operations themselves remain deterministic and zero-model; the
live claim means their resulting graph and replacement were actually used by
the model task. It does not claim that inert Reentry or workspace fork/import
plans ran.

## Deliberately unavailable

The following are not advertised as executable v1 operations: node-level cuts
inside a monolithic Agent workflow component, implicit broadcast, local live
replacement, cross-Registry migration, historical-token reentry, workspace
fork/import, or cloning an in-flight model/tool invocation. Typed plan objects
for reentry and workspace policy are inert preparation data only; they do not
grant execution authority. These branches require dedicated Registry protocols
before an apply function can exist.

From the repository root, run the deterministic acceptance tests with:

```bash
python -m pytest -q tests/test_native_net_operations.py
```

The tests cover definition round trips, serial and parallel topology,
anti-broadcast rejection, exact config ABI, a real registered Extract firing,
zero model calls, a real same-owner replacement adoption, and preservation of
the Agent workspace lineage during replacement.
