---
name: rpnh-user-collaboration
description: "Integrate trusted local users with exact proposal review, protected Branch acceptance and owner adoption."
metadata:
  document-kind: guide
  audience: trusted-host-developer
  language: en
  counterpart: user-collaboration_ZH.md
  revision: "2026-10-11.1"
  status: maintained
  basis: "trusted local HOST; plain author revisions; original Registry authority"
---

[English](user-collaboration.md) | [中文](user-collaboration_ZH.md)

# Controlled local user collaboration

`UserCollaborationHost` in `cpn.rpnh.collaboration` connects local participants
to a protected plain-author Branch. The target owner invites contributors,
reviews exact proposals, accepts a revision, and separately decides whether to
adopt it into a running task. The existing Registry and owner remain responsible
for publication, execution and recovery.

## Establish the trusted HOST boundary

Construct `UserCollaborationHost(core, gateway)` with the target's existing
owner Core and its original `RegistryRegistrationGateway`. Compose the explicit
`user_collaboration_schema_data()` inventory into the HOST's selected catalog;
this does not change the default catalog or grant authority.

Each participant session is derived from an owned original Registry, its source
binding, task and exact `principal/v1`. Route participants through their own
original Registries and principals. A display name or a JSON actor field cannot
establish that identity. Session and action handles are sealed to the target
EventStore instance; `resume_session` re-establishes the same original binding.
Keep these handles and Core objects inside the trusted HOST, outside model tools
and plugins. This is a local integration boundary, not external-account
authentication or isolation from code running as the same OS user.

Selected method signatures, with existing typed references supplied by the HOST:

```text
create_session(*, origin_core, principal_ref, command_id)
protect(session, *, branch_ref, runtime_net_ref=None, command_id)
join(session, invitation_ref, *, command_id)
review(session, proposal_ref, *, disposition, reason='', command_id)
```

These are interface shapes, not a standalone bootstrap or runnable example.

## Invite and submit

1. The target owner calls `protect` with an exact plain
   `collaboration_branch/v1`. For an existing run, supply its current typed
   `net_instance/v1` as `runtime_net_ref`. Protection is an immutable task-level
   project; incorrect target types are rejected before project publication.
2. The owner calls `invite` for the contributor's exact session, with role
   `propose`. The contributor calls `join` with that exact invitation.
3. `submit_proposal` binds the source TEXT, provenance, complete Module payload,
   target-local candidate revision, base, expected Branch and expected head.
   The candidate must descend from that exact base. A revised submission is a
   new fact with `previous_proposal_ref`; it does not rewrite the old proposal.

For completed execution output, the HOST uses `ingest_completed_text` and
`delivered_proposal_inputs`. The source owner route re-derives the successful
stock TEXT product from its original run, invocation and firing records. A
separate execution is explicitly bound with `bind_execution` before its first
invocation. The target's copied resource has a target publisher; its delivery
fact preserves the original producer. Imported author revisions are not inferred
from this TEXT transfer. Candidates still use the target-local authoring path.

## Review and accept the exact revision

The owner uses `review` with `approve` or `request_changes`; eligible contributors
can leave `comment` reviews. Approval pins the entire proposal, including its
provenance, candidate and Branch expectations.

An owner `decide(..., operation='advance', ...)` names the exact proposals,
approvals, result revision, Branch and head. `action` creates the trusted action
handle. Route it with `command` around the original gateway's
`advance_author_branch`, using that operation's exact idempotency key. A decision
alone does not advance the Branch.

At the original commit boundary, acceptance rechecks the current owner,
every required contributor's proposal eligibility, current exact approvals,
Branch expectations and result. A single proposal accepts its exact candidate.
A two-parent plain merge result, M, must carry the original verified merge proof,
use the expected common base, and include approval for every unaccepted direct
input. Labels or descriptor-only ancestry are insufficient.

The Branch successor and its collaboration receipt commit atomically.
`receipt_ref` identifies that receipt using the actual acceptance transaction's
idempotency key. Protected Branch aliases, ordinary adoption routes and owner
edits cannot bypass the guard by omitting the HOST action.

## Adopt through the original owner

Runtime adoption is a separate owner decision with `operation='adopt'`, the
accepted Branch receipt, exact accepted revision and current Branch/runtime
expectations. Pass its action as `collaboration_action` to the existing
`owner.control.edits.submit` path. Original replacement preparation, PN/marking
checks and owner admission rules still apply; acceptance does not itself change
the running net. See [native net operations](net-operations.md).

A contributor's later revocation does not undo an accepted Branch or prevent the
current owner from adopting that exact accepted revision. Adoption still checks
the current owner, accepted receipt and Branch/runtime expectations. Exact replay
retains actor and action identity, creates no new acceptance effects, and must
pass the current read fence.

## Keep reading authority separate

`revoke` changes contribution eligibility. `issue_read_access` and
`revoke_read_access` use the original observer grant/profile mechanism; they
control a different authority. Joining, including with role `read`, does not
itself issue a read grant. Bind an authorized observer context with
`bind_read_authority` for non-owner `read_fact` or `read_view` calls. These methods
require complete collaboration-record scope and return collaboration facts; PN
material uses the original scoped reader.
See [Registry read sessions](../reference/registry-read-sessions.md).

To inspect a plain merge, select the actual M revision in an authorized fixed-cut
reader. The stored projection is verified from its exact proof materials;
comparison with a parent uses `full_pair`, with merge correspondence and runtime
continuity `not_provided`. Read limits still apply: an unavailable or oversized
projection does not become a truncated success or a topology-only fallback.
See [independent exact-net comparison](comparison-context.md#plain-merge-results).

## Source entry points

- `cpn/rpnh/collaboration/user_collaboration.py`: HOST methods and exact handles
- `cpn/rpnh/registry/_event_store/user_collaboration.py`: original commit guard
- `cpn/rpnh/registry/user_collaboration_source.py`: successful TEXT provenance
- `cpn/rpnh/registry/_event_store/user_collaboration_merge.py`: original merge proof

For authoring and merge contracts, see [plain merge results](../reference/plain-merge-result.md).
