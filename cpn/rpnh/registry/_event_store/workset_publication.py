"""Workset CAS and JR15--19 checks in the original BEGIN IMMEDIATE cut."""
from __future__ import annotations

import hashlib
import json

from ..event_store import RegistryConflict
from ..identities import TypedId
from ..models import PreparedObject
from ..schema_catalog import canonical_json, canonical_text
from .collaboration_descriptors import descriptor_store, exact_descriptor, exact_prepared, readable_descriptor
from .source_identity import read_source_binding


def _fail(message):
    raise RegistryConflict(message)


def _exact(context, reference):
    ref = reference.get("ref", reference)
    if "source_id" in reference and reference["source_id"] != _binding(context)["source_id"]:
        _fail("Workset dependency requires its exact local source")
    store = descriptor_store(context.event_store)
    if ref.get("entity_type") == "resource_version/v1":
        prepared = exact_prepared(context.db, store, context.task_id, ref)
        # A resource's bytes are application content, not its Registry descriptor.
        # Verify those exact registered bytes without equating them to metadata.
        store.read_registered(prepared)
        return dict(prepared.metadata)
    return exact_descriptor(context.db, store, context.task_id, ref)


def _binding(context):
    row = read_source_binding(context.db, context.event_store.catalog, context.task_id)
    if row is None:
        _fail("Workset publication requires explicit source owner registration")
    return json.loads(row["binding_metadata_json"])


def _latest(context, logical_id):
    row = context.db.execute("SELECT o.version_id,o.metadata_json,e.stream_sequence FROM objects o "
        "JOIN events e ON e.event_id=o.published_event_id WHERE o.logical_id=? "
        "ORDER BY e.stream_sequence DESC LIMIT 1", (logical_id,)).fetchone()
    if row is None:
        return None
    document = json.loads(row["metadata_json"])
    if document.get("record_ref", {}).get("ref", {}).get("entity_type") != "collaboration_workset/v1":
        _fail("Workset identity is occupied by another object type")
    exact = _exact(context, document["record_ref"])
    head = context.db.execute("SELECT sequence FROM stream_heads WHERE stream_id=?",
                              ("object:" + logical_id,)).fetchone()
    if head is None or head[0] != row["stream_sequence"] or exact["body"]["sequence"] != head[0]:
        _fail("Workset stream head differs from its exact canonical version")
    return exact


def _ref(document):
    return document["record_ref"]["ref"]


def _metadata(context, reference, kind):
    if reference.get("entity_type") != kind or not context.exact_ref_exists(reference):
        _fail("Workset Success dependency lacks exact Registry authority")
    result = context.version_metadata(reference["version_id"], kind)
    if result is None:
        _fail("Workset Success dependency is absent")
    return result


