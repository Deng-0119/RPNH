"""Application update discovery; never a Registry authority or an installer.

Only official, published RPNH releases with an exact supported/tested entry can
be offered. Upstream package tags and private/source candidates are not offers.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import tomllib
from urllib.request import HTTPRedirectHandler, Request, build_opener

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

REPOSITORY = "Deng-0119/RPNH"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=20"
RAW_ROOT = f"https://raw.githubusercontent.com/{REPOSITORY}"
ENTRIES = ("basic", "opencode", "dsh-web", "dsh-legacy", "dsh-operator")
CHANNELS = ("stable", "prerelease")
SCHEMA = "rpnh/update_state/v1"
TTL = 24 * 60 * 60
MAX_STALE = 7 * TTL
CHECKS = {"startup", "upgrade", "rollback", "offline"}
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?")


@dataclass(frozen=True)
class Offer:
    entry: str
    tag: str
    rpnh_version: str
    upstream_version: str
    upstream_revision: str | None
    channel: str
    wheel_url: str
    evidence: str

    @property
    def key(self) -> str:
        return f"{self.entry}:{self.channel}:{self.tag}"


def current_version() -> str:
    # Editable/source candidates must not inherit another installed wheel's
    # version simply because that wheel has the same distribution name.
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if project.is_file():
        return str(tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"])
    try:
        return importlib.metadata.version("rpnh-harness")
    except importlib.metadata.PackageNotFoundError:
        raise ValueError("cannot identify installed RPNH version") from None


def state_path() -> Path:
    from .user_config import config_path
    path = config_path()
    return path.with_name(path.name + ".updates.json")


def _initial() -> dict:
    return {"schema_version": SCHEMA, "channel": "stable", "skipped": [],
            "later": {}, "last_attempt": 0, "checked_at": 0, "offers": []}


def _read(path: Path) -> dict:
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError("update state exceeds limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _initial()
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA
            or value.get("channel") not in CHANNELS
            or not isinstance(value.get("skipped"), list)
            or not all(isinstance(v, str) and len(v) < 160 for v in value["skipped"])
            or not isinstance(value.get("later"), dict)
            or not all(isinstance(k, str) and isinstance(v, (int, float))
                       and not isinstance(v, bool) and 0 <= v < 10**12
                       for k, v in value["later"].items())
            or not isinstance(value.get("offers"), list)
            or not all(type(value.get(k)) in (int, float) and 0 <= value[k] < 10**12
                       for k in ("last_attempt", "checked_at"))):
        raise ValueError("update state is invalid; use explicit reset")
    return value


def read_state(path: Path | None = None) -> dict:
    return _read(path or state_path())


@contextmanager
def _state_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_suffix(path.suffix + ".lock"),
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def mutate_state(fn, path: Path | None = None) -> dict:
    destination = path or state_path()
    with _state_lock(destination):
        value = _read(destination)
        fn(value)
        content = json.dumps(value, allow_nan=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                dir=destination.parent, prefix=".rpnh-update-", delete=False) as stream:
            name = Path(stream.name)
            try:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                name.unlink(missing_ok=True)
                raise
        try:
            os.replace(name, destination)
        finally:
            name.unlink(missing_ok=True)
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("update metadata redirect rejected")


def get_json(url: str):
    # Callers construct these URLs. No user token, release-body URL, auth
    # header, executable instruction or remote command is accepted.
    if not (url == RELEASES_URL or url.startswith(RAW_ROOT + "/v")):
        raise ValueError("untrusted update metadata source")
    request = Request(url, headers={"Accept": "application/json",
                                   "User-Agent": "RPNH-update-check/1"})
    with build_opener(_NoRedirect()).open(request, timeout=2.0) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("update metadata exceeds limit")
    return json.loads(data)


def _version(value: object) -> Version:
    if not isinstance(value, str) or VERSION_RE.fullmatch(value) is None:
        raise ValueError("unsupported release version")
    result = Version(value)
    if str(result) != value:
        raise ValueError("noncanonical release version")
    return result


def _upstream(entry: str, value: object, channel: str) -> tuple[str, str | None]:
    if not isinstance(value, dict) or set(value) != {"version", "revision"}:
        raise ValueError("invalid upstream identity")
    version, revision = value["version"], value["revision"]
    if entry == "basic":
        if version != "builtin" or revision is not None:
            raise ValueError("invalid builtin identity")
    else:
        if not isinstance(version, str) or not re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)\.[0-9]+)?", version):
            raise ValueError("upstream must be an exact supported version")
        if channel == "stable" and "-" in version:
            raise ValueError("prerelease upstream requires prerelease channel")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("upstream must name its exact commit")
    return version, revision


def discover(*, channel: str, installed: str | None, fetch=get_json,
             now: float | None = None) -> list[Offer]:
    """Read official release declarations plus exact recorded test evidence.

    The trust boundary is the maintainer's published release, not a claim that
    this client independently reruns native validation or authenticates hashes.
    """
    if channel not in CHANNELS:
        raise ValueError("unknown update channel")
    current = Version(installed) if installed is not None else None
    now = time.time() if now is None else now
    started = time.monotonic()
    transport = fetch
    def bounded_fetch(url):
        if time.monotonic() - started > 6:
            raise OSError("update metadata time budget exhausted")
        return transport(url)
    fetch = bounded_fetch
    releases = fetch(RELEASES_URL)
    if not isinstance(releases, list) or len(releases) > 20:
        raise ValueError("invalid release list")
    candidates = []
    for release in releases:
        try:
            tag = release["tag_name"]
            version = _version(tag[1:] if isinstance(tag, str) and tag.startswith("v") else None)
            published = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
            if (release["draft"] is not False or type(release["id"]) is not int
                    or release["id"] <= 0 or type(release["prerelease"]) is not bool
                    or published.tzinfo is None or published.timestamp() > now
                    or release["html_url"] != f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
                    or channel == "stable" and (release["prerelease"] or version.is_prerelease)
                    or version.is_prerelease and not release["prerelease"]
                    or current is not None and version <= current):
                continue
            wheel = f"rpnh_harness-{version}-py3-none-any.whl"
            wheel_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{wheel}"
            assets = release["assets"]
            if not isinstance(assets, list) or not any(
                    isinstance(asset, dict) and asset.get("name") == wheel
                    and asset.get("browser_download_url") == wheel_url
                    and asset.get("state") == "uploaded" for asset in assets):
                continue
            candidates.append((version, tag, wheel_url, release["prerelease"]))
        except (KeyError, TypeError, ValueError, InvalidVersion, AttributeError):
            continue
    offers = []
    # A finite metadata read budget, independent of upstream release volume.
    for version, tag, wheel_url, prerelease in sorted(candidates, reverse=True)[:5]:
        try:
            support = fetch(f"{RAW_ROOT}/{tag}/cpn/frontend/update_support.v1.json")
            if (support["schema_version"] != "rpnh/update_support/v1"
                    or support["status"] != "released"
                    or support["release"] != tag or support["rpnh_version"] != str(version)
                    or support["channel"] != ("prerelease" if prerelease else "stable")
                    or support["manager_protocol"] != "managed-side-by-side-v1"
                    or not isinstance(support["entries"], dict)):
                continue
            for entry, declaration in support["entries"].items():
                if entry not in ENTRIES:
                    continue
                try:
                    if (declaration["status"] != "supported"
                            or declaration["data_policy"] != "no-migration"
                            or declaration["rollback_data_compatible"] is not True
                            or declaration["platforms"] != ["linux"]
                            or not sys.platform.startswith("linux")
                            or Version(".".join(map(str, sys.version_info[:3])))
                            not in SpecifierSet(declaration["python"])):
                        continue
                    upstream, revision = _upstream(entry, declaration["upstream"], channel)
                    evidence_path = declaration["evidence"]
                    if not isinstance(evidence_path, str) or not re.fullmatch(
                            r"docs/validation/[a-zA-Z0-9_-]+\.json", evidence_path):
                        continue
                    evidence = fetch(f"{RAW_ROOT}/{tag}/{evidence_path}")
                    if (evidence["schema_version"] != "rpnh/update_validation/v1"
                            or evidence["release"] != tag or evidence["entry"] != entry
                            or evidence["rpnh_version"] != str(version)
                            or evidence["upstream"] != declaration["upstream"]
                            or evidence["result"] != "passed"
                            or not all(evidence["checks"].get(check) == "passed" for check in CHECKS)):
                        continue
                    offers.append(Offer(entry, tag, str(version), upstream, revision,
                                        channel, wheel_url, evidence_path))
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
        except (KeyError, TypeError, ValueError, AttributeError, OSError):
            continue
    return offers


def _cached_offer(value: dict, entry: str, installed: str, now: float) -> Offer | None:
    if not 0 <= now - value["checked_at"] <= MAX_STALE:
        return None
    valid = []
    for record in value["offers"]:
        try:
            offer = Offer(**record)
            if (offer.entry != entry or offer.channel != value["channel"]
                    or offer.tag != "v" + offer.rpnh_version
                    or _version(offer.rpnh_version) <= Version(installed)
                    or offer.channel == "stable" and _version(offer.rpnh_version).is_prerelease
                    or offer.key in value["skipped"]
                    or value["later"].get(offer.key, 0) > now):
                continue
            _upstream(entry, {"version": offer.upstream_version,
                              "revision": offer.upstream_revision}, offer.channel)
            if offer.wheel_url != (f"https://github.com/{REPOSITORY}/releases/download/"
                    f"{offer.tag}/rpnh_harness-{offer.rpnh_version}-py3-none-any.whl"):
                continue
            valid.append(offer)
        except (ValueError, TypeError, InvalidVersion):
            continue
    return max(valid, key=lambda o: Version(o.rpnh_version), default=None)


def refresh(*, path: Path | None = None, installed: str | None = None,
            fetch=get_json, now: float | None = None) -> list[Offer]:
    now = time.time() if now is None else now
    value = mutate_state(lambda s: s.update(last_attempt=now), path)
    offers = discover(channel=value["channel"], installed=installed or current_version(),
                      fetch=fetch, now=now)
    # Reload under lock: never overwrite a skip/channel choice made in flight.
    def save(state):
        if state["channel"] == value["channel"]:
            state.update(checked_at=now, offers=[asdict(o) for o in offers])
    mutate_state(save, path)
    return offers


def automatic_refresh_sync(*, path: Path | None = None, installed: str | None = None,
                           fetch=get_json, now: float | None = None) -> None:
    """Bounded check for a short-lived native UI bridge, honoring off and TTL."""
    if os.environ.get("RPNH_UPDATES", "on") == "off":
        return
    now = time.time() if now is None else now
    if 0 <= now - read_state(path)["last_attempt"] < TTL:
        return
    def reserve(value):
        if 0 <= now - value["last_attempt"] < TTL:
            raise BlockingIOError("another update check already reserved")
        value["last_attempt"] = now
    mutate_state(reserve, path)
    refresh(path=path, installed=installed, fetch=fetch, now=now)


def background_refresh(*, path: Path | None = None, installed: str | None = None,
                       now: float | None = None) -> None:
    now = time.time() if now is None else now
    value = read_state(path)
    if 0 <= now - value["last_attempt"] < TTL:
        return
    # Reserve before starting to coalesce repeated entry points/processes.
    def reserve(state):
        if 0 <= now - state["last_attempt"] < TTL:
            raise BlockingIOError("another update check already reserved")
        state["last_attempt"] = now
    mutate_state(reserve, path)
    def work():
        try:
            refresh(path=path, installed=installed, now=now)
        except (OSError, ValueError, TypeError, InvalidVersion):
            pass  # No exception strings, proxy URLs, or credentials in UI/logs.
    threading.Thread(target=work, name="rpnh-update-check", daemon=True).start()


def status(entry: str, *, path: Path | None = None, installed: str | None = None,
           automatic: bool = False, now: float | None = None) -> Offer | None:
    if entry not in ENTRIES:
        return None
    now = time.time() if now is None else now
    installed = installed or current_version()
    value = read_state(path)
    if automatic and os.environ.get("RPNH_UPDATES", "on") != "off":
        background_refresh(path=path, installed=installed, now=now)
    return _cached_offer(value, entry, installed, now)


def record_choice(offer: Offer, choice: str, *, path: Path | None = None,
                  now: float | None = None) -> None:
    now = time.time() if now is None else now
    if choice not in ("later", "skip"):
        raise ValueError("only preference choices may be persisted")
    def change(value):
        if choice == "later":
            value["later"][offer.key] = now + TTL
        elif offer.key not in value["skipped"]:
            value["skipped"].append(offer.key)
        # Bound user-local history without suppressing a different new version.
        value["skipped"] = value["skipped"][-128:]
        value["later"] = {k: v for k, v in value["later"].items() if v > now}
    mutate_state(change, path)


def startup(entry: str, *, interactive: bool, input_fn=None, output_fn=None) -> bool:
    """Return True only after successful activation, so caller exits/restarts.

    No cache/network/preference failure may prevent use of the installed app.
    """
    if (not interactive or entry not in ENTRIES
            or os.environ.get("RPNH_UPDATES", "on") == "off"):
        return False
    write, read = output_fn or print, input_fn or input
    try:
        offer = status(entry, automatic=True)
    except (OSError, RuntimeError, ValueError, TypeError, InvalidVersion):
        return False  # Offline/corrupt/busy cache is not a startup warning or failure.
    if offer is None:
        return False
    try:
        from .update_install import managed_root, install_managed
        root = managed_root()
        automatic_install = root is not None and entry in ("basic", "opencode", "dsh-web")
        write(f"RPNH {current_version()} -> {offer.rpnh_version} [{offer.channel}], "
              f"{entry}: {offer.upstream_version}.")
        write("1 Update (new isolated environment; restart required)" if automatic_install else
              "1 Update instructions (this installation is manually managed; no automatic install)")
        write("2 Not now (remind after 24 hours)\n3 Skip this RPNH version for this entry/channel")
        try:
            answer = read("RPNH update [2]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "2"
        if answer in ("3", "skip"):
            record_choice(offer, "skip")
        elif answer in ("1", "update"):
            if not automatic_install:
                write(manual_guidance(offer))
                record_choice(offer, "later")
            else:
                install_managed(offer, root=root)
                write(f"Update prepared and activated. Restart with {root / 'bin/rpnh'}. "
                      "The previous environment is retained; tasks were not resumed.")
                return True
        else:
            record_choice(offer, "later")
    except (OSError, RuntimeError, ValueError, TypeError, InvalidVersion):
        write("RPNH update unavailable; the existing installation is unchanged. "
              "Use `rpnh update status` for safe instructions.")
    return False


def manual_guidance(offer: Offer) -> str:
    if offer.entry == "dsh-operator":
        return ("Manual stock DSH plugin update only. Stop/checkpoint affected tasks and "
                "exit the affected DSH host; retain configurations and consistent private backups. "
                f"Review https://github.com/{REPOSITORY}/releases/tag/{offer.tag} "
                "for the exact matching operator tarball and RPNH Python companion. "
                "Follow that release's exact-version package instructions. If the matching pair "
                "or its instructions are absent, do not update. Do not overwrite dirty sources "
                "or mix package managers. This notice does not install packages, hot-upgrade "
                "the plugin, migrate data, restart workers or resume tasks.")
    return (f"Manual update required for {offer.entry}. Stop/checkpoint tasks and retain "
            "consistent private backups. Review the official release "
            f"https://github.com/{REPOSITORY}/releases/tag/{offer.tag}. "
            "Do not overwrite a source/dirty checkout or mix package managers. "
            "For a new dedicated managed Basic/OpenCode/DSH Web install, use "
            "`rpnh update install --root NEW_EMPTY_DIRECTORY --frontend NAME "
            f"--version {offer.rpnh_version}`; it revalidates release support and asks confirmation. "
            "Stock DSH plugins and legacy hosts must use their documented exact-version "
            "package/source procedure; this command does not hot-upgrade them.")

