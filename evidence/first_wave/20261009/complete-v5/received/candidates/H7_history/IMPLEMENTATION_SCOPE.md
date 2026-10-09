# Historical acceptance: narrow offline slice

Input: the frozen H7 core, 992 files, manifest SHA-256 ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e, H7 patch 7746881935e0d35e85cb71b2e08bab82ab3f844d52771212b39b3a7728448ed3. This is an isolated overlay, not a remote-HEAD statement. Frozen core, S1 and v3 design are unchanged.

## Boundary

`EventStore.classify_child_acceptance_history(assertion)` is read-only. A typed assertion contains exact source/binding/run/task/acceptance identities, one recorded acceptance transaction/event/commit ordinal, and digests of the copied acceptance/intent plus declaration. Caller claims are compared to original immutable Registry bytes and original committed rows. A copied JSON body or a true Boolean is never evidence.

The result is VALID, INVALID or UNAVAILABLE, with finite proof identities and digests only on VALID. It contains no request/business body, allowed action, dispatch ticket, receipt, reservation or native-boundary evidence. No live gate, bootstrap, resume, reissue, observation scheduler or Success path consumes the result.

The classifier proves original *mechanical* Registry acceptance. It cannot prove OS peer identity, delivery of a complete receipt, absence of SIGINT, in-memory stop state, actual Popen, exclusive target reservation or a child's creation. Those native issuers remain missing and fail closed.

## Evidence and cut

- Current identity is the existing canonical `collaboration_source_binding/v1`, validated through `read_source_binding`, including its native owner registration. A path or asserted source name does not establish identity.
- Acceptance cut comes from its unique real `transaction_committed/v1` ordinal, matched to the asserted exact transaction/event. The transaction, outbox, original publication rows, stream/task positions and command material are checked.
- `_CommittedCut` is an internal SELECT-only CTE projection on the *same original SQLite read transaction*. It does not create a database, table, view or durable cache. Complete committed transactions through the cut select objects, events, relations and temporary members. Actual root promotion transaction identity determines PROVISIONAL/PUBLISHED at a cut; no ABANDONED state exists.
- The historical epoch is the original committed transaction's writer epoch, joined to its persisted event and outbox evidence, and checked against the initial native writer and execution lease. It is not the current `registry_meta.writer_epoch` and is never a current fence.
- Existing H7 `_validate_parent_record_transaction` is shared by live commit and historical validation. The live wrapper still separately demands its opaque original native boundary; no bool bypass was added. This shared matcher uses the original `_execution_at` and `revalidate_started_operation_at`, including ordinary Start, PN claim/marking, source, admitted/current-at-cut run authority and original net/admission closure.
- The same original FiringView rules select canonical dependencies plus the exact producer's temporary area. A separate internal integrity walk verifies immutable descriptor bytes/schema, declared resource schema material, exact transactions, complete member ownership and strong causal edges; it exports no generic retained-row reader.
- Current whole-run phase uniqueness is checked beyond the old cut. A later conflicting record cannot create another slot or repair an old one.

The cut projection is a review focus: it supplies original relation rows to shared native rules rather than a second implementation of authority rules. Its regenerated historical stream/task heads are not substitutes for verification of persisted current source structure.

## Actual lifecycle coverage

The original APIs can record parent `stopped_by_owner` and increment the writer fence after acceptance while the firing remains PROVISIONAL and unresolved. Historical proof must survive these changes even though the live gate rejects the old execution.

A real later execution_generation is currently unreachable for this unresolved H7 firing: reentry requires drained active firings, while H7 settlement/Success and generic interrupted recovery are deliberately unsupported. No positive test may patch SQL to pretend that lifecycle exists. Actual H7 publication/abandon, future native child binding, H7b recovery and later-generation integration remain unsupported; a PUBLISHED H7 root is UNAVAILABLE in this slice rather than accepted without the future promotion contract.

## Side effects and trust

The reader uses SQLite mode=ro and query_only. It does not mutate canonical DB tables, events, writer epochs or immutable objects. SQLite may create/update SHM/WAL coordination files; no claim of zero directory writes is made.

The trust boundary remains the original Registry store and trusted installed code, not a cryptographically signed external ledger. Descriptor contents are checked against registered metadata; the request is checked against its recorded digest, and all H7 material identities/inventory/envelope are rechecked. The frozen core's preparer records supplied public-material inventory, not a full installed HOST collector, so this slice does not claim to authenticate absent external payload files or invent their bytes.

## Integrity hardening after independent review

Every inspected transaction now checks the complete original temporary-member inventory bidirectionally, including orphan rows. The native Start retains its original persisted command, causality, producer, stream, scope and epoch envelope; rebuilding a valid payload alone is insufficient. Broken SQLite files return a finite fail-closed classification.

Dependency traversal follows the original registered descriptor schema's declared reference properties, arrays and maps, plus the existing resource origin/provenance authority field contracts. Unconstrained request definition/configuration, normalized business material, descriptor extensions and other opaque values are not dependencies merely because their keys resemble a reference. Their original bytes and declared payload schema are still validated; no business payload is rewritten or exported. Actual reference fields still require their original registered bytes. This walk checks integrity only; authority rules remain the shared original Registry/PN validators.