def _success(context, obj, body):
    """Prove the record's actual producer, deposit, result and Success cut."""
    firing = _metadata(context, body["firing_ref"], "transition_firing/v1")
    completion = _metadata(context, body["completion_ref"], "firing_completion/v2")
    checkpoint = _metadata(context, body["checkpoint_ref"], "marking_checkpoint/v1")
    token = _metadata(context, body["occurrence_ref"], "petri_token/v1")
    result = _metadata(context, completion["operation_result_ref"], "operation_result/v1")
    delta = _metadata(context, completion["marking_delta_ref"], "marking_delta/v1")
    inv = body["invocation_ref"]
    events = [event for event in context.events if event.event_type == "transition_firing_settled/v1"]
    if (len(events) != 1 or events[0].payload.get("transition_firing_ref") != body["firing_ref"]
            or events[0].payload.get("firing_completion_ref") != body["completion_ref"]
            or obj.producer_invocation_id is None or str(obj.producer_invocation_id) != inv["logical_id"]
            or completion["invocation_ref"] != inv
            or completion["transition_firing_ref"] != body["firing_ref"]
            or result["invocation_ref"] != inv or result["transition_firing_ref"] != body["firing_ref"]
            or result["business_outcome"] != "completed"
            or body["output_resource_ref"] not in result["output_resource_refs"]
            or token["resource_ref"] != {"resource_id": body["output_resource_ref"]["logical_id"],
                                         "resource_version_id": body["output_resource_ref"]["version_id"]}
            or body["occurrence_ref"] not in delta["deposited_refs"]
            or body["occurrence_ref"] not in checkpoint["token_refs"]
            or any(context.version_transaction(body[key]["version_id"]) != str(context.transaction_id)
                   for key in ("completion_ref", "checkpoint_ref", "occurrence_ref"))):
        _fail("collaboration record must close with its own ordinary Success and usable occurrence")
    from ..declared_effect_validation import validate_declared_effect_success
    invocation = _metadata(context, inv, "invocation/v1")
    _validate_registered_return(context, firing, invocation, result)
    predecessor = _metadata(context, checkpoint["previous_checkpoint_ref"], "marking_checkpoint/v1")
    from ..normal_root_token_allocation import normal_root_allocation_scheme
    ordinary_token_ref_scheme = normal_root_allocation_scheme(
        objects=context.objects, task_id=context.task_id, transaction_id=context.transaction_id,
        firing=firing, invocation=invocation, result=result, delta=delta,
        completion=completion, checkpoint=checkpoint,
        historical=getattr(context, 'published_transaction_id', None) is not None)
    validate_declared_effect_success(context.event_store, context.db, firing=firing, invocation=invocation,
        result=result, delta=delta, predecessor=predecessor, checkpoint=checkpoint,
        exact=context.exact_ref_exists, metadata=context.version_metadata, require_ordinary=True,
        published_transaction_id=getattr(context, "published_transaction_id", None),
        historical_cut=getattr(context, "historical_cut", None),
        ordinary_token_ref_scheme=ordinary_token_ref_scheme)
    return firing, delta


def _validate_registered_return(context, firing, invocation, result):
    # Original RunOwner.products persists resources and returns a verified DTO;
    # it does not produce the dispatcher's optional recovery-return event.
    # Mandatory proof therefore comes from the actual Start/publication facts.
    _validate_original_product_facts(context, firing, invocation, result)
    rows = context.db.execute("SELECT * FROM events WHERE event_type='registered_operation_completion_recorded/v1' "
        "AND aggregate_id=?", (invocation["operation_execution_lease_ref"]["logical_id"],)).fetchall()
    if not rows:
        return
    if len(rows) != 1:
        _fail("Workset Success has conflicting durable registered operation returns")
    row = rows[0]
    _own_durable_fact(context, row, firing=firing, invocation=invocation)
    body = json.loads(row["payload_json"])
    context.event_store.catalog.validate_event_payload(row["event_type"], body, criticality=row["criticality"])
    outputs = [{"entity_type": "resource_version/v1", "logical_id": item["resource_ref"]["resource_id"],
                "version_id": item["resource_ref"]["resource_version_id"]} for item in body["ordered_outputs"]]
    start = context.db.execute("SELECT * FROM events WHERE event_id=?", (body["operation_start_event_id"],)).fetchone()
    if (row["payload_schema_ref"] != "registry_v1/registered_operation_completion_recorded/v1"
            or row["task_id"] != str(context.task_id) or row["producer_invocation_id"] != invocation["invocation_ref"]["logical_id"]
            or not context.persisted_member_visible("event", row["event_id"])
            or body["invocation_ref"] != invocation["invocation_ref"]
            or body["transition_firing_ref"] != firing["transition_firing_ref"]
            or body["net_instance_ref"] != firing["net_instance_ref"]
            or body["operation_binding_ref"] != firing["operation_binding_ref"]
            or body["operation_execution_lease_ref"] != invocation["operation_execution_lease_ref"]
            or sorted(outputs, key=canonical_text) != sorted(result["output_resource_refs"], key=canonical_text)
            or body["workspace_revision_candidate_ref"] is not None
            or start is None or start["event_type"] != "operation_execution_started/v1"
            or start["producer_invocation_id"] != row["producer_invocation_id"]
            or not context.persisted_member_visible("event", start["event_id"])):
        _fail("Workset Success differs from its exact durable Start/registered return")
    for reference in outputs:
        output = _metadata(context, reference, "resource_version/v1")
        if output["descriptors"].get("output_outcome_id") != body["selected_outcome_id"]:
            _fail("Workset Success selected outcome differs from registered return")


