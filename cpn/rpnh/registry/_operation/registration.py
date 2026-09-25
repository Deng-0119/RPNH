"""Process-global HOST operation registration and lookup boundary.

The execution owner binds one trusted registration inventory.  This module is
the sole owner of that process-global reference and validates the pure-data
operation contract before registration or use.  ``registry.operations``
re-exports the established API objects as its compatibility facade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from ..schema_catalog import _SCHEMA_ID as _CONTENT_SCHEMA_ID


_LEXICAL_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class OperationAuthorityError(RuntimeError):
    """An operation cannot be proven from one exact Registry closure."""


@dataclass(frozen=True, slots=True)
class RegisteredOperationPortContract:
    port_id: str
    content_schema_id: str
    minimum: int
    maximum: int | None


_OPERATION_REGISTRATION: Any = None


def bind_operation_registration(registration: Any) -> None:
    """Bind the execution owner's sole trusted HOST implementation inventory.

    No default recipes are installed here. Workflow/components register their
    implementations before launch, including the current example operations.
    """
    if any(not callable(getattr(registration, method, None)) for method in (
            "declaration", "declarations", "register_executor", "resolve")):
        raise OperationAuthorityError(
            "operation registration inventory is unavailable")
    global _OPERATION_REGISTRATION
    _OPERATION_REGISTRATION = registration


def register_operation_contract(
    operation_id: str, executor: Any, *,
    identity: Mapping[str, Any], contracts: Mapping[str, Any],
) -> None:
    """Register a trusted executor and its pure-data operation ABI.

    The retained API parameter ``operation_id`` denotes the opaque HOST key,
    not the independent lexical operation id of an OperationSpecAuthority.
    ``transport`` is llm/deterministic. ``input_ports``/``output_ports`` are
    ordered lists of {port_id, content_schema_id, minimum, maximum}; optional
    ports have minimum=0, unbounded maxima use null. Explicit null lists mean
    declaration-owned ports, whose exact typed/schema/cardinality checks remain
    mandatory. Additional output protocol, effects, budget, executor and config
    constraints remain exact data for lowering/launch to consume.
    """
    _require_executor_key(operation_id)
    _validate_operation_contract(contracts)
    if _OPERATION_REGISTRATION is None:
        raise OperationAuthorityError("HOST operation registration is not bound")
    _OPERATION_REGISTRATION.register_executor(
        operation_id, executor, identity=identity, contracts=contracts)


def _validate_operation_contract(contracts: Mapping[str, Any]) -> None:
    if (not isinstance(contracts, Mapping)
            or contracts.get("transport") not in {"llm", "deterministic"}
            or not {"input_ports", "output_ports"}.issubset(contracts)):
        raise OperationAuthorityError(
            "operation contract must declare transport and port ABI")
    from ...resource_access import ResourceReadContract
    read_contracts = contracts.get("resource_read_contracts", [])
    if not isinstance(read_contracts, (list, tuple)):
        raise OperationAuthorityError(
            "registered read contracts must be data declarations")
    try:
        for value in read_contracts:
            ResourceReadContract.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise OperationAuthorityError(
            "registered read contract is not its closed data contract") from exc
    minimum_bytes = contracts.get("resource_minimum_bytes", {})
    if (not isinstance(minimum_bytes, Mapping)
            or any(not isinstance(key, str)
                   or _CONTENT_SCHEMA_ID.fullmatch(key) is None
                   or type(value) is not int or value < 0
                   for key, value in minimum_bytes.items())):
        raise OperationAuthorityError(
            "registered publication byte requirements are invalid")
    seed = contracts.get("workspace_seed_contract")
    if seed is not None and (
            not isinstance(seed, Mapping)
            or set(seed) != {
                "protocol", "descriptor_equals", "members_field",
                "resource_ref_field",
            }
            or not isinstance(seed["descriptor_equals"], Mapping)
            or any(not isinstance(seed[key], str) or not seed[key]
                   for key in (
                       "protocol", "members_field", "resource_ref_field"))):
        raise OperationAuthorityError(
            "registered workspace seed contract is not closed data")
    protocols = contracts.get("input_protocols", ())
    if (not isinstance(protocols, (list, tuple))
            or any(not isinstance(protocol, str) or not protocol
                   for protocol in protocols)
            or len(set(protocols)) != len(protocols)):
        raise OperationAuthorityError(
            "operation input protocols must be unique exact identities")
    host_protocols = contracts.get("host_protocols", ())
    if (not isinstance(host_protocols, (list, tuple))
            or any(protocol != "registered_llm/v1"
                   for protocol in host_protocols)
            or len(set(host_protocols)) != len(host_protocols)):
        raise OperationAuthorityError(
            "operation host protocols must be unique supported identities")
    for direction in ("input", "output"):
        ports = contracts[f"{direction}_ports"]
        if ports is None:
            continue
        if not isinstance(ports, (list, tuple)):
            raise OperationAuthorityError(
                "operation port ABI is not an ordered list")
        ids: list[str] = []
        for port in ports:
            if not isinstance(port, Mapping) or set(port) != {
                    "port_id", "content_schema_id", "minimum", "maximum"}:
                raise OperationAuthorityError(
                    "operation port ABI fields are not closed")
            ids.append(_require_lexical_id("port_id", port["port_id"]))
            minimum, maximum = port["minimum"], port["maximum"]
            if (not isinstance(port["content_schema_id"], str)
                    or _CONTENT_SCHEMA_ID.fullmatch(
                        port["content_schema_id"]) is None
                    or isinstance(minimum, bool)
                    or not isinstance(minimum, int)
                    or minimum < 0
                    or (maximum is not None and (
                        isinstance(maximum, bool)
                        or not isinstance(maximum, int)
                        or maximum < minimum))):
                raise OperationAuthorityError(
                    "operation port ABI schema/cardinality is invalid")
        if len(set(ids)) != len(ids):
            raise OperationAuthorityError(
                "operation port ABI identities must be unique")


def registered_operation_contract(executor_key: str) -> dict[str, Any]:
    """Return exact HOST identity and complete contracts, with no callable data."""
    _require_executor_key(executor_key)
    if _OPERATION_REGISTRATION is None:
        raise OperationAuthorityError("HOST operation registration is not bound")
    try:
        declaration = _OPERATION_REGISTRATION.declaration(
            "executor", executor_key)
        _OPERATION_REGISTRATION.resolve("executor", executor_key)
    except ValueError as exc:
        raise OperationAuthorityError(
            "executor_key is not HOST-registered") from exc
    _validate_operation_contract(declaration["contracts"])
    return declaration


def registered_operation_ids() -> tuple[str, ...]:
    """Return the explicitly HOST-registered executor keys."""
    if _OPERATION_REGISTRATION is None:
        return ()
    return tuple(
        item["key"]
        for item in _OPERATION_REGISTRATION.declarations("executor")
        if {"transport", "input_ports", "output_ports"}.issubset(
            item["contracts"])
    )


def registered_operation_executor(executor_key: str) -> Any:
    """Resolve trusted HOST code only after its exact contract is checked."""
    registered_operation_contract(executor_key)
    return _OPERATION_REGISTRATION.resolve("executor", executor_key)


def registered_operation_transport(
        executor_key: str) -> Literal["llm", "deterministic"]:
    """Return the closed execution transport for one registered operation."""

    return registered_operation_contract(executor_key)["contracts"]["transport"]


def registered_operation_host_protocols(
        executor_key: str) -> tuple[str, ...]:
    """Return the executor's explicit firing-local HOST capabilities."""
    return tuple(registered_operation_contract(executor_key)[
        "contracts"].get("host_protocols", ()))


