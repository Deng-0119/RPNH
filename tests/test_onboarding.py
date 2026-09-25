from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from cpn.rpnh.onboarding import SetupCancelled, configuration_report, setup_profile
from cpn.rpnh.provider_setup import add_provider_model, build_provider_catalog
from cpn.rpnh.user_config import discover_profiles, read_selected_path, resolve_execution_path
from cpn.rpnh_cli import _choose_frontend, main


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "user" / "config.json"))
    for name in ("RPNH_PROFILE_DIR", "RPNH_PROVIDER_CATALOG", "RPNH_EXECUTION_CONFIG", "RPNH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Setup and doctor must not build or invoke a provider transport.
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port",
                        lambda *a, **k: pytest.fail("configuration called a model transport"))


def answers(values):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def api_answers(*, auth="3", name="my-model", save="y", endpoint=None):
    values = ["1", "My provider", "vendor/model@exact", name,
              endpoint or "https://api.example.invalid/v1/chat/completions", auth]
    if auth != "3":
        values.append("MY_API_KEY")
    if auth == "2":
        values.append("X-API-Key")
    return [*values, "n", save]


def setup_api(**kwargs):
    return setup_profile(input_fn=answers(api_answers(**kwargs)), output_fn=lambda _: None)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("auth", ["1", "2", "3"])
def test_fresh_setup_builds_exact_catalog_profiles_and_selection(tmp_path, monkeypatch, auth):
    monkeypatch.setenv("MY_API_KEY", "not-a-real-secret")
    output = []
    profile = setup_profile(input_fn=answers(api_answers(auth=auth)), output_fn=output.append)
    assert profile.name == "my-model"
    assert profile.model_condition == "vendor/model@exact"
    assert read_selected_path() == profile.path
    result = build_provider_catalog(check=True)
    assert result["profile_count"] == 1
    catalog = json.loads((tmp_path / "user/provider_models.json").read_bytes())
    model = catalog["providers"][0]["models"][0]
    assert model["model_condition"] == profile.model_condition
    credential = model["adapter"]["credential"]
    if auth == "3":
        assert credential is None
    else:
        assert credential == {"environment": "MY_API_KEY",
                              "header": "Authorization" if auth == "1" else "X-API-Key",
                              "prefix": "Bearer " if auth == "1" else ""}
    assert "not-a-real-secret" not in "\n".join(output)
    assert all(b"not-a-real-secret" not in b for b in snapshot(tmp_path / "user").values())
    assert configuration_report()["ready"] is True
    assert configuration_report()["network_checked"] is False
    assert not (tmp_path / ".rpnh").exists()


def test_add_preserves_existing_model_and_can_select_again(tmp_path):
    first = setup_api()
    before_adapter = first.path.parent.parent.joinpath("adapters/my-model.json").read_bytes()
    second = setup_profile(input_fn=answers(api_answers(name="second")),
                           output_fn=lambda _: None, add_new=True)
    assert len(discover_profiles()) == 2
    assert first.path.parent.parent.joinpath("adapters/my-model.json").read_bytes() == before_adapter
    assert read_selected_path() == second.path
    selected = setup_profile(input_fn=answers(["1"]), output_fn=lambda _: None)
    assert selected.name == first.name and read_selected_path() == first.path


@pytest.mark.parametrize("where", ["confirm", "eof", "interrupt"])
def test_cancelled_setup_does_not_write_user_files(tmp_path, where):
    def cancelled(_prompt):
        if where == "eof":
            raise EOFError
        raise KeyboardInterrupt
    read = answers(api_answers(save="n")) if where == "confirm" else cancelled
    with pytest.raises(SetupCancelled):
        setup_profile(input_fn=read, output_fn=lambda _: None)
    assert not (tmp_path / "user").exists()


def test_duplicate_name_and_invalid_input_do_not_modify_existing_config(tmp_path):
    setup_api()
    root = tmp_path / "user"
    catalog = json.loads((root / "provider_models.json").read_bytes())
    model = catalog["providers"][0]["models"][0]
    before = snapshot(root)
    with pytest.raises(ValueError, match="already exists"):
        add_provider_model("My provider", model)
    assert snapshot(root) == before
    model["profile"] = "second"
    model["adapter"]["endpoint"] = "http://api.example.invalid"
    with pytest.raises(ValueError):
        add_provider_model("My provider", model)
    assert snapshot(root) == before