def _own_durable_fact(context, event, *, firing, invocation):
    """Exact already-committed fact and transaction within this firing root."""
    if (event is None or event["task_id"] != str(context.task_id)
            or event["task_round_id"] != invocation["task_round_ref"]["logical_id"]
            or event["net_instance_id"] != firing["net_instance_ref"]["logical_id"]
            or event["criticality"] != "authoritative"
            or event["producer_invocation_id"] != invocation["invocation_ref"]["logical_id"]
            or event["writer_fencing_epoch"] != context.transaction_writer_epoch):
        _fail("ordinary product/Start fact differs from exact task/round/net/producer/fence")
    transaction = context.db.execute("SELECT status FROM transactions WHERE transaction_id=?",
                                     (event["transaction_id"],)).fetchone()
    terminals = context.db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                                    (event["transaction_id"],)).fetchall()
    if transaction is None or transaction[0] != "committed" or len(terminals) != 1:
        _fail("ordinary product/Start lacks its exact committed transaction")
    terminal = terminals[0]
    if (terminal["task_id"] != str(context.task_id) or terminal["criticality"] != "authoritative"
            or terminal["payload_schema_ref"] != "registry_v1/transaction_committed/v1"
            or terminal["stream_id"] != "transaction:" + event["transaction_id"]
            or terminal["aggregate_id"] != event["transaction_id"] or terminal["aggregate_type"] != "transaction"
            or terminal["producer_principal"] != "framework" or terminal["producer_invocation_id"] is not None):
        _fail("ordinary product/Start terminal envelope differs")
    for kind, identity in (("event", event["event_id"]), ("event", terminal["event_id"]),
                           ("transaction", event["transaction_id"])):
        _own_temporary_member(context, kind, identity, firing, invocation)


def _own_temporary_member(context, kind, identity, firing, invocation):
    rows = context.db.execute("SELECT p.* FROM firing_temporary_members m JOIN firing_publications p "
        "ON p.firing_version_id=m.firing_version_id WHERE m.member_kind=? AND m.member_identity=?",
        (kind, identity)).fetchall()
    publication = getattr(context, "published_transaction_id", None)
    expected_state = "PROVISIONAL" if publication is None else "PUBLISHED"
    if (len(rows) != 1 or rows[0]["state"] != expected_state
            or (publication is not None and rows[0]["published_transaction_id"] != publication)
            or rows[0]["firing_version_id"] != firing["transition_firing_ref"]["version_id"]
            or rows[0]["invocation_version_id"] != invocation["invocation_ref"]["version_id"]
            or rows[0]["invocation_logical_id"] != invocation["invocation_ref"]["logical_id"]
            or rows[0]["net_version_id"] != firing["net_instance_ref"]["version_id"]):
        _fail("ordinary product/Start is outside the exact producing firing root")


