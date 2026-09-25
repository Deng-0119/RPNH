"""Canonical, firing-local, and provisional EventStore authority views."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from ..event_store import (
    CanonicalView,
    FiringView,
    ProvisionalObservationView,
    RegistryAuthorityView,
    RegistryConflict,
    RegistryCorruptError,
    _task_model_call_cap_handoff_member_authority_sql,
    _task_model_call_cap_handoff_member_publication_ordinal,
)
from ..identities import TypedId
from ..models import EventEnvelope
from ..schema_catalog import canonical_json

def canonical_events(
        store, *, after_ordinal: int = 0,
        through_ordinal: int | None = None,
        stream_id: str | None = None) -> tuple[EventEnvelope, ...]:
    """Return facts whose firing publication is authoritative at the head."""

    upper = store.max_ordinal() if through_ordinal is None else through_ordinal
    if upper < after_ordinal:
        return ()
    stream_clause = "" if stream_id is None else "AND e.stream_id=? "
    parameters: list[Any] = [after_ordinal, upper]
    if stream_id is not None:
        parameters.append(stream_id)
    predicate = store._canonical_member_sql(
        member_kind="event", member_identity_sql="e.event_id",
        published_event_sql="e.ordinal")
    parameters.extend((upper, upper, upper, upper))
    with store.connect() as db:
        rows = db.execute(
            "SELECT e.* FROM events e "
            "WHERE e.ordinal>? AND e.ordinal<=? "
            f"{stream_clause}AND {predicate} ORDER BY e.ordinal",
            tuple(parameters),
        ).fetchall()
    return tuple(store._row_to_envelope(row) for row in rows)

def ordered_firing_record(
        store, firing_version_id: TypedId | str,
) -> Mapping[str, Any]:
    """Return one firing's exact event and Petri closure in ordinal order."""
    firing_id = str(firing_version_id)
    with store.connect() as db:
        publication = db.execute(
            "SELECT * FROM firing_publications "
            "WHERE firing_version_id=?", (firing_id,)).fetchone()
        if publication is None:
            raise RegistryCorruptError(
                "firing reconstruction has no publication root")
        transaction_ids = tuple({
            str(publication["opened_transaction_id"]),
            str(publication["published_transaction_id"])
            if publication["published_transaction_id"] is not None else "",
        } - {""})
        for transaction_id in transaction_ids:
            transaction = db.execute(
                "SELECT status FROM transactions WHERE transaction_id=?",
                (transaction_id,)).fetchone()
            if transaction is None or transaction["status"] != "committed":
                raise RegistryCorruptError(
                    "firing reconstruction transaction authority is not committed")
        member_rows = db.execute(
            "SELECT member_kind,member_identity FROM "
            "firing_temporary_members WHERE firing_version_id=? "
            "ORDER BY member_kind,member_identity", (firing_id,)).fetchall()
        event_rows = db.execute(
            "SELECT e.* FROM firing_temporary_members m JOIN events e "
            "ON e.event_id=m.member_identity JOIN transactions t "
            "ON t.transaction_id=e.transaction_id AND t.status='committed' "
            "WHERE "
            "m.firing_version_id=? AND m.member_kind='event' "
            "ORDER BY e.ordinal", (firing_id,)).fetchall()
        events = tuple(store._row_to_envelope(row) for row in event_rows)

        def metadata(version_id: str, object_type: str) -> Mapping[str, Any]:
            row = db.execute(
                "SELECT object_type,metadata_json FROM objects "
                "WHERE version_id=?", (version_id,)).fetchone()
            if row is None or row["object_type"] != object_type:
                raise RegistryCorruptError(
                    f"firing reconstruction is missing {object_type}: {version_id}")
            try:
                value = json.loads(str(row["metadata_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RegistryCorruptError(
                    "firing reconstruction object metadata is malformed") from exc
            if not isinstance(value, Mapping):
                raise RegistryCorruptError(
                    "firing reconstruction metadata is not a mapping")
            return dict(value)

        firing = metadata(firing_id, "transition_firing/v1")
        admission_version = str(publication["admission_checkpoint_version_id"])
        admission = metadata(admission_version, "marking_checkpoint/v1")
        successor_version = publication["marking_checkpoint_version_id"]
        successor = (metadata(str(successor_version), "marking_checkpoint/v1")
                     if successor_version else None)
        ordered = tuple({
            "event_id": str(event.event_id),
            "ordinal": event.ordinal,
            "event_type": event.event_type,
        } for event in events)
        if publication["state"] != "PUBLISHED":
            return {
                "firing_version_id": firing_id,
                "state": str(publication["state"]),
                "firing": firing,
                "firing_completion": None,
                "admission_checkpoint": admission,
                "successor_checkpoint": successor,
                "marking_delta": None,
                "ordered_event_records": ordered,
                "events": events,
                "temporary_members": tuple(
                    (str(row["member_kind"]), str(row["member_identity"]))
                    for row in member_rows),
            }

        if successor is None:
            raise RegistryCorruptError(
                "published firing has no successor checkpoint")
        settlement = tuple(
            event for event in events
            if event.event_type == "transition_firing_settled/v1"
            and event.aggregate_id == str(firing.get(
                "transition_firing_ref", {}).get("logical_id", "")))
        checkpoint_events = tuple(
            event for event in events
            if event.event_type == "marking_checkpoint_committed/v1"
            and event.payload.get("checkpoint_ref") == successor.get(
                "marking_checkpoint_ref"))
        if len(settlement) != 1 or len(checkpoint_events) != 1:
            raise RegistryCorruptError(
                "published firing lacks one settlement/checkpoint event")
        settlement_payload = settlement[0].payload
        completion_ref = settlement_payload.get("firing_completion_ref")
        if not isinstance(completion_ref, Mapping):
            raise RegistryCorruptError(
                "published firing settlement lacks completion authority")
        completion = metadata(str(completion_ref.get("version_id", "")),
                              "firing_completion/v2")
        delta_ref = successor.get("settlement_delta_ref")
        previous = successor.get("previous_checkpoint_ref")
        admission_ref = admission.get("marking_checkpoint_ref")
        generic_bridges = tuple(event for event in events
            if event.event_type == "net_adopted/v1" and
            event.payload.get("operation_revision", {}).get("source_firing_ref") == firing.get("transition_firing_ref") and
            event.payload.get("operation_revision", {}).get("successor_checkpoint_ref") == successor.get("marking_checkpoint_ref"))
        settlement_predecessor = admission
        if len(generic_bridges) == 1:
            bridge = generic_bridges[0]
            if (bridge.payload["operation_revision"]["predecessor_checkpoint_ref"] != previous
                    or previous is None or bridge.payload["supersedes_net_ref"] != firing["net_instance_ref"]):
                raise RegistryCorruptError("generic Success predecessor differs from exact registered adoption")
            settlement_predecessor = metadata(previous["version_id"], "marking_checkpoint/v1")
            if (settlement_predecessor["marking_checkpoint_ref"] != previous
                    or settlement_predecessor["net_instance_ref"] != firing["net_instance_ref"]):
                raise RegistryCorruptError("generic Success predecessor is not exact old-NET authority")
        elif previous != admission_ref:
            # Independent firings may be admitted from the same checkpoint
            # and settle serially. Pair-CAS commits the later sibling from
            # the then-current checkpoint while preserving its immutable
            # admission checkpoint as provenance.
            if not isinstance(previous, Mapping):
                raise RegistryCorruptError(
                    "firing reconstruction lacks settlement predecessor")
            settlement_predecessor = metadata(
                str(previous.get("version_id", "")),
                "marking_checkpoint/v1")
            if (settlement_predecessor.get("marking_checkpoint_ref") != previous
                    or settlement_predecessor.get("net_instance_ref")
                    != firing.get("net_instance_ref")):
                raise RegistryCorruptError(
                    "sibling Success predecessor is not exact current-NET authority")
        if not isinstance(delta_ref, Mapping):
            raise RegistryCorruptError(
                "firing reconstruction predecessor or delta ref differs")
        delta = metadata(str(delta_ref.get("version_id", "")),
                         "marking_delta/v1")
        if (delta.get("marking_delta_ref") != delta_ref
                or delta.get("phase") != "settlement"
                or delta.get("net_instance_ref") != firing.get(
                    "net_instance_ref")
                or delta.get("transition_firing_refs") != [firing.get(
                    "transition_firing_ref")]):
            raise RegistryCorruptError(
                "firing reconstruction delta is not its exact settlement")
        if (completion.get("firing_completion_ref") != completion_ref
                or completion.get("transition_firing_ref")
                != firing.get("transition_firing_ref")
                or completion.get("marking_delta_ref") != delta_ref
                or completion.get("successor_checkpoint_ref")
                != successor.get("marking_checkpoint_ref")):
            raise RegistryCorruptError(
                "firing completion is not linked to its exact closure")

        def token_keys(values: object, label: str) -> set[bytes]:
            if not isinstance(values, list):
                raise RegistryCorruptError(
                    f"firing reconstruction {label} is malformed")
            result: set[bytes] = set()
            for value in values:
                if (not isinstance(value, Mapping)
                        or value.get("entity_type") != "petri_token/v1"):
                    raise RegistryCorruptError(
                        f"firing reconstruction {label} contains a non-token ref")
                version_id = str(value.get("version_id", ""))
                token = db.execute(
                    "SELECT logical_id,object_type FROM objects WHERE version_id=?",
                    (version_id,)).fetchone()
                if (token is None
                        or token["object_type"] != "petri_token/v1"
                        or token["logical_id"] != value.get("logical_id")):
                    raise RegistryCorruptError(
                        f"firing reconstruction {label} token is not registered")
                result.add(canonical_json(value))
            if len(result) != len(values):
                raise RegistryCorruptError(
                    f"firing reconstruction {label} repeats a token ref")
            return result

        before = token_keys(settlement_predecessor.get("token_refs"), "pre-marking")
        consumed = token_keys(delta.get("consumed_refs"), "consumed")
        deposited = token_keys(delta.get("deposited_refs"), "deposited")
        after = token_keys(successor.get("token_refs"), "post-marking")
        operation_bridges = tuple(event for event in events
            if event.event_type == "net_adopted/v1" and
            event.payload.get("operation_revision", {}).get("source_firing_ref") == firing.get("transition_firing_ref") and
            event.payload.get("operation_revision", {}).get("successor_checkpoint_ref") == successor.get("marking_checkpoint_ref"))
        mapped_successor = False
        if len(operation_bridges) == 1:
            witness = operation_bridges[0].payload["operation_revision"]
            mappings = witness["token_mappings"]
            sources = token_keys([m["source_token_ref"] for m in mappings], "revision sources")
            targets = token_keys([m["new_token_ref"] for m in mappings], "revision targets")
            retired = token_keys(witness["instruction"]["retire_token_refs"], "revision retirements")
            declared = delta.get("declared_effects", {})
            activation_values = [activation["token_ref"]
                for effect in declared.get("effects", [])
                if effect.get("effect_kind") == "structural_revision"
                for activation in effect.get("activation_witnesses", [])]
            activations = token_keys(
                activation_values, "revision activations")
            mapped_successor = (sources.isdisjoint(retired) and sources | retired == before - consumed | deposited
                and targets.isdisjoint(activations)
                and targets | activations == after
                and witness["predecessor_checkpoint_ref"] == previous)
            if not mapped_successor:
                raise RegistryCorruptError("firing reconstruction lacks exact ordinary-success mapping accounting")
        if (not consumed.issubset(before)
                or (not mapped_successor and before - consumed | deposited != after)):
            raise RegistryCorruptError(
                "firing reconstruction violates pre-consumed+deposited=post")

        net_ref = firing.get("net_instance_ref")
        successor_net_ref = successor.get("net_instance_ref")
        if not isinstance(net_ref, Mapping) or not isinstance(
                successor_net_ref, Mapping):
            raise RegistryCorruptError(
                "firing reconstruction lacks exact net references")
        net = metadata(str(net_ref.get("version_id", "")),
                       "net_instance/v1")
        if net.get("net_instance_ref") != net_ref:
            raise RegistryCorruptError(
                "firing reconstruction net identity differs")
        declaration = net.get("team_net_declaration_resource_ref")
        if (not isinstance(declaration, Mapping)
                or not declaration.get("resource_version_id")):
            raise RegistryCorruptError(
                "firing reconstruction net lacks declaration authority")
        declaration_row = db.execute(
            "SELECT logical_id,object_type FROM objects WHERE version_id=?",
            (str(declaration["resource_version_id"]),)).fetchone()
        if (declaration_row is None
                or declaration_row["object_type"] != "resource_version/v1"
                or declaration_row["logical_id"] != declaration.get(
                    "resource_id")):
            raise RegistryCorruptError(
                "firing reconstruction declaration authority is missing")
        if successor_net_ref != net_ref:
            bridges = tuple(
                event for event in events
                if event.event_type == "structural_growth_adopted/v1"
                and event.payload.get("predecessor_net_ref") == net_ref
                and event.payload.get("candidate_net_ref") == successor_net_ref
                and event.payload.get("predecessor_marking_checkpoint_ref")
                == admission_ref
                and event.payload.get("candidate_marking_checkpoint_ref")
                == successor.get("marking_checkpoint_ref"))
            bridges = tuple(event for event in events
                if event.event_type == "net_adopted/v1" and
                event.payload.get("supersedes_net_ref") == net_ref and
                event.payload.get("net_instance_ref") == successor_net_ref and
                event.payload.get("operation_revision", {}).get("source_firing_ref") == firing.get("transition_firing_ref") and
                event.payload.get("operation_revision", {}).get("successor_checkpoint_ref") == successor.get("marking_checkpoint_ref"))
            if len(bridges) != 1:
                raise RegistryCorruptError(
                    "firing reconstruction lacks one structural net bridge")
            successor_net = metadata(
                str(successor_net_ref.get("version_id", "")),
                "net_instance/v1")
            if successor_net.get("net_instance_ref") != successor_net_ref:
                raise RegistryCorruptError(
                    "successor net identity differs")

        persisted_ordered = settlement_payload.get("ordered_event_records")
        if persisted_ordered is not None and persisted_ordered != list(ordered):
            raise RegistryCorruptError(
                "settlement ordered event record differs from members")
        settlement_event_id = settlement_payload.get("settlement_event_id")
        if (settlement_event_id is not None
                and settlement_event_id != str(settlement[0].event_id)):
            raise RegistryCorruptError(
                "settlement event identity differs from its persisted record")
        checkpoint_event_id = checkpoint_events[0].payload.get(
            "settlement_event_id")
        if (checkpoint_event_id is not None
                and checkpoint_event_id != str(settlement[0].event_id)):
            raise RegistryCorruptError(
                "checkpoint commit is not linked to settlement event")
        return {
            "firing_version_id": firing_id,
            "state": "PUBLISHED",
            "firing": firing,
            "firing_completion": completion,
            "admission_checkpoint": admission,
            "successor_checkpoint": successor,
            "marking_delta": delta,
            "ordered_event_records": ordered,
            "events": events,
            "temporary_members": tuple(
                (str(row["member_kind"]), str(row["member_identity"]))
                for row in member_rows),
        }

def reconstruct_firing_state(
        store, firing_version_id: TypedId | str,
) -> Mapping[str, Any]:
    """Return settled Petri state reconstructed from Registry facts."""
    record = store.ordered_firing_record(firing_version_id)
    if record["state"] != "PUBLISHED":
        raise RegistryConflict(
            "firing state reconstruction requires a settled firing")
    return {
        "predecessor_checkpoint": record["admission_checkpoint"],
        "successor_checkpoint": record["successor_checkpoint"],
        "marking_delta": record["marking_delta"],
        "firing_completion": record["firing_completion"],
        "net_instance_ref": record["firing"].get("net_instance_ref"),
        "token_refs": record["successor_checkpoint"].get("token_refs", []),
        "ordered_event_records": record["ordered_event_records"],
    }

def canonical_view(
        store, *, through_ordinal: int | None = None) -> CanonicalView:
    """Capture one immutable canonical read boundary."""

    with store.connect() as db:
        row = db.execute(
            "SELECT COALESCE(MAX(ordinal),0) AS maximum FROM events"
        ).fetchone()
    maximum = int(row["maximum"] if row is not None else 0)
    chosen = maximum if through_ordinal is None else through_ordinal
    if not isinstance(chosen, int) or isinstance(chosen, bool):
        raise TypeError("canonical view requires one exact ordinal")
    if chosen < 0 or chosen > maximum:
        raise RegistryConflict(
            "canonical view ordinal is outside persisted Registry history")
    return CanonicalView(chosen)

def firing_view(
        store, *, firing_version_id: TypedId | str,
        invocation_version_id: TypedId | str) -> FiringView:
    """Capture one exact provisional firing and its current member set."""

    firing_key = str(firing_version_id)
    invocation_key = str(invocation_version_id)
    with store.connect() as db:
        root = db.execute(
            "SELECT * FROM firing_publications "
            "WHERE firing_version_id=?",
            (firing_key,),
        ).fetchone()
        maximum_row = db.execute(
            "SELECT COALESCE(MAX(ordinal),0) AS maximum FROM events"
        ).fetchone()
        members = db.execute(
            "SELECT member_kind,member_identity "
            "FROM firing_temporary_members WHERE firing_version_id=?",
            (firing_key,),
        ).fetchall()
    if (root is None or root["state"] != "PROVISIONAL"
            or str(root["invocation_version_id"]) != invocation_key):
        raise RegistryConflict(
            "firing view requires one matching provisional invocation root")
    return FiringView(
        canonical=CanonicalView(int(maximum_row["maximum"])),
        firing_version_id=firing_key,
        invocation_version_id=invocation_key,
        temporary_members=frozenset(
            (str(row["member_kind"]), str(row["member_identity"]))
            for row in members),
    )

def provisional_observation_view(
        store, *, firing_version_id: TypedId | str,
        invocation_version_id: TypedId | str,
        ) -> ProvisionalObservationView:
    """Capture diagnostics for one provisional root without authority."""

    firing_key = str(firing_version_id)
    invocation_key = str(invocation_version_id)
    with store.connect() as db:
        root = db.execute(
            "SELECT * FROM firing_publications "
            "WHERE firing_version_id=?",
            (firing_key,),
        ).fetchone()
        members = db.execute(
            "SELECT member_kind,member_identity "
            "FROM firing_temporary_members WHERE firing_version_id=?",
            (firing_key,),
        ).fetchall()
    if (root is None or root["state"] != "PROVISIONAL"
            or str(root["invocation_version_id"]) != invocation_key):
        raise RegistryConflict(
            "provisional observation requires one matching active root")
    return ProvisionalObservationView(
        firing_version_id=firing_key,
        invocation_version_id=invocation_key,
        opened_transaction_id=str(root["opened_transaction_id"]),
        temporary_members=frozenset(
            (str(row["member_kind"]), str(row["member_identity"]))
            for row in members),
    )

def _authority_ordinal(view: RegistryAuthorityView) -> int:
    if isinstance(view, CanonicalView):
        return view.through_ordinal
    if isinstance(view, FiringView):
        return view.canonical.through_ordinal
    raise TypeError(
        "Registry authority queries reject diagnostics-only observations")

def _canonical_member_sql(
        *, member_kind: str, member_identity_sql: str,
        published_event_sql: str) -> str:
    """SQL predicate for canonical visibility at one supplied ordinal."""

    return (
        f"{published_event_sql}<=? AND NOT EXISTS ("
        "SELECT 1 FROM firing_temporary_members m "
        "JOIN firing_publications p "
        "ON p.firing_version_id=m.firing_version_id "
        f"WHERE m.member_kind='{member_kind}' "
        f"AND m.member_identity={member_identity_sql} AND ("
        "p.state!='PUBLISHED' OR p.published_transaction_id IS NULL OR "
        "NOT EXISTS (SELECT 1 FROM events authority_event "
        "WHERE authority_event.transaction_id=p.published_transaction_id "
        "AND authority_event.event_type='transaction_committed/v1' "
        "AND authority_event.ordinal<=?)) AND NOT "
        + _task_model_call_cap_handoff_member_authority_sql(
            member_alias="m", publication_alias="p",
            through_ordinal_sql="?") + ")"
    )

def canonical_object_row(
        store, version_id: TypedId, *,
        through_ordinal: int | None = None) -> sqlite3.Row | None:
    """Read an object only when canonical at the requested history head."""

    upper = store.max_ordinal() if through_ordinal is None else through_ordinal
    predicate = store._canonical_member_sql(
        member_kind="object", member_identity_sql="o.version_id",
        published_event_sql="published_event.ordinal")
    with store.connect() as db:
        return db.execute(
            "SELECT o.* FROM objects o JOIN events published_event "
            "ON published_event.event_id=o.published_event_id "
            f"WHERE o.version_id=? AND {predicate}",
            (str(version_id), upper, upper, upper, upper),
        ).fetchone()

def object_row_for_view(
        store, view: RegistryAuthorityView,
        version_id: TypedId | str) -> sqlite3.Row | None:
    """Resolve one object through only the supplied authority capability."""

    upper = store._authority_ordinal(view)
    canonical = store.canonical_object_row(
        TypedId.parse(str(version_id)), through_ordinal=upper)
    if canonical is not None or isinstance(view, CanonicalView):
        return canonical
    if ("object", str(version_id)) not in view.temporary_members:
        return None
    with store.connect() as db:
        return db.execute(
            "SELECT * FROM objects WHERE version_id=?",
            (str(version_id),),
        ).fetchone()

def canonical_object_publication_ordinal(
        store, version_id: TypedId, *,
        through_ordinal: int | None = None) -> int | None:
    """Return the head ordinal at which one object became authoritative."""

    upper = store.max_ordinal() if through_ordinal is None else through_ordinal
    row = store.canonical_object_row(version_id, through_ordinal=upper)
    if row is None:
        return None
    with store.connect() as db:
        publication = db.execute(
            "SELECT p.state,p.published_transaction_id FROM "
            "firing_temporary_members m JOIN firing_publications p "
            "ON p.firing_version_id=m.firing_version_id "
            "WHERE m.member_kind='object' AND m.member_identity=?",
            (str(version_id),),
        ).fetchone()
        if publication is None:
            event = db.execute(
                "SELECT ordinal FROM events WHERE event_id=?",
                (str(row["published_event_id"]),),
            ).fetchone()
            return int(event["ordinal"])
        if publication["state"] == "PROVISIONAL":
            return _task_model_call_cap_handoff_member_publication_ordinal(
                db, member_kind="object", member_identity=str(version_id),
                through_ordinal=upper)
        authority = db.execute(
            "SELECT ordinal FROM events WHERE transaction_id=? "
            "AND event_type='transaction_committed/v1' AND ordinal<=?",
            (str(publication["published_transaction_id"]), upper),
        ).fetchone()
    return (int(authority["ordinal"])
            if authority is not None and authority["ordinal"] is not None
            else None)

def canonical_object_rows(
        store, *, through_ordinal: int | None = None,
        object_type: str | None = None) -> tuple[sqlite3.Row, ...]:
    """Return objects canonical at the requested history head."""

    upper = store.max_ordinal() if through_ordinal is None else through_ordinal
    predicate = store._canonical_member_sql(
        member_kind="object", member_identity_sql="o.version_id",
        published_event_sql="published_event.ordinal")
    type_clause = "" if object_type is None else "AND o.object_type=? "
    parameters: list[Any] = [upper, upper, upper, upper]
    if object_type is not None:
        parameters.append(object_type)
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT o.* FROM objects o JOIN events published_event "
            "ON published_event.event_id=o.published_event_id "
            f"WHERE {predicate} {type_clause}ORDER BY o.rowid",
            tuple(parameters),
        ).fetchall())

