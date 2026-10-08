"""Portable frozen-contract evidence and source export, using synthetic runs."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rpnh_erp_bench import evidence, export
from rpnh_erp_bench.source import (ERP_PREFIX, RPNH_BASE, TASK_IDS, VerifiedTask,
                                   json_bytes, load_json, sha256)
from rpnh_erp_bench.scoring import parse_original_score
from test_scoring import reports


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        validator = os.environ.get("ERP_SHARED_VALIDATOR")
        if not validator:
            self.skipTest("explicit frozen ERP_SHARED_VALIDATOR not supplied")
        self.validator = Path(validator)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.src = self.root/"source"
        self.name = ERP_PREFIX + "src/rpnh_erp_bench/demo.py"
        self.file = self.src/self.name
        self.file.parent.mkdir(parents=True)
        self.file.write_text("print('synthetic')\n")
        self.art = self.root/"condition"
        self.art.mkdir()
        self.names = [self.name]
        self.head = RPNH_BASE
        self.ancestor = True
        self.committed_paths = [self.name]
        self.dirty_paths = []
        self.base_files = {}
        self.git_calls = []
        self.git_patch = patch.object(export, "git", side_effect=self.git)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        self.task = VerifiedTask(TASK_IDS[0], self.root/"upstream-task", b"synthetic instruction\n",
                                 {"metadata": {"seed": 17}, "agent": {"timeout_sec": 3600},
                                  "verifier": {"timeout_sec": 300}, "environment": {"build_timeout_sec": 600}}, {})
        self.stages = {name: evidence.stage_record("blocked", reason="Synthetic environment prerequisite unavailable.")
                       for name in evidence.STAGES}

    def git(self, root, *args):
        self.git_calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return self.head.encode()
        if args == ("rev-parse", "--show-toplevel"):
            return str(self.src).encode()
        if args == ("rev-parse", "HEAD^{tree}"):
            return b"a" * 40
        if args[0] == "ls-files":
            return "\0".join(self.names).encode()
        if args == ("merge-base", "--is-ancestor", RPNH_BASE, self.head):
            if not self.ancestor:
                raise ValueError("git merge-base failed (exit 1)")
            return b""
        if args[0] == "diff":
            self.assertIn("--no-renames", args)
            paths = self.dirty_paths if "HEAD" in args else self.committed_paths
            return "\0".join(paths).encode()
        if args[0] == "ls-tree":
            present = args[-1] in self.base_files if args[1] == RPNH_BASE else args[-1] in self.names
            return b"100644 blob synthetic" if present else b""
        if args[0] == "show":
            return self.base_files[args[1].split(":", 1)[1]]
        raise AssertionError(args)

    def build(self, **kw):
        args = dict(record_id="synthetic-blocked-01", task=self.task,
                    source_root=self.src, artifacts_root=self.art,
                    shared_validator_path=self.validator,
                    condition={"execution": "synthetic fixture only"}, stages=self.stages,
                    mode="synthetic_contract", adaptations=["Synthetic deterministic fixture; no real model/business call."])
        args.update(kw)
        return evidence.build_result_manifest(**args)

    def validate(self, manifest):
        return evidence.validate_result_manifest(manifest, shared_validator_path=self.validator,
                                                   artifacts_root=self.art, source_root=self.src)

    def write_artifact(self, name, payload, identifier, role="test_log"):
        (self.art/name).write_bytes(json_bytes(payload))
        return evidence.artifact_record(self.art, name, artifact_id=identifier, role=role)

    def test_blocked_record_is_complete_and_portable(self):
        manifest = self.build()
        self.assertTrue(self.validate(manifest)["contract_valid"])
        self.assertEqual(manifest["scores"], [])
        self.assertIsNone(manifest["model_calls"]["real_provider_calls"])
        self.assertIsNone(manifest["model_calls"]["fake_provider_calls"])
        self.assertEqual(set(manifest["stages"]), set(evidence.STAGES))
        self.assertNotIn(str(self.root), json_bytes(manifest).decode())
        self.assertEqual(manifest["source"]["changed_files"][0]["final_sha256"], sha256(self.file.read_bytes()))
        path = evidence.write_result_manifest(manifest, self.art/"result-manifest.json",
                    shared_validator_path=self.validator, artifacts_root=self.art, source_root=self.src)
        self.assertEqual(load_json(path.read_bytes()), manifest)
        with self.assertRaises(FileExistsError):
            evidence.write_result_manifest(manifest, path, shared_validator_path=self.validator,
                                             artifacts_root=self.art, source_root=self.src)

    def test_artifact_bytes_missing_duplicates_and_escape(self):
        manifest = self.build()
        for mutation in ("hash", "missing", "duplicate_id", "duplicate_path", "escape"):
            bad = copy.deepcopy(manifest)
            if mutation == "hash":
                bad["artifacts"][0]["sha256"] = "0" * 64
            elif mutation == "missing":
                bad["artifacts"][0]["path"] = "missing.json"
            elif mutation == "duplicate_id":
                bad["artifacts"].append(copy.deepcopy(bad["artifacts"][0]))
            elif mutation == "duplicate_path":
                new = copy.deepcopy(bad["artifacts"][0]); new["id"] = "other"
                bad["artifacts"].append(new)
            else:
                bad["artifacts"][0]["path"] = "../elsewhere.json"
            with self.subTest(case=mutation), self.assertRaises(ValueError):
                self.validate(bad)

    def test_symlink_artifact_rejected(self):
        target = self.art/"link.json"
        target.symlink_to(self.file)
        with self.assertRaises(ValueError):
            evidence.artifact_record(self.art, "link.json", artifact_id="link", role="test_log")

    def test_unknown_cannot_be_zero(self):
        manifest = self.build()
        manifest["model_calls"]["real_provider_calls"] = 0
        with self.assertRaisesRegex(ValueError, "unknown usage"):
            self.validate(manifest)

    def test_measured_fake_and_zero_have_evidence(self):
        row = self.write_artifact("accounting.json", {"real_provider_calls": 0, "fake_provider_calls": 3}, "accounting")
        calls = {"status": "verified", "real_provider_calls": 0, "fake_provider_calls": 3,
                 "evidence": ["accounting"], "reason": None}
        manifest = self.build(artifacts=[row], model_calls=calls)
        self.assertEqual(manifest["model_calls"]["fake_provider_calls"], 3)
        manifest["model_calls"]["evidence"] = []
        with self.assertRaises(ValueError):
            self.validate(manifest)

    def test_native_pass_cannot_be_invented(self):
        row = self.write_artifact("fixture.json", {"synthetic": True}, "fixture")
        self.stages["native"] = evidence.stage_record("passed", commands=["synthetic fixture"], evidence=["fixture"])
        with self.assertRaisesRegex(ValueError, "actual owner projection"):
            self.build(artifacts=[row])

    def test_actual_projection_ref_must_match(self):
        ref = {"entity_type": "test_entity", "logical_id": "fixture-object", "version_id": "fixture-version"}
        row = self.write_artifact("owner-projection.json", {"task_id": "fixture-owner", "result_ref": ref},
                                  "projection", "registry_projection")
        registry = [{"purpose": "fixture_ref_check", "status": "available", "task_id": "fixture-owner",
                     "projection_artifact_id": "projection", "reason": None, "ref": ref}]
        manifest = self.build(artifacts=[row], registry_refs=registry)
        manifest["registry_refs"][0]["ref"] = {**ref, "version_id": "invented"}
        with self.assertRaisesRegex(ValueError, "exact reference"):
            self.validate(manifest)

    def test_source_snapshot_must_not_be_stale(self):
        manifest = self.build()
        added = ERP_PREFIX + "new.py"
        (self.src/added).write_text("# new source\n")
        self.names.append(added)
        with self.assertRaisesRegex(ValueError, "stale tested"):
            self.validate(manifest)

    def test_erp_descendant_manifest_records_actual_head_and_base_additions(self):
        self.head = "b" * 40
        manifest = self.build()
        self.assertTrue(self.validate(manifest)["contract_valid"])
        self.assertEqual(manifest["source"]["base_commit"], RPNH_BASE)
        self.assertEqual(manifest["source"]["tested_commit"], self.head)
        self.assertEqual(manifest["source"]["changed_files"], [{
            "path": self.name, "base_sha256": None, "final_sha256": sha256(self.file.read_bytes())}])
        identity = load_json((self.art/"source-identity.json").read_bytes())
        self.assertTrue(identity["tracked_clean"])
        self.assertEqual(identity["tested_commit"], self.head)
        self.assertEqual(identity["owned_files"][0]["sha256"], sha256(self.file.read_bytes()))
        self.assertIn(("merge-base", "--is-ancestor", RPNH_BASE, self.head), self.git_calls)

    def test_non_descendant_source_is_rejected(self):
        self.head = "c" * 40
        self.ancestor = False
        with self.assertRaisesRegex(ValueError, "must descend"):
            export.source_identity(self.src)

    def test_descendant_with_outside_lane_commits_is_rejected(self):
        self.head = "b" * 40
        for outside in ("cpn/core.py", "README.md", "examples/slopcodebench/demo.py",
                        "examples/erp_bench_other/demo.py"):
            self.committed_paths = [self.name, outside]
            with self.subTest(path=outside), self.assertRaisesRegex(ValueError, "committed changes outside"):
                export.source_identity(self.src)

    def test_descendant_keeps_worktree_ownership_check(self):
        self.head = "b" * 40
        self.dirty_paths = ["cpn/core.py"]
        with self.assertRaisesRegex(ValueError, "tracked changes outside"):
            export.source_identity(self.src)

    def test_descendant_patch_still_targets_original_base(self):
        # Include one original-base file and one addition committed only at HEAD.
        existing = ERP_PREFIX + "existing.txt"
        self.names.append(existing)
        self.base_files[existing] = b"original baseline\n"
        (self.src/existing).write_bytes(b"integrated change\n")
        before = self.root/"base-export"
        export.export_source_overlay(self.src, before, paths=self.names)
        self.head = "b" * 40
        self.committed_paths = list(self.names)
        after = self.root/"integrated-export"
        result = export.export_source_overlay(self.src, after, paths=self.names)
        self.assertEqual((after/"implementation.patch").read_bytes(), (before/"implementation.patch").read_bytes())
        self.assertEqual((after/"changed-files.json").read_bytes(), (before/"changed-files.json").read_bytes())
        changes = {row["path"]: row for row in result["changed_files"]}
        self.assertIsNone(changes[self.name]["base_sha256"])
        self.assertEqual(changes[self.name]["change_kind"], "added")
        self.assertEqual(changes[existing]["base_sha256"], sha256(self.base_files[existing]))
        self.assertEqual(changes[existing]["change_kind"], "modified")
        work = self.root/"apply-integrated"; work.mkdir()
        baseline_file = work/existing
        baseline_file.parent.mkdir(parents=True)
        baseline_file.write_bytes(self.base_files[existing])
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(self.root)}
        for extra in (["--check"], []):
            process = subprocess.run(["git", "apply", *extra, str(after/"implementation.patch")],
                                     cwd=work, env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
        for name in self.names:
            self.assertEqual((work/name).read_bytes(), (self.src/name).read_bytes())

    def test_predecessor_is_retained_and_cannot_self_reference(self):
        previous = self.build(record_id="previous-condition")
        new_art = self.root/"next-condition"
        new_art.mkdir()
        self.art = new_art
        row = self.write_artifact("previous-result.json", previous, "previous", "history")
        manifest = self.build(artifacts=[row], previous_record_artifacts=["previous"])
        self.assertEqual(manifest["history"]["previous_record_artifacts"], ["previous"])
        manifest["record_id"] = "previous-condition"
        with self.assertRaisesRegex(ValueError, "predecessor"):
            self.validate(manifest)

    def test_original_projection_digest_and_score_binding(self):
        raw = self.root/"raw-reports"
        raw.mkdir()
        reports(raw)
        score = parse_original_score(raw, TASK_IDS[0], verifier_exit_code=0)
        rows = evidence.record_original_score(score, self.art)
        self.stages["evaluation"] = evidence.stage_record("passed", commands=["synthetic report reader"], evidence=["original-score"])
        manifest = self.build(artifacts=rows, scores=[score.score_record(claim="grader_compatibility", evidence=["original-score"])])
        self.assertEqual(manifest["scores"][0]["components"]["overall_score"], 12.5)
        manifest["scores"][0]["components"]["overall_score"] = 99
        with self.assertRaisesRegex(ValueError, "projection identity"):
            self.validate(manifest)

    def test_stale_original_bytes_rejected_before_projection(self):
        raw = self.root/"reports"; raw.mkdir(); reports(raw)
        score = parse_original_score(raw, TASK_IDS[0])
        (raw/"reward.txt").write_text("99.00\n")
        with self.assertRaisesRegex(ValueError, "changed after parsing"):
            evidence.record_original_score(score, self.art)

    def test_unrun_has_no_score_and_fixture_not_benchmark(self):
        raw = self.root/"reports"; raw.mkdir(); reports(raw)
        score = parse_original_score(raw, TASK_IDS[0])
        rows = evidence.record_original_score(score, self.art)
        manifest = self.build(artifacts=rows)
        manifest["scores"] = [score.score_record(claim="benchmark_result", evidence=["original-score"])]
        with self.assertRaisesRegex(ValueError, "completed evaluation"):
            self.validate(manifest)
        manifest["stages"]["evaluation"] = evidence.stage_record("passed", commands=["fixture"], evidence=["original-score"])
        with self.assertRaisesRegex(ValueError, "fake/synthetic/adapted"):
            self.validate(manifest)

    def test_public_export_filters(self):
        for name in ("private/raw.json", "Registry/state.db", "credentials/data.json", "../source.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                export.publication_path(name)
        for data in ({"password": "sensitive"}, {"path": "/home/user/private"}, {"endpoint": "Bearer secret"}):
            with self.assertRaises(ValueError):
                evidence.assert_public_safe(data)

    def test_stage_observation_records_exit_and_time(self):
        kwargs = dict(commands=["python -m unittest"], started_at="2026-10-08T00:00:00+00:00",
                      ended_at="2026-10-08T00:00:01+00:00", tested_tree_sha256="a"*64)
        self.assertEqual(evidence.stage_observation(status="passed", exit_code=0, **kwargs)["exit_code"], 0)
        with self.assertRaises(ValueError):
            evidence.stage_observation(status="passed", exit_code=7, **kwargs)

    def test_allowlisted_overlay_patch_applies_to_exact_bytes(self):
        # Exercise no-final-newline and empty new files too.
        self.file.write_bytes(b"print('synthetic')")
        empty = ERP_PREFIX + "empty.txt"
        (self.src/empty).write_bytes(b""); self.names.append(empty)
        dest = self.root/"export"
        result = export.export_source_overlay(self.src, dest, paths=self.names)
        self.assertEqual(len(result["changed_files"]), 2)
        work = self.root/"apply"; work.mkdir()
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(self.root)}
        for extra in (["--check"], []):
            process = subprocess.run(["git", "apply", *extra, str(dest/"implementation.patch")],
                                     cwd=work, env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual((work/self.name).read_bytes(), self.file.read_bytes())
        self.assertEqual((work/empty).read_bytes(), b"")
        with self.assertRaises(FileExistsError):
            export.export_source_overlay(self.src, dest, paths=self.names)

    def test_out_of_lane_patch_rejected(self):
        with self.assertRaises(ValueError):
            export.validate_patch_paths("diff --git a/cpn/core.py b/cpn/core.py\n")
        with self.assertRaises(ValueError):
            export.export_source_overlay(self.src, self.root/"bad", paths=["cpn/core.py"])


if __name__ == "__main__":
    unittest.main()
