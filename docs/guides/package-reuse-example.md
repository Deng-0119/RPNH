---
name: rpnh-package-reuse-example
description: "Consume the shipped native-add v2 package through existing, new-venv and setup-document receiver routes."
metadata:
  document-kind: tutorial
  audience: operator-and-developer
  language: en
  counterpart: package-reuse-example_ZH.md
  revision: "2026-10-07.2"
  status: source-reviewed-pre-release
---

[English](package-reuse-example.md) | [中文](package-reuse-example_ZH.md)

# Reuse the shipped native-add v2 package

Start with the complete [native-add package walkthrough](../../examples/package_reuse/README.md).
It begins with `rpnh examples export --example package_reuse --output DIR` from
an installed 0.1.0rc2-or-later candidate, outside the source checkout. The export
includes the existing trusted native plugin source as its dependency.

You receive the real v2 ZIP and exact package lock, full owner request and
existing/new-venv selection templates, a path-filling helper, expected output,
and result-verification commands. No fixture construction from tests or private
Registry API is required.

The tutorial covers:

1. Preview the inert package and recompute its exact lock
2. Collect the complete local wheel closure for the selected target Python, without mixed ABI/platform wheels
3. Select an existing interpreter, an absent new venv or a local-operator setup route
4. Check, resolve, plan and inspect the setup document for the same package target
5. Approve actual installation/HOST assembly and recheck its exact binding
6. Separately approve the business run, explicitly save its registered JSON result and verify `2 + 3 = 5`
7. Open the read-only PetriNet viewer and reuse the same package with custom input `12 + 8 = 20`

Only receiver environment selections differ across the three routes. The ZIP,
manifest, package lock, entry and declared-requirements digests remain identical.
Changing inputs needs a new owner request/run; changing the graph, plugin,
configuration, schemas or resource bytes needs fresh authored material and
receiver preparation.

The default trial uses the controller Python to create its existing environment.
For a different existing Python, follow the target-interpreter acquisition block
for both exact harness/demo roots and their transitive dependencies, and rebuild
wheel arguments for each route. The resolver may retain compatible installed
packages; matching version metadata is not exact-candidate payload evidence.
Exact-candidate verification still requires that evidence and real HOST assembly.

The pure native example has no provider or numerical-package requirement. Its
trusted HOST is the existing `rpnh-native/v1` and its operation is the existing
`demo/add`; it does not invent a second workflow or runtime.

Preparation and business acceptance are separate. If a restricted host blocks
AF_UNIX owner control, retain `BUSINESS_TERMINAL_NOT_VERIFIED` and follow the
walkthrough's local Linux/WSL2 instructions. A successful venv or compiled Module
must not be reported as a real terminal result.

Related contracts: [environment preparation](../environment-preparation.md),
[v2 environment declarations](package-environments.md),
[portable packages](portable-packages.md), and [viewer guide](viewer.md).
