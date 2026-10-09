"""Real Module inputs and protected origin, without executing either plugin."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
import pytest
from cpn.rpnh.registry.operations import OperationAuthorityError
from cpn.rpnh.registry.parent_bound import assert_bound_integrity
from bound_module_gate_fixtures import module_material,start_module_owner

@pytest.mark.parametrize('story',['numbers','files'])
@pytest.mark.parametrize('case',['same','missing_context','entry','task','global_budget'])
def test_bound_module_actual_source_and_inputs(tmp_path,monkeypatch,story,case):
    material=module_material(tmp_path,monkeypatch,story)
    changed={'values':[9.0],'unit':'m'} if story=='numbers' else {'files':{'a.txt':'changed'}}
    owner=start_module_owner(material,monkeypatch,with_context=case!='missing_context',
        entry_override=changed if case=='entry' else None,task_override=changed if case=='task' else None,global_cap=2 if case=='global_budget' else 1)
    try:
        assert assert_bound_integrity(owner.owner._core,for_execution=True) is not None
        assert len(owner.execution.operation.inputs)==2  # request plus capability
        from cpn.rpnh.registry.public_materials import read_inventory
        sealed_before=read_inventory(material.owner._core,material.inventory.resource_ref)
        head=owner.owner._core.event_store.max_ordinal()
        if case in ('same','global_budget'):
            owner.prepare_dispatcher()
            # Exercise the read cap on the same real authority without creating
            # thousands of artificial checkpoints or changing product files.
            import cpn.components.registered_material_checks as checks
            with monkeypatch.context() as patch:
                patch.setattr(checks,'MAX_INITIAL_CHECKPOINT_READS',0)
                with pytest.raises(OperationAuthorityError,match='checkpoint read bound exceeded'):
                    owner.prepare_dispatcher()
        else:
            with pytest.raises(OperationAuthorityError): owner.prepare_dispatcher()
        assert read_inventory(material.owner._core,material.inventory.resource_ref)==sealed_before
        assert owner.owner._core.event_store.max_ordinal()==head
        assert 'numpy' not in sys.modules and 'cpn.plugins.worker' not in sys.modules
        assert owner.boundaries.counts['synthetic_inventory_capture']==0
        assert not owner.owner._core.event_store.object_rows_by_type('provider_payload_materialization_receipt/v1')
    finally:owner.close()

@pytest.mark.parametrize('story',['numbers','files'])
def test_ordinary_legacy_module_still_has_no_material_context_requirement(tmp_path,monkeypatch,story):
    material=module_material(tmp_path,monkeypatch,story)
    owner=start_module_owner(material,monkeypatch,bound_mode=False,with_context=False,global_cap=2)
    try:
        assert assert_bound_integrity(owner.owner._core) is None
        owner.prepare_dispatcher()
        assert 'numpy' not in sys.modules and 'cpn.plugins.worker' not in sys.modules
    finally:owner.close()
