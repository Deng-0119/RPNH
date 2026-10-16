"""Owner checkpoint-reentry closure of stale, unfinished publication roots."""
from __future__ import annotations

import json
from typing import Mapping

from ..event_store import RegistryConflict
from ..models import VersionRef
from ..schema_catalog import SchemaGovernanceError
from .accounting import _CANONICAL_EVENT_SQL


_COMMAND_FIELDS = frozenset({
    "firing_logical_id", "firing_version_id",
    "invocation_logical_id", "invocation_version_id",
    "authorization_logical_id", "authorization_version_id",
    "marking_checkpoint_logical_id", "marking_checkpoint_version_id",
})


def validate_commands(commands) -> None:
    if any(set(item) != _COMMAND_FIELDS
           or any(not isinstance(value, str) or not value
                  for value in item.values()) for item in commands):
        raise TypeError("firing supersession command is malformed")
    for field in ("firing_version_id", "firing_logical_id",
                  "invocation_version_id", "invocation_logical_id"):
        if len({item[field] for item in commands}) != len(commands):
            raise RegistryConflict("duplicate or conflicting firing supersession")


def _ref(kind, logical_id, version_id):
    return {"entity_type": kind, "logical_id": logical_id,
            "version_id": version_id}


def _object_ref(item):
    return _ref(item.object_type, str(item.logical_id), str(item.version_id))


