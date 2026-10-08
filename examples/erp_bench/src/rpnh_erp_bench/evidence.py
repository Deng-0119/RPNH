"""Construct real per-condition records against an explicit read-only contract.

This module owns publication data only. Runtime/Registry authority remains with
RPNH. The caller supplies actual stage observations and owner projections.
"""
from __future__ import annotations

from pathlib import Path
import re
import runpy

from .export import publication_path, source_identity, write_new
from .source import (ERP_PREFIX, RPNH_BASE, UPSTREAM_COMMIT, UPSTREAM_REPOSITORY,
                     VerifiedTask, json_bytes, load_json, safe_file, sha256)

STAGES = ("offline", "mock", "native", "provider", "evaluation")
STATUSES = ("passed", "failed", "blocked", "not_run", "unknown")


def assert_public_safe(value):
    """Reject known private material; publication still needs human review.

    This is not a generic secret detector. Safe projection is an explicit caller
    operation, never automatic substitution of raw bytes under their old hash.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in ("api_key", "password", "access_token", "secret", "authorization") and item:
                raise ValueError("credential-bearing field in public evidence")
            assert_public_safe(key)
            assert_public_safe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_public_safe(item)
    elif isinstance(value, str):
        if (re.search(r"(?:/home/|/mnt/[a-z]/|/Users/|/tmp/|[A-Za-z]:\\)", value)
                or re.search(r"(?<![A-Za-z0-9_:/.-])/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", value)
                or re.search(r"Bearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}", value)
                or value.startswith("/")):
            raise ValueError("private absolute path or credential in public evidence")


def artifact_record(artifacts_root, path: str, *, artifact_id: str, role: str) -> dict:
    publication_path(path)
    data = safe_file(artifacts_root, path).read_bytes()
    if Path(path).suffix == ".json":
        assert_public_safe(load_json(data))
    else:
        assert_public_safe(data.decode("utf-8"))
    return {"id": artifact_id, "role": role, "visibility": "public", "path": path,
            "sha256": sha256(data), "bytes": len(data),
            "hash_scope": "distributed_bytes", "immutable": True}


def private_artifact_record(path, *, artifact_id: str, role: str) -> dict:
    """Only identity leaves this function; the private path is never serialized."""
    path = Path(path)
    data = safe_file(path.parent, path.name).read_bytes()
    return {"id": artifact_id, "role": role, "visibility": "private", "path": None,
            "sha256": sha256(data), "bytes": len(data),
            "hash_scope": "retained_private_bytes", "immutable": True}


def record_original_score(score, artifacts_root, *, artifact_id="original-score", path="original-score.json") -> list[dict]:
    """Publish the safe projection and pathless identities of retained originals."""
    publication_path(path)
    score.verify_raw_reports()
    projection = score.public_projection()
    assert_public_safe(projection)
    write_new(artifacts_root, path, json_bytes(projection))
    rows = [artifact_record(artifacts_root, path, artifact_id=artifact_id, role="grader_output")]
    for name, identity in score.raw_digests.items():
        rows.append({"id": f"{artifact_id}-raw-{name}", "role": "grader_output", "visibility": "private",
                     "path": None, **identity, "immutable": True})
    return rows


def stage_record(status: str, *, commands=(), evidence=(), reason=None) -> dict:
    if status not in STATUSES:
        raise ValueError("invalid stage status")
    return {"status": status, "commands": list(commands), "evidence": list(evidence), "reason": reason}


def stage_observation(*, status: str, commands, started_at, ended_at, exit_code,
                      tested_tree_sha256, reason=None, measurements=None) -> dict:
    """Detailed stage artifact accompanying the four-field shared stage record."""
    from datetime import datetime
    if status not in STATUSES:
        raise ValueError("invalid stage status")
    if exit_code is not None and type(exit_code) is not int:
        raise ValueError("invalid stage exit code")
    if started_at is not None and ended_at is not None:
        start, end = datetime.fromisoformat(started_at), datetime.fromisoformat(ended_at)
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError("stage timestamps must be ordered and timezone-aware")
    if status in ("passed", "failed") and (started_at is None or ended_at is None or exit_code is None):
        raise ValueError("measured stage needs timestamps and exit code")
    if status == "passed" and exit_code != 0:
        raise ValueError("nonzero process exit cannot establish stage pass")
    if not re.fullmatch(r"[0-9a-f]{64}", tested_tree_sha256):
        raise ValueError("missing tested tree identity")
    result = {"status": status, "commands": list(commands), "started_at": started_at,
              "ended_at": ended_at, "exit_code": exit_code,
              "tested_tree_sha256": tested_tree_sha256, "reason": reason,
              "measurements": measurements or {}}
    json_bytes(result)
    assert_public_safe(result)
    return result


def _validator(shared_validator_path):
    path = Path(shared_validator_path).resolve(strict=True)
    if path.name != "validate.py" or not path.with_name("result-manifest.schema.json").is_file():
        raise ValueError("supply the frozen shared_contract/validate.py explicitly")
    return runpy.run_path(str(path))


def validate_result_manifest(manifest, *, shared_validator_path, artifacts_root, source_root=None):
    helper = _validator(shared_validator_path)
    summary = helper["validate"](manifest, artifacts_root=artifacts_root,
                                  source_root=source_root, expected_base=RPNH_BASE,
                                  allowed_prefixes=[ERP_PREFIX])
    assert_public_safe(manifest)
    for row in manifest["artifacts"]:
        if row["visibility"] == "public":
            actual = artifact_record(artifacts_root, row["path"], artifact_id=row["id"], role=row["role"])
            if actual != row:
                raise ValueError("public artifact identity mismatch")
    if manifest["stages"]["native"]["status"] == "passed":
        if not any(row["status"] == "available" for row in manifest["registry_refs"]):
            raise ValueError("native pass requires actual owner projection")
    artifacts = {row["id"]: row for row in manifest["artifacts"]}
    if source_root is not None:
        row = artifacts.get("source-identity")
        if row is None or row["visibility"] != "public":
            raise ValueError("missing exact tested source identity")
        recorded = load_json(safe_file(artifacts_root, row["path"]).read_bytes())
        current = source_identity(source_root)
        if recorded != current or manifest["source"]["changed_files"] != current["changed_files"]:
            raise ValueError("stale tested source tree identity")
    for score in manifest["scores"]:
        if score["claim"] == "supplementary":
            continue
        projections = []
        for identifier in score["evidence"]:
            row = artifacts[identifier]
            if row["role"] == "grader_output" and row["visibility"] == "public":
                payload = load_json(safe_file(artifacts_root, row["path"]).read_bytes())
                if payload.get("schema_version") == "rpnh/erp-original-score-projection/v1":
                    projections.append(payload)
        if len(projections) != 1:
            raise ValueError("original score requires exactly one faithful original projection")
        projection = projections[0]
        if (projection["task_id"] != manifest["task"]["task_id"]
                or projection["scorer_revision"] != score["scorer_revision"]
                or projection["components"] != score["components"]):
            raise ValueError("original score/projection identity mismatch")
        for raw in projection["original_reports"].values():
            if not any(row["visibility"] == "private" and row["role"] == "grader_output"
                       and row["sha256"] == raw["sha256"] and row["bytes"] == raw["bytes"]
                       for row in artifacts.values()):
                raise ValueError("original raw report digest not retained in manifest")
    for identifier in manifest["history"]["previous_record_artifacts"]:
        row = artifacts[identifier]
        if row["visibility"] != "public":
            raise ValueError("append-only predecessors must be portable public records")
        previous = load_json(safe_file(artifacts_root, row["path"]).read_bytes())
        helper["validate"](previous, expected_base=RPNH_BASE, allowed_prefixes=[ERP_PREFIX])
        if previous["record_id"] == manifest["record_id"]:
            raise ValueError("predecessor cannot reuse current record ID")
    return summary


def build_result_manifest(*, record_id: str, task: VerifiedTask, source_root,
                          artifacts_root, shared_validator_path, condition: dict,
                          stages: dict, artifacts=(), model=None, model_calls=None,
                          registry_refs=None, scores=(), previous_record_artifacts=(),
                          findings=(), public_notes=(), mode="upstream_original",
                          adaptations=(), phase="initial", privacy_reviewed=False) -> dict:
    """Create condition/task/tree artifacts and a validated, completed record.

    ``artifacts_root`` is one new condition directory, containing caller-written
    safe stage logs/projections. Generated files are exclusive-create. ``stages``
    must specify all five stages (including blockers); no stage is inferred from
    a template or from another stage's success. Use a new directory for retries.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", record_id):
        raise ValueError("unsafe/empty record ID")
    if set(stages) != set(STAGES):
        raise ValueError("provide all five distinct stages")
    if not isinstance(condition, dict) or not condition:
        raise ValueError("a nonempty frozen condition is required")
    identity = source_identity(source_root)
    if task.source_pin != UPSTREAM_COMMIT:
        raise ValueError("verified task pin mismatch")
    rows = list(artifacts)
    generated_ids = {"condition", "task-identity", "source-identity"}
    if any(row["id"] in generated_ids for row in rows):
        raise ValueError("reserved generated artifact ID")
    frozen_condition = {"record_id": record_id, "task_id": task.task_id,
                        "task_input_sha256": task.input_sha256,
                        "tested_owned_tree_sha256": identity["owned_tree_sha256"],
                        "condition": condition}
    generated = (("task-identity.json", "task-identity", "task_identity", task.public_identity()),
                 ("source-identity.json", "source-identity", "environment", identity),
                 ("condition.json", "condition", "condition", frozen_condition))
    for _, _, _, value in generated:
        assert_public_safe(value)
        json_bytes(value)
    for path, identifier, role, value in generated:
        write_new(artifacts_root, path, json_bytes(value))
        rows.append(artifact_record(artifacts_root, path, artifact_id=identifier, role=role))
    condition_row = next(r for r in rows if r["id"] == "condition")
    registry = list(registry_refs) if registry_refs is not None else [{
        "purpose": "terminal_result", "status": "unavailable", "task_id": None,
        "projection_artifact_id": None, "ref": None,
        "reason": "No actual owner projection supplied for this condition."}]
    manifest = {
        "schema_version": "rpnh/example-evidence/v1", "record_id": record_id, "example_id": "erp_bench",
        "source": {"repository": "https://github.com/Deng-0119/RPNH", "base_commit": RPNH_BASE,
                   "tested_commit": identity["tested_commit"], "owned_prefixes": [ERP_PREFIX],
                   "changed_files": identity["changed_files"]},
        "upstream": [{"role": "task_and_scorer", "repository": UPSTREAM_REPOSITORY,
                      "revision": UPSTREAM_COMMIT, "license": "CC0-1.0"}],
        "task": {"task_id": task.task_id, "phase": phase, "mode": mode,
                 "original_semantics": "Unchanged official seeded Odoo task, instruction, limits and original verifier.",
                 "adaptations": list(adaptations), "instruction_sha256": task.instruction_sha256,
                 "input_sha256": task.input_sha256, "unavailable_reason": None},
        "condition": {"sha256": condition_row["sha256"], "artifact_id": "condition", "unavailable_reason": None},
        "model": model if model is not None else {
            "kind": "none", "provider": None, "exact_model": None,
            "configuration_sha256": None, "configuration_artifact_id": None,
            "unavailable_reason": "No model identity supplied for this condition."},
        "stages": stages,
        "model_calls": model_calls if model_calls is not None else {
            "status": "unknown", "real_provider_calls": None, "fake_provider_calls": None,
            "evidence": [], "reason": "No complete call accounting supplied; unknown is not zero."},
        "scores": list(scores), "artifacts": rows, "registry_refs": registry,
        "history": {"policy": "append_only", "previous_record_artifacts": list(previous_record_artifacts),
                    "note": "Each condition/attempt is retained as a distinct record; previous records are immutable."},
        "findings": list(findings),
        "privacy": {"review_status": "passed" if privacy_reviewed else "pending",
                    "reviewed_public_artifacts": [r["id"] for r in rows if r["visibility"] == "public"] if privacy_reviewed else [],
                    "raw_provider_transcripts_included": False,
                    "note": "Public artifacts are allowlisted projections; raw reports and runtime state remain private."},
        "public_notes": list(public_notes) or [
            "This is a per-condition record. Unrun/blocked stages establish neither task success nor zero model calls.",
            "Source input identity is distinct from fresh seeded-world and terminal snapshot identity."],
    }
    validate_result_manifest(manifest, shared_validator_path=shared_validator_path,
                             artifacts_root=artifacts_root, source_root=source_root)
    return manifest


def write_result_manifest(manifest, destination, *, shared_validator_path, artifacts_root, source_root) -> Path:
    """Validate current bytes and refuse overwriting any existing record."""
    validate_result_manifest(manifest, shared_validator_path=shared_validator_path,
                             artifacts_root=artifacts_root, source_root=source_root)
    destination = Path(destination)
    if destination.name != "result-manifest.json":
        raise ValueError("shared contract requires result-manifest.json")
    return write_new(destination.parent, destination.name, json_bytes(manifest))
