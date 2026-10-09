"""Read-only owner material checks. Success never grants execution permission."""
from dataclasses import dataclass
import importlib.metadata as metadata
from pathlib import Path
from cpn.rpnh import public_material_contracts as w
from cpn.rpnh.registry.public_materials import read_verified_materials
from cpn.rpnh.public_module_materials import read_installed_contract
from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.operations import OperationAuthorityError

# Finite negative-only owner read bound; no new checkpoint index or authority.
MAX_INITIAL_CHECKPOINT_READS = 4096


@dataclass(frozen=True, slots=True)
class RegisteredMaterialContext:
    """Owner-created exact source locators; no self-reported authorization flag."""
    material_core: object
    inventory_ref: ResourceVersionRef
    public_material_digest: str
    owner_core: object

    def read_verified(self):
        checked = read_verified_materials(self.material_core, self.inventory_ref)
        root=checked.inventory.root
        if root['public_material_digest']!=self.public_material_digest:
            raise OperationAuthorityError('sealed registered material digest changed')
        contract=w.decode(checked.contract_bytes,canonical_required=True)
        docs=checked.documents
        selected=docs[root['roots']['selection']]
        pairs={(v['kind'],v['key']) for v in selected['selected_registrations']}
        entries=[ep for ep in metadata.entry_points(group='rpnh.environment_hosts') if ep.name==contract['profile_id']]
        if len(entries)!=1: raise OperationAuthorityError('selected public installation unavailable')
        observed=read_installed_contract(entries[0],root['child_kind'],pairs)
        if observed.contract_bytes!=checked.contract_bytes:
            raise OperationAuthorityError('sealed installed contract changed')
        actual=dict(observed.observations)
        for name in root['roots']['implementation']:
            if actual.get(docs[name]['unit_id'])!=dict(checked.payloads)[name]:
                raise OperationAuthorityError('sealed implementation observation changed')
        for name,raw in observed.inline_assets:
            if dict(checked.payloads).get(name)!=raw:
                raise OperationAuthorityError('sealed implementation asset changed')
        return checked


def read_execution_resources(services,execution,*,reader,required=True):
    """One original FiringView, binding membership and original read grants."""
    kernel=services._kernel;core=services._owner._core
    context=execution.operation.canonical.context
    view=core.event_store.firing_view(firing_version_id=context.own_transition_firing_ref.version_id,
        invocation_version_id=context.invocation_ref.version_id)
    binding=kernel._exact_object_for_view(view,context.operation_binding_ref,expected_type='operation_binding/v1').metadata
    roles=('optional_agent_backend','optional_agent_transport') if reader=='optional' else ('registered_host_llm_backend','registered_host_llm_transport')
    if not required:
        # Discovery only: legacy HOSTs may legitimately omit these resources.
        # A public schema marker makes the exact full reader mandatory even
        # when the caller omitted both port and registered material context.
        public_marker=False
        for raw in binding['readable_resource_refs']:
            version=_version_from_payload(raw)
            if version.entity_type!='resource_version/v1': continue
            ref=ResourceVersionRef(version.entity_id,version.version_id)
            prepared=kernel._firing_prepared(context,ref,view=view)
            if prepared.metadata.get('descriptors',{}).get('content_role')!=roles[0]: continue
            body=w.decode(kernel._read_firing_registered(context,ref,view=view,prepared=prepared))
            selected=body.get('selection',{})
            version=selected.get('schema_version') if type(selected) is dict else None
            if type(version) is str and version.startswith('rpnh/public_execution_policy/'):
                public_marker=True
        if not public_marker: return None
    found={}
    def read(ref):
        kernel._authorize_resource(context,context.operation_binding_ref,ref,metadata_only=False)
        prepared=kernel._firing_prepared(context,ref,view=view)
        raw=kernel._read_firing_registered(context,ref,view=view,prepared=prepared)
        return prepared,w.decode(raw)
    for raw in binding['readable_resource_refs']:
        version=_version_from_payload(raw)
        if version.entity_type!='resource_version/v1': continue
        ref=ResourceVersionRef(version.entity_id,version.version_id)
        prepared=kernel._firing_prepared(context,ref,view=view)
        role=prepared.metadata.get('descriptors',{}).get('content_role')
        if role in roles:
            if role in found: raise OperationAuthorityError('duplicate exact public execution resource role')
            _,doc=read(ref);found[role]=(ref,doc)
    if set(found)!=set(roles): raise OperationAuthorityError('public execution resources are unavailable')
    target_ref=_resource_from_payload(binding['llm_input_target_ref'])
    _,target=read(target_ref)
    return target_ref,target,found[roles[0]],found[roles[1]]


