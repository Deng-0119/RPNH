"""Linux installer lifetime tests; synthetic candidate is not product acceptance."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import venv
from unittest.mock import patch

import pytest

from cpn.process_supervision import ProcessStartError, SupervisedProcess
from cpn.rpnh import install_executor as executor
from cpn.rpnh import install_materials as materials

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux installer ownership")

LOCAL_PATHS = ("HOME", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
               "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "PIP_CACHE_DIR",
               "UV_CACHE_DIR", "npm_config_cache", "NODE_COMPILE_CACHE")


@pytest.fixture
def candidate(tmp_path):
    """Private -I interpreter and a reduced candidate, with a real exclusive FD."""
    directory = tmp_path / ("i-" + "1" * 32)
    directory.mkdir()
    venv.EnvBuilder(with_pip=False).create(directory / "venv")
    site = next((directory / "venv/lib").glob("python*/site-packages"))
    package = site / "cpn/rpnh"
    package.mkdir(parents=True)
    (site / "cpn/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    module = package / "install_candidate.py"
    with (tmp_path / "lifecycle.lock").open("w+b") as lease:
        os.fchmod(lease.fileno(), 0o600)
        fcntl.flock(lease, fcntl.LOCK_EX)
        yield tmp_path, directory, module, lease


def test_candidate_preserves_private_interpreter_lease_argv_group_and_environment(candidate):
    root, directory, module, lease = candidate
    module.write_text("""
import fcntl,json,os,sys,tempfile
from pathlib import Path
import cpn
root=Path(os.environ["RPNH_MANAGED_ROOT"])
fd=int(os.environ["RPNH_CANDIDATE_FD"])
assert (os.fstat(fd).st_dev,os.fstat(fd).st_ino)==(root.joinpath("lifecycle.lock").stat().st_dev,root.joinpath("lifecycle.lock").stat().st_ino)
with root.joinpath("lifecycle.lock").open("rb") as probe:
    try: fcntl.flock(probe,fcntl.LOCK_SH|fcntl.LOCK_NB)
    except BlockingIOError: pass
    else: raise AssertionError("EX lease missing")
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
print(json.dumps({"operation":sys.argv[1],"status":"verified",
 "installation_id":os.environ["RPNH_CANDIDATE_ID"],"pgid":os.getpgrp(),
 "prefix":sys.prefix,"executable":sys.executable,"module":cpn.__file__,
 "isolated":sys.flags.isolated,"tmp":tempfile.gettempdir(),"environment":dict(os.environ)}))
""")
    original = executor.start_process
    launches = []
    def launch(argv, **kwargs):
        launches.append((argv, kwargs))
        return original(argv, **kwargs)
    with patch.object(executor, "start_process", side_effect=launch):
        result = executor.run_candidate_child(root, directory, lease, "probe", timeout=5)
    assert result["pgid"] == os.getpgrp()
    assert result["prefix"] == str(directory / "venv")
    assert result["executable"] == str(directory / "venv/bin/python")
    assert Path(result["module"]).is_relative_to(directory / "venv")
    assert result["isolated"] == 1
    assert Path(result["tmp"]).is_relative_to(directory)
    for key in LOCAL_PATHS:
        assert Path(result["environment"][key]).is_relative_to(directory)
    argv, options = launches[0]
    assert argv == [str(directory / "venv/bin/python"), "-I", "-m", "cpn.rpnh.install_candidate", "probe"]
    assert options["pass_fds"] == (lease.fileno(),)
    assert options["deadline"] > 0
    with (root / "lifecycle.lock").open("rb") as probe:
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)


@pytest.mark.parametrize("timeout", [False, True])
def test_candidate_reaps_descendant_and_refuses_leftovers_or_timeout(candidate, timeout):
    root, directory, module, lease = candidate
    module.write_text("""
import json,os,sys,time
from pathlib import Path
pid=os.fork()
if pid==0:
    Path("child.pid").write_text(str(os.getpid()))
    time.sleep(30)
    Path("late").write_text("must not happen")
    os._exit(0)