def _validate_original_product_facts(context, firing, invocation, result):
    starts = context.db.execute("SELECT * FROM events WHERE event_type='operation_execution_started/v1' AND aggregate_id=?",
        (invocation["operation_execution_lease_ref"]["logical_id"],)).fetchall()
    if len(starts) != 1:
        _fail("Workset Success requires one real durable Start")
    event = starts[0]
    _own_durable_fact(context, event, firing=firing, invocation=invocation)
    start = json.loads(event["payload_json"])
    context.event_store.catalog.validate_event_payload(event["event_type"], start, criticality=event["criticality"])
    binding = _metadata(context, firing["operation_binding_ref"], "operation_binding/v1")
    executable = _metadata(context, start["executable_transition_binding_ref"], "executable_transition_binding/v1")
    if (event["payload_schema_ref"] != "registry_v1/operation_execution_started/v1"
            or event["stream_id"] != "operation-lease:" + invocation["operation_execution_lease_ref"]["logical_id"]
            or event["aggregate_type"] != "operation_execution_lease"
            or event["producer_principal"] != invocation["principal_ref"]["logical_id"]
            or invocation["own_transition_firing_ref"] != firing["transition_firing_ref"]
            or start["invocation_ref"] != invocation["invocation_ref"]
            or start["operation_execution_lease_ref"] != invocation["operation_execution_lease_ref"]
            or start["transition_firing_ref"] != firing["transition_firing_ref"]
            or start["operation_binding_ref"] != firing["operation_binding_ref"]
            or start["principal_ref"] != invocation["principal_ref"]
            or start["operation_spec_ref"] != binding["operation_spec_ref"]
            or executable["operation_binding_ref"] != firing["operation_binding_ref"]
            or executable["net_instance_ref"] != firing["net_instance_ref"]
            or executable["principal_ref"] != invocation["principal_ref"]
            or start["claimed_input_refs"] != firing["claimed_input_refs"]):
        _fail("Workset Success Start differs from actual invocation/binding/claims")
    for ref in result["output_resource_refs"]:
        data = _metadata(context, ref, "resource_version/v1")
        row = context.db.execute("SELECT * FROM objects WHERE object_type='resource_version/v1' AND logical_id=? AND version_id=?",
                                 (ref["logical_id"], ref["version_id"])).fetchone()
        if row is None or row["producer_invocation_id"] != invocation["invocation_ref"]["logical_id"]:
            _fail("ordinary output lacks its real resource producer")
        publication = context.db.execute("SELECT * FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()
        _own_durable_fact(context, publication, firing=firing, invocation=invocation)
        _own_temporary_member(context, "object", ref["version_id"], firing, invocation)
        expected = {"object_type": row["object_type"], "logical_id": row["logical_id"], "version_id": row["version_id"],
            "size": row["size"], "media_type": row["media_type"], "schema_ref": row["schema_ref"],
            "storage_locator": row["storage_locator"], "metadata": data}
        if (publication["event_type"] != "object_version_published/v1"
                or publication["payload_schema_ref"] != "registry_v1/object_version_published/v1"
                or publication["producer_principal"] != "framework"
                or publication["stream_id"] != "object:" + ref["logical_id"]
                or publication["aggregate_id"] != ref["logical_id"] or publication["aggregate_type"] != "resource_version/v1"
                or publication["transaction_id"] != row["transaction_id"]
                or publication["ordinal"] <= event["ordinal"]
                or json.loads(publication["payload_json"]) != expected
                or data["resource_id"] != ref["logical_id"] or data["resource_version_id"] != ref["version_id"]
                or data["producer_ref"] != invocation["invocation_ref"] or data["task_ref"] != invocation["task_ref"]
                or data["net_ref"] != firing["net_instance_ref"] or data["round_ref"] != invocation["task_round_ref"]
                or data["origin_kind"] != "petri_output" or data["origin"]["kind"] != "petri_output"
                or data["origin"].get("secondary_ref") != invocation["activation_ref"]):
            _fail("ordinary output publication/header/origin differs from its real producer scope")
        # Exact bytes/schema/outcome/port/binding and whole quantity are reclosed
        # again by require_ordinary=True below, before the original commit.
        _resource_bytes(context, ref)


def _resource_bytes(context, reference):
    _metadata(context, reference, "resource_version/v1")
    row = context.db.execute("SELECT * FROM objects WHERE version_id=?", (reference["version_id"],)).fetchone()
    if row is None:
        _fail("collaboration product must already be a registered operation output")
    prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]),
        TypedId.parse(row["version_id"]), row["size"], row["media_type"], row["schema_ref"],
        TypedId.parse(row["producer_invocation_id"]) if row["producer_invocation_id"] else None,
        row["storage_locator"], json.loads(row["metadata_json"]))
    return descriptor_store(context.event_store).read_registered(prepared)