def compare_public_execution(checked,port,target,backend,transport,*,reader):
    """The sole policy/Binding/capability/target comparison kernel."""
    from cpn.llm_adapters.factory import BoundLLMInputPort
    from cpn.llm_adapters.config import RegisteredLLMExecutionSelection
    from cpn.rpnh.public_agent_materials import TRANSPORT
    root=checked.inventory.root;docs=checked.documents
    contract=w.decode(checked.contract_bytes,canonical_required=True)
    if root['child_kind']!='agent_task': raise OperationAuthorityError('Module does not declare public LLM execution')
    spec=docs[root['roots']['payload']]
    profiles=docs[root['roots']['selection']]['execution_profiles']
    policy_id=spec['registered_execution_sources']['default']
    if len(profiles)!=1 or profiles[0]['profile_id']!='default' or profiles[0]['policy_node_id']!=policy_id:
        raise OperationAuthorityError('sealed default execution profile differs')
    observations={docs[name]['unit_id']:docs[name] for name in root['roots']['implementation']}
    selection=RegisteredLLMExecutionSelection(contract=contract,policy=docs[policy_id],bindings=root['opaque_bindings'],observations=observations)
    if type(port) is not BoundLLMInputPort:
        raise OperationAuthorityError('public execution requires actual original Bound port')
    actual=port.public_identity
    expected=selection.public_identity
    if w.canonical(actual['policy'])!=w.canonical(expected['policy']) or w.canonical(port.execution_policy)!=w.canonical(expected['policy']):
        raise OperationAuthorityError('actual public port policy differs from sealed policy')
    if w.canonical(actual['binding'])!=w.canonical(expected['binding']):
        raise OperationAuthorityError('actual public port full opaque Binding differs from sealed binding')
    if w.canonical(actual)!=w.canonical(expected):
        raise OperationAuthorityError('actual public port capability/UnitObservation differs from sealed identity')
    expected_schema='optional_agent_execution_provenance/v1' if reader=='optional' else 'registered_host_execution_provenance/v1'
    policy=selection.as_registry_policy();route=policy['route_provenance'][0]
    expected_backend={'schema_version':expected_schema,'model':policy['model_condition'],'backend':'external_provider',
        'timeout_seconds':policy['timeout_seconds'],'selection':policy,'transport_kind':route['transport'],'response_protocol':'llm_response_envelope/v1'}
    if (w.canonical(backend)!=w.canonical(expected_backend)
            or canonical_json(target)!=canonical_json(selection.input_target.as_registry_document())
            or canonical_json(transport)!=canonical_json(TRANSPORT)):
        raise OperationAuthorityError('current registered target/backend/transport differs from sealed public policy')
    # Optional route body is also the exact material producer's original body.
    profile=profiles[0]
    if reader=='optional' and w.canonical(backend)!=w.canonical(docs[profile['backend_node_id']]):
        raise OperationAuthorityError('current optional backend differs from sealed body')
    if canonical_json(target)!=canonical_json(docs[profile['target_node_id']]) or canonical_json(transport)!=canonical_json(docs[profile['transport_node_id']]):
        raise OperationAuthorityError('current target/transport differs from sealed body')


