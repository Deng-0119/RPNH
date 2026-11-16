"""Explicit human read operations using original one-use resource delivery.

Preparing a delivery does not expose bytes or complete the registered operation.
Only the real HTTP adapter can supply a sealed successful transport observation.
No user JSON, memory writer, local-byte ACK or expired witness can mint it.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from types import MappingProxyType

from .registry._event_store.human_viewer import (
    READ_PROTOCOL, READ_EXECUTOR, READ_IDENTITY, READ_CONFIG_SCHEMA, READ_CONTRACTS,
    TRANSPORT_PROTOCOL, TRANSPORT_ADAPTER, TRANSPORT_ACTION, UNKNOWN_ACTION, READ_PURPOSE,
    read_definition, validate_read_baseline, require_read_delivery_pending, _transport_evidence,
)
from .registry.event_store import RegistryConflict
from .registry.errors import DeliveryOutcomeUnknown
from .registry.identities import new_id, TypedId
from .registry.models import VersionRef
from .registry.module_host_bindings import HostExecutionBinding, validate_host_execution_bindings
from .registry.operation_execution import verify_operation_execution
from .registry.operations import OperationExecutionResult, register_operation_outputs
from .registry.publication import _version_from_payload
from .registry.resources import PrepareResourceDelivery, AuthorizeResourceRelease, AcknowledgeResourceDelivery, ResourceVersionRef
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload


_ISSUER = object()
READ_CONFIG = {"$id": READ_CONFIG_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False, "required": ["outcome_id"],
    "properties": {"outcome_id": {"type": "string", "minLength": 1}}}


@dataclass(frozen=True, slots=True)
class _LiveRead:
    issuer: object
    owner: object
    kernel: object
    execution: object
    context: object
    session: object
    release: object
    binding_bytes: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class HumanViewerDelivery:
    """Opaque live handle. Its original witness remains the I/O authority."""
    _seal: object


@dataclass(frozen=True, slots=True)
class _TransportPermit:
    issuer: object
    store: object
    binding_bytes: bytes
    evidence_bytes: bytes
    outcome: str


def _live(delivery):
    if (type(delivery) is not HumanViewerDelivery or type(delivery._seal) is not _LiveRead
            or delivery._seal.issuer is not _ISSUER):
        raise RegistryConflict("HUMAN_VIEWER_REQUIRED: untrusted delivery handle")
    return delivery._seal


def transport_binding(delivery):
    """Detached exact identity for the trusted server's real transport proof."""
    return json.loads(_live(delivery).binding_bytes)


def delivery_media_type(delivery):
    return _live(delivery).media_type


def human_viewer_operation_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    # No automatic sending or retry: this executor can finish only after its
    # explicit transport step has left a genuine original boundary receipt.
    return gateway.complete_human_viewer_operation(execution)


def register_human_viewer_read(registration):
    registration.register_schema(READ_CONFIG_SCHEMA, READ_CONFIG)
    registration.register_executor(READ_EXECUTOR, human_viewer_operation_executor,
        identity=READ_IDENTITY, contracts=READ_CONTRACTS)


def human_viewer_host_bindings(*, core, plan, source_ref, bootstrap_ref, compiled):
    from .material_availability import availability_host_bindings
    from .material_execution import _material_host_publication
    result = dict(availability_host_bindings(core=core, plan=plan, source_ref=source_ref,
        bootstrap_ref=bootstrap_ref, compiled=compiled))
    definitions = {item.declaration.name: item for item in compiled.operations}
    for transition in compiled.symbolic.transitions:
        operation = definitions[transition.operation]
        declaration = operation.executor_declaration
        if declaration.get("contracts", {}).get("owner_material_reads") != READ_PROTOCOL:
            continue
        if (declaration.get("identity") != READ_IDENTITY or declaration.get("contracts") != READ_CONTRACTS
                or operation.declaration.executor != READ_EXECUTOR
                or operation.declaration.outputs or operation.declaration.tools):
            raise RegistryConflict("human viewer HOST requires its exact registered read operation")
        key = f"human-viewer-host:{plan.net_ref.version_id}:{transition.name}"
        saved = _material_host_publication(core, key, "agent/v1")
        ref = (VersionRef("agent/v1", saved.logical_id, saved.version_id) if saved is not None
               else VersionRef("agent/v1", new_id("agent"), new_id("agent_version")))
        metadata = {"agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id),
            "agent_ref": ref_payload(ref), "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
            "declared_agent_id": operation.declaration.name, "shadow_transition_id": transition.name}
        if saved is not None:
            if saved.metadata != metadata or core.object_store.read_registered(saved) != canonical_json(metadata):
                raise RegistryConflict("human viewer HOST replay changed its original static agent")
        else:
            core.publish_bytes(object_type="agent/v1", logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
                schema_ref="registry_v1/agent/v1", idempotency_key=key)
        result[transition.name] = HostExecutionBinding(agent_ref=ref)
    validate_host_execution_bindings(core, plan, result)
    return MappingProxyType(result)


