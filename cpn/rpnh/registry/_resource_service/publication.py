"""Publication operations for the live Registry resource kernel."""

from __future__ import annotations
from cpn.rpnh.registry.publication import (_NO_CONTENT_SCHEMA_INSTANCE, _content_schema_instance, _content_schema_source_from_payload, _effective_operation_binding_metadata, _fresh_reference_resource_ref, _effective_firing_resource_access_events, _origin_payload, _fresh_reference_resource_metadata, _fresh_bootstrap_reference_resource_metadata, _append_direct_resource_version_publication, _ref_payload, _resource_from_payload, _stable_id, _version_from_payload)
from cpn.rpnh.resource_access import (ResourceReadContract)
import json
import os
import threading
import traceback
import uuid
from dataclasses import (asdict)
from datetime import (datetime, timedelta, timezone)
from pathlib import (Path, PurePath)
from typing import (Any, Iterable, Mapping, Sequence)
from jsonschema import (Draft7Validator)
from cpn.rpnh.registry._registry import (RegistryReadError, _RegistryCore)
from cpn.rpnh.registry.errors import (DeliveryAcknowledgementConflict, DeliveryOutcomeUnknown, MissingResourceAddress, PathPublicationFault, ReleaseAuthorizationDenied, ReleaseWitnessConsumed, ResourceAddressConflict, ResourceIdempotencyConflict, ResourceIntegrityFault, ResourcePayloadSchemaViolation, ResourceServiceError, ResourceSchemaViolation, StaleAuthorityHead, StaleInvocationContext, StaleQueryContext, StaleWriterFence, UnauthorizedResourceDelivery, UndeclaredPublicationOrigin, UnknownResourceVersion, UnmonitorableDeliveryBoundary, WrongNetOutputBinding)
from cpn.rpnh.registry.event_store import (FiringView, RegistryConflict, RegistryAuthorityView, StaleWriterError)
from cpn.rpnh.registry.content_schemas import (hydrate_registered_content_schema)
from cpn.rpnh.registry.identities import (TypedId, fresh_bootstrap_resource_id)
from cpn.rpnh.registry.invocations import (InvocationAdmissionError, InvocationContext, InvocationLifecycle)
from cpn.rpnh.registry.models import (PendingEvent, TypedRelation, VersionRef)
from cpn.rpnh.registry.operations import (RegisteredContentSchemaAuthority)
from cpn.rpnh.registry.resources import (AcknowledgeResourceDelivery, AddressBindingIntent, AuthorizedResourceRelease, AuthorizeResourceRelease, BindResourceAddress, CheckpointRepairOrigin, OpaqueBrokerChannelRef, PreparedResourceDelivery, PrepareResourceDelivery, PrivateSystemOrigin, PublishPathResource, PublicationOrigin, PublishResource, PublishResourceWithoutPayload, QueryCursor, RegistryHead, RegistryObserverContext, RelationQuery, RelationQueryResult, ResourceAddress, ResourceAddressBindingRef, ResourceAddressResult, ResourceDeliveryReceipt, ResourceHeader, ResourceQuery, ResourceQueryResult, ResourceRelation, ResourceVersionRef, SafeDescriptor, UnbindResourceAddress)
from cpn.rpnh.registry.schema_catalog import (canonical_json, canonical_text)
from .common import (
    _OBSERVER_SAFE_FIELDS,
    _fresh_checkpoint_repair_resource_metadata,
    _parse_timestamp,
    _resolve_registry_workspace_root,
    _resource_payload,
    _timestamp,
    _utc_now,
)

def _validate_descriptor(self, command: PublishResourceWithoutPayload) -> None:
    if not command.idempotency_key:
        raise ResourceSchemaViolation("resource idempotency key is required")
    if not command.media_type:
        raise ResourceSchemaViolation("resource media type is required")
    if not isinstance(command.summary, str):
        raise ResourceSchemaViolation("resource summary must be text")
    if not isinstance(command.extensions, Mapping):
        raise ResourceSchemaViolation("resource extensions must be an object")
    for key, value in command.descriptors.items():
        if not isinstance(key, str) or not key:
            raise ResourceSchemaViolation("descriptor names must be non-empty strings")
        values = value if isinstance(value, tuple) else (value,)
        if any(not isinstance(item, (str, int, float, bool, type(None))) for item in values):
            raise ResourceSchemaViolation("descriptor values must be JSON scalars")

