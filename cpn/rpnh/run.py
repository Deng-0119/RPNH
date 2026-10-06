"""Fresh Module ownership through the sole Registry writer.

Starting publishes owner inputs, the declared graph, its initial marking and
explicit budgets. It does not execute a model, provider, tool or component.
Control clients obtain snapshots/commands through the owner's channel, not by
calling this writer constructor.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .compiler import compile_module
from .module import ModuleDeclaration
from .registration import Registration
from .registry._registry import _RegistryCore
from .registry.bootstrap import (
    NativeBootstrapManifest, NativeRunIdentity, _bootstrap_identity,
)
from .registry.identities import new_id
from .registry.models import VersionRef
from .registry.module_budgets import ModuleBudgetDeclaration, publish_module_budgets
from .registry.module_configuration import publish_module_configuration
from .registry.module_execution import admit_module_firing, install_active_module_claims
from .registry.module_marking import publish_module_initial_marking
from .registry.module_nets import publish_module_net
from .registry.module_operations import publish_module_operations
from .registry.module_runtime import hydrate_module_runtime
from .registry.operations import bind_operation_registration
from .registry.publication import _version_from_payload, _resource_from_payload
from .registry.registration_gateway import RegistryRegistrationGateway
from .registry.resource_service import _ResourceServiceKernel, _publish_private_system
from .registry.resources import PrivateSystemOrigin, PublishResource
from .registry.schema_catalog import canonical_json, PROTECTED_SCHEMA_REFS
from .registry.strict_contracts import publish_user_authority_decision, ref_payload


@dataclass(frozen=True)
class OwnerInput:
    schema_id: str
    payload: bytes
    summary: str


@dataclass(frozen=True, slots=True)
class _ResumeMaterial:
    identity: NativeRunIdentity
    bootstrap_ref: VersionRef
    executable: Any
    structure: Any
    original_input_ref: Any
    principal_ref: VersionRef
    task_round_ref: VersionRef
    recovery_manifest_ref: VersionRef
    budgets: ModuleBudgetDeclaration


def _validate_resume_registration(
        core: _RegistryCore, registration: Registration, compiled: Any) -> None:
    """Check the complete HOST inventory before reserving a writer epoch."""

    durable: dict[tuple[str, str], list[object]] = {}
    for row in core.event_store.canonical_object_rows(
            object_type="resource_version/v1"):
        metadata = json.loads(row["metadata_json"])
        descriptors = metadata.get("descriptors", {})
        kind = descriptors.get("host_registration_kind")
        key = descriptors.get("registered_key")
        if not isinstance(kind, str) or not isinstance(key, str):
            continue
        prepared = core.get_version(row["version_id"])
        try:
            payload = json.loads(core.object_store.read_registered(prepared))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "resume HOST registration is not readable JSON") from exc
        durable.setdefault((kind, key), []).append(
            {"kind": "schema", "key": key, "schema": payload}
            if kind == "schema" else payload)
    for supplied in registration.declarations():
        kind = supplied["kind"]
        key = supplied["key"]
        selected = compiled.registrations.get(kind, {}).get(key)
        if selected != supplied and supplied not in durable.get((kind, key), []):
            raise ValueError(
                f"resume {kind} registration differs from the run: {key}")
    for kind, declarations in compiled.registrations.items():
        for key, expected in declarations.items():
            if registration.declaration(kind, key) != expected:
                raise ValueError(
                    f"resume {kind} registration differs from the run: {key}")
            if kind != "schema":
                registration.resolve(kind, key)


def _load_resume_material(
        core: _RegistryCore, registration: Registration) -> _ResumeMaterial:
    """Read and validate immutable resume material without opening a writer."""

    from .registry.event_store import validate_registered_net_closure
    from .registry.strict_contracts import _registered

    try:
        run_ref = _version_from_payload(json.loads(
            core.event_store.get_meta("native_run_ref")))
        run = _ResourceServiceKernel(core)._exact_object(
            run_ref, expected_type="native_run_identity/v1").metadata
        identity = NativeRunIdentity(
            run_ref=run_ref,
            task_ref=_version_from_payload(run["task_ref"]),
            task_branch_ref=_version_from_payload(run["task_branch_ref"]),
            genesis_manifest_ref=_version_from_payload(json.loads(
                core.event_store.get_meta("native_genesis_ref"))),
            branch_id=run["branch_id"],
            protocol_versions=tuple(run["protocol_versions"]),
        )
        bootstrap_ref = _version_from_payload(json.loads(
            core.event_store.get_meta("bootstrap_command_ref")))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("resume run identity is malformed") from exc

    executable, structure, _marking = hydrate_module_runtime(core)
    _validate_resume_registration(core, registration, structure.compiled)
    net = validate_registered_net_closure(
        core.event_store, core.catalog, executable.net_ref)
    root_ref = _version_from_payload(net["team_design_root_ref"])
    _root_ref, root = _registered(core, root_ref, "team_design_root/v1")
    principal_ref = _version_from_payload(root["owner_principal_ref"])
    task_round_ref = _version_from_payload(root["task_round_ref"])

    immutable_targets = []
    for row in core.event_store.canonical_relation_rows():
        metadata = json.loads(row["metadata_json"])
        if metadata.get("authority_role") != "immutable_genesis":
            continue
        target = json.loads(row["target_json"])
        if target.get("entity_type") == "resource_version/v1":
            immutable_targets.append(_resource_from_payload({
                "resource_id": target["entity_id"],
                "resource_version_id": target["version_id"],
            }))
    immutable_targets = list(dict.fromkeys(immutable_targets))
    if len(immutable_targets) != 1:
        raise ValueError("resume run lacks one immutable input authority")

    manifest_ref = core.recovery_manifest_ref()
    _manifest_ref, manifest = _registered(
        core, manifest_ref, "task_recovery_manifest/v1")
    budgets = ModuleBudgetDeclaration(
        tuple(manifest["budget_buckets"]),
        tuple(manifest["protocol_versions"]),
        manifest["ordinary_global_cap"],
        manifest["terminal_quota"],
        manifest["task_total_hard_cap"],
        manifest["finalization_budget"],
    )
    return _ResumeMaterial(
        identity=identity,
        bootstrap_ref=bootstrap_ref,
        executable=executable,
        structure=structure,
        original_input_ref=immutable_targets[0],
        principal_ref=principal_ref,
        task_round_ref=task_round_ref,
        recovery_manifest_ref=manifest_ref,
        budgets=budgets,
    )


def _operation_services(core):
    """Build owner/read-only operation services over one exact Core."""
    from .registry.operation_repository import (
        OperationRepositoryHost, RegistryOperationAuthorityRepository,
    )
    from .registry.resource_verification import (
        verify_resource, verify_petri_input_receipt,
    )
    from .registry.errors import ResourceIntegrityFault
    kernel = _ResourceServiceKernel(core)

    def historical_input(invocation_ref, resource_ref):
        from .registry.resources import (
            ResourceDeliveryReceipt, HistoricalPetriInputArtifact,
        )
        from .registry.resource_service import _resource_payload
        matches = []
        for event in core.event_store.list_events_by_type(
                ("resource_delivery_acknowledged/v1",)):
            delivery_ref = _version_from_payload(
                event.payload["terminal_delivery_ref"])
            delivery = kernel._exact_object(
                delivery_ref, expected_type="resource_delivery/v1")
            if (delivery.metadata["context_ref"] == ref_payload(invocation_ref)
                    and delivery.metadata["resource_ref"]
                    == _resource_payload(resource_ref)):
                matches.append((event, delivery_ref))
        if len(matches) != 1:
            raise ResourceIntegrityFault(
                "registered input lacks exact acknowledged delivery")
        event, delivery_ref = matches[0]
        reads = [read for read in core.event_store.list_events_by_transaction(
                     str(event.transaction_id))
                 if (read.event_type == "observed_read/v1"
                     and read.transaction_id == event.transaction_id)]
        if len(reads) != 1:
            raise ResourceIntegrityFault(
                "acknowledged input lacks exact same-transaction observed read")
        receipt = ResourceDeliveryReceipt(
            delivery_ref, event.event_id, "acknowledged", reads[0].event_id)
        authority = verify_petri_input_receipt(
            core, kernel, receipt, resource_ref)
        from .registry.firing_authority import canonical_invocation
        prepared = kernel._exact_object(
            delivery_ref, expected_type="resource_delivery/v1")
        canonical = canonical_invocation(
            core, kernel,
            _version_from_payload(prepared.metadata["context_ref"]),
            require_current_writer=not core.read_only)
        artifact = verify_resource(
            core, kernel, canonical, resource_ref,
            native_resume=core.read_only)
        return HistoricalPetriInputArtifact(
            kernel._read_firing_registered(canonical.context, resource_ref),
            artifact, authority)

    host = OperationRepositoryHost(
        core, kernel,
        verify_resource=lambda canonical, ref: verify_resource(
            core, kernel, canonical, ref,
            native_resume=core.read_only),
        historical_input=historical_input,
        historical_output=lambda canonical, ref: verify_resource(
            core, kernel, canonical, ref,
            native_resume=core.read_only),
        verify_input_receipt=lambda receipt, ref: verify_petri_input_receipt(
            core, kernel, receipt, ref),
    )
    return (
        kernel,
        RegistryOperationAuthorityRepository(core, kernel, host=host),
        historical_input,
    )


class RunOwner:
    """Execution-owning host, never instantiated by an observer/client."""

    def __init__(self, core, registration, identity, publication, original_input_ref,
                 principal_ref, task_round_ref, bootstrap_ref, schema_gateway, budgets):
        self._core = core
        self.registration = registration
        self.identity = identity
        self.publication = publication
        self.original_input_ref = original_input_ref
        self.current_input_ref = original_input_ref
        self.principal_ref = principal_ref
        self.task_round_ref = task_round_ref
        self.bootstrap_ref = bootstrap_ref
        self.schema_gateway = schema_gateway
        self.budgets = budgets
        self.admission_paused = False
        self.host_execution_bindings = None
        from .owner_commands import OwnerControl
        self.control = OwnerControl(self)

    def command(self, name, arguments, *, command_id):
        if name == "message":
            return self.control.message(arguments, command_id)
        if name == "edit":
            return self.control.edit(arguments, command_id)
        if name == "retract_edit":
            return self.control.edits.retract(arguments, command_id)
        if name == "correct_edit":
            return self.control.edits.correct(arguments, command_id)
        raise ValueError("unregistered owner command")

    def request_boundary(self, context, *, request_id):
        return self.control.request_boundary(context, request_id)

    def operation_repository(self):
        """Current exact observations; no component/policy import or writer clone."""
        kernel, repository, _historical_input = _operation_services(self._core)
        return kernel, repository

    def start(self, admitted, *, command_id):
        from .registry.module_gateway import start_module_firing
        kernel, repository = self.operation_repository()
        return start_module_firing(
            self._core, kernel, repository,
            (admitted.admission.context.invocation_ref
             if admitted.started is None else None),
            started=admitted.started,
            preflight=admitted.preflight,
            idempotency_key=command_id)

    def access_resource(self, execution, resource_ref, *, access_mode,
                        command_id):
        """Commit a Module-selected exact resource access through Harness."""
        from .registry.firing_resource_access import (
            access_module_firing_resource,
        )
        kernel, repository = self.operation_repository()
        return access_module_firing_resource(
            self._core,
            kernel,
            repository,
            execution,
            resource_ref,
            access_mode,
            idempotency_key=command_id,
        )

    def products(self, execution, *, outcome_id, products, command_id):
        from .registry.operation_outputs import publish_operation_products
        kernel, repository = self.operation_repository()
        return publish_operation_products(self._core, kernel, repository, execution,
            outcome_id=outcome_id, products=products, idempotency_key=command_id)

    def succeed(self, outputs, *, command_id, workset_action=None):
        """Settle one generic registered operation; module policy stays outside H."""
        from .registry.firing_success import succeed_module_operation
        from .workspace_settlement import prepare_firing_workspace_plans
        kernel, repository = self.operation_repository()
        workspace_plans = prepare_firing_workspace_plans(
            self._core, kernel, outputs, idempotency_key=command_id)
        result = succeed_module_operation(self._core, kernel, repository, outputs,
            idempotency_key=command_id, registration=self.registration,
            candidate_publisher=getattr(self, "revision_candidate_publisher", None),
            workspace_plans=workspace_plans, workset_action=workset_action)
        self.control.edits.advance()
        return result

    def recover_completed_firing(self, recovery, *, command_id):
        """Settle one preflight-proven executor return without dispatching HOST."""
        from .registry.firing_recovery import (
            recover_registered_operation_completion,
        )
        from .registry.run_authority import current_run_execution_authority
        from .workspace_settlement import prepare_firing_workspace_plans
        kernel, repository = self.operation_repository()
        workspace_plans = prepare_firing_workspace_plans(
            self._core, kernel, recovery.outputs,
            idempotency_key=command_id)
        _authority_ref, current = current_run_execution_authority(
            self._core, kernel)
        result = recover_registered_operation_completion(
            self._core, kernel, repository, recovery,
            current_run=current, idempotency_key=command_id,
            registration=self.registration,
            candidate_publisher=getattr(
                self, "revision_candidate_publisher", None),
            workspace_plans=workspace_plans)
        self.control.edits.advance()
        return result

    def recover_interrupted_firing(self, recovery, *, command_id):
        """Settle one owner-abandoned stale firing without replaying HOST."""
        from .registry.firing_recovery import (
            recover_registered_operation_interruption,
        )
        from .registry.run_authority import current_run_execution_authority
        from .workspace_settlement import prepare_firing_workspace_plans
        kernel, repository = self.operation_repository()
        # A replacement owner can observe an AgentLoop after its last action
        # batch settled but before the normal executor finalized the private
        # workspace.  At those durable, action-free loop boundaries, perform
        # the same one-shot workspace finalization used by the in-process stop
        # path before preparing the interrupted firing's revision.  This keeps
        # already-settled action effects while never treating an in-progress
        # workspace action as a checkpoint.
        from cpn.components.agent_loop.models import AgentLoopState
        from cpn.components.agent_loop.optional_execution import (
            OptionalAgentLoopRegistryService,
        )
        agent_registry = OptionalAgentLoopRegistryService(
            owner=self, kernel=kernel, repository=repository,
            provider_attempts=None, invoke_tool=None)
        context = recovery.execution.operation.canonical.context
        loop_rows = self._core.event_store.agent_loop_rows_for_invocation(
            invocation_ref=context.invocation_ref,
            operation_binding_ref=context.operation_binding_ref)
        loop_documents = tuple(
            json.loads(str(row["metadata_json"])) for row in loop_rows)
        loop_identities = {
            str(document["agent_loop_id"])
            for document in loop_documents}
        if len(loop_identities) > 1:
            from .registry.errors import ResourceIntegrityFault
            raise ResourceIntegrityFault(
                "interrupted firing has multiple exact AgentLoops")
        loop = None
        if loop_documents:
            highest_revision = max(
                int(document["revision"])
                for document in loop_documents)
            current_documents = tuple(
                document for document in loop_documents
                if int(document["revision"]) == highest_revision)
            if len(current_documents) != 1:
                from .registry.errors import ResourceIntegrityFault
                raise ResourceIntegrityFault(
                    "interrupted firing has an ambiguous AgentLoop head")
            loop = agent_registry.mechanical_lifecycle.hydrate_loop(
                _version_from_payload(
                    current_documents[0]["agent_loop_ref"]))
        if loop is not None:
            safe_workspace_states = frozenset({
                AgentLoopState.NEW,
                AgentLoopState.WAITING_FOR_LLM,
                AgentLoopState.COMPACTING,
                AgentLoopState.WAITING_RESOURCE,
                AgentLoopState.COMPLETED,
                AgentLoopState.TIMED_OUT,
                AgentLoopState.EXHAUSTED,
                AgentLoopState.INFRASTRUCTURE_FAILED,
                AgentLoopState.RECONCILIATION_REQUIRED,
            })
            if loop.state in safe_workspace_states:
                firing_version = (
                    recovery.execution.operation.firing
                    .transition_firing_ref.version_id)
                agent_registry.finalize_recovered_agent_workspace_v1(
                    recovery.execution, loop,
                    idempotency_key=(
                        "registered-operation-workspace-finalization:"
                        f"{firing_version}"))
        # Interruption preserves the firing-private workspace just like an
        # in-process owner stop.  The interrupted semantic outcome has no
        # products, but settlement still publishes the exact workspace head.
        from .registry.operations import register_operation_outputs
        outputs = register_operation_outputs(
            repository, recovery.execution, (),
            selected_outcome_id="interrupted",
            idempotency_key=(
                "owner-interrupt-plan:"
                f"{recovery.execution.operation_execution_lease_ref.version_id}"))
        workspace_plans = prepare_firing_workspace_plans(
            self._core, kernel, outputs,
            idempotency_key=command_id)
        _authority_ref, current = current_run_execution_authority(
            self._core, kernel)
        result = recover_registered_operation_interruption(
            self._core, kernel, repository, recovery,
            current_run=current, idempotency_key=command_id,
            registration=self.registration,
            candidate_publisher=getattr(
                self, "revision_candidate_publisher", None),
            workspace_plans=workspace_plans)
        self.control.edits.advance()
        return result

    def terminal(self):
        from .registry.module_terminal import register_module_terminal
        return register_module_terminal(self._core, _ResourceServiceKernel(self._core))

    def record_owner_stop(self, *, idempotency_key: str) -> VersionRef:
        """Record an exact authorized launcher stop at the safe owner boundary.

        No signal handling, firing settlement, claim release or writer reopen.
        The caller is responsible for the explicit owner's stop authorization.
        """
        if self.control.edits.queue:
            raise RuntimeError(
                "owner stop is blocked while a graph replacement is pending; "
                "retract it or let active firings settle")
        from .registry.run_authority import record_owner_stop
        return record_owner_stop(self._core, _ResourceServiceKernel(self._core),
                                 idempotency_key=idempotency_key)

    def admit(self, transition_id: str, *, logical_tau: int, command_id: str,
              prepare_admission=None):
        if self.admission_paused:
            raise RuntimeError("new firing admission is paused for owner graph editing")
        return admit_module_firing(self._core, transition_id=transition_id,
            logical_tau=logical_tau, idempotency_key=command_id,
            prepare_admission=prepare_admission)

    def snapshot(self):
        """Exact read-only data, with no score/terminal or liveness authority."""
        executable, structure, marking = hydrate_module_runtime(self._core)
        from .marking import TeamNetMarking
        current = TeamNetMarking.from_authority(structure, marking)
        active = install_active_module_claims(self._core, current, executable.net_ref)
        from .registry.resource_wait_snapshot import resource_wait_snapshot
        return {"run_ref": ref_payload(self.identity.run_ref),
            "task_ref": ref_payload(self.identity.task_ref),
            "net_ref": ref_payload(executable.net_ref),
            "checkpoint_ref": ref_payload(marking.checkpoint_ref),
            "declaration": structure.compiled.source.to_dict(),
            "admission_paused": self.admission_paused,
            "pending_edits": [{"command_id": edit.command_id, "status": edit.status,
                "candidate_ref": ref_payload(edit.publication.declaration_resource_ref.as_version_ref())}
                for edit in self.control.edits.queue],
            "pending_messages": self.control.pending_messages(),
            "active_firings": [ref_payload(firing.transition_firing_ref) for firing in active],
            "resource_wait": resource_wait_snapshot(self._core, task_ref=self.identity.task_ref,
                net_ref=executable.net_ref,
                active_firing_refs=[firing.transition_firing_ref for firing in active]),
            "marking": [{"token_ref": ref_payload(t.token_ref), "place": t.state.place,
                          "kind": t.state.kind, "verdict": t.state.verdict,
                          "resource_ref": None if t.state.resource_ref is None else
                              ref_payload(t.state.resource_ref.as_version_ref())}
                         for t in marking.tokens],
            "enabled_transitions": list(current.enabled_transitions()),
            "global_liveness": "UNKNOWN"}


def start_run(module: ModuleDeclaration, registration: Registration, *,
              run_dir: str | Path, task_input: OwnerInput,
              entry_inputs: Mapping[str, OwnerInput], budgets: ModuleBudgetDeclaration,
              model_condition: str, owner_statement: str,
              owner_name: str = "Owner", command_id: str,
              resource_inputs: Mapping[str, OwnerInput] | None = None,
              inventory_input: OwnerInput | None = None,
              catalog=None, host_execution_bindings=None,
              configuration_sources=None) -> RunOwner:
    """Create one fresh writer lineage from the shared human/AI declaration.

    HOST implementations are explicitly trusted Registration callables; JSON
    carries their keys only. Inputs are owner-supplied schema-valid bytes and
    are immutable registered resources. No submitted request is rewritten.
    """
    if not isinstance(task_input, OwnerInput) or not isinstance(budgets, ModuleBudgetDeclaration):
        raise TypeError("start_run requires explicit typed owner input and budget data")
    if not command_id or not owner_statement or not owner_name or not model_condition:
        raise ValueError("owner command, statement, name and exact model condition are required")
    compiled = compile_module(module, registration)
    if compiled.source.budget_buckets:
        declared = {b.bucket_id: asdict(b) for b in compiled.source.budget_buckets}
        supplied = {b["bucket_id"]: dict(b) for b in budgets.budget_buckets}
        if declared != supplied:
            raise ValueError("run budgets differ from the shared Module's exact declared buckets")
    ports = {port.name: port for port in compiled.ports}
    required = {key for key, name in compiled.symbolic.entry.items() if ports[name].minimum > 0}
    if not required <= set(entry_inputs) <= set(compiled.symbolic.entry):
        raise ValueError("owner inputs must bind required entries without unknown keys")
    input_bundles = {}
    for key, value in entry_inputs.items():
        values = (value,) if isinstance(value, OwnerInput) else tuple(value)
        port = ports[compiled.symbolic.entry[key]]
        if (any(not isinstance(item, OwnerInput) or item.schema_id != port.schema for item in values)
                or not port.minimum <= len(values) <= port.maximum):
            raise ValueError("owner entry bundle differs from declared schema/quantity")
        input_bundles[key] = values
    resource_inputs = {} if resource_inputs is None else dict(resource_inputs)
    resource_symbols = {lease.name for lease in compiled.symbolic.lease_identities if lease.kind == "resource"}
    if set(resource_inputs) != resource_symbols:
        raise ValueError("owner resource inputs must bind exact declared resource lease symbols")
    # Optional libraries supply their schema/type data explicitly at HOST
    # startup; Core does not import or discover those libraries itself.
    core = _RegistryCore(Path(run_dir), create=True, catalog=catalog)
    identity = _bootstrap_identity(core, NativeBootstrapManifest(tuple(budgets.protocol_versions)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    registration.bind_schema_catalog(core.catalog)
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    registration.bind_gateway(gateway)
    from .registry.schema_catalog import PROTECTED_SCHEMA_REFS
    for schema_id in compiled.source.required_schemas:
        if schema_id in PROTECTED_SCHEMA_REFS:
            gateway.bind_builtin_schema(schema_id)
    if "rpnh/module_declaration/v1" not in gateway.schema_refs:
        gateway.bind_builtin_schema("rpnh/module_declaration/v1")
    bind_operation_registration(registration)

    def publish_object(object_type, logical_kind, version_kind, metadata, key):
        ref = VersionRef(object_type, new_id(logical_kind), new_id(version_kind))
        body = metadata(ref)
        core.publish_bytes(object_type=object_type, logical_id=ref.entity_id,
            version_id=ref.version_id, payload=canonical_json(body), metadata=body,
            media_type="application/json", schema_ref=f"registry_v1/{object_type}",
            idempotency_key=key)
        return ref

    principal = publish_object("principal/v1", "principal", "principal_version", lambda ref: {
        "principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id),
        "display_name": owner_name}, f"{command_id}:owner")
    task_round = publish_object("task_round/v1", "task_round", "task_round_version", lambda ref: {
        "task_round_id": str(ref.entity_id), "task_id": str(core.task_id), "round_number": 1,
        "predecessor_task_round_id": None, "task_branch_ref": ref_payload(identity.task_branch_ref)},
        f"{command_id}:round")

    def publish_input(value, key):
        if not isinstance(value, OwnerInput) or not isinstance(value.payload, bytes):
            raise TypeError("owner inputs require explicit schema and bytes")
        from jsonschema import Draft7Validator
        schema = registration.declaration("schema", value.schema_id)["schema"]
        Draft7Validator(schema).validate(json.loads(value.payload))
        return _publish_private_system(core, identity.task_ref, PublishResource(
            origin=PrivateSystemOrigin(bootstrap), payload=value.payload,
            media_type="application/json", content_schema_ref=value.schema_id,
            content_schema_authority_ref=(None if value.schema_id in PROTECTED_SCHEMA_REFS
                                          else gateway.schema_refs[value.schema_id]),
            summary=value.summary, lifetime_ref=bootstrap, idempotency_key=key))

    original = publish_input(task_input, f"{command_id}:original-input")
    inventory = original if inventory_input is None else publish_input(
        inventory_input, f"{command_id}:resource-inventory")
    inputs = {name: tuple(publish_input(value, f"{command_id}:entry:{name}:{index}")
                         for index, value in enumerate(values))
              for name, values in sorted(input_bundles.items())}
    resource_refs = {name: publish_input(value, f"{command_id}:lease:{name}")
                     for name, value in sorted(resource_inputs.items())}
    decision = publish_user_authority_decision(core, authority_kind="scope",
        canonical_statement=owner_statement, user_principal_ref=principal,
        governed_artifact_refs=(identity.task_ref, original.as_version_ref(),
                                *((inventory.as_version_ref(),) if inventory != original else ()),
                                *(r.as_version_ref() for bundle in inputs.values() for r in bundle),
                                *(r.as_version_ref() for r in resource_refs.values())),
        selected_choices={"module": compiled.source.name}, effective_sequence=1,
        supersedes_ref=None, idempotency_key=f"{command_id}:scope")
    operations = publish_module_operations(core, compiled, registration,
        gateway.schema_refs, idempotency_key=f"{command_id}:operations")
    authored = _publish_private_system(core, identity.task_ref, PublishResource(
        origin=PrivateSystemOrigin(bootstrap), payload=canonical_json(module.to_dict()),
        media_type="application/json", content_schema_ref="rpnh/module_declaration/v1",
        summary=f"Authored Module {module.name}", lifetime_ref=bootstrap,
        idempotency_key=f"{command_id}:authored-module"))
    def host_factory(plan):
        return host_execution_bindings(core=core, plan=plan, source_ref=authored,
            bootstrap_ref=bootstrap, compiled=compiled)
    publication = publish_module_net(core, compiled, registration, identity=identity,
        bootstrap_ref=bootstrap, principal_ref=principal, task_round_ref=task_round,
        authority_decision_ref=decision, entry_inputs=inputs,
        schema_refs=gateway.schema_refs, operation_refs=operations,
        owner_resource_inputs=resource_refs,
        host_execution_bindings={} if host_execution_bindings is None else host_factory,
        idempotency_key=f"{command_id}:graph")
    publish_module_initial_marking(core, publication, entry_inputs=inputs,
        idempotency_key=f"{command_id}:initial-marking")
    core.task_control.adopt_net(net_instance_ref=publication.net_ref,
        idempotency_key=f"{command_id}:initial-adopt")
    manifest = publish_module_budgets(core, publication, inventory_ref=inventory,
        inventory_schema_id=(task_input.schema_id if inventory_input is None
            else inventory_input.schema_id), declaration=budgets)
    owner = RunOwner(core, registration, identity, publication, original,
                     principal, task_round, bootstrap, gateway, budgets)
    owner.host_execution_bindings = host_execution_bindings
    owner.authority_decision_ref = decision
    # An explicitly trusted HOST may publish its optional application's
    # configuration sources through this same owner before any admission.
    # JSON supplies data/registered keys, never a callable or import locator.
    immutable_config, mutable_config = original, publication.declaration_resource_ref
    if configuration_sources is not None:
        if not callable(configuration_sources):
            raise TypeError("configuration source publisher is an explicit trusted HOST callable")
        selected = configuration_sources(owner)
        if (not isinstance(selected, tuple) or len(selected) != 2
                or any(not isinstance(ref, type(original)) for ref in selected)):
            raise TypeError("HOST configuration publisher must return two exact resource refs")
        immutable_config, mutable_config = selected
    publish_module_configuration(core, publication, identity=identity,
        immutable_input_ref=immutable_config, mutable_stage_ref=mutable_config,
        recovery_manifest_ref=manifest, model_condition=model_condition,
        idempotency_key=f"{command_id}:configuration")
    return owner


def resume_run(
        registration: Registration, *, run_dir: str | Path,
        model_condition: str, catalog=None,
        host_execution_bindings=None,
        checkpoint_version_id: str | None = None,
        reopen_command_id: str | None = None,
        reopen_reason: str | None = None,
) -> RunOwner:
    """Reopen one clean owner-stop checkpoint as the sole new writer."""
    if (not isinstance(registration, Registration)
            or not isinstance(model_condition, str) or not model_condition
            or ((checkpoint_version_id is None)
                != (reopen_command_id is None))
            or ((checkpoint_version_id is None)
                != (reopen_reason is None))):
        raise TypeError("resume_run requires registration and exact model")
    from .registry.run_authority import current_run_execution_authority
    destination = Path(run_dir)
    preflight = _RegistryCore(
        destination, create=False, read_only=True, catalog=catalog)
    _preflight_authority_ref, preflight_current = current_run_execution_authority(
        preflight, _ResourceServiceKernel(preflight),
        expected_model_condition=model_condition)
    registration.bind_schema_catalog(preflight.catalog)
    material = _load_resume_material(preflight, registration)
    # Recovery classification may need the trusted executor contract before a
    # writer or Registry gateway exists.  A fresh worker process has no prior
    # process-global binding, so establish it immediately after the complete
    # persisted registration inventory has passed read-only validation.
    bind_operation_registration(registration)
    recovery = None
    checkpoint_recoveries = []
    selected_checkpoint = None
    if checkpoint_version_id is not None:
        from .registry.checkpoint_reentry import resolve_committed_checkpoint
        selected_checkpoint = resolve_committed_checkpoint(
            preflight, checkpoint_version_id)
        if preflight_current["status"] not in {
                "terminal", "stopped_by_owner", "running"}:
            from .registry.errors import ResourceIntegrityFault
            raise ResourceIntegrityFault(
                "checkpoint reentry requires terminal, owner-stopped, or its recoverable running generation")
        if preflight_current["status"] == "running":
            from .registry.module_execution import active_module_firings
            preflight_executable, _structure, _marking = (
                hydrate_module_runtime(preflight))
            active = active_module_firings(
                preflight, preflight_executable.net_ref,
                require_current_writer=False)
            if active:
                from .registry.firing_recovery import (
                    classify_registered_operation_interruption,
                    classify_registered_operation_recovery,
                )
                preflight_kernel, preflight_repository, historical_input = (
                    _operation_services(preflight))
                completion_events = (
                    preflight.event_store.list_events_by_type((
                        "registered_operation_completion_recorded/v1",)))
                for firing in active:
                    completions = tuple(
                        event for event in completion_events
                        if event.payload.get("transition_firing_ref")
                        == ref_payload(firing.transition_firing_ref))
                    if len(completions) == 1:
                        item = classify_registered_operation_recovery(
                            preflight, preflight_kernel,
                            preflight_repository,
                            current_run=preflight_current,
                            historical_input=historical_input,
                            expected_firing=firing,
                            checkpoint_reentry=True)
                        checkpoint_recoveries.append(("completion", item))
                    elif not completions:
                        item = classify_registered_operation_interruption(
                            preflight, preflight_kernel,
                            preflight_repository,
                            current_run=preflight_current,
                            historical_input=historical_input,
                            expected_firing=firing,
                            checkpoint_reentry=True)
                        checkpoint_recoveries.append(("interruption", item))
                    else:
                        from .registry.errors import ResourceIntegrityFault
                        raise ResourceIntegrityFault(
                            "active firing has ambiguous operation completions")
    elif preflight_current["status"] == "running":
        from .registry.firing_recovery import (
            classify_registered_operation_recovery,
        )
        preflight_kernel, preflight_repository, historical_input = (
            _operation_services(preflight))
        recovery = classify_registered_operation_recovery(
            preflight, preflight_kernel, preflight_repository,
            current_run=preflight_current,
            historical_input=historical_input)
    elif preflight_current["status"] == "stopped_by_owner":
        from .registry.module_execution import active_module_firings
        preflight_executable, _structure, _marking = hydrate_module_runtime(
            preflight)
        if active_module_firings(preflight, preflight_executable.net_ref):
            from .registry.errors import ResourceIntegrityFault
            raise ResourceIntegrityFault(
                "run resume requires a drained interruption checkpoint")
    else:
        from .registry.errors import ResourceIntegrityFault
        raise ResourceIntegrityFault(
            "run resume requires stopped_by_owner or one recoverable completion")
    core = _RegistryCore(destination, create=False, catalog=catalog)
    kernel = _ResourceServiceKernel(core)
    current_run_execution_authority(
        core, kernel, expected_model_condition=model_condition)
    executable, structure, _marking = hydrate_module_runtime(core)
    if (executable.net_ref != material.executable.net_ref
            or executable.team_design_root_ref
            != material.executable.team_design_root_ref
            or executable.declaration_resource_ref
            != material.executable.declaration_resource_ref
            or structure.compiled != material.structure.compiled):
        raise ValueError("resume run changed after read-only preflight")

    registration.bind_schema_catalog(core.catalog)
    gateway = RegistryRegistrationGateway(
        core, material.identity.task_ref, material.bootstrap_ref)
    registration.bind_gateway(gateway)
    for schema_id in structure.compiled.source.required_schemas:
        if (schema_id in PROTECTED_SCHEMA_REFS
                and schema_id not in gateway.schema_refs):
            gateway.bind_builtin_schema(schema_id)
    owner = RunOwner(
        core, registration, material.identity, None,
        material.original_input_ref, material.principal_ref,
        material.task_round_ref, material.bootstrap_ref, gateway,
        material.budgets)
    owner.host_execution_bindings = host_execution_bindings
    if checkpoint_recoveries:
        for ordinal, (kind, item) in enumerate(checkpoint_recoveries):
            firing_ref = (
                item.outputs.execution.operation.firing.transition_firing_ref
                if kind == "completion" else
                item.execution.operation.firing.transition_firing_ref)
            command_id = (
                "rpnh:reopen:registered-operation-"
                f"{kind}:{ordinal}:{firing_ref.version_id}:"
                f"writer-{core.writer_epoch}")
            if kind == "completion":
                owner.recover_completed_firing(
                    item, command_id=command_id)
            else:
                owner.recover_interrupted_firing(
                    item, command_id=command_id)
    elif recovery is not None:
        owner.recover_completed_firing(
            recovery,
            command_id=(
                "rpnh:resume:registered-operation-completion:"
                f"writer-{core.writer_epoch}"),
        )
    if selected_checkpoint is not None:
        from .registry.module_execution import active_module_firings
        selected_executable, _selected_structure, _selected_marking = (
            hydrate_module_runtime(core))
        _selected_authority_ref, selected_authority = (
            current_run_execution_authority(core, kernel))
        active_selected = active_module_firings(
            core, selected_executable.net_ref,
            require_current_writer=False)
        if (selected_authority["status"] == "running"
                and not active_selected):
            owner.record_owner_stop(
                idempotency_key=(
                    "rpnh:reopen:drained-owner-stop:"
                    f"writer-{core.writer_epoch}"))
        from .registry.checkpoint_reentry import (
            materialize_reentry_workspaces,
            stage_checkpoint_reentry,
        )
        reopened_checkpoint = stage_checkpoint_reentry(
            core, kernel, source_checkpoint_ref=selected_checkpoint,
            command_id=reopen_command_id, reason=reopen_reason)
        _reopen_authority_ref, reopen_authority = (
            current_run_execution_authority(core, kernel))
        reopened_executable, _reopened_structure, _reopened_marking = (
            hydrate_module_runtime(core))
        active_reopened = active_module_firings(
            core, reopened_executable.net_ref,
            require_current_writer=False)
        if (reopen_authority["latest_checkpoint_ref"]
                == ref_payload(reopened_checkpoint)
                and (reopen_authority["status"] == "stopped_by_owner"
                     or (reopen_authority["status"] == "running"
                         and not active_reopened))):
            materialize_reentry_workspaces(
                core, kernel, reopened_checkpoint)
        executable, structure, _marking = hydrate_module_runtime(core)

    _current_authority_ref, current_authority = (
        current_run_execution_authority(core, kernel))
    if current_authority["status"] == "terminal":
        from .registry.errors import ResourceIntegrityFault
        raise ResourceIntegrityFault(
            "checkpoint reopen command already reached terminal authority")
    if current_authority["status"] == "stopped_by_owner":
        from .registry.run_authority import resume_owner_stopped_run
        resume_owner_stopped_run(
            core, kernel,
            immutable_input_ref=material.original_input_ref,
            mutable_stage_ref=executable.declaration_resource_ref,
            recovery_manifest_ref=material.recovery_manifest_ref,
            idempotency_key=f"rpnh:resume:writer-{core.writer_epoch}",
        )
    elif recovery is not None and selected_checkpoint is None:
        recovered_executable, _structure, _marking = hydrate_module_runtime(core)
        from .registry.run_authority import record_recovered_run_entry
        record_recovered_run_entry(
            core, kernel,
            immutable_input_ref=material.original_input_ref,
            mutable_stage_ref=recovered_executable.declaration_resource_ref,
            recovery_manifest_ref=material.recovery_manifest_ref,
        )
    elif (current_authority["status"] == "running"
          and current_authority.get("reopen_authorization_ref") is not None):
        from .registry.run_authority import record_recovered_run_entry
        record_recovered_run_entry(
            core, kernel,
            immutable_input_ref=material.original_input_ref,
            mutable_stage_ref=executable.declaration_resource_ref,
            recovery_manifest_ref=material.recovery_manifest_ref,
        )
    else:
        from .registry.errors import ResourceIntegrityFault
        raise ResourceIntegrityFault(
            "run resume has no recoverable current execution state")
    owner.current_input_ref = owner.control.current_input()
    return owner


def snapshot(owner_or_client):
    """Same snapshot API for the execution owner or its channel-only client."""
    return owner_or_client.snapshot()


__all__ = ("OwnerInput", "RunOwner", "start_run", "resume_run", "snapshot")
