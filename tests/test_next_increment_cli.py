"""Installed-entry dispatch boundaries for the read-only next increment."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import socket
import sys
import urllib.request
import zipfile

import pytest

from cpn import rpnh_cli
from cpn.plugins import catalog
from cpn.rpnh.collaboration import package_cli
from cpn.rpnh.collaboration.package_resolution import resolve_package


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "cpn/examples/portable_packages/minimal"
    path = tmp_path / "minimal.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for member in sorted(source.rglob("*")):
            if member.is_file():
                target.write(member, member.relative_to(source).as_posix())
    return path


@pytest.fixture
def inert_dispatch(monkeypatch, tmp_path):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("package inspection crossed an execution boundary")

    monkeypatch.setattr(catalog, "load_catalog", forbidden)
    monkeypatch.setattr(catalog, "read_config", forbidden)
    monkeypatch.setattr(importlib.metadata, "entry_points", forbidden)
    monkeypatch.setattr(rpnh_cli, "resolve_execution_path", forbidden)
    monkeypatch.setattr(rpnh_cli, "interactive_setup", forbidden)
    monkeypatch.setattr(rpnh_cli, "_run_basic_frontend", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "absent-config.json"))
    monkeypatch.setenv("RPNH_PLUGIN_CONFIG", str(tmp_path / "absent-plugins.json"))
    monkeypatch.chdir(tmp_path)
    yield calls
    assert not calls
    assert not (tmp_path / "absent-config.json").exists()
    assert not (tmp_path / ".rpnh").exists()


def test_package_dispatch_forwards_arguments_and_return_code(monkeypatch):
    received = []
    monkeypatch.setattr(package_cli, "main", lambda argv: received.append(argv) or 7)
    argv = ["package", "resolve", "root.zip", "--entry", "selected", "--local-package", "child.zip"]
    assert rpnh_cli.main(argv) == 7
    assert received == [argv[1:]]
    assert argv[0] == "package"


def test_package_preview_uses_no_plugin_provider_or_network(archive, inert_dispatch, capsys):
    assert rpnh_cli.main(["package", "preview", str(archive)]) == 0
    capture = capsys.readouterr()
    report = json.loads(capture.out)
    assert capture.err == ""
    assert report["schema_version"] == "rpnh/package_preview/v1"
    assert report["execution_permitted"] is False
    assert any(row["check_id"] == "host_readiness" and row["status"] == "not_checked"
               for row in report["checks"])


def test_package_resolve_emits_exact_digest_bytes(archive, inert_dispatch, capsys):
    expected = resolve_package(archive)
    assert rpnh_cli.main(["package", "resolve", str(archive), "--entry", "main"]) == 0
    capture = capsys.readouterr()
    assert capture.err == ""
    assert capture.out.encode("ascii") == expected.to_bytes()
    assert hashlib.sha256(capture.out.encode("ascii")).hexdigest() == expected.package_lock_digest


def test_package_rejection_preserves_json_error(inert_dispatch, capsys):
    assert rpnh_cli.main(["package", "preview", "does-not-exist.zip"]) == 2
    capture = capsys.readouterr()
    assert capture.out == ""
    assert json.loads(capture.err)["error"]["code"] == "PACKAGE_UNAVAILABLE"


def test_package_help_is_available_without_config(inert_dispatch, capsys):
    with pytest.raises(SystemExit) as result:
        rpnh_cli.main(["package", "--help"])
    assert result.value.code == 0
    assert "never install or execute" in capsys.readouterr().out


def test_package_unknown_command_does_not_fall_back_to_session(inert_dispatch, capsys):
    with pytest.raises(SystemExit) as result:
        rpnh_cli.main(["package", "install", "module.zip"])
    assert result.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_main_help_mentions_package_without_calling_its_handler(monkeypatch, capsys):
    def forbidden(argv):
        raise AssertionError("main help invoked package command")
    monkeypatch.setattr(package_cli, "main", forbidden)
    with pytest.raises(SystemExit) as result:
        rpnh_cli.main(["--help"])
    assert result.value.code == 0
    assert "rpnh package {preview|resolve} ZIP" in capsys.readouterr().out


def test_package_dispatch_uses_process_arguments(monkeypatch, capsys, inert_dispatch):
    monkeypatch.setattr(sys, "argv", ["rpnh", "package", "preview", "absent.zip"])
    assert rpnh_cli.main() == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "PACKAGE_UNAVAILABLE"