def canonical_relation_rows(
        store, *, through_ordinal: int | None = None,
        version_id: TypedId | str | None = None) -> tuple[sqlite3.Row, ...]:
    """Return relations canonical at the requested history head."""

    upper = store.max_ordinal() if through_ordinal is None else through_ordinal
    predicate = store._canonical_member_sql(
        member_kind="relation", member_identity_sql="r.relation_id",
        published_event_sql="published_event.ordinal")
    endpoint_clause = "" if version_id is None else (
        "AND (json_extract(r.source_json,'$.version_id')=? OR "
        "json_extract(r.target_json,'$.version_id')=?) ")
    parameters: list[Any] = [upper, upper, upper, upper]
    if version_id is not None:
        parameters.extend((str(version_id), str(version_id)))
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT r.* FROM relations r JOIN events published_event "
            "ON published_event.event_id=r.published_event_id "
            f"WHERE {predicate} {endpoint_clause}ORDER BY r.rowid",
            tuple(parameters),
        ).fetchall())

def relation_rows_for_view(
        store, view: RegistryAuthorityView, *,
        version_id: TypedId | str | None = None,
        relation_type: str | None = None,
        endpoint: str = "either") -> tuple[sqlite3.Row, ...]:
    """Resolve canonical plus exact-firing relations through one view."""

    if endpoint not in {"source", "target", "either"}:
        raise ValueError("relation endpoint must be source, target, or either")
    upper = store._authority_ordinal(view)
    predicate = store._canonical_member_sql(
        member_kind="relation", member_identity_sql="r.relation_id",
        published_event_sql="published_event.ordinal")
    visibility = predicate
    parameters: list[Any] = [upper, upper, upper, upper]
    if isinstance(view, FiringView):
        temporary_ids = tuple(sorted(
            identity for kind, identity in view.temporary_members
            if kind == "relation"))
        if temporary_ids:
            placeholders = ",".join("?" for _value in temporary_ids)
            visibility = f"(({predicate}) OR r.relation_id IN ({placeholders}))"
            parameters.extend(temporary_ids)
    clauses = [visibility]
    if relation_type is not None:
        clauses.append("r.relation_type=?")
        parameters.append(relation_type)
    if version_id is not None:
        endpoint_clauses = {
            "source": "json_extract(r.source_json,'$.version_id')=?",
            "target": "json_extract(r.target_json,'$.version_id')=?",
            "either": (
                "(json_extract(r.source_json,'$.version_id')=? OR "
                "json_extract(r.target_json,'$.version_id')=?)"),
        }
        clauses.append(endpoint_clauses[endpoint])
        parameters.append(str(version_id))
        if endpoint == "either":
            parameters.append(str(version_id))
    with store.connect() as db:
        rows = tuple(db.execute(
            "SELECT r.* FROM relations r JOIN events published_event "
            "ON published_event.event_id=r.published_event_id WHERE "
            + " AND ".join(clauses) + " ORDER BY r.rowid",
            tuple(parameters),
        ).fetchall())
    if isinstance(view, FiringView):
        for row in rows:
            try:
                endpoints = (
                    json.loads(str(row["source_json"])),
                    json.loads(str(row["target_json"])),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RegistryCorruptError(
                    "visible relation has malformed exact endpoints") from exc
            for value in endpoints:
                candidate = value.get("version_id") \
                    if isinstance(value, Mapping) else None
                if (candidate is not None
                        and store.object_row_for_view(view, candidate) is None):
                    raise RegistryCorruptError(
                        "firing relation crosses its exact read authority")
    return rows

def events_for_view(
        store, view: RegistryAuthorityView, *,
        event_types: tuple[str, ...] = (),
        aggregate_id: str | None = None,
        idempotency_key: str | None = None,
        net_instance_id: TypedId | str | None = None,
        producer_invocation_id: TypedId | str | None = None,
        ) -> tuple[EventEnvelope, ...]:
    """Resolve events through canonical or one frozen exact-firing view."""

    upper = store._authority_ordinal(view)
    events = list(store.canonical_events(through_ordinal=upper))
    if isinstance(view, FiringView):
        temporary_ids = tuple(sorted(
            identity for kind, identity in view.temporary_members
            if kind == "event"))
        if temporary_ids:
            placeholders = ",".join("?" for _value in temporary_ids)
            with store.connect() as db:
                rows = db.execute(
                    "SELECT * FROM events WHERE event_id IN "
                    f"({placeholders}) ORDER BY ordinal",
                    temporary_ids,
                ).fetchall()
            by_id = {str(event.event_id): event for event in events}
            by_id.update({
                str(row["event_id"]): store._row_to_envelope(row)
                for row in rows})
            events = list(by_id.values())
    return tuple(sorted((
        event for event in events
        if (not event_types or event.event_type in event_types)
        and (aggregate_id is None or event.aggregate_id == aggregate_id)
        and (idempotency_key is None
             or event.idempotency_key == idempotency_key)
        and (net_instance_id is None
             or event.net_instance_id is not None
             and str(event.net_instance_id) == str(net_instance_id))
        and (producer_invocation_id is None
             or event.producer_invocation_id is not None
             and str(event.producer_invocation_id)
             == str(producer_invocation_id))
    ), key=lambda event: event.ordinal))

def provisional_firing_inventory(
        store, firing_version_id: TypedId | str,
) -> tuple[sqlite3.Row, ...]:
    """Explicit monitor/cap view of one firing's durable temporary area."""

    with store.connect() as db:
        root = db.execute(
            "SELECT state FROM firing_publications WHERE firing_version_id=?",
            (str(firing_version_id),),
        ).fetchone()
        if root is None:
            raise RegistryCorruptError(
                "provisional firing inventory has no publication root")
        return tuple(db.execute(
            "SELECT * FROM firing_temporary_members "
            "WHERE firing_version_id=? ORDER BY transaction_id,"
            "member_kind,member_identity",
            (str(firing_version_id),),
        ).fetchall())

def provisional_firing_object_rows(
        store, firing_version_id: TypedId | str,
) -> tuple[sqlite3.Row, ...]:
    """Return exact object locations for explicit firing observability."""

    with store.connect() as db:
        root = db.execute(
            "SELECT state FROM firing_publications WHERE firing_version_id=?",
            (str(firing_version_id),),
        ).fetchone()
        if root is None or root["state"] != "PROVISIONAL":
            raise RegistryConflict(
                "provisional object observation requires an active firing")
        return tuple(db.execute(
            "SELECT o.* FROM firing_temporary_members m JOIN objects o "
            "ON o.version_id=m.member_identity "
            "WHERE m.firing_version_id=? AND m.member_kind='object' "
            "ORDER BY o.rowid",
            (str(firing_version_id),),
        ).fetchall())

def provisional_firing_events(
        store, firing_version_id: TypedId | str,
) -> tuple[EventEnvelope, ...]:
    """Return exact provisional facts for monitor/cap-critic use only."""

    with store.connect() as db:
        root = db.execute(
            "SELECT state FROM firing_publications WHERE firing_version_id=?",
            (str(firing_version_id),),
        ).fetchone()
        if root is None or root["state"] != "PROVISIONAL":
            raise RegistryConflict(
                "provisional event observation requires an active firing")
        rows = db.execute(
            "SELECT e.* FROM firing_temporary_members m JOIN events e "
            "ON e.event_id=m.member_identity "
            "WHERE m.firing_version_id=? AND m.member_kind='event' "
            "ORDER BY e.ordinal",
            (str(firing_version_id),),
        ).fetchall()
    return tuple(store._row_to_envelope(row) for row in rows)
