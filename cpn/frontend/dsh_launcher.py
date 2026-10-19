"""Native DSH presentation over the shared RPNH main-session application."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.resources
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Mapping

from cpn.dsh.message_codec import CURRENT_DSH_REVISION as REVISION
from cpn.rpnh.frontend_application import FrontendGateway, RegistryFrontendApplication
from cpn.rpnh.frontend_config import read_frontend_preferences


def resolve_dsh_source(source: Path | None = None) -> Path:
    if source is None:
        override = os.environ.get("DSH_SOURCE")
        source = Path(override) if override else read_frontend_preferences().dsh_source
    if source is None:
        raise RuntimeError(
            "Configure the DSH checkout with `rpnh config frontend dsh "
            "--dsh-source CHECKOUT`, or set DSH_SOURCE")
    return source.expanduser().resolve()


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


def prepare_dsh_frontend(source: Path, env: Mapping[str, str]) -> tuple[str, Path]:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("DSH frontend currently supports Linux/WSL2 only")
    if not source.is_dir():
        raise RuntimeError(f"DSH source checkout is unavailable: {source}")
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        env=dict(env), capture_output=True, text=True, timeout=5, check=False)
    if revision.returncode or revision.stdout.strip() != REVISION:
        raise RuntimeError(f"RPNH requires the exact DSH source revision {REVISION}")
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("DSH needs Node ^22.19.0 or >=24.0.0")
    version = subprocess.run(
        [node, "--version"], env=dict(env), capture_output=True,
        text=True, timeout=5, check=False)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.\d+", version.stdout.strip())
    if (version.returncode or match is None
            or not (int(match[1]) >= 24
                    or int(match[1]) == 22 and int(match[2]) >= 19)):
        raise RuntimeError("DSH needs Node ^22.19.0 or >=24.0.0")
    if not (source / "node_modules/.bin/tsx").is_file():
        raise RuntimeError("Install the pinned DSH dependencies before starting its frontend")
    resource = importlib.resources.files("integrations.dsh")
    target = source / "rpnh-frontend"
    target.mkdir(exist_ok=True)
    for module in resource.joinpath("frontend").iterdir():
        if (module.name.endswith(".ts") and not module.name.endswith(".test.ts")
                or module.name == "web-packages.json"):
            with importlib.resources.as_file(module) as path:
                shutil.copyfile(path, target / module.name)
    from cpn.frontend.dsh_assets import prepare_official_web_assets
    prepare_official_web_assets(target / "node_modules")
    native_env = {**env, "TSX_TSCONFIG_PATH": str(source / "tsconfig.base.json")}
    prepared = subprocess.run(
        [node, "--import", "tsx", str(target / "setup.ts")],
        cwd=source, env=native_env, capture_output=True, text=True, check=False)
    if prepared.returncode:
        print((prepared.stderr or prepared.stdout)[-4000:], file=sys.stderr)
        raise RuntimeError("DSH official Web host preparation failed")
    native_env["TSX_TSCONFIG_PATH"] = str(target / "tsconfig.json")
    checked = subprocess.run(
        [node, "--import", "tsx", str(target / "frontend.ts"), "--check"],
        cwd=source, env=native_env, capture_output=True,
        text=True, timeout=30, check=False)
    if checked.returncode:
        print((checked.stderr or checked.stdout)[-4000:], file=sys.stderr)
        raise RuntimeError("DSH official Web frontend is not ready; check its source dependencies and assets")
    return node, target / "frontend.ts"


def run_dsh_frontend(
        root: Path, execution: Path, *, resume: bool = False,
        source: Path | None = None, open_browser: bool = True,
) -> int:
    from cpn.frontend.dsh_rpc import DshFrontendRPC
    source = resolve_dsh_source(source)
    with tempfile.TemporaryDirectory(prefix="rpnh-dsh-frontend-") as directory:
        scratch = Path(directory)
        env = isolated_environment(scratch, os.environ)
        env["RPNH_DSH_CALLER_CWD"] = str(Path.cwd().resolve())
        node, entry = prepare_dsh_frontend(source, env)
        env["TSX_TSCONFIG_PATH"] = str(source / "rpnh-frontend/tsconfig.json")
        gateway = FrontendGateway(
            lambda: RegistryFrontendApplication(root, execution, resume=resume))
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


__all__ = ("resolve_dsh_source", "prepare_dsh_frontend", "run_dsh_frontend")
