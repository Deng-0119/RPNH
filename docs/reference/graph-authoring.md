---
name: rpnh-graph-authoring
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: graph-authoring_ZH.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](graph-authoring.md) | [中文](graph-authoring_ZH.md)

# Source-authoritative ordinary graph revisions

[中文](graph-authoring_ZH.md)

For explicit two-parent ordinary graph source merge, later edits, Branch/v3 and
Assembly/v8 consumption, see [graph source merge](graph-source-merge.md). That
separate opt-in protocol does not widen the v2 history described here.

## Supported contract

The opt-in `GraphModuleAuthor` publishes a standalone, closed, ordinary v3
workflow graph through the existing Registry owner, registration gateway,
private resource publisher and transaction machinery. Its explicit
`collaboration_net_revision/v2` descriptor is distinct from legacy v1 revisions.
This is an I01 authoring slice, not completion of graph authoring or I00–I10.
Publication neither starts a run nor adopts or dispatches any operation.

The supported history is one root followed by exact, local-source, single-parent
v2 revisions. Dependency arcs and bounded feedback use the existing
`build_agent_workflow_module` implementation unchanged. The ordinary builder's
request-port selection, default-compatible explicit cap of 12, and explicit null
cap retain their meanings. No new algorithm is inferred from persisted operations.

Seven immutable materials precede the final successful revision:

1. `graph_author_source/v1`: complete explicit v3 graph wire, component key and
   config-schema identity. Missing execution, arc kind or rework fields are
   rejected before the compatibility loader can supply old-wire defaults
2. `graph_build_recipe/v1`: builder contract, executor and terminal keys, tools,
   required schema inputs, explicit per-node cap, empty managed maps, null plugin
   catalog, and every selected exact HOST declaration resource reference
3. `graph_source_map/v1`: stable source element identities and exact copy provenance
4. The complete generated Module declaration
5. Its generated Module element identity map
6. Its boundary map
7. Exact compile-consumed HOST requirements and declaration resources

Unknown or incomplete versions fail closed. The new version has no plain-Module
branch. Native and managed plugin selections remain unsupported, including an
ordinary-looking alias whose selected HOST declaration has a `native_plugin` or
`managed_plugin` contract. Selected schemas must be self-contained with only
local-fragment `$ref` values. HOST implementations are explicitly trusted by the
caller; source verification is not proof of arbitrary executable Python behavior.

## Owner API

The caller supplies an existing, authorized local owner gateway, a trusted
`Registration`, and its exact local producer principal. Compose
`graph_author_schema_data()` into the Registry catalog before creating it, or
`graph_assembly_schema_data()` when the v2 Assembly API below is needed. Shared
schema/HOST registration may occur during author setup; this does not freeze an
author command. Give every source element a distinct canonical `element:` UUID.
Use `graph_source_elements(source)` for current locator coverage.

```python
from cpn.rpnh.collaboration import (
    GraphModuleAuthor, make_graph_source, make_graph_recipe,
    graph_source_elements, validate_closed_revision,
)

source = make_graph_source(explicit_v3_graph_document)
recipe = make_graph_recipe(
    executor_key=selected_executor_key,
    terminal_key=selected_terminal_key,
    tools=selected_tools,
    required_schemas=selected_schema_keys,
    max_attempts_per_node=12,  # None is an explicit unmetered choice.
)
author = GraphModuleAuthor(gateway, trusted_registration, exact_principal_ref)
result = author.publish(
    source=source, recipe=recipe, source_ids=stable_source_ids,
    command_id="author:graph:r0",
)
validated = validate_closed_revision(
    read_only_core, result.revision.revision_ref, independently_selected_registration,
)
```

An empty recipe declaration selection asks publication to fix the exact
compile-consumed resources before the first author material. A nonempty selection
must match exactly. Both yield a complete durable recipe with all exact refs.
A retry may use the original request or that frozen recipe. It cannot switch a
selected declaration to a same-named, different version.

The source map covers the graph, nodes, node input/output ports, arcs, ingress and
egress. Locator names may change while IDs remain stable. Module IDs are derived
from the source identity and its semantic role, never from a display name, array
position or revision hash. Copying supplies new IDs plus a map from each copied
new ID to its exact same-kind parent ID through `copy_sources`. Parent revisions
and source/derived copy provenance are independently checked on reopening.
A rename of multiple input ports can change the existing builder's first-input
request selection; stable identity does not promise unchanged execution semantics.

## Opt-in graph-v2 Branch publication

