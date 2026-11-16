"""Session-local OpenCode attach launcher; execution credentials stay in RPNH."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping

from cpn.rpnh.frontend_application import FrontendGateway, RegistryFrontendApplication
from cpn.rpnh.frontend_config import read_frontend_preferences
from .opencode_http import OpenCodeHTTPServer
from .opencode_protocol import (
    DEFAULT_PROFILE, OPENCODE_VERSION, OpenCodeCompatibilityProfile,
    OpenCodeProtocol, get_opencode_profile, require_opencode_profile,
)


def isolated_environment(root: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    # No PATH-based config loaders, NODE_OPTIONS, provider/API keys, proxies,
    # user git/npm/OpenCode settings or plugin routes cross this boundary.
    env = {name: inherited[name] for name in ("PATH", "TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE",
                                             "TERM_PROGRAM", "TERM_PROGRAM_VERSION") if name in inherited}
    for name, directory in {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
                            "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "state", "TMPDIR": "tmp"}.items():
        path = root / directory
        path.mkdir(mode=0o700)
        env[name] = str(path)
    display = root / "display"
    display.mkdir(mode=0o700)
    config = root / "config" / "opencode"
    config.mkdir(mode=0o700)
    (config / "opencode.json").write_text('{"plugin":[],"mcp":{},"autoupdate":false,"share":"disabled"}\n', encoding="utf-8")
    (config / "tui.json").write_text('{"plugin":[]}\n', encoding="utf-8")
    (config / "cli.json").write_text('{"plugins":[]}\n', encoding="utf-8")
    env.update({"OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_CONFIG_DIR": str(config),
                "OPENCODE_CONFIG": str(config / "opencode.json"), "OPENCODE_TUI_CONFIG": str(config / "tui.json")})
    return env


def check_version(binary: str, env: Mapping[str, str], cwd: Path, *,
                  profile: OpenCodeCompatibilityProfile = DEFAULT_PROFILE) -> None:
    require_opencode_profile(profile)
    try:
        result = subprocess.run([binary, "--version"], env=dict(env), cwd=cwd,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=10, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("OpenCode version probe timed out") from None
    value = result.stdout.strip()
    # The official 2.2.6 release binary reports "opencode v2.2.6".
    # Keep the candidate's exact numeric version and reject suffixes/output
    # additions; the stable probe retains its established output grammar.
    prefix = (r"(?:opencode[ \t]+v?)?" if profile.version == "2.2.6"
              else r"(?:opencode[ \t]+)?")
    if result.returncode != 0 or not re.fullmatch(prefix + re.escape(profile.version), value):
        raise RuntimeError(f"RPNH requires exactly OpenCode {profile.version}; version probe was rejected")


def frontend_launch_spec(binary: str, server_url: str, password: str, directory: Path, *,
                         profile: OpenCodeCompatibilityProfile = DEFAULT_PROFILE,
                         resume: bool = False,
                         session_id: str | None = None) -> tuple[list[str], dict[str, str]]:
    """Fixed-version argv/auth; only an explicit profile selects a candidate."""
    require_opencode_profile(profile)
    if profile.version.startswith("2."):
        args = [binary, str(directory), "--server", server_url]
        auth = {"OPENCODE_PASSWORD": password}
    else:
        args = [binary, "attach", server_url, "--dir", str(directory), "--username", "rpnh"]
        auth = {"OPENCODE_SERVER_USERNAME": "rpnh", "OPENCODE_SERVER_PASSWORD": password}
    if profile.version == "2.2.6":
        if not isinstance(session_id, str) or not re.fullmatch(r"ses_[0-9a-f]{32}", session_id):
            raise ValueError("The 2.2.6 frontend requires an already bound RPNH session")
        args.extend(["--session", session_id])
    elif resume:
        args.append("--continue")
    return args, auth


def run_opencode_frontend(
        root: Path, execution: Path, *, resume: bool = False,
        opencode_binary: str | None = None,
        compatibility_profile: str | None = None,
) -> int:
    from cpn.rpnh.update_install import lifecycle_lease
    with lifecycle_lease():
        return _run_opencode_frontend(root, execution, resume=resume,
            opencode_binary=opencode_binary, compatibility_profile=compatibility_profile)


def _run_opencode_frontend(
        root: Path, execution: Path, *, resume: bool = False,
        opencode_binary: str | None = None,
        compatibility_profile: str | None = None,
) -> int:
    from cpn.rpnh.platform_support import require_platform_support
    require_platform_support()
    with tempfile.TemporaryDirectory(prefix="rpnh-opencode-") as name:
        scratch = Path(name)
        env = isolated_environment(scratch, os.environ)
        display = scratch / "display"
        from cpn.rpnh.frontend_launch import resolve_current_launch_spec, probe_frontend_version
        launch = resolve_current_launch_spec("opencode", explicit_binary=opencode_binary,
            compatibility_profile=compatibility_profile, environment=env, cwd=display)
        profile = get_opencode_profile(certification_version=(
            None if launch.profile == OPENCODE_VERSION else launch.profile))
        binary = str(launch.entry_path)
        probe_frontend_version(launch, env, display)  # Before any owner.
        holder: list[OpenCodeProtocol] = []
        gateway = FrontendGateway(lambda: RegistryFrontendApplication(
            root, execution, resume=resume, frontend="opencode"),
                                  on_change=lambda: holder[0].notify() if holder else None)
        server = None
        try:
            if profile.version == "2.2.6":
                from .opencode_v2_protocol import OpenCodeV2Protocol
                protocol = OpenCodeV2Protocol(gateway, str(display), profile=profile)
            else:
                protocol = OpenCodeProtocol(gateway, str(display), profile=profile)
            holder.append(protocol)
            session_id = None
            if profile.version == "2.2.6":
                views = gateway.call("snapshot")
                if len(views) > 1 or (resume and not views):
                    raise RuntimeError("The candidate requires exactly one existing RPNH session on resume")
                session_id = (views[0]["id"] if views else gateway.call(
                    "create_session", protocol.default_selection,
                    reasoning_effort=protocol.default_reasoning_effort))
            server = OpenCodeHTTPServer(protocol)
            server.start()
            args, auth = frontend_launch_spec(binary, server.url, server.password, display,
                                              profile=profile, resume=resume, session_id=session_id)
            args = [*launch.argv, *args[1:]]
            env.update(auth)
            print("RPNH OpenCode frontend — Registry-owned execution; UI metrics unavailable.")
            print(f"main session root: {root.resolve()}")
            if profile.version == "2.2.6":
                print("Explicit 2.2.6 candidate; execution and real API acceptance remain separate gates.")
                print("Each native Enter has its message ID; retry the same ID to avoid another turn.")
                print("One bound RPNH session; /rpnh-resume and /rpnh-rollback use its existing checkpoint rules.")
            else:
                print("Identical unkeyed text is one request; use /rpnh-send NEW_ID TEXT for an intentional repeat.")
            result = subprocess.run(args, cwd=display, env=env, check=False)
            if result.returncode:
                print("OpenCode attach exited unsuccessfully; bounded route diagnostics:", file=sys.stderr)
                print(json.dumps(server.diagnostics(), ensure_ascii=True), file=sys.stderr)
            return result.returncode
        finally:
            if server is not None:
                server.close()
            gateway.close()
