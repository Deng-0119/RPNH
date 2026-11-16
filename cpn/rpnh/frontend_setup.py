"""Local frontend discovery and explicit, version-pinned onboarding.

Discovery never installs packages or creates a MainSession. Installation is
confined to ``frontends/`` beside the frontend preferences; only a verified
binding is persisted. Native launchers remain responsible for session startup.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .frontend_config import (
    FrontendPreferences, preferences_path, read_frontend_preferences,
    save_frontend_preferences, _validated_profile,
)
from .platform_support import require_platform_support


def _frontend(value: str) -> str:
    if value not in ("basic", "codex", "opencode", "dsh"):
        raise ValueError("Choose a frontend: basic, codex, opencode, dsh")
    return value


def _platform() -> None:
    # A platform/API preflight never awards native-client or managed-install
    # qualification. In particular, macOS remains a native acceptance candidate.
    require_platform_support()


def _dsh_pin(compatibility_profile: str | None = None) -> dict:
    from cpn.frontend.dsh_assets import official_web_profile
    return official_web_profile(compatibility_profile)[0]


def _version(frontend: str, compatibility_profile: str | None = None) -> str:
    if frontend == "codex":
        from cpn.frontend.codex_app_server import _compatibility_profile
        return _compatibility_profile(compatibility_profile).version
    if frontend == "opencode":
        from cpn.frontend.opencode_protocol import OPENCODE_VERSION, get_opencode_profile
        return get_opencode_profile(certification_version=(
            None if compatibility_profile == OPENCODE_VERSION else compatibility_profile)).version
    return _dsh_pin(compatibility_profile)["version"]


def _npm_package_spec(frontend: str, compatibility_profile: str | None = None) -> str:
    """Read exact installation coordinates from the packaged profile source."""
    if frontend not in ("codex", "opencode"):
        raise ValueError("Only Codex and OpenCode use a native npm package")
    if compatibility_profile is not None:
        _validated_profile(frontend, compatibility_profile)
    version = _version(frontend, compatibility_profile)
    manifest_path = Path(__file__).resolve().parents[1] / "frontend" / (
        frontend + "_compatibility.v1.json")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    if frontend == "codex":
        package = document["frontend"].get("package")
    else:
        declarations = [document["upstream"], *document.get("certification_candidates", []),
                        *document.get("protocol_candidates", [])]
        declaration = next((item for item in declarations
                            if item.get("package_version") == version), None)
        package = None if declaration is None else declaration.get("npm_package")
    if not isinstance(package, str) or re.fullmatch(
            r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*", package) is None:
        raise ValueError(f"No exact npm installation coordinate is declared for {frontend} {version}")
    return f"{package}@{version}"


def _managed(frontend: str, compatibility_profile: str | None = None) -> Path:
    return preferences_path().parent / "frontends" / f"{frontend}-{_version(frontend, compatibility_profile)}"


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
            int(match[1]) > 24 or int(match[1]) == 24 and int(match[2]) >= 9))
            or (not dsh and int(match[1]) < 16)):
        requirement = "Node >=24.9.0" if dsh else "Node >=16"
        raise RuntimeError(f"Install {requirement} and put it on PATH, then rerun frontend setup")
    return node


def _probe_binary(frontend: str, binary: Path, compatibility_profile: str | None = None) -> Path:
    _platform()
    if frontend == "codex":
        from cpn.frontend.codex_app_server import isolated_codex_environment as isolate
    else:
        from cpn.frontend.opencode_launcher import isolated_environment as isolate
    from .frontend_launch import resolve_launch_spec, probe_frontend_version
    with tempfile.TemporaryDirectory(prefix=f"rpnh-{frontend}-probe-") as directory:
        root = Path(directory)
        env = isolate(root, os.environ)
        spec = resolve_launch_spec(frontend, preferences=FrontendPreferences(frontend=frontend),
            explicit_binary=binary, compatibility_profile=compatibility_profile,
            environment=env, cwd=root / "display")
        probe_frontend_version(spec, env, root / "display")
        return spec.entry_path


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


def _probe_dsh(source: Path, compatibility_profile: str | None = None) -> Path:
    """Use the launcher's same read-only readiness check during discovery."""
    from cpn.frontend.dsh_launcher import probe_dsh_frontend
    source = source.expanduser().resolve()
    options = {} if compatibility_profile is None else {"compatibility_profile": compatibility_profile}
    probe_dsh_frontend(source, _probe_environment(), **options)
    return source


def _dsh_source_version(source: Path, compatibility_profile: str | None = None) -> None:
    from cpn.frontend.dsh_assets import check_dsh_source_version
    check_dsh_source_version(source, compatibility_profile)


