"""Official npm Web assets: versioned reuse, repair and bounded extraction."""
from __future__ import annotations

import io
import json
import tarfile

import pytest

from cpn.frontend import dsh_assets


NAME = "@deepseek-ai/dsh-test"
VERSION = "0.2.1-rc.2"


def package(*, version=VERSION, name=NAME, extra=None):
    entries = {
        "package/package.json": json.dumps({"name": name, "version": version,
            "exports": {".": {"default": "./lib/index.js"},
                        "./client": {"default": "./lib/client.js"}}}).encode(),
        "package/lib/index.js": b"export function apply() {}",
        "package/lib/client.js": b"/* official client fixture */",
    }
    entries.update(extra or {})
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, body in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    return output.getvalue()


@pytest.fixture
def install(monkeypatch):
    calls = []
    responses = [package()]
    monkeypatch.setattr(dsh_assets, "official_web_packages", lambda: (VERSION, (NAME,)))
    def fetch(url, *, timeout):
        calls.append(url)
        return io.BytesIO(responses[-1])
    monkeypatch.setattr(dsh_assets, "urlopen", fetch)
    return calls, responses


def test_prepares_official_packages_and_reuses_installed_version(tmp_path, install):
    calls, _ = install
    store = tmp_path / "node_modules"
    assert dsh_assets.prepare_official_web_assets(store) == store
    target = store / NAME
    assert (target / "lib/client.js").read_bytes() == b"/* official client fixture */"
    assert dsh_assets.prepare_official_web_assets(store) == store
    assert calls == [f"https://registry.npmjs.org/{NAME}/-/dsh-test-{VERSION}.tgz"]


def test_incomplete_package_is_reinstalled(tmp_path, install):
    calls, _ = install
    store = dsh_assets.prepare_official_web_assets(tmp_path / "node_modules")
    (store / NAME / "lib/client.js").unlink()
    dsh_assets.prepare_official_web_assets(store)
    assert (store / NAME / "lib/client.js").is_file()
    assert len(calls) == 2


def test_missing_official_startup_script_is_reinstalled(tmp_path, install, monkeypatch):
    calls, responses = install
    name = "@deepseek-ai/dsh-web-frontend"
    monkeypatch.setattr(dsh_assets, "official_web_packages", lambda: (VERSION, (name,)))
    responses.append(package(name=name, extra={
        "package/dist/index.html": b'<script type="module" src="/assets/main.js"></script><link rel="stylesheet" href="/assets/main.css">',
        "package/dist/assets/main.js": b"/* native startup */",
        "package/dist/assets/main.css": b"body {margin:0}",
    }))
    store = dsh_assets.prepare_official_web_assets(tmp_path / "node_modules")
    (store / name / "dist/assets/main.js").unlink()
    dsh_assets.prepare_official_web_assets(store)
    assert (store / name / "dist/assets/main.js").is_file()
    assert len(calls) == 2


def test_wrong_download_version_preserves_previous_package(tmp_path, install):
    _, responses = install
    store = dsh_assets.prepare_official_web_assets(tmp_path / "node_modules")
    metadata = store / NAME / "package.json"
    old = json.loads(metadata.read_text())
    old["version"] = "older-version"
    metadata.write_text(json.dumps(old))
    responses.append(package(version="another-version"))
    with pytest.raises(RuntimeError, match="wrong version"):
        dsh_assets.prepare_official_web_assets(store)
    assert json.loads(metadata.read_text())["version"] == "older-version"


def test_archive_cannot_write_outside_its_package(tmp_path, install):
    _, responses = install
    responses.append(package(extra={"package/../../outside.txt": b"must not escape"}))
    with pytest.raises(RuntimeError, match="Unexpected path"):
        dsh_assets.prepare_official_web_assets(tmp_path / "node_modules")
    assert not list(tmp_path.rglob("outside.txt"))


def test_symlinked_package_is_not_replaced(tmp_path, install):
    calls, _ = install
    other = tmp_path / "existing-user-files"
    other.mkdir()
    store = tmp_path / "node_modules"
    target = store / NAME
    target.parent.mkdir(parents=True)
    target.symlink_to(other, target_is_directory=True)
    with pytest.raises(RuntimeError, match="escapes"):
        dsh_assets.prepare_official_web_assets(store)
    assert other.is_dir() and target.is_symlink()
    assert calls == []