def _source_delivery(context, acceptance):
    from ...collaboration.delivery_evidence import read_delivery_evidence
    reference = acceptance["logical_delivery_ref"]
    source = getattr(context.event_store, "_workset_local_sources", {}).get(reference["source_id"])
    if source is None:
        _fail("acceptance requires its explicitly attached local source reader")
    physical = acceptance["physical_delivery_ref"]
    attempt = getattr(source, "_workset_delivery_attempts", {}).get(physical["ref"]["version_id"])
    if attempt is None:
        _fail("acceptance lacks an actually consumed local delivery; reprepare after restart")
    with source.event_store.connect() as db:
        db.execute("BEGIN")
        delivery, exported, payload, _physical = read_delivery_evidence(
            source, db, reference, physical, active=attempt)
    return delivery, exported, payload


def validate_workset_publication(context):
    from ...collaboration.worksets import (TYPES, WORKSET, REQUEST, EXPORT, DELIVERY,
        ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL, RECONCILIATION, record_ref,
        acceptance_identity, command_key, NORMAL_ROOT_TERMINAL)
    selected = [obj for obj in context.objects if obj.object_type in (*TYPES, NORMAL_ROOT_TERMINAL)]
    if not selected:
        for obj in context.objects:
            if context.db.execute("SELECT 1 FROM objects WHERE logical_id=? AND object_type='collaboration_workset/v1'",
                                  (str(obj.logical_id),)).fetchone():
                _fail("another object type cannot overwrite the Workset stream")
        return
    binding = _binding(context)
    store = descriptor_store(context.event_store)
    documents = {str(obj.version_id): readable_descriptor(store, obj) for obj in selected}
    for obj in selected:
        document = documents[str(obj.version_id)]
        reference = document["record_ref"]
        if (reference["source_id"] != binding["source_id"]
                or reference["ref"] != {"entity_type": obj.object_type,
                     "logical_id": str(obj.logical_id), "version_id": str(obj.version_id)}
                or document["owner_task_ref"]["source_id"] != binding["source_id"]
                or document["owner_task_ref"]["ref"] != binding["task_ref"]
                or context.idempotency_key not in {document["command_id"], command_key(document["command_id"])}):
            _fail("collaboration command/source/owner/self identity differs")
        body = document["body"]
        if obj.object_type != WORKSET:
            if context.db.execute("SELECT 1 FROM objects WHERE logical_id=?", (str(obj.logical_id),)).fetchone():
                _fail("logical collaboration records are immutable; retry their original command")
        if obj.object_type in {EXPORT, ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL, NORMAL_ROOT_TERMINAL}:
            _success(context, obj, body)
        elif obj.object_type != WORKSET and obj.producer_invocation_id is not None:
            _fail("standalone owner record cannot counterfeit a firing producer")
        if obj.object_type == REQUEST:
            _exact(context, body["requirements_ref"])
            _exact(context, body["input_binding_ref"])
        elif obj.object_type == EXPORT:
            request = _exact(context, body["request_ref"])
            if _ref(request)["entity_type"] != REQUEST:
                _fail("export must refer to a real source request")
            if hashlib.sha256(_resource_bytes(context, body["output_resource_ref"])).hexdigest() != body["content_digest"]:
                _fail("export digest differs from its actual ordinary output")
        elif obj.object_type == DELIVERY:
            export = _exact(context, body["export_ref"])
            if _ref(export)["entity_type"] != EXPORT:
                _fail("logical delivery requires its real source Success export")
        elif obj.object_type == RECONCILIATION:
            _validate_reconciliation(context, body)
    worksets = [obj for obj in selected if obj.object_type == WORKSET]
    dependent = [obj for obj in selected if obj.object_type in {ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL, NORMAL_ROOT_TERMINAL}]
    if dependent and (len(dependent) != 1 or len(worksets) != 1):
        _fail("one Success command must advance exactly one Workset and business record")
    if len(worksets) > 1:
        _fail("one owner command can advance only one Workset")
    for obj in worksets:
        document = documents[str(obj.version_id)]
        body = document["body"]
        previous = _latest(context, str(obj.logical_id))
        if body["action"] == "create":
            if (previous is not None or body["expected"] is not None or body["sequence"] != 1
                    or body["collection_version"] != 1 or body["state"] != "open"
                    or body["acceptances"] or body["contributions"] or body["terminal_ref"] is not None
                    or body["required_child_seal_ref"] is not None or obj.producer_invocation_id is not None
                    or obj.logical_id != record_ref(WORKSET, context.task_id, document["command_id"], document["command_id"]).entity_id):
                _fail("Workset creation lacks its exact empty initial scope")
            _exact(context, body["requirements_ref"])
            _exact(context, body["input_binding_ref"])
            continue
        expected = body["expected"]
        if (previous is None or expected is None or expected["record_ref"] != previous["record_ref"]
                or expected["stream_head"] != previous["body"]["sequence"]
                or expected["command_id"] != previous["command_id"]
                or body["sequence"] != previous["body"]["sequence"] + 1):
            _fail("stale Workset expected version, head, or command")
        before = previous["body"]
        if before["state"] == "completed":
            _fail("completed Workset cannot be reopened by a late result")
        if any(body[key] != before[key] for key in
               ("requirements_ref", "input_binding_ref", "generation", "required_child_seal_ref")):
            _fail("Workset advancement cannot silently change requirements/input/generation/child seal")
        action = body["action"]
        allowed = {"action", "expected", "sequence"}
        if action == "grow":
            if (before["state"] != "open" or body["collection_version"] != before["collection_version"] + 1
                    or not set(before["expected_slots"]) < set(body["expected_slots"])
                    or before["acceptances"] or before["contributions"]
                    or body["acceptances"] or body["contributions"] or dependent):
                _fail("growth needs an open larger collection and explicit handling of prior results")
            allowed |= {"collection_version", "expected_slots"}
        elif action == "seal":
            if before["state"] != "open" or body["state"] != "sealed" or dependent:
                _fail("Workset seal is stale or closed")
            allowed.add("state")
        elif action in {"accept", "contribute", "complete"}:
            if len(dependent) != 1 or obj.producer_invocation_id != dependent[0].producer_invocation_id:
                _fail("business advancement requires its same-Success owner record")
            record = documents[str(dependent[0].version_id)]
            business = record["body"]
            if business["expected"] != expected:
                _fail("business record and Workset compare different owner versions")
            _validate_business(context, action, previous, body, record, dependent[0])
            allowed |= {"acceptances"} if action == "accept" else {"contributions"} if action == "contribute" else {"state", "terminal_ref"}
        else:
            _fail("unknown Workset transition")
        if any(body[key] != before[key] for key in body if key not in allowed):
            _fail("Workset command changed unrelated owner state")


