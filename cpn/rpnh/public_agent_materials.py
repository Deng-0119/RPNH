"""Original plugin-free AgentTask producer under the installed public contract.

Only declared installed assets and explicit public input are read. These
materials never supply native authority, credentials, or a second scheduler.
"""
from dataclasses import dataclass
import importlib.metadata as metadata
from pathlib import Path
from . import public_material_contracts as w
from .public_module_materials import PublicMaterialDraft, read_installed_contract
from .registry.parent_child import ParentChildUnsupported, _target, _json
from .registry.schema_catalog import SchemaCatalog, canonical_json
from .registry.runtime_binding_contracts import freeze_candidate_document

SPEC = 'rpnh/agent_task_spec/v13'
TARGET = 'registry_v1/llm_input_target/v1'
BACKEND = 'component/optional_agent_execution_provenance/v1'
TRANSPORT = {'interaction_protocol_ref':'llm_request_envelope/v1',
             'response_adapter_ref':'llm_response_envelope/v1'}


def public_agent_payload(document, target):
    """Parse/round-trip at final K-derived root without rewriting the request."""
    from .agent_tasks import AgentTaskSpec, build_agent_task_module
    spec = AgentTaskSpec.from_worker_document(document, document_root=Path(target['document_root']))
    if not spec.registered_execution_sources:
        raise ParentChildUnsupported('PUBLIC_AGENT_REQUIRES_REGISTERED_SPEC')
    expected_run = Path(target['document_root']).parent.parent / target['relative_path']
    if spec.run_dir != expected_run.resolve() or spec.owner_socket_path != expected_run.resolve() / 'owner.sock':
        raise ValueError('registered AgentTask target differs from original K-derived target')
    if canonical_json(spec.as_worker_document(document_root=Path(target['document_root']))) != canonical_json(document):
        raise ValueError('registered AgentTask requires canonical original Spec round-trip')
    module = build_agent_task_module(spec.stages, max_attempts_per_stage=spec.max_attempts_per_stage)
    return spec, module


def agent_slot_values(spec, selection, bound):
    from .agent_tasks import TEXT_SCHEMA, _execution_route
    return {
        'execution_policy': (w.POLICY, selection.as_registry_policy()),
        'target': (TARGET, selection.input_target.as_registry_document()),
        'backend': (BACKEND, _execution_route(selection)),
        'transport': (None, TRANSPORT),
        'input': (TEXT_SCHEMA, spec.prompt),
        'budget': (None, {'budgets':bound.to_dict()['budgets'], 'budget_buckets':bound.to_dict()['budget_buckets']}),
    }


def agent_slot_kind(slot):
    # Finite schema-based selection; not a caller-supplied executable locator.
    from .agent_tasks import TEXT_SCHEMA
    if slot['role'] in ('execution_policy','input','budget'):
        expected={'execution_policy':w.POLICY,'input':TEXT_SCHEMA,'budget':'rpnh/module_declaration/v1'}[slot['role']]
        if slot['body_schema_id']!=expected: raise ValueError('AgentTask public slot schema differs from original consumer')
        return slot['role']
    if slot['role'] != 'public_host': raise ValueError('unsupported AgentTask slot role')
    if slot['body_schema_id'] == TARGET: return 'target'
    if slot['body_schema_id'] == BACKEND: return 'backend'
    if slot['body_schema_id'] == 'llm_request_envelope/v1': return 'transport'
    raise ValueError('unsupported AgentTask public host slot')


