# Protected prospective closure audit

This is a bounded audit of the original Registry transaction validation path, following the independently reproduced protected-capability lifetime failure. It does not establish native transport authentication or full H7 D0.

## Confirmed defect and repair

On source aggregate `61421bd9fd2f389a164c54fbe6fc92086e3a9ff571367c888174782a51a3ca9d`, substituting a locally valid task ref for the capability lifetime could commit the origin transaction. The later integrity reader rejected it, which was too late. The prospective protected-origin check now requires both `origin_kind == private_system` and `lifetime_ref == marker.bootstrap_ref`, alongside its existing exact producer/task checks. The independent failing test is preserved and rerun without weakening its no-commit assertion.

## Complete set checked before the transaction commits

- All protected objects have no producer invocation. All events are original framework publication/relation/commit events, with the exact event count. Relations are strong, system-owned, have no producer invocation or arbitrary metadata.
- Bootstrap has exactly six native identity/marker objects in one fresh transaction. The marker's self ref is both its actual envelope ref and the deterministic ref derived from the exact native run.
- Each of task, branch, bootstrap command, run and genesis is the exact proposed object, with its own logical/version metadata matching its envelope. Task ID, branch→task, branch name, run→task/branch, protocol, original epoch, genesis→run/transaction, catalog ref and accepted declaration digest all close in that transaction.
- Marker relations are the complete three-edge set: derived_from genesis, derived_from run, produced_by bootstrap. Their source is the exact marker.
- Origin is one-time, has its exact actual/deterministic self ref, and matches marker/run/task/epoch/declaration. Its source-binding ref comes from the original committed source-binding reader.
- Capability has the exact protected schema and original native catalog source. The catalog's protected schema bytes match the installed mechanical schema. Payload bytes are verified and equal the exact expected origin/marker/run/task/acceptance/epoch/declaration object.
- Capability producer and lifetime are the exact bootstrap, its origin kind is private_system, and its task is the exact child task. Generic original resource validation also enforces resource self IDs, envelope bytes/size/media, primary/secondary/producer closure, immutable publication/reference provenance, canonical producer and exact strong birth-transaction relations. Those generic checks remain authoritative, rather than being replaced by an H7 resource implementation.
- The complete origin/capability relation set has five edges: origin produced_by bootstrap and derived_from marker/source binding/capability, plus capability produced_by bootstrap. No extra edge is accepted.

Marker actual/self/exact identity and complete-edge checks were defensively tightened in the same validator. They are not labelled separately confirmed old defects: the independent extra-capability-edge probe already failed closed on the old source through existing lower validation.

## Existing checks preserved

The historical reader still verifies marker/genesis same-transaction identity, exact producer/lifetime/schema/source/payload, protected origin edges, one original writer, original run authority and exact PN origin token. The same original admission, Start, I/O and settlement paths enforce live authority and fixed bound-child net constraints. Ordinary non-bound Module and S1 behavior retain their original authority and semantics.

No issuer, peer/receipt mechanism, target reservation, actual wrapper, historical acceptance classifier, parent native observation or parent completion was added by this repair. See the independent final review and final validation inventory for executed tests and exclusions.
