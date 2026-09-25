"""Immutable owner messages and request-boundary read resources.

Execution owns this port. Clients submit JSON commands over the local channel;
they neither create a Registry writer nor alter a submitted request.
"""
from __future__ import annotations

import json

from .registry.identities import TypedId, new_id
from .registry.models import VersionRef
from .registry.publication import _version_from_payload
from .registry.resource_service import _ResourceServiceKernel, _publish_private_system
from .registry.resources import (
    AcknowledgeResourceDelivery, AuthorizeResourceRelease, PrepareResourceDelivery,
    PrivateSystemOrigin, PublishResource, ResourceVersionRef,
)
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload

SCHEMA = "rpnh/owner_control/v1"


class OwnerControl:
    def __init__(self, owner):
        self.owner = owner
        self.pending_edit = None
        from .owner_edits import OwnerEdits
        self.edits = OwnerEdits(owner)

    def edit(self, arguments, command_id):
        return self.edits.submit(arguments, command_id)

    def publish(self, kind, command_id, target, data, *, lineage=(), transaction=None, suffix=""):
        owner = self.owner
        body = {"kind": kind, "command_id": command_id, "source": ref_payload(owner.principal_ref),
                "target": target, "data": data, "lineage": [ref_payload(ref) for ref in lineage]}
        owner._core.catalog.validate_schema_ref(SCHEMA, body)
        resource_lineage = tuple(ResourceVersionRef(ref.entity_id, ref.version_id)
                                 for ref in lineage if ref.entity_type == "resource_version/v1")
        return _publish_private_system(owner._core, owner.identity.task_ref, PublishResource(
            origin=PrivateSystemOrigin(owner.bootstrap_ref), payload=canonical_json(body),
            media_type="application/json", content_schema_ref=SCHEMA,
            summary=f"Owner {kind}", lifetime_ref=owner.bootstrap_ref,
            derived_from=resource_lineage,
            descriptors={"owner_control_kind": kind, "command_id": command_id},
            idempotency_key=f"owner:{command_id}:{kind}:{suffix}"), transaction=transaction)

    def records(self, kind):
        result = []
        core = self.owner._core
        for row in core.event_store.canonical_object_rows(object_type="resource_version/v1"):
            metadata = json.loads(row["metadata_json"])
            if metadata.get("content_schema_ref") != SCHEMA:
                continue
            prepared = core.get_version(row["version_id"])
            body = json.loads(core.object_store.read_registered(prepared))
            core.catalog.validate_schema_ref(SCHEMA, body)
            if (body["kind"] == kind and body["source"] == ref_payload(self.owner.principal_ref)
                    and metadata["task_ref"] == ref_payload(self.owner.identity.task_ref)):
                result.append((ResourceVersionRef(prepared.logical_id, prepared.version_id), body))
        return result

    def receiver(self, target):
        from .registry.module_runtime import hydrate_module_runtime
        _executable, structure, _marking = hydrate_module_runtime(self.owner._core)
        transition = next((item for item in structure.compiled.symbolic.transitions if item.name == target), None)
        if transition is None:
            return "TARGET_DELETED"
        operation = next(item for item in structure.compiled.operations
                         if item.declaration.name == transition.operation)
        if (operation.declaration.request_port is None
                and operation.executor_declaration["contracts"].get("owner_messages") is not True):
            return "TARGET_UNRECEIVABLE"
        return None

    def message(self, arguments, command_id):
        if set(arguments) - {"target", "body", "input_revision"}:
            raise ValueError("message carries undeclared arguments")
        target, text = arguments["target"], arguments["body"]
        if not isinstance(target, str) or not target or not isinstance(text, str):
            raise TypeError("owner message requires a symbolic target and text")
        existing = [(ref, body) for ref, body in self.records("message") if body["command_id"] == command_id]
        data = {"body": text}
        if "input_revision" in arguments:
            data["input_revision"] = arguments["input_revision"]
        if existing:
            ref, previous = existing[0]
            if len(existing) != 1 or previous["target"] != target or previous["data"] != data:
                raise ValueError("owner command id was reused for different message data")
        else:
            revision = None
            if "input_revision" in arguments:
                revision = self.publish_input(arguments["input_revision"],
                    command_id=f"{command_id}:input", derived_from=(self.current_input(),
                        self.owner.original_input_ref))
            ref = self.publish("message", command_id, target, data,
                lineage=(self.owner.original_input_ref.as_version_ref(),))
            if revision is not None:
                self.publish("input_revision", command_id, target,
                    {"input_authority_ref": ref_payload(revision.as_version_ref()),
                     "message_ref": ref_payload(ref.as_version_ref())},
                    lineage=(ref.as_version_ref(), revision.as_version_ref(),
                             self.owner.original_input_ref.as_version_ref()))
                self.owner.current_input_ref = revision
        reason = self.receiver(target)
        status = reason or "QUEUED"
        result = self.publish("result", command_id, target,
            {"status": status, "message_ref": ref_payload(ref.as_version_ref())},
            lineage=(ref.as_version_ref(),), suffix="submission")
        return {"status": status, "message_ref": ref_payload(ref.as_version_ref()),
                "result_ref": ref_payload(result.as_version_ref())}

    def publish_input(self, declaration, *, command_id, derived_from=()):
        """Explicit JSON owner input, through normal registered schema authority."""
        if (not isinstance(declaration, dict)
                or set(declaration) != {"schema_id", "value", "summary"}
                or not isinstance(declaration["schema_id"], str)
                or not isinstance(declaration["summary"], str)):
            raise ValueError("owner input requires schema_id, JSON value and summary")
        owner = self.owner
        schema_id = declaration["schema_id"]
        schema = owner.registration.declaration("schema", schema_id)["schema"]
        from jsonschema import Draft7Validator
        from .registry.schema_catalog import PROTECTED_SCHEMA_REFS
        Draft7Validator(schema).validate(declaration["value"])
        lineage = tuple(dict.fromkeys(derived_from))
        return _publish_private_system(owner._core, owner.identity.task_ref, PublishResource(
            origin=PrivateSystemOrigin(owner.bootstrap_ref), payload=canonical_json(declaration["value"]),
            media_type="application/json", content_schema_ref=schema_id,
            content_schema_authority_ref=(None if schema_id in PROTECTED_SCHEMA_REFS
                else owner.schema_gateway.schema_refs[schema_id]),
            summary=declaration["summary"], lifetime_ref=owner.bootstrap_ref,
            derived_from=lineage, descriptors={"owner_input_command_id": command_id},
            idempotency_key=command_id))

    def current_input(self):
        """Prospective authority is derived from immutable registered revisions."""
        revisions = self.records("input_revision")
        if not revisions:
            return self.owner.original_input_ref
        ref = _version_from_payload(revisions[-1][1]["data"]["input_authority_ref"])
        return ResourceVersionRef(ref.entity_id, ref.version_id)

    def pending_messages(self):
        """Read-only exact receipts; deleted targets are visible, never rerouted."""
        delivered = {body["data"].get("message_ref", {}).get("version_id")
                     for _ref, body in self.records("receipt") if body["data"].get("status") == "DELIVERED"}
        return [{"message_ref": ref_payload(ref.as_version_ref()), "target": body["target"],
                 "status": self.receiver(body["target"]) or "QUEUED"}
                for ref, body in self.records("message") if str(ref.resource_version_id) not in delivered]

    def deliver(self, context, kernel, resource, key):
        """Ordinary read-grant extension, release and acknowledged exact bytes."""
        core = self.owner._core
        grant = core.capabilities.issue(target_invocation_id=context.invocation_ref.entity_id,
            operation_binding_ref=context.operation_binding_ref, resource_version_ids=(resource.resource_version_id,),
            revocation_condition="invocation closes", idempotency_key=f"{key}:grant")
        row = core._logical_object(grant, "capability_grant/v1")
        grant_ref = VersionRef("capability_grant/v1", grant, TypedId.parse(row["version_id"], expected="grant"))
        core.capabilities.transition(grant, "activate", payload={}, idempotency_key=f"{key}:activate")
        core.capabilities.transition(grant, "allow", payload={}, idempotency_key=f"{key}:allow")
        prepared = kernel.prepare_delivery(context, PrepareResourceDelivery(resource, grant_ref,
            new_id("resource_delivery"), f"{key}:prepare", "parent_receipt", "owner request input"))
        release = kernel.authorize_release(context, AuthorizeResourceRelease(prepared.delivery_ref,
            "parent_receipt", new_id("release_nonce"), f"{key}:release"))
        payload = kernel._consume_authorized_release(context, release)
        boundary = kernel._record_boundary_receipt(context, release, outcome="acknowledged",
            positive_byte_count=len(payload), consumer_evidence="request builder consumed registered owner input",
            idempotency_key=f"{key}:boundary")
        receipt = kernel.acknowledge_delivery(context, AcknowledgeResourceDelivery(release.delivery_ref,
            boundary, "acknowledged", f"{key}:acknowledge"))
        return {"resource_ref": ref_payload(resource.as_version_ref()), "grant_ref": ref_payload(grant_ref),
                "observed_read_event_id": str(receipt.observed_read_event_id)}, payload

    def request_boundary(self, context, request_id):
        """Select/deliver feedback once BEFORE assembling this new request.

        The immutable boundary resource is the request's input selection. An
        existing request id returns its old selection, never backfilled text.
        Caller publishes its normal request using these ordinary read receipts.
        """
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("new request identity is required")
        owner, core = self.owner, self.owner._core
        kernel = _ResourceServiceKernel(core)
        from .registry.firing_authority import canonical_invocation
        canonical = canonical_invocation(core, kernel, context.invocation_ref)
        if canonical.context != context:
            raise ValueError("request boundary context differs from exact Registry invocation")
        context = canonical.context
        from .registry.firing_authority import verify_transition_firing
        firing = verify_transition_firing(core, kernel, canonical)
        key = f"request:{context.invocation_ref.version_id}:{request_id}"
        previous = [(ref, body) for ref, body in self.records("receipt")
                    if body["command_id"] == key and body["data"].get("status") == "REQUEST_INPUTS"]
        if previous:
            if len(previous) != 1:
                raise ValueError("request input boundary has conflicting evidence")
            return previous[0][1]["data"]
        reason = self.receiver(firing.transition_id)
        delivered = {body["data"].get("message_ref", {}).get("version_id")
                     for _ref, body in self.records("receipt")
                     if body["data"].get("status") == "DELIVERED"}
        inputs, messages, failures = [], [], []
        for ref, body in self.records("message"):
            if body["target"] != firing.transition_id or str(ref.resource_version_id) in delivered:
                continue
            if reason:
                failures.append({"message_ref": ref_payload(ref.as_version_ref()), "reason": reason})
                continue
            prefix = f"{key}:{ref.resource_version_id}"
            selected, payload = self.deliver(context, kernel, ref, prefix)
            message_data = json.loads(payload)
            inputs.append(selected)
            messages.append(message_data["data"]["body"])
            self.publish("receipt", f"{prefix}:delivered", firing.transition_id,
                {"status": "DELIVERED", "message_ref": ref_payload(ref.as_version_ref()),
                 "request_id": request_id, "invocation_ref": ref_payload(context.invocation_ref),
                 "observed_read_event_id": selected["observed_read_event_id"]}, lineage=(ref.as_version_ref(),))
        current_input = self.current_input()
        if current_input != owner.original_input_ref:
            selected, _payload = self.deliver(context, kernel, current_input, f"{key}:input-authority")
            inputs.append(selected)
        result = {"status": "REQUEST_INPUTS", "request_id": request_id,
                  "invocation_ref": ref_payload(context.invocation_ref), "resources": inputs,
                  "messages": messages, "undeliverable": failures,
                  "input_authority_ref": ref_payload(current_input.as_version_ref())}
        self.publish("receipt", key, firing.transition_id, result,
                     lineage=(owner.original_input_ref.as_version_ref(),))
        return result


__all__ = ("OwnerControl",)
