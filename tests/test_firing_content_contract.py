"""Original Registry contract tests with a tiny deterministic HOST checker.

No AgentLoop, provider, domain fixture, historical test or parallel fact store.
"""
import hashlib
import json

import pytest

from cpn.components.basic import register_basic_components, CONFIG_SCHEMA_ID
from cpn.rpnh.firing_content_contract import (
    FiringContentContractError, preflight_candidate, seal_candidate,
    verify_firing_content_check, get_firing_content_check,
)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.resource_service import _publish_private_system, _ResourceServiceKernel
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run

SCHEMA = 'application/firing_contract_test/v1'
PRODUCTS = {'work.analysis': canonical_json({'value': 7}), 'work.plan': canonical_json({'value': 7})}


def _owner(path, *, bound=True, wrong_source=False, callback=None, missing_binding=False):
    reg = Registration()
    register_basic_components(reg)
    reg.register_schema(SCHEMA, {'$schema': 'http://json-schema.org/draft-07/schema#',
        '$id': SCHEMA, 'type': 'object', 'required': ['value'],
        'properties': {'value': {'type': 'integer'}}, 'additionalProperties': False})
    reg.register_executor('contract-test', lambda *args: None,
        identity={'revision': 'v1'}, contracts={'transport':'deterministic','input_ports':None,'output_ports':None})
    reg.register_tool('test-terminal',lambda *args: None,identity={'revision':'v1'},contracts={})

    def checker(*, snapshot, evidence):
        assert evidence.read_resource(snapshot['sources']['input']['resource_ref']) == canonical_json({'value':7})
        if any(json.loads(p['payload']) != {'value':7} for p in snapshot['products'].values()):
            raise FiringContentContractError('CONTENT_INVALID')
        if callback is not None:
            return callback(snapshot=snapshot,evidence=evidence)

    reg.register_tool('test-checker',checker,identity={'revision':'v1'},
        contracts={'protocol':'firing_content_checker/v1'})
    marker = {'protocol':'firing_content_contract/v1','binding_key':'work',
              'checker_key':'test-checker','success_outcomes':['complete']}
    module = ModuleDeclaration.from_dict({'schema_version':'rpnh/module_declaration/v1',
        'name':'ContractTest','components':[{'name':'work','key':'operation',
        'config_schema':CONFIG_SCHEMA_ID,'config':{'interrupt_returns':{'returned':'request'}},
        'ports':[{'name':n,'direction':d,'schema':SCHEMA} for n,d in
                 [('request','input'),('analysis','output'),('plan','output'),('returned','output')]],
        'operations':[{'name':'run','executor':'contract-test','inputs':['request'],
            'budget_binding':{'bucket_id':'ordinary','budget_scope':'module','finalization_scope':None},
            'outputs':['analysis','plan','returned'],'tools':[],
            'config':{'firing_content_contract':marker} if bound else {},
            'outcomes':[{'name':'complete','products':[{'port':'analysis'},{'port':'plan'}]},
                        {'name':'interrupted','products':[]}]}]}],
        'links':[],'entry':{'request':{'component':'work','port':'request'}},
        'exit':{'analysis':{'component':'work','port':'analysis'},'plan':{'component':'work','port':'plan'}},
        'terminal':{'key':'test-terminal','source':{'component':'work','port':'plan'},
                    'operation':'run','outcome':'complete','config':{'run_outcome':'complete'}},
        'required_schemas':[SCHEMA,CONFIG_SCHEMA_ID],'budgets':{}})
    def configure(owner):
        def publish(value,key):
            return _publish_private_system(owner._core,owner.identity.task_ref,
                PublishResource(origin=PrivateSystemOrigin(owner.bootstrap_ref),payload=canonical_json(value),
                    media_type='application/json',content_schema_ref=None,summary=key,
                    lifetime_ref=owner.identity.task_ref,idempotency_key=key))
        source = owner.original_input_ref
        if wrong_source:
            source = publish({'value': 6}, 'wrong-source-version')
        contract = {'protocol':'firing_content_contract/v1',
            'run_ref':_ref_payload(owner.identity.run_ref),'net_ref':_ref_payload(owner.publication.net_ref),
            'operation_binding_refs':[_ref_payload(r) for r in owner.publication.binding_refs.values()],
            'checker':reg.declaration('tool','test-checker'),
            'sources':{'input':{'resource_ref':{'resource_id':str(source.resource_id),'resource_version_id':str(source.resource_version_id)},
                'sha256':hashlib.sha256(canonical_json({'value':7})).hexdigest()}},
            'products':{name:SCHEMA for name in PRODUCTS},'required_reads':[],'refinement':None}
        contract_ref=publish(contract,'contract')
        mapping={} if missing_binding else {'work':{'resource_id':str(contract_ref.resource_id),'resource_version_id':str(contract_ref.resource_version_id)}}
        return publish({'firing_content_contracts':mapping},'config'),owner.publication.declaration_resource_ref
    value=OwnerInput(SCHEMA,canonical_json({'value':7}),'test')
    owner=start_run(module,reg,run_dir=path,task_input=value,entry_inputs={'request':value},
        budgets=ModuleBudgetDeclaration(({'bucket_id':'ordinary','budget_scope':'module',
            'finalization_scope':None,'max_attempts':4},),('rpnh/module_declaration/v1',),4,0,4,0),
        model_condition='SCRIPTED_OFFLINE_TEST',owner_statement='offline contract test',command_id='fresh',
        configuration_sources=configure if bound else None)
    admitted=owner.admit('work.run',logical_tau=0,command_id='admit')
    execution=owner.start(admitted,command_id='start')
    return owner,execution