def check_registered_execution(services,execution,*,reader='optional',attempt=None,provider=None):
    port=services._selected_input_port(execution)
    context=services._registered_material_context
    if context is None:
        # Public bound origin is an independent source marker. A deterministic
        # Module has no LLM policy/port from which to infer this requirement.
        from cpn.rpnh.registry.parent_bound import assert_bound_integrity
        if assert_bound_integrity(services._owner._core,for_execution=True) is not None:
            raise OperationAuthorityError('registered bound execution requires sealed owner material context')
    resources=None
    public_port=False
    if port is not None:
        policy=getattr(port,'execution_policy',None)
        public_port=isinstance(policy,dict) and policy.get('schema_version')==w.POLICY
    declaration=services._owner.registration.declaration('executor',execution.operation.spec.executor_key)
    if declaration['contracts'].get('transport')=='llm':
        resources=read_execution_resources(services,execution,reader=reader,required=context is not None or public_port)
    elif context is None and not public_port:
        return
    public_body=resources is not None and resources[2][1].get('selection',{}).get('schema_version')==w.POLICY
    if context is None:
        if public_port or public_body: raise OperationAuthorityError('registered public execution requires sealed owner material context')
        return
    if type(context) is not RegisteredMaterialContext or context.owner_core is not services._owner._core:
        raise OperationAuthorityError('registered materials crossed owner source context')
    checked=context.read_verified();root=checked.inventory.root;docs=checked.documents
    if root['child_kind']=='module':
        if port is not None: raise OperationAuthorityError('Module material does not include public LLM policy')
    else:
        if resources is None or port is None: raise OperationAuthorityError('registered AgentTask has no actual port')
        target_ref,target,(backend_ref,backend),(transport_ref,transport)=resources
        compare_public_execution(checked,port,target,backend,transport,reader=reader)
        if attempt is not None:
            call=provider.call
            neutral=services._kernel._exact_object(attempt.invocation_ref,expected_type='llm_invocation_spec/v1').metadata
            if (_resource_from_payload(neutral['llm_input_target_ref'])!=target_ref
                    or call.llm_execution_target_ref!=backend_ref or call.transport_contract_ref!=transport_ref
                    or call.model!=backend['model'] or call.backend!=backend['backend']
                    or call.timeout_seconds!=backend['timeout_seconds'] or call.max_response_bytes!=target['max_response_bytes']
                    or call.interaction_protocol_ref!=transport['interaction_protocol_ref'] or call.response_adapter_ref!=transport['response_adapter_ref']):
                raise OperationAuthorityError('neutral invocation/call exact public resource join differs')
    snapshot={'declarations':sorted(services._owner.registration.declarations(),key=lambda d:(d['kind'],d['key']))}
    if canonical_json(snapshot)!=canonical_json(docs[root['roots']['registration']]):
        raise OperationAuthorityError('current owner complete Registration differs from sealed material')
    check_owner_bound_materials(services,execution,checked)


