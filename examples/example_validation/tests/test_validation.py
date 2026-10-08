"""Pure local fixtures; no RPNH owner, service, model or original grader runs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("example_evidence_validator", HERE / "validate.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = validator.load(HERE / "template.json")

    def add_artifact(self, identifier="test-log", *, role="test_log", content=b"one pure fixture passed\n"):
        path = self.root / (identifier + ".json")
        path.write_bytes(content)
        row = {"id": identifier, "role": role, "visibility": "public", "path": path.name,
               "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
               "hash_scope": "distributed_bytes", "immutable": True}
        self.manifest["artifacts"].append(row)
        return row

    def assert_bad(self, phrase, **kwargs):
        with self.assertRaisesRegex(ValueError, phrase):
            validator.validate(self.manifest, **kwargs)

    def test_template_has_no_runtime_or_zero_call_claim(self):
        summary = validator.validate(self.manifest)
        self.assertTrue(summary["contract_valid"])
        self.assertFalse(summary["publication_review_passed"])
        self.assertIsNone(summary["model_calls"]["real_provider_calls"])
        self.assertTrue(all(s["status"] == "not_run" for s in summary["stages"].values()))
        self.assertFalse(any(summary["verification"].values()))

    def test_unknown_fields_and_bool_counts_rejected(self):
        self.manifest["api_key"] = "never-allow-this-field"
        self.assert_bad("unknown field")
        del self.manifest["api_key"]
        self.manifest["model_calls"]["real_provider_calls"] = False
        self.assert_bad("expected")

    def test_repeated_keys_and_nan_rejected(self):
        file = self.root / "bad.json"
        for content in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            file.write_text(content)
            with self.assertRaises(ValueError):
                validator.load(file)

    def test_public_bytes_verified_and_tamper_rejected(self):
        artifact = self.add_artifact()
        validator.validate(self.manifest, artifacts_root=self.root)
        (self.root / artifact["path"]).write_bytes(b"changed")
        self.assert_bad("byte/hash mismatch", artifacts_root=self.root)

    def test_paths_and_symlinks_rejected(self):
        artifact = self.add_artifact()
        for path in ("../secret", "/home/user/secret", "a/./b", "a//b", "C:\\secret"):
            artifact["path"] = path
            self.assert_bad("unsafe relative path")
        artifact["path"] = "link.json"
        (self.root / "link.json").symlink_to(self.root / "test-log.json")
        self.assert_bad("symlink", artifacts_root=self.root)

    def test_private_evidence_has_no_exported_path(self):
        artifact = self.add_artifact()
        artifact.update(visibility="private", hash_scope="retained_private_bytes")
        self.assert_bad("private evidence")
        artifact["path"] = None
        summary = validator.validate(self.manifest, artifacts_root=self.root)
        self.assertFalse(summary["verification"]["private_artifacts_verified"])

    def test_source_prefix_hash_and_safe_addition(self):
        source = self.root / "final"
        base = self.root / "base"
        base.mkdir()
        relative = "examples/erp_bench/adapter.py"
        (source / relative).parent.mkdir(parents=True)
        (source / relative).write_bytes(b"pass\n")
        row = {"path": relative, "base_sha256": None,
               "final_sha256": hashlib.sha256(b"pass\n").hexdigest()}
        self.manifest["source"]["changed_files"] = [row]
        summary = validator.validate(self.manifest, source_root=source, base_root=base,
                                     expected_base=self.manifest["source"]["base_commit"])
        self.assertTrue(summary["verification"]["final_file_hashes_checked"])
        (base / relative).parent.mkdir(parents=True)
        (base / relative).write_bytes(b"existing\n")
        self.assert_bad("overwrite base", source_root=source, base_root=base)
        row["path"] = "cpn/core.py"
        self.assert_bad("outside ownership")

    def test_base_mismatch_and_modified_file_mismatch(self):
        self.assert_bad("commit mismatch", expected_base="b" * 40)
        relative = "examples/erp_bench/existing.py"
        file = self.root / relative
        file.parent.mkdir(parents=True)
        file.write_bytes(b"old\n")
        self.manifest["source"]["changed_files"] = [{"path": relative, "base_sha256": "a"*64, "final_sha256": "b"*64}]
        self.assert_bad("base hash mismatch", base_root=self.root)
        self.assert_bad("final hash mismatch", source_root=self.root)

    def test_zero_requires_evidence_and_unknown_remains_null(self):
        calls = self.manifest["model_calls"]
        calls["real_provider_calls"] = 0
        self.assert_bad("unknown usage")
        calls.update(status="verified", fake_provider_calls=0)
        self.assert_bad("including zero")
        self.add_artifact()
        calls["evidence"] = ["test-log"]
        validator.validate(self.manifest, artifacts_root=self.root)

    def test_measured_stage_requires_evidence(self):
        self.manifest["stages"]["offline"]["status"] = "passed"
        self.assert_bad("requires command and evidence")
        self.add_artifact()
        self.manifest["stages"]["offline"].update(commands=["python -m unittest"], evidence=["test-log"])
        validator.validate(self.manifest, artifacts_root=self.root)

    def test_fake_provider_is_never_official_score(self):
        config = self.add_artifact("config", role="model_configuration", content=b'{"provider":"fixture","exact_model":"fake"}\n')
        self.manifest["model"].update(kind="fake", provider="fixture", exact_model="fake",
                                      configuration_sha256=config["sha256"], configuration_artifact_id=config["id"])
        self.add_artifact("score", role="grader_output")
        self.manifest["stages"]["evaluation"].update(status="passed", commands=["fixture-evaluator"], evidence=["score"])
        self.manifest["scores"] = [{"claim":"benchmark_result", "scorer_id":"original-fixture",
                                   "scorer_revision":self.manifest["upstream"][0]["revision"], "components":{"reward":1.0}, "evidence":["score"]}]
        self.assert_bad("fake/synthetic/adapted")
        self.manifest["scores"][0]["claim"] = "grader_compatibility"
        validator.validate(self.manifest, artifacts_root=self.root)

    def test_registry_reference_must_exist_in_projection(self):
        self.add_artifact("registry", role="registry_projection", content=json.dumps({"task_id":"actual-task", "terminal_result_ref":{"entity_type":"run_terminal_result/v1", "logical_id":"entity", "version_id":"version"}}).encode())
        row = self.manifest["registry_refs"][0]
        row.update(status="available", task_id="actual-task", ref={"entity_type":"run_terminal_result/v1", "logical_id":"entity", "version_id":"version"}, projection_artifact_id="registry")
        validator.validate(self.manifest, artifacts_root=self.root)
        row["ref"]["version_id"] = "invented"
        self.assert_bad("exact reference", artifacts_root=self.root)

    def test_unavailable_registry_cannot_have_fake_refs(self):
        self.manifest["registry_refs"][0]["ref"] = {"resource_id":"fake", "resource_version_id":"fake"}
        self.assert_bad("must not be invented")

    def test_history_reference_must_be_retained_artifact(self):
        self.manifest["history"]["previous_record_artifacts"] = ["previous"]
        self.assert_bad("unknown artifact")
        self.add_artifact("previous", role="history", content=b"original record unchanged")
        validator.validate(self.manifest, artifacts_root=self.root)

    def test_configuration_must_be_excluded_before_model_blame(self):
        self.add_artifact()
        self.manifest["findings"] = [{"id":"f1", "status":"confirmed", "category":"model_reasoning", "configuration_excluded":False,
                                      "expected":"correct task", "observed":"failed task", "evidence":["test-log"],
                                      "reproduction":"synthetic fixture", "impact":"unknown", "proposed_owner":"unassigned",
                                      "next_step":"verify bindings first"}]
        self.assert_bad("exclude configuration")
        self.manifest["findings"][0]["configuration_excluded"] = True
        validator.validate(self.manifest)

    def test_privacy_review_covers_public_allowlist(self):
        self.add_artifact()
        self.manifest["privacy"]["review_status"] = "passed"
        self.assert_bad("cover every public artifact")
        self.manifest["privacy"]["reviewed_public_artifacts"] = ["test-log"]
        validator.validate(self.manifest, artifacts_root=self.root)

    def test_result_evidence_source_task_and_resource_ref(self):
        ref = {"resource_id":"res:fixture", "resource_version_id":"resver:fixture"}
        self.add_artifact("registry", role="registry_projection", content=json.dumps({
            "source":{"task_id":"task:fixture"}, "requests":[{"request_resource_ref":ref}]}).encode())
        self.manifest["registry_refs"][0].update(status="available", task_id="task:fixture",
                                                ref=ref, projection_artifact_id="registry")
        validator.validate(self.manifest, artifacts_root=self.root)

    def test_alternate_invented_registry_shape_is_rejected(self):
        self.manifest["registry_refs"][0]["ref"] = {"entity_id":"wrong", "version_id":"wrong"}
        self.assert_bad("supported reference shape")

    def test_integrator_allowlist_is_independent(self):
        self.assert_bad("integrator allowlist", allowed_prefixes=["examples/slopcodebench/"])
        summary = validator.validate(self.manifest, allowed_prefixes=["examples/erp_bench/"])
        self.assertTrue(summary["verification"]["ownership_matched"])

    def test_harness_attribution_also_requires_configuration_exclusion(self):
        self.add_artifact()
        self.manifest["findings"] = [{"id":"h1", "status":"confirmed", "category":"harness_implementation", "configuration_excluded":False,
                                      "expected":"supported behavior", "observed":"failure", "evidence":["test-log"],
                                      "reproduction":"deterministic reproducer", "impact":"blocked task", "proposed_owner":"harness",
                                      "next_step":"verify supported environment and bindings first"}]
        self.assert_bad("exclude configuration")

    def test_added_file_rejects_symlinked_base_parent(self):
        (self.root / "outside").mkdir()
        (self.root / "examples").mkdir()
        (self.root / "examples" / "erp_bench").symlink_to(self.root / "outside", target_is_directory=True)
        self.manifest["source"]["changed_files"] = [{"path":"examples/erp_bench/new.py",
                                                     "base_sha256":None, "final_sha256":"a"*64}]
        self.assert_bad("symlink in base addition", base_root=self.root)

    def test_unconfirmed_source_hypothesis_does_not_require_false_certainty(self):
        self.add_artifact("source-review", role="finding")
        self.manifest["findings"] = [{"id":"h2", "status":"hypothesis", "category":"harness_implementation",
                                      "configuration_excluded":False, "expected":"workspace binding may be needed",
                                      "observed":"source inspection raises an unresolved question", "evidence":["source-review"],
                                      "reproduction":"inspect pinned supported task interface", "impact":"not measured",
                                      "proposed_owner":"integration review", "next_step":"verify bindings before runtime attribution"}]
        validator.validate(self.manifest, artifacts_root=self.root)
        self.manifest["findings"][0]["status"] = "confirmed"
        self.assert_bad("exclude configuration")


if __name__ == "__main__":
    unittest.main()
