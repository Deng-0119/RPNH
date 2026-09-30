"""Managed provider tools backed by explicitly selected native plugins.

The immutable catalog projects only an explicit allowlist. Invocation is an
owner-bound service over an arbitrary admitted caller operation; it does not
pretend that caller is a native-plugin component firing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .api import (
    PluginError, ResourceView, canonical, frozen, json_copy, symbol, validate,
)
from .catalog import BoundPlugin, PluginCatalog


CATALOG_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_catalog/v1"
INVOCATION_PROTOCOL = "rpnh/managed_native_plugin_tool_invocation/v1"
RECEIPT_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_receipt/v1"
RESULT_SCHEMA_VERSION = "rpnh/managed_native_plugin_tool_result/v1"


class ManagedPluginInvocationConflict(PluginError):
    """A durable call identity was reused with different exact material."""


class ManagedPluginInvocationReconciliationRequired(PluginError):
    """A durable dispatch claim exists without a terminal observation."""


class ManagedPluginInvocationFailed(PluginError):
    """A managed invocation has a durable failed terminal state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"managed native plugin execution failed: {code}")


@dataclass(frozen=True, slots=True)
class ManagedToolSelector:
    """One provider-visible name mapped to one exact plugin selector."""

    name: str
    selector: str

    def __post_init__(self) -> None:
        symbol(self.name)
        if not isinstance(self.selector, str) or self.selector.count("/") != 1:
            raise PluginError(
                "managed tool selector must be plugin_name/operation_name")


@dataclass(frozen=True, slots=True)
class ManagedToolDeclaration:
    """Immutable provider and Registry declaration for one managed tool."""

    name: str
    selector: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
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
        return {
            **self.provider_declaration(),
            "selector": self.selector,
            "output_schema": json_copy(self.output_schema),
            "registration": {
                "key": self.registration_key,
                "identity": json_copy(self.identity),
                "contracts": json_copy(self.contracts),
            },
        }


def _allowlist_rows(
    allowlist: Iterable[ManagedToolSelector | tuple[str, str] | str]
    | Mapping[str, str],
) -> tuple[ManagedToolSelector, ...]:
    source: Iterable[ManagedToolSelector | tuple[str, str] | str]
    source = tuple(allowlist.items()) if isinstance(allowlist, Mapping) else allowlist
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
    tools: tuple[ManagedToolDeclaration, ...]
    _bindings: Mapping[str, tuple[BoundPlugin, Any]] = field(
        repr=False, compare=False)

    def __init__(
        self,
        plugin_catalog: PluginCatalog,
        allowlist: Iterable[ManagedToolSelector | tuple[str, str] | str]
        | Mapping[str, str],
    ) -> None:
        if not isinstance(plugin_catalog, PluginCatalog):
            raise PluginError("managed tools require an exact PluginCatalog")
        rows = _allowlist_rows(allowlist)
        names = tuple(row.name for row in rows)
        selectors = tuple(row.selector for row in rows)
        if len(set(names)) != len(names):
            raise PluginError("managed provider-visible tool names must be unique")
        if len(set(selectors)) != len(selectors):
            raise PluginError("managed plugin selectors must be unique")

        declarations: list[ManagedToolDeclaration] = []
        bindings: dict[str, tuple[BoundPlugin, Any]] = {}
        for row in rows:
            plugin, operation = plugin_catalog.resolve(row.selector)
            if operation.effect != "pure":
                raise PluginError("managed tools support pure plugin operations only")
            if operation.input_schema.get("type") != "object":
                raise PluginError("managed tools require an object input schema")
            operation_key = plugin_catalog.operation_key(row.selector)
            managed_binding = {
                "selector": row.selector,
                "plugin_name": plugin.definition.name,
                "plugin_version": plugin.definition.version,
                "binding_digest": plugin.digest,
                "operation": operation.descriptor(),
            }
            registration_key = f"{operation_key}/managed-tool/v1/{row.name}"
            identity = frozen({
                "implementation_id": "rpnh.managed_native_plugin_tool",
                "revision": "v1",
                "provider_name": row.name,
                "selector": row.selector,
                "plugin_version": plugin.definition.version,
                "binding_digest": plugin.digest,
                "plugin_catalog_digest": plugin_catalog.digest,
            })
            contracts = frozen({
                "invocation_protocol": INVOCATION_PROTOCOL,
                "provider_name": row.name,
                "selector": row.selector,
                "plugin_catalog_digest": plugin_catalog.digest,
                "managed_plugin": managed_binding,
                "input_schema": json_copy(operation.input_schema),
                "output_schema": json_copy(operation.output_schema),
            })
            declarations.append(ManagedToolDeclaration(
                name=row.name,
                selector=row.selector,
                description=operation.description,
                input_schema=frozen(operation.input_schema),
                output_schema=frozen(operation.output_schema),
                registration_key=registration_key,
                identity=identity,
                contracts=contracts,
            ))
            bindings[row.name] = (plugin, operation)

        object.__setattr__(self, "plugin_catalog_digest", plugin_catalog.digest)
        object.__setattr__(self, "tools", tuple(declarations))
        object.__setattr__(self, "_bindings", MappingProxyType(bindings))

    @property
    def provider_declarations(self) -> tuple[dict[str, Any], ...]:
        return tuple(tool.provider_declaration() for tool in self.tools)

    def declaration(self, name: str) -> ManagedToolDeclaration:
        for declaration in self.tools:
            if declaration.name == name:
                return declaration
        raise PluginError("tool name is outside the managed plugin allowlist")

    def binding(self, name: str) -> tuple[BoundPlugin, Any]:
        self.declaration(name)
        return self._bindings[name]

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "plugin_catalog_digest": self.plugin_catalog_digest,
            "tools": [tool.document() for tool in self.tools],
        }


