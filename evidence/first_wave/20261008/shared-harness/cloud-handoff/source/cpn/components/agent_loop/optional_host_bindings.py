"""Explicit optional default-agent HOST startup for plain declared operations.

Publishes static authority only through the supplied owner's Core. Selection
uses registered executor transport, never component keys or workflow roles.
This factory does not provide the default AgentLoop's execution Registry port.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from collections.abc import Mapping
from types import MappingProxyType

from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.runtime_policy import WorkspacePolicy
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.module_host_bindings import HostExecutionBinding
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import _registered, ref_payload
from cpn.components.tool_executors import (
    ExecutionEnvironmentIdentity,
    NumericalToolProfile,
    capture_execution_environment_inventory,
)
from cpn.plugins.api import json_copy


def _declared_agent_prompt(operation, target: LLMInputTarget) -> dict:
    """Project the exact operation responsibility into its model prompt."""
    instruction = operation.declaration.config.get("node_synopsis")
    if (instruction is not None
            and (not isinstance(instruction, str) or not instruction.strip()
                 or instruction != instruction.strip())):
        raise ValueError(
            "optional agent node_synopsis must be nonempty trimmed text")
    messages = []
    if instruction is not None:
        messages.append({"role": "system", "content": instruction})
    semantic_outcomes = tuple(
        outcome for outcome in operation.declaration.outcomes
        if outcome.name != "interrupted")
    bundles = "; ".join(
        f"{outcome.name}="
        + ",".join(product.port for product in outcome.products)
        for outcome in semantic_outcomes)
    messages.append({
        "role": "system",
        "content": (
            "Use only these exact declared semantic outcome and output-port "
            "identifiers: " + bundles + ". On every write_file call, set "
            "outcome_id to the exact name before '=' and output_port_id to "
            "one exact port after '='; never substitute internal port_* "
            "handles. For an application/rpnh_agent_text/v1 output, content "
            "is direct text; for other output schemas, content is one JSON "
            "document validated against the declared schema. Supply content "
            "or one exact visible source_resource_ref authorized to this "
            "firing, never both; workspace bytes are never an implicit write "
            "source. "
            "A timed-out or nonzero-exit workspace action remains immutable "
            "evidence. Account for it accurately when deciding what to do "
            "next; Registry records the fact but does not impose a retry or "
            "reserved-comment policy on completion. "
            "After all products are registered, call complete_interaction "
            "as the final tool call."),
    })
    messages.append({
        "role": "user",
        "content": (
            "Complete the exact registered request using only the "
            "declared tools."),
    })
    return {
        "model_condition": target.model_condition,
        "max_output_tokens": target.max_output_tokens,
        "messages": messages,
    }


def make_optional_agent_host_bindings(llm_input_target: LLMInputTarget, *,
        provider_backend_config: Mapping | None = None,
        transport_contract: Mapping | None = None,
        execution_profiles: Mapping | None = None,
        workspace_policy: WorkspacePolicy | None = None,
        managed_catalogs: Mapping | None = None,
        managed_tool_policy: Mapping | None = None,
        tool_program_policy: Mapping | None = None):
    """Return the public ``start_run(host_execution_bindings=...)`` factory.

    The owner must pass the same exact model condition to start_run and this
    target. Operation configuration (including an explicit agent_loop_role for
    default execution) and tools remain manually declared; nothing is injected.
    The equation workspace starts empty, with one shared fresh genesis and one
    lineage template/write intent per declared LLM transition.
    """
    if not isinstance(llm_input_target, LLMInputTarget):
        raise TypeError("optional agent HOST startup requires an exact LLMInputTarget")
    workspace_policy = workspace_policy or WorkspacePolicy()
    if not isinstance(workspace_policy, WorkspacePolicy):
        raise TypeError("optional agent HOST startup requires WorkspacePolicy")
    managed_catalogs = dict(managed_catalogs or {})
    if managed_catalogs:
        from cpn.plugins.managed_tools import ManagedPluginToolCatalog
        if any(not isinstance(node_id, str)
               or not isinstance(catalog, ManagedPluginToolCatalog)
               for node_id, catalog in managed_catalogs.items()):
            raise TypeError(
                "optional managed catalogs require exact node/catalog bindings")
    if (provider_backend_config is None) != (transport_contract is None):
        raise ValueError("optional provider startup requires both exact route and transport documents")
    backend_data = None if provider_backend_config is None else dict(provider_backend_config)
    transport_data = None if transport_contract is None else dict(transport_contract)
    profile_data = {
        "default": (llm_input_target, backend_data, transport_data),
    }
    if execution_profiles is not None:
        if (not isinstance(execution_profiles, Mapping)
                or any(not isinstance(profile_id, str)
                       or not isinstance(value, tuple) or len(value) != 3
                       or not isinstance(value[0], LLMInputTarget)
                       or not isinstance(value[1], Mapping)
                       or not isinstance(value[2], Mapping)
                       for profile_id, value in execution_profiles.items())):
            raise TypeError(
                "optional execution profiles require target/route/transport tuples")
        profile_data.update({
            profile_id: (target, dict(route), dict(transport))
            for profile_id, (target, route, transport)
            in execution_profiles.items()
        })
    for profile_target, profile_backend, profile_transport in (
            profile_data.values()):
        if (profile_backend is None) != (profile_transport is None):
            raise ValueError(
                "optional provider profile requires route and transport together")
        if profile_backend is None:
            continue
        from .optional_execution import EXECUTION_PROVENANCE_DOCUMENT
        from jsonschema import Draft7Validator
        Draft7Validator(EXECUTION_PROVENANCE_DOCUMENT).validate(
            profile_backend)
        if profile_backend.get("model") != profile_target.model_condition:
            raise ValueError("optional route must preserve the exact configured model")
        if set(profile_transport) != {
                "interaction_protocol_ref", "response_adapter_ref"}:
            raise ValueError("optional transport requires explicit protocol and response adapter identities")

    def bind(*, core, plan, source_ref, bootstrap_ref, compiled):
        if core.read_only:
            raise TypeError("optional agent HOST startup requires the owner's writable Core")
        _, source = _registered(core, source_ref.as_version_ref(), "resource_version/v1")
        authored = ModuleDeclaration.from_json(core.object_store.read_registered(
            core.get_version(source_ref.resource_version_id)).decode("utf-8"))
        if (source["content_schema_ref"] != "rpnh/module_declaration/v1"
                or source["task_ref"] != ref_payload(plan.task_ref)
                or source["branch_id"] != core.branch_id or authored != compiled.source
                or core.event_store.canonical_object_row(source_ref.resource_version_id) is None):
            raise ValueError("optional agent source must be the exact canonical authored Module")
        _registered(core, bootstrap_ref, "bootstrap_command/v1")
        if set(plan.node_refs) != {item.name for item in compiled.symbolic.transitions}:
            raise ValueError("optional agent plan differs from the compiled transition inventory")
        operations = {item.declaration.name: item for item in compiled.operations}
        agents = {item.name: operations[item.operation] for item in compiled.symbolic.transitions
                  if operations[item.operation].executor_declaration["contracts"].get("transport") == "llm"}
        if not agents:
            return MappingProxyType({})
        key = f"optional-agent-host:{plan.net_ref.version_id}"

        def publish(object_type, logical_kind, version_kind, build, suffix, payload=None):
            ref = VersionRef(object_type, new_id(logical_kind), new_id(version_kind))
            metadata = build(ref)
            core.catalog.validate_instance(object_type, category="object", instance=metadata)
            core.publish_bytes(object_type=object_type, logical_id=ref.entity_id,
                version_id=ref.version_id, payload=canonical_json(metadata) if payload is None else payload,
                metadata=metadata, media_type="application/json" if payload is None else "application/x-tar",
                schema_ref=f"registry_v1/{object_type}", idempotency_key=f"{key}:{suffix}")
            return ref

        def static(suffix, data, role, schema=None):
            authority = None
            if schema is not None:
                rows = [row for row in core.event_store.canonical_object_rows(object_type="resource_version/v1")
                        if json.loads(row["metadata_json"]).get("descriptors", {}).get("host_registration_kind") == "schema"
                        and json.loads(row["metadata_json"]).get("descriptors", {}).get("registered_key") == schema]
                if len(rows) != 1:
                    raise ValueError("optional static provenance requires its exact HOST-registered schema")
                row = rows[0]
                from cpn.rpnh.registry.identities import TypedId
                from cpn.rpnh.registry.resources import ResourceVersionRef
                authority = ResourceVersionRef(TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]))
            return _publish_private_system(core, plan.task_ref, PublishResource(
                origin=PrivateSystemOrigin(bootstrap_ref), payload=canonical_json(data),
                media_type="application/json", content_schema_ref=schema,
                content_schema_authority_ref=authority,
                summary="Optional agent HOST " + role, lifetime_ref=bootstrap_ref,
                descriptors={"content_role": role}, idempotency_key=f"{key}:{suffix}"))

        profile_refs = {}
        for profile_id, (profile_target, profile_backend,
                         profile_transport) in sorted(profile_data.items()):
            target_ref = _publish_private_system(
                core, plan.task_ref, PublishResource(
                    origin=PrivateSystemOrigin(bootstrap_ref),
                    payload=canonical_json(
                        profile_target.as_registry_document()),
                    media_type="application/json",
                    content_schema_ref="registry_v1/llm_input_target/v1",
                    summary=("Optional agent HOST exact model input target "
                             + profile_id),
                    lifetime_ref=bootstrap_ref,
                    idempotency_key=f"{key}:llm-target:{profile_id}"))
            backend_ref = transport_ref = None
            if profile_backend is not None:
                from .optional_execution import EXECUTION_PROVENANCE_SCHEMA
                core.catalog.validate_schema_ref(
                    EXECUTION_PROVENANCE_SCHEMA, profile_backend)
                backend_ref = static(
                    f"backend:{profile_id}", profile_backend,
                    "optional_agent_backend", EXECUTION_PROVENANCE_SCHEMA)
                transport_ref = static(
                    f"transport:{profile_id}", profile_transport,
                    "optional_agent_transport")
            profile_refs[profile_id] = (
                profile_target, target_ref, backend_ref, transport_ref)
        # Fresh runs create one empty lineage.  A prospective replacement net
        # inherits the sole current settled workspace lineage instead of
        # silently creating an empty one.  The candidate is not yet adopted,
        # so its plan.net_ref differs from the current adoption head.
        from cpn.rpnh.registry.event_store import (
            verified_adoption_head, verified_checkpoint_head,
        )
        from cpn.rpnh.registry.publication import _version_from_payload
        current_net = (
            verified_adoption_head(core.event_store, core.catalog, core.task_id)
            if core.event_store.list_events_by_type(("net_adopted/v1",))
            else None)
        inherited_workspace = current_net is not None and current_net != plan.net_ref
        if inherited_workspace:
            checkpoint_ref = verified_checkpoint_head(
                core.event_store, core.catalog, core.task_id, current_net)
            _, checkpoint = _registered(
                core, checkpoint_ref, "marking_checkpoint/v1")
            heads = tuple(_version_from_payload(value)
                          for value in checkpoint["workspace_revision_refs"])
            if len(heads) != 1:
                raise ValueError(
                    "optional agent replacement requires one exact current workspace lineage")
            from cpn.rpnh.registry.module_host_bindings import (
                preserved_workspace_authority,
            )
            genesis, _evidence = preserved_workspace_authority(
                core, plan, heads[0])
        else:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w"):
                pass
            genesis = publish("workspace_revision/v1", "workspace_lineage", "workspace_revision", lambda ref: {
                "workspace_lineage_id": str(ref.entity_id), "workspace_revision_id": str(ref.version_id),
                "workspace_revision_ref": ref_payload(ref), "run_ref": ref_payload(plan.run_ref),
                "task_ref": ref_payload(plan.task_ref), "net_instance_ref": ref_payload(plan.net_ref),
                "parent_revision_ref": None, "base_revision_ref": None, "producer_invocation_ref": None,
                "transition_firing_ref": None, "firing_workspace_binding_ref": None,
                "reopen_authorization_ref": None, "disposition": "genesis",
                "changed_paths": [], "deleted_paths": [], "path_deltas": [],
                "inventory_paths": [], "conflict_paths": [],
                "semantic_output_refs": [], "trace_summary_refs": [], "payload_kind": "full_workspace_tar",
                "settled": True}, "workspace-genesis", stream.getvalue())
        if inherited_workspace:
            # Environment/profile are run-scoped HOST authority.  A same-owner
            # net replacement must bind its operations to that authority, not
            # publish a second pair that makes workspace execution ambiguous.
            environment_rows = core.event_store.canonical_object_rows(
                object_type="execution_environment_identity/v1")
            profile_rows = core.event_store.canonical_object_rows(
                object_type="numerical_tool_profile/v1")
            if len(environment_rows) != 1 or len(profile_rows) != 1:
                raise ValueError(
                    "optional agent replacement requires one exact workspace runtime")
            environment_data = json.loads(
                environment_rows[0]["metadata_json"])
            workspace_profile_data = json.loads(
                profile_rows[0]["metadata_json"])
            environment = _version_from_payload(
                environment_data["environment_ref"])
            workspace_profile = _version_from_payload(
                workspace_profile_data["profile_ref"])
            _registered(
                core, environment, "execution_environment_identity/v1")
            _registered(
                core, workspace_profile, "numerical_tool_profile/v1")
            expected_profile = {
                "timeout_seconds": workspace_policy.timeout_seconds,
                "memory_bytes": workspace_policy.memory_bytes,
                "process_limit": workspace_policy.process_limit,
                "source_size_bytes": workspace_policy.source_size_bytes,
                "input_size_bytes": workspace_policy.input_size_bytes,
            }
            if (workspace_profile_data.get("environment_ref")
                    != ref_payload(environment)
                    or any(workspace_profile_data.get(name) != value
                           for name, value in expected_profile.items())):
                raise ValueError(
                    "optional agent replacement changed the workspace runtime")
        else:
            environment = publish(
                "execution_environment_identity/v1",
                "execution_environment", "execution_environment_version",
                lambda ref: {
                    "environment_ref": ref_payload(ref),
                    "name": "research-exp",
                    "python_executable": os.path.realpath(sys.executable),
                    "python_prefix": os.path.realpath(sys.prefix),
                },
                "workspace-environment",
            )
            workspace_profile = publish(
                "numerical_tool_profile/v1",
                "numerical_tool_profile", "numerical_tool_profile_version",
                lambda ref: {
                    "profile_ref": ref_payload(ref),
                    "environment_ref": ref_payload(environment),
                    "timeout_seconds": workspace_policy.timeout_seconds,
                    "memory_bytes": workspace_policy.memory_bytes,
                    "process_limit": workspace_policy.process_limit,
                    "source_size_bytes": workspace_policy.source_size_bytes,
                    "input_size_bytes": workspace_policy.input_size_bytes,
                },
                "workspace-profile",
            )
            environment_data = {
                "name": "research-exp",
                "python_executable": os.path.realpath(sys.executable),
                "python_prefix": os.path.realpath(sys.prefix),
            }
            workspace_profile_data = {
                "timeout_seconds": workspace_policy.timeout_seconds,
                "memory_bytes": workspace_policy.memory_bytes,
                "process_limit": workspace_policy.process_limit,
                "source_size_bytes": workspace_policy.source_size_bytes,
                "input_size_bytes": workspace_policy.input_size_bytes,
            }
        inventory = static(
            "workspace-inventory",
            capture_execution_environment_inventory(
                environment=ExecutionEnvironmentIdentity(
                    environment, environment_data["name"],
                    environment_data["python_executable"],
                    environment_data["python_prefix"]),
                profile=NumericalToolProfile(
                    workspace_profile, environment,
                    workspace_profile_data["timeout_seconds"],
                    workspace_profile_data["memory_bytes"],
                    workspace_profile_data["process_limit"],
                    workspace_profile_data["source_size_bytes"],
                    workspace_profile_data["input_size_bytes"])),
            "execution_environment_inventory",
        )
        result = {}
        for name, operation in sorted(agents.items()):
            profile_id = (
                operation.declaration.config.get("execution_profile_id")
                or "default")
            if profile_id not in profile_refs:
                raise ValueError(
                    "optional operation references an unknown execution profile")
            selected_target, target, backend, transport = profile_refs[
                profile_id]
            # The exact HOST declarations, not a global Current tool surface,
            # own the optional catalog. Empty startup remains startup-only.
            node_id = operation.declaration.config.get("semantic_node_id")
            managed = managed_catalogs.get(node_id)
            managed_by_key = ({
                declaration.registration_key: declaration
                for declaration in managed.tools
            } if managed is not None else {})
            tools = []
            for tool_name in operation.declaration.tools:
                declared = compiled.registrations["tool"][tool_name]
                contracts = declared["contracts"]
                if contracts.get("binding_protocol") == "optional_agent_tool/v1":
                    tools.append({"name": tool_name,
                                  "description": contracts["description"],
                                  "arguments": contracts["arguments"]})
                    continue
                declaration = managed_by_key.get(tool_name)
                if (declaration is None
                        or declared["identity"].get("provider_name")
                        != declaration.name
                        or declared["identity"].get("selector")
                        != declaration.selector
                        or declared["identity"].get("binding_digest")
                        != declaration.identity["binding_digest"]
                        or declared["contracts"].get("invocation_protocol")
                        != "rpnh/managed_native_plugin_tool_invocation/v2"):
                    raise ValueError(
                        "optional loop tool lacks its exact HOST capability contract")
                tools.append({
                    "name": declaration.name,
                    "description": declaration.description,
                    "arguments": json_copy(declaration.input_schema),
                })
            tools.sort(key=lambda item: item["name"])
            catalog = static(f"catalog:{name}", {"schema_version": "agent_tool_catalog/v1",
                "surface_kind": "parent_current", "workspace_scope": "firing_private_projection",
                "tools": tools}, "optional_agent_tool_catalog")
            managed_binding = None
            scheduler_binding = None
            program_binding = None
            if managed is not None:
                if tool_program_policy is not None and any(
                        item["name"] == "run_tool_program" for item in tools):
                    program_binding = static(
                        f"tool-program:{name}", json_copy(tool_program_policy),
                        "optional_tool_program_policy")
                if managed_tool_policy is not None:
                    scheduler_binding = static(
                        f"managed-scheduler:{name}", json_copy(managed_tool_policy),
                        "optional_managed_tool_scheduler")
                managed_binding = static(
                    f"managed-tools:{name}", {
                        "schema_version": "rpnh/agent_loop_managed_tool_bindings/v1",
                        "semantic_node_id": node_id,
                        "plugin_catalog_digest": managed.plugin_catalog_digest,
                        "bindings": {
                            declaration.name: {
                                "registration_key": declaration.registration_key,
                                "selector": declaration.selector,
                                "binding_digest": declaration.identity[
                                    "binding_digest"],
                                "effect": declaration.effect,
                                "max_result_bytes": declaration.max_result_bytes,
                            }
                            for declaration in managed.tools
                        },
                    }, "optional_agent_managed_tool_bindings")
            prompt = static(
                f"prompt:{name}",
                _declared_agent_prompt(operation, selected_target),
                "optional_agent_prompt")
            agent = publish("agent/v1", "agent", "agent_version", lambda ref: {
                "agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id),
                "agent_ref": ref_payload(ref), "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
                "declared_agent_id": operation.declaration.name, "shadow_transition_id": name}, f"agent:{name}")
            workspace = publish("workspace_binding/v1", "workspace_binding", "workspace_binding_version", lambda ref: {
                "workspace_binding_id": str(ref.entity_id), "workspace_binding_version_id": str(ref.version_id),
                "operation_binding_ref": ref_payload(plan.binding_refs[name]), "binding_kind": "lineage_template",
                "workspace_lineage_ref": ref_payload(genesis), "base_revision_ref": ref_payload(genesis),
                "template_binding_ref": None, "invocation_ref": None, "transition_firing_ref": None,
                "allowed_root": "workspace/views",
                "allowed_prefixes": ["*"], "mode": "read_write",
                "workspace_seed_manifest_ref": None, "workspace_runtime_ref": None}, f"workspace:{name}")
            publish("workspace_write_intent/v1", "write_intent", "write_intent_version", lambda ref: {
                "write_intent_id": str(ref.entity_id), "write_intent_version_id": str(ref.version_id),
                "operation_binding_ref": ref_payload(plan.binding_refs[name]), "source_binding_ref": ref_payload(workspace),
                "allowed_relative_root": "", "one_lineage": True}, f"workspace-intent:{name}")
            result[name] = HostExecutionBinding(agent_ref=agent, llm_input_target_ref=target,
                workspace_binding_ref=workspace,
                module_artifact_refs=(environment, workspace_profile),
                extra_resource_refs=(catalog, prompt, inventory)
                + (() if managed_binding is None else (managed_binding,))
                + (() if scheduler_binding is None else (scheduler_binding,))
                + (() if program_binding is None else (program_binding,))
                + (() if backend is None else (backend, transport)))
        return MappingProxyType(result)

    return bind


__all__ = ("make_optional_agent_host_bindings",)
