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
from .opencode_http import OpenCodeHTTPServer
from .opencode_protocol import (
    DEFAULT_PROFILE, OPENCODE_VERSION, OpenCodeCompatibilityProfile,
    OpenCodeProtocol, require_opencode_profile,
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
    if result.returncode != 0 or not re.fullmatch(r"(?:opencode[ \t]+)?" + re.escape(profile.version), value):
        raise RuntimeError(f"RPNH requires exactly OpenCode {profile.version}; version probe was rejected")


def run_opencode_frontend(root: Path, execution: Path, *, resume: bool = False) -> int:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("OpenCode frontend currently supports Linux/WSL2 only")
    binary = shutil.which("opencode")
    if binary is None:
        raise RuntimeError(f"Install the pinned OpenCode {OPENCODE_VERSION} client before using this frontend")
    binary = str(Path(binary).absolute())
    with tempfile.TemporaryDirectory(prefix="rpnh-opencode-") as name:
        scratch = Path(name)
        env = isolated_environment(scratch, os.environ)
        display = scratch / "display"
        # Production never resolves a certification candidate from CLI or env.
        profile = DEFAULT_PROFILE
        check_version(binary, env, display, profile=profile)  # Before any owner.
        holder: list[OpenCodeProtocol] = []
        gateway = FrontendGateway(lambda: RegistryFrontendApplication(root, execution, resume=resume),
                                  on_change=lambda: holder[0].notify() if holder else None)
        server = None
        try:
            protocol = OpenCodeProtocol(gateway, str(display), profile=profile)
            holder.append(protocol)
            server = OpenCodeHTTPServer(protocol)
            server.start()
            env.update({"OPENCODE_SERVER_USERNAME": "rpnh", "OPENCODE_SERVER_PASSWORD": server.password})
            print("RPNH OpenCode frontend — Registry-owned execution; UI metrics unavailable.")
            print(f"main session root: {root.resolve()}")
            print("Identical unkeyed text is one request; use /rpnh-send NEW_ID TEXT for an intentional repeat.")
            args = [binary, "attach", server.url, "--dir", str(display), "--username", "rpnh"]
            if resume:
                args.append("--continue")
            result = subprocess.run(args, cwd=display, env=env, check=False)
            if result.returncode:
                print("OpenCode attach exited unsuccessfully; bounded route diagnostics:", file=sys.stderr)
                print(json.dumps(server.diagnostics(), ensure_ascii=True), file=sys.stderr)
            return result.returncode
        finally:
            if server is not None:
                server.close()
            gateway.close()