@dataclass(frozen=True, slots=True)
class _RegisteredManagedTool:
    name: str
    plugin_catalog_digest: str

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
            interruption_requested=interruption_requested)


class ManagedPluginToolAdapter:
    """Register the immutable managed-tool declarations with a HOST Registry."""

    def __init__(self, catalog: ManagedPluginToolCatalog) -> None:
        if not isinstance(catalog, ManagedPluginToolCatalog):
            raise TypeError("managed plugin adapter requires its immutable catalog")
        self.catalog = catalog

    def register(self, registration: Any) -> None:
        register = getattr(registration, "register_tool", None)
        if not callable(register):
            raise TypeError("managed tools require a Registry Registration")
        for declaration in self.catalog.tools:
            register(
                declaration.registration_key,
                _RegisteredManagedTool(
                    declaration.name, self.catalog.plugin_catalog_digest),
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
            "schema_version": RECEIPT_SCHEMA_VERSION,
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
            for phase in ("started", "returned", "failed")
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
        if started is None:
            if returned is not None or failed is not None:
                raise ManagedPluginInvocationConflict(
                    "managed invocation terminal receipt lacks its started claim")
            return None
        if canonical(started) != canonical({**expected, "state": "started"}):
            raise ManagedPluginInvocationConflict(
                "managed call id was reused with different tool or arguments")
        if returned is not None and failed is not None:
            raise ManagedPluginInvocationConflict(
                "managed invocation has conflicting terminal receipts")
        terminal_base = {
            **expected,
            "started_receipt_ref": _resource_ref_payload(refs["started"]),
        }
        if returned is not None:
            if (set(returned) != set(terminal_base) | {"state", "output"}
                    or any(canonical(returned.get(name)) != canonical(value)
                           for name, value in terminal_base.items())):
                raise ManagedPluginInvocationConflict(
                    "managed returned receipt differs from its started claim")
            output = validate(declaration.output_schema, returned["output"])
            return self._result(
                declaration, call_id, output, refs["started"], refs["returned"])
        if failed is not None:
            if (set(failed) != set(terminal_base) | {"state", "error_code"}
                    or any(canonical(failed.get(name)) != canonical(value)
                           for name, value in terminal_base.items())
                    or not isinstance(failed.get("error_code"), str)
                    or not failed["error_code"]):
                raise ManagedPluginInvocationConflict(
                    "managed failed receipt differs from its started claim")
            raise ManagedPluginInvocationFailed(failed["error_code"])
        raise ManagedPluginInvocationReconciliationRequired(
            "managed invocation was dispatched without a terminal observation")

    def _claim(self, execution: Any, declaration: ManagedToolDeclaration,
               call_id: str, arguments: Mapping[str, Any], key: str,
               refs: Mapping[str, Any]) -> bool:
        from cpn.rpnh.registry.event_store import RegistryConflict
        from cpn.rpnh.registry.identities import new_id
        document = {
            **self._call_material(execution, declaration, call_id, arguments),
            "state": "started",
        }
        try:
            self._publish_receipt(
                execution, key, refs["started"], "started", document,
                unique_claim=str(new_id("transaction")))
            return True
        except RegistryConflict:
            replay = self._existing(
                execution, declaration, call_id, arguments, key, refs)
            if replay is not None:
                return False
            raise

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
        document = {
            **self._call_material(execution, declaration, call_id, arguments),
            "state": phase,
            "started_receipt_ref": _resource_ref_payload(refs["started"]),
            **value,
        }
        self._publish_receipt(
            execution, key, refs[phase], phase, document,
            started_ref=refs["started"])

    @staticmethod
    def _result(declaration: ManagedToolDeclaration, call_id: str, output: Any,
                started_ref: Any, terminal_ref: Any) -> dict[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "name": declaration.name,
            "selector": declaration.selector,
            "call_id": call_id,
            "output": json_copy(output),
            "started_receipt_ref": _resource_ref_payload(started_ref),
            "terminal_receipt_ref": _resource_ref_payload(terminal_ref),
        }

    def invoke(self, name: str, *, execution: Any, call_id: str,
               arguments: Mapping[str, Any],
               interruption_requested=None) -> Mapping[str, Any]:
        declaration = self.catalog.declaration(name)
        plugin, operation = self.catalog.binding(name)
        if (not isinstance(call_id, str) or not call_id.strip()
                or len(call_id) > 512):
            raise PluginError(
                "managed tool call id must be a nonempty bounded string")
        if not isinstance(arguments, Mapping):
            raise PluginError(
                "managed tool arguments must be a parsed JSON object")
        arguments = json_copy(arguments)
        if not isinstance(arguments, dict):
            raise PluginError(
                "managed tool arguments must be a parsed JSON object")
        arguments = validate(declaration.input_schema, arguments)
        if interruption_requested is None:
            interruption_requested = lambda: False
        if not callable(interruption_requested):
            raise TypeError("interruption probe must be callable")

        execution = self._authorize(execution, declaration)
        key, refs = self._refs(execution, call_id)
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
            self._terminal(
                execution, declaration, call_id, arguments, key, refs,
                "failed", error_code=code)
            raise ManagedPluginInvocationFailed(code) from exc

        self._terminal(
            execution, declaration, call_id, arguments, key, refs,
            "returned", output=output)
        return self._result(
            declaration, call_id, output, refs["started"], refs["returned"])


def build_managed_plugin_tool_catalog(
    plugin_catalog: PluginCatalog,
    allowlist: Iterable[ManagedToolSelector | tuple[str, str] | str]
    | Mapping[str, str],
) -> ManagedPluginToolCatalog:
    return ManagedPluginToolCatalog(plugin_catalog, allowlist)


__all__ = (
    "CATALOG_SCHEMA_VERSION",
    "INVOCATION_PROTOCOL",
    "RECEIPT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
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
