"""Pure managed-v2 software records and complete frontend selection plans.

These records do not authorize installation, certify a platform, or describe
Registry execution. Published origins require the release resolver's checks;
local candidates require explicit selection. Filesystem/runtime health and
frozen dependency materials are separate from structural validation here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from email.parser import BytesParser
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib
from typing import Mapping
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version


V2_PROTOCOL = "managed-side-by-side-v2"
INSTALLED_SCHEMA = "rpnh/installed/v2"
MANAGED_SCHEMA = "rpnh/managed/v2"
PLAN_SCHEMA = "rpnh/install_plan/v2"
FRONTEND_SCHEMA = "rpnh/frontend_preferences/v2"
UPDATE_SUPPORT_SCHEMA = "rpnh/update_support/v2"
UPDATE_VALIDATION_SCHEMA = "rpnh/update_validation/v2"
UPDATE_SUPPORT_V3_SCHEMA = "rpnh/update_support/v3"
UPDATE_VALIDATION_V3_SCHEMA = "rpnh/update_validation/v3"
UPDATE_SUPPORT_V4_SCHEMA = "rpnh/update_support/v4"
UPDATE_VALIDATION_V4_SCHEMA = "rpnh/update_validation/v4"
PRIMARY_ENTRIES = ("basic", "codex", "opencode", "dsh-web")
ENTRY_FRONTEND = {"basic": "basic", "codex": "codex", "opencode": "opencode", "dsh-web": "dsh"}
INSTALLATION_ID_RE = re.compile(r"i-[0-9a-f]{32}")
TARGET_RE = re.compile(r"versions/i-[0-9a-f]{32}")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.dev[0-9]+)?")
_RUNTIME_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+|[-+][A-Za-z0-9.-]+)?")
_NPM_PACKAGE_RE = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*")


def _mapping(value, keys, label: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"invalid {label} fields")
    return dict(value)


def _text(value, label: str, limit: int = 4096) -> str:
    if (not isinstance(value, str) or not value or len(value) > limit
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f"invalid {label}")
    return value


def _enum(value, choices, label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"invalid {label}")
    return value


def _version(value) -> str:
    value = _text(value, "RPNH version", 80)
    if _VERSION_RE.fullmatch(value) is None:
        raise ValueError("RPNH version must be an exact supported PEP 440 version")
    try:
        if str(Version(value)) != value:
            raise ValueError("RPNH version must be canonical")
    except InvalidVersion as exc:
        raise ValueError("invalid RPNH version") from exc
    return value


def installation_id(value: str) -> str:
    if not isinstance(value, str) or INSTALLATION_ID_RE.fullmatch(value) is None:
        raise ValueError("invalid installation identity")
    return value


def installation_target(identity: str) -> str:
    return "versions/" + installation_id(identity)


def _target(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or TARGET_RE.fullmatch(value) is None:
        raise ValueError("invalid installation target")
    return value


def _absolute(value, label: str) -> str:
    value = _text(value, label)
    path = PurePosixPath(value)
    if (not path.is_absolute() or path.as_posix() != value
            or ".." in path.parts or "\\" in value or value.startswith("//")):
        raise ValueError(f"{label} must be a normalized absolute POSIX path")
    return value


def _relative(value, label: str) -> str:
    value = _text(value, label)
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or value == "."
            or ".." in path.parts or "\\" in value):
        raise ValueError(f"{label} must remain relative to its installation")
    return value


def validate_source(document) -> dict:
    """Validate a source description, without granting release qualification."""
    value = _mapping(document, ("kind", "rpnh_version", "release", "artifact", "channel"), "source")
    version = _version(value["rpnh_version"])
    artifact = _mapping(value["artifact"], ("filename", "location"), "source artifact")
    filename = _text(artifact["filename"], "wheel filename", 200)
    if filename != f"rpnh_harness-{version}-py3-none-any.whl":
        raise ValueError("source wheel filename does not match RPNH version")
    if value["kind"] == "local_candidate":
        if value["release"] is not None or value["channel"] is not None:
            raise ValueError("local candidates have no published release or update channel")
        location = _absolute(artifact["location"], "candidate wheel")
        if PurePosixPath(location).name != filename:
            raise ValueError("candidate wheel path does not match its filename")
    elif value["kind"] == "published_release":
        _enum(value["channel"], {"stable", "prerelease"}, "published channel")
        if value["release"] != "v" + version:
            raise ValueError("published source release/channel is inconsistent")
        if Version(version).is_devrelease or (value["channel"] == "stable" and Version(version).is_prerelease):
            raise ValueError("source version is not eligible for this published channel")
        location = _text(artifact["location"], "published artifact URL")
        parsed = urlsplit(location)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or PurePosixPath(parsed.path).name != filename):
            raise ValueError("invalid published artifact URL")
        # The official host, release asset identity and qualification evidence
        # are deliberately the release resolver's responsibility, not this tag.
    else:
        raise ValueError("unknown installation source kind")
    return deepcopy(value)


def validate_platform(document) -> dict:
    value = _mapping(document, ("os", "architecture", "os_release", "libc", "emulated"), "platform")
    _enum(value["os"], {"linux", "darwin"}, "managed-v2 Linux/Darwin target")
    _text(value["architecture"], "target architecture", 80)
    _text(value["os_release"], "OS release", 200)
    if value["emulated"] is not None and type(value["emulated"]) is not bool:
        raise ValueError("emulation observation must be boolean or unknown")
    if value["libc"] is not None:
        libc = _mapping(value["libc"], ("family", "version"), "libc")
        _text(libc["family"], "libc family", 80)
        _text(libc["version"], "libc version", 80)
    return deepcopy(value)


def validate_runtime_bindings(document, *, require_python=True) -> dict:
    if not isinstance(document, Mapping) or set(document) - {"python", "node", "npm", "pnpm"}:
        raise ValueError("invalid runtime binding names")
    if require_python and "python" not in document:
        raise ValueError("complete installation requires its exact Python observation")
    result = {}
    for name, item in document.items():
        value = _mapping(item, ("path", "version", "implementation", "architecture"), f"{name} runtime")
        _absolute(value["path"], f"{name} runtime path")
        if not isinstance(value["version"], str) or _RUNTIME_VERSION_RE.fullmatch(value["version"]) is None:
            raise ValueError(f"{name} runtime requires a complete version, not a range")
        _text(value["implementation"], f"{name} implementation", 80)
        _text(value["architecture"], f"{name} execution architecture", 80)
        result[name] = deepcopy(value)
    return result


def runtime_signature(document) -> dict:
    """Keep exact runtime facts without turning one host's paths into policy."""
    bindings = validate_runtime_bindings(document)
    return {name: {key: value[key] for key in ("version", "implementation", "architecture")}
            for name, value in bindings.items()}


