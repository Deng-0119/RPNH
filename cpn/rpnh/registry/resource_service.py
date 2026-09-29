"""Execution-owner Registry resource-service compatibility facade.

The kernel remains the sole live-state owner. Functional components receive
that exact kernel and no workflow/component module is imported.
"""
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

from cpn.rpnh.registry._resource_service import addresses as _addresses
from cpn.rpnh.registry._resource_service import delivery as _delivery
from cpn.rpnh.registry._resource_service import publication as _publication
from cpn.rpnh.registry._resource_service import queries as _queries
from cpn.rpnh.registry._resource_service import references as _references
from cpn.rpnh.registry._resource_service.common import (
    _OBSERVER_SAFE_FIELDS,
    _fresh_checkpoint_repair_resource_metadata,
    _parse_timestamp,
    _resolve_registry_workspace_root,
    _resource_payload,
    _timestamp,
    _utc_now,
)

class _ResourceServiceKernel:
    """Registry-owned implementation behind the typed application surface."""

    __slots__ = (
        "__core", "__release_lock", "__release_channels",
        "__current_head_cache")

    def __init__(self, core: _RegistryCore) -> None:
        if not isinstance(core, _RegistryCore):
            raise TypeError("ResourceService requires the native registry coordinator")
        self.__core = core
        self.__release_lock = threading.RLock()
        self.__release_channels: dict[str, dict[str, Any]] = {}
        self.__current_head_cache: RegistryHead | None = None

    def _head(self, *, ordinal: int | None = None) -> RegistryHead:
        return _queries._head(**locals())

    def _historical_heads(
            self, ordinals: Iterable[int],
    ) -> Mapping[int, RegistryHead]:
        return _queries._historical_heads(**locals())

    def _canonical_heads(
            self, ordinals: Iterable[int | None],
    ) -> Mapping[int, RegistryHead]:
        return _queries._canonical_heads(**locals())

    def _canonical_head(self, *, ordinal: int | None = None) -> RegistryHead:
        return _queries._canonical_head(**locals())

    def head(
            self, context: InvocationContext | RegistryObserverContext, *,
            ordinal: int | None = None) -> RegistryHead:
        return _queries.head(**locals())

    def _prepared(self, ref: ResourceVersionRef):
        return _references._prepared(**locals())

    def _canonical_prepared(
            self, ref: ResourceVersionRef, *, through_ordinal: int):
        return _references._canonical_prepared(**locals())

    def _firing_prepared(
            self, context: InvocationContext, ref: ResourceVersionRef, *,
            view: FiringView | None = None):
        return _references._firing_prepared(**locals())

    def _agent_response_prepared(
            self, invocation_ref: VersionRef, response_ref: ResourceVersionRef,
            *, require_current_writer: bool):
        return _references._agent_response_prepared(**locals())

    def _read_firing_registered(
            self, context: InvocationContext,
            ref: ResourceVersionRef, *, view: FiringView | None = None,
            prepared: Any = None) -> bytes:
        return _references._read_firing_registered(**locals())

    def _prepared_reference(
            self, ref: ResourceVersionRef, *, prepared: Any = None,
            view: RegistryAuthorityView | None = None):
        return _references._prepared_reference(**locals())

    def _read_registered(self, ref: ResourceVersionRef) -> bytes:
        return _references._read_registered(**locals())

    def _prepared_fresh_bootstrap_reference(
            self, ref: ResourceVersionRef, *, prepared: Any,
            direct: Mapping[str, Any],
            view: RegistryAuthorityView):
        return _references._prepared_fresh_bootstrap_reference(**locals())

    def _prepared_checkpoint_repair_reference(
            self, ref: ResourceVersionRef, *, prepared: Any,
            direct: Mapping[str, Any],
            view: RegistryAuthorityView):
        return _references._prepared_checkpoint_repair_reference(**locals())

    def _prepared_fresh_reference(
            self, ref: ResourceVersionRef, *, prepared: Any,
            direct: Mapping[str, Any],
            view: RegistryAuthorityView):
        return _references._prepared_fresh_reference(**locals())

    def _exact_object(self, ref: VersionRef, *, expected_type: str | None = None):
        return _references._exact_object(**locals())

    def _exact_object_for_view(
            self, view: RegistryAuthorityView, ref: VersionRef, *,
            expected_type: str | None = None):
        return _references._exact_object_for_view(**locals())

    def _revalidate_invocation(
            self, context: InvocationContext, *, boundary: str,
            native_resume: bool = False) -> None:
        return _references._revalidate_invocation(**locals())

    def _authorization_context(self, context: InvocationContext) -> InvocationContext:
        return _references._authorization_context(**locals())

    @staticmethod
    def _ref_in(values: Iterable[Mapping[str, Any]], ref: VersionRef) -> bool:
        return _references._ref_in(**locals())

    def _grant_allows(self, context: InvocationContext, authorization_ref: VersionRef,
                      resource_ref: ResourceVersionRef) -> bool:
        return _references._grant_allows(**locals())

    def _claimed_petri_input_allows(
            self, context: InvocationContext, authorization_ref: VersionRef,
            resource_ref: ResourceVersionRef) -> bool:
        return _references._claimed_petri_input_allows(**locals())

    def _live_firing_resource_extension_allows(
            self, context: InvocationContext,
            authorization_ref: VersionRef,
            resource_ref: ResourceVersionRef) -> bool:
        return _references._live_firing_resource_extension_allows(**locals())

    def _binding_allows(self, context: InvocationContext, authorization_ref: VersionRef,
                        resource_ref: ResourceVersionRef, *, metadata_only: bool) -> bool:
        return _references._binding_allows(**locals())

    def _authorize_resource(self, context: InvocationContext,
                            authorization_ref: VersionRef,
                            resource_ref: ResourceVersionRef, *, metadata_only: bool,
                            native_resume: bool = False) -> None:
        return _references._authorize_resource(**locals())

    def _origin_authority(
            self, context: InvocationContext,
            origin: PublicationOrigin, *, native_resume: bool = False,
    ) -> tuple[str, VersionRef, VersionRef | None, VersionRef]:
        return _references._origin_authority(**locals())

    def _validate_descriptor(self, command: PublishResourceWithoutPayload) -> None:
        return _publication._validate_descriptor(**locals())

    def _validate_content(
            self, command: PublishResource, *,
            content_schema_authority: RegisteredContentSchemaAuthority | None = None,
            content_schema_source: VersionRef | ResourceVersionRef | None = None,
    ) -> RegisteredContentSchemaAuthority | None:
        return _publication._validate_content(**locals())

    @staticmethod
    def _binding_intent_payload(intent: AddressBindingIntent) -> dict[str, Any]:
        return _publication._binding_intent_payload(**locals())

    def _publish(
            self, context: InvocationContext,
            command: PublishResource, *, transaction: Any | None = None,
            native_resume: bool = False,
    ) -> ResourceVersionRef:
        return _publication._publish(**locals())

    def _validate_publication_contract(
            self, context: InvocationContext,
            command: PublishResource) -> None:
        return _publication._validate_publication_contract(**locals())

    def _preflight_publish(
            self, context: InvocationContext,
            command: PublishResource, *, native_resume: bool = False,
    ) -> tuple[
            str, VersionRef, VersionRef | None, VersionRef,
            RegisteredContentSchemaAuthority | None,
    ]:
        return _publication._preflight_publish(**locals())

    def _publish_fresh_reference(
            self, context: InvocationContext, command: PublishResource, *,
            kind: str, primary: VersionRef, secondary: VersionRef | None,
            producer_ref: VersionRef,
            content_schema_authority: RegisteredContentSchemaAuthority | None,
            contributors: Sequence[VersionRef], transaction: Any | None,
            native_resume: bool = False,
    ) -> ResourceVersionRef:
        return _publication._publish_fresh_reference(**locals())

    def _publish_fresh_bootstrap_reference(
            self, task_ref: VersionRef, command: PublishResource, *,
            bootstrap_ref: VersionRef,
            content_schema_authority: RegisteredContentSchemaAuthority | None,
            transaction: Any | None,
    ) -> ResourceVersionRef:
        return _publication._publish_fresh_bootstrap_reference(**locals())

    def _publish_checkpoint_repair_reference(
            self, task_ref: VersionRef, round_ref: VersionRef,
            net_ref: VersionRef, command: PublishResource, *,
            repair_origin: CheckpointRepairOrigin,
            transaction: Any,
    ) -> ResourceVersionRef:
        return _publication._publish_checkpoint_repair_reference(**locals())

    def _publish_bytes_in_transaction(
            self, context: InvocationContext, command: PublishResource,
            transaction: Any, *, native_resume: bool = False,
    ) -> ResourceVersionRef:
        return _publication._publish_bytes_in_transaction(**locals())

    def publish_bytes(
            self, context: InvocationContext,
            command: PublishResource) -> ResourceVersionRef:
        return _publication.publish_bytes(**locals())

    def publish_path(
            self, context: InvocationContext,
            command: PublishPathResource) -> ResourceVersionRef:
        return _publication.publish_path(**locals())

    @staticmethod
    def _address_key(address: ResourceAddress) -> str:
        return _addresses._address_key(**locals())

    def _address_scope_allowed(self, context: InvocationContext,
                               address: ResourceAddress,
                               authorization_ref: VersionRef, *,
                               native_resume: bool = False) -> None:
        return _addresses._address_scope_allowed(**locals())

    def _binding_events(
            self, address: ResourceAddress, *, through_ordinal: int,
            provisional_firing_ref: VersionRef | None = None) -> list[Any]:
        return _addresses._binding_events(**locals())

    def _current_binding(self, address: ResourceAddress, *, through_ordinal: int,
                         include_tombstone: bool,
                         provisional_firing_ref: VersionRef | None = None,
                         ) -> ResourceAddressBindingRef | None:
        return _addresses._current_binding(**locals())

    def _append_binding(
            self, context: InvocationContext, tx: Any, address: ResourceAddress,
            resource_ref: ResourceVersionRef, authorization_ref: VersionRef,
            expected_binding_ref: ResourceAddressBindingRef | None, *,
            idempotency_key: str, tombstone: bool = False,
            resource_already_validated: bool = False,
            native_resume: bool = False) -> ResourceAddressBindingRef:
        return _addresses._append_binding(**locals())

    def _binding_replay(
            self, address: ResourceAddress, resource_ref: ResourceVersionRef,
            authorization_ref: VersionRef,
            expected_binding_ref: ResourceAddressBindingRef | None, *,
            idempotency_key: str,
            tombstone: bool) -> ResourceAddressBindingRef | None:
        return _addresses._binding_replay(**locals())

    def bind_address(
            self, context: InvocationContext,
            command: BindResourceAddress, *,
            native_resume: bool = False,
            reference_only: bool = False) -> ResourceAddressBindingRef:
        return _addresses.bind_address(**locals())

    def bind_addresses(
            self, context: InvocationContext,
            commands: tuple[BindResourceAddress, ...], *,
            idempotency_key: str,
            native_resume: bool = False,
            reference_only: bool = False,
    ) -> tuple[ResourceAddressBindingRef, ...]:
        return _addresses.bind_addresses(**locals())

    def unbind_address(
            self, context: InvocationContext,
            command: UnbindResourceAddress) -> ResourceAddressBindingRef:
        return _addresses.unbind_address(**locals())

    def _observer_authority(
            self, context: RegistryObserverContext, *,
            required_fields: Iterable[str] = ()) -> VersionRef:
        return _queries._observer_authority(**locals())

    def _query_authority(
            self, context: InvocationContext | RegistryObserverContext,
            resource_ref: ResourceVersionRef | None = None, *,
            observer_fields: Iterable[str] = ()) -> tuple[VersionRef, VersionRef]:
        return _queries._query_authority(**locals())

    def _published_ordinal(self, version_id: TypedId) -> int:
        return _queries._published_ordinal(**locals())

    def _canonical_published_ordinal(
            self, version_id: TypedId, *, through_ordinal: int) -> int:
        return _queries._canonical_published_ordinal(**locals())

    def _header(self, ref: ResourceVersionRef, *, through_head: RegistryHead) -> ResourceHeader:
        return _queries._header(**locals())

    def _firing_header(
            self, context: InvocationContext, ref: ResourceVersionRef, *,
            view: FiringView | None = None, prepared: Any = None,
    ) -> ResourceHeader:
        return _queries._firing_header(**locals())

    def get_header(
            self, context: InvocationContext | RegistryObserverContext,
            ref: ResourceVersionRef) -> ResourceHeader:
        return _queries.get_header(**locals())

    def _visible_refs(
            self, context: InvocationContext | RegistryObserverContext,
            *, through_ordinal: int) -> set[ResourceVersionRef]:
        return _queries._visible_refs(**locals())

    def _authority_facts(
            self, context: InvocationContext | RegistryObserverContext,
    ) -> Mapping[str, Any]:
        return _queries._authority_facts(**locals())

    @staticmethod
    def _query_material(query: ResourceQuery) -> dict[str, Any]:
        return _queries._query_material(**locals())

    def _validate_query_head(self, head: RegistryHead) -> None:
        return _queries._validate_query_head(**locals())

    def _cursor_for(
            self, *, query_facts: Mapping[str, Any], principal_ref: VersionRef,
            grant_ref: VersionRef, through_head: RegistryHead, offset: int,
            authority_facts: Mapping[str, Any]) -> QueryCursor:
        return _queries._cursor_for(**locals())

    def _validate_cursor(
            self, cursor: QueryCursor, *, query_facts: Mapping[str, Any],
            principal_ref: VersionRef, grant_ref: VersionRef,
            authority_facts: Mapping[str, Any]) -> None:
        return _queries._validate_cursor(**locals())

    def query(
            self, context: InvocationContext | RegistryObserverContext,
            query: ResourceQuery) -> ResourceQueryResult:
        return _queries.query(**locals())

    def _resolve_address_unchecked(
            self, address: ResourceAddress,
            head: RegistryHead) -> ResourceAddressResult:
        return _addresses._resolve_address_unchecked(**locals())

    def resolve_address(
            self, context: InvocationContext | RegistryObserverContext,
            address: ResourceAddress, at_head: RegistryHead) -> ResourceAddressResult:
        return _addresses.resolve_address(**locals())

    def history(
            self, context: InvocationContext | RegistryObserverContext,
            resource_id: TypedId) -> tuple[ResourceVersionRef, ...]:
        return _queries.history(**locals())

    @staticmethod
    def _relation_endpoint(value: Mapping[str, Any]) -> ResourceVersionRef | VersionRef:
        return _queries._relation_endpoint(**locals())

    def relation_query(
            self, context: InvocationContext | RegistryObserverContext,
            query: RelationQuery) -> RelationQueryResult:
        return _queries.relation_query(**locals())

    def prepare_delivery(
            self, context: InvocationContext,
            command: PrepareResourceDelivery, *,
            native_resume: bool = False) -> PreparedResourceDelivery:
        return _delivery.prepare_delivery(**locals())

    def _delivery_events(self, delivery_id: TypedId) -> list[Any]:
        return _delivery._delivery_events(**locals())

    def _delivery_object(self, ref: VersionRef, *, state: str | None = None):
        return _delivery._delivery_object(**locals())

    def authorize_release(
            self, context: InvocationContext,
            command: AuthorizeResourceRelease, *,
            native_resume: bool = False) -> AuthorizedResourceRelease:
        return _delivery.authorize_release(**locals())

    def _consume_authorized_release(
            self, context: InvocationContext,
            release: AuthorizedResourceRelease) -> bytes:
        return _delivery._consume_authorized_release(**locals())

    def _record_boundary_receipt(
            self, context: InvocationContext,
            release: AuthorizedResourceRelease, *, outcome: str,
            positive_byte_count: int | None, consumer_evidence: str,
            idempotency_key: str) -> VersionRef:
        return _delivery._record_boundary_receipt(**locals())

    def acknowledge_delivery(
            self, context: InvocationContext,
            command: AcknowledgeResourceDelivery) -> ResourceDeliveryReceipt:
        return _delivery.acknowledge_delivery(**locals())

    def reconcile_incomplete_deliveries_on_resume(self) -> tuple[TypedId, ...]:
        return _delivery.reconcile_incomplete_deliveries_on_resume(**locals())

def _publish_private_system(
        core: _RegistryCore, task_ref: VersionRef,
        command: PublishResource, *,
        content_schema_authority: RegisteredContentSchemaAuthority | None = None,
        transaction: Any | None = None,
        context: InvocationContext | None = None,
        native_resume: bool = False,
        framework_firing_stage: bool = False,
) -> ResourceVersionRef:
    """Registry-owned bootstrap publication boundary; not an application API."""
    resources = _ResourceServiceKernel(core)
    return _publication.publish_private_system(
        resources, core, task_ref, command,
        content_schema_authority=content_schema_authority,
        transaction=transaction, context=context,
        native_resume=native_resume,
        framework_firing_stage=framework_firing_stage)
