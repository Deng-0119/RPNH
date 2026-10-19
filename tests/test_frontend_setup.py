from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cpn.rpnh import frontend_setup as setup
from cpn.rpnh.frontend_config import (
    FrontendPreferences, preferences_path, read_frontend_preferences,
    save_frontend_preferences,
)


@pytest.fixture(autouse=True)
def private_runtime(tmp_path, monkeypatch):
    # --basetemp must be inside the task workspace when running this module.
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("TMPDIR", "XDG_CACHE_HOME", "npm_config_cache"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.delenv("RPNH_CODEX_BIN", raising=False)
    monkeypatch.delenv("DSH_SOURCE", raising=False)


@pytest.fixture
def process_fixture(tmp_path, monkeypatch):
    """Only local preflight/package/Git commands; no real downloads or hosts."""
    state = {"calls": [], "installed": set(), "tools": {"node", "npm", "git", "pnpm"},
             "node": "v22.22.1", "pnpm": setup._dsh_pin()["pnpm"],
             "revision": setup._dsh_pin()["revision"], "fail": None,
             "bad_installed_version": False, "dsh_check_failure": False,
             "probe_roots": []}

    def which(value):
        if value in state["tools"]:
            return str(tmp_path / value if value in ("codex", "opencode")
                       else tmp_path / "tools" / value)
        path = Path(value)
        return str(path) if path.is_file() else None

    def run(argv, **kwargs):
        argv = [str(arg) for arg in argv]
        state["calls"].append((argv, kwargs))
        if Path(argv[0]).parent != tmp_path / "tools" and not Path(argv[0]).is_file():
            raise FileNotFoundError(argv[0])
        name = Path(argv[0]).name
        stdout, stderr, code = "", "", 0
        if state["fail"] and state["fail"] in argv:
            code, stderr = 1, "fixture download failure"
        elif argv[1:] == ["--version"]:
            if name == "node":
                stdout = state["node"]
            elif name == "pnpm":
                stdout = state["pnpm"]
            elif name in ("codex", "opencode"):
                if name == "opencode":
                    env = kwargs["env"]
                    root = Path(env["HOME"]).parent
                    state["probe_roots"].append(root)
                    assert Path(env["HOME"]).is_dir()
                    assert Path(env["XDG_CACHE_HOME"]).is_dir()
                    assert Path(env["TMPDIR"]).is_dir()
                    assert Path(env["OPENCODE_CONFIG"]).is_file()
                    assert kwargs["cwd"] == root / "display"
                    assert env["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
                    assert "PROVIDER_API_KEY" not in env
                version = "0.0.0" if state["bad_installed_version"] else setup._version(name)
                stdout = ("codex-cli " if name == "codex" else "") + version
            else:
                pytest.fail(f"Unexpected version command: {argv}")
        elif name == "npm" and argv[1] == "install":
            destination = Path(argv[argv.index("--prefix") + 1])
            assert destination.is_relative_to(tmp_path)
            assert kwargs["cwd"] == destination
            assert kwargs["env"]["HOME"].startswith(str(tmp_path))
            assert "--global" not in argv and "-g" not in argv
            assert kwargs["timeout"] == 1800
            package, version = argv[-1].rsplit("@", 1)
            binary = {"@openai/codex": "codex", "opencode-ai": "opencode", "pnpm": "pnpm"}[package]
            if binary != "pnpm":
                assert version == setup._version(binary)
            else:
                assert version == setup._dsh_pin()["pnpm"]
            target = destination / "node_modules/.bin" / binary
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
            state["installed"].add(binary)
        elif name == "git":
            if argv[1] == "init":
                Path(argv[2]).mkdir(parents=True)
            elif argv[-2:] == ["rev-parse", "HEAD"]:
                stdout = state["revision"]
            elif "fetch" in argv:
                pin = setup._dsh_pin()
                assert argv[-2:] == [f"https://github.com/{pin['repository']}.git", pin["revision"]]
            else:
                assert argv[-3:] == ["checkout", "--detach", "FETCH_HEAD"]
        elif name == "pnpm" and argv[1] == "install":
            assert "--frozen-lockfile" in argv and "--ignore-scripts" in argv
            assert Path(argv[-1]).is_relative_to(tmp_path)
            target = kwargs["cwd"] / "node_modules/.bin/tsx"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        elif name == "node" and argv[-1] == "--check":
            assert kwargs["env"]["TSX_DISABLE_CACHE"] == "1"
            assert kwargs["env"]["HOME"] == os.devnull
            assert "PROVIDER_API_KEY" not in kwargs["env"]
            assert kwargs["cwd"].is_relative_to(tmp_path)
            if state["dsh_check_failure"]:
                code, stderr = 1, "official asset missing"
        else:
            pytest.fail(f"Unexpected subprocess: {argv}")
        return subprocess.CompletedProcess(argv, code, stdout + "\n", stderr)

    monkeypatch.setattr(setup.shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    return state


def _prepared_source(tmp_path):
    source = tmp_path / "dsh"
    for path in (source / "node_modules/.bin/tsx", source / "rpnh-frontend/frontend.ts"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return source


def _prepare_fixture(monkeypatch, state):
    from cpn.frontend import dsh_launcher

    def prepare(source, env):
        assert "PROVIDER_API_KEY" not in env
        assert (source / "node_modules/.bin/tsx").is_file()
        state["prepared"] = source
        target = source / "rpnh-frontend/frontend.ts"
        target.parent.mkdir(exist_ok=True)
        target.touch()
        return "node", target

    monkeypatch.setattr(dsh_launcher, "prepare_dsh_frontend", prepare)


def test_basic_is_deliberate_and_never_probes_or_installs(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unexpected process"))
    assert setup.frontend_status("basic") == {
        "frontend": "basic", "available": True, "detail": "Built-in Basic frontend is ready"}
    assert not preferences_path().exists()
    assert setup.configure_frontend("basic") == FrontendPreferences("basic")
    assert json.loads(preferences_path().read_text()) == {
        "schema_version": "rpnh/frontend_preferences/v1", "frontend": "basic"}


@pytest.mark.parametrize("frontend", ["auto", "invalid", "", None])
def test_invalid_setup_target_is_rejected(frontend):
    with pytest.raises(ValueError, match="Choose a frontend"):
        setup.configure_frontend(frontend)
    with pytest.raises(ValueError):
        setup.frontend_status(frontend)
    assert not preferences_path().exists()


@pytest.mark.parametrize("frontend", ["codex", "opencode"])
def test_existing_real_version_fixture_links_without_install(tmp_path, monkeypatch, frontend):
    binary = tmp_path / frontend
    text = ("codex-cli " if frontend == "codex" else "") + setup._version(frontend)
    startup = ""
    if frontend == "opencode":
        startup = (
            "import os\nfrom pathlib import Path\n"
            "assert Path(os.environ['HOME']).is_dir()\n"
            "(Path(os.environ['XDG_CACHE_HOME']) / 'startup-cache').write_text('fixture')\n")
    binary.write_text(f"#!{sys.executable}\nimport sys\nassert sys.argv[1:] == ['--version']\n{startup}print({text!r})\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert setup.frontend_status(frontend)["available"] is True
    assert not preferences_path().exists()
    result = setup.configure_frontend(frontend, output_fn=lambda _: None)
    assert getattr(result, f"{frontend}_binary") == binary
    assert read_frontend_preferences() == result
    assert not (tmp_path / "frontends").exists()


@pytest.mark.parametrize("frontend", ["codex", "opencode", "dsh"])
def test_status_leaves_no_probe_directories_or_preferences(tmp_path, monkeypatch, process_fixture, frontend):
    if frontend == "dsh":
        monkeypatch.setenv("DSH_SOURCE", str(_prepared_source(tmp_path)))
    else:
        (tmp_path / frontend).touch()
        process_fixture["tools"].add(frontend)
    if frontend != "opencode":
        monkeypatch.setattr(Path, "mkdir", lambda *a, **k: pytest.fail("status created a directory"))
    result = setup.frontend_status(frontend)
    assert set(result) == {"frontend", "available", "detail"}
    assert result["available"]
    assert not preferences_path().exists()
    assert all(not root.exists() for root in process_fixture["probe_roots"])
    assert not any("install" in args or "fetch" in args for args, _ in process_fixture["calls"])


@pytest.mark.parametrize("frontend", ["codex", "opencode", "dsh"])
def test_missing_with_install_disabled_preserves_selection(process_fixture, frontend):
    save_frontend_preferences("basic")
    original = preferences_path().read_text()
    assert not setup.frontend_status(frontend)["available"]
    with pytest.raises(RuntimeError, match="not ready"):
        setup.configure_frontend(frontend, install=False)
    assert preferences_path().read_text() == original
    assert all(not root.exists() for root in process_fixture["probe_roots"])
    assert not (preferences_path().parent / "frontends").exists()


@pytest.mark.parametrize("frontend", ["codex", "opencode"])
def test_missing_native_client_installs_exact_pin_and_reuses_it(tmp_path, process_fixture, frontend):
    messages = []
    save_frontend_preferences("basic", dsh_source=tmp_path / "old-dsh")
    result = setup.configure_frontend(frontend, output_fn=messages.append)
    expected = setup._managed(frontend) / "node_modules/.bin" / frontend
    assert getattr(result, f"{frontend}_binary") == expected
    assert result.dsh_source == tmp_path / "old-dsh"
    assert read_frontend_preferences() == result
    assert process_fixture["installed"] == {frontend}
    assert any(message.startswith("Installing ") for message in messages)
    installed = len(process_fixture["calls"])
    assert setup.configure_frontend(frontend, output_fn=messages.append) == result
    assert all("install" not in args for args, _ in process_fixture["calls"][installed:])


@pytest.mark.parametrize("frontend", ["codex", "opencode"])
@pytest.mark.parametrize("failure", ["download", "version"])
def test_install_or_verification_failure_keeps_preferences(process_fixture, frontend, failure):
    save_frontend_preferences("basic")
    original = preferences_path().read_text()
    if failure == "download":
        process_fixture["fail"] = "install"
    else:
        process_fixture["bad_installed_version"] = True
    with pytest.raises(RuntimeError):
        setup.configure_frontend(frontend, output_fn=lambda _: None)
    assert preferences_path().read_text() == original
    assert all(not root.exists() for root in process_fixture["probe_roots"])


@pytest.mark.parametrize("failure", ["version", "timeout", "cancel"])
def test_opencode_private_probe_is_cleaned_on_failure(tmp_path, monkeypatch, failure):
    from cpn.frontend import opencode_launcher

    roots = []

    def fail_probe(binary, env, cwd):
        root = Path(env["HOME"]).parent
        roots.append(root)
        assert root.is_dir() and cwd == root / "display"
        (Path(env["XDG_CACHE_HOME"]) / "native-startup").write_text("fixture")
        if failure == "cancel":
            raise KeyboardInterrupt
        if failure == "timeout":
            raise subprocess.TimeoutExpired([binary, "--version"], 10)
        raise RuntimeError("RPNH requires exactly OpenCode 1.19.3")

    monkeypatch.setattr(opencode_launcher, "check_version", fail_probe)
    expected = {"version": RuntimeError, "timeout": subprocess.TimeoutExpired,
                "cancel": KeyboardInterrupt}[failure]
    with pytest.raises(expected):
        setup._probe_binary("opencode", tmp_path / "opencode")
    assert roots and all(not root.exists() for root in roots)
    assert not preferences_path().exists()


@pytest.mark.parametrize("tool", ["node", "npm"])
def test_actionable_prerequisite_failure_keeps_preferences(process_fixture, tool):
    process_fixture["tools"].remove(tool)
    save_frontend_preferences("basic")
    with pytest.raises(RuntimeError, match=f"Install {tool}"):
        setup.configure_frontend("codex", output_fn=lambda _: None)
    assert read_frontend_preferences().frontend == "basic"


@pytest.mark.parametrize("frontend", ["codex", "opencode"])
def test_invalid_explicit_binding_never_installs(tmp_path, process_fixture, frontend):
    with pytest.raises(RuntimeError, match="not ready"):
        setup.configure_frontend(frontend, **{f"{frontend}_binary": tmp_path / "missing"})
    assert not preferences_path().exists()
    assert not process_fixture["installed"]


def test_codex_environment_override_is_deliberate(tmp_path, monkeypatch, process_fixture):
    monkeypatch.setenv("RPNH_CODEX_BIN", str(tmp_path / "missing-wrapper"))
    assert not setup.frontend_status("codex")["available"]
    with pytest.raises(RuntimeError, match="not ready"):
        setup.configure_frontend("codex")
    assert not process_fixture["installed"]


def test_dsh_ready_checkout_is_connected_without_preparing(tmp_path, monkeypatch, process_fixture):
    source = _prepared_source(tmp_path)
    monkeypatch.setenv("DSH_SOURCE", str(source))
    result = setup.configure_frontend("dsh", output_fn=lambda _: None)
    assert result.dsh_source == source
    assert not process_fixture["installed"]
    assert all("install" not in args and "fetch" not in args for args, _ in process_fixture["calls"])


@pytest.mark.parametrize("existing_pnpm", [True, False])
def test_dsh_install_uses_official_source_frozen_dependencies_and_preflight(
        tmp_path, monkeypatch, process_fixture, existing_pnpm):
    _prepare_fixture(monkeypatch, process_fixture)
    monkeypatch.setenv("PROVIDER_API_KEY", "fixture-not-a-credential")
    proxies = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
    for name in proxies:
        monkeypatch.setenv(name, f"fixture-{name.lower()}")
    if not existing_pnpm:
        process_fixture["tools"].remove("pnpm")
    result = setup.configure_frontend("dsh", output_fn=lambda _: None)
    assert result.dsh_source == setup._managed("dsh")
    assert process_fixture["prepared"] == result.dsh_source
    assert read_frontend_preferences() == result
    assert process_fixture["calls"][-1][0][-1] == "--check"
    assert process_fixture["installed"] == (set() if existing_pnpm else {"pnpm"})
    downloads = []
    for argv, kwargs in process_fixture["calls"]:
        env = kwargs["env"]
        assert "PROVIDER_API_KEY" not in env
        if "fetch" in argv or "install" in argv:
            downloads.append(argv)
            assert all(env[name] == f"fixture-{name.lower()}" for name in proxies)
            assert kwargs["timeout"] == (1800 if Path(argv[0]).name == "npm" else 600)
        else:
            assert all(name not in env for name in proxies)
    assert any("fetch" in argv for argv in downloads)
    assert any(Path(argv[0]).name == "pnpm" and "install" in argv for argv in downloads)


def test_existing_dsh_checkout_gets_dependencies_without_fetch(tmp_path, monkeypatch, process_fixture):
    source = tmp_path / "checkout"
    source.mkdir()
    _prepare_fixture(monkeypatch, process_fixture)
    result = setup.configure_frontend("dsh", dsh_source=source, output_fn=lambda _: None)
    assert result.dsh_source == source
    assert not any("fetch" in args for args, _ in process_fixture["calls"])


@pytest.mark.parametrize("problem", ["revision", "node", "pnpm", "assets", "git"])
def test_dsh_failure_keeps_old_preferences(tmp_path, monkeypatch, process_fixture, problem):
    source = _prepared_source(tmp_path)
    save_frontend_preferences("basic")
    original = preferences_path().read_text()
    _prepare_fixture(monkeypatch, process_fixture)
    if problem == "revision":
        process_fixture["revision"] = "wrong-revision"
    elif problem == "node":
        process_fixture["node"] = "v23.0.0"
    elif problem == "pnpm":
        (source / "node_modules/.bin/tsx").unlink()
        process_fixture["pnpm"] = "wrong-version"
    elif problem == "git":
        process_fixture["tools"].remove("git")
    else:
        process_fixture["dsh_check_failure"] = True
    with pytest.raises(RuntimeError):
        setup.configure_frontend("dsh", dsh_source=source, output_fn=lambda _: None)
    assert preferences_path().read_text() == original


def test_explicit_dsh_source_precedes_environment_and_saved(tmp_path, monkeypatch, process_fixture):
    source = _prepared_source(tmp_path)
    monkeypatch.setenv("DSH_SOURCE", str(tmp_path / "missing"))
    save_frontend_preferences("basic", dsh_source=tmp_path / "saved")
    result = setup.configure_frontend("dsh", dsh_source=source, output_fn=lambda _: None)
    assert result.dsh_source == source


def test_bad_preferences_are_not_replaced_or_used_for_install(process_fixture):
    preferences_path().write_text("{broken")
    assert not setup.frontend_status("opencode")["available"]
    with pytest.raises(ValueError, match="invalid JSON"):
        setup.configure_frontend("opencode")
    assert not process_fixture["calls"]
    assert preferences_path().read_text() == "{broken"


def test_unsupported_platform_can_still_choose_basic(monkeypatch, process_fixture):
    monkeypatch.setattr(setup.sys, "platform", "win32")
    assert not setup.frontend_status("opencode")["available"]
    with pytest.raises(RuntimeError, match="Linux/WSL2"):
        setup.configure_frontend("opencode")
    assert setup.configure_frontend("basic").frontend == "basic"


@pytest.mark.parametrize("kwargs", [{"codex_binary": "wrong-type"},
                                        {"opencode_binary": Path("wrong-frontend")}])
def test_invalid_binding_arguments_fail_without_changes(kwargs, process_fixture):
    with pytest.raises(ValueError):
        setup.configure_frontend("codex", **kwargs)
    assert not preferences_path().exists()
    assert not process_fixture["calls"]


@pytest.mark.parametrize("frontend", ["codex", "opencode", "dsh"])
def test_cancelled_install_never_saves_preferences(monkeypatch, process_fixture, frontend):
    save_frontend_preferences("basic")
    original = preferences_path().read_text()
    previous = subprocess.run

    def cancel(argv, **kwargs):
        if "install" in argv or "fetch" in argv:
            raise KeyboardInterrupt
        return previous(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", cancel)
    with pytest.raises(KeyboardInterrupt):
        setup.configure_frontend(frontend, output_fn=lambda _: None)
    assert preferences_path().read_text() == original
    if frontend == "dsh":
        assert not setup._managed("dsh").exists()


@pytest.mark.parametrize("frontend", ["codex", "opencode"])
def test_status_reports_stale_saved_binding_and_setup_links_ready_path(
        tmp_path, monkeypatch, process_fixture, frontend):
    save_frontend_preferences(frontend, **{f"{frontend}_binary": tmp_path / "missing"})
    (tmp_path / frontend).touch()
    process_fixture["tools"].add(frontend)
    assert not setup.frontend_status(frontend)["available"]
    result = setup.configure_frontend(frontend, output_fn=lambda _: None)
    assert getattr(result, f"{frontend}_binary") == tmp_path / frontend
    assert not process_fixture["installed"]


def test_missing_custom_dsh_source_is_not_created(tmp_path, process_fixture):
    source = tmp_path / "custom-missing"
    with pytest.raises(RuntimeError, match="supply an existing pinned checkout"):
        setup.configure_frontend("dsh", dsh_source=source, output_fn=lambda _: None)
    assert not source.exists()
    assert not preferences_path().exists()


def test_failed_dsh_download_is_retryable(tmp_path, monkeypatch, process_fixture):
    _prepare_fixture(monkeypatch, process_fixture)
    process_fixture["fail"] = "fetch"
    with pytest.raises(RuntimeError, match="fixture download failure"):
        setup.configure_frontend("dsh", output_fn=lambda _: None)
    assert not setup._managed("dsh").exists()
    assert not preferences_path().exists()
    process_fixture["fail"] = None
    assert setup.configure_frontend("dsh", output_fn=lambda _: None).dsh_source == setup._managed("dsh")


def _backend_wrapper(tmp_path, target):
    wrapper = tmp_path / ".local/bin/codex"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        '#!/bin/bash\n'
        f'real_codex="{target}"\n'
        # --version succeeds despite the wrapper being unsuitable for remote.
        f'if [[ "$1" == --version ]]; then echo "codex-cli {setup._version("codex")}"; exit; fi\n'
        'exec "$real_codex" --config "developer_instructions=fixture" --add-dir /unused "$@"\n')
    wrapper.chmod(0o755)
    return wrapper


def test_default_codex_recovers_stock_from_known_backend_wrapper(tmp_path, monkeypatch):
    package = tmp_path / "node/lib/node_modules/@openai/codex"
    stock = package / "bin/codex.js"
    stock.parent.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@openai/codex", "version": setup._version("codex")}))
    stock.write_text(
        f'#!{sys.executable}\nimport sys\nassert sys.argv[1:] == ["--version"]\n'
        f'print("codex-cli {setup._version("codex")}")\n')
    stock.chmod(0o755)
    wrapper = _backend_wrapper(tmp_path, stock)
    original = wrapper.read_text()
    monkeypatch.setenv("PATH", str(wrapper.parent))
    status = setup.frontend_status("codex")
    assert not status["available"] and "backend wrapper" in status["detail"]
    result = setup.configure_frontend("codex", output_fn=lambda _: None)
    assert result.codex_binary == stock
    assert setup.frontend_status("codex")["available"]
    assert wrapper.read_text() == original
    assert not setup._managed("codex").exists()


def test_backend_wrapper_without_stock_falls_back_to_managed_install(
        tmp_path, process_fixture):
    wrapper = _backend_wrapper(tmp_path, tmp_path / "missing-stock")
    process_fixture["tools"].add("codex")
    previous = setup.shutil.which

    def which(value):
        return str(wrapper) if value == "codex" else previous(value)

    # Keep the fixture's package and process mechanics; only PATH resolution differs.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(setup.shutil, "which", which)
        result = setup.configure_frontend("codex", output_fn=lambda _: None)
    assert result.codex_binary == setup._managed("codex") / "node_modules/.bin/codex"
    assert process_fixture["installed"] == {"codex"}


@pytest.mark.parametrize("explicit", ["environment", "argument"])
def test_explicit_codex_wrapper_binding_retains_priority(tmp_path, monkeypatch, explicit):
    wrapper = _backend_wrapper(tmp_path, tmp_path / "missing-stock")
    if explicit == "environment":
        monkeypatch.setenv("RPNH_CODEX_BIN", str(wrapper))
        kwargs = {}
    else:
        kwargs = {"codex_binary": wrapper}
    result = setup.configure_frontend("codex", output_fn=lambda _: None, **kwargs)
    assert result.codex_binary == wrapper
    assert setup.frontend_status("codex")["available"]


def test_install_children_receive_only_requested_proxy_settings(tmp_path, monkeypatch, process_fixture):
    names = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
    # Fixture values only; never print or persist the owner's routing settings.
    for name in names:
        monkeypatch.setenv(name, f"fixture-{name.lower()}")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PROVIDER_API_KEY", "ALL_PROXY"):
        monkeypatch.setenv(name, "fixture-excluded")
    env = setup._install_environment(tmp_path / "install-runtime")
    assert all(name not in env for name in names)
    setup._npm_install("@openai/codex@" + setup._version("codex"), setup._managed("codex"), env)
    version_call, install_call = process_fixture["calls"][-2:]
    assert version_call[0][-1] == "--version"
    assert version_call[1]["timeout"] == 5
    assert all(name not in version_call[1]["env"] for name in names)
    assert install_call[1]["timeout"] == 1800
    assert all(install_call[1]["env"][name] == f"fixture-{name.lower()}" for name in names)
    assert all(name not in install_call[1]["env"] for name in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PROVIDER_API_KEY", "ALL_PROXY"))
    assert all(name not in setup._probe_environment() for name in names)
    assert all(name not in env for name in names)
    _prepare_fixture(monkeypatch, process_fixture)
    setup.configure_frontend("dsh", output_fn=lambda _: None)
    for argv, kwargs in process_fixture["calls"]:
        if "install" in argv or "fetch" in argv:
            assert all(kwargs["env"][name] == f"fixture-{name.lower()}" for name in names)
            assert kwargs["timeout"] == (1800 if Path(argv[0]).name == "npm" else 600)
        else:
            assert all(name not in kwargs.get("env", {}) for name in names)


def test_npm_timeout_keeps_selection_and_does_not_retry(tmp_path, monkeypatch, process_fixture):
    save_frontend_preferences("basic")
    original = preferences_path().read_text()
    previous = subprocess.run
    installs = []

    def timeout(argv, **kwargs):
        if Path(argv[0]).name == "npm" and "install" in argv:
            installs.append(argv)
            assert kwargs["timeout"] == 1800
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return previous(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="Frontend command failed"):
        setup.configure_frontend("codex", output_fn=lambda _: None)
    assert len(installs) == 1
    assert preferences_path().read_text() == original


def test_partial_npm_package_requires_install_and_successful_probe(tmp_path, process_fixture):
    save_frontend_preferences("basic")
    destination = setup._managed("codex")
    partial = destination / "node_modules/@openai/codex/package.json"
    partial.parent.mkdir(parents=True)
    partial.write_text(json.dumps({"name": "@openai/codex", "version": setup._version("codex")}))
    assert not (destination / "node_modules/.bin/codex").exists()
    process_fixture["fail"] = "install"
    with pytest.raises(RuntimeError, match="fixture download failure"):
        setup.configure_frontend("codex", output_fn=lambda _: None)
    assert read_frontend_preferences().frontend == "basic"
    process_fixture["fail"] = None
    result = setup.configure_frontend("codex", output_fn=lambda _: None)
    assert result.codex_binary == destination / "node_modules/.bin/codex"
    installs = [(args, kwargs) for args, kwargs in process_fixture["calls"] if "install" in args]
    assert len(installs) == 2
    assert all(args[args.index("--prefix") + 1] == str(destination) for args, _ in installs)
    assert process_fixture["calls"][-1][0][-1] == "--version"
