"""Offline contracts for the exported tutorial's target-specific wheel commands.

Recording executables below check shell routing only. They do not download,
install, build, prepare an environment, or prove business execution.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples/package_reuse"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="tutorial requires Bash")


@pytest.fixture(params=["README.md", "README_ZH.md"])
def tutorial(request):
    return (SAMPLE / request.param).read_text()


def blocks(text):
    return re.findall(r"```bash\n(.*?)```", text, re.S)


def block_with(text, command):
    return next(block for block in blocks(text) if command in block)


def recorder(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
with pathlib.Path(os.environ["COMMAND_LOG"]).open("a") as stream:
    stream.write(json.dumps({"executable": sys.argv[0], "args": sys.argv[1:]}) + "\\n")
args = sys.argv[1:]
if args[:2] == ["-m", "venv"]:
    target = pathlib.Path(args[2]) / "bin/python"
    target.parent.mkdir(parents=True)
    target.write_bytes(pathlib.Path(__file__).read_bytes())
    target.chmod(0o700)
elif args[:3] == ["-m", "pip", "download"]:
    dest = pathlib.Path(args[args.index("--dest") + 1])
    for name in ("rpnh_harness-0.1.0rc2-py3-none-any.whl",
                 "rpnh_native_demo-0.3.0-py3-none-any.whl",
                 "target_dependency-1-cp313-cp313-linux_x86_64.whl"):
        (dest / name).write_text("shell routing fixture, not a wheel")
''')
    path.chmod(0o700)
    return path


def shell_context(tmp_path):
    # Paths deliberately contain spaces, as may an installed example export.
    work = tmp_path / "outside checkout"
    work.mkdir()
    controller = recorder(tmp_path / "controller python")
    target = recorder(tmp_path / "target python")
    bin_dir = tmp_path / "bin"
    recorder(bin_dir / "python")
    wheelhouse = work / "wheelhouse"
    wheelhouse.mkdir()
    harness = wheelhouse / "rpnh_harness-0.1.0rc2-py3-none-any.whl"
    native = wheelhouse / "rpnh_native_demo-0.3.0-py3-none-any.whl"
    for path in (harness, native, wheelhouse / "controller-1-cp312-cp312-linux_x86_64.whl"):
        path.write_text("shell routing fixture, not a wheel")
    env = {**os.environ, "WORK": str(work), "CONTROL_PYTHON": str(controller),
           "CONTROL_WHEELHOUSE": str(wheelhouse), "RPNH_WHEEL": str(harness),
           "NATIVE_WHEEL": str(native), "SAMPLE": str(work / "examples/package_reuse"),
           "COMMAND_LOG": str(work / "commands.jsonl"), "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    return env, target


def run_shell(script, env, *, stdin=""):
    completed = subprocess.run([BASH, "-ec", script], cwd=env["WORK"], env=env,
                               input=stdin, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    log = Path(env["COMMAND_LOG"])
    commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return commands


def wheel_arguments(tutorial):
    block = block_with(tutorial, "WHEELS=()")
    # Execute only array selection, never an actual package command.
    return block.split("if rpnh package check-environment", 1)[0] + '''
printf '%s\\n' "${WHEELS[@]}" > "$WORK/wheel-args.txt"
'''


def test_every_documented_bash_block_has_valid_shell_syntax(tutorial):
    for block in blocks(tutorial):
        result = subprocess.run([BASH, "-n"], input=block, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_default_existing_environment_uses_controller_python(tutorial, tmp_path):
    env, _ = shell_context(tmp_path)
    script = block_with(tutorial, '-m venv "$WORK/existing-python"')
    script += block_with(tutorial, 'ROUTE="$WORK/existing"')
    script += wheel_arguments(tutorial)
    commands = run_shell(script, env)
    assert commands[0] == {"executable": env["CONTROL_PYTHON"], "args": ["-m", "venv", env["WORK"] + "/existing-python"]}
    assert commands[1]["executable"] == env["WORK"] + "/existing-python/bin/python"
    assert commands[1]["args"] == ["-m", "pip", "install", "--no-index", "--find-links", env["CONTROL_WHEELHOUSE"], env["RPNH_WHEEL"]]
    assert commands[2]["args"][1:3] == ["--python", env["WORK"] + "/existing-python/bin/python"]
    assert all(Path(arg).parent == Path(env["CONTROL_WHEELHOUSE"]) for arg in
               (Path(env["WORK"]) / "wheel-args.txt").read_text().splitlines()[1::2])


def test_custom_existing_closure_uses_target_and_both_exact_roots(tutorial, tmp_path):
    env, target = shell_context(tmp_path)
    script = 'WHEELS=(--wheel /stale/controller.whl)\n'
    script += block_with(tutorial, "Absolute path to your existing Python:")
    script += block_with(tutorial, 'ROUTE="$WORK/existing"')
    script += wheel_arguments(tutorial)
    commands = run_shell(script, env, stdin=str(target) + "\n")
    assert commands[0] == {"executable": str(target), "args": ["-m", "pip", "--version"]}
    download = commands[1]
    assert download == {"executable": str(target), "args": ["-m", "pip", "download", "--only-binary=:all:", "--dest",
                                                             env["WORK"] + "/wheelhouse-existing", env["RPNH_WHEEL"], env["NATIVE_WHEEL"]]}
    assert commands[2]["args"][1:3] == ["--python", str(target)]
    wheel_args = (Path(env["WORK"]) / "wheel-args.txt").read_text().splitlines()
    assert wheel_args[::2] == ["--wheel"] * 3
    assert all(Path(path).parent == Path(env["WORK"]) / "wheelhouse-existing" for path in wheel_args[1::2])
    assert not any("cp312" in path or "stale" in path for path in wheel_args)


@pytest.mark.parametrize("route", ["new-venv", "setup-document"])
def test_controller_routes_reset_target_wheelhouse_and_arguments(tutorial, tmp_path, route):
    env, _ = shell_context(tmp_path)
    script = 'WHEELHOUSE="$WORK/wheelhouse-existing"\nWHEELS=(--wheel /stale/target.whl)\n'
    script += block_with(tutorial, f'ROUTE="$WORK/{route}"')
    script += wheel_arguments(tutorial)
    commands = run_shell(script, env)
    assert commands[0]["args"][1:3] == ["--python", env["CONTROL_PYTHON"]]
    wheel_args = (Path(env["WORK"]) / "wheel-args.txt").read_text().splitlines()
    assert wheel_args[::2] == ["--wheel"] * 3
    assert all(Path(path).parent == Path(env["CONTROL_WHEELHOUSE"]) for path in wheel_args[1::2])
    assert not any("stale" in path or "wheelhouse-existing" in path for path in wheel_args)


def test_docs_keep_strict_compatibility_bootstrap_and_provenance_boundaries(tutorial):
    for required in ('"$EXISTING_PYTHON" -m ensurepip --upgrade', "py3-none-any",
                     "ENVIRONMENT_WHEEL_PLATFORM_INCOMPATIBLE", "rpds-py", "websockets",
                     "HOST", "after-check", "metadata", "RPNH_WHEEL"):
        assert required in tutorial
    assert "python3 -m venv" not in tutorial
    assert "--break-system-packages" not in tutorial
