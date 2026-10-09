"""Independent original structural-revision gate probe on mechanical bound PN."""
from dataclasses import replace
import pytest
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.bound_child_lowering import ORIGIN_SYMBOL, with_bound_origin
from cpn.rpnh.registry.parent_bound import assert_bound_integrity


def test_actual_operation_revision_cannot_change_bound_initial_net(tmp_path, monkeypatch):
    import cpn.rpnh.run as run
    import bound_lowering_fixtures as fixtures
    import test_local_takeover_ordinary_revision as legacy
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.module_revision import DeclaredModuleRevision
    from cpn.rpnh.registration import Registration
    observed = {}
    original_register = Registration.register_tool
    def register(self, key, callback, **kwargs):
        if key == 'test/b4-pure-module-revision/v1':
            original = callback
            def callback(context):
                observed['revision_called'] = True
                return original(context)
        return original_register(self, key, callback, **kwargs)
    original_copy = legacy.deepcopy
    def copy_bound(value):
        return with_bound_origin(__import__('cpn.rpnh.module', fromlist=['ModuleDeclaration']).ModuleDeclaration.from_dict(original_copy(value))).to_dict()
    monkeypatch.setattr(legacy, 'deepcopy', copy_bound)
    monkeypatch.setattr(Registration, 'register_tool', register)
    monkeypatch.setattr(fixtures, 'ModuleBudgetDeclaration', lambda buckets, allowed, *rest: ModuleBudgetDeclaration(buckets, (*allowed, 'rpnh/module_declaration/v1'), *rest))
    def start(module, registration, **kwargs):
        owner, _ = fixtures.compiled_bound_owner(kwargs['run_dir'], monkeypatch, registration, module,
            entries=kwargs['entry_inputs'], task=kwargs['task_input'])
        observed['owner'] = owner
        executable, _, marking = hydrate_module_runtime(owner._core)
        observed['net'] = executable.net_ref
        observed['origin'] = next(t.state for t in marking.tokens if t.state.place == ORIGIN_SYMBOL)
        return owner
    monkeypatch.setattr(run, 'start_run', start)
    with pytest.raises((RegistryConflict, ValueError)) as captured:
        legacy.test_ordinary_revision_success_has_legacy_unmarked_delta(tmp_path)
    assert observed.get('revision_called'), str(captured.value)
    owner = observed['owner']
    executable, _, marking = hydrate_module_runtime(owner._core)
    assert executable.net_ref == observed['net']
    assert next(t.state for t in marking.tokens if t.state.place == ORIGIN_SYMBOL) == observed['origin']
    assert len(owner._core.event_store.list_events_by_type(('net_adopted/v1',))) == 1
    assert not owner._core.event_store.list_events_by_type(('transition_firing_settled/v1',))
    assert_bound_integrity(owner._core)
    print('EXACT_REVISION_REJECTION=' + str(captured.value))
