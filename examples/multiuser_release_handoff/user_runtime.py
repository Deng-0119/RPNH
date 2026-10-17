"""Native independent-user HOST, adapted from the retained long_team helper.

Create one private AgentTaskSpec per user, passing the parent's existing
plugin configuration, managed bindings/policy and execution selection unchanged.
``create_user`` prepares a RunOwner; the parent owns execution and recovery.
For a task awaiting knowledge, pass ``await_shared=True``, then open a wait and
deliver one explicitly granted TEXT resource before admitting its operation.
Feedback is an explicit recipient-owner action, delivered at the next native
request boundary. None of these helpers calls a model or a business tool.

These are same-process trusted HOST objects, never model-supplied credentials.
Checkpoint recovery uses resume_run with the returned registration/catalog and
host bindings; this module does not acquire another writer or migrate old runs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path

from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation
from cpn.llm_adapters import load_llm_execution_selection
from cpn.plugins.catalog import load_catalog
from cpn.plugins.managed_tools import ManagedPluginToolCatalog
from cpn.rpnh.agent_tasks import (AgentTaskSpec, TEXT_SCHEMA, _execution_route,
    agent_task_catalog, agent_task_registration, build_agent_task_module)
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.registry_read_contracts import (ExplicitSources, ReadLimits,
    ReadSessionRequest, SourceSelection)
from cpn.rpnh.collaboration.registry_read_session import (ExistingReadAuthorityProvider,
    RegistryReadHostBinding, open_readonly_source, open_registry_session)
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import PortDeclaration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.observer_access import ObserverReadScope
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, RunOwner, start_run
from cpn.rpnh.run_inputs import InputWaitSpec, RunInputHost


_COMPONENT = "long_team/independent_user_input/v1"
_PURPOSE = "independent-user-resource"


def _lower_shared(config, context):
    """Existing operation lowerer, with one native private required input."""
    port = PortDeclaration("knowledge", "input", TEXT_SCHEMA)
    fragment = lower_operation(config, replace(context, ports=(*context.ports, port)))
    return replace(fragment,
        ports=tuple(p for p in fragment.ports if p.name != "knowledge"),
        internal_ports=(port,),
        internal_bindings=tuple(p for p in fragment.ports if p.name == "knowledge"))


@dataclass(frozen=True)
class IndependentUser:
    owner: RunOwner
    selection: object
    source_id: str
    transition: str
    awaits_shared: bool
    host: UserCollaborationHost
    session: object

    def session_for(self, visitor):
        """Native local representation of the visitor's original principal."""
        return self.host.create_session(origin_core=visitor.owner._core,
            principal_ref=visitor.owner.principal_ref,
            command_id="independent-session:" + str(visitor.owner.principal_ref.version_id))


