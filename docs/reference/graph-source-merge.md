---
name: rpnh-graph-source-merge
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: graph-source-merge_ZH.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](graph-source-merge.md) | [中文](graph-source-merge_ZH.md)

# Ordinary graph source and recipe merge

[中文](graph-source-merge_ZH.md)

`GraphMergeAnalyzer` and `GraphMergeAuthor` are an opt-in I01 authoring slice.
They merge complete ordinary graph-v3 sources and frozen builder recipes, rebuild
every derived operation through the existing builder, and publish an explicit
`collaboration_net_revision/v3`. A merge has two ordered exact parents; its later
edits have one exact v3 parent. This does not change graph-v3 language or the
existing graph-v2 root/single-parent protocol.

Only an existing trusted local Registry owner, its exact producer principal and
an explicitly trusted offline `Registration` can configure these authors. Nothing
is adopted or executed. Native/managed plugins, external schema resolution and
cross-source author histories are not supported by this slice.

## Analyze real history and choose explicitly

Compose `graph_merge_schema_data()` into the catalog before Registry creation.
Use `graph_merge_assembly_schema_data()` when Assembly/v8 is required. These
inventories add new contracts; old inventories are unchanged.

```python
analyzer = GraphMergeAnalyzer(gateway, registration, exact_producer)
analysis = analyzer.analyze(
    local_ref=left_exact_revision,
    incoming_ref=right_exact_revision,
    base_ref=optional_asserted_base,
    command_id="graph:analysis:1",
)
```

The analyzer reads the complete local ancestry, independently verifies every
participating graph's materials, and computes the nearest common ancestors. An
asserted base must equal the unique nearest base. Unrelated histories and multiple
nearest bases are saved as unresolved analyses, with no chosen winner. Invalid,
missing, cyclic or unsupported history cannot produce a successful analysis.

The saved analysis contains exact source/recipe/material pins, complete ancestry,
stable-identity normalized models, B/L/R differences and exact conflict IDs.
Graph, node, port, arc and boundary identities remain distinct from names. Their
introduction revision and original copy evidence distinguish continuous retention
from deletion followed by reuse of the same ID. Every source field and wire-array
order is retained. The complete recipe, including exact HOST selections, is one
indivisible value. Booleans, integers, null, missing fields and array order do not
coalesce.

Equal and strictly one-sided states combine using exact three-way rules. Divergent
values, origin collisions, delete/modify and delete/dependency interactions, name
collisions and coupled graph/recipe contracts require caller decisions. A conflict
may span several subjects. The caller supplies every subject explicitly:

```python
choices = [{
    "conflict_id": exact_saved_conflict_id,
    "reason": "Keep the local rename and the incoming recipe chosen for this graph.",
    "selections": [
        {"subject": exact_name_subject, "side": "local"},
        {"subject": "recipe/complete", "side": "incoming"},
        # Include every other exact subject listed by this conflict.
    ],
}]
author = GraphMergeAuthor(gateway, registration, exact_producer)
merged = author.publish(
    analysis_ref=analysis.analysis_ref, choices=choices, command_id="graph:merge:1",
)
```

This example describes the choice shape; actual subjects come from the saved
analysis. Every conflict and subject must be covered exactly once. Decisions on
overlapping subjects must agree. Choosing an absent B/L/R state is an explicit
deletion; the result must still contain complete ownership, order and endpoint
references. No missing order, business reference, default or HOST selection is
repaired. A combination that cannot form a valid graph stays unresolved.

The resulting source and recipe must losslessly round-trip the selected atoms.
The original `rebuild_graph_module` rebuilds the complete Module, including budget,
request-port, feedback and terminal behavior. The default-compatible explicit cap
12 and explicit null cap keep their old meanings. Source and Module identities,
boundaries and exact compile-consumed HOST declarations are recomputed. Persisted
derived operations are never an independent editable authority.

## Durable result and later edits

A complete immutable command precedes eight materials: resolution, source, recipe,
source map, Module definition, Module element map, boundary map and HOST evidence.
It freezes the requested decisions and reasons, owner/producer, both parent refs,
all following documents, exact resource refs, schema authorities, byte counts,
SHA256 values and metadata. The final v3 descriptor commit marks success.

Interrupted publication can reopen and replay the same command at every durable
cut. Changing the source, recipe, parents, decision reason, exact HOST selection or
any later material conflicts from the first command onward. Shared schema/HOST
registration is setup, distinct from this command lock.

```python
edited = author.publish_edit(
    parent_ref=merged.revision.revision_ref,
    source=complete_edited_source, recipe=complete_requested_recipe,
    source_ids=stable_source_ids, copy_sources=explicit_new_to_parent_ids,
    command_id="graph:edit:1",
)
proof = validate_closed_revision(read_only_core, edited.revision.revision_ref, registration)
```

Edits retain complete merge ancestry and origin decisions. New copies need fresh
IDs and exact same-kind parent IDs. There is no new v3 root or implicit v2-to-v3
upgrade. The old `GraphModuleAuthor` cannot accept a v3 parent.

The public full consumer dispatches by exact version, rereads the entire proof in
one local read cut, and independently reconstructs source, recipe, choices,
identities, all material bytes and their authorities. Publication repeats this
complete closure just before final success; the existing Registry commit boundary
fences stale owners. This is not an atomic snapshot across SQLite and filesystem
payloads. Changed dependencies fail without modifying the frozen command.

## Branch and Assembly consumption

`gateway.create_graph_merge_branch` and `advance_graph_merge_branch` publish
Branch/v3 using the existing shared Branch command domain and exact version/head/
stream-sequence CAS. The expected head must be an actual parent of the next
revision: either exact parent of M, or E's sole parent. Ordered L/R provenance adds
no first-parent restriction. Complete predecessor history rejects ABA/reused heads.
Old Branch/v2 records are not migrated. Branch authority remains descriptor-only;
call `validate_closed_revision` explicitly for the selected head's full materials.

`AssemblyAuthorV8` and `AssemblyMemberV8` consume exact plain-v1, ordinary graph-v2
and fully proved graph-v3 members in a flat closed Assembly. Supply the existing
typed connections/completion and explicit `shared_exact`/`same_run_candidate`.
Repeated member instances have separate stable member IDs. The source/recipe is
rebuilt and lowered in every actual final context; the map pins complete graph
origin evidence, v3 command/resolution refs and per-element introduction evidence.
The actual Assembly A and generated G pair is fully verified together.

The first v8 plan freezes complete member/HOST/schema choices and all following
material signatures. Final publication repeats the complete dependency closure.
`validate_assembly_revision` independently checks source, final-context primitives,
exact A/G pairing and every durable resource. Reading generated G alone retains
its ordinary closed-Module contract and does not prove the Assembly closure.

This is graph author merge, not Assembly merge. Recursive Assembly merge, other
graph-language versions, open graph merge, automatic order invention and runtime
adoption are outside this finite slice. They remain separate design work.