def _validate_content(
        self, command: PublishResource, *,
        content_schema_authority: RegisteredContentSchemaAuthority | None = None,
        content_schema_source: VersionRef | ResourceVersionRef | None = None,
) -> RegisteredContentSchemaAuthority | None:
    if command.content_schema_ref is None:
        if (content_schema_authority is not None
                or command.content_schema_authority_ref is not None
                or content_schema_source is not None):
            raise ResourceSchemaViolation(
                "schema authority cannot accompany an untyped resource")
        return None
    sources = tuple(
        source for source in (
            command.content_schema_authority_ref,
            content_schema_source,
            (content_schema_authority.content_schema_ref
             if content_schema_authority is not None else None),
        ) if source is not None)
    if sources and any(source != sources[0] for source in sources[1:]):
        raise ResourceSchemaViolation(
            "content schema exact sources disagree")
    source = sources[0] if sources else None
    if source is None:
        if not command.content_schema_ref.startswith(("registry_v1/", "rpnh/", "petri/")):
            raise ResourceSchemaViolation(
                "application/runtime schema requires an exact source ref")
        rows = self._ResourceServiceKernel__core.event_store.object_rows_by_type(
            "registry_type_catalog/v1")
        if len(rows) != 1:
            raise ResourceSchemaViolation(
                "Registry schema requires one exact frozen catalog")
        source = VersionRef(
            "registry_type_catalog/v1",
            TypedId.parse(str(rows[0]["logical_id"]), expected="schema"),
            TypedId.parse(
                str(rows[0]["version_id"]),
                expected="resource_version"),
        )
    try:
        verified = hydrate_registered_content_schema(
            self._ResourceServiceKernel__core, source, schema_id=command.content_schema_ref,
            fresh_reader=(
                self._read_registered
                if isinstance(source, ResourceVersionRef)
                else None))
        if (content_schema_authority is not None
                and verified != content_schema_authority):
            raise ResourceSchemaViolation(
                "content schema authority differs from Registry state")
        if verified.catalog_ref is not None:
            schema_document = None
        else:
            assert verified.resource_ref is not None
            payload = self._read_registered(verified.resource_ref)
            decoded = json.loads(payload)
            if not isinstance(decoded, Mapping):
                raise TypeError("schema root is not an object")
            schema_document = decoded
    except ResourceSchemaViolation:
        raise
    except Exception as exc:
        raise ResourceSchemaViolation(
            "registered content schema cannot be hydrated exactly") from exc
    try:
        instance = _content_schema_instance(
            command.payload, media_type=command.media_type,
            decode_provider_backend=(
                command.content_schema_ref
                == "registry_v1/provider_backend_config/v1"))
        if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
            if verified.catalog_ref is not None:
                self._ResourceServiceKernel__core.catalog.validate_schema_ref(
                    verified.schema_id, instance)
            else:
                Draft7Validator(schema_document).validate(instance)
    except Exception as exc:
        raise ResourcePayloadSchemaViolation(
            f"resource payload violates content schema "
            f"{command.content_schema_ref!r}: {exc}") from exc
    return verified

def _binding_intent_payload(intent: AddressBindingIntent) -> dict[str, Any]:
    return {
        "address": {"scope_ref": _ref_payload(intent.address.scope_ref),
                    "opaque_name": intent.address.opaque_name},
        "authorization_ref": _ref_payload(intent.authorization_ref),
        "expected_binding_ref": (
            _ref_payload(intent.expected_binding_ref.as_version_ref())
            if intent.expected_binding_ref else None),
    }

