---
name: rpnh-declaration-reference
description: "Reference data-only declarations, trusted registration and compilation boundaries."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: declarations_ZH.md
  revision: "2026-10-05.1"
  status: source-reviewed-v0.1.0rc1
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

Declared operation protocols and compiled inventory checks distinguish JSON
booleans from numbers, including inside operation and effect configs: `true`
cannot replace `1`, nor `false` replace `0`. Existing numeric equality such as
`1` and `1.0` is unchanged; schema-declared integer fields remain strict.
Outcome/product/effect bundles and tool inventories keep their existing order
normalization, while operation inputs/outputs and config arrays remain ordered.
Place fusion applies the same boolean/number distinction to initial payloads;
initial-token inventories remain multisets, and payload arrays remain ordered.

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

## Optional source-qualified references

`cpn.rpnh.collaboration` provides advanced, opt-in content contracts for the
first collaboration identity boundary. `SourceQualifiedVersionRef(source_id,
ref)` wraps an existing exact `VersionRef`; `SourceQualifiedResourceRef` wraps
`ResourceVersionRef`. Both are immutable. Source identity participates in
equality, so identical local refs from two sources remain distinct. The caller
supplies a stable, nonempty source ID, separate from a locator, display alias,
or access path. No source registry, resolver, or access grant is created.

`to_dict()` returns a detached, versioned document with `schema_version`,
`source_id`, and `ref`. Object refs preserve the existing JSON keys
`entity_type`, `logical_id`, `version_id`; resource refs preserve `resource_id`,
`resource_version_id`. `from_dict(document, catalog=catalog)` requires an
explicitly configured `SchemaCatalog`. It rejects unsupported versions,
missing fields, and extra fields rather than inferring a source or converting
to a legacy ref. Object-type-specific ID-kind/existence checks remain the
responsibility of the eventual record producer and reader.

```python
from cpn.rpnh.collaboration import collaboration_schema_data
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

schemas, types, paths = collaboration_schema_data()
catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
```

The inventory contains only `rpnh/collaboration/source_version_ref/v1` and
`rpnh/collaboration/source_resource_ref/v1`. `types` is empty: these are resource
content schemas, not new Registry object/event types. Trusted HOSTs can compose
the documents through their existing `Registration` and schema-resource
publication gateway. New optional content publication still requires its exact
registered schema resource; a content contract does not bypass that authority.

| Reader/input combination | Result |
|---|---|
| Existing local refs and mechanical v1 schemas | Unchanged |
| New qualified content with default catalog | Unsupported schema, fail closed |
| New qualified content with explicit inventory | Strict v1 typed decode |
| Unknown qualified v2 passed to a v1 decoder | Rejected, even if v2 is separately registered |
| Existing raw resource read without this inventory | Bytes remain readable; this does not establish typed support |

This reference boundary implements identity only. Author publication, Branch CAS
and Assembly use their separate opt-in contracts below; runtime binding/adoption
and viewer integration are outside this reference contract. No runtime defaults or production
HOST registration change merely by importing these contracts.

## Optional immutable author revision records

`NetRevision` adds an opt-in `collaboration_net_revision/v1` object contract.
Use `authoring_schema_data()` with the same `SchemaCatalog` constructor to
compose the two reference schemas plus this one object type. The reference-only
inventory and default Registry inventory remain unchanged. The new object's
exact local IDs reuse `resource` / `resource_version`; no global identity
service or new mechanical ID kind is introduced.

The descriptor pins its own source-qualified revision ref, local owner task,
declared producer principal, and command ID. It separately retains full parent
revision refs, selected-change resource refs, and exact resources for the
definition, element mapping, boundary mapping, and HOST requirements. The
record preserves parent order and does not reinterpret a selected change as
full merge ancestry. `closed_module` has no open-region contract;
`open_region` must supply its exact outstanding-boundary/assembly contract.

`NetRevision.from_dict(document, catalog=catalog)` checks the versioned object
contract and typed invariants. `read_net_revision(core, reference,
local_source_id=...)` is an advanced trusted-host, read-only boundary: the
caller supplies its trusted core-to-source association. The reader checks the
canonical exact object, schema support, payload/metadata equality, self-ref,
owner task, and registered owner/producer identities. These checks share one
read snapshot and the Branch descriptor validator: object/publication/unique
commit and PUBLISHED promotion evidence must be canonical, and exact stored
JSON media, locator, size, and bytes must agree. A default reader without
the optional object registration reports unsupported semantics. It does not
resolve remote sources, register schemas, repair records, or change runtime
state.