def _automatic_codex_candidate(binary: Path, *, bound_only: bool,
                               compatibility_profile: str | None = None) -> Path:
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
                and metadata.get("version") == _version("codex", compatibility_profile)):
            return candidate
    except (OSError, ValueError, AttributeError):
        pass
    raise RuntimeError(guidance)


def _discover(frontend: str, preferences: FrontendPreferences, explicit: Path | None = None,
              *, bound_only: bool = False, dsh_profile: str | None = None) -> Path:
    if frontend == "dsh":
        override = os.environ.get("DSH_SOURCE")
        source = explicit or (Path(override) if override else preferences.dsh_source)
        if bound_only and source is None:
            raise RuntimeError("DSH is not connected; run frontend setup to link or install its official Web frontend")
        from cpn.frontend.dsh_launcher import resolve_dsh_profile
        profile = dsh_profile or resolve_dsh_profile()
        return _probe_dsh(source or _managed("dsh", profile), profile)
    profile = getattr(preferences, f"{frontend}_compatibility_profile")
    if explicit is not None:
        return _probe_binary(frontend, explicit.expanduser().resolve(), profile)
    override = os.environ.get("RPNH_CODEX_BIN") if frontend == "codex" else None
    # Match launcher precedence. An explicit environment binding is deliberate;
    # do not silently replace it with another executable.
    if override:
        return _probe_binary(frontend, Path(override).expanduser(), profile)
    saved = getattr(preferences, f"{frontend}_binary")
    if frontend == "opencode" and profile == "2.2.6" and bound_only and saved is None:
        raise RuntimeError("OpenCode 2.2.6 needs a saved binary; run frontend setup to connect it")
    on_path = shutil.which(frontend)
    managed = _managed(frontend, profile) / "node_modules/.bin" / frontend
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
                candidate = _automatic_codex_candidate(
                    candidate, bound_only=bound_only, compatibility_profile=profile)
            return _probe_binary(frontend, candidate, profile)
        except (RuntimeError, OSError, ValueError) as exc:
            failures.append(str(exc))
    raise RuntimeError("; ".join(failures) or f"{frontend} is missing; rerun frontend setup with installation enabled")


def frontend_status(frontend: str) -> dict:
    from .update_install import lifecycle_lease
    try:
        with lifecycle_lease():
            return _frontend_status(frontend)
    except (RuntimeError, OSError, ValueError) as exc:
        return {"frontend": frontend, "available": False, "detail": str(exc)}


def _frontend_status(frontend: str) -> dict:
    """Return frontend/available/detail using local probes only, without saving."""
    selected = _frontend(frontend)
    preferences = read_frontend_preferences()
    if selected == "basic" and preferences.installation_id is None:
        return {"frontend": selected, "available": True, "detail": "Built-in Basic frontend is ready"}
    try:
        if preferences.installation_id is not None:
            from .frontend_launch import resolve_current_launch_spec, probe_frontend_version
            if selected == "codex":
                from cpn.frontend.codex_app_server import isolated_codex_environment as isolate
            else:
                from cpn.frontend.opencode_launcher import isolated_environment as isolate
            with tempfile.TemporaryDirectory(prefix="rpnh-managed-probe-") as raw:
                directory = Path(raw)
                env = isolate(directory, os.environ)
                spec = resolve_current_launch_spec(selected, environment=env, cwd=directory / "display")
                if selected == "dsh":
                    from cpn.frontend.dsh_launcher import probe_dsh_frontend
                    node, entry = probe_dsh_frontend(spec.source, env, compatibility_profile=spec.profile,
                                                    node_binary=spec.argv[0])
                    if str(Path(node).resolve()) != spec.argv[0] or entry.resolve() != spec.entry_path:
                        raise RuntimeError("DSH prepared entry changed after launch resolution")
                else:
                    probe_frontend_version(spec, env, directory / "display")
                binding = spec.source or spec.entry_path
        else:
            binding = _discover(selected, preferences, bound_only=True)
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


def _install_dsh(source: Path, env, output_fn, compatibility_profile: str | None = None) -> Path:
    pin = _dsh_pin(compatibility_profile)
    _node(env, dsh=True)
    if not source.exists():
        if source != _managed("dsh", compatibility_profile).resolve():
            raise RuntimeError(
                f"DSH checkout is missing: {source}; supply an existing pinned checkout "
                "or omit dsh_source/unset DSH_SOURCE to install in the managed directory")
        git = _tool("git")
        output_fn(f"Downloading official DSH source at {pin['release_tag']}")
        source.parent.mkdir(parents=True, exist_ok=True)
        # Publish a checkout only after fetch succeeds, so cancellation or a
        # failed download leaves setup retryable without deleting user files.
        with tempfile.TemporaryDirectory(prefix=".dsh-source-", dir=source.parent) as directory:
            checkout = Path(directory) / "checkout"
            _run([git, "init", checkout], env=env)
            _run([git, "-C", checkout, "fetch", "--depth=1",
                  f"https://github.com/{pin['repository']}.git", pin["release_tag"]],
                 env=_download_environment(env), timeout=600)
            _run([git, "-C", checkout, "checkout", "--detach", "FETCH_HEAD"], env=env)
            _dsh_source_version(checkout, compatibility_profile)
            checkout.rename(source)
    _dsh_source_version(source, compatibility_profile)
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
    options = {} if compatibility_profile is None else {"compatibility_profile": compatibility_profile}
    prepare_dsh_frontend(source, env, **options)
    return _probe_dsh(source, compatibility_profile)


