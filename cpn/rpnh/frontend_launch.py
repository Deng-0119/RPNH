"""Resolve one immutable frontend launch description without preparing assets.

Managed callers supply the installation already selected under their lifecycle
lease. This module does not discover another I, install a component, create a
HOME, or construct an Owner. Version probes and launch share the returned argv.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from typing import Mapping

from .frontend_config import FrontendPreferences, ReconfigurationRequired, read_frontend_preferences
from .install_contract import (
    ProfileSpec, get_profile, load_profile_catalog, validate_installed,
    validate_runtime_bindings,
)


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    path: Path
    version: str | None = None
    implementation: str | None = None
    architecture: str | None = None


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    frontend: str
    entry: str
    profile: str
    upstream_version: str
    installation_id: str | None
    rpnh_version: str
    kind: str
    entry_path: Path
    source: Path | None
    argv: tuple[str, ...]
    python: RuntimeObservation | None = None
    node: RuntimeObservation | None = None
    pnpm: RuntimeObservation | None = None
    managed: bool = False
    binding_kind: str | None = None
    binding_path: Path | None = None
    artifact_variant: str | None = None
    artifact_architecture: str | None = None


class _ExecutableUnavailable(RuntimeError):
    pass


def _path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("Frontend/runtime path must be nonempty")
    return Path(value).expanduser().resolve()


def _executable(value: str | Path) -> Path:
    path = _path(value)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise _ExecutableUnavailable(f"Selected executable is unavailable: {path}")
    return path


def _binary(value: str | Path, environment: Mapping[str, str]) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("Frontend executable must be nonempty")
    selected = str(Path(value).expanduser())
    found = shutil.which(selected, path=environment.get("PATH", os.defpath))
    if found is None:
        raise _ExecutableUnavailable(f"Selected frontend executable is unavailable: {selected}")
    return _executable(found)


def _run(argv: tuple[str, ...], environment: Mapping[str, str], cwd: Path | None,
         *, timeout: int = 5) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, env=dict(environment), cwd=cwd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Selected executable probe failed: {argv[0]} ({exc})") from exc


def observe_runtime(name: str, path: str | Path, *,
                    environment: Mapping[str, str] | None = None,
                    cwd: Path | None = None) -> RuntimeObservation:
    """Observe the executable itself, including its execution architecture.

    Architecture is the target process's own report (Python platform.machine,
    Node process.arch), not the manager's host architecture. Install records
    must retain that exact value rather than mixing architecture aliases.
    """
    executable = _executable(path)
    inherited = os.environ if environment is None else environment
    env = {key: inherited[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
           if key in inherited}
    if name == "python":
        code = ("import json,platform,sys; from pathlib import Path; "
                "print(json.dumps({'path':str(Path(sys.executable).resolve()),"
                "'version':platform.python_version(),'implementation':sys.implementation.name,"
                "'architecture':platform.machine()}))")
        argv = (str(executable), "-I", "-S", "-B", "-c", code)
    elif name == "node":
        code = ("JSON.stringify({path:require('node:fs').realpathSync(process.execPath),"
                "version:process.versions.bun||process.versions.deno||process.versions.node,"
                "implementation:process.versions.bun?'bun':process.versions.deno?'deno':process.release.name,"
                "architecture:process.arch})")
        argv = (str(executable), "-p", code)
    else:
        raise ValueError("Only Python and Node have runtime observation probes")
    result = _run(argv, env, cwd)
    try:
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError("runtime observation failed")
        observation = json.loads(result.stdout)
        validate_runtime_bindings({name: observation}, require_python=False)
        if observation["path"] != str(executable):
            raise ValueError("runtime reported a different executable")
        if name == "node" and observation["implementation"] != "node":
            raise ValueError("selected runtime is not the declared Node implementation")
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(f"Selected {name} did not provide an exact runtime observation: {executable}") from exc
    return RuntimeObservation(Path(observation["path"]), observation["version"],
                              observation["implementation"], observation["architecture"])


def _bound_runtime(name: str, record: dict, frontend: str,
                   environment: Mapping[str, str], cwd: Path | None) -> RuntimeObservation:
    expected = record["runtime_bindings"].get(name)
    if expected is None:
        raise RuntimeError(f"Selected installation does not declare its {name} runtime")
    observed = observe_runtime(name, expected["path"], environment=environment, cwd=cwd)
    actual = {"path": str(observed.path), "version": observed.version,
              "implementation": observed.implementation, "architecture": observed.architecture}
    if actual != expected:
        raise ReconfigurationRequired(record["installation_id"], frontend, (f"runtime_bindings.{name}",))
    return observed


def _prefix(entry: Path) -> bytes:
    # A native executable can be large. Only inspect the bounded interpreter
    # line; never load its complete content just to choose an invocation.
    with entry.open("rb") as stream:
        return stream.read(256)


def _official_node_wrapper(entry: Path, specification: ProfileSpec) -> None:
    coordinate = specification.coordinate
    if coordinate.get("kind") != "npm":
        raise RuntimeError("Selected profile does not declare a Node package wrapper")
    for directory in tuple(entry.parents)[:4]:
        metadata = directory / "package.json"
        try:
            if not metadata.is_file() or metadata.stat().st_size > 128 * 1024:
                continue
            package = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (not isinstance(package, dict) or package.get("name") != coordinate.get("package")
                or package.get("version") != specification.version):
            continue
        command = "codex" if specification.entry == "codex" else "opencode"
        bins = package.get("bin")
        relative = bins.get(command) if isinstance(bins, dict) else bins
        if not isinstance(relative, str):
            continue
        relative_path = PurePosixPath(relative)
        if (relative_path.is_absolute() or ".." in relative_path.parts
                or "\\" in relative):
            continue
        declared = (directory / relative).resolve()
        if declared == entry and declared.is_relative_to(directory.resolve()):
            return
    raise RuntimeError("Unknown Node wrapper: exact package name, version and bin entry are required")


def _entry_kind(entry: Path, specification: ProfileSpec) -> str:
    prefix = _prefix(entry)
    if not prefix.startswith(b"#!"):
        return "native"
    first_line = prefix.split(b"\n", 1)[0].rstrip(b"\r")
    if first_line != b"#!/usr/bin/env node":
        raise RuntimeError("Unknown frontend wrapper interpreter; use a declared native or official Node entry")
    _official_node_wrapper(entry, specification)
    return "node_wrapper"


def _override_matches(value: str | Path, bound: Path, environment: Mapping[str, str],
                      *, binary: bool) -> bool:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("Frontend override must be a nonempty path")
    if binary and not Path(value).expanduser().is_absolute() and "/" not in str(value):
        found = shutil.which(str(value), path=environment.get("PATH", os.defpath))
        return found is not None and _path(found) == bound
    return _path(value) == bound


def _managed_spec(frontend: str, preferences: FrontendPreferences, record: dict,
                  directory: Path, catalog, environment: Mapping[str, str], cwd: Path | None,
                  explicit_binary, explicit_source, compatibility_profile) -> LaunchSpec:
    identity = record["installation_id"]
    if preferences.installation_id != identity:
        raise ValueError("Frontend preferences belong to another installation")
    if catalog.rpnh_version != record["rpnh_version"]:
        raise ValueError("Launch resolver package differs from the installed RPNH release")
    entry_name = "dsh-web" if frontend == "dsh" else frontend
    component = record["components"].get(entry_name)
    if component is None:
        raise ReconfigurationRequired(identity, frontend, ("frontend",))
    specification = get_profile(entry_name, component["profile"], catalog=catalog)
    if frontend != "basic" and component["artifact"]["version"] != specification.version:
        raise ValueError("Installed artifact and selected frontend profile have different versions")
    launch = component["launch"]
    declared = (directory / launch["path"] if launch["scope"] == "installation"
                else Path(launch["path"]))
    entry = declared.resolve()
    if launch["scope"] == "installation" and not entry.is_relative_to(directory):
        raise ValueError("Managed frontend entry escaped its installation")
    if launch["scope"] == "external" and str(entry) != launch["path"]:
        raise ReconfigurationRequired(identity, frontend, ("launch.path",))
    changes = []
    requested_paths = [("explicit_binary", explicit_binary, True),
                       ("explicit_source", explicit_source, False)]
    if frontend == "codex" and environment.get("RPNH_CODEX_BIN"):
        requested_paths.append(("RPNH_CODEX_BIN", environment["RPNH_CODEX_BIN"], True))
    if frontend == "dsh" and environment.get("DSH_SOURCE"):
        requested_paths.append(("DSH_SOURCE", environment["DSH_SOURCE"], False))
    for name, value, is_binary in requested_paths:
        if value is not None and not _override_matches(value, entry, environment, binary=is_binary):
            changes.append(name)
    if compatibility_profile is not None and compatibility_profile != component["profile"]:
        changes.append("compatibility_profile")
    candidate = environment.get("RPNH_DSH_FRONTEND_CANDIDATE") if frontend == "dsh" else None
    if candidate and candidate != component["profile"]:
        changes.append("RPNH_DSH_FRONTEND_CANDIDATE")
    if changes:
        raise ReconfigurationRequired(identity, frontend, tuple(changes))

    kind = launch["kind"]
    if kind == "node_wrapper" and (
            component["artifact"]["kind"] != "npm"
            or component["artifact"]["name"] != specification.coordinate.get("package")):
        raise ValueError("Node wrapper must identify its declared npm artifact")
    source = None
    if kind == "dsh_prepared":
        if not entry.is_dir():
            raise RuntimeError(f"Selected prepared DSH source is unavailable: {entry}")
        source = entry
        entry = (source / "rpnh-frontend/frontend.ts").resolve()
        if not entry.is_relative_to(source):
            raise ValueError("Prepared DSH entry escaped its declared source")
    else:
        if not entry.is_file():
            raise RuntimeError(f"Selected frontend entry is unavailable: {entry}")
        if kind != "builtin" and _entry_kind(entry, specification) != kind:
            raise ValueError("Installed launch kind does not match its executable")
        if kind in {"native", "builtin"}:
            _executable(entry)
    python = _bound_runtime("python", record, frontend, environment, cwd)
    node = (_bound_runtime("node", record, frontend, environment, cwd)
            if kind in {"node_wrapper", "dsh_prepared"} else None)
    argv = ((str(node.path), str(entry)) if kind == "node_wrapper"
            else (str(node.path),) if kind == "dsh_prepared" else (str(entry),))
    pnpm_binding = record["runtime_bindings"].get("pnpm")
    pnpm = RuntimeObservation(Path(pnpm_binding["path"])) if pnpm_binding else None
    return LaunchSpec(frontend, entry_name, specification.profile, specification.version,
                      identity, record["rpnh_version"], kind, entry, source, argv,
                      python, node, pnpm, True, component["binding"]["kind"],
                      Path(component["binding"]["path"]) if "path" in component["binding"] else None,
                      component["artifact"]["variant"], component["artifact"]["architecture"])


def _manual_spec(frontend: str, preferences: FrontendPreferences, catalog,
                 environment: Mapping[str, str], explicit_binary, explicit_source,
                 compatibility_profile) -> LaunchSpec:
    entry_name = "dsh-web" if frontend == "dsh" else frontend
    selected_profile = compatibility_profile
    if selected_profile is None and frontend == "dsh":
        selected_profile = environment.get("RPNH_DSH_FRONTEND_CANDIDATE") or preferences.dsh_compatibility_profile
    elif selected_profile is None and frontend != "basic":
        selected_profile = getattr(preferences, f"{frontend}_compatibility_profile")
    specification = get_profile(entry_name, selected_profile, catalog=catalog)
    python = RuntimeObservation(_path(sys.executable))
    node = None
    source = None
    if frontend == "basic":
        kind, entry = "builtin", python.path
        argv = (str(entry), "-m", "cpn.rpnh_cli")
    elif frontend == "dsh":
        selected_source = explicit_source
        if selected_source is None:
            selected_source = environment.get("DSH_SOURCE") or preferences.dsh_source
        if selected_source is None:
            raise RuntimeError("DSH source is not configured; run frontend setup or select an explicit source")
        source = _path(selected_source)
        if not source.is_dir():
            raise RuntimeError(f"Selected DSH source is unavailable: {source}")
        node = RuntimeObservation(_binary("node", environment))
        kind = "dsh_prepared"
        entry = (source / "rpnh-frontend/frontend.ts").resolve()
        if not entry.is_relative_to(source):
            raise ValueError("Prepared DSH entry escaped its selected source")
        argv = (str(node.path),)
    else:
        selected = explicit_binary
        if selected is None and frontend == "codex":
            selected = environment.get("RPNH_CODEX_BIN") or None
        if selected is None:
            selected = getattr(preferences, f"{frontend}_binary")
        if (frontend == "opencode" and specification.profile != catalog.defaults["opencode"]
                and selected is None):
            raise RuntimeError("An OpenCode candidate requires an explicit or saved binary binding")
        entry = _binary(selected if selected is not None else frontend, environment)
        kind = _entry_kind(entry, specification)
        if kind == "node_wrapper":
            node = RuntimeObservation(_binary("node", environment))
            argv = (str(node.path), str(entry))
        else:
            argv = (str(entry),)
    return LaunchSpec(frontend, entry_name, specification.profile, specification.version,
                      None, catalog.rpnh_version, kind, entry, source, argv, python, node)


def resolve_launch_spec(
        frontend: str | None, *, preferences: FrontendPreferences | None = None,
        installed: dict | None = None, installation_directory: Path | None = None,
        explicit_binary: str | Path | None = None, explicit_source: str | Path | None = None,
        compatibility_profile: str | None = None,
        environment: Mapping[str, str] | None = None, cwd: Path | None = None,
) -> LaunchSpec:
    """Resolve from one preferences/installation/environment snapshot.

    ``auto`` retains the terminal policy (Codex when selected and interactive,
    otherwise Basic). Managed auto never discovers an undeclared PATH client;
    an unhealthy selected managed component is reported rather than replaced.
    Native frontend banner probes are deliberately separate, so callers can
    establish their isolated HOME first and reuse the exact argv for launch.
    """
    preferences = read_frontend_preferences() if preferences is None else preferences
    env = dict(os.environ if environment is None else environment)
    selected = preferences.frontend if frontend is None else frontend
    if selected not in ("auto", "basic", "codex", "opencode", "dsh"):
        raise ValueError("Unknown frontend selection")
    record = None
    directory = None
    if installed is not None:
        if installation_directory is None:
            raise ValueError("Managed launch requires its installation directory")
        requested = Path(installation_directory).expanduser().absolute()
        directory = requested.resolve()
        if requested != directory or not directory.is_dir():
            raise ValueError("Managed launch requires a real, unaliased installation directory")
        record = validate_installed(installed, expected_id=directory.name)
    elif installation_directory is not None or preferences.installation_id is not None:
        raise ValueError("Managed preferences require the leased installed record and directory")
    automatic = selected == "auto"
    if automatic:
        selected = ("codex" if sys.stdin.isatty()
                    and (record is None or "codex" in record["components"]) else "basic")
    if explicit_binary is not None and selected not in {"codex", "opencode"}:
        raise ValueError("This frontend does not accept a binary override")
    if explicit_source is not None and selected != "dsh":
        raise ValueError("Only DSH accepts a source override")
    catalog = load_profile_catalog()
    if record is not None:
        return _managed_spec(selected, preferences, record, directory, catalog, env, cwd,
                             explicit_binary, explicit_source, compatibility_profile)
    try:
        return _manual_spec(selected, preferences, catalog, env, explicit_binary,
                            explicit_source, compatibility_profile)
    except _ExecutableUnavailable:
        if not (automatic and selected == "codex" and explicit_binary is None
                and compatibility_profile is None and not env.get("RPNH_CODEX_BIN")
                and preferences.codex_binary is None and preferences.codex_compatibility_profile is None):
            raise
        return _manual_spec("basic", preferences, catalog, env, None, None, None)


def probe_frontend_version(spec: LaunchSpec, environment: Mapping[str, str],
                           cwd: Path | None = None) -> None:
    """Probe the exact native/Node argv once, before an Owner is created.

    DSH keeps its existing source/assets/Node readiness check; Basic has no
    external presentation client version to probe here.
    """
    if spec.frontend not in {"codex", "opencode"}:
        return
    result = _run((*spec.argv, "--version"), environment, cwd,
                  timeout=10 if spec.frontend == "opencode" else 5)
    reported = result.stdout.strip()
    if spec.frontend == "codex":
        matches = reported == f"codex-cli {spec.upstream_version}"
    else:
        prefix = (r"(?:opencode[ \t]+v?)?" if spec.upstream_version == "2.2.6"
                  else r"(?:opencode[ \t]+)?")
        matches = re.fullmatch(prefix + re.escape(spec.upstream_version), reported) is not None
    if result.returncode or not matches:
        raise RuntimeError(
            f"RPNH requires exactly {spec.frontend} {spec.upstream_version}; "
            f"selected executable reported {reported or result.stderr.strip() or 'unknown'}")


def resolve_current_launch_spec(frontend: str | None, **options) -> LaunchSpec:
    """Use the current context while the caller already holds its runtime lease.

    No lease is acquired or released here. Selection-only parent environment
    inputs are merged into a private resolver snapshot; the isolated probe/UI
    environment supplied by the caller is not modified or returned.
    """
    if {"preferences", "installed", "installation_directory"} & set(options):
        raise ValueError("Current launch context cannot be replaced through resolver options")
    from .update_install import managed_root, _v2_root
    root = managed_root()
    record = directory = None
    if root is not None and _v2_root(root):
        from .install_v2 import read_target
        current = read_target(root)
        if current is None:
            raise ValueError("Managed installation has no current environment")
        _, directory, record = current
    preferences = read_frontend_preferences()
    inherited = options.pop("environment", None)
    environment = dict(os.environ if inherited is None else inherited)
    for name in ("RPNH_CODEX_BIN", "DSH_SOURCE", "RPNH_DSH_FRONTEND_CANDIDATE"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return resolve_launch_spec(
        frontend, preferences=preferences, installed=record, installation_directory=directory,
        environment=environment, **options)


__all__ = ("LaunchSpec", "RuntimeObservation", "observe_runtime",
           "resolve_launch_spec", "resolve_current_launch_spec", "probe_frontend_version")