def _runtime_signature(document) -> dict:
    if (not isinstance(document, Mapping) or "python" not in document
            or set(document) - {"python", "node", "npm", "pnpm"}):
        raise ValueError("invalid qualification runtime names")
    result = {}
    for name, item in document.items():
        value = _mapping(item, ("version", "implementation", "architecture"), "qualification runtime")
        if not isinstance(value["version"], str) or _RUNTIME_VERSION_RE.fullmatch(value["version"]) is None:
            raise ValueError("qualification runtime requires an exact complete version")
        _text(value["implementation"], "runtime implementation", 80)
        _text(value["architecture"], "runtime architecture", 80)
        result[name] = deepcopy(value)
    return result


def _artifact(document, entry: str) -> dict:
    value = _mapping(document, ("kind", "name", "version", "variant", "architecture"), "component artifact")
    allowed = {"wheel"} if entry == "basic" else {"dsh_source"} if entry == "dsh-web" else {"npm", "native"}
    _enum(value["kind"], allowed, "component artifact kind")
    for key in ("name", "version", "variant", "architecture"):
        _text(value[key], f"artifact {key}", 240)
    if entry == "basic":
        _version(value["version"])
        if (value["name"] != "rpnh-harness" or value["variant"] != "py3-none-any"
                or value["architecture"] != "any"):
            raise ValueError("Basic artifact must identify the common RPNH wheel")
    elif _RUNTIME_VERSION_RE.fullmatch(value["version"]) is None:
        raise ValueError("component artifact version must be exact")
    return deepcopy(value)


def _binding(document, entry: str) -> dict:
    if not isinstance(document, Mapping):
        raise ValueError("component binding must be an object")
    kind = _enum(document.get("kind"), {"managed", "external", "source_import"}, "component binding kind")
    if kind == "managed":
        value = _mapping(document, ("kind",), "managed binding")
    elif kind in {"external", "source_import"}:
        value = _mapping(document, ("kind", "path"), "external/input binding")
        _absolute(value["path"], "binding path")
    else:
        raise ValueError("unknown component binding kind")
    if entry == "basic" and kind != "managed":
        raise ValueError("Basic is an installation-owned component")
    if (kind == "source_import" and entry != "dsh-web") or (entry == "dsh-web" and kind == "external"):
        raise ValueError("DSH source input must be imported into the installation before launch")
    return deepcopy(value)


def _selection(document, *, complete: bool) -> dict:
    if (not isinstance(document, Mapping) or not document or set(document) - set(PRIMARY_ENTRIES)
            or "basic" not in document):
        raise ValueError("component set must include Basic and only primary frontend entries")
    result = {}
    for entry in PRIMARY_ENTRIES:
        if entry not in document:
            continue
        item = document[entry]
        fields = ("profile", "binding", "launch", "artifact") if complete else ("profile", "binding")
        value = _mapping(item, fields, f"{entry} component")
        profile = _text(value["profile"], "component profile", 120)
        if entry == "basic" and profile != "builtin":
            raise ValueError("Basic requires the builtin profile")
        value["binding"] = _binding(value["binding"], entry)
        if complete:
            launch = _mapping(value["launch"], ("kind", "scope", "path"), "component launch")
            allowed = ({"builtin"} if entry == "basic" else {"dsh_prepared"}
                       if entry == "dsh-web" else {"native", "node_wrapper"})
            _enum(launch["kind"], allowed, "launch kind for frontend entry")
            if launch["scope"] == "installation":
                _relative(launch["path"], "component launch path")
                if value["binding"]["kind"] == "external":
                    raise ValueError("external binding cannot silently become a managed launch")
            elif launch["scope"] == "external":
                _absolute(launch["path"], "external launch path")
                if value["binding"] != {"kind": "external", "path": launch["path"]}:
                    raise ValueError("external launch must match its exact external binding")
            else:
                raise ValueError("unknown launch scope")
            if entry == "basic" and launch != {
                    "kind": "builtin", "scope": "installation", "path": "venv/bin/rpnh"}:
                raise ValueError("Basic launch must use this installation's fixed entry")
            if entry == "dsh-web" and launch["scope"] != "installation":
                raise ValueError("DSH preparation must belong to this installation")
            value["artifact"] = _artifact(value["artifact"], entry)
        result[entry] = deepcopy(value)
    return result


def validate_installed(document, *, expected_id: str | None = None) -> dict:
    """Check completed software facts only; never inspect runtime health.

    Transaction recovery must still recognize an already committed target when
    its external Node/Python has subsequently disappeared. Probe health later.
    """
    value = _mapping(document, ("schema_version", "protocol", "installation_id", "rpnh_version", "source",
                                "platform", "runtime_bindings", "components", "complete"), "installed record")
    if value["schema_version"] != INSTALLED_SCHEMA or value["protocol"] != V2_PROTOCOL or value["complete"] is not True:
        raise ValueError("installation record is not completed managed-v2")
    identity = installation_id(value["installation_id"])
    if expected_id is not None and identity != installation_id(expected_id):
        raise ValueError("installation record identity does not match its directory")
    version = _version(value["rpnh_version"])
    value["source"] = validate_source(value["source"])
    if value["source"]["rpnh_version"] != version:
        raise ValueError("installed source and RPNH version differ")
    value["platform"] = validate_platform(value["platform"])
    value["runtime_bindings"] = validate_runtime_bindings(value["runtime_bindings"])
    value["components"] = _selection(value["components"], complete=True)
    basic = value["components"]["basic"]["artifact"]
    if basic["kind"] != "wheel" or basic["name"] != "rpnh-harness" or basic["version"] != version:
        raise ValueError("Basic artifact must identify the same RPNH wheel")
    if (any(item["launch"]["kind"] in {"node_wrapper", "dsh_prepared"}
            for item in value["components"].values()) and "node" not in value["runtime_bindings"]):
        raise ValueError("Node-backed launch requires its exact Node observation")
    return deepcopy(value)


