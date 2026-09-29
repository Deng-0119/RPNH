"""Viewer packaging and read-only HTTP boundary; no model or plugin execution."""
from pathlib import Path
import json
from zipfile import ZipFile
import pytest
from cpn.frontend import server
from scripts.check_net_viewer_wheel import (
    REQUIRED_STATIC_FILES,
    REQUIRED_VENDOR_FILES,
    check_wheel,
    validate_asset_manifest,
)
from test_net_viewer import _projection


@pytest.mark.parametrize(
    "asset",
    [f"/{name}" for name in REQUIRED_STATIC_FILES if name.endswith(".mjs")],
)
def test_viewer_modules_never_read_a_registry(asset):
    def unexpected(): pytest.fail("static asset invoked Registry provider")
    response = server.handle_request(unexpected, "GET", asset)
    assert response.status == 200 and "javascript" in response.headers["Content-Type"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "worker-src 'self'" in response.headers["Content-Security-Policy"]
    assert "script-src 'self';" in response.headers["Content-Security-Policy"]


def test_wheel_audit_covers_every_top_level_served_static_file():
    served_files = {"index.html"} | {
        filename for filename, _content_type in server._ASSETS.values()
        if not filename.startswith("assets/")
    }
    assert set(REQUIRED_STATIC_FILES) == served_files
    assert {"overview.mjs", "wire-geometry.mjs"} <= set(REQUIRED_STATIC_FILES)
    served_vendor_files = {
        filename.removeprefix("assets/")
        for filename, _content_type in server._ASSETS.values()
        if filename.startswith("assets/")
        and filename != "assets/manifest.json"
    }
    assert set(REQUIRED_VENDOR_FILES) == served_vendor_files


@pytest.mark.parametrize("path", ["/assets/../server.py", "/assets/%2e%2e/server.py", "/assets/no-such-file", "/../pyproject.toml"])
def test_asset_routes_are_an_explicit_allowlist(path):
    assert server.handle_request(lambda: pytest.fail("unexpected read"), "GET", path).status == 404


def test_missing_vendor_build_is_an_explicit_service_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_STATIC_ROOT", tmp_path)
    response = server.handle_request(_projection, "GET", "/assets/joint.js")
    assert response.status == 503 and b"asset-complete wheel" in response.body


def test_bootstrap_is_data_not_an_inline_executable_script():
    response = server.handle_request(_projection, "GET", "/", show_resources=True)
    assert b'type="application/json"' in response.body
    assert b'"showResources": true' in response.body
    assert b"window.NET_VIEW_BOOTSTRAP=" not in response.body


def test_vendor_assets_are_optional_for_python_imports_but_checked_when_built():
    # Build completeness is an unconditional wheel/browser release gate,
    # not a requirement for Python-only source checks before assets are built.
    root = Path(server.__file__).with_name("static") / "assets"
    if not root.exists():
        assert server.handle_request(_projection, "GET", "/assets/manifest.json").status == 503
        return
    manifest = json.loads((root / "manifest.json").read_text())
    assert set(validate_asset_manifest(manifest)) == set(REQUIRED_VENDOR_FILES)
    assert {d["name"]: d["version"] for d in manifest["dependencies"]} == {"@joint/core": "4.3.1", "elkjs": "0.12.0"}
    for name in manifest["files"]:
        response = server.handle_request(_projection, "GET", "/assets/" + name)
        assert response.status == 200 and response.body
        assert server.handle_request(_projection, "HEAD", "/assets/" + name).body == b""


def test_wheel_manifest_cannot_hide_missing_vendor_assets(
        tmp_path: Path,
) -> None:
    manifest = {
        "schema_version": "rpnh/viewer_assets/v1",
        "dependencies": [
            {"name": "@joint/core", "version": "4.3.1"},
            {"name": "elkjs", "version": "0.12.0"},
        ],
        "files": [],
    }
    with pytest.raises(
            AssertionError, match="every required vendor file"):
        validate_asset_manifest(manifest)
    wheel_path = tmp_path / "incomplete.whl"
    with ZipFile(wheel_path, "w") as wheel:
        prefix = "cpn/frontend/static/"
        for name in REQUIRED_STATIC_FILES:
            wheel.writestr(prefix + name, b"present")
        wheel.writestr(
            prefix + "assets/manifest.json", json.dumps(manifest))
    with pytest.raises(
            AssertionError, match="every required vendor file"):
        check_wheel(wheel_path)


def test_english_shell_and_language_control_are_local_static_data():
    response = server.handle_request(lambda: pytest.fail("language UI read Registry"), "GET", "/")
    text = response.body.decode("utf8")
    assert '<html lang="en">' in text
    assert 'id="language"' in text
    assert '>English</option>' in text and '>中文</option>' in text
    assert 'Interface language' in text
