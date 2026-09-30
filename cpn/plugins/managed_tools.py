"""Managed provider tools backed by explicitly selected native plugins.

The immutable catalog projects only an explicit allowlist. Invocation is an
owner-bound service over an arbitrary admitted caller operation; it does not
pretend that caller is a native-plugin component firing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from threading import Event, Lock
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .api import (
    PluginError, ResourceView, canonical, frozen, json_copy, symbol, validate,
)
from .catalog import BoundPlugin, PluginCatalog
from .host import capability_value


LEGACY_CATALOG_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_catalog/v1"
LEGACY_INVOCATION_PROTOCOL = "rpnh/managed_native_plugin_tool_invocation/v1"
LEGACY_RECEIPT_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_receipt/v1"
LEGACY_RESULT_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_result/v1"
CATALOG_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_catalog/v2"
INVOCATION_PROTOCOL = "rpnh/managed_native_plugin_tool_invocation/v2"
RECEIPT_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_receipt/v2"
RESULT_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_result/v2"


class ManagedPluginInvocationConflict(PluginError):
    """A durable call identity was reused with different exact material."""


class ManagedPluginInvocationReconciliationRequired(PluginError):
    """A durable managed call requires owner reconciliation, never replay."""

    def __init__(self, message: str, *, evidence: Mapping[str, Any] | None = None) -> None:
        self.evidence = None if evidence is None else json_copy(evidence)
        super().__init__(message)


class ManagedPluginInvocationFailed(PluginError):
    """A managed invocation has a durable failed terminal state."""

    def __init__(self, code: str, *, evidence: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.evidence = None if evidence is None else json_copy(evidence)
        super().__init__(f"managed native plugin execution failed: {code}")


@dataclass(frozen=True, slots=True)
class ManagedToolSelector:
    """One provider-visible name mapped to one exact plugin selector."""

    name: str
    selector: str
    description: str | None = None
    input_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        symbol(self.name)
        if not isinstance(self.selector, str) or self.selector.count("/") != 1:
            raise PluginError(
                "managed tool selector must be plugin_name/operation_name")
        if (self.description is not None
                and (not isinstance(self.description, str)
                     or not self.description.strip())):
            raise PluginError("managed tool description override must be nonempty text")
        if self.input_schema is not None:
            if not isinstance(self.input_schema, Mapping):
                raise PluginError("managed tool input override must be a JSON schema")
            from jsonschema import Draft7Validator
            from jsonschema.exceptions import SchemaError
            schema = json_copy(self.input_schema)
            try:
                Draft7Validator.check_schema(schema)
            except SchemaError as exc:
                raise PluginError("managed tool input override is not a valid schema") from exc
            if schema.get("type") != "object":
                raise PluginError("managed tools require an object input schema")


@dataclass(frozen=True, slots=True)
class ManagedToolDeclaration:
    """Immutable provider and Registry declaration for one managed tool."""

    name: str
    selector: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    effect: str
    max_result_bytes: int
    protocol_version: str
    registration_key: str
    identity: Mapping[str, Any]
    contracts: Mapping[str, Any]

    def provider_declaration(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": json_copy(self.input_schema),
            },
        }

    def document(self) -> dict[str, Any]:
        document = {
            **self.provider_declaration(),
            "selector": self.selector,
            "output_schema": json_copy(self.output_schema),
            "registration": {
                "key": self.registration_key,
                "identity": json_copy(self.identity),
                "contracts": json_copy(self.contracts),
            },
        }
        if self.protocol_version == "v2":
            document.update(
                effect=self.effect,
                max_result_bytes=self.max_result_bytes)
        return document


def _allowlist_rows(
    allowlist: Iterable[ManagedToolSelector | tuple[str, str] | str]
    | Mapping[str, Any],
) -> tuple[ManagedToolSelector, ...]:
    source: Iterable[ManagedToolSelector | tuple[str, str] | str]
    if isinstance(allowlist, Mapping):
        expanded = []
        for name, value in allowlist.items():
            if isinstance(value, str):
                expanded.append((name, value))
            elif isinstance(value, Mapping) and set(value).issubset({
                    "selector", "description", "input_schema"}):
                expanded.append(ManagedToolSelector(
                    name, value.get("selector"), value.get("description"),
                    value.get("input_schema")))
            else:
                raise PluginError(
                    "managed tool mapping values require an exact selector declaration")
        source = tuple(expanded)
    else:
        source = allowlist
    if isinstance(source, (str, bytes)):
        raise PluginError("managed tool allowlist must be an explicit collection")
    try:
        values = tuple(source)
    except TypeError as exc:
        raise PluginError("managed tool allowlist must be iterable") from exc
    rows: list[ManagedToolSelector] = []
    for value in values:
        if isinstance(value, ManagedToolSelector):
            row = value
        elif isinstance(value, str):
            if value.count("/") != 1:
                raise PluginError(
                    "managed tool selector must be plugin_name/operation_name")
            row = ManagedToolSelector(value.split("/", 1)[1], value)
        elif (isinstance(value, tuple) and len(value) == 2
              and all(isinstance(item, str) for item in value)):
            row = ManagedToolSelector(value[0], value[1])
        else:
            raise PluginError(
                "managed tool allowlist entries require a name and exact selector")
        rows.append(row)
    return tuple(rows)


@dataclass(frozen=True, slots=True, init=False)
class ManagedPluginToolCatalog:
    """Immutable allowlist projection of a selected :class:`PluginCatalog`."""

    plugin_catalog_digest: str
    protocol_version: str
    tools: tuple[ManagedToolDeclaration, ...]
    _bindings: Mapping[str, tuple[BoundPlugin, Any]] = field(
        repr=False, compare=False)
    _legacy_declarations: Mapping[str, ManagedToolDeclaration] = field(
        repr=False, compare=False)

    def __init__(
        self,
        plugin_catalog: PluginCatalog,
        allowlist: Iterable[ManagedToolSelector | tuple[str, str] | str]
        | Mapping[str, Any],
        *, admitted_effects: Iterable[str] = ("pure",),
        protocol_version: str = "v2",
    ) -> None:
        if not isinstance(plugin_catalog, PluginCatalog):
            raise PluginError("managed tools require an exact PluginCatalog")
        if protocol_version not in {"v1", "v2"}:
            raise PluginError("managed tool protocol version is unsupported")
        rows = _allowlist_rows(allowlist)
        try:
            effects = tuple(admitted_effects)
        except TypeError as exc:
            raise PluginError("managed admitted effects must be an explicit collection") from exc
        if (not effects or len(set(effects)) != len(effects)
                or any(effect not in {
                    "pure", "external_read", "external_write"}
                       for effect in effects)):
            raise PluginError("managed admitted effects are invalid")
        names = tuple(row.name for row in rows)
        selectors = tuple(row.selector for row in rows)
        if len(set(names)) != len(names):
            raise PluginError("managed provider-visible tool names must be unique")
        if len(set(selectors)) != len(selectors):
            raise PluginError("managed plugin selectors must be unique")

        declarations: list[ManagedToolDeclaration] = []
        bindings: dict[str, tuple[BoundPlugin, Any]] = {}
        legacy_declarations: dict[str, ManagedToolDeclaration] = {}
        for row in rows:
            plugin, operation = plugin_catalog.resolve(row.selector)
            if operation.effect not in effects:
                raise PluginError(
                    "managed tool effect was not explicitly admitted")
            if operation.input_schema.get("type") != "object":
                raise PluginError("managed tools require an object input schema")
            operation_key = plugin_catalog.operation_key(row.selector)
            if (protocol_version == "v1"
                    and (operation.effect != "pure"
                         or row.description is not None
                         or row.input_schema is not None)):
                raise PluginError(
                    "legacy managed v1 supports only the exact pure plugin descriptor")
            input_schema = (
                operation.input_schema if row.input_schema is None
                else row.input_schema)
            description = (
                operation.description if row.description is None
                else row.description)
            descriptor_digest = hashlib.sha256(canonical({
                "name": row.name,
                "description": description,
                "input_schema": input_schema,
            })).hexdigest()

            def declaration(version: str) -> ManagedToolDeclaration:
                legacy = version == "v1"
                selected_description = (
                    operation.description if legacy else description)
                selected_input = (
                    operation.input_schema if legacy else input_schema)
                managed_binding = {
                    "selector": row.selector,
                    "plugin_name": plugin.definition.name,
                    "plugin_version": plugin.definition.version,
                    "binding_digest": plugin.digest,
                    "operation": operation.descriptor(),
                    **({} if legacy else {
                        "capability": capability_value(plugin, operation),
                    }),
                }
                registration_key = (
                    f"{operation_key}/managed-tool/v1/{row.name}"
                    if legacy else
                    f"{operation_key}/managed-tool/v2/{row.name}/"
                    f"{descriptor_digest}")
                identity = {
                    "implementation_id": "rpnh.managed_native_plugin_tool",
                    "revision": version,
                    "provider_name": row.name,
                    "selector": row.selector,
                    "plugin_version": plugin.definition.version,
                    "binding_digest": plugin.digest,
                    "plugin_catalog_digest": plugin_catalog.digest,
                    **({} if legacy else {"effect": operation.effect}),
                }
                contracts = {
                    "invocation_protocol": (
                        LEGACY_INVOCATION_PROTOCOL
                        if legacy else INVOCATION_PROTOCOL),
                    "provider_name": row.name,
                    "selector": row.selector,
                    "plugin_catalog_digest": plugin_catalog.digest,
                    "managed_plugin": managed_binding,
                    "input_schema": json_copy(selected_input),
                    "output_schema": json_copy(operation.output_schema),
                    **({} if legacy else {
                        "effect": operation.effect,
                        "max_result_bytes": operation.max_result_bytes,
                    }),
                }
                return ManagedToolDeclaration(
                    name=row.name,
                    selector=row.selector,
                    description=selected_description,
                    input_schema=frozen(selected_input),
                    output_schema=frozen(operation.output_schema),
                    effect=operation.effect,
                    max_result_bytes=operation.max_result_bytes,
                    protocol_version=version,
                    registration_key=registration_key,
                    identity=frozen(identity),
                    contracts=frozen(contracts),
                )

            current = declaration(protocol_version)
            declarations.append(current)
            if (protocol_version == "v2" and operation.effect == "pure"
                    and row.description is None and row.input_schema is None):
                legacy_declarations[row.name] = declaration("v1")
            bindings[row.name] = (plugin, operation)

        object.__setattr__(self, "plugin_catalog_digest", plugin_catalog.digest)
        object.__setattr__(self, "protocol_version", protocol_version)
        object.__setattr__(self, "tools", tuple(declarations))
        object.__setattr__(self, "_bindings", MappingProxyType(bindings))
        object.__setattr__(
            self, "_legacy_declarations",
            MappingProxyType(legacy_declarations))

    @property
    def provider_declarations(self) -> tuple[dict[str, Any], ...]:
        return tuple(tool.provider_declaration() for tool in self.tools)

    def declaration(self, name: str) -> ManagedToolDeclaration:
        for declaration in self.tools:
            if declaration.name == name:
                return declaration
        raise PluginError("tool name is outside the managed plugin allowlist")

    def legacy_declaration(self, name: str) -> ManagedToolDeclaration:
        try:
            return self._legacy_declarations[name]
        except KeyError as exc:
            raise PluginError(
                "tool has no exact legacy managed v1 declaration") from exc

    def declaration_for_registration_key(
            self, registration_key: str) -> ManagedToolDeclaration:
        declarations = (*self.tools, *self._legacy_declarations.values())
        for declaration in declarations:
            if declaration.registration_key == registration_key:
                return declaration
        raise PluginError("registration key is outside the managed plugin catalog")

    def registration_declarations(
            self, *, include_legacy: bool = False,
    ) -> tuple[ManagedToolDeclaration, ...]:
        return self.tools + (
            tuple(self._legacy_declarations.values())
            if include_legacy else ())

    def binding(self, name: str) -> tuple[BoundPlugin, Any]:
        self.declaration(name)
        return self._bindings[name]

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": (
                LEGACY_CATALOG_SCHEMA_VERSION
                if self.protocol_version == "v1" else
                CATALOG_SCHEMA_VERSION),
            "plugin_catalog_digest": self.plugin_catalog_digest,
            "tools": [tool.document() for tool in self.tools],
        }


@dataclass(frozen=True, slots=True)
class _RegisteredManagedTool:
    name: str
    plugin_catalog_digest: str
    registration_key: str

    def __call__(self, *, service: "ManagedPluginInvocationService",
                 execution: Any, call_id: str, arguments: Mapping[str, Any],
                 interruption_requested=None) -> Mapping[str, Any]:
        if (not isinstance(service, ManagedPluginInvocationService)
                or service.catalog.plugin_catalog_digest
                != self.plugin_catalog_digest):
            raise PluginError(
                "managed invocation service differs from registered catalog")
        return service.invoke(
            self.name, execution=execution, call_id=call_id,
            arguments=arguments,
            interruption_requested=interruption_requested,
            registration_key=self.registration_key)


class ManagedPluginToolAdapter:
    """Register the immutable managed-tool declarations with a HOST Registry."""

    def __init__(self, catalog: ManagedPluginToolCatalog) -> None:
        if not isinstance(catalog, ManagedPluginToolCatalog):
            raise TypeError("managed plugin adapter requires its immutable catalog")
        self.catalog = catalog

    def register(
            self, registration: Any, *, registration_keys=None,
            include_legacy: bool = False,
    ) -> None:
        register = getattr(registration, "register_tool", None)
        if not callable(register):
            raise TypeError("managed tools require a Registry Registration")
        available = self.catalog.registration_declarations(
            include_legacy=include_legacy)
        selected = (
            {tool.registration_key for tool in available}
            if registration_keys is None else set(registration_keys))
        if selected - {tool.registration_key for tool in available}:
            raise PluginError("managed registration selection is outside the catalog")
        for declaration in available:
            if declaration.registration_key not in selected:
                continue
            register(
                declaration.registration_key,
                _RegisteredManagedTool(
                    declaration.name, self.catalog.plugin_catalog_digest,
                    declaration.registration_key),
                identity=declaration.identity,
                contracts=declaration.contracts,
            )


def _resource_ref_payload(ref: Any) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }


class ManagedPluginInvocationService:
    """Owner-bound durable execution service for managed native-plugin tools.

    The started receipt is a durable single-dispatch authorization. A crash
    after external worker dispatch but before a terminal receipt intentionally
    requires reconciliation; this contract does not claim physical exactly-once
    execution across such a crash.
    """

    # One RunOwner/core is process-exclusive. Services reconstructed over that
    # same live owner still share this small coordinator, so a duplicate
    # observer waits for the active executor instead of misclassifying its
    # durable started receipt as executor loss.
    _active_lock = Lock()
    _active_calls: dict[tuple[int, str], Event] = {}

    def __init__(self, owner: Any, kernel: Any, repository: Any,
                 catalog: ManagedPluginToolCatalog) -> None:
        from cpn.rpnh.run import RunOwner
        from cpn.rpnh.registry.operation_repository import (
            RegistryOperationAuthorityRepository,
        )
        from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
        if (not isinstance(owner, RunOwner)
                or not isinstance(kernel, _ResourceServiceKernel)
                or not isinstance(repository, RegistryOperationAuthorityRepository)
                or not isinstance(catalog, ManagedPluginToolCatalog)):
            raise TypeError(
                "managed invocation service requires one owner/kernel/repository/catalog")
        if (getattr(kernel, "_ResourceServiceKernel__core", None) is not owner._core
                or getattr(repository,
                    "_RegistryOperationAuthorityRepository__core", None)
                is not owner._core
                or getattr(repository,
                    "_RegistryOperationAuthorityRepository__kernel", None)
                is not kernel):
            raise TypeError(
                "managed invocation service must use the owner's exact repository")
        self.owner = owner
        self.core = owner._core
        self.kernel = kernel
        self.repository = repository
        self.catalog = catalog

    def _enter_active_call(self, key: str) -> tuple[Event, bool]:
        active_key = (id(self.core), key)
        with self._active_lock:
            event = self._active_calls.get(active_key)
            if event is not None:
                return event, False
            event = Event()
            self._active_calls[active_key] = event
            return event, True

    def _leave_active_call(self, key: str, event: Event) -> None:
        active_key = (id(self.core), key)
        with self._active_lock:
            if self._active_calls.get(active_key) is event:
                self._active_calls.pop(active_key)
                event.set()

    def _authorize(self, execution: Any, declaration: ManagedToolDeclaration):
        from cpn.rpnh.executable_net import load_compiled_net
        from cpn.rpnh.registry.operation_execution import verify_operation_execution
        from cpn.rpnh.registry.operations import OperationAuthorityError
        from cpn.rpnh.registry.publication import _resource_from_payload

        execution = verify_operation_execution(
            self.core, self.kernel, self.repository, execution)
        operation = execution.operation
        if declaration.registration_key not in operation.spec.allowed_tool_ids:
            raise OperationAuthorityError(
                "managed tool is outside this caller operation's declared tools")
        binding = self.kernel._exact_object(
            operation.transition.binding_ref,
            expected_type="executable_transition_binding/v1")
        source = _resource_from_payload(
            binding.metadata["declaration_resource_ref"])
        compiled = load_compiled_net(json.loads(
            self.kernel._read_firing_registered(
                operation.canonical.context, source)))
        declared = next(
            item for item in compiled.operations
            if item.operation_id == operation.spec.operation_id)
        if tuple(declared.declaration.tools) != operation.spec.allowed_tool_ids:
            raise OperationAuthorityError(
                "managed caller tool inventory differs from admitted operation")
        registered = self.owner.registration.declaration(
            "tool", declaration.registration_key)
        expected = {
            "kind": "tool",
            "key": declaration.registration_key,
            "identity": json_copy(declaration.identity),
            "contracts": json_copy(declaration.contracts),
        }
        implementation = self.owner.registration.resolve(
            "tool", declaration.registration_key)
        if (registered != expected
                or compiled.registrations["tool"].get(
                    declaration.registration_key) != registered
                or not isinstance(implementation, _RegisteredManagedTool)
                or implementation.name != declaration.name
                or implementation.registration_key
                != declaration.registration_key
                or implementation.plugin_catalog_digest
                != self.catalog.plugin_catalog_digest):
            raise OperationAuthorityError(
                "managed tool differs from exact HOST registration")
        return execution

    def _call_material(self, execution: Any, declaration: ManagedToolDeclaration,
                       call_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from cpn.rpnh.registry.strict_contracts import ref_payload
        context = execution.operation.canonical.context
        return {
            "schema_version": (
                LEGACY_RECEIPT_SCHEMA_VERSION
                if declaration.protocol_version == "v1" else
                RECEIPT_SCHEMA_VERSION),
            "caller_execution_ref": ref_payload(
                execution.operation_execution_lease_ref),
            "caller_firing_ref": ref_payload(
                execution.operation.firing.transition_firing_ref),
            "call_id": call_id,
            "registration": {
                "key": declaration.registration_key,
                "identity": json_copy(declaration.identity),
                "contracts": json_copy(declaration.contracts),
            },
            "selector": declaration.selector,
            "plugin_catalog_digest": self.catalog.plugin_catalog_digest,
            "binding_digest": declaration.identity["binding_digest"],
            "arguments": json_copy(arguments),
            "producer_invocation_ref": ref_payload(context.invocation_ref),
        }

    def _refs(self, execution: Any, call_id: str):
        from cpn.rpnh.registry.publication import _stable_id
        from cpn.rpnh.registry.resources import ResourceVersionRef
        key = (
            f"managed-plugin:{execution.operation_execution_lease_ref.version_id}:"
            f"{execution.operation.firing.transition_firing_ref.version_id}:{call_id}")
        logical = _stable_id("resource", key)
        return key, {
            phase: ResourceVersionRef(
                logical, _stable_id("resource_version", key, phase))
        for phase in ("started", "returned", "failed", "outcome_unknown")
        }

    def _read_receipt(self, execution: Any, key: str, ref: Any,
                      phase: str) -> dict[str, Any] | None:
        from cpn.rpnh.registry.strict_contracts import ref_payload
        row = self.core.event_store.object_row(ref.resource_version_id)
        if row is None:
            return None
        prepared = self.core.get_version(ref.resource_version_id)
        context = execution.operation.canonical.context
        metadata = prepared.metadata
        if (prepared.object_type != "resource_version/v1"
                or prepared.logical_id != ref.resource_id
                or metadata.get("producer_ref") != ref_payload(
                    context.invocation_ref)
                or metadata.get("lifetime_ref") != ref_payload(
                    execution.operation_execution_lease_ref)
                or metadata.get("descriptors") != {
                    "managed_plugin_call": key, "phase": phase}):
            raise ManagedPluginInvocationConflict(
                "managed invocation receipt differs from exact caller provenance")
        try:
            payload = self.core.object_store.read_registered(prepared)
            document = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ManagedPluginInvocationConflict(
                "managed invocation receipt is not canonical JSON") from exc
        if (not isinstance(document, dict) or canonical(document) != payload
                or document.get("state") != phase):
            raise ManagedPluginInvocationConflict(
                "managed invocation receipt has a different terminal state")
        return document

    def _publish_receipt(self, execution: Any, key: str, ref: Any,
                         phase: str, document: Mapping[str, Any], *,
                         started_ref: Any | None = None,
                         unique_claim: str | None = None) -> None:
        from cpn.rpnh.registry.publication import (
            _append_direct_resource_version_publication,
            _direct_resource_metadata,
        )
        context = execution.operation.canonical.context
        idempotency_key = (
            f"{key}:{phase}:{unique_claim}" if unique_claim is not None
            else f"{key}:{phase}")
        payload = canonical(document)
        inputs = () if started_ref is None else (started_ref,)
        tx = self.core.begin(
            idempotency_key=idempotency_key,
            task_round_id=context.task_round_ref.entity_id,
            net_instance_id=context.net_instance_ref.entity_id)

        def metadata(size: int) -> dict[str, Any]:
            value = _direct_resource_metadata(
                self.core, ref=ref,
                origin_kind="managed_native_plugin_invocation",
                primary=execution.operation_execution_lease_ref,
                secondary=context.invocation_ref,
                task_ref=context.task_ref,
                round_ref=context.task_round_ref,
                net_ref=context.net_instance_ref,
                producer_ref=context.invocation_ref,
                lifetime_ref=execution.operation_execution_lease_ref,
                operation_binding_ref=context.operation_binding_ref,
                agent_loop_ref=context.invocation_ref,
                payload_size=size, media_type="application/json",
                content_schema_ref=None,
                content_schema_authority_ref=None,
                summary=f"Managed native plugin call {phase}",
                descriptors={"managed_plugin_call": key, "phase": phase},
                extensions={}, input_resource_refs=inputs,
                intended_boundary="not_applicable")
            provenance = value["reference_provenance"]
            provenance["invocation_ref"] = provenance.pop("agent_loop_ref")
            return value

        _append_direct_resource_version_publication(
            tx, ref=ref, payload=payload, metadata_factory=metadata,
            media_type="application/json",
            producer_ref=context.invocation_ref,
            producer_invocation_id=context.invocation_ref.entity_id,
            relation_key=idempotency_key,
            direct_owners=(context.operation_binding_ref,
                           execution.operation_execution_lease_ref),
            input_resources=inputs,
            supersedes=started_ref)
        tx.commit()

    def _existing(self, execution: Any, declaration: ManagedToolDeclaration,
                  call_id: str, arguments: Mapping[str, Any], key: str,
                  refs: Mapping[str, Any]) -> Mapping[str, Any] | None:
        expected = self._call_material(
            execution, declaration, call_id, arguments)
        started = self._read_receipt(
            execution, key, refs["started"], "started")
        returned = self._read_receipt(
            execution, key, refs["returned"], "returned")
        failed = self._read_receipt(
            execution, key, refs["failed"], "failed")
        unknown = (
            None if declaration.protocol_version == "v1" else
            self._read_receipt(
                execution, key, refs["outcome_unknown"], "outcome_unknown"))
        if started is None:
            if returned is not None or failed is not None or unknown is not None:
                raise ManagedPluginInvocationConflict(
                    "managed invocation terminal receipt lacks its started claim")
            return None
        legacy = declaration.protocol_version == "v1"
        admitted_at = None if legacy else started.get("admitted_at_utc")
        expected_started = {
            **expected,
            "state": "started",
            **({} if legacy else {"admitted_at_utc": admitted_at}),
        }
        if (canonical(started) != canonical(expected_started)
                or (not legacy
                    and (not isinstance(admitted_at, str)
                         or not admitted_at))):
            raise ManagedPluginInvocationConflict(
                "managed call id was reused with different tool or arguments")
        if sum(value is not None for value in (returned, failed, unknown)) > 1:
            raise ManagedPluginInvocationConflict(
                "managed invocation has conflicting terminal receipts")
        terminal_base = {
            **expected,
            "started_receipt_ref": _resource_ref_payload(refs["started"]),
            **({} if legacy else {"admitted_at_utc": admitted_at}),
        }
        if returned is not None:
            if (set(returned) != set(terminal_base) | {"state", "output"}
                    or any(canonical(returned.get(name)) != canonical(value)
                           for name, value in terminal_base.items())):
                raise ManagedPluginInvocationConflict(
                    "managed returned receipt differs from its started claim")
            output = validate(declaration.output_schema, returned["output"])
            return self._result(
                declaration, call_id, output, refs["started"], refs["returned"],
                admitted_at)
        if failed is not None:
            if (set(failed) != set(terminal_base) | {"state", "error_code"}
                    or any(canonical(failed.get(name)) != canonical(value)
                           for name, value in terminal_base.items())
                    or not isinstance(failed.get("error_code"), str)
                    or not failed["error_code"]):
                raise ManagedPluginInvocationConflict(
                    "managed failed receipt differs from its started claim")
            raise ManagedPluginInvocationFailed(
                failed["error_code"], evidence=self._failure_result(
                    declaration, call_id, failed["error_code"], "failed",
                    refs["started"], refs["failed"], admitted_at))
        if unknown is not None:
            if (set(unknown) != set(terminal_base) | {"state", "error_code"}
                    or any(canonical(unknown.get(name)) != canonical(value)
                           for name, value in terminal_base.items())
                    or not isinstance(unknown.get("error_code"), str)
                    or not unknown["error_code"]):
                raise ManagedPluginInvocationConflict(
                    "managed outcome-unknown receipt differs from its started claim")
            raise ManagedPluginInvocationReconciliationRequired(
                "managed invocation outcome requires reconciliation",
                evidence=self._failure_result(
                    declaration, call_id, unknown["error_code"],
                    "outcome_unknown", refs["started"],
                    refs["outcome_unknown"], admitted_at))
        if not legacy:
            error_code = "managed_terminal_observation_missing"
            self._terminal(
                execution, declaration, call_id, arguments, key, refs,
                "outcome_unknown", error_code=error_code)
            raise ManagedPluginInvocationReconciliationRequired(
                "managed invocation outcome requires reconciliation",
                evidence=self._failure_result(
                    declaration, call_id, error_code, "outcome_unknown",
                    refs["started"], refs["outcome_unknown"], admitted_at))
        raise ManagedPluginInvocationReconciliationRequired(
            "managed invocation was dispatched without a terminal observation",
            evidence=None)

    def _claim(self, execution: Any, declaration: ManagedToolDeclaration,
               call_id: str, arguments: Mapping[str, Any], key: str,
               refs: Mapping[str, Any]) -> bool:
        from cpn.rpnh.registry.event_store import RegistryConflict
        from cpn.rpnh.registry.identities import new_id
        from datetime import datetime, timezone
        document = {
            **self._call_material(execution, declaration, call_id, arguments),
            "state": "started",
            **({} if declaration.protocol_version == "v1" else {
                "admitted_at_utc": datetime.now(timezone.utc).isoformat(
                    timespec="microseconds").replace("+00:00", "Z"),
            }),
        }
        try:
            self._publish_receipt(
                execution, key, refs["started"], "started", document,
                unique_claim=str(new_id("transaction")))
            return True
        except RegistryConflict:
            return False

    def _packet(self, execution: Any, selector: str, plugin: BoundPlugin,
                operation: Any, call_id: str,
                arguments: Mapping[str, Any]) -> dict[str, Any]:
        resources = {item.name: item for item in plugin.definition.resources}
        context = execution.operation.canonical.context
        views = tuple(ResourceView(
            name,
            resources[name].payload,
            resources[name].media_type,
            f"managed-plugin-resource:{plugin.definition.name}:{name}",
            plugin.digest,
        ) for name in operation.resources)
        return {
            "context": {
                "config": json_copy(plugin.config),
                "resources": views,
                "operation_id": selector,
                "invocation_id": str(context.invocation_ref.version_id),
                "firing_id": str(
                    execution.operation.firing.transition_firing_ref.version_id),
                "call_id": call_id,
            },
            "arguments": json_copy(arguments),
            "max_result_bytes": operation.max_result_bytes,
            "implementation": json_copy(operation.implementation),
        }

    def _terminal(self, execution: Any, declaration: ManagedToolDeclaration,
                  call_id: str, arguments: Mapping[str, Any], key: str,
                  refs: Mapping[str, Any], phase: str, **value: Any) -> None:
        started = self._read_receipt(
            execution, key, refs["started"], "started")
        if started is None:
            raise ManagedPluginInvocationConflict(
                "managed terminal observation lacks its admission receipt")
        document = {
            **self._call_material(execution, declaration, call_id, arguments),
            "state": phase,
            "started_receipt_ref": _resource_ref_payload(refs["started"]),
            **({} if declaration.protocol_version == "v1" else {
                "admitted_at_utc": started["admitted_at_utc"],
            }),
            **value,
        }
        self._publish_receipt(
            execution, key, refs[phase], phase, document,
            started_ref=refs["started"])

    @staticmethod
    def _result(declaration: ManagedToolDeclaration, call_id: str, output: Any,
                started_ref: Any, terminal_ref: Any,
                admitted_at_utc: str | None = None) -> dict[str, Any]:
        result = {
            "schema_version": (
                LEGACY_RESULT_SCHEMA_VERSION
                if declaration.protocol_version == "v1" else
                RESULT_SCHEMA_VERSION),
            "name": declaration.name,
            "selector": declaration.selector,
            "call_id": call_id,
            "output": json_copy(output),
            "started_receipt_ref": _resource_ref_payload(started_ref),
            "terminal_receipt_ref": _resource_ref_payload(terminal_ref),
        }
        if declaration.protocol_version == "v2":
            result.update(
                effect=declaration.effect,
                outcome="returned",
                admitted_at_utc=admitted_at_utc)
        return result

    @staticmethod
    def _failure_result(
            declaration: ManagedToolDeclaration, call_id: str, code: str,
            outcome: str, started_ref: Any,
            terminal_ref: Any | None,
            admitted_at_utc: str | None) -> dict[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "name": declaration.name,
            "selector": declaration.selector,
            "call_id": call_id,
            "effect": declaration.effect,
            "outcome": outcome,
            "error": {"code": code},
            "admitted_at_utc": admitted_at_utc,
            "started_receipt_ref": _resource_ref_payload(started_ref),
            "terminal_receipt_ref": (
                None if terminal_ref is None
                else _resource_ref_payload(terminal_ref)),
        }

    def invoke(self, name: str, *, execution: Any, call_id: str,
               arguments: Mapping[str, Any],
               interruption_requested=None,
               registration_key: str | None = None) -> Mapping[str, Any]:
        declaration, plugin, operation, arguments = self.validate_arguments(
            name, arguments, registration_key=registration_key)
        if (not isinstance(call_id, str) or not call_id.strip()
                or len(call_id) > 512):
            raise PluginError(
                "managed tool call id must be a nonempty bounded string")
        if interruption_requested is None:
            interruption_requested = lambda: False
        if not callable(interruption_requested):
            raise TypeError("interruption probe must be callable")

        execution = self._authorize(execution, declaration)
        key, refs = self._refs(execution, call_id)
        active, owns_call = self._enter_active_call(key)
        if not owns_call:
            active.wait()
            replay = self._existing(
                execution, declaration, call_id, arguments, key, refs)
            assert replay is not None
            return replay
        try:
            return self._invoke_owned(
                declaration, plugin, operation, execution, call_id,
                arguments, interruption_requested, key, refs)
        finally:
            self._leave_active_call(key, active)

    def _invoke_owned(
            self, declaration: ManagedToolDeclaration, plugin: BoundPlugin,
            operation: Any, execution: Any, call_id: str,
            arguments: Mapping[str, Any], interruption_requested: Any,
            key: str, refs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self.core.event_store.object_row(
                refs["started"].resource_version_id) is not None:
            replay = self._existing(
                execution, declaration, call_id, arguments, key, refs)
            assert replay is not None
            return replay
        if not self._claim(
                execution, declaration, call_id, arguments, key, refs):
            replay = self._existing(
                execution, declaration, call_id, arguments, key, refs)
            assert replay is not None
            return replay

        from .worker import WorkerFailure, execute_worker
        try:
            output = execute_worker(
                operation.handler,
                self._packet(
                    execution, declaration.selector, plugin, operation,
                    call_id, arguments),
                environment_names=plugin.environment,
                timeout_seconds=operation.timeout_seconds,
                cancelled=interruption_requested)
            output = validate(declaration.output_schema, output)
        except (WorkerFailure, PluginError) as exc:
            code = (
                exc.code if isinstance(exc, WorkerFailure)
                else "output_schema_mismatch")
            phase = (
                "outcome_unknown"
                if (declaration.effect == "external_write"
                    and (not isinstance(exc, WorkerFailure)
                         or exc.may_have_executed))
                else "failed")
            self._terminal(
                execution, declaration, call_id, arguments, key, refs,
                phase, error_code=code)
            if declaration.protocol_version == "v1":
                raise ManagedPluginInvocationFailed(code) from exc
            evidence = self._failure_result(
                declaration, call_id, code, phase, refs["started"],
                refs[phase], self._read_receipt(
                    execution, key, refs["started"], "started")[
                        "admitted_at_utc"])
            if phase == "outcome_unknown":
                raise ManagedPluginInvocationReconciliationRequired(
                    "managed external write outcome requires reconciliation",
                    evidence=evidence) from exc
            raise ManagedPluginInvocationFailed(
                code, evidence=evidence) from exc

        self._terminal(
            execution, declaration, call_id, arguments, key, refs,
            "returned", output=output)
        started = self._read_receipt(
            execution, key, refs["started"], "started")
        assert started is not None
        admitted_at = (
            None if declaration.protocol_version == "v1"
            else started["admitted_at_utc"])
        return self._result(
            declaration, call_id, output, refs["started"], refs["returned"],
            admitted_at)

    def validate_arguments(
            self, name: str, arguments: Mapping[str, Any], *,
            registration_key: str | None = None,
    ) -> tuple[ManagedToolDeclaration, BoundPlugin, Any, dict[str, Any]]:
        """Validate both the provider declaration and plugin operation input."""
        declaration = (
            self.catalog.declaration(name)
            if registration_key is None else
            self.catalog.declaration_for_registration_key(registration_key))
        if declaration.name != name:
            raise PluginError("managed registration key differs from tool name")
        plugin, operation = self.catalog.binding(name)
        if not isinstance(arguments, Mapping):
            raise PluginError(
                "managed tool arguments must be a parsed JSON object")
        arguments = json_copy(arguments)
        if not isinstance(arguments, dict):
            raise PluginError(
                "managed tool arguments must be a parsed JSON object")
        arguments = validate(declaration.input_schema, arguments)
        arguments = validate(operation.input_schema, arguments)
        return declaration, plugin, operation, arguments


def build_managed_plugin_tool_catalog(
    plugin_catalog: PluginCatalog,
    allowlist: Iterable[ManagedToolSelector | tuple[str, str] | str]
    | Mapping[str, Any],
    *, admitted_effects: Iterable[str] = ("pure",),
    protocol_version: str = "v2",
) -> ManagedPluginToolCatalog:
    return ManagedPluginToolCatalog(
        plugin_catalog, allowlist, admitted_effects=admitted_effects,
        protocol_version=protocol_version)


__all__ = (
    "CATALOG_SCHEMA_VERSION",
    "LEGACY_CATALOG_SCHEMA_VERSION",
    "INVOCATION_PROTOCOL",
    "LEGACY_INVOCATION_PROTOCOL",
    "RECEIPT_SCHEMA_VERSION",
    "LEGACY_RECEIPT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "LEGACY_RESULT_SCHEMA_VERSION",
    "ManagedToolSelector",
    "ManagedToolDeclaration",
    "ManagedPluginToolCatalog",
    "ManagedPluginToolAdapter",
    "ManagedPluginInvocationService",
    "ManagedPluginInvocationConflict",
    "ManagedPluginInvocationReconciliationRequired",
    "ManagedPluginInvocationFailed",
    "build_managed_plugin_tool_catalog",
)