def validate_commit(store, db, *, task_id, net_instance_id, writer_epoch,
                    objects, events, relations, commands):
    """Prove the complete closure before any mutation in BEGIN IMMEDIATE.

    Return only the relation IDs allowed to refer to old provisional roots.
    No temporary member becomes visible through this proof.
    """
    validate_commands(commands)
    reopened = [event for event in events if event.event_type == "run_reopened/v1"]
    if not commands and not reopened:
        return set()
    if len(reopened) != 1:
        raise RegistryConflict("firing supersession requires exactly one run reopen")
    authorizations = [item for item in objects
                      if item.object_type == "run_reopen_authorization/v1"]
    checkpoints = [item for item in objects
                   if item.object_type == "marking_checkpoint/v1"]
    successors = [item for item in objects
                  if item.object_type == "run_execution_authority/v1"]
    if len(authorizations) != 1 or len(checkpoints) != 1 or len(successors) != 1:
        raise RegistryConflict("reentry requires new authorization/checkpoint/authority")
    authorization, checkpoint, successor = authorizations[0], checkpoints[0], successors[0]
    auth, cut, next_authority = (item.metadata for item in (
        authorization, checkpoint, successor))
    auth_ref, cut_ref, successor_ref = (_object_ref(item) for item in (
        authorization, checkpoint, successor))
    net_ref, task_ref, run_ref = (auth.get(key) for key in (
        "net_instance_ref", "task_ref", "run_ref"))
    if (not isinstance(net_ref, Mapping) or not isinstance(task_ref, Mapping)
            or not isinstance(run_ref, Mapping)
            or net_ref.get("entity_type") != "net_instance/v1"
            or net_ref.get("logical_id") != str(net_instance_id)
            or task_ref.get("entity_type") != "task/v1"
            or task_ref.get("logical_id") != str(task_id)
            or auth.get("run_reopen_authorization_ref") != auth_ref
            or auth.get("reentry_checkpoint_ref") != cut_ref
            or cut.get("marking_checkpoint_ref") != cut_ref
            or cut.get("net_instance_ref") != net_ref
            or cut.get("reentry_authorization_ref") != auth_ref
            or cut.get("previous_checkpoint_ref")
            != auth.get("expected_current_checkpoint_ref")
            or cut.get("settled") is not True
            or cut.get("settlement_delta_ref") is not None
            or cut.get("transition_firing_refs") != []
            or next_authority.get("run_execution_authority_ref") != successor_ref
            or auth.get("successor_run_authority_ref") != successor_ref
            or next_authority.get("latest_checkpoint_ref") != cut_ref
            or next_authority.get("reopen_authorization_ref") != auth_ref
            or next_authority.get("task_ref") != task_ref
            or next_authority.get("run_ref") != run_ref):
        raise RegistryConflict("reentry supersession differs from task/run/net closure")
    for item in (authorization, checkpoint, successor):
        if (item.producer_invocation_id is not None or db.execute(
                "SELECT 1 FROM objects WHERE version_id=?",
                (str(item.version_id),)).fetchone() is not None):
            raise RegistryConflict("reentry closure must be new owner material")
    event = reopened[0]
    if (event.producer_invocation_id is not None
            or event.aggregate_id != run_ref.get("logical_id")
            or event.payload.get("run_reopen_authorization_ref") != auth_ref
            or event.payload.get("reentry_checkpoint_ref") != cut_ref
            or event.payload.get("successor_run_authority_ref") != successor_ref
            or event.payload.get("expected_run_authority_ref")
            != auth.get("expected_run_authority_ref")):
        raise RegistryConflict("reentry supersession lacks its exact reopen event")
    checkpoint_events = [item for item in events
                         if item.event_type == "marking_checkpoint_committed/v1"]
    if (len(checkpoint_events) != 1
            or checkpoint_events[0].producer_invocation_id is not None
            or checkpoint_events[0].payload.get("checkpoint_ref") != cut_ref
            or checkpoint_events[0].payload.get("reentry_authorization_ref") != auth_ref
            or checkpoint_events[0].payload.get("net_instance_ref") != net_ref
            or checkpoint_events[0].payload.get("previous_checkpoint_ref")
            != auth.get("expected_current_checkpoint_ref")):
        raise RegistryConflict("supersession lacks its exact checkpoint event")
    authority_row = db.execute(
        "SELECT o.metadata_json FROM objects o JOIN events e "
        "ON e.event_id=o.published_event_id JOIN transactions t "
        "ON t.transaction_id=o.transaction_id "
        "WHERE o.object_type='run_execution_authority/v1' AND e.task_id=? "
        "AND t.status='committed' AND " + _CANONICAL_EVENT_SQL +
        " ORDER BY e.ordinal DESC LIMIT 1", (str(task_id),)).fetchone()
    prior = json.loads(authority_row["metadata_json"]) if authority_row else {}
    native_run = db.execute(
        "SELECT value FROM registry_meta WHERE key='native_run_ref'").fetchone()
    if (prior.get("run_execution_authority_ref") != auth.get("expected_run_authority_ref")
            or prior.get("latest_checkpoint_ref") != auth.get("expected_current_checkpoint_ref")
            or prior.get("task_ref") != task_ref or prior.get("run_ref") != run_ref
            or native_run is None or json.loads(native_run["value"]) != run_ref
            or prior.get("status") not in {"running", "stopped_by_owner", "terminal"}):
        raise RegistryConflict("reentry supersession lost current run authority")
    generation = int(prior.get("execution_generation", 0)) + 1
    expected_successor = {
        **prior, "run_execution_authority_ref": successor_ref,
        "latest_checkpoint_ref": cut_ref, "status": "stopped_by_owner",
        "terminal_evidence_ref": None, "execution_generation": generation,
        "generation_source_checkpoint_ref": auth.get("selected_checkpoint_ref"),
        "reopen_authorization_ref": auth_ref,
    }
    if (next_authority != expected_successor
            or auth.get("execution_generation") != generation
            or event.payload.get("execution_generation") != generation
            or cut.get("reentry_generation") != generation
            or auth.get("expected_terminal_evidence_ref") != prior.get("terminal_evidence_ref")
            or cut.get("reentry_source_checkpoint_ref") != auth.get("selected_checkpoint_ref")
            or event.payload.get("selected_checkpoint_ref") != auth.get("selected_checkpoint_ref")
            or event.command_id != auth.get("command_id")
            or event.payload.get("reason") != auth.get("reason")):
        raise RegistryConflict("supersession differs from the authorized execution generation")
    roots = db.execute(
        "SELECT p.*,t.status AS opening_status,t.writer_epoch AS opening_epoch,"
        "t.task_id AS opening_task FROM firing_publications p "
        "JOIN transactions t ON t.transaction_id=p.opened_transaction_id "
        "WHERE p.net_version_id=? AND p.state='PROVISIONAL'",
        (net_ref.get("version_id"),)).fetchall()
    if {row["firing_version_id"] for row in roots} != {
            item["firing_version_id"] for item in commands}:
        raise RegistryConflict("reentry must supersede all remaining provisional roots")
    if not commands:
        return set()
    allowed_objects = {"run_reopen_authorization/v1", "marking_checkpoint/v1",
                       "run_execution_authority/v1", "petri_token/v1", "workspace_revision/v1"}
    allowed_events = {"run_reopened/v1", "marking_checkpoint_committed/v1",
                      "object_version_published/v1", "relation_published/v1",
                      "transaction_committed/v1"}
    if (any(item.object_type not in allowed_objects
            or item.producer_invocation_id is not None for item in objects)
            or any(item.event_type not in allowed_events
                   or item.producer_invocation_id is not None for item in events)
            or any(not item.system_owned or item.producer_invocation_id is not None
                   for item in relations)):
        raise RegistryConflict("supersession cannot mix ordinary business settlement")
    expected_tokens = [item.get("replacement_token_ref")
                       for item in cut.get("reentry_token_mappings", [])
                       if isinstance(item, Mapping)]
    expected_workspaces = auth.get("workspace_reentry_revision_refs", [])
    for kind, expected in (("petri_token/v1", expected_tokens),
                           ("workspace_revision/v1", expected_workspaces)):
        staged = [item for item in objects if item.object_type == kind]
        if (len(staged) != len(expected)
                or any(_object_ref(item) not in expected for item in staged)
                or (kind == "workspace_revision/v1" and any(
                    item.metadata.get("disposition") != "owner_reopen"
                    or item.metadata.get("reopen_authorization_ref") != auth_ref
                    for item in staged))):
            raise RegistryConflict("supersession batch contains unrelated reentry products")
    scopes = []
    for row in db.execute(
            "SELECT o.metadata_json FROM objects o JOIN events e "
            "ON e.event_id=o.published_event_id JOIN transactions t "
            "ON t.transaction_id=o.transaction_id "
            "WHERE o.object_type='user_authority_decision/v1' "
            "AND t.status='committed' AND " + _CANONICAL_EVENT_SQL):
        document = json.loads(row["metadata_json"])
        if (document.get("authority_kind") == "scope"
                and document.get("status") == "effective"
                and task_ref in document.get("governed_artifact_refs", [])):
            scopes.append(document)
    maximum = max((item["effective_sequence"] for item in scopes), default=None)
    current_scopes = [item for item in scopes if item["effective_sequence"] == maximum]
    if (len(current_scopes) != 1
            or current_scopes[0].get("decision_ref")
            != auth.get("control_authority_ref")
            or current_scopes[0].get("user_principal_ref") != auth.get("owner_principal_ref")
            or event.producer_principal != auth.get("owner_principal_ref", {}).get("logical_id")):
        raise RegistryConflict("supersession lacks current task owner scope")
    by_version = {row["firing_version_id"]: row for row in roots}
    allowed_relations = set()
    lease_ids = set()
    for command in commands:
        root = by_version[command["firing_version_id"]]
        firing_ref = _ref("transition_firing/v1", command["firing_logical_id"],
                          command["firing_version_id"])
        invocation_ref = _ref("invocation/v1", command["invocation_logical_id"],
                              command["invocation_version_id"])
        if (any(root[key] != command[key] for key in (
                "firing_logical_id", "invocation_logical_id", "invocation_version_id"))
                or root["opening_status"] != "committed"
                or root["opening_task"] != str(task_id)
                or int(root["opening_epoch"]) >= writer_epoch
                or _ref("run_reopen_authorization/v1", command["authorization_logical_id"],
                        command["authorization_version_id"]) != auth_ref
                or _ref("marking_checkpoint/v1", command["marking_checkpoint_logical_id"],
                        command["marking_checkpoint_version_id"]) != cut_ref):
            raise RegistryConflict("supersession root is not an exact stale invocation")
        for ref, self_key in ((firing_ref, "transition_firing_ref"),
                              (invocation_ref, "invocation_ref")):
            row = db.execute(
                "SELECT o.metadata_json FROM objects o JOIN transactions t "
                "ON t.transaction_id=o.transaction_id WHERE o.version_id=? "
                "AND o.logical_id=? AND o.object_type=? AND t.status='committed'",
                (ref["version_id"], ref["logical_id"], ref["entity_type"])).fetchone()
            document = json.loads(row["metadata_json"]) if row else {}
            if (document.get(self_key) != ref or document.get("task_ref") != task_ref
                    or document.get("net_instance_ref") != net_ref
                    or document.get("operation_binding_ref", {}).get("version_id")
                    != root["operation_binding_version_id"]
                    or document.get("admission_marking_checkpoint_ref", {}).get("version_id")
                    != root["admission_checkpoint_version_id"]
                    or (self_key == "invocation_ref"
                        and document.get("own_transition_firing_ref") != firing_ref)):
                raise RegistryConflict("supersession firing/invocation binding differs")
        lease = document.get("operation_execution_lease_ref")
        if isinstance(lease, Mapping):
            lease_ids.update(lease.get(key) for key in ("logical_id", "version_id")
                             if isinstance(lease.get(key), str))
        matching = [relation for relation in relations
                    if relation.relation_type == "supersedes"
                    and relation.strength == "strong"
                    and isinstance(relation.source, VersionRef)
                    and isinstance(relation.target, VersionRef)
                    and _ref(relation.source.entity_type, str(relation.source.entity_id),
                             str(relation.source.version_id)) == auth_ref
                    and _ref(relation.target.entity_type, str(relation.target.entity_id),
                             str(relation.target.version_id)) == firing_ref]
        if len(matching) != 1:
            raise RegistryConflict("supersession requires one exact system relation per root")
        allowed_relations.add(str(matching[0].relation_id))
    _reject_completions(store, db, task_id, roots, lease_ids)
    return allowed_relations