def _original_for_key(core, key, object_type):
    from .registry._event_store.material_delivery_drain import _Cut
    from .registry._event_store.material_control import row_ref
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        rows = db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE t.task_id=? AND t.idempotency_key=? AND t.status='committed' AND o.object_type=?",
            (str(core.task_id), key, object_type)).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise RegistryConflict("human viewer original command has ambiguous evidence")
        return _Cut(core.event_store, db, core.task_id).obj(row_ref(rows[0]))


def prepare_human_viewer_operation(owner, execution):
    """Explicit actual read operation; no body is returned by preparation."""
    from .run import RunOwner
    from .registry._event_store.material_settlement import material_admission
    from .registry._event_store.collaboration_descriptors import exact_prepared
    from .registry.user_collaboration import mint_session
    if not isinstance(owner, RunOwner):
        raise TypeError("human viewer requires the original RunOwner")
    core = owner._core
    kernel, repository = owner.operation_repository()
    execution = verify_operation_execution(core, kernel, repository, execution)
    context = execution.operation.canonical.context
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        found = material_admission(core.event_store, db, core.task_id,
            ref_payload(context.own_transition_firing_ref), current=True)
        if found is None:
            raise RegistryConflict("human viewer requires its original accepted read command")
        admission, command = found
        _firing, _binding, _spec, definition, _config = read_definition(core.event_store, db, core.task_id, admission, command)
        if owner.registration.declaration("executor", READ_EXECUTOR) != definition.executor_declaration:
            raise RegistryConflict("human viewer executor differs from its frozen registration")
        require_read_delivery_pending(core.event_store, db, core.task_id, admission)
        target = validate_read_baseline(core.event_store, db, core.task_id, command["data"], current=True)
        original = exact_prepared(db, core.object_store, core.task_id, target)
    target_ref = _version_from_payload(target)
    resource = ResourceVersionRef(target_ref.entity_id, target_ref.version_id)
    key = "human-viewer:" + str(context.invocation_ref.version_id)
    # Restart cannot reconstruct a live broker or silently issue a replacement
    # witness. Existing original release recovery retains its unknown outcome.
    if _original_for_key(core, key + ":boundary:boundary-receipt", "delivery_boundary_receipt/v1") is not None:
        raise DeliveryOutcomeUnknown("human viewer has an original transport outcome; query that command")
    grant_id = core.capabilities.issue(target_invocation_id=context.invocation_ref.entity_id,
        operation_binding_ref=context.operation_binding_ref, resource_version_ids=(resource.resource_version_id,),
        revocation_condition="invocation closes", idempotency_key=key + ":grant")
    grant_row = core._logical_object(grant_id, "capability_grant/v1")
    grant = VersionRef("capability_grant/v1", grant_id, TypedId.parse(grant_row["version_id"], expected="grant"))
    core.capabilities.transition(grant_id, "activate", payload={}, idempotency_key=key + ":activate")
    core.capabilities.transition(grant_id, "allow", payload={}, idempotency_key=key + ":allow")
    prior = _original_for_key(core, key + ":prepare", "resource_delivery/v1")
    identity = new_id("resource_delivery") if prior is None else TypedId.parse(prior[0]["logical_id"], expected="resource_delivery")
    prepared = kernel.prepare_delivery(context, PrepareResourceDelivery(resource, grant, identity,
        key + ":prepare", "human_viewer", READ_PURPOSE))
    prior = _original_for_key(core, key + ":release", "resource_release_witness/v1")
    nonce = new_id("release_nonce") if prior is None else TypedId.parse(prior[1]["release_nonce"], expected="release_nonce")
    release = kernel.authorize_release(context, AuthorizeResourceRelease(prepared.delivery_ref,
        "human_viewer", nonce, key + ":release"))
    actor = command["data"]["user_action"]
    session = mint_session(core.event_store, _version_from_payload(actor["session_ref"]), actor["actor"])
    binding = {"protocol": TRANSPORT_PROTOCOL, "command_ref": admission["owner_command_ref"],
        "firing_ref": admission["transition_firing_ref"], "invocation_ref": admission["invocation_ref"],
        "actor": actor["actor"], "session_ref": actor["session_ref"], "resource_ref": target,
        "delivery_ref": ref_payload(release.delivery_ref), "witness_ref": ref_payload(release.witness_ref),
        "broker_channel_id": release.broker_channel_ref.channel_id, "byte_count": release.byte_count}
    return HumanViewerDelivery(_LiveRead(_ISSUER, owner, kernel, execution, context, session, release,
                                        canonical_json(binding), original.media_type))