def _seal(owner,execution,key='seal',products=PRODUCTS):
    return seal_candidate(owner._core,execution,products=products,selected_outcome_id='complete',idempotency_key=key)


def _checks(owner):
    return [json.loads(r['metadata_json']) for r in owner._core.event_store.object_rows_by_type('resource_version/v1')
            if json.loads(r['metadata_json']).get('descriptors',{}).get('firing_content_check')=='v1']


def test_correctable_draft_atomic_success_and_same_command_replay(tmp_path):
    owner,execution=_owner(tmp_path/'positive')
    head=owner._core.event_store.max_ordinal()
    with pytest.raises(FiringContentContractError,match='CONTENT_INVALID'):
        _seal(owner,execution,products={k:canonical_json({'value':8}) for k in PRODUCTS})
    assert owner._core.event_store.max_ordinal()==head
    assert not _checks(owner)
    preflight_candidate(owner._core,execution,products=PRODUCTS,selected_outcome_id='complete')
    outputs=_seal(owner,execution)
    assert len(outputs.outputs)==2 and len(_checks(owner))==1
    sealed_head=owner._core.event_store.max_ordinal()
    replay=_seal(owner,execution)
    assert [o.resource_ref for o in outputs.outputs]==[o.resource_ref for o in replay.outputs]
    assert owner._core.event_store.max_ordinal()==sealed_head
    with pytest.raises(FiringContentContractError,match='cannot be overwritten'):
        _seal(owner,execution,key='other')
    rows=[owner._core.event_store.object_row(o.resource_ref.resource_version_id) for o in outputs.outputs]
    check=_checks(owner)[0]
    checkrow=owner._core.event_store.object_row(check['resource_version_id'])
    assert len({r['transaction_id'] for r in [*rows,checkrow]})==1
    exact_check,checked=get_firing_content_check(owner._core,execution)
    assert str(exact_check.resource_version_id)==check['resource_version_id']
    assert checked['selected_outcome_id']=='complete'
    verify_firing_content_check(owner._core,{k:check[k] for k in ['resource_id','resource_version_id']})
    owner.succeed(outputs,command_id='success')
    assert len(owner._core.event_store.object_rows_by_type('operation_result/v1'))==1


