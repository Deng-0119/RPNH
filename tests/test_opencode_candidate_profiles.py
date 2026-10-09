"""Pure G1 checks for explicit, certification-only OpenCode profiles.

These are Python projection tests with ApplicationDouble/LocalGateway and mocked
version-process results. They open no socket, PTY, native client, or model port.
The shared manifest is an RPNH source-extracted SDK subset, not an upstream
SDK-generated validator or evidence of native G2/G3 certification.
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, fields
import importlib.resources
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from types import SimpleNamespace

import jsonschema
import pytest

from cpn.frontend import opencode_launcher as launcher
from cpn.frontend import opencode_protocol as protocol_module
from cpn.frontend.opencode_protocol import (
    DEFAULT_PROFILE, OPENCODE_COMMIT, OPENCODE_VERSION,
    OpenCodeCompatibilityProfile, OpenCodeProtocol, encode_sse,
    get_opencode_profile, require_opencode_profile,
)
from cpn.rpnh.frontend_application import FrontendError
from test_opencode_frontend import (
    ApplicationDouble, LocalGateway, MODEL, MODEL_TWO, SID, prompt,
)

DEFAULT_VERSION = "1.18.32"
DEFAULT_COMMIT = "545f51d26cc39a907d2867492d498d9607ea5fa4"
CANDIDATE_VERSION = "1.18.35"
CANDIDATE_COMMIT = "53d1eabb61e21162157817bf677da0a4ad3332e3"
EFFECTS = {"create_session", "submit", "command", "abort"}


def _manifest():
    return json.loads(importlib.resources.files("cpn.frontend").joinpath(
        "opencode_compatibility.v1.json").read_text(encoding="utf-8"))


def _forbid_process(*_args, **_kwargs):
    pytest.fail("Pure G1 must not launch a real subprocess")


@pytest.fixture(autouse=True)
def no_native_effects(monkeypatch):
    # Individual probe tests replace run with fixed outputs or a timeout.
    # Keep the same boundaries closed when this file is run on its own.
    monkeypatch.setattr(subprocess, "run", _forbid_process)
    monkeypatch.setattr(subprocess, "Popen", _forbid_process)
    def forbidden_boundary(*_args, **_kwargs):
        pytest.fail("Pure G1 must not open sockets, PTYs, processes, or threads")
    monkeypatch.setattr(socket, "socket", forbidden_boundary)
    monkeypatch.setattr(threading.Thread, "start", forbidden_boundary)
    for name in ("openpty", "fork"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden_boundary)


@pytest.fixture(params=[None, CANDIDATE_VERSION], ids=["production", "candidate"])
def profile(request):
    return get_opencode_profile(certification_version=request.param)


@pytest.fixture
def context(tmp_path, profile):
    app = ApplicationDouble()
    protocol = OpenCodeProtocol(LocalGateway(app), str(tmp_path), profile=profile)
    protocol.route("POST", "/session", {})
    try:
        yield app, protocol
    finally:
        protocol.close()


def test_manifest_is_authoritative_and_default_pin_is_unchanged():
    manifest = _manifest()
    assert manifest["status"] == "source-extracted-subset-not-upstream-generator-output"
    assert get_opencode_profile() is DEFAULT_PROFILE
    assert DEFAULT_PROFILE == OpenCodeCompatibilityProfile(
        DEFAULT_VERSION, DEFAULT_COMMIT, False)
    assert OPENCODE_VERSION == DEFAULT_VERSION == manifest["upstream"]["package_version"]
    assert OPENCODE_COMMIT == DEFAULT_COMMIT == manifest["upstream"]["commit"]
    assert launcher.DEFAULT_PROFILE is DEFAULT_PROFILE
    assert launcher.OPENCODE_VERSION == DEFAULT_VERSION
    assert inspect.signature(launcher.check_version).parameters["profile"].default is DEFAULT_PROFILE
    assert inspect.signature(OpenCodeProtocol).parameters["profile"].default is DEFAULT_PROFILE
    candidates = manifest["certification_candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["package_version"] == CANDIDATE_VERSION
    assert candidate["commit"] == CANDIDATE_COMMIT
    assert candidate["status"] == "certification-only"
    assert candidate["production_enabled"] is False
    assert candidate["contract_source_commit"] == DEFAULT_COMMIT
    assert candidate["native_g2"] == candidate["native_g3"] == "not-run"
    assert candidate["shared_contract_evidence"]
    selected = get_opencode_profile(certification_version=CANDIDATE_VERSION)
    assert selected == OpenCodeCompatibilityProfile(CANDIDATE_VERSION, CANDIDATE_COMMIT, True)
    assert require_opencode_profile(selected) is selected


def test_profile_is_frozen_and_protocol_binding_is_read_only(context, profile):
    _, protocol = context
    assert [field.name for field in fields(profile)] == ["version", "commit", "certification_only"]
    for name, replacement in [
        ("version", "9.9.9"), ("commit", "0" * 40),
        ("certification_only", not profile.certification_only),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(profile, name, replacement)
    assert protocol.profile is profile
    with pytest.raises(AttributeError):
        protocol.profile = DEFAULT_PROFILE


@pytest.mark.parametrize("version", [
    "", DEFAULT_VERSION, "1.18.33", "1.18.36", "latest", "next",
    ">=1.18.35", "^1.18.35", "~1.18.35", "1.18.x", "v1.18.35",
    "1.18.35+local", "1.18.35-rc.1", "1.18.35 ", " 1.18.35",
    "1.18.35\n", "opencode 1.18.35", "/tmp/opencode", CANDIDATE_COMMIT,
    11835, True, {}, [CANDIDATE_VERSION], Path("1.18.35"),
])
def test_resolver_rejects_every_unregistered_exact_target(version):
    with pytest.raises(ValueError):
        get_opencode_profile(certification_version=version)


@pytest.mark.parametrize("forged", [
    OpenCodeCompatibilityProfile(CANDIDATE_VERSION, DEFAULT_COMMIT, True),
    OpenCodeCompatibilityProfile(DEFAULT_VERSION, CANDIDATE_COMMIT, False),
    OpenCodeCompatibilityProfile(CANDIDATE_VERSION, CANDIDATE_COMMIT, False),
    OpenCodeCompatibilityProfile(DEFAULT_VERSION, DEFAULT_COMMIT, True),
    OpenCodeCompatibilityProfile(CANDIDATE_VERSION, CANDIDATE_COMMIT, 1),
    OpenCodeCompatibilityProfile(DEFAULT_VERSION, DEFAULT_COMMIT, 0),
    OpenCodeCompatibilityProfile(CANDIDATE_VERSION, "0" * 40, True),
    OpenCodeCompatibilityProfile("1.18.36", CANDIDATE_COMMIT, True),
    OpenCodeCompatibilityProfile("1.18.35+local", CANDIDATE_COMMIT, True),
    SimpleNamespace(version=CANDIDATE_VERSION, commit=CANDIDATE_COMMIT, certification_only=True),
    {"version": CANDIDATE_VERSION, "commit": CANDIDATE_COMMIT, "certification_only": True},
    None,
])
def test_forged_profile_fails_before_probe_or_application(tmp_path, forged):
    app = ApplicationDouble()
    with pytest.raises(ValueError):
        require_opencode_profile(forged)
    with pytest.raises(ValueError):
        launcher.check_version("never-executed", {}, tmp_path, profile=forged)
    with pytest.raises(ValueError):
        OpenCodeProtocol(LocalGateway(app), str(tmp_path), profile=forged)
    assert app.calls == []


@pytest.mark.parametrize("prefix", ["", "opencode "])
def test_probe_uses_exact_selected_profile_and_restricted_arguments(
        tmp_path, monkeypatch, profile, prefix):
    calls = []
    env = {"PATH": "/fixture/bin", "TERM": "xterm"}
    def fixed_probe(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=prefix + profile.version + "\n", returncode=0)
    monkeypatch.setattr(subprocess, "run", fixed_probe)
    launcher.check_version("/fixture/opencode", env, tmp_path, profile=profile)
    assert calls == [(["/fixture/opencode", "--version"], {
        "env": env, "cwd": tmp_path, "stdin": subprocess.DEVNULL,
        "capture_output": True, "text": True, "timeout": 10, "check": False,
    })]
    assert calls[0][1]["env"] is not env
    protocol = OpenCodeProtocol(LocalGateway(ApplicationDouble()), str(tmp_path), profile=profile)
    assert protocol.profile is profile
    assert protocol.route("GET", "/health").body["version"] == profile.version
    protocol.close()


@pytest.mark.parametrize("output", [
    "{version}+modified", "{version}-rc.1", "v{version}",
    "OpenCode {version}", "{version}\nextra text", "warning\n{version}",
    "opencode {version} trailing", "opencode\n{version}", "", "latest", "1.18.36",
])
def test_probe_rejects_suffixes_extra_output_and_unknown_versions(
        tmp_path, monkeypatch, profile, output):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        stdout=output.format(version=profile.version), returncode=0))
    with pytest.raises(RuntimeError, match="exactly"):
        launcher.check_version("/fixture/opencode", {}, tmp_path, profile=profile)


def test_probe_rejects_other_registered_version(tmp_path, monkeypatch, profile):
    other = CANDIDATE_VERSION if profile is DEFAULT_PROFILE else DEFAULT_VERSION
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        stdout=other, returncode=0))
    with pytest.raises(RuntimeError, match="exactly"):
        launcher.check_version("/fixture/opencode", {}, tmp_path, profile=profile)


@pytest.mark.parametrize("returncode", [1, 127, -15])
def test_probe_rejects_nonzero_even_when_version_matches(tmp_path, monkeypatch, profile, returncode):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        stdout=profile.version, returncode=returncode))
    with pytest.raises(RuntimeError, match="exactly"):
        launcher.check_version("/fixture/opencode", {}, tmp_path, profile=profile)


def test_probe_timeout_is_not_an_acceptance(tmp_path, monkeypatch, profile):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        launcher.check_version("/fixture/opencode", {}, tmp_path, profile=profile)


def test_default_probe_rejects_candidate_without_explicit_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        stdout=CANDIDATE_VERSION, returncode=0))
    with pytest.raises(RuntimeError, match=DEFAULT_VERSION):
        launcher.check_version("/fixture/opencode", {}, tmp_path)


def _forbid_owner(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("RPNH owner must not be constructed")
    monkeypatch.setattr(launcher, "FrontendGateway", forbidden)
    monkeypatch.setattr(launcher, "RegistryFrontendApplication", forbidden)
    monkeypatch.setattr(launcher, "OpenCodeHTTPServer", forbidden)


def test_production_rejects_candidate_before_owner_despite_environment(tmp_path, monkeypatch):
    _forbid_owner(monkeypatch)
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/fixture/opencode")
    attempted_overrides = {
        "OPENCODE_VERSION": CANDIDATE_VERSION,
        "OPENCODE_CERTIFY_VERSION": CANDIDATE_VERSION,
        "OPENCODE_CERTIFICATION_VERSION": CANDIDATE_VERSION,
        "RPNH_OPENCODE_VERSION": CANDIDATE_VERSION,
        "RPNH_OPENCODE_CERTIFY_VERSION": CANDIDATE_VERSION,
        "OPENCODE_CONFIG": "/fixture/candidate-config.json",
    }
    for name, value in attempted_overrides.items():
        monkeypatch.setenv(name, value)
    calls = []
    def fixed_probe(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=CANDIDATE_VERSION, returncode=0)
    monkeypatch.setattr(subprocess, "run", fixed_probe)
    with pytest.raises(RuntimeError, match=DEFAULT_VERSION):
        launcher.run_opencode_frontend(tmp_path / "root", tmp_path / "execution.json")
    assert len(calls) == 1 and calls[0][0] == ["/fixture/opencode", "--version"]
    actual_env = calls[0][1]["env"]
    assert not (set(attempted_overrides) - {"OPENCODE_CONFIG"}) & set(actual_env)
    assert actual_env["OPENCODE_CONFIG"] != attempted_overrides["OPENCODE_CONFIG"]
    assert "profile" not in inspect.signature(launcher.run_opencode_frontend).parameters
    assert "certification_version" not in inspect.signature(launcher.run_opencode_frontend).parameters


def test_production_success_uses_default_profile_after_real_probe_logic(tmp_path, monkeypatch):
    # All execution/transport endpoints are doubles; the real check_version and
    # OpenCodeProtocol run unchanged, so this cannot conceal a skipped probe.
    timeline = []
    observed = []
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/fixture/opencode")
    monkeypatch.setattr(launcher, "RegistryFrontendApplication", _forbid_process)
    class FakeGateway(LocalGateway):
        def __init__(self, _factory, **_kwargs):
            timeline.append("owner")
            super().__init__(ApplicationDouble())
        def close(self):
            timeline.append("owner-close")
            self.app.close()
    class FakeServer:
        url = "http://127.0.0.1:1"
        password = "fixture-only"
        def __init__(self, protocol):
            observed.append(protocol)
            timeline.append("server")
        def start(self):
            timeline.append("start")
        def close(self):
            timeline.append("server-close")
    def fixed_process(args, **_kwargs):
        if args[1] == "--version":
            timeline.append("probe")
            return SimpleNamespace(stdout=DEFAULT_VERSION, returncode=0)
        assert args[1] == "attach"
        timeline.append("attach")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher, "FrontendGateway", FakeGateway)
    monkeypatch.setattr(launcher, "OpenCodeHTTPServer", FakeServer)
    monkeypatch.setattr(subprocess, "run", fixed_process)
    assert launcher.run_opencode_frontend(tmp_path / "root", tmp_path / "execution.json") == 0
    assert timeline == ["probe", "owner", "server", "start", "attach", "server-close", "owner-close"]
    assert len(observed) == 1
    assert observed[0].profile is DEFAULT_PROFILE
    assert observed[0].route("GET", "/health").body["version"] == DEFAULT_VERSION


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_non_linux_platform_rejected_before_probe_and_owner(tmp_path, monkeypatch, platform):
    _forbid_owner(monkeypatch)
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: pytest.fail("No binary lookup on unsupported OS"))
    with pytest.raises(RuntimeError, match="Linux/WSL2 only"):
        launcher.run_opencode_frontend(tmp_path / "root", tmp_path / "execution.json")


def test_default_and_candidate_instances_are_isolated_without_global_mutation(tmp_path):
    before = (protocol_module.OPENCODE_VERSION, protocol_module.OPENCODE_COMMIT, launcher.OPENCODE_VERSION)
    selected = get_opencode_profile(certification_version=CANDIDATE_VERSION)
    default = OpenCodeProtocol(LocalGateway(ApplicationDouble()), str(tmp_path / "default"))
    candidate = OpenCodeProtocol(LocalGateway(ApplicationDouble()), str(tmp_path / "candidate"), profile=selected)
    for instance, expected in [(default, DEFAULT_PROFILE), (candidate, selected)]:
        created = instance.route("POST", "/session", {}).body
        assert instance.profile is expected
        assert created["version"] == expected.version
        assert instance.route("GET", f"/session/{SID}").body["version"] == expected.version
        assert instance.route("GET", "/session").body[0]["version"] == expected.version
        for route in ("/health", "/global/health"):
            assert instance.route("GET", route).body == {"healthy": True, "version": expected.version}
    candidate.close()
    assert default.route("GET", "/health").body["version"] == DEFAULT_VERSION
    assert default.profile is get_opencode_profile()
    assert (protocol_module.OPENCODE_VERSION, protocol_module.OPENCODE_COMMIT, launcher.OPENCODE_VERSION) == before
    assert before == (DEFAULT_VERSION, DEFAULT_COMMIT, DEFAULT_VERSION)
    default.close()


def test_shared_source_extracted_subset_validates_both_profile_projections(context, profile):
    app, protocol = context
    manifest = _manifest()
    assert manifest["status"] == "source-extracted-subset-not-upstream-generator-output"
    for path, original_schema in manifest["responses"].items():
        schema = copy.deepcopy(original_schema)
        response = protocol.route("GET", path).body
        if path == "/global/health":
            # The manifest's production const is intentional. Only profile
            # metadata varies; the DTO structure and $defs remain shared.
            assert schema["properties"]["version"] == {"const": DEFAULT_VERSION}
            assert response["version"] == profile.version
            if profile.certification_only:
                with pytest.raises(jsonschema.ValidationError):
                    jsonschema.Draft202012Validator(original_schema).validate(response)
                schema["properties"]["version"] = {"const": profile.version}
        jsonschema.Draft202012Validator({**schema, "$defs": manifest["$defs"]}).validate(response)
    receipt = protocol.route("POST", f"/session/{SID}/message", prompt(messageID="msg_schema_candidate"))
    jsonschema.Draft202012Validator({
        **manifest["post_responses"]["/session/:session/message"], "$defs": manifest["$defs"],
    }).validate(receipt.body)
    app.commit()
    message_validator = jsonschema.Draft202012Validator({
        "$ref": "#/$defs/MessageWithParts", "$defs": manifest["$defs"],
    })
    for message in protocol.route("GET", f"/session/{SID}/message").body:
        message_validator.validate(message)
    event_validator = jsonschema.Draft202012Validator({
        **manifest["events"]["message.part.updated"], "$defs": manifest["$defs"],
    })
    part_events = [event for event in protocol.events() if event["payload"]["type"] == "message.part.updated"]
    assert part_events
    for event in part_events:
        event_validator.validate(event["payload"])
    assert app.physical_calls == 0


@pytest.mark.parametrize("shape", ["create", "prompt", "command"])
def test_three_model_shapes_round_trip_exact_selection_and_variant(tmp_path, profile, shape):
    app = ApplicationDouble()
    gateway = LocalGateway(app)
    calls = []
    original_call = gateway.call
    def record(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        return original_call(name, *args, **kwargs)
    gateway.call = record
    protocol = OpenCodeProtocol(gateway, str(tmp_path), profile=profile)
    if shape != "create":
        protocol.route("POST", "/session", {})
    if shape == "create":
        reply = protocol.route("POST", "/session", {
            "model": {"providerID": "rpnh", "id": MODEL_TWO["selection"], "variant": "high"},
        })
        assert reply.body["model"] == {"providerID": "rpnh", "id": MODEL_TWO["selection"]}
        assert reply.body["metadata"]["rpnh_provider"] == MODEL_TWO["provider"]
        assert reply.body["metadata"]["rpnh_exact_model"] == MODEL_TWO["model"]
        assert reply.body["metadata"]["rpnh_reasoning_effort"] == "high"
        effect = [call for call in calls if call[0] == "create_session"][-1]
        assert effect[1] == (MODEL_TWO["selection"],)
    elif shape == "prompt":
        reply = protocol.route("POST", f"/session/{SID}/message", prompt(
            model={"providerID": "rpnh", "modelID": MODEL_TWO["selection"]}, variant="high"))
        assert reply.body["info"]["modelID"] == MODEL_TWO["selection"]
        assert app.views[0]["turns"][0]["model"]["model"] == MODEL_TWO["model"]
        effect = [call for call in calls if call[0] == "submit"][-1]
        assert effect[2]["selection_id"] == MODEL_TWO["selection"]
    else:
        protocol.route("POST", f"/session/{SID}/command", {
            "command": "rpnh-help", "arguments": "", "model": "rpnh/" + MODEL_TWO["selection"], "variant": "high",
        })
        effect = [call for call in calls if call[0] == "command"][-1]
        assert effect[2]["selection_id"] == MODEL_TWO["selection"]
    assert effect[2]["reasoning_effort"] == "high"
    assert app.physical_calls == 0
    protocol.close()


@pytest.mark.parametrize("shape,model", [
    ("create", {"providerID": "rpnh", "modelID": MODEL["selection"]}),
    ("create", "rpnh/" + MODEL["selection"]),
    ("prompt", {"providerID": "rpnh", "id": MODEL["selection"]}),
    ("prompt", "rpnh/" + MODEL["selection"]),
    ("command", {"providerID": "rpnh", "modelID": MODEL["selection"]}),
    ("command", MODEL["selection"]),
    ("command", "other/" + MODEL["selection"]),
    ("prompt", {"providerID": "other", "modelID": MODEL["selection"]}),
    ("prompt", {"providerID": "rpnh", "modelID": "unknown"}),
    ("prompt", {"providerID": "rpnh", "modelID": MODEL["selection"], "variant": "high"}),
    ("command", "rpnh/unknown"),
])
def test_invalid_model_shapes_never_fallback_or_reach_effect(context, shape, model):
    app, protocol = context
    before = len(app.calls)
    target = "/session" if shape == "create" else f"/session/{SID}/{'message' if shape == 'prompt' else 'command'}"
    body = {"model": model}
    if shape == "prompt":
        body = prompt(model=model)
    elif shape == "command":
        body.update(command="rpnh-help", arguments="")
    with pytest.raises(FrontendError):
        protocol.route("POST", target, body)
    assert not EFFECTS & set(app.calls[before:])
    assert app.views[0]["turns"] == []


def test_variants_are_scoped_to_exact_model_and_not_injected(tmp_path, profile):
    class DistinctVariants(ApplicationDouble):
        def configuration(self):
            configuration = super().configuration()
            configuration["profiles"][1].update(
                reasoning_effort="low", supported_reasoning_efforts=["low"], default_reasoning_effort="low")
            return configuration
    app = DistinctVariants()
    protocol = OpenCodeProtocol(LocalGateway(app), str(tmp_path), profile=profile)
    protocol.route("POST", "/session", {})
    catalog = protocol.route("GET", "/provider").body["all"][0]["models"]
    assert set(catalog[MODEL["selection"]]["variants"]) == {"low", "medium", "high"}
    assert set(catalog[MODEL_TWO["selection"]]["variants"]) == {"low"}
    second_model = {"providerID": "rpnh", "modelID": MODEL_TWO["selection"]}
    with pytest.raises(FrontendError, match="reasoning effort"):
        protocol.route("POST", f"/session/{SID}/message", prompt(model=second_model, variant="high"))
    assert app.views[0]["turns"] == []
    protocol.route("POST", f"/session/{SID}/message", prompt(model=second_model))
    assert app.views[0]["turns"][0]["model"]["reasoning_effort"] == "low"
    assert app.views[0]["turns"][0]["model"]["model"] == MODEL_TWO["model"]
    protocol.close()


@pytest.mark.parametrize("method,target", [
    ("POST", f"/session/{SID}/{operation}")
    for operation in ["shell", "fork", "revert", "share", "summarize", "init", "permissions/id"]
] + [("PUT", "/auth/provider"), ("PATCH", "/config"), ("PATCH", "/global/config"),
     ("POST", "/mcp"), ("POST", "/auth/provider"), ("POST", "/config")])
def test_unsupported_routes_remain_zero_effect_for_both_profiles(context, method, target):
    app, protocol = context
    before = list(app.calls)
    with pytest.raises(FrontendError):
        protocol.route(method, target, {"command": "forbidden"})
    assert app.calls == before
    assert app.physical_calls == 0


@pytest.mark.parametrize("changes", [
    {"agent": "build"}, {"variant": "ultra"}, {"variant": {"effort": "high"}},
    {"system": "override"}, {"tools": {}}, {"reasoning_effort": "high"},
    {"parts": [{"type": "file", "url": "file:///unavailable"}]},
    {"parts": [{"type": "text", "text": "question", "synthetic": True}]},
])
def test_unsupported_prompt_overrides_do_not_reach_effect(context, changes):
    app, protocol = context
    before = len(app.calls)
    with pytest.raises(FrontendError):
        protocol.route("POST", f"/session/{SID}/message", prompt(**changes))
    assert not EFFECTS & set(app.calls[before:])


@pytest.mark.parametrize("target,headers", [
    ("/session?workspace=elsewhere", {}),
    ("/session?directory=/foreign", {}),
    ("/session", {"X-OpenCode-Directory": "/foreign"}),
    ("/session", {"X-OpenCode-Workspace": "elsewhere"}),
    ("https://elsewhere.invalid/session", {}),
])
def test_foreign_location_rejected_without_application_access(context, target, headers):
    app, protocol = context
    before = list(app.calls)
    with pytest.raises(FrontendError):
        protocol.route("GET", target, headers=headers)
    assert app.calls == before


def test_stable_messages_parts_sse_and_read_only_replay(context, profile):
    app, protocol = context
    path = f"/session/{SID}/message"
    protocol.route("POST", path, prompt(messageID="msg_g1_stable"))
    app.commit()
    protocol.notify()
    expected = protocol.route("GET", path).body
    assert [message["info"]["role"] for message in expected] == ["user", "assistant"]
    stable_ids = [(message["info"]["id"], [part["id"] for part in message["parts"]]) for message in expected]
    calls_before = {effect: app.calls.count(effect) for effect in EFFECTS}
    first_events = protocol.events()
    assert first_events == protocol.events()
    seen = set()
    for event in first_events + protocol.events():
        assert set(event) == {"directory", "payload"}
        assert event["directory"] == protocol.directory
        assert event["payload"]["id"].startswith("evt_")
        assert event["payload"]["properties"]["sessionID"] == SID
        encoded = encode_sse(event)
        assert encoded.startswith(b"id: ") and encoded.endswith(b"\n\n")
        assert encoded.count(b"\ndata: ") == 1
        assert json.loads(encoded.split(b"\ndata: ")[1]) == event
        seen.add(event["payload"]["id"])
    assert len(seen) == len(first_events)
    recreated = OpenCodeProtocol(LocalGateway(app), protocol.directory, profile=profile)
    for _ in range(3):
        assert recreated.route("GET", path).body == expected
        assert [(message["info"]["id"], [part["id"] for part in message["parts"]])
                for message in recreated.route("GET", path).body] == stable_ids
        recreated.route("GET", "/session")
        recreated.route("GET", "/session/status")
        recreated.events()
    assert {effect: app.calls.count(effect) for effect in EFFECTS} == calls_before
    assert app.physical_calls == 0
    recreated.close()


def test_retries_and_conflicting_idempotency_keys_preserve_single_turn(context):
    app, protocol = context
    path = f"/session/{SID}/message"
    body = prompt(messageID="msg_g1_retry")
    headers = {"Idempotency-Key": "msg_g1_retry"}
    first = protocol.route("POST", path, body, headers)
    assert protocol.route("POST", path, body, headers).headers == first.headers
    assert len(app.views[0]["turns"]) == 1
    before = app.calls.count("submit")
    with pytest.raises(FrontendError, match="agree"):
        protocol.route("POST", path, body, {"Idempotency-Key": "other"})
    assert app.calls.count("submit") == before
    with pytest.raises(FrontendError, match="Conflict"):
        protocol.route("POST", path, prompt("different text", messageID="msg_g1_retry"), headers)
    assert len(app.views[0]["turns"]) == 1
    app.commit()
    assert protocol.route("POST", path, body, headers).headers == first.headers
    assert len(app.views[0]["turns"]) == 1
    protocol.route("POST", path, prompt(messageID="msg_g1_new"))
    assert len(app.views[0]["turns"]) == 2
    assert app.physical_calls == 0
