"""Canonical, firing-local, and provisional EventStore authority views."""

from __future__ import annotations

import json
from contextlib import nullcontext
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
        store, firing_version_id: TypedId | str, *, _db=None,
) -> Mapping[str, Any]:
    """Return one firing's exact event and Petri closure in ordinal order."""
    firing_id = str(firing_version_id)
    with (store.connect() if _db is None else nullcontext(_db)) as db:
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


# Display-only activity observations. These values never produce FiringView or
# become inputs to execution, canonical marking or lifecycle reconstruction.
ACTIVITY_TYPES = ('firing_admitted/v1', 'transition_firing_started/v1',
                  'operation_execution_started/v1')
ACTIVITY_MAX_BYTES = 8 * 1024 * 1024
ACTIVITY_DESCRIPTOR_BYTES = 256 * 1024
ACTIVITY_TX_EVENTS = 2048
ACTIVITY_DEADLINE_SECONDS = 2.0


class ActivityStaleError(RuntimeError):
    pass


class ActivityCursorError(ValueError):
    pass


def activity_reference(value, kind):
    stems = {'native_run_identity': ('run', 'run_version'),
             'node_declaration': ('node', 'node_declaration_version')}
    stem = kind.split('/')[0]
    expected = stems.get(stem, (stem, stem + '_version'))
    if (type(value) is not dict or set(value) != {'entity_type', 'logical_id', 'version_id'}
            or value['entity_type'] != kind or any(type(v) is not str for v in value.values())
            or (TypedId.parse(value['logical_id']).kind, TypedId.parse(value['version_id']).kind) != expected):
        raise ValueError('activity requires exact typed references')
    return dict(value)


def _activity_head(db, reserve=None):
    # Control scalars are bounded too; an untrusted TEXT epoch cannot allocate
    # an arbitrary string before conversion to an integer.
    size = db.execute("SELECT LENGTH(CAST(value AS BLOB)) FROM registry_meta WHERE key='writer_epoch'").fetchone()
    if size is None or type(size[0]) is not int or not 1 <= size[0] <= 20:
        raise ValueError('activity writer epoch has invalid bounded storage')
    if reserve is not None:
        reserve(80 + size[0])
    head = int(db.execute('SELECT COALESCE(MAX(ordinal),0) FROM events').fetchone()[0])
    epoch = db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0]
    if type(epoch) is not str or not epoch.isascii() or not epoch.isdecimal():
        raise ValueError('activity writer epoch is not an unsigned integer')
    return head, int(epoch)


def _provisional_activity_root(root, invocation_version_id):
    """The existing provisional diagnostic root predicate, without member scan."""
    return (root is not None and root['state'] == 'PROVISIONAL'
            and str(root['invocation_version_id']) == invocation_version_id)



def _activity_descriptor_bytes(object_store, prepared):
    """Read only a validated exact descriptor, with a hard content read bound."""
    if (prepared.object_type not in ('transition_firing/v1', 'firing_admission/v1',
            'invocation/v1', 'operation_execution_lease/v1')
            or prepared.media_type != 'application/json'
            or type(prepared.size) is not int or not 0 <= prepared.size <= ACTIVITY_DESCRIPTOR_BYTES):
        raise ValueError('activity body is outside the descriptor allowlist or budget')
    object_store.validate_envelope(prepared)
    if prepared.storage_locator != object_store.locator_for_version(prepared.version_id):
        raise ValueError('activity descriptor locator is not exact')
    path = object_store.path_for_version(prepared.version_id)
    if path.stat().st_size != prepared.size:
        raise ValueError('activity descriptor file size differs before reading')
    with path.open('rb') as stream:
        body = stream.read(prepared.size + 1)
    if len(body) != prepared.size:
        raise ValueError('activity descriptor size changed during bounded read')
    return body


