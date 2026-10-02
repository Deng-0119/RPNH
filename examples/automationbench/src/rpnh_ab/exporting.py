"""Allowlisted return bundle; native stores/profiles are retained locally."""
from __future__ import annotations
import json
import os
from pathlib import Path
import zipfile
from .io import load, now
from .scoring import summarize


def export_return(work: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("return ZIP already exists; use a new name")
    plan = load(work / "plan.json")
    included = []
    redactions = []
    # Exact known credential values only: do not guess from phone/ID-like text.
    # Originals remain untouched locally. Unknown credentials still require review.
    secret_values = sorted({value for name, value in os.environ.items()
                            if value and len(value) >= 8 and name.upper().endswith(
                                ("_API_KEY", "_ACCESS_TOKEN", "_SECRET_KEY"))},
                           key=len, reverse=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        def add_file(path, name):
            data = path.read_bytes()
            changed = False
            for value in secret_values:
                raw = value.encode("utf-8")
                if raw in data:
                    data = data.replace(raw, b"[REDACTED_KNOWN_CREDENTIAL]")
                    changed = True
            if changed:
                redactions.append(name)
            archive.writestr(name, data)

        archive.writestr("summary.json", json.dumps(summarize(plan, work), ensure_ascii=False, indent=2))
        for name in ("plan.json", "conditions.json", "tool_schemas.json", "doctor.json", "native_acceptance.json", "acceptance.json"):
            path = work / name
            if path.is_file():
                add_file(path, name)
                included.append(name)
        for task in plan["tasks"]:
            attempt = work / "attempts" / task["id"] / "a0001"
            # No native Registry database, private adapter, env, control specs,
            # provider raw logs or unrestricted directory recursion.
            names = ("attempt.json", "public_task.json", "task_contract.json", "scoring_input.json",
                     "final_world.json", "lifecycle.json", "native_evidence.json", "dsh_evidence.json",
                     "tool_events.jsonl", "transport_events.jsonl", "bridge_registry_check.json", "latest_projection.json")
            for name in names:
                path = attempt / name
                if path.is_file():
                    relative = path.relative_to(work).as_posix()
                    add_file(path, relative)
                    included.append(relative)
            paths = sorted(attempt.glob("score-*.json"))
            paths += sorted((attempt / "projection-revisions").glob("r*/*.json"))
            for path in paths:
                relative = path.relative_to(work).as_posix()
                add_file(path, relative)
                included.append(relative)
        report = {"schema": "rpnh-ab/return/v1", "at": now(), "file_count": len(included)+2,
                  "native_registry_raw_included": False, "private_profiles_included": False,
                  "incomplete_attempts_preserved_in_plan": True,
                  "known_credential_redactions": redactions,
                  "redaction_scope": "exact current environment credential values >=8 characters, copied export only; not a complete secret detector",
                  "note": "Keep the complete work root locally. Missing final states cannot be reconstructed from this ZIP alone."}
        archive.writestr("RETURN_MANIFEST.json", json.dumps(report, ensure_ascii=False, indent=2))
    return report
