"""Offline test writer using only main's Registry/PN APIs, never viewer code.

Creates a fresh integer-loop Registry with three deterministic operations.
No provider, CLI backend, native-plugin package or credentials are used.
"""
from __future__ import annotations
from concurrent.futures import Future
import json
from pathlib import Path
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.harness import Harness, OperationDispatch, OperationProducts
from cpn.rpnh.inspection import project_compiled_net, project_registry_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (ArcDeclaration, PNFragment, PlaceDeclaration,
    PortBinding, PortDeclaration, TransitionDeclaration)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run

VALUE = 'application/viewer_demo_value/v1'
CONFIG = 'application/viewer_demo_config/v1'
COMPONENT = 'rpnh/viewer-demo-loop/v1'
EXECUTOR = 'rpnh/viewer-demo-increment/v1'
TERMINAL = 'rpnh/viewer-demo-terminal/v1'


def lower(_config, context):
    return PNFragment(
        places=(PlaceDeclaration('input', VALUE, capacity=1), PlaceDeclaration('done', VALUE, capacity=1)),
        transitions=(TransitionDeclaration('step', 'step'),),
        arcs=(ArcDeclaration('input', 'step', 'input'),
              ArcDeclaration('input', 'step', 'output', mode='produce', outcome='again'),
              ArcDeclaration('input', 'step', 'output', mode='produce', outcome='interrupted', emit='forward', forward_source='input'),
              ArcDeclaration('done', 'step', 'output', mode='produce', outcome='complete')),
        operations=context.operations,
        ports=(PortBinding('input', 'input'), PortBinding('done', 'done')),
        internal_ports=(PortDeclaration('retry', 'output', VALUE),),
        internal_bindings=(PortBinding('retry', 'input'),))


def declaration():
    budget = {'bucket_id': 'demo', 'budget_scope': 'module', 'finalization_scope': None}
    return ModuleDeclaration.from_dict({
        'schema_version': 'rpnh/module_declaration/v1', 'name': 'ViewerDemo',
        'components': [{'name': 'loop', 'key': COMPONENT, 'config_schema': CONFIG, 'config': {},
            'ports': [{'name': 'input', 'direction': 'input', 'schema': VALUE},
                      {'name': 'done', 'direction': 'output', 'schema': VALUE}],
            'operations': [{'name': 'step', 'executor': EXECUTOR, 'inputs': ['input'], 'outputs': ['retry', 'done'],
                'tools': [], 'config': {}, 'request_port': None, 'budget_binding': budget,
                'outcomes': [{'name': 'again', 'products': [{'port': 'retry'}]},
                             {'name': 'complete', 'products': [{'port': 'done'}]},
                             {'name': 'interrupted', 'products': []}]}]}],
        'links': [], 'entry': {'input': {'component': 'loop', 'port': 'input'}},
        'exit': {'done': {'component': 'loop', 'port': 'done'}},
        'terminal': {'key': TERMINAL, 'source': {'component': 'loop', 'port': 'done'},
                     'operation': 'step', 'outcome': 'complete', 'config': {'run_outcome': 'complete'}},
        'required_schemas': [VALUE, CONFIG], 'budgets': {}, 'budget_buckets': [{**budget, 'max_attempts': 8}]})


def registration():
    reg = Registration()
    reg.register_schema(VALUE, {'$id': VALUE, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'integer', 'minimum': 0})
    reg.register_schema(CONFIG, {'$id': CONFIG, '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object', 'properties': {}, 'additionalProperties': False})
    reg.register_component(COMPONENT, lower, identity={'implementation_id': 'viewer.demo.loop', 'revision': 'v1'}, contracts={'config_schema': CONFIG})
    reg.register_executor(EXECUTOR, dict, identity={'implementation_id': 'viewer.demo.increment', 'revision': 'v1'},
        contracts={'transport': 'deterministic', 'input_ports': None, 'output_ports': None, 'config_schema': CONFIG})
    reg.register_tool(TERMINAL, dict, identity={'implementation_id': 'viewer.demo.terminal', 'revision': 'v1'}, contracts={'binding_protocol': 'rpnh/module_terminal/v1'})
    return reg


