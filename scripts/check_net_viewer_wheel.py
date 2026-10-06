"""Reject incomplete distributions before release."""
from pathlib import Path
import json
import sys
from zipfile import ZipFile


REQUIRED_STATIC_FILES = (
    "app.js",
    "style.css",
    "index.html",
    "model.mjs",
    "layout.mjs",
    "renderer.mjs",
    "panels.mjs",
    "dashboard-model.mjs",
    "i18n.mjs",
    "messages.mjs",
    "canvas-text.mjs",
    "overview.mjs",
    "wire-geometry.mjs",
    "checkpoint-view.mjs",
    "agent-members.mjs",
    "observation-panel.mjs",
    "source-observation.mjs",
    "firing-activity.mjs",
    "worksets.mjs",
)
REQUIRED_VENDOR_FILES = (
    "joint.js",
    "joint-LICENSE.txt",
    "elk-api.js",
    "elk-worker.js",
    "elk-LICENSE.txt",
)
REQUIRED_DEPENDENCIES = {
    "@joint/core": "4.3.1",
    "elkjs": "0.12.0",
}


def validate_asset_manifest(manifest):
    if not isinstance(manifest, dict):
        raise AssertionError("viewer asset manifest must be one object")
    if manifest.get("schema_version") != "rpnh/viewer_assets/v1":
        raise AssertionError("viewer asset manifest schema is invalid")
    files = manifest.get("files")
    if (not isinstance(files, list)
            or any(not isinstance(name, str) or not name for name in files)
            or len(files) != len(set(files))
            or set(files) != set(REQUIRED_VENDOR_FILES)):
        raise AssertionError(
            "viewer asset manifest must list every required vendor file exactly once")
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, list):
        raise AssertionError("viewer asset dependencies must be one list")
    try:
        selected = {item["name"]: item["version"] for item in dependencies}
    except (KeyError, TypeError) as exc:
        raise AssertionError("viewer asset dependencies are malformed") from exc
    if len(selected) != len(dependencies) or selected != REQUIRED_DEPENDENCIES:
        raise AssertionError("viewer asset dependencies differ from the pinned set")
    return tuple(files)


def check_wheel(path):
    path = Path(path)
    with ZipFile(path) as wheel:
        prefix = "cpn/frontend/static/"
        for name in REQUIRED_STATIC_FILES:
            assert wheel.read(prefix + name), name
        manifest = json.loads(wheel.read(prefix + "assets/manifest.json"))
        for name in validate_asset_manifest(manifest):
            assert wheel.read(prefix + "assets/" + name), name
    return manifest


def main():
    path = Path(sys.argv[1])
    manifest = check_wheel(path)
    print(json.dumps({"wheel": path.name, "viewer_assets_verified": True,
                      "dependencies": manifest["dependencies"]}, indent=2))


if __name__ == "__main__": main()
