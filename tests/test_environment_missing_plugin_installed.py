"""Optional wheel-only regression, with real installation and HOST assembly.

Set RPNH_ENVIRONMENT_TEST_WHEELHOUSE to a local directory containing the exact
harness candidate, native demo wheel, and their wheel dependency closure. The
test never accesses an index, executes an owner, or calls a provider.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest


_PREPARE_INSTALLED = r'''
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import cpn
from cpn.rpnh.collaboration.environment_check import check_environment
from cpn.rpnh.collaboration.environment_local_contracts import EnvironmentSelection
from cpn.rpnh.collaboration.environment_plan import plan_environment, resolve_local_wheels
from cpn.rpnh.collaboration.environment_prepare import PreparationExecutionContext, prepare_environment
from cpn.rpnh.collaboration.environment_requirements import read_package_environment
from cpn.rpnh.collaboration.share_packages import PackageResolutionLock

assert Path(cpn.__file__).is_relative_to(Path(sys.prefix))
assert not [ep for ep in metadata.entry_points(group="rpnh.plugins") if ep.name == "demo"]
sample, selection_path, wheelhouse, reports = map(Path, sys.argv[1:])
archive = sample / "native-add-v2.zip"
requirements = read_package_environment(archive,
    package_lock=PackageResolutionLock((sample / "native-add-v2.lock.json").read_bytes()))
selection = EnvironmentSelection.from_bytes(selection_path.read_bytes())
before = check_environment(requirements, selection)
plugin, = [r for r in before.to_dict()["checks"] if r["check_id"].startswith("plugins:")]
assert (plugin["status"], plugin["reason_code"]) == ("missing", "ENVIRONMENT_PLUGIN_MISSING")
assert before.to_dict()["aggregate"] == "blocked"
concrete = resolve_local_wheels(requirements, selection, before, sorted(wheelhouse.glob("*.whl")))
assert concrete.resolution.to_dict()["unresolved"] == []
plan = plan_environment(requirements, selection, before, concrete_selections=concrete)
actions = plan.to_dict()["actions"]
assert plan.to_dict()["unresolved"] == []
assert [(r["kind"], r["target"]["name"]) for r in actions] == [
    ("install_distribution", "rpnh-native-demo"), ("assemble_trusted_host", "rpnh-native/v1")]
approvals = []
def approve_exact_test_plan(digest, inventory, target):
    assert digest == plan.digest
    assert inventory == tuple(actions)
    assert target == requirements.target.to_dict()
    approvals.append(digest)
    return True
context = PreparationExecutionContext(requirements, selection, concrete.resolution, before,
    archive, (), approve_exact_test_plan, report_directory=reports)
prepared = prepare_environment(plan, execution_context=context)
receipt = prepared.receipt.to_dict()
assert approvals == [plan.digest]
assert receipt["failure"] is None, receipt
assert receipt["host_declarations_digest"]
assert all(row["status"] == "completed" for row in receipt["action_results"])
assert prepared.binding is not None
assert prepared.binding.to_dict()["host_profile_id"] == "rpnh-native/v1"
assert prepared.after_check.to_dict()["aggregate"] == "passed_for_checked_scope"
rechecked = check_environment(requirements, prepared.binding, resolution=concrete.resolution)
assert rechecked.to_dict()["aggregate"] == "passed_for_checked_scope"
assert rechecked.to_dict()["execution_permitted"] is False
assert before.to_dict()["aggregate"] == "blocked"
print(json.dumps({"failure": receipt["failure"], "aggregate": rechecked.to_dict()["aggregate"],
    "execution_permitted": rechecked.to_dict()["execution_permitted"], "actions": len(actions)}))
'''


def test_actual_installed_sample_resolves_and_prepares_missing_native_plugin(tmp_path):
    configured = os.environ.get("RPNH_ENVIRONMENT_TEST_WHEELHOUSE")
    if not configured:
        pytest.skip("set RPNH_ENVIRONMENT_TEST_WHEELHOUSE to exact local wheels")
    wheelhouse = Path(configured).resolve()
    harness, = wheelhouse.glob("rpnh_harness-*.whl")
    assert list(wheelhouse.glob("rpnh_native_demo-*.whl"))
    prefix = tmp_path / "existing-python"
    venv.EnvBuilder(with_pip=True).create(prefix)
    python = prefix / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("PIP_")}
    environment.update(PIP_CONFIG_FILE=os.devnull, RPNH_CONFIG=str(tmp_path / "config/config.json"))
    def run(*args):
        result = subprocess.run([str(python), "-I", *map(str, args)], cwd=tmp_path,
            env=environment, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout
    run("-m", "pip", "--isolated", "install", "--no-index", "--find-links", wheelhouse,
        "--disable-pip-version-check", harness)
    exported = tmp_path / "tutorial"
    run("-m", "cpn.examples.cli", "export", "--example", "package_reuse", "--output", exported)
    sample = exported / "examples/package_reuse"
    selected = tmp_path / "selection.json"
    run(sample / "select_environment.py", "--python", python, "--output", selected)
    result = json.loads(run("-c", _PREPARE_INSTALLED, sample, selected, wheelhouse, tmp_path / "reports"))
    assert result == {"failure": None, "aggregate": "passed_for_checked_scope",
        "execution_permitted": False, "actions": 2}