This slice implements descriptor typing and reading. It does not establish
producer command authorization, validate the referenced maps or HOST
requirements, compile the definition, publish a Branch head, or adopt a net.
The authoritative publication/CAS and material-consumer checks remain separate
implementation boundaries. A record labelled `closed_module` is not itself
proof that a target Registration can compile its referenced definition.

Without an explicit local source binding, the transaction visibility scan
conservatively collects nested IDs as local refs. A foreign qualified ID that
collides with a different local provisional firing can therefore be rejected.
The opt-in source-aware boundary below resolves that ambiguity without relaxing
the local provisional guard.

## Explicit local source binding

`source_identity_schema_data()` composes the author/reference inventory with an
optional `collaboration_source_binding/v1` object type. A trusted HOST holding
the existing `RegistryRegistrationGateway` can explicitly call
`bind_source_identity(source_id=..., command_id=...)`. The same live Registry
writer and exact registered task/bootstrap authority govern this call; no
principal string, incoming author record, or task-authored JSON grants that
capability. The source ID remains distinct from run identity, names, paths, and
access grants.

The immutable binding object and its existing `object_version_published/v1`
fact commit atomically. The fixed in-transaction validator checks current writer
fencing, exact native owner registration, one local binding, and agreement with
existing author self-identities. The binding cannot rename earlier authors or
be changed by another command. Identical retries return the original binding;
different content under the same command conflicts. No new mechanical event
type, global Registry, remote trust, or permission setting is introduced.

Only a previously committed, non-provisional binding fact informs the reference
scanner. Complete, supported v1 qualified references with the bound source ID
retain the existing local visibility checks. Foreign qualified refs are not
interpreted as local dependencies merely because their IDs collide. Bare local
refs still use the old guard; malformed/extra-field/unknown-version wrappers do
not gain this exception. An unbound Registry retains the conservative behavior.
Referenced foreign objects are not resolved, accepted, or authorized by this
distinction.

`get_local_source_identity(core)` reads this association without writing or
acquiring a new writer. `read_net_revision` checks a present binding before
accepting the caller's local-source association. Missing/corrupt binding
evidence and unsupported optional catalogs fail closed. Actual author-material
validation, authorized producer operations, and cross-Registry delivery remain
separate work. The narrow Branch publication boundary is described below.

## Same-source author Branch publication

`branch_schema_data()` adds the optional `collaboration_branch/v1` object
contract. With an explicitly bound local source, the existing trusted
registration gateway exposes `create_author_branch(head_revision_ref,
command_id, upstream_branch_ref=None)` and
`advance_author_branch(expected_branch_version_ref, expected_head_revision_ref,
expected_stream_head, next_revision_ref, command_id)` as keyword-only calls.
Targets are exact readable `NetRevision` refs in that same owner/source. A fork
can pin an exact local upstream Branch version and its head as the fork base.

Each immutable `BranchVersion` retains the owner task, publisher bootstrap,
exact head, fork base/upstream, predecessor, command ID, sequence, and all three
caller expectations. The object and its existing publication fact commit in
the Registry's existing transaction. The fixed in-transaction validator checks
the actual Branch version, revision head, and stream sequence together. It
cannot replace a stale caller expectation with a fresh read. Other object types
and extra facts cannot take over an existing Branch's object stream. The same
commit snapshot verifies exact immutable descriptor bytes, media, locator, size,
and canonical publication/commit closure for the Branch and its revision/owner/
producer. Direct Core and EventStore batch calls do not bypass those checks.
Already PUBLISHED revision/producer firing members remain eligible with canonical
promotion evidence. The local source binding and publisher bootstrap are stricter
static owner authorities: Branch commit and exact reading additionally verify
both immutable descriptor payloads in that same cut. Earlier Branch checks used
their registered metadata/closure without proving those two payloads were readable;
this shared v1/v2 check closes that inherited storage-corruption gap. It does not
relax static owner authority into PUBLISHED membership or change global source
identity policy.

