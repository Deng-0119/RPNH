from dataclasses import replace
from unittest.mock import patch
import pytest
import test_terminal_identity as fixture
from cpn.rpnh.agent_tasks import agent_task_catalog
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import read_run_execution
from cpn.rpnh.task_control import TaskControl
from test_taskcontrol_independent import facade


def test_valid_descriptor_above_four_mib_is_readable(tmp_path):
    original = fixture.ModuleBudgetDeclaration
    def large(*args, **kwargs):
        declaration = original(*args, **kwargs)
        return replace(declaration, protocol_versions=('test/' + 'x' * (4 * 1024 * 1024),))
    with patch.object(fixture, 'ModuleBudgetDeclaration', large):
        owner = fixture.owner_at(tmp_path / 'run')
    fixture.finish(owner)
    prepared = owner._core.get_version(owner.identity.run_ref.version_id)
    assert prepared.size > 4 * 1024 * 1024
    control, handle = facade(tmp_path, owner)
    assert control.result(handle.task_id)['output'] == 'canonical final answer'
    assert control._read_terminal_status(handle.run_dir)['execution_status'] == 'terminal'
    core = _RegistryCore(handle.run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    with pytest.raises(RuntimeError, match='descriptor exceeds reader byte bound'):
        read_run_execution(core, _ResourceServiceKernel(core), max_descriptor_bytes=4 * 1024 * 1024)
