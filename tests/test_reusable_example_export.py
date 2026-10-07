"""Reusable example acquisition and customization; no task or provider is run."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cpn.examples import gallery
from cpn.examples.cli import main


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def assets(tmp_path, monkeypatch):
    staged = tmp_path / "installed-resources"
    staged.mkdir()
    shutil.copyfile(ROOT / "LICENSE", staged / "LICENSE")
    for source in (ROOT / "examples").rglob("*"):
        relative = source.relative_to(ROOT)
        if any(part in {"__pycache__", "build", "dist"} or part.endswith(".egg-info")
               for part in relative.parts):
            continue
        if source.is_file() and source.suffix in {".py", ".json", ".md", ".png", ".toml", ".txt", ".in", ".zip"}:
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    monkeypatch.setattr(gallery, "_assets", lambda: staged)
    return staged


def test_discovery_preserves_adapter_manifest_and_marks_source_only(capsys):
    assert main(["list"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["example_id"] == "batch-summary-v1"
    examples = {item["id"]: item for item in document["examples"]}
    assert {name for name, item in examples.items() if item["exportable"]} == {
        "adapter_task", "native_plugin", "hybrid_summary", "compose_serial", "package_reuse"}
    for name in ("harnessaudit_office", "rrsi_v06", "automationbench", "jb_steering_packet"):
        assert examples[name]["exportable"] is False
        assert examples[name]["source_url"].startswith("https://github.com/Deng-0119/RPNH/")
        assert examples[name]["prerequisites"]


@pytest.mark.parametrize("name", ["native_plugin", "hybrid_summary", "compose_serial", "package_reuse"])
def test_named_export_preserves_complete_declared_closure(tmp_path, assets, name):
    output = tmp_path / "用户 案例" / name
    assert main(["export", "--example", name, "--output", str(output)]) == 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["example_id"] == name
    for item in manifest["files"]:
        assert hashlib.sha256((output / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert (output / "README_ZH.md").is_file()
    assert (output / "LICENSE").is_file()
    if name == "hybrid_summary":
        assert (output / "examples/_support/profile.py").is_file()
        assert (output / "examples/_support/scripted_model.py").is_file()
        assert (output / "examples/native_plugin/rpnh_demo.py").is_file()
        assert (output / "examples/hybrid_summary/graph.json").is_file()
    if name == "compose_serial":
        assert (output / "examples/net_operations/compose_serial.py").is_file()
        assert not (output / "examples/net_operations/live_agent_replacement.py").exists()
    if name == "package_reuse":
        assert (output / "examples/native_plugin/setup.py").is_file()
        assert (output / "examples/native_plugin/rpnh_environment_plugins.json").is_file()


def test_list_export_never_import_or_launch_example_code(tmp_path, assets, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("export launched a process"))
    source = assets / "examples/native_plugin/rpnh_demo.py"
    source.write_text("raise AssertionError('example code was imported')\n")
    before = set(sys.modules)
    assert main(["list"]) == 0
    assert main(["export", "--example", "hybrid_summary", "--output", str(tmp_path / "copy")]) == 0
    assert "rpnh_demo" not in set(sys.modules) - before


def test_export_never_overwrites_edited_files_or_symlink(tmp_path, assets):
    output = tmp_path / "mine"
    assert main(["export", "--example", "native_plugin", "--output", str(output)]) == 0
    edited = output / "examples/native_plugin/rpnh_demo.py"
    edited.write_text("my edited handler\n")
    with pytest.raises(ValueError, match="already exist"):
        main(["export", "--example", "native_plugin", "--output", str(output)])
    assert edited.read_text() == "my edited handler\n"
    target = tmp_path / "absent-target"
    link = tmp_path / "owned-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="already exist"):
        main(["export", "--example", "native_plugin", "--output", str(link)])
    assert not target.exists()


def test_missing_assets_or_source_only_selection_leave_no_output(tmp_path, assets):
    for name in ["does_not_exist", "automationbench"]:
        output = tmp_path / name
        with pytest.raises(ValueError):
            main(["export", "--example", name, "--output", str(output)])
        assert not output.exists()
    shutil.rmtree(assets / "examples/_support")
    with pytest.raises(ValueError, match="missing"):
        main(["export", "--example", "hybrid_summary", "--output", str(tmp_path / "incomplete")])
    assert not (tmp_path / "incomplete").exists()



def test_incomplete_local_guide_is_rejected_before_export(tmp_path, assets):
    (assets / "examples/native_plugin/README_ZH.md").unlink()
    output = tmp_path / "incomplete-guide"
    with pytest.raises(ValueError, match="guide is missing"):
        main(["export", "--example", "native_plugin", "--output", str(output)])
    assert not output.exists()


def test_local_expected_is_explicit_and_does_not_change_stock(tmp_path, monkeypatch, capsys):
    expected = tmp_path / "expected.json"
    result = tmp_path / "answer.json"
    body = '{"count":3,"total":39,"mean":13,"minimum":9,"maximum":18}'
    expected.write_text(body)
    result.write_text(body)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        main(["verify", "--result", str(result)])
    assert main(["verify", "--result", str(result), "--expected", str(expected)]) == 0
    assert json.loads(capsys.readouterr().out)["expected_source"] == "explicit-local-file"


@pytest.mark.parametrize("body", [
    '{}',
    '{"count":3,"total":' + '9' * 400 + ',"mean":13,"minimum":9,"maximum":18}',
    '{"count":3,"count":3,"total":39,"mean":13,"minimum":9,"maximum":18}',
    '{"total":39,"count":3,"mean":13,"minimum":9,"maximum":18}',
    '{"count":true,"total":39,"mean":13,"minimum":9,"maximum":18}',
    '{"count":3,"total":39,"mean":NaN,"minimum":9,"maximum":18}',
    '{"count":3,"total":1e999,"mean":13,"minimum":9,"maximum":18}',
    '{"count":0,"total":39,"mean":13,"minimum":9,"maximum":18}',
    '{"count":3,"total":38,"mean":13,"minimum":9,"maximum":18}',
    '{"count":3,"total":39,"mean":13,"minimum":20,"maximum":18}',
    '{"count":3,"total":39,"mean":13,"minimum":9,"maximum":18,"extra":0}',
])
def test_local_expected_rejects_malformed_summary_contract(tmp_path, body):
    expected = tmp_path / "expected.json"
    expected.write_text(body)
    with pytest.raises(ValueError):
        main(["verify", "--result", str(expected), "--expected", str(expected)])


def test_actual_installed_wheel_exports_outside_checkout(tmp_path):
    """Run with RPNH_INSTALLED_TEST_PYTHON pointing to a fresh wheel install."""
    python = os.environ.get("RPNH_INSTALLED_TEST_PYTHON")
    if not python:
        pytest.skip("set RPNH_INSTALLED_TEST_PYTHON to a fresh wheel-only environment")
    outside = tmp_path / "已安装 用户目录"
    outside.mkdir()
    def run(*args):
        return subprocess.run([python, "-I", *args], cwd=outside, text=True,
                              capture_output=True, check=True).stdout
    location = run("-c", "import cpn; print(cpn.__file__)").strip()
    assert str(ROOT) not in location
    manifest = json.loads(run("-m", "cpn.examples.cli", "list"))
    assert any(item["id"] == "package_reuse" for item in manifest["examples"])
    for name in ("native_plugin", "hybrid_summary", "compose_serial", "package_reuse"):
        exported = outside / name
        run("-m", "cpn.examples.cli", "export", "--example", name, "--output", str(exported))
        assert (exported / "manifest.json").is_file()
    hybrid = outside / "hybrid_summary/examples/hybrid_summary/run.py"
    assert "--graph" in run(str(hybrid), "--help")
    composition = outside / "compose_serial/examples/net_operations/compose_serial.py"
    original = json.loads(run(str(composition)))
    composition.write_text(composition.read_text().replace('"SerialExample"', '"MyEditedSerial"'))
    changed = json.loads(run(str(composition)))
    assert original["name"] == "SerialExample"
    assert changed["name"] == "MyEditedSerial"
    check = subprocess.run([python, "-I", "-m", "cpn.rpnh_cli", "examples", "export", "--example", "compose_serial", "--output", str(outside / "compose_serial")],
                           cwd=outside, text=True, capture_output=True)
    assert check.returncode != 0
    assert "MyEditedSerial" in composition.read_text()
