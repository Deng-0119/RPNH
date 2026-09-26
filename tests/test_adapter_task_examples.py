"""Installed cross-adapter task bundle; no provider or host process is used."""
from __future__ import annotations

import json
from pathlib import Path

from cpn.examples.cli import main as examples_main
from cpn.rpnh_cli import main as rpnh_main


def test_manifest_exposes_one_task_across_all_supported_hosts(capsys) -> None:
    assert rpnh_main(["examples", "list"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["schema_version"] == "rpnh/live_adapter_example/v1"
    assert manifest["example_id"] == "batch-summary-v1"
    assert {host["id"] for host in manifest["hosts"]} == {
        "basic", "codex", "dsh", "opencode"}
    assert {host["evidence"] for host in manifest["hosts"]} == {
        f"{host}/evidence.json" for host in ("basic", "codex", "dsh", "opencode")}


def test_exported_bundle_is_self_contained_and_verifiable(
        tmp_path: Path, capsys,
) -> None:
    exported = tmp_path / "adapter-task"
    assert examples_main(["export", "--output", str(exported)]) == 0
    capsys.readouterr()
    required = {
        "README.md", "README_ZH.md", "manifest.json", "task.txt",
        "expected.json",
        *(f"{host}/README.md" for host in ("basic", "codex", "dsh", "opencode")),
        *(f"{host}/README_ZH.md" for host in ("basic", "codex", "dsh", "opencode")),
        *(f"{host}/evidence.json" for host in ("basic", "codex", "dsh", "opencode")),
    }
    assert required <= {
        path.relative_to(exported).as_posix()
        for path in exported.rglob("*") if path.is_file()}
    answer = exported / "answer.json"
    answer.write_text((exported / "expected.json").read_text(), encoding="utf-8")
    assert examples_main(["verify", "--result", str(answer)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "PASS"
    assert verified["normalized_result"]["total"] == 36


def test_verifier_rejects_prose_extra_fields_and_wrong_arithmetic(
        tmp_path: Path,
) -> None:
    invalid = (
        "```json\n{\"count\":3,\"total\":36,\"mean\":12,"
        "\"minimum\":9,\"maximum\":15}\n```",
        '{"count":3,"total":36,"mean":12,"minimum":9,"maximum":15,"note":"x"}',
        '{"count":3,"total":35,"mean":12,"minimum":9,"maximum":15}',
        '{"total":36,"count":3,"mean":12,"minimum":9,"maximum":15}',
    )
    for index, body in enumerate(invalid):
        result = tmp_path / f"invalid-{index}.json"
        result.write_text(body, encoding="utf-8")
        try:
            examples_main(["verify", "--result", str(result)])
        except (json.JSONDecodeError, ValueError):
            pass
        else:
            raise AssertionError("invalid adapter-task answer was accepted")


def test_sanitized_live_evidence_is_separate_and_complete() -> None:
    root = Path(__file__).resolve().parents[1] / "cpn/examples/adapter_task"
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    physical = {}
    for host in ("basic", "codex", "dsh", "opencode"):
        evidence = json.loads(
            (root / host / "evidence.json").read_text(encoding="utf-8"))
        assert evidence["schema_version"] == "rpnh/live_adapter_example_evidence/v1"
        assert evidence["host"] == host
        assert evidence["validation"]["authority_status"] == "PASS"
        assert evidence["validation"]["semantic_status"] == "PASS"
        assert evidence["validation"]["health_probe_calls"] == 0
        assert evidence["validation"]["route_switches"] == 0
        assert evidence["normalized_result"] == expected
        serialized = json.dumps(evidence, sort_keys=True)
        assert "/home/" not in serialized and "/tmp/" not in serialized
        physical[host] = evidence["validation"]["successful_physical_responses"]
    assert physical == {"basic": 2, "codex": 2, "dsh": 1, "opencode": 2}