def _validate_business(context, action, previous, after, record, obj):
    from ...collaboration.worksets import (ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL,
                                          NORMAL_ROOT_TERMINAL, acceptance_identity, record_ref)
    body = record["body"]
    before = previous["body"]
    normal = action == "complete" and obj.object_type == NORMAL_ROOT_TERMINAL
    kind = {"accept": ACCEPTANCE, "contribute": CONTRIBUTION,
            "complete": NORMAL_ROOT_TERMINAL if normal else ROOT_TERMINAL}[action]
    if obj.object_type != kind:
        _fail("Workset command and same-Success record type differ")
    completion = _metadata(context, body["completion_ref"], "firing_completion/v2")
    delta = _metadata(context, completion["marking_delta_ref"], "marking_delta/v1")
    if action in {"accept", "contribute"}:
        slot = body["slot"]
        if slot not in before["expected_slots"]:
            _fail("result names an unexpected contribution slot")
    if action == "accept":
        identity = acceptance_identity(body["logical_delivery_ref"], previous, slot)
        expected_ref = record_ref(ACCEPTANCE, context.task_id, identity, record["command_id"])
        if (slot in before["acceptances"] or body["decision"] not in {"new", "carry"}
                or str(obj.logical_id) != str(expected_ref.entity_id) or str(obj.version_id) != str(expected_ref.version_id)
                or any(body[key] != value for key, value in identity.items())
                or after["acceptances"] != {**before["acceptances"], slot: record["record_ref"]}):
            _fail("acceptance identity/slot conflicts; retransmission must return the existing A/O")
        delivery, export, payload = _source_delivery(context, body)
        target = delivery["body"]
        expected_target = {"target_owner_ref": previous["owner_task_ref"],
            "target_source_id": previous["record_ref"]["source_id"],
            "target_workset_id": _ref(previous)["logical_id"], "slot": slot,
            **{key: before[key] for key in ("collection_version", "generation", "requirements_ref", "input_binding_ref")}}
        if (any(target[key] != value for key, value in expected_target.items())
                or payload != _resource_bytes(context, body["output_resource_ref"])
                or body["content_digest"] != export["body"]["content_digest"]):
            _fail("acceptance target or registered local output differs from the delivered export")
    elif action == "contribute":
        acceptance = _exact(context, body["acceptance_ref"])
        if (before["acceptances"].get(slot) != body["acceptance_ref"]
                or slot in before["contributions"]
                or acceptance["body"]["occurrence_ref"] not in delta["consumed_refs"]
                or after["contributions"] != {**before["contributions"], slot: record["record_ref"]}):
            _fail("contribution must actually consume its current accepted occurrence once")
    else:
        contributions = list(before["contributions"].values())
        if (before["state"] != "sealed" or set(before["expected_slots"]) != set(before["contributions"])
                or set(before["acceptances"]) != set(before["expected_slots"])
                or body["contribution_refs"] != contributions
                or after["state"] != "completed" or after["terminal_ref"] != record["record_ref"]
                or (not normal and body["required_child_seal_ref"] is not None)):
            _fail("root completion requires a sealed complete Workset and independent child closure")
        if context.db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL' AND firing_version_id<>?",
                              (body["firing_ref"]["version_id"],)).fetchone():
            _fail("Workset root completion cannot bypass another active ordinary firing")
        if not normal and context.db.execute("SELECT 1 FROM objects WHERE object_type='execution_instance/v1' "
                              "AND json_extract(metadata_json,'$.parent_business_firing_ref.version_id')=?",
                              (body["firing_ref"]["version_id"],)).fetchone():
            _fail("this finite Workset root requires a separate implemented enrolled-child closure contract")
        if normal:
            _validate_normal_completion_request(context, body, completion)
        for reference in contributions:
            contribution = _exact(context, reference)
            if contribution["body"]["occurrence_ref"] not in delta["consumed_refs"]:
                _fail("root terminal must actually consume every expected contribution")
        _validate_terminal_binding(context, body)