def make_run(run_dir: Path):
    """Return initial, before/after execution projections and their catalog."""
    if run_dir.exists():
        raise FileExistsError('demo requires a new run directory')
    module = declaration()
    initial = project_compiled_net(compile_module(module, registration()), source={'mode': 'initial_configured'})
    value = OwnerInput(VALUE, canonical_json(0), 'Local integer-loop demo input')
    owner = start_run(module, registration(), run_dir=run_dir, task_input=value, entry_inputs={'input': value},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']), ('rpnh/module_declaration/v1',), 8, 0, 8, 0),
        model_condition='rpnh-offline/deterministic-v1', owner_statement='Offline viewer acceptance: increment 0 to 3', command_id='viewer:demo')
    before = project_registry_net(run_dir, catalog=owner._core.catalog)
    dispatches = []
    def prepare(*, execution, **_):
        def invoke():
            output = json.loads(execution.operation.inputs[0].artifact.payload) + 1
            outcome, port = ('complete', 'done') if output == 3 else ('again', 'retry')
            dispatches.append(output)
            return OperationProducts(owner.products(execution, outcome_id=outcome,
                products={f'loop.{port}': (canonical_json(output),)}, command_id=f'viewer:{output}:products'))
        return OperationDispatch(execution, invoke)
    def submit(callback):
        future = Future()
        try: future.set_result(callback())
        except BaseException as error: future.set_exception(error)
        return future
    loop = OwnerEventLoop(owner, run_dir.parent / 'viewer-demo.sock')
    try:
        result = Harness(owner=owner, event_loop=loop, prepare_dispatcher=prepare, submit_operation=submit, max_in_flight=1).exact_execute()
        assert result.stop_reason == 'terminal' and dispatches == [1, 2, 3]
    finally:
        loop.close()
    return initial, before, project_registry_net(run_dir, catalog=owner._core.catalog), owner._core.catalog


def make_agent_run(run_dir: Path):
    """Register three actual main Agent steps separated by mechanical steps.

    This fixture adopts the declaration only. It never dispatches a model or a
    mechanical operation. It exercises real Registry classification, not labels
    renamed to look like Agents. Separate make_run tests completed history.
    """
    from copy import deepcopy
    from cpn.rpnh.agent_tasks import (AgentStage, build_agent_task_module,
        agent_task_registration, TEXT_SCHEMA)
    from cpn.components.basic import CONFIG_SCHEMA_ID
    names = ('planner', 'lookup', 'reviewer', 'format', 'writer')
    documents = [build_agent_task_module((AgentStage(name, 'PRIVATE fixture instruction; do not display.'),),
                                        max_attempts_per_stage=2).to_dict() for name in names]
    wire = deepcopy(documents[0])
    wire['name'] = 'AgentOnlyOverviewFixture'
    wire['components'] = [doc['components'][0] for doc in documents]
    wire['budget_buckets'] = [doc['budget_buckets'][0] for doc in documents]
    for bucket in wire['budget_buckets']:
        bucket['budget_scope'] = bucket['bucket_id']
    for component in wire['components']:
        component['operations'][0]['budget_binding']['budget_scope'] = component['name']
    wire['links'] = [{'source': {'component': a, 'port': 'result'},
                      'target': {'component': b, 'port': 'request'}} for a,b in zip(names,names[1:])]
    wire['exit'] = documents[-1]['exit']
    wire['terminal'] = documents[-1]['terminal']
    mechanical = 'rpnh/viewer-mechanical-step/v1'
    for component in wire['components']:
        if component['name'] in {'lookup','format'}:
            op = component['operations'][0]
            op.update(executor=mechanical, tools=[], config={}, request_port=None)
    reg = agent_task_registration()
    reg.register_executor(mechanical, dict,
        identity={'implementation_id': 'viewer.mechanical.declaration.fixture', 'revision': 'v1'},
        contracts={'transport': 'deterministic', 'input_ports': None, 'output_ports': None,
                   'config_schema': CONFIG_SCHEMA_ID})
    module = ModuleDeclaration.from_dict(wire)
    value = OwnerInput(TEXT_SCHEMA, canonical_json('Agent overview fixture'), 'Offline declaration input')
    owner = start_run(module, reg, run_dir=run_dir, task_input=value, entry_inputs={'request':value},
        budgets=ModuleBudgetDeclaration(tuple(wire['budget_buckets']), ('rpnh/module_declaration/v1',), 10, 0, 10, 0),
        model_condition='rpnh-offline/declaration-only', owner_statement='Adopt only; no model dispatch',
        command_id='viewer:agent-overview-fixture')
    return owner._core.catalog