def validate_pending_activation(document) -> dict | None:
    if document is None:
        return None
    value = _mapping(document, ("operation", "from", "to", "old_previous", "previous_after"), "pending activation")
    _enum(value["operation"], {"bootstrap", "upgrade", "reconfigure", "rollback"}, "activation operation")
    _target(value["to"])
    for key in ("from", "old_previous", "previous_after"):
        _target(value[key], optional=True)
    if value["from"] == value["to"]:
        raise ValueError("an idempotent no-op must not create a new activation intent")
    if value["operation"] == "bootstrap":
        if any(value[key] is not None for key in ("from", "old_previous", "previous_after")):
            raise ValueError("bootstrap requires an identified empty installation root")
    elif value["from"] is None:
        raise ValueError("non-bootstrap activation requires its previous current target")
    elif value["operation"] == "rollback":
        if value["old_previous"] != value["to"] or value["previous_after"] != value["to"]:
            raise ValueError("rollback must retain its target as previous for idempotence")
    elif value["previous_after"] != value["from"]:
        raise ValueError("upgrade/reconfigure must retain the outgoing current target")
    return deepcopy(value)


def validate_managed(document) -> dict:
    value = _mapping(document, ("schema_version", "protocol", "pending_activation"), "managed root")
    if value["schema_version"] != MANAGED_SCHEMA or value["protocol"] != V2_PROTOCOL:
        raise ValueError("unsupported managed root protocol; v1 roots are not adopted")
    value["pending_activation"] = validate_pending_activation(value["pending_activation"])
    return deepcopy(value)


def validate_frontend_view(document, *, expected_id=None, selected_entries=None) -> dict:
    value = _mapping(document, ("schema_version", "installation_id", "frontend"), "managed frontend view")
    if value["schema_version"] != FRONTEND_SCHEMA:
        raise ValueError("managed frontend view requires its v2 schema")
    identity = installation_id(value["installation_id"])
    if expected_id is not None and identity != installation_id(expected_id):
        raise ValueError("frontend view belongs to another installation")
    _enum(value["frontend"], {"auto", *ENTRY_FRONTEND.values()}, "frontend display preference")
    if selected_entries is not None:
        try:
            selected_entries = set(selected_entries)
        except TypeError as exc:
            raise ValueError("invalid installed frontend set") from exc
        if selected_entries - set(PRIMARY_ENTRIES) or "basic" not in selected_entries:
            raise ValueError("invalid installed frontend set")
        entry = "dsh-web" if value["frontend"] == "dsh" else value["frontend"]
        if entry != "auto" and entry not in selected_entries:
            raise ValueError("display selection requires installation reconfiguration")
    return deepcopy(value)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _json(content):
    return json.loads(content, object_pairs_hook=_unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))


def _read_record(path: Path, *, limit=256 * 1024):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("software record must be a bounded regular file")
    return _json(path.read_text(encoding="utf-8"))


def read_software_record(path: Path, *, limit=256 * 1024):
    """Read bounded, non-symlink JSON with unique keys; apply its validator next."""
    return _read_record(Path(path), limit=limit)


def read_frontend_view(directory: Path, *, expected_id=None, selected_entries=None) -> dict:
    directory = Path(directory)
    return validate_frontend_view(_read_record(directory / "frontend.json", limit=16 * 1024),
                                  expected_id=directory.name if expected_id is None else expected_id,
                                  selected_entries=selected_entries)


def read_installed(directory: Path) -> dict:
    directory = Path(directory)
    return validate_installed(_read_record(directory / "installed.json"), expected_id=directory.name)


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    entry: str
    profile: str
    version: str
    coordinate: dict
    revision: str | None = None
    selectable: bool = True
    web_packages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileCatalog:
    rpnh_version: str
    defaults: dict[str, str]
    profiles: dict[str, dict[str, ProfileSpec]]


def _wheel_version(archive: ZipFile) -> str:
    names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    if len(names) != 1 or archive.getinfo(names[0]).file_size > 256 * 1024:
        raise ValueError("candidate wheel must contain one bounded distribution metadata record")
    metadata = BytesParser().parsebytes(archive.read(names[0]))
    if metadata["Name"] != "rpnh-harness":
        raise ValueError("candidate wheel is not RPNH")
    return _version(metadata["Version"])