def test_failed_install_restores_existing_catalog_and_generated_files(tmp_path, monkeypatch):
    setup_api()
    from cpn.rpnh import provider_setup
    root = tmp_path / "user"
    before = snapshot(root)
    original = provider_setup._write_atomic
    failed = False

    def fail_once(path, payload):
        nonlocal failed
        if path == root / "profiles/execution/second.json" and not failed:
            failed = True
            raise OSError("injected write failure")
        return original(path, payload)

    monkeypatch.setattr(provider_setup, "_write_atomic", fail_once)
    with pytest.raises(OSError, match="injected write failure"):
        setup_profile(input_fn=answers(api_answers(name="second")),
                      output_fn=lambda _: None, add_new=True)
    assert failed and snapshot(root) == before
    assert build_provider_catalog(check=True)["status"] == "in_sync"


def test_missing_key_diagnostics_and_guidance_never_display_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_API_KEY", raising=False)
    output = []
    setup_profile(input_fn=answers(api_answers(auth="1")), output_fn=output.append)
    assert "export MY_API_KEY" in "\n".join(output)
    report = configuration_report()
    assert not report["ready"]
    assert any(c["check"] == "credentials" and c["status"] == "error" for c in report["checks"])
    monkeypatch.setenv("MY_API_KEY", "fake-private-value")
    assert configuration_report()["ready"]
    assert "fake-private-value" not in json.dumps(configuration_report())


def test_local_setup_keeps_argv_and_inherited_names_without_execution(tmp_path):
    values = ["2", "Local", "model with spaces", "local-model",
              '{python} -m my_adapter --model "{model}"', "{python} --version",
              "OPTIONAL_KEY,OPTIONAL_KEY", "y", "120", "4096", "1048576", "y"]
    profile = setup_profile(input_fn=answers(values), output_fn=lambda _: None)
    catalog = json.loads((tmp_path / "user/provider_models.json").read_bytes())
    model = catalog["providers"][0]["models"][0]
    assert model["adapter"]["inherit_env"] == ["OPTIONAL_KEY"]
    adapter = json.loads(profile.path.parent.parent.joinpath("adapters/local-model.json").read_bytes())
    assert adapter["argv"][-1] == "model with spaces"
    assert model["timeout_seconds"] == 120 and model["max_output_tokens"] == 4096
    # doctor checks the executable, not whether my_adapter can answer a model call.
    assert configuration_report()["ready"]


def test_invalid_endpoint_reprompts_without_saving_bad_value(tmp_path):
    values = api_answers()
    values.insert(4, "https://user:password@example.invalid/v1/chat/completions")
    output = []
    setup_profile(input_fn=answers(values), output_fn=output.append)
    assert any("without credentials" in line for line in output)
    assert all(b"password" not in data for data in snapshot(tmp_path / "user").values())


def test_alternate_catalog_and_profile_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_PROVIDER_CATALOG", str(tmp_path / "catalog/models.json"))
    monkeypatch.setenv("RPNH_PROFILE_DIR", str(tmp_path / "other/execution"))
    profile = setup_api()
    assert profile.path == tmp_path / "other/execution/my-model.json"
    assert read_selected_path() == profile.path
    assert (tmp_path / "catalog/models.json").is_file()
    assert configuration_report()["ready"]


