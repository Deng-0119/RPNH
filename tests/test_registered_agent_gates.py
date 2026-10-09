"""Actual owner gates, real Registry/Start and original whole public ports.

The owner socket/selector and environment inventory use the declared test-only
boundaries. Neither bound worker nor native transport is established here.
"""
from copy import deepcopy
import json
from concurrent.futures import ThreadPoolExecutor
import time
import pytest
from cpn.rpnh import public_material_contracts as w
from cpn.components.registered_material_checks import RegisteredMaterialContext
from cpn.rpnh.registry.operations import OperationAuthorityError
from registered_agent_material_fixtures import public_port,public_selection,registered_agent_case
from registered_agent_owner_fixtures import start_optional_owner,start_registered_host_owner,prepare_neutral_attempt,registered_host_request,start_bound_optional_owner
from test_registered_agent_materials import agent_case


def context_factory(material):
    return lambda owner:RegisteredMaterialContext(material.owner._core,material.inventory.resource_ref,
        material.inventory.root['public_material_digest'],owner._core)


def materialization_counter(owner_case,monkeypatch):
    counts={'materialization':0}
    original=owner_case.services._provider_ledger.record_materialization
    def counted(*a,**k):
        counts['materialization']+=1
        return original(*a,**k)
    monkeypatch.setattr(owner_case.services._provider_ledger,'record_materialization',counted)
    return counts


@pytest.mark.parametrize('phase',['dispatch','submission','registered_host'])
@pytest.mark.parametrize('changed',['endpoint','account','runtime','binding_revision'])
def test_real_whole_port_mismatch_before_all_side_effects(agent_case,tmp_path,monkeypatch,phase,changed):
    policy=deepcopy(agent_case.setup.policy);bindings=deepcopy(agent_case.setup.bindings)
    if changed=='endpoint': policy['route_provenance'][0]['endpoint']='https://other.example.invalid/v1/chat/completions'
    elif changed=='account':
        policy['route_provenance'][0]['account_binding_id']='account-b';bindings[0]['account_binding_id']='account-b'
    elif changed=='runtime': policy['runtime']['context_pressure_trigger_ratio']=0.61
    else: bindings[0]['binding_revision']='r2'
    port,credential_counts=public_port(agent_case,tmp_path/'port',policy=policy,bindings=bindings)
    if changed=='binding_revision': assert w.canonical(port.execution_policy)==w.canonical(agent_case.setup.policy)
    constructor=start_registered_host_owner if phase=='registered_host' else start_optional_owner
    owner=constructor(tmp_path/'owner',monkeypatch,target=public_selection(agent_case).input_target,
        policy=agent_case.setup.policy,port=port,material_context_factory=context_factory(agent_case))
    counts=materialization_counter(owner,monkeypatch)
    try:
        expected='full opaque Binding' if changed=='binding_revision' else 'port policy differs'
        with pytest.raises(OperationAuthorityError,match=expected):
            if phase=='dispatch': owner.prepare_dispatcher()
            elif phase=='submission':
                attempt=prepare_neutral_attempt(owner)
                owner.services._prepare_optional_input_submission(owner.execution,attempt)
            else:
                owner.services._registered_host_llm_service.prepare(owner.execution,registered_host_request(),agent_case.setup.policy)
        assert counts['materialization']==0
        assert credential_counts=={}
        owner.boundaries.assert_no_external_calls()
        print(json.dumps({'phase':phase,'changed':changed,'materialization':0,'resolver':0,'renderer':0,'transport':0,'owner_test_boundaries':dict(owner.boundaries.counts)},sort_keys=True))
    finally:
        owner.close();port.close()