def _validate_normal_completion_request(context, body, completion):
    """Reclose all registered products; a terminal-port subset is not a command."""
    from ...registry.execution_child_closure import PROFILE
    result = _metadata(context, completion["operation_result_ref"], "operation_result/v1")
    bundle = []
    outcomes = set()
    for ref in result["output_resource_refs"]:
        metadata = _metadata(context, ref, "resource_version/v1")
        descriptors = metadata["descriptors"]
        outcomes.add(descriptors["output_outcome_id"])
        bundle.append({"port_id": descriptors["output_port_id"],
            "output_binding_ref": metadata["origin"]["primary_ref"], "resource_ref": ref})
    bundle.sort(key=lambda item: (item["port_id"], item["resource_ref"]["version_id"], item["output_binding_ref"]["version_id"]))
    request = body["completion_request"]
    chosen = [item for item in bundle if item["port_id"] == request["output_port"]]
    if (body["closure_profile"] != PROFILE or outcomes != {request["selected_outcome_id"]}
            or request["output_bundle"] != bundle or len(chosen) != 1
            or chosen[0]["resource_ref"] != body["output_resource_ref"]):
        _fail("normal completion command differs from its entire registered product bundle")


def _validate_terminal_binding(context, body):
    from ...executable_net import load_compiled_net
    firing = _metadata(context, body["firing_ref"], "transition_firing/v1")
    net = _metadata(context, firing["net_instance_ref"], "net_instance/v1")
    # Use the actual executable binding selected by this firing, never a caller title.
    rows = context.db.execute("SELECT metadata_json FROM objects WHERE object_type='executable_transition_binding/v1'").fetchall()
    matches = [json.loads(row[0]) for row in rows]
    matches = [value for value in matches if value.get("operation_binding_ref") == firing["operation_binding_ref"]
               and value.get("net_instance_ref") == firing["net_instance_ref"]]
    if len(matches) != 1:
        _fail("root terminal lacks its exact executable binding")
    resource = matches[0]["declaration_resource_ref"]
    compiled = load_compiled_net(json.loads(_resource_bytes(context, {
        "entity_type": "resource_version/v1", "logical_id": resource["resource_id"],
        "version_id": resource["resource_version_id"]})))
    token = _metadata(context, body["occurrence_ref"], "petri_token/v1")
    transition = next(item for item in compiled.symbolic.transitions if item.name == firing["transition_id"])
    matches = [terminal for terminal in (compiled.source.terminal, *compiled.source.terminal_alternatives)
               if terminal.config.get("run_outcome") == "complete"
               and transition.operation == f"{terminal.source.component}.{terminal.operation}"
               and any(port.name == f"{terminal.source.component}.{terminal.source.port}" and port.place == token["place"]
                       for port in compiled.ports)
               and terminal.outcome == token["verdict"]]
    if len(matches) != 1:
        _fail("Workset root Success is not the actual registered Module terminal")