def check_owner_bound_materials(services,execution,checked):
    """Reuse protected bound integrity, then join the same actual net/input.

    Original PN and bound-origin validators remain the only authority. Public
    material consistency cannot turn an ordinary owner into a bound child.
    """
    from cpn.rpnh.registry.parent_bound import assert_bound_integrity
    from cpn.rpnh.public_agent_materials import public_agent_payload
    core=services._owner._core;kernel=services._kernel
    origin=assert_bound_integrity(core,for_execution=True)
    if origin is None:
        raise OperationAuthorityError('registered material execution requires original protected bound origin')
    marker=kernel._exact_object(_version_from_payload(origin['marker_ref']),expected_type='parent_bound_bootstrap/v1').metadata
    root=checked.inventory.root;docs=checked.documents
    accepted=w.decode(marker['acceptance']['intent_json'].encode())
    from cpn.rpnh.registry.public_materials import pair
    if (accepted['materials']['public_material_digest']!=root['public_material_digest']
            or accepted['materials']['registered_inventory_ref']!=pair(checked.inventory.resource_ref)
            or accepted['parent']['binding_ref']!=root['source']['binding_ref']
            or accepted['parent']['task_ref']!=root['source']['task_ref']
            or accepted['parent']['source_id']!=root['source']['source_id']
            or marker['initial_declaration_digest']!=root['initial_declaration_digest']):
        raise OperationAuthorityError('protected bound origin differs from sealed registered material')
    context=execution.operation.canonical.context
    net=kernel._exact_object(context.net_instance_ref,expected_type='net_instance/v1').metadata
    compiled_ref=_resource_from_payload(net['team_net_declaration_resource_ref'])
    actual=kernel._read_firing_registered(context,compiled_ref)
    if actual!=dict(checked.payloads)[root['roots']['lowered_net']]:
        raise OperationAuthorityError('current owner compiled net differs from sealed material')
    normalized=docs[root['roots']['normalized_request']]
    if accepted['target']!=normalized['target'] or w.canonical(accepted['request'])!=w.canonical(normalized['request']):
        raise OperationAuthorityError('protected parent request/target differs from sealed material')
    manifest_ref=core.recovery_manifest_ref()
    manifest=kernel._exact_object(manifest_ref,expected_type='task_recovery_manifest/v1').metadata
    expected_budgets=docs[root['roots']['budgets'][0]]
    if canonical_json(manifest['budget_buckets'])!=canonical_json(expected_budgets['budget_buckets']):
        raise OperationAuthorityError('current owner budget buckets differ from sealed material')
    if root['child_kind']=='module':
        check_module_initial_inputs(services,execution,checked,actual,net,marker,manifest)
    if root['child_kind']=='agent_task':
        spec,_=public_agent_payload(docs[root['roots']['payload']],normalized['target'])
        if core.run_dir.resolve()!=spec.run_dir:
            raise OperationAuthorityError('current owner run target differs from sealed Spec')
        inputs=execution.operation.inputs
        if len(inputs)!=1 or kernel._read_firing_registered(context,inputs[0].resource_ref)!=dict(checked.payloads)[root['roots']['inputs'][0]]:
            raise OperationAuthorityError('current owner claimed prompt differs from sealed Spec input')
        original=services._owner.original_input_ref
        if kernel._read_firing_registered(context,original)!=canonical_json(spec.prompt):
            raise OperationAuthorityError('current owner task prompt differs from sealed Spec')
        buckets=expected_budgets['budget_buckets']
        cap=None if any(row['max_attempts'] is None for row in buckets) else sum(row['max_attempts'] for row in buckets)
        expected={'ordinary_global_cap':cap,'task_total_hard_cap':cap,'terminal_quota':0,'finalization_budget':0,'protocol_versions':['rpnh/module_declaration/v1']}
        if any(w.canonical(manifest[key])!=w.canonical(value) for key,value in expected.items()):
            raise OperationAuthorityError('current owner global budget differs from original sealed AgentTask policy')



