"""Fixed local materials for managed-v2 installation.

The manifest selects data, never commands or Python callbacks.  Python wheels
must describe one exact transitive dependency closure.  Frontend inputs are
copied into the candidate before fixed readiness probes; DSH preparation uses
only the supplied source/dependencies/Web assets and this wheel's adapters.
Published-source records retain their release URL and bind declared wheels and
frontend recipes to their exact material selection. This module never downloads inputs or authenticates asset
origins.  Neither these records nor a successful probe confer release acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from email.parser import BytesParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from typing import Mapping
from zipfile import ZipFile

from cpn.process_supervision import ProcessStartError, start_process

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from .install_contract import (
    build_install_plan, get_profile, load_profile_catalog, read_software_record, validate_installed,
    validate_material_selection, validate_plan, validate_platform, validate_runtime_bindings,
)


MATERIALS_SCHEMA = "rpnh/install_materials/v1"
PUBLISHED_MATERIALS_SCHEMA = "rpnh/install_materials/v2"
_BOOTSTRAP_DISTRIBUTIONS = frozenset({"pip", "setuptools", "wheel"})
_TREE_LIMIT = 200_000
_TREE_BYTES_LIMIT = 8 * 1024**3


@dataclass(frozen=True)
class MaterialSet:
    document: dict
    wheels: tuple[dict, ...]
    plan: dict
    retained_probe_only: bool = False


def _fields(value, keys, label):
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"invalid {label} fields")
    return dict(value)


def _path(value, label, *, directory=False):
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError(f"invalid {label} path")
    path = Path(value)
    if (not path.is_absolute() or path.as_posix() != value
            or path != path.resolve() or ".." in path.parts):
        raise ValueError(f"{label} requires an absolute, real path")
    mode = path.lstat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError(f"{label} requires a {'directory' if directory else 'regular file'}")
    return path


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("frontend entry must be a relative path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or ".." in path.parts
            or value == "." or any(ord(c) < 32 for c in value)):
        raise ValueError("frontend entry must remain inside its material tree")
    return value


def _document(plan):
    return validate_plan(plan.to_document() if hasattr(plan, "to_document") else plan)


def _architecture(value):
    return {"x64": "x86_64", "amd64": "x86_64", "aarch64": "arm64"}.get(value, value)


def selected_root_extras(document) -> tuple[str, ...]:
    """Read the explicit root optional-dependency selection; old records mean []."""
    if not isinstance(document, Mapping):
        raise ValueError("installation materials must be an object")
    values = document.get("root_extras", [])
    if (not isinstance(values, list) or len(values) > 64
            or any(not isinstance(value, str) or len(value) > 80
                   or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None
                   or canonicalize_name(value) != value for value in values)):
        raise ValueError("root_extras must be a bounded list of canonical extra names")
    if len(set(values)) != len(values):
        raise ValueError("root_extras repeats an extra")
    return tuple(sorted(values))


def ensure_explicit_reconfigure_extras(new_document, old_document) -> None:
    """Do not silently drop an existing nonempty optional-dependency selection."""
    selected_root_extras(new_document)
    old_extras = selected_root_extras(old_document)
    if old_extras and "root_extras" not in new_document:
        raise ValueError("reconfiguration of nonempty extras requires explicit root_extras; use [] to clear them")


def _metadata_extras(values):
    if (not isinstance(values, (list, tuple)) or len(values) > 64
            or any(not isinstance(value, str) or len(value) > 80 for value in values)):
        raise ValueError("invalid distribution Provides-Extra declarations")
    normalized = [canonicalize_name(value) for value in values]
    if (any(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None for value in normalized)
            or len(set(normalized)) != len(normalized)):
        raise ValueError("invalid or repeated distribution Provides-Extra declarations")
    return frozenset(normalized)


def _metadata_requirements(values):
    if (not isinstance(values, (list, tuple)) or len(values) > 512
            or any(not isinstance(value, str) for value in values)):
        raise ValueError("invalid or excessive distribution dependency declarations")
    requirements = []
    for text in values:
        requirement = Requirement(text)
        if requirement.url is not None:
            raise ValueError("URL dependencies are not supported by the local wheel recipe")
        requirements.append(requirement)
    return tuple(requirements)


def _wheel_declaration(item):
    item = _fields(item, ("path", "name", "version"), "Python wheel material")
    raw = item["path"]
    if (not isinstance(raw, str) or not raw or "\\" in raw
            or any(ord(c) < 32 for c in raw)):
        raise ValueError("invalid Python wheel material path")
    path = PurePosixPath(raw)
    if (not path.is_absolute() or path.as_posix() != raw or ".." in path.parts
            or path.suffix != ".whl"):
        raise ValueError("only local wheels are allowed; source builds are not installation materials")
    name, version, _build, tags = parse_wheel_filename(path.name)
    if (not isinstance(item["name"], str) or item["name"] != canonicalize_name(item["name"])
            or name != item["name"] or str(version) != item["version"]):
        raise ValueError("wheel filename differs from its exact material name/version")
    return {**item, "tags": frozenset(str(t) for t in tags)}


def _wheel(item):
    declared = _wheel_declaration(item)
    path = _path(declared["path"], "Python wheel")
    name, version = declared["name"], Version(declared["version"])
    with ZipFile(path) as archive:
        names = archive.namelist()
        metadata_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        if (len(metadata_names) != 1 or len(names) != len(set(names))
                or archive.getinfo(metadata_names[0]).file_size > 1024 * 1024):
            raise ValueError("wheel requires one bounded, unique METADATA record")
        for member in archive.infolist():
            member_path = PurePosixPath(member.filename)
            if (member_path.is_absolute() or ".." in member_path.parts
                    or "\\" in member.filename or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("wheel contains an unsafe member")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        if (len(metadata.get_all("Name", [])) != 1
                or len(metadata.get_all("Version", [])) != 1
                or canonicalize_name(metadata["Name"]) != name
                or str(Version(metadata["Version"])) != str(version)):
            raise ValueError("wheel METADATA differs from its material name/version")
        return {**declared, "requires_python": metadata.get("Requires-Python"),
                "requirements": _metadata_requirements(metadata.get_all("Requires-Dist", [])),
                "provides_extras": _metadata_extras(metadata.get_all("Provides-Extra", []))}


def _marker_environment(plan):
    python = plan["runtime_bindings"].get("python")
    if python is None:
        raise ValueError("material plan requires an exact source Python binding")
    target = plan["platform"]
    environment = default_environment()
    environment.update({
        "implementation_name": python["implementation"],
        "implementation_version": python["version"],
        "python_full_version": python["version"],
        "python_version": ".".join(python["version"].split(".")[:2]),
        "os_name": "posix", "sys_platform": target["os"],
        "platform_machine": target["architecture"],
        "platform_release": target["os_release"],
        "platform_system": "Darwin" if target["os"] == "darwin" else "Linux",
        "platform_python_implementation": {"cpython": "CPython", "pypy": "PyPy"}.get(
            python["implementation"], python["implementation"]),
        "extra": "",
    })
    return environment


def _require_source_materials(wheels, plan, root_extras=(), published_selection=None):
    """Match input declarations, without treating local bytes as official assets.

    Retained probes use the same declaration check after original inputs have
    disappeared.  File existence/size belongs to initial material loading;
    installed distribution observations separately recheck the closure.
    """
    indexed = {wheel["name"]: wheel for wheel in wheels}
    if len(indexed) != len(wheels):
        raise ValueError("material list repeats a distribution")
    rpnh = indexed.get("rpnh-harness")
    source = plan["source"]
    if source["kind"] == "local_candidate":
        if (published_selection is not None or rpnh is None
                or rpnh["path"] != source["artifact"]["location"]
                or rpnh["version"] != plan["rpnh_version"]):
            raise ValueError("materials must include the exact explicitly selected local RPNH wheel")
    else:
        if published_selection is None:
            raise ValueError("published materials require an explicit published selection")
        selection = validate_material_selection(published_selection, source=source)
        if list(root_extras) != selection["root_extras"]:
            raise ValueError("material root_extras differ from the published selection")
        actual = sorted((wheel["name"], wheel["version"], PurePosixPath(wheel["path"]).name)
                        for wheel in wheels)
        expected = sorted((wheel["name"], wheel["version"], wheel["filename"])
                          for wheel in selection["python_wheels"])
        if actual != expected:
            raise ValueError("local wheel declarations differ from the exact published selection")
    return indexed


def _closure(wheels, plan, root_extras=(), *, published_selection=None):
    if not isinstance(root_extras, (tuple, list)):
        raise ValueError("root_extras requires an explicit extra-name sequence")
    root_extras = selected_root_extras({"root_extras": list(root_extras)})
    indexed = _require_source_materials(wheels, plan, root_extras, published_selection)
    rpnh = indexed["rpnh-harness"]
    if set(root_extras) - rpnh.get("provides_extras", frozenset()):
        raise ValueError("root_extras names an extra not declared by the selected RPNH distribution")
    environment = _marker_environment(plan)
    python_version = Version(environment["python_full_version"])
    for wheel in wheels:
        if wheel["requires_python"] and python_version not in SpecifierSet(wheel["requires_python"]):
            raise ValueError(f"{wheel['name']} does not support the selected Python version")
    extras = {"rpnh-harness": {"", *root_extras}}
    pending = ["rpnh-harness"]
    while pending:
        name = pending.pop()
        for requirement in indexed[name]["requirements"]:
            if requirement.marker is not None and not any(
                    requirement.marker.evaluate({**environment, "extra": extra})
                    for extra in extras[name]):
                continue
            dependency = canonicalize_name(requirement.name)
            selected = indexed.get(dependency)
            if selected is None or Version(selected["version"]) not in requirement.specifier:
                raise ValueError(f"local wheel closure does not satisfy {name}: {requirement}")
            requested_extras = {canonicalize_name(extra) for extra in requirement.extras}
            if requested_extras - selected.get("provides_extras", frozenset()):
                raise ValueError(f"local wheel closure requests an undeclared extra of {dependency}: {requirement}")
            requested_extras.add("")
            old = extras.get(dependency, set())
            if not requested_extras <= old:
                extras[dependency] = old | requested_extras
                pending.append(dependency)
    if set(indexed) != set(extras):
        raise ValueError("local wheel materials contain distributions outside the selected runtime closure")


def _tree_check(source: Path):
    """Allow only owned input data and internal links in the selected tree.

    This is a one-time material import check, not a recurring business-root
    scan.  copy2 below breaks source hardlinks; internal relative symlinks keep
    their relationship after import, including pnpm's local layout.
    """
    count = total = 0
    for root, directories, files in os.walk(source, followlinks=False):
        for name in (*directories, *files):
            path = Path(root) / name
            info = path.lstat()
            count += 1
            if count > _TREE_LIMIT:
                raise ValueError("frontend material tree exceeds the file limit")
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(path)
                if os.path.isabs(link) or not path.resolve().is_relative_to(source):
                    raise ValueError("frontend material contains a link outside its private tree")
                if not path.exists():
                    raise ValueError("frontend material contains a broken link")
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > _TREE_BYTES_LIMIT:
                    raise ValueError("frontend material tree exceeds the byte limit")
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError("frontend material contains a special file")


def _macho_header_cpu(header, size):
    """Read a complete 64-bit header, without claiming loader/readiness success."""
    if len(header) < 32 or size < 32 or header[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("native frontend has no complete 64-bit Mach-O header")
    cpu, subtype = struct.unpack_from("<II", header, 4)
    if struct.unpack_from("<I", header, 20)[0] > size - 32:
        raise ValueError("native frontend Mach-O load-command extent exceeds its slice")
    return cpu, subtype


def _native_header(path, target):
    architecture = _architecture(target["architecture"])
    with path.open("rb") as stream:
        header = stream.read(4096)
        if target["os"] == "linux":
            if len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
                raise ValueError("native frontend is not a supported 64-bit Linux ELF")
            actual = {62: "x86_64", 183: "arm64"}.get(struct.unpack_from("<H", header, 18)[0])
            if actual != architecture:
                raise ValueError("native frontend ELF architecture differs from the selected platform")
            return
        wanted = {"x86_64": 0x01000007, "arm64": 0x0100000C}.get(architecture)
        size = os.fstat(stream.fileno()).st_size
        if header[:4] == b"\xcf\xfa\xed\xfe":
            cpu, _subtype = _macho_header_cpu(header, size)
            if cpu == wanted:
                return
        elif len(header) >= 8 and header[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
            # FAT declarations locate slices; they do not establish that those
            # bytes exist or contain the claimed CPU. Read from this same FD.
            # Apple fat_arch/fat_arch_64 use big-endian offsets and lengths.
            wide = header[:4] == b"\xca\xfe\xba\xbf"
            count = struct.unpack_from(">I", header, 4)[0]
            stride = 32 if wide else 20
            table_end = 8 + count * stride
            if not (0 < count <= 32 and table_end <= len(header)):
                raise ValueError("native frontend has an incomplete macOS architecture table")
            selected = False
            for index in range(count):
                start = 8 + index * stride
                cpu, subtype = struct.unpack_from(">II", header, start)
                offset, length = struct.unpack_from(">QQ" if wide else ">II", header, start + 8)
                if offset < table_end or length < 32 or offset > size or length > size - offset:
                    raise ValueError("native frontend macOS architecture slice is outside its file")
                if cpu != wanted:
                    continue
                stream.seek(offset)
                actual_cpu, actual_subtype = _macho_header_cpu(stream.read(32), length)
                # CPU_SUBTYPE_MASK contains capability bits, as in dyld's
                # slice/header correspondence check; it is not a CPU subtype.
                if (actual_cpu != cpu or (actual_subtype & 0x00FFFFFF) != (subtype & 0x00FFFFFF)):
                    raise ValueError("native frontend Mach-O slice CPU differs from its architecture table")
                selected = True
            if selected:
                return
        raise ValueError("native frontend has no declared macOS architecture slice")


def _npm_root(source, specification):
    _manager_config(source)
    package = read_software_record(source / "package.json", limit=1024 * 1024)
    locked = read_software_record(source / "package-lock.json", limit=16 * 1024 * 1024)
    expected = {specification.coordinate["package"]: specification.version}
    if (not isinstance(package, dict) or package.get("private") is not True
            or package.get("dependencies") != expected
            or any(package.get(key) for key in ("scripts", "workspaces", "devDependencies", "optionalDependencies", "overrides"))):
        raise ValueError("npm material requires a private root with its exact selected package and no root scripts")
    if (not isinstance(locked, dict) or locked.get("lockfileVersion") not in (2, 3)
            or not isinstance(locked.get("packages"), dict)
            or locked["packages"].get("", {}).get("dependencies") != expected):
        raise ValueError("npm root manifest and package-lock do not describe the same exact frontend")
    for name, item in locked["packages"].items():
        if name == "":
            continue
        _relative(name)
        if (not name.startswith("node_modules/") or not isinstance(item, dict)
                or item.get("link") or not isinstance(item.get("version"), str)
                or not item["version"] or any(c in item["version"] for c in "^~* <>=|")):
            raise ValueError("npm lock contains an unsupported linked or unpinned package")
        resolved = item.get("resolved")
        if resolved is not None and (not isinstance(resolved, str) or not resolved.startswith("https://")):
            raise ValueError("npm lock contains an unsupported source outside its offline package cache")
    _path(str(source / ".rpnh-npm-cache"), "fixed offline npm cache", directory=True)
    # The v2 OpenCode package needs its postinstall to select/generate native
    # payloads. The fixed offline recipe deliberately uses an explicit native
    # input for that profile, instead of pretending --ignore-scripts suffices.
    if specification.entry == "opencode" and specification.version.startswith("2."):
        raise ValueError("OpenCode v2 managed materials require its explicit selected native_file")
    return locked


def _manager_config(source):
    """Permit small local layout settings, not redirection or runtime hooks."""
    path = source / ".npmrc"
    if not path.exists():
        return
    _path(str(path), "local package-manager configuration")
    if path.stat().st_size > 32 * 1024:
        raise ValueError("local package-manager configuration is too large")
    boolean_keys = {"auto-install-peers", "strict-peer-dependencies", "engine-strict",
                    "link-workspace-packages", "prefer-workspace-packages",
                    "shared-workspace-lockfile", "shamefully-hoist"}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not ((key in boolean_keys and value in {"true", "false"})
                                 or (key == "node-linker" and value in {"isolated", "hoisted"})):
            raise ValueError("fixed frontend recipe does not permit this .npmrc setting: " + key)


def _local_reference(source, base, text):
    if (not text or "\\" in text or "%" in text or text.startswith(("/", "~"))
            or any(character.isspace() for character in text)
            or not (base / text).resolve().is_relative_to(source)):
        raise ValueError("pnpm material references a path outside its private source tree")


def _pnpm_inputs(source):
    """Check the narrow, pinned DSH YAML path surface before pnpm can read it.

    This is not a generic YAML parser. Unsupported alias/tag/escaped/multiline
    forms fail closed; the published profiles use ordinary mapping/list data.
    The package manager remains responsible for the frozen lock semantics.
    """
    _manager_config(source)
    workspace = source / "pnpm-workspace.yaml"
    _path(str(workspace), "DSH workspace material")
    allowed_workspace_keys = {"packages", "overrides", "peerDependencyRules", "allowBuilds",
                              "minimumReleaseAgeExclude", "patchedDependencies"}
    package_patterns = []
    for filename in ("pnpm-workspace.yaml", "pnpm-lock.yaml"):
        path = _path(str(source / filename), "fixed pnpm material")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("fixed pnpm material exceeds its size bound")
        section, importer = "", source
        for original in path.read_text(encoding="utf-8").splitlines():
            line = original.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            if ("\\" in line or "\t" in line
                    or re.search(r"(?:^|[\s:\[,])(?:!!|!<|&[A-Za-z]|\*[A-Za-z])", line)
                    or re.search(r":\s*[|>][+-]?\s*$", line)):
                raise ValueError("unsupported YAML indirection in fixed pnpm materials")
            if not line.startswith(" "):
                head = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*):(?:\s+.*)?", line)
                if head is None:
                    raise ValueError("unsupported root YAML shape in fixed pnpm materials")
                section, importer = head[1], source
                if filename == "pnpm-workspace.yaml" and section not in allowed_workspace_keys:
                    raise ValueError("unsupported DSH workspace configuration: " + section)
                if section == "packages" and filename == "pnpm-workspace.yaml" and line != "packages:":
                    raise ValueError("fixed DSH workspace requires its ordinary package pattern list")
            elif filename == "pnpm-lock.yaml" and section == "importers":
                match = re.fullmatch(r"  (\S.*):", line)
                if match:
                    relative = match[1].strip("'\"")
                    _local_reference(source, source, relative)
                    importer = (source / relative).resolve()
            elif filename == "pnpm-workspace.yaml" and section == "packages":
                match = re.fullmatch(r"  - (.+)", line)
                if match is None:
                    raise ValueError("unsupported DSH package pattern")
                pattern = match[1].strip("'\"")
                include_pattern = pattern[1:] if pattern.startswith("!") else pattern
                if (not include_pattern or any(part not in {"*", "**"}
                        and re.fullmatch(r"[A-Za-z0-9@_+.-]+", part) is None
                        or part in {".", ".."} for part in include_pattern.split("/"))):
                    raise ValueError("fixed DSH workspace permits only ordinary internal glob segments")
                _local_reference(source, source, include_pattern)
                package_patterns.append(pattern)
            if filename == "pnpm-workspace.yaml" and section == "patchedDependencies" and line.startswith("  "):
                match = re.fullmatch(r"  .+:\s+(.+)", line)
                if match is None:
                    raise ValueError("unsupported DSH local patch declaration")
                _local_reference(source, source, match[1].strip("'\""))
            for match in re.finditer(r"(?:file|link):([^\s'\"(){}\[\],]+)", line):
                _local_reference(source, importer, match[1])
                tail = line[match.end():].lstrip()
                if tail and tail[0] not in "'\")}] ,":
                    raise ValueError("unsupported whitespace in pnpm local path")
    # Workspace package manifests are data too; ignore-scripts does not stop
    # file/link dependencies or local runtime-manager settings from being read.
    manifests = {source / "package.json"}
    for pattern in package_patterns:
        if not pattern.startswith("!"):
            manifests.update(directory / "package.json" for directory in source.glob(pattern)
                             if directory.is_dir() and (directory / "package.json").is_file())
    for path in manifests:
        package = read_software_record(path, limit=1024 * 1024)
        if package.get("pnpm") or package.get("devEngines"):
            raise ValueError("fixed DSH material cannot supply an extra package-manager/runtime configuration")
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            declarations = package.get(field, {})
            if not isinstance(declarations, dict):
                raise ValueError("invalid DSH package dependency declarations")
            for value in declarations.values():
                if not isinstance(value, str):
                    raise ValueError("invalid DSH package dependency specifier")
                if value.startswith(("file:", "link:")):
                    _local_reference(source, path.parent, value.partition(":")[2])


def _frontend_materials(document, plan, catalog, *, installed=False):
    selected = set(plan["components"]) - {"basic"}
    if not isinstance(document, Mapping) or set(document) != selected:
        raise ValueError("frontend materials must exactly cover every selected non-Basic component")
    from .frontend_launch import _entry_kind
    for entry, value in document.items():
        if not isinstance(value, Mapping):
            raise ValueError("frontend material must be an object")
        kind = value.get("kind")
        fields = ("kind", "path", "entry", "artifact") if kind == "node_tree" else ("kind", "path", "artifact")
        _fields(value, fields, "frontend material")
        if kind not in {"native_file", "node_tree", "external", "dsh_tree"}:
            raise ValueError("unknown fixed frontend material recipe")
        selection = plan["components"][entry]
        binding = selection["binding"]
        specification = get_profile(entry, selection["profile"], catalog=catalog)
        is_tree = kind in {"node_tree", "dsh_tree"}
        raw_source = value["path"]
        if (not isinstance(raw_source, str) or not raw_source or "\\" in raw_source
                or not PurePosixPath(raw_source).is_absolute()
                or PurePosixPath(raw_source).as_posix() != raw_source
                or ".." in PurePosixPath(raw_source).parts
                or any(ord(c) < 32 for c in raw_source)):
            raise ValueError("frontend material requires a normalized absolute input path")
        source = Path(raw_source) if installed else _path(raw_source, "frontend material", directory=is_tree)
        artifact = _fields(value["artifact"], ("kind", "name", "version", "variant", "architecture"), "frontend artifact")
        if (artifact["version"] != specification.version
                or not isinstance(artifact["variant"], str) or not artifact["variant"]
                or _architecture(artifact["architecture"]) != _architecture(plan["platform"]["architecture"])):
            raise ValueError("frontend artifact must identify the exact selected version and platform architecture")
        if kind == "external":
            if binding != {"kind": "external", "path": str(source)} or entry == "dsh-web":
                raise ValueError("external frontend material differs from the explicit binding")
        elif binding["kind"] == "external":
            raise ValueError("external frontend cannot silently become installation-owned")
        if binding["kind"] == "source_import" and binding["path"] != str(source):
            raise ValueError("DSH source import material differs from the explicit binding")
        if installed:
            if kind == "node_tree":
                _relative(value["entry"])
            expected_name = "@deepseek-ai/dsh-root" if entry == "dsh-web" else specification.coordinate.get("package")
            if (artifact["name"] != expected_name
                    or (entry == "dsh-web") != (kind == "dsh_tree")
                    or artifact["kind"] not in ({"dsh_source"} if entry == "dsh-web" else {"native", "npm"})):
                raise ValueError("retained frontend materials differ from their fixed profile")
            continue
        if is_tree:
            _tree_check(source)
        if entry == "dsh-web":
            if kind != "dsh_tree" or artifact["kind"] != "dsh_source" or artifact["name"] != "@deepseek-ai/dsh-root":
                raise ValueError("DSH needs a private, fixed source-tree recipe")
            package = read_software_record(source / "package.json", limit=1024 * 1024)
            if (package.get("name") != artifact["name"] or package.get("version") != specification.version
                    or package.get("packageManager") != "pnpm@" + specification.coordinate["pnpm"]):
                raise ValueError("DSH source package/version/package manager differs from its profile")
            _path(str(source / "pnpm-lock.yaml"), "DSH frozen dependency lock")
            _pnpm_inputs(source)
            _path(str(source / ".rpnh-pnpm-store"), "fixed offline pnpm store", directory=True)
            _path(str(source / ".rpnh-pnpm-cache"), "fixed offline pnpm metadata cache", directory=True)
            if "node" not in plan["runtime_bindings"] or "pnpm" not in plan["runtime_bindings"]:
                raise ValueError("DSH material requires exact Node and pnpm bindings")
            if plan["runtime_bindings"]["pnpm"]["version"] != specification.coordinate["pnpm"]:
                raise ValueError("DSH pnpm runtime differs from the fixed profile")
            from cpn.frontend.dsh_assets import _ready
            for name in specification.web_packages:
                if not _ready(source / "rpnh-frontend/node_modules" / name, name, specification.version):
                    raise ValueError(f"missing fixed local DSH Web asset: {name}")
        else:
            if kind == "dsh_tree" or artifact["name"] != specification.coordinate.get("package"):
                raise ValueError("frontend artifact name differs from the selected package coordinate")
            if kind == "node_tree":
                _relative(value["entry"])
                _npm_root(source, specification)
                if "node" not in plan["runtime_bindings"] or "npm" not in plan["runtime_bindings"]:
                    raise ValueError("fixed npm tree requires exact Node and npm bindings")
                continue  # npm ci creates this entry in the candidate, never in its input tree.
            executable = source / _relative(value["entry"]) if kind == "node_tree" else source
            if not executable.resolve().is_relative_to(source) and is_tree:
                raise ValueError("frontend entry escapes its material tree")
            executable = executable.resolve()
            actual_kind = _entry_kind(executable, specification)
            if ((kind == "native_file" and actual_kind != "native")
                    or artifact["kind"] != ("npm" if actual_kind == "node_wrapper" else "native")):
                raise ValueError("frontend material recipe differs from its actual launch format")
            if actual_kind == "native":
                _native_header(executable, plan["platform"])
                if not os.access(executable, os.X_OK):
                    raise ValueError("native material is not executable")
            elif "node" not in plan["runtime_bindings"]:
                raise ValueError("Node-wrapper material requires an exact Node binding")


def _require_published_frontends(document, plan, selection=None):
    """Match fixed declarations without reopening acquired or retained inputs.

    The existing readers validate the published selection and its origin. This
    pure correspondence check also runs before preparation because MaterialSet
    retains mutable dictionaries after its initial read.
    """
    selected = set(plan["components"]) - {"basic"}
    if not isinstance(document, Mapping) or set(document) != selected:
        raise ValueError("frontend materials must exactly cover every selected non-Basic component")
    if plan["source"]["kind"] == "local_candidate":
        if selection is not None:
            raise ValueError("local frontend materials cannot claim a published selection")
        return
    if not isinstance(selection, Mapping):
        raise ValueError("published frontend materials require an explicit published selection")
    if "frontends" not in selection:
        if selected or set(plan["runtime_bindings"]) != {"python"}:
            raise ValueError("published v3 materials support only Basic with its Python runtime")
        return
    declared = selection["frontends"]
    if not isinstance(declared, Mapping) or set(declared) != selected:
        raise ValueError("published frontend materials do not bind the complete selected set")
    for entry in selected:
        expected = declared[entry]
        fields = ("kind", "path", "artifact", "entry") if expected["kind"] == "node_tree" else (
            "kind", "path", "artifact")
        actual = _fields(document[entry], fields, "published frontend material")
        if (plan["components"][entry]["binding"] != {"kind": "managed"}
                or any(actual.get(key) != expected.get(key) for key in ("kind", "artifact", "entry"))):
            raise ValueError("local frontend recipe differs from the exact published selection")


def load_materials(path: Path, plan, catalog=None, *, installed=False) -> MaterialSet:
    """Read exact selection; retained probes do not reopen original inputs.

    ``installed`` is only for an already prepared I: its actual distribution
    inventory, dependency checks and fixed frontend entries are probed later.
    It cannot be used to create a new environment or prepare frontend assets.
    Version 1 remains the local-candidate recipe.  Version 2 requires an exact
    published_selection and explicit root_extras, while all material paths still
    identify local inputs. V4 selections additionally bind every frontend recipe.
    Matching them proves correspondence/closure only;
    the parent release resolver/executor must establish the actual asset origin.
    """
    plan = _document(plan)
    if plan["conflicts"]:
        raise ValueError("cannot prepare materials for a conflicting plan")
    value = read_software_record(Path(path), limit=1024 * 1024)
    required = ("schema_version", "platform", "runtime_bindings", "python_wheels", "frontends")
    schema = value.get("schema_version") if isinstance(value, Mapping) else None
    if schema == MATERIALS_SCHEMA:
        fields = (*required, "root_extras") if "root_extras" in value else required
        if plan["source"]["kind"] != "local_candidate":
            raise ValueError("published sources require installation materials v2")
    elif schema == PUBLISHED_MATERIALS_SCHEMA:
        fields = (*required, "root_extras", "published_selection")
        if plan["source"]["kind"] != "published_release":
            raise ValueError("installation materials v2 require a published source")
    else:
        raise ValueError("unsupported installation material schema")
    value = _fields(value, fields, "installation materials")
    published_selection = None
    if schema == PUBLISHED_MATERIALS_SCHEMA:
        published_selection = validate_material_selection(value["published_selection"], source=plan["source"])
        value["published_selection"] = published_selection
    if (validate_platform(value["platform"]) != plan["platform"]
            or validate_runtime_bindings(value["runtime_bindings"]) != plan["runtime_bindings"]):
        raise ValueError("materials and selected plan have different platform/runtime bindings")
    if not isinstance(value["python_wheels"], list) or not 1 <= len(value["python_wheels"]) <= 256:
        raise ValueError("materials require a bounded list of exact local wheels")
    _require_published_frontends(value["frontends"], plan, published_selection)
    root_extras = selected_root_extras(value)
    wheels = tuple((_wheel_declaration if installed else _wheel)(item) for item in value["python_wheels"])
    if installed:
        _require_source_materials(wheels, plan, root_extras, published_selection)
    else:
        _closure(wheels, plan, root_extras, published_selection=published_selection)
        if published_selection is not None:
            sizes = {row["name"]: row["asset"]["size"] for row in published_selection["python_wheels"]}
            if any(Path(wheel["path"]).stat().st_size != sizes[wheel["name"]] for wheel in wheels):
                raise ValueError("local wheel size differs from the exact published asset declaration")
    if catalog is None:
        if installed:
            candidates = [p for p in (Path(path).parent / "venv/lib").glob("python*/site-packages")
                          if (p / "cpn").is_dir()]
            if len(candidates) != 1:
                raise ValueError("retained installation has no unique local package catalog")
            catalog = load_profile_catalog(package_root=candidates[0])
        else:
            rpnh = next(wheel for wheel in wheels if wheel["name"] == "rpnh-harness")
            catalog = load_profile_catalog(wheel=Path(rpnh["path"]))
    if catalog.rpnh_version != plan["rpnh_version"]:
        raise ValueError("material catalog differs from the selected target wheel")
    _frontend_materials(value["frontends"], plan, catalog, installed=installed)
    return MaterialSet(deepcopy(value), wheels, deepcopy(plan), installed)


def describe_basic_materials(candidate_wheel: Path, wheelhouse: Path, python=None, *, root_extras=()) -> dict:
    """Describe Basic and explicit root extras; never resolve/download a version.

    Every wheel in the selected directory participates. Duplicates, unneeded
    distributions and missing dependencies are errors, not requests to choose a
    latest package. No installation root or material file is written here.
    """
    from .frontend_launch import observe_runtime
    from .platform_support import require_platform_support
    if not isinstance(root_extras, (list, tuple)):
        raise ValueError("root_extras requires an explicit extra-name sequence")
    root_extras = selected_root_extras({"root_extras": list(root_extras)})
    candidate = _path(str(Path(candidate_wheel).expanduser().resolve()), "candidate wheel")
    supplied = _path(str(Path(wheelhouse).expanduser().resolve()), "wheelhouse", directory=True)
    catalog = load_profile_catalog(wheel=candidate)
    observation = observe_runtime("python", Path(python or sys.executable).expanduser().resolve())
    runtime = {"python": {"path": str(observation.path), "version": observation.version,
                          "implementation": observation.implementation, "architecture": observation.architecture}}
    host = require_platform_support()
    target = {"os": host["platform"], "architecture": host["machine"], "os_release": host["os_release"],
              "libc": host["libc"], "emulated": None}
    source = {"kind": "local_candidate", "rpnh_version": catalog.rpnh_version, "release": None,
              "artifact": {"filename": candidate.name, "location": str(candidate)}, "channel": None}
    plan = build_install_plan(source, target, runtime, catalog=catalog).to_document()
    selected = sorted(set(supplied.glob("*.whl")) | {candidate})
    if len(selected) > 256:
        raise ValueError("wheelhouse exceeds the fixed material count bound")
    declarations = []
    for path in selected:
        name, version, _build, _tags = parse_wheel_filename(path.name)
        declarations.append({"path": str(path), "name": str(name), "version": str(version)})
    wheels = tuple(_wheel(item) for item in declarations)
    _closure(wheels, plan, root_extras)
    return {"schema_version": MATERIALS_SCHEMA, "platform": target, "runtime_bindings": runtime,
            "python_wheels": declarations, "frontends": {}, "root_extras": list(root_extras)}


def _environment(directory):
    environment = {key: os.environ[key] for key in ("LANG", "LC_ALL", "LC_CTYPE") if key in os.environ}
    environment.update({
        "PATH": os.defpath, "HOME": str(directory), "PYTHONNOUSERSITE": "1",
        "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "npm_config_offline": "true", "npm_config_audit": "false", "npm_config_fund": "false",
        "COREPACK_ENABLE_NETWORK": "0", "COREPACK_ENABLE_PROJECT_SPEC": "0",
        "CI": "true",
        "TSX_DISABLE_CACHE": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    })
    if sys.platform == "linux":
        state = Path(directory) / ".material-state"
        for child in ("tmp", "cache", "config", "data", "state", "runtime"):
            (state / child).mkdir(mode=0o700, parents=True, exist_ok=True)
        environment.update({
            "TMPDIR": str(state / "tmp"), "TMP": str(state / "tmp"), "TEMP": str(state / "tmp"),
            "XDG_CACHE_HOME": str(state / "cache"), "XDG_CONFIG_HOME": str(state / "config"),
            "XDG_DATA_HOME": str(state / "data"), "XDG_STATE_HOME": str(state / "state"),
            "XDG_RUNTIME_DIR": str(state / "runtime"),
            "PIP_CACHE_DIR": str(state / "cache/pip"), "UV_CACHE_DIR": str(state / "cache/uv"),
            "npm_config_cache": str(state / "cache/npm"), "NODE_COMPILE_CACHE": str(state / "cache/node"),
        })
    return environment


def _run(argv, *, directory, timeout=120, environment=None):
    from .install_executor import _require_group_absent, _stop_owned_process
    # Validated candidate commands remain owned by the outer executor tree.
    # This key controls lifetime ownership only; candidate_context grants authority.
    own_group = "RPNH_CANDIDATE_ID" not in os.environ
    with tempfile.TemporaryFile(dir=directory) as output_file, tempfile.TemporaryFile(dir=directory) as error_file:
        deadline = time.monotonic() + timeout
        command = [str(arg) for arg in argv]
        options = dict(env=_environment(directory) if environment is None else dict(environment),
                       cwd=directory, stdin=subprocess.DEVNULL, stdout=output_file, stderr=error_file)
        supervised = sys.platform == "linux" and own_group
        if supervised:
            try:
                process = start_process(command, **options, deadline=deadline)
            except ProcessStartError as exc:
                if exc.code == "timeout":
                    raise subprocess.TimeoutExpired(command, timeout) from exc
                raise
        else:
            process = subprocess.Popen(command, **options, start_new_session=own_group)
        try:
            process.wait(timeout=max(0, deadline - time.monotonic())
                         if sys.platform == "linux" else timeout)
        except BaseException:
            _stop_owned_process(process, own_group=own_group)
            raise
        if own_group:
            # Reject incomplete cleanup and normally exiting targets that left
            # descendants, even when the Linux supervisor has since reaped them.
            _require_group_absent(process, label="fixed material command")
        if max(os.fstat(output_file.fileno()).st_size, os.fstat(error_file.fileno()).st_size) > 2 * 1024 * 1024:
            raise RuntimeError("fixed local material command exceeded its output limit")
        output_file.seek(0)
        error_file.seek(0)
        output = output_file.read(2 * 1024 * 1024 + 1).decode("utf-8", errors="replace")
        error = error_file.read(2 * 1024 * 1024 + 1).decode("utf-8", errors="replace")
        if max(len(output), len(error)) > 2 * 1024 * 1024:
            raise RuntimeError("fixed local material command exceeded its output limit")
        if process.returncode:
            detail = (error or output)[-3000:].strip()
            raise RuntimeError(f"fixed local material command failed (exit {process.returncode}): {detail}")
        return output


def _runtime_observations(bindings, directory):
    from .frontend_launch import observe_runtime
    actual = {}
    for name in ("python", "node"):
        if name not in bindings:
            continue
        observation = observe_runtime(name, bindings[name]["path"], environment=_environment(directory), cwd=directory)
        actual[name] = {"path": str(observation.path), "version": observation.version,
                        "implementation": observation.implementation, "architecture": observation.architecture}
        if actual[name] != bindings[name]:
            raise ValueError(f"selected {name} runtime changed after material planning")
    for name in ("npm", "pnpm"):
        if name not in bindings:
            continue
        if "node" not in actual:
            raise ValueError("package-manager runtime requires the exact Node binding")
        binding = bindings[name]
        executable = _path(binding["path"], name + " runtime")
        with executable.open("rb") as stream:
            first_line = stream.read(256).split(b"\n", 1)[0].rstrip(b"\r")
        if first_line != b"#!/usr/bin/env node":
            raise ValueError("package-manager runtime must be its explicit Node script")
        package = None
        for parent in tuple(executable.parents)[:4]:
            candidate = parent / "package.json"
            if candidate.is_file():
                document = read_software_record(candidate, limit=1024 * 1024)
                if document.get("name") == name:
                    declared_bin = document.get("bin")
                    declared_bin = declared_bin.get(name) if isinstance(declared_bin, dict) else declared_bin
                    if (not isinstance(declared_bin, str)
                            or (parent / _relative(declared_bin)).resolve() != executable):
                        raise ValueError("package-manager runtime is not its declared package bin")
                    package = document
                    break
        if package is None or package.get("version") != binding["version"]:
            raise ValueError("package-manager runtime has no matching exact package metadata")
        observed = _run([actual["node"]["path"], executable, "--version"], directory=directory, timeout=15).strip()
        if (observed != binding["version"] or binding["implementation"] != name
                or binding["architecture"] != actual["node"]["architecture"]):
            raise ValueError("package-manager runtime observation differs from its binding")
        actual[name] = deepcopy(binding)
    return actual


def _check_host(plan):
    from .platform_support import require_platform_support
    observation = require_platform_support()
    target = plan["platform"]
    if (observation["platform"] != target["os"] or observation["os_release"] != target["os_release"]
            or _architecture(observation["machine"]) != _architecture(target["architecture"])
            or observation["libc"] != target["libc"]):
        raise ValueError("installation materials target a different actual host platform")
    if target["emulated"] is True:
        raise ValueError("emulated material execution is outside the native installation recipe")


def _inventory(python, directory, *, metadata=False):
    code = ("import importlib.metadata as m,json,sys,site; from pathlib import Path; "
            "print(json.dumps({'prefix':str(Path(sys.prefix).resolve()),'user_site':site.ENABLE_USER_SITE,"
            "'distributions':[{'name':info['Name'],'version':info['Version'],"
            "'location':str(Path(d.locate_file('')).resolve()),"
            "'requires_python':info.get('Requires-Python'),"
            "'requires_dist':info.get_all('Requires-Dist',[]),"
            "'provides_extras':info.get_all('Provides-Extra',[])} for d in m.distributions() for info in [d.metadata]]}))")
    value = json.loads(_run([python, "-I", "-B", "-c", code], directory=directory))
    prefix = directory / "venv"
    if value["prefix"] != str(prefix) or value["user_site"] is not False:
        raise ValueError("candidate Python is not its isolated installation environment")
    result = {}
    for item in value["distributions"]:
        name = canonicalize_name(item["name"])
        if name in result or not Path(item["location"]).is_relative_to(prefix):
            raise ValueError("candidate Python has duplicate or externally borrowed distributions")
        result[name] = item if metadata else item["version"]
    return result


def _verify_python(directory, materials):
    python = directory / "venv/bin/python"
    inventory = _inventory(python, directory, metadata=True)
    actual = {name: item["version"] for name, item in inventory.items()}
    expected = {wheel["name"]: wheel["version"] for wheel in materials.wheels}
    if any(actual.get(name) != version for name, version in expected.items()):
        raise ValueError("candidate installed distribution versions differ from the fixed wheel closure")
    if set(actual) - set(expected) - _BOOTSTRAP_DISTRIBUTIONS:
        raise ValueError("candidate Python contains undeclared runtime distributions")
    # pip check does not activate root extras. Recheck the exact closure using
    # this I's actual METADATA, including for retained probes after source
    # wheels have disappeared. One inventory subprocess supplied all facts.
    observed_wheels = tuple({**wheel,
        "requires_python": inventory[wheel["name"]]["requires_python"],
        "requirements": _metadata_requirements(inventory[wheel["name"]]["requires_dist"]),
        "provides_extras": _metadata_extras(inventory[wheel["name"]]["provides_extras"]),
    } for wheel in materials.wheels)
    root_extras = selected_root_extras(materials.document)
    _closure(observed_wheels, materials.plan, root_extras,
             published_selection=materials.document.get("published_selection"))
    _run([python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "check"], directory=directory)
    return {"distributions": expected, "root_extras": list(root_extras), "bootstrap_distributions": {
        name: actual[name] for name in sorted(set(actual) & _BOOTSTRAP_DISTRIBUTIONS)},
        "system_site_packages": False, "pip_check": "passed"}


def prepare_python_environment(directory: Path, plan, materials: MaterialSet) -> dict:
    """Create one clean venv and install only the previously selected wheels."""
    plan = _document(plan)
    if materials.retained_probe_only or materials.plan != plan:
        raise ValueError("retained material observations cannot prepare a new environment")
    directory = _path(str(Path(directory)), "candidate installation", directory=True)
    _check_host(plan)
    actual = _runtime_observations(plan["runtime_bindings"], directory)
    environment = directory / "venv"
    if environment.exists() or environment.is_symlink():
        raise ValueError("candidate Python preparation never reuses an existing environment")
    _run([actual["python"]["path"], "-I", "-m", "venv", "--copies", environment], directory=directory)
    python = environment / "bin/python"
    tags_code = "from pip._vendor.packaging.tags import sys_tags; import json; print(json.dumps([str(t) for t in sys_tags()]))"
    supported_tags = set(json.loads(_run([python, "-I", "-B", "-c", tags_code], directory=directory)))
    if any(not wheel["tags"] & supported_tags for wheel in materials.wheels):
        raise ValueError("a fixed wheel is incompatible with the actual candidate Python platform tags")
    _run([python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
          "--no-index", "--no-deps", "--no-cache-dir", "--only-binary=:all:",
          *[wheel["path"] for wheel in materials.wheels]], directory=directory)
    _verify_python(directory, materials)
    from .frontend_launch import observe_runtime
    observation = observe_runtime("python", python, environment=_environment(directory), cwd=directory)
    actual["python"] = {"path": str(observation.path), "version": observation.version,
                        "implementation": observation.implementation, "architecture": observation.architecture}
    if (actual["python"]["path"] != str(python)
            or any(actual["python"][key] != plan["runtime_bindings"]["python"][key]
                   for key in ("version", "implementation", "architecture"))):
        raise ValueError("candidate Python differs from the selected interpreter version or is not private")
    return actual


def _copy_tree(source, destination):
    _tree_check(source)
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    _tree_check(destination)


def _prepare_dsh(source, specification, node, directory):
    """Prepare fixed adapters from local assets, without the downloader path."""
    from cpn.frontend.dsh_assets import _ready, official_web_profile
    from cpn.frontend.dsh_launcher import _adapter_modules
    _, manifest = official_web_profile(specification.profile)
    target = source / "rpnh-frontend"
    for name in specification.web_packages:
        if not _ready(target / "node_modules" / name, name, specification.version):
            raise ValueError(f"fixed local DSH Web asset is absent: {name}")
    for module in _adapter_modules():
        (target / module.name).write_bytes(module.read_bytes())
    (target / "web-packages.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    env = _environment(directory)
    env["TSX_TSCONFIG_PATH"] = str(source / "tsconfig.base.json")
    _run([node, "--import", "tsx", str(target / "setup.ts")], directory=source, environment=env)


def _consume_lock(source, kind, bindings, specification):
    """Use the selected package manager and its frozen local root materials."""
    manager = "npm" if kind == "node_tree" else "pnpm"
    lock_name = "package-lock.json" if manager == "npm" else "pnpm-lock.yaml"
    lock_before = (source / lock_name).read_bytes()
    package_before = (source / "package.json").read_bytes()
    if manager == "npm":
        locked = _npm_root(source, specification)
        args = ["ci", "--prefix", str(source), "--offline", "--ignore-scripts", "--no-audit", "--no-fund",
                "--cache", str(source / ".rpnh-npm-cache")]
    else:
        _pnpm_inputs(source)
        args = ["install", "--dir", str(source), "--offline", "--frozen-lockfile", "--ignore-scripts", "--ignore-pnpmfile",
                "--pm-on-fail=error", "--modules-dir", str(source / "node_modules"),
                "--virtual-store-dir", str(source / "node_modules/.pnpm"),
                "--store-dir", str(source / ".rpnh-pnpm-store"),
                "--cache-dir", str(source / ".rpnh-pnpm-cache")]
    _run([bindings["node"]["path"], bindings[manager]["path"], *args], directory=source, timeout=300)
    if ((source / lock_name).read_bytes() != lock_before
            or (source / "package.json").read_bytes() != package_before):
        raise ValueError("fixed frontend package manager changed its frozen root materials")
    if manager == "npm":
        for path, item in locked["packages"].items():
            if not path:
                continue
            metadata = source / path / "package.json"
            if not metadata.exists() and item.get("optional") is True:
                continue  # The selected executable readiness probe still must succeed.
            actual = read_software_record(metadata, limit=1024 * 1024)
            if actual.get("version") != item["version"]:
                raise ValueError("installed npm dependency differs from its fixed lock version")
    _tree_check(source)


def prepare_frontends(directory: Path, plan, materials: MaterialSet) -> dict:
    """Import every selected frontend privately; return actual complete facts."""
    plan = _document(plan)
    if materials.retained_probe_only or materials.plan != plan:
        raise ValueError("retained material observations cannot prepare frontend assets")
    directory = _path(str(Path(directory)), "candidate installation", directory=True)
    indexed = _require_source_materials(materials.wheels, plan, selected_root_extras(materials.document),
                                       materials.document.get("published_selection"))
    _require_published_frontends(materials.document["frontends"], plan,
                                 materials.document.get("published_selection"))
    catalog = load_profile_catalog(wheel=Path(indexed["rpnh-harness"]["path"]))
    _frontend_materials(materials.document["frontends"], plan, catalog)
    components = {"basic": {**deepcopy(plan["components"]["basic"]),
        "launch": {"kind": "builtin", "scope": "installation", "path": "venv/bin/rpnh"},
        "artifact": {"kind": "wheel", "name": "rpnh-harness", "version": plan["rpnh_version"],
                     "variant": "py3-none-any", "architecture": "any"}}}
    if materials.document["frontends"]:
        (directory / "frontends").mkdir(mode=0o700)
    from .frontend_launch import _entry_kind
    for entry, material in materials.document["frontends"].items():
        specification = get_profile(entry, plan["components"][entry]["profile"], catalog=catalog)
        source = Path(material["path"])
        kind = material["kind"]
        if kind == "external":
            executable, scope = source, "external"
        else:
            destination = directory / "frontends" / entry
            if kind == "native_file":
                destination.mkdir(mode=0o700)
                executable = destination / entry
                shutil.copy2(source, executable)
            else:
                _copy_tree(source, destination)
                _consume_lock(destination, kind, plan["runtime_bindings"], specification)
                executable = destination / material["entry"] if kind == "node_tree" else destination
            scope = "installation"
        if kind == "dsh_tree":
            _prepare_dsh(executable, specification, plan["runtime_bindings"]["node"]["path"], directory)
            launch_kind = "dsh_prepared"
        else:
            executable = executable.resolve()
            launch_kind = _entry_kind(executable, specification)
            expected_kind = "npm" if launch_kind == "node_wrapper" else "native"
            if material["artifact"]["kind"] != expected_kind:
                raise ValueError("prepared frontend entry differs from its fixed artifact kind")
            if launch_kind == "native":
                _native_header(executable, plan["platform"])
        components[entry] = {**deepcopy(plan["components"][entry]),
            "launch": {"kind": launch_kind, "scope": scope,
                       "path": str(executable.relative_to(directory)) if scope == "installation" else str(executable)},
            "artifact": deepcopy(material["artifact"])}
    return components


def probe_installation(directory: Path, record, materials: MaterialSet, *, retained=False) -> dict:
    """Probe the installed closure and fixed launch entries, without a task."""
    directory = _path(str(Path(directory)), "candidate installation", directory=True)
    record = validate_installed(record, expected_id=directory.name)
    selected = materials.plan
    if (record["source"] != selected["source"] or record["rpnh_version"] != selected["rpnh_version"]
            or record["installation_id"] != selected["installation_id"]
            or set(record["components"]) != set(selected["components"])):
        raise ValueError("prepared software differs from its material plan")
    for entry, component in record["components"].items():
        if any(component[key] != selected["components"][entry][key] for key in ("profile", "binding")):
            raise ValueError("prepared frontend selection differs from its material plan")
        if entry != "basic":
            material = materials.document["frontends"][entry]
            if component["artifact"] != material["artifact"]:
                raise ValueError("prepared frontend artifact differs from its exact material")
            kind = material["kind"]
            location = (Path(material["path"]) if kind == "external" else
                        directory / "frontends" / entry / entry if kind == "native_file" else
                        directory / "frontends" / entry / material["entry"] if kind == "node_tree" else
                        directory / "frontends" / entry)
            expected_path = str(location) if kind == "external" else str(location.resolve().relative_to(directory))
            if (component["launch"]["path"] != expected_path
                    or component["launch"]["scope"] != ("external" if kind == "external" else "installation")):
                raise ValueError("prepared frontend entry differs from its fixed recipe destination")
    if record["platform"] != materials.document["platform"]:
        raise ValueError("prepared platform differs from the material selection")
    expected_runtime = deepcopy(materials.document["runtime_bindings"])
    # The source interpreter prepares a copied Python at this exact I path.
    # Version, implementation, architecture and all other runtime paths stay fixed.
    expected_runtime["python"]["path"] = str(directory / "venv/bin/python")
    if record["runtime_bindings"] != expected_runtime:
        raise ValueError("prepared runtime differs from the fixed materials and private Python path")
    _check_host(record)
    observed_bindings = record["runtime_bindings"]
    if retained:
        required = {"python"}
        if any(component["launch"]["kind"] in {"node_wrapper", "dsh_prepared"}
               for component in record["components"].values()):
            required.add("node")
        observed_bindings = {name: value for name, value in observed_bindings.items() if name in required}
    _runtime_observations(observed_bindings, directory)
    python_evidence = _verify_python(directory, materials)
    from .frontend_config import FrontendPreferences
    from .frontend_launch import resolve_launch_spec, probe_frontend_version
    evidence = {}
    for entry in record["components"]:
        frontend = "dsh" if entry == "dsh-web" else entry
        preferences = FrontendPreferences(frontend=frontend, installation_id=record["installation_id"])
        spec = resolve_launch_spec(frontend, preferences=preferences, installed=record,
                                   installation_directory=directory,
                                   environment=_environment(directory), cwd=directory)
        if entry == "dsh-web":
            from cpn.frontend.dsh_launcher import probe_dsh_frontend
            node, prepared = probe_dsh_frontend(spec.source, _environment(directory),
                compatibility_profile=spec.profile, node_binary=spec.argv[0])
            if str(Path(node).resolve()) != spec.argv[0] or prepared.resolve() != spec.entry_path:
                raise ValueError("DSH readiness selected a different runtime or entry")
        else:
            probe_frontend_version(spec, _environment(directory), directory)
        evidence[entry] = {"profile": spec.profile, "launch_kind": spec.kind, "readiness": "passed"}
    return {"schema_version": "rpnh/material_probe/v1", "python": python_evidence,
            "frontends": evidence, "runtime_probes": sorted(observed_bindings),
            "native_platform_acceptance": "not_performed",
            "business_execution": "not_performed"}