def create_user(spec: AgentTaskSpec, *, workspace_root, source_id, owner_name,
                await_shared=False, factories=None) -> IndependentUser:
    """Prepare a fresh independent owner; return its unchanged execution selection.

    The run directory must be absent and inside the parent's writable workspace.
    Single-stage or private workflow declarations; budgets, domain bridge and managed policies
    come from spec. The parent must serialize shared-world execution itself.
    """
    if (not isinstance(spec, AgentTaskSpec)
            or (not spec.stages and spec.workflow_graph is None) or spec.execution_profiles
            or spec.registered_execution_sources):
        raise ValueError("requires native single-stage or private graph spec with default execution selection")
    root, destination = Path(workspace_root).resolve(), spec.run_dir.resolve()
    if not destination.is_relative_to(root) or destination == root:
        raise ValueError("run directory must be inside the writable workspace")
    if spec.run_dir.is_symlink() or destination.exists():
        raise ValueError("independent user requires an absent run directory")
    selection = load_llm_execution_selection(spec.execution_config_path)
    plugins = load_catalog(spec.plugin_configuration, factories=factories)
    if spec.plugin_catalog_digest is not None and plugins.digest != spec.plugin_catalog_digest:
        raise ValueError("plugin catalog differs from parent's pinned bridge")
    managed = {node: ManagedPluginToolCatalog(plugins, binding["tools"],
        admitted_effects=binding["admitted_effects"])
        for node, binding in spec.managed_bindings.items()}
    registration = agent_task_registration(plugins, managed.values())
    if spec.workflow_graph is None:
        module = build_agent_task_module(spec.stages,
            max_attempts_per_stage=spec.max_attempts_per_stage,
            managed_tools={n: tuple(t.registration_key for t in c.tools) for n,c in managed.items()},
            managed_tool_names={n: tuple(t.name for t in c.tools) for n,c in managed.items()})
    else:
        from cpn.rpnh.agent_tasks import build_agent_workflow_module, EXECUTOR_KEY, TERMINAL_KEY
        from cpn.components.agent_loop.optional_execution import OPTIONAL_TOOL_BINDINGS, optional_agent_loop_schema_data
        schemas,_=optional_agent_loop_schema_data()
        module=build_agent_workflow_module(spec.workflow_graph,executor_key=EXECUTOR_KEY,
            terminal_key=TERMINAL_KEY,tools=tuple(n for n in OPTIONAL_TOOL_BINDINGS
                if n not in {'read_managed_output','read_tool_program_output','run_tool_program'}),
            required_schemas=(CONFIG_SCHEMA_ID,*schemas),max_attempts_per_node=spec.max_attempts_per_stage,
            plugin_catalog=plugins,
            managed_tools={n:tuple(t.registration_key for t in c.tools) for n,c in managed.items()},
            managed_tool_names={n:tuple(t.name for t in c.tools) for n,c in managed.items()})
    # A business user must not gain the generic host shell merely by selecting
    # a single-agent task. Narrow the actual native operation declaration.
    document = module.to_dict()
    for component in document["components"]:
        for operation in component["operations"]:
            allowed = {"read_file", "search_text", "write_file", "complete_interaction",
                       *(t.registration_key for c in managed.values() for t in c.tools)}
            operation["tools"] = [tool for tool in operation["tools"] if tool in allowed]
            if managed:
                operation["tools"] = sorted([*operation["tools"], "read_managed_output"])
    module = ModuleDeclaration.from_dict(document)
    if await_shared:
        if spec.workflow_graph is not None: raise ValueError("Shared-input helper is single-stage only")
        registration.register_component(_COMPONENT, _lower_shared,
            identity={"implementation_id": "long_team.independent_user_input", "revision": "v1"},
            contracts={"config_schema": CONFIG_SCHEMA_ID})
        document = module.to_dict()
        document["components"][0]["key"] = _COMPONENT
        document["components"][0]["operations"][0]["inputs"].append("knowledge")
        module = ModuleDeclaration.from_dict(document)
    catalog = agent_task_catalog(plugins)
    schemas, types, paths = user_collaboration_schema_data()
    for key, schema in schemas.items():
        catalog.register_schema(key, schema, source_path=paths.get(key))
    for definition in types:
        catalog.register_type(definition)
    bindings = make_optional_agent_host_bindings(selection.input_target,
        workspace_policy=selection.runtime_policy.workspace,
        provider_backend_config=_execution_route(selection),
        transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
                            "response_adapter_ref": "llm_response_envelope/v1"},
        managed_catalogs=managed, managed_tool_policy=spec.managed_tool_policy,
        tool_program_policy=spec.tool_program_policy)
    bucket_limits=[b["max_attempts"] for b in module.to_dict()["budget_buckets"]]
    cap=None if any(v is None for v in bucket_limits) else sum(bucket_limits)
    if spec.aggregate_call_cap is not None:
        cap = spec.aggregate_call_cap if cap is None else min(cap, spec.aggregate_call_cap)
    request = OwnerInput(TEXT_SCHEMA, canonical_json(spec.prompt), "Independent user task request")
    owner = start_run(module, registration, run_dir=destination, task_input=request,
        entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), cap, 0, cap, 0),
        model_condition=selection.input_target.model_condition,
        owner_statement=spec.owner_statement, owner_name=owner_name,
        command_id="independent-user:fresh", catalog=catalog, host_execution_bindings=bindings)
    owner.schema_gateway.bind_source_identity(source_id=source_id, command_id="independent-source")
    host = UserCollaborationHost(owner._core, owner.schema_gateway)
    session = host.create_session(origin_core=owner._core, principal_ref=owner.principal_ref,
                                  command_id="independent-owner-session")
    return IndependentUser(owner, selection, source_id,
                           (spec.stages[0].stage_id + ".run" if spec.workflow_graph is None else "team."+spec.workflow_graph.ingress.node_id),
                           await_shared, host, session)