def test_registered_host_matching_port_stops_at_explicit_registration_scope(agent_case,tmp_path,monkeypatch):
    port,credential_counts=public_port(agent_case,tmp_path/'port')
    owner=start_registered_host_owner(tmp_path/'owner',monkeypatch,target=public_selection(agent_case).input_target,
        policy=agent_case.setup.policy,port=port,material_context_factory=context_factory(agent_case))
    counts=materialization_counter(owner,monkeypatch)
    try:
        with pytest.raises(OperationAuthorityError,match='current owner complete Registration differs'):
            owner.services._registered_host_llm_service.prepare(owner.execution,registered_host_request(),agent_case.setup.policy)
        assert counts['materialization']==0 and credential_counts=={}
    finally: owner.close();port.close()


@pytest.mark.parametrize('has_port',[True,False])
def test_public_backend_cannot_fall_back_to_legacy_without_context(agent_case,tmp_path,monkeypatch,has_port):
    port,counts=public_port(agent_case,tmp_path/'port')
    owner=start_optional_owner(tmp_path/'owner',monkeypatch,target=public_selection(agent_case).input_target,
        policy=agent_case.setup.policy,port=port if has_port else None)
    try:
        with pytest.raises(OperationAuthorityError,match='sealed owner material context'): owner.prepare_dispatcher()
        assert counts=={}
    finally: owner.close();port.close()


def test_optional_real_public_dispatch_and_submission_gate(tmp_path,monkeypatch):
    agent_case=registered_agent_case(tmp_path/'materials',monkeypatch)
    port,credential_counts=public_port(agent_case,tmp_path/'port')
    owner=start_bound_optional_owner(agent_case,monkeypatch,port=port)
    counts=materialization_counter(owner,monkeypatch)
    try:
        owner.prepare_dispatcher()
        key=str(owner.execution.operation.canonical.context.invocation_ref.version_id)
        wrapper=owner.services._input_bindings[key]
        assert wrapper.input_port is port
        from cpn.components.default_agent_executor import _agent_context_policy
        reduction,ratio=_agent_context_policy(wrapper)
        assert ratio==0.73 and reduction.tool_output_byte_limit==10000
        attempt=prepare_neutral_attempt(owner)
        provider=owner.services._optional_input_authority(owner.execution,attempt)[1]
        assert not hasattr(provider.call,'llm_input_target_ref')
        service=owner.services._optional_agent_service
        target=service._target(owner.execution.operation.canonical.context)
        assert target.context_window_tokens==32768 and target.context_compaction_retained_tokens==4096
        owner.gateway_call('prepare_optional_input_submission',owner.execution,attempt,_owner_binding=wrapper)
        assert counts['materialization']==1 and credential_counts=={}
        assert owner.owner._core.event_store.object_rows_by_type('provider_payload_materialization_receipt/v1')
        # Actual bridge -> actual registered compaction consumer, stopped by
        # its existing no-prior-turn rule. No model/compaction worker runs.
        from cpn.components.agent_loop.service import RegistryAgentLoopLLMPort
        observed=[]
        original_compaction=service.prepare_agent_context_compaction_v1
        def observe_compaction(*args,**kwargs):
            observed.append((kwargs['reduction_settings'],kwargs['pressure_policy']))
            return original_compaction(*args,**kwargs)
        monkeypatch.setattr(service,'prepare_agent_context_compaction_v1',observe_compaction)
        bridge=RegistryAgentLoopLLMPort(service,wrapper,reduction_settings=reduction,context_pressure_trigger_ratio=ratio)
        with pytest.raises(Exception,match='nonempty contiguous turn closure'):
            bridge.compact_agent_context_v1(owner.execution,owner.waiting_loop,owner.tool_catalog,
                trigger_reason='context_pressure',force=False,idempotency_key='actual-context-consumer')
        assert len(observed)==1
        settings,pressure=observed[0]
        assert settings.retained_history_token_limit==4096
        assert pressure.context_window_tokens==32768 and pressure.trigger_ratio==0.73
        assert credential_counts=={}
    finally: owner.close();port.close()