Identical commands return their original result even after later advances or a
reopen. Reusing a command with different material conflicts. Concurrent
advances from one expected tuple have one winner; a failed SQL publication does
not register a partial successor. `read_branch_version` preserves exact history;
`current_branch(core, branch_id)` selects the current local publication.

This first publication slice supports direct-descendant advances only: the next
revision must explicitly name the current head among its parents, and no exact
revision head already used in this Branch lineage may be reused. This history
check prevents a cyclic input descriptor from enabling an A → B → A reset
without claiming that all author ancestry has been validated as a DAG. Reset,
same-head republishing under a new command, skipping intermediate parents,
remote Branch resolution, merge algorithms, and runtime adoption are
not enabled. Branch publication checks readable typed revision authority; it does not
validate all referenced author materials or prove target compilation. The
Registry's runtime `branch_id`, adopted net, profiles, and execution policies
are unchanged. Tests include both an ABA-shaped snapshot predicate check and
a real gateway attempt to return A → B → A, which is rejected atomically.

For ordinary source-authoritative graph revisions, the separate opt-in
`graph_branch_schema_data()` adds `GraphBranchVersion` and explicit
`create_graph_author_branch` / `advance_graph_author_branch` owner calls.
Existing public current/history readers dispatch known exact v1/v2 versions;
the v1 schema, record decoder and producer remain strict. The graph-v2 Branch
uses the same descriptor-level authority, shared command domain, three-axis CAS
and complete noreset checks. See [graph Branch publication](graph-authoring.md#opt-in-graph-v2-branch-publication)
for the full-consumer connection and its separate observation cuts.

## Closed-Module author materials

`author_material_schema_data()` adds three optional content schemas for a
Module-declaration element map, a closed boundary map, and HOST requirements.
`ClosedModuleAuthor(gateway, registration, producer_principal_ref)` is explicitly
configured by the trusted owner/HOST. It reuses the owner registration gateway
and local source binding; the principal records attribution and is not a
self-authorizing credential. No executable implementation is loaded from JSON.

`publish(module=..., element_ids=..., command_id=..., parent_ref=None,
copy_sources=None)` accepts an already closed `ModuleDeclaration`. The caller
supplies one stable `element:<32 lowercase hex>` ID for every declared Module,
component, port, operation, link, public entry/exit, and terminal locator.
Locators and display names are not identity: a rename keeps the selected IDs;
a copy supplies new IDs and explicitly names the exact parent elements through
`copy_sources`. Root or one validated local parent is supported. Missing,
duplicate, dangling, or kind-changing identities fail. Terminals must name an
explicitly declared operation in this first material contract.

The publisher really compiles with the supplied trusted `Registration`, records
the exact declarations consumed by that compile, and publishes four resources:
Module definition, element map, boundary map, and HOST requirements. Protected
content schemas use their existing frozen catalog authority; the three optional
schemas use exact registered schema resources. Physical handles, readiness,
credentials, running net identities, and adopted state are not material outputs.

Existing private-resource identity rules allow one such resource per command
transaction. Each immutable preparation resource is therefore registered first;
the final NetRevision commit is the success point. The first preparation resource
locks the complete normalized command in immutable metadata, including owner,
producer, parent, all four documents and exact HOST choices. Interrupted retries
cannot change inputs whose later resources were not yet written. Failure can leave prepared
resources, but no successful revision, Branch advance, or adoption. Identical
retry reuses those exact resources; changed material under the same command
conflicts. A final SQL publication failure rolls back the revision transaction.

`validate_closed_revision(core, reference, registration)` is an explicit material
consumer, separate from `read_net_revision`'s descriptor semantics. It checks
canonical exact objects, publication/commit/promotion evidence, immutable bytes,
bootstrap provenance and producer relations, selected schema authority, complete
maps, the first-resource complete-command manifest, and actual compile-consumed
HOST contracts. JSON comparisons retain type distinctions (for example `1` and
`true`), while ignoring formatting and key order. These reads share one Registry
snapshot. A raw descriptor with dangling or inconsistent materials can still be
read as a descriptor, but fails this material consumer. Branch publication itself
does not silently acquire the stronger material guarantee.

This slice supports only local closed modules and root/single-parent history.
Open regions and BoundaryAdaptation, selected transplants, three-way merge/resolution
and runtime candidate/adoption services remain later work. Ordinary graph source
reconstruction uses the separate v2 contract in [graph authoring](graph-authoring.md).
Closed Assembly member/lowering maps are covered below. Tests connect a real
material publisher's revisions to Branch creation/advance without sockets,
subprocesses, executors, provider calls, or runtime adoption.

## Stable-member closed Assemblies (v1)

`assembly_schema_data()` explicitly adds `collaboration_assembly_revision/v1`
and the Assembly plan/lowering-map content schemas. It does not change the
current default type catalog. `AssemblyAuthor(gateway, registration,
producer_principal_ref)` uses the existing trusted owner gateway and HOST.

Its `publish` arguments are `name`, `members`, `connections`, `completion`,
`budget_policy`, `deployment_intent`, `command_id`, and optional `parent_ref`.
Use `AssemblyMember(member_id, display_name, revision_ref)`,
`AssemblyConnection(source_member_id, source_exit_element_id,
target_member_id, target_entry_element_id)`, and
`AssemblyCompletion(member_id, terminal_element_id)`. A member identity is
`member:<32 lowercase hex>`; its generated symbol prefix is `m_<32 hex>`.
The display name can repeat or change without changing generated symbols.
Members pin exact local closed NetRevisions, independently of any Branch head.
One exact local parent Assembly is allowed; labels and parent are request data.

Connections select stable public exit/entry element identities, not port names
or positions. Members and connections are canonically ordered, and composition
always uses `ComposePlan(mode="explicit")`. Empty connections preserve
independent lanes; they create neither broadcast nor an all-settled join.
Completion explicitly selects one member's primary terminal and preserves all
its alternatives. Other member terminals remain explicit provenance and are
not additional root-completion requirements. A selected terminal or alternative
whose carrier is consumed by a connection is rejected. Aliased/multiple
producers and implicit multiple consumers are rejected.

These checks use actual final-context HOST places, including aliases created by
the member's internal links. The carrier comparison keeps internal fusion and
excludes only new Assembly connections; it does not mistake normal serial
post-fusion equality for duplicated producers/consumers. A source connection
consumes all public exit aliases of that actual carrier, removes them from the
generated exits, and maps every origin to the same explicit connection. A target
carrier shared by multiple public entries is rejected even for one connection.
Connected public input/output aliases across directions are conservatively
unsupported in this slice. The public-boundary contraction reuses the actual
final fragments through the existing compiled-inventory validation path, without
another HOST lowering call; the generated author material is compiled and
cross-checked again by the normal closed author consumer.

The caller must choose `budget_policy="shared_exact"` and
`deployment_intent="same_run_candidate"`. Same-key module budgets/buckets are
shared and must have identical canonical JSON, including numeric types; this
is not isolated per-member accounting. No deployment is performed. Nonempty
member `designer_constraints` and the known AgentWorkflowGraph v1–v4 component
keys are rejected in this slice, including a graph with cleared constraints.
Actual compilation alone does not prove graph/derived-operation consistency.

Before any Assembly-dependent material preparation, a separate immutable plan
resource fixes the full normalized request: exact owner/source/producer,
command, parent, member IDs/labels/revisions, connections, completion, policies,
and compile-consumed HOST declarations with exact resource refs. The publisher
then derives a Module, publishes it through `ClosedModuleAuthor`, and stores the
actual `rpnh/executable_net/v1` compiled inventory and lowering map. The compiled
inventory uses the existing protected frozen-catalog schema authority; it is a
resource, not a running `net_instance`. Final Assembly descriptor publication
is the success point. Interrupted attempts can leave the plan, preparation
resources or a generated author candidate, but cannot create a successful
Assembly, move a Branch or adopt a net. Same-command replay retains the exact
original resources; changed input conflicts even if generated topology is equal.
The strict consumer also checks the deterministic command/parent-derived result
identity, so cloning the same plan into a second version is not a valid retry.

The lowering map has two stages. Every original member element records the
member ID, exact revision, stable element ID, original declaration locator,
explicit disposition and generated declaration locators. This includes the
flattened module, consumed public boundaries and unselected terminals. Separate
Assembly sources account for the root, introduced links, exposed boundaries and
completion. Each generated declaration then maps to JSON pointers in the exact
compiled resource selected by the Assembly descriptor. Components cover their
actual full fragment and derived nodes; ports cover their qualified inventory,
handle and final fused place; operations cover their handle and actual
transitions; all original fragment places record their fusion representative.
Many-to-one fusion and one-to-many lowering are supported. Array indexes are
resource-version-local locators, never stable author element IDs. This map is
built from the final composition's real compile, not prefixed member precompiles.

`read_assembly_revision(core, reference)` reads only the canonical descriptor.
`validate_assembly_revision(core, reference, registration)` checks its complete
material closure in one read snapshot: exact original command-plan resource,
validated members and parent, independently reconstructed composition and HOST
compile, derived author material, complete maps, canonical persisted bytes,
schema authorities and publication/producer evidence. It returns a
`ValidatedAssemblyRevision` with `revision`, `plan`, `generated`, `compiled` and
`lowering_map`. This read-only consumer does not publish, repair or adopt.

This is the single-layer closed-member part of I01, not the entire unified plan.
Open regions and BoundaryAdaptation, recursive Assemblies, member copy/split/
fusion provenance, three-way merge/resolution and selected transplant remain
pending. The independent v2 contract below adds ordinary graph source proof.
Runtime candidate binding/adoption remains separate. Offline checks cover actual
publication, context-dependent lowering, fusion, rename/upgrade, rejected
boundaries/materials, preparation cuts, complete-command conflicts, concurrency
and read-only reopen.

## Source-authoritative closed Assemblies (v2)

`graph_assembly_schema_data()` explicitly composes the graph-author and Assembly
inventories with `collaboration_assembly_revision/v2`, `assembly_plan/v2` and
`assembly_lowering_map/v2`. It does not change the default catalog or reinterpret
v1 records. Use `AssemblyAuthorV2(gateway, registration, producer_principal_ref)`
and `AssemblyMemberV2(member_id, display_name, revision_ref)`. Publication keeps
the v1 argument names and explicit connection/completion records above, in a
separate v2 command domain. Exact local owner/source and a single v2 parent
history are required; a Branch head is not implicitly selected.

Each member must first pass its complete material consumer. Supported inputs
are ordinary-v3 source-authoritative `collaboration_net_revision/v2` and plain
closed v1 Modules. A graph member is rebuilt from its complete source and recipe;
its whole Module, stable source map, derived identities and actual HOST selection
are checked before composition. A plain v1 with nonempty designer constraints or
any known graph v1–v4 component key is rejected, including one with its graph
constraints removed. The independent v1 reader retains only its original closed
Module proof. A generated v1 carrying `native_composition` cannot be recursively
used as plain proof; this remains a single-layer Assembly contract.

Members and public boundaries use stable member UUID prefixes. The explicit
`shared_exact` policy preserves same-key bucket identity and requires identical
canonical budget values; it introduces no isolated-budget default. Graph
constraints are retained per member in `assembly_member_constraints`. Ordinary
rework permits remain local to their graph fragments, including one permit for
a rework activation with multiple feedback inputs. Existing per-node caps and
explicit null caps retain their meanings. `same_run_candidate` describes intent
and performs no run, binding, adoption or business callback.

Compilation uses the actual final member contexts. The carrier checks above
preserve internal fusion and remove only the new Assembly connections when
constructing the comparison baseline. They reuse the final fragments without
an extra lowering call for that comparison. Final-used HOST declarations must
already exist as exact resources before publication; missing declarations fail
before the first plan write. The selection is the actual final compile inventory,
not a union of member inventories. The publisher does not auto-register or repair
missing final-context HOST materials during request validation.

The first canonical plan fixes the full normalized request, resolver recipe,
exact member revisions and their material references, and final-used HOST refs.
It also fixes the exact refs, schema, byte count and SHA256 of all six subsequent
material documents. Its metadata locks the complete plan/document envelope.
The seven resources are plan, generated Module, element map, boundary map, HOST
requirements, compiled inventory and lowering map. Together with the generated
v1 and final Assembly-v2 descriptors, these form nine durable publication cuts.
Only the final Assembly descriptor commit marks success. Before it, reopening
with a fresh authorized owner can complete the original request; a committed
final result whose reply was lost is returned on replay without new facts.
A changed request conflicts even when its generated Module is equal. Prewritten
immutable blobs without canonical publication are not registration/authority
facts, although their existing bytes can still constrain retries under the
ObjectStore's immutable-version contract.

The v2 lowering map retains declaration provenance and adds source-field/role
origins plus coverage of the actual graph fragment primitives. Internal source
ports/arcs without independent Module declarations are represented through
source fields and compiled roles; no fictitious Module element is invented.
A shared primitive can have several source origins. Final-context pointers and
array positions identify this exact compiled version, never stable element IDs.
Unexplained primitives and unsupported ordinary fragment topology are rejected.

The existing `read_assembly_revision` and `validate_assembly_revision` dispatch
on the exact descriptor version. For v2, the full read-only consumer returns
`ValidatedAssemblyRevisionV2` with `revision`, `plan`, `generated`, `compiled`
and `lowering_map`. In one read snapshot it validates local authority, ancestry,
members and source proof, independently reconstructs composition and maps, checks
the complete command/signatures, exact generated-v1 pairing, persisted materials,
schema authorities and canonical producer/commit closure. The descriptor-only
reader does not supply this proof. A different independently valid generated-v1
revision with the same Module cannot replace the exact selected pair. Reading
the generated v1 alone does not revalidate the Assembly's composite source proof.

This contract covers only bounded single-layer closed ordinary graphs.
Open regions/BoundaryAdaptation, recursive composition, member copy/split/fusion
provenance, merge/resolution, selected changes, additional graph versions,
cross-version author/Branch conversion and runtime adoption remain later work
in the unified plan. See [graph authoring](graph-authoring.md) for source inputs
and the opt-in API boundary.

## Opt-in finite author ControlIR

`ControlIR.from_dict` / `from_json` accepts `rpnh/control_ir/v1`. Pass the
result to the same `compile_module(author, registration)` entry. The author
document contains an ordinary `module`, explicit `bindings`, the complete
`atoms` inventory, and explicit `calls`, `continuations`, `closures` lists.
The backend must be `business_pn/v1` or `execution/v1`; the latter is reported
as unsupported by this Module compiler. Review JSON is not executable input.

The first adapter performs **finite author specialization**: typed expressions
over supplied immutable values become the actual operation configuration that
the trusted component lowerer receives. Each executor must explicitly register
`contracts.control_ir` with `schema_version=rpnh/finite_atomic/v1`, effect class,
record `config_type`, exact input/output schema maps and a closed outcome union
with product quantities. Registration validates this contract before publishing
it to a gateway. The adapter supports pure atomic contracts, ordinary
`consume_occurrence` / `emit_occurrence` arcs and immutable guards proven true.
It verifies the actual lowered operation/transition inventory, config types,
arc multiplicities and outcome routing. It never invokes an executor or tool.
The original operation config must be an empty placeholder, so specialization
cannot silently replace an existing configuration.

Finite types are Bool, Int, Rational, Text, Enum, ExactRef, Record, Sequence,
Set and Maybe (explicit known value or unknown reason). Numeric `unit` maps
participate in arithmetic and comparison checks. Bool is distinct from Int;
Int excludes floats, including `1.0`. Rational uses `Fraction(str(value))` and
stores canonical rational text. Fields/parameters, arithmetic, floor, min/max,
count, membership/absence, boolean short-circuit, match-known and deterministic
sort are closed AST operators. Sort requires explicit keys and a final unique
tie-break. There is no string evaluation, import, clock, network, implicit
history or callback expression, and no new global execution or quantity cap.

`evaluate_expression(expression, bindings)` returns an `EvalResult` containing
a value or `EvalError(code, path, binding_ref)` and its exact `ReadSet`.
Missing fields are errors, never Unknown. Only executed branches add reads.
Origins identify each mutable source/stream/identity/version separately or an
immutable source-qualified ref. `ReadSet.stale_heads` compares all observations
against caller-supplied heads; it neither reads a Registry nor grants admission.
Mutable-head expressions, false/dynamic guards, shared observations, access
and quantity arcs require an exact runtime adapter and are refused by this
compiler instead of being frozen or erased.

`control_calls.call_contract(site)` provides the seven fixed compatible-profile
contracts: Q.segment_compact, Q.length_call, Q.pressure_call, Q.normal_call,
C.request_summary, N.adapter_call and M.observe_existing. `rpnh/typed_call/v1`
preserves all 46 input types/modes/sources/default/omission rules and 35 returns,
including payload requirements, parent projections/destinations, extra context
and duplicate/late rules. Changing a field or omitting a return is a precise
author error. A valid call still produces `ControlIRCapabilityError` with
`NEW_VERSION_REQUIRED`: attach/return, wakes, continuation, closure, Q/C/N
migration and runtime admission are not implemented by this author adapter.
The compatible normal/leaf/compaction attempt policy remains 1/1/2.

Every accepted compilation carries its full author input, evaluated values,
read-set and per-construct capability report in the reserved
`designer_constraints.rpnh_control_ir_v1` proof. The ordinary compiled-net
loader reconstructs and checks this proof without HOST callbacks; typed
documents select offline schema validation, preserve local recursive schema
scopes and do not retrieve remote schema resources. Typed dictionary wire
inputs must use exact JSON builtin containers; tuple and dict-subclass coercion
is rejected before their callbacks. The legacy general Mapping input interface
remains a trusted Python interface. An unknown version in the reserved
`rpnh_control_ir_` family or a present-but-null proof is rejected.
`load_compiled_control_net(document)` is the fixed, data-only public reader for
consumers requiring a typed proof, and the ControlIR compiler uses it for its
own output. It rejects an absent, stripped, malformed or unsupported proof.
The default `load_compiled_net` intentionally accepts valid legacy v1 after
all typed markers have been removed, without claiming it is typed. Without an
external requirement, it cannot distinguish that material from ordinary v1.
Existing unmarked Module/v1 numeric int/float compatibility and default
validation are unchanged; an executor capability declaration is not a typed
author opt-in.

Compiling proves the supported author-to-lowering boundary, not publication,
adoption, execution, provider behavior, dispatch rights or return-at-most-once.

## Lifecycle and stability
Declaration objects describe candidates and can be passed between authoring/validation stages. They must not be mutated into substitutes for Registry refs. Public declaration exports in `cpn.rpnh.__all__` are distinct from advanced owner functions and underscore-prefixed internals. Versioned schemas describe a contract, not a promise that every Python helper is a stable SDK. When changing field shape, update schema, compiler, examples and docs together; retain provenance for old evidence.

Sources: `cpn/rpnh/__init__.py`, `module.py:ModuleDeclaration,SymbolicNet,validate_document`, `registration.py`, `compiler.py:compile_module`, `petri_contracts.py`, `schemas/rpnh/module_declaration.v1.schema.json` under `cpn`. See [runtime/Registry](runtime-registry.md) for the publication boundary.

### Finite typed members in Assembly v9

`typed_assembly_schema_data()` explicitly installs `AssemblyAuthorV9` and its
v9 plan, complete command and lowering-map contracts. A member explicitly claims
`finite_control_ir_v1` or `plain_closed_v1`; the selected revision and its actual
single-parent history must retain that role. Old Assembly domains are unchanged.
Connections, the selected primary completion, `shared_exact` budgets and
`same_run_candidate` intent remain caller choices. Graph, open, derived and
nested Assembly members require their own contracts and are not accepted here.

A finite member keeps its entire original ControlIR author, evaluations,
immutable read origins and capability proof. The full consumer first validates
the original member, then rechecks that proof against the actual fragments
lowered in the final component namespace, required schemas and shared budgets.
Only fragment dictionary keys project back to the original component names;
local fragment symbols are never guessed or replaced with a member precompile.
The lowering map records exact contexts, fragment bytes/hashes, source carriers,
final pre-connection carriers and final fusion representatives. Int/Rational and
unit checks, pure atomic contracts, occurrence arcs and exact generated bytes
are checked again. No global ControlIR author is invented.

The generated G revision is an ordinary closed Module. Its ordinary reader alone
does not prove typed Assembly membership. Use `validate_assembly_revision` on
exact A, or `validate_generated_assembly_v9(core, A_ref, G_ref, registration)` to
require the complete exact A/G proof. Missing proof or a different pair is an
error, never an implicit downgrade. V9 uses fixed offline schema validation
through compilation, publication, carrier reconstruction and full readback;
legacy public call signatures and default behavior remain unchanged.

The first durable complete command pins all materials, identities and schema
authorities. Recovery and replay must match it exactly. This is finite author
composition only; it adds no execution, call/return, admission, worker, provider
or runtime continuation support.
