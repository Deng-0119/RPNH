"""Local frontend discovery and explicit, version-pinned onboarding.

Discovery never installs packages or creates a MainSession. Installation is
confined to ``frontends/`` beside the frontend preferences; only a verified
binding is persisted. Native launchers remain responsible for session startup.
"""
from __future__ import annotations

import importlib.resources
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .frontend_config import (
    FrontendPreferences, preferences_path, read_frontend_preferences,
    save_frontend_preferences,
)


def _frontend(value: str) -> str:
    if value not in ("basic", "codex", "opencode", "dsh"):
        raise ValueError("Choose a frontend: basic, codex, opencode, dsh")
    return value


def _linux() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Native frontend onboarding currently supports Linux/WSL2 only; choose basic")


def _dsh_pin() -> dict:
    return json.loads(importlib.resources.files("integrations.dsh").joinpath(
        "UPSTREAM.json").read_text(encoding="utf-8"))


def _version(frontend: str) -> str:
    if frontend == "codex":
        from cpn.frontend.codex_app_server import CODEX_FRONTEND_VERSION
        return CODEX_FRONTEND_VERSION
    if frontend == "opencode":
        from cpn.frontend.opencode_protocol import OPENCODE_VERSION
        return OPENCODE_VERSION
    return _dsh_pin()["revision"]


def _managed(frontend: str) -> Path:
    return preferences_path().parent / "frontends" / f"{frontend}-{_version(frontend)}"