def user_host_configuration(spec, *, factories=None):
    """Rebuild only the original HOST inventory and execution bindings."""
    selection = load_llm_execution_selection(spec.execution_config_path)
    plugins = load_catalog(spec.plugin_configuration, factories=factories)
    if spec.plugin_catalog_digest is not None and plugins.digest != spec.plugin_catalog_digest:
        raise ValueError("plugin catalog differs from original bridge")
    managed = {node: ManagedPluginToolCatalog(plugins, binding["tools"],
        admitted_effects=binding["admitted_effects"])
        for node, binding in spec.managed_bindings.items()}
    registration = agent_task_registration(plugins, managed.values())
    catalog = agent_task_catalog(plugins)
    schemas, types, paths = user_collaboration_schema_data()
    for key, schema in schemas.items():
        catalog.register_schema(key, schema, source_path=paths.get(key))
    for definition in types:
        catalog.register_type(definition)
    bindings = make_optional_agent_host_bindings(selection.input_target,
        workspace_policy=selection.runtime_policy.workspace,
        provider_backend_config=_execution_route(selection),
        transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
                            "response_adapter_ref": "llm_response_envelope/v1"},
        managed_catalogs=managed, managed_tool_policy=spec.managed_tool_policy,
        tool_program_policy=spec.tool_program_policy)
    return selection, registration, catalog, bindings


def resume_user(spec, *, checkpoint_version_id, command_id, reason,
                expected_identity, factories=None):
    """Resume the same private Task; never rebuild its persisted declaration/budgets."""
    from cpn.rpnh.run import resume_run
    from cpn.rpnh.collaboration.sources import get_local_source_identity
    from cpn.rpnh.registry.publication import _ref_payload
    selection, registration, catalog, bindings = user_host_configuration(spec, factories=factories)
    from cpn.rpnh.agent_tasks import _validate_resumed_execution_profiles
    _validate_resumed_execution_profiles(spec.run_dir, {"default": selection},
        catalog=catalog, registration=registration)
    owner = resume_run(registration, run_dir=spec.run_dir,
        model_condition=selection.input_target.model_condition, catalog=catalog,
        host_execution_bindings=bindings, checkpoint_version_id=checkpoint_version_id,
        reopen_command_id=command_id, reopen_reason=reason)
    identity = get_local_source_identity(owner._core)
    if (_ref_payload(owner.identity.task_ref) != expected_identity['task_ref']
            or _ref_payload(owner.principal_ref) != expected_identity['principal_ref']
            or identity.source_id != expected_identity['source_id']):
        raise ValueError("recovered user differs from original native identity")
    host = UserCollaborationHost(owner._core, owner.schema_gateway)
    session = host.create_session(origin_core=owner._core, principal_ref=owner.principal_ref,
                                 command_id=command_id + ':session')
    transition = (spec.stages[0].stage_id + '.run' if spec.workflow_graph is None
                  else 'team.' + spec.workflow_graph.ingress.node_id)
    return IndependentUser(owner, selection, identity.source_id, transition, False, host, session)


def grant_resource(sender, recipient, resource_ref, *, expires_at, command_id):
    """Sender-owner grants only the named immutable material version."""
    session = sender.session_for(recipient)
    return sender.host.issue_read_access(sender.session,
        principal_ref=sender.host.session_principal(session),
        scope=ObserverReadScope({}, {},
            material_refs=(SourceQualifiedResourceRef(sender.source_id, resource_ref),)),
        purpose=_PURPOSE, expires_at=expires_at, command_id=command_id)


