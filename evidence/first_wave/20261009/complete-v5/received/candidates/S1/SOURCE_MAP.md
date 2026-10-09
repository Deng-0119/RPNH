# Source map / 源码链

Base: Deng-0119/RPNH `1f191645c4d60c8b190d42e9fad99c85e8981c03`.
Candidate file hashes are authoritative in source-identity.json; line references below describe the candidate, not a published remote commit.

## Changed paths / 修改路径

- cpn/rpnh/runtime_net.py:47–52: derive lease_reference_arcs from adopted input/read arcs and declared resource_lease kind; retain registry_read_arcs and token_input_arcs interfaces.
- cpn/rpnh/_marking/selection.py:90–147: preserve ordinary reads in consume projection; static references contribute structural enabling; exact-only allowed-token filtering shares existing guard checks.
- cpn/rpnh/marking.py:776–778: facade forwards the optional exact enabledness restriction; default path remains unrestricted.
- cpn/rpnh/_marking/claims.py:57–159: selected-ref reconstruction, variable/static reference union, read-weight coverage, freshness, and original sibling exclusions.
- cpn/rpnh/_marking/claims.py:183–287: finite occurrence allocation and bidirectional consume/reference overlap checks using original claim fields.
- cpn/rpnh/_marking/claims.py:419–495: static lease read selection, exact union and reuse of lease_accesses; consuming and reference lists stay distinct.
- cpn/rpnh/registry/static_lease_claims.py:16–77: reconstruct at the original admission transaction cut from adopted PN/checkpoint, verify exact refs/index and unchanged Module consume-index convention.
- cpn/rpnh/registry/_event_store/validation/firing.py:272–273: invoke the narrow check after current adopted net/checkpoint membership checks.
- cpn/rpnh/registry/module_effects.py:238–241 and declared_effect_validation.py:289–293: exclude static resource_lease read from the existing effect consumption projection.

## Reused paths / 原路径复用

- checks.py:141–160: exact variable consume-return requirement remains for actually consumed leases; references do not enter its consumed set.
- firing_preflight.py:269 onward: uses exact marking reconstruction; retained references count as present and require no return.
- registry/module_execution.py:193–205: original consumed_input_refs index is derived from token_input_arcs; unchanged. Ordinary reads and variable pools retain their old admission-index convention.
- registry/_invocation/admission.py:268 onward and registry/_event_store/validation/firing.py:132 onward: existing claimed/consumed schema and conflict checks; no new claim table or phase.
- registry/_resource_service/references.py:1135 onward: existing exact claimed-input resource-byte provenance; unchanged. This does not authorize physical use or edit.
- registry/success_projection.py and success_publication.py: exact predecessor/token delta and ordinary Success publication; retained token state is not re-minted.
- registry/module_runtime.py, module_execution.install_active_module_claims, and _event_store/views.py: Registry-backed cold hydration, active references, and settled-state equation reconstruction; unchanged storage/index schema.
- registry/firing_recovery.py and native resume validation: original durable-completion recovery and identity replacement conventions; no second settlement path.
- registry/module_effects.py / declared_effect_validation.py: existing reset blockers for reusable/lease places.
- registry/owner_adoption.py: active replacement drain and exact token mappings; registry/module_revision.py: exclusive-active-firing guard; no new application origin invariant.

## Important boundary / 重要边界

The exact helper's allowed-token refinement accepts a valid explicit carrier when the default first carrier is dead. It does not change the default scheduler's search, selection policy or liveness. The static admission guard does not globally repair every historical low-level invocation shape. H7 target/bootstrap/origin work is absent.
