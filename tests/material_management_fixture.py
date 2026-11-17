"""Native offline material-admission fixture, independent of pytest.

This fixture publishes a real Module, deterministic operation contract, and
agent/v1 through the original HOST binding port.  Tests drive the original
Owner lifecycle explicitly; it does not stand in for a material executor or
claim to test workspace/product publication for the Viewer.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation, register_basic_components
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import InitialTokenDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_host_bindings import HostExecutionBinding
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run

TEXT = "application/rpnh_agent_text/v1"
EXECUTOR = "test/viewer-material-management/v1"
TERMINAL = "test/viewer-material-terminal/v1"
COMMAND_PROTOCOL = "rpnh/material_command/v1"
TRANSITION = "step.run"


def _no_dispatch(**_kwargs):
    raise AssertionError("admission fixture cannot dispatch a model, tool, or material executor")


def management_host_bindings(*, core, plan, source_ref, bootstrap_ref, compiled):
    """Publish real static agent identities with no model target or AgentLoop."""
    del bootstrap_ref
    result = {}
    operations = {item.declaration.name: item for item in compiled.operations}
    for transition in compiled.symbolic.transitions:
        operation = operations[transition.operation]
        ref = VersionRef("agent/v1", new_id("agent"), new_id("agent_version"))
        metadata = {
            "agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id),
            "agent_ref": ref_payload(ref),
            "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
            "declared_agent_id": operation.declaration.name,
            "shadow_transition_id": transition.name,
        }
        core.catalog.validate_instance("agent/v1", category="object", instance=metadata)
        core.publish_bytes(object_type="agent/v1", logical_id=ref.entity_id,
            version_id=ref.version_id, payload=canonical_json(metadata), metadata=metadata,
            media_type="application/json", schema_ref="registry_v1/agent/v1",
            idempotency_key=f"fixture-agent:{plan.net_ref.version_id}:{transition.name}")
        result[transition.name] = HostExecutionBinding(agent_ref=ref)
    return result


def material_owner(path, *, occurrences=1, ordinary_read=True, management=True,
                   command_id="test:material-owner", executor=None,
                   configure_registration=None, operation_tools=(), host_bindings=None,
                   operation_config=None, executor_contracts=None, required_schemas=()):
    """Create a genuine original Registry and PN with one management operation."""
    if not isinstance(occurrences, int) or occurrences < 1:
        raise ValueError("fixture requires a positive number of native input tokens")
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, {
        "$id": TEXT, "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "string",
    })
    contracts = {"transport": "deterministic", "input_ports": None,
                 "output_ports": None, "config_schema": CONFIG_SCHEMA_ID}
    if management:
        contracts["owner_material_commands"] = COMMAND_PROTOCOL
    if executor_contracts is not None:
        contracts.update(executor_contracts)
    registration.register_executor(EXECUTOR, _no_dispatch if executor is None else executor,
        identity={"implementation_id": "test.viewer_material_admission", "revision": "v1"},
        contracts=contracts)
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "test.viewer_material_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    if configure_registration is not None:
        configure_registration(registration)

    def lower(config, context):
        fragment = lower_operation(config, context)
        if occurrences == 1:
            return fragment
        return replace(fragment, places=tuple(
            replace(place, initial_tokens=(InitialTokenDeclaration(
                count=occurrences - 1, value="work", schema=TEXT),))
            if place.name == "request" else place for place in fragment.places))

    registration.register_component("material_fixture_operation", lower,
        identity={"implementation_id": "test.viewer_material_component", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    budget = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
    document = {
        "schema_version": "rpnh/module_declaration/v1", "name": "MaterialAdmission",
        "components": [{
            "name": "step", "key": "material_fixture_operation",
            "config_schema": CONFIG_SCHEMA_ID,
            "config": {"input_modes": {"request": "read"}} if ordinary_read else {},
            "ports": [
                {"name": "request", "direction": "input", "schema": TEXT},
                {"name": "result", "direction": "output", "schema": TEXT},
            ],
            "operations": [{
                "name": "run", "executor": EXECUTOR, "inputs": ["request"],
                "outputs": ["result"], "request_port": None,
                "tools": list(operation_tools),
                "config": {} if operation_config is None else dict(operation_config),
                "budget_binding": budget,
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
            }],
        }],
        "links": [], "entry": {"request": {"component": "step", "port": "request"}},
        "exit": {"result": {"component": "step", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "step", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": sorted({CONFIG_SCHEMA_ID, TEXT, *required_schemas}), "budgets": {},
        "budget_buckets": [{**budget, "max_attempts": 8}],
    }
    schemas, types, paths = user_collaboration_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    task = OwnerInput(TEXT, canonical_json("task"), "Material admission fixture task")
    return start_run(ModuleDeclaration.from_dict(document), registration,
        run_dir=path, catalog=catalog, task_input=task, entry_inputs={"request": task},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 8, 0, 8, 0),
        model_condition="offline-material-admission", owner_statement="Offline material admission test",
        host_execution_bindings=(management_host_bindings if host_bindings is None else host_bindings),
        command_id=command_id)


@dataclass(frozen=True)
class MaterialWorld:
    owner: object
    host: UserCollaborationHost
    session: object


def material_world(path, **kwargs):
    """Create the original local HOST owner session used to seal requests."""
    owner = material_owner(path, **kwargs)
    owner.schema_gateway.bind_source_identity(source_id="material-fixture",
                                               command_id="fixture:source")
    host = UserCollaborationHost(owner._core, owner.schema_gateway)
    session = host.create_session(origin_core=owner._core, principal_ref=owner.principal_ref,
                                  command_id="fixture:session")
    return MaterialWorld(owner, host, session)


def submit_material(world, key, value="complete material text", *, opaque_name="documents/note.txt"):
    """Prepare a full typed input and submit with the trusted original HOST."""
    owner, host, session = world.owner, world.host, world.session
    params = {"protocol": "rpnh/material_input/v1", "document_type": TEXT, "value": value}
    action = host.material_action(session, owner_command="prepare_material_input",
                                  command_id=key + ":input", params=params)
    prepared = owner.command("prepare_material_input", params, command_id=key + ":input",
                              collaboration_action=action)
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    executable, _structure, _marking = hydrate_module_runtime(owner._core)
    params = {
        "protocol": COMMAND_PROTOCOL, "operation": "create_text", "target": TRANSITION,
        "execution": {"task_ref": ref_payload(owner.identity.task_ref),
                      "task_round_ref": ref_payload(owner.task_round_ref),
                      "net_instance_ref": ref_payload(executable.net_ref)},
        "address": {"scope_ref": ref_payload(owner.identity.task_ref), "opaque_name": opaque_name},
        "expected_binding_ref": None, "base_resource_ref": None,
        "input_ref": prepared["input_ref"],
    }
    action = host.material_action(session, owner_command="material_submit",
                                  command_id=key, params=params)
    accepted = owner.command("material_submit", params, command_id=key,
                             collaboration_action=action)
    return prepared, accepted


def command_ref(accepted):
    return _version_from_payload(accepted["command_ref"])


def direct_claim(owner, owner_command_ref=None, *, token_index=0):
    """Use the original low-level admission with one genuine PN occurrence."""
    from cpn.rpnh.registry.invocations import FiringClaim
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    executable, structure, marking = hydrate_module_runtime(owner._core)
    transition = executable.transitions[0]
    root = owner._core.get_version(executable.team_design_root_ref.version_id).metadata
    task_round = _version_from_payload(root["task_round_ref"])
    round_data = owner._core.get_version(task_round.version_id).metadata
    net = owner._core.get_version(executable.net_ref.version_id).metadata
    inputs = sorted((token for token in marking.tokens if token.state.place == "step.request"),
                    key=lambda token: str(token.token_ref.version_id))
    refs = (inputs[token_index].token_ref,)
    consumed_places = {place for place, target, _weight in structure.token_input_arcs
                       if target == TRANSITION}
    consumed = refs if inputs[token_index].state.place in consumed_places else ()
    return FiringClaim(
        task_ref=_version_from_payload(root["task_ref"]),
        task_branch_ref=_version_from_payload(round_data["task_branch_ref"]),
        task_round_ref=task_round, net_instance_ref=executable.net_ref,
        plan_ref=_version_from_payload(net["plan_ref"]), node_ref=transition.node_ref,
        operation_binding_ref=transition.operation_binding_ref,
        marking_checkpoint_ref=marking.checkpoint_ref, principal_ref=transition.principal_ref,
        logical_tau=0, attempt_index=1, claimed_input_refs=refs, consumed_input_refs=consumed,
        activation_ref=transition.activation_ref, agent_ref=transition.agent_ref,
        owner_command_ref=owner_command_ref)


def finish(owner, admitted, key):
    """Exercise original Start/products/Success only, without a Viewer save."""
    execution = owner.start(admitted, command_id=key + ":start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"step.result": (canonical_json("result"),)}, command_id=key + ":products")
    return owner.succeed(outputs, command_id=key + ":success")