def read_resource(sender, recipient, resource_ref, *, observer, limits=None):
    """Read through the native finite read session; enforce recipient identity."""
    session = sender.session_for(recipient)
    if observer.observer_principal_ref != sender.host.session_principal(session):
        raise ValueError("read grant belongs to another recipient")
    limits = ReadLimits() if limits is None else limits
    core = sender.owner._core
    selection = SourceSelection(SourceQualifiedVersionRef(sender.source_id,
        sender.owner.identity.task_ref), "local")

    def resolve(source_id, access_path):
        if (source_id, access_path) != (sender.source_id, "local"):
            raise ValueError("source is outside this exact grant")
        return open_readonly_source(core.run_dir, catalog=core.catalog)

    host = RegistryReadHostBinding(session.actor, resolve,
        ExistingReadAuthorityProvider({(session.actor, sender.source_id, "local"): observer}),
        TypedReaderCatalog(), limits=limits)
    with open_registry_session(ReadSessionRequest(ExplicitSources((selection,)),
            _PURPOSE, limits=limits), host=host) as reader:
        return reader.read_material(resource_ref=SourceQualifiedResourceRef(sender.source_id,
            resource_ref), at_cut=reader.capture_cut(sender.source_id), representation="bytes")


def open_shared_wait(recipient, sender, *, command_id):
    """Recipient-owner admits exactly this sender at its current live anchor."""
    if not recipient.awaits_shared:
        raise ValueError("task was not declared with a private knowledge input")
    session = recipient.session_for(sender)
    stage = recipient.transition.rsplit(".", 1)[0]
    anchor, = (t for t in recipient.owner.snapshot()["marking"] if t["place"] == stage + ".request")
    ref = _version_from_payload(anchor["resource_ref"])
    spec = InputWaitSpec(transition_id=recipient.transition, input_port=stage + ".knowledge",
        anchor_token_ref=_version_from_payload(anchor["token_ref"]),
        binding_ref=ResourceVersionRef(ref.entity_id, ref.version_id),
        allowed_principal_refs=(recipient.host.session_principal(session),))
    return RunInputHost(recipient.owner).open_wait(recipient.session, spec=spec, command_id=command_id)


def _shared_text(sender, recipient, resource_ref, observer):
    material = read_resource(sender, recipient, resource_ref, observer=observer)
    if material["content_schema_ref"] != TEXT_SCHEMA:
        raise ValueError("single-agent sharing requires an exact native TEXT resource")
    provenance = {"source": SourceQualifiedResourceRef(sender.source_id, resource_ref).to_dict(),
                  "read_grant": str(observer.grant_ref.version_id)}
    return material["bytes"], provenance


def deliver_shared(sender, recipient, resource_ref, *, observer, wait_ref,
                   expected_checkpoint_ref, command_id):
    """Consume a granted exact TEXT version and submit it with native provenance.

    Checkpoint compare-and-swap and sender allowlist remain RunInputHost gates.
    Source bytes are unchanged; the immutable input summary names the source
    version/grant. Native input lineage binds to the recipient's live anchor.
    """
    payload, provenance = _shared_text(sender, recipient, resource_ref, observer)
    host = RunInputHost(recipient.owner)
    wait = host.inspect(wait_ref)
    return host.submit(recipient.session_for(sender), wait_ref=wait_ref,
        expected_checkpoint_ref=expected_checkpoint_ref, binding_ref=wait["spec"].binding_ref,
        declaration=OwnerInput(TEXT_SCHEMA, payload, canonical_json(provenance).decode()),
        command_id=command_id)


def queue_feedback(sender, recipient, resource_ref, *, observer, command_id):
    """Recipient owner relays granted TEXT; QUEUED is not a delivery receipt.

    Native request_boundary records DELIVERED plus observed-read evidence when
    the parent runs the recipient. The envelope preserves foreign provenance
    without attributing the owner command to a foreign principal.
    """
    payload, provenance = _shared_text(sender, recipient, resource_ref, observer)
    return recipient.owner.command("message", {"target": recipient.transition,
        "body": canonical_json({**provenance, "text": json.loads(payload)}).decode()},
        command_id=command_id)