def test_low_level_single_port_publication_is_rejected_without_sealing(tmp_path):
    from cpn.rpnh.registry.resources import PetriOutputOrigin
    owner,execution=_owner(tmp_path/'bypass')
    kernel,_=owner.operation_repository()
    context=execution.operation.canonical.context
    binding=execution.operation.operation_binding.output_port_bindings[0]
    head=owner._core.event_store.max_ordinal()
    with pytest.raises(FiringContentContractError,match='atomic content publication'):
        kernel.publish_bytes(context,PublishResource(origin=PetriOutputOrigin(binding.output_binding_ref,context.activation_ref),
            payload=canonical_json({'value':7}),media_type='application/json',content_schema_ref=SCHEMA,
            summary='bypass',lifetime_ref=context.invocation_ref,idempotency_key='bypass',
            descriptors={'output_outcome_id':'complete','output_port_id':binding.port_id,'place':binding.place}))
    assert owner._core.event_store.max_ordinal()==head
    assert not _checks(owner)
    owner.succeed(_seal(owner,execution),command_id='success')


@pytest.mark.parametrize('case',['wrong_source','missing_binding','forged_pass'])
def test_wrong_source_binding_and_forged_pass_fail_closed(tmp_path,case):
    owner,execution=_owner(tmp_path/case,wrong_source=case=='wrong_source',missing_binding=case=='missing_binding',
        callback=(lambda **kw:{'PASS':True}) if case=='forged_pass' else None)
    head=owner._core.event_store.max_ordinal()
    with pytest.raises(FiringContentContractError):
        _seal(owner,execution)
    assert owner._core.event_store.max_ordinal()==head
    assert not _checks(owner)
    assert not owner._core.event_store.object_rows_by_type('operation_result/v1')


@pytest.mark.parametrize('bound',[False,True])
def test_original_interrupted_exit_requires_no_products_or_pass(tmp_path,bound):
    owner,execution=_owner(tmp_path/str(bound),bound=bound,missing_binding=True)
    outputs=owner.products(execution,outcome_id='interrupted',products={},command_id='interrupt-products')
    owner.succeed(outputs,command_id='interrupt-success')
    assert not _checks(owner)
    result=json.loads(owner._core.event_store.object_rows_by_type('operation_result/v1')[0]['metadata_json'])
    assert result['selected_outcome_id']=='interrupted' and not result['output_resource_refs']
    assert owner.snapshot()['enabled_transitions']==['work.run']


def test_unbound_original_products_unchanged(tmp_path):
    owner,execution=_owner(tmp_path/'unbound',bound=False)
    outputs=owner.products(execution,outcome_id='complete',products={k:(v,) for k,v in PRODUCTS.items()},command_id='outputs')
    owner.succeed(outputs,command_id='success')
    assert not _checks(owner)


def test_recovery_shared_gate_rechecks_actual_bytes_and_inventory(tmp_path,monkeypatch):
    from cpn.rpnh.registry.firing_success import _verify_closed_operation_outputs
    from cpn.rpnh.registry._operation import registration
    owner,execution=_owner(tmp_path/'recovery')
    outputs=_seal(owner,execution)
    kernel,repo=owner.operation_repository()
    assert _verify_closed_operation_outputs(kernel,repo,execution,outputs)==outputs
    with monkeypatch.context() as m:
        m.setattr(registration,'_OPERATION_REGISTRATION',None)
        with pytest.raises(FiringContentContractError,match='inventory'):
            _verify_closed_operation_outputs(kernel,repo,execution,outputs)
    # A saved check cannot be reapplied to changed actual product bytes.
    prepared=owner._core.get_version(outputs.outputs[0].resource_ref.resource_version_id)
    version=prepared.version_id
    path=owner._core.root/'objects'/version.kind/version.value
    assert path.resolve().is_relative_to(tmp_path.resolve())
    original=path.read_bytes()
    try:
        path.write_bytes(original.replace(b'7',b'8'))
        with pytest.raises(FiringContentContractError):
            _verify_closed_operation_outputs(kernel,repo,execution,outputs)
    finally:
        path.write_bytes(original)