def registered_operation_port_contract(
        executor_key: str, direction: Literal["input", "output"],
) -> tuple[RegisteredOperationPortContract, ...] | None:
    """Return an exact source-owned port ABI when an operation freezes one."""

    if direction not in {"input", "output"}:
        raise OperationAuthorityError("operation port direction is not closed")
    ports = registered_operation_contract(executor_key)["contracts"][
        f"{direction}_ports"]
    return (None if ports is None else tuple(
        RegisteredOperationPortContract(**port) for port in ports))


def _require_executor_key(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise OperationAuthorityError(
            "executor_key must be a nonempty registered key")
    return value


def _require_lexical_id(label: str, value: object) -> str:
    if not isinstance(value, str) or _LEXICAL_ID.fullmatch(value) is None:
        raise OperationAuthorityError(
            f"{label} must be a closed lexical id, never an import or path")
    return value


def _require_registered_tool(tool_id: str) -> None:
    """Require one trusted tool from the same bound HOST inventory."""
    try:
        _OPERATION_REGISTRATION.declaration("tool", tool_id)
        _OPERATION_REGISTRATION.resolve("tool", tool_id)
    except Exception as exc:
        raise OperationAuthorityError(
            "allowed tool key is not HOST-registered") from exc


# Preserve the established facade-qualified runtime identities used by
# introspection, exception rendering, and DTO pickling.
for _facade_object in (
        OperationAuthorityError,
        RegisteredOperationPortContract,
        bind_operation_registration,
        register_operation_contract,
        _validate_operation_contract,
        registered_operation_contract,
        registered_operation_ids,
        registered_operation_executor,
        registered_operation_transport,
        registered_operation_port_contract,
        _require_executor_key,
        _require_lexical_id,
):
    _facade_object.__module__ = "cpn.rpnh.registry.operations"
del _facade_object


__all__ = (
    "OperationAuthorityError",
    "RegisteredOperationPortContract",
    "bind_operation_registration",
    "register_operation_contract",
    "registered_operation_contract",
    "registered_operation_executor",
    "registered_operation_ids",
    "registered_operation_port_contract",
    "registered_operation_transport",
    "registered_operation_host_protocols",
)