def _run(argv, *, env, cwd=None, timeout=30) -> str:
    try:
        result = subprocess.run(
            [str(arg) for arg in argv], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Frontend command failed: {argv[0]} ({exc})") from exc
    if result.returncode:
        raise RuntimeError(
            f"Frontend command failed: {argv[0]} (exit {result.returncode}). "
            f"{(result.stderr or result.stdout).strip()[-1500:]}")
    return result.stdout.strip()


def _tool(name: str) -> str:
    result = shutil.which(name)
    if result is None:
        raise RuntimeError(f"Install {name} and put it on PATH, then rerun frontend setup")
    return result


def _node(env, *, dsh=False) -> str:
    node = _tool("node")
    value = _run([node, "--version"], env=env, timeout=5)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.\d+", value)
    if (match is None or (dsh and not (
            int(match[1]) >= 24 or int(match[1]) == 22 and int(match[2]) >= 19))
            or (not dsh and int(match[1]) < 16)):
        requirement = "Node ^22.19.0 or >=24.0.0" if dsh else "Node >=16"
        raise RuntimeError(f"Install {requirement} and put it on PATH, then rerun frontend setup")
    return node


def _probe_binary(frontend: str, binary: Path) -> Path:
    _linux()
    if frontend == "codex":
        from cpn.frontend.codex_app_server import resolve_codex_binary
        return Path(resolve_codex_binary(str(binary))).resolve()
    from cpn.frontend.opencode_launcher import check_version, isolated_environment
    # The native client initializes its XDG directories even for --version.
    # Reuse the launcher's process-private environment and discard it on every
    # exit path, including probe failure, timeout, and owner cancellation.
    with tempfile.TemporaryDirectory(prefix="rpnh-opencode-probe-") as directory:
        root = Path(directory)
        env = isolated_environment(root, os.environ)
        check_version(str(binary), env, root / "display")
    return binary.expanduser().resolve()


def _probe_environment() -> dict[str, str]:
    # DSH --check does not need a user home or writable cache. In particular,
    # tsx otherwise writes its transpilation cache during --check.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
           if key in os.environ}
    env.update({"HOME": os.devnull, "XDG_CONFIG_HOME": os.devnull,
                "XDG_CACHE_HOME": os.devnull, "XDG_DATA_HOME": os.devnull,
                "XDG_STATE_HOME": os.devnull, "TSX_DISABLE_CACHE": "1",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1"})
    return env


def _probe_dsh(source: Path) -> Path:
    """Read-only counterpart of prepare_dsh_frontend, including its --check."""
    _linux()
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f"DSH checkout is missing: {source}; rerun setup with installation enabled")
    env = _probe_environment()
    _dsh_revision(source, env)
    node = _node(env, dsh=True)
    entry = source / "rpnh-frontend/frontend.ts"
    if not (source / "node_modules/.bin/tsx").is_file() or not entry.is_file():
        raise RuntimeError("DSH dependencies or official Web assets are missing; rerun frontend setup with installation enabled")
    # The same native import and asset check used by the launcher; never
    # call its preparation/download path during discovery.
    env["TSX_TSCONFIG_PATH"] = str(source / "rpnh-frontend/tsconfig.json")
    _run([node, "--import", "tsx", entry, "--check"], env=env, cwd=source)
    return source


def _dsh_revision(source: Path, env) -> None:
    revision = _run([_tool("git"), "-C", source, "rev-parse", "HEAD"], env=env, timeout=5)
    if revision != _dsh_pin()["revision"]:
        raise RuntimeError(
            f"RPNH requires DSH revision {_dsh_pin()['revision']}; "
            "supply the pinned checkout with dsh_source or unset DSH_SOURCE")


def _automatic_codex_candidate(binary: Path, *, bound_only: bool) -> Path:
    """Recover the stock npm client named by the known local backend wrapper.

    This inspects only the selected ~/.local/bin/codex and its literal
    real_codex target. Explicit API/environment and saved bindings bypass it.
    """
    if binary.expanduser().absolute() != Path.home() / ".local/bin/codex":
        return binary
    try:
        source = binary.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return binary
    match = re.search(r'''(?m)^real_codex=(["'])(/[^\r\n"']+)\1\s*$''', source)
    if match is None or "--config" not in source or "--add-dir" not in source:
        return binary
    guidance = (
        "The PATH Codex client is a backend wrapper that adds options incompatible "
        "with --remote; run frontend setup to link the stock client or install the "
        "managed official package. Explicit RPNH_CODEX_BIN/codex_binary overrides this discovery.")
    if bound_only:
        raise RuntimeError(guidance)
    candidate = Path(match[2]).resolve()
    package = candidate.parent.parent
    # An installation-layout check, not an authenticity claim. Never execute
    # wrapper text or search other user installations.
    try:
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        if (candidate.name == "codex.js" and candidate.parent.name == "bin"
                and package.name == "codex" and package.parent.name == "@openai"
                and metadata.get("name") == "@openai/codex"
                and metadata.get("version") == _version("codex")):
            return candidate
    except (OSError, ValueError, AttributeError):
        pass
    raise RuntimeError(guidance)


def _discover(frontend: str, preferences: FrontendPreferences, explicit: Path | None = None,
              *, bound_only: bool = False) -> Path:
    if frontend == "dsh":
        override = os.environ.get("DSH_SOURCE")
        source = explicit or (Path(override) if override else preferences.dsh_source)
        if bound_only and source is None:
            raise RuntimeError("DSH is not connected; run frontend setup to link or install its official Web frontend")
        return _probe_dsh(source or _managed("dsh"))
    if explicit is not None:
        return _probe_binary(frontend, explicit.expanduser().resolve())
    override = os.environ.get("RPNH_CODEX_BIN") if frontend == "codex" else None
    # Match launcher precedence. An explicit environment binding is deliberate;
    # do not silently replace it with another executable.
    if override:
        return _probe_binary(frontend, Path(override).expanduser())
    saved = getattr(preferences, f"{frontend}_binary")
    on_path = shutil.which(frontend)
    managed = _managed(frontend) / "node_modules/.bin" / frontend
    failures = []
    candidates = (saved, Path(on_path) if on_path else None, managed)
    if bound_only:
        # Readiness must match what the launchers will actually run. Setup can
        # repair a stale saved binding using PATH or a retained managed client.
        candidates = (saved,) if saved is not None else (Path(on_path),) if on_path else ()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            if frontend == "codex" and candidate != saved:
                candidate = _automatic_codex_candidate(candidate, bound_only=bound_only)
            return _probe_binary(frontend, candidate)
        except (RuntimeError, OSError, ValueError) as exc:
            failures.append(str(exc))
    raise RuntimeError("; ".join(failures) or f"{frontend} is missing; rerun frontend setup with installation enabled")


def frontend_status(frontend: str) -> dict:
    """Return frontend/available/detail using local probes only, without saving."""
    selected = _frontend(frontend)
    if selected == "basic":
        return {"frontend": selected, "available": True, "detail": "Built-in Basic frontend is ready"}
    try:
        binding = _discover(selected, read_frontend_preferences(), bound_only=True)
        return {"frontend": selected, "available": True, "detail": f"Ready: {binding}"}
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"frontend": selected, "available": False, "detail": str(exc)}


def _install_environment(root: Path) -> dict[str, str]:
    from cpn.frontend.dsh_launcher import isolated_environment
    root.mkdir(parents=True, exist_ok=True)
    env = isolated_environment(root, os.environ)
    env.update({"npm_config_cache": env["XDG_CACHE_HOME"],
                "npm_config_registry": "https://registry.npmjs.org/",
                "COREPACK_ENABLE_PROJECT_SPEC": "0",
                "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"})
    return env


def _npm_install(package: str, destination: Path, env) -> None:
    npm = _tool("npm")
    _node(env)
    destination.mkdir(parents=True, exist_ok=True)
    _run([npm, "install", "--prefix", destination, "--no-audit", "--no-fund",
          "--save-exact", "--registry=https://registry.npmjs.org/", package],
         cwd=destination, env=_download_environment(env), timeout=1800)


def _download_environment(env) -> dict[str, str]:
    # Preserve configured download routing only for installation commands.
    # Local version/readiness probes keep the launcher's isolated environment.
    return {**env, **{name: os.environ[name] for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    ) if name in os.environ}}


