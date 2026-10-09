# H7a-1 core boundary independent review plan

Scope: author `rpnh-parent-child-core-implementation`, original source `rpnh-static-lease-read-fix/source`. Product edits are outside this reviewer’s scope. Frozen design: `rpnh-parent-child-seam-v3`; accepted design only, not H7 execution evidence.

## Evidence contract

- Record each reviewed source snapshot and SHA-256 manifest. No review against an unfrozen moving tree may be called final.
- Execute only deterministic pure D0 tests over temporary real Registries. Socket, PTY, native clients, subprocess/real worker/model/API/install/Actions/push are denied by an offline sentinel.
- Separate reviewer-authored testcase identities, reruns of author cases, and specifications not run. Baseline identity checks are not runtime tests.
- Original S1 candidate: 979 manifest files checked with zero mismatch; exact patch and source identity in `INPUT_AUDIT.json`.

## Low-level criteria

1. Protected object/schema/namespace publication is validated inside the original EventStore transaction, including direct `publish_batch`; caller `private_system`, JSON, protocol tag or DTO construction cannot manufacture permission.
2. Parent intent → dispatch → worker → acceptance facts are producer-owned by one exact original invocation/FiringView. Only original ordinary Start, current Registry authority and exact PN claims provide positive authority. No new durable scheduler/ledger/marking or `ready/allow` table.
3. Fresh slot uniqueness is whole-run, not writer/generation/firing/command keyed. Same-material replay must not manufacture a second process ticket. Slot history remains negative exclusion evidence only.
4. Every acceptance redelivery is live-revalidated before existing-command/exact-transaction replay. Closed, settled, stale, fenced or stopped entry fails closed. History cannot mint receipt/retry authority.
5. Unconnected native peer/stop/material/worker channels stay explicitly UNSUPPORTED; test-only offline witnesses are not accepted through public production entry points.
6. Bound marker and required protocol are mutually checked against one native genesis transaction and verified bytes. Missing/malformed/conflicting state never falls back to standalone.
7. Protected origin-capability is an original resource and one S1 static lease reference in original PN/M0. Exact protected provenance, declared read arc, reference claim and original child writer-entry are checked before admission replay, ordinary Start and direct transaction validation.
8. Bound child successor adoption is refused at owner adoption, operation revision and direct/retained commit, while ordinary standalone and generic S1 retain existing behavior.
9. Parent-bound restart/resume/reopen is refused before any writable Core/SQLite/ObjectStore/acquire_writer, and again at native low-level reentry producers/validators. Read-only diagnosis stays possible.
10. Directory reservation occurs before writable Core, atomic exclusive leaf only. Existing empty/partial/normal/bound/symlink target and injected cross-intent collision all fail zero-touch. No path-only/self-asserted typed permit admission.

## Targeted counterexamples for the implementation

- Direct protected publication with no producer, wrong producer, wrong firing/root, wrong source binding, missing or mismatched Start.
- Intent request changed after initial fact; same slot new firing/generation/key; same live execution exact replay and conflicting replay.
- Raw transaction replay after stop/settlement/fence; helper replay after closed authority; sibling settlement with still-live exact claim remains legal where supported.
- Public caller JSON peer, ready/stop flags, fake Popen tuple, synthetic receipt, test-only constructor or dictionary replacing private witness.
- Corrupt marker/protocol pair; marker from another genesis; copied capability under normal private-system publisher; duplicate/foreign origin; missing/consuming read; successor deletes read arc but retains token.
- Direct Core open and reentry producer calls on bound child; retained exact commit replay; before/after database, writer epoch, object file digest comparison.
- Legacy standalone create/admit/Start/products/Success and generic S1 paired control.

This is a plan, not a pass count. H7 reviewer runtime testcase count remains zero until actual execution is recorded.
