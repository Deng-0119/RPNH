---
name: rpnh-plain-merge-nested-assembly
description: "Explicit plain merge proofs in closed two-level Assembly/v3."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: plain-merge-nested-assembly_ZH.md
  revision: "2026-10-05.1"
  status: integrated-author-proof-candidate
---

[English](plain-merge-nested-assembly.md) | [中文](plain-merge-nested-assembly_ZH.md)

# Plain merge results in closed nested Assembly

Revision: 2026-10-05.1. Scope: explicit catalog and same-snapshot proof integration.

Use `plain_merge_nested_assembly_schema_data()` to opt in to plain merge analysis/results, ordinary author revisions, Branch/v1, and the existing closed Assembly/v1–v3 contracts. Earlier catalog builders retain their previous inventories. The combined builder checks canonical schema equality and exact type-definition equality before combining shared entries.

A real merge result M remains a `NetRevision/v1` with ordered exact parents and a separate complete merge command. An ordinary edit E after M retains the original single-parent author contract. Either can be an explicit leaf of Assembly/v3 or a member of a flat Assembly/v2 or Assembly/v3 child. Repeated exact revisions under distinct member IDs remain distinct instances. The existing two-level, closed-child restriction and explicit connection/completion semantics remain in force.

One public full read uses one Registry snapshot and a private active-reference path. Direct leaf validation and paired generated validation inherit the enclosing Assembly path. A child-v2 member reread inherits that path plus the child's exact reference. Parent validation at both producer preparation and final publication inherits the prospective result reference. Siblings extend their own active path; completed siblings do not enter a global visited set.

Every paired generated result must still be the exact ordinary `ValidatedClosedRevision`, with its original command, material references, declarations, and actual compilation. Accepting a merge leaf does not make a merge result a valid substitute for an Assembly's generated revision. Descriptor reads and full proof reads remain separate APIs. Full proof consumers rebuild merge analysis and explicit caller decisions before composing declarations; successful compilation alone does not resolve business conflicts.

Focused integration tests cover real M/E paths, child-v2 and flat-child-v3 paths, repeated instance origins, reopened full readers, exact no-write replay, damaged merge proofs and cycles, and parent/member/generated final-cut failures. Committed prefix resources may survive an interrupted or rejected final cut; restoring their exact inputs allows the same command to finish once. Validation status belongs to the corresponding execution evidence, not to this contract text.

This integration does not add runtime adoption, execution, joint-cut/latest guarantees, arbitrary nesting, custom merge order, or incompatible same-key HOST reconciliation. Those separate capabilities retain their existing plan status.