def test_doctor_reports_missing_config_without_creating_it(tmp_path, capsys):
    assert main(["doctor", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False and report["network_checked"] is False
    assert not (tmp_path / "user").exists()


def test_doctor_detects_generated_drift(tmp_path):
    profile = setup_api()
    doc = json.loads(profile.path.read_bytes())
    doc["max_output_tokens"] += 1
    profile.path.write_text(json.dumps(doc))
    assert not configuration_report()["ready"]


@pytest.mark.parametrize("command", [["init"], ["config", "setup"], ["config", "add"]])
def test_noninteractive_setup_fails_without_reading_stdin_or_writing(tmp_path, monkeypatch, command):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    assert main(command) == 2
    assert not (tmp_path / "user").exists()


def test_cli_init_runs_wizard_and_leaves_task_creation_to_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", answers(api_answers()))
    assert main(["init"]) == 0
    assert read_selected_path() is not None
    assert not (tmp_path / ".rpnh").exists()


def test_prompt_does_not_launch_first_run_wizard(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("batch prompt must not prompt"))
    with pytest.raises(SystemExit) as caught:
        main(["--prompt", "hello"])
    assert caught.value.code == 2
    assert not (tmp_path / "user").exists()


def test_save_default_without_execution_does_not_start_wizard(tmp_path):
    with pytest.raises(ValueError, match="requires --execution"):
        resolve_execution_path(None, save_default=True, allow_interactive_setup=True)
    assert not (tmp_path / "user").exists()


@pytest.mark.parametrize("available", [True, False])
def test_auto_frontend_uses_compatible_client_or_builtin(monkeypatch, available):
    from cpn.frontend import codex_app_server
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    def resolve():
        if not available:
            raise codex_app_server.CodexCompatibilityError("not installed")
        return "/test/codex"
    monkeypatch.setattr(codex_app_server, "resolve_codex_binary", resolve)
    assert _choose_frontend("auto") == ("codex" if available else "basic")
    assert _choose_frontend("codex") == "codex"  # explicit choice never silently changes


def test_auto_noninteractive_and_basic_skip_client_probe(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("cpn.frontend.codex_app_server.resolve_codex_binary",
                        lambda: pytest.fail("must not probe optional frontend"))
    assert _choose_frontend("auto") == "basic"
    assert _choose_frontend("basic") == "basic"


def test_codex_version_check_is_bounded(monkeypatch):
    from cpn.frontend.codex_app_server import CodexCompatibilityError, resolve_codex_binary
    monkeypatch.setattr("cpn.frontend.codex_app_server.shutil.which", lambda _: "/test/codex")
    def timeout(argv, **kwargs):
        assert argv == ["/test/codex", "--version"] and kwargs["timeout"] == 5
        raise subprocess.TimeoutExpired(argv, 5)
    monkeypatch.setattr("cpn.frontend.codex_app_server.subprocess.run", timeout)
    with pytest.raises(CodexCompatibilityError, match="timed out"):
        resolve_codex_binary()


def test_newly_configured_model_launches_real_main_session(tmp_path, monkeypatch, capsys):
    from test_main_session_registry import _TerminalPort
    from cpn.rpnh.main_session import MainSession
    profile = setup_api()
    from cpn.rpnh.llm_contracts import LLMInputResponseBytes
    class HTTPSPort(_TerminalPort):
        def request_once(self, attempt):
            raw = super().request_once(attempt)
            return LLMInputResponseBytes(bytes(raw), status_code=200,
                                         external_request_id="offline-onboarding")
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port",
                        lambda *_a, **_k: HTTPSPort({"reply": "setup works", "task": None}))
    root = tmp_path / "session"
    assert main(["--frontend", "basic", "--session-dir", str(root), "--prompt", "hello"]) == 0
    assert "setup works" in capsys.readouterr().out
    restored = MainSession.resume(root, profile.path)
    history = restored._main_thread.recover_thread()["committed_history"]
    assert len(history) == 1 and history[0]["answer"]["reply"] == "setup works"


def test_setup_recovers_from_stale_saved_selection(tmp_path):
    profile = setup_api()
    path = tmp_path / "user/config.json"
    value = json.loads(path.read_bytes())
    value["profile"] = "removed-profile"
    path.write_text(json.dumps(value))
    selected = setup_profile(input_fn=answers(["1"]), output_fn=lambda _: None)
    assert selected.path == profile.path and read_selected_path() == profile.path


def test_setup_builds_an_existing_unbuilt_catalog(tmp_path):
    import shutil
    setup_api()
    root = tmp_path / "user"
    catalog_bytes = (root / "provider_models.json").read_bytes()
    shutil.rmtree(root / "profiles")
    (root / "config.json").unlink()
    selected = setup_profile(input_fn=answers(["y", "1"]), output_fn=lambda _: None)
    assert selected.name == "my-model"
    assert (root / "provider_models.json").read_bytes() == catalog_bytes
    assert build_provider_catalog(check=True)["status"] == "in_sync"


def test_add_never_overwrites_unmanaged_generated_file(tmp_path):
    setup_api()
    root = tmp_path / "user"
    (root / "profiles/adapters/second.json").write_text("preserve this unrelated file")
    before = snapshot(root)
    with pytest.raises(ValueError, match="unmanaged"):
        setup_profile(input_fn=answers(api_answers(name="second")),
                      output_fn=lambda _: None, add_new=True)
    assert snapshot(root) == before


def test_first_launch_wizard_continues_into_builtin_session(tmp_path, monkeypatch):
    # Test the CLI routing separately from the real execution test above.
    from types import SimpleNamespace
    observed = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", answers([*api_answers(), "/quit"]))
    def create(root, execution):
        assert read_selected_path() == execution
        observed.append(execution)
        return SimpleNamespace(root=root)
    monkeypatch.setattr("cpn.rpnh_cli.MainSession", create)
    assert main(["--frontend", "basic", "--session-dir", str(tmp_path / "session")]) == 0
    assert len(observed) == 1


def test_doctor_rejects_missing_absolute_local_executable(tmp_path):
    values = ["2", "Local", "test-model", "local", "/not/a/real/executable",
              "{python} --version", "", "n", "y"]
    setup_profile(input_fn=answers(values), output_fn=lambda _: None)
    assert not configuration_report()["ready"]
