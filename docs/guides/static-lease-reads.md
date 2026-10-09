---
name: rpnh-static-lease-reads
description: "Non-consuming exact resource-lease reads."
metadata:
  document-kind: guide
  audience: developer
  language: en
  counterpart: static-lease-reads_ZH.md
  revision: "2026-10-09.1"
  status: maintained
---

[English](static-lease-reads.md) | [中文](static-lease-reads_ZH.md)

# Static resource-lease read arcs

A declared input arc with `mode="read"` on a `resource_lease` place is a
non-consuming reference to an exact token in the adopted Petri marking.
It uses the same claim/reference fields as variable lease reads. There is no
second marking, reader table, scheduler or lock policy.

## What a reference means

- The exact token must be present, fresh, in the adopted net, and satisfy the
  declared read weight. The Registry verifies the immutable PN and checkpoint;
  a caller-supplied claim or consume index cannot replace that contract.
- The token remains unconsumed while the firing is active. Concurrent legal
  firings can reference the same token. An independent consuming control/input
  token, the existing budget, or other declared structure bounds occurrences.
  A read prerequisite by itself does not limit firing count or concurrency.
- Success retains the original token identity and state. It does not return,
  duplicate or mint the reference token. The same reference survives a sibling
  Success, active-claim reconstruction, and normal completion recovery.
- This is a Petri read prerequisite and resource reference, not a borrow or an
  authorization to use a physical resource. Existing exact-resource byte-read
  provenance remains unchanged. Dynamic resource access still requires its
  existing variable resource arc and access checks.

## Compatibility

Ordinary data/control read arcs keep their existing local consume-and-return
behavior. Their predecessor occurrence is consumed and a successor occurrence
carries the same resource. Static lease reads retain the original occurrence.

Existing `VariableResourceArc` declarations remain supported. A static read and
variable read may reference the same token once, or select different tokens in
one pool. The exact claim is their union, not the sum of duplicate references.
Every extra token must be explained by a declared arc; an over-wide claim is
rejected. A variable consuming claim and a static reference cannot own the same
occurrence concurrently. Legal variable `produce`/`edit` consume-return remains
unchanged.

A transition with only static references is structurally enabled while its
references are available. One candidate is enumerated per selection pass; the
read does not create an infinite local enumeration or act as a one-shot gate.

No schema migration is required. Existing `claimed_input_refs`, claim delta
`consumed_refs`, marking references and publication/index structures are reused.
The Module admission consume index and the local consume-return projection are
not interchangeable: ordinary reads retain their existing index semantics.

## Admission and recovery boundary

For transitions declaring static lease reads, the existing Registry admission
transaction additionally reconstructs the selected claim against the exact
adopted PN and checkpoint. It verifies claimed references and their version
index, preserves the Module's existing consume-index convention, and rejects
missing, foreign, malformed, surplus, or falsely consumed references. This
check also covers direct low-level admission and transaction proposals.

The additional check is limited to this static-read shape. It is not a claim
that every historical low-level invocation shape has received a new global
claim/index audit. Legal declarations without static lease reads retain their
existing behavior.

Exact-ref validation checks enabledness using the same explicitly selected
occurrence. A dead default/first variable carrier cannot veto a different valid
exact claim. Count/verdict/timed guards still apply. The default owner scheduler
is unchanged; this is not a new binding-search or liveness guarantee.

Selection, exact claim reconstruction, capacity preflight, declared-effect
projection and Success validation use the same static lease-read classification.
Registry hydration and settled-state reconstruction retain their original
exact-identity and state-equation checks.

## Graph changes and scope

The existing rules continue to apply: lease/reusable tokens cannot be reset or
ordinarily retired; owner replacement drains active firings, and operation net
replacement requires its existing exclusive active firing. This capability does
not make a particular application prerequisite undeletable after those rules
are satisfied. Application-specific origin protection is separate work.

This change does not implement child launch, transport ownership, bootstrap
acceptance, origin guards, H7, or physical-resource permission policy. Passing
these offline tests is not H7 or native-transport acceptance.

## Focused verification

`tests/test_static_lease_reads.py`, `tests/test_static_lease_interactions.py`,
and `tests/test_static_lease_exact_selection.py`
exercise real temporary Registries with direct admission, Start, products and
Success. They include concurrent readers with independent consuming inputs,
exact-reference negative tests, genuine nonempty variable claims, ordinary
read regressions, declared effects, cold reconstruction in a separate Python
process, completion recovery and existing graph-change restrictions.

Run with the project's existing supported environment:

```sh
python -m pytest -q tests/test_static_lease_reads.py tests/test_static_lease_interactions.py tests/test_static_lease_exact_selection.py
```

These cases need no socket, external provider/model, native client, or network.