def firing_activity_page(store, *, catalog, object_store, scope, bindings,
                         expected_capture, limit, after=None):
    """One bounded metadata observation in BEGIN, followed by a fresh H/E guard.

    ``scope`` and ``bindings`` come from the selected checkpoint's verified
    closure. The caller still checks its source binding before and after this
    read. No business resource body, completion, result or settlement is read.
    """
    import time
    from .queries import firing_activity_event_rows, _activity_rows, ACTIVITY_EVENT_COLUMNS, ACTIVITY_OBJECT_COLUMNS
    from ..event_store import fact_event_envelope
    from ..models import PreparedObject
    if not store.read_only or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('activity requires a read-only store and bounded page')
    head, epoch = expected_capture
    deadline = time.monotonic() + ACTIVITY_DEADLINE_SECONDS
    def check_time():
        if time.monotonic() >= deadline:
            raise RuntimeError('activity read deadline exceeded')
    task = activity_reference(scope['task_ref'], 'task/v1')
    activity_reference(scope['run_ref'], 'native_run_identity/v1')
    net = activity_reference(scope['net_ref'], 'net_instance/v1')
    members = {b['transition_id']: b for b in bindings}
    if not members or sorted(members) != scope['transition_ids']:
        raise ValueError('activity subject differs from selected bindings')
    used_bytes, transactions, descriptors = 0, {}, {}
    def reserve(byte_count):
        nonlocal used_bytes
        if type(byte_count) is not int or byte_count < 0 or byte_count > ACTIVITY_MAX_BYTES - used_bytes:
            raise RuntimeError('activity verification byte budget exceeded')
        used_bytes += byte_count
        check_time()
    with store.connect() as db:
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute('BEGIN')
        try:
            if _activity_head(db, reserve) != (head, epoch):
                raise ActivityStaleError('activity observation changed')
            def read_rows(label, columns, source_sql, parameters=(), max_rows=1, max_cell_bytes=None):
                return _activity_rows(db, columns=columns, source_sql=source_sql, parameters=parameters,
                    reserve=reserve, max_rows=max_rows, label=label, max_cell_bytes=max_cell_bytes)
            def read_one(label, columns, source_sql, parameters=(), max_cell_bytes=None):
                values = read_rows(label, columns, source_sql, parameters, max_cell_bytes=max_cell_bytes)
                return values[0] if values else None
            def transaction(key):
                if key in transactions:
                    return transactions[key]
                tx = read_one('transaction', ('transaction_id','task_id','status','writer_epoch'),
                    'transactions WHERE transaction_id=?', (key,))
                out = read_one('outbox', ('task_id','writer_epoch','event_ids_json'),
                    'outbox WHERE transaction_id=?', (key,))
                rows = read_rows('transaction-events', ACTIVITY_EVENT_COLUMNS,
                    'events WHERE transaction_id=? ORDER BY ordinal', (key,), max_rows=ACTIVITY_TX_EVENTS)
                events = [store._row_to_envelope(r) for r in rows]
                terminals = [e for e in events if e.event_type in ('transaction_committed/v1', 'transaction_aborted/v1')]
                if len(terminals) != 1 or terminals[0].event_type != 'transaction_committed/v1':
                    raise ValueError('activity transaction has no unique commit')
                terminal = terminals[0]
                if (events[-1] != terminal or terminal.ordinal > head or tx is None or out is None
                        or tx['status'] != 'committed' or tx['task_id'] != task['logical_id']
                        or out['task_id'] != tx['task_id'] or str(terminal.task_id) != tx['task_id']
                        or int(tx['writer_epoch']) != int(out['writer_epoch'])
                        or int(tx['writer_epoch']) != terminal.writer_fencing_epoch
                        or json.loads(out['event_ids_json']) != [str(e.event_id) for e in events]):
                    raise ValueError('activity transaction terminal/outbox differs')
                for e in events:
                    if (str(e.transaction_id) != key or e.task_id != terminal.task_id
                            or e.writer_fencing_epoch != terminal.writer_fencing_epoch
                            or e.ordinal > terminal.ordinal):
                        raise ValueError('activity transaction envelope differs')
                    catalog.validate_fact_envelope(fact_event_envelope(e))
                    catalog.validate_event_payload(e.event_type, e.payload, criticality=e.criticality)
                transactions[key] = terminal, {str(e.event_id): e for e in events}
                return transactions[key]
            # The evidence head is a complete commit, not an arbitrary ordinal.
            last = read_one('head-transaction', ('transaction_id',), 'events WHERE ordinal=?', (head,))
            if last is None or transaction(last['transaction_id'])[0].ordinal != head:
                raise ValueError('activity head is not a complete commit boundary')
            def member(kind, identity, firing_id, txid):
                rows = read_rows('member', ('firing_version_id','transaction_id'),
                    'firing_temporary_members WHERE member_kind=? AND member_identity=?', (kind, identity), max_rows=2)
                if len(rows) != 1 or rows[0]['firing_version_id'] != firing_id or rows[0]['transaction_id'] != txid:
                    raise ValueError('activity member identity is ambiguous or inconsistent')
            def descriptor(ref, kind, root):
                ref = activity_reference(ref, kind)
                key = canonical_json(ref)
                if key in descriptors:
                    return descriptors[key]
                row = read_one('descriptor', ACTIVITY_OBJECT_COLUMNS, 'objects WHERE version_id=?',
                    (ref['version_id'],), max_cell_bytes=ACTIVITY_DESCRIPTOR_BYTES)
                if (row is None or row['object_type'] != kind or row['logical_id'] != ref['logical_id']
                        or not 0 <= row['size'] <= ACTIVITY_DESCRIPTOR_BYTES
                        or len(row['metadata_json'].encode()) > ACTIVITY_DESCRIPTOR_BYTES):
                    raise ValueError('activity descriptor identity or budget differs')
                meta = json.loads(row['metadata_json'])
                terminal, events = transaction(row['transaction_id'])
                pub = events.get(row['published_event_id'])
                if (pub is None or pub.event_type != 'object_version_published/v1'
                        or canonical_json(pub.payload.get('metadata')) != canonical_json(meta)
                        or (str(pub.producer_invocation_id) if pub.producer_invocation_id else None) != row['producer_invocation_id']
                        or any(pub.payload.get(k) != row[k] for k in ('object_type', 'logical_id', 'version_id', 'size', 'media_type', 'schema_ref', 'storage_locator'))):
                    raise ValueError('activity descriptor publication differs')
                member('object', ref['version_id'], root['firing_version_id'], row['transaction_id'])
                member('transaction', row['transaction_id'], root['firing_version_id'], row['transaction_id'])
                catalog.validate_instance(kind, category='object', instance=meta)
                if meta.get(kind.split('/')[0] + '_ref') != ref:
                    raise ValueError('activity descriptor self identity differs')
                prepared = PreparedObject(object_type=kind, logical_id=TypedId.parse(ref['logical_id']),
                    version_id=TypedId.parse(ref['version_id']), size=row['size'], media_type=row['media_type'],
                    schema_ref=row['schema_ref'], storage_locator=row['storage_locator'], metadata=meta,
                    producer_invocation_id=TypedId.parse(row['producer_invocation_id']) if row['producer_invocation_id'] else None)
                object_store.validate_envelope(prepared)
                reserve(prepared.size + 1)
                body = _activity_descriptor_bytes(object_store, prepared)
                if len(body) != row['size'] or canonical_json(json.loads(body)) != canonical_json(meta):
                    raise ValueError('activity registered descriptor differs')
                if root['state'] == 'PUBLISHED':
                    predicate = store._canonical_member_sql(member_kind='object', member_identity_sql='o.version_id', published_event_sql='e.ordinal')
                    ok = db.execute('SELECT 1 FROM objects o JOIN events e ON e.event_id=o.published_event_id WHERE o.version_id=? AND ' + predicate,
                                    (ref['version_id'], head, head, head, head)).fetchone()
                    if ok is None:
                        raise ValueError('published activity descriptor is not canonical')
                descriptors[key] = meta
                return meta
            args = dict(task_id=task['logical_id'], net_ref=net, transition_ids=scope['transition_ids'], event_types=ACTIVITY_TYPES, head=head, reserve=reserve)
            # A cursor is only a position; independently prove its event belongs
            # to this query in the same snapshot before reading its successor.
            if after is not None:
                prior = firing_activity_event_rows(db, **args, after=(after[0], ''), limit=1)
                if len(prior) != 1 or (prior[0]['ordinal'], prior[0]['event_id']) != tuple(after):
                    raise ActivityCursorError('activity cursor is outside the exact query')
            rows = firing_activity_event_rows(db, **args, after=after or (0, ''), limit=limit + 1)
            has_more = len(rows) > limit
            records, firings, verified = [], {}, {}
            for row in rows[:limit]:
                e = store._row_to_envelope(row)
                p = e.payload
                ref = activity_reference(p['transition_firing_ref'], 'transition_firing/v1')
                fid = ref['version_id']
                if row['activity_firing_version_id'] != fid:
                    raise ValueError('activity event and firing member differ')
                if fid not in verified:
                    root = read_one('publication', ('firing_version_id','firing_logical_id',
                        'invocation_version_id','invocation_logical_id','net_version_id',
                        'operation_binding_version_id','admission_checkpoint_version_id','state',
                        'opened_transaction_id','published_transaction_id'),
                        'firing_publications WHERE firing_version_id=?', (fid,))
                    if root is None or root['state'] not in ('PROVISIONAL', 'PUBLISHED'):
                        raise ValueError('activity publication root unavailable')
                    invocation_ref = activity_reference(p['invocation_ref'], 'invocation/v1')
                    if (root['firing_logical_id'] != ref['logical_id']
                            or root['invocation_logical_id'] != invocation_ref['logical_id']
                            or root['invocation_version_id'] != invocation_ref['version_id']):
                        raise ValueError('activity publication identity differs')
                    transaction(root['opened_transaction_id'])
                    member('transaction', root['opened_transaction_id'], fid, root['opened_transaction_id'])
                    if root['state'] == 'PROVISIONAL':
                        if not _provisional_activity_root(root, invocation_ref['version_id']) or root['published_transaction_id'] is not None:
                            raise ValueError('activity diagnostic root differs')
                        visible = None
                    else:
                        if not root['published_transaction_id']:
                            raise ValueError('activity publication has no commit')
                        visible = transaction(root['published_transaction_id'])[0].ordinal
                        member('transaction', root['published_transaction_id'], fid, root['published_transaction_id'])
                    f = descriptor(ref, 'transition_firing/v1', root)
                    a = descriptor(f['firing_admission_ref'], 'firing_admission/v1', root)
                    i = descriptor(invocation_ref, 'invocation/v1', root)
                    lease = descriptor(i['operation_execution_lease_ref'], 'operation_execution_lease/v1', root)
                    binding = members.get(f['transition_id'])
                    if (binding is None or f['task_ref'] != task or i['task_ref'] != task
                            or f['net_instance_ref'] != net or i['net_instance_ref'] != net
                            or f['node_ref'] != binding['node_ref'] or i['own_node_ref'] != binding['node_ref']
                            or f['operation_binding_ref'] != binding['operation_binding_ref'] or i['operation_binding_ref'] != binding['operation_binding_ref']
                            or f['principal_ref'] != binding['principal_ref'] or i['principal_ref'] != binding['principal_ref']
                            or i['authority_decision_ref'] != binding['authority_decision_ref']
                            or i['team_design_root_ref'] != scope['team_design_root_ref']
                            or i['task_branch_ref'] != scope['task_branch_ref'] or f['task_branch_ref'] != scope['task_branch_ref']
                            or f['task_round_ref'] != scope['task_round_ref'] or i['task_round_ref'] != scope['task_round_ref']
                            or i['own_transition_firing_ref'] != ref or a['transition_firing_ref'] != ref
                            or a['invocation_ref'] != invocation_ref or lease['invocation_ref'] != invocation_ref
                            or a['operation_execution_lease_ref'] != i['operation_execution_lease_ref']
                            or f['admission_marking_checkpoint_ref'] != a['admission_marking_checkpoint_ref']
                            or i['admission_marking_checkpoint_ref'] != a['admission_marking_checkpoint_ref']
                            or root['admission_checkpoint_version_id'] != a['admission_marking_checkpoint_ref']['version_id']
                            or root['net_version_id'] != net['version_id']
                            or root['operation_binding_version_id'] != binding['operation_binding_ref']['version_id']
                            or any(f[k] != a[k] for k in ('logical_tau', 'claim_marking_delta_ref'))):
                        raise ValueError('activity firing/admission/invocation/binding differs')
                    activity_reference(f['node_ref'], 'node_declaration/v1')
                    activity_reference(f['operation_binding_ref'], 'operation_binding/v1')
                    activity_reference(f['admission_marking_checkpoint_ref'], 'marking_checkpoint/v1')
                    verified[fid] = root, f, a, i, binding
                    firings[fid] = {'firing_ref': ref, 'node_ref': f['node_ref'], 'transition_id': f['transition_id'],
                        'attempt_index': f['attempt_index'], 'admission_checkpoint_ref': f['admission_marking_checkpoint_ref'],
                        'invocation_ref': invocation_ref, 'publication_class_at_evidence': root['state'],
                        'publication_visible_position': visible,
                        'business_outcome': 'not_provided', 'completion': 'not_provided', 'result': 'not_provided',
                        'successor_checkpoint': 'not_provided', 'delta': 'not_provided'}
                root, f, a, i, binding = verified[fid]
                if (ref != f['transition_firing_ref']
                        or p['invocation_ref'] != i['invocation_ref'] or p['operation_execution_lease_ref'] != i['operation_execution_lease_ref']
                        or str(e.task_id) != task['logical_id'] or str(e.net_instance_id) != net['logical_id']
                        or str(e.task_round_id) != f['task_round_ref']['logical_id']
                        or str(e.producer_invocation_id) != i['invocation_ref']['logical_id']
                        or e.branch_id != scope['branch_id']):
                    raise ValueError('activity event exact identity differs')
                if e.event_type in ACTIVITY_TYPES[:2]:
                    if (str(e.transaction_id) != root['opened_transaction_id'] or p['firing_admission_ref'] != f['firing_admission_ref']
                            or any(p[k] != a[k] for k in ('logical_tau', 'claim_marking_delta_ref'))):
                        raise ValueError('activity admission/start witness differs')
                elif (p['operation_binding_ref'] != binding['operation_binding_ref']
                      or p['executable_transition_binding_ref'] != binding['executable_binding_ref']
                      or any(p[k] != binding[k] for k in ('operation_spec_ref', 'principal_ref', 'authority_decision_ref'))
                      or p['agent_ref'] != f['agent_ref'] or p['agent_ref'] != i['agent_ref']):
                    raise ValueError('activity operation-start witness differs')
                member('event', str(e.event_id), fid, str(e.transaction_id))
                member('transaction', str(e.transaction_id), fid, str(e.transaction_id))
                commit = transaction(str(e.transaction_id))[0].ordinal
                if not e.ordinal <= commit <= head:
                    raise ValueError('activity record/commit positions differ')
                if root['state'] == 'PUBLISHED':
                    predicate = store._canonical_member_sql(member_kind='event', member_identity_sql='e.event_id', published_event_sql='e.ordinal')
                    if db.execute('SELECT 1 FROM events e WHERE e.event_id=? AND ' + predicate,
                                  (str(e.event_id), head, head, head, head)).fetchone() is None:
                        raise ValueError('published activity event is not canonical')
                records.append({'firing_ref': ref, 'event_id': str(e.event_id), 'event_type': e.event_type,
                    'transaction_id': str(e.transaction_id), 'recorded_ordinal': e.ordinal,
                    'recorded_at': e.recorded_at, 'transaction_commit_ordinal': commit})
            check_time()
        finally:
            db.execute('ROLLBACK')
            db.set_progress_handler(None, 0)
    with store.connect() as fresh:
        fresh.execute('BEGIN')
        try:
            if _activity_head(fresh, reserve) != (head, epoch):
                raise ActivityStaleError('activity observation changed')
        finally:
            fresh.execute('ROLLBACK')
    return {'firings': list(firings.values()), 'records': records, 'has_more': has_more,
            'last_key': [records[-1]['recorded_ordinal'], records[-1]['event_id']] if records else after}