def load_profile_catalog(*, package_root: Path | None = None, wheel: Path | None = None) -> ProfileCatalog:
    """Read profile/default facts from one target payload, not a second table.

    A selected future/older wheel must supply its own catalog. The current
    manager's defaults never silently stand in for the target release.
    """
    if package_root is not None and wheel is not None:
        raise ValueError("choose a package directory or one wheel")
    archive = None
    try:
        if wheel is not None:
            archive = ZipFile(wheel)
            version = _wheel_version(archive)
            def read(name):
                if archive.namelist().count(name) != 1 or archive.getinfo(name).file_size > 2 * 1024 * 1024:
                    raise ValueError("profile document exceeds limit")
                return _json(archive.read(name))
        else:
            root = Path(package_root) if package_root is not None else Path(__file__).resolve().parents[2]
            project = root / "pyproject.toml"
            if project.is_file():
                metadata = tomllib.loads(project.read_text(encoding="utf-8"))["project"]
                if metadata["name"] != "rpnh-harness":
                    raise ValueError("profile catalog directory is not RPNH")
                version = _version(metadata["version"])
            elif package_root is None:
                version = _version(importlib.metadata.version("rpnh-harness"))
            else:
                names = tuple(root.glob("rpnh_harness-*.dist-info/METADATA"))
                if len(names) != 1:
                    raise ValueError("profile directory has no unique RPNH version metadata")
                metadata = BytesParser().parsebytes(names[0].read_bytes())
                if metadata["Name"] != "rpnh-harness":
                    raise ValueError("profile catalog directory is not RPNH")
                version = _version(metadata["Version"])
            def read(name):
                return _read_record(root / name, limit=2 * 1024 * 1024)

        profiles = {entry: {} for entry in PRIMARY_ENTRIES}
        profiles["basic"]["builtin"] = ProfileSpec("basic", "builtin", "builtin", {"kind": "builtin"})
        defaults = {"basic": "builtin"}
        codex = read("cpn/frontend/codex_compatibility.v1.json")
        declared = codex["compatibility_profiles"]
        defaults["codex"] = declared["default"]
        for profile, item in declared["profiles"].items():
            profiles["codex"][profile] = ProfileSpec("codex", profile, item["version"], {
                "kind": "npm", "package": codex["frontend"]["package"], "version": item["version"]})
        opencode = read("cpn/frontend/opencode_compatibility.v1.json")
        defaults["opencode"] = opencode["upstream"]["package_version"]
        for item in [opencode["upstream"], *opencode.get("certification_candidates", []),
                     *opencode.get("protocol_candidates", [])]:
            profile = item["package_version"]
            package = item.get("npm_package")
            coordinate = {"kind": "npm", "package": package, "version": profile} if package else {}
            spec = ProfileSpec("opencode", profile, profile, coordinate,
                               revision=item["commit"], selectable=bool(package))
            existing = profiles["opencode"].get(profile)
            if existing is not None and existing != spec:
                raise ValueError("conflicting OpenCode profile declarations")
            profiles["opencode"][profile] = spec
        dsh_profiles = [("UPSTREAM.json", "web-packages.json", True),
                        ("UPSTREAM_CANDIDATE.json", "web-packages.rc1.json", False)]
        # Historical wheels don't declare rc.5. In a new target payload both
        # records are required if either is present; read() still rejects duplicates.
        rc5_pin = "integrations/dsh/UPSTREAM_RC5_CANDIDATE.json"
        rc5_web = "integrations/dsh/frontend/web-packages.rc5.json"
        if (any(name in archive.namelist() for name in (rc5_pin, rc5_web)) if archive is not None
                else any((root / name).is_file() for name in (rc5_pin, rc5_web))):
            dsh_profiles.append(("UPSTREAM_RC5_CANDIDATE.json", "web-packages.rc5.json", False))
        for pin_name, manifest_name, default in dsh_profiles:
            pin = read("integrations/dsh/" + pin_name)
            manifest = read("integrations/dsh/frontend/" + manifest_name)
            profile = pin["version"]
            if manifest["version"] != profile:
                raise ValueError("DSH source and Web manifest versions differ")
            packages = manifest["packages"]
            if (not isinstance(packages, list) or not packages or len(packages) != len(set(packages))
                    or not all(isinstance(name, str) and name.startswith("@deepseek-ai/") for name in packages)):
                raise ValueError("invalid DSH Web package selection")
            profiles["dsh-web"][profile] = ProfileSpec("dsh-web", profile, profile, {
                "kind": "source", "repository": pin["repository"], "release_tag": pin["release_tag"],
                "revision": pin["revision"], "pnpm": pin["pnpm"]}, revision=pin["revision"], web_packages=tuple(packages))
            if default:
                defaults["dsh-web"] = profile
        for entry in PRIMARY_ENTRIES:
            if defaults[entry] not in profiles[entry]:
                raise ValueError("profile catalog default is undeclared")
            for profile, spec in profiles[entry].items():
                _text(profile, "profile identifier", 120)
                if entry == "basic":
                    continue
                if not isinstance(spec.version, str) or _RUNTIME_VERSION_RE.fullmatch(spec.version) is None:
                    raise ValueError("profile version must be exact")
                if spec.revision is not None:
                    _text(spec.revision, "profile source revision", 120)
                if spec.coordinate.get("kind") == "npm":
                    package = spec.coordinate.get("package")
                    if not isinstance(package, str) or _NPM_PACKAGE_RE.fullmatch(package) is None:
                        raise ValueError("profile requires a fixed npm package coordinate")
                if entry == "dsh-web":
                    for key in ("repository", "release_tag", "pnpm"):
                        _text(spec.coordinate[key], "DSH " + key, 240)
                    if _RUNTIME_VERSION_RE.fullmatch(spec.coordinate["pnpm"]) is None:
                        raise ValueError("DSH package manager version must be exact")
        return ProfileCatalog(version, defaults, profiles)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("invalid packaged frontend profile catalog") from exc
    finally:
        if archive is not None:
            archive.close()


def get_profile(entry: str, profile: str | None = None, *, catalog: ProfileCatalog | None = None) -> ProfileSpec:
    catalog = load_profile_catalog() if catalog is None else catalog
    if entry not in PRIMARY_ENTRIES:
        raise ValueError("unknown primary frontend entry")
    selected = catalog.defaults[entry] if profile is None else profile
    if not isinstance(selected, str) or selected not in catalog.profiles[entry]:
        raise ValueError(f"unknown {entry} profile: {selected}")
    result = catalog.profiles[entry][selected]
    if not result.selectable:
        raise ValueError(f"{entry} profile has no declared ordinary installation coordinate: {selected}")
    return result


def validate_profile(entry: str, profile: str, *, catalog: ProfileCatalog | None = None) -> str:
    return get_profile(entry, profile, catalog=catalog).profile


@dataclass(frozen=True, slots=True)
class InstallPlan:
    installation_id: str
    operation: str
    expected_from: str | None
    source: dict
    platform: dict
    runtime_bindings: dict
    components: dict
    conflicts: tuple[dict, ...]

    @property
    def rpnh_version(self) -> str:
        return self.source["rpnh_version"]

    @property
    def target(self) -> str:
        return installation_target(self.installation_id)

    @property
    def selection_resolved(self) -> bool:
        # No dependency, native acceptance, source authorization or release
        # qualification is inferred from a resolved frontend selection.
        return not self.conflicts

    def to_document(self) -> dict:
        return deepcopy({"schema_version": PLAN_SCHEMA, "protocol": V2_PROTOCOL,
                         "installation_id": self.installation_id, "operation": self.operation,
                         "expected_from": self.expected_from, "rpnh_version": self.rpnh_version,
                         "source": self.source, "platform": self.platform,
                         "runtime_bindings": self.runtime_bindings,
                         "components": self.components, "conflicts": list(self.conflicts)})


def validate_plan(document) -> dict:
    value = _mapping(document, ("schema_version", "protocol", "installation_id", "operation", "expected_from",
                                "rpnh_version", "source", "platform", "runtime_bindings", "components", "conflicts"), "install plan")
    if value["schema_version"] != PLAN_SCHEMA or value["protocol"] != V2_PROTOCOL:
        raise ValueError("unsupported install plan contract")
    identity = installation_id(value["installation_id"])
    _enum(value["operation"], {"bootstrap", "upgrade", "reconfigure"}, "install plan operation")
    previous = _target(value["expected_from"], optional=True)
    if (value["operation"] == "bootstrap") != (previous is None) or previous == installation_target(identity):
        raise ValueError("install plan origin/operation is inconsistent")
    version = _version(value["rpnh_version"])
    value["source"] = validate_source(value["source"])
    if value["source"]["rpnh_version"] != version:
        raise ValueError("plan source and RPNH version differ")
    value["platform"] = validate_platform(value["platform"])
    value["runtime_bindings"] = validate_runtime_bindings(value["runtime_bindings"], require_python=False)
    value["components"] = _selection(value["components"], complete=False)
    if not isinstance(value["conflicts"], list) or len(value["conflicts"]) > 100:
        raise ValueError("invalid plan conflicts")
    for conflict in value["conflicts"]:
        row = _mapping(conflict, ("entry", "code", "detail"), "plan conflict")
        if row["entry"] is not None and (not isinstance(row["entry"], str)
                                          or row["entry"] not in value["components"]):
            raise ValueError("plan conflict names an unselected entry")
        _text(row["code"], "conflict code", 80)
        _text(row["detail"], "conflict detail", 2000)
    return deepcopy(value)


