"""Frozen-R1 negative evidence. No Registry creation, socket or HOST execution."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from cpn.components.basic import register_basic_components
from cpn.rpnh.iteration_profile import IterationProfile, compile_iteration_profile
from cpn.rpnh.registration import Registration
from cpn.rpnh.petri_contracts import DeclarationError
from cpn.rpnh.registry import module_terminal
from test_inert_profile_independent import prepare, document, no_execute


def test_frozen_r1_has_unresolved_terminal_config_not_declared_as_obligation():
    prepared = prepare(2)
    terminals = (prepared.module.terminal, *prepared.module.terminal_alternatives)
    assert len(terminals) == 4
    assert all(t.config == {} for t in terminals)
    assert not hasattr(prepared, 'required_terminal_bindings')
    assert not hasattr(prepared, 'required_terminal_configs')


def test_native_registration_requiring_run_outcome_rejects_original_profile():
    data = document()
    reg = Registration()
    register_basic_components(reg)
    for key in data['schemas'].values():
        reg.register_schema(key, {'$id': key, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'})
    identity = {'implementation_id': 'terminal-gap-negative', 'revision': 'v1'}
    for role in data['roles'].values():
        reg.register_executor(role['executor_key'], no_execute, identity=identity, contracts={})
    schema = 'application/required_native_terminal_config/v1'
    reg.register_schema(schema, {
        '$id': schema, '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object', 'required': ['run_outcome'], 'additionalProperties': False,
        'properties': {'run_outcome': {'enum': ['complete', 'failed']}},
    })
    reg.register_tool(data['terminal_key'], no_execute, identity=identity,
                      contracts={'config_schema': schema, 'binding_protocol': 'rpnh/module_terminal/v1'})
    with pytest.raises(DeclarationError, match='Invalid registered tool config'):
        compile_iteration_profile(IterationProfile.from_dict(data), registration=reg)


class PastOutcomeGate(RuntimeError):
    pass


class AuthorityGate(dict):
    def __getitem__(self, key):
        if key == 'declaration_ref':
            raise PastOutcomeGate('Native reader reached checks after run_outcome')
        return super().__getitem__(key)


@pytest.mark.parametrize('terminal_index', [0, 1, 2, 3])
def test_native_reader_rejects_original_config_before_any_registry_checks(monkeypatch, terminal_index):
    """Exercises the real native reader's gate with inert prestate seams.

    This is a native-reader unit negative, not an actual Registry execution.
    """
    prepared = prepare(2)
    terminal = (prepared.module.terminal, *prepared.module.terminal_alternatives)[terminal_index]
    core = SimpleNamespace(event_store=SimpleNamespace(canonical_object_rows=lambda **kwargs: [object()]))
    authority = AuthorityGate(declaration_schema_ref='rpnh/executable_net/v1')
    monkeypatch.setattr(module_terminal, 'current_run_execution_authority', lambda *args: (object(), authority))
    monkeypatch.setattr(module_terminal, 'hydrate_module_runtime',
                        lambda *args: (object(), SimpleNamespace(compiled=prepared.compiled), object()))
    assert module_terminal._terminal_material_for_binding(core, object(), terminal) is None
    for value in ('complete', 'failed'):
        with pytest.raises(PastOutcomeGate):
            module_terminal._terminal_material_for_binding(core, object(), replace(terminal, config={'run_outcome': value}))