def run_user(user, spec, *, interruption_requested=lambda: False, factories=None, on_prepare=None):
    """Execute this user's prepared owner through stock services, serially.

    The caller owns world lifetime, process/resource limits and model authority.
    Native grants/messages may be queued before this call. No new owner is made.
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from types import MappingProxyType
    from cpn.components.execution_services import ExecutionServices
    from cpn.llm_adapters import build_llm_input_port
    from cpn.orchestrator.runner import Orchestrator
    from cpn.plugins.managed_tools import ManagedPluginInvocationService
    from cpn.plugins.managed_scheduler import ManagedRunCapacity
    from cpn.components.agent_loop.managed_execution import managed_scheduler_policy_from_document
    from cpn.rpnh.control_server import OwnerEventLoop
    from cpn.rpnh.agent_tasks import _terminal_output

    if user.owner._core.run_dir.resolve() != spec.run_dir.resolve():
        raise ValueError("execution spec belongs to another owner")
    catalog = load_catalog(spec.plugin_configuration, factories=factories)
    if spec.plugin_catalog_digest is not None and catalog.digest != spec.plugin_catalog_digest:
        raise ValueError("execution plugin differs from prepared native owner")
    managed = {node: ManagedPluginToolCatalog(catalog, binding["tools"],
        admitted_effects=binding["admitted_effects"])
        for node, binding in spec.managed_bindings.items()}
    class BoundManagedService(ManagedPluginInvocationService):
        def prepare(self, name, *, execution, call_id, arguments, **kwargs):
            prepared = super().prepare(name, execution=execution, call_id=call_id,
                arguments=arguments, **kwargs)
            if on_prepare is not None:
                from cpn.plugins.managed_tools import PreparedManagedInvocation
                if isinstance(prepared, PreparedManagedInvocation):
                    on_prepare(user, execution, call_id, name, arguments, prepared)
            return prepared

    event_loop = OwnerEventLoop(user.owner, spec.run_dir / "owner.sock")
    port = capacity = None
    try:
        port = build_llm_input_port(user.selection, destination_run_root=spec.run_dir)
        services = ExecutionServices(owner=user.owner, event_loop=event_loop,
            llm_input_port=port, interruption_requested=interruption_requested)
        optional = services._optional_agent_service
        if managed:
            optional.managed_plugin_services = MappingProxyType({node:
                BoundManagedService(user.owner, services._kernel,
                    services._repository, tools) for node, tools in managed.items()})
            optional.managed_plugin_interruption_requested = interruption_requested
        if spec.managed_tool_policy is not None:
            policy = managed_scheduler_policy_from_document(spec.managed_tool_policy)
            capacity = ManagedRunCapacity(policy.max_in_flight)
            optional.managed_scheduler_policy = policy
            optional.managed_run_capacity = capacity
        with ThreadPoolExecutor(max_workers=1) as workers:
            runner = Orchestrator(owner=user.owner, event_loop=event_loop,
                prepare_dispatcher=services.prepare_dispatcher,
                submit_operation=workers.submit, max_in_flight=1)
            finished = threading.Event()
            def watch_stop():
                while not finished.is_set():
                    if interruption_requested():
                        runner.request_owner_stop()
                        return
                    finished.wait(0.05)
            watcher = threading.Thread(target=watch_stop, daemon=True)
            watcher.start()
            try:
                if interruption_requested():
                    runner.request_owner_stop()
                result = runner.run()
            finally:
                finished.set()
                watcher.join(timeout=1)
        reference, output = _terminal_output(user.owner, result.terminal_evidence_ref)
        return {"stop_reason": result.stop_reason, "output": output,
                "terminal_result_ref": reference,
                "task_ref": str(user.owner.identity.task_ref.version_id),
                "principal_ref": str(user.owner.principal_ref.version_id)}
    finally:
        if port is not None:
            port.close()
        if capacity is not None:
            capacity.close()
        event_loop.close()
