"""The actual metadata worker must run without optional numerical packages."""
from pathlib import Path
import subprocess
import venv
from cpn.components.tool_executors import (
    ExecutionEnvironmentIdentity, NumericalToolProfile,
    capture_execution_environment_inventory, query_execution_environment_resources,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef


def test_inventory_needs_no_optional_numeric_packages(tmp_path: Path) -> None:
    prefix = tmp_path / 'bare-python'
    venv.EnvBuilder(with_pip=False, symlinks=False).create(prefix)
    executable = prefix / 'bin/python'
    result = subprocess.run([str(executable), '-I', '-c',
        'import importlib.util; assert importlib.util.find_spec("numpy") is None; '
        'assert importlib.util.find_spec("scipy") is None'],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    environment_ref = VersionRef('execution_environment_identity/v1',
        new_id('execution_environment'), new_id('execution_environment_version'))
    profile_ref = VersionRef('numerical_tool_profile/v1',
        new_id('numerical_tool_profile'), new_id('numerical_tool_profile_version'))
    environment = ExecutionEnvironmentIdentity(environment_ref, 'research-exp',
        str(executable), str(prefix))
    profile = NumericalToolProfile(profile_ref, environment_ref, 15,
        256 * 1024 * 1024, 16, 1024 * 1024, 1024 * 1024)
    inventory = capture_execution_environment_inventory(environment=environment, profile=profile)
    assert inventory['inventory_complete'] is True
    assert inventory['installed_resources'] == []
    observed = query_execution_environment_resources(inventory=inventory,
        environment=environment, packages=('numpy', 'scipy'))
    assert observed['not_installed'] == ['numpy', 'scipy']
    assert observed['installed_resources'] == []