def configure_frontend(
        frontend: str, *, install: bool = True,
        codex_binary: Path | None = None, opencode_binary: Path | None = None,
        dsh_source: Path | None = None,
        codex_compatibility_profile: str | None = None,
        opencode_compatibility_profile: str | None = None,
        dsh_compatibility_profile: str | None = None, output_fn=print,
) -> FrontendPreferences:
    from .update_install import lifecycle_lease
    with lifecycle_lease():
        return _configure_frontend(frontend, install=install, codex_binary=codex_binary,
            opencode_binary=opencode_binary, dsh_source=dsh_source,
            codex_compatibility_profile=codex_compatibility_profile,
            opencode_compatibility_profile=opencode_compatibility_profile,
            dsh_compatibility_profile=dsh_compatibility_profile, output_fn=output_fn)


def _configure_frontend(
        frontend: str, *, install: bool = True,
        codex_binary: Path | None = None, opencode_binary: Path | None = None,
        dsh_source: Path | None = None,
        codex_compatibility_profile: str | None = None,
        opencode_compatibility_profile: str | None = None,
        dsh_compatibility_profile: str | None = None, output_fn=print,
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
    profiles = {"codex_compatibility_profile": codex_compatibility_profile,
                "opencode_compatibility_profile": opencode_compatibility_profile,
                "dsh_compatibility_profile": dsh_compatibility_profile}
    changes = {}
    for name, value in profiles.items():
        if value is not None:
            if name != f"{selected}_compatibility_profile":
                raise ValueError(f"{name} is not a profile for {selected}")
            changes[name] = _validated_profile(selected, value)
    saved_preferences = read_frontend_preferences()
    if saved_preferences.installation_id is not None:
        # A managed I is immutable software. Existing selections are UX only;
        # a new component, profile, or path requires a complete reconfiguration.
        return save_frontend_preferences(selected, **{
            key: value for key, value in bindings.items() if value is not None}, **changes)
    preferences = replace(saved_preferences, **changes)
    if selected == "dsh":
        from cpn.frontend.dsh_launcher import resolve_dsh_profile
        profile = resolve_dsh_profile(dsh_compatibility_profile)
        changes["dsh_compatibility_profile"] = profile
        preferences = replace(preferences, dsh_compatibility_profile=profile)
    profile = getattr(preferences, f"{selected}_compatibility_profile", None)
    if selected == "basic":
        return save_frontend_preferences(selected)
    _platform()
    explicit = bindings["dsh_source" if selected == "dsh" else f"{selected}_binary"]
    try:
        options = {"dsh_profile": profile} if selected == "dsh" else {}
        binding = _discover(selected, preferences, explicit, **options)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if not install or (selected != "dsh" and (
                explicit is not None or selected == "codex" and os.environ.get("RPNH_CODEX_BIN"))):
            raise RuntimeError(f"{selected} is not ready: {exc}") from exc
        with tempfile.TemporaryDirectory(prefix="rpnh-frontend-install-",
                                         dir=_ensure_managed_parent(selected, profile)) as directory:
            env = _install_environment(Path(directory) / "runtime")
            if selected == "dsh":
                override = os.environ.get("DSH_SOURCE")
                saved_source = preferences.dsh_source
                if saved_source is not None and not saved_source.is_dir():
                    saved_source = None
                source = explicit or (Path(override) if override else saved_source)
                binding = _install_dsh((source or _managed("dsh", profile)).expanduser().resolve(),
                                       env, output_fn, profile)
            else:
                package = _npm_package_spec(selected, profile)
                output_fn(f"Installing {package}")
                _npm_install(package, _managed(selected, profile), env)
                binding = _probe_binary(selected, _managed(selected, profile) / "node_modules/.bin" / selected, profile)
    output_fn(f"Connected {selected}: {binding}")
    return save_frontend_preferences(selected, **{
        "dsh_source" if selected == "dsh" else f"{selected}_binary": binding}, **changes)


def _ensure_managed_parent(frontend: str, compatibility_profile: str | None = None) -> Path:
    parent = _managed(frontend, compatibility_profile).parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent


__all__ = ("frontend_status", "configure_frontend")
