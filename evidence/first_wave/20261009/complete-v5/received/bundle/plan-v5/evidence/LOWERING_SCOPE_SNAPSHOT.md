# Bound-child origin lowering and enumerable declaration subset

This candidate continues the frozen H7 core + acceptance-history input. It is not complete H7, a production two-payload adapter, or a native execution implementation.

## Implemented

- `with_bound_origin(ModuleDeclaration)` returns a detached original Module declaration with one reserved mechanical composition request and the existing protected capability schema.
- The original `compose_fragments` applies that request after ordinary component lowering and fusion. Exactly one internal `rpnhOrigin` resource-lease place/identity/pool is added, with capacity 1, reusable true, no literal token, one initial-resource symbol, and one non-consuming static read on every transition. Ordinary business ports, operations, guards, resets and variable arcs are retained.
- The original compiler and compiled-wire reader use the same composition function. A wire cannot delete/change origin arcs, aliases or schema while retaining a valid declaration. H7-bound compilation uses the existing offline schema-validation path; it never permits remote schema retrieval.
- Existing Registry bootstrap/origin producers, low-level admission/Start/Success guards and fixed-initial-net restriction remain authoritative. The constraint is a structural resource requirement, not permission. No new runner, scheduler, ledger, admission table or HOST-ready flag is introduced.
- `freeze_bound_module_declarations` freezes actual referenced Registration declarations/schema bodies, original Module bytes, mechanically lowered Module/net bytes and budgets. It accepts the original already-selected trusted Registration; it does not discover or import a HOST. Registered lowerers retain their existing trust contract.
- `freeze_bound_agent_declarations` supports only the declaration portion of a plugin-free, unmanaged single-stage AgentTaskSpec without extra execution profiles. It builds through the original `build_agent_task_module` and `agent_task_registration`. It checks already-derived target/socket paths, serializes once against the final sealed document_root, round-trips through the existing serializer, and preserves legal sibling relative paths. No post-freeze path rewriting occurs.

## Deliberately incomplete and rejected

`FrozenBoundChildDeclarations` is a distinct immutable type, not `PreparedChildMaterials`. The original intent producer rejects it. Its `require_execution_materials()` always raises `ParentChildUnsupported`. No automatic conversion or fabricated empty HOST profile is provided.

Complete public execution inventory cannot be inferred from today's standard interfaces:

- HostProfile includes arbitrary factories, configuration sources, execution bindings, services and before-dispatch callbacks. A profile_id or fingerprint alone does not enumerate their transitive public inputs.
- AgentTask execution selection references a separate adapter configuration; the existing registry-policy projection reads that file and can include header and credential-binding data. This candidate does not invoke it, inspect private configuration paths, or freeze secrets.
- Changing a referenced execution-profile file therefore need not change a declaration digest. A dedicated test preserves this distinction and proves that the object still cannot become execution materials. This is not claimed as O38/O39/O40 coverage.
- ControlIR proof-bearing Modules are explicitly unsupported for this mechanical extension pending a proof contract. Ordinary ControlIR compilation without the H7 constraint is unchanged.
- Workflow graph, plugins, managed bindings/tool-program policy and extra execution profiles are unsupported in the AgentTask declaration helper. General Module lowering retains existing VariableResourceArc semantics.

The new helper does not authenticate an installed implementation, read arbitrary dependency trees, select an account, open a socket, start a worker, acquire a new native reservation or finish a parent firing. It does not claim that existing Python lowerers are sandboxed or mechanically pure. The AgentTask serializer still uses its original filesystem path resolution; this is not a lexical/symlink reservation proof.

## D0 evidence boundary

The new real-Registry fixture injects only the inherited private D0 native-evidence boundary. All origin PN structure now comes from the product compiler, not a test component. It exercises Module fanout siblings, normal Start/products/Success, business guards, structured/ref-shaped payloads, existing variable read and produce/consume-return, and read-only cold claim hydration.

Fanout here means ordinary business PN branches within one child Registry. It does not add native child slots, recursive workers or multiple native children.

Actual native peer/receipt issuers, sealed transport installation, physical directory reservation, actual AgentTask/Module worker composition, child binding, parent terminal completion, D1, H7b, H8 and consumers remain NOT_RUN or unimplemented exactly as the input handoff states. No real client/model/business execution, install, Actions, remote push or user host access was used.