def read_plan(directory: Path) -> dict:
    directory = Path(directory)
    result = validate_plan(_read_record(directory / "plan.json"))
    if result["installation_id"] != installation_id(directory.name):
        raise ValueError("plan belongs to another installation directory")
    return result


def build_install_plan(source, platform, runtime_bindings, *, requested=None, current=None,
                       catalog: ProfileCatalog | None = None, installation_id: str | None = None) -> InstallPlan:
    """Resolve all of S without writes, subprocesses, network or PATH probes.

    No request preserves every current profile/binding. An explicit request is
    a replacement set (plus mandatory Basic); omitted per-entry values retain
    that entry's current selection, or use target defaults for a new component.
    Unresolvable selected components remain visible with conflicts, never fall
    back to Basic or a different profile. Applying a plan requires independent
    source/material validation and a fresh comparison under the root EX lease.
    """
    source = validate_source(source)
    platform = validate_platform(platform)
    runtime_bindings = validate_runtime_bindings(runtime_bindings, require_python=False)
    current = None if current is None else validate_installed(current)
    catalog = load_profile_catalog() if catalog is None else catalog
    identity = installation_id or "i-" + uuid4().hex
    installation_target(identity)
    existing = {} if current is None else current["components"]
    if requested is None:
        requested = {entry: {"profile": value["profile"], "binding": value["binding"]}
                     for entry, value in existing.items()}
    if not isinstance(requested, Mapping) or set(requested) - set(PRIMARY_ENTRIES):
        raise ValueError("request must name a complete set of primary frontend entries")
    desired = {"basic": {}, **deepcopy(dict(requested))}
    components, conflicts = {}, []
    def conflict(entry, code, detail):
        conflicts.append({"entry": entry, "code": code, "detail": detail})
    for entry in PRIMARY_ENTRIES:
        if entry not in desired:
            continue
        request = desired[entry]
        if not isinstance(request, Mapping) or set(request) - {"profile", "binding"}:
            raise ValueError("component requests accept only profile and binding")
        old = existing.get(entry, {})
        profile = request.get("profile", old.get("profile", catalog.defaults[entry]))
        binding = request.get("binding", old.get("binding", {"kind": "managed"}))
        components[entry] = {"profile": _text(profile, "component profile", 120), "binding": _binding(binding, entry)}
        try:
            specification = get_profile(entry, profile, catalog=catalog)
        except ValueError as exc:
            conflict(entry, "unavailable_profile", str(exc))
            continue
        if (source["kind"] == "published_release" and source["channel"] == "stable"
                and "-" in specification.version):
            conflict(entry, "prerelease_profile", "Selected prerelease frontend requires a prerelease release channel")
        required = {"python"}
        if entry == "dsh-web":
            required.update(("node", "pnpm"))
        elif specification.coordinate.get("kind") == "npm" and binding["kind"] == "managed":
            required.update(("node", "npm"))
        for runtime in sorted(required - set(runtime_bindings)):
            conflict(entry, "missing_runtime", f"The selected installation recipe needs an exact {runtime} observation")
    if catalog.rpnh_version != source["rpnh_version"]:
        conflict(None, "target_catalog_mismatch", "Read the frontend catalog from the selected target payload")
    expected_from = None if current is None else installation_target(current["installation_id"])
    if current is None:
        operation = "bootstrap"
    else:
        operation = "reconfigure" if current["rpnh_version"] == source["rpnh_version"] else "upgrade"
        if Version(source["rpnh_version"]) < Version(current["rpnh_version"]):
            conflict(None, "downgrade_requires_rollback", "Use the retained compatible installation instead of preparing a downgrade")
    result = InstallPlan(identity, operation, expected_from, source, platform,
                         runtime_bindings, components, tuple(conflicts))
    validate_plan(result.to_document())
    return result


def _strings(document, label: str, *, allow_empty=False, paths=False) -> list[str]:
    if (not isinstance(document, list) or len(document) > 256
            or not document and not allow_empty):
        raise ValueError(f"invalid {label} list")
    result = []
    for item in document:
        item = _relative(item, label) if paths else _text(item, label, 240)
        if item in result:
            raise ValueError(f"duplicate {label}")
        result.append(item)
    return result


def _qualification_components(document, version: str) -> dict:
    if (not isinstance(document, Mapping) or "basic" not in document
            or set(document) - set(PRIMARY_ENTRIES)):
        raise ValueError("qualification must identify a complete primary component set including Basic")
    result = {}
    for entry, item in document.items():
        value = _mapping(item, ("profile", "artifact"), "qualification component")
        _text(value["profile"], "qualification profile", 120)
        if entry == "basic" and value["profile"] != "builtin":
            raise ValueError("Basic qualification requires the builtin profile")
        value["artifact"] = _artifact(value["artifact"], entry)
        if entry == "basic" and value["artifact"]["version"] != version:
            raise ValueError("qualification Basic artifact and source version differ")
        result[entry] = value
    return result


def _release_frontend_materials(document, *, directory_url, identities, filenames):
    if not isinstance(document, Mapping) or set(document) - {"codex", "opencode", "dsh-web"}:
        raise ValueError("release frontend materials must name primary non-Basic entries")
    result = {}
    for entry, item in sorted(document.items()):
        if not isinstance(item, Mapping):
            raise ValueError("invalid release frontend material")
        kind = _enum(item.get("kind"), {"native_file", "node_tree", "dsh_tree"}, "release frontend recipe")
        fields = ("kind", "artifact", "filename", "asset") + (("entry",) if kind == "node_tree" else ())
        row = _mapping(item, fields, "release frontend material")
        artifact = _artifact(row["artifact"], entry)
        expected_kind = {"native_file": "native", "node_tree": "npm", "dsh_tree": "dsh_source"}[kind]
        if (artifact["kind"] != expected_kind or (entry == "dsh-web") != (kind == "dsh_tree")):
            raise ValueError("release frontend recipe and artifact differ")
        filename = _text(row["filename"], "release frontend filename", 240)
        if (not filename.isascii() or filename in {".", ".."}
                or any(character in filename for character in "/\\?#% ")
                or PurePosixPath(filename).name != filename
                or kind != "native_file" and not filename.endswith(".tar.gz")):
            raise ValueError("release frontend asset requires a plain basename and fixed tree archive format")
        if filename in filenames:
            raise ValueError("release materials repeat an asset filename")
        filenames.add(filename)
        asset = _mapping(row["asset"], ("id", "size", "url"), "release frontend asset")
        if (type(asset["id"]) is not int or asset["id"] <= 0
                or type(asset["size"]) is not int or asset["size"] <= 0
                or asset["id"] in identities):
            raise ValueError("release frontend asset requires a unique positive identity and size")
        identities.add(asset["id"])
        if _text(asset["url"], "release frontend asset URL") != directory_url + filename:
            raise ValueError("release frontend asset differs from its exact release coordinate")
        if kind == "node_tree":
            row["entry"] = _relative(row["entry"], "release frontend entry")
        result[entry] = {**row, "artifact": artifact, "asset": asset}
    return result