def test_public_wrapper_and_owner_associations_cannot_be_replaced(tmp_path,monkeypatch):
    agent_case=registered_agent_case(tmp_path/'materials',monkeypatch)
    port,_=public_port(agent_case,tmp_path/'port')
    other,_=public_port(agent_case,tmp_path/'other',bindings=[{**agent_case.setup.bindings[0],'binding_revision':'r2'}])
    owner=start_bound_optional_owner(agent_case,monkeypatch,port=port)
    try:
        owner.prepare_dispatcher()
        wrapper=owner.services._input_bindings[str(owner.execution.operation.canonical.context.invocation_ref.version_id)]
        with pytest.raises(AttributeError): wrapper.input_port=other
        with pytest.raises(AttributeError): owner.services._llm_input_port=other
        with pytest.raises(TypeError): owner.services._llm_input_ports_by_transition['worker.run']=other
        attempt=prepare_neutral_attempt(owner)
        from cpn.components.execution_services import _RegisteredOptionalInputBinding
        unassociated=_RegisteredOptionalInputBinding(owner.services.gateway,other,owner.execution)
        with pytest.raises(OperationAuthorityError,match='original owner wrapper association'):
            owner.services._prepare_optional_input_submission(owner.execution,attempt,_owner_binding=unassociated)
    finally: owner.close();port.close();other.close()


def test_real_optional_wrapper_consumes_exact_public_port_after_owner_gate(tmp_path,monkeypatch):
    agent_case=registered_agent_case(tmp_path/'materials',monkeypatch)
    from test_registered_public_adapter import fake_transport
    port,credential_counts=public_port(agent_case,tmp_path/'port')
    owner=start_bound_optional_owner(agent_case,monkeypatch,port=port)
    counts=materialization_counter(owner,monkeypatch)
    try:
        owner.prepare_dispatcher()
        wrapper=owner.services._input_bindings[str(owner.execution.operation.canonical.context.invocation_ref.version_id)]
        attempt=prepare_neutral_attempt(owner)
        transport=fake_transport(monkeypatch)
        submit=owner.event_loop.submit_host
        def synchronous_owner_pump(callback):
            future=submit(callback)
            owner.event_loop.dispatch_ready(timeout=0)
            return future
        monkeypatch.setattr(owner.event_loop,'submit_host',synchronous_owner_pump)
        response=wrapper.request_once(attempt)
        assert response
        assert counts['materialization']==1
        assert credential_counts=={'resolver':1,'renderer':1}
        assert transport.constructions==len(transport.requests)==1
        assert owner.owner._core.event_store.object_rows_by_type('provider_payload_materialization_receipt/v1')
        print(json.dumps({'original_wrapper':True,'materialization':1,'resolver':1,'renderer':1,'fake_http_requests':1,'real_network':0,
            'owner_boundaries':dict(owner.boundaries.counts)},sort_keys=True))
    finally: owner.close();port.close()


@pytest.mark.parametrize('changed',['prompt','global_cap','instruction','attempt_limit'])
def test_sealed_spec_actual_owner_mismatch_rejected(tmp_path,monkeypatch,changed):
    material=registered_agent_case(tmp_path/'materials',monkeypatch)
    port,counts=public_port(material,tmp_path/'port')
    options={'prompt':{'prompt_override':'different registered prompt'},'global_cap':{'global_cap_override':3},
        'instruction':{'instruction_override':'Different registered instruction.'},'attempt_limit':{'max_attempts_override':3}}[changed]
    owner=None
    try:
        if changed in ('instruction','attempt_limit'):
            with pytest.raises(Exception,match='declaration|digest|frozen'):
                owner=start_bound_optional_owner(material,monkeypatch,port=port,**options)
        else:
            owner=start_bound_optional_owner(material,monkeypatch,port=port,**options)
            expected='claimed prompt differs' if changed=='prompt' else 'global budget differs'
            with pytest.raises(OperationAuthorityError,match=expected): owner.prepare_dispatcher()
        assert counts=={}
    finally:
        if owner is not None: owner.close()
        port.close()
