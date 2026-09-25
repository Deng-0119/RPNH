"""Reject incomplete distributions before release."""
from pathlib import Path
import json
import sys
from zipfile import ZipFile


def main():
    path = Path(sys.argv[1])
    with ZipFile(path) as wheel:
        prefix = "cpn/frontend/static/"
        for name in ("app.js", "model.mjs", "dashboard-model.mjs", "layout.mjs", "renderer.mjs", "panels.mjs", "i18n.mjs", "messages.mjs", "canvas-text.mjs", "index.html", "style.css"):
            assert wheel.read(prefix + name), name
        manifest = json.loads(wheel.read(prefix + "assets/manifest.json"))
        for name in manifest["files"]:
            assert wheel.read(prefix + "assets/" + name), name
    print(json.dumps({"wheel": path.name, "viewer_assets_verified": True,
                      "dependencies": manifest["dependencies"]}, indent=2))


if __name__ == "__main__": main()