def test_final_commit_gate_rejects_when_ordinary_wrappers_are_bypassed(tmp_path,monkeypatch):
    from cpn.rpnh.registry.operation_repository import RegistryOperationAuthorityRepository
    reject = [False]
    def callback(**kw):
        if reject[0]:
            raise FiringContentContractError('FINAL_COMMIT_RECHECK')
    owner,execution=_owner(tmp_path/'commit-gate',callback=callback)
    outputs=_seal(owner,execution)
    reject[0]=True
    # Model the public closure having been bypassed. The original EventStore
    # validator must still invoke the trusted checker inside its write DB.
    monkeypatch.setattr(RegistryOperationAuthorityRepository,'verify_firing_content_outputs',lambda *args:None)
    head=owner._core.event_store.max_ordinal()
    with pytest.raises(FiringContentContractError,match='FINAL_COMMIT_RECHECK'):
        owner.succeed(outputs,command_id='blocked-success')
    assert owner._core.event_store.max_ordinal()==head
    assert not owner._core.event_store.object_rows_by_type('operation_result/v1')
    assert not owner._core.event_store.list_events_by_type(('transition_firing_settled/v1',))


def test_precommit_crash_has_no_partial_ports_and_same_command_replays(tmp_path,monkeypatch):
    from cpn.rpnh.registry.transaction import RegistryTransaction
    owner,execution=_owner(tmp_path/'crash')
    head=owner._core.event_store.max_ordinal()
    original=RegistryTransaction.commit
    def crash(tx):
        if tx.idempotency_key=='crash-seal':
            assert len(tx._objects)==3
            raise RuntimeError('simulated process loss before atomic commit')
        return original(tx)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction,'commit',crash)
        with pytest.raises(RuntimeError,match='simulated process loss'):
            _seal(owner,execution,key='crash-seal')
    assert owner._core.event_store.max_ordinal()==head
    assert not _checks(owner)
    outputs=_seal(owner,execution,key='crash-seal')
    owner.succeed(outputs,command_id='success')
    assert len(_checks(owner))==1