def _publish(
        self, context: InvocationContext,
        command: PublishResource, *, transaction: Any | None = None,
        native_resume: bool = False,
) -> ResourceVersionRef:
    self._validate_descriptor(command)
    if not isinstance(command.payload, bytes):
        raise ResourceSchemaViolation("resource payload must be bytes")
    kind, primary, secondary, producer_ref = self._origin_authority(
        context, command.origin, native_resume=native_resume)
    schema_source: VersionRef | ResourceVersionRef | None = None
    if kind == "petri_output":
        output_binding = self._exact_object(
            primary, expected_type="output_binding/v1")
        schema_source = _content_schema_source_from_payload(
            output_binding.metadata.get("content_schema_ref"))
        if command.content_schema_ref != output_binding.metadata.get(
                "content_schema_id"):
            raise ResourceSchemaViolation(
                "Petri output schema differs from its exact output binding")
    content_schema_authority = self._validate_content(
        command, content_schema_source=schema_source)
    self._validate_publication_contract(context, command)
    round_ref = context.task_round_ref
    net_ref = context.net_instance_ref
    self._exact_object(command.lifetime_ref)
    if command.contributor_delegations:
        raise ResourceSchemaViolation(
            "current parent-owned publication cannot claim delegated contributors")
    return self._publish_fresh_reference(
        context, command, kind=kind, primary=primary,
        secondary=secondary, producer_ref=producer_ref,
        content_schema_authority=content_schema_authority,
        contributors=(), transaction=transaction,
        native_resume=native_resume)

def _validate_publication_contract(
        self, context: InvocationContext,
        command: PublishResource) -> None:
    """Enforce registered mechanical publication requirements, not prose."""
    if command.tool_evidence_refs:
        raise ResourceSchemaViolation(
            "LLM-authored documents cannot declare framework provenance")
    binding = self._exact_object(context.operation_binding_ref,
                                 expected_type="operation_binding/v1")
    spec = self._exact_object(_version_from_payload(binding.metadata["operation_spec_ref"]),
                              expected_type="operation_spec/v1")
    minimum = spec.metadata["implementation_contracts"].get(
        "resource_minimum_bytes", {}).get(command.content_schema_ref, 0)
    if len(command.payload) < minimum:
        raise ResourceSchemaViolation("resource is smaller than its registered publication contract")

