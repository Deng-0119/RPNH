"""Distribution coverage for the bundled DSH integration assets."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.dsh import launcher


ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "cpn/examples/__init__.py",
    "cpn/examples/cli.py",
    "cpn/examples/adapter_task/manifest.json",
    "cpn/examples/adapter_task/task.txt",
    "cpn/examples/adapter_task/expected.json",
    "cpn/examples/adapter_task/README.md",
    "cpn/examples/adapter_task/README_ZH.md",
    *(f"cpn/examples/adapter_task/{host}/{name}"
      for host in ("basic", "codex", "dsh", "opencode")
      for name in ("README.md", "README_ZH.md", "evidence.json")),
    "integrations/dsh/UPSTREAM.json",
    "integrations/dsh/UPSTREAM_LICENSE",
    "integrations/dsh/prepare.sh",
    "integrations/dsh/run.sh",
    "integrations/dsh/verify.sh",
    "integrations/dsh/patch_upstream.py",
    *(f"integrations/dsh/src/{path.name}" for path in (ROOT / "integrations/dsh/src").glob("*.ts")),
}

PUBLIC_SOURCE_ASSETS = {
    "AGENTS.md",
    "CHANGELOG.md",
    "CHANGELOG_ZH.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING_ZH.md",
    "SECURITY.md",
    "SECURITY_ZH.md",
    "docs/index.md",
    "docs/index_ZH.md",
    "docs/guides/installation.md",
    "docs/guides/installation_ZH.md",
    "examples/README.md",
    "examples/README_ZH.md",
    "examples/workflow_patterns/scenarios/parallel.json",
}


def _write_factory_seam(root: Path, *, drift: bool = False) -> Path:
    target = root / "packages/core/agent-loop/src/index.ts"
    target.parent.mkdir(parents=True)
    target.write_text(
        "\n".join((
            "import { ReactLoopAgent } from './react.js'",
            "interface PreparedAgent {",
            "  agent: ReactLoopAgent",
            "}",
            "let machine: ReactLoopAgent | undefined",
            "const loopCtx = this.runtime.ctx",
            "machine = new ReactLoopAgent(loopCtx, id, options, session)",
            "  constructor(ctx: Context, config: Config) {" if not drift
            else "  constructor(ctx: Context) {",
        )),
        encoding="utf-8",
    )
    return target


def test_dsh_factory_patch_is_semantic_and_idempotent(tmp_path: Path) -> None:
    target = _write_factory_seam(tmp_path)
    command = [sys.executable, str(ROOT / "integrations/dsh/patch_upstream.py"),
               str(tmp_path)]
    subprocess.run(command, check=True)
    prepared = target.read_text(encoding="utf-8")
    assert "export interface AgentMachine" in prepared
    assert "machine = createMachine(loopCtx, id, options, session)" in prepared
    subprocess.run(command, check=True)
    assert target.read_text(encoding="utf-8") == prepared


def test_dsh_factory_patch_fails_loud_on_semantic_drift(tmp_path: Path) -> None:
    target = _write_factory_seam(tmp_path, drift=True)
    original = target.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "integrations/dsh/patch_upstream.py"),
         str(tmp_path)],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "differs from the pinned semantic seam" in result.stderr
    assert target.read_text(encoding="utf-8") == original


def test_distribution_contains_runtime_and_public_source_assets(
        tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    command = (
        "import setuptools.build_meta as backend; "
        f"backend.build_wheel({str(dist_dir)!r}); backend.build_sdist({str(dist_dir)!r})"
    )
    subprocess.run([sys.executable, "-c", command], cwd=ROOT, check=True)

    wheel, sdist = sorted(dist_dir.iterdir())
    for archive in (wheel, sdist):
        if archive.suffix == ".whl":
            import zipfile

            with zipfile.ZipFile(archive) as contents:
                names = set(contents.namelist())
        else:
            import tarfile

            with tarfile.open(archive) as contents:
                names = {name.split("/", 1)[1] for name in contents.getnames() if "/" in name}
        assert ASSETS <= names
        if archive.suffix != ".whl":
            assert PUBLIC_SOURCE_ASSETS <= names


def test_dsh_console_help_works_from_installed_distribution(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    subprocess.run(
        [sys.executable, "-c", "import setuptools.build_meta as b; b.build_wheel(" + repr(str(wheel_dir)) + ")"],
        cwd=ROOT,
        check=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))
    environment = tmp_path / "installed"
    venv.EnvBuilder(with_pip=True).create(environment)
    scripts = environment / ("Scripts" if sys.platform == "win32" else "bin")
    subprocess.run(
        [str(scripts / "python"), "-m", "pip", "install",
         "--no-deps", "--force-reinstall", str(wheel)],
        cwd=tmp_path,
        check=True,
    )
    script = scripts / "rpnh-dsh"
    result = subprocess.run([str(script), "--help"], cwd=tmp_path, text=True, capture_output=True, check=True)
    assert "pinned DSH source checkout" in result.stdout
    assert str(ROOT) not in result.stdout


def test_dsh_launcher_delegates_to_the_bundled_runner(monkeypatch) -> None:
    runner = ROOT / "integrations/dsh/run.sh"
    calls = []
    monkeypatch.setattr(launcher.importlib.resources, "files", lambda package: runner.parent)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, check: calls.append((command, check)) or SimpleNamespace(returncode=23),
    )

    assert launcher.main(["/tmp/pinned-dsh", "--offline", "--app-option"]) == 23
    assert calls == [([
        "bash", str(runner), sys.executable, "/tmp/pinned-dsh",
        "--offline", "--app-option",
    ], False)]


def test_dsh_launcher_rejects_offline_execution_conflict(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    with pytest.raises(SystemExit) as raised:
        launcher.main([
            "/tmp/pinned-dsh", "--offline", "--execution", "/tmp/profile.json",
            "--root", "sessions", "--task", "test",
        ])

    assert raised.value.code == 2


def test_dsh_launcher_resolves_public_profile_without_credentials(
        monkeypatch, tmp_path: Path,
) -> None:
    runner = ROOT / "integrations/dsh/run.sh"
    selected = (tmp_path / "selection.json").resolve()
    calls: list[tuple[list[str], bool]] = []
    resolved: list[Path | None] = []
    profile = SimpleNamespace(
        name="configured", selection_id="provider/exact-model",
        provider="provider", provider_display_name="Provider",
    )
    selection = SimpleNamespace(
        input_target=SimpleNamespace(
            model_condition="exact-model",
            max_output_tokens=128,
            max_response_bytes=4096,
        ),
        adapter_kind="external_provider",
        timeout_seconds=30,
        as_registry_policy=lambda: {
            "adapter_kind": "external_provider",
            "timeout_seconds": 30,
            "route_provenance": [{
                "route_id": "primary", "provider": "provider",
                "backend": "responses", "transport": "https",
            }],
        },
    )
    monkeypatch.setattr(launcher.importlib.resources, "files", lambda _package: runner.parent)
    monkeypatch.setattr(
        launcher, "resolve_execution_path",
        lambda explicit, **kwargs: (
            resolved.append(explicit) or selected),
    )
    monkeypatch.setattr(launcher, "profile_for_path", lambda path: profile)
    monkeypatch.setattr(launcher, "missing_credentials", lambda path: ())
    monkeypatch.setattr(
        launcher, "load_llm_execution_selection", lambda path: selection)
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda command, check: calls.append((command, check))
        or SimpleNamespace(returncode=0),
    )
    monkeypatch.setenv("RPNH_API_KEY", "credential-must-not-be-serialized")

    assert launcher.main([
        "/tmp/pinned-dsh", "--execution", str(selected),
        "--root", "sessions", "--task", "test",
    ]) == 0

    assert resolved == [selected]
    command = calls[0][0]
    assert command[:4] == [
        "bash", str(runner), sys.executable, "/tmp/pinned-dsh"]
    assert "--execution" not in command
    assert command[command.index("--execution-path") + 1] == str(selected)
    public = json.loads(command[command.index("--execution-profile") + 1])
    assert public["provider"] == "provider"
    assert public["model_condition"] == "exact-model"
    assert public["timeout_seconds"] == 30
    assert public["transport_kind"] == "https"
    assert "policy" not in public
    serialized = json.dumps(public)
    assert "credential-must-not-be-serialized" not in serialized
    assert "headers" not in serialized


def test_dsh_launcher_forwards_explicit_managed_tool_selection(
        monkeypatch, tmp_path: Path,
) -> None:
    runner = ROOT / "integrations/dsh/run.sh"
    selected = (tmp_path / "selection.json").resolve()
    plugin_config = (tmp_path / "plugins.json").resolve()
    calls: list[list[str]] = []
    profile = SimpleNamespace(
        name="configured", selection_id="provider/exact-model",
        provider="provider", provider_display_name="Provider",
    )
    selection = SimpleNamespace(
        input_target=SimpleNamespace(
            model_condition="exact-model",
            max_output_tokens=128,
            max_response_bytes=4096,
        ),
        adapter_kind="external_provider",
        timeout_seconds=30,
        as_registry_policy=lambda: {
            "route_provenance": [{"transport": "https"}],
        },
    )
    monkeypatch.setattr(
        launcher.importlib.resources, "files", lambda _package: runner.parent)
    monkeypatch.setattr(
        launcher, "resolve_execution_path",
        lambda *_args, **_kwargs: selected)
    monkeypatch.setattr(launcher, "profile_for_path", lambda _path: profile)
    monkeypatch.setattr(launcher, "missing_credentials", lambda _path: ())
    monkeypatch.setattr(
        launcher, "load_llm_execution_selection", lambda _path: selection)
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda command, check: calls.append(command)
        or SimpleNamespace(returncode=0),
    )

    assert launcher.main([
        "/tmp/pinned-dsh", "--execution", str(selected),
        "--plugin-config", str(plugin_config),
        "--managed-tool", "double_value=demo/double",
        "--root", "sessions", "--task", "test",
    ]) == 0

    command = calls[0]
    assert command[command.index("--plugin-config") + 1] == str(plugin_config)
    assert command[command.index("--managed-tool") + 1] == (
        "double_value=demo/double")
    assert command[command.index("--execution-path") + 1] == str(selected)


def test_dsh_history_never_resolves_or_inspects_a_profile(monkeypatch) -> None:
    runner = ROOT / "integrations/dsh/run.sh"
    calls = []
    monkeypatch.setattr(launcher.importlib.resources, "files", lambda _package: runner.parent)
    for name in (
            "resolve_execution_path", "profile_for_path", "missing_credentials",
            "load_llm_execution_selection"):
        monkeypatch.setattr(
            launcher, name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(
                f"history called {_name}"),
        )
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda command, check: calls.append(command)
        or SimpleNamespace(returncode=0),
    )

    assert launcher.main([
        "/tmp/pinned-dsh", "--history", "--root", "sessions",
        "--session-id", "existing",
    ]) == 0

    assert calls == [[
        "bash", str(runner), sys.executable, "/tmp/pinned-dsh",
        "--history", "--root", "sessions", "--session-id", "existing",
    ]]


def test_dsh_missing_credentials_fails_before_runner(monkeypatch, tmp_path: Path) -> None:
    selected = (tmp_path / "selection.json").resolve()
    monkeypatch.setattr(
        launcher, "resolve_execution_path", lambda *_args, **_kwargs: selected)
    monkeypatch.setattr(
        launcher, "profile_for_path", lambda _path: SimpleNamespace(
            name="configured", selection_id="provider/model", provider="provider",
            provider_display_name="Provider"))
    monkeypatch.setattr(
        launcher, "missing_credentials", lambda _path: ("RPNH_API_KEY",))
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    with pytest.raises(SystemExit) as raised:
        launcher.main([
            "/tmp/pinned-dsh", "--execution", str(selected),
            "--root", "sessions", "--task", "test",
        ])

    assert raised.value.code == 2


def test_dsh_run_propagates_explicit_python_interpreter(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    dsh = tmp_path / "dsh"
    (dsh / "node_modules/.bin").mkdir(parents=True)
    (dsh / "node_modules/.bin/tsx").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (dsh / "node_modules/.bin/tsx").chmod(0o755)
    python_log = tmp_path / "python.log"
    node_log = tmp_path / "node.log"
    fake_python = tmp_path / "selected-python"
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$RPNH_TEST_PYTHON_LOG"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (fake_bin / "git").write_text(
        "#!/bin/sh\nprintf '%s\\n' ddefc45fbc7f8e46dd73185e68295696d1297887\n",
        encoding="utf-8",
    )
    (fake_bin / "node").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" >> "$RPNH_TEST_NODE_LOG"\n',
        encoding="utf-8",
    )
    (fake_bin / "git").chmod(0o755)
    (fake_bin / "node").chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "RPNH_TEST_PYTHON_LOG": str(python_log),
        "RPNH_TEST_NODE_LOG": str(node_log),
    }

    subprocess.run([
        "bash", str(ROOT / "integrations/dsh/run.sh"), str(fake_python),
        str(dsh), "--history", "--root", "sessions", "--session-id", "saved",
    ], cwd=tmp_path, env=environment, check=True)

    python_arguments = python_log.read_text(encoding="utf-8").splitlines()
    assert python_arguments == [
        str(ROOT / "integrations/dsh/patch_upstream.py"), str(dsh)]
    node_arguments = node_log.read_text(encoding="utf-8").splitlines()
    assert node_arguments[-2:] == ["--python", str(fake_python)]
