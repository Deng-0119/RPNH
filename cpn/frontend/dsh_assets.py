"""Prepare versioned official DSH Web packages inside its dedicated runtime."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import importlib.resources
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from urllib.request import urlopen
from urllib.parse import unquote, urlsplit


def official_web_packages() -> tuple[str, tuple[str, ...]]:
    source = importlib.resources.files("integrations.dsh")
    version = json.loads(source.joinpath("UPSTREAM.json").read_text())["version"]
    packages = json.loads(source.joinpath("frontend/web-packages.json").read_text())["packages"]
    return version, tuple(packages)


def _entry(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("default"), str):
        return value["default"]
    return None


class _StartupAssets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.styles: list[str] = []

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        attrs = dict(attributes)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        elif tag == "link" and attrs.get("href"):
            if set((attrs.get("rel") or "").split()) & {"stylesheet", "modulepreload"}:
                self.styles.append(attrs["href"])


def _web_ready(directory: Path) -> bool:
    dist = (directory / "dist").resolve()
    if not dist.is_relative_to(directory.resolve()):
        return False
    page = _StartupAssets()
    page.feed((dist / "index.html").read_text())
    if not page.scripts:
        return False
    for link in page.scripts + page.styles:
        url = urlsplit(link)
        if url.scheme or url.netloc:
            return False
        asset = (dist / unquote(url.path).lstrip("/")).resolve()
        if not asset.is_relative_to(dist) or not asset.is_file():
            return False
    return True


def _ready(directory: Path, name: str, version: str) -> bool:
    try:
        package = json.loads((directory / "package.json").read_text())
        if package.get("name") != name or package.get("version") != version:
            return False
        if name == "@deepseek-ai/dsh-web-frontend":
            return _web_ready(directory)
        exports = package.get("exports", {})
        required = [_entry(exports.get(key)) for key in (".", "./client", "./remote", "./typert") if key in exports]
        return bool(required) and all(
            path is not None and (directory / path).resolve().is_relative_to(directory.resolve())
            and (directory / path).is_file() for path in required)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _ensure_package(store: Path, name: str, version: str) -> None:
    target = store / name
    if not target.resolve().is_relative_to(store):
        raise RuntimeError("DSH Web package directory escapes its asset store")
    if _ready(target, name, version):
        return
    short = name.split("/")[-1]
    url = f"https://registry.npmjs.org/{name}/-/{short}-{version}.tgz"
    with urlopen(url, timeout=45) as response:
        payload = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rpnh-web-", dir=target.parent) as temporary:
        stage = Path(temporary).resolve()
        package_root = stage / "package"
        package_root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if (not path.parts or path.parts[0] != "package"
                        or ".." in path.parts or member.issym() or member.islnk()):
                    raise RuntimeError("Unexpected path in official DSH package")
                destination = package_root.joinpath(*path.parts[1:])
                if not destination.resolve().is_relative_to(package_root):
                    raise RuntimeError("DSH package member escapes its directory")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as stream:
                        destination.write_bytes(stream.read())
                else:
                    raise RuntimeError("Unsupported official DSH package entry")
        if not _ready(package_root, name, version):
            raise RuntimeError(f"Official DSH package is incomplete or has the wrong version: {name}")
        # Download and validate before replacing an old managed package. Staging
        # beside it prevents an interrupted download from becoming reusable.
        if target.exists():
            if target.is_symlink():
                raise RuntimeError("DSH asset package must be a managed directory")
            shutil.rmtree(target)
        package_root.rename(target)


def prepare_official_web_assets(directory: Path) -> Path:
    """Reuse complete pinned packages; fetch missing packages before UI startup."""
    store = directory.expanduser().resolve()
    store.mkdir(parents=True, exist_ok=True)
    version, packages = official_web_packages()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda name: _ensure_package(store, name, version), packages))
    return store