while not Path("child.pid").exists(): time.sleep(0.01)
""" + ("time.sleep(30)\n" if timeout else """
print(json.dumps({"operation":"probe","status":"verified","installation_id":os.environ["RPNH_CANDIDATE_ID"]}))
"""))
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired if timeout else RuntimeError,
                       match=None if timeout else "unfinished descendants"):
        executor.run_candidate_child(root, directory, lease, "probe", timeout=0.5 if timeout else 5)
    assert time.monotonic() - started < 4
    with pytest.raises(ProcessLookupError):
        os.kill(int((directory / "child.pid").read_text()), 0)
    assert not (directory / "late").exists()
    assert (directory / "candidate-probe.out").exists()
    assert (directory / "candidate-probe.err").exists()


def test_material_retains_inherited_group_and_local_temporary_cache_paths(tmp_path):
    assert "RPNH_CANDIDATE_ID" not in os.environ
    output = materials._run([sys.executable, "-I", "-c",
        "import json,os,tempfile; print(json.dumps([os.getpgrp(),tempfile.gettempdir(),dict(os.environ)]))"],
        directory=tmp_path)
    group, temporary, environment = json.loads(output)
    assert group == os.getpgrp()
    assert Path(temporary).is_relative_to(tmp_path)
    assert "RPNH_CANDIDATE_ID" not in environment
    for key in LOCAL_PATHS:
        assert Path(environment[key]).is_relative_to(tmp_path)


@pytest.mark.parametrize("complete,leftovers", [(False, False), (False, True), (True, True)])
def test_strict_completion_requires_report_and_no_leftovers(complete, leftovers):
    process = SupervisedProcess(SimpleNamespace(returncode=0), None)
    process.cleanup_complete = complete
    process.had_live_descendants = leftovers
    with patch.object(executor.os, "killpg", side_effect=AssertionError("no group signals")):
        with pytest.raises(RuntimeError, match="cleanup is unconfirmed|unfinished descendants"):
            executor._require_group_absent(process, label="installer")


@pytest.mark.parametrize("caller", ["candidate", "material"])
def test_startup_timeout_keeps_original_failure_and_deadline(tmp_path, caller):
    target = executor if caller == "candidate" else materials
    with patch.object(target, "start_process", side_effect=ProcessStartError("timeout")) as launch:
        with pytest.raises(subprocess.TimeoutExpired) as error:
            if caller == "candidate":
                executor.run_candidate_child(tmp_path, tmp_path, SimpleNamespace(fileno=lambda: 15), "probe", timeout=0)
            else:
                materials._run([sys.executable, "-I", "-c", "pass"], directory=tmp_path, timeout=0)
    assert error.value.timeout == 0
    assert isinstance(error.value.__cause__, ProcessStartError)
    assert launch.call_args.kwargs["deadline"] <= time.monotonic()


def test_material_failure_and_output_bounds_are_retained(tmp_path):
    with pytest.raises(RuntimeError, match="exit 23.*original failure"):
        materials._run([sys.executable, "-I", "-c",
                        "import sys; print('original failure',file=sys.stderr); sys.exit(23)"],
                       directory=tmp_path)
    with pytest.raises(RuntimeError, match="output limit"):
        materials._run([sys.executable, "-I", "-c", "print('x' * (2*1024*1024+1))"],
                       directory=tmp_path)


@pytest.mark.parametrize("caller", ["candidate", "material"])
def test_startup_consumes_the_original_deadline(tmp_path, caller):
    waits = []
    child = SupervisedProcess(SimpleNamespace(returncode=0,
        wait=lambda timeout: waits.append(timeout) or 0), None)
    child.cleanup_complete = True
    target = executor if caller == "candidate" else materials
    def launch(argv, **options):
        assert options["deadline"] == 105
        if caller == "candidate":
            options["stdout"].write(json.dumps({"operation": "probe", "status": "verified",
                "installation_id": tmp_path.name}).encode())
            options["stdout"].flush()
        return child
    with patch.object(target, "start_process", side_effect=launch), \
            patch.object(target.time, "monotonic", side_effect=[100, 104]):
        if caller == "candidate":
            executor.run_candidate_child(tmp_path, tmp_path, SimpleNamespace(fileno=lambda: 15), "probe", timeout=5)
        else:
            materials._run(["unused"], directory=tmp_path, timeout=5)
    assert waits == [1]
