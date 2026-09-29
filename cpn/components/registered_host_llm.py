"""Firing-local LLM capability for explicitly opted-in HOST executors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType

from cpn.rpnh.llm_contracts import LLMCallAttempt, LLMInputTarget
from cpn.rpnh.registry.models import PendingEvent, TypedRelation, VersionRef
from cpn.rpnh.registry.module_host_bindings import HostExecutionBinding
from cpn.rpnh.registry.operation_execution import verify_operation_execution
from cpn.rpnh.registry.operations import (
    OperationAuthorityError, registered_operation_host_protocols,
)
from cpn.rpnh.registry.provider_calls import (
    LLMCallV3, ProviderAttemptV3,
)
from cpn.rpnh.registry.publication import (
    _append_direct_resource_version_publication, _direct_resource_metadata,
    _registry_type_catalog_ref, _resource_from_payload, _stable_id,
    _version_from_payload,
)
from cpn.rpnh.registry.resource_service import (
    _publish_private_system, _resource_payload,
)
from cpn.rpnh.registry.resources import (
    AcknowledgeResourceDelivery, AuthorizeResourceRelease,
    PrepareResourceDelivery, PrivateSystemOrigin, PublishResource,
    ResourceVersionRef,
)
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import _registered, ref_payload
from cpn.rpnh.response_protocol import (
    LLMResponseProtocolError, canonicalize_llm_response_payload,
)


HOST_PROTOCOL = "registered_llm/v1"
EXECUTION_IDENTITY_VERSION = "registered_host_execution_identity/v1"
EXECUTION_PROVENANCE_SCHEMA = (
    "component/registered_host_execution_provenance/v1")
EXECUTION_PROVENANCE_DOCUMENT = {
    "$id": EXECUTION_PROVENANCE_SCHEMA,
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {
            "const": "registered_host_execution_provenance/v1"},
        "model": {"type": "string", "minLength": 1},
        "backend": {"type": "string", "minLength": 1},
        "timeout_seconds": {"type": "integer", "minimum": 1},
        "selection": {"type": "object"},
        "transport_kind": {"type": "string", "minLength": 1},
        "response_protocol": {"const": "llm_response_envelope/v1"},
    },
    "required": [
        "schema_version", "model", "backend", "timeout_seconds",
        "selection", "transport_kind", "response_protocol",
    ],
}
_HOST_REQUEST_FIELDS = frozenset({
    "protocol", "messages", "tools", "tool_choice", "placeholders",
})


@dataclass(frozen=True, slots=True)
class RegisteredHostLLMCallAttempt(LLMCallAttempt):
    """Adapter-compatible attempt without AgentLoop invocation identities."""

    provider_attempt_ref: VersionRef

    def __post_init__(self) -> None:
        if (not isinstance(self.invocation_ref, VersionRef)
                or self.invocation_ref.entity_type != "llm_call_spec/v3"
                or not isinstance(self.attempt_ref, VersionRef)
                or self.attempt_ref.entity_type
                != "registered_host_llm_attempt/v1"
                or not isinstance(self.provider_attempt_ref, VersionRef)
                or self.provider_attempt_ref.entity_type
                != "provider_attempt_spec/v1"
                or self.attempt_ordinal != 0
                or not isinstance(self.model_condition, str)
                or not self.model_condition.strip()
                or self.model_condition != self.model_condition.strip()
                or not isinstance(self.canonical_request_bytes, bytes)
                or not self.canonical_request_bytes
                or isinstance(self.max_response_bytes, bool)
                or not isinstance(self.max_response_bytes, int)
                or self.max_response_bytes < 1):
            raise TypeError("registered HOST LLM attempt authority is invalid")


_RESUME_CLASSIFICATIONS = frozenset({
    "fresh", "pre_submission", "submission_ready", "submission_unknown",
    "raw_response", "semantic_success", "closed", "inconsistent",
})


@dataclass(frozen=True, slots=True)
class RegisteredHostLLMResumePlan:
    """Immutable exact-firing decision for one registered-HOST v3 call."""

    classification: str
    authority_ref: VersionRef
    attempt: RegisteredHostLLMCallAttempt | None = None
    response: bytes | None = None
    preparation_step: str | None = None
    block_kind: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if (self.classification not in _RESUME_CLASSIFICATIONS
                or not isinstance(self.authority_ref, VersionRef)
                or (self.attempt is not None
                    and not isinstance(
                        self.attempt, RegisteredHostLLMCallAttempt))
                or (self.response is not None
                    and not isinstance(self.response, bytes))):
            raise TypeError("registered HOST LLM resume plan is invalid")
        if self.classification == "fresh":
            valid = (
                self.attempt is None and self.response is None
                and self.preparation_step is None
                and self.block_kind is None and self.error_code is None)
        elif self.classification == "pre_submission":
            valid = (
                self.attempt is not None and self.response is None
                and self.preparation_step in {
                    "materialization", "dispatch", "permit"}
                and self.block_kind is None and self.error_code is None)
        elif self.classification == "submission_ready":
            valid = (
                self.attempt is not None and self.response is None
                and self.preparation_step is None
                and self.block_kind is None and self.error_code is None)
        elif self.classification == "raw_response":
            valid = (
                self.attempt is not None and self.response is not None
                and self.preparation_step is None
                and self.block_kind is None and self.error_code is None)
        elif self.classification == "semantic_success":
            valid = (
                self.attempt is not None and self.response is not None
                and self.preparation_step is None
                and self.block_kind is None and self.error_code is None)
        else:
            valid = (
                self.response is None and self.preparation_step is None
                and isinstance(self.block_kind, str)
                and bool(self.block_kind)
                and isinstance(self.error_code, str)
                and bool(self.error_code))
        if not valid:
            raise ValueError(
                "registered HOST LLM resume plan fields are inconsistent")

    @property
    def physical_request_allowed(self) -> bool:
        return self.classification == "submission_ready"


class RegisteredHostLLMCallBlocked(RuntimeError):
    """The physical call closed without a successful semantic response."""

    def __init__(
            self, attempt: RegisteredHostLLMCallAttempt | None, *,
            block_kind: str, error_code: str,
            exact_error_ref: VersionRef | None = None,
    ) -> None:
        super().__init__(error_code)
        self.attempt = attempt
        self.block_kind = block_kind
        self.error_code = error_code
        self.exact_error_ref = (
            attempt.attempt_ref if exact_error_ref is None and attempt is not None
            else exact_error_ref)
        if not isinstance(self.exact_error_ref, VersionRef):
            raise TypeError(
                "registered HOST LLM block requires one exact authority ref")


def registered_host_execution_identity(
        execution_policy: Mapping[str, object],
) -> dict[str, object]:
    """Reduce private adapter policy to its non-secret Registry identity."""
    if not isinstance(execution_policy, Mapping):
        raise TypeError(
            "registered HOST execution policy must be an object")
    document = json.loads(canonical_json(dict(execution_policy)))
    if document.get("schema_version") == EXECUTION_IDENTITY_VERSION:
        route_keys = {
            "route_id", "provider", "backend", "transport", "protocol",
            "outbound_model"}
        profile_keys = {
            "config_schema_version", "config_revision", "recovery",
            "route_count"}
        if (set(document) != {
                    "schema_version", "adapter_kind", "timeout_seconds",
                    "max_output_tokens", "max_response_bytes", "routes",
                    "adapter_profile"}
                or not isinstance(document.get("routes"), list)
                or len(document["routes"]) != 1
                or not isinstance(document["routes"][0], dict)
                or not {
                    "route_id", "provider", "backend", "transport"
                }.issubset(document["routes"][0])
                or not set(document["routes"][0]).issubset(route_keys)
                or not isinstance(document.get("adapter_profile"), dict)
                or not set(document["adapter_profile"]).issubset(
                    profile_keys)):
            raise ValueError(
                "registered HOST execution identity is malformed")
        return document
    routes = document.get("route_provenance")
    profile = document.get("adapter_profile")
    if (not isinstance(routes, list) or len(routes) != 1
            or not isinstance(routes[0], dict)
            or not isinstance(profile, dict)):
        raise ValueError(
            "registered HOST execution policy lacks one route identity")
    source_route = routes[0]
    required_route = ("route_id", "provider", "backend", "transport")
    if any(not isinstance(source_route.get(name), str)
           or not source_route[name] for name in required_route):
        raise ValueError(
            "registered HOST execution route identity is malformed")
    route = {name: source_route[name] for name in required_route}
    for name in ("protocol", "outbound_model"):
        value = source_route.get(name)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise ValueError(
                    "registered HOST execution route identity is malformed")
            route[name] = value
    adapter_profile = {}
    schema_version = profile.get("config_schema_version")
    if isinstance(schema_version, str) and schema_version:
        adapter_profile["config_schema_version"] = schema_version
    revision = profile.get("config_revision")
    if revision is not None:
        if (not isinstance(revision, dict)
                or set(revision) != {"byte_count", "modified_ns"}
                or any(isinstance(revision[name], bool)
                       or not isinstance(revision[name], int)
                       or revision[name] < 0
                       for name in revision)):
            raise ValueError(
                "registered HOST adapter revision is malformed")
        adapter_profile["config_revision"] = dict(revision)
    recovery = profile.get("recovery")
    if recovery is not None:
        if not isinstance(recovery, dict):
            raise ValueError(
                "registered HOST recovery identity is malformed")
        adapter_profile["recovery"] = recovery
    route_count = profile.get("route_count")
    if route_count is not None:
        if isinstance(route_count, bool) or not isinstance(route_count, int):
            raise ValueError(
                "registered HOST route count identity is malformed")
        adapter_profile["route_count"] = route_count
    identity = {
        "schema_version": EXECUTION_IDENTITY_VERSION,
        "adapter_kind": document.get("adapter_kind"),
        "timeout_seconds": document.get("timeout_seconds"),
        "max_output_tokens": document.get("max_output_tokens"),
        "max_response_bytes": document.get("max_response_bytes"),
        "routes": [route],
        "adapter_profile": adapter_profile,
    }
    if (not isinstance(identity["adapter_kind"], str)
            or not identity["adapter_kind"]
            or any(isinstance(identity[name], bool)
                   or not isinstance(identity[name], int)
                   or identity[name] < 1
                   for name in (
                       "timeout_seconds", "max_output_tokens",
                       "max_response_bytes"))):
        raise ValueError(
            "registered HOST execution policy identity is malformed")
    return json.loads(canonical_json(identity))


def registered_host_execution_route(selection) -> dict[str, object]:
    """Return the common Registry route document for any HOST adapter."""
    from cpn.llm_adapters.config import LLMExecutionSelection

    if not isinstance(selection, LLMExecutionSelection):
        raise TypeError(
            "registered HOST route requires LLMExecutionSelection")
    identity = registered_host_execution_identity(
        selection.as_registry_policy())
    routes = identity.get("routes")
    if (not isinstance(routes, list) or len(routes) != 1
            or not isinstance(routes[0], Mapping)
            or not isinstance(routes[0].get("transport"), str)
            or not routes[0]["transport"]):
        raise ValueError(
            "registered HOST route lacks one exact transport provenance")
    return {
        "schema_version": "registered_host_execution_provenance/v1",
        "model": selection.input_target.model_condition,
        "backend": selection.adapter_kind,
        "timeout_seconds": selection.timeout_seconds,
        "selection": identity,
        "transport_kind": routes[0]["transport"],
        "response_protocol": "llm_response_envelope/v1",
    }


def registered_host_llm_schema_data():
    """Return only the catalog extension required by the generic HOST seam."""
    from cpn.rpnh.registry.schema_catalog import TypeDefinition

    path = (
        Path(__file__).resolve().parents[1]
        / "schemas/registry_v1/provider_attempt_completed.v1.schema.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    return ({document["$id"]: document}, (
        TypeDefinition(
            "provider_attempt_completed/v1", "event",
            "registered-host-llm", document["$id"], "authoritative",
            "writer-only", "permanent", "registry-reference-replay",
            "exact-schema-and-reference-validation"),
    ))


class RegisteredHostLLM:
    """One-use worker-side capability over the configured shared input port."""

    __slots__ = (
        "_execution", "_gateway", "_input_port", "_interruption_requested",
        "_used",
    )

    def __init__(self, *, execution, gateway, input_port,
                 interruption_requested) -> None:
        self._execution = execution
        self._gateway = gateway
        self._input_port = input_port
        self._interruption_requested = interruption_requested
        self._used = False

    def request(self, request: Mapping[str, object] | bytes) -> bytes:
        if self._used:
            raise OperationAuthorityError(
                "registered HOST LLM capability is one-use per firing")
        self._used = True
        if isinstance(request, Mapping):
            payload = canonical_json(dict(request))
        elif isinstance(request, bytes):
            payload = request
        else:
            raise TypeError(
                "registered HOST LLM request requires canonical DTO or bytes")
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationAuthorityError(
                "registered HOST LLM request is not canonical JSON") from exc
        if canonical_json(document) != payload:
            raise OperationAuthorityError(
                "registered HOST LLM request bytes are not canonical")
        from cpn.llm_adapters.factory import bound_llm_execution_policy
        execution_policy = registered_host_execution_identity(
            bound_llm_execution_policy(self._input_port))
        plan = self._gateway.reconcile_registered_host_llm(
            self._execution, payload, execution_policy)
        if plan.classification == "semantic_success":
            return plan.response
        if not plan.physical_request_allowed:
            raise RegisteredHostLLMCallBlocked(
                plan.attempt, block_kind=plan.block_kind,
                error_code=plan.error_code,
                exact_error_ref=plan.authority_ref)
        attempt = plan.attempt
        interruptible = getattr(
            self._input_port, "request_once_interruptible", None)
        try:
            response = (
                interruptible(
                    attempt,
                    interruption_requested=self._interruption_requested)
                if callable(interruptible) else
                self._input_port.request_once(attempt))
        except Exception as exc:
            from cpn.rpnh.llm_contracts import (
                LLMInputPortFailure, LLMInputPortInterrupted,
            )
            if isinstance(exc, LLMInputPortInterrupted):
                self._gateway.close_registered_host_llm(
                    self._execution, attempt,
                    disposition="owner_interrupted",
                    submission_state=exc.submission_state,
                    failure_code="owner_interrupted")
                raise RegisteredHostLLMCallBlocked(
                    attempt, block_kind="owner_interrupted",
                    error_code="owner_interrupted") from exc
            if isinstance(exc, LLMInputPortFailure):
                self._gateway.close_registered_host_llm(
                    self._execution, attempt,
                    disposition=exc.disposition,
                    submission_state=exc.submission_state,
                    failure_code=exc.failure_code)
                raise RegisteredHostLLMCallBlocked(
                    attempt,
                    block_kind=(
                        "submission_reconciliation"
                        if exc.disposition == "submission_unknown"
                        else "llm_repair"),
                    error_code=exc.failure_code) from exc
            raise
        return self._gateway.complete_registered_host_llm(
            self._execution, attempt, bytes(response),
            status_code=getattr(response, "status_code", None),
            external_request_id=getattr(
                response, "external_request_id", None))


@dataclass(frozen=True, slots=True)
class _RegisteredHostRequestAuthority:
    execution: object
    context: object
    target_ref: ResourceVersionRef
    prompt_ref: ResourceVersionRef
    catalog_ref: ResourceVersionRef
    route_ref: ResourceVersionRef
    transport_ref: ResourceVersionRef
    target_document: Mapping[str, object]
    route: Mapping[str, object]
    transport: Mapping[str, object]
    canonical_request_bytes: bytes
    key: str
    request_ref: ResourceVersionRef
    attempt_ref: VersionRef
    call_ref: VersionRef
    provider_ref: VersionRef


class RegisteredHostLLMRegistryService:
    """Owner-side preparation and terminal recording for registered HOST calls."""

    def __init__(self, *, owner, kernel, repository, provider_attempts) -> None:
        self.owner = owner
        self.core = owner._core
        self.kernel = kernel
        self.repository = repository
        self.ledger = provider_attempts

    def gateway_methods(self):
        return {
            "prepare_registered_host_llm": self.prepare,
            "classify_registered_host_llm_resume": self.classify_resume,
            "reconcile_registered_host_llm": self.reconcile,
            "complete_registered_host_llm": self.complete,
            "close_registered_host_llm": self.close,
        }

    def _execution(self, execution):
        execution = verify_operation_execution(
            self.core, self.kernel, self.repository, execution)
        if HOST_PROTOCOL not in registered_operation_host_protocols(
                execution.operation.spec.executor_key):
            raise OperationAuthorityError(
                "registered executor did not opt into the HOST LLM protocol")
        return execution

    def _resources(self, execution):
        context = execution.operation.canonical.context
        binding = self.kernel._exact_object(
            context.operation_binding_ref,
            expected_type="operation_binding/v1").metadata
        required = {
            "registered_host_llm_backend",
            "registered_host_llm_transport",
            "registered_host_llm_prompt",
            "registered_host_llm_tool_catalog",
        }
        roles = {}
        for payload in binding["readable_resource_refs"]:
            version = _version_from_payload(payload)
            if version.entity_type != "resource_version/v1":
                continue
            ref = ResourceVersionRef(version.entity_id, version.version_id)
            prepared = self.kernel._firing_prepared(context, ref)
            role = prepared.metadata.get("descriptors", {}).get(
                "content_role")
            if role in required:
                if role in roles:
                    raise OperationAuthorityError(
                        "registered HOST LLM binding has duplicate resources")
                roles[role] = (ref, prepared)
        if set(roles) != required:
            raise OperationAuthorityError(
                "registered HOST LLM binding lacks its exact resources")
        target_ref = _resource_from_payload(binding["llm_input_target_ref"])
        target = self.kernel._firing_prepared(context, target_ref)
        return context, target_ref, target, roles

    def _hydrate(self, execution, attempt):
        execution = self._execution(execution)
        if not isinstance(attempt, RegisteredHostLLMCallAttempt):
            raise TypeError(
                "registered HOST LLM closure requires its typed attempt")
        context = execution.operation.canonical.context
        attempt_document = self.kernel._exact_object(
            attempt.attempt_ref,
            expected_type="registered_host_llm_attempt/v1").metadata
        call_document = self.kernel._exact_object(
            attempt.invocation_ref,
            expected_type="llm_call_spec/v3").metadata
        provider_document = self.kernel._exact_object(
            attempt.provider_attempt_ref,
            expected_type="provider_attempt_spec/v1").metadata
        if (attempt_document.get("llm_call_ref")
                != ref_payload(attempt.invocation_ref)
                or attempt_document.get("provider_attempt_ref")
                != ref_payload(attempt.provider_attempt_ref)
                or call_document.get("invocation_ref")
                != ref_payload(context.invocation_ref)
                or call_document.get("operation_binding_ref")
                != ref_payload(context.operation_binding_ref)
                or provider_document.get("llm_call_ref")
                != ref_payload(attempt.invocation_ref)
                or attempt.canonical_request_bytes
                != self.kernel._read_firing_registered(
                    context, _resource_from_payload(
                        call_document["request_resource_ref"]))):
            raise OperationAuthorityError(
                "registered HOST LLM attempt crossed its exact firing")
        call = LLMCallV3(
            attempt.invocation_ref.entity_id,
            attempt.invocation_ref.version_id,
            context.invocation_ref, context.operation_binding_ref,
            _resource_from_payload(call_document["request_resource_ref"]),
            _version_from_payload(call_document["terminal_delivery_ref"]),
            _resource_from_payload(
                call_document["semantic_prompt_resource_ref"]),
            _resource_from_payload(call_document["llm_input_target_ref"]),
            _resource_from_payload(
                call_document["llm_execution_target_ref"]),
            call_document["backend"], call_document["model"],
            _resource_from_payload(call_document["transport_contract_ref"]),
            call_document["interaction_protocol_ref"],
            call_document["response_adapter_ref"],
            _resource_from_payload(call_document["tool_catalog_ref"]),
            call_document["timeout_seconds"],
            call_document["max_response_bytes"])
        provider = ProviderAttemptV3(
            attempt.provider_attempt_ref.entity_id,
            attempt.provider_attempt_ref.version_id, call,
            provider_document["reservation_class"],
            provider_document["finalization_scope"])
        return execution, context, call, provider, attempt_document

    def _request_authority(
            self, execution, request_bytes, execution_policy,
    ) -> _RegisteredHostRequestAuthority:
        execution = self._execution(execution)
        if not isinstance(request_bytes, bytes) or not request_bytes:
            raise TypeError("registered HOST LLM request requires bytes")
        try:
            envelope = json.loads(request_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationAuthorityError(
                "registered HOST LLM request is not JSON") from exc
        if canonical_json(envelope) != request_bytes:
            raise OperationAuthorityError(
                "registered HOST LLM request bytes are not canonical")
        if (not isinstance(envelope, dict)
                or set(envelope) != _HOST_REQUEST_FIELDS
                or envelope.get("protocol") != HOST_PROTOCOL
                or not isinstance(envelope.get("messages"), list)
                or not envelope["messages"]
                or any(not isinstance(item, dict)
                       for item in envelope["messages"])
                or not isinstance(envelope.get("tools"), list)
                or any(not isinstance(item, dict)
                       for item in envelope["tools"])
                or envelope.get("tool_choice") not in {"auto", "none"}
                or not isinstance(envelope.get("placeholders"), list)
                or any(not isinstance(item, dict)
                       for item in envelope["placeholders"])):
            raise OperationAuthorityError(
                "registered HOST LLM request DTO is not exact")
        if not isinstance(execution_policy, Mapping):
            raise OperationAuthorityError(
                "registered HOST LLM input port lacks execution identity")
        execution_policy = registered_host_execution_identity(
            execution_policy)
        context, target_ref, target, roles = self._resources(execution)
        target_document = json.loads(
            self.core.object_store.read_registered(target))
        resource_documents = {
            role: json.loads(self.core.object_store.read_registered(prepared))
            for role, (_ref, prepared) in roles.items()
        }
        prompt_ref = roles["registered_host_llm_prompt"][0]
        catalog_ref = roles["registered_host_llm_tool_catalog"][0]
        route_ref = roles["registered_host_llm_backend"][0]
        transport_ref = roles["registered_host_llm_transport"][0]
        route = resource_documents["registered_host_llm_backend"]
        transport = resource_documents["registered_host_llm_transport"]
        catalog = resource_documents["registered_host_llm_tool_catalog"]
        if (envelope.get("tools") != catalog.get("tools")
                or route.get("model")
                != target_document.get("model_condition")
                or route.get("selection") != execution_policy
                or route.get("backend")
                != execution_policy.get("adapter_kind")
                or route.get("timeout_seconds")
                != execution_policy.get("timeout_seconds")
                or transport != {
                    "interaction_protocol_ref": "llm_request_envelope/v1",
                    "response_adapter_ref": "llm_response_envelope/v1",
                }):
            raise OperationAuthorityError(
                "registered HOST LLM request differs from firing bindings")
        envelope = {
            "protocol": "llm_request_envelope/v1",
            "model_condition": target_document["model_condition"],
            "max_output_tokens": target_document["max_output_tokens"],
            "messages": envelope["messages"],
            "tools": envelope["tools"],
            "tool_choice": envelope["tool_choice"],
            "source_prompt_ref": _resource_payload(prompt_ref),
            "tool_catalog_ref": _resource_payload(catalog_ref),
            "placeholders": envelope["placeholders"],
        }
        request_bytes = canonical_json(envelope)
        self.core.catalog.validate_schema_ref(
            "runtime/llm_request_envelope/v1", envelope)
        key = (
            "registered-host-llm:"
            + str(context.own_transition_firing_ref.version_id))
        request_ref = ResourceVersionRef(
            _stable_id("resource", key, "request"),
            _stable_id("resource_version", key, "request"))
        attempt_ref = VersionRef(
            "registered_host_llm_attempt/v1",
            _stable_id("registered_host_llm_attempt", key),
            _stable_id("registered_host_llm_attempt_version", key))
        call_ref = VersionRef(
            "llm_call_spec/v3", _stable_id("llm_call", key + ":call"),
            _stable_id("llm_call_version", key + ":call"))
        provider_ref = VersionRef(
            "provider_attempt_spec/v1",
            _stable_id("provider_attempt", key + ":provider"),
            _stable_id("provider_attempt_version", key + ":provider"))
        return _RegisteredHostRequestAuthority(
            execution, context, target_ref, prompt_ref, catalog_ref,
            route_ref, transport_ref, target_document, route, transport,
            request_bytes, key, request_ref, attempt_ref, call_ref,
            provider_ref)

    def prepare(self, execution, request_bytes, execution_policy):
        authority = self._request_authority(
            execution, request_bytes, execution_policy)
        execution = authority.execution
        context = authority.context
        target_ref = authority.target_ref
        prompt_ref = authority.prompt_ref
        catalog_ref = authority.catalog_ref
        route_ref = authority.route_ref
        transport_ref = authority.transport_ref
        target_document = authority.target_document
        route = authority.route
        transport = authority.transport
        request_bytes = authority.canonical_request_bytes
        key = authority.key
        request_ref = authority.request_ref
        attempt_ref = authority.attempt_ref
        if self.core.event_store.object_row(attempt_ref.version_id) is not None:
            previous = self.kernel._read_firing_registered(
                context, request_ref)
            if previous != request_bytes:
                raise OperationAuthorityError(
                    "registered HOST LLM firing already owns a different "
                    "canonical request")
            raise OperationAuthorityError(
                "registered HOST LLM firing is already prepared; its "
                "permitted provider attempt must be reconciled, not replayed")
        inputs = (prompt_ref, catalog_ref, target_ref, route_ref, transport_ref)
        tx = self.core.begin(idempotency_key=key + ":request")
        _append_direct_resource_version_publication(
            tx, ref=request_ref, payload=request_bytes,
            metadata_factory=lambda size: _direct_resource_metadata(
                self.core, ref=request_ref, origin_kind="provider_request",
                primary=context.operation_binding_ref,
                secondary=context.invocation_ref,
                task_ref=context.task_ref, round_ref=context.task_round_ref,
                net_ref=context.net_instance_ref,
                producer_ref=context.invocation_ref,
                lifetime_ref=context.operation_execution_lease_ref,
                operation_binding_ref=context.operation_binding_ref,
                agent_loop_ref=context.operation_binding_ref,
                payload_size=size, media_type="application/json",
                content_schema_ref="runtime/llm_request_envelope/v1",
                content_schema_authority_ref=ref_payload(
                    _registry_type_catalog_ref(self.core)),
                summary="Registered HOST canonical LLM request",
                descriptors={
                    "content_role": "registered_host_llm_request"},
                extensions={}, input_resource_refs=inputs,
                intended_boundary="llm_prompt"),
            media_type="application/json",
            producer_ref=context.invocation_ref,
            producer_invocation_id=context.invocation_ref.entity_id,
            relation_key=key + ":request",
            direct_owners=(context.operation_binding_ref,),
            input_resources=inputs)
        tx.commit()
        prepared_delivery = self.kernel.prepare_delivery(
            context, PrepareResourceDelivery(
                request_ref, context.operation_binding_ref,
                _stable_id("resource_delivery", key, "delivery"),
                key + ":delivery:prepare",
                "llm_prompt", "Registered HOST exact request bytes"))
        release = self.kernel.authorize_release(
            context, AuthorizeResourceRelease(
                prepared_delivery.delivery_ref, "llm_prompt",
                _stable_id("release_nonce", key, "release"),
                key + ":delivery:release"))
        payload = self.kernel._consume_authorized_release(context, release)
        receipt = self.kernel._record_boundary_receipt(
            context, release, outcome="acknowledged",
            positive_byte_count=len(payload),
            consumer_evidence="Registered HOST consumed exact request bytes",
            idempotency_key=key + ":delivery:receipt")
        delivery = self.kernel.acknowledge_delivery(
            context, AcknowledgeResourceDelivery(
                release.delivery_ref, receipt, "acknowledged",
                key + ":delivery:ack"))
        call = self.ledger.create_call_v3(
            context=context, request_resource_ref=request_ref,
            terminal_delivery_ref=delivery.delivery_ref,
            semantic_prompt_resource_ref=prompt_ref,
            llm_input_target_ref=target_ref,
            llm_execution_target_ref=route_ref,
            backend=route["backend"], model=route["model"],
            transport_contract_ref=transport_ref,
            interaction_protocol_ref=transport["interaction_protocol_ref"],
            response_adapter_ref=transport["response_adapter_ref"],
            tool_catalog_ref=catalog_ref,
            timeout_seconds=route["timeout_seconds"],
            max_response_bytes=target_document["max_response_bytes"],
            idempotency_key=key + ":call")
        provider = self.ledger.reserve_v3(
            context=context, call=call,
            idempotency_key=key + ":provider")
        attempt_document = {
            "registered_host_llm_attempt_id": str(attempt_ref.entity_id),
            "registered_host_llm_attempt_version_id": str(
                attempt_ref.version_id),
            "registered_host_llm_attempt_ref": ref_payload(attempt_ref),
            "invocation_kind": "registered_host",
            "invocation_ref": ref_payload(context.invocation_ref),
            "operation_binding_ref": ref_payload(
                context.operation_binding_ref),
            "llm_call_ref": ref_payload(call.ref),
            "provider_attempt_ref": ref_payload(provider.ref),
            "request_resource_ref": _resource_payload(request_ref),
            "model_condition": call.model,
            "max_response_bytes": call.max_response_bytes,
            "attempt_ordinal": 0,
        }
        tx = self.core.begin(idempotency_key=key + ":host-attempt")
        tx.prewrite(
            object_type=attempt_ref.entity_type,
            logical_id=attempt_ref.entity_id,
            version_id=attempt_ref.version_id,
            payload=canonical_json(attempt_document),
            metadata=attempt_document, media_type="application/json",
            schema_ref="registry_v1/registered_host_llm_attempt/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.relate(TypedRelation(
            _stable_id("relation", key, "host-attempt-provider"),
            "derived_from", attempt_ref, provider.ref),
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.append(PendingEvent(
            "registered_host_llm_attempt_reserved/v1", "authoritative",
            f"registered-host-llm-attempt:{attempt_ref.entity_id}",
            str(attempt_ref.entity_id), "registered_host_llm_attempt",
            key + ":host-attempt", key + ":host-attempt",
            attempt_document,
            "registry_v1/registered_host_llm_attempt_reserved/v1",
            task_control=True,
            producer_invocation_id=context.invocation_ref.entity_id))
        tx.commit()
        attempt = RegisteredHostLLMCallAttempt(
            call.ref, attempt_ref, 0, call.model, request_bytes,
            call.max_response_bytes, provider.ref)
        try:
            materialized = self.ledger.record_materialization(
                context=context, attempt=provider,
                request_payload=request_bytes,
                idempotency_key=key + ":materialization")
            self.ledger.dispatch_started(
                provider,
                materialization_receipt_ref=materialized.receipt.
                provider_payload_materialization_receipt_ref,
                request_byte_count=len(request_bytes),
                idempotency_key=key + ":dispatch")
            dispatches = tuple(
                event for event in
                self.core.event_store.list_events_by_aggregate(
                    str(provider.attempt_id), event_types=(
                        "provider_attempt_dispatch_started/v2",))
                if event.idempotency_key == key + ":dispatch")
            if len(dispatches) != 1:
                raise OperationAuthorityError(
                    "registered HOST LLM lacks one exact dispatch fact")
            self.ledger.submission_permitted(
                provider, dispatch_event_id=dispatches[0].event_id,
                operation_execution_lease_ref=
                execution.operation_execution_lease_ref,
                operation_start_event_id=execution.start_event_id,
                lifecycle_observation_event_id=dispatches[0].event_id,
                evidence_receipt_id=None,
                idempotency_key=key + ":permit")
        except Exception:
            self.close(
                execution, attempt,
                disposition="adapter_not_submitted",
                submission_state="not_submitted",
                failure_code="framework_prepare_failed")
            raise
        return attempt

    @staticmethod
    def _events_of_type(events, event_type):
        return tuple(event for event in events
                     if event.event_type == event_type)

    def _inconsistent_plan(
            self, authority: _RegisteredHostRequestAuthority,
            attempt: RegisteredHostLLMCallAttempt | None = None,
    ) -> RegisteredHostLLMResumePlan:
        return RegisteredHostLLMResumePlan(
            "inconsistent",
            attempt.attempt_ref if attempt is not None
            else authority.context.operation_binding_ref,
            attempt=attempt, block_kind="submission_reconciliation",
            error_code="registered_host_resume_inconsistent")

    def _attempt_for_authority(
            self, authority: _RegisteredHostRequestAuthority,
    ) -> RegisteredHostLLMCallAttempt:
        context = authority.context
        attempt_document = dict(self.kernel._exact_object(
            authority.attempt_ref,
            expected_type="registered_host_llm_attempt/v1").metadata)
        expected_attempt = {
            "registered_host_llm_attempt_id": str(
                authority.attempt_ref.entity_id),
            "registered_host_llm_attempt_version_id": str(
                authority.attempt_ref.version_id),
            "registered_host_llm_attempt_ref": ref_payload(
                authority.attempt_ref),
            "invocation_kind": "registered_host",
            "invocation_ref": ref_payload(context.invocation_ref),
            "operation_binding_ref": ref_payload(
                context.operation_binding_ref),
            "llm_call_ref": ref_payload(authority.call_ref),
            "provider_attempt_ref": ref_payload(authority.provider_ref),
            "request_resource_ref": _resource_payload(
                authority.request_ref),
            "model_condition": authority.route["model"],
            "max_response_bytes": authority.target_document[
                "max_response_bytes"],
            "attempt_ordinal": 0,
        }
        if attempt_document != expected_attempt:
            raise OperationAuthorityError(
                "registered HOST LLM attempt differs from its exact firing")
        attempt = RegisteredHostLLMCallAttempt(
            authority.call_ref, authority.attempt_ref, 0,
            str(authority.route["model"]),
            authority.canonical_request_bytes,
            int(authority.target_document["max_response_bytes"]),
            authority.provider_ref)
        _execution, _context, call, provider, _document = self._hydrate(
            authority.execution, attempt)
        call_document = dict(self.kernel._exact_object(
            authority.call_ref, expected_type="llm_call_spec/v3").metadata)
        expected_call = {
            "llm_call_ref": ref_payload(authority.call_ref),
            "invocation_kind": "registered_host",
            "invocation_ref": ref_payload(context.invocation_ref),
            "operation_binding_ref": ref_payload(
                context.operation_binding_ref),
            "request_resource_ref": _resource_payload(authority.request_ref),
            "semantic_prompt_resource_ref": _resource_payload(
                authority.prompt_ref),
            "llm_input_target_ref": _resource_payload(authority.target_ref),
            "llm_execution_target_ref": _resource_payload(
                authority.route_ref),
            "backend": authority.route["backend"],
            "model": authority.route["model"],
            "transport_contract_ref": _resource_payload(
                authority.transport_ref),
            "interaction_protocol_ref": authority.transport[
                "interaction_protocol_ref"],
            "response_adapter_ref": authority.transport[
                "response_adapter_ref"],
            "tool_catalog_ref": _resource_payload(authority.catalog_ref),
            "timeout_seconds": authority.route["timeout_seconds"],
            "max_response_bytes": authority.target_document[
                "max_response_bytes"],
        }
        if (call.ref != authority.call_ref
                or provider.ref != authority.provider_ref
                or call.request_resource_ref != authority.request_ref
                or any(call_document.get(name) != value
                       for name, value in expected_call.items())):
            raise OperationAuthorityError(
                "registered HOST LLM call differs from its exact route")
        provider_document = dict(self.kernel._exact_object(
            authority.provider_ref,
            expected_type="provider_attempt_spec/v1").metadata)
        expected_provider = {
            "provider_attempt_ref": ref_payload(authority.provider_ref),
            "llm_call_ref": ref_payload(authority.call_ref),
            "invocation_ref": ref_payload(context.invocation_ref),
            "operation_binding_ref": ref_payload(
                context.operation_binding_ref),
            "llm_execution_target_ref": _resource_payload(
                authority.route_ref),
            "request_resource_ref": _resource_payload(authority.request_ref),
            "terminal_delivery_ref": call_document["terminal_delivery_ref"],
            "backend": authority.route["backend"],
            "model": authority.route["model"],
            "transport_kind": authority.route["transport_kind"],
            "response_protocol": authority.route["response_protocol"],
            "timeout_seconds": authority.route["timeout_seconds"],
            "prior_attempt_ref": None,
            "retry_cause": None,
        }
        if any(provider_document.get(name) != value
               for name, value in expected_provider.items()):
            raise OperationAuthorityError(
                "registered HOST LLM provider attempt crossed its route")
        return attempt

    def _partial_firing_exists(
            self, authority: _RegisteredHostRequestAuthority,
    ) -> bool:
        store = self.core.event_store
        deterministic_versions = (
            authority.request_ref.resource_version_id,
            authority.call_ref.version_id,
            authority.provider_ref.version_id,
        )
        if any(store.object_row(version_id) is not None
               for version_id in deterministic_versions):
            return True
        return store.has_event_idempotency_prefix(authority.key + ":")

    def _raw_response(
            self, authority: _RegisteredHostRequestAuthority,
            attempt: RegisteredHostLLMCallAttempt, observation,
    ) -> bytes:
        response_ref = _resource_from_payload(
            observation.payload["response_resource_ref"])
        prepared = self.kernel._exact_object(
            response_ref.as_version_ref(), expected_type="resource_version/v1")
        payload = self.core.object_store.read_registered(prepared)
        metadata = prepared.metadata
        origin = metadata.get("origin")
        extensions = metadata.get("extensions")
        facts = (extensions.get("registry.provider_raw_response/v3")
                 if isinstance(extensions, Mapping) else None)
        if (observation.payload.get("provider_attempt_ref")
                != ref_payload(attempt.provider_attempt_ref)
                or observation.payload.get("llm_call_ref")
                != ref_payload(attempt.invocation_ref)
                or observation.payload.get("invocation_ref")
                != ref_payload(authority.context.invocation_ref)
                or observation.payload.get("response_size") != len(payload)
                or prepared.size != len(payload)
                or metadata.get("origin_kind") != "provider_raw_response"
                or not isinstance(origin, Mapping)
                or origin.get("primary_ref")
                != ref_payload(attempt.provider_attempt_ref)
                or origin.get("secondary_ref")
                != ref_payload(attempt.invocation_ref)
                or metadata.get("producer_ref")
                != ref_payload(authority.context.invocation_ref)
                or not isinstance(facts, Mapping)
                or facts.get("external_request_id")
                != observation.payload.get("external_request_id")):
            raise OperationAuthorityError(
                "registered HOST LLM raw response lineage is inconsistent")
        return payload

    def _semantic_response(
            self, authority: _RegisteredHostRequestAuthority,
            attempt: RegisteredHostLLMCallAttempt, success_events,
    ) -> bytes:
        first, second = success_events
        if (dict(first.payload) != dict(second.payload)
                or first.transaction_id != second.transaction_id
                or first.idempotency_key != second.idempotency_key):
            raise OperationAuthorityError(
                "registered HOST LLM semantic success pair is inconsistent")
        common = first.payload
        response_ref = _resource_from_payload(common["response_resource_ref"])
        prepared = self.kernel._exact_object(
            response_ref.as_version_ref(), expected_type="resource_version/v1")
        payload = self.core.object_store.read_registered(prepared)
        extension = prepared.metadata.get("extensions", {}).get(
            "registry.registered_host_llm_response/v1")
        if (common.get("registered_host_llm_attempt_ref")
                != ref_payload(attempt.attempt_ref)
                or common.get("llm_call_ref")
                != ref_payload(attempt.invocation_ref)
                or common.get("provider_attempt_ref")
                != ref_payload(attempt.provider_attempt_ref)
                or common.get("invocation_ref")
                != ref_payload(authority.context.invocation_ref)
                or common.get("operation_binding_ref")
                != ref_payload(authority.context.operation_binding_ref)
                or not isinstance(extension, Mapping)
                or extension.get("registered_host_llm_attempt_ref")
                != ref_payload(attempt.attempt_ref)
                or extension.get("llm_call_ref")
                != ref_payload(attempt.invocation_ref)
                or extension.get("model_condition")
                != attempt.model_condition
                or canonicalize_llm_response_payload(payload) != payload):
            raise OperationAuthorityError(
                "registered HOST LLM semantic response lineage is inconsistent")
        return payload

    def classify_resume(
            self, execution, request_bytes, execution_policy,
    ) -> RegisteredHostLLMResumePlan:
        """Classify one exact firing without creating or replaying a call."""
        authority = self._request_authority(
            execution, request_bytes, execution_policy)
        store = self.core.event_store
        if store.object_row(authority.attempt_ref.version_id) is None:
            if self._partial_firing_exists(authority):
                return self._inconsistent_plan(authority)
            return RegisteredHostLLMResumePlan(
                "fresh", authority.context.operation_binding_ref)
        try:
            attempt = self._attempt_for_authority(authority)
            host_rows = tuple(
                row for row in store.object_rows_by_type(
                    "registered_host_llm_attempt/v1")
                if json.loads(row["metadata_json"]).get("invocation_ref")
                == ref_payload(authority.context.invocation_ref))
            call_rows = tuple(
                row for row in store.object_rows_by_type(
                    "llm_call_spec/v3")
                if json.loads(row["metadata_json"]).get("invocation_ref")
                == ref_payload(authority.context.invocation_ref))
            provider_rows = store.provider_attempt_rows_for_call(
                authority.call_ref.entity_id, authority.call_ref.version_id)
            if (len(host_rows) != 1 or len(call_rows) != 1
                    or len(provider_rows) != 1
                    or host_rows[0]["version_id"]
                    != str(authority.attempt_ref.version_id)
                    or call_rows[0]["version_id"]
                    != str(authority.call_ref.version_id)
                    or provider_rows[0]["version_id"]
                    != str(authority.provider_ref.version_id)):
                return self._inconsistent_plan(authority, attempt)
            provider_events = store.list_events_by_aggregate(
                str(authority.provider_ref.entity_id))
            host_events = store.list_events_by_aggregate(
                str(authority.attempt_ref.entity_id))
            call_events = store.list_events_by_aggregate(
                str(authority.call_ref.entity_id))
            names = (
                "provider_attempt_reserved/v1",
                "provider_payload_materialization_recorded/v1",
                "provider_attempt_dispatch_started/v2",
                "provider_attempt_submission_permitted/v2",
                "provider_attempt_submission_observed/v1",
                "provider_attempt_completed/v1",
                "provider_attempt_host_closed/v1",
            )
            facts = {
                name: self._events_of_type(provider_events, name)
                for name in names
            }
            host_reserved = self._events_of_type(
                host_events, "registered_host_llm_attempt_reserved/v1")
            successes = (
                *self._events_of_type(
                    host_events, "llm_response_registered/v2"),
                *self._events_of_type(
                    host_events, "llm_invocation_succeeded/v2"),
            )
            host_closed = self._events_of_type(
                host_events, "registered_host_llm_attempt_closed/v1")
            call_closed = self._events_of_type(
                call_events, "llm_call_registered_host_closed/v1")
            if (any(len(value) > 1 for value in facts.values())
                    or len(host_reserved) != 1 or len(successes) > 2
                    or len(host_closed) > 1 or len(call_closed) > 1
                    or dict(host_reserved[0].payload) != dict(
                        self.kernel._exact_object(
                            authority.attempt_ref,
                            expected_type="registered_host_llm_attempt/v1"
                        ).metadata)):
                return self._inconsistent_plan(authority, attempt)
            materialized = facts[
                "provider_payload_materialization_recorded/v1"]
            dispatch = facts["provider_attempt_dispatch_started/v2"]
            permit = facts["provider_attempt_submission_permitted/v2"]
            observed = facts["provider_attempt_submission_observed/v1"]
            completed = facts["provider_attempt_completed/v1"]
            provider_closed = facts["provider_attempt_host_closed/v1"]
            if len(facts["provider_attempt_reserved/v1"]) != 1:
                return self._inconsistent_plan(authority, attempt)
            expected_refs = {
                "provider_attempt_ref": ref_payload(authority.provider_ref),
                "llm_call_ref": ref_payload(authority.call_ref),
                "invocation_ref": ref_payload(
                    authority.context.invocation_ref),
                "operation_binding_ref": ref_payload(
                    authority.context.operation_binding_ref),
                "request_resource_ref": _resource_payload(
                    authority.request_ref),
            }
            exact_events = tuple(
                event for group in facts.values() for event in group)
            if any(
                    event.producer_invocation_id
                    != authority.context.invocation_ref.entity_id
                    or any(
                        name in event.payload
                        and event.payload.get(name) != value
                        for name, value in expected_refs.items())
                    for event in exact_events):
                return self._inconsistent_plan(authority, attempt)
            if (materialized and (
                    materialized[0].payload.get("byte_count")
                    != len(authority.canonical_request_bytes)
                    or materialized[0].payload.get("request_recipe_ref")
                    != _resource_payload(authority.request_ref))):
                return self._inconsistent_plan(authority, attempt)
            if (dispatch and (
                    dispatch[0].payload.get("request_payload_byte_count")
                    != len(authority.canonical_request_bytes)
                    or dispatch[0].payload.get("response_protocol")
                    != authority.route["response_protocol"])):
                return self._inconsistent_plan(authority, attempt)
            if (permit and (not dispatch
                    or permit[0].payload.get("dispatch_event_id")
                    != str(dispatch[0].event_id)
                    or permit[0].payload.get("operation_execution_lease_ref")
                    != ref_payload(
                        authority.execution.operation_execution_lease_ref)
                    or permit[0].payload.get("operation_start_event_id")
                    != str(authority.execution.start_event_id)
                    or permit[0].payload.get("state")
                    != "submission_permitted")):
                return self._inconsistent_plan(authority, attempt)
            if (provider_closed or call_closed or host_closed):
                closed = (*provider_closed, *call_closed, *host_closed)
                if (len(closed) != 3 or successes or completed
                        or len({event.transaction_id for event in closed}) != 1
                        or len({event.idempotency_key for event in closed}) != 1
                        or any(dict(event.payload) != dict(closed[0].payload)
                               for event in closed[1:])):
                    return self._inconsistent_plan(authority, attempt)
                return RegisteredHostLLMResumePlan(
                    "closed", attempt.attempt_ref, attempt=attempt,
                    block_kind="llm_repair",
                    error_code=str(
                        closed[0].payload.get(
                            "failure_code", "registered_host_closed")))
            if successes or completed:
                success_types = {event.event_type for event in successes}
                if (len(successes) != 2 or len(completed) != 1
                        or len(observed) != 1
                        or success_types != {
                            "llm_response_registered/v2",
                            "llm_invocation_succeeded/v2"}):
                    return self._inconsistent_plan(authority, attempt)
                raw = self._raw_response(
                    authority, attempt, observed[0])
                completion = completed[0]
                if (completion.payload.get("response_version_id")
                        != observed[0].payload["response_resource_ref"][
                            "resource_version_id"]
                        or completion.payload.get(
                            "response_observed_event_id")
                        != str(observed[0].event_id)):
                    return self._inconsistent_plan(authority, attempt)
                del raw
                semantic = self._semantic_response(
                    authority, attempt, successes)
                return RegisteredHostLLMResumePlan(
                    "semantic_success", attempt.attempt_ref,
                    attempt=attempt, response=semantic)
            if observed:
                if not (len(materialized) == len(dispatch) == len(permit) == 1):
                    return self._inconsistent_plan(authority, attempt)
                raw = self._raw_response(authority, attempt, observed[0])
                return RegisteredHostLLMResumePlan(
                    "raw_response", attempt.attempt_ref,
                    attempt=attempt, response=raw)
            if permit:
                if not (len(materialized) == len(dispatch) == 1):
                    return self._inconsistent_plan(authority, attempt)
                return RegisteredHostLLMResumePlan(
                    "submission_unknown", attempt.attempt_ref,
                    attempt=attempt,
                    block_kind="submission_reconciliation",
                    error_code="submission_unknown")
            if dispatch:
                if len(materialized) != 1:
                    return self._inconsistent_plan(authority, attempt)
                return RegisteredHostLLMResumePlan(
                    "pre_submission", attempt.attempt_ref,
                    attempt=attempt, preparation_step="permit")
            if materialized:
                return RegisteredHostLLMResumePlan(
                    "pre_submission", attempt.attempt_ref,
                    attempt=attempt, preparation_step="dispatch")
            return RegisteredHostLLMResumePlan(
                "pre_submission", attempt.attempt_ref,
                attempt=attempt, preparation_step="materialization")
        except Exception:
            return self._inconsistent_plan(authority)

    def reconcile(
            self, execution, request_bytes, execution_policy,
    ) -> RegisteredHostLLMResumePlan:
        """Reconcile one v3 attempt, never issuing a physical request here."""
        plan = self.classify_resume(
            execution, request_bytes, execution_policy)
        if plan.classification == "fresh":
            attempt = self.prepare(execution, request_bytes, execution_policy)
            return RegisteredHostLLMResumePlan(
                "submission_ready", attempt.attempt_ref, attempt=attempt)
        if plan.classification == "raw_response":
            refreshed = self.classify_resume(
                execution, request_bytes, execution_policy)
            if refreshed.classification != "raw_response":
                return refreshed
            observation = self._events_of_type(
                self.core.event_store.list_events_by_aggregate(
                    str(refreshed.attempt.provider_attempt_ref.entity_id)),
                "provider_attempt_submission_observed/v1")[0]
            prepared = self.kernel._exact_object(
                _resource_from_payload(
                    observation.payload["response_resource_ref"]
                ).as_version_ref(),
                expected_type="resource_version/v1")
            facts = prepared.metadata["extensions"][
                "registry.provider_raw_response/v3"]
            self.complete(
                execution, refreshed.attempt, refreshed.response,
                status_code=facts.get("status_code"),
                external_request_id=facts.get("external_request_id"))
            return self.classify_resume(
                execution, request_bytes, execution_policy)
        committed_permit = False
        while plan.classification == "pre_submission":
            authority = self._request_authority(
                execution, request_bytes, execution_policy)
            _execution, context, _call, provider, _document = self._hydrate(
                authority.execution, plan.attempt)
            key = authority.key
            if plan.preparation_step == "materialization":
                self.ledger.record_materialization(
                    context=context, attempt=provider,
                    request_payload=authority.canonical_request_bytes,
                    idempotency_key=key + ":materialization")
            elif plan.preparation_step == "dispatch":
                recorded = self._events_of_type(
                    self.core.event_store.list_events_by_aggregate(
                        str(provider.attempt_id)),
                    "provider_payload_materialization_recorded/v1")
                if len(recorded) != 1:
                    return self._inconsistent_plan(authority, plan.attempt)
                receipt_ref = _version_from_payload(
                    recorded[0].payload[
                        "provider_payload_materialization_receipt_ref"])
                self.ledger.dispatch_started(
                    provider, materialization_receipt_ref=receipt_ref,
                    request_byte_count=len(authority.canonical_request_bytes),
                    idempotency_key=key + ":dispatch")
            elif plan.preparation_step == "permit":
                dispatches = self._events_of_type(
                    self.core.event_store.list_events_by_aggregate(
                        str(provider.attempt_id)),
                    "provider_attempt_dispatch_started/v2")
                if len(dispatches) != 1:
                    return self._inconsistent_plan(authority, plan.attempt)
                self.ledger.submission_permitted(
                    provider, dispatch_event_id=dispatches[0].event_id,
                    operation_execution_lease_ref=
                    authority.execution.operation_execution_lease_ref,
                    operation_start_event_id=authority.execution.start_event_id,
                    lifecycle_observation_event_id=dispatches[0].event_id,
                    evidence_receipt_id=None,
                    idempotency_key=key + ":permit")
                committed_permit = True
            plan = self.classify_resume(
                execution, request_bytes, execution_policy)
        if committed_permit and plan.classification == "submission_unknown":
            return RegisteredHostLLMResumePlan(
                "submission_ready", plan.attempt.attempt_ref,
                attempt=plan.attempt)
        return plan

    def complete(self, execution, attempt, response, *, status_code,
                 external_request_id):
        execution, context, call, provider, _ = self._hydrate(
            execution, attempt)
        raw = self.ledger.complete_v3(
            provider, response=response, status_code=status_code,
            external_request_id=external_request_id,
            idempotency_key=(
                "registered-host-llm:"
                + str(attempt.attempt_ref.version_id) + ":raw-return"))
        try:
            canonical = canonicalize_llm_response_payload(response)
        except LLMResponseProtocolError as exc:
            self.close(
                execution, attempt, disposition="protocol_rejected",
                submission_state="response_observed",
                failure_code="framework_response_protocol_invalid")
            raise RegisteredHostLLMCallBlocked(
                attempt, block_kind="llm_repair",
                error_code="framework_response_protocol_invalid") from exc
        if canonical != response:
            self.close(
                execution, attempt, disposition="protocol_rejected",
                submission_state="response_observed",
                failure_code="framework_response_protocol_invalid")
            raise RegisteredHostLLMCallBlocked(
                attempt, block_kind="llm_repair",
                error_code="framework_response_protocol_invalid")
        response_ref = ResourceVersionRef(
            _stable_id("resource", attempt.attempt_ref.version_id,
                       "semantic-response"),
            _stable_id("resource_version", attempt.attempt_ref.version_id,
                       "semantic-response"))
        key = (
            "registered-host-llm:"
            + str(attempt.attempt_ref.version_id) + ":success")
        tx = self.core.begin(idempotency_key=key)
        _append_direct_resource_version_publication(
            tx, ref=response_ref, payload=canonical,
            metadata_factory=lambda size: _direct_resource_metadata(
                self.core, ref=response_ref, origin_kind="llm_response",
                primary=attempt.attempt_ref, secondary=call.ref,
                task_ref=context.task_ref, round_ref=context.task_round_ref,
                net_ref=context.net_instance_ref,
                producer_ref=context.invocation_ref,
                lifetime_ref=context.operation_execution_lease_ref,
                operation_binding_ref=context.operation_binding_ref,
                agent_loop_ref=call.ref, payload_size=size,
                media_type="application/json",
                content_schema_ref="runtime/llm_response_envelope/v1",
                content_schema_authority_ref=ref_payload(
                    _registry_type_catalog_ref(self.core)),
                summary="Registered HOST canonical LLM response",
                descriptors={"content_role": "llm_response_envelope"},
                extensions={
                    "registry.registered_host_llm_response/v1": {
                        "registered_host_llm_attempt_ref": ref_payload(
                            attempt.attempt_ref),
                        "llm_call_ref": ref_payload(call.ref),
                        "model_condition": call.model,
                    }},
                input_resource_refs=(call.request_resource_ref,),
                intended_boundary="registered_host_response"),
            media_type="application/json",
            producer_ref=context.invocation_ref,
            producer_invocation_id=context.invocation_ref.entity_id,
            relation_key=key,
            direct_owners=(attempt.attempt_ref, call.ref),
            input_resources=(call.request_resource_ref,))
        raw_events = tuple(
            event for event in self.core.event_store.list_events_by_aggregate(
                str(provider.attempt_id), event_types=(
                    "provider_attempt_submission_observed/v1",))
            if event.payload.get("response_resource_ref")
            == _resource_payload(raw.response_resource_ref))
        if len(raw_events) != 1:
            raise OperationAuthorityError(
                "registered HOST LLM lacks its exact raw observation")
        common = {
            "invocation_kind": "registered_host",
            "invocation_ref": ref_payload(context.invocation_ref),
            "operation_binding_ref": ref_payload(
                context.operation_binding_ref),
            "llm_call_ref": ref_payload(call.ref),
            "registered_host_llm_attempt_ref": ref_payload(
                attempt.attempt_ref),
            "provider_attempt_ref": ref_payload(provider.ref),
            "response_resource_ref": _resource_payload(response_ref),
        }
        tx.append(PendingEvent(
            "provider_attempt_completed/v1", "authoritative",
            f"provider-attempt:{provider.attempt_id}",
            str(provider.attempt_id), "provider_attempt", key, key,
            {
                "provider_attempt_id": str(provider.attempt_id),
                "response_version_id": str(
                    raw.response_resource_ref.resource_version_id),
                "response_observed_event_id": str(raw_events[0].event_id),
                "finish_reason": raw_events[0].payload.get("finish_reason"),
                "external_request_id": raw_events[0].payload.get(
                    "external_request_id"),
            }, "registry_v1/provider_attempt_completed/v1",
            task_control=True,
            producer_invocation_id=context.invocation_ref.entity_id))
        for event_type in (
                "llm_response_registered/v2",
                "llm_invocation_succeeded/v2"):
            tx.append(PendingEvent(
                event_type, "authoritative",
                f"registered-host-llm-attempt:{attempt.attempt_ref.entity_id}",
                str(attempt.attempt_ref.entity_id),
                "registered_host_llm_attempt", key, key, common,
                "registry_v1/" + event_type, task_control=True,
                producer_invocation_id=context.invocation_ref.entity_id))
        tx.commit()
        return canonical

    def close(self, execution, attempt, *, disposition, submission_state,
              failure_code):
        _execution, context, call, provider, _ = self._hydrate(
            execution, attempt)
        key = (
            "registered-host-llm:"
            + str(attempt.attempt_ref.version_id) + ":closed")
        payload = {
            "invocation_kind": "registered_host",
            "invocation_ref": ref_payload(context.invocation_ref),
            "operation_binding_ref": ref_payload(
                context.operation_binding_ref),
            "llm_call_ref": ref_payload(call.ref),
            "registered_host_llm_attempt_ref": ref_payload(
                attempt.attempt_ref),
            "provider_attempt_ref": ref_payload(provider.ref),
            "disposition": disposition,
            "submission_state": submission_state,
            "failure_code": failure_code,
        }
        tx = self.core.begin(idempotency_key=key)
        for event_type, aggregate_id, aggregate_type, stream in (
                ("provider_attempt_host_closed/v1",
                 str(provider.attempt_id), "provider_attempt",
                 f"provider-attempt:{provider.attempt_id}"),
                ("llm_call_registered_host_closed/v1",
                 str(call.call_id), "llm_call",
                 f"llm-call:{call.call_id}"),
                ("registered_host_llm_attempt_closed/v1",
                 str(attempt.attempt_ref.entity_id),
                 "registered_host_llm_attempt",
                 f"registered-host-llm-attempt:{attempt.attempt_ref.entity_id}")):
            tx.append(PendingEvent(
                event_type, "authoritative", stream, aggregate_id,
                aggregate_type, key, key, payload,
                "registry_v1/" + event_type, task_control=True,
                producer_invocation_id=context.invocation_ref.entity_id))
        tx.commit()


def make_registered_llm_host_bindings(
        llm_input_target: LLMInputTarget, *,
        provider_backend_config: Mapping[str, object],
        provider_backend_schema_ref: str,
        transport_contract: Mapping[str, object],
        prompt: Mapping[str, object],
        tool_catalog: Mapping[str, object]):
    """Publish only the resources required by opted-in HOST executors."""
    if not isinstance(llm_input_target, LLMInputTarget):
        raise TypeError("registered HOST LLM bindings require LLMInputTarget")
    route = dict(provider_backend_config)
    route["selection"] = registered_host_execution_identity(
        route.get("selection"))
    transport = dict(transport_contract)
    prompt_document = dict(prompt)
    catalog_document = dict(tool_catalog)
    if (not isinstance(provider_backend_schema_ref, str)
            or not provider_backend_schema_ref
            or route.get("model") != llm_input_target.model_condition
            or set(transport) != {
                "interaction_protocol_ref", "response_adapter_ref"}
            or transport["interaction_protocol_ref"]
            != "llm_request_envelope/v1"
            or transport["response_adapter_ref"]
            != "llm_response_envelope/v1"
            or not isinstance(catalog_document.get("tools"), list)):
        raise ValueError(
            "registered HOST LLM binding documents are not exact")

    def bind(*, core, plan, source_ref, bootstrap_ref, compiled):
        if core.read_only:
            raise TypeError(
                "registered HOST LLM bindings require the writable owner")
        _, source = _registered(
            core, source_ref.as_version_ref(), "resource_version/v1")
        if (source["task_ref"] != ref_payload(plan.task_ref)
                or core.event_store.canonical_object_row(
                    source_ref.resource_version_id) is None):
            raise ValueError(
                "registered HOST LLM source differs from the Module")
        opted = {
            transition.name: next(
                operation for operation in compiled.operations
                if operation.declaration.name == transition.operation)
            for transition in compiled.symbolic.transitions
            if HOST_PROTOCOL in next(
                operation for operation in compiled.operations
                if operation.declaration.name == transition.operation
            ).executor_declaration["contracts"].get("host_protocols", ())
        }
        if not opted:
            return MappingProxyType({})
        key = "registered-host-llm:" + str(plan.net_ref.version_id)

        def static(suffix, document, role, *, schema=None):
            authority = None
            if schema is not None and schema != "registry_v1/llm_input_target/v1":
                rows = tuple(
                    row for row in core.event_store.canonical_object_rows(
                        object_type="resource_version/v1")
                    if json.loads(row["metadata_json"]).get(
                        "descriptors", {}).get("host_registration_kind")
                    == "schema"
                    and json.loads(row["metadata_json"]).get(
                        "descriptors", {}).get("registered_key") == schema)
                if len(rows) != 1:
                    raise ValueError(
                        "registered HOST route schema lacks exact authority")
                from cpn.rpnh.registry.identities import TypedId
                authority = ResourceVersionRef(
                    TypedId.parse(rows[0]["logical_id"], expected="resource"),
                    TypedId.parse(
                        rows[0]["version_id"], expected="resource_version"))
            return _publish_private_system(
                core, plan.task_ref, PublishResource(
                    origin=PrivateSystemOrigin(bootstrap_ref),
                    payload=canonical_json(document),
                    media_type="application/json",
                    content_schema_ref=schema,
                    content_schema_authority_ref=authority,
                    summary="Registered HOST LLM " + role,
                    lifetime_ref=bootstrap_ref,
                    descriptors={"content_role": role},
                    idempotency_key=f"{key}:{suffix}"))

        target = static(
            "target", llm_input_target.as_registry_document(),
            "registered_host_llm_target",
            schema="registry_v1/llm_input_target/v1")
        core.catalog.validate_schema_ref(provider_backend_schema_ref, route)
        backend = static(
            "backend", route, "registered_host_llm_backend",
            schema=provider_backend_schema_ref)
        transport_ref = static(
            "transport", transport, "registered_host_llm_transport")
        prompt_ref = static(
            "prompt", prompt_document, "registered_host_llm_prompt")
        catalog_ref = static(
            "catalog", catalog_document,
            "registered_host_llm_tool_catalog")
        return MappingProxyType({
            name: HostExecutionBinding(
                llm_input_target_ref=target,
                extra_resource_refs=(
                    backend, transport_ref, prompt_ref, catalog_ref))
            for name in opted
        })

    return bind


__all__ = (
    "HOST_PROTOCOL", "EXECUTION_PROVENANCE_SCHEMA",
    "EXECUTION_PROVENANCE_DOCUMENT", "EXECUTION_IDENTITY_VERSION",
    "registered_host_execution_identity", "registered_host_execution_route",
    "registered_host_llm_schema_data",
    "RegisteredHostLLM", "RegisteredHostLLMCallAttempt",
    "RegisteredHostLLMResumePlan",
    "RegisteredHostLLMCallBlocked", "RegisteredHostLLMRegistryService",
    "make_registered_llm_host_bindings",
)