def prepare_agent_material_draft(*, profile_id, parent, request_bytes, root_binding, control_root):
    request = w.decode(request_bytes)
    SchemaCatalog().validate_schema_ref('rpnh/parent_child_request/v1', request)
    if _json(request) != request_bytes or request['child_kind'] != 'agent_task':
        raise ValueError('AgentTask needs exact canonical public request')
    target = _target(parent, request['slot_id'], root_binding, control_root)
    spec, module = public_agent_payload(request['definition'], target)
    config = request['public_configuration']
    if set(config) != {'policy', 'opaque_bindings'}:
        raise ValueError('AgentTask public configuration requires policy and opaque bindings')
    policy = w.validate_policy(config['policy'])
    entries = [ep for ep in metadata.entry_points(group='rpnh.environment_hosts') if ep.name == profile_id]
    if len(entries) != 1: raise ParentChildUnsupported('PUBLIC_MATERIAL_PROFILE_AMBIGUOUS_OR_MISSING')
    # Static contract is checked before original built-in factories. No ep.load
    # or arbitrary callback: this ABI explicitly chooses the original builder.
    installed = read_installed_contract(entries[0], 'agent_task', set())
    contract = installed.contract
    if len(contract['binding_slots']) > 1:
        raise ParentChildUnsupported('single-stage AgentTask supports one declared credential slot')
    from .agent_tasks import agent_task_registration, agent_task_catalog
    registration = agent_task_registration()
    catalog = agent_task_catalog(registered_public=True)
    registration.bind_schema_catalog(catalog)
    snapshot = {'declarations':sorted(registration.declarations(), key=lambda d:(d['kind'],d['key']))}
    before = freeze_candidate_document(snapshot).encode('ascii')
    if {(d['kind'],d['key']) for d in snapshot['declarations']} != {(d['kind'],d['key']) for d in contract['registrations']}:
        raise ValueError('original complete AgentTask Registration differs from installed contract')
    from .bound_child_lowering import with_bound_origin
    from .compiler import compile_module
    bound = with_bound_origin(module)
    compiled = compile_module(bound, registration)
    if before != freeze_candidate_document({'declarations':sorted(registration.declarations(), key=lambda d:(d['kind'],d['key']))}).encode('ascii'):
        raise ValueError('AgentTask Registration changed during compile')
    pairs = {(kind,key) for kind, entries in compiled.registrations.items() for key in entries}
    installed = read_installed_contract(entries[0], 'agent_task', pairs)
    observations = {key:w.decode(raw,canonical_required=True) for key,raw in installed.observations}
    from cpn.llm_adapters.config import RegisteredLLMExecutionSelection
    selection = RegisteredLLMExecutionSelection(contract=contract, policy=policy,
        bindings=config['opaque_bindings'], observations=observations)
    slots = w.selected_slots(contract,pairs,'agent_task')
    kinds = [agent_slot_kind(slot) for slot in slots]
    if sorted(kinds) != sorted(('execution_policy','target','backend','transport','input','budget')):
        raise ValueError('AgentTask requires exact six finite public slots')
    values = agent_slot_values(spec,selection,bound)
    data, nodes = {}, {}
    def node(identity,role,value,schema=None,deps=(),encoding=w.ORIGINAL_ENCODING,media='application/json'):
        if identity in nodes: raise ValueError('duplicate material node')
        raw = value if type(value) is bytes else w.canonical(value) if encoding == w.C_ENCODING else canonical_json(w.strict_copy(value))
        data[identity]=raw
        nodes[identity]={'node_id':identity,'role':role,'media_type':media,'body_schema_id':schema,'encoding':encoding,'size':len(raw),'sha256':w.sha(raw),'depends_on':sorted(set(deps))}
    schema_ids = set(compiled.registrations.get('schema',{})) | set(w.SCHEMAS) | {SPEC,'rpnh/module_declaration/v1','rpnh/executable_net/v1'}
    schema_ids.update(schema for schema,_ in values.values() if schema is not None)
    schema_nodes={sid:'schema.s'+str(i) for i,sid in enumerate(sorted(schema_ids))}
    for sid,name in schema_nodes.items():
        doc=registration.declaration('schema',sid)['schema']
        node(name,'schema',canonical_json(doc),'registry_v1/registry_type_catalog/v1',media='application/schema+json')
    obsraw=dict(installed.observations); inline=dict(installed.inline_assets)
    for unit in contract['implementation_units']:
        uid=unit['unit_id']
        if uid not in observations: continue
        observation=observations[uid]; deps=[schema_nodes[w.OBSERVATION],*['observation.'+u for u in unit['dependency_unit_ids']]]
        for asset in observation['assets']:
            aid=asset['inline_node_id']
            if aid is not None:
                node(aid,'implementation_asset',inline[aid],encoding=w.OPAQUE_ENCODING,media='application/octet-stream');deps.append(aid)
        node('observation.'+uid,'implementation_observation',obsraw[uid],w.OBSERVATION,deps,w.C_ENCODING)
    policy_id=dict(spec.registered_execution_sources)['default']
    names={kind:policy_id if kind=='execution_policy' else 'slot.'+slot['slot_id'] for slot,kind in zip(slots,kinds)}
    credential_units={config['opaque_bindings'][0][role+'_unit_id'] for role in ('resolver','renderer')} if config['opaque_bindings'] else set()
    for slot,kind in zip(slots,kinds):
        schema,value=values[kind];deps=[] if schema is None else [schema_nodes[schema]]
        if kind=='execution_policy': deps += ['observation.'+u for u in {policy['implementation_unit_id'],*credential_units}]
        elif kind in ('target','backend','transport'): deps.append(policy_id)
        node(names[kind],slot['role'],value,schema,deps,w.C_ENCODING if kind=='execution_policy' else w.ORIGINAL_ENCODING)
    selection_doc={'schema_version':w.SELECTION,'child_kind':'agent_task',
        'selected_registrations':[{'kind':k,'key':v} for k,v in sorted(pairs)],
        'selected_public_slots':[{'slot_id':slot['slot_id'],'node_id':names[kind]} for slot,kind in zip(slots,kinds)],
        'execution_profiles':[{'profile_id':'default','policy_node_id':policy_id,'target_node_id':names['target'],'backend_node_id':names['backend'],'transport_node_id':names['transport']}]}
    node('selection','selection',selection_doc,w.SELECTION,[schema_nodes[w.SELECTION],*names.values()],w.C_ENCODING)
    obs_names=sorted('observation.'+u for u in observations)
    node('payload','payload',canonical_json(spec.as_worker_document(document_root=Path(target['document_root']))),SPEC,['selection',policy_id,schema_nodes[SPEC]])
    node('declaration','declaration',canonical_json(bound.to_dict()),'rpnh/module_declaration/v1',['payload',schema_nodes['rpnh/module_declaration/v1']])
    node('registration','registration',before,None,[*obs_names,*schema_nodes.values()])
    node('lowered-net','lowered_net',canonical_json(compiled.to_dict()),'rpnh/executable_net/v1',['declaration','registration',schema_nodes['rpnh/executable_net/v1']])
    node('normalized-request','normalized_request',_json({'request':request,'target':target}),None,['payload'])
    roots={'payload':'payload','normalized_request':'normalized-request','declaration':'declaration','lowered_net':'lowered-net','registration':'registration','selection':'selection',
        'schemas':sorted(schema_nodes.values()),'implementation':obs_names,'public_host':sorted(names[k] for k in ('target','backend','transport')),
        'execution_policy':[policy_id],'inputs':[names['input']],'budgets':[names['budget']]}
    if read_installed_contract(entries[0],'agent_task',pairs) != installed:
        raise ParentChildUnsupported('PUBLIC_MATERIAL_CHANGED_DURING_PREPARATION')
    draft=PublicMaterialDraft(installed.contract_bytes,w.canonical([nodes[k] for k in sorted(nodes)]),w.canonical(roots),tuple(sorted(data.items())),registration,'agent_task',w.canonical(config['opaque_bindings']))
    from .registry.public_materials import validate_draft
    validate_draft(draft)
    return draft


@dataclass(frozen=True, slots=True)
class RegisteredAgentExecutionContext:
    """Owner source locators only; never permission to execute a child."""
    materials: object

    def __post_init__(self):
        from cpn.components.registered_material_checks import RegisteredMaterialContext
        if type(self.materials) is not RegisteredMaterialContext:
            raise TypeError('bound execution context requires original owner material source context')

    def verify_spec(self,spec):
        checked=self.materials.read_verified()
        payload=checked.documents[checked.inventory.root['roots']['payload']]
        normalized=checked.documents[checked.inventory.root['roots']['normalized_request']]
        if canonical_json(spec.as_worker_document(document_root=Path(normalized['target']['document_root']))) != canonical_json(payload):
            raise ValueError('bound execution Spec differs from sealed inventory')

    def require_native_execution(self):
        raise ParentChildUnsupported('native receipt/origin issuer and bound worker execution remain unavailable')
