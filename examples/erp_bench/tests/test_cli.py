"""Offline CLI dispatch and safety tests; no Docker, model or installation."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from rpnh_erp_bench import cli
from rpnh_erp_bench.source import TASK_IDS


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_args = ["run", "--upstream", "upstream/erp-bench", "--task", TASK_IDS[0],
                         "--execution-selection", "private/selection.json",
                         "--run-root", "private/condition-01", "--condition-id", "condition-01",
                         "--source-root", "source/RPNH", "--shared-validator", "contracts/validate.py"]

    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = cli.main(args)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def driver(self, result=None, exception=None):
        module = types.ModuleType("rpnh_erp_bench.driver")
        module.run_trial = AsyncMock(return_value=result, side_effect=exception)
        return module

    def write_json(self, filename, payload):
        target = self.root/filename
        target.write_text(json.dumps(payload))
        return str(target)

    def test_help_lists_all_commands_without_driver(self):
        with patch.dict(sys.modules, {"rpnh_erp_bench.driver": None}):
            code, output, error = self.invoke(["--help"])
        self.assertEqual(code, 0)
        self.assertFalse(error)
        for name in ("inspect-task", "check-plan", "validate-report", "export-source", "run"):
            self.assertIn(name, output)

    def test_inspect_task_prints_only_public_identity(self):
        task = Mock()
        task.public_identity.return_value = {"task_id": TASK_IDS[0], "seed": 17}
        with patch.object(cli, "validate_task", return_value=task) as validate:
            code, output, error = self.invoke(["inspect-task", "--upstream", "upstream/erp-bench", "--task", TASK_IDS[0]])
        validate.assert_called_once_with(Path("upstream/erp-bench"), TASK_IDS[0])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), task.public_identity.return_value)
        self.assertFalse(error)

    def test_unsupported_task_and_abbreviated_flags_rejected(self):
        with patch.object(cli, "validate_task") as validate:
            for args in (["inspect-task", "--upstream", "upstream", "--task", "../task"],
                         ["inspect-task", "--up", "upstream", "--task", TASK_IDS[0]]):
                self.assertEqual(self.invoke(args)[0], 2)
        validate.assert_not_called()

    def test_check_plan_actual_arithmetic_and_domain_failure(self):
        document = {
            "orders": [{"order_ref": "synthetic-order", "quantity": 2, "list_price": 10,
                        "budget": 20, "due_days": 3}],
            "routes": [{"route_ref": "synthetic-offer", "kind": "buy", "capacity": 2,
                        "minimum_quantity": 1, "unit_cost": 6, "lead_days": 2}],
            "allocations": [{"order_ref": "synthetic-order", "route_ref": "synthetic-offer", "quantity": 2}],
            "minimum_margin": 0.4}
        path = self.write_json("plan.json", document)
        code, output, error = self.invoke(["check-plan", "--input", path])
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["new_spend"], "12")
        document["routes"][0]["capacity"] = 1
        self.write_json("plan.json", document)
        code, output, error = self.invoke(["check-plan", "--input", path])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "domain_infeasible")
        self.assertFalse(error)

    def test_malformed_input_fails_without_echoing_private_bytes(self):
        target = self.root/"plan.json"
        for content in ('{"x": NaN}', '{"x":1,"x":2}', 'private secret invalid JSON'):
            target.write_text(content)
            code, output, error = self.invoke(["check-plan", "--input", str(target)])
            self.assertEqual(code, 1)
            self.assertFalse(output)
            self.assertNotIn("private secret", error)
            self.assertNotIn(str(target), error)

    def test_validate_report_forwards_explicit_paths(self):
        path = self.write_json("manifest.json", {"record_id": "synthetic"})
        for extra, source_root in (([], None), (["--source-root", "source/RPNH"], Path("source/RPNH"))):
            with patch.object(cli, "validate_result_manifest", return_value={"contract_valid": True}) as validate:
                code, output, error = self.invoke(["validate-report", "--manifest", path,
                    "--shared-validator", "contracts/validate.py", "--artifacts-root", "reports", *extra])
            self.assertEqual(code, 0, error)
            self.assertTrue(json.loads(output)["contract_valid"])
            validate.assert_called_once_with({"record_id": "synthetic"},
                shared_validator_path=Path("contracts/validate.py"), artifacts_root=Path("reports"), source_root=source_root)

    def test_export_uses_complete_current_owned_allowlist(self):
        names = ["examples/erp_bench/README.md", "examples/erp_bench/src/rpnh_erp_bench/cli.py"]
        with patch.object(cli, "source_identity", return_value={"owned_files": [{"path": n} for n in names]}) as identity, \
             patch.object(cli, "export_source_overlay", return_value={"files": names}) as export:
            code, output, error = self.invoke(["export-source", "--source-root", "source/RPNH", "--output", "returns/erp"])
        self.assertEqual(code, 0, error)
        identity.assert_called_once_with(Path("source/RPNH"))
        export.assert_called_once_with(Path("source/RPNH"), Path("returns/erp"), paths=names)
        self.assertEqual(json.loads(output), {"files": names})

    def test_run_refuses_missing_authorization_before_driver(self):
        driver = self.driver({"status": "should-not-run"})
        with patch.dict(sys.modules, {"rpnh_erp_bench.driver": driver}):
            code, output, error = self.invoke(self.run_args)
        self.assertEqual(code, 2)
        self.assertFalse(output)
        self.assertIn("--authorize-existing-model", error)
        driver.run_trial.assert_not_called()

    def test_run_forwards_exact_arguments_and_defaults_without_claim_upgrade(self):
        result = {"record_id": "condition-01", "stages": {"provider": {"status": "blocked"}},
                  "model_calls": {"real_provider_calls": None}, "scores": []}
        driver = self.driver(result)
        with patch.dict(sys.modules, {"rpnh_erp_bench.driver": driver}):
            code, output, error = self.invoke([*self.run_args, "--authorize-existing-model"])
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output), result)
        driver.run_trial.assert_awaited_once_with(upstream=Path("upstream/erp-bench"), task_id=TASK_IDS[0],
            execution_selection=Path("private/selection.json"), run_root=Path("private/condition-01"),
            condition_id="condition-01", source_root=Path("source/RPNH"), shared_validator=Path("contracts/validate.py"),
            authorize_existing_model=True, previous_record=None, firewall_packages=(), build_compatibility=False,
            codex_binary=None)

    def test_run_forwards_predecessor_and_explicit_repairs(self):
        driver = self.driver({"record_id": "repaired"})
        with patch.dict(sys.modules, {"rpnh_erp_bench.driver": driver}):
            code, _, error = self.invoke([*self.run_args, "--authorize-existing-model",
                "--previous-record", "reports/previous/result-manifest.json",
                "--firewall-package", "packages/a.deb", "--firewall-package", "packages/b.deb",
                "--build-compatibility"])
        self.assertEqual(code, 0, error)
        kwargs = driver.run_trial.await_args.kwargs
        self.assertEqual(kwargs["previous_record"], Path("reports/previous/result-manifest.json"))
        self.assertEqual(kwargs["firewall_packages"], (Path("packages/a.deb"), Path("packages/b.deb")))
        self.assertIs(kwargs["build_compatibility"], True)

    def test_no_arbitrary_model_or_budget_flags(self):
        driver = self.driver({})
        with patch.dict(sys.modules, {"rpnh_erp_bench.driver": driver}):
            for flag in ("--model", "--max-calls", "--spend-cap", "--agent-timeout"):
                self.assertEqual(self.invoke([*self.run_args, "--authorize-existing-model", flag, "1"])[0], 2)
        driver.run_trial.assert_not_called()

    def test_driver_exception_and_unsafe_result_do_not_escape(self):
        for driver in (self.driver(exception=RuntimeError("/home/private/key sensitive")),
                       self.driver({"private_path": "/home/private/key"})):
            with patch.dict(sys.modules, {"rpnh_erp_bench.driver": driver}):
                code, output, error = self.invoke([*self.run_args, "--authorize-existing-model"])
            self.assertEqual(code, 1)
            self.assertFalse(output)
            self.assertEqual(json.loads(error)["error"], "command_failed")
            self.assertNotIn("/home/", error)
            self.assertNotIn("sensitive", error)

    def test_invalid_report_error_is_safe(self):
        path = self.write_json("bad-manifest.json", {})
        with patch.object(cli, "validate_result_manifest", side_effect=ValueError("private /home/owner/file")):
            code, output, error = self.invoke(["validate-report", "--manifest", path,
                "--shared-validator", "contracts/validate.py", "--artifacts-root", "reports"])
        self.assertEqual(code, 1)
        self.assertFalse(output)
        self.assertEqual(json.loads(error), {"error": "command_failed", "error_type": "ValueError"})


if __name__ == "__main__":
    unittest.main()
