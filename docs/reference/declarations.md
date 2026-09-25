---
name: rpnh-declaration-reference
description: "Reference data-only declarations, trusted registration and compilation boundaries."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: declarations_ZH.md
  revision: "2026-09-24.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](declarations.md) | [中文](declarations_ZH.md)

# Declarations, registration and compilation

## Responsibility and intended use
Use this layer to describe and validate a reusable workflow before there is a run. `ModuleDeclaration` is data; `Registration` is a trusted host's binding of symbolic keys to implementation and schema declarations. A model-authored JSON declaration is not permission to import an arbitrary implementation. Compiling does not adopt a graph or execute a provider.

| Interface | Parameters and result | Errors and effects |
|---|---|---|
| `ModuleDeclaration.from_dict(document)` | JSON-compatible mapping → validated `ModuleDeclaration` | `DeclarationError` for malformed data/schema/ports; no Registry publication |
| `ModuleDeclaration.from_json(document)` | JSON text → same boundary | Invalid JSON also becomes `DeclarationError` |
| `module.to_dict()`, `module.to_json()` | Revalidated data representation | Does not mint runtime identities |
| `lower_module(module, registration)` | Public wrapper → `SymbolicNet` | Wrong module type raises `TypeError`; runs registered trusted lowering callbacks |
| `compile_module(module, registration)` | Actual `ModuleDeclaration` and `Registration` → `CompiledPetriNet` | Wrong types raise `TypeError`; invalid lowering raises `DeclarationError` |
| `symbolic.validate_products(operation, outcome, products, registration)` | Qualified operation/outcome and output-port lists | Validates candidate products without publishing them |

The principal `ModuleDeclaration` fields are `name`, `components`, `links`, `entry`, `exit`, `terminal`, `required_schemas`, `budgets`; optional fields include `designer_constraints`, `analyzers`, `terminal_alternatives` and `budget_buckets`. `schema_version` is `rpnh/module_declaration/v1`. Each component has name/key/config_schema/config/ports and optional operations. `Endpoint(component, port)` identifies an endpoint; terminal bindings include key/source/operation/outcome/config.

## What validation guarantees
Python construction and JSON input share validation, including finite JSON conversion and strict integer handling where declared. Required schemas and registered config contracts must match. Lowering observes the registered component's actual `PNFragment`; compiler inventory records declarations and fragments before creating the compiled document. A user-defined lowering function is trusted executable host code and must remain declaration-only; the framework cannot make arbitrary callbacks pure by naming them “lower”.

`SymbolicNet` uses qualified symbolic names. `CompiledPetriNet` is publication input, still not Registry execution authority. A place link fuses endpoints rather than cloning tokens for every consumer. Explicit product/arc declarations and budgets determine behavior; topology drawn by a UI does not replace them.

## Example: validate a supplied declaration without starting a run
The source file must contain a real complete declaration, such as the output of the native `plugins build` command in [customization](../guides/customization.md). This code parses it only:

```python
from pathlib import Path
from cpn.rpnh import DeclarationError, ModuleDeclaration

def read_declaration(path: Path) -> ModuleDeclaration:
    try:
        return ModuleDeclaration.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, DeclarationError) as exc:
        raise ValueError("Declaration could not be loaded; no run was started") from exc
```

To compile, supply the matching **trusted host registration**, not a guessed empty binding set. For example, the plugin CLI builds its own `plugin_registration(catalog)` alongside the module. A valid JSON document with missing registered keys cannot be treated as executable.

## Lifecycle and stability
Declaration objects describe candidates and can be passed between authoring/validation stages. They must not be mutated into substitutes for Registry refs. Public declaration exports in `cpn.rpnh.__all__` are distinct from advanced owner functions and underscore-prefixed internals. Versioned schemas describe a contract, not a promise that every Python helper is a stable SDK. When changing field shape, update schema, compiler, examples and docs together; retain provenance for old evidence.

Sources: `cpn/rpnh/__init__.py`, `module.py:ModuleDeclaration,SymbolicNet,validate_document`, `registration.py`, `compiler.py:compile_module`, `petri_contracts.py`, `schemas/rpnh/module_declaration.v1.schema.json` under `cpn`. See [runtime/Registry](runtime-registry.md) for the publication boundary.