def check_module_initial_inputs(services,execution,checked,compiled_bytes,net,marker,manifest):
    """Bind initial data through original M0 port/resource provenance.

    This does not equate later PN-produced values with initial input. Request,
    capability and lease inputs remain distinct under the original compiled
    ports and original registered checkpoint/claim authorities.
    """
    from cpn.rpnh.executable_net import _load_compiled_net_offline
    from cpn.rpnh.registry.event_store import verified_checkpoint_head
    from cpn.rpnh.registry.publication import _ref_payload
    core=services._owner._core;kernel=services._kernel
    context=execution.operation.canonical.context
    compiled=_load_compiled_net_offline(w.decode(compiled_bytes))
    root=checked.inventory.root;docs=checked.documents
    descriptors={node['node_id']:node for node in root['content_inventory']}
    expected={}
    for name in root['roots']['inputs']:
        schema=descriptors[name]['body_schema_id']
        if schema in expected: raise OperationAuthorityError('Module material initial input schema mapping is ambiguous')
        expected[schema]=dict(checked.payloads)[name]
    port_by_name={port.name:port for port in compiled.ports}
    entries=[port_by_name[name] for name in compiled.symbolic.entry.values()]
    if {port.schema for port in entries}!=set(expected):
        raise OperationAuthorityError('Module material inputs differ from original declared entry schemas')
    # Walk the original verified checkpoint chain to its immutable M0. Merely
    # finding a same-schema resource in the Registry is not an entry binding.
    ref=verified_checkpoint_head(core.event_store,core.catalog,core.task_id,context.net_instance_ref)
    seen=set()
    while True:
        if len(seen)>=MAX_INITIAL_CHECKPOINT_READS:
            raise OperationAuthorityError('registered initial checkpoint read bound exceeded')
        if ref in seen: raise OperationAuthorityError('cyclic registered checkpoint chain')
        seen.add(ref)
        checkpoint=kernel._exact_object(ref,expected_type='marking_checkpoint/v1').metadata
        if checkpoint['net_instance_ref']!=_ref_payload(context.net_instance_ref):
            raise OperationAuthorityError('Module initial checkpoint crossed its exact net')
        prior=checkpoint['previous_checkpoint_ref']
        if prior is None: break
        ref=_version_from_payload(prior)
    if checkpoint['epoch']!=0 or checkpoint['settlement_delta_ref'] is not None:
        raise OperationAuthorityError('Module lacks original initial checkpoint')
    graph_root=kernel._exact_object(_version_from_payload(net['team_design_root_ref']),expected_type='team_design_root/v1').metadata
    members={w.canonical(value) for value in graph_root['resource_refs']}
    initial={port.place:[] for port in entries}
    for raw_ref in checkpoint['token_refs']:
        token_ref=_version_from_payload(raw_ref)
        token=kernel._exact_object(token_ref,expected_type='petri_token/v1').metadata
        if token['place'] not in initial: continue
        if token['producer'] is not None or token['epoch']!=0 or token['resource_ref'] is None:
            raise OperationAuthorityError('Module entry token lacks original M0 provenance')
        resource=_resource_from_payload(token['resource_ref'])
        if w.canonical(_ref_payload(resource.as_version_ref())) not in members:
            raise OperationAuthorityError('Module initial entry is outside exact design-root resources')
        initial[token['place']].append((token_ref,resource))
    initial_tokens={}
    for port in entries:
        rows=initial[port.place]
        if not port.minimum<=len(rows)<=port.maximum:
            raise OperationAuthorityError('Module M0 entry quantity differs from declared port')
        for token_ref,resource in rows:
            prepared=kernel._firing_prepared(context,resource)
            metadata=prepared.metadata
            if (metadata['content_schema_ref']!=port.schema or metadata['origin_kind']!='private_system'
                    or metadata['producer_ref']!=marker['bootstrap_ref'] or metadata['task_ref']!=marker['child_task_ref']):
                raise OperationAuthorityError('Module initial resource lacks exact entry/schema/bootstrap identity')
            payload=kernel._read_firing_registered(context,resource,prepared=prepared)
            if payload!=expected[port.schema]:
                raise OperationAuthorityError('Module actual initial entry input differs from sealed material')
            initial_tokens[token_ref]=(port.port_id,resource,port.schema)
    entry_ports={port.port_id:port for port in entries}
    for item in execution.operation.inputs:
        if item.port_id not in entry_ports: continue
        token=kernel._exact_object(item.claimed_token_ref,expected_type='petri_token/v1').metadata
        if token['producer'] is None:
            original=initial_tokens.get(item.claimed_token_ref)
            if original is None or original[:2]!=(item.port_id,item.resource_ref):
                raise OperationAuthorityError('Module initial claim is not its exact original entry token/resource')
            if item.consumer_payload!=expected[original[2]]:
                raise OperationAuthorityError('Module initial claimed bytes differ from sealed material')
        # Non-initial producers were already checked by original Start/input
        # authority. Their legitimate intermediate values are not frozen M0.
    task_ref=services._owner.original_input_ref
    task=kernel._firing_prepared(context,task_ref)
    schema=task.metadata['content_schema_ref']
    if schema not in expected or kernel._read_firing_registered(context,task_ref,prepared=task)!=expected[schema]:
        raise OperationAuthorityError('Module actual task input differs from sealed material')
    if (manifest['host_resource_inventory_ref']!=str(task_ref.resource_version_id)
            or manifest['inventory_schema_id']!=schema):
        raise OperationAuthorityError('Module original task/inventory input identity differs')
    declaration=services._owner.registration.declaration('executor',execution.operation.spec.executor_key)
    if 'native_plugin' in declaration['contracts']:
        # Original ABI closes the extra capability token and its exact config;
        # no plugin handler, worker, factory or business transport is invoked.
        from cpn.plugins.host import NativePluginHost
        _,_contract,_binding,_request,_capability,body,_result=NativePluginHost(
            services._owner,kernel,services._repository)._binding(execution)
        for name in root['roots']['public_host']:
            if w.canonical(body['config'])!=w.canonical(docs[name]):
                raise OperationAuthorityError('Module actual capability configuration differs from sealed public configuration')
        # Global Core caps are NOT in the reviewed Module budget leaf. They
        # remain original owner/PN authority; graph shape cannot invent a
        # frozen run_plugin policy. Declared buckets were compared above.