def consume_human_viewer_delivery(delivery):
    live = _live(delivery)
    return live.kernel._consume_authorized_release(live.context, live.release)


def _validated_transport_permit(store, action):
    """Private final-write authority, separate from serialized observations."""
    permit = action.observer
    if (type(permit) is not _TransportPermit or permit.issuer is not _ISSUER or permit.store is not store
            or action.kind != (TRANSPORT_ACTION if permit.outcome == "acknowledged" else UNKNOWN_ACTION)
            or action.creation != permit.evidence_bytes):
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: receipt has no sealed adapter permit")
    return {"binding": json.loads(permit.binding_bytes), "evidence": json.loads(permit.evidence_bytes)}


def _existing_boundary(live):
    core = live.owner._core
    key = "human-viewer:" + str(live.context.invocation_ref.version_id)
    prior = _original_for_key(core, key + ":boundary:boundary-receipt", "delivery_boundary_receipt/v1")
    if prior is None:
        return None
    row, value = prior
    outcome = value["outcome"]
    if outcome not in {"acknowledged", "unknown"}:
        raise RegistryConflict("human viewer has an unsupported original transport outcome")
    binding = json.loads(live.binding_bytes)
    evidence = _transport_evidence(json.loads(value["consumer_evidence"]), binding, outcome=outcome)
    with core.event_store.connect() as db:
        tx = db.execute("SELECT command_json FROM transactions WHERE transaction_id=?", (row["transaction_id"],)).fetchone()
    action = json.loads(tx["command_json"]).get("user_collaboration")
    if (value["delivery_ref"] != binding["delivery_ref"] or value["witness_ref"] != binding["witness_ref"]
            or value["boundary"] != "human_viewer" or action != {
                "session_ref": binding["session_ref"], "actor": binding["actor"], "action_ref": None,
                "kind": TRANSPORT_ACTION if outcome == "acknowledged" else UNKNOWN_ACTION}):
        raise RegistryConflict("human viewer original receipt lacks its trusted action")
    return VersionRef("delivery_boundary_receipt/v1", TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"])), outcome


def _record_transport(live, evidence, outcome):
    from .registry.user_collaboration import mint_action, exact_command
    core = live.owner._core
    key = "human-viewer:" + str(live.context.invocation_ref.version_id)
    binding = json.loads(live.binding_bytes)
    _transport_evidence(evidence, binding, outcome=outcome)
    evidence_bytes = canonical_json(evidence)
    permit = _TransportPermit(_ISSUER, core.event_store, live.binding_bytes, evidence_bytes, outcome)
    action = mint_action(core.event_store, live.session,
        TRANSPORT_ACTION if outcome == "acknowledged" else UNKNOWN_ACTION, None,
        creation=evidence_bytes, observer=permit)
    with exact_command(core.event_store, key + ":boundary:boundary-receipt", action):
        boundary = live.kernel._record_boundary_receipt(live.context, live.release, outcome=outcome,
            positive_byte_count=live.release.byte_count if outcome == "acknowledged" else None,
            consumer_evidence=evidence_bytes.decode("utf-8"), idempotency_key=key + ":boundary")
    return boundary, outcome


def _acknowledge(live, original):
    boundary, outcome = original
    return live.kernel.acknowledge_delivery(live.context, AcknowledgeResourceDelivery(
        live.release.delivery_ref, boundary, outcome,
        "human-viewer:" + str(live.context.invocation_ref.version_id) + ":acknowledge"))


def record_human_viewer_transport(delivery, proof):
    """Record actual HTTP closure, or replay its exact already committed fact."""
    live = _live(delivery)
    original = _existing_boundary(live)
    if original is None:
        from cpn.frontend.human_viewer_transport import verify_human_viewer_transport_evidence
        evidence = verify_human_viewer_transport_evidence(proof, transport_binding(delivery))
        original = _record_transport(live, evidence, "acknowledged")
    if original[1] != "acknowledged":
        raise DeliveryOutcomeUnknown("human viewer original outcome is unknown; it cannot be upgraded or resent")
    return _acknowledge(live, original)


def record_human_viewer_unknown(delivery, reason_code):
    """Conservative original failure evidence; it never proves a safe drain."""
    if not isinstance(reason_code, str) or not re.fullmatch(r"[A-Z_]{1,80}", reason_code):
        raise ValueError("human viewer unknown requires a finite reason code")
    live = _live(delivery)
    original = _existing_boundary(live)
    if original is None:
        original = _record_transport(live, {**transport_binding(delivery), "adapter": TRANSPORT_ADAPTER,
            "completion": "unknown", "reason_code": reason_code}, "unknown")
    return _acknowledge(live, original)


def complete_human_viewer_operation(owner, execution):
    """Return an empty executor result only after the original transport ACK.

    Normal registered completion and Success revalidate the entire exact read
    inventory. This preflight creates neither a completion nor a replacement I/O.
    """
    from .registry._event_store.material_settlement import material_admission
    from .registry._event_store.material_delivery_drain import _Cut, _delivery_chain
    core = owner._core
    kernel, repository = owner.operation_repository()
    execution = verify_operation_execution(core, kernel, repository, execution)
    context = execution.operation.canonical.context
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        found = material_admission(core.event_store, db, core.task_id,
            ref_payload(context.own_transition_firing_ref), current=False)
        if found is None:
            raise RegistryConflict("human viewer lacks its original read command")
        admission, command = found
        _firing, _binding, _spec, _definition, config = read_definition(core.event_store, db, core.task_id, admission, command)
        rows = db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE t.task_id=? AND t.idempotency_key=? AND t.status='committed' AND o.object_type='resource_delivery/v1'",
            (str(core.task_id), "human-viewer:" + str(context.invocation_ref.version_id) + ":prepare")).fetchall()
        if len(rows) != 1:
            raise RegistryConflict("HUMAN_VIEWER_PENDING: no exact original human delivery")
        target = command["data"]["target_resource_ref"]
        chain = _delivery_chain(_Cut(core.event_store, db, core.task_id), rows[0]["logical_id"],
            {(target["logical_id"], target["version_id"])})
        if chain["prepared_only"]:
            raise RegistryConflict("HUMAN_VIEWER_PENDING: actual transport has not completed")
        boundary_row, boundary = chain["boundary"]
        evidence = json.loads(boundary["consumer_evidence"])
        from .registry._event_store.human_viewer import _expected_binding
        from .registry._event_store.material_control import row_ref
        expected = _expected_binding(admission, command, chain["stages"]["prepared"][1],
            row_ref(chain["stages"]["release_authorized"][0]), row_ref(chain["witness"][0]),
            channel=evidence.get("broker_channel_id"))
        _transport_evidence(evidence, expected, outcome="acknowledged")
        transaction = db.execute("SELECT command_json FROM transactions WHERE transaction_id=?",
            (boundary_row["transaction_id"],)).fetchone()
        action = json.loads(transaction["command_json"]).get("user_collaboration")
        if action != {"kind": TRANSPORT_ACTION, "action_ref": None,
                "session_ref": expected["session_ref"], "actor": expected["actor"]}:
            raise RegistryConflict("HUMAN_VIEWER_PENDING: receipt lacks original trusted transport authority")
    return OperationExecutionResult((), selected_outcome_id=config["outcome_id"])