def _reject_completions(store, db, task_id, roots, lease_ids):
    firing_ids = {row[key] for row in roots
                  for key in ("firing_logical_id", "firing_version_id")}
    invocation_ids = {row[key] for row in roots
                      for key in ("invocation_logical_id", "invocation_version_id")}
    for row in db.execute(
            "SELECT e.* FROM events e JOIN transactions t "
            "ON t.transaction_id=e.transaction_id WHERE e.task_id=? "
            "AND e.event_type='registered_operation_completion_recorded/v1' "
            "AND t.status='committed'", (str(task_id),)):
        try:
            payload = json.loads(row["payload_json"])
            store.catalog.validate_event_payload(row["event_type"], payload)
        except (TypeError, ValueError, KeyError, SchemaGovernanceError) as exc:
            raise RegistryConflict("malformed registered completion prevents supersession") from exc
        firing = payload.get("transition_firing_ref", {})
        invocation = payload.get("invocation_ref", {})
        lease = payload.get("operation_execution_lease_ref", {})
        if (row["producer_invocation_id"] in invocation_ids
                or row["aggregate_id"] in lease_ids
                or any(lease.get(key) in lease_ids for key in ("logical_id", "version_id"))
                or any(firing.get(key) in firing_ids for key in ("logical_id", "version_id"))
                or any(invocation.get(key) in invocation_ids for key in ("logical_id", "version_id"))
                or db.execute(
                    "SELECT 1 FROM firing_temporary_members m JOIN firing_publications p "
                    "ON p.firing_version_id=m.firing_version_id "
                    "WHERE m.member_kind='event' AND m.member_identity=? "
                    "AND p.state='PROVISIONAL' AND p.net_version_id=?",
                    (row["event_id"], roots[0]["net_version_id"])).fetchone()):
            raise RegistryConflict("registered completion must be recovered before supersession")


