"""Portable author entries and inert catalog observations; no provider calls."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import zipfile

import pytest

from cpn.plugins import PluginDefinition, PluginOperation, PluginResource, PluginError
from cpn.plugins.catalog import inspect_plugins, inspect_plugin_wheel, load_catalog

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/native_plugin"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handler(context, arguments):
    return {"value": 5}


def definition():
    return PluginDefinition("demo", "0.3.0", (
        PluginOperation("add", "fixture", {"type": "object"}, {"type": "object"}, handler),),
        resources=(PluginResource("instruction", b"Exact author resource"),))


class Distribution:
    metadata = {"Name": "rpnh-native-demo"}
    version = "0.3.0"
    files = [Path(__file__).name]

    def locate_file(self, name):
        return Path(__file__).parent / name

    def read_text(self, name):
        return (EXAMPLE / name).read_text()


def entrypoint(factory=definition, *, dist=None):
    return SimpleNamespace(name="demo", value="rpnh_demo:plugin", module=__name__,
                           dist=dist or Distribution(), load=lambda: factory)


def selection():
    return json.loads((EXAMPLE / "plugins.json").read_text())


def test_inert_missing_metadata_does_not_import_even_selected_factory(monkeypatch):
    ep = entrypoint()
    ep.dist.read_text = lambda name: None
    ep.load = lambda: pytest.fail("inert inspection loaded a factory")
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [ep])
    row, = inspect_plugins(selection())["plugins"]
    assert row["inert_metadata"]["status"] == "unknown"
    assert row["selection"]["status"] == "selected"
    assert row["factory_loaded"] == row["host_bound"] == "not_checked"
    assert row["installed_distribution"]["wheel_sha256"] is None


def test_inert_invalid_and_ambiguous_metadata_remain_unverified(monkeypatch):
    ep = entrypoint()
    ep.dist.read_text = lambda name: "{}"
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [ep, ep])
    rows = inspect_plugins(selection())["plugins"]
    assert all(row["inert_metadata"]["status"] == "invalid" for row in rows)
    assert all(row["selection"]["entry_point_resolution"] == "ambiguous" for row in rows)


def test_selected_missing_and_installed_unselected_are_distinct(monkeypatch):
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [])
    row, = inspect_plugins(selection())["plugins"]
    assert row["installed_distribution"] is None
    assert row["selection"]["entry_point_resolution"] == "missing"
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [entrypoint()])
    row, = inspect_plugins()["plugins"]
    assert row["selection"]["status"] == "not_selected"
    assert row["inert_metadata"]["status"] == "declared"


@pytest.mark.parametrize("bad", ["", "1/2", "has space", None])
def test_existing_selected_version_validation_remains_before_import(monkeypatch, bad):
    doc = selection()
    doc["plugins"][0]["version"] = bad
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: pytest.fail("early import/discovery"))
    for call in (inspect_plugins, load_catalog):
        with pytest.raises(PluginError, match="version"):
            call(doc)


@pytest.mark.parametrize("inert", [True, False])
def test_explicit_wheel_metadata_is_not_installed_or_loaded(tmp_path, inert):
    wheel = tmp_path / "demo.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo.dist-info/METADATA", "Name: rpnh-native-demo\nVersion: 0.3.0\n")
        archive.writestr("demo.dist-info/entry_points.txt", "[rpnh.plugins]\ndemo = poisonous:plugin\n")
        archive.writestr("poisonous.py", "raise AssertionError('must not import')")
        if inert:
            archive.writestr("demo.dist-info/rpnh_environment_plugins.json", (EXAMPLE / "rpnh_environment_plugins.json").read_bytes())
    report = inspect_plugin_wheel(wheel)
    assert report["inert_metadata"]["status"] == ("declared" if inert else "unknown")
    assert report["installed"] == report["factory_loaded"] == report["host_bound"] == "not_checked"
    assert "poisonous" not in sys.modules


def test_environment_selected_version_is_configuration_not_factory_evidence(tmp_path, monkeypatch):
    from cpn.rpnh.collaboration.environment_host import _native_plugin_metadata
    config = tmp_path / "plugins.json"
    config.write_text(json.dumps(selection()))
    ep = entrypoint()
    ep.module = Path(__file__).stem
    ep.load = lambda: pytest.fail("preflight must not load")
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [ep])
    result = _native_plugin_metadata(requirement={"plugin_id": "demo", "version_specifier": "==0.3.0",
            "api_contract": "rpnh/plugin/v1"}, binding={"configuration_ref": str(config)}, interpreter=sys.executable)
    assert result["status"] == "satisfied"
    assert result["evidence_level"] == "configuration_observed"
    assert result["reason_code"] == "PLUGIN_SELECTION_OBSERVED"


def source_copy(tmp_path):
    for name in ("pyproject.toml", "rpnh_environment_plugins.json", "plugins.json"):
        (tmp_path / name).write_bytes((EXAMPLE / name).read_bytes())
    (tmp_path / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    return tmp_path


def test_public_author_declaration_loads_trusted_factory_and_complete_descriptor(tmp_path, monkeypatch):
    source_copy(tmp_path)
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [entrypoint()])
    report = load_script("selfcheck_declaration").check(tmp_path, tmp_path / "plugins.json")
    assert report["status"] == "PASS", report
    assert report["factory_loaded"] == "verified" and report["host_bound"] == "not_checked"
    descriptor = report["descriptors"]["demo"]
    assert descriptor["resources"][0]["sha256"]
    assert descriptor["operations"][0]["implementation"]["identity"].endswith(".handler")
    assert descriptor["operations"][0]["effect"] == "pure"


@pytest.mark.parametrize("damage,needle", [("pyproject.toml", "pyproject.toml"),
    ("rpnh_environment_plugins.json", "inert metadata"), ("factory", "PluginDefinition"),
    ("plugins.json", "plugins.json"), ("import_source", "outside installed wheel"), ("source_bytes", "source factory module")])
def test_author_locates_identity_conflicts(tmp_path, monkeypatch, damage, needle):
    source_copy(tmp_path)
    ep = entrypoint()
    if damage == "factory":
        ep.load = lambda: lambda: PluginDefinition("demo", "9.0", definition().operations)
    elif damage == "import_source":
        ep.dist.files = []
    elif damage == "source_bytes":
        (tmp_path / Path(__file__).name).write_text("# stale author source\n")
    else:
        path = tmp_path / damage
        path.write_text(path.read_text().replace("0.3.0", "9.0"))
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [ep])
    report = load_script("selfcheck_declaration").check(tmp_path, tmp_path / "plugins.json")
    assert report["status"] == "FAIL"
    assert any(needle in error for error in report["errors"]), report


def test_descriptor_diff_reports_schema_effect_dependency_resource_and_handler():
    before = definition().descriptor()
    after = json.loads(json.dumps(before))
    after["version"] = "0.4.0"
    after["requires"] = {"dependency": "1.0"}
    after["resources"][0]["sha256"] = "a" * 64
    after["operations"][0].update(effect="external_read", input_schema={"type": "string"})
    after["operations"][0]["implementation"]["identity"] = "other.handler"
    changes = load_script("selfcheck_declaration").descriptor_diff(before, after)
    assert {row["path"] for row in changes} == {"/version", "/requires/dependency", "/resources", "/operations"}


def test_export_closure_contains_both_entries_without_private_test_imports(tmp_path, monkeypatch):
    from cpn.examples import gallery
    # Scope only this example; no writes to shared fixtures or other examples.
    monkeypatch.setattr(gallery, "_assets", lambda: ROOT)
    _, copied = gallery.export_files("native_plugin")
    for filename in ("selfcheck_declaration.py", "selfcheck_terminal.py", "expected-output.json", "AUTHOR_VERSIONS.md", "AUTHOR_VERSIONS_ZH.md"):
        assert "examples/native_plugin/" + filename in copied
    for filename in ("selfcheck_declaration.py", "selfcheck_terminal.py"):
        source = copied["examples/native_plugin/" + filename].decode()
        assert "_core" not in source and "from test_" not in source and "import test_" not in source


def test_terminal_selfcheck_rejects_nonpure_before_runtime(tmp_path, monkeypatch):
    script = load_script("selfcheck_terminal")
    from cpn.plugins import BoundPlugin, PluginCatalog
    op = definition().operations[0]
    catalog = PluginCatalog([BoundPlugin(PluginDefinition("demo", "0.3.0", (
        PluginOperation(op.name, op.description, op.input_schema, op.output_schema, handler, effect="external_write"),)), {})])
    monkeypatch.setattr(script, "load_catalog", lambda _: catalog)
    monkeypatch.setattr(script, "run_plugin", lambda *a, **k: pytest.fail("nonpure runtime"))
    with pytest.raises(PluginError, match="only a declared pure"):
        script.main(["--run-dir", str(tmp_path / "run"), "--result", str(tmp_path / "result.json")])


def test_empty_catalog_keeps_no_discovery_behavior(monkeypatch):
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: pytest.fail("empty load discovers plugins"))
    assert load_catalog().plugins == ()


@pytest.mark.parametrize("damage,status,reason,fields", [
    ("none", "matched", "selected_declaration_match", []),
    ("version", "mismatch", "selected_declaration_mismatch", ["version"]),
    ("version_exact", "mismatch", "selected_declaration_mismatch", ["version"]),
    ("api", "mismatch", "selected_declaration_mismatch", ["api_contract"]),
    ("version_api", "mismatch", "selected_declaration_mismatch", ["version", "api_contract"]),
    ("plugin", "not_checked", "selected_plugin_not_declared", []),
    ("entry_point", "not_checked", "entry_point_not_declared", []),
    ("missing_metadata", "not_checked", "inert_metadata_missing", []),
    ("invalid_metadata", "not_checked", "inert_metadata_invalid", []),
    ("missing_install", "not_checked", "entry_point_missing", []),
    ("ambiguous_install", "not_checked", "entry_point_ambiguous", []),
])
def test_selection_diagnostic_exact_inert_states(monkeypatch, damage, status, reason, fields):
    from cpn.plugins import catalog as catalog_module
    declaration = {"plugin_id": "demo", "version": "0.3.0",
                   "api_contract": "rpnh/plugin/v1", "entry_point": "demo"}
    if damage in {"version", "version_api"}:
        declaration["version"] = "0.4.0"
    if damage == "version_exact":
        declaration["version"] = "0.3"
    if damage in {"api", "version_api"}:
        declaration["api_contract"] = "rpnh/plugin/v2"
    if damage == "plugin":
        declaration["plugin_id"] = "other"
    if damage == "entry_point":
        declaration["entry_point"] = "other"
    raw = json.dumps({"schema_version": "rpnh/installed_plugin_metadata/v1",
                      "plugins": [declaration]})
    if damage == "missing_metadata":
        raw = None
    elif damage == "invalid_metadata":
        raw = "{}"
    ep = entrypoint()
    ep.dist.version = "2026.1"  # A distribution version is not a plugin version.
    reads, parses = [], []
    def read_text(name):
        reads.append(name)
        return raw
    ep.dist.read_text = read_text
    parse_metadata = catalog_module.read_plugin_metadata
    def parse(text):
        parses.append(text)
        return parse_metadata(text)
    monkeypatch.setattr(catalog_module, "read_plugin_metadata", parse)
    ep.load = lambda: pytest.fail("inert diagnostic loaded a factory")
    installed = [] if damage == "missing_install" else [ep, ep] if damage == "ambiguous_install" else [ep]
    discovery = []
    def find(**kwargs):
        discovery.append(kwargs)
        return installed
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", find)
    doc = selection()
    doc["plugins"][0]["config"] = {"sentinel": "PRIVATE_CONFIG_SENTINEL"}
    doc["plugins"][0]["environment"] = ["PRIVATE_ENV_SENTINEL"]
    report = inspect_plugins(doc, diagnose_selection=True)
    assert report["selection_diagnostics"] == {
        "evidence_scope": "inert_metadata_only", "expected_api_contract": "rpnh/plugin/v1",
        "checks": [{"entry_point": "demo", "name": "demo", "version": "0.3.0",
                    "status": status, "reason": reason, "mismatched_fields": fields}],
    }
    assert discovery == [{"group": "rpnh.plugins"}]
    assert reads == ["rpnh_environment_plugins.json"] * len(installed)
    assert parses == [raw] * len(installed)
    assert all(row["factory_loaded"] == row["host_bound"] == "not_checked" for row in report["plugins"])
    assert "PRIVATE_CONFIG_SENTINEL" not in json.dumps(report)
    assert "PRIVATE_ENV_SENTINEL" not in json.dumps(report)


def test_selection_diagnostic_preserves_default_and_selection_order(monkeypatch):
    ep = entrypoint()
    ep.load = lambda: pytest.fail("inert diagnostic loaded a factory")
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [ep])
    doc = selection()
    original = inspect_plugins(doc)
    assert "selection_diagnostics" not in original
    assert inspect_plugins(doc, diagnose_selection=False) == original
    diagnosed = inspect_plugins(doc, diagnose_selection=True)
    assert diagnosed.pop("selection_diagnostics")["checks"][0]["status"] == "matched"
    assert diagnosed == original
    doc["plugins"].insert(0, {**doc["plugins"][0], "name": "other"})
    checks = inspect_plugins(doc, diagnose_selection=True)["selection_diagnostics"]["checks"]
    assert [(row["name"], row["reason"]) for row in checks] == [
        ("other", "selected_plugin_not_declared"), ("demo", "selected_declaration_match")]
    assert inspect_plugins(diagnose_selection=True)["selection_diagnostics"]["checks"] == []


def test_selection_diagnostic_rejects_nonbool_before_discovery(monkeypatch):
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points",
                        lambda **_: pytest.fail("invalid bool reached discovery"))
    for invalid in (1, None, "false", []):
        with pytest.raises(TypeError, match="diagnose_selection"):
            inspect_plugins(selection(), diagnose_selection=invalid)


def test_selection_diagnostic_cli_stays_readonly(tmp_path, monkeypatch, capsys):
    import socket
    import sqlite3
    from cpn.plugins import cli
    from cpn.rpnh import compiler
    from cpn.rpnh.registry._registry import _RegistryCore
    def forbidden(*args, **kwargs):
        pytest.fail("inspection crossed a factory, execution, Registry or network boundary")
    ep = entrypoint()
    ep.load = forbidden
    monkeypatch.setattr("cpn.plugins.catalog.metadata.entry_points", lambda **_: [ep])
    monkeypatch.setattr(cli, "load_catalog", forbidden)
    monkeypatch.setattr(cli, "run_plugin", forbidden)
    monkeypatch.setattr(compiler, "compile_module", forbidden)
    monkeypatch.setattr(_RegistryCore, "__init__", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    doc = selection()
    doc["plugins"][0]["version"] = "9.0"
    config = tmp_path / "plugins.json"
    config.write_text(json.dumps(doc))
    assert cli.main(["--config", str(config), "inspect"]) == 0
    original = json.loads(capsys.readouterr().out)
    assert "selection_diagnostics" not in original
    assert cli.main(["--config", str(config), "inspect", "--diagnose-selection"]) == 0
    diagnosed = json.loads(capsys.readouterr().out)
    assert diagnosed.pop("selection_diagnostics")["checks"][0]["status"] == "mismatch"
    assert diagnosed == original
    with pytest.raises(SystemExit) as error:
        cli.main(["--config", str(config), "list", "--diagnose-selection"])
    assert error.value.code == 2
    assert "unrecognized arguments: --diagnose-selection" in capsys.readouterr().err