def validate_material_selection(document, *, source) -> dict:
    """Normalize exact release materials, without fetching or authenticating them.

    Asset identities and coordinates must describe the same declared release;
    the release resolver still has to match them to actual official assets.
    The material reader separately proves the complete Requires-Dist closure
    from the selected files. Neither this record nor a matching filename proves
    that arbitrary local bytes came from the declared asset.
    """
    source = validate_source(source)
    if source["kind"] != "published_release":
        raise ValueError("release material selection requires a published source description")
    source_url = source["artifact"]["location"]
    if PurePosixPath(urlsplit(source_url).path).parent.name != source["release"]:
        raise ValueError("release materials require the source's exact release directory")
    directory_url = source_url.rsplit("/", 1)[0] + "/"
    extended = isinstance(document, Mapping) and "frontends" in document
    fields = ("root_extras", "python_wheels") + (("frontends",) if extended else ())
    value = _mapping(document, fields, "release material selection")
    extras = value["root_extras"]
    if (not isinstance(extras, list) or len(extras) > 64
            or any(not isinstance(extra, str) or len(extra) > 80
                   or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", extra) is None
                   or canonicalize_name(extra) != extra for extra in extras)):
        raise ValueError("release root_extras requires explicit canonical extra names")
    if len(set(extras)) != len(extras):
        raise ValueError("release root_extras repeats an extra")
    wheels = value["python_wheels"]
    if not isinstance(wheels, list) or not 1 <= len(wheels) <= 256:
        raise ValueError("release materials require a bounded exact Python wheel selection")
    result, names, identities, filenames = [], set(), set(), set()
    for item in wheels:
        wheel = _mapping(item, ("name", "version", "filename", "asset"), "release Python wheel")
        name = _text(wheel["name"], "release distribution name", 200)
        version = _text(wheel["version"], "release distribution version", 200)
        filename = _text(wheel["filename"], "release wheel filename", 240)
        if (not filename.isascii() or any(character in filename for character in "/\\?#% ")
                or PurePosixPath(filename).name != filename):
            raise ValueError("release wheel filename must be a plain wheel basename")
        parsed_name, parsed_version, _build, _tags = parse_wheel_filename(filename)
        if (name != canonicalize_name(name) or parsed_name != name
                or str(parsed_version) != version):
            raise ValueError("release wheel filename differs from its exact canonical name/version")
        if name in names:
            raise ValueError("release materials repeat a distribution")
        names.add(name)
        filenames.add(filename)
        asset = _mapping(wheel["asset"], ("id", "size", "url"), "release wheel asset")
        if (type(asset["id"]) is not int or asset["id"] <= 0
                or type(asset["size"]) is not int or asset["size"] <= 0):
            raise ValueError("release wheel asset requires positive integer identity and size")
        if asset["id"] in identities:
            raise ValueError("release materials repeat an asset identity")
        identities.add(asset["id"])
        if _text(asset["url"], "release wheel asset URL") != directory_url + filename:
            raise ValueError("release wheel asset differs from its exact release coordinate")
        if name == "rpnh-harness" and (version != source["rpnh_version"]
                or filename != source["artifact"]["filename"] or asset["url"] != source_url):
            raise ValueError("release RPNH wheel differs from its source artifact")
        result.append({**wheel, "asset": asset})
    if "rpnh-harness" not in names:
        raise ValueError("release materials must include their exact RPNH wheel")
    normalized = {"root_extras": sorted(extras), "python_wheels": sorted(result, key=lambda row: row["name"])}
    if extended:
        normalized["frontends"] = _release_frontend_materials(value["frontends"],
            directory_url=directory_url, identities=identities, filenames=filenames)
    return deepcopy(normalized)


def _bound_material_selection(document, *, source, components, extended):
    material = validate_material_selection(document, source=source)
    if ("frontends" in material) != extended:
        raise ValueError("release material shape differs from its sidecar contract version")
    if extended:
        if set(material["frontends"]) != set(components) - {"basic"}:
            raise ValueError("release frontend materials must cover the complete selected component set")
        for entry, item in material["frontends"].items():
            if item["artifact"] != components[entry]["artifact"]:
                raise ValueError("release frontend material differs from its exact qualified component artifact")
    return material


def _entry_scopes(document, components, scope):
    if not isinstance(document, Mapping) or set(document) != set(components):
        raise ValueError("entry_scope must explicitly cover the complete component set")
    result = {}
    for entry, cases in sorted(document.items()):
        selected = _strings(cases, "entry check scope", allow_empty=True)
        if not set(selected) <= set(scope):
            raise ValueError("entry_scope cannot claim cases absent from the same result scope")
        result[entry] = sorted(selected)
    return result


def material_selection_signature(document, *, source) -> dict:
    """Return the complete normalized declaration for equality, without a hash.

    Unlike a local install-material document, this value has no input paths.
    Exact filenames and asset identities remain significant even when name and
    version match. Callers must not turn this structural value into authority.
    """
    return validate_material_selection(document, source=source)


def _sidecar_schema(document, versions, label: str) -> str:
    if not isinstance(document, Mapping):
        raise ValueError("invalid update sidecar fields")
    return _enum(document.get("schema_version"), versions, label)


def _sidecar_header(document, schema: str, fields) -> dict:
    value = _mapping(document, ("schema_version", "protocol", "source", *fields), "update sidecar")
    if value["schema_version"] != schema or value["protocol"] != V2_PROTOCOL:
        raise ValueError("unsupported update sidecar contract")
    value["source"] = validate_source(value["source"])
    return value


def _release_ranges(document, version: str, operation: str) -> list[dict]:
    if not isinstance(document, list) or len(document) > 128:
        raise ValueError("invalid release compatibility range")
    result, seen = [], set()
    for item in document:
        value = _mapping(item, ("version", "capabilities", "evidence"), "release compatibility declaration")
        other = _version(value["version"])
        if (other in seen or Version(other) > Version(version)
                or operation == "upgrade" and other == version):
            raise ValueError("invalid or duplicate release compatibility version")
        seen.add(other)
        value["capabilities"] = _strings(value["capabilities"], "compatibility capability")
        value["evidence"] = _strings(value["evidence"], "validation result ID", allow_empty=True)
        result.append(value)
    return result