def _install_dsh(source: Path, env, output_fn) -> Path:
    pin = _dsh_pin()
    git = _tool("git")
    _node(env, dsh=True)
    if not source.exists():
        if source != _managed("dsh").resolve():
            raise RuntimeError(
                f"DSH checkout is missing: {source}; supply an existing pinned checkout "
                "or omit dsh_source/unset DSH_SOURCE to install in the managed directory")
        output_fn(f"Downloading official DSH source at {pin['revision']}")
        source.parent.mkdir(parents=True, exist_ok=True)
        # Publish a checkout only after fetch succeeds, so cancellation or a
        # failed download leaves setup retryable without deleting user files.
        with tempfile.TemporaryDirectory(prefix=".dsh-source-", dir=source.parent) as directory:
            checkout = Path(directory) / "checkout"
            _run([git, "init", checkout], env=env)
            _run([git, "-C", checkout, "fetch", "--depth=1",
                  f"https://github.com/{pin['repository']}.git", pin["revision"]],
                 env=_download_environment(env), timeout=600)
            _run([git, "-C", checkout, "checkout", "--detach", "FETCH_HEAD"], env=env)
            _dsh_revision(checkout, env)
            checkout.rename(source)
    _dsh_revision(source, env)
    if not (source / "node_modules/.bin/tsx").is_file():
        pnpm = shutil.which("pnpm")
        if pnpm is not None:
            try:
                if _run([pnpm, "--version"], env=env, timeout=10) != pin["pnpm"]:
                    pnpm = None
            except RuntimeError:
                pnpm = None
        if pnpm is None:
            destination = _managed("dsh").parent / f"pnpm-{pin['pnpm']}"
            candidate = destination / "node_modules/.bin/pnpm"
            if not candidate.is_file():
                _npm_install(f"pnpm@{pin['pnpm']}", destination, env)
            pnpm = str(candidate)
        if _run([pnpm, "--version"], env=env, timeout=10) != pin["pnpm"]:
            raise RuntimeError(f"DSH requires pnpm {pin['pnpm']}; reinstall its managed package")
        output_fn(f"Installing DSH dependencies with pnpm {pin['pnpm']}")
        _run([pnpm, "install", "--frozen-lockfile", "--ignore-scripts",
              "--store-dir", str(_managed("dsh").parent / "pnpm-store")],
             env=_download_environment(env), cwd=source, timeout=600)
    from cpn.frontend.dsh_launcher import prepare_dsh_frontend
    output_fn("Preparing official DSH Web assets")
    prepare_dsh_frontend(source, env)
    return _probe_dsh(source)


def configure_frontend(
        frontend: str, *, install: bool = True,
        codex_binary: Path | None = None, opencode_binary: Path | None = None,
        dsh_source: Path | None = None, output_fn=print,
) -> FrontendPreferences:
    """Connect a ready frontend or install its exact pin, then save atomically.

    ``auto`` is a selection policy, not a setup target. Invalid explicit binary
    bindings fail rather than being replaced. Omitting bindings preserves them.
    RuntimeError/OSError/ValueError leave existing preferences untouched.
    """
    selected = _frontend(frontend)
    bindings = {"codex_binary": codex_binary, "opencode_binary": opencode_binary,
                "dsh_source": dsh_source}
    for name, value in bindings.items():
        if value is not None and not isinstance(value, Path):
            raise ValueError(f"{name} must be a Path or None")
        if value is not None and name != f"{selected}_binary" and not (
                selected == "dsh" and name == "dsh_source"):
            raise ValueError(f"{name} is not a binding for {selected}")
    preferences = read_frontend_preferences()
    if selected == "basic":
        return save_frontend_preferences(selected)
    _linux()
    explicit = bindings["dsh_source" if selected == "dsh" else f"{selected}_binary"]
    try:
        binding = _discover(selected, preferences, explicit)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if not install or (selected != "dsh" and (
                explicit is not None or selected == "codex" and os.environ.get("RPNH_CODEX_BIN"))):
            raise RuntimeError(f"{selected} is not ready: {exc}") from exc
        with tempfile.TemporaryDirectory(prefix="rpnh-frontend-install-",
                                         dir=_ensure_managed_parent(selected)) as directory:
            env = _install_environment(Path(directory) / "runtime")
            if selected == "dsh":
                override = os.environ.get("DSH_SOURCE")
                saved_source = preferences.dsh_source
                if saved_source is not None and not saved_source.is_dir():
                    saved_source = None
                source = explicit or (Path(override) if override else saved_source)
                binding = _install_dsh((source or _managed("dsh")).expanduser().resolve(), env, output_fn)
            else:
                package = "@openai/codex" if selected == "codex" else "opencode-ai"
                output_fn(f"Installing {package}@{_version(selected)}")
                _npm_install(f"{package}@{_version(selected)}", _managed(selected), env)
                binding = _probe_binary(selected, _managed(selected) / "node_modules/.bin" / selected)
    output_fn(f"Connected {selected}: {binding}")
    return save_frontend_preferences(selected, **{
        "dsh_source" if selected == "dsh" else f"{selected}_binary": binding})


def _ensure_managed_parent(frontend: str) -> Path:
    parent = _managed(frontend).parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent


__all__ = ("frontend_status", "configure_frontend")