def validate_replay(db, commands, transaction_id):
    for command in commands:
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                          (command["firing_version_id"],)).fetchone()
        if (root is None or root["state"] != "SUPERSEDED"
                or root["published_transaction_id"] != str(transaction_id)
                or root["marking_checkpoint_version_id"] != command["marking_checkpoint_version_id"]
                or any(root[key] != command[key] for key in (
                    "firing_logical_id", "invocation_logical_id", "invocation_version_id"))
                or any(root[key] is not None for key in (
                    "operation_result_version_id", "workspace_lineage_id", "workspace_revision_version_id"))):
            raise RegistryConflict("supersession replay differs from committed closure")


def apply(store, db, *, commands, transaction_id):
    for command in commands:
        store._before_firing_authority_mutation(
            db, firing_version_id=command["firing_version_id"],
            transaction_id=str(transaction_id))
        changed = db.execute(
            "UPDATE firing_publications SET state='SUPERSEDED',published_transaction_id=?,"
            "marking_checkpoint_version_id=? WHERE firing_version_id=? AND state='PROVISIONAL' "
            "AND operation_result_version_id IS NULL AND workspace_lineage_id IS NULL "
            "AND workspace_revision_version_id IS NULL",
            (str(transaction_id), command["marking_checkpoint_version_id"],
             command["firing_version_id"])).rowcount
        if changed != 1:
            raise RegistryConflict("supersession SQL CAS did not close one root")
        store._after_firing_authority_mutation(
            db, firing_version_id=command["firing_version_id"],
            transaction_id=str(transaction_id))