def validate_update_support(document) -> dict:
    """Parse exact declared combinations; this does not verify their evidence.

    This sidecar remains outside the frozen wheel/sdist. Source descriptions
    are data, not authentication of official publication or update eligibility.
    An empty compatibility range grants no upgrade/rollback scope.
    """
    schema = _sidecar_schema(document, {UPDATE_SUPPORT_SCHEMA, UPDATE_SUPPORT_V3_SCHEMA, UPDATE_SUPPORT_V4_SCHEMA}, "update support schema")
    value = _sidecar_header(document, schema, ("combinations", "upgrade_from", "rollback_to"))
    source, seen = value["source"], set()
    bound = schema in {UPDATE_SUPPORT_V3_SCHEMA, UPDATE_SUPPORT_V4_SCHEMA}
    extended = schema == UPDATE_SUPPORT_V4_SCHEMA
    if bound and source["kind"] != "published_release":
        raise ValueError("bound release materials require a published source description")
    combinations = value["combinations"]
    if not isinstance(combinations, list) or len(combinations) > 128:
        raise ValueError("invalid supported combination list")
    result = []
    for item in combinations:
        fields = ("id", "platform", "runtimes", "components", "scope", "evidence")
        if bound:
            fields += ("materials",)
        if extended:
            fields += ("entry_scope",)
        row = _mapping(item, fields, "support combination")
        identity = _text(row["id"], "combination ID", 120)
        if identity in seen:
            raise ValueError("duplicate support combination ID")
        seen.add(identity)
        row["platform"] = validate_platform(row["platform"])
        row["runtimes"] = _runtime_signature(row["runtimes"])
        row["components"] = _qualification_components(row["components"], source["rpnh_version"])
        row["scope"] = _strings(row["scope"], "check scope")
        row["evidence"] = _strings(row["evidence"], "validation result ID", allow_empty=True)
        if bound:
            row["materials"] = _bound_material_selection(row["materials"], source=source,
                components=row["components"], extended=extended)
        if extended:
            row["entry_scope"] = _entry_scopes(row["entry_scope"], row["components"], row["scope"])
        if source["channel"] == "stable":
            for component in row["components"].values():
                try:
                    prerelease = Version(component["artifact"]["version"]).is_prerelease
                except InvalidVersion as exc:
                    raise ValueError("stable qualification cannot classify its upstream version") from exc
                if prerelease:
                    raise ValueError("stable qualification cannot include a prerelease frontend")
        result.append(row)
    value["combinations"] = result
    value["upgrade_from"] = _release_ranges(value["upgrade_from"], source["rpnh_version"], "upgrade")
    value["rollback_to"] = _release_ranges(value["rollback_to"], source["rpnh_version"], "rollback")
    return deepcopy(value)


def validate_update_validation(document) -> dict:
    """Parse native, fixture, failure and blocked evidence without promoting it."""
    schema = _sidecar_schema(document, {UPDATE_VALIDATION_SCHEMA, UPDATE_VALIDATION_V3_SCHEMA, UPDATE_VALIDATION_V4_SCHEMA}, "update validation schema")
    value = _sidecar_header(document, schema, ("results",))
    bound = schema in {UPDATE_VALIDATION_V3_SCHEMA, UPDATE_VALIDATION_V4_SCHEMA}
    extended = schema == UPDATE_VALIDATION_V4_SCHEMA
    if bound and value["source"]["kind"] != "published_release":
        raise ValueError("bound release materials require a published source description")
    version, seen = value["source"]["rpnh_version"], set()
    if not isinstance(value["results"], list) or len(value["results"]) > 1024:
        raise ValueError("invalid validation results")
    result = []
    for item in value["results"]:
        fields = ("id", "combination", "platform", "runtime_bindings", "components",
                  "operation", "from_version", "to_version", "capabilities",
                  "mode", "status", "scope", "evidence")
        if bound:
            fields += ("materials",)
        if extended:
            fields += ("entry_scope",)
        row = _mapping(item, fields, "validation result")
        identity = _text(row["id"], "validation result ID", 120)
        if identity in seen:
            raise ValueError("duplicate validation result ID")
        seen.add(identity)
        _text(row["combination"], "validation combination", 120)
        _enum(row["mode"], {"native", "fixture"}, "validation mode")
        _enum(row["status"], {"pass", "fail", "blocked"}, "validation status")
        operation = _enum(row["operation"], {"install", "upgrade", "rollback"}, "validation operation")
        row["platform"] = validate_platform(row["platform"])
        row["runtime_bindings"] = validate_runtime_bindings(row["runtime_bindings"],
                                                            require_python=row["status"] != "blocked")
        row["components"] = _qualification_components(row["components"], version)
        if bound:
            row["materials"] = _bound_material_selection(row["materials"], source=value["source"],
                components=row["components"], extended=extended)
        row["scope"] = _strings(row["scope"], "check scope")
        if extended:
            row["entry_scope"] = _entry_scopes(row["entry_scope"], row["components"], row["scope"])
        row["evidence"] = _strings(row["evidence"], "evidence reference", paths=True,
                                    allow_empty=row["status"] != "pass")
        row["capabilities"] = _strings(row["capabilities"], "compatibility capability", allow_empty=operation == "install")
        to_version = _version(row["to_version"])
        if operation == "install":
            if row["from_version"] is not None or to_version != version or row["capabilities"]:
                raise ValueError("installation evidence cannot assert a release compatibility pair")
        else:
            from_version = _version(row["from_version"])
            if operation == "upgrade" and (to_version != version or Version(from_version) >= Version(version)):
                raise ValueError("upgrade evidence must identify its exact predecessor and target release")
            if operation == "rollback" and (from_version != version or Version(to_version) > Version(version)):
                raise ValueError("rollback evidence must identify its exact outgoing and retained release")
        result.append(row)
    value["results"] = result
    return deepcopy(value)


def read_update_support(path: Path) -> dict:
    return validate_update_support(_read_record(Path(path), limit=2 * 1024 * 1024))


def read_update_validation(path: Path) -> dict:
    return validate_update_validation(_read_record(Path(path), limit=2 * 1024 * 1024))


