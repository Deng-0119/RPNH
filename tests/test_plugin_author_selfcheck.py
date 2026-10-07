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