def run_human_viewer_operation(owner, execution):
    from .registry.firing_recovery import record_registered_operation_completion
    result = complete_human_viewer_operation(owner, execution)
    kernel, repository = owner.operation_repository()
    key = "human-viewer:" + str(execution.operation.canonical.context.invocation_ref.version_id)
    outputs = register_operation_outputs(repository, execution, result.outputs,
        selected_outcome_id=result.selected_outcome_id, idempotency_key=key + ":outputs")
    record_registered_operation_completion(owner._core, kernel, repository, outputs,
        idempotency_key=key + ":completed")
    return outputs


def read_human_viewer_progress(owner, *, session, material_command_id=None, material_command_ref=None):
    """Original-authorized metadata projection; never create a second transfer.

    The issue flag is advisory for the explicit POST route. Admission and new
    witness authority are still rechecked by their original final writer gates.
    Any approved release or incomplete proof makes that flag false.
    """
    from .registry._event_store.material_control import read_accepted_material_command, row_ref
    from .registry._event_store.material_not_admitted import read_not_admitted
    from .registry._event_store.material_settlement import terminal_result
    from .registry._event_store.material_locations import _original_member_objects
    from .registry._event_store.material_delivery_drain import _Cut, _delivery_chain
    from .registry._event_store.human_viewer import is_read_material, _expected_binding
    from .registry.event_store import RegistryCorruptError
    from .registry.object_store import ObjectIntegrityError
    from .registry.schema_catalog import SchemaGovernanceError
    if (material_command_id is None) == (material_command_ref is None):
        raise ValueError("one exact human read command reference or identity is required")
    materials = owner.control.materials
    core = owner._core
    with materials._cut(session) as db:
        result = {"protocol": "rpnh/human_viewer_progress/v1",
            "command_id": material_command_id, "command_ref": material_command_ref,
            "state": "not_found_at_read_cut", "can_issue_new_delivery": False,
            "as_of": int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])}
        if material_command_ref is None:
            found = materials._controls(db, material_command_id, "command")
            if not found:
                return result
            if len(found) != 1:
                raise RegistryConflict("human read command identity is not unique")
            material_command_ref, _body = found[0]
        command = read_accepted_material_command(core.event_store, db, core.task_id,
            material_command_ref, current=False)
        original_actor = command["data"]["user_action"]
        if (original_actor["session_ref"] != ref_payload(session.ref)
                or original_actor["actor"] != session.actor
                or command["source"] != ref_payload(owner.principal_ref)):
            raise RegistryConflict("ACTOR_NOT_AUTHORIZED: human read progress requires its original command actor and session")
        if not is_read_material(command["data"]):
            raise RegistryConflict("INVALID_COMMAND: progress requires an original read_material command")
        result.update(command_id=command["command_id"], command_ref=material_command_ref,
            resource_ref=command["data"]["target_resource_ref"], target=command["target"],
            execution=command["data"]["execution"], state="accepted_waiting",
            can_issue_new_delivery=True)
        not_admitted = read_not_admitted(core.event_store, db, core.task_id, material_command_ref)
        if not_admitted is not None:
            result.update(state="rejected", can_issue_new_delivery=False)
            return result
        admissions = materials._admissions(db, material_command_ref)
        if len(admissions) > 1:
            raise RegistryConflict("human read command has multiple original firings")
        if not admissions:
            return result
        admission = materials._checked_admission(db, admissions[0], material_command_ref)
        result.update(state="admitted_waiting_delivery", admission_ref=admission["firing_admission_ref"],
            firing_ref=admission["transition_firing_ref"], invocation_ref=admission["invocation_ref"])
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
            (admission["transition_firing_ref"]["version_id"],)).fetchone()
        if root is None:
            result.update(state="unknown", can_issue_new_delivery=False, reason_code="FIRING_PROOF_UNAVAILABLE")
            return result
        if root["state"] in {"PUBLISHED", "REJECTED"}:
            result_ref, settled = terminal_result(core.event_store, db, core.task_id, admission, command, root)
            result.update(state="completed" if root["state"] == "PUBLISHED" else "rejected",
                can_issue_new_delivery=False, result_ref=result_ref, result_kind=settled["result_kind"],
                error=settled["error"])
            return result
        if root["state"] != "PROVISIONAL":
            result.update(state="unknown", can_issue_new_delivery=False, reason_code="RECOVERY_REQUIRED")
            return result
        try:
            read_definition(core.event_store, db, core.task_id, admission, command)
            rows = _original_member_objects(core.event_store, db, core.task_id, admission)
            cut = _Cut(core.event_store, db, core.task_id)
            original_events = db.execute("SELECT * FROM events WHERE producer_invocation_id=? "
                "AND event_type IN ('resource_delivery_prepared/v1','resource_release_authorized/v1','observed_read/v1')",
                (admission["invocation_ref"]["logical_id"],)).fetchall()
            human_facts = []
            for event in original_events:
                value = json.loads(event["payload_json"])
                if value.get("boundary") != "human_viewer":
                    continue
                value = cut.event(event)
                members = db.execute("SELECT firing_version_id,transaction_id FROM firing_temporary_members "
                    "WHERE member_kind='event' AND member_identity=?", (event["event_id"],)).fetchall()
                if (len(members) != 1 or members[0]["firing_version_id"] != admission["transition_firing_ref"]["version_id"]
                        or members[0]["transaction_id"] != event["transaction_id"]):
                    raise RegistryConflict("human read progress lost an original delivery event member")
                human_facts.append((event["event_type"], value))
            transport_rows = [row for row in rows if row["object_type"] in {
                "resource_delivery/v1", "resource_release_witness/v1", "delivery_boundary_receipt/v1"}
                and json.loads(row["metadata_json"])["boundary"] != "petri_input"]
            if not transport_rows:
                if human_facts:
                    raise RegistryConflict("human read progress lost its original delivery projection")
                require_read_delivery_pending(core.event_store, db, core.task_id, admission)
                return result
            exact = [(row, cut.obj(row_ref(row))[1]) for row in transport_rows]
            if any(data["boundary"] != "human_viewer" for _row, data in exact):
                raise RegistryConflict("human read has an unrelated transport responsibility")
            stages = {data["state"]: (row, data) for row, data in exact if row["object_type"] == "resource_delivery/v1"}
            deliveries = [row for row, _data in exact if row["object_type"] == "resource_delivery/v1"]
            if len(stages) != len(deliveries) or "prepared" not in stages:
                raise RegistryConflict("human read delivery stages are incomplete or repeated")
            prepared_row, prepared = stages["prepared"]
            if (prepared["context_ref"] != admission["invocation_ref"]
                    or prepared["resource_ref"] != {
                        "resource_id": result["resource_ref"]["logical_id"],
                        "resource_version_id": result["resource_ref"]["version_id"]}
                    or prepared["purpose"] != READ_PURPOSE):
                raise RegistryConflict("human read delivery is outside the accepted command")
            if (sum(kind == "resource_delivery_prepared/v1" for kind, _value in human_facts) != 1
                    or any(value.get("resource_ref") != prepared["resource_ref"]
                        or (value.get("delivery_ref") != row_ref(prepared_row)
                            if kind != "observed_read/v1" else value.get("delivery_id") != prepared_row["logical_id"])
                        for kind, value in human_facts)):
                raise RegistryConflict("human read progress contains another original delivery fact")
            witnesses = [(row, data) for row, data in exact if row["object_type"] == "resource_release_witness/v1"]
            receipts = [(row, data) for row, data in exact if row["object_type"] == "delivery_boundary_receipt/v1"]
            result["delivery_ref"] = row_ref(prepared_row)
            if set(stages) == {"prepared"} and not witnesses and not receipts:
                if any(kind != "resource_delivery_prepared/v1" for kind, _value in human_facts):
                    raise RegistryConflict("human read progress lost an approved original release")
                require_read_delivery_pending(core.event_store, db, core.task_id, admission)
                return result
            result.update(can_issue_new_delivery=False)
            if "release_authorized" not in stages or len(witnesses) != 1:
                raise RegistryConflict("human read has incomplete original release authority")
            released_row, released = stages["release_authorized"]
            witness_row, witness = witnesses[0]
            if (released["previous_delivery_ref"] != row_ref(prepared_row)
                    or released["witness_ref"] != row_ref(witness_row)
                    or witness["delivery_ref"] != row_ref(prepared_row)
                    or any(data["resource_ref"] != prepared["resource_ref"]
                        or data["context_ref"] != prepared["context_ref"] for data in (released, witness))):
                raise RegistryConflict("human read release does not close its original preparation")
            result.update(state="release_authorized", delivery_ref=row_ref(released_row), witness_ref=row_ref(witness_row))
            if not receipts:
                if set(stages) != {"prepared", "release_authorized"}:
                    raise RegistryConflict("human read terminal lost its boundary receipt")
                require_read_delivery_pending(core.event_store, db, core.task_id, admission)
                return result
            if len(receipts) != 1:
                raise RegistryConflict("human read has multiple original transport observations")
            boundary_row, boundary = receipts[0]
            outcome = boundary["outcome"]
            if outcome not in {"acknowledged", "unknown"}:
                raise RegistryConflict("human read boundary has an unsupported outcome")
            evidence = json.loads(boundary["consumer_evidence"])
            binding = _expected_binding(admission, command, prepared, row_ref(released_row),
                row_ref(witness_row), channel=evidence.get("broker_channel_id"))
            _transport_evidence(evidence, binding, outcome=outcome)
            tx = db.execute("SELECT command_json,idempotency_key FROM transactions WHERE transaction_id=?",
                (boundary_row["transaction_id"],)).fetchone()
            action = json.loads(tx["command_json"]).get("user_collaboration") if tx is not None else None
            if (boundary["delivery_ref"] != row_ref(released_row) or boundary["witness_ref"] != row_ref(witness_row)
                    or tx is None or tx["idempotency_key"] != "human-viewer:" + admission["invocation_ref"]["version_id"] + ":boundary:boundary-receipt"
                    or action != {"kind": TRANSPORT_ACTION if outcome == "acknowledged" else UNKNOWN_ACTION,
                        "session_ref": binding["session_ref"], "actor": binding["actor"], "action_ref": None}):
                raise RegistryConflict("human read progress lacks its trusted original transport action")
            result["boundary_receipt_ref"] = row_ref(boundary_row)
            if outcome == "unknown":
                result.update(state="unknown", reason_code=evidence["reason_code"])
                return result
            if "acknowledged" in stages:
                _delivery_chain(cut, prepared_row["logical_id"],
                    {(result["resource_ref"]["logical_id"], result["resource_ref"]["version_id"])})
            elif set(stages) != {"prepared", "release_authorized"}:
                raise RegistryConflict("human read progress has an inconsistent terminal")
            result.update(state="delivered_pending_settlement")
            return result
        except (RegistryConflict, RegistryCorruptError, ObjectIntegrityError, SchemaGovernanceError,
                KeyError, TypeError, ValueError, OSError):
            result.update(state="unknown", can_issue_new_delivery=False, reason_code="DELIVERY_PROOF_UNAVAILABLE")
            return result


__all__ = ("HumanViewerDelivery", "READ_PROTOCOL", "READ_EXECUTOR", "READ_IDENTITY", "READ_CONTRACTS",
    "READ_CONFIG_SCHEMA", "register_human_viewer_read", "human_viewer_host_bindings",
    "prepare_human_viewer_operation", "consume_human_viewer_delivery", "transport_binding", "delivery_media_type",
    "record_human_viewer_transport", "record_human_viewer_unknown", "complete_human_viewer_operation",
    "run_human_viewer_operation", "read_human_viewer_progress")