`graph_branch_schema_data()` explicitly composes the graph author inventory with
`collaboration_branch/v2`. The older `branch_schema_data()` and graph-only
`graph_author_schema_data()` inventories do not add this writer protocol.
`GraphBranchVersion` accepts only same-source graph-v2 revision refs and v2
predecessor/upstream Branch refs. Legacy `BranchVersion.from_dict` and the old
producer entry points stay strict v1, including their descriptor-level support
for open and multi-parent legacy revisions. No existing Branch is migrated.

Use the same trusted owner gateway, without a Registration or runtime argument:

```python
from cpn.rpnh.collaboration import current_branch, read_branch_version

first = gateway.create_graph_author_branch(
    head_revision_ref=published_root.revision.revision_ref,
    command_id="branch:graph:create",
)
second = gateway.advance_graph_author_branch(
    expected_branch_version_ref=first.branch_ref,
    expected_head_revision_ref=first.head_revision_ref,
    expected_stream_head=first.sequence,
    next_revision_ref=published_successor.revision.revision_ref,
    command_id="branch:graph:advance",
)
observed = current_branch(read_only_core, first.branch_ref.ref.entity_id)
proof = validate_closed_revision(
    read_only_core, observed.head_revision_ref, independently_selected_registration,
)
historical = read_branch_version(read_only_core, first.branch_ref)
```

Creation may select an already published graph-v2 descendant as its exact fork
base. An optional `upstream_branch_ref` pins an exact same-source v2 Branch whose
head equals that fork base; later upstream advancement does not change the pin.
There is no implicit latest lookup, inherited upstream history, merge, or v1
conversion. Subsequent advances require the current head as the next revision's
single parent. The complete local Branch predecessor chain rejects repeated heads,
including A → B → A or longer descriptor cycles.

The existing writer transaction checks the caller's exact Branch version, head
and stream sequence together. Namespace and canonical descriptor checks apply
through gateway, staged Core transactions and direct EventStore batches. The
Branch family shares its command key and deterministic ID domain across v1/v2:
reusing a command under a different protocol conflicts. Identical commands return
the original result after later advances, writer reopening or a lost reply.

This remains **descriptor authority**. Branch publication does not compile,
invoke HOST code, prove material validity, start a run or adopt anything. A
canonical descriptor with missing or forged source/recipe/derived materials can
still be a Branch head; the existing explicit `validate_closed_revision` must
reject invalid materials. The public current/history readers dispatch only known
exact Branch versions and return the corresponding record type. Unknown versions,
unconfigured catalogs, wrong source/envelope and current-stream redirection fail
closed, with no fallback to an old head.

Selecting a Branch and then validating its immutable exact head uses separate
read transactions. It proves the selected head and its materials, not a joint
Branch/material observation cut or that the head is still latest when validation
finishes. Full validation still requires explicitly trusted offline HOST lowering;
this is separate from the descriptor-only Branch commit boundary.

## Consumption, recovery and compatibility

The existing `validate_closed_revision` entry dispatches by exact revision type.
For v2 it rereads source, recipe, source map and all material authorities within
one read transaction, reconstructs the **entire** Module through the original
builder, and compares canonical JSON bytes before compiling. It then checks all
derived element identities, boundary data, exact HOST selections, payload bytes,
schema authorities, producer relations, commit evidence and the complete command.
Booleans, numbers, missing/null values and opaque array order do not coalesce.
Successful compilation alone is not source proof.

The first source material contains the immutable complete author command,
including owner, producer, command ID, parent and all seven material values.
Reordered input arrays still change this command even if the builder produces the
same Module. Every subsequent material cut can be reopened and retried, reusing
its original exact resources. A changed command conflicts from that first durable
source onward; only the final v2 revision commit marks success. Shared setup
registrations before the source material are separate facts.

Legacy v1 plain and graph revisions retain their original material-validation
contract and bytes. They are not reported as `ValidatedGraphRevision` and do not
gain source verification. A v1 parent cannot be silently upgraded into this v2
lineage, nor can a v2 parent be silently downgraded into legacy publication.
Branch v1 and `AssemblyAuthor` retain their explicit v1 descriptor boundaries;
v2 is not downcast to make them accept it. Legacy Assembly rejection of graph
v1–v4 is retained. Graph-aware composition uses the independent v2 API below.

## Opt-in source-authoritative Assembly v2

