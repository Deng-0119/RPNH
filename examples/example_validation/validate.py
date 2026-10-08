#!/usr/bin/env python3
"""Read-only validation of this fixed publication schema; never execute a run."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

SCHEMA_PATH = Path(__file__).with_name("result-manifest.schema.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=pairs, parse_constant=nonfinite)


def shape(value, spec, at="manifest"):
    """Validate only the small draft-07 subset used by the adjacent fixed schema."""
    if "oneOf" in spec:
        matches = 0
        for option in spec["oneOf"]:
            try:
                shape(value, option, at)
                matches += 1
            except ValueError:
                pass
        require(matches == 1, f"{at}: expected exactly one supported reference shape")
        return
    types = {"object": lambda x: type(x) is dict, "array": lambda x: type(x) is list,
             "string": lambda x: type(x) is str, "null": lambda x: x is None,
             "integer": lambda x: type(x) is int,
             "number": lambda x: type(x) in (int, float) and math.isfinite(x),
             "boolean": lambda x: type(x) is bool}
    if "type" in spec:
        allowed = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        require(any(types[t](value) for t in allowed), f"{at}: expected {allowed}")
    if "enum" in spec:
        require(any(type(value) is type(x) and value == x for x in spec["enum"]),
                f"{at}: invalid enum value")
    if isinstance(value, dict):
        require(set(spec.get("required", [])) <= value.keys(), f"{at}: missing fields")
        props = spec.get("properties", {})
        additional = spec.get("additionalProperties", True)
        for key, item in value.items():
            require(key in props or additional is not False, f"{at}: unknown field {key}")
            child = props.get(key, additional if isinstance(additional, dict) else {})
            shape(item, child, f"{at}.{key}")
    elif isinstance(value, list):
        require(len(value) >= spec.get("minItems", 0), f"{at}: too few items")
        for index, item in enumerate(value):
            shape(item, spec.get("items", {}), f"{at}[{index}]")
    elif isinstance(value, str):
        require(len(value) >= spec.get("minLength", 0), f"{at}: empty string")
        if "pattern" in spec:
            require(re.fullmatch(spec["pattern"], value) is not None, f"{at}: invalid format")
    elif type(value) in (int, float) and "minimum" in spec:
        require(value >= spec["minimum"], f"{at}: below minimum")


def safe_relative(value):
    path = PurePosixPath(value)
    require(value and not path.is_absolute() and "\\" not in value and ":" not in value
            and all(part not in ("", ".", "..") for part in value.split("/")),
            f"unsafe relative path: {value}")
    require(not any(ord(char) < 32 for char in value), "control character in path")
    return path


def safe_file(root, name):
    relative = safe_relative(name)
    root = Path(root).resolve(strict=True)
    target = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        require(not cursor.is_symlink(), f"symlink is not permitted: {name}")
    require(target.is_file(), f"missing regular file: {name}")
    require(target.resolve().is_relative_to(root), f"escaping path: {name}")
    return target


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(manifest, *, artifacts_root=None, source_root=None, base_root=None,
             expected_base=None, allowed_prefixes=None):
    shape(manifest, load(SCHEMA_PATH))
    source = manifest["source"]
    if expected_base:
        require(source["base_commit"] == expected_base, "RPNH base commit mismatch")
    prefixes = source["owned_prefixes"]
    if allowed_prefixes is not None:
        require(set(prefixes) <= set(allowed_prefixes), "ownership differs from integrator allowlist")
    for prefix in prefixes:
        require(prefix.endswith("/") and prefix.startswith("examples/"), "ownership must be an example subtree")
        safe_relative(prefix[:-1])
        require(len(PurePosixPath(prefix).parts) >= 2, "ownership cannot cover all examples")
    paths = set()
    for row in source["changed_files"]:
        name = row["path"]
        safe_relative(name)
        require(name not in paths, "duplicate changed file")
        paths.add(name)
        require(any(name.startswith(prefix) for prefix in prefixes), f"file outside ownership: {name}")
        if source_root is not None:
            require(digest(safe_file(source_root, name)) == row["final_sha256"], f"final hash mismatch: {name}")
        if base_root is not None:
            if row["base_sha256"] is None:
                candidate = Path(base_root).resolve(strict=True)
                for part in PurePosixPath(name).parts:
                    candidate = candidate / part
                    require(not candidate.is_symlink(), f"symlink in base addition path: {name}")
                require(not candidate.exists(), f"addition would overwrite base: {name}")
            else:
                require(digest(safe_file(base_root, name)) == row["base_sha256"], f"base hash mismatch: {name}")
    artifacts = {}
    for row in manifest["artifacts"]:
        require(row["id"] not in artifacts, "duplicate artifact id")
        artifacts[row["id"]] = row
        if row["visibility"] == "public":
            require(row["path"] is not None and row["hash_scope"] == "distributed_bytes", "public artifact needs distributed-byte identity")
            safe_relative(row["path"])
            if artifacts_root is not None:
                path = safe_file(artifacts_root, row["path"])
                require(path.stat().st_size == row["bytes"] and digest(path) == row["sha256"],
                        f"artifact byte/hash mismatch: {row['id']}")
        else:
            require(row["path"] is None and row["hash_scope"] == "retained_private_bytes",
                    "private evidence must not disclose a path or claim distributed bytes")
    public = {key for key, row in artifacts.items() if row["visibility"] == "public"}
    require(len({row['path'] for row in artifacts.values() if row['visibility'] == 'public'}) == len(public),
            "duplicate public artifact path")

    def refs(values, *, role=None):
        require(len(values) == len(set(values)), "duplicate evidence reference")
        for identifier in values:
            require(identifier in artifacts, f"unknown artifact: {identifier}")
            if role:
                require(artifacts[identifier]["role"] == role, f"wrong artifact role: {identifier}")

    def identity(row, hash_key, artifact_key, role):
        sha, identifier = row[hash_key], row[artifact_key]
        require((sha is None) == (identifier is None), f"{role}: incomplete identity")
        if sha is None:
            require(bool(row["unavailable_reason"]), f"{role}: missing unavailable reason")
        else:
            refs([identifier], role=role)
            require(identifier in public and artifacts[identifier]["sha256"] == sha,
                    f"{role}: must match a public artifact")

    identity(manifest["condition"], "sha256", "artifact_id", "condition")
    model = manifest["model"]
    identity(model, "configuration_sha256", "configuration_artifact_id", "model_configuration")
    if model["kind"] == "none":
        require(all(model[key] is None for key in ("provider", "exact_model", "configuration_sha256")),
                "unselected model cannot have selected identity")
    else:
        require(all(model[key] is not None for key in ("provider", "exact_model", "configuration_sha256")),
                "selected model requires exact provider, model and public configuration identity")
    task = manifest["task"]
    if task["instruction_sha256"] is None or task["input_sha256"] is None:
        require(bool(task["unavailable_reason"]), "missing task hash requires explanation")
    if task["mode"] == "synthetic_contract":
        require(bool(task["adaptations"]), "synthetic task must disclose adaptations")
    for name, stage in manifest["stages"].items():
        refs(stage["evidence"])
        if stage["status"] in ("passed", "failed"):
            require(bool(stage["commands"]) and bool(stage["evidence"]), f"{name}: measured stage requires command and evidence")
        else:
            require(bool(stage["reason"]), f"{name}: unavailable stage requires explanation")
    calls = manifest["model_calls"]
    refs(calls["evidence"])
    if calls["status"] == "verified":
        require(calls["real_provider_calls"] is not None and calls["fake_provider_calls"] is not None
                and bool(calls["evidence"]), "verified counts, including zero, require evidence")
    else:
        require(calls["real_provider_calls"] is None and calls["fake_provider_calls"] is None
                and bool(calls["reason"]), "unknown usage must remain null, not zero")
    for score in manifest["scores"]:
        refs(score["evidence"])
        require(bool(score["components"]), "score must preserve components")
        if score["claim"] in ("benchmark_result", "grader_compatibility"):
            require(score["scorer_revision"] in {row["revision"] for row in manifest["upstream"]},
                    "original scorer revision must be one of the pinned upstreams")
        require(manifest["stages"]["evaluation"]["status"] == "passed", "score requires completed evaluation")
        if score["claim"] == "benchmark_result":
            require(model["kind"] == "real" and task["mode"] == "upstream_original",
                    "fake/synthetic/adapted runs cannot claim original benchmark results")
            require(manifest["stages"]["provider"]["status"] in ("passed", "failed"), "benchmark result needs actual provider execution")
            require(manifest["stages"]["native"]["status"] == "passed"
                    and any(row["status"] == "available" for row in manifest["registry_refs"]),
                    "benchmark result needs native acceptance and actual Registry evidence")
            require(manifest["condition"]["sha256"] and task["instruction_sha256"] and task["input_sha256"],
                    "benchmark result requires frozen task and condition identity")
    for row in manifest["registry_refs"]:
        keys = ("task_id", "ref", "projection_artifact_id")
        if row["status"] == "unavailable":
            require(all(row[key] is None for key in keys) and row["reason"], "unavailable Registry refs must not be invented")
        else:
            require(all(row[key] is not None for key in keys), "actual Registry ref is incomplete")
            refs([row["projection_artifact_id"]], role="registry_projection")
            require(row["projection_artifact_id"] in public, "Registry reference needs a sanitized public projection")
            if artifacts_root is not None:
                projection = load(safe_file(artifacts_root, artifacts[row["projection_artifact_id"]]["path"]))
                require(isinstance(projection, dict), "Registry projection must be an object")
                task_id = projection.get("task_id", projection.get("task_ref"))
                if task_id is None and isinstance(projection.get("source"), dict):
                    task_id = projection["source"].get("task_id")
                require(task_id == row["task_id"], "Registry projection task mismatch")
                def contains_ref(value):
                    if isinstance(value, dict):
                        return (value == row["ref"]) or any(contains_ref(x) for x in value.values())
                    return isinstance(value, list) and any(contains_ref(x) for x in value)
                require(contains_ref(projection), "Registry projection does not contain exact reference")
    refs(manifest["history"]["previous_record_artifacts"], role="history")
    for finding in manifest["findings"]:
        refs(finding["evidence"])
        require(finding["status"] != "confirmed"
                or finding["category"] not in ("model_reasoning", "harness_implementation")
                or finding["configuration_excluded"],
                "exclude configuration/bindings before confirmed model or harness attribution")
    privacy = manifest["privacy"]
    refs(privacy["reviewed_public_artifacts"])
    require(set(privacy["reviewed_public_artifacts"]) <= public, "privacy review cannot list private artifacts")
    if privacy["review_status"] == "passed":
        require(set(privacy["reviewed_public_artifacts"]) == public, "privacy review must cover every public artifact")
    return {
        "schema_version": "rpnh/example-merge-summary/v1", "record_id": manifest["record_id"],
        "example_id": manifest["example_id"], "contract_valid": True,
        "verification": {"base_commit_matched": expected_base is not None,
                         "ownership_matched": allowed_prefixes is not None,
                         "base_file_hashes_checked": base_root is not None,
                         "final_file_hashes_checked": source_root is not None,
                         "public_artifact_hashes_checked": artifacts_root is not None,
                         "private_artifacts_verified": False},
        "source": source, "upstream": manifest["upstream"], "task": task,
        "condition": manifest["condition"], "artifacts": manifest["artifacts"],
        "model": model, "stages": manifest["stages"], "scores": manifest["scores"],
        "model_calls": calls, "registry_refs": manifest["registry_refs"],
        "history": manifest["history"], "findings": manifest["findings"],
        "publication_review_passed": privacy["review_status"] == "passed",
        "merge_review_ready": (all(value is not None for value in
                               (expected_base, allowed_prefixes, base_root, source_root, artifacts_root))
                               and bool(paths) and privacy["review_status"] == "passed"
                               and manifest["stages"]["offline"]["status"] == "passed"),
        "public_notes": manifest["public_notes"],
        "limits": "A read-only structural/hash check is not merge approval, proof of execution, scorer correctness, secret scanning, or acceptance on a later merged revision.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--artifacts-root", type=Path, help="Verify every declared public artifact")
    parser.add_argument("--source-root", type=Path, help="Verify changed final file bytes")
    parser.add_argument("--base-root", type=Path, help="Verify base bytes and safe additions")
    parser.add_argument("--expected-base", help="Require the lane's exact agreed RPNH base SHA")
    parser.add_argument("--allow-prefix", action="append", help="Integrator-owned path allowlist (repeatable)")
    args = parser.parse_args(argv)
    try:
        manifest = load(args.manifest)
        summary = validate(manifest, artifacts_root=args.artifacts_root, source_root=args.source_root,
                           base_root=args.base_root, expected_base=args.expected_base,
                           allowed_prefixes=args.allow_prefix)
        summary["manifest_sha256"] = digest(args.manifest)
        print(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"contract_valid": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