def _publish_fresh_reference(
        self, context: InvocationContext, command: PublishResource, *,
        kind: str, primary: VersionRef, secondary: VersionRef | None,
        producer_ref: VersionRef,
        content_schema_authority: RegisteredContentSchemaAuthority | None,
        contributors: Sequence[VersionRef], transaction: Any | None,
        native_resume: bool = False,
) -> ResourceVersionRef:
    """Publish one fresh application resource from exact Registry refs."""

    def visible_resource(candidate: ResourceVersionRef):
        # An invocation-owned publication is provisional until the whole
        # firing closes.  Replays, derivations, and the post-commit check
        # must therefore use that invocation's exact firing authority;
        # canonical-only validation would reject the firing's own member.
        if context.own_transition_firing_ref is not None:
            return self._firing_prepared(context, candidate)
        return self._prepared_reference(candidate)

    resource_lineage_suffix: tuple[object, ...] = ()
    if command.address_bindings:
        address_identities = tuple(sorted(({
                "scope_ref": _ref_payload(intent.address.scope_ref),
                "opaque_name": intent.address.opaque_name,
            } for intent in command.address_bindings), key=canonical_json))
        if len(address_identities) != len({
                canonical_json(value) for value in address_identities}):
            raise ResourceSchemaViolation(
                "resource address bindings contain duplicate identities")
        resource_lineage_suffix = (
            "address_set",
            canonical_json(address_identities).decode("utf-8"))
    ref = _fresh_reference_resource_ref(
        task_id=self._ResourceServiceKernel__core.task_id,
        task_round_ref=context.task_round_ref,
        net_instance_ref=context.net_instance_ref,
        kind=kind, primary=primary, secondary=secondary,
        idempotency_key=command.idempotency_key,
        invocation_ref=context.invocation_ref,
        operation_binding_ref=context.operation_binding_ref,
        resource_lineage_suffix=resource_lineage_suffix)
    resource_id = ref.resource_id
    version_id = ref.resource_version_id
    schema_source = None
    if content_schema_authority is not None:
        exact_schema_source = content_schema_authority.content_schema_ref
        schema_source = (
            _resource_payload(exact_schema_source)
            if isinstance(exact_schema_source, ResourceVersionRef)
            else _ref_payload(exact_schema_source))

    def metadata_factory(payload_size: int):
        return _fresh_reference_resource_metadata(
            self._ResourceServiceKernel__core, ref=ref, origin_kind=kind,
            primary=primary, secondary=secondary, context=context,
            producer_ref=producer_ref, lifetime_ref=command.lifetime_ref,
            payload_size=payload_size,
            media_type=command.media_type,
            content_schema_ref=command.content_schema_ref,
            content_schema_authority_ref=schema_source,
            summary=command.summary, descriptors=command.descriptors,
            extensions=command.extensions,
            derived_from=command.derived_from,
            tool_evidence_refs=command.tool_evidence_refs,
            contributors=contributors, supersedes=command.supersedes)

    existing = self._ResourceServiceKernel__core.event_store.object_row(
        ref.resource_version_id)
    if existing is not None:
        prepared = visible_resource(ref)
        expected = metadata_factory(prepared.size)
        stored = self._ResourceServiceKernel__core.object_store.read_registered(prepared)
        if (existing["logical_id"] != str(resource_id)
                or existing["object_type"] != "resource_version/v1"
                or stored != command.payload
                or dict(prepared.metadata) != expected):
            raise ResourceIdempotencyConflict(
                "fresh resource replay differs from exact publication refs")
        return ref
    for ancestor in command.derived_from:
        visible_resource(ancestor)
    if command.supersedes is not None:
        prior = visible_resource(command.supersedes)
        if prior.logical_id != resource_id:
            raise ResourceSchemaViolation(
                "supersedes must preserve the mechanically allocated lineage")
    for contributor in contributors:
        self._exact_object(contributor)
    owns_transaction = transaction is None
    tx = (self._ResourceServiceKernel__core.begin(
        idempotency_key=command.idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
          if transaction is None else transaction)
    _append_direct_resource_version_publication(
        tx, ref=ref, payload=command.payload,
        metadata_factory=metadata_factory, media_type=command.media_type,
        producer_ref=producer_ref,
        producer_invocation_id=context.invocation_ref.entity_id,
        relation_key=tx.idempotency_key,
        input_resources=command.derived_from,
        tool_evidence_refs=command.tool_evidence_refs,
        contributors=contributors, supersedes=command.supersedes)
    for intent in command.address_bindings:
        self._append_binding(
            context, tx, intent.address, ref, intent.authorization_ref,
            intent.expected_binding_ref,
            idempotency_key=tx.idempotency_key,
            resource_already_validated=True,
            native_resume=native_resume)
    if owns_transaction:
        try:
            tx.commit()
        except StaleWriterError as exc:
            raise StaleWriterFence(str(exc)) from exc
        except RegistryConflict as exc:
            raise ResourceIdempotencyConflict(str(exc)) from exc
        visible_resource(ref)
    return ref

def _publish_fresh_bootstrap_reference(
        self, task_ref: VersionRef, command: PublishResource, *,
        bootstrap_ref: VersionRef,
        content_schema_authority: RegisteredContentSchemaAuthority | None,
        transaction: Any | None,
) -> ResourceVersionRef:
    """Publish one fresh private resource without inventing an invocation."""

    resource_id = fresh_bootstrap_resource_id(
        self._ResourceServiceKernel__core.task_id, bootstrap_ref.version_id,
        command.idempotency_key)
    version_id = _stable_id(
        "resource_version", self._ResourceServiceKernel__core.task_id, command.idempotency_key,
        "fresh_bootstrap_reference", bootstrap_ref.version_id,
        command.lifetime_ref.version_id)
    ref = ResourceVersionRef(resource_id, version_id)
    schema_source = None
    if content_schema_authority is not None:
        exact_schema_source = content_schema_authority.content_schema_ref
        schema_source = (
            _resource_payload(exact_schema_source)
            if isinstance(exact_schema_source, ResourceVersionRef)
            else _ref_payload(exact_schema_source))

    def metadata_factory(payload_size: int):
        return _fresh_bootstrap_reference_resource_metadata(
            self._ResourceServiceKernel__core, ref=ref, task_ref=task_ref,
            bootstrap_ref=bootstrap_ref,
            lifetime_ref=command.lifetime_ref,
            payload_size=payload_size,
            media_type=command.media_type,
            content_schema_ref=command.content_schema_ref,
            content_schema_authority_ref=schema_source,
            summary=command.summary, descriptors=command.descriptors,
            extensions=command.extensions,
            derived_from=command.derived_from,
            supersedes=command.supersedes)

    existing = self._ResourceServiceKernel__core.event_store.object_row(version_id)
    if existing is not None:
        prepared = self._prepared_reference(ref)
        expected = metadata_factory(prepared.size)
        stored = self._read_registered(ref)
        if (existing["logical_id"] != str(resource_id)
                or existing["object_type"] != "resource_version/v1"
                or stored != command.payload
                or dict(prepared.metadata) != expected):
            raise ResourceIdempotencyConflict(
                "fresh bootstrap replay differs from exact publication refs")
        return ref
    for ancestor in command.derived_from:
        self._prepared_reference(ancestor)
    if command.supersedes is not None:
        prior = self._prepared_reference(command.supersedes)
        if prior.logical_id != resource_id:
            raise ResourceSchemaViolation(
                "private supersedes must preserve allocated lineage")
    owns_transaction = transaction is None
    tx = (self._ResourceServiceKernel__core.begin(idempotency_key=command.idempotency_key)
          if transaction is None else transaction)
    _append_direct_resource_version_publication(
        tx, ref=ref, payload=command.payload,
        metadata_factory=metadata_factory, media_type=command.media_type,
        producer_ref=bootstrap_ref, producer_invocation_id=None,
        relation_key=tx.idempotency_key,
        input_resources=command.derived_from,
        supersedes=command.supersedes)
    if owns_transaction:
        try:
            tx.commit()
        except StaleWriterError as exc:
            raise StaleWriterFence(str(exc)) from exc
        except RegistryConflict as exc:
            raise ResourceIdempotencyConflict(str(exc)) from exc
        self._prepared_reference(ref)
    return ref

def _publish_checkpoint_repair_reference(
        self, task_ref: VersionRef, round_ref: VersionRef,
        net_ref: VersionRef, command: PublishResource, *,
        repair_origin: CheckpointRepairOrigin,
        transaction: Any,
) -> ResourceVersionRef:
    """Publish one replacement owned by its atomic checkpoint repair."""

    if not isinstance(repair_origin, CheckpointRepairOrigin):
        raise TypeError("checkpoint repair publication requires typed origin")
    if command.origin != repair_origin:
        raise UndeclaredPublicationOrigin(
            "checkpoint repair command and publication origin differ")
    if (command.address_bindings or command.contributor_delegations
            or command.supersedes is not None):
        raise ResourceSchemaViolation(
            "checkpoint repair replacement cannot bind, contribute, or supersede")
    if (command.lifetime_ref != repair_origin.checkpoint_repair_ref
            or repair_origin.source_resource_ref
            not in command.derived_from):
        raise ResourceSchemaViolation(
            "checkpoint repair replacement lacks its exact source/lifetime")
    self._validate_descriptor(command)
    content_schema_authority = self._validate_content(command)
    self._exact_object(task_ref, expected_type="task/v1")
    self._exact_object(round_ref, expected_type="task_round/v1")
    self._exact_object(net_ref, expected_type="net_instance/v1")
    for ancestor in command.derived_from:
        self._prepared_reference(ancestor)

    repair_ref = repair_origin.checkpoint_repair_ref
    source_ref = repair_origin.source_resource_ref
    resource_id = _stable_id(
        "resource", self._ResourceServiceKernel__core.task_id, "checkpoint_repair",
        repair_ref.version_id, source_ref.resource_id,
        source_ref.resource_version_id)
    version_id = _stable_id(
        "resource_version", self._ResourceServiceKernel__core.task_id,
        transaction.idempotency_key, "checkpoint_repair_reference",
        repair_ref.version_id, source_ref.resource_version_id)
    ref = ResourceVersionRef(resource_id, version_id)
    schema_source = None
    if content_schema_authority is not None:
        exact_schema_source = content_schema_authority.content_schema_ref
        schema_source = (
            _resource_payload(exact_schema_source)
            if isinstance(exact_schema_source, ResourceVersionRef)
            else _ref_payload(exact_schema_source))

    def metadata_factory(payload_size: int):
        return _fresh_checkpoint_repair_resource_metadata(
            self._ResourceServiceKernel__core, ref=ref, task_ref=task_ref,
            round_ref=round_ref, net_ref=net_ref,
            repair_ref=repair_ref, source_ref=source_ref,
            payload_size=payload_size,
            media_type=command.media_type,
            content_schema_ref=command.content_schema_ref,
            content_schema_authority_ref=schema_source,
            summary=command.summary, descriptors=command.descriptors,
            extensions=command.extensions,
            derived_from=command.derived_from)

    existing = self._ResourceServiceKernel__core.event_store.object_row(version_id)
    if existing is not None:
        prepared = self._prepared_reference(ref)
        expected = metadata_factory(prepared.size)
        stored = self._read_registered(ref)
        if (existing["logical_id"] != str(resource_id)
                or existing["object_type"] != "resource_version/v1"
                or stored != command.payload
                or dict(prepared.metadata) != expected):
            raise ResourceIdempotencyConflict(
                "checkpoint repair resource replay differs")
        return ref
    _append_direct_resource_version_publication(
        transaction, ref=ref, payload=command.payload,
        metadata_factory=metadata_factory, media_type=command.media_type,
        producer_ref=repair_ref, producer_invocation_id=None,
        relation_key=transaction.idempotency_key,
        input_resources=command.derived_from)
    return ref

def _publish_bytes_in_transaction(
        self, context: InvocationContext, command: PublishResource,
        transaction: Any, *, native_resume: bool = False,
) -> ResourceVersionRef:
    """Append resource/address publication to an owning atomic command."""
    if isinstance(command.origin, PrivateSystemOrigin):
        self._revalidate_invocation(
            context, boundary="resource-publish",
            native_resume=native_resume)
        return publish_private_system(
            self, self._ResourceServiceKernel__core, context.task_ref, command,
            context=context, transaction=transaction,
            native_resume=native_resume)
    return self._publish(
        context, command, transaction=transaction,
        native_resume=native_resume)

def publish_bytes(
        self, context: InvocationContext,
        command: PublishResource) -> ResourceVersionRef:
    return self._publish(context, command)

def publish_path(
        self, context: InvocationContext,
        command: PublishPathResource) -> ResourceVersionRef:
    self._revalidate_invocation(context, boundary="resource-path-publish")
    source = self._exact_object(
        command.source_binding_ref, expected_type="workspace_binding/v1")
    binding = self._exact_object(
        context.operation_binding_ref, expected_type="operation_binding/v1")
    binding_metadata = _effective_operation_binding_metadata(
        self._ResourceServiceKernel__core, context.operation_binding_ref, binding.metadata)
    if (binding_metadata.get("workspace_binding_ref")
            != _ref_payload(command.source_binding_ref)):
        raise PathPublicationFault("source binding is not registered for this operation")
    relative = PurePath(command.source_relative_path)
    if relative.is_absolute() or not relative.parts or any(
            part in {"", os.curdir, os.pardir} for part in relative.parts):
        raise PathPublicationFault("source path must be a strict relative path")
    root = _resolve_registry_workspace_root(
        self._ResourceServiceKernel__core, source.metadata.get("allowed_root"))
    flags_dir = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags_file = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, flags_dir)
        descriptors.append(current)
        for part in relative.parts[:-1]:
            current = os.open(part, flags_dir, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(relative.parts[-1], flags_file, dir_fd=current)
        descriptors.append(file_descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
    except OSError as exc:
        raise PathPublicationFault("descriptor-relative path publication failed") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    resource = command.resource
    return self.publish_bytes(context, PublishResource(
        origin=resource.origin, media_type=resource.media_type,
        content_schema_ref=resource.content_schema_ref, summary=resource.summary,
        lifetime_ref=resource.lifetime_ref,
        content_schema_authority_ref=(
            resource.content_schema_authority_ref),
        address_bindings=resource.address_bindings,
        supersedes=resource.supersedes, derived_from=resource.derived_from,
        contributor_delegations=resource.contributor_delegations,
        descriptors=resource.descriptors, extensions=resource.extensions,
        idempotency_key=resource.idempotency_key, payload=payload))


def publish_private_system(
        self, core: _RegistryCore, task_ref: VersionRef,
        command: PublishResource, *,
        content_schema_authority: RegisteredContentSchemaAuthority | None = None,
        transaction: Any | None = None,
        context: InvocationContext | None = None,
        native_resume: bool = False,
        framework_firing_stage: bool = False,
) -> ResourceVersionRef:
    """Implement bootstrap publication against one exact live kernel."""
    self._validate_descriptor(command)
    content_schema_authority = self._validate_content(
        command, content_schema_authority=content_schema_authority)
    if not isinstance(command.origin, PrivateSystemOrigin):
        raise UndeclaredPublicationOrigin(
            "private publication requires a bootstrap-command origin")
    if command.address_bindings or command.contributor_delegations:
        raise ResourceSchemaViolation(
            "private bootstrap publication cannot mutate addresses or claim leaves")
    self._exact_object(task_ref, expected_type="task/v1")
    if task_ref.entity_id != core.task_id:
        raise ResourceSchemaViolation("private publication cannot cross tasks")
    self._exact_object(
        command.origin.bootstrap_command_ref,
        expected_type="bootstrap_command/v1")
    self._exact_object(command.lifetime_ref)
    primary = command.origin.bootstrap_command_ref
    if context is None:
        if framework_firing_stage:
            raise TypeError(
                "framework firing-stage publication requires an exact context")
        return self._publish_fresh_bootstrap_reference(
            task_ref, command, bootstrap_ref=primary,
            content_schema_authority=content_schema_authority,
            transaction=transaction)
    if framework_firing_stage:
        self._revalidate_invocation(
            context, boundary="framework-firing-stage", native_resume=True)
        firing_ref = context.own_transition_firing_ref
        publication = core.event_store.firing_publication_for_invocation(
            context.invocation_ref.version_id)
        if (native_resume or not isinstance(firing_ref, VersionRef)
                or publication is None
                or str(publication["state"]) != "PROVISIONAL"
                or str(publication["firing_version_id"])
                != str(firing_ref.version_id)
                or str(publication["invocation_version_id"])
                != str(context.invocation_ref.version_id)):
            raise StaleInvocationContext(
                "framework firing-stage publication lacks its exact open root")
    else:
        self._revalidate_invocation(
            context, boundary="resource-publish", native_resume=native_resume)
    if context.task_ref != task_ref:
        raise ResourceSchemaViolation(
            "current private publication owner belongs to another task")
    return self._publish_fresh_reference(
        context, command, kind="private_system", primary=primary,
        secondary=primary, producer_ref=primary,
        content_schema_authority=content_schema_authority,
        contributors=(), transaction=transaction,
        native_resume=native_resume)
