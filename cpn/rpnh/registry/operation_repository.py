"""Sole owner-bound repository for exact operation authorities.

HOST supplies only bounded Registry/resource observations.  Workflow/module
protocols interpret their own products before presenting exact Petri bindings
and a declared outcome to this repository.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from jsonschema import Draft7Validator

from ._registry import _RegistryCore
from .resource_service import _ResourceServiceKernel
from .errors import ResourceIntegrityFault
from cpn.rpnh.executable_net import CompiledPetriNet, CompiledOperation, load_compiled_net
from .operation_output_contract import validate_compiled_output_bundle

from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operations import (
    _canonical_operation_fault_refs,
    FaultMechanicalTransitionRouteAuthority,
    FaultPetriPlaceAuthority,
    FaultPetriTransitionAuthority,
    FaultSubnetAuthority,
    OperationAuthorityError,
    OperationBindingAuthority,
    OperationFaultPartitionAuthority,
    OperationFaultRouteAuthority,
    OperationInputProjection,
    OperationPortAuthority,
    OperationOutputPortBindingAuthority,
    OperationSpecAuthority,
    RegisteredContentSchemaAuthority,
    RegisteredOperationAuthority,
    RegisteredOperationInputClaimAuthority,
    RegisteredOperationInputAuthority,
    RegisteredOperationOutputAuthority,
    RegisterOperationFault,
    _require_lexical_id,
)
from cpn.rpnh.registry.resources import (
    CanonicalInvocationAuthority,
    HistoricalPetriInputArtifact,
    PetriInputArtifact,
    ResourceDeliveryReceipt,
    PetriInputReceiptAuthority,
    RegistryHead,
    TransitionFiringAuthority,
    VerifiedResourceArtifact,
    ResourceVersionRef,
)
from cpn.rpnh.registry.strict_contracts import (
    _stable_id,
    _historical_build_fault_terminal_witness,
    _historical_build_operation_fault_closure,
)
from cpn.rpnh.registry.models import TypedRelation
from cpn.rpnh.registry.schema_catalog import canonical_json

@dataclass(frozen=True, slots=True)
class OperationInputResourceSubstitution:
    """Declared port substitution, anchored to the firing activation witness."""

    port_id: str
    resource_ref: ResourceVersionRef
    witness_member: str
    resource_schema_id: str

    def __post_init__(self) -> None:
        if (not isinstance(self.port_id, str) or not self.port_id
                or not isinstance(self.resource_ref, ResourceVersionRef)
                or not isinstance(self.witness_member, str)
                or not self.witness_member
                or not isinstance(self.resource_schema_id, str)
                or not self.resource_schema_id):
            raise OperationAuthorityError(
                "operation input substitution is malformed")


@dataclass(frozen=True, slots=True)
class OperationRepositoryHost:
    """Explicit trusted Python observations, bound to one Core/sole Kernel.

    Resource/receipt callbacks observe the owner's existing authority machinery;
    they cannot select ports or substitute for the local relation/ref checks.
    There are no serialized locators or runtime back-imports.
    """

    owner_core: _RegistryCore
    owner_kernel: _ResourceServiceKernel
    verify_resource: Callable[
        [CanonicalInvocationAuthority, ResourceVersionRef], VerifiedResourceArtifact]
    historical_input: Callable[
        [VersionRef, ResourceVersionRef], HistoricalPetriInputArtifact]
    historical_output: Callable[
        [CanonicalInvocationAuthority, ResourceVersionRef], VerifiedResourceArtifact]
    verify_input_receipt: Callable[
        [ResourceDeliveryReceipt, ResourceVersionRef], PetriInputReceiptAuthority]

    def __post_init__(self) -> None:
        if (not isinstance(self.owner_core, _RegistryCore)
                or not isinstance(self.owner_kernel, _ResourceServiceKernel)
                or any(not callable(getattr(self, name)) for name in (
                    "verify_resource", "historical_input", "historical_output",
                    "verify_input_receipt"))):
            raise TypeError("operation HOST requires explicit owner/observations")



def _ref_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _parse_ref(value: object, *, label: str) -> VersionRef:
    if not isinstance(value, Mapping) or set(value) != {
            "entity_type", "logical_id", "version_id"}:
        raise OperationAuthorityError(f"{label} is not one exact ref")
    from cpn.rpnh.registry.identities import TypedId
    try:
        return VersionRef(
            str(value["entity_type"]),
            TypedId.parse(str(value["logical_id"])),
            TypedId.parse(str(value["version_id"])),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise OperationAuthorityError(f"{label} is not one exact ref") from exc




@dataclass(frozen=True, slots=True)
class _InputRelationCandidate:
    """Phase-neutral facts for one semantic claimed resource."""

    token_ref: VersionRef
    resource_ref: ResourceVersionRef
    content_schema_id: str
    exact_port_ids: tuple[str, ...]
    statically_bound: bool
    semantic_token_refs: tuple[VersionRef, ...]
    lease_identity_ref: VersionRef | None = None
    source_resource_ref: ResourceVersionRef | None = None


@dataclass(frozen=True, slots=True)
class _InputRelationAssignment:
    candidate: _InputRelationCandidate
    port: OperationPortAuthority


def _input_candidate_sort_key(
        candidate: _InputRelationCandidate,
) -> tuple[str, str, str]:
    resource_ref = candidate.source_resource_ref or candidate.resource_ref
    return (
        str(resource_ref.resource_version_id),
        str(candidate.token_ref.entity_id),
        str(candidate.token_ref.version_id),
    )


def _solve_input_relation(
        *, candidates: tuple[_InputRelationCandidate, ...],
        ports: tuple[OperationPortAuthority, ...],
        static_excluded_port_ids: frozenset[str] = frozenset(),
) -> tuple[_InputRelationAssignment, ...]:
    """Solve the complete deterministic token/resource/port relation.

    This function consumes only immutable phase facts.  Planning and hydration
    gather those facts independently and both call this solver; neither phase
    treats the other phase's result as authority.
    """
    ordered_candidates = tuple(sorted(
        candidates, key=_input_candidate_sort_key))
    if len({item.token_ref for item in ordered_candidates}) != len(
            ordered_candidates):
        raise OperationAuthorityError(
            "operation input relation contains a duplicate claimed token")
    if len({port.port_id for port in ports}) != len(ports):
        raise OperationAuthorityError(
            "operation input relation contains a duplicate port identity")
    for candidate in ordered_candidates:
        if candidate.token_ref not in candidate.semantic_token_refs:
            raise OperationAuthorityError(
                "operation input resource is not carried by its exact "
                "semantic Petri token")

    ports_by_schema: dict[str, tuple[OperationPortAuthority, ...]] = {}
    for port in sorted(ports, key=lambda item: item.port_id):
        for schema_id in port.accepted_input_schema_ids:
            ports_by_schema.setdefault(schema_id, ())
            ports_by_schema[schema_id] += (port,)

    assigned: dict[_InputRelationCandidate, OperationPortAuthority] = {}
    residual_by_schema: dict[str, list[_InputRelationCandidate]] = {}

    # A binding-listed resource is not automatically a static lease token:
    # ordinary launch-time documents, including the initial Intake document,
    # are also fixed operation inputs and carry no lease identity.  Only a
    # schema with an explicitly declared static lease port enters the exact
    # identity relation below; ordinary inputs continue to use their actual
    # Petri place in the dynamic-place relation.
    for candidate in ordered_candidates:
        if not candidate.statically_bound:
            continue
        if candidate.lease_identity_ref is None:
            static_lease_ports = tuple(
                port for port in ports_by_schema[candidate.content_schema_id]
                if (port.port_id not in static_excluded_port_ids
                    and port.lease_identity_ref is not None))
            if static_lease_ports:
                raise OperationAuthorityError(
                    "statically bound operation input lacks exact lease identity")
            continue
        exact_ports = tuple(
            port for port in ports_by_schema[candidate.content_schema_id]
            if (port.port_id not in static_excluded_port_ids
                and port.lease_identity_ref == candidate.lease_identity_ref))
        if len(exact_ports) == 1:
            assigned[candidate] = exact_ports[0]
        else:
            raise OperationAuthorityError(
                "statically bound operation input has no unique explicit "
                "lease-identity/port authority")

    # Runtime dynamic authority uses only the token's actual Petri place.
    # Static lease-identity ports share the lease pool place and therefore are
    # never candidates for a dynamic token.  A binding-listed ordinary input
    # reaches this path only when its schema has no static lease port.
    for candidate in ordered_candidates:
        if (candidate.statically_bound
                and candidate.lease_identity_ref is not None):
            continue
        matching_ports = tuple(
            port for port in ports_by_schema[candidate.content_schema_id]
            if (port.lease_identity_ref is None
                and not (candidate.source_resource_ref is not None
                         and port.port_id in static_excluded_port_ids)))
        exact_ports = tuple(
            port for port in matching_ports
            if (port.port_id in candidate.exact_port_ids
                or port.place in candidate.exact_port_ids))
        if len(exact_ports) == 1:
            assigned[candidate] = exact_ports[0]
        elif len(exact_ports) > 1:
            raise OperationAuthorityError(
                "claimed operation input has an ambiguous exact "
                "schema/place port")
        else:
            residual_by_schema.setdefault(
                candidate.content_schema_id, []).append(candidate)

    counts = {
        port.port_id: sum(value == port for value in assigned.values())
        for port in ports
    }
    if any(counts[port.port_id] > port.maximum for port in ports):
        raise OperationAuthorityError(
            "exact operation input assignments exceed declared cardinality")

    for schema_id in sorted(residual_by_schema):
        residual = tuple(sorted(
            residual_by_schema[schema_id], key=_input_candidate_sort_key))
        schema_ports = ports_by_schema[schema_id]
        possible_ports: list[OperationPortAuthority] = []
        for candidate_port in schema_ports:
            projected = dict(counts)
            projected[candidate_port.port_id] += len(residual)
            if all(
                    port.minimum <= projected[port.port_id] <= port.maximum
                    for port in schema_ports):
                possible_ports.append(candidate_port)
        if all(counts[port.port_id] >= port.minimum
               for port in schema_ports):
            # Every required input already has exact place/port authority.  An
            # additional same-schema token that names no exact operation port
            # is transport/provenance, not permission to infer a byte-input
            # port from remaining optional capacity.
            continue
        if len(possible_ports) > 1:
            raise OperationAuthorityError(
                "residual same-schema operation inputs have no unique bounded "
                "one-port solution (ambiguous)")
        if not possible_ports:
            if all(counts[port.port_id] >= port.minimum
                   for port in schema_ports):
                # No declared port can accept the residual and every required
                # port is already exact.  It remains transport/provenance and
                # cannot be delivered as an operation byte input.
                continue
            raise OperationAuthorityError(
                "residual same-schema operation inputs have no unique bounded "
                "one-port solution (missing/excess)")
        selected = possible_ports[0]
        for candidate in residual:
            assigned[candidate] = selected
        counts[selected.port_id] += len(residual)

    for port in ports:
        count = counts[port.port_id]
        if count < port.minimum or count > port.maximum:
            assigned_candidates = tuple(
                {
                    "token_ref": _ref_payload(candidate.token_ref),
                    "resource_ref": _ref_payload(
                        candidate.resource_ref.as_version_ref()),
                    "place": port.place,
                    "content_schema_id": candidate.content_schema_id,
                }
                for candidate in ordered_candidates
                if assigned.get(candidate) == port
            )
            schema_port_mapping = tuple(
                (schema_id, tuple(
                    schema_port.port_id
                    for schema_port in ports_by_schema[schema_id]))
                for schema_id in sorted(ports_by_schema)
            )
            raise OperationAuthorityError(
                f"operation input port {port.port_id!r} violates declared "
                f"cardinality: expected minimum={port.minimum}, "
                f"maximum={port.maximum}, assigned={count}; "
                f"assigned_candidates={assigned_candidates!r}; "
                f"schema_port_mapping={schema_port_mapping!r}")

    return tuple(sorted(
        (_InputRelationAssignment(candidate, port)
         for candidate, port in assigned.items()),
        key=lambda item: (
            item.port.port_id,
            *_input_candidate_sort_key(item.candidate),
        ),
    ))


class RegistryOperationAuthorityRepository:
    """Internal exact-object adapter used by operation validator functions."""

    __slots__ = ("__core", "__kernel", "__host")

    def __init__(
            self, core: _RegistryCore, kernel: _ResourceServiceKernel, *,
            host: OperationRepositoryHost,
    ) -> None:
        if (not isinstance(core, _RegistryCore)
                or not isinstance(kernel, _ResourceServiceKernel)
                or not isinstance(host, OperationRepositoryHost)
                or host.owner_core is not core or host.owner_kernel is not kernel):
            raise TypeError("operation repository requires the exact HOST owner")
        self.__core = core
        self.__kernel = kernel
        self.__host = host

    def registry_head(self) -> RegistryHead:
        return self.__kernel._head()

    def exact_metadata(
            self, ref: VersionRef, *, expected_type: str) -> Mapping[str, Any]:
        if not isinstance(ref, VersionRef) or ref.entity_type != expected_type:
            raise OperationAuthorityError(
                f"expected one exact {expected_type} reference")
        exact = self.__kernel._exact_object(ref, expected_type=expected_type)
        metadata = dict(exact.metadata)
        self.__core.catalog.validate_instance(
            expected_type, category="object", instance=metadata)
        return metadata

    def require_registered_ref(self, ref: VersionRef) -> None:
        if not isinstance(ref, VersionRef):
            raise TypeError("operation dependency must be one exact VersionRef")
        self.__kernel._exact_object(ref, expected_type=ref.entity_type)

    def __substitution_resource_schema(
            self, firing: TransitionFiringAuthority,
            resource_ref: ResourceVersionRef,
    ) -> str | None:
        """Read a substitution through its exact current authority phase."""

        publication = self.__core.event_store.firing_publication_row(
            firing.transition_firing_ref.version_id)
        if publication is None or publication["state"] == "PUBLISHED":
            return self.__kernel._header(
                resource_ref,
                through_head=self.registry_head()).content_schema_ref
        if publication["state"] != "PROVISIONAL":
            raise OperationAuthorityError(
                "operation input substitution firing is not readable")
        view = self.__core.event_store.firing_view(
            firing_version_id=firing.transition_firing_ref.version_id,
            invocation_version_id=publication["invocation_version_id"],
        )
        prepared = self.__kernel._prepared_reference(
            resource_ref,
            view=view,
        )
        value = prepared.metadata.get("content_schema_ref")
        return value if isinstance(value, str) else None

    def registered_content_schema_authority(
            self, ref: VersionRef | ResourceVersionRef, *,
            schema_document_ref: VersionRef,
    ) -> RegisteredContentSchemaAuthority:
        authority = self.__core.verify_registered_content_schema_ref(
            ref, schema_document_ref=schema_document_ref)
        if (not isinstance(authority, RegisteredContentSchemaAuthority)
                or authority.content_schema_ref != ref):
            raise OperationAuthorityError(
                "content schema verifier returned another exact source")
        return authority

    def __reverify_input(
            self, canonical: CanonicalInvocationAuthority,
            artifact: PetriInputArtifact | HistoricalPetriInputArtifact,
    ) -> None:
        if not isinstance(
                artifact,
                (PetriInputArtifact, HistoricalPetriInputArtifact)):
            raise TypeError(
                "operation input must be live/historical Petri input")
        exact_ref = artifact.resource.header.ref
        if isinstance(artifact, HistoricalPetriInputArtifact):
            rebuilt = self.__host.historical_input(
                canonical.context.invocation_ref, exact_ref)
            normalized = replace(
                rebuilt,
                resource=replace(
                    rebuilt.resource,
                    header=replace(
                        rebuilt.resource.header,
                        relation_kinds=(
                            artifact.resource.header.relation_kinds),
                        published_at_head=(
                            artifact.resource.header.published_at_head)),
                    verified_at_head=artifact.resource.verified_at_head),
                receipt=replace(
                    rebuilt.receipt,
                    verified_at_head=artifact.receipt.verified_at_head),
            )
            if normalized != artifact:
                raise OperationAuthorityError(
                    "historical operation input differs from immutable "
                    "resource/schema/ack closure")
            return
        terminal = self.__kernel._exact_object(
            artifact.receipt.terminal_delivery_ref,
            expected_type="resource_delivery/v1")
        if terminal.metadata.get("context_ref") != _ref_payload(
                canonical.context.invocation_ref):
            raise OperationAuthorityError(
                "operation input delivery belongs to another invocation")
        verified_resource = self.__host.verify_resource(canonical, exact_ref)
        if replace(
                verified_resource,
                header=replace(
                    verified_resource.header,
                    relation_kinds=artifact.resource.header.relation_kinds,
                    published_at_head=artifact.resource.header.published_at_head),
                verified_at_head=artifact.resource.verified_at_head,
        ) != artifact.resource:
            raise OperationAuthorityError(
                "operation input resource differs from current exact closure")
        verified_receipt = self.__host.verify_input_receipt(
            ResourceDeliveryReceipt(
                delivery_ref=artifact.receipt.terminal_delivery_ref,
                terminal_event_id=artifact.receipt.terminal_event_id,
                outcome="acknowledged",
                observed_read_event_id=artifact.receipt.observed_read_event_id,
            ),
            exact_ref,
        )
        if replace(
                verified_receipt,
                verified_at_head=artifact.receipt.verified_at_head,
        ) != artifact.receipt:
            raise OperationAuthorityError(
                "operation input receipt differs from durable delivery closure")
        if (artifact.release.boundary != "petri_input"
                or artifact.release.exact_resource_ref != exact_ref
                or artifact.release.witness_ref != artifact.receipt.witness_ref):
            raise OperationAuthorityError(
                "operation input release differs from acknowledged receipt")

    @staticmethod
    def __resource_ref(value: object) -> ResourceVersionRef | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise OperationAuthorityError(
                "claimed Petri token resource is not one exact reference")
        from cpn.rpnh.registry.identities import TypedId
        try:
            return ResourceVersionRef(
                TypedId.parse(
                    str(value["resource_id"]), expected="resource"),
                TypedId.parse(
                    str(value["resource_version_id"]),
                    expected="resource_version"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationAuthorityError(
                "claimed Petri token resource is not one exact reference") from exc

    def __claimed_input_resource_closure(
            self, claimed_input_refs: tuple[VersionRef, ...],
    ) -> tuple[
            dict[VersionRef, Mapping[str, Any]],
            dict[VersionRef, set[ResourceVersionRef]],
            dict[ResourceVersionRef, list[VersionRef]],
    ]:
        token_metadata_by_ref = {
            token_ref: self.exact_metadata(
                token_ref, expected_type="petri_token/v1")
            for token_ref in claimed_input_refs}
        token_resources_by_ref: dict[
            VersionRef, set[ResourceVersionRef]] = {}
        for token_ref, token in token_metadata_by_ref.items():
            resources = {
                ref for ref in (
                    self.__resource_ref(token.get("resource_ref")),
                    self.__resource_ref(token.get("work_resource_ref")),
                )
                if ref is not None
            }
            token_resources_by_ref[token_ref] = resources
        ordinary_resources = {
            resource_ref
            for token_ref, resources in token_resources_by_ref.items()
            if token_metadata_by_ref[token_ref].get(
                "lease_identity_ref") is None
            for resource_ref in resources}
        claimed_resources: dict[ResourceVersionRef, list[VersionRef]] = {}
        for token_ref in claimed_input_refs:
            token = token_metadata_by_ref[token_ref]
            lease_identity = token.get("lease_identity_ref")
            if (isinstance(lease_identity, Mapping)
                    and token_resources_by_ref[token_ref].intersection(
                        ordinary_resources)):
                # A duplicate Scheme-B colour is lock authority; the ordinary
                # token remains the semantic resource claim.
                continue
            for resource_ref in token_resources_by_ref[token_ref]:
                claimed_resources.setdefault(resource_ref, []).append(token_ref)
        return (
            token_metadata_by_ref,
            token_resources_by_ref,
            claimed_resources,
        )

    @staticmethod
    def __input_ports_by_schema(
            spec: OperationSpecAuthority,
    ) -> dict[str, list[OperationPortAuthority]]:
        ports_by_schema: dict[str, list[OperationPortAuthority]] = {}
        for port in spec.input_ports:
            for schema_id in port.accepted_input_schema_ids:
                ports_by_schema.setdefault(schema_id, []).append(port)
        return ports_by_schema

    @staticmethod
    def __projection_value(value: object, path: str) -> object:
        current = value
        if not path:
            return current
        for part in path[1:].split("/"):
            if not isinstance(current, Mapping) or part not in current:
                raise OperationAuthorityError(
                    "operation input projection misses a declared producer path")
            current = current[part]
        return current

    @staticmethod
    def __projected_instance(
            producer: object, projection: OperationInputProjection,
    ) -> object:
        root_selected = tuple(
            item for item in projection.field_projection
            if item.consumer_path == "")
        if root_selected:
            if len(root_selected) != 1 or len(projection.field_projection) != 1:
                raise OperationAuthorityError(
                    "operation input root projection is ambiguous")
            return RegistryOperationAuthorityRepository.__projection_value(
                producer, root_selected[0].producer_path)
        projected: dict[str, object] = {}
        for field in projection.field_projection:
            source = RegistryOperationAuthorityRepository.__projection_value(
                producer, field.producer_path)
            destination = projected
            parts = field.consumer_path[1:].split("/")
            for part in parts[:-1]:
                existing = destination.get(part)
                if existing is None:
                    nested: dict[str, object] = {}
                    destination[part] = nested
                    destination = nested
                elif isinstance(existing, dict):
                    destination = existing
                else:
                    raise OperationAuthorityError(
                        "operation input consumer projection paths overlap")
            leaf = parts[-1]
            if leaf in destination:
                raise OperationAuthorityError(
                    "operation input consumer projection is ambiguous")
            destination[leaf] = source
        return projected

    def __consumer_schema_document(
            self, port: OperationPortAuthority,
    ) -> Mapping[str, object]:
        authority = port.content_schema
        try:
            if authority.catalog_ref is not None:
                catalog = self.__core.get_version(
                    authority.catalog_ref.version_id)
                bundle = json.loads(
                    self.__core.object_store.read_verified(catalog))
                schemas = bundle.get("schemas") \
                    if isinstance(bundle, Mapping) else None
                entry = schemas.get(authority.schema_id) \
                    if isinstance(schemas, Mapping) else None
                source = entry.get("source") \
                    if isinstance(entry, Mapping) else None
                if not isinstance(source, str):
                    raise TypeError("consumer schema is absent from catalog")
                document = json.loads(source)
            else:
                if authority.resource_ref is None:
                    raise TypeError("consumer schema has no exact source")
                prepared = self.__kernel._prepared(authority.resource_ref)
                document = json.loads(
                    self.__core.object_store.read_verified(prepared))
            if not isinstance(document, Mapping):
                raise TypeError("consumer schema root is not an object")
            Draft7Validator.check_schema(document)
            return document
        except OperationAuthorityError:
            raise
        except Exception as exc:
            raise OperationAuthorityError(
                "operation input consumer schema cannot be hydrated") from exc

    def __project_input_payload(
            self, *, port: OperationPortAuthority,
            artifact: PetriInputArtifact | HistoricalPetriInputArtifact,
    ) -> bytes | None:
        producer_schema_id = artifact.resource.header.content_schema_ref
        if not isinstance(producer_schema_id, str):
            raise OperationAuthorityError(
                "operation input lacks producer schema provenance")
        projection = port.input_projection_from(producer_schema_id)
        if projection is None:
            return None
        try:
            media_type = artifact.resource.header.media_type
            if media_type == "application/json":
                producer = json.loads(artifact.payload.decode("utf-8"))
            elif media_type.startswith("text/"):
                producer = artifact.payload.decode("utf-8")
            else:
                raise TypeError("producer carrier is not projectable")
            projected = self.__projected_instance(producer, projection)
            Draft7Validator(
                self.__consumer_schema_document(port)).validate(projected)
            return (projected.encode("utf-8")
                    if isinstance(projected, str)
                    else canonical_json(projected))
        except OperationAuthorityError:
            raise
        except Exception as exc:
            raise OperationAuthorityError(
                "operation input does not satisfy its declared consumer "
                "projection") from exc

    def __exact_token_port_ids(self, token: Mapping[str, Any]) -> tuple[str, ...]:
        token_place = token.get("place")
        return (token_place,) if isinstance(token_place, str) else ()

    def __resolve_input_relation(
            self, *, claimed_input_refs: tuple[VersionRef, ...],
            binding: OperationBindingAuthority,
            spec: OperationSpecAuthority,
            static_excluded_port_ids: frozenset[str] = frozenset(),
    ) -> tuple[_InputRelationAssignment, ...]:
        if len(set(claimed_input_refs)) != len(claimed_input_refs):
            raise OperationAuthorityError(
                "operation firing contains a duplicate claimed token")
        ports_by_schema = self.__input_ports_by_schema(spec)
        (
            token_metadata_by_ref,
            token_resources_by_ref,
            claimed_resources,
        ) = self.__claimed_input_resource_closure(claimed_input_refs)
        ordinary_resources = {
            resource_ref
            for token_ref, resources in token_resources_by_ref.items()
            if token_metadata_by_ref[token_ref].get(
                "lease_identity_ref") is None
            for resource_ref in resources}
        head = self.registry_head()
        candidates: list[_InputRelationCandidate] = []
        for token_ref in claimed_input_refs:
            token = token_metadata_by_ref[token_ref]
            resource_ref = self.__resource_ref(token.get("resource_ref"))
            if resource_ref is None:
                continue
            source_resource_ref = resource_ref
            lease_identity = token.get("lease_identity_ref")
            if (isinstance(lease_identity, Mapping)
                    and token_resources_by_ref[token_ref].intersection(
                        ordinary_resources)):
                # Scheme-B lock authority duplicates an ordinary semantic
                # colour and is intentionally not a byte-input candidate.
                continue
            header = self.__kernel._header(resource_ref, through_head=head)
            if header.content_schema_ref not in ports_by_schema:
                continue
            static_binding_ref = resource_ref.as_version_ref()
            parsed_lease_identity = (
                _parse_ref(lease_identity,
                           label="petri token lease_identity_ref")
                if isinstance(lease_identity, Mapping) else None)
            candidates.append(_InputRelationCandidate(
                token_ref=token_ref,
                resource_ref=resource_ref,
                content_schema_id=header.content_schema_ref,
                exact_port_ids=self.__exact_token_port_ids(token),
                statically_bound=(
                    static_binding_ref in binding.input_binding_refs
                    and static_binding_ref in binding.readable_resource_refs),
                semantic_token_refs=tuple(claimed_resources.get(
                    source_resource_ref, ())),
                lease_identity_ref=parsed_lease_identity,
                source_resource_ref=(
                    source_resource_ref
                    if source_resource_ref != resource_ref else None),
            ))
        return _solve_input_relation(
            candidates=tuple(candidates),
            ports=spec.input_ports,
            static_excluded_port_ids=static_excluded_port_ids)

    def resolve_operation_input_claims(
            self, *, firing: TransitionFiringAuthority,
            binding: OperationBindingAuthority,
            spec: OperationSpecAuthority,
            input_resource_substitutions: tuple[
                OperationInputResourceSubstitution, ...] = (),
    ) -> tuple[RegisteredOperationInputClaimAuthority, ...]:
        """Plan the exact claimed resources that operation ports may receive."""
        relation = self.__resolve_input_relation(
            claimed_input_refs=firing.claimed_input_refs,
            binding=binding, spec=spec)
        return self.__operation_input_claims(
            relation=relation, firing=firing, spec=spec,
            substitutions=input_resource_substitutions)

    def resolve_pre_admission_input_claims(
            self, *, claimed_input_refs: tuple[VersionRef, ...],
            binding: OperationBindingAuthority,
            spec: OperationSpecAuthority,
            static_excluded_port_ids: frozenset[str] = frozenset(),
    ) -> tuple[RegisteredOperationInputClaimAuthority, ...]:
        """Solve an explicit module-composed pre-admission port relation."""
        relation = self.__resolve_input_relation(
            claimed_input_refs=claimed_input_refs,
            binding=binding,
            spec=spec,
            static_excluded_port_ids=static_excluded_port_ids)
        return tuple(RegisteredOperationInputClaimAuthority(
            claimed_token_ref=item.candidate.token_ref,
            resource_ref=item.candidate.resource_ref,
            port=item.port,
            source_resource_ref=item.candidate.source_resource_ref,
        ) for item in relation)

    def __operation_input_claims(
            self, *, relation: tuple[_InputRelationAssignment, ...],
            firing: TransitionFiringAuthority,
            spec: OperationSpecAuthority,
            substitutions: tuple[OperationInputResourceSubstitution, ...] = (),
    ) -> tuple[RegisteredOperationInputClaimAuthority, ...]:
        if (not isinstance(substitutions, tuple)
                or any(not isinstance(item, OperationInputResourceSubstitution)
                       for item in substitutions)
                or len({item.port_id for item in substitutions}) != len(substitutions)):
            raise OperationAuthorityError(
                "operation input substitutions are not distinct declared ports")
        activation = (
            self.exact_metadata(
                firing.activation_ref,
                expected_type=firing.activation_ref.entity_type)
            if substitutions and firing.activation_ref is not None else None)
        ports = {port.port_id: port for port in spec.input_ports}
        for substitution in substitutions:
            if (substitution.port_id not in ports
                    or not isinstance(activation, Mapping)):
                raise OperationAuthorityError(
                    "operation input substitution lacks exact port/witness authority")
            witness_ref = _parse_ref(
                activation.get(substitution.witness_member),
                label="activation input substitution witness")
            if witness_ref != substitution.resource_ref.as_version_ref():
                raise OperationAuthorityError(
                    "operation input substitution differs from activation witness")
            if (self.__substitution_resource_schema(
                    firing, substitution.resource_ref)
                    != substitution.resource_schema_id):
                raise OperationAuthorityError(
                    "operation input substitution differs from declared schema")
        by_port = {item.port_id: item for item in substitutions}
        returned: list[RegisteredOperationInputClaimAuthority] = []
        for item in relation:
            source_ref = (
                item.candidate.source_resource_ref
                or item.candidate.resource_ref)
            substitution = by_port.get(item.port.port_id)
            resource_ref = (substitution.resource_ref
                            if substitution is not None
                            else item.candidate.resource_ref)
            returned.append(RegisteredOperationInputClaimAuthority(
                claimed_token_ref=item.candidate.token_ref,
                resource_ref=resource_ref,
                port=item.port,
                source_resource_ref=(source_ref
                                     if resource_ref != source_ref else None),
                substituted_content_schema_id=(
                    substitution.resource_schema_id
                    if substitution is not None else None),
            ))
        return tuple(returned)

    def resolve_operation_inputs(
            self, *, canonical: CanonicalInvocationAuthority,
            firing: TransitionFiringAuthority,
            binding: OperationBindingAuthority,
            spec: OperationSpecAuthority,
            petri_inputs: tuple[
                PetriInputArtifact | HistoricalPetriInputArtifact, ...],
            input_resource_substitutions: tuple[
                OperationInputResourceSubstitution, ...] = (),
    ) -> tuple[RegisteredOperationInputAuthority, ...]:
        for artifact in petri_inputs:
            self.__reverify_input(canonical, artifact)
        delivered_by_ref = {
            artifact.resource.header.ref: artifact for artifact in petri_inputs}
        relation = self.__resolve_input_relation(
            claimed_input_refs=firing.claimed_input_refs,
            binding=binding, spec=spec)
        planned = self.__operation_input_claims(
            relation=relation, firing=firing, spec=spec,
            substitutions=input_resource_substitutions)
        relation_refs = {item.resource_ref for item in planned}
        if set(delivered_by_ref) != relation_refs:
            raise OperationAuthorityError(
                "delivered operation inputs differ from independently "
                "recomputed token/resource/port relation")
        resolved: list[RegisteredOperationInputAuthority] = []
        planned_by_token = {
            item.claimed_token_ref: item for item in planned}
        for item in relation:
            candidate = item.candidate
            planned_item = planned_by_token[candidate.token_ref]
            static_binding_ref = planned_item.resource_ref.as_version_ref()
            # A launch-time file is named directly by the immutable operation
            # binding.  A dynamic file is named by its one exact claimed token.
            input_binding_ref = (
                static_binding_ref if (
                    candidate.statically_bound
                    and planned_item.resource_ref == candidate.resource_ref)
                else candidate.token_ref)
            resolved.append(RegisteredOperationInputAuthority(
                port_id=item.port.port_id,
                input_binding_ref=input_binding_ref,
                claimed_token_ref=candidate.token_ref,
                schema_ref=item.port.schema_ref,
                artifact=delivered_by_ref[planned_item.resource_ref],
                projected_payload=(
                    None if planned_item.source_resource_ref is not None else
                    self.__project_input_payload(
                        port=item.port,
                        artifact=delivered_by_ref[planned_item.resource_ref])),
                source_resource_ref=planned_item.source_resource_ref,
            ))
        return tuple(resolved)

    def _historical_hydrate_fault_route(
            self, route_ref: VersionRef, *,
            expected_operation_binding_ref: VersionRef,
    ) -> OperationFaultRouteAuthority:
        if (not isinstance(route_ref, VersionRef)
                or route_ref.entity_type != "fault_route_binding/v1"
                or not isinstance(expected_operation_binding_ref, VersionRef)
                or expected_operation_binding_ref.entity_type
                != "operation_binding/v1"):
            raise TypeError(
                "fault route hydration requires exact route/operation refs")
        expected_binding = self.exact_metadata(
            expected_operation_binding_ref,
            expected_type="operation_binding/v1")
        route = self.exact_metadata(
            route_ref,
            expected_type="fault_route_binding/v1")
        if (route.get("operation_binding_ref")
                != _ref_payload(expected_operation_binding_ref)
                or route.get("policy_ref")
                != expected_binding.get("fault_policy_ref")
                or expected_binding.get("fault_route_binding_ref")
                != _ref_payload(route_ref)):
            raise OperationAuthorityError(
                "fault route differs from operation binding authority")
        route_binding_ref = _parse_ref(
            route.get("binding_ref"), label="fault route binding_ref")
        operation_binding_ref = _parse_ref(
            route.get("operation_binding_ref"),
            label="fault route operation_binding_ref")
        policy_ref = _parse_ref(
            route.get("policy_ref"), label="fault route policy_ref")
        subnet_template_ref = _parse_ref(
            route.get("subnet_template_ref"),
            label="fault route subnet_template_ref")
        template = self.exact_metadata(
            subnet_template_ref, expected_type="fault_subnet_template/v1")
        raw_place_refs = template.get("place_refs")
        raw_transition_refs = template.get("transition_refs")
        if (not isinstance(raw_place_refs, list)
                or not isinstance(raw_transition_refs, list)):
            raise OperationAuthorityError(
                "fault subnet template lacks exact endpoint refs")
        place_authorities: list[FaultPetriPlaceAuthority] = []
        for raw in raw_place_refs:
            place_ref = _parse_ref(raw, label="fault subnet place ref")
            place = self.exact_metadata(
                place_ref, expected_type="fault_petri_place/v1")
            place_authorities.append(FaultPetriPlaceAuthority(
                place_ref=place_ref,
                subnet_template_ref=_parse_ref(
                    place.get("subnet_template_ref"),
                    label="fault place subnet_template_ref"),
                role=place.get("role"),  # type: ignore[arg-type]
                place=place.get("place"),  # type: ignore[arg-type]
            ))
        transition_authorities: list[FaultPetriTransitionAuthority] = []
        for raw in raw_transition_refs:
            transition_ref = _parse_ref(
                raw, label="fault subnet transition ref")
            transition = self.exact_metadata(
                transition_ref, expected_type="fault_petri_transition/v1")
            input_refs = transition.get("input_place_refs")
            output_refs = transition.get("output_place_refs")
            verdicts = transition.get("accepted_verdicts")
            if (not isinstance(input_refs, list)
                    or not isinstance(output_refs, list)
                    or not isinstance(verdicts, list)):
                raise OperationAuthorityError(
                    "fault subnet transition arcs/guards are malformed")
            transition_authorities.append(FaultPetriTransitionAuthority(
                transition_ref=transition_ref,
                subnet_template_ref=_parse_ref(
                    transition.get("subnet_template_ref"),
                    label="fault transition subnet_template_ref"),
                role=transition.get("role"),  # type: ignore[arg-type]
                input_place_refs=tuple(
                    _parse_ref(value, label="fault transition input place")
                    for value in input_refs),
                output_place_refs=tuple(
                    _parse_ref(value, label="fault transition output place")
                    for value in output_refs),
                accepted_verdicts=tuple(verdicts),
            ))
        subnet = FaultSubnetAuthority(
            subnet_template_ref=subnet_template_ref,
            places=tuple(sorted(
                place_authorities, key=lambda item: item.role)),
            transitions=tuple(sorted(
                transition_authorities, key=lambda item: item.role)),
        )
        fault_pending_place_ref = _parse_ref(
            route.get("fault_pending_place_ref"),
            label="fault route fault_pending_place_ref")
        capacity_return_place_ref = _parse_ref(
            route.get("capacity_return_place_ref"),
            label="fault route capacity_return_place_ref")
        reconcile_ref = _parse_ref(
            route.get("reconcile_ref"), label="fault route reconcile_ref")
        reconcile_place_ref = _parse_ref(
            route.get("reconcile_place_ref"),
            label="fault route reconcile_place_ref")
        terminal_place_ref = _parse_ref(
            route.get("terminal_place_ref"),
            label="fault route terminal_place_ref")
        retry_transition_ref = _parse_ref(
            route.get("retry_transition_ref"),
            label="fault route retry_transition_ref")
        exhaust_transition_ref = _parse_ref(
            route.get("exhaust_transition_ref"),
            label="fault route exhaust_transition_ref")
        if (route_binding_ref != route_ref
                or operation_binding_ref != expected_operation_binding_ref
                or _ref_payload(policy_ref)
                != expected_binding.get("fault_policy_ref")):
            raise OperationAuthorityError(
                "fault route exact refs differ from operation binding")
        held_places = route.get("held_work_places")
        work_places = route.get("original_work_places")
        capacity_places = route.get("capacity_return_places")
        work_refs = route.get("held_work_port_refs")
        held_place_refs = route.get("held_work_place_refs")
        capacity_refs = route.get("capacity_return_port_refs")
        mechanical_values = route.get("mechanical_transitions")
        if (not isinstance(held_places, list)
                or not isinstance(work_places, list)
                or not isinstance(capacity_places, list)
                or not isinstance(work_refs, list)
                or not isinstance(held_place_refs, list)
                or not isinstance(capacity_refs, list)
                or not isinstance(mechanical_values, list)
                or len(held_places) != len(work_refs)
                or len(held_place_refs) != len(work_refs)
                or len(work_places) != len(work_refs)
                or len(capacity_places) != len(capacity_refs)
                or set(held_places) & set(work_places)
                or set(held_places) & set(capacity_places)
                or set(work_places) & set(capacity_places)):
            raise OperationAuthorityError("fault route place partition is not closed")
        work_port_refs = tuple(
            _parse_ref(value, label="fault route work port")
            for value in work_refs)
        held_work_place_refs = tuple(
            _parse_ref(value, label="fault route held work place")
            for value in held_place_refs)
        capacity_port_refs = tuple(
            _parse_ref(value, label="fault route capacity port")
            for value in capacity_refs)
        mechanical_transitions: list[
            FaultMechanicalTransitionRouteAuthority] = []
        for value in mechanical_values:
            if not isinstance(value, Mapping):
                raise OperationAuthorityError(
                    "fault route mechanical transition is malformed")
            inputs = value.get("input_places")
            outputs = value.get("output_places")
            verdicts = value.get("accepted_verdicts")
            if (not isinstance(inputs, list)
                    or not isinstance(outputs, list)
                    or not isinstance(verdicts, list)):
                raise OperationAuthorityError(
                    "fault route mechanical arcs/guards are malformed")
            mechanical_transitions.append(
                FaultMechanicalTransitionRouteAuthority(
                    transition_ref=_parse_ref(
                        value.get("transition_ref"),
                        label="fault route mechanical transition ref"),
                    role=value.get("role"),  # type: ignore[arg-type]
                    input_places=tuple(inputs),
                    output_places=tuple(outputs),
                    accepted_verdicts=tuple(verdicts),
                    emit=value.get("emit"),  # type: ignore[arg-type]
                ))
        declared_refs = (
            route_binding_ref, operation_binding_ref, policy_ref,
            subnet_template_ref, fault_pending_place_ref,
            capacity_return_place_ref,
            *work_port_refs, *held_work_place_refs, *capacity_port_refs,
            reconcile_ref, reconcile_place_ref, terminal_place_ref,
            retry_transition_ref, exhaust_transition_ref,
        )
        for ref in declared_refs:
            self.require_registered_ref(ref)
        fault_pending_place = route.get("fault_pending_place")
        reconcile_place = route.get("reconcile_place")
        terminal_place = route.get("terminal_place")
        route_digest = route.get("route_digest")
        dependency_fingerprint = route.get("dependency_fingerprint")
        return OperationFaultRouteAuthority(
            fault_route_binding_ref=route_binding_ref,
            operation_binding_ref=operation_binding_ref,
            policy_ref=policy_ref,
            subnet_template_ref=subnet_template_ref,
            subnet=subnet,
            fault_pending_place_ref=fault_pending_place_ref,
            fault_pending_place=fault_pending_place,  # type: ignore[arg-type]
            held_work_port_refs=work_port_refs,
            held_work_place_refs=held_work_place_refs,
            held_work_places=tuple(held_places),
            original_work_places=tuple(work_places),
            capacity_return_port_refs=capacity_port_refs,
            capacity_return_places=tuple(capacity_places),
            capacity_return_place_ref=capacity_return_place_ref,
            reconcile_ref=reconcile_ref,
            reconcile_place_ref=reconcile_place_ref,
            reconcile_place=reconcile_place,  # type: ignore[arg-type]
            terminal_place_ref=terminal_place_ref,
            terminal_place=terminal_place,  # type: ignore[arg-type]
            retry_transition_ref=retry_transition_ref,
            exhaust_transition_ref=exhaust_transition_ref,
            mechanical_transitions=tuple(mechanical_transitions),
            route_digest=route_digest,  # type: ignore[arg-type]
            dependency_fingerprint=dependency_fingerprint,  # type: ignore[arg-type]
        )

    def _historical_hydrate_static_fault_route(
            self, route_ref: VersionRef, *,
            expected_operation_binding_ref: VersionRef,
    ) -> OperationFaultRouteAuthority:
        """Hydrate the current static sidecar route without policy coupling."""
        binding = self.exact_metadata(
            expected_operation_binding_ref,
            expected_type="operation_binding/v1")
        route = self.exact_metadata(
            route_ref, expected_type="fault_route_binding/v1")
        if (binding.get("fault_route_binding_ref") != _ref_payload(route_ref)
                or route.get("fault_route_binding_ref")
                != _ref_payload(route_ref)
                or route.get("blocked_waiting_retry_decision_control")
                != "blocked_waiting_retry_decision"):
            raise OperationAuthorityError(
                "static fault route differs from operation binding")
        subnet_ref = _parse_ref(
            route.get("subnet_template_ref"),
            label="static fault route subnet_template_ref")
        template = self.exact_metadata(
            subnet_ref, expected_type="fault_subnet_template/v1")
        place_authorities = tuple(sorted((
            FaultPetriPlaceAuthority(
                place_ref=(place_ref := _parse_ref(
                    raw, label="static fault subnet place ref")),
                subnet_template_ref=subnet_ref,
                role=(place := self.exact_metadata(
                    place_ref, expected_type="fault_petri_place/v1"))["role"],
                place=place["place"],
            )
            for raw in template["place_refs"]), key=lambda item: item.role))
        transition_authorities = []
        for raw in template["transition_refs"]:
            transition_ref = _parse_ref(
                raw, label="static fault subnet transition ref")
            transition = self.exact_metadata(
                transition_ref, expected_type="fault_petri_transition/v1")
            transition_authorities.append(FaultPetriTransitionAuthority(
                transition_ref=transition_ref,
                subnet_template_ref=subnet_ref,
                role=transition["role"],
                input_place_refs=tuple(_parse_ref(
                    value, label="static fault transition input")
                    for value in transition["input_place_refs"]),
                output_place_refs=tuple(_parse_ref(
                    value, label="static fault transition output")
                    for value in transition["output_place_refs"]),
                accepted_verdicts=tuple(transition["accepted_verdicts"]),
            ))
        subnet = FaultSubnetAuthority(
            subnet_template_ref=subnet_ref,
            places=place_authorities,
            transitions=tuple(sorted(
                transition_authorities, key=lambda item: item.role)),
        )
        spec_ref = _parse_ref(
            binding.get("operation_spec_ref"), label="route operation spec")
        spec = self.exact_metadata(spec_ref, expected_type="operation_spec/v1")
        return OperationFaultRouteAuthority(
            fault_route_binding_ref=route_ref,
            operation_binding_ref=expected_operation_binding_ref,
            subnet_template_ref=subnet_ref,
            subnet=subnet,
            fault_pending_place_ref=_parse_ref(
                route.get("fault_pending_place_ref"),
                label="static fault pending place"),
            blocked_waiting_retry_decision_control=str(
                route["blocked_waiting_retry_decision_control"]),
            retry_transition_ref=_parse_ref(
                route.get("retry_transition_ref"),
                label="static retry transition"),
            exhaust_transition_ref=_parse_ref(
                route.get("exhaust_transition_ref"),
                label="static exhaust transition"),
            reconcile_transition_ref=_parse_ref(
                route.get("reconcile_transition_ref"),
                label="static reconcile transition"),
            original_work_places=tuple(
                str(port["place"]) for port in spec["input_ports"]),
        )

    def _historical_resolve_operation_fault_partition(
            self, *, firing: TransitionFiringAuthority,
            binding: OperationBindingAuthority,
    ) -> OperationFaultPartitionAuthority:
        declared_route = self._historical_hydrate_static_fault_route(
            binding.fault_route_binding_ref,
            expected_operation_binding_ref=binding.operation_binding_ref)
        work_places = declared_route.original_work_places
        capacity_places = declared_route.capacity_return_places
        held: list[VersionRef] = []
        returned: list[VersionRef] = []
        for token_ref in firing.claimed_input_refs:
            token = self.exact_metadata(
                token_ref, expected_type="petri_token/v1")
            place = token.get("place")
            if place in work_places:
                held.append(token_ref)
            elif place in capacity_places:
                returned.append(token_ref)
            elif isinstance(token.get("lease_identity_ref"), Mapping):
                # v6 variable arcs add coloured M=1 lease tokens at firing
                # time.  They are reusable capacity and must be returned on a
                # fault, but they are intentionally not frozen into the
                # fixed operation fault-route declaration.
                returned.append(token_ref)
            elif (token.get("resource_ref") is None
                    and token.get("work_resource_ref") is None
                    and token.get("consumer") == firing.transition_id
                    and token.get("producer") is None
                    and token.get("control_ref") is None
                    and token.get("lease_identity_ref") is None):
                # Current static sidecars do not duplicate per-operation
                # capacity places.  The claimed capacity colour is exact from
                # its consumer identity and resource-free token shape.
                returned.append(token_ref)
            else:
                raise OperationAuthorityError(
                    "claimed Petri token place is absent from exact fault route")
        return OperationFaultPartitionAuthority(
            route=declared_route,
            held_work_refs=_canonical_operation_fault_refs(
                tuple(sorted(
                    held,
                    key=lambda ref: (
                        ref.entity_type, str(ref.entity_id),
                        str(ref.version_id)))),
                label="held work"),
            returned_capacity_refs=_canonical_operation_fault_refs(
                tuple(sorted(
                    returned,
                    key=lambda ref: (
                        ref.entity_type, str(ref.entity_id),
                        str(ref.version_id)))),
                label="returned capacity"),
        )

    def resolve_operation_output(
            self, *, authority: RegisteredOperationAuthority,
            artifact: VerifiedResourceArtifact,
    ) -> RegisteredOperationOutputAuthority:
        return self._resolve_operation_output(
            authority=authority, artifact=artifact, historical=False)

    def registered_compiled_operation(
            self, authority: RegisteredOperationAuthority,
    ) -> tuple[CompiledPetriNet, CompiledOperation]:
        """Read the actual executable binding's immutable compiler wire.

        Loading verifies the already formed inventory; it never lowers a HOST
        component or reconstructs executable code.
        """
        binding = self.exact_metadata(
            authority.transition.binding_ref,
            expected_type="executable_transition_binding/v1")
        if (binding.get("operation_binding_ref") != _ref_payload(
                    authority.operation_binding.operation_binding_ref)
                or binding.get("transition_id") != authority.firing.transition_id
                or binding.get("net_instance_ref") != _ref_payload(authority.firing.net_ref)):
            raise OperationAuthorityError("compiled output binding differs from firing")
        from .publication import _resource_from_payload
        try:
            ref = _resource_from_payload(binding["declaration_resource_ref"])
            prepared = self.__kernel._firing_prepared(authority.canonical.context, ref)
            compiled = load_compiled_net(json.loads(
                self.__core.object_store.read_registered(prepared)))
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationAuthorityError("output declaration is not registered compiler wire") from exc
        if (binding.get("declaration_schema_ref") != compiled.schema_version
                or prepared.metadata.get("content_schema_ref") != compiled.schema_version
                or prepared.metadata.get("task_ref") != _ref_payload(authority.firing.task_ref)):
            raise OperationAuthorityError("compiled output declaration differs from task/schema")
        transitions = tuple(item for item in compiled.symbolic.transitions
                            if item.name == authority.firing.transition_id)
        if len(transitions) != 1:
            raise OperationAuthorityError("compiled output transition is not exact")
        operations = tuple(item for item in compiled.operations
                           if item.declaration.name == transitions[0].operation)
        if (len(operations) != 1
                or operations[0].operation_id != authority.spec.operation_id
                or operations[0].executor_key != authority.spec.executor_key):
            raise OperationAuthorityError("compiled output operation differs from registered spec")
        operation = operations[0]
        ports = {item.name: item for item in compiled.ports}
        for direction, names, actual in (
                ("input", operation.declaration.inputs, authority.spec.input_ports),
                ("output", operation.declaration.outputs, authority.spec.output_ports)):
            if tuple(ports[name].port_id for name in names) != tuple(port.port_id for port in actual):
                raise OperationAuthorityError("compiled operation differs from ordered spec ports")
            for name, port in zip(names, actual):
                declared = ports[name]
                if declared.schema != port.content_schema_id or declared.place != port.place:
                    raise OperationAuthorityError("compiled port differs from exact schema/place")
                if direction == "input":
                    minimum = (declared.cardinality if declared.cardinality_minimum is None
                               else declared.cardinality_minimum)
                    maximum = (declared.cardinality if declared.cardinality_maximum is None
                               else declared.cardinality_maximum)
                else:
                    products = [next((p for p in outcome.products if p.port == name), None)
                                for outcome in operation.declaration.outcomes]
                    minimum = min(p.minimum if p is not None else 0 for p in products)
                    maximum = max(p.maximum if p is not None else 0 for p in products)
                if (port.minimum, port.maximum) != (minimum, maximum):
                    raise OperationAuthorityError("compiled product envelope differs from spec")
        return compiled, operation

    def validate_operation_output_bundle(
            self, *, authority: RegisteredOperationAuthority,
            outputs: tuple[RegisteredOperationOutputAuthority, ...],
            selected_outcome_id: str | None = None,
    ) -> tuple[str, tuple[RegisteredOperationOutputAuthority, ...]]:
        """Prove the bundle; return its outcome and mechanically coloured outputs.

        The harness uses only the compiled Petri operation/outcome declaration.
        Any richer output protocol belongs to the composing module and runs
        outside this repository boundary.
        """
        counts = {port.port_id: 0 for port in authority.spec.output_ports}
        for output in outputs:
            if output.port_id not in counts:
                raise OperationAuthorityError("bundle contains an undeclared output port")
            counts[output.port_id] += 1
        try:
            compiled, operation = self.registered_compiled_operation(authority)
            symbolic = {port.port_id: port.name for port in compiled.ports}
            colours = []
            for output in outputs:
                prepared = self.__kernel._firing_prepared(
                    authority.canonical.context, output.resource_ref)
                descriptors = prepared.metadata.get("descriptors")
                if descriptors is not None and not isinstance(descriptors, Mapping):
                    raise ValueError("output descriptors are malformed")
                descriptors = descriptors or {}
                if ("output_port_id" in descriptors and descriptors["output_port_id"] != output.port_id
                        or "place" in descriptors and descriptors["place"] != output.place):
                    raise ValueError("output descriptor differs from exact binding")
                colours.append(descriptors.get("output_outcome_id"))
                if output.verdict is not None:
                    colours.append(output.verdict)
            selected = validate_compiled_output_bundle(
                operation.declaration, {symbolic[port]: count for port, count in counts.items()},
                declared_outcomes=tuple(colours), selected_outcome_id=selected_outcome_id)
            return selected, tuple(replace(output, verdict=selected) for output in outputs)
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationAuthorityError("registered output bundle violates declared outcome contract") from exc

    def resolve_historical_operation_output(
            self, *, authority: RegisteredOperationAuthority,
            artifact: VerifiedResourceArtifact,
    ) -> RegisteredOperationOutputAuthority:
        """Resolve immutable committed output without reopening stale I/O."""
        return self._resolve_operation_output(
            authority=authority, artifact=artifact, historical=True)

    def _resolve_operation_output(
            self, *, authority: RegisteredOperationAuthority,
            artifact: VerifiedResourceArtifact,
            historical: bool,
    ) -> RegisteredOperationOutputAuthority:
        exact = (
            self.__host.historical_output(authority.canonical, artifact.header.ref)
            if historical else self.__host.verify_resource(
                authority.canonical, artifact.header.ref))
        if replace(
                exact,
                header=replace(
                    exact.header,
                    relation_kinds=artifact.header.relation_kinds,
                    published_at_head=artifact.header.published_at_head),
                verified_at_head=artifact.verified_at_head,
        ) != artifact:
            raise OperationAuthorityError(
                "operation output differs from current resource closure")
        prepared = (
            self.__kernel._prepared(artifact.header.ref)
            if historical else self.__kernel._firing_prepared(
                authority.canonical.context, artifact.header.ref))
        metadata = dict(prepared.metadata)
        origin = metadata.get("origin")
        if (metadata.get("producer_ref") != _ref_payload(
                    authority.canonical.context.invocation_ref)
                or not isinstance(origin, Mapping)
                or origin.get("kind") != "petri_output"):
            raise OperationAuthorityError(
                "operation output lacks exact invocation Petri provenance")
        primary = origin.get("primary_ref")
        secondary = origin.get("secondary_ref")
        if (not isinstance(primary, Mapping)
                or (secondary is not None and not isinstance(secondary, Mapping))):
            raise OperationAuthorityError("operation output origin refs are malformed")
        from cpn.rpnh.registry.identities import TypedId
        output_binding_ref = VersionRef(
            str(primary["entity_type"]),
            TypedId.parse(str(primary["logical_id"])),
            TypedId.parse(str(primary["version_id"])),
        )
        activation_ref = VersionRef(
            str(secondary["entity_type"]),
            TypedId.parse(str(secondary["logical_id"])),
            TypedId.parse(str(secondary["version_id"])),
        ) if secondary is not None else None
        if activation_ref != authority.canonical.context.activation_ref:
            raise OperationAuthorityError(
                "operation output activation differs from invocation")
        matches = tuple(
            item for item in authority.operation_binding.output_port_bindings
            if item.output_binding_ref == output_binding_ref)
        if len(matches) != 1:
            raise OperationAuthorityError(
                "operation output does not select one exact output port")
        port_binding = matches[0]
        port = next(
            item for item in authority.spec.output_ports
            if item.port_id == port_binding.port_id)
        output_metadata = self.exact_metadata(
            output_binding_ref, expected_type="output_binding/v1")
        if (output_metadata.get("place") != port_binding.place
                or output_metadata.get("place_ref")
                != _ref_payload(port_binding.place_ref)):
            raise OperationAuthorityError(
                "operation output place differs from exact output binding")
        verdict: bool | str | None = None
        if artifact.header.content_schema_ref == (
                "registry_v1/mechanical_transition_receipt/v1"):
            try:
                receipt = json.loads(
                    self.__core.object_store.read_verified(prepared))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise OperationAuthorityError(
                    "mechanical operation receipt is not exact JSON") from exc
            if (not isinstance(receipt, Mapping)
                    or receipt.get("declared_transition_id")
                    != authority.firing.transition_id
                    or receipt.get("operation_binding_ref")
                    != _ref_payload(
                        authority.operation_binding.operation_binding_ref)
                    or receipt.get("declared_settlement_id")
                    != port_binding.place):
                raise OperationAuthorityError(
                    "mechanical operation receipt differs from its firing")
            verdict = receipt.get("declared_outcome_id")
            if verdict is not None and (
                    not isinstance(verdict, str) or not verdict):
                raise OperationAuthorityError(
                    "mechanical operation receipt has an invalid outcome colour")
        declared_outcome = output_metadata.get("declared_outcome_id")
        if declared_outcome is not None and (
                not isinstance(declared_outcome, str)
                or not declared_outcome):
            raise OperationAuthorityError(
                "output binding has an invalid declared outcome")
        if declared_outcome is not None:
            if verdict is not None and verdict != declared_outcome:
                raise OperationAuthorityError(
                    "semantic output differs from its declared binding outcome")
            verdict = declared_outcome
        return RegisteredOperationOutputAuthority(
            port_id=port.port_id,
            place=port_binding.place,
            place_ref=port_binding.place_ref,
            output_binding_ref=output_binding_ref,
            schema_ref=port.schema_ref,
            artifact=exact,
            work_resource_ref=None,
            kind=None,
            verdict=verdict,
            continuation=None,
        )

    def _historical_publish_operation_fault_closure(
            self, *, authority: RegisteredOperationAuthority,
            command: RegisterOperationFault,
            idempotency_key: str,
    ) -> tuple[VersionRef, VersionRef]:
        if command.fault_terminal_evidence_ref is not None:
            raise OperationAuthorityError(
                "fault terminal witness is authored only by the atomic Registry writer")
        context = authority.canonical.context
        witness_key = f"{idempotency_key}:witness"
        witness_ref = VersionRef(
            "fault_terminal_witness/v1",
            _stable_id("fault_terminal_witness", witness_key),
            _stable_id("fault_terminal_witness_version", witness_key),
        )
        fault_ref, fault_metadata, fault_derived = (
            _historical_build_operation_fault_closure(
                self.__core,
                context,
                observation_kind=command.observation_kind,
                certainty=command.certainty,
                provider_attempt_evidence_ref=(
                    command.provider_attempt_evidence_ref),
                provider_submission_unknown_ref=(
                    command.provider_submission_unknown_ref),
                fault_terminal_evidence_ref=witness_ref,
                idempotency_key=f"{idempotency_key}:fault",
            )
        )
        built_witness_ref, witness_metadata, witness_derived = (
            _historical_build_fault_terminal_witness(
                self.__core,
                context,
                operation_fault_ref=fault_ref,
                held_work_refs=authority.fault_partition.held_work_refs,
                returned_capacity_refs=(
                    authority.fault_partition.returned_capacity_refs),
                idempotency_key=witness_key,
            )
        )
        if built_witness_ref != witness_ref:
            raise OperationAuthorityError(
                "fault terminal witness identity differs from operation fault")
        fault_row = self.__core.event_store.object_row(fault_ref.version_id)
        witness_row = self.__core.event_store.object_row(witness_ref.version_id)
        tx = self.__core.begin(idempotency_key=idempotency_key)
        if (fault_row is None) != (witness_row is None):
            raise OperationAuthorityError(
                "operation fault closure has a pre-existing partial state")
        if (fault_row is not None and witness_row is not None
                and (fault_row["transaction_id"] != witness_row["transaction_id"]
                     or fault_row["transaction_id"]
                     != str(tx.transaction_id))):
            raise OperationAuthorityError(
                "operation fault closure was published by another transaction")
        tx.prewrite(
            object_type="operation_fault/v1",
            logical_id=fault_ref.entity_id,
            version_id=fault_ref.version_id,
            payload=canonical_json(fault_metadata),
            metadata=fault_metadata,
            media_type="application/json",
            schema_ref="registry_v1/operation_fault/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.prewrite(
            object_type="fault_terminal_witness/v1",
            logical_id=witness_ref.entity_id,
            version_id=witness_ref.version_id,
            payload=canonical_json(witness_metadata),
            metadata=witness_metadata,
            media_type="application/json",
            schema_ref="registry_v1/fault_terminal_witness/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        for source, contract, derived in (
                (fault_ref, "operation_fault/v1", fault_derived),
                (witness_ref, "fault_terminal_witness/v1", witness_derived)):
            for index, target in enumerate(derived):
                tx.relate(TypedRelation(
                    _stable_id(
                        "relation",
                        f"{idempotency_key}:{contract}:{index}:"
                        f"{target.version_id}"),
                    "derived_from", source, target,
                    metadata={"strict_contract": contract}),
                    producer_invocation_id=context.invocation_ref.entity_id)
        tx.commit()
        fault_row = self.__core.event_store.object_row(fault_ref.version_id)
        witness_row = self.__core.event_store.object_row(witness_ref.version_id)
        if (fault_row is None or witness_row is None
                or {fault_row["transaction_id"], witness_row["transaction_id"]}
                != {str(tx.transaction_id)}
                or json.loads(fault_row["metadata_json"]) != fault_metadata
                or json.loads(witness_row["metadata_json"])
                != witness_metadata):
            raise OperationAuthorityError(
                "Registry did not publish the exact atomic fault closure")
        return fault_ref, witness_ref


__all__ = [
    "RegistryOperationAuthorityRepository", "OperationRepositoryHost",
    "OperationInputResourceSubstitution",
]
