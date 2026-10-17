"""Actor-visible business contracts and validation of generated role stages."""
import json

ROLES = ('operations', 'developer', 'qa', 'support')


def actor_contracts(task):
    """Project specified original business inputs, never original judge assets.

    The original developer prompt refers to 'specified content'; those bytes
    and required original tool fields are supplied as task requirements.
    """
    steps = task['sub_task_list']
    return {
        'operations': {'user_prompt':steps[0]['user_prompt'],
            'required_file_fields':steps[0]['ground_truth'][0]['arguments']},
        'developer': {'user_prompt':steps[1]['user_prompt'],
            'required_branch':steps[1]['ground_truth'][0]['arguments'],
            'required_sql':steps[1]['ground_truth'][1]['arguments']},
        'qa': {'user_prompt':steps[2]['user_prompt'],
            'required_checklist':steps[2]['ground_truth'][0]['arguments']},
        'support': {'task':'Prepare support coverage after QA handoff.',
            'required_incident_fields':steps[2]['ground_truth'][2]['arguments']}}


def parse_design(text):
    from cpn.rpnh.agent_tasks import AgentStage
    document = json.loads(text)
    if set(document) != {'name','rationale','roles'} or set(document['roles']) != set(ROLES):
        raise ValueError('design must name the four controlled roles and its rationale')
    for role, stages in document['roles'].items():
        if not isinstance(stages,list) or not 1 <= len(stages) <= 4:
            raise ValueError('each private user needs one to four generated stages')
        seen = set()
        for stage in stages:
            if set(stage) != {'stage_id','instruction'}:
                raise ValueError('stage fields must be stage_id and instruction')
            value = AgentStage(**stage)
            if value.stage_id in seen:
                raise ValueError('private stage ids must be unique')
            seen.add(value.stage_id)
    return document


def designer_prompt(contracts, tool_descriptions):
    return json.dumps({'task':'Design actual private agent-operation PNs for SQL release handoff.',
        'controlled_users':{'operations':'Olivia','developer':'Ethan','qa':'Mia','support':'Ivan'},
        'original_requirements':contracts,
        'output_contract':{'name':'your project name','rationale':'explain stage and coordination choices',
            'roles':{r:[{'stage_id':'your_valid_stage_id','instruction':'complete actionable agent stage instruction'}] for r in ROLES}},
        'architecture':'Each role has its own independent Task/Registry/principal and generated sequential stages. '
            'Transitions are agent operations. Choose 1–4 stages per user. All role Tasks run concurrently; '
            'published Registry resources and legal handoff messages connect them. No all-knowing runtime coordinator.',
        'coordination_contract':[
            'Operations creates the exact Drive shell and writes a meaningful release/rollback note; handoff developer topic note_ready.',
            'Developer discovers project index, reads note, makes exact original branch/SQL commit; handoff qa AND support topic patch_ready with actual commit.',
            'QA and support each discover then read SAME current canonical mutable note. read_note holds a source native PN read claim across their actual reasoning until finish_read. Their reads may overlap.',
            'Operations waits topic readers_active, then proposes a business-related revision: clarify that the query only normalizes returned values, stored rows remain unchanged, and rollback/triage instructions must avoid destroying original NULL evidence. Write the revision yourself; no supplied answer.',
            'Operations update_note may return busy while readers hold it. On busy handoff qa AND support topic revision_requested so they finish their initial checks and release claims. Then retry confirmed no-effect busy only; publish release_revised to qa/support.',
            'QA/support initial read: use body, wait revision_requested (or release_revised if already released), finish_read with findings, then wait release_revised and actually reread new version. Do not label old results as new.',
            'QA writes the exact original checklist template, and separately confirm_release records actual findings/time/SQL commit/current note version. Handoff support topic qa_final.',
            'Support creates original incident fields unchanged; confirm_release also binds actual incident id. Wait qa_final and current release revision before final readiness.',
            'Operations ensures final note names exact SQL commit before final handoff. All agents preserve unknowns and use tool results, not assumptions.',
            'write_file to each stage declared result port then complete_interaction publishes stage result; each later stage must read its located inputs. Use registry discovery/handoff tools for shared business objects, not host filesystem.'],
        'native_tools':tool_descriptions,
        'format':'Output only the JSON design to the declared result output with write_file, then complete_interaction. No business tools in design phase.'},ensure_ascii=False)


def native_stage_graph(stages):
    """Lower the model's declared sequential stages into one private native graph.

    Stage names/instructions and order come from the actual model declaration;
    this mechanical lowering adds typed ports/arcs, not new business decisions.
    """
    from cpn.rpnh.agent_workflows import (AgentWorkflowGraph,AgentWorkflowNode,
        AgentWorkflowPort,AgentWorkflowEndpoint,AgentWorkflowArc)
    nodes=tuple(AgentWorkflowNode(s.stage_id,s.instruction,
        (AgentWorkflowPort('request','task_request' if i==0 else 'stage_result'),),
        (AgentWorkflowPort('result','stage_result'),)) for i,s in enumerate(stages))
    arcs=tuple(AgentWorkflowArc('stage_dependency_'+str(i),
        AgentWorkflowEndpoint(stages[i-1].stage_id,'result'),
        AgentWorkflowEndpoint(stages[i].stage_id,'request')) for i in range(1,len(stages)))
    return AgentWorkflowGraph(nodes,arcs,AgentWorkflowEndpoint(stages[0].stage_id,'request'),
        AgentWorkflowEndpoint(stages[-1].stage_id,'result'))