Create the Registry with the explicit `graph_assembly_schema_data()` inventory.
`AssemblyAuthorV2` accepts `AssemblyMemberV2` exact revisions: complete ordinary
v3 graph-v2 proof or plain closed-v1 proof, under the same local source/owner.
The public `AssemblyConnection` and `AssemblyCompletion` records select stable
member boundary identities; give every member a stable `member:<32 lowercase
hex>` ID and explicitly choose `shared_exact` and `same_run_candidate`.
The API has the same named publish arguments as legacy Assembly, in a separate
command/descriptor version. See [declarations](declarations.md#source-authoritative-closed-assemblies-v2)
for the full contract.

The final composition independently rebuilds every graph from source/recipe,
projects its constraints per member, and compiles in the actual final contexts.
The map covers source fields/roles and actual fragment primitives as well as
Module declarations. Internal source ports/arcs do not receive invented Module
elements. Existing internal fusion, final-context aliases, terminal selection
and conservative carrier restrictions remain explicit. Budget identities are
shared only under exact equal contracts; rework permits remain local.

Every final-used HOST resource must already be registered. Missing HOST evidence
fails before plan publication; the publisher does not repair it. The first plan
locks the request, member/HOST selections, resolver and exact refs/size/SHA256 of
six following materials, with a complete document envelope in its metadata.
Seven resources plus generated-v1 and Assembly-v2 descriptors can be recovered
from each successful cut; final descriptor commit is the success point.

`validate_assembly_revision` dispatches on exact v2 and returns
`ValidatedAssemblyRevisionV2` after independent source/composition reconstruction,
complete material/authority checks and exact generated-v1 pairing. The generated
v1 is still independently readable as a closed Module; reading it alone does not
prove the Assembly source closure. Generated composition constraints also prevent
using it as a recursive plain member. No run, adoption, provider, executor or tool
is dispatched by authoring or validation; explicitly trusted offline lowerers run.

Later slices still need multi-version graph support, open/unbounded-recursive Assembly,
cross-version author/Branch conversion, merge/selected changes, open regions,
native/managed selection, and separately authorized runtime adoption/provider/
effect validation. None is permanently removed from the wider design.

## Opt-in two-level closed Assembly v3

Create the Registry with `nested_assembly_schema_data()` and publish through
`AssemblyAuthorV3` using `AssemblyMemberV3`. Members may be plain closed-v1,
ordinary graph-v2, Assembly-v2, or a flat Assembly-v3. The explicit first slice
supports root → child Assembly → leaf; arbitrary depth and open assemblies are
not accepted. Every exact member must share the local source and exact owner.
Old v2 catalogs, recipes, command identities and validation remain unchanged.

Containment uses member-instance paths; history remains a same-v3 logical
revision chain. The same child exact revision may appear multiple times. A
single active exact-reference path guards history and containment at one
Registry snapshot, so legal shared-child DAGs are not cycles. A selected child
must be flat, independently of any history it has.

The child Assembly descriptor, complete immutable plan, all child materials and
its exact generated-v1 pair are fully verified. Passing only a child generated
v1 does not supply that proof and is rejected as an opaque plain member.
`shared_exact`, `same_run_candidate`, and selected-primary completion with its
alternatives remain explicit.

The root is lowered in its final context. Child connection cuts are rechecked
against those actual root fragments, including exact contracted public exits.
The v3 map records full member/reference paths, leaf declaration origins, every
transitive graph source role and primitive, and the child's introduced links,
boundaries and terminal provenance. Compiled pointers are recomputed from the
root inventory rather than prefixed from a child's standalone compilation.

A v3 plan locks the resolver, exact selections and all following material
signatures. The seven-resource/two-descriptor durable publication protocol is
unchanged in shape, with independent v3 plan/map/command identities. Read-only
`validate_assembly_revision` returns `ValidatedAssemblyRevisionV3` only after
reconstructing the entire closure. Authoring does not adopt or run the net.

## Focused offline checks

- `tests/test_collaboration_graph_source.py`: existing-builder byte oracles,
  strict v3/recipe versions, stable rename/copy identities and order behavior
- `tests/test_collaboration_graph_materials.py`: actual author-only Registry
  publication, read-only reopening, complete-source and derived-data forgeries,
  exact authority faults, each durable cut, command conflicts and concurrency
- `tests/test_collaboration_graph_branches.py`: real root/r1/r2 author-to-Branch
  current/history-to-full-consumer chain, independent descriptor/material failures,
  three-axis CAS, command replay/recovery, complete noreset lineage and direct-batch authority
- Assembly-v2 publication, source/fragment, carrier, recovery, request-lock,
  concurrency and authority/pairing tests exercise the explicit composition API
- Existing closed-author material tests, legacy Assembly graph rejection cases
  and ordinary builder tests provide bounded compatibility coverage

Executors, tools and terminal callbacks in the author-only fixture raise if
invoked. Only explicitly registered, trusted offline lowerers run. Deliberate corrupted
bytes/SQL deletions are labeled storage fault injection, never fabricated success.
These tests do not establish runtime execution or provider readiness.
