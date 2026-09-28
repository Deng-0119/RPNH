"""Python-only HOST execution refs for a prospective Module graph.

The factory receives immutable framework identities, not JSON implementation
locators. It may publish ordinary static authority records through the owner's
Core before returning already canonical refs. It must not start a firing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Mapping

from .models import VersionRef
from .resources import ResourceVersionRef
from .publication import _resource_from_payload, _version_from_payload
from .strict_contracts import _registered, ref_payload


@dataclass(frozen=True, slots=True)
class ModuleHostBindingPlan:
    task_ref: VersionRef
    run_ref: VersionRef
    task_round_ref: VersionRef
    plan_ref: VersionRef
    root_ref: VersionRef
    net_ref: VersionRef
    node_refs: Mapping[str, VersionRef]
    binding_refs: Mapping[str, VersionRef]
    output_refs: Mapping[tuple[str, str], VersionRef]


@dataclass(frozen=True, slots=True)
class HostExecutionBinding:
    agent_ref: VersionRef | None = None
    activation_ref: VersionRef | None = None
    llm_input_target_ref: ResourceVersionRef | None = None
    workspace_binding_ref: VersionRef | None = None
    module_artifact_refs: tuple[VersionRef, ...] = ()
    extra_resource_refs: tuple[ResourceVersionRef, ...] = ()


HostExecutionBindings = Mapping[str, HostExecutionBinding]
HostExecutionBindingFactory = Callable[[ModuleHostBindingPlan], HostExecutionBindings]


def preserved_workspace_authority(core, plan: ModuleHostBindingPlan,
                                  workspace_ref: VersionRef):
    """Close a current adopted checkpoint's settled head back to its genesis.

    This is not permission to read arbitrary historical workspace authority:
    both the current checkpoint and the registered SQL-CAS head must select the
    supplied exact revision, and every creation net must be in this run's
    adopted ancestry. Return the clean template seed and immutable evidence.
    """
    from .event_store import verified_adoption_lineage, verified_checkpoint_head

    def exact(ref, kind):
        row, data = _registered(core, ref, kind)
        if core.event_store.canonical_object_row(ref.version_id) is None:
            raise ValueError("preserved workspace authority is not canonical")
        core.catalog.validate_instance(kind, category="object", instance=data)
        prepared = core.get_version(ref.version_id)
        payload = core.object_store.read_registered(prepared)
        if len(payload) != row["size"]:
            raise ValueError("preserved workspace evidence byte count differs")
        if kind != "workspace_revision/v1" and json.loads(payload) != data:
            raise ValueError("preserved workspace evidence bytes differ from registered facts")
        return data

    if not isinstance(workspace_ref, VersionRef):
        raise TypeError("preserved workspace requires an exact workspace revision VersionRef")
    run = exact(plan.run_ref, "native_run_identity/v1")
    branch = exact(_version_from_payload(run["task_branch_ref"]), "task_branch/v1")
    native = json.loads(core.event_store.get_meta("native_run_ref") or "null")
    round_data = exact(plan.task_round_ref, "task_round/v1")
    if (native != ref_payload(plan.run_ref) or run["task_ref"] != ref_payload(plan.task_ref)
            or plan.task_ref.entity_id != core.task_id or run["branch_id"] != core.branch_id
            or branch["task_ref"] != ref_payload(plan.task_ref)
            or round_data["task_branch_ref"] != run["task_branch_ref"]):
        raise ValueError("preserved workspace differs from exact run/task/branch authority")
    adopted = verified_adoption_lineage(core.event_store, core.catalog, core.task_id)
    checkpoint_ref = verified_checkpoint_head(core.event_store, core.catalog, core.task_id, adopted[-1])
    checkpoint = exact(checkpoint_ref, "marking_checkpoint/v1")
    selected = [value for value in checkpoint["workspace_revision_refs"]
                if value["logical_id"] == str(workspace_ref.entity_id)]
    head = core.event_store.workspace_lineage_head(workspace_ref.entity_id)
    head_row, _ = _registered(core, workspace_ref, "workspace_revision/v1")
    if (checkpoint["settled"] is not True or selected != [ref_payload(workspace_ref)]
            or head is None or head["workspace_revision_version_id"] != str(workspace_ref.version_id)):
        raise ValueError("preserved workspace must be the current adopted checkpoint's exact settled head")
    if head["transaction_id"] != head_row["transaction_id"]:
        raise ValueError("preserved workspace head lacks exact publication transaction")
    evidence = set()
    cursor = workspace_ref
    while True:
        if cursor in evidence:
            raise ValueError("preserved workspace ancestry is cyclic")
        revision = exact(cursor, "workspace_revision/v1")
        if (cursor.entity_id.kind != "workspace_lineage" or cursor.version_id.kind != "workspace_revision"
                or cursor.entity_id != workspace_ref.entity_id
                or revision["workspace_revision_ref"] != ref_payload(cursor)
                or revision["workspace_lineage_id"] != str(cursor.entity_id)
                or revision["workspace_revision_id"] != str(cursor.version_id)
                or revision["run_ref"] != ref_payload(plan.run_ref)
                or revision["task_ref"] != ref_payload(plan.task_ref)
                or _version_from_payload(revision["net_instance_ref"]) not in adopted
                or revision["settled"] is not True
                or revision["disposition"] not in {
                    "genesis", "committed", "merged", "owner_reopen"}
                or revision["payload_kind"] != "full_workspace_tar"
                or core.get_version(cursor.version_id).media_type != "application/x-tar"):
            raise ValueError("preserved workspace is outside exact settled run/task/net ancestry")
        evidence.add(cursor)
        if revision["disposition"] == "genesis":
            if any(revision[field] is not None for field in (
                    "parent_revision_ref", "base_revision_ref", "producer_invocation_ref",
                    "transition_firing_ref", "firing_workspace_binding_ref")):
                raise ValueError("preserved workspace genesis has non-genesis evidence")
            genesis = cursor
            break
        if revision["disposition"] == "owner_reopen":
            if any(revision[field] is not None for field in (
                    "producer_invocation_ref", "transition_firing_ref",
                    "firing_workspace_binding_ref")):
                raise ValueError(
                    "owner-reopen workspace has ordinary firing authority")
            authorization_ref = _version_from_payload(
                revision["reopen_authorization_ref"])
            authorization = exact(
                authorization_ref, "run_reopen_authorization/v1")
            revision_row, _ = _registered(
                core, cursor, "workspace_revision/v1")
            events = tuple(
                event for event in core.event_store.list_events_by_aggregate(
                    str(plan.run_ref.entity_id),
                    event_types=("run_reopened/v1",))
                if event.payload.get("run_reopen_authorization_ref")
                == ref_payload(authorization_ref))
            if (authorization.get("run_ref") != ref_payload(plan.run_ref)
                    or authorization.get("task_ref") != ref_payload(plan.task_ref)
                    or ref_payload(cursor) not in authorization.get(
                        "workspace_reentry_revision_refs", [])
                    or authorization.get("selected_workspace_revision_refs", [])
                    .count(revision["base_revision_ref"]) != 1
                    or authorization.get("expected_workspace_head_refs", [])
                    .count(revision["parent_revision_ref"]) != 1
                    or len(events) != 1
                    or str(events[0].transaction_id)
                    != str(revision_row["transaction_id"])):
                raise ValueError(
                    "owner-reopen workspace lacks exact atomic authority")
            evidence.add(authorization_ref)
            cursor = _version_from_payload(revision["parent_revision_ref"])
            continue
        firing_ref = _version_from_payload(revision["transition_firing_ref"])
        invocation_ref = _version_from_payload(revision["producer_invocation_ref"])
        binding_ref = _version_from_payload(revision["firing_workspace_binding_ref"])
        firing = exact(firing_ref, "transition_firing/v1")
        exact(invocation_ref, "invocation/v1")
        binding = exact(binding_ref, "workspace_binding/v1")
        with core.event_store.connect() as db:
            publication = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                                     (str(firing_ref.version_id),)).fetchone()
        revision_row, _ = _registered(core, cursor, "workspace_revision/v1")
        if (publication is None or publication["state"] != "PUBLISHED"
                or publication["published_transaction_id"] != revision_row["transaction_id"]
                or publication["workspace_lineage_id"] != str(cursor.entity_id)
                or publication["workspace_revision_version_id"] != str(cursor.version_id)
                or publication["invocation_version_id"] != str(invocation_ref.version_id)
                or publication["invocation_logical_id"] != str(invocation_ref.entity_id)
                or publication["net_version_id"] != revision["net_instance_ref"]["version_id"]
                or firing["net_instance_ref"] != revision["net_instance_ref"]
                or binding["binding_kind"] != "firing_view"
                or binding["transition_firing_ref"] != ref_payload(firing_ref)
                or binding["invocation_ref"] != ref_payload(invocation_ref)
                or binding["operation_binding_ref"] != firing["operation_binding_ref"]
                or binding["base_revision_ref"] != revision["base_revision_ref"]
                or binding["workspace_lineage_ref"]["logical_id"] != str(cursor.entity_id)):
            raise ValueError("preserved workspace lacks exact ordinary firing settlement evidence")
        settled_object = core.get_version(publication["marking_checkpoint_version_id"])
        settled_cp = VersionRef("marking_checkpoint/v1", settled_object.logical_id, settled_object.version_id)
        settled = exact(settled_cp, "marking_checkpoint/v1")
        if (ref_payload(cursor) not in settled["workspace_revision_refs"]
                or settled["settled"] is not True
                or settled["net_instance_ref"] != revision["net_instance_ref"]):
            raise ValueError("preserved workspace revision lacks exact settlement checkpoint")
        evidence.update((firing_ref, invocation_ref, binding_ref, settled_cp))
        cursor = _version_from_payload(revision["parent_revision_ref"])
    # Bases and lineage refs must resolve within this same settled parent chain,
    # not merely share its logical ID.
    revisions = {ref for ref in evidence if ref.entity_type == "workspace_revision/v1"}
    for ref in revisions:
        _, revision = _registered(core, ref, "workspace_revision/v1")
        if revision["disposition"] == "owner_reopen":
            base = _version_from_payload(revision["base_revision_ref"])
            ancestors = set()
            parent = _version_from_payload(revision["parent_revision_ref"])
            while parent not in ancestors:
                ancestors.add(parent)
                _, ancestor = _registered(
                    core, parent, "workspace_revision/v1")
                if ancestor["parent_revision_ref"] is None:
                    break
                parent = _version_from_payload(
                    ancestor["parent_revision_ref"])
            if base not in ancestors:
                raise ValueError(
                    "owner-reopen workspace base is not historical ancestry")
        elif revision["disposition"] != "genesis":
            base = _version_from_payload(revision["base_revision_ref"])
            binding = exact(_version_from_payload(revision["firing_workspace_binding_ref"]), "workspace_binding/v1")
            ancestors = set()
            parent = _version_from_payload(revision["parent_revision_ref"])
            while parent not in ancestors:
                ancestors.add(parent)
                _, ancestor = _registered(core, parent, "workspace_revision/v1")
                if ancestor["parent_revision_ref"] is None:
                    break
                parent = _version_from_payload(ancestor["parent_revision_ref"])
            if base not in ancestors or _version_from_payload(binding["workspace_lineage_ref"]) != genesis:
                raise ValueError("preserved workspace base/lineage lacks exact settled ancestry")
    return genesis, evidence


def validate_host_execution_bindings(core, plan: ModuleHostBindingPlan,
                                     bindings: HostExecutionBindings):
    """Validate registered refs; return exact root resource/artifact inventory."""
    if not isinstance(bindings, Mapping) or not set(bindings) <= set(plan.node_refs):
        raise ValueError("HOST execution bindings require exact symbolic transition keys")
    resources, artifacts = set(), set()
    def exact(ref, expected):
        _, data = _registered(core, ref, expected)
        if core.event_store.canonical_object_row(ref.version_id) is None:
            raise ValueError("HOST execution authority is not canonical")
        core.catalog.validate_instance(expected, category="object", instance=data)
        return data

    def resource(ref, *, prospective=True):
        if not isinstance(ref, ResourceVersionRef):
            raise TypeError("HOST resource authority requires ResourceVersionRef")
        data = exact(ref.as_version_ref(), "resource_version/v1")
        if (data["task_ref"] != ref_payload(plan.task_ref)
                or data["branch_id"] != core.branch_id
                or (prospective and data["net_ref"] not in (None, ref_payload(plan.net_ref)))
                or (prospective and data["round_ref"] not in (None, ref_payload(plan.task_round_ref)))):
            raise ValueError("HOST resource authority is outside exact task/round/net scope")
        resources.add(ref.as_version_ref())
        return data

    for name, binding in bindings.items():
        if not isinstance(binding, HostExecutionBinding):
            raise TypeError("HOST execution bindings require typed Python values, not JSON refs")
        if binding.agent_ref is not None:
            agent = exact(binding.agent_ref, "agent/v1")
            if (agent["agent_ref"] != ref_payload(binding.agent_ref)
                    or agent["agent_id"] != str(binding.agent_ref.entity_id)
                    or agent["agent_version_id"] != str(binding.agent_ref.version_id)
                    or agent["shadow_transition_id"] != name):
                raise ValueError("HOST agent self identity differs")
            declaration = _version_from_payload(agent["team_net_declaration_ref"])
            resource(ResourceVersionRef(declaration.entity_id, declaration.version_id), prospective=False)
            artifacts.add(binding.agent_ref)
        if (not isinstance(binding.module_artifact_refs, tuple)
                or len(set(binding.module_artifact_refs))
                != len(binding.module_artifact_refs)):
            raise TypeError(
                "HOST module artifacts require unique exact VersionRefs")
        for ref in binding.module_artifact_refs:
            if not isinstance(ref, VersionRef):
                raise TypeError(
                    "HOST module artifact authority requires VersionRef")
            exact(ref, ref.entity_type)
            artifacts.add(ref)
        if not isinstance(binding.extra_resource_refs, tuple):
            raise TypeError("HOST extra resources require a tuple of ResourceVersionRef values")
        for ref in binding.extra_resource_refs:
            resource(ref)
        if binding.llm_input_target_ref is not None:
            target = resource(binding.llm_input_target_ref)
            if target["content_schema_ref"] != "registry_v1/llm_input_target/v1":
                raise ValueError("HOST LLM target lacks exact mechanical schema")
            prepared = core.get_version(binding.llm_input_target_ref.resource_version_id)
            document = json.loads(core.object_store.read_registered(prepared))
            core.catalog.validate_schema_ref("registry_v1/llm_input_target/v1", document)
        if binding.activation_ref is not None:
            if not isinstance(binding.activation_ref, VersionRef):
                raise TypeError("HOST activation authority requires VersionRef")
            exact(binding.activation_ref, binding.activation_ref.entity_type)
            artifacts.add(binding.activation_ref)
        if binding.workspace_binding_ref is not None:
            workspace = exact(binding.workspace_binding_ref, "workspace_binding/v1")
            if (workspace["workspace_binding_id"] != str(binding.workspace_binding_ref.entity_id)
                    or workspace["workspace_binding_version_id"] != str(binding.workspace_binding_ref.version_id)
                    or workspace["binding_kind"] != "lineage_template"
                    or workspace["operation_binding_ref"] != ref_payload(plan.binding_refs[name])):
                raise ValueError("HOST workspace must be the prospective operation's lineage template, not a firing view")
            lineage = _version_from_payload(workspace["workspace_lineage_ref"])
            seed_ref = _version_from_payload(workspace["base_revision_ref"])
            _, origin = _registered(core, lineage, "workspace_revision/v1")
            inherited = origin["net_instance_ref"] != ref_payload(plan.net_ref)
            if inherited:
                from .event_store import verified_adoption_head, verified_checkpoint_head
                current_net = verified_adoption_head(core.event_store, core.catalog, core.task_id)
                current_cp = verified_checkpoint_head(core.event_store, core.catalog, core.task_id, current_net)
                _, checkpoint = _registered(core, current_cp, "marking_checkpoint/v1")
                heads = [_version_from_payload(value) for value in checkpoint["workspace_revision_refs"]
                         if value["logical_id"] == str(lineage.entity_id)]
                if len(heads) != 1:
                    raise ValueError("HOST preserved workspace lacks exact current checkpoint lineage")
                genesis, evidence = preserved_workspace_authority(core, plan, heads[0])
                if lineage != genesis or seed_ref != genesis:
                    raise ValueError("HOST preserved workspace template must retain its exact original genesis")
                artifacts.update(evidence)
            for ref in (lineage, seed_ref):
                revision = exact(ref, "workspace_revision/v1")
                if (ref.entity_id.kind != "workspace_lineage" or ref.version_id.kind != "workspace_revision"
                        or revision["workspace_revision_ref"] != ref_payload(ref)
                        or revision["workspace_lineage_id"] != str(ref.entity_id)
                        or revision["workspace_revision_id"] != str(ref.version_id)
                        or revision["run_ref"] != ref_payload(plan.run_ref)
                        or revision["task_ref"] != ref_payload(plan.task_ref)
                        or (not inherited and revision["net_instance_ref"] != ref_payload(plan.net_ref))
                        or revision["disposition"] != "genesis" or revision["settled"] is not True
                        or seed_ref.entity_id != lineage.entity_id):
                    raise ValueError("HOST workspace seed is outside exact prospective run/task/net lineage")
                artifacts.add(ref)
            for field in ("workspace_seed_manifest_ref", "workspace_runtime_ref"):
                if workspace[field] is not None:
                    resource(_resource_from_payload(workspace[field]))
            artifacts.add(binding.workspace_binding_ref)
    return resources, artifacts


__all__ = ("HostExecutionBinding", "HostExecutionBindings", "HostExecutionBindingFactory",
           "ModuleHostBindingPlan")
