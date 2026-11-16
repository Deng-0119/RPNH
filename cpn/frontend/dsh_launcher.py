"""Native DSH presentation over the shared RPNH main-session application."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.resources
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Mapping

from cpn.rpnh.frontend_application import FrontendGateway, RegistryFrontendApplication
from cpn.rpnh.frontend_config import read_frontend_preferences
from cpn.rpnh.platform_support import require_platform_support


REVISION = json.loads(importlib.resources.files("integrations.dsh").joinpath("UPSTREAM.json").read_text())["revision"]


def resolve_dsh_source(source: Path | None = None) -> Path:
    if source is None:
        override = os.environ.get("DSH_SOURCE")
        source = Path(override) if override else read_frontend_preferences().dsh_source
    if source is None:
        raise RuntimeError(
            "Configure the DSH checkout with `rpnh frontend setup dsh --install`, "
            "or link an already prepared checkout with `rpnh config frontend dsh "
            "--dsh-source CHECKOUT` or DSH_SOURCE")
    return source.expanduser().resolve()


def resolve_dsh_profile(compatibility_profile: str | None = None) -> str:
    """Resolve explicit, legacy-environment, saved, then packaged default once."""
    from .dsh_assets import DSH_CANDIDATE_ENV, official_web_profile
    selected = compatibility_profile
    if selected is None:
        selected = os.environ.get(DSH_CANDIDATE_ENV) or read_frontend_preferences().dsh_compatibility_profile
    return official_web_profile(selected)[0]["version"]


def isolated_environment(root: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    env = {key: inherited[key] for key in (
        "PATH", "TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE",
        "DISPLAY", "WAYLAND_DISPLAY", "WSL_INTEROP", "WSL_DISTRO_NAME",
    ) if key in inherited}
    for key, name in {
        "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "state", "TMPDIR": "tmp",
    }.items():
        directory = root / name
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    return env


def _not_prepared(detail: str) -> RuntimeError:
    return RuntimeError(
        f"{detail}; run `rpnh frontend setup dsh --install` to prepare the selected frontend")


def _source_profile(source: Path, compatibility_profile: str | None = None) -> dict:
    require_platform_support()
    if not source.is_dir():
        raise _not_prepared(f"DSH source checkout is unavailable: {source}")
    from cpn.frontend.dsh_assets import check_dsh_source_version, official_web_profile
    check_dsh_source_version(source, compatibility_profile)
    _, web_manifest = official_web_profile(compatibility_profile)
    if not (source / "node_modules/.bin/tsx").is_file():
        raise _not_prepared("DSH pinned source dependencies are missing")
    if not (source / "tsconfig.base.json").is_file():
        raise _not_prepared("DSH source tsconfig.base.json is missing")
    return web_manifest


def _node(env: Mapping[str, str], selected: str | None = None) -> str:
    # Resolve once against the launch environment, before probing it. The
    # absolute result is also the executable used for the eventual UI child.
    if selected is None:
        selected = shutil.which("node", path=env.get("PATH", os.defpath))
    node = None if selected is None else str(Path(selected).expanduser().resolve())
    if node is None:
        raise RuntimeError("DSH needs Node >=24.9.0")
    try:
        version = subprocess.run(
            [node, "--version"], env=dict(env), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"DSH Node version probe failed: {node} ({exc})") from exc
    match = re.fullmatch(r"v(\d+)\.(\d+)\.\d+", version.stdout.strip())
    if (version.returncode or match is None
            or not (int(match[1]) > 24
                    or int(match[1]) == 24 and int(match[2]) >= 9)):
        raise RuntimeError("DSH needs Node >=24.9.0")
    return node


def _prepared_entry(source: Path, web_manifest: dict) -> Path:
    target = source / "rpnh-frontend"
    try:
        prepared_manifest = json.loads((target / "web-packages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _not_prepared("DSH prepared assets are missing or unreadable") from exc
    if prepared_manifest != web_manifest:
        raise _not_prepared("DSH prepared assets do not match the selected source profile")
    for name in ("frontend.ts", "tsconfig.json", "official.json"):
        if not (target / name).is_file():
            raise _not_prepared(f"DSH prepared frontend file is missing: {name}")
    # Only this distribution's small adapter surface is compared. Upstream
    # source and dependency trees are not scanned or hashed during launch.
    for module in _adapter_modules():
        try:
            matches = (target / module.name).read_bytes() == module.read_bytes()
        except OSError as exc:
            raise _not_prepared(f"DSH prepared adapter is missing: {module.name}") from exc
        if not matches:
            raise _not_prepared(f"DSH prepared adapter differs from this RPNH installation: {module.name}")
    _check_host_paths(target, web_manifest)
    return target / "frontend.ts"


def _adapter_modules():
    resource = importlib.resources.files("integrations.dsh").joinpath("frontend")
    return tuple(module for module in resource.iterdir()
                 if module.name.endswith(".ts") and not module.name.endswith(".test.ts"))


def _check_host_paths(target: Path, web_manifest: dict) -> None:
    # setup.ts records absolute host entries. Moving/copying a prepared source
    # must require explicit setup, even while the old source still exists.
    expected = {str(target / "node_modules" / name / "rpnh-host.ts")
                for name in web_manifest["packages"]}
    bound = str(target / "generated/bound-client/host.ts")
    try:
        entries = json.loads((target / "official.json").read_text(encoding="utf-8"))
        if not isinstance(entries, list) or not entries:
            raise ValueError("host entries are empty or invalid")
        seen = set()
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"id", "name"}
                    or not isinstance(entry["id"], str) or not isinstance(entry["name"], str)):
                raise ValueError("host entry is invalid")
            identity, name = entry["id"], entry["name"]
            if identity in seen:
                raise ValueError("host entry identity is repeated")
            seen.add(identity)
            if identity == "bound-session":
                if name != bound:
                    raise ValueError("bound host belongs to a different prepared source")
            elif re.fullmatch(r"client-[0-9]+", identity) is None or name not in expected:
                raise ValueError("client host belongs to a different prepared source")
            path = Path(name)
            if (not path.is_absolute() or not path.resolve().is_relative_to(target.resolve())
                    or not path.is_file()):
                raise ValueError("host entry is absent or outside its prepared source")
        if "bound-session" not in seen:
            raise ValueError("bound host entry is missing")
    except (OSError, ValueError, RuntimeError) as exc:
        raise _not_prepared(f"DSH prepared host paths are not ready ({exc})") from exc


def _native_check(source: Path, node: str, entry: Path, env: Mapping[str, str]) -> None:
    native_env = {**env, "TSX_TSCONFIG_PATH": str(entry.parent / "tsconfig.json"),
                  "TSX_DISABLE_CACHE": "1"}
    try:
        checked = subprocess.run(
            [node, "--import", "tsx", str(entry), "--check"],
            cwd=source, env=native_env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _not_prepared(f"DSH native readiness probe failed ({exc})") from exc
    if checked.returncode:
        print((checked.stderr or checked.stdout)[-4000:], file=sys.stderr)
        raise _not_prepared("DSH official Web frontend dependencies or assets are not ready")


def probe_dsh_frontend(source: Path, env: Mapping[str, str], *,
                       compatibility_profile: str | None = None,
                       node_binary: str | None = None) -> tuple[str, Path]:
    """Read the selected prepared runtime and run its existing native --check.

    Discovery and ordinary launch never copy adapters, prepare a host, or fetch
    packages. The check disables the TSX cache and returns the same absolute
    executable/entry pair that launch must use. Native acceptance is separate.
    """
    source = source.expanduser().resolve()
    web_manifest = _source_profile(source, compatibility_profile)
    entry = _prepared_entry(source, web_manifest)
    node = _node(env, node_binary)
    _native_check(source, node, entry, env)
    return node, entry


def prepare_dsh_frontend(source: Path, env: Mapping[str, str], *,
                         compatibility_profile: str | None = None) -> tuple[str, Path]:
    """Explicit setup path: copy adapters, prepare pinned assets, then probe."""
    source = source.expanduser().resolve()
    web_manifest = _source_profile(source, compatibility_profile)
    node = _node(env)
    target = source / "rpnh-frontend"
    target.mkdir(exist_ok=True)
    for module in _adapter_modules():
        with importlib.resources.as_file(module) as path:
            shutil.copyfile(path, target / module.name)
    (target / "web-packages.json").write_text(
        json.dumps(web_manifest, indent=2) + "\n", encoding="utf-8")
    from cpn.frontend.dsh_assets import prepare_official_web_assets
    if compatibility_profile is None:
        prepare_official_web_assets(target / "node_modules")
    else:
        prepare_official_web_assets(target / "node_modules", compatibility_profile)
    native_env = {**env, "TSX_TSCONFIG_PATH": str(source / "tsconfig.base.json"),
                  "TSX_DISABLE_CACHE": "1"}
    prepared = subprocess.run(
        [node, "--import", "tsx", str(target / "setup.ts")],
        cwd=source, env=native_env, capture_output=True, text=True, check=False)
    if prepared.returncode:
        print((prepared.stderr or prepared.stdout)[-4000:], file=sys.stderr)
        raise RuntimeError("DSH official Web host preparation failed")
    entry = _prepared_entry(source, web_manifest)
    _native_check(source, node, entry, env)
    return node, entry


def run_dsh_frontend(
        root: Path, execution: Path, *, resume: bool = False,
        source: Path | None = None, open_browser: bool = True,
        compatibility_profile: str | None = None,
) -> int:
    from cpn.rpnh.update_install import lifecycle_lease
    with lifecycle_lease():
        return _run_dsh_frontend(root, execution, resume=resume, source=source,
                                 open_browser=open_browser, compatibility_profile=compatibility_profile)


def _run_dsh_frontend(
        root: Path, execution: Path, *, resume: bool = False,
        source: Path | None = None, open_browser: bool = True,
        compatibility_profile: str | None = None,
) -> int:
    from cpn.frontend.dsh_rpc import DshFrontendRPC
    with tempfile.TemporaryDirectory(prefix="rpnh-dsh-frontend-") as directory:
        scratch = Path(directory)
        env = isolated_environment(scratch, os.environ)
        env["RPNH_DSH_CALLER_CWD"] = str(Path.cwd().resolve())
        from cpn.rpnh.frontend_launch import resolve_current_launch_spec
        launch = resolve_current_launch_spec("dsh", explicit_source=source,
            compatibility_profile=compatibility_profile, environment=env, cwd=scratch)
        source = launch.source
        node, entry = probe_dsh_frontend(source, env, compatibility_profile=launch.profile,
                                        node_binary=launch.argv[0])
        if str(Path(node).resolve()) != launch.argv[0] or entry.resolve() != launch.entry_path:
            raise RuntimeError("DSH prepared entry changed after launch resolution")
        env["TSX_TSCONFIG_PATH"] = str(source / "rpnh-frontend/tsconfig.json")
        env["TSX_DISABLE_CACHE"] = "1"
        gateway = FrontendGateway(
            lambda: RegistryFrontendApplication(root, execution, resume=resume, frontend="dsh"))
        try:
            rows = gateway.call("snapshot")
            sid = rows[0]["id"] if rows else gateway.call("create_session")
            bridge = DshFrontendRPC(gateway, sid)
            with ExitStack() as stack:
                client_read, owner_write = os.pipe()
                owner_read, client_write = os.pipe()
                child_reader = stack.enter_context(os.fdopen(client_read, "rb"))
                writer = stack.enter_context(os.fdopen(owner_write, "wb"))
                reader = stack.enter_context(os.fdopen(owner_read, "rb"))
                child_writer = stack.enter_context(os.fdopen(client_write, "wb"))
                argv = [node, "--import", "tsx", str(entry),
                        "--read-fd", str(client_read), "--write-fd", str(client_write)]
                if not open_browser:
                    argv.append("--no-open-browser")
                print(f"RPNH DSH frontend — main session root: {root.resolve()}", flush=True)
                process = subprocess.Popen(
                    argv, cwd=source, env=env, pass_fds=(client_read, client_write))
                child_reader.close()
                child_writer.close()
                failures: list[BaseException] = []
                def serve() -> None:
                    try:
                        bridge.serve(reader, writer)
                    except (OSError, RuntimeError, ValueError) as exc:
                        failures.append(exc)
                    finally:
                        try:
                            writer.close()
                        except OSError as exc:
                            failures.append(exc)
                owner = threading.Thread(target=serve, name="rpnh-dsh-pipe", daemon=True)
                owner.start()
                try:
                    try:
                        result = process.wait()
                    except KeyboardInterrupt:
                        process.terminate()
                        result = 130
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    owner.join(timeout=35)
                if owner.is_alive():
                    raise RuntimeError("DSH frontend pipe did not close")
                if failures and result == 0:
                    raise RuntimeError("DSH frontend transport failed")
                return result
        finally:
            gateway.close()


__all__ = ("resolve_dsh_source", "probe_dsh_frontend", "prepare_dsh_frontend", "run_dsh_frontend")