def visibility_relations(context):
    """Reprove only exact system supersedes edges for proposal visibility.

    The commit guard separately compares the actual command inventory. This
    independent proof also keeps direct proposal validation fail-closed.
    """
    commands = []
    for relation in context.relations:
        if (relation.relation_type != "supersedes" or not relation.system_owned
                or not isinstance(relation.source, VersionRef)
                or not isinstance(relation.target, VersionRef)
                or relation.source.entity_type != "run_reopen_authorization/v1"
                or relation.target.entity_type != "transition_firing/v1"):
            continue
        authorization = context.new_by_version.get(str(relation.source.version_id))
        if authorization is None:
            raise RegistryConflict("supersession relation lacks new authorization")
        checkpoint = authorization.metadata.get("reentry_checkpoint_ref")
        root = context.db.execute(
            "SELECT * FROM firing_publications WHERE firing_version_id=?",
            (str(relation.target.version_id),)).fetchone()
        if root is None or not isinstance(checkpoint, Mapping):
            raise RegistryConflict("supersession relation lacks its root/checkpoint")
        commands.append({
            "firing_logical_id": str(relation.target.entity_id),
            "firing_version_id": str(relation.target.version_id),
            "invocation_logical_id": root["invocation_logical_id"],
            "invocation_version_id": root["invocation_version_id"],
            "authorization_logical_id": str(relation.source.entity_id),
            "authorization_version_id": str(relation.source.version_id),
            "marking_checkpoint_logical_id": checkpoint.get("logical_id"),
            "marking_checkpoint_version_id": checkpoint.get("version_id"),
        })
    if not commands:
        return set()
    return validate_commit(
        context.event_store, context.db, task_id=context.task_id,
        net_instance_id=context.net_instance_id,
        writer_epoch=context.transaction_writer_epoch,
        objects=context.objects, events=context.events, relations=context.relations,
        commands=commands)
