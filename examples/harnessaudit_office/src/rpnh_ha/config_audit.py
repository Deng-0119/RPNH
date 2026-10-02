"""No-model public configuration audit. Does not invent task policies."""
from __future__ import annotations
from typing import Mapping
from .workflow import public_argument_schema, ARGUMENT_SCHEMA_REVISION


def audit_bindings(public: Mapping, bindings: Mapping, roles_by_node: Mapping) -> dict:
    tools={tool['name']:tool for tool in public['tools']}
    findings=[];nodes=[]
    for node,role in roles_by_node.items():
        actual=bindings.get(node,{}).get('tools',{})
        missing=sorted(set(tools)-set(actual))
        if missing:
            findings.append({'node':node,'layer':'agent_configuration',
                             'code':'tool_not_bound','tools':missing,
                             'root_cause_confirmed':False,
                             'next_check':'distinguish absent user binding from adapter-dropped binding'})
        parameter_count=0;description_count=0
        for name in sorted(set(tools)&set(actual)):
            expected=public_argument_schema(tools[name]);schema=actual[name].get('input_schema',{})
            for parameter,document in expected['properties'].items():
                parameter_count+=1
                delivered=schema.get('properties',{}).get(parameter)
                if isinstance(delivered,Mapping) and delivered.get('description')==document['description']:
                    description_count+=1
                if not isinstance(delivered,Mapping) or any(delivered.get(key)!=value for key,value in document.items()):
                    findings.append({'node':node,'tool':name,'parameter':parameter,
                                     'layer':'adapter_projection','code':'public_parameter_projection_mismatch'})
        nodes.append({'node':node,'business_role':role,'configured_tools':len(actual),
                      'missing_tools':missing,'checked_parameters':parameter_count,
                      'preserved_descriptions':description_count})
    for node in set(bindings)-set(roles_by_node):
        findings.append({'node':node,'layer':'agent_configuration','code':'binding_has_no_role'})
    return {'schema_version':'rpnh-ha/config-audit/v3',
            'argument_schema_revision':ARGUMENT_SCHEMA_REVISION,
            'declared_binding_projection_ok':not findings,'nodes':nodes,'findings':findings,
            'runtime_tool_execution_tested':False,'actual_provider_prompt_tested':False,
            'runtime_model_calls':0,'grader_rules_used':False,
            'scope':'declaration projection checks; not causal attribution of installation vs transformation'}


def check_explicit_dependencies(graph: Mapping, required_edges: list[dict]) -> dict:
    """Only caller-supplied application requirements are checked; no gold paths."""
    actual={(arc['source']['node_id'],arc['target']['node_id']) for arc in graph['arcs']
            if arc.get('kind','dependency')=='dependency'}
    missing=[edge for edge in required_edges if (edge['source'],edge['target']) not in actual]
    return {'scope':'declared_application_dependency_edges',
            'missing_declarations':missing,
            'classification':'application_workflow_configuration' if missing else 'configuration_present',
            'runtime_enforcement_tested':False}


def compare_judge_evidence(old: list[dict], new: list[dict]) -> dict:
    """Compare fields used by pinned completion/AVS judges, not whole trace bytes.

    Reuse also requires identical scorer version, task, judge prompts/config and
    a successful previous judgment. SAR must be recomputed with the rule checker
    because raw communication context is an additional SAR input.
    """
    keys=('sequence_num','surface','agent_role','target_role','tool_name','tool_args','tool_result','content')
    left=[{key:row.get(key) for key in keys} for row in old]
    right=[{key:row.get(key) for key in keys} for row in new]
    return {'schema_version':'rpnh-ha/judge-input-comparison/v3',
            'same_judge_evidence_fields':left==right,
            'previous_judgments_reuse_candidate':left==right,
            'requires_same_task_scorer_prompts_and_judge_condition':True,
            'requires_valid_current_trace':True,'sar_rule_regrade_required':True,
            'model_calls_made':0,
            'note':'No score has been copied or recomputed by this comparison.'}
