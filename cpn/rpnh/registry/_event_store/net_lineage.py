"""Exact registered-net, adoption, and checkpoint lineage closure."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any, Mapping

from ..event_store import RegistryCorruptError, _CANONICAL_EVENT_SQL, _exact_ref_payload
from ..identities import TypedId
from ..models import VersionRef
from ..schema_catalog import SchemaCatalog, canonical_json

if TYPE_CHECKING:
    from ..event_store import EventStore

def _version_ref_from_payload(value: Mapping[str, Any]) -> VersionRef:
    return VersionRef(
        str(value["entity_type"]),
        TypedId.parse(str(value["logical_id"])),
        TypedId.parse(str(value["version_id"])),
    )

def _exact_object_metadata(event_store: "EventStore", ref: VersionRef,
                           *, expected_type: str | None = None,
                           db: sqlite3.Connection | None = None,
                           rows: Mapping[str, sqlite3.Row] | None = None,
                           ) -> Mapping[str, Any]:
    if expected_type is not None and ref.entity_type != expected_type:
        raise RegistryCorruptError(
            f"exact reference type mismatch: {ref.entity_type} != {expected_type}")
    row = (rows.get(str(ref.version_id)) if rows is not None else
           event_store.object_row(ref.version_id) if db is None else
           db.execute(
               "SELECT * FROM objects WHERE version_id=?",
               (str(ref.version_id),),
           ).fetchone())
    if (row is None or row["object_type"] != ref.entity_type
            or row["logical_id"] != str(ref.entity_id)):
        raise RegistryCorruptError(
            f"exact registered object is unavailable: {ref.version_id}")
    try:
        return json.loads(row["metadata_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RegistryCorruptError(
            f"exact registered object metadata is malformed: {ref.version_id}") from exc

def _canonical_ref_array(value: Any, *, label: str) -> tuple[VersionRef, ...]:
    if not isinstance(value, list):
        raise RegistryCorruptError(f"{label} is not an exact-ref array")
    try:
        refs = tuple(_version_ref_from_payload(item) for item in value)
    except (KeyError, TypeError, ValueError) as exc:
        raise RegistryCorruptError(f"{label} contains a malformed exact ref") from exc
    payloads = [_exact_ref_payload(ref) for ref in refs]
    if payloads != sorted(payloads, key=canonical_json) or len(set(
            canonical_json(item) for item in payloads)) != len(payloads):
        raise RegistryCorruptError(f"{label} must be sorted and unique")
    return refs

def validate_registered_net_closure(event_store: "EventStore", catalog: SchemaCatalog,
                                    net_ref: VersionRef, *,
                                    _db: sqlite3.Connection | None = None,
                                    ) -> Mapping[str, Any]:
    """Validate one immutable model-designed workflow/net exact-ref closure."""
    key = str(net_ref.version_id)
    memo = event_store._net_closure_memo
    if key in memo:
        return memo[key]
    if _db is None:
        # One closure can contain hundreds of exact refs.  Keep the entire
        # read-only validation on one SQLite snapshot instead of opening a new
        # connection for every object.  The closure is still revalidated on
        # every authority check; this only changes I/O locality.
        with event_store.connect() as db:
            return validate_registered_net_closure(
                event_store, catalog, net_ref, _db=db)
    rows = {
        str(row["version_id"]): row
        for row in _db.execute("SELECT * FROM objects").fetchall()
    }
    net = _exact_object_metadata(
        event_store, net_ref, expected_type="net_instance/v1",
        db=_db, rows=rows)
    catalog.validate_instance("net_instance/v1", category="object", instance=net)
    if net.get("net_instance_ref") != _exact_ref_payload(net_ref):
        raise RegistryCorruptError("net self reference differs from its exact object key")
    try:
        root_ref = _version_ref_from_payload(net["team_design_root_ref"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RegistryCorruptError(
            "net lacks an exact workflow-design root ref") from exc
    root = _exact_object_metadata(
        event_store, root_ref, expected_type="team_design_root/v1",
        db=_db, rows=rows)
    catalog.validate_instance("team_design_root/v1", category="object", instance=root)
    if root.get("team_design_root_ref") != _exact_ref_payload(root_ref):
        raise RegistryCorruptError(
            "workflow-design root self ref is inconsistent")
    closure_fields = (
        "llm_macro_net_ref", "node_refs", "operation_binding_refs",
        "output_binding_refs",
    )
    for field in closure_fields:
        if net.get(field) != root.get(field):
            raise RegistryCorruptError(
                f"net and workflow-design root closure disagree at {field}")
    if root.get("task_round_ref") != net.get("task_round_ref"):
        raise RegistryCorruptError(
            "net and workflow-design root belong to different rounds")

    expected_types = {
        "node_refs": "node_declaration/v1",
        "operation_binding_refs": "operation_binding/v1",
        "output_binding_refs": "output_binding/v1",
    }
    closure_refs: dict[str, tuple[VersionRef, ...]] = {}
    closure_metadata: dict[str, dict[VersionRef, Mapping[str, Any]]] = {}
    for field, expected_type in expected_types.items():
        refs = _canonical_ref_array(net.get(field), label=f"net {field}")
        if any(ref.entity_type != expected_type for ref in refs):
            raise RegistryCorruptError(
                f"net {field} contains a non-{expected_type} exact ref")
        closure_refs[field] = refs
        closure_metadata[field] = {}
        for ref in refs:
            metadata = _exact_object_metadata(
                event_store, ref, expected_type=expected_type,
                db=_db, rows=rows)
            catalog.validate_instance(
                expected_type, category="object", instance=metadata)
            closure_metadata[field][ref] = metadata

    node_refs = frozenset(closure_refs["node_refs"])
    binding_refs = frozenset(closure_refs["operation_binding_refs"])
    output_refs = frozenset(closure_refs["output_binding_refs"])
    root_payload = _exact_ref_payload(root_ref)
    net_payload = _exact_ref_payload(net_ref)
    plan_payload = net.get("plan_ref")

    for node_ref, node in closure_metadata["node_refs"].items():
        if (node.get("node_ref") != _exact_ref_payload(node_ref)
                or node.get("team_design_root_ref") != root_payload
                or node.get("plan_ref") != plan_payload):
            raise RegistryCorruptError(
                "node self/root/plan identity is outside the registered net closure")
        try:
            producer_ref = _version_ref_from_payload(
                node["producer_operation_binding_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryCorruptError(
                "node lacks an exact producer operation binding") from exc
        if producer_ref not in binding_refs:
            raise RegistryCorruptError(
                "node producer operation binding is outside the registered net closure")
        offered_refs = frozenset(_canonical_ref_array(
            node.get("offered_output_binding_refs"),
            label="node offered_output_binding_refs"))
        if (any(ref.entity_type != "output_binding/v1" for ref in offered_refs)
                or not offered_refs.issubset(output_refs)):
            raise RegistryCorruptError(
                "node offered outputs are outside the registered net closure")
        if any(closure_metadata["output_binding_refs"][ref].get("node_ref")
               != _exact_ref_payload(node_ref) for ref in offered_refs):
            raise RegistryCorruptError(
                "node offered output binding belongs to another node")

    for binding_ref, binding in closure_metadata["operation_binding_refs"].items():
        if (binding.get("operation_binding_ref") != _exact_ref_payload(binding_ref)
                or binding.get("operation_binding_id") != str(binding_ref.entity_id)
                or binding.get("operation_binding_version_id")
                != str(binding_ref.version_id)
                or binding.get("team_design_root_ref") != root_payload):
            raise RegistryCorruptError(
                "operation binding self/root identity is outside the registered net closure")
        try:
            node_ref = _version_ref_from_payload(binding["node_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryCorruptError(
                "registered net operation binding lacks an exact node ref") from exc
        if node_ref not in node_refs:
            raise RegistryCorruptError(
                "operation binding node is outside the registered net closure")
        node = closure_metadata["node_refs"][node_ref]
        if node.get("producer_operation_binding_ref") != _exact_ref_payload(binding_ref):
            raise RegistryCorruptError(
                "node and operation binding producer identities disagree")
        declared_refs = frozenset(_canonical_ref_array(
            binding.get("output_binding_refs"),
            label="operation binding output_binding_refs"))
        if (any(ref.entity_type != "output_binding/v1" for ref in declared_refs)
                or not declared_refs.issubset(output_refs)):
            raise RegistryCorruptError(
                "operation binding outputs are outside the registered net closure")
        if any(closure_metadata["output_binding_refs"][ref].get("node_ref")
               != _exact_ref_payload(node_ref) for ref in declared_refs):
            raise RegistryCorruptError(
                "operation binding output belongs to another node")

    for output_ref, output in closure_metadata["output_binding_refs"].items():
        if (output.get("output_binding_id") != str(output_ref.entity_id)
                or output.get("output_binding_version_id") != str(output_ref.version_id)
                or output.get("team_design_root_ref") != root_payload
                or output.get("net_ref") != net_payload
                or output.get("task_round_ref") != net.get("task_round_ref")):
            raise RegistryCorruptError(
                "output binding self/root/net identity is outside the registered closure")
        try:
            output_node_ref = _version_ref_from_payload(output["node_ref"])
            action_ref = _version_ref_from_payload(output["opaque_action_ref"])
            place_ref = _version_ref_from_payload(output["place_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryCorruptError(
                "output binding contains a malformed exact ref") from exc
        if output_node_ref not in node_refs:
            raise RegistryCorruptError(
                "output binding node is outside the registered net closure")
        action = _exact_object_metadata(
            event_store, action_ref, expected_type="operation_spec/v1",
            db=_db, rows=rows)
        _exact_object_metadata(event_store, place_ref, db=_db, rows=rows)
        output_port_id = output.get("output_port_id")
        matching_ports = tuple(
            port for port in action.get("output_ports", ())
            if isinstance(port, Mapping)
            and port.get("port_id") == output_port_id)
        if (len(matching_ports) != 1
                or matching_ports[0].get("content_schema_ref")
                != output.get("content_schema_ref")
                or matching_ports[0].get("cardinality")
                != output.get("normal_output_cardinality")):
            raise RegistryCorruptError(
                "output binding differs from its exact declared output port")

    for field in ("llm_macro_net_ref",):
        try:
            ref = _version_ref_from_payload(net[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryCorruptError(f"net {field} is malformed") from exc
        _exact_object_metadata(event_store, ref, db=_db, rows=rows)
    from ..module_binding_authority import validate_module_bindings
    def exact_module_binding(value, expected):
        return _exact_object_metadata(event_store, _version_ref_from_payload(value),
                                      expected_type=expected, db=_db, rows=rows)
    validate_module_bindings(net, root, closure_metadata["output_binding_refs"],
                            closure_metadata["node_refs"], exact_module_binding, RegistryCorruptError)
    result = dict(net)
    memo[key] = result
    return result

def verified_adoption_lineage(
        event_store: "EventStore", catalog: SchemaCatalog,
        task_id: TypedId, *,
        _db: sqlite3.Connection | None = None,
) -> tuple[VersionRef, ...]:
    """Return the complete, integrity-checked task-control adoption lineage."""
    if _db is None:
        with event_store.connect() as db:
            return verified_adoption_lineage(
                event_store, catalog, task_id, _db=db)
    task_key = str(task_id)
    event_rows = _db.execute(
        "SELECT e.* FROM events e WHERE e.task_id=? "
        "AND e.event_type=? AND "
        f"{_CANONICAL_EVENT_SQL} ORDER BY e.task_control_sequence",
        (task_key, "net_adopted/v1"),
    ).fetchall()
    maximum_sequence = max(
        (int(row["task_control_sequence"] or 0)
         for row in event_rows), default=0)
    lineage: list[VersionRef] = []
    head: VersionRef | None = None
    events = sorted(
        (event_store._row_to_envelope(row) for row in event_rows),
        key=lambda event: (
            int(event.task_control_sequence)
            if event.task_control_sequence is not None else -1),
    )
    if not events:
        raise RegistryCorruptError("task has no adopted net")
    rows_by_event_id = {
        str(row["event_id"]): row for row in event_rows
    }
    for event in events:
        event_store._verified_persisted_event_record(
            _db, rows_by_event_id[str(event.event_id)])
        if event.task_control_sequence is None:
            raise RegistryCorruptError("net adoption is outside task control")
        try:
            candidate = _version_ref_from_payload(event.payload["net_instance_ref"])
            supersedes = (None if event.payload["supersedes_net_ref"] is None else
                          _version_ref_from_payload(event.payload["supersedes_net_ref"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryCorruptError("net adoption exact refs are malformed") from exc
        if supersedes != head or candidate == head:
            raise RegistryCorruptError("net adoption chain is forked or discontinuous")
        closure = validate_registered_net_closure(
            event_store, catalog, candidate, _db=_db)
        expected = {
            "net_instance_ref": _exact_ref_payload(candidate),
            "team_design_root_ref": closure["team_design_root_ref"],
            "llm_macro_net_ref": closure["llm_macro_net_ref"],
            "node_refs": closure["node_refs"],
            "operation_binding_refs": closure["operation_binding_refs"],
            "output_binding_refs": closure["output_binding_refs"],
            "supersedes_net_ref": _exact_ref_payload(supersedes),
        }
        growth_fields = {
            "growth_proposal_ref",
            "growth_producer_firing_ref",
            "growth_proposal_token_ref",
        }
        present_growth_fields = growth_fields.intersection(event.payload)
        if present_growth_fields:
            raise RegistryCorruptError("historical fixed growth is not current adoption authority")
        if present_growth_fields:
            if present_growth_fields != growth_fields:
                raise RegistryCorruptError(
                    "growth adoption provenance tuple is incomplete")
            try:
                proposal_ref = _version_ref_from_payload(
                    event.payload["growth_proposal_ref"])
                producer_firing_ref = _version_ref_from_payload(
                    event.payload["growth_producer_firing_ref"])
                proposal_token_ref = _version_ref_from_payload(
                    event.payload["growth_proposal_token_ref"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryCorruptError(
                    "growth adoption provenance refs are malformed") from exc
            proposal = _exact_object_metadata(
                event_store, proposal_ref,
                expected_type="resource_version/v1", db=_db)
            producer_firing = _exact_object_metadata(
                event_store, producer_firing_ref,
                expected_type="transition_firing/v1", db=_db)
            proposal_token = _exact_object_metadata(
                event_store, proposal_token_ref,
                expected_type="petri_token/v1", db=_db)
            provenance = proposal.get("reference_provenance")
            proposal_origin = proposal.get("origin")
            if (proposal_token.get("resource_ref") != {
                    "resource_id": str(proposal_ref.entity_id),
                    "resource_version_id": str(proposal_ref.version_id),
                }
                    or proposal_token.get("producer")
                    != producer_firing.get("transition_id")
                    or proposal_token.get("consumed_by") is not None
                    or not isinstance(provenance, Mapping)
                    or provenance.get("schema_version")
                    != "resource_reference_provenance/v1"
                    or provenance.get("producer_invocation_ref")
                    != proposal.get("producer_ref")
                    or provenance.get("operation_binding_ref")
                    != producer_firing.get("operation_binding_ref")
                    or not isinstance(proposal_origin, Mapping)
                    or proposal_origin.get("secondary_ref")
                    != producer_firing.get("activation_ref")):
                raise RegistryCorruptError(
                    "growth adoption provenance differs from its live proposal token")
            expected.update({
                "growth_proposal_ref": _exact_ref_payload(proposal_ref),
                "growth_producer_firing_ref": _exact_ref_payload(
                    producer_firing_ref),
                "growth_proposal_token_ref": _exact_ref_payload(
                    proposal_token_ref),
            })
        repair_fields = {
            "repair_kind",
            "repair_source_firing_ref",
            "repair_predecessor_checkpoint_ref",
            "repair_candidate_checkpoint_ref",
        }
        present_repair_fields = repair_fields.intersection(event.payload)
        if present_repair_fields:
            raise RegistryCorruptError("historical fixed repair is not current adoption authority")
        if present_repair_fields:
            if present_repair_fields != repair_fields or present_growth_fields:
                raise RegistryCorruptError(
                    "net adoption repair provenance tuple is incomplete")
            try:
                repair_source = _version_ref_from_payload(
                    event.payload["repair_source_firing_ref"])
                repair_predecessor = _version_ref_from_payload(
                    event.payload["repair_predecessor_checkpoint_ref"])
                repair_candidate = _version_ref_from_payload(
                    event.payload["repair_candidate_checkpoint_ref"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryCorruptError(
                    "net adoption repair provenance refs are malformed") from exc
            source_metadata = _exact_object_metadata(
                event_store, repair_source,
                expected_type="transition_firing/v1", db=_db)
            repair_checkpoint = _exact_object_metadata(
                event_store, repair_candidate,
                expected_type="marking_checkpoint/v1", db=_db)
            if (event.payload["repair_kind"] != "a2c_indicator_return"
                    or repair_source.entity_type != "transition_firing/v1"
                    or source_metadata.get("net_instance_ref")
                    != _exact_ref_payload(supersedes)
                    or repair_predecessor.entity_type
                    != "marking_checkpoint/v1"
                    or repair_candidate.entity_type
                    != "marking_checkpoint/v1"
                    or repair_checkpoint.get("marking_checkpoint_ref")
                    != _exact_ref_payload(repair_candidate)
                    or repair_checkpoint.get("net_instance_ref")
                    != _exact_ref_payload(candidate)
                    or repair_checkpoint.get("previous_checkpoint_ref")
                    != _exact_ref_payload(repair_predecessor)):
                raise RegistryCorruptError(
                    "net adoption repair provenance differs from its exact closure")
            expected.update({
                "repair_kind": "a2c_indicator_return",
                "repair_source_firing_ref": _exact_ref_payload(repair_source),
                "repair_predecessor_checkpoint_ref": (
                    _exact_ref_payload(repair_predecessor)),
                "repair_candidate_checkpoint_ref": (
                    _exact_ref_payload(repair_candidate)),
            })
        if "owner_command_ref" in event.payload:
            from ..owner_adoption import verified_owner_adoption_fields
            expected.update(verified_owner_adoption_fields(
                event_store, catalog, _db, event))
        if "operation_revision" in event.payload:
            from ..module_revision import verified_operation_revision_fields
            expected.update(verified_operation_revision_fields(event_store, catalog, _db, event))
        if supersedes is not None and not ({"owner_command_ref", "operation_revision"} & event.payload.keys()):
            raise RegistryCorruptError("current adoption requires an owner or registered operation witness")
        if dict(event.payload) != expected:
            raise RegistryCorruptError(
                "net adoption fact differs from the immutable registered closure")
        head = candidate
        lineage.append(candidate)
    result = tuple(lineage)
    event_store._adoption_lineage_memo[task_key] = (
        maximum_sequence, result)
    return result

def verified_adoption_head(event_store: "EventStore", catalog: SchemaCatalog,
                           task_id: TypedId, *,
                           _db: sqlite3.Connection | None = None,
                           ) -> VersionRef:
    """Return the head of the explicit task-control adoption chain."""

    return verified_adoption_lineage(
        event_store, catalog, task_id, _db=_db)[-1]

def _require_exact_checkpoint_identity(
        checkpoint: Mapping[str, Any], *, checkpoint_ref: VersionRef,
        net_ref: VersionRef, previous_checkpoint_ref: VersionRef | None,
) -> None:
    """Close the checkpoint self/net/predecessor identity tuple."""

    if (not isinstance(checkpoint_ref, VersionRef)
            or checkpoint_ref.entity_type != "marking_checkpoint/v1"
            or not isinstance(net_ref, VersionRef)
            or net_ref.entity_type != "net_instance/v1"
            or (previous_checkpoint_ref is not None
                and (not isinstance(previous_checkpoint_ref, VersionRef)
                     or previous_checkpoint_ref.entity_type
                     != "marking_checkpoint/v1"))
            or checkpoint.get("marking_checkpoint_ref")
            != _exact_ref_payload(checkpoint_ref)
            or checkpoint.get("net_instance_ref")
            != _exact_ref_payload(net_ref)
            or checkpoint.get("previous_checkpoint_ref")
            != _exact_ref_payload(previous_checkpoint_ref)):
        raise RegistryCorruptError(
            "checkpoint self, net, or predecessor exact identity differs")

def verified_checkpoint_head(event_store: "EventStore", catalog: SchemaCatalog,
                             task_id: TypedId, net_ref: VersionRef, *,
                             _db: sqlite3.Connection | None = None,
                             ) -> VersionRef:
    """Return one checkpoint lineage, including an exact structural bridge."""
    if _db is None:
        with event_store.connect() as db:
            return verified_checkpoint_head(
                event_store, catalog, task_id, net_ref, _db=db)
    head: VersionRef | None = None
    cache_key = (str(task_id), str(net_ref.version_id))
    event_rows = _db.execute(
        "SELECT e.* FROM events e WHERE e.task_id=? "
        "AND e.event_type=? AND e.net_instance_id=? AND "
        f"{_CANONICAL_EVENT_SQL} ORDER BY e.task_control_sequence",
        (str(task_id), "marking_checkpoint_committed/v1",
         str(net_ref.entity_id)),
    ).fetchall()
    maximum_sequence = max(
        (int(row["task_control_sequence"] or 0)
         for row in event_rows), default=0)
    net = validate_registered_net_closure(
        event_store, catalog, net_ref, _db=_db)
    events = sorted(
        (event_store._row_to_envelope(row) for row in event_rows),
        key=lambda event: (
            int(event.task_control_sequence)
            if event.task_control_sequence is not None else -1),
    )
    structural_rows = _db.execute(
        "SELECT e.* FROM events e WHERE e.task_id=? "
        "AND e.event_type='structural_growth_adopted/v1' AND "
        f"{_CANONICAL_EVENT_SQL} ORDER BY e.task_control_sequence",
        (str(task_id),),
    ).fetchall()
    structural_events = tuple(
        event_store._row_to_envelope(row) for row in structural_rows)
    repair_adoption_rows = _db.execute(
        "SELECT e.* FROM events e WHERE e.task_id=? "
        "AND e.event_type='net_adopted/v1' AND "
        f"{_CANONICAL_EVENT_SQL} ORDER BY e.task_control_sequence",
        (str(task_id),),
    ).fetchall()
    repair_adoption_events = tuple(
        event_store._row_to_envelope(row) for row in repair_adoption_rows)
    if not events and head is None:
        raise RegistryCorruptError("adopted net has no committed checkpoint")
    for event in events:
        if event.task_control_sequence is None:
            raise RegistryCorruptError("checkpoint commit is outside task control")
        try:
            checkpoint_ref = _version_ref_from_payload(event.payload["checkpoint_ref"])
            event_net_ref = _version_ref_from_payload(event.payload["net_instance_ref"])
            previous = (None if event.payload["previous_checkpoint_ref"] is None else
                        _version_ref_from_payload(event.payload["previous_checkpoint_ref"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryCorruptError("checkpoint commit exact refs are malformed") from exc
        structural_bridge = False
        if event_net_ref == net_ref and head is None and previous is not None:
            operation_bridges = tuple(candidate for candidate in repair_adoption_events
                if (candidate.payload.get("net_instance_ref") == _exact_ref_payload(net_ref)
                    and candidate.payload.get("operation_revision", {}).get("successor_checkpoint_ref") == _exact_ref_payload(checkpoint_ref)
                    and candidate.payload.get("operation_revision", {}).get("predecessor_checkpoint_ref") == _exact_ref_payload(previous)
                    and candidate.transaction_id == event.transaction_id))
            if len(operation_bridges) == 1:
                predecessor_net_ref = _version_ref_from_payload(operation_bridges[0].payload["supersedes_net_ref"])
                lineage = verified_adoption_lineage(event_store, catalog, task_id, _db=_db)
                positions = tuple(i for i, value in enumerate(lineage) if value == net_ref)
                structural_bridge = (len(positions) == 1 and positions[0] > 0
                    and lineage[positions[0] - 1] == predecessor_net_ref
                    and verified_checkpoint_head(event_store, catalog, task_id, predecessor_net_ref, _db=_db) == previous)
            owner_bridges = tuple(
                candidate_event for candidate_event in repair_adoption_events
                if (candidate_event.payload.get("owner_command_ref")
                    == event.payload.get("owner_command_ref")
                    and "owner_command_ref" in event.payload
                    and candidate_event.payload.get("owner_command_result_ref")
                    == event.payload.get("owner_command_result_ref")
                    and candidate_event.payload.get("net_instance_ref")
                    == _exact_ref_payload(net_ref)
                    and candidate_event.payload.get("owner_candidate_checkpoint_ref")
                    == _exact_ref_payload(checkpoint_ref)
                    and candidate_event.payload.get("owner_predecessor_checkpoint_ref")
                    == _exact_ref_payload(previous)
                    and candidate_event.transaction_id == event.transaction_id))
            if len(owner_bridges) == 1:
                predecessor_net_ref = _version_ref_from_payload(
                    owner_bridges[0].payload["supersedes_net_ref"])
                lineage = verified_adoption_lineage(event_store, catalog, task_id, _db=_db)
                positions = tuple(i for i, value in enumerate(lineage) if value == net_ref)
                structural_bridge = (len(positions) == 1 and positions[0] > 0
                    and lineage[positions[0] - 1] == predecessor_net_ref
                    and verified_checkpoint_head(event_store, catalog, task_id,
                        predecessor_net_ref, _db=_db) == previous)
            bridges = tuple(
                candidate for candidate in structural_events
                if (candidate.payload.get("candidate_net_ref")
                    == _exact_ref_payload(net_ref)
                    and candidate.payload.get(
                        "candidate_marking_checkpoint_ref")
                    == _exact_ref_payload(checkpoint_ref)
                    and candidate.payload.get(
                        "predecessor_marking_checkpoint_ref")
                    == _exact_ref_payload(previous)
                    and candidate.transaction_id == event.transaction_id))
            if len(bridges) == 1:
                predecessor_payload = bridges[0].payload.get(
                    "predecessor_net_ref")
                try:
                    predecessor_net_ref = _version_ref_from_payload(
                        predecessor_payload)
                except (TypeError, ValueError) as exc:
                    raise RegistryCorruptError(
                        "structural checkpoint predecessor net is malformed") from exc
                adoption_lineage = verified_adoption_lineage(
                    event_store, catalog, task_id, _db=_db)
                positions = tuple(
                    index for index, value in enumerate(adoption_lineage)
                    if value == net_ref)
                if (len(positions) == 1 and positions[0] > 0
                        and adoption_lineage[positions[0] - 1]
                        == predecessor_net_ref
                        and verified_checkpoint_head(
                            event_store, catalog, task_id,
                            predecessor_net_ref, _db=_db) == previous):
                    structural_bridge = True
            if not structural_bridge:
                bridges = tuple(
                    candidate_event for candidate_event in repair_adoption_events
                    if (candidate_event.payload.get("net_instance_ref")
                        == _exact_ref_payload(net_ref)
                        and candidate_event.payload.get(
                            "repair_candidate_checkpoint_ref")
                        == _exact_ref_payload(checkpoint_ref)
                        and candidate_event.payload.get(
                            "repair_predecessor_checkpoint_ref")
                        == _exact_ref_payload(previous)
                        and candidate_event.transaction_id
                        == event.transaction_id
                        and candidate_event.payload.get("repair_kind")
                        == "a2c_indicator_return"))
                if len(bridges) == 1:
                    predecessor_payload = bridges[0].payload.get(
                        "supersedes_net_ref")
                    try:
                        predecessor_net_ref = _version_ref_from_payload(
                            predecessor_payload)
                    except (TypeError, ValueError) as exc:
                        raise RegistryCorruptError(
                            "A2C repair checkpoint predecessor net is malformed") from exc
                    adoption_lineage = verified_adoption_lineage(
                        event_store, catalog, task_id, _db=_db)
                    positions = tuple(
                        index for index, value in enumerate(adoption_lineage)
                        if value == net_ref)
                    if (len(positions) == 1 and positions[0] > 0
                            and adoption_lineage[positions[0] - 1]
                            == predecessor_net_ref
                            and verified_checkpoint_head(
                                event_store, catalog, task_id,
                                predecessor_net_ref, _db=_db) == previous):
                        structural_bridge = True
        if (event_net_ref != net_ref
                or (previous != head and not structural_bridge)):
            raise RegistryCorruptError("checkpoint lineage is foreign, forked, or discontinuous")
        checkpoint = _exact_object_metadata(
            event_store, checkpoint_ref,
            expected_type="marking_checkpoint/v1", db=_db)
        catalog.validate_instance(
            "marking_checkpoint/v1", category="object", instance=checkpoint)
        _require_exact_checkpoint_identity(
            checkpoint, checkpoint_ref=checkpoint_ref, net_ref=net_ref,
            previous_checkpoint_ref=previous)
        expected = {
            "checkpoint_ref": _exact_ref_payload(checkpoint_ref),
            "net_instance_ref": _exact_ref_payload(net_ref),
            "team_design_root_ref": net["team_design_root_ref"],
            "previous_checkpoint_ref": _exact_ref_payload(previous),
            "settlement_delta_ref": checkpoint["settlement_delta_ref"],
            "transition_firing_refs": checkpoint["transition_firing_refs"],
            "workspace_revision_refs": checkpoint[
                "workspace_revision_refs"],
            "settled": True,
        }
        for field in ("owner_command_ref", "owner_command_result_ref"):
            if field in checkpoint or field in event.payload:
                expected[field] = checkpoint.get(field, [])
        repair_fields = (
            "checkpoint_repair_ref", "repair_base_checkpoint_ref")
        if any(field in checkpoint or field in event.payload
               for field in repair_fields):
            if not all(field in checkpoint and field in event.payload
                       for field in repair_fields):
                raise RegistryCorruptError(
                    "checkpoint repair provenance tuple is incomplete")
            try:
                repair_ref = _version_ref_from_payload(
                    checkpoint["checkpoint_repair_ref"])
                repair_base_ref = _version_ref_from_payload(
                    checkpoint["repair_base_checkpoint_ref"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryCorruptError(
                    "checkpoint repair provenance refs are malformed") from exc
            repair = _exact_object_metadata(
                event_store, repair_ref,
                expected_type="checkpoint_repair/v1", db=_db)
            if (repair.get("successor_checkpoint_ref")
                    != _exact_ref_payload(checkpoint_ref)
                    or repair.get("expected_checkpoint_ref")
                    != _exact_ref_payload(previous)
                    or repair.get("repair_base_checkpoint_ref")
                    != _exact_ref_payload(repair_base_ref)
                    or repair.get("net_instance_ref")
                    != _exact_ref_payload(net_ref)
                    or checkpoint.get("transition_firing_refs")):
                raise RegistryCorruptError(
                    "checkpoint repair differs from its immutable closure")
            committed = tuple(
                candidate for candidate in event_store.list_events_by_aggregate(
                    str(repair_ref.entity_id),
                    event_types=("checkpoint_repair_committed/v1",))
                if candidate.payload.get("checkpoint_repair_ref")
                == _exact_ref_payload(repair_ref))
            if (len(committed) != 1
                    or committed[0].payload.get("successor_checkpoint_ref")
                    != _exact_ref_payload(checkpoint_ref)):
                raise RegistryCorruptError(
                    "checkpoint repair lacks one exact committed event")
            expected.update({
                "checkpoint_repair_ref": _exact_ref_payload(repair_ref),
                "repair_base_checkpoint_ref": _exact_ref_payload(
                    repair_base_ref),
            })
        event_payload = dict(event.payload)
        settlement_event_id = event_payload.pop("settlement_event_id", None)
        if settlement_event_id is not None:
            settlement_row = _db.execute(
                "SELECT event_type,payload_json,transaction_id FROM events "
                "WHERE event_id=?",
                (str(settlement_event_id),),
            ).fetchone()
            settlement_payload = (
                json.loads(str(settlement_row["payload_json"]))
                if settlement_row is not None else None)
            if (settlement_row is None
                    or settlement_row["event_type"]
                    != "transition_firing_settled/v1"
                    or str(settlement_row["transaction_id"])
                    != str(event.transaction_id)
                    or not isinstance(settlement_payload, Mapping)
                    or settlement_payload.get("successor_checkpoint_ref")
                    != _exact_ref_payload(checkpoint_ref)):
                raise RegistryCorruptError(
                    "checkpoint commit settlement event link is foreign")
        if (event_payload != expected
                or checkpoint.get("team_design_root_ref")
                != net["team_design_root_ref"]
                or checkpoint.get("settled") is not True):
            raise RegistryCorruptError(
                "checkpoint commit differs from its immutable same-net object")
        head = checkpoint_ref
    assert head is not None
    event_store._checkpoint_head_memo[cache_key] = (
        maximum_sequence, head)
    return head
