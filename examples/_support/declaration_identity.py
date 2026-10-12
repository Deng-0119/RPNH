"""Export Agent identities from compiled declarations without running a task.

The caller supplies the real compiled Module and its existing initial frame.
Only the executor's transport contract and scoped semantic ID are disclosed;
prompts, provider configuration, runtime identities and firings are not exported.
"""
from __future__ import annotations

from copy import deepcopy

from cpn.frontend.dashboard import public_net
from cpn.rpnh.inspection import project_compiled_boundaries, project_compiled_net


def compile_example_declaration(example, *, public_task=None):
    """Compile only the four source workflows whose initial exports lack identity.

    Managed tool registrations are unnecessary for this identity projection and
    are not recreated. Their historical inventory stays in source-provenance.
    HarnessAudit requires its separately retained public input, never grader data.
    """
    if example == 'erp_bench':
        from examples.erp_bench.src.rpnh_erp_bench.native import graph_for
        graph = graph_for()
    elif example == 'automationbench':
        from examples.automationbench.src.rpnh_ab.native import graph_for
        from examples.automationbench.src.rpnh_ab.offline_fixture import synthetic_row
        graph, _ = graph_for(synthetic_row()['prompt'])
    elif example == 'slopcodebench':
        from examples.slopcodebench.pilot import graph as graph_for
        graph = graph_for()
    elif example == 'harnessaudit_office':
        import json
        from examples.harnessaudit_office.src.rpnh_ha.task_view import PublicTask, assert_no_hidden_keys
        from examples.harnessaudit_office.src.rpnh_ha.workflow import build_business_workflow
        if public_task is None:
            raise ValueError('HarnessAudit identity export requires the public task input')
        assert_no_hidden_keys(public_task)
        graph, _ = build_business_workflow(PublicTask(json.dumps(public_task)))
    else:
        raise ValueError('unsupported identity export example')
    from cpn.rpnh.agent_tasks import EXECUTOR_KEY, TERMINAL_KEY, agent_task_registration
    from cpn.rpnh.agent_workflows import build_agent_workflow_module
    from cpn.rpnh.compiler import compile_module
    module = build_agent_workflow_module(
        graph, executor_key=EXECUTOR_KEY, terminal_key=TERMINAL_KEY,
        tools=(), required_schemas=(), max_attempts_per_node=None)
    return compile_module(module, agent_task_registration())


def export_declaration_identity(frame, compiled):
    """Enrich an initial frame only when its complete public net still matches.

    Checking nodes, ports, arcs and boundary rules prevents pairing new identities
    with an unrelated old graph. The original frame is never mutated.
    """
    if (frame.get('source', {}).get('mode') != 'initial_configured'
            or frame.get('net', {}).get('source', {}).get('mode') != 'initial_configured'
            or frame.get('transition_bindings')
            or any(n.get('runtime') for n in frame['net']['nodes'])):
        raise ValueError('identity export requires a declaration-only frame')
    raw = project_compiled_net(compiled, source=frame['net']['source'])
    if (public_net(raw) != public_net(frame['net'])
            or project_compiled_boundaries(compiled) != frame['boundaries']):
        raise ValueError('compiled declaration differs from the initial frame')
    result = deepcopy(frame)
    nodes = {node['id']: node for node in result['net']['nodes']}
    agents = []
    for node in raw['nodes']:
        if node['kind'] != 'transition':
            continue
        transport = node['executor_declaration']['contracts'].get('transport')
        # A narrow source-derived disclosure also supports the legacy net view.
        nodes[node['id']]['executor_declaration'] = {'contracts': {'transport': transport}}
        semantic = node['config'].get('semantic_node_id')
        nodes[node['id']]['config'] = (
            {'semantic_node_id': semantic} if isinstance(semantic, str) and semantic else {})
        if transport == 'llm':
            agents.append({
                'transition_id': node['id'], 'source': 'declared_llm_executor',
                'semantic_group': ({'component': node['operation'].rsplit('.', 1)[0],
                                    'semantic_node_id': semantic}
                                   if isinstance(semantic, str) and semantic else None),
                'agent_ref': None, 'executable_binding_ref': None,
            })
    result['agent_nodes'] = agents
    result['coverage']['agent_nodes'] = 'declared_llm_executor_only'
    return result
