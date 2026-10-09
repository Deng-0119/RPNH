"""AgentLoop context assembly, navigation, history rendering, and schemas."""

from __future__ import annotations

from dataclasses import dataclass
import re

from collections.abc import Mapping
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import replace
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import stat

from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.llm_contracts import LLMCallAttempt, LLMInputTarget
from cpn.rpnh.response_protocol import (
    PublishedLLMResponse, canonicalize_llm_response_payload,
    observe_registered_llm_response,
)
from cpn.rpnh.registry.errors import (
    ResourceIntegrityFault, ResourcePayloadSchemaViolation,
    StaleAuthorityHead, UnauthorizedResourceDelivery,
)
from cpn.rpnh.registry.firing_authority import canonical_invocation
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.agent_resource_broker import (
    AgentLoopResourceRequest, prepare_agent_resource_request,
    stage_resumed_agent_resource_lifecycle,
)
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operation_execution import verify_operation_execution
from cpn.rpnh.registry.publication import (
    _append_direct_resource_version_publication, _direct_resource_metadata,
    _provider_request_resource_metadata, _ref_payload, _registry_type_catalog_ref,
    _resource_from_payload, _version_from_payload, _stable_id,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resource_verification import verify_resource
from cpn.rpnh.registry.resources import (
    AcknowledgeResourceDelivery, AddressBindingIntent,
    AgentLoopResourceGrantAuthority, AgentLoopResourceLifecycleAuthority,
    AuthorizeResourceRelease, PetriOutputOrigin, PrepareResourceDelivery,
    PublishResource, ResourceAddress, ResourceVersionRef,
    UnbindResourceAddress, WorkspaceWriteOrigin,
)
from cpn.rpnh.registry.schema_catalog import TypeDefinition, canonical_json

from .compact import (build_replacement_history, reduce_tool_messages,
                      bound_tool_result_groups, should_compact)
from .request_envelope_materialization import materialize_agent_request_envelope
from .models import (
    AgentActionRecord, AgentLoopSnapshot, AgentLoopState, AgentTurnRecord,
    AgentSystemInitialization, LocatedAgentInput,
)
from .delegated_history import compact_delegated_subtask_history_after_length
from .tool_catalog import (
    AGENT_TOOL_NAMES, DELEGATE_LEAF_TOOL_NAME, IMMEDIATE_COMPLETION_GUIDANCE,
    READ_FILE_MODEL_DESCRIPTION, READ_FILE_UNREGISTERED_PATH_DETAIL,
    READ_FILE_WORKSPACE_ROUTING_GUIDANCE, REQUEST_RESOURCE_TOOL_NAME,
    TOOL_ARGUMENT_SCHEMAS, AgentToolCatalog, build_agent_tool_catalog,
    derive_atomic_subtask_tools, model_visible_agent_tool_description,
    parse_agent_tool_catalog,
)
from .tool_validation import (
    AgentToolSyntaxError, ValidatedAgentToolAction,
    validate_agent_tool_observation,
)
from .tool_projection import (
    bounded_agent_action_output_projection, bounded_agent_text_search_projection,
)
from cpn.components.tool_executors import (
    ExecutionEnvironmentIdentity, NumericalToolProfile,
    query_execution_environment_resources,
)


@dataclass(frozen=True, slots=True)
class UserAttachmentNavigation:
    """Body-free attachment metadata shown only for an attached task."""

    attachment_id: str
    ordinal: int
    display_name: str
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        if (not isinstance(self.attachment_id, str)
                or not self.attachment_id
                or isinstance(self.ordinal, bool)
                or not isinstance(self.ordinal, int)
                or self.ordinal < 0
                or not isinstance(self.display_name, str)
                or not self.display_name
                or not isinstance(self.media_type, str)
                or not self.media_type
                or isinstance(self.size_bytes, bool)
                or not isinstance(self.size_bytes, int)
                or self.size_bytes < 1):
            raise ValueError("user attachment navigation is invalid")

def render_registered_hardware_inventory(
        resource_ref: ResourceVersionRef,
        inventory: Mapping[str, object],
) -> str:
    """Render the exact launch-registered CPU/memory capability facts."""

    if not isinstance(resource_ref, ResourceVersionRef):
        raise TypeError("hardware inventory requires one exact resource ref")
    required = {
        "protocol", "observed_at_utc", "total_memory_bytes",
        "available_memory_bytes", "reserve_memory_bytes",
        "per_firing_budget_bytes", "safe_worker_capacity",
        "logical_cpu_count", "cpu_detection", "memory_detection",
        "gpu_detection",
    }
    if (not isinstance(inventory, Mapping)
            or set(inventory) != required
            or inventory.get("protocol") != "host_resource_inventory/v1"
            or inventory.get("cpu_detection") != {
                "method": "linux_sched_getaffinity/v1",
                "status": "observed"}
            or inventory.get("memory_detection") != {
                "method": "linux_proc_meminfo/v1",
                "status": "observed"}
            or not isinstance(inventory.get("gpu_detection"), Mapping)):
        raise ValueError(
            "hardware inventory differs from its closed current contract")
    observed = inventory["observed_at_utc"]
    integer_fields = (
        "total_memory_bytes", "available_memory_bytes",
        "reserve_memory_bytes", "per_firing_budget_bytes",
        "safe_worker_capacity", "logical_cpu_count",
    )
    if (not isinstance(observed, str)
            or re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z", observed) is None
            or any(isinstance(inventory[field], bool)
                   or not isinstance(inventory[field], int)
                   for field in integer_fields)
            or int(inventory["total_memory_bytes"]) < 1
            or int(inventory["available_memory_bytes"]) < 0
            or int(inventory["available_memory_bytes"])
            > int(inventory["total_memory_bytes"])
            or int(inventory["reserve_memory_bytes"]) < 0
            or int(inventory["per_firing_budget_bytes"]) < 1
            or int(inventory["safe_worker_capacity"]) < 0
            or int(inventory["logical_cpu_count"]) < 1):
        raise ValueError("hardware inventory values are invalid")
    gpu_detection = inventory["gpu_detection"]
    if (set(gpu_detection) != {"method", "status", "devices"}
            or gpu_detection.get("method") != "nvidia_smi_query/v1"
            or gpu_detection.get("status") not in {"observed", "unavailable"}
            or not isinstance(gpu_detection.get("devices"), list)):
        raise ValueError("hardware GPU inventory values are invalid")
    devices = gpu_detection["devices"]
    if (any(not isinstance(device, Mapping)
            or set(device) != {
                "ordinal", "uuid", "name", "total_memory_bytes"}
            or isinstance(device.get("ordinal"), bool)
            or not isinstance(device.get("ordinal"), int)
            or int(device["ordinal"]) < 0
            or not isinstance(device.get("uuid"), str)
            or not device["uuid"]
            or not isinstance(device.get("name"), str)
            or not device["name"]
            or isinstance(device.get("total_memory_bytes"), bool)
            or not isinstance(device.get("total_memory_bytes"), int)
            or int(device["total_memory_bytes"]) < 1
            for device in devices)
            or (gpu_detection["status"] == "unavailable" and devices)):
        raise ValueError("hardware GPU inventory values are invalid")
    if gpu_detection["status"] == "observed":
        gpu_facts = (
            f"gpu_detection_status=observed; visible_gpu_count={len(devices)}; "
            "gpu_devices="
            + json.dumps(
                devices, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"))
            + ".")
        if not devices:
            gpu_facts += " Zero visible GPU devices were observed at capture."
    else:
        gpu_facts = (
            "gpu_detection_status=unavailable; GPU capture was unavailable, so "
            "GPU absence is not established.")
    return (
        "Registered run hardware inventory (exact launch-time authority): "
        f"resource_id={resource_ref.resource_id}; "
        f"resource_version_id={resource_ref.resource_version_id}; "
        f"observed_at_utc={observed}; "
        f"logical_cpu_count={inventory['logical_cpu_count']}; "
        f"total_memory_bytes={inventory['total_memory_bytes']}; "
        "available_memory_bytes_at_capture="
        f"{inventory['available_memory_bytes']}; "
        f"per_firing_budget_bytes={inventory['per_firing_budget_bytes']}; "
        f"safe_worker_capacity={inventory['safe_worker_capacity']}; "
        + gpu_facts
        + " Host availability and GPU facts are launch-time information; "
        "per_firing_budget_bytes is scheduler admission, not the workspace "
        "command memory ceiling. Use the separately stated workspace execution "
        "limits "
        "when planning execution."
    )

def build_agent_system_initialization(
    *, invocation_ref: VersionRef,
    sponsoring_transition_firing_ref: VersionRef | None,
    role: str,
    catalog: AgentToolCatalog,
    located_inputs: tuple[LocatedAgentInput, ...],
    output_kind: str,
    output_contract: str,
    llm_turn_cap: int | None,
    llm_turns_used: int,
    task_call_cap: int | None,
    task_calls_used: int,
    semantic_output_port_ids: tuple[str, ...] = (),
    verified_evidence_output_port_ids: tuple[str, ...] = (),
    workspace_root: str | None = None,
    user_attachments: tuple[UserAttachmentNavigation, ...] = (),
) -> AgentSystemInitialization:
    """Render only wired capabilities into one framework system section."""
    if not isinstance(catalog, AgentToolCatalog):
        raise TypeError("agent initialization requires one exact tool catalog")
    names = tuple(sorted(catalog.tool_names))
    inputs = tuple(located_inputs)
    if any(not isinstance(item, LocatedAgentInput) for item in inputs):
        raise TypeError("agent initialization located inputs must be typed")
    if (not isinstance(user_attachments, tuple)
            or any(not isinstance(item, UserAttachmentNavigation)
                   for item in user_attachments)
            or tuple(item.ordinal for item in user_attachments)
            != tuple(range(len(user_attachments)))
            or len({item.attachment_id for item in user_attachments})
            != len(user_attachments)):
        raise TypeError(
            "agent initialization attachments must be one canonical tuple")
    inputs = tuple(sorted(
        inputs,
        key=lambda item: (
            item.sandbox_path, str(item.resource_ref.resource_id),
            str(item.resource_ref.resource_version_id)),
    ))
    if not isinstance(output_contract, str) or not output_contract.strip():
        raise ValueError("agent initialization output contract is required")
    if (llm_turn_cap is not None
            and (isinstance(llm_turn_cap, bool)
                 or not isinstance(llm_turn_cap, int)
                 or llm_turn_cap < 1)):
        raise ValueError(
            "agent initialization provider turn cap must be null or positive")
    if (isinstance(llm_turns_used, bool)
            or not isinstance(llm_turns_used, int)
            or llm_turns_used < 0
            or (llm_turn_cap is not None
                and llm_turns_used > llm_turn_cap)):
        raise ValueError(
            "agent initialization provider turn usage must be within its cap")
    if (task_call_cap is not None
            and (isinstance(task_call_cap, bool)
                 or not isinstance(task_call_cap, int)
                 or task_call_cap < 1)):
        raise ValueError(
            "agent initialization task call cap must be null or positive")
    if (isinstance(task_calls_used, bool)
            or not isinstance(task_calls_used, int)
            or task_calls_used < 0
            or (task_call_cap is not None
                and task_calls_used > task_call_cap)):
        raise ValueError(
            "agent initialization task call usage must be within its cap")
    contract = output_contract.strip()
    semantic_ports = tuple(semantic_output_port_ids)
    if (any(not isinstance(item, str) or not item for item in semantic_ports)
            or len(set(semantic_ports)) != len(semantic_ports)):
        raise ValueError(
            "agent initialization semantic output ports must be unique ids")
    evidence_ports = tuple(verified_evidence_output_port_ids)
    if (any(not isinstance(item, str) or not item for item in evidence_ports)
            or len(set(evidence_ports)) != len(evidence_ports)
            or not set(evidence_ports).issubset(set(semantic_ports))):
        raise ValueError(
            "agent initialization verified-evidence ports must be a unique "
            "subset of semantic output ports")
    module_design = output_kind == "module_declaration"
    finalization_target_rule = (
        "target_node_refs is required and nonempty only for rework; each target "
        "is one exact node_declaration/v1 reference object from the adopted net."
    )

    skills: list[str] = []
    sections = [
        "### FRAMEWORK SYSTEM INITIALIZATION",
        f"Role: {role}",
        "Actually available tools: " + ", ".join(names),
        ("Framework tool protocol: every provider response must contain one or "
         "more calls to the actually available tools; chat text alone is not a "
         "valid progress state. Complete only after write_file has registered every "
         "intended output file required by the assignment. The final write_file and "
         "complete_interaction may be "
         "in the same response when complete_interaction is the final ordered "
         "tool call."),
        ("Task-scope rule: the exact user message delivered for this firing is "
         "your sole semantic assignment. Use the available calls to complete its "
         "required outputs; do not spend turns on unrelated investigation, "
         "self-assigned extensions, or work not needed by that assignment. This "
         "rule is execution guidance and is not a framework judgment of semantic "
         "quality or sufficiency."),
        IMMEDIATE_COMPLETION_GUIDANCE,
        ("Completion rule: decide when this role's assigned semantic "
         "responsibility is complete. As soon as the required outputs are "
         "registered, finish immediately."),
        ("Registry file authority: Registry is the authoritative core for "
         "finding file identities, requesting one exact immutable version, "
         "and reading its registered bytes. A workspace path is only a staged "
         "view of Registry-authorized material; it is not a second file "
         "authority. Use exact Registry indexes and request_resource when a "
         "needed version is not already a Located input."),
        ("Evidence-first execution rule: before new execution or reconstruction, "
         "inspect the supplied Located-input and Registry summaries, relevant "
         "critic or continuation advice, and already-delivered tool or result "
         "evidence. Reuse evidence and outputs that already satisfy the assignment. "
         "Read exact originals only to resolve a necessary gap, and perform or "
         "repeat work only when it is missing, failed, contradictory, or needed to "
         "validate a declared change."),
        ("Automatic Registry-index rule: the framework supplies exact Registry "
         "file indexes for declared graph relationships, the current node's "
         "outputs, and review evidence needed by the current responsibility. "
         "The declared graph, not node-array position, determines upstream "
         "visibility and any affected rework region. Unaffected settled branches "
         "remain evidence. Use the supplied exact Registry facts; do not infer "
         "relationships from position or predict filenames."),
        ("Role-ownership and evidence-chain rule: perform only the work assigned "
         "to this role. Consume exact registered upstream evidence instead of "
         "repeating another role's work merely to recreate it. If upstream "
         "evidence is unsupported, contradictory, or insufficient, preserve "
         "that limitation while completing the work this role can legitimately "
         "perform; do not silently take over the upstream responsibility. The "
         "paired critic decides the declared A2C route."),
        ("Executable-work guidance: when a declared hard requirement or necessary "
         "registered evidence requires executing created or changed work, run a "
         "small bounded smoke check before a long or high-cost run, inspect the "
         "observed result, and correct an observed failure before scaling up. Do "
         "not run it after the immediate-completion condition is already met."),
        ("External-component guidance: consult registered external components "
         "when they are necessary for the assignment. The KB is one read-only "
         "example, not the only possible external component."),
        ("Output-port rule: when provider-writable semantic output_port_ids are "
         "listed below, every write_file call must use one of those exact ids. "
         "A business artifact name or filename is not an output_port_id. Omit "
         "output_port_id only when this initialization explicitly says the "
         "framework selects the binding."),
    ]
    if output_kind == "a2c_critic_document":
        sections.extend((
            "Critic route-ownership rule: select continue only for a specific "
            "unmet declared requirement or missing necessary evidence that "
            "belongs to the current actor and that another firing of this actor "
            "can and should correct. Select pass when this actor's responsibility "
            "is satisfied and no unresolved defect requires escalation. Select "
            "escalate when a real issue is outside this actor's local repair "
            "responsibility, including an upstream defect, a cross-role conflict, "
            "or a gap assigned to a later role; state the reason and relevant "
            "Registry evidence in the critique. Select give_up only when the "
            "allowed downstream work and finalization rework cannot complete the "
            "path. Give_up is a node disposition, not a run-terminal verdict: "
            "the framework closes non-activated downstream nodes and still "
            "routes settled evidence through the declared finalization path. "
            "Complete the critic decision immediately and never request "
            "optional improvement, a preferred method, or unrequired evidence.",
            "Critic multi-resource review guidance: use the registered summaries "
            "to plan the review before opening full bodies. When an exact "
            "comparison requires several registered files, issue every needed "
            "read_file or search_text call together in one turn so their results "
            "form one comparison set on the immediately following turn; then "
            "decide and write the critic document. Do not alternate whole-file "
            "reads across turns, and do not use workspace merely to cat or "
            "redisplay registered inputs already available through Registry "
            "tools. Use search_text for a specific claim, constant, requirement, "
            "or result, and reserve workspace for actual execution or ordinary "
            "workspace files. Re-read a full body only when a specifically "
            "identified missing passage cannot be resolved from its summary, "
            "bounded search, or the current comparison set.",
        ))
    if module_design:
        sections.append(
            "Workflow Designer boundary: author one complete ModuleDeclaration "
            "using registered component keys/config and symbolic typed links, "
            "entry/exit bindings, budgets and effects. The declaration describes "
            "only the requested workflow graph. Give each node a task-derived goal, clear "
            "responsibility boundary, registered upstream evidence to inspect, "
            "and expected result or evidence to produce. Do not invent or recommend a "
            "task-specific algorithm, solver, architecture, parameter value, "
            "numerical resolution, implementation procedure, experiment plan, "
            "or fallback method; solution choice belongs to the executing "
            "agent. A user-required or user-preferred method remains an Intake "
            "condition and must not be elaborated as Designer advice. "
            "Do not make a later role recreate earlier work merely to recover "
            "evidence. A prompt may naturally name an earlier agent or its "
            "results and may optionally place "
            "{{registry_index:<earlier_agent_id>}} where a graph-upstream index "
            "should be inserted; runtime replaces it with exact Registry facts. "
            "Declare logical interfaces and effects through registered component "
            "contracts, never concrete Registry runtime ids.")
    if output_kind == "finalization_review":
        sections.extend([
            ("Finalization immediate-decision rule: when the original task's "
             "declared hard requirements and necessary registered evidence are "
             "satisfied, select the declared accepting outcome (pass or complete) "
             "and complete immediately. Select rework or failure only for an "
             "unmet declared hard requirement, missing necessary evidence, or a "
             "mandatory framework-contract defect; never for an undeclared method "
             "or quality preference."),
            "Finalization capability rule: inspect the already-registered task, "
            "artifacts, settlement, and review evidence first. Use the available "
            "tools only when necessary to resolve a declared evidence gap. Do not "
            "rerun the task, train, solve, simulate, or create new empirical "
            "evidence when the registered evidence already supports the required "
            "decision. Write the review and any declared result/evidence file "
            "directly with write_file, then complete the interaction.",
        ])
    if "query_kb" in names:
        from ..external_kb import KB_QUERY_SKILL_DESCRIPTION, KB_QUERY_SKILL_ID
        skills.append(KB_QUERY_SKILL_ID)
        sections.extend([
            "KB distinction: the KB is one immutable read-only external-resource "
            "example; "
            "query_kb is the sole authorized/audited access tool; "
            f"{KB_QUERY_SKILL_ID} is framework guidance, not another content "
            "or control path.",
            "KB-use guidance: when the registered KB may "
            "materially help the assigned task, you may consult it before "
            "recreating information. The call is optional, and absent KB results "
            "are never task failure.",
            KB_QUERY_SKILL_DESCRIPTION,
        ])
    if "write_file" in names:
        skills.append("registered_output_write/v1")
    if "workspace" in names:
        skills.append("isolated_workspace_shell/v1")
        workspace_location = (
            "The exact firing-private workspace root for this firing is "
            f"{workspace_root}. Use that root and Registry-resolved exact "
            "Located-input/resource references; never guess a host-specific "
            "filesystem root. "
            if workspace_root is not None else "")
        sections.append(
            "Workspace rule: " + workspace_location +
            "workspace receives one opaque shell script and runs it "
            "from the firing workspace. Use it for all internal file inspection, "
            "source editing, custom/helper scripts, temporary files, supporting "
            "traces, and commands. To publish semantic output, use write_file "
            "with direct content or with an exact current Registry source. The "
            "same private workspace remains available while this responsibility "
            "is being completed. Use read_action_output for an earlier command "
            "result when needed. Network access is unavailable.")
        if "read_file" in names:
            sections.append(READ_FILE_WORKSPACE_ROUTING_GUIDANCE)
    if "query_environment_resources" in names:
        sections.append(
            "query_environment_resources is an optional informational lookup "
            "against the Registry inventory captured before provider use.")
    if "query_registry_resources" in names:
        sections.append(
            "Registry resource discovery: query_registry_resources searches only "
            "bounded metadata authorized to this firing and does not open bodies. "
            "Use its summaries first; select an exact resource identity or path, "
            "then use request_resource, read_file, or search_text only when the "
            "original body is needed.")
    if user_attachments:
        if ("query_registry_resources" not in names
                or "request_resource" not in names):
            raise ValueError(
                "attachment navigation requires Registry discovery and request")
        sections.append(
            "User attachments are present for this task. The following entries "
            "are body-free navigation metadata, not evidence. Search by "
            "attachment_id or display_name with query_registry_resources, "
            "request the selected exact resource with read access, and inspect "
            "it on the next turn with read_file/search_text for UTF-8 text or "
            "workspace for other media. Preserve or consume attachment meaning "
            "only as required by this role's existing output contract; do not "
            "create a separate material-catalog output.")
        sections.append("User attachment navigation:")
        sections.extend(
            "- " + json.dumps({
                "attachment_id": item.attachment_id,
                "ordinal": item.ordinal,
                "display_name": item.display_name,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in user_attachments)
    sections.append("Actually available skills: " + (
        ", ".join(sorted(skills)) if skills else "none"))

    if inputs:
        sections.append(
            "Located inputs (canonical navigation index; summaries describe "
            "purpose and important results but are not evidence):")
        sections.extend(
            "- " + json.dumps(
                {
                     "semantic_name": item.semantic_name,
                     "resource_ref": {
                         "resource_id": str(item.resource_ref.resource_id),
                         "resource_version_id": str(
                             item.resource_ref.resource_version_id),
                     },
                     "sandbox_path": item.sandbox_path,
                     "file_name": item.file_name,
                     "source_relative_path": item.source_relative_path,
                     "summary": item.summary,
                     "summary_truncated": item.summary_truncated,
                     "media_type": item.media_type,
                     "content_schema_ref": item.content_schema_ref,
                     "head_preview": item.head_preview,
                     "head_truncated": item.head_truncated,
                 },
                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in inputs)
        sections.append(
            "Located-input path rule: each sandbox_path already exists relative "
            "to the firing-private workspace root. Start with semantic_name, "
            "file_name, source_relative_path, and summary to select relevant "
            "files. A summary is a navigation abstract, never a substitute for "
            "the original evidence. When looking for a symbol, requirement, "
            "constant, or reported result, use search_text before reading full "
            "pages; then inspect only the necessary original passages through "
            + ("read_file" if "read_file" in names else "workspace")
            + ". A nonempty head_preview is only a bounded partial locator aid; "
            "short resources omit it so a complete body is never inlined.")
    else:
        sections.append("Located inputs: none.")
    prior_products = tuple(
        item for item in inputs
        if item.semantic_name.startswith("prior_product:"))
    if prior_products:
        sections.append(
            "Prior-product guidance: continue or revise the exact listed "
            "prior_product resources under the delivered feedback; do not "
            "substitute unrelated products.")

    sections.append(
        "Provider-writable semantic output_port_ids (exact): " + (
            ", ".join(semantic_ports) if semantic_ports else "none"))
    if evidence_ports:
        sections.extend([
            "Verified-evidence output_port_ids (exact): "
            + ", ".join(evidence_ports),
            ("Verified-evidence content rule: for each listed evidence port, "
             "register one nonempty JSON object with arbitrary free-form fields. "
             "No field, field name, order, wording, or copied action/result "
             "record is authoritative. The Registry separately owns observed "
             "tool execution and provenance facts."),
        ])
    sections.extend([
        "Broad output contract: " + contract,
        ("Ordinary documents: materialize the direct nonempty human-readable body "
         "in the workspace and register it against "
         "registry_v1/agent_document/v2. The body is opaque; do not "
         "construct a semantic JSON envelope and the framework will not wrap, "
         "parse, normalize, or rewrite it."
         if output_kind == "ordinary_document" else
         "ModuleDeclaration: materialize one JSON object that conforms to the "
         "registered rpnh/module_declaration/v1 schema, register it on the "
         "declared semantic output port, and do not use concrete runtime ids."
         if module_design else
         "Executable design index: the index remains the strict structural "
         "machine contract. Companion ordinary documents are direct nonempty "
         "opaque bodies under registry_v1/agent_document/v2."
         if output_kind == "executable_design_index" else
         "Finalization review document: materialize one direct UTF-8 JSON "
         "review object under outputs/, register it, and select exactly one outcome "
         "output_port_id listed above. The output_port_id is the verdict; do not "
         "duplicate it in protocol, reviewer, disposition, schema_version, or "
         "other routing fields. Review prose and its JSON organization are "
         "free-form and are not framework acceptance fields. "
         + finalization_target_rule + " Unknown extension fields are ignored by "
         "the outcome projection. "
         "If a result output_port_id is listed, a pass also "
         "requires exactly one separate result/evidence file on that port; "
         "rework writes only the review file."
         if output_kind == "finalization_review" else
         "A2C critic document: materialize and register one JSON object. Verdict "
         "must be one literal of "
         "continue, pass, escalate, or give_up. Critique prose is free-form and "
         "optional for framework parsing; unrelated fields are ignored. Write "
         "the document under "
         "outputs/ and omit output_port_id; the "
         "framework selects the fixed internal binding."),
    ])
    if not module_design:
        sections.append("Minimal legal examples (only available tools):")
        if "query_kb" in names:
            sections.extend([
                '- query_kb search: {"operation":"search","query":"relevant existing knowledge"}',
                '- query_kb read: {"operation":"read","item_id":"<exact search item_id>"}',
            ])
        if "query_registry_resources" in names:
            sections.append(
                '- query_registry_resources: {"query":"controller","view":"current"}')
        if "workspace" in names:
            sections.append(
                '- workspace: {"script":"ls -la && sed -n \'1,200p\' located_inputs/task.txt"}')
        if "write_file" in names:
            example = {
                "path": "outputs/report.md",
                "description": "Final report and its principal result.",
                "content": "# Final report\n\nResult.\n",
            }
            if output_kind == "finalization_review":
                example = {
                    "path": "outputs/finalization_review.json",
                    "description": "Final review based on registered evidence.",
                    "content": '{"body":"review based on registered evidence"}',
                }
            if semantic_ports:
                example["output_port_id"] = semantic_ports[0]
            sections.append(
                "- write_file: " + json.dumps(
                    example, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":")))
            if "query_registry_resources" in names:
                sections.append(
                    "- write_file from the exact current Registry row returned "
                    "above: " + json.dumps({
                        "path": "outputs/copied-report.md",
                        "description": (
                            "Semantic output projected from the selected current "
                            "Registry resource."),
                        "source_resource_ref": {
                            "resource_id": "resource:" + "0" * 32,
                            "resource_version_id": (
                                "resource_version:" + "0" * 32),
                        },
                        **({"output_port_id": semantic_ports[0]}
                           if semantic_ports else {}),
                    }, ensure_ascii=True, sort_keys=True,
                        separators=(",", ":"))
                    + " (replace both zero identities with the exact selected "
                    "current row identities)")
        if "query_environment_resources" in names:
            sections.append(
                '- query_environment_resources: {"packages":["numpy","scipy"]}')
    rejection_guidance = (
        "Fixed tool-rejection handling: structural_closure_failed means the "
        "current tool call violated a framework contract. Use its closed detail "
        "diagnostic to revise the call; do not repeat an unchanged rejected call. "
        "Never silently normalize or rewrite invalid arguments. "
        "A rejected finalization review reports the exact invalid field or "
        "conditional output bundle; "
        "semantic_output_port_required "
        "or semantic_output_port_undeclared requires one exact provider-writable "
        "semantic output_port_id listed above.")
    if "query_kb" in names:
        rejection_guidance += (
            " query_kb search is optional and an empty page is ordinary. A KB "
            "package or read-infrastructure fault is a framework fault, not a "
            "model-correctable tool rejection.")
    sections.append(rejection_guidance)
    payload = ("\n".join(sections) + "\n").encode("utf-8")
    return AgentSystemInitialization(
        invocation_ref=invocation_ref,
        sponsoring_transition_firing_ref=sponsoring_transition_firing_ref,
        role=role,
        tool_names=names,
        skill_ids=tuple(sorted(skills)),
        located_inputs=inputs,
        output_kind=output_kind,
        output_contract=contract,
        payload=payload,
    )

def optional_agent_loop_schema_data():
    """Only the generic schemas/types required by this optional boundary.

    Supply the returned schemas to HOST Registration and schemas/types to the
    public start_run catalog. No old digest-bearing turn schema is registered.
    """
    from .action_execution import (
        EXECUTION_PROVENANCE_DOCUMENT,
        EXECUTION_PROVENANCE_SCHEMA,
    )
    root = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
    schemas, types = {}, []
    for name, category in (("agent_loop/v1", "object"), ("agent_action/v2", "object"),
            ("agent_action/v3", "object"),
            ("agent_context_compaction/v3", "object"),
            ("agent_tool_error/v1", "object"), ("agent_loop_started/v1", "event"),
            ("agent_action_settled/v1", "event"), ("agent_loop_terminal/v1", "event"),
            ("provider_attempt_completed/v1", "event"),
            ("numerical_tool_profile/v1", "object")):
        family, version = name.split("/")
        document = json.loads((root / f"{family}.{version}.schema.json").read_text())
        schemas[document["$id"]] = document
        types.append(TypeDefinition(name, category, "optional-agent-loop", document["$id"],
            "authoritative" if category == "event" else None, "writer-only", "permanent",
            "registry-reference-replay", "exact-schema-and-reference-validation"))
    schemas[EXECUTION_PROVENANCE_SCHEMA] = EXECUTION_PROVENANCE_DOCUMENT
    recipe_schema = json.loads((root / "logical_provider_request_recipe.v1.schema.json").read_text())
    schemas[recipe_schema["$id"]] = recipe_schema
    from .program_execution import program_execution_schema_data
    program_schemas, program_types = program_execution_schema_data()
    schemas.update(program_schemas)
    types.extend(program_types)
    return schemas, tuple(types)


class ContextExecutionMixin:
    def _owner_reentry_prompt(self) -> str | None:
        """Project the current Registry-authorized reopen reason to agents."""

        from cpn.rpnh.registry.run_authority import (
            current_run_execution_authority,
        )
        _authority_ref, authority = current_run_execution_authority(
            self.core, self.kernel)
        raw_ref = authority.get("reopen_authorization_ref")
        if raw_ref is None:
            return None
        authorization = self.kernel._exact_object(
            _version_from_payload(raw_ref),
            expected_type="run_reopen_authorization/v1").metadata
        generation = authority.get("execution_generation")
        reason = authorization.get("reason")
        if (not isinstance(generation, int) or generation < 1
                or authorization.get("execution_generation") != generation
                or not isinstance(reason, str) or not reason):
            raise ResourceIntegrityFault(
                "current checkpoint reentry instruction is malformed")
        return (
            "OWNER_CHECKPOINT_REENTRY_INSTRUCTION (Registry-authorized, "
            f"execution generation {generation}): {reason} Continue from the "
            "restored checkpoint and preserve already completed work unless "
            "this instruction explicitly requires replacing it."
        )

    def _context(self, loop, *, native_resume: bool = False):
        canonical = canonical_invocation(
            self.core, self.kernel, loop.invocation_ref,
            require_current_writer=not native_resume)
        if canonical.context.operation_binding_ref != loop.operation_binding_ref:
            raise ResourceIntegrityFault("optional loop crossed its invocation binding")
        if native_resume:
            self.kernel._revalidate_invocation(
                canonical.context, boundary="optional-agent-loop-recovery",
                native_resume=True)
        else:
            InvocationLifecycle(self.core).revalidate_io(
                canonical.context, boundary="optional-agent-loop")
        return canonical.context

    def _declared(self, context):
        binding = self.kernel._exact_object(context.operation_binding_ref,
                                            expected_type="operation_binding/v1").metadata
        net = self.kernel._exact_object(context.net_instance_ref, expected_type="net_instance/v1")
        source = _resource_from_payload(net.metadata["team_net_declaration_resource_ref"])
        compiled = load_compiled_net(json.loads(self.kernel._read_firing_registered(context, source)))
        node = self.kernel._exact_object(_version_from_payload(binding["node_ref"]),
                                         expected_type="node_declaration/v1")
        transition = next(item for item in compiled.symbolic.transitions
                          if item.name == node.metadata["transition_id"])
        operation = next(item for item in compiled.operations if item.declaration.name == transition.operation)
        return binding, compiled, operation

    def _static(self, context, role):
        from .action_execution import OptionalAgentCapabilityUnavailable
        binding = self.kernel._exact_object(context.operation_binding_ref,
                                            expected_type="operation_binding/v1").metadata
        found = []
        for raw in binding["readable_resource_refs"]:
            ref = _version_from_payload(raw)
            if ref.entity_type != "resource_version/v1":
                continue
            resource = ResourceVersionRef(ref.entity_id, ref.version_id)
            prepared = self.kernel._firing_prepared(context, resource)
            if prepared.metadata.get("descriptors", {}).get("content_role") == role:
                found.append((resource, prepared, self.kernel._read_firing_registered(context, resource)))
        if len(found) != 1:
            raise OptionalAgentCapabilityUnavailable("optional HOST requires one exact " + role)
        if role == 'optional_agent_backend':
            from cpn.rpnh.public_material_contracts import decode, validate_public_backend
            validate_public_backend(decode(found[0][2]))
        return found[0]

    def current_agent_tool_catalog_v1(self, execution=None):
        from .action_execution import OptionalAgentCapabilityUnavailable
        if execution is not None:
            execution = self._execution(execution)
            _ref, _prepared, payload = self._static(
                execution.operation.canonical.context,
                "optional_agent_tool_catalog")
            return parse_agent_tool_catalog(payload)
        catalogs = [self.core.object_store.read_registered(self.core.get_version(row["version_id"]))
                    for row in self.core.event_store.canonical_object_rows(object_type="resource_version/v1")
                    if json.loads(row["metadata_json"]).get("descriptors", {}).get("content_role")
                    == "optional_agent_tool_catalog"
                    and json.loads(row["metadata_json"]).get("task_ref") == _ref_payload(self.owner.identity.task_ref)]
        if not catalogs or any(payload != catalogs[0] for payload in catalogs):
            raise OptionalAgentCapabilityUnavailable("optional default loop needs a single declared tool surface")
        return parse_agent_tool_catalog(catalogs[0])

    def _catalog(self, context, catalog):
        from .action_execution import (
            OPTIONAL_TOOL_BINDINGS,
            OptionalAgentCapabilityUnavailable,
        )
        ref, _, payload = self._static(context, "optional_agent_tool_catalog")
        _, compiled, operation = self._declared(context)
        managed = self._managed_tool_bindings(context)
        registration_keys = tuple(sorted(
            managed.get(name, {}).get("registration_key", name)
            for name in catalog.tool_names))
        if (payload != catalog.payload
                or tuple(operation.declaration.tools) != registration_keys):
            raise ResourceIntegrityFault("optional tool catalog differs from the exact admitted declaration")
        if (not catalog.tool_names
                or set(catalog.tool_names)
                - set(OPTIONAL_TOOL_BINDINGS) - set(managed)):
            raise OptionalAgentCapabilityUnavailable(
                "optional loop supports only exact declared capabilities")
        for name in catalog.tool_names:
            registration_key = managed.get(name, {}).get(
                "registration_key", name)
            actual = compiled.registrations["tool"][registration_key]
            if canonical_json(actual) != canonical_json(
                    self.owner.registration.declaration(
                        "tool", registration_key)):
                raise ResourceIntegrityFault("optional tool HOST registration changed")
            if name in managed:
                binding = managed[name]
                contracts = actual["contracts"]
                identity = actual["identity"]
                if (contracts.get("invocation_protocol")
                        != "rpnh/managed_native_plugin_tool_invocation/v2"
                        or identity.get("provider_name") != name
                        or identity.get("selector") != binding["selector"]
                        or identity.get("binding_digest")
                        != binding["binding_digest"]
                        or identity.get("plugin_catalog_digest")
                        != binding["plugin_catalog_digest"]
                        or identity.get("effect") != binding["effect"]):
                    raise ResourceIntegrityFault(
                        "managed tool differs from its node-scoped binding")
            elif (actual["identity"] != OPTIONAL_TOOL_BINDINGS[name][1]["identity"]
                    or actual["contracts"] != OPTIONAL_TOOL_BINDINGS[name][1]["contracts"]):
                raise ResourceIntegrityFault("optional tool is not its declared capability implementation")
        return ref

    def _managed_tool_bindings(self, context):
        """Read the immutable per-node visible-name to registration mapping."""
        from .action_execution import OptionalAgentCapabilityUnavailable
        try:
            _ref, _prepared, payload = self._static(
                context, "optional_agent_managed_tool_bindings")
        except OptionalAgentCapabilityUnavailable:
            return {}
        try:
            document = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "managed tool binding resource is not JSON") from exc
        bindings = document.get("bindings") if isinstance(document, Mapping) else None
        if (not isinstance(document, Mapping)
                or set(document) != {
                    "schema_version", "semantic_node_id",
                    "plugin_catalog_digest", "bindings"}
                or document.get("schema_version")
                != "rpnh/agent_loop_managed_tool_bindings/v1"
                or not isinstance(bindings, Mapping)
                or any(not isinstance(name, str)
                       or not isinstance(value, Mapping)
                       or set(value) != {
                           "registration_key", "selector", "binding_digest",
                           "effect", "max_result_bytes"}
                       for name, value in bindings.items())):
            raise ResourceIntegrityFault(
                "managed tool binding resource is malformed")
        digest = document["plugin_catalog_digest"]
        return {
            name: {**dict(value), "plugin_catalog_digest": digest}
            for name, value in bindings.items()
        }

    def prepare_agent_loop_start_v1(self, execution, catalog, *, idempotency_key):
        from .action_execution import OptionalAgentCapabilityUnavailable
        execution = self._execution(execution)
        context = execution.operation.canonical.context
        binding, _, operation = self._declared(context)
        if operation.declaration.config.get("agent_loop_role") not in {
                "actor", "critic", "finalization_reviewer"}:
            raise OptionalAgentCapabilityUnavailable(
                "optional default service requires one declared agent role")
        target = self._target(context)
        existing = self.mechanical_lifecycle.loop_for_invocation(
            context.invocation_ref)
        identifier = (existing.loop_id if existing is not None
                      else str(new_id("agent_loop")))
        from .service import StartAgentLoopCommand
        return StartAgentLoopCommand(identifier, context.invocation_ref, context.operation_binding_ref,
            _version_from_payload(binding["principal_ref"]), target.model_condition,
            operation.declaration.config["resource_bounds"]["max_tool_turns"], catalog,
            self._catalog(context, catalog), idempotency_key)

    def _target(self, context):
        binding = self.kernel._exact_object(context.operation_binding_ref,
                                            expected_type="operation_binding/v1").metadata
        ref = _resource_from_payload(binding["llm_input_target_ref"])
        data = json.loads(self.kernel._read_firing_registered(context, ref))
        self.core.catalog.validate_schema_ref("registry_v1/llm_input_target/v1", data)
        return LLMInputTarget(
            data["model_condition"], data["max_output_tokens"],
            data["max_response_bytes"], data.get("context_window_tokens"),
            data.get("context_compaction_retained_tokens"))

    @staticmethod
    def _row_ref(row, object_type, logical_kind, version_kind):
        return VersionRef(
            object_type,
            TypedId.parse(str(row["logical_id"]), expected=logical_kind),
            TypedId.parse(str(row["version_id"]), expected=version_kind),
        )

    def prepare_agent_system_initialization_v1(self, execution, loop, catalog):
        execution = self._execution(execution, loop)
        self._catalog(self._context(loop), catalog)
        _binding, _compiled, operation = self._declared(
            self._context(loop))
        semantic_outcomes = tuple(
            outcome for outcome in operation.declaration.outcomes
            if outcome.name != "interrupted")
        outcome_bundles = {
            outcome.name: [product.port for product in outcome.products]
            for outcome in semantic_outcomes
        }
        semantic_output_ports = sorted({
            product.port
            for outcome in semantic_outcomes
            for product in outcome.products
        })
        located = self._project_workspace(execution, loop)
        task_calls_used = self.core.event_store.actual_model_call_counts()[0]
        return build_agent_system_initialization(
            invocation_ref=loop.invocation_ref,
            sponsoring_transition_firing_ref=(
                execution.operation.firing.transition_firing_ref),
            role=operation.declaration.config["agent_loop_role"],
            catalog=catalog, located_inputs=located,
            output_kind="ordinary_document",
            output_contract=(
                "Use exactly one declared semantic outcome bundle: "
                + json.dumps(outcome_bundles, sort_keys=True)
                + ". Every agent-created regular workspace file is registered "
                  "and later agents receive the current Registry version."),
            llm_turn_cap=loop.llm_turn_budget,
            llm_turns_used=loop.llm_turns_used,
            task_call_cap=self.core.event_store.ordinary_model_call_limit(),
            task_calls_used=task_calls_used,
            semantic_output_port_ids=tuple(semantic_output_ports),
            workspace_root=".",
        )

    @staticmethod
    def _input_path(ref):
        return f"registered_resources/{ref.resource_id.value}/{ref.resource_version_id.value}/content"

    def prepare_agent_turn_context_v1(
            self, execution, loop, catalog, *,
            tool_output_byte_limit=10_000):
        execution = self._execution(execution, loop)
        if (isinstance(tool_output_byte_limit, bool)
                or not isinstance(tool_output_byte_limit, int)
                or tool_output_byte_limit < 128):
            raise TypeError(
                "agent context tool output byte limit is invalid")
        initialization = self.prepare_agent_system_initialization_v1(execution, loop, catalog)
        context = self._context(loop)
        binding, _, _ = self._declared(context)
        target_ref = _resource_from_payload(binding["llm_input_target_ref"])
        prompt_ref, prompt_prepared, prompt_payload = self._static(context, "optional_agent_prompt")
        catalog_ref, catalog_prepared, catalog_payload = self._static(context, "optional_agent_tool_catalog")
        if catalog_ref != loop.tool_catalog_ref:
            raise ResourceIntegrityFault("optional context crossed its exact tool catalog")
        def read_response(ref):
            return self.kernel._read_firing_registered(context, ref)

        def render_tool_result(action, action_ref):
            result = action.result_metadata
            if result and result["kind"] == "managed_native_plugin_result/v1":
                from .managed_output import render_managed_output
                return render_managed_output(
                    result, _ref_payload(action_ref),
                    reader_available="read_managed_output" in catalog.tool_names,
                    max_bytes=tool_output_byte_limit)
            if (result and result["kind"] == "workspace_execution/v1"):
                return dict(
                    result, agent_action_ref=_ref_payload(action_ref))
            if result and result["kind"] == "registered_file_read/v1":
                return dict(
                    result, content=self._decoded_read(
                        context, _resource_from_payload(
                            result["resource_ref"]), action.arguments))
            if (result and result["kind"]
                    == "parent_owned_delegated_subtask_result/v1"):
                payload = self.kernel._read_firing_registered(
                    context, _resource_from_payload(
                        result["result_resource_ref"]))
                try:
                    content = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ResourceIntegrityFault(
                        "delegated result is not exact JSON text") from exc
                if not isinstance(content, str) or not content.strip():
                    raise ResourceIntegrityFault(
                        "delegated result lacks concrete text")
                return dict(result, content=content)
            if action.tool_error_ref is not None:
                return self.kernel._exact_object(
                    action.tool_error_ref).metadata
            return result

        try:
            messages = self.mechanical_lifecycle.history_messages(
                loop, read_response=read_response,
                render_tool_result=render_tool_result)
        except Exception as exc:
            raise ResourceIntegrityFault(
                "optional history lacks exact immutable turn/action facts") from exc
        # Registry retains the complete immutable action result.  Only the
        # model-visible history is bounded, using the same user-configured
        # limit as compaction.  This protects the very next turn as well as
        # later compacted history; a large first tool result must not reach the
        # provider before context-pressure compaction has a prior turn to
        # summarize.
        messages = reduce_tool_messages(
            messages, byte_limit=tool_output_byte_limit)
        messages = bound_tool_result_groups(messages)
        events = self.mechanical_lifecycle.turn_events(loop)
        from cpn.components.request_protocol import validate_llm_request_message_history
        validate_llm_request_message_history(messages)
        target = self._target(context)
        located = tuple({"semantic_name": item.semantic_name, "resource_ref": _resource_payload(item.resource_ref),
            "location": item.sandbox_path, "access_cue": "read_file", "media_type": item.media_type,
            "content_schema_ref": item.content_schema_ref} for item in initialization.located_inputs)
        prior = tuple(
            self.mechanical_lifecycle.turn_ref_from_event(event)
            for event in events)
        interruption = self.mechanical_lifecycle.hydrate_length_interruption(
            loop, sequence=loop.next_turn_sequence,
            read_response=read_response)
        forced_reason = None
        if interruption is not None:
            overlay = self.mechanical_lifecycle.latest_context_overlay(loop)
            if overlay is None:
                forced_reason = "response_length"
            else:
                document = self.mechanical_lifecycle.exact_object_document(
                    overlay.compaction_ref,
                    expected_type="agent_context_compaction/v3")
                if (document.get("trigger_reason") != "response_length"
                        or document.get(
                            "interrupted_llm_invocation_attempt_ref")
                        != _ref_payload(
                            interruption.llm_invocation_attempt_ref)):
                    forced_reason = "response_length"
        from .service import PreparedAgentTurnContext
        return PreparedAgentTurnContext(loop, execution, initialization, located,
            tuple(self.kernel._read_firing_registered(context, item.resource_ref) for item in initialization.located_inputs),
            prior, prior, target, target_ref, self.kernel._firing_prepared(context, target_ref),
            prompt_ref, prompt_prepared, prompt_payload, (), catalog_prepared, catalog_payload,
            canonical_json(messages), canonical_json([_ref_payload(ref) for ref in prior]),
            forced_compaction_reason=forced_reason)

    def prepared_agent_turn_context_scope_v1(self, prepared_context):
        self._current(prepared_context.loop)
        return nullcontext(prepared_context)

    def _envelope(self, prepared, catalog, *, owner_messages=()):
        prompt = json.loads(prepared.prompt_payload)
        envelope = materialize_agent_request_envelope(
            model_condition=prepared.target.model_condition,
            max_output_tokens=prepared.target.max_output_tokens,
            system_content=prepared.initialization.payload.decode("utf-8"),
            prompt_messages=prompt["messages"],
            history_messages=json.loads(prepared.prior_turn_messages_payload),
            tool_descriptors=catalog.tool_descriptors,
            source_prompt_ref=_resource_payload(prepared.prompt_ref),
            tool_catalog_ref=_resource_payload(prepared.loop.tool_catalog_ref),
            owner_reentry_prompt=self._owner_reentry_prompt(),
            owner_messages=owner_messages)
        self.core.catalog.validate_schema_ref("runtime/llm_request_envelope/v1", envelope)
        return canonical_json(envelope)


__all__ = [
    "ContextExecutionMixin", "UserAttachmentNavigation",
    "build_agent_system_initialization", "optional_agent_loop_schema_data",
    "render_registered_hardware_inventory",
]
