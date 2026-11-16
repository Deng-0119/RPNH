"""Candidate contexts cannot enter the public Python business APIs."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpn.rpnh import agent_tasks, run
from cpn.rpnh.task_control import TaskControl, claim_task_worker_launch


class CandidateBusinessEntryTests(unittest.TestCase):
    def test_task_control_and_claim_reject_before_launch_lock_creation(self):
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "uncreated-control"
            with patch.dict(os.environ, {"RPNH_CANDIDATE_ID": ""}, clear=True):
                for operation in (
                        lambda: TaskControl(destination),
                        lambda: claim_task_worker_launch(
                            destination / "specs" / "task-uncreated.json", None, resume=False)):
                    with self.assertRaisesRegex(ValueError, "candidate installation context"):
                        operation()
                    self.assertFalse(destination.exists())

    def test_agent_apis_reject_even_empty_candidate_hints_before_source_reads(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            spec = agent_tasks.AgentTaskSpec(
                run_dir=directory / "uncreated-run", prompt="No task may start here.",
                stages=(agent_tasks.AgentStage("worker", "Return a result."),),
                execution_config_path=directory / "unread-execution.json")
            operations = (
                lambda: agent_tasks.run_agent_task(spec),
                lambda: agent_tasks.resume_agent_task(spec),
                lambda: agent_tasks.reopen_agent_task(
                    spec, checkpoint_version_id="checkpoint", command_id="reopen", reason="explicit"),
            )
            for key in ("RPNH_CANDIDATE_ID", "RPNH_CANDIDATE_FD"):
                with self.subTest(key=key), patch.dict(os.environ, {key: ""}, clear=True), \
                        patch.object(agent_tasks, "load_llm_execution_selection") as selection:
                    for operation in operations:
                        with self.assertRaisesRegex(ValueError, "candidate installation context"):
                            operation()
                    selection.assert_not_called()
                    self.assertFalse(spec.run_dir.exists())
                    self.assertEqual(tuple(directory.iterdir()), ())

    def test_run_apis_reject_before_compilation_or_registry_construction(self):
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "uncreated-run"
            operations = (
                lambda: run.start_run(
                    None, None, run_dir=destination, task_input=None, entry_inputs={}, budgets=None,
                    model_condition="unused", owner_statement="unused", command_id="unused"),
                lambda: run.resume_run(None, run_dir=destination, model_condition="unused"),
            )
            for key in ("RPNH_CANDIDATE_ID", "RPNH_CANDIDATE_FD"):
                with self.subTest(key=key), patch.dict(os.environ, {key: ""}, clear=True), \
                        patch.object(run, "_RegistryCore") as registry, \
                        patch.object(run, "compile_module") as compile_module:
                    for operation in operations:
                        with self.assertRaisesRegex(ValueError, "candidate installation context"):
                            operation()
                    registry.assert_not_called()
                    compile_module.assert_not_called()
                    self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