def _validate_reconciliation(context, body):
    acceptance = _exact(context, body["acceptance_ref"])
    if acceptance["body"]["logical_delivery_ref"] != body["logical_delivery_ref"]:
        _fail("reconciliation does not refer to the accepted logical delivery")
    source = getattr(context.event_store, "_workset_local_sources", {}).get(body["physical_delivery_ref"]["source_id"])
    if source is None or body["physical_delivery_ref"]["source_id"] != body["logical_delivery_ref"]["source_id"]:
        _fail("reconciliation requires the same explicitly attached physical source")
    from ...collaboration.delivery_evidence import read_delivery_evidence
    with source.event_store.connect() as db:
        db.execute("BEGIN")
        logical, exported, payload, physical = read_delivery_evidence(source, db,
            body["logical_delivery_ref"], body["physical_delivery_ref"], terminal_required=True)
        if (physical["state"] != body["original_outcome"]
                or acceptance["body"]["content_digest"] != exported["body"]["content_digest"]
                or acceptance["owner_task_ref"] != logical["body"]["target_owner_ref"]
                or any(acceptance["body"][key] != logical["body"][key] for key in
                       ("target_source_id", "target_workset_id", "collection_version", "input_binding_ref", "slot"))):
            _fail("reconciliation must retain the exact original physical terminal and accepted target")
        if physical["state"] == "acknowledged":
            receipt = exact_descriptor(db, source.object_store, source.task_id, physical["boundary_receipt_ref"])
            expected_evidence = {"acceptance_ref": body["acceptance_ref"],
                "physical_delivery_ref": {**body["physical_delivery_ref"], "ref": physical["previous_delivery_ref"]}}
            if receipt["consumer_evidence"] != canonical_text(expected_evidence):
                _fail("acknowledged reconciliation lacks the receipt naming this exact target A/O")
