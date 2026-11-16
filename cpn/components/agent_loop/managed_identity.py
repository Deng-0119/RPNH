"""Closed legacy/versioned identity projections for existing managed actions.

This is data validation only. Registration, input delivery and invocation
authority remain with their existing owner services.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

_SYMBOL = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_LEGACY = re.compile(r"[a-f0-9]{64}\Z")


def _binding(value):
    if (not isinstance(value, Mapping)
            or set(value) != {"schema_version", "plugin_name", "plugin_version", "binding_revision"}
            or value.get("schema_version") != "rpnh/plugin_binding/v2"
            or not isinstance(value.get("plugin_name"), str)
            or not _SYMBOL.fullmatch(value["plugin_name"])
            or any(not isinstance(value.get(key), str) or not _VERSION.fullmatch(value[key])
                   for key in ("plugin_version", "binding_revision"))):
        raise ValueError("managed binding identity is malformed")
    return dict(value)


def managed_identity_fields(value):
    """Extract exactly one identity mode, rejecting mixed or partial fields."""
    old = {"plugin_catalog_digest", "binding_digest"}
    new = {"plugin_catalog_identity", "binding_identity"}
    present = (old | new) & set(value)
    if present == old:
        if any(not isinstance(value[key], str) or not _LEGACY.fullmatch(value[key]) for key in old):
            raise ValueError("legacy managed identity is malformed")
        return {key: value[key] for key in sorted(old)}
    if present != new:
        raise ValueError("managed identity requires one complete versioned branch")
    binding = _binding(value["binding_identity"])
    catalog = value["plugin_catalog_identity"]
    if (not isinstance(catalog, Mapping)
            or set(catalog) != {"schema_version", "revision", "bindings"}
            or catalog.get("schema_version") != "rpnh/plugin_catalog/v2"
            or not isinstance(catalog.get("revision"), str)
            or not _VERSION.fullmatch(catalog["revision"])
            or not isinstance(catalog.get("bindings"), (list, tuple))):
        raise ValueError("managed catalog identity is malformed")
    bindings = [_binding(item) for item in catalog["bindings"]]
    if len({item["plugin_name"] for item in bindings}) != len(bindings) or binding not in bindings:
        raise ValueError("managed binding is not unique in its exact catalog")
    selector = value.get("selector")
    if selector is not None and (not isinstance(selector, str) or selector.count("/") != 1
            or selector.split("/")[0] != binding["plugin_name"]):
        raise ValueError("managed selector differs from its explicit plugin identity")
    return {"binding_identity": binding, "plugin_catalog_identity": {
        "schema_version": "rpnh/plugin_catalog/v2", "revision": catalog["revision"],
        "bindings": bindings}}


def parse_managed_tool_bindings(payload):
    """Parse the original per-node managed binding resource without authority.

    AgentLoop execution and selected-net resume admission use this same closed
    legacy/versioned reader. Parsing grants neither registration nor invocation.
    """
    from cpn.rpnh.registry.errors import ResourceIntegrityFault

    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "managed tool binding resource is not JSON") from exc
    bindings = document.get("bindings") if isinstance(document, Mapping) else None
    versioned = isinstance(document, Mapping) and document.get("schema_version") == (
        "rpnh/agent_loop_managed_tool_bindings/v2")
    catalog_key = "plugin_catalog_identity" if versioned else "plugin_catalog_digest"
    binding_key = "binding_identity" if versioned else "binding_digest"
    if (not isinstance(document, Mapping)
            or set(document) != {
                "schema_version", "semantic_node_id",
                catalog_key, "bindings"}
            or document.get("schema_version")
            not in {"rpnh/agent_loop_managed_tool_bindings/v1", "rpnh/agent_loop_managed_tool_bindings/v2"}
            or not isinstance(bindings, Mapping)
            or any(not isinstance(name, str)
                   or not isinstance(value, Mapping)
                   or set(value) != {
                       "registration_key", "selector", binding_key,
                       "effect", "max_result_bytes"}
                   for name, value in bindings.items())):
        raise ResourceIntegrityFault(
            "managed tool binding resource is malformed")
    result = {
        name: {**dict(value), catalog_key: document[catalog_key]}
        for name, value in bindings.items()
    }
    try:
        for value in result.values():
            managed_identity_fields(value)
            if (value["effect"] not in {"pure", "external_read", "external_write"}
                    or type(value["max_result_bytes"]) is not int
                    or not 0 < value["max_result_bytes"] <= 16 * 1024 * 1024):
                raise ValueError("managed binding limits are invalid")
    except (TypeError, ValueError) as exc:
        raise ResourceIntegrityFault("managed tool binding identity is malformed") from exc
    return result


def validate_tool_catalog_binding(*, catalog, registered_payload, compiled,
                                  operation, registration, managed):
    """Check the original catalog/declaration correspondence without a firing.

    Inputs are the original parsed catalog, compiled operation and HOST
    Registration. This function reads declarations only and confers no tool
    invocation or Registry publication authority.
    """
    from cpn.rpnh.registry.errors import ResourceIntegrityFault
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from .action_execution import OPTIONAL_TOOL_BINDINGS, OptionalAgentCapabilityUnavailable

    registration_keys = tuple(sorted(
        managed.get(name, {}).get("registration_key", name)
        for name in catalog.tool_names))
    if (registered_payload != catalog.payload
            or tuple(operation.declaration.tools) != registration_keys):
        raise ResourceIntegrityFault("optional tool catalog differs from the exact admitted declaration")
    if set(catalog.tool_names) - set(OPTIONAL_TOOL_BINDINGS) - set(managed):
        raise OptionalAgentCapabilityUnavailable(
            "optional loop supports only exact declared capabilities")
    for name in catalog.tool_names:
        registration_key = managed.get(name, {}).get(
            "registration_key", name)
        actual = compiled.registrations["tool"][registration_key]
        if canonical_json(actual) != canonical_json(
                registration.declaration("tool", registration_key)):
            raise ResourceIntegrityFault("optional tool HOST registration changed")
        if name in managed:
            binding = managed[name]
            contracts = actual["contracts"]
            identity = actual["identity"]
            try:
                same_identity = canonical_json(managed_identity_fields(identity)) == canonical_json(
                    managed_identity_fields(binding))
            except (TypeError, ValueError) as exc:
                raise ResourceIntegrityFault("managed tool identity is malformed") from exc
            if (contracts.get("invocation_protocol")
                    != ("rpnh/managed_native_plugin_tool_invocation/v3"
                        if "binding_identity" in binding else
                        "rpnh/managed_native_plugin_tool_invocation/v2")
                    or identity.get("provider_name") != name
                    or identity.get("selector") != binding["selector"]
                    or not same_identity
                    or contracts.get("max_result_bytes") != binding["max_result_bytes"]
                    or identity.get("effect") != binding["effect"]):
                raise ResourceIntegrityFault(
                    "managed tool differs from its node-scoped binding")
        elif (actual["identity"] != OPTIONAL_TOOL_BINDINGS[name][1]["identity"]
                or actual["contracts"] != OPTIONAL_TOOL_BINDINGS[name][1]["contracts"]):
            raise ResourceIntegrityFault("optional tool is not its declared capability implementation")