def assess_update_support(support, validation) -> dict:
    """Match declarations to precise recorded evidence, without granting offers.

    Only the named check scopes are reported. This function neither executes
    tests, reads the referenced logs, authenticates release facts, nor decides
    that a scope satisfies all product acceptance or business-data admission
    requirements. Runtime paths remain observations of the tested host; exact
    versions, implementation and execution architecture define combinations.
    Fixture passes and failed/blocked native results never back native scope.
    """
    support = validate_update_support(support)
    validation = validate_update_validation(validation)
    version_pairs = {UPDATE_SUPPORT_SCHEMA: UPDATE_VALIDATION_SCHEMA,
                     UPDATE_SUPPORT_V3_SCHEMA: UPDATE_VALIDATION_V3_SCHEMA,
                     UPDATE_SUPPORT_V4_SCHEMA: UPDATE_VALIDATION_V4_SCHEMA}
    material_binding = support["schema_version"] != UPDATE_SUPPORT_SCHEMA
    entry_binding = support["schema_version"] == UPDATE_SUPPORT_V4_SCHEMA
    if version_pairs[support["schema_version"]] != validation["schema_version"]:
        raise ValueError("support and validation require the same sidecar contract version")
    if support["source"] != validation["source"]:
        raise ValueError("support and validation identify different source artifacts")
    combinations = {row["id"]: row for row in support["combinations"]}
    results = {row["id"]: row for row in validation["results"]}
    report = {"source": support["source"], "declared_combinations": list(combinations),
              "evidence_backed_combinations": [], "upgrade_from": [], "rollback_to": [], "diagnostics": []}

    def issue(kind, subject, code, detail):
        report["diagnostics"].append({"kind": kind, "subject": subject, "code": code, "detail": detail})

    def result_matches(row, combination, operation, kind, subject):
        identity = row["id"]
        if row["mode"] != "native" or row["status"] != "pass":
            issue(kind, subject, "not_native_pass", f"Result {identity} is {row['mode']}/{row['status']}")
            return False
        if row["operation"] != operation:
            issue(kind, subject, "operation_mismatch", f"Result {identity} did not test {operation}")
            return False
        if (row["combination"] != combination["id"] or row["platform"] != combination["platform"]
                or row["components"] != combination["components"]
                or runtime_signature(row["runtime_bindings"]) != combination["runtimes"]):
            issue(kind, subject, "combination_mismatch", f"Result {identity} tested different platform, runtimes or components")
            return False
        if material_binding and row["materials"] != combination["materials"]:
            issue(kind, subject, "material_selection_mismatch", f"Result {identity} used different exact release materials")
            return False
        if row["platform"]["emulated"] is None:
            issue(kind, subject, "unknown_emulation", f"Result {identity} did not observe native/emulated execution")
            return False
        return True

    def resolve_references(declaration, kind, subject):
        references = declaration["evidence"]
        if not references:
            issue(kind, subject, "missing_evidence", "No validation result IDs are declared")
            return None
        missing = [identity for identity in references if identity not in results]
        if missing:
            issue(kind, subject, "missing_evidence", "Unknown validation result IDs: " + ", ".join(missing))
            return None
        return [results[identity] for identity in references]

    backed = set()
    for identity, combination in combinations.items():
        rows = resolve_references(combination, "combination", identity)
        if rows is None:
            continue
        matching = [result_matches(row, combination, "install", "combination", identity) for row in rows]
        covered = {scope for row in rows for scope in row["scope"]}
        if not set(combination["scope"]) <= covered:
            issue("combination", identity, "incomplete_scope", "Evidence does not cover every declared check scope")
            continue
        if entry_binding and all(matching) and any(not set(scopes) <= {case for row in rows for case in row["entry_scope"][entry]}
                                 for entry, scopes in combination["entry_scope"].items()):
            issue("combination", identity, "incomplete_entry_scope", "Evidence does not cover every selected entry's declared checks")
            continue
        if all(matching):
            backed.add(identity)
            supported = {"id": identity, "scope": combination["scope"], "evidence": combination["evidence"]}
            if material_binding:
                supported["materials"] = deepcopy(combination["materials"])
            if entry_binding:
                supported["entry_scope"] = deepcopy(combination["entry_scope"])
            report["evidence_backed_combinations"].append(supported)

    version = support["source"]["rpnh_version"]
    for kind, operation in (("upgrade_from", "upgrade"), ("rollback_to", "rollback")):
        for declaration in support[kind]:
            other = declaration["version"]
            rows = resolve_references(declaration, kind, other)
            if rows is None:
                continue
            grouped = {}
            for row in rows:
                grouped.setdefault(row["combination"], []).append(row)
            for identity, group in grouped.items():
                if identity not in backed:
                    issue(kind, other, "unbacked_combination", f"Combination {identity} has no matching native installation scope")
                    continue
                matching = [result_matches(row, combinations[identity], operation, kind, other) for row in group]
                pair = (other, version) if operation == "upgrade" else (version, other)
                if any((row["from_version"], row["to_version"]) != pair for row in group):
                    issue(kind, other, "release_pair_mismatch", "Evidence identifies a different release pair or direction")
                    continue
                capabilities = {capability for row in group for capability in row["capabilities"]}
                if not set(declaration["capabilities"]) <= capabilities:
                    issue(kind, other, "incomplete_capabilities", "Evidence does not cover every declared compatibility capability")
                    continue
                if all(matching):
                    report[kind].append({"version": other, "combination": identity,
                                         "capabilities": declaration["capabilities"],
                                         "evidence": [row["id"] for row in group]})
    return deepcopy(report)


__all__ = (
    "V2_PROTOCOL", "INSTALLED_SCHEMA", "MANAGED_SCHEMA", "PLAN_SCHEMA", "FRONTEND_SCHEMA",
    "UPDATE_SUPPORT_SCHEMA", "UPDATE_VALIDATION_SCHEMA",
    "UPDATE_SUPPORT_V3_SCHEMA", "UPDATE_VALIDATION_V3_SCHEMA",
    "UPDATE_SUPPORT_V4_SCHEMA", "UPDATE_VALIDATION_V4_SCHEMA",
    "PRIMARY_ENTRIES", "ENTRY_FRONTEND", "INSTALLATION_ID_RE", "TARGET_RE", "ProfileSpec", "ProfileCatalog", "InstallPlan",
    "installation_id", "installation_target", "validate_source", "validate_platform", "validate_runtime_bindings", "runtime_signature",
    "validate_installed", "read_installed", "validate_pending_activation", "validate_managed",
    "validate_frontend_view", "read_frontend_view", "read_software_record", "load_profile_catalog", "get_profile", "validate_profile",
    "build_install_plan", "validate_plan", "read_plan",
    "validate_material_selection", "material_selection_signature",
    "validate_update_support", "validate_update_validation", "read_update_support", "read_update_validation", "assess_update_support",
)
