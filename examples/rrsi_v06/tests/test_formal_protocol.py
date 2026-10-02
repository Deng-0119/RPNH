from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace

import pytest

from rpnh_rrsi.formal_protocol import (
    FormalProtocolError,
    formal_protocol_mapping,
    load_formal_protocol,
    role_input_manifest,
    score_timeout_response,
    validate_execution_selection,
)


CONFIG = Path(__file__).parents[1] / "protocol.example.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _write(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_loads_the_frozen_local_formal_protocol():
    protocol = load_formal_protocol(CONFIG)

    assert protocol.scope == "formal_rrsi_v06_b0_local/v1"
    assert protocol.protocol_id == "rrsi-v06-b0-example"
    assert protocol.execution["round_count"] == 2
    assert protocol.method["role_limits"]["Proposer"]["max_edits"] == 80
    assert protocol.source_fixture["editable_paths"] == ("policy.py",)
    assert protocol.scorer["outer_bounds"]["max_llm_attempts"] == 1
    assert protocol.source_basis["official_source"] == "public_and_reviewed"
    assert protocol.source_basis["reproduction_claim"] == (
        "not_an_official_paper_benchmark_reproduction")


@pytest.mark.parametrize("mutate", [
    lambda data: data["execution"].update(round_count=1),
    lambda data: data["source_basis"].update(reproduction_claim="official_reproduction"),
    lambda data: data["task_manifests"]["heldout"]["tasks"][0].update(expected=31),
    lambda data: data["source_fixture"].update(editable_paths=["policy.py", "agent/__init__.py"]),
    lambda data: data.update(protocol_id="another-valid-id"),
    lambda data: data["task_manifests"]["evolve"].update(manifest_id="another-valid-id"),
    lambda data: data["source_fixture"]["files"][0].update(
        content="# def build_messages(raw): only JSON timeout zero-padded default 30\n"),
    lambda data: data.update(checksum="forbidden"),
])
def test_rejects_constant_and_identity_tampering(tmp_path: Path, mutate):
    data = _config()
    mutate(data)

    with pytest.raises(FormalProtocolError):
        load_formal_protocol(_write(tmp_path, data))


def test_role_projection_exposes_only_calibration_and_evolve():
    protocol = load_formal_protocol(CONFIG)
    evolve = role_input_manifest(protocol, "evolve")

    assert evolve["split"] == "evolve"
    assert [task["task_id"] for task in evolve["tasks"]] == [
        "evolve-missing", "evolve-null", "evolve-blank"]
    for split in ("heldout", "export"):
        with pytest.raises(FormalProtocolError, match="evaluation-only"):
            role_input_manifest(protocol, split)


def test_public_mapping_is_detached_and_has_no_identity_fields():
    mapping = formal_protocol_mapping(load_formal_protocol(CONFIG))
    mapping["execution"]["round_count"] = 99

    assert load_formal_protocol(CONFIG).execution["round_count"] == 2
    assert not {"hash", "checksum", "fingerprint", "digest"}.intersection(
        key.lower() for key in json.loads(CONFIG.read_text(encoding="utf-8")))


@pytest.mark.parametrize(("response", "expected", "result"), [
    ('{"timeout": 7}', 7, True),
    ('{"timeout": "7"}', 7, False),
    ('{"timeout": 7, "extra": 1}', 7, False),
    ('{"timeout": 7, "timeout": 7}', 7, False),
])
def test_fixed_scorer_accepts_only_the_exact_timeout_object(response, expected, result):
    assert score_timeout_response(response, expected) is result


def test_user_owned_execution_selection_is_provider_neutral(tmp_path: Path) -> None:
    from cpn.llm_adapters.config import LLMExecutionSelection
    from cpn.rpnh.llm_contracts import LLMInputTarget

    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "example-test-model",
        "argv": ["example-test-model"],
        "probe_argv": ["example-test-model", "--probe"],
        "env": {}, "inherit_env": [],
    }), encoding="utf-8")
    selection = LLMExecutionSelection(
        LLMInputTarget("example-test-model", 4096, 65536),
        "local_process", adapter.resolve(), 30)
    protocol = load_formal_protocol(CONFIG)
    validate_execution_selection(protocol, selection)
    with pytest.raises(FormalProtocolError, match="complete RPNH target"):
        validate_execution_selection(
            protocol, replace(selection, timeout_seconds=0))
