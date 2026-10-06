"""Explicit owner Worksets and ordinary-Success collaboration records.

This optional local adapter never schedules work, chooses applicability, or
dispatches an executor. Its records are validated again inside the original
EventStore writer transaction. Source readers are explicit trusted-HOST inputs;
source names alone never resolve a Registry or grant access.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import uuid

from ..registry.event_store import RegistryConflict
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.publication import _ref_payload, _version_from_payload, _stable_id
from ..registry.schema_catalog import canonical_json, canonical_text
from ..registry._event_store.collaboration_descriptors import exact_descriptor
from .references import SourceQualifiedVersionRef
from .sources import get_local_source_identity


WORKSET = "collaboration_workset/v1"
REQUEST = "collaboration_request/v1"
EXPORT = "collaboration_result_export/v1"
DELIVERY = "collaboration_logical_delivery/v1"
ACCEPTANCE = "collaboration_acceptance/v1"
CONTRIBUTION = "collaboration_contribution/v1"
ROOT_TERMINAL = "collaboration_root_terminal/v1"
NORMAL_ROOT_TERMINAL = "collaboration_root_terminal/v2"
RECONCILIATION = "collaboration_delivery_reconciliation/v1"
TYPES = (WORKSET, REQUEST, EXPORT, DELIVERY, ACCEPTANCE, CONTRIBUTION,
         ROOT_TERMINAL, RECONCILIATION)


def _data(value):
    return json.loads(json.dumps(value, allow_nan=False))


def record_ref(kind, task_id, identity, version):
    def uid(label, material):
        return uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            "namespace": "rpnh-workset-v1", "type": kind,
            "task": str(task_id), "label": label, "material": material})).hex
    return VersionRef(kind, TypedId("resource", uid("logical", identity)),
                      TypedId("resource_version", uid("version", version)))


def qualified(source_id, ref):
    return SourceQualifiedVersionRef(source_id, ref).to_dict()


def command_key(command_id):
    if not isinstance(command_id, str) or not command_id:
        raise ValueError("an explicit nonempty collaboration command is required")
    return "collaboration-workset:" + canonical_text({"command_id": command_id})


def _source(core):
    identity = get_local_source_identity(core)
    if identity is None:
        raise RegistryConflict("Workset owner requires an explicitly bound source")
    return identity


def _stage(core, tx, kind, ref, body, *, command_id, producer=None):
    source = _source(core)
    document = {"schema_version": "registry_v1/" + kind,
                "record_ref": qualified(source.source_id, ref),
                "owner_task_ref": qualified(source.source_id, source.task_ref),
                "command_id": command_id, "body": _data(body)}
    core.catalog.validate_instance(kind, category="object", instance=document)
    tx.prewrite(object_type=kind, logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(document), metadata=document,
                media_type="application/json", schema_ref="registry_v1/" + kind,
                producer_invocation_id=producer)
    return document


def read_record(core, reference):
    """Read an exact canonical record, never latest or provisional fallback."""
    ref = reference.to_dict() if isinstance(reference, SourceQualifiedVersionRef) else reference
    source = _source(core)
    if ref["source_id"] != source.source_id:
        raise RegistryConflict("record source differs from the selected Registry")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        document = exact_descriptor(db, core.object_store, core.task_id, ref["ref"])
    if document["record_ref"] != ref:
        raise RegistryConflict("record has an inconsistent self-reference")
    return document


@dataclass(frozen=True, slots=True)
class WorksetExpectation:
    record_ref: SourceQualifiedVersionRef
    stream_head: int
    command_id: str

    @classmethod
    def from_record(cls, core, document):
        return cls(SourceQualifiedVersionRef.from_dict(document["record_ref"], catalog=core.catalog),
                   document["body"]["sequence"], document["command_id"])

    def to_dict(self):
        return {"record_ref": self.record_ref.to_dict(), "stream_head": self.stream_head,
                "command_id": self.command_id}


class WorksetOwner:
    """Trusted local owner facade over the sole Registry writer."""
    def __init__(self, owner):
        self.owner = owner
        self.core = owner._core

    def _publish(self, kind, identity, body, command_id, *, logical_id=None):
        ref = record_ref(kind, self.core.task_id, identity, command_id)
        if logical_id is not None:
            ref = VersionRef(kind, logical_id, ref.version_id)
        tx = self.core.begin(idempotency_key=command_key(command_id))
        _stage(self.core, tx, kind, ref, body, command_id=command_id)
        tx.commit()
        return read_record(self.core, qualified(_source(self.core).source_id, ref))

    def create(self, *, requirements_ref, input_binding_ref, generation,
               expected_slots, command_id):
        slots = list(expected_slots)
        return self._publish(WORKSET, command_id, {
            "action": "create", "expected": None, "sequence": 1,
            "collection_version": 1, "generation": generation,
            "requirements_ref": requirements_ref.to_dict(),
            "input_binding_ref": input_binding_ref.to_dict(),
            "expected_slots": slots, "state": "open",
            "acceptances": {}, "contributions": {}, "terminal_ref": None,
            "required_child_seal_ref": None,
        }, command_id)

    def complete_normal_children(self, *, expected, outputs, output_port, command_id):
        """Explicit normal completion, or exact read-only replay of that command.

        The default RunOwner.succeed and CompleteWorkset remain unchanged.
        A replay does not repeat I/O, workspace planning, or owner edit advance.
        """
        from ..registry.operations import RegisteredOperationOutputsAuthority
        from ..registry.execution_child_closure import SEAL, PROFILE
        from .root_terminals import read_root_terminal_snapshot
        if (not isinstance(expected, WorksetExpectation)
                or not isinstance(outputs, RegisteredOperationOutputsAuthority)
                or not isinstance(output_port, str) or not output_port
                or not isinstance(command_id, str) or not command_id):
            raise TypeError("normal completion requires exact expectation/products/port/command")
        self.core.catalog.require(SEAL, category="object")
        self.core.catalog.require(NORMAL_ROOT_TERMINAL, category="object")
        reference = qualified(_source(self.core).source_id,
            record_ref(NORMAL_ROOT_TERMINAL, self.core.task_id, command_id, command_id))
        request = normal_completion_request(outputs, output_port)
        context = outputs.execution.operation.canonical.context
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            prior = db.execute("SELECT 1 FROM objects WHERE version_id=?", (reference["ref"]["version_id"],)).fetchone()
            if prior is not None:
                verified = read_root_terminal_snapshot(self.core, db, reference)
                root, seal = verified["root"], verified["seal"]
                parent = {"parent_invocation_ref": _ref_payload(context.invocation_ref),
                    "parent_business_firing_ref": _ref_payload(context.own_transition_firing_ref),
                    "parent_business_net_ref": _ref_payload(context.net_instance_ref),
                    "parent_business_checkpoint_ref": _ref_payload(context.admission_marking_checkpoint_ref)}
                if (root["command_id"] != command_id or root["body"]["expected"] != expected.to_dict()
                        or root["body"]["closure_profile"] != PROFILE
                        or root["body"]["completion_request"] != request
                        or any(seal[key] != value for key, value in parent.items())):
                    raise RegistryConflict("normal completion replay changed exact command material")
                return verified
        self.owner.succeed(outputs, command_id=command_id,
            workset_action=CompleteWorksetNormalChildren(expected, output_port))
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            return read_root_terminal_snapshot(self.core, db, reference)

    def change(self, expected, *, action, command_id, expected_slots=None):
        if action not in {"grow", "seal"}:
            raise ValueError("owner collection command must explicitly grow or seal")
        previous = read_record(self.core, expected.record_ref)
        body = _data(previous["body"])
        body.update(action=action, expected=expected.to_dict(), sequence=body["sequence"] + 1)
        if action == "grow":
            body.update(collection_version=body["collection_version"] + 1,
                        expected_slots=list(expected_slots), acceptances={}, contributions={})
        elif expected_slots is not None:
            raise ValueError("seal cannot silently change expected slots")
        else:
            body["state"] = "sealed"
        return self._publish(WORKSET, str(expected.record_ref.ref.entity_id), body, command_id,
                             logical_id=expected.record_ref.ref.entity_id)

    def request(self, *, requirements_ref, input_binding_ref, command_id):
        return self._publish(REQUEST, command_id, {
            "requirements_ref": requirements_ref.to_dict(),
            "input_binding_ref": input_binding_ref.to_dict()}, command_id)

    def logical_delivery(self, *, export_ref, target_workset, slot, command_id):
        target = target_workset["body"]
        return self._publish(DELIVERY, command_id, {
            "export_ref": export_ref.to_dict(),
            "target_owner_ref": target_workset["owner_task_ref"],
            "target_workset_id": target_workset["record_ref"]["ref"]["logical_id"],
            "target_source_id": target_workset["record_ref"]["source_id"],
            "collection_version": target["collection_version"],
            "generation": target["generation"],
            "requirements_ref": target["requirements_ref"],
            "input_binding_ref": target["input_binding_ref"], "slot": slot}, command_id)

    def lookup_acceptance(self, *, delivery_ref, target_workset, slot, content_digest):
        """A retransmission retrieves the same A/O before starting new work."""
        key = acceptance_identity(delivery_ref.to_dict(), target_workset, slot)
        ref = record_ref(ACCEPTANCE, self.core.task_id, key, key)
        rows = [row for row in self.core.event_store.canonical_object_rows(object_type=ACCEPTANCE)
                if row["logical_id"] == str(ref.entity_id)]
        if not rows:
            return None
        if len(rows) != 1:
            raise RegistryConflict("logical acceptance has multiple canonical versions")
        ref = VersionRef(ACCEPTANCE, ref.entity_id, TypedId.parse(rows[0]["version_id"]))
        document = read_record(self.core, qualified(_source(self.core).source_id, ref))
        if document["body"]["content_digest"] != content_digest:
            raise RegistryConflict("logical delivery identity was reused with different content")
        return document

    def reconcile(self, *, logical_delivery_ref, physical_delivery_ref,
                  acceptance_ref, original_outcome, command_id):
        """Append later target evidence without changing a physical terminal."""
        return self._publish(RECONCILIATION, command_id, {
            "logical_delivery_ref": logical_delivery_ref.to_dict(),
            "physical_delivery_ref": physical_delivery_ref.to_dict(),
            "acceptance_ref": acceptance_ref.to_dict(),
            "original_outcome": original_outcome}, command_id)

    def accept_delivery(self, attempt, *, expected, slot, decision, command_id,
                        outputs=None, output_port=None):
        """Accept once through ordinary Success, or return the exact prior A/O.

        A retry can look up the committed result before starting a new firing.
        A first submission must supply a real registered operation return; this
        facade does not create an execution, products, or extra occurrences.
        """
        if not isinstance(attempt, LocalDeliveryAttempt):
            raise TypeError("acceptance requires the explicit local delivery adapter result")
        scope = read_record(self.core, expected.record_ref)
        prior = self.lookup_acceptance(delivery_ref=attempt.logical_delivery_ref,
            target_workset=scope, slot=slot, content_digest=hashlib.sha256(attempt.payload).hexdigest())
        if prior is not None:
            if (outputs is not None and _ref_payload(outputs.execution.operation.canonical.context.invocation_ref)
                    != prior["body"]["invocation_ref"]):
                raise RegistryConflict("retrieve existing acceptance before starting another execution")
            return prior
        if outputs is None or output_port is None:
            raise RegistryConflict("first acceptance requires actual registered operation outputs")
        self.owner.succeed(outputs, command_id=command_id, workset_action=AcceptDelivery(
            expected, attempt.logical_delivery_ref, attempt.physical_delivery_ref, output_port, slot, decision))
        result = self.lookup_acceptance(delivery_ref=attempt.logical_delivery_ref,
            target_workset=scope, slot=slot, content_digest=hashlib.sha256(attempt.payload).hexdigest())
        if result is None:
            raise RegistryConflict("ordinary Success did not publish its exact acceptance")
        return result


def acceptance_identity(delivery, workset, slot):
    body = workset["body"]
    return {"logical_delivery_ref": delivery,
            "target_source_id": workset["record_ref"]["source_id"],
            "target_workset_id": workset["record_ref"]["ref"]["logical_id"],
            "collection_version": body["collection_version"],
            "input_binding_ref": body["input_binding_ref"], "slot": slot}


@dataclass(frozen=True, slots=True)
class ExportResult:
    request_ref: SourceQualifiedVersionRef
    output_port: str


@dataclass(frozen=True, slots=True)
class AcceptDelivery:
    expected: WorksetExpectation
    delivery_ref: SourceQualifiedVersionRef
    physical_delivery_ref: SourceQualifiedVersionRef
    output_port: str
    slot: str
    decision: str


@dataclass(frozen=True, slots=True)
class Contribute:
    expected: WorksetExpectation
    acceptance_ref: SourceQualifiedVersionRef
    output_port: str
    slot: str


@dataclass(frozen=True, slots=True)
class CompleteWorkset:
    expected: WorksetExpectation
    output_port: str


@dataclass(frozen=True, slots=True)
class CompleteWorksetNormalChildren:
    """Opt in to normal execution/v1 closure; callers never provide a seal."""
    expected: WorksetExpectation
    output_port: str


def normal_completion_request(outputs, output_port):
    bundle = [{"port_id": item.port_id, "output_binding_ref": _ref_payload(item.output_binding_ref),
               "resource_ref": _ref_payload(item.resource_ref.as_version_ref())} for item in outputs.outputs]
    bundle.sort(key=lambda item: (item["port_id"], item["resource_ref"]["version_id"], item["output_binding_ref"]["version_id"]))
    return {"selected_outcome_id": outputs.selected_outcome_id, "output_port": output_port, "output_bundle": bundle}


def bind_local_workset_source(target_owner, source_owner, *, source_id):
    """Explicit trusted-HOST selection of an in-process source, never a locator.

    Rebind on reopen. The object only enables validation of named immutable
    exports and real delivery facts; it is not an arbitrary query capability.
    """
    actual = _source(source_owner._core)
    if actual.source_id != source_id:
        raise RegistryConflict("selected source identity does not match its Registry")
    readers = getattr(target_owner._core.event_store, "_workset_local_sources", None)
    if readers is None:
        readers = {}
        target_owner._core.event_store._workset_local_sources = readers
    prior = readers.get(source_id)
    if prior is not None and prior is not source_owner._core:
        raise RegistryConflict("source identity is already attached to another Registry")
    readers[source_id] = source_owner._core


@dataclass(frozen=True, slots=True)
class LocalDeliveryAttempt:
    logical_delivery_ref: SourceQualifiedVersionRef
    physical_delivery_ref: SourceQualifiedVersionRef
    context: object
    release: object
    payload: bytes
    kernel: object
    command_id: str


def prepare_local_delivery(source_owner, execution, *, logical_delivery_ref, command_id):
    """Consume one original Registry release into this explicit local adapter."""
    from ..registry.resources import PrepareResourceDelivery, AuthorizeResourceRelease, ResourceVersionRef
    core = source_owner._core
    delivery = read_record(core, logical_delivery_ref)
    export = read_record(core, delivery["body"]["export_ref"])
    ref = _version_from_payload(export["body"]["output_resource_ref"])
    resource = ResourceVersionRef(ref.entity_id, ref.version_id)
    context = execution.operation.canonical.context
    cached = [attempt for attempt in getattr(core, "_workset_delivery_attempts", {}).values()
              if attempt.command_id == command_id]
    if cached:
        if (len(cached) != 1 or cached[0].logical_delivery_ref != logical_delivery_ref
                or cached[0].context.invocation_ref != context.invocation_ref):
            raise RegistryConflict("physical attempt command was reused with different material")
        return cached[0]
    kernel, _repository = source_owner.operation_repository()
    prepared = kernel.prepare_delivery(context, PrepareResourceDelivery(
        resource, context.operation_binding_ref, _stable_id("resource_delivery", core.task_id, command_id),
        command_id + ":prepare", "parent_receipt", canonical_text(logical_delivery_ref.to_dict())))
    release = kernel.authorize_release(context, AuthorizeResourceRelease(
        prepared.delivery_ref, "parent_receipt", _stable_id("release_nonce", core.task_id, command_id), command_id + ":release"))
    payload = kernel._consume_authorized_release(context, release)
    attempt = LocalDeliveryAttempt(logical_delivery_ref,
        SourceQualifiedVersionRef(logical_delivery_ref.source_id, release.delivery_ref),
        context, release, payload, kernel, command_id)
    attempts = getattr(core, "_workset_delivery_attempts", None)
    if attempts is None:
        attempts = {}
        core._workset_delivery_attempts = attempts
    attempts[str(release.delivery_ref.version_id)] = attempt
    return attempt


def finish_local_delivery(source_owner, attempt, *, outcome, command_id,
                          target_owner=None, acceptance_ref=None):
    """ACK only after exact target A/O is canonically committed; unknown stays terminal."""
    from ..registry.resources import AcknowledgeResourceDelivery
    if outcome not in {"acknowledged", "unknown"}:
        raise ValueError("consumed local attempts can be acknowledged or unknown")
    if outcome == "acknowledged":
        if target_owner is None or acceptance_ref is None:
            raise RegistryConflict("ACK requires the explicit target's committed acceptance")
        acceptance = read_record(target_owner._core, acceptance_ref)
        body = acceptance["body"]
        if (body["logical_delivery_ref"] != attempt.logical_delivery_ref.to_dict()
                or body["content_digest"] != hashlib.sha256(attempt.payload).hexdigest()):
            raise RegistryConflict("ACK target differs from this exact logical/physical delivery")
        logical = read_record(source_owner._core, attempt.logical_delivery_ref)["body"]
        if (acceptance["owner_task_ref"] != logical["target_owner_ref"]
                or any(body[key] != logical[key] for key in
                       ("target_source_id", "target_workset_id", "collection_version", "input_binding_ref", "slot"))):
            raise RegistryConflict("ACK acceptance belongs to another logical delivery target")
        evidence = canonical_text({"acceptance_ref": acceptance_ref.to_dict(),
                                   "physical_delivery_ref": attempt.physical_delivery_ref.to_dict()})
    else:
        evidence = "local adapter did not establish target acceptance"
    kernel = attempt.kernel
    if kernel._ResourceServiceKernel__core is not source_owner._core:
        raise RegistryConflict("physical attempt belongs to another source owner")
    boundary = kernel._record_boundary_receipt(attempt.context, attempt.release,
        outcome=outcome, positive_byte_count=len(attempt.payload) if outcome == "acknowledged" else None,
        consumer_evidence=evidence, idempotency_key=command_id + ":receipt")
    return kernel.acknowledge_delivery(attempt.context, AcknowledgeResourceDelivery(
        attempt.release.delivery_ref, boundary, outcome, command_id + ":terminal"))


def _output(core, outputs, new_tokens, port):
    # The caller names the full lowered output port, not a free-standing token.
    selected = [item for item in outputs.outputs if item.port_id == port]
    if len(selected) != 1:
        raise RegistryConflict("collaboration Success requires one exact registered output")
    output = selected[0]
    tokens = [ref for ref, state in new_tokens if state.resource_ref == output.resource_ref
              and state.place == output.place]
    if len(tokens) != 1:
        raise RegistryConflict("collaboration output must deposit one ordinary occurrence")
    return output, tokens[0]


def stage_workset_success(core, tx, *, action, outputs, new_tokens, publication,
                          material, command_id):
    """Stage records in the original Success transaction, after PN projection."""
    if type(action) not in {ExportResult, AcceptDelivery, Contribute, CompleteWorkset, CompleteWorksetNormalChildren}:
        raise TypeError("collaboration Success requires an explicit typed action")
    output, occurrence = _output(core, outputs, new_tokens, action.output_port)
    context = outputs.execution.operation.canonical.context
    source = _source(core)
    firing = outputs.execution.operation.firing.transition_firing_ref
    resource = output.resource_ref.as_version_ref()
    common = {"invocation_ref": _ref_payload(context.invocation_ref),
              "firing_ref": _ref_payload(firing),
              "completion_ref": _ref_payload(publication.completion_ref),
              "checkpoint_ref": _ref_payload(material.checkpoint_ref),
              "output_resource_ref": _ref_payload(resource),
              "occurrence_ref": _ref_payload(occurrence)}
    if isinstance(action, ExportResult):
        from ..registry.resource_service import _ResourceServiceKernel
        payload = _ResourceServiceKernel(core)._read_firing_registered(context, output.resource_ref)
        body = {**common, "request_ref": action.request_ref.to_dict(),
                "content_digest": hashlib.sha256(payload).hexdigest()}
        ref = record_ref(EXPORT, core.task_id, command_id, command_id)
        _stage(core, tx, EXPORT, ref, body, command_id=command_id,
               producer=context.invocation_ref.entity_id)
        return
    previous = read_record(core, action.expected.record_ref)
    body = _data(previous["body"])
    body.update(expected=action.expected.to_dict(), sequence=body["sequence"] + 1)
    if isinstance(action, AcceptDelivery):
        from ..registry.resource_service import _ResourceServiceKernel
        payload = _ResourceServiceKernel(core)._read_firing_registered(context, output.resource_ref)
        identity = acceptance_identity(action.delivery_ref.to_dict(), previous, action.slot)
        ref = record_ref(ACCEPTANCE, core.task_id, identity, command_id)
        record_body = {**common, **identity, "expected": action.expected.to_dict(),
                       "physical_delivery_ref": action.physical_delivery_ref.to_dict(),
                       "content_digest": hashlib.sha256(payload).hexdigest(),
                       "decision": action.decision}
        body["action"] = "accept"
        body["acceptances"][action.slot] = qualified(source.source_id, ref)
        kind = ACCEPTANCE
    elif isinstance(action, Contribute):
        ref = record_ref(CONTRIBUTION, core.task_id, command_id, command_id)
        record_body = {**common, "expected": action.expected.to_dict(),
                       "acceptance_ref": action.acceptance_ref.to_dict(), "slot": action.slot}
        body["action"] = "contribute"
        body["contributions"][action.slot] = qualified(source.source_id, ref)
        kind = CONTRIBUTION
    else:
        normal = type(action) is CompleteWorksetNormalChildren
        kind = NORMAL_ROOT_TERMINAL if normal else ROOT_TERMINAL
        ref = record_ref(kind, core.task_id, command_id, command_id)
        record_body = {**common, "expected": action.expected.to_dict(),
                       "contribution_refs": list(body["contributions"].values()),
                       "required_child_seal_ref": body["required_child_seal_ref"]}
        if normal:
            from ..registry.execution_child_closure import SEAL, PROFILE
            seal_ref = publication.execution_child_seal_ref
            if seal_ref is None or seal_ref.entity_type != SEAL:
                raise RegistryConflict("normal root requires the original Success producer's real seal")
            record_body.update(required_child_seal_ref=qualified(source.source_id, seal_ref),
                closure_profile=PROFILE, completion_request=normal_completion_request(outputs, action.output_port))
        body.update(action="complete", state="completed", terminal_ref=qualified(source.source_id, ref))
    _stage(core, tx, kind, ref, record_body, command_id=command_id,
           producer=context.invocation_ref.entity_id)
    next_ref = VersionRef(WORKSET, action.expected.record_ref.ref.entity_id,
                         record_ref(WORKSET, core.task_id, "unused", command_id).version_id)
    _stage(core, tx, WORKSET, next_ref, body, command_id=command_id,
           producer=context.invocation_ref.entity_id)