def test_final_commit_cannot_label_complete_products_as_interrupted(tmp_path,monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.registry import firing_success
    owner,execution=_owner(tmp_path/'wrong-outcome')
    outputs=_seal(owner,execution)
    original=firing_success.prepare_firing_success
    def wrong(*args,**kwargs):
        prepared=original(*args,**kwargs)
        return replace(prepared,result_metadata={**prepared.result_metadata,'selected_outcome_id':'interrupted'},
            terminal_ready_payload={**prepared.terminal_ready_payload,'selected_outcome_id':'interrupted'})
    monkeypatch.setattr(firing_success,'prepare_firing_success',wrong)
    with pytest.raises(FiringContentContractError,match='outcome'):
        owner.succeed(outputs,command_id='false-interruption')
    assert not owner._core.event_store.object_rows_by_type('operation_result/v1')


def test_original_resume_rejects_missing_contract_then_recovers_exact_completion(tmp_path):
    from cpn.rpnh.registry.firing_recovery import record_registered_operation_completion
    from cpn.rpnh.registry.publication import _resource_from_payload
    from cpn.rpnh.run import resume_run
    owner,execution=_owner(tmp_path/'native-recovery')
    outputs=_seal(owner,execution)
    kernel,repo=owner.operation_repository()
    record_registered_operation_completion(owner._core,kernel,repo,outputs,idempotency_key='returned')
    _,saved=get_firing_content_check(owner._core,execution)
    config_ref=_resource_from_payload(saved['configuration_ref'])
    prepared=owner._core.get_version(config_ref.resource_version_id)
    path=owner._core.root/'objects'/prepared.version_id.kind/prepared.version_id.value
    assert path.resolve().is_relative_to(tmp_path.resolve())
    raw=path.read_bytes()
    head=owner._core.event_store.max_ordinal()
    try:
        path.write_bytes(raw.replace(b'"work"',b'"gone"'))
        with pytest.raises(FiringContentContractError,match='binding is missing'):
            resume_run(owner.registration,run_dir=owner._core.run_dir,model_condition='SCRIPTED_OFFLINE_TEST')
        assert owner._core.event_store.max_ordinal()==head
        assert not owner._core.event_store.object_rows_by_type('operation_result/v1')
    finally:
        path.write_bytes(raw)
    recovered=resume_run(owner.registration,run_dir=owner._core.run_dir,model_condition='SCRIPTED_OFFLINE_TEST')
    results=recovered._core.event_store.object_rows_by_type('operation_result/v1')
    assert len(results)==1
    result=json.loads(results[0]['metadata_json'])
    assert result['selected_outcome_id']=='complete'
    assert {r['version_id'] for r in result['output_resource_refs']}=={str(o.resource_ref.resource_version_id) for o in outputs.outputs}


def test_existing_partial_original_publication_blocks_new_candidate(tmp_path,monkeypatch):
    from cpn.rpnh import firing_content_contract
    from cpn.rpnh.registry.resources import PetriOutputOrigin
    owner,execution=_owner(tmp_path/'partial')
    kernel,_=owner.operation_repository()
    context=execution.operation.canonical.context
    binding=execution.operation.operation_binding.output_port_bindings[0]
    # Inject a pre-existing partial original publication as the crash input;
    # ordinary low-level publication without this injection is tested rejected.
    with monkeypatch.context() as patch:
        patch.setattr(firing_content_contract,'validate_content_transaction',lambda context:None)
        ref=kernel.publish_bytes(context,PublishResource(
            origin=PetriOutputOrigin(binding.output_binding_ref,context.activation_ref),
            payload=canonical_json({'value':7}),media_type='application/json',content_schema_ref=SCHEMA,
            summary='injected partial publication',lifetime_ref=context.invocation_ref,idempotency_key='partial',
            descriptors={'output_outcome_id':'complete','output_port_id':binding.port_id,'place':binding.place}))
    head=owner._core.event_store.max_ordinal()
    with pytest.raises(FiringContentContractError,match='prior partial'):
        _seal(owner,execution)
    assert owner._core.event_store.max_ordinal()==head
    assert owner._core.event_store.object_row(ref.resource_version_id) is not None
    assert not _checks(owner)


def test_final_commit_binds_actual_resource_schema(tmp_path, monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.resources import PetriOutputOrigin
    owner, execution = _owner(tmp_path / 'actual-schema')
    head = owner._core.event_store.max_ordinal()
    original = _ResourceServiceKernel._publish_bytes_in_transaction
    def wrong_schema(kernel, context, command, transaction, **kwargs):
        ref = original(kernel, context, command, transaction, **kwargs)
        if isinstance(command.origin, PetriOutputOrigin):
            # Bypass the ordinary ResourceService preflight at the pending
            # low-level transaction boundary under review; bytes stay valid.
            for index, item in enumerate(transaction._objects):
                if item.version_id == ref.resource_version_id:
                    metadata = dict(item.metadata, content_schema_ref=None,
                                    content_schema_authority_ref=None)
                    transaction._objects[index] = replace(item, metadata=metadata)
        return ref
    monkeypatch.setattr(_ResourceServiceKernel, '_publish_bytes_in_transaction', wrong_schema)
    with pytest.raises(FiringContentContractError, match='OUTPUT_SCHEMA_BINDING_MISMATCH'):
        _seal(owner, execution)
    assert owner._core.event_store.max_ordinal() == head
    assert not _checks(owner)
    assert not owner._core.event_store.object_rows_by_type('operation_result/v1')


def test_foreign_firing_accepted_check_cannot_seal_identical_candidate(tmp_path,monkeypatch):
    from cpn.rpnh import firing_content_contract as contract
    owner_a,execution_a=_owner(tmp_path/'source-firing')
    _seal(owner_a,execution_a)
    _,accepted_a=get_firing_content_check(owner_a._core,execution_a)
    copied=contract._plain(accepted_a)
    owner_b,execution_b=_owner(tmp_path/'target-firing')
    head=owner_b._core.event_store.max_ordinal()
    # Keep B's real schemas, bytes, checker and publication transaction. Inject
    # only the copied authentic acceptance material where a supplied PASS could
    # otherwise be substituted. Final commit owns the rejection.
    monkeypatch.setattr(contract,'_without_bytes',lambda snapshot:copied)
    with pytest.raises(FiringContentContractError,match='CHECK_BINDING_MISMATCH'):
        _seal(owner_b,execution_b)
    assert owner_b._core.event_store.max_ordinal()==head
    assert not _checks(owner_b)
    assert not owner_b._core.event_store.object_rows_by_type('operation_result/v1')
    with owner_b._core.event_store.connect() as db:
        assert not contract._has_products(
            contract.FiringContentEvidence(owner_b._core.event_store,db,head),
            _ref_payload(execution_b.operation.canonical.context.invocation_ref))
