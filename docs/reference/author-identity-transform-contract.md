---
name: rpnh-author-identity-transform-contract
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: author-identity-transform-contract_ZH.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](author-identity-transform-contract.md) | [中文](author-identity-transform-contract_ZH.md)

# Explicit ordinary author split and fusion

`PlainModuleTransformAuthor` publishes a caller-defined closed Module with explicit author identity history. Opt in with `plain_transform_schema_data()` or `plain_transform_assembly_schema_data()`. Existing catalog composers, schemas, ordinary commands and Assembly/v2 resolver recipes are unchanged.

Call `publish` with `parent_ref`, complete `module`, total locator-to-ID `element_ids`, nonempty canonically sorted `transform_groups`, `copy_sources`, sorted `created_element_ids`, sorted `removed_element_ids`, and `command_id`. Every group has `kind` (`split` or `fusion`), exact `source_revision_ref`, sorted `source_element_ids` and sorted `target_element_ids`. A split is one-to-many; a fusion is many-to-one. All group members have the same element kind. Group source IDs retire, group target IDs are new, and groups cannot overlap. Every new transformed/copied/created ID must be absent from the entire verified ancestry.

Current IDs are partitioned into retained, transformed targets, copied and created. Parent IDs are partitioned into retained, transformed sources and removed. Retained IDs are the parent/current intersection; every other disposition is explicit. A retained ID means identity continuity, not unchanged content. Copy sources are nonconsuming exact parent edges and may also be retained, removed or transformed. The current v1 element map still describes locators, kinds and copies; the separate transformation map describes the complete many-sided history. Unaccounted IDs are rejected rather than inferred to be created or removed.

The first durable command locks the complete request, exact source and historical material/HOST/schema pins, output identities, payloads and metadata. The full reader reconstructs history, total mapping, actual compile and all five output resources in one read cut. Prefix retry accepts only the original complete command; a stale writer cannot publish. Legacy ordinary author and merge consumers explicitly reject transform parents/histories. Unsupported proof families are also rejected when hidden in ancestry.

The bounded source domain is local exact-owner unconstrained plain ordinary root/single-parent history and this transform family's own descendants. Graph, open/adapted, merge/transplant, generated Assembly and opaque constrained histories are not supported. The result has exactly its selected parent, preserves lineage and carries no selected changes or current adaptation. Caller declarations do not establish behavioral/business equivalence, select donor changes, or authorize execution.

Branch/v1 can advance the exact result using its existing three expectations. With explicit transform catalog support, unchanged Assembly/v2 reconstructs the complete member proof and pins its current four materials. Actual lowering origins identify current author IDs; the member proof explains their parent history. The generated G remains a strict ordinary closed proof paired with its exact Assembly. Compiler one-to-many lowering and many-to-one fusion remain separate from author split/fusion history.
